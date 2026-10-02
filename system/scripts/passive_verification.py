#!/usr/bin/env python3
"""
passive_verification.py — auto-close open loops when evidence appears.

The "system makes you better" piece of the operating layer: open loops should
not require manual closing when canonical state already proves the work was
done. Examples:

  * Loop says "Send Amy the proposal." Email overlay shows an outbound to
    Amy after the loop opened → propose auto-closure.
  * Loop says "Follow up with Becky Cattie next week." A sent_followup
    matched to Becky in the email overlay → propose auto-closure.
  * Meeting-prep loop opened by smart_loops references an artifact path.
    The artifact exists AND the meeting start time has passed → propose
    auto-closure.

The module is read-only by default. Apply requires `--confirm` and goes
through `mutations.cmd_loop_close`, which already snapshots the ledger
before writing (P-009). Two confidence tiers:

  * `auto_closeable` — evidence directly matches the loop's stated intent.
    Safe to close on a single confirm.
  * `possible_resolution` — outbound activity to the loop's party after the
    loop opened, but the description doesn't name the action. Operator
    review recommended.

Per the smoke-test-mutations memory, the smoke never reaches the apply
branch in non-dry-run mode.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core


# Keyword groups that map a loop's description to an evidence-source bias.
DESCRIPTION_KEYWORDS = {
    "outbound_required": (
        "send", "reply", "follow up", "follow-up", "followup", "email ",
        "ping", "owe a text", "owe text", "owed text", "owed a text",
        " text ", "text him", "text her", "text them", "text back",
        "send text", "send a text", "reach back",
    ),
    "meeting_required": (
        "schedule", "meeting", "call with", "phone screen", "discovery call",
        "intro call", "kickoff", "interview",
    ),
    "outreach": (
        "reach out", "reconnect", "warm up", "check in", "touch base",
    ),
    "meeting_prep_signal": (
        "meeting prep", "prep brief", "artifact target", "talking points",
    ),
}


# -----------------------------------------------------------------------------
# Data model
# -----------------------------------------------------------------------------

@dataclass
class ClosureProposal:
    """A single proposed closure for an open loop, with named evidence."""
    loop_id: str
    party: str
    opened: str
    target: str
    description: str
    confidence: str            # "auto_closeable" | "possible_resolution"
    proposed_close_reason: str
    evidence: list[dict] = field(default_factory=list)
    matched_contact_id: Optional[str] = None
    detected_intent: Optional[str] = None
    apply_status: Optional[str] = None
    apply_detail: Optional[str] = None


# -----------------------------------------------------------------------------
# Loop intent + party matching
# -----------------------------------------------------------------------------

def _classify_intent(description: str) -> Optional[str]:
    """Return the strongest matched intent label (or None).

    Order matters — `meeting_prep_signal` wins over `outbound_required` so a
    meeting-prep loop opened by smart_loops doesn't get auto-closed by an
    unrelated email to the same party.
    """
    low = (description or "").lower()
    for key in ("meeting_prep_signal", "meeting_required", "outbound_required", "outreach"):
        for kw in DESCRIPTION_KEYWORDS[key]:
            if kw in low:
                return key
    return None


def _extract_artifact_path(description: str) -> Optional[str]:
    """Pull `system/meeting_briefs/...md` out of a description if present."""
    m = re.search(r"(system/meeting_briefs/[\w\-/]+\.md)", description or "")
    return m.group(1) if m else None


def _strip_party_qualifier(party: str) -> str:
    """`Mike Schwartz (Global Payments / Genius-Xenial)` -> `Mike Schwartz`.

    `Dave Miller (Franke), Bob Gibson, H&K Dallas/Franke ecosystem` -> first chunk.
    """
    if not party:
        return ""
    head = party.split(",", 1)[0]
    head = re.sub(r"\s*\(.*?\)\s*", " ", head).strip()
    head = re.sub(r"\s+/.+$", "", head).strip()
    return head


def _match_party_to_baseline(party: str, baseline: list[dict]) -> Optional[dict]:
    """Best-effort name → baseline-entry resolution.

    Substring match against canonical names; the canonical names are
    well-curated, so this is high-precision. Returns the first match.
    """
    if not party:
        return None
    head = _strip_party_qualifier(party).lower()
    if not head:
        return None
    by_name = {(e.get("name") or "").lower(): e for e in baseline}
    if head in by_name:
        return by_name[head]
    # Try first-name only when no exact hit.
    for name_low, entry in by_name.items():
        if head in name_low or name_low in head:
            return entry
    return None


# -----------------------------------------------------------------------------
# Evidence collection helpers (one per overlay)
# -----------------------------------------------------------------------------

def _parse_iso(d) -> Optional[date]:
    if d is None:
        return None
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    try:
        return date.fromisoformat(str(d)[:10])
    except (TypeError, ValueError):
        return None


def _evidence_from_sent_followups(party_contact_id: Optional[str],
                                  party_name: str, opened: date,
                                  email_payload: dict) -> list[dict]:
    """Return sent_followup rows that match the loop's party after `opened`."""
    out: list[dict] = []
    for sf in email_payload.get("sent_followups") or []:
        sent_at = _parse_iso(sf.get("sent_at"))
        if not sent_at or sent_at < opened:
            continue
        # Recipient matching: prefer matched_contacts.id; fall back to name in `to`
        match = False
        for mc in (sf.get("matched_contacts") or []):
            if party_contact_id and mc.get("id") == party_contact_id:
                match = True
                break
            if (mc.get("name") or "").lower() == party_name.lower():
                match = True
                break
        if not match and party_name:
            for r in sf.get("to") or []:
                rn = (r.get("name") or "").lower()
                if rn and (rn == party_name.lower() or party_name.lower() in rn):
                    match = True
                    break
        if match:
            out.append({
                "source": "email_overlay.sent_followups",
                "thread_id": sf.get("thread_id"),
                "subject": sf.get("subject"),
                "sent_at": sf.get("sent_at"),
                "ref": f"email_overlay.sent_followups.{sf.get('thread_id')}",
            })
    return out


