#!/usr/bin/env python3
"""
loop_state.py -- RB-DEFECT-074: self-updating execution-loop state.

Why this exists. Processed intelligence (captures, executive declarations,
Outlook metadata) never reached the live execution loops:

  * eolms.match_and_transition() only ever searched EOLMS (EL-) records. The
    loops Todd actually works from are legacy L- rows in loop_ledger.md, which
    were never candidates, so every declaration came back `no_match`.
  * Its intent lexicon had no vocabulary for the commonest real updates
    ("met with", "emailed", "no response", "requested"), so most declarations
    were `no completion/advance/block language` before matching even started.
  * A `no_match` was still wrapped in an `ok`/`action_recorded` receipt that
    read like a success.
  * The ledger row has only description/target/status columns, so the only way
    to change "what is the current action / who are we waiting on" was to
    append prose via redateLoop -- leaving obsolete text in place and the loop
    date-only bucketed as overdue.

This module adds:
  - a structured per-loop state overlay (system/loop_state.json),
  - update_loop_state(): the narrow write path behind `updateLoopState`,
  - reconcile_text()/reconcile_capture(): pipeline-triggered, idempotent,
    provenance-backed transitions over legacy L- loops,
  - nonresponse tracking + a review-first strategy proposal,
  - reconcile_weekly_plan(): refresh linked-outcome progress and expose a stale
    week_of instead of presenting old prose as the current state.

Safety posture is unchanged: a completion closes ONLY a loop whose own
obligation that evidence satisfies; a contact touch never closes an
introduction or approves an initiative; ambiguous evidence becomes a review
item, never a guess.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core  # noqa: E402

STATE_SCHEMA_VERSION = "1.0"

ALLOWED_STATES = (
    "active", "waiting", "awaiting_response", "awaiting_internal_review",
    "waiting_internal", "in_progress", "parked", "monitoring", "blocked",
)

_UNSET: Any = object()


# -----------------------------------------------------------------------------
# Storage
# -----------------------------------------------------------------------------

def _path() -> Path:
    return core.LOOP_STATE_PATH


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_doc() -> dict:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw.setdefault("schema_version", STATE_SCHEMA_VERSION)
    if not isinstance(raw.get("loops"), dict):
        raw["loops"] = {}
    if not isinstance(raw.get("review_queue"), list):
        raw["review_queue"] = []
    return raw


def _save_doc(doc: dict) -> None:
    doc["updated_at"] = _now()
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".loop_state.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _find_loop(loop_id: str) -> core.Loop | None:
    return next((l for l in core.parse_loop_ledger() if l.id == loop_id), None)


# -----------------------------------------------------------------------------
# updateLoopState
# -----------------------------------------------------------------------------

def _valid_checkpoint(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError(f"next_checkpoint must be YYYY-MM-DD or null, got {value!r}") from exc


def strategy_for(entry: dict) -> dict | None:
    """Review-first disposition proposal for a loop with repeated unanswered
    outreach. Never auto-applied; never recommends another chase."""
    attempts = int(entry.get("outreach_attempts") or 0)
    if attempts < 2 or entry.get("state") not in ("awaiting_response", "waiting", "parked"):
        return None
    return {
        "type": "disposition_proposal",
        "review_first": True,
        "suppress_chase": True,
        "basis": f"{attempts} unanswered outreach attempt(s)"
                 + (f"; last {entry['last_interaction']}" if entry.get("last_interaction") else ""),
        "options": [
            "Validate relevance/access through a sponsored path (e.g. the relationship manager) before any further direct outreach",
            "Change the value proposition or trigger before re-approaching",
            "Park or stop: no sponsored path and no concrete trigger",
        ],
        "not_recommended": "another direct follow-up of the same kind",
    }


def update_loop_state(
    loop_id: str,
    *,
    current_action: Any = _UNSET,
    state: Any = _UNSET,
    waiting_on: Any = _UNSET,
    next_checkpoint: Any = _UNSET,
    source_evidence: list[str] | None = None,
    outreach_attempts: Any = _UNSET,
    last_interaction: Any = _UNSET,
    note: str | None = None,
    actor: str = "updateLoopState",
) -> dict:
    """Set the structured current state of an OPEN legacy L- loop.

    Raises ValueError (-> HTTP 400/422) on unknown/closed loop, invalid state,
    bad date, missing provenance, or an empty update. `next_checkpoint=None`
    explicitly means "date unknown" and clears any prior checkpoint -- it is
    never replaced with an invented date.
    Returns changed_fields separately from no-ops; an update that changes
    nothing reports status="no_change" and writes nothing.
    """
    if not re.fullmatch(r"L-\d{4}-\d{2}-\d{2}-\d{3}", loop_id or ""):
        raise ValueError(f"{loop_id!r} is not a legacy L- loop id (format L-YYYY-MM-DD-NNN)")
    loop = _find_loop(loop_id)
    if loop is None:
        raise ValueError(f"loop {loop_id} not found in loop_ledger.md")
    if loop.closed:
        raise ValueError(f"loop {loop_id} is already closed; state updates apply to open loops only")
    evidence = [str(e).strip() for e in (source_evidence or []) if str(e).strip()]
    if not evidence:
        raise ValueError("source_evidence is required: at least one stable evidence id/reference "
                         "(capture id, interaction id, email/thread id, or 'todd:<date>')")

    requested: dict[str, Any] = {}
    if current_action is not _UNSET:
        requested["current_action"] = (current_action or "").strip() or None
    if state is not _UNSET:
        if state not in ALLOWED_STATES:
            raise ValueError(f"state must be one of {', '.join(ALLOWED_STATES)}; got {state!r}")
        requested["state"] = state
    if waiting_on is not _UNSET:
        requested["waiting_on"] = (waiting_on or "").strip() or None
    if next_checkpoint is not _UNSET:
        requested["next_checkpoint"] = _valid_checkpoint(next_checkpoint)
    if outreach_attempts is not _UNSET:
        n = int(outreach_attempts)
        if n < 0:
            raise ValueError("outreach_attempts must be >= 0")
        requested["outreach_attempts"] = n
    if last_interaction is not _UNSET:
        requested["last_interaction"] = _valid_checkpoint(last_interaction)
    if not requested:
        raise ValueError("no state fields supplied; pass at least one of current_action, state, "
                         "waiting_on, next_checkpoint, outreach_attempts, last_interaction")

    doc = _load_doc()
    entry = doc["loops"].setdefault(loop_id, {"loop_id": loop_id, "history": [], "evidence_ids": []})
    changed = {}
    for k, v in requested.items():
        if entry.get(k) != v:
            changed[k] = {"from": entry.get(k), "to": v}
    new_evidence = [e for e in evidence if e not in entry.get("evidence_ids", [])]
    if not changed and not new_evidence:
        return {"status": "no_change", "mutation_applied": False, "loop_id": loop_id,
                "changed_fields": {}, "state": entry}
    for k, v in requested.items():
        entry[k] = v
    entry["evidence_ids"] = entry.get("evidence_ids", []) + new_evidence
    entry["updated_at"] = _now()
    entry.setdefault("history", []).append({
        "at": entry["updated_at"], "actor": actor, "changed": changed,
        "evidence": evidence, "note": note,
    })
    strategy = strategy_for(entry)
    if strategy:
        entry["strategy"] = strategy
    else:
        entry.pop("strategy", None)
    _save_doc(doc)
    return {"status": "updated", "mutation_applied": bool(changed), "loop_id": loop_id,
            "changed_fields": changed, "new_evidence": new_evidence, "state": entry}


# -----------------------------------------------------------------------------
# Declaration / evidence reconciliation (legacy L- loops)
# -----------------------------------------------------------------------------

_STOP = {
    "the", "a", "an", "and", "or", "to", "of", "for", "on", "in", "is", "are", "was", "were",
    "it", "its", "i", "we", "he", "she", "they", "with", "at", "by", "from", "that", "this",
    "has", "have", "had", "be", "been", "not", "just", "now", "today", "will", "would", "can",
    "up", "out", "me", "my", "our", "us", "so", "as", "if", "then", "than", "into", "his",
    "her", "him", "them", "also", "per",
}
_TOK = re.compile(r"[a-z0-9']+")
_NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "a": 1, "an": 1}

# Order matters: nonresponse and block are checked before completion so that
# "emails sent, no response" can never be read as a completion.
_NONRESPONSE = ("no response", "no reply", "not responded", "hasn't responded", "has not responded",
                "haven't heard", "unanswered", "no answer", "not heard back", "without a response",
                "without response")
_BLOCK = ("blocked", "stuck", "delayed", "paused", "pushed back", "pushed out")
_COMPLETE = ("meeting occurred", "meeting happened", "meeting took place", "had the meeting",
             "met on", "thank-you sent", "thank you sent", "thank-you note sent", "resolved",
             "fixed", "completed", "finished", "closed out", "is done", "are done", "all done", "introduced me", "introduced us",
             "made the intro", "made the introduction", "intro was made", "introduction was made")
_PROGRESS = ("emailed", "e-mailed", "sent", "met", "called", "spoke", "floated", "requested",
             "asked for", "started", "drafting", "proforma", "pro forma", "linkedin", "replied",
             "responded", "confirmed", "heard back", "updated", "forwarded", "shared",
             "moving forward", "moved forward")

_RECEIPT_WORDS = ("received", "introduced", "made the intro", "intro made", "made an intro",
                  "connected me", "connected us", "introduction was made", "got the intro")
_APPROVAL_WORDS = ("approved", "approval granted", "signed off", "green light", "greenlit")


def classify_intent(text: str) -> str | None:
    tl = text.lower()
    for name, phrases in (("nonresponse", _NONRESPONSE), ("block", _BLOCK),
                          ("complete", _COMPLETE), ("progress", _PROGRESS)):
        if any(re.search(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])", tl) for p in phrases):
            return name
    return None


def _tokens(text: str) -> set[str]:
    return {t for t in _TOK.findall(text.lower()) if t not in _STOP and len(t) > 2}


_ORG_WORDS = {"guys", "burger", "burgers", "chicken", "pizza", "foods", "food", "inc", "llc", "group",
              "labs", "bank", "pay", "payments", "systems", "software", "technologies", "solutions",
              "brands", "restaurants", "restaurant", "kitchen", "grill", "coffee", "taco", "audit",
              "self", "global", "genius", "worldpay", "partners", "holdings", "capital", "corp"}


def _people(party: str) -> list[list[str]]:
    """Person name token lists from a ledger 'Person/Company' cell.
    'Josh Wesolowski / McDonald's' -> [['josh','wesolowski']]; arrows split."""
    people = []
    for chunk in re.split(r"\s*(?:→|->|/|;)\s*", re.sub(r"\([^)]*\)", " ", party)):
        words = [w for w in _TOK.findall(chunk.lower()) if w not in _STOP]
        # Ledger person cells are "First Last"; a lone capitalised word is an
        # org/brand ("McDonald's", "Worldpay"), not someone to match a meeting on.
        if 2 <= len(words) <= 3 and chunk.strip()[:1].isalpha() and not (set(words) & _ORG_WORDS):
            people.append(words)
    return people


