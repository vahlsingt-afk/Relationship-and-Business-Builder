#!/usr/bin/env python3
"""
smart_loops.py — propose tracked loops from the canonical daily brief.

The Daily Brief operating layer surfaces "what should I do?". This module is
the execution bridge: it converts those recommendations into structured loop
proposals carrying entity, source signal, action type, due date, verification
method, confidence, and closure criteria — the fields P-009 needs to actually
open a tracked loop.

Two modes:

    python3 smart_loops.py --dry-run                       # proposals only (default)
    python3 smart_loops.py --apply --confirm               # call mutations.cmd_loop_add per proposal
    python3 smart_loops.py --apply --confirm --ids p-001 p-003   # selective

Design choices (anchored in the sprint handoff and tenets):

  * Default is read-only. The smoke-test-mutations memory says any path that
    can reach apply_mutations must take a dry_run guard; here we go further
    and require both --apply AND --confirm before anything is written.
  * Dedupe uses canonical `source_refs` first, then a party+action_type+
    target-within-7-days heuristic. We never want to spam the ledger with
    near-duplicate proposals across days.
  * Proposals carry verification_method so passive verification (a future
    sprint workstream) can auto-close them later from observed evidence.
  * Output is JSON-clean so the API can return it without reshaping, and so
    the Daily Brief can lift each proposal into a canonical item.

The module is intentionally additive — it does not change how `mutations.py`
or `loop_parser.py` behave. It composes on top.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core


# -----------------------------------------------------------------------------
# Data model
# -----------------------------------------------------------------------------

ACTION_TYPES = {
    "follow_up",
    "meeting_prep",
    "waiting",
    "reconciliation",
    "commitment",
}

VERIFICATION_BY_TYPE = {
    "follow_up": "Outbound message to entity observed in email/messages/social OR inbound reply received.",
    "meeting_prep": "Calendar event has passed AND prep brief artifact exists for the meeting.",
    "waiting": "Inbound reply observed from recipient OR expected-response window expires (then convert to follow_up).",
    "reconciliation": "Operator answers the reconciliation question and the canonical state is updated.",
    "commitment": "Active-thread closure recorded OR explicit operator confirmation that the commitment was fulfilled.",
}

DEFAULT_AFFORDANCES = {
    "follow_up":      ["create_loop", "draft_message", "defer", "mark_done", "mark_irrelevant"],
    "meeting_prep":   ["create_meeting_prep", "create_loop", "defer", "skip_prep"],
    "waiting":        ["create_waiting_loop", "draft_followup_now", "extend_window", "mark_resolved"],
    "reconciliation": ["answer_question", "create_loop", "defer", "suppress_question"],
    "commitment":     ["create_loop", "draft_next_action", "redate", "mark_done"],
}


@dataclass
class LoopProposal:
    """A single proposed loop, ready to drop into mutations.cmd_loop_add."""
    proposal_id: str
    entity: str
    source_signal: str
    action_type: str
    due_date: str        # ISO YYYY-MM-DD
    verification_method: str
    confidence: str      # "high" | "medium" | "low"
    closure_criteria: str
    # Ledger-shaped fields (what mutations.py loop-add expects).
    party: str
    description: str
    target: str          # mirrors due_date
    # Affordances + audit trail.
    action_options: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    origin_section: str = ""
    origin_title: str = ""
    dedupe_key: str = ""
    # Populated when apply runs:
    applied_loop_id: Optional[str] = None
    apply_status: Optional[str] = None      # "applied" | "deduped" | "dry_run" | "error:<msg>"
    apply_detail: Optional[str] = None


# -----------------------------------------------------------------------------
# Date helpers
# -----------------------------------------------------------------------------

def _today() -> date:
    return date.today()


def _add_business_days(start: date, n: int) -> date:
    """Add n business days (skips Saturday and Sunday)."""
    if n <= 0:
        return start
    d = start
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def _parse_iso_date(s) -> Optional[date]:
    """Accept str | date | datetime | None and return a date or None.

    PyYAML auto-coerces ISO-format date scalars into datetime.date objects
    when active_threads.yaml is loaded, so this helper has to tolerate both
    string and native-date inputs.
    """
    if s is None:
        return None
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    if not isinstance(s, str):
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _parse_iso_datetime(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.fromisoformat(s[:19])
        except ValueError:
            return None


# -----------------------------------------------------------------------------
# Proposal construction
# -----------------------------------------------------------------------------

def _hash_proposal_id(parts: list[str]) -> str:
    raw = "|".join(p for p in parts if p)
    return "P-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def _confidence_from_item(item: dict, default: str = "medium") -> str:
    c = (item or {}).get("confidence")
    if c in {"high", "medium", "low"}:
        return c
    return default


def _entity_from_calendar_event(ev: dict) -> str:
    attendees = ev.get("attendees_matched") or []
    matched = [a.get("name") for a in attendees if a.get("id") and a.get("name")]
    if matched:
        return ", ".join(matched[:3])
    threads = ev.get("matched_threads") or []
    if threads:
        return threads[0]
    return ev.get("title") or "Unknown meeting attendee"


def _propose_meeting_prep(report: dict, today: date) -> list[LoopProposal]:
    out: list[LoopProposal] = []
    canonical = (report.get("canonical_brief") or {}).get("sections") or {}
    meeting_items = canonical.get("meeting_prep_and_deliverables") or []
    # The Daily Brief already ranked these. We still need the underlying
    # calendar payload to pin a due-date to "day before the meeting".
    cal = report.get("calendar") or {}
    events_by_id: dict[str, dict] = {}
    for bucket in ("today", "tomorrow", "this_week"):
        for ev in cal.get(bucket) or []:
            ev_id = ev.get("id") or ev.get("title") or ""
            events_by_id[str(ev_id)] = ev

    for item in meeting_items:
        # source_refs look like "calendar.<id_or_title>" (plus, since the
        # meeting-prep artifact wiring landed, an optional
        # "meeting_briefs.<relpath>" entry that pins the artifact target).
        ev = None
        artifact_rel = None
        for ref in item.get("source_refs", []):
            if ref.startswith("calendar."):
                key = ref[len("calendar."):]
                if not ev:
                    ev = events_by_id.get(key)
            elif ref.startswith("meeting_briefs."):
                artifact_rel = ref[len("meeting_briefs."):]
        if not ev:
            continue
        start = _parse_iso_datetime(ev.get("start"))
        if not start:
            continue
        meeting_day = start.date()
        # Due "day before the meeting", but never earlier than today + 1.
        due = max(meeting_day - timedelta(days=1), today)
        # If the meeting is today, set due to today (still actionable).
        if meeting_day <= today:
            due = today
        entity = _entity_from_calendar_event(ev)
        title = ev.get("title") or "Untitled meeting"
        # When the daily brief has already pinned an artifact path, fold it
        # into closure + verification so passive verification can check for
        # the artifact's existence and the meeting's start time having passed.
        artifact_clause = (
            f" Artifact target: {artifact_rel}." if artifact_rel else ""
        )
        closure = (
            f"Prep brief for '{title}' on {meeting_day.isoformat()} reviewed; "
            "talking points + expected outcome + likely follow-ups identified."
            + artifact_clause
        )
        description = (
            f"Meeting prep — {title} on {meeting_day.isoformat()} "
            f"with {entity}. Closure: {closure}"
        )
        source_signal = f"calendar.{ev.get('id') or title}"
        dedupe_key = f"meeting_prep::{ev.get('id') or title}::{meeting_day.isoformat()}"
        verification = VERIFICATION_BY_TYPE["meeting_prep"]
        if artifact_rel:
            verification = (
                f"{artifact_rel} exists AND the meeting start time has passed."
            )
        out.append(LoopProposal(
            proposal_id=_hash_proposal_id(["meeting_prep", entity, title, meeting_day.isoformat()]),
            entity=entity,
            source_signal=source_signal,
            action_type="meeting_prep",
            due_date=due.isoformat(),
            verification_method=verification,
            confidence=_confidence_from_item(item, "high"),
            closure_criteria=closure,
            party=entity,
            description=description,
            target=due.isoformat(),
            action_options=list(DEFAULT_AFFORDANCES["meeting_prep"]),
            source_refs=list(item.get("source_refs", [])),
            origin_section="meeting_prep_and_deliverables",
            origin_title=item.get("title", ""),
            dedupe_key=dedupe_key,
        ))
    return out


def _propose_sent_followups(report: dict, today: date) -> list[LoopProposal]:
    out: list[LoopProposal] = []
    em = report.get("email") or {}
    for sf in em.get("sent_followups") or []:
        status = sf.get("response_status") or ""
        thread_id = sf.get("thread_id") or ""
        subject = (sf.get("subject") or "").strip()[:120]
        # Recipient label
        recipient = ""
        if sf.get("matched_contacts"):
            recipient = (sf["matched_contacts"][0] or {}).get("name") or ""
        if not recipient and (sf.get("to") or []):
            r0 = sf["to"][0] or {}
            recipient = r0.get("name") or r0.get("email") or "(recipient)"
        recipient = recipient or "(recipient)"

        if status == "response_overdue":
            action_type = "follow_up"
            due = today
            closure = (
                f"Outbound follow-up sent to {recipient} on thread '{subject}' "
                "OR inbound reply received."
            )
            description = (
                f"Follow up with {recipient} on thread '{subject}' — "
                f"expected-response window passed ({sf.get('business_days_since_sent')} bdays since send)."
            )
        elif status == "awaiting_response":
            action_type = "waiting"
            expected = _parse_iso_date(sf.get("expected_response_by")) or today
            # Convert to follow_up two business days after expected window.
            due = _add_business_days(expected, 2)
            closure = (
                f"Inbound reply observed from {recipient} OR window expires on {due.isoformat()} "
                "and waiting loop converts to follow_up."
            )
            description = (
                f"Waiting on reply from {recipient} on '{subject}'. "
                f"Expected by {sf.get('expected_response_by') or 'unknown'}; "
                f"convert to follow_up after {due.isoformat()} if silent."
            )
        else:
            continue

        source_signal = f"email_overlay.sent_followups.{thread_id}"
        dedupe_key = f"{action_type}::{recipient}::{thread_id}"
        out.append(LoopProposal(
            proposal_id=_hash_proposal_id([action_type, recipient, thread_id, subject]),
            entity=recipient,
            source_signal=source_signal,
            action_type=action_type,
            due_date=due.isoformat(),
            verification_method=VERIFICATION_BY_TYPE[action_type],
            confidence=_confidence_from_item(sf, "medium"),
            closure_criteria=closure,
            party=recipient,
            description=description,
            target=due.isoformat(),
            action_options=list(DEFAULT_AFFORDANCES[action_type]),
            source_refs=[source_signal] + [
                f"active_threads.{tid}" for tid in (sf.get("matched_threads") or [])
            ],
            origin_section="sent_followups_awaiting_response",
            origin_title=f"{recipient} — {status}",
            dedupe_key=dedupe_key,
        ))
    return out


def _propose_relationship_signals(report: dict, today: date) -> list[LoopProposal]:
    out: list[LoopProposal] = []
    rsr = report.get("relationship_signals") or {}
    for s in rsr.get("signals") or []:
        if s.get("strategic_relevance") != "high":
            continue
        if (s.get("recommended_action") or "").lower() in {"monitor", "ignore"}:
            continue
        entity = s.get("name") or s.get("entity") or s.get("contact_id") or "Unknown contact"
        signal_type = s.get("signal_type") or "high_signal_interaction"
        source = s.get("source") or "relationship_signal"
        event_at = s.get("event_at") or ""
        action_text = s.get("recommended_action") or "Follow up on this signal."
        closure = (
            f"Outbound message or scheduled meeting with {entity} recorded "
            "as evidence the signal was acted on."
        )
        description = (
            f"Act on high-relevance {signal_type} signal from {entity}: {action_text}"
        )
        source_signal = f"relationship_signals.signals.{source}.{event_at}"
        # Due tomorrow — high-relevance signals deserve action within one business day.
        due = _add_business_days(today, 1)
        dedupe_key = f"follow_up::{entity}::{source}::{event_at[:10]}"
        confidence_raw = s.get("confidence", 0.7)
        if isinstance(confidence_raw, (int, float)):
            confidence = "high" if confidence_raw >= 0.8 else ("medium" if confidence_raw >= 0.5 else "low")
        else:
            confidence = str(confidence_raw) if confidence_raw in {"high", "medium", "low"} else "medium"
        out.append(LoopProposal(
            proposal_id=_hash_proposal_id(["follow_up", entity, source, event_at]),
            entity=entity,
            source_signal=source_signal,
            action_type="follow_up",
            due_date=due.isoformat(),
            verification_method=VERIFICATION_BY_TYPE["follow_up"],
            confidence=confidence,
            closure_criteria=closure,
            party=entity,
            description=description,
            target=due.isoformat(),
            action_options=list(DEFAULT_AFFORDANCES["follow_up"]),
            source_refs=[source_signal],
            origin_section="last_24h_relationship_signals",
            origin_title=entity,
            dedupe_key=dedupe_key,
        ))
    return out


def _propose_thread_commitments(report: dict, today: date) -> list[LoopProposal]:
    out: list[LoopProposal] = []
    high_threads = [
        t for t in report.get("active_threads") or []
        if t.get("boost_for_brief") == "high"
    ]
    for t in high_threads:
        # Skip if any existing open loop already references this thread.
        thread_id = t.get("id") or ""
        title = t.get("title") or thread_id or "Active thread"
        target_close = _parse_iso_date(t.get("target_close"))
        due = target_close or (today + timedelta(days=7))
        if due <= today:
            due = today + timedelta(days=1)
        entity = title
        if t.get("people"):
            entity = ", ".join(t["people"][:3])
        closure = (
            f"Active thread '{title}' closed OR operator records next concrete move "
            f"by {due.isoformat()}."
        )
        description = (
            f"Move active thread '{title}' forward. "
            f"Current state: {t.get('current_state') or t.get('context') or 'see thread.'} "
            f"Target close: {target_close.isoformat() if target_close else 'unset'}."
        )
        source_signal = f"active_threads.{thread_id}"
        dedupe_key = f"commitment::{thread_id}"
        out.append(LoopProposal(
            proposal_id=_hash_proposal_id(["commitment", thread_id, due.isoformat()]),
            entity=entity,
            source_signal=source_signal,
            action_type="commitment",
            due_date=due.isoformat(),
            verification_method=VERIFICATION_BY_TYPE["commitment"],
            confidence="medium",
            closure_criteria=closure,
            party=entity,
            description=description,
            target=due.isoformat(),
            action_options=list(DEFAULT_AFFORDANCES["commitment"]),
            source_refs=[source_signal],
            origin_section="top_priorities_today",
            origin_title=title,
            dedupe_key=dedupe_key,
        ))
    return out


def _propose_reconciliations(report: dict, today: date) -> list[LoopProposal]:
    out: list[LoopProposal] = []
    rsr = report.get("relationship_signals") or {}
    for r in rsr.get("reconciliation_needed") or []:
        prompt = r.get("prompt") or ""
        reason = r.get("reason") or "reconciliation"
        rec_id = r.get("id") or _hash_proposal_id(["reconciliation", reason, prompt])
        due = today + timedelta(days=2)
        closure = (
            "Operator answered the reconciliation question and canonical state "
            "was updated (or the question was explicitly suppressed)."
        )
        description = f"Reconciliation needed: {reason}. Question: {prompt}"
        source_signal = f"relationship_signals.reconciliation_needed.{rec_id}"
        dedupe_key = f"reconciliation::{rec_id}"
        out.append(LoopProposal(
            proposal_id=_hash_proposal_id(["reconciliation", rec_id]),
            entity=reason,
            source_signal=source_signal,
            action_type="reconciliation",
            due_date=due.isoformat(),
            verification_method=VERIFICATION_BY_TYPE["reconciliation"],
            confidence="medium",
            closure_criteria=closure,
            party=reason,
            description=description,
            target=due.isoformat(),
            action_options=list(DEFAULT_AFFORDANCES["reconciliation"]),
            source_refs=[source_signal],
            origin_section="reconciliation_prompts",
            origin_title=reason,
            dedupe_key=dedupe_key,
        ))
    return out


# -----------------------------------------------------------------------------
# Dedupe
# -----------------------------------------------------------------------------

def _existing_open_loops() -> list[dict]:
    """Return open loops in a dict-shaped, dataclass-free form."""
    loops = core.parse_loop_ledger()
    return [
        {
            "id": L.id,
            "party": L.party,
            "description": L.description,
            "target": L.target,
            "opened": L.opened,
        }
        for L in loops if not L.closed
    ]


def _is_duplicate(proposal: LoopProposal, open_loops: list[dict]) -> Optional[str]:
    """Return the existing loop id if `proposal` looks like a duplicate, else None.

    Heuristics, ordered by reliability:
      1. Source-signal match: any open loop description that contains a
         distinctive fragment from the proposal's source_signal (e.g.,
         a thread id, calendar id, or sent-followup thread_id).
      2. Party + action keyword + target within ±7 days.
    """
    src = proposal.source_signal
    # 1. source-signal exact-fragment match
    for frag in [src] + (proposal.source_refs or []):
        # use the last segment after the final '.' or ':' or '/' as the discriminator
        token = frag.replace(":", ".").rsplit(".", 1)[-1]
        if not token or len(token) < 3:
            continue
        for L in open_loops:
            if token in (L["description"] or ""):
                return L["id"]

    # 2. party + action keyword + target window
    action_keyword = {
        "follow_up": "follow",
        "meeting_prep": "prep",
        "waiting": "waiting",
        "reconciliation": "reconcil",
        "commitment": "thread",
    }.get(proposal.action_type, proposal.action_type)
    p_party = (proposal.party or "").lower().strip()
    if not p_party:
        return None
    try:
        p_target = date.fromisoformat(proposal.target)
    except ValueError:
        return None
    for L in open_loops:
        if not L["party"]:
            continue
        if p_party not in L["party"].lower() and L["party"].lower() not in p_party:
            continue
        if action_keyword and action_keyword not in (L["description"] or "").lower():
            continue
        delta = abs((L["target"] - p_target).days)
        if delta <= 7:
            return L["id"]
    return None


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def _propose_linkedin_inbound(report: dict, today: date) -> list[LoopProposal]:
    """LinkedIn inbound RI from a matched RC/LKI → follow_up proposal."""
    out: list[LoopProposal] = []
    li = report.get("linkedin_messaging") or {}
    for c in li.get("inbound_ri_candidates") or []:
        contact_id = c.get("contact_id") or ""
        name = c.get("name") or contact_id or "LinkedIn contact"
        last_in = c.get("last_inbound_at") or ""
        # Due tomorrow — same window as email-side high-relevance signals.
        due = _add_business_days(today, 1)
        closure = (
            f"Outbound LinkedIn message OR email reply to {name} observed "
            "as evidence the inbound was acted on."
        )
        description = (
            f"Follow up on inbound LinkedIn from {name}: "
            f"{c.get('inbound_count', 0)} message(s); last {last_in[:10] if last_in else 'recent'}."
        )
        source_signal = c.get("ref") or f"linkedin_messaging.matched_contacts.{contact_id}"
        dedupe_key = f"follow_up::{name}::linkedin::{last_in[:10] if last_in else ''}"
        out.append(LoopProposal(
            proposal_id=_hash_proposal_id(["follow_up", name, "linkedin", last_in[:10] if last_in else ""]),
            entity=name,
            source_signal=source_signal,
            action_type="follow_up",
            due_date=due.isoformat(),
            verification_method=VERIFICATION_BY_TYPE["follow_up"],
            confidence="high" if c.get("match_quality") == "high" else "medium",
            closure_criteria=closure,
            party=name,
            description=description,
            target=due.isoformat(),
            action_options=list(DEFAULT_AFFORDANCES["follow_up"]),
            source_refs=[source_signal],
            origin_section="last_24h_relationship_signals",
            origin_title=name,
            dedupe_key=dedupe_key,
        ))
    return out


def propose_loops(report: dict, *, today: Optional[date] = None) -> dict:
    """Return a structured proposal report.

    The shape is API-stable: the FastAPI surface returns this dict directly.
    """
    today = today or _today()
    proposals: list[LoopProposal] = []
    proposals.extend(_propose_meeting_prep(report, today))
    proposals.extend(_propose_sent_followups(report, today))
    proposals.extend(_propose_relationship_signals(report, today))
    proposals.extend(_propose_linkedin_inbound(report, today))
    proposals.extend(_propose_thread_commitments(report, today))
    proposals.extend(_propose_reconciliations(report, today))

    open_loops = _existing_open_loops()
    fresh: list[LoopProposal] = []
    deduped: list[LoopProposal] = []
    for p in proposals:
        existing_id = _is_duplicate(p, open_loops)
        if existing_id:
            p.apply_status = "deduped"
            p.apply_detail = f"matched existing open loop {existing_id}"
            deduped.append(p)
        else:
            fresh.append(p)

    return {
        "today": today.isoformat(),
        "counts": {
            "total": len(proposals),
            "fresh": len(fresh),
            "deduped": len(deduped),
            "by_type": _by_type([asdict(p) for p in proposals]),
        },
        "fresh": [asdict(p) for p in fresh],
        "deduped": [asdict(p) for p in deduped],
        "contract": "smart_loops_v1",
    }


def _by_type(items: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for it in items:
        t = it.get("action_type", "unknown")
        counts[t] = counts.get(t, 0) + 1
    return counts


def apply_proposals(report: dict, *, today: Optional[date] = None,
                    only_ids: Optional[list[str]] = None,
                    confirm: bool = False) -> dict:
    """Open loops for each non-deduped proposal.

    Safety contract — three layers, defense in depth:
      1. Default-safe: confirm=False returns proposals + 'not_confirmed' status
         and writes nothing. This protects against the smoke-test-mutations
         pattern noted in memory.
      2. Each apply call goes through mutations.cmd_loop_add, which already
         snapshots + writes the ledger per P-009.
      3. Dedupe runs again right before each apply in case the ledger changed
         between the propose call and the apply call.
    """
    proposal_report = propose_loops(report, today=today)
    if not confirm:
        for p in proposal_report["fresh"]:
            p["apply_status"] = "not_confirmed"
            p["apply_detail"] = "Pass --apply --confirm (CLI) or {confirm:true} (API) to actually open loops."
        proposal_report["applied_count"] = 0
        proposal_report["confirmed"] = False
        return proposal_report

    # Real apply path. Import locally so a propose-only caller never imports
    # mutations.
    import mutations  # type: ignore

    open_loops = _existing_open_loops()
    applied = 0
    errors: list[str] = []
    for p in proposal_report["fresh"]:
        if only_ids and p["proposal_id"] not in only_ids:
            p["apply_status"] = "skipped_not_in_ids"
            continue
        # Re-check dedupe against the freshest ledger state.
        proxy = LoopProposal(**{k: v for k, v in p.items() if k in LoopProposal.__dataclass_fields__})
        existing_id = _is_duplicate(proxy, open_loops)
        if existing_id:
            p["apply_status"] = "deduped"
            p["apply_detail"] = f"matched existing open loop {existing_id}"
            continue
        try:
            ns = _ns_for_loop_add(p)
            rc = mutations.cmd_loop_add(ns)
            if rc != 0:
                p["apply_status"] = "error:loop_add_nonzero"
                p["apply_detail"] = f"mutations.cmd_loop_add returned {rc}"
                errors.append(p["proposal_id"])
            else:
                # cmd_loop_add prints "Added L-...." but doesn't return the id;
                # we re-read the ledger to find the most recent matching row.
                new_loops = _existing_open_loops()
                new_id = None
                for L in new_loops:
                    if L["party"] == p["party"] and L["description"] == p["description"]:
                        new_id = L["id"]
                        break
                p["applied_loop_id"] = new_id
                p["apply_status"] = "applied"
                p["apply_detail"] = f"opened loop {new_id}" if new_id else "opened (id unverified)"
                applied += 1
                # Refresh open-loops snapshot for next iteration's dedupe.
                open_loops = new_loops
        except Exception as exc:  # noqa: BLE001
            p["apply_status"] = f"error:{type(exc).__name__}"
            p["apply_detail"] = str(exc)
            errors.append(p["proposal_id"])

    proposal_report["applied_count"] = applied
    proposal_report["confirmed"] = True
    proposal_report["errors"] = errors
    return proposal_report


def _ns_for_loop_add(p: dict):
    """Build the argparse.Namespace shape mutations.cmd_loop_add expects."""
    class Ns:
        pass
    ns = Ns()
    ns.party = p["party"]
    ns.description = p["description"]
    ns.target = p["target"]
    ns.opened = None
    ns.id = None
    ns.dry_run = False
    return ns


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _load_report(date_str: Optional[str]) -> dict:
    """Build a fresh daily-brief report for proposal generation.

    Imported locally to avoid a daily_brief import cycle when smart_loops is
    used purely as a library (e.g., by the API or by daily_brief itself).
    """
    import daily_brief  # type: ignore
    d = date.fromisoformat(date_str) if date_str else _today()
    return daily_brief.build_report(d)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", help="ISO date for the brief (default: system date).")
    p.add_argument("--dry-run", action="store_true",
                   help="Default. Print proposals; no mutations.")
    p.add_argument("--apply", action="store_true",
                   help="Open loops via mutations.cmd_loop_add. Requires --confirm.")
    p.add_argument("--confirm", action="store_true",
                   help="Second-factor flag required with --apply.")
    p.add_argument("--ids", nargs="*", default=None,
                   help="Restrict --apply to these proposal_ids.")
    p.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    p.add_argument("--smoke", action="store_true",
                   help="Run the in-memory regression (no inbox I/O, no mutations).")
    args = p.parse_args()

    if args.smoke:
        return _smoke()

    today = date.fromisoformat(args.date) if args.date else _today()
    report = _load_report(args.date)

    if args.apply:
        if not args.confirm:
            print("ERROR: --apply requires --confirm.", file=sys.stderr)
            return 2
        out = apply_proposals(report, today=today, only_ids=args.ids, confirm=True)
    else:
        out = propose_loops(report, today=today)

    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    # Human-readable summary
    counts = out.get("counts") or {}
    print(f"smart_loops for {out.get('today')}: "
          f"{counts.get('total', 0)} total, "
          f"{counts.get('fresh', 0)} fresh, "
          f"{counts.get('deduped', 0)} deduped.")
    for t, n in (counts.get("by_type") or {}).items():
        print(f"  {t:16s} {n}")
    if out.get("confirmed"):
        print(f"applied: {out.get('applied_count', 0)}")
        if out.get("errors"):
            print(f"errors: {', '.join(out['errors'])}", file=sys.stderr)
    elif args.apply and not args.confirm:
        # Already handled above; defensive.
        pass
    else:
        print("(no mutations — pass --apply --confirm to open these loops)")
    return 0


# -----------------------------------------------------------------------------
# Smoke test
# -----------------------------------------------------------------------------

def _smoke() -> int:
    """In-memory regression. No file I/O, no mutations. Per the
    smoke-test-mutations memory: any path that could reach apply_mutations()
    must take a dry_run-equivalent path. This smoke uses confirm=False which
    is the API-level dry-run guard."""
    failures: list[str] = []

    def ck(cond: bool, msg: str) -> None:
        mark = "OK" if cond else "FAIL"
        print(f"  {mark}   {msg}")
        if not cond:
            failures.append(msg)

    today = date(2026, 5, 21)
    synthetic_report = {
        "today": today.isoformat(),
        "active_threads": [
            {
                "id": "AT-test",
                "title": "Test commitment thread",
                "boost_for_brief": "high",
                "target_close": "2026-05-28",
                "people": ["test-person"],
                "current_state": "Awaiting next move.",
            },
        ],
        "calendar": {
            "fetched_at": "2026-05-21T06:00:00-05:00",
            "today": [
                {
                    "id": "ev-test",
                    "title": "McDonald's account review deck",
                    "start": "2026-05-22T14:00:00-05:00",
                    "end": "2026-05-22T14:30:00-05:00",
                    "attendees_matched": [
                        {"id": "p-simin", "name": "Simin"},
                    ],
                    "matched_threads": ["AT-test"],
                    "active_thread_company_hits": ["McDonald's"],
                },
            ],
            "tomorrow": [],
            "this_week": [],
        },
        "email": {
            "sent_followups": [
                {
                    "thread_id": "T-A",
                    "subject": "Re: Harri intro follow-up",
                    "sent_at": "2026-05-10T12:00:00+00:00",
                    "to": [{"name": "Simin", "email": "simin@example.com"}],
                    "matched_threads": ["AT-test"],
                    "matched_contacts": [],
                    "response_window_business_days": [3, 5],
                    "business_days_since_sent": 7,
                    "expected_response_by": "2026-05-15",
                    "response_status": "response_overdue",
                    "confidence": "high",
                },
                {
                    "thread_id": "T-B",
                    "subject": "Catching up",
                    "sent_at": "2026-05-19T12:00:00+00:00",
                    "to": [{"name": "Jane Doe", "email": "jane@example.org"}],
                    "matched_threads": [],
                    "matched_contacts": [{"id": "p-jane", "name": "Jane Doe"}],
                    "response_window_business_days": [5, 7],
                    "business_days_since_sent": 1,
                    "expected_response_by": "2026-05-29",
                    "response_status": "awaiting_response",
                    "confidence": "medium",
                },
            ],
        },
        "relationship_signals": {
            "stale_sources": [],
            "signals": [
                {
                    "name": "Ish Singh",
                    "source": "email",
                    "event_at": "2026-05-20T18:00:00+00:00",
                    "signal_type": "warm_inbound",
                    "strategic_relevance": "high",
                    "signal_strength": "high",
                    "recommended_action": "Schedule Maho deep-dive via Calendly.",
                    "confidence": 0.9,
                    "reasoning": "Direct invite to deeper conversation.",
                },
                {
                    "name": "Quiet contact",
                    "source": "social",
                    "event_at": "2026-05-20",
                    "signal_type": "low",
                    "strategic_relevance": "low",
                    "recommended_action": "monitor",
                    "confidence": 0.3,
                },
            ],
            "reconciliation_needed": [
                {
                    "id": "rec-1",
                    "reason": "Jeff Wayman last_touch",
                    "prompt": "Should May 12 message volume update last_touch?",
                    "urgency": "medium",
                },
            ],
        },
        "loops": {"overdue": [], "due_today": [], "this_week": []},
        # canonical_brief is populated by daily_brief.build_canonical_brief in real runs;
        # the smoke pre-populates only what _propose_meeting_prep needs.
        "canonical_brief": {
            "sections": {
                "meeting_prep_and_deliverables": [
                    {
                        "title": "Prep needed: McDonald's account review deck",
                        "source_refs": ["calendar.ev-test"],
                        "confidence": "high",
                    },
                ],
            },
        },
    }

    # The smoke must not touch the on-disk ledger. Stub out _existing_open_loops.
    global _existing_open_loops
    real_existing = _existing_open_loops

    def _no_existing():
        return []
    _existing_open_loops = _no_existing  # type: ignore
    try:
        result = propose_loops(synthetic_report, today=today)
    finally:
        _existing_open_loops = real_existing  # type: ignore

    counts = result["counts"]
    ck(counts["total"] >= 5,
       f"at least 5 proposals (meeting + 2 followups + 1 signal + 1 thread + 1 reconciliation), got {counts['total']}")
    ck(counts["by_type"].get("meeting_prep", 0) == 1, "one meeting_prep proposal")
    ck(counts["by_type"].get("follow_up", 0) >= 2,
       "at least two follow_up proposals (overdue sent + high signal)")
    ck(counts["by_type"].get("waiting", 0) == 1, "one waiting proposal")
    ck(counts["by_type"].get("commitment", 0) == 1, "one commitment proposal")
    ck(counts["by_type"].get("reconciliation", 0) == 1, "one reconciliation proposal")

    by_id = {p["proposal_id"]: p for p in result["fresh"]}
    meeting = next((p for p in result["fresh"] if p["action_type"] == "meeting_prep"), None)
    ck(meeting is not None, "meeting proposal materialized")
    if meeting:
        ck(meeting["due_date"] == "2026-05-21",
           f"meeting_prep due-date is day-before-meeting (got {meeting['due_date']})")
        ck("Simin" in meeting["entity"], "meeting_prep entity carries known attendee name")
        ck(meeting["verification_method"].startswith("Calendar event"),
           "meeting_prep verification_method references calendar evidence")

    overdue = next((p for p in result["fresh"]
                    if p["action_type"] == "follow_up" and "Simin" in p["entity"]), None)
    ck(overdue is not None, "overdue follow_up proposal materialized")
    if overdue:
        ck(overdue["due_date"] == today.isoformat(), "overdue follow_up due today")
        ck("T-A" in overdue["source_signal"], "overdue follow_up carries email thread id")

    waiting = next((p for p in result["fresh"] if p["action_type"] == "waiting"), None)
    ck(waiting is not None, "waiting proposal materialized")
    if waiting:
        # expected 2026-05-29 + 2 bdays = Tuesday 2026-06-02
        ck(waiting["due_date"] == "2026-06-02",
           f"waiting due = expected+2bdays (got {waiting['due_date']})")

    rec = next((p for p in result["fresh"] if p["action_type"] == "reconciliation"), None)
    ck(rec is not None, "reconciliation proposal materialized")
    if rec:
        ck(rec["due_date"] == "2026-05-23", "reconciliation due today+2")

    thread = next((p for p in result["fresh"] if p["action_type"] == "commitment"), None)
    ck(thread is not None, "commitment proposal materialized")
    if thread:
        ck(thread["due_date"] == "2026-05-28",
           f"commitment due = thread.target_close (got {thread['due_date']})")
        ck(thread["entity"] == "test-person",
           "commitment entity drawn from thread.people")

    # Apply path with confirm=False must NOT write.
    apply_result = apply_proposals(synthetic_report, today=today, confirm=False)
    ck(apply_result.get("confirmed") is False,
       "apply with confirm=False reports confirmed=false")
    ck(apply_result.get("applied_count", 0) == 0,
       "apply with confirm=False writes no loops")
    ck(all(p.get("apply_status") == "not_confirmed" for p in apply_result["fresh"]),
       "every fresh proposal carries apply_status='not_confirmed' when not confirmed")

    # Dedupe check: synthesize a matching open loop and ensure proposal is deduped.
    def _one_existing():
        return [{
            "id": "L-2026-05-08-099",
            "party": "Simin",
            "description": (
                "Follow up with Simin on thread 'Re: Harri intro follow-up' — "
                "T-A thread reference"
            ),
            "target": date(2026, 5, 22),
            "opened": date(2026, 5, 8),
        }]
    _existing_open_loops = _one_existing  # type: ignore
    try:
        deduped_result = propose_loops(synthetic_report, today=today)
    finally:
        _existing_open_loops = real_existing  # type: ignore

    ck(deduped_result["counts"]["deduped"] >= 1,
       "matching open loop causes dedupe")
    matched = [p for p in deduped_result["deduped"]
               if p["action_type"] == "follow_up" and "Simin" in p["entity"]]
    ck(bool(matched), "Simin overdue follow_up gets routed to deduped bucket")
    if matched:
        ck(matched[0]["apply_detail"] and "L-2026-05-08-099" in matched[0]["apply_detail"],
           "deduped proposal carries the existing loop id")

    print(f"--- smart_loops smoke complete: {len(failures)} failure(s) ---")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