def _evidence_from_linkedin_messaging(party_contact_id: Optional[str], opened: date,
                                     linkedin_payload: dict) -> list[dict]:
    """Outbound LinkedIn message(s) to the matched contact after the loop opened.

    Sourced from linkedin_messaging.outbound_evidence rows. Inbound LinkedIn
    is weaker (they wrote — Todd may not have replied), so it's not counted
    as outbound evidence here.
    """
    if not party_contact_id:
        return []
    out: list[dict] = []
    for ev in linkedin_payload.get("outbound_evidence") or []:
        if ev.get("contact_id") != party_contact_id:
            continue
        last_at_str = ev.get("last_outbound_at") or ""
        try:
            last_at = date.fromisoformat(last_at_str[:10]) if last_at_str else None
        except ValueError:
            last_at = None
        if last_at and last_at < opened:
            continue
        out.append({
            "source": "linkedin_messaging.outbound_evidence",
            "contact_id": ev.get("contact_id"),
            "name": ev.get("name"),
            "outbound_count": ev.get("outbound_count", 0),
            "last_outbound_at": ev.get("last_outbound_at"),
            "ref": ev.get("ref") or f"linkedin_messaging.matched_contacts.{ev.get('contact_id')}",
        })
        break
    return out


def _evidence_from_interaction(party_contact_id: Optional[str], opened: date,
                              interaction_payload: dict) -> list[dict]:
    """Outbound messages/calls to the matched contact after the loop opened."""
    if not party_contact_id:
        return []
    out: list[dict] = []
    for mc in interaction_payload.get("matched_contacts") or []:
        if mc.get("id") != party_contact_id:
            continue
        last_at = _parse_iso(mc.get("last_interaction_at"))
        if not last_at or last_at < opened:
            continue
        if (mc.get("messages_out") or 0) > 0 or (mc.get("calls_out") or 0) > 0:
            out.append({
                "source": "interaction_overlay.matched_contacts",
                "contact_id": mc.get("id"),
                "name": mc.get("name"),
                "messages_out": mc.get("messages_out", 0),
                "calls_out": mc.get("calls_out", 0),
                "last_interaction_at": mc.get("last_interaction_at"),
                "ref": f"interaction_overlay.matched_contacts.{mc.get('id')}",
            })
            break
    return out