def obligation_kind(loop: core.Loop) -> str:
    """What kind of completion evidence this loop's own obligation requires."""
    d = loop.description.lower()
    head = d[:260]
    if "self-audit findings:" in d:
        return "self_audit"
    if "approval gate" in head or head.startswith("**internal") or "approval" in head[:120]:
        return "approval"
    if re.search(r"\b(receive|receipt of)\b", head) or "introduction" in head[:160] and "waiting on" in d:
        return "await_receipt"
    return "action"


def _first_name_unique(first: str, loops: list[core.Loop], me: str) -> bool:
    for l in loops:
        if l.id == me or l.closed:
            continue
        for p in _people(l.party):
            if p and p[0] == first:
                return False
    return True


def _topic_tokens(loop: core.Loop, open_loops: list[core.Loop]) -> set[str]:
    """Rare descriptive tokens that identify a loop with no person in its party
    cell (e.g. a pricing initiative). Rare = appears in no other open loop."""
    def head(l: core.Loop) -> set[str]:
        return {t for t in _tokens(l.party + " " + l.description[:300]) if len(t) >= 6}
    mine = head(loop)
    others = set().union(*[head(l) for l in open_loops if l.id != loop.id]) if len(open_loops) > 1 else set()
    return mine - others


def _person_hit(loop: core.Loop, text_tokens: set[str], open_loops: list[core.Loop],
                allow_topic: bool = False) -> int:
    """0 = nothing identifying present; 2 = full name; 1 = unique first name or
    >=2 rare topic tokens (touch-level only -- never sufficient to close)."""
    best = 0
    # Topic path: only for short, declaration-sized evidence. In a 3,000-word
    # transcript, common descriptive words match everything (confirmed on the
    # 2026-10-07 recording: four unrelated loops) -- long text must name a person.
    if (allow_topic and obligation_kind(loop) != "self_audit"
            and len(_topic_tokens(loop, open_loops) & text_tokens) >= 3):
        best = 1
    for p in _people(loop.party):
        if all(t in text_tokens for t in p):
            best = max(best, 2)
        elif p and p[0] in text_tokens and _first_name_unique(p[0], open_loops, loop.id):
            best = max(best, 1)
    return best