def _evidence_from_email_inbound(party_contact_id: Optional[str], opened: date,
                                 email_payload: dict) -> list[dict]:
    """Inbound replies from the matched contact after the loop opened.

    Inbound evidence is weaker than outbound for closing a "send X" loop
    (they replied — but Todd may not have replied back). It is, however,
    strong evidence for a "schedule a call" loop or an "owed text" loop
    where bidirectional traffic itself satisfies the obligation.
    """
    if not party_contact_id:
        return []
    out: list[dict] = []
    for t in email_payload.get("from_baseline") or []:
        sender = t.get("sender") or {}
        if sender.get("contact_id") != party_contact_id and (sender.get("id") != party_contact_id):
            continue
        last_at = _parse_iso(t.get("last_message_at") or t.get("last_at"))
        if last_at and last_at < opened:
            continue
        out.append({
            "source": "email_overlay.from_baseline",
            "thread_id": t.get("thread_id") or t.get("id"),
            "subject": t.get("subject"),
            "last_message_at": t.get("last_message_at"),
            "ref": f"email_overlay.from_baseline.{t.get('thread_id') or t.get('id')}",
        })
    return out


def _evidence_from_calendar(party_contact_id: Optional[str], party_name: str,
                           opened: date, today: date, calendar_payload: dict) -> list[dict]:
    """Calendar events that have already started and include the party.

    Only buckets that contain past-start events are considered. "today" is
    included because an event at 09:00 today already happened by the 05:00
    brief tomorrow — we never auto-close from the morning-of, but evidence
    accrues throughout the day for the next run.
    """
    out: list[dict] = []
    now = datetime.now()
    for bucket in ("today", "tomorrow", "this_week"):
        for ev in calendar_payload.get(bucket) or []:
            start_str = ev.get("start") or ""
            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            # Normalize for compare (drop tz to avoid aware/naive mismatch).
            start_naive = start_dt.replace(tzinfo=None) if start_dt.tzinfo else start_dt
            if start_naive >= now:
                continue
            if start_naive.date() < opened:
                continue
            attendees = ev.get("attendees_matched") or []
            matched = False
            for a in attendees:
                if party_contact_id and a.get("id") == party_contact_id:
                    matched = True
                    break
                an = (a.get("name") or "").lower()
                if an and party_name and (party_name.lower() == an or party_name.lower() in an):
                    matched = True
                    break
            if matched:
                out.append({
                    "source": "calendar.past_event",
                    "event_id": ev.get("id"),
                    "title": ev.get("title"),
                    "start": start_str,
                    "ref": f"calendar.{ev.get('id') or ev.get('title')}",
                })
    return out


def _artifact_exists(artifact_rel: str) -> bool:
    """Resolve a `system/...` path against the project root and check existence."""
    if not artifact_rel:
        return False
    return (core.PROJECT_DIR / artifact_rel).exists()


# -----------------------------------------------------------------------------
# Per-loop verification
# -----------------------------------------------------------------------------

def verify_loop(loop, *, baseline: list[dict], today: date,
                email_payload: dict, interaction_payload: dict,
                calendar_payload: dict,
                linkedin_payload: Optional[dict] = None) -> Optional[ClosureProposal]:
    """Return a ClosureProposal if evidence supports closure, else None."""
    if loop.closed:
        return None

    intent = _classify_intent(loop.description)
    party_head = _strip_party_qualifier(loop.party)
    contact = _match_party_to_baseline(party_head, baseline)
    contact_id = (contact or {}).get("id")
    contact_name = (contact or {}).get("name") or party_head

    evidence: list[dict] = []
    confidence: Optional[str] = None
    reason_bits: list[str] = []

    # 0. Meeting-prep loops: check the artifact-path-and-meeting-passed rule.
    if intent == "meeting_prep_signal":
        artifact_rel = _extract_artifact_path(loop.description)
        # Find the meeting date. RB-DEFECT-2026-08-12: this used to require
        # the literal phrase "on YYYY-MM-DD" in the description, which only
        # matches smart_loops.py's own auto-generated meeting_prep template.
        # A loop created through a different path phrased it "Meeting
        # confirmed for Tuesday, 2026-08-11 at 1:00 PM Central" instead --
        # no match, so meeting_passed silently stayed False forever, even
        # with the prep artifact on disk and the meeting long over. Confirmed
        # live: L-2026-08-10-001 (Jeff Coffland) kept re-escalating as
        # "overdue" a full day after the meeting happened, was recorded via
        # Just Press Record, and had a full intelligence rollup written.
        #
        # Prefer the artifact filename's own YYYY-MM-DD prefix -- a
        # structured, reliable source per meeting_prep.py's naming
        # convention (system/meeting_briefs/YYYY-MM-DD-<slug>.md) -- and
        # only fall back to scanning the free-text description (which
        # varies by loop-creation path) when there's no artifact path to
        # anchor on.
        meeting_passed = False
        meeting_day = None
        if artifact_rel:
            fname_match = re.search(r"(\d{4}-\d{2}-\d{2})", Path(artifact_rel).name)
            if fname_match:
                try:
                    meeting_day = date.fromisoformat(fname_match.group(1))
                except ValueError:
                    meeting_day = None
        if meeting_day is None:
            m = re.search(r"\b(?:on|for)\b[^0-9]{0,20}(\d{4}-\d{2}-\d{2})", loop.description or "")
            if m:
                try:
                    meeting_day = date.fromisoformat(m.group(1))
                except ValueError:
                    meeting_day = None
        if meeting_day is not None:
            meeting_passed = meeting_day <= today
        if artifact_rel and _artifact_exists(artifact_rel) and meeting_passed:
            evidence.append({
                "source": "meeting_briefs.artifact",
                "path": artifact_rel,
                "exists": True,
                "ref": f"meeting_briefs.{artifact_rel}",
            })
            confidence = "auto_closeable"
            reason_bits.append(
                f"prep artifact {artifact_rel} exists and meeting start has passed"
            )

    # 1. Outbound evidence from sent_followups + interaction overlay + LinkedIn outbound.
    sf_evidence = _evidence_from_sent_followups(
        contact_id, contact_name, _parse_iso(loop.opened) or today,
        email_payload,
    )
    int_evidence = _evidence_from_interaction(
        contact_id, _parse_iso(loop.opened) or today, interaction_payload,
    )
    cal_evidence = _evidence_from_calendar(
        contact_id, contact_name, _parse_iso(loop.opened) or today,
        today, calendar_payload,
    )
    li_evidence = _evidence_from_linkedin_messaging(
        contact_id, _parse_iso(loop.opened) or today, linkedin_payload or {},
    )

    if intent == "outbound_required" and (sf_evidence or int_evidence or li_evidence):
        evidence.extend(sf_evidence + int_evidence + li_evidence)
        confidence = confidence or "auto_closeable"
        if sf_evidence:
            reason_bits.append(
                f"sent followup to {contact_name} ({sf_evidence[0]['sent_at'][:10] if sf_evidence[0].get('sent_at') else 'recent'})"
            )
        if int_evidence:
            reason_bits.append(
                f"outbound messages/calls to {contact_name} "
                f"({int_evidence[0]['last_interaction_at'][:10] if int_evidence[0].get('last_interaction_at') else 'recent'})"
            )
        if li_evidence:
            reason_bits.append(
                f"outbound LinkedIn to {contact_name} "
                f"({li_evidence[0]['last_outbound_at'][:10] if li_evidence[0].get('last_outbound_at') else 'recent'})"
            )
    elif intent == "meeting_required" and cal_evidence:
        evidence.extend(cal_evidence)
        confidence = confidence or "auto_closeable"
        reason_bits.append(
            f"calendar event '{cal_evidence[0]['title']}' on {cal_evidence[0]['start'][:10]} has passed"
        )
    elif intent == "outreach" and (sf_evidence or int_evidence or cal_evidence or li_evidence):
        evidence.extend(sf_evidence + int_evidence + cal_evidence + li_evidence)
        confidence = confidence or "auto_closeable"
        reason_bits.append(
            f"outreach evidence observed: "
            f"{len(sf_evidence)} email(s), {len(int_evidence)} interaction set(s), "
            f"{len(cal_evidence)} calendar event(s)"
        )
    else:
        # No description-keyword intent — but if there's outbound evidence to
        # a matched contact after open, flag as possible_resolution so the
        # operator can review.
        any_outbound = sf_evidence + int_evidence + li_evidence
        if any_outbound and contact_id:
            evidence.extend(any_outbound[:3])
            confidence = "possible_resolution"
            reason_bits.append(
                f"outbound activity to {contact_name} after {loop.opened.isoformat()} "
                "(no description keyword match — review recommended)"
            )

    if not evidence:
        return None

    reason = "; ".join(reason_bits) or "evidence observed"
    full_reason = f"Auto-verified: {reason}."
    return ClosureProposal(
        loop_id=loop.id,
        party=loop.party,
        opened=loop.opened.isoformat(),
        target=loop.target.isoformat(),
        description=loop.description,
        confidence=confidence,
        proposed_close_reason=full_reason,
        evidence=evidence,
        matched_contact_id=contact_id,
        detected_intent=intent,
    )


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def scan(report: Optional[dict] = None, *, today: Optional[date] = None,
         baseline: Optional[list[dict]] = None) -> dict:
    """Scan all open loops against report overlays for closure evidence.

    Pass a `report` dict (from daily_brief.build_report) to reuse cached
    overlays; otherwise the function reads each overlay from disk.
    """
    today = today or date.today()
    if baseline is None:
        baseline = core.load_baseline()
    if report is None:
        email_payload = core.email_overlay(baseline=baseline)
        interaction_payload = core.interaction_overlay(baseline=baseline, today=today)
        calendar_payload = core.calendar_overlay(today=today, baseline=baseline)
        try:
            import linkedin_messaging as _lim  # type: ignore
            linkedin_payload = _lim.linkedin_message_overlay(baseline=baseline, today=today)
        except Exception:  # noqa: BLE001
            linkedin_payload = {}
    else:
        email_payload = report.get("email") or {}
        interaction_payload = report.get("interaction") or {}
        calendar_payload = report.get("calendar") or {}
        linkedin_payload = report.get("linkedin_messaging") or {}

    loops = [L for L in core.parse_loop_ledger() if not L.closed]
    proposals: list[ClosureProposal] = []
    for L in loops:
        p = verify_loop(
            L,
            baseline=baseline, today=today,
            email_payload=email_payload,
            interaction_payload=interaction_payload,
            calendar_payload=calendar_payload,
            linkedin_payload=linkedin_payload,
        )
        if p:
            proposals.append(p)

    auto = [p for p in proposals if p.confidence == "auto_closeable"]
    possible = [p for p in proposals if p.confidence == "possible_resolution"]

    return {
        "today": today.isoformat(),
        "open_loops_scanned": len(loops),
        "counts": {
            "auto_closeable": len(auto),
            "possible_resolution": len(possible),
            "total": len(proposals),
        },
        "auto_closeable": [p.__dict__ for p in auto],
        "possible_resolution": [p.__dict__ for p in possible],
        "contract": "passive_verification_v1",
    }