def _parse_count(text: str) -> int:
    tl = text.lower()
    total = 0
    for m in re.finditer(r"\b(\d+|one|two|three|four|five)\s+(?:follow-?up\s+)?(emails?|e-mails?|messages?|calls?|texts?|voicemails?|linkedin\s+\w+)", tl):
        n = m.group(1)
        total += int(n) if n.isdigit() else _NUMWORDS.get(n, 1)
    if re.search(r"\blinkedin (response|reply|message|dm)\b", tl) and "linkedin" not in " ".join(
            m.group(2) for m in re.finditer(r"\b(\d+|one|two|three)\s+(linkedin\s+\w+)", tl)):
        total += 1
    return total or 1


def _queue_review(doc: dict, item: dict) -> dict:
    """Idempotent review-queue insert keyed by (loop_id, evidence_id, kind)."""
    key = (item["loop_id"], item.get("evidence_id"), item["kind"])
    for existing in doc["review_queue"]:
        if (existing.get("loop_id"), existing.get("evidence_id"), existing.get("kind")) == key:
            return existing
    item = {"id": f"lr-{len(doc['review_queue']) + 1:04d}", "status": "open",
            "created_at": _now(), **item}
    doc["review_queue"].append(item)
    return item


def _close_in_ledger(loop_id: str, reason: str) -> bool:
    import mutations  # local import: mutations imports eolms, which imports rb_core
    rc = mutations.cmd_loop_close(SimpleNamespace(id=loop_id, reason=reason, dry_run=False))
    return rc == 0