def apply(report: Optional[dict] = None, *, today: Optional[date] = None,
          baseline: Optional[list[dict]] = None,
          only_ids: Optional[list[str]] = None,
          include_possible: bool = False,
          confirm: bool = False) -> dict:
    """Close auto_closeable proposals (and optionally possible_resolution).

    Mirrors smart_loops.apply_proposals: confirm=False returns the scan
    payload with no mutations; confirm=True goes through
    mutations.cmd_loop_close per proposal.
    """
    scan_result = scan(report=report, today=today, baseline=baseline)
    targets = list(scan_result["auto_closeable"])
    if include_possible:
        targets += scan_result["possible_resolution"]

    if not confirm:
        for p in targets:
            p["apply_status"] = "not_confirmed"
            p["apply_detail"] = (
                "Pass --apply --confirm (CLI) or {confirm:true} (API) to actually close these loops."
            )
        scan_result["confirmed"] = False
        scan_result["applied_count"] = 0
        scan_result["targets"] = targets
        return scan_result

    import mutations  # local import: read-only callers never touch mutations

    applied = 0
    errors: list[str] = []
    for p in targets:
        if only_ids and p["loop_id"] not in only_ids:
            p["apply_status"] = "skipped_not_in_ids"
            continue
        try:
            ns = _ns_for_loop_close(p)
            rc = mutations.cmd_loop_close(ns)
            if rc != 0:
                p["apply_status"] = "error:loop_close_nonzero"
                p["apply_detail"] = f"mutations.cmd_loop_close returned {rc}"
                errors.append(p["loop_id"])
            else:
                p["apply_status"] = "closed"
                p["apply_detail"] = f"closed with reason: {p['proposed_close_reason']}"
                applied += 1
        except Exception as exc:  # noqa: BLE001
            p["apply_status"] = f"error:{type(exc).__name__}"
            p["apply_detail"] = str(exc)
            errors.append(p["loop_id"])

    scan_result["confirmed"] = True
    scan_result["applied_count"] = applied
    scan_result["errors"] = errors
    scan_result["targets"] = targets
    return scan_result