def reconcile_text(
    text: str,
    *,
    evidence_id: str | None = None,
    source: str = "executive_declaration",
    event_date: str | None = None,
    evidence_kind: str | None = None,
    metadata_only: bool = False,
    apply: bool = True,
) -> dict:
    """Reconcile narrated/processed evidence against open legacy L- loops.

    Returns a result whose `mutation_applied` is True ONLY if a ledger loop
    actually changed (closed, or state/evidence persisted). `no_match` and
    review-only outcomes are reported separately and are never successes.

    evidence_kind='meeting_occurred' (a processed recording / calendar-backed
    meeting) is treated as completion evidence for scheduling-type
    obligations only. metadata_only evidence (Outlook subject lines) records
    a touch and can never close anything.
    """
    out: dict = {"status": "no_match", "mutation_applied": False, "applied": [],
                 "review_items": [], "rejected": [], "evidence_id": evidence_id}
    intent = "complete" if evidence_kind == "meeting_occurred" else classify_intent(text or "")
    if intent is None and not metadata_only:
        out["reason"] = "no completion/progress/block/nonresponse language detected"
        return out
    if intent is None:
        intent = "progress"
    out["intent"] = intent

    open_loops = [l for l in core.parse_loop_ledger() if not l.closed]
    text_tokens = _tokens(text)
    short_text = len((text or "").split()) <= 80 and evidence_kind != "meeting_occurred"
    hits = [(l, _person_hit(l, text_tokens, open_loops, allow_topic=short_text)) for l in open_loops]
    hits = [(l, h) for l, h in hits if h > 0]
    if not hits:
        out["reason"] = "no open legacy loop names a person/org present in the evidence"
        return out

    when = event_date or date.today().isoformat()
    doc = _load_doc()
    ev = evidence_id or "text:" + hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:12]
    dirty = False

    # Eligible-for-completion set is computed BEFORE ambiguity: an introduction
    # awaiting receipt or an approval gate is simply not satisfied by "sent"/
    # "met", so it must not compete with the loop the evidence really closes.
    tl = text.lower()
    eligible_complete = []
    if intent == "complete" and not metadata_only:
        for l, h in hits:
            kind = obligation_kind(l)
            desc = l.description.lower()
            if kind == "self_audit":
                continue
            if kind == "await_receipt" and not any(w in tl for w in _RECEIPT_WORDS):
                continue
            if kind == "approval" and not any(w in tl for w in _APPROVAL_WORDS):
                continue
            if evidence_kind == "meeting_occurred" and not re.search(
                    r"\b(meeting|schedul|find time|set up|connect)\b", desc[:400]):
                continue
            eligible_complete.append((l, h))

    for l, h in hits:
        entry = doc["loops"].setdefault(l.id, {"loop_id": l.id, "history": [], "evidence_ids": []})
        if ev in entry.get("evidence_ids", []):
            out["rejected"].append({"loop_id": l.id, "reason": "evidence already applied (idempotent)"})
            continue
        closable = (l, h) in eligible_complete

        if intent == "complete" and closable and len(eligible_complete) == 1 and not metadata_only:
            reason = (f"Closed from {source} evidence {ev} ({when}): original obligation satisfied. "
                      f"Evidence: {text.strip()[:160]}")
            if apply:
                if not _close_in_ledger(l.id, reason):
                    out["rejected"].append({"loop_id": l.id, "reason": "ledger close refused"})
                    continue
                entry.update({"state": "closed", "closed_at": _now(), "last_interaction": when})
                entry["evidence_ids"].append(ev)
                entry["history"].append({"at": _now(), "actor": source, "changed": {"state": "closed"},
                                         "evidence": [ev], "note": reason})
                dirty = True
            out["applied"].append({"loop_id": l.id, "transition": "closed", "provenance": ev})
            continue

        if intent == "complete" and len(eligible_complete) > 1 and closable:
            item = _queue_review(doc, {
                "loop_id": l.id, "kind": "ambiguous_completion", "evidence_id": ev,
                "summary": f"Completion evidence could satisfy {len(eligible_complete)} loops; not auto-closed.",
                "candidates": [x.id for x, _h in eligible_complete], "text": text.strip()[:240],
            })
            out["review_items"].append(item)
            dirty = True
            continue

        # Touch / progress / nonresponse / block / un-satisfied completion.
        if apply:
            entry["last_interaction"] = when
            entry["evidence_ids"].append(ev)
            changed = {"last_interaction": when}
            if intent == "nonresponse":
                entry["outreach_attempts"] = int(entry.get("outreach_attempts") or 0) + _parse_count(text)
                entry["state"] = "awaiting_response"
                changed.update({"state": "awaiting_response",
                                "outreach_attempts": entry["outreach_attempts"]})
                strat = strategy_for(entry)
                if strat:
                    entry["strategy"] = strat
                    item = _queue_review(doc, {
                        "loop_id": l.id, "kind": "disposition_proposal", "evidence_id": ev,
                        "summary": strat["basis"], "options": strat["options"], "text": text.strip()[:240],
                    })
                    out["review_items"].append(item)
            else:
                suggested = {"complete": "unchanged -- completion evidence did not satisfy this loop's own obligation",
                             "block": "blocked", "progress": "state/next action needs confirmation"}[intent]
                item = _queue_review(doc, {
                    "loop_id": l.id, "kind": f"{intent}_signal", "evidence_id": ev,
                    "summary": f"{source} evidence touches this loop ({intent}); suggested: {suggested}.",
                    "text": text.strip()[:240],
                    "metadata_only": bool(metadata_only),
                })
                out["review_items"].append(item)
            entry["history"].append({"at": _now(), "actor": source, "changed": changed,
                                     "evidence": [ev], "note": f"{intent} (touch recorded; no loop closed)"})
            entry["updated_at"] = _now()
            dirty = True
        out["applied"].append({"loop_id": l.id, "transition": f"touch_recorded:{intent}", "provenance": ev})

    if dirty:
        _save_doc(doc)
    closed_or_state = [a for a in out["applied"]]
    out["mutation_applied"] = bool(closed_or_state) and apply
    if not apply:
        out["status"] = "dry_run"
    elif out["applied"]:
        out["status"] = "applied" if any(a["transition"] == "closed" for a in out["applied"]) else "recorded"
    elif out["review_items"]:
        out["status"] = "review"
    elif out["rejected"]:
        out["status"] = "already_applied"
    return out


# -----------------------------------------------------------------------------
# Capture + weekly-plan reconciliation
# -----------------------------------------------------------------------------

def reconcile_capture(capture: dict, *, apply: bool = True) -> dict:
    """Run reconciliation for one processed capture record (dict as written to
    system/captures/processed/*.json). A recording that names a loop's person
    is `meeting_occurred` evidence; transcripts are never metadata-only."""
    transcript = capture.get("transcript") or ""
    if not transcript.strip():
        return {"status": "no_match", "mutation_applied": False, "reason": "no transcript"}
    when = (capture.get("queued_at") or "")[:10] or None
    return reconcile_text(
        transcript, evidence_id=f"capture:{capture.get('file_id')}", source="capture_transcript",
        event_date=when, evidence_kind="meeting_occurred", apply=apply,
    )


def plan_health(plan: dict | None, today: date | None = None) -> dict:
    import weekly_planning as wp
    today = today or date.today()
    if not plan:
        return {"stale": True, "reason": "no weekly plan on disk", "week_of": None}
    current = wp._week_of(today)
    ids = [o.get("id") for o in plan.get("outcomes", [])]
    dupes = sorted({i for i in ids if i and ids.count(i) > 1})
    return {
        "week_of": plan.get("week_of"), "current_week_of": current,
        "stale": plan.get("week_of") != current,
        "duplicate_outcome_ids": dupes,
        "unresolved_reconciliation": sum(1 for r in _load_doc()["review_queue"] if r.get("status") == "open"),
    }