def _ns_for_loop_close(p: dict):
    class Ns:
        pass
    ns = Ns()
    ns.id = p["loop_id"]
    ns.reason = p["proposed_close_reason"]
    ns.dry_run = False
    return ns


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", help="ISO date for scan (default: today).")
    p.add_argument("--apply", action="store_true",
                   help="Close auto_closeable proposals (and possible_resolution if --include-possible). Requires --confirm.")
    p.add_argument("--confirm", action="store_true",
                   help="Second-factor flag required with --apply.")
    p.add_argument("--include-possible", action="store_true",
                   help="Include possible_resolution proposals in --apply targets.")
    p.add_argument("--ids", nargs="*", default=None,
                   help="Restrict --apply to these loop_ids.")
    p.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    p.add_argument("--smoke", action="store_true",
                   help="In-memory regression — no I/O, no mutations.")
    args = p.parse_args()

    if args.smoke:
        return _smoke()

    today = date.fromisoformat(args.date) if args.date else date.today()
    if args.apply:
        if not args.confirm:
            print("ERROR: --apply requires --confirm.", file=sys.stderr)
            return 2
        result = apply(today=today, include_possible=args.include_possible,
                       only_ids=args.ids, confirm=True)
    else:
        result = scan(today=today)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    counts = result["counts"]
    print(f"passive_verification for {result.get('today')}: "
          f"{result.get('open_loops_scanned',0)} open loops scanned; "
          f"{counts['auto_closeable']} auto-closeable, "
          f"{counts['possible_resolution']} possible resolution.")
    for p in result.get("auto_closeable", []):
        print(f"  auto  {p['loop_id']:24s} {p['party'][:40]:40s}  {p['proposed_close_reason'][:80]}")
    for p in result.get("possible_resolution", []):
        print(f"  poss  {p['loop_id']:24s} {p['party'][:40]:40s}  {p['proposed_close_reason'][:80]}")
    if result.get("confirmed"):
        print(f"applied: {result.get('applied_count',0)}")
    elif not args.apply:
        print("(no mutations — pass --apply --confirm to close these loops)")
    return 0


# -----------------------------------------------------------------------------
# Smoke test
# -----------------------------------------------------------------------------

def _smoke() -> int:
    """In-memory regression. Synthesizes loops + overlays so the smoke is pure.

    No disk reads, no mutations. Per the smoke-test-mutations memory: the
    apply() branch is only exercised with confirm=False.
    """
    failures: list[str] = []

    def ck(cond: bool, msg: str) -> None:
        mark = "OK" if cond else "FAIL"
        print(f"  {mark}   {msg}")
        if not cond:
            failures.append(msg)

    baseline = [
        {"id": "amy-spytko", "name": "Amy Spytko", "signal_class": "RC", "rc_tier": "inner"},
        {"id": "dave-richards", "name": "Dave Richards", "signal_class": "RC", "rc_tier": "inner"},
        {"id": "becky-cattie", "name": "Becky Cattie", "signal_class": "LKI", "rc_tier": None},
        {"id": "patrick-nelson", "name": "Patrick Nelson", "signal_class": "RC", "rc_tier": "broader"},
    ]
    today = date(2026, 5, 21)
    from rb_core import Loop

    # Fixture loops (mirrors current ledger shapes — outbound, meeting,
    # outreach, meeting_prep, unrelated).
    loops = [
        Loop(id="L-test-001", opened=date(2026, 5, 18),
             party="Amy Spytko", description="Send Amy the proposal draft.",
             target=date(2026, 5, 25), status_raw="open", closed=False),
        Loop(id="L-test-002", opened=date(2026, 5, 15),
             party="Patrick Nelson (Matrix)",
             description="Schedule follow-up call next week (May 18-22).",
             target=date(2026, 5, 22), status_raw="open", closed=False),
        Loop(id="L-test-003", opened=date(2026, 5, 12),
             party="Dave Richards",
             description="Owed text. Daily-cadence inner.",
             target=date(2026, 5, 15), status_raw="open", closed=False),
        Loop(id="L-test-004", opened=date(2026, 5, 19),
             party="Becky Cattie",
             description="Monitor Coates Group response aging. No chase.",
             target=date(2026, 5, 26), status_raw="open", closed=False),
        Loop(id="L-test-005", opened=date(2026, 5, 21),
             party="Ashwin Rajput and Todd Vahlsing",
             description=(
                 "Meeting prep — Ashwin Rajput on 2026-05-21 with Ashwin Rajput. "
                 "Closure: Prep brief for 'Ashwin Rajput and Todd Vahlsing' on "
                 "2026-05-21 reviewed; talking points + expected outcome + likely "
                 "follow-ups identified. Artifact target: system/meeting_briefs/"
                 "2026-05-21-ashwin-rajput-and-todd-vahlsing-test.md."
             ),
             target=date(2026, 5, 21), status_raw="open", closed=False),
    ]

    # Stub overlays — Amy got an email sent_followup, Patrick has a past calendar event,
    # Dave got outbound texts, Becky got nothing (monitor-only loop), meeting prep
    # artifact does not exist on disk.
    email_payload = {
        "sent_followups": [
            {
                "thread_id": "T-amy-1",
                "subject": "Proposal draft attached",
                "sent_at": "2026-05-19T10:00:00+00:00",
                "to": [{"name": "Amy Spytko", "email": "amy@example.com"}],
                "matched_contacts": [{"id": "amy-spytko", "name": "Amy Spytko"}],
            },
        ],
        "from_baseline": [],
    }
    interaction_payload = {
        "matched_contacts": [
            {
                "id": "dave-richards", "name": "Dave Richards",
                "messages_out": 2, "messages_in": 1,
                "calls_out": 0, "calls_in": 0,
                "last_interaction_at": "2026-05-19T19:00:00",
            },
        ],
    }
    # Far-past event so naive comparison is stable.
    calendar_payload = {
        "today": [],
        "tomorrow": [],
        "this_week": [
            {
                "id": "ev-pat",
                "title": "Patrick Nelson follow-up call",
                "start": "2026-05-20T14:00:00",
                "attendees_matched": [
                    {"id": "patrick-nelson", "name": "Patrick Nelson"},
                ],
            },
        ],
    }

    # Monkey-patch parse_loop_ledger to return the fixture.
    real_parse = core.parse_loop_ledger
    core.parse_loop_ledger = lambda *a, **kw: loops  # type: ignore

    try:
        result = scan(
            report={
                "email": email_payload,
                "interaction": interaction_payload,
                "calendar": calendar_payload,
            },
            today=today, baseline=baseline,
        )
    finally:
        core.parse_loop_ledger = real_parse  # type: ignore

    counts = result["counts"]
    ck(result["open_loops_scanned"] == 5,
       f"five open loops scanned (got {result['open_loops_scanned']})")
    ck(counts["auto_closeable"] >= 3,
       f"three auto-closeable (Amy/Patrick/Dave), got {counts['auto_closeable']}")
    ids_auto = {p["loop_id"] for p in result["auto_closeable"]}
    ck("L-test-001" in ids_auto, "Amy (outbound) loop is auto-closeable")
    ck("L-test-002" in ids_auto, "Patrick (meeting) loop is auto-closeable")
    ck("L-test-003" in ids_auto, "Dave (owed text → outbound texts) loop is auto-closeable")
    ck("L-test-004" not in ids_auto,
       "Becky (monitor-only) loop is NOT auto-closeable (no false positive)")
    ck("L-test-005" not in ids_auto,
       "Meeting-prep loop is NOT auto-closeable when artifact does not exist on disk")

    # Spot-check reason content
    amy = next(p for p in result["auto_closeable"] if p["loop_id"] == "L-test-001")
    ck("sent followup to Amy" in amy["proposed_close_reason"],
       f"Amy reason names the sent followup (got: {amy['proposed_close_reason']!r})")
    patrick = next(p for p in result["auto_closeable"] if p["loop_id"] == "L-test-002")
    ck("calendar event" in patrick["proposed_close_reason"],
       "Patrick reason names the past calendar event")
    dave = next(p for p in result["auto_closeable"] if p["loop_id"] == "L-test-003")
    ck("outbound messages" in dave["proposed_close_reason"],
       "Dave reason names outbound messages")
    ck(amy.get("matched_contact_id") == "amy-spytko",
       "Amy proposal carries matched_contact_id")

    # Apply confirm=False must not mutate (no real ledger here anyway, but
    # the gate is checked).
    apply_dry = apply(
        report={
            "email": email_payload,
            "interaction": interaction_payload,
            "calendar": calendar_payload,
        },
        today=today, baseline=baseline, confirm=False,
    )
    # parse_loop_ledger inside apply() runs through scan(); patch again.
    core.parse_loop_ledger = lambda *a, **kw: loops  # type: ignore
    try:
        apply_dry = apply(
            report={
                "email": email_payload,
                "interaction": interaction_payload,
                "calendar": calendar_payload,
            },
            today=today, baseline=baseline, confirm=False,
        )
    finally:
        core.parse_loop_ledger = real_parse  # type: ignore
    ck(apply_dry.get("confirmed") is False, "apply with confirm=False reports confirmed=false")
    ck(apply_dry.get("applied_count", 0) == 0, "apply with confirm=False applies nothing")
    ck(all(t.get("apply_status") == "not_confirmed" for t in apply_dry.get("targets", [])),
       "every target carries apply_status='not_confirmed' under confirm=false")

    # Helper unit checks
    ck(_classify_intent("Send Amy the proposal.") == "outbound_required",
       "intent classifier: send -> outbound_required")
    ck(_classify_intent("Schedule follow-up call next week.") == "meeting_required",
       "intent classifier: schedule -> meeting_required")
    ck(_classify_intent("Reach out to reconnect.") == "outreach",
       "intent classifier: reach out -> outreach")
    ck(_classify_intent("Meeting prep brief artifact target X.") == "meeting_prep_signal",
       "intent classifier: meeting prep signal takes precedence")
    ck(_classify_intent("Nothing actionable here") is None,
       "intent classifier: no keyword -> None")

    ck(_strip_party_qualifier("Mike Schwartz (Global Payments / Genius-Xenial)") == "Mike Schwartz",
       "party qualifier strips parens")
    ck(_strip_party_qualifier("Dave Miller (Franke), Bob Gibson, H&K") == "Dave Miller",
       "party qualifier takes first chunk")

    print(f"--- passive_verification smoke complete: {len(failures)} failure(s) ---")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