def reconcile_weekly_plan(plan_path: Path | None = None, *, today: date | None = None) -> dict:
    """Refresh linked-loop progress on each outcome and report staleness.

    Only derived fields (`loop_progress`, status when ALL linked loops closed)
    are written; titles/criteria are never rewritten. Stale week_of is
    reported, not silently rolled forward -- regenerating the plan is a
    review decision.
    """
    import weekly_planning as wp
    plan_path = plan_path or wp.WEEKLY_PLAN_PATH
    plan = wp.load_plan(plan_path)
    health = plan_health(plan, today)
    if not plan:
        return {"status": "no_plan", "health": health}
    loops = {l.id: l for l in core.parse_loop_ledger()}
    changed = []
    for o in plan.get("outcomes", []):
        ids = o.get("linked_loop_ids") or []
        if not ids:
            continue
        known = [loops[i] for i in ids if i in loops]
        closed = [l.id for l in known if l.closed]
        prog = {"total": len(ids), "closed": len(closed), "closed_ids": closed,
                "open_ids": [l.id for l in known if not l.closed],
                "unknown_ids": [i for i in ids if i not in loops]}
        if o.get("loop_progress") != prog:
            o["loop_progress"] = prog
            changed.append(o.get("id"))
        if known and len(closed) == len(known) and o.get("status") == "active":
            o["status"] = "closed"
            if o.get("id") not in changed:
                changed.append(o.get("id"))
    if changed:
        plan["reconciled_at"] = _now()
        wp.save_plan(plan, plan_path)
    return {"status": "reconciled" if changed else "no_change", "changed_outcomes": changed,
            "health": health}


def read_state(loop_id: str | None = None) -> dict:
    doc = _load_doc()
    if loop_id:
        return doc["loops"].get(loop_id) or {}
    return doc


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("reconcile-capture")
    c.add_argument("capture_id")
    c.add_argument("--dry-run", action="store_true")
    sub.add_parser("reconcile-plan")
    sub.add_parser("show")
    args = ap.parse_args(argv)
    if args.cmd == "reconcile-capture":
        p = core.SYSTEM_DIR / "captures" / "processed" / f"{args.capture_id}.json"
        res = reconcile_capture(json.loads(p.read_text(encoding="utf-8")), apply=not args.dry_run)
    elif args.cmd == "reconcile-plan":
        res = reconcile_weekly_plan()
    else:
        res = read_state()
    print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
