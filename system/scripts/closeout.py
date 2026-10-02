#!/usr/bin/env python3
"""
closeout.py — end-of-day operating-layer closeout.

The morning brief opens the day. The closeout closes it. Composes six
buckets from canonical state so RB doesn't only create work — it also
reports what got done:

  * closed_today        loops where the close-line names today's date
  * slipped             open loops whose target was today or earlier and still
                        carry no closure evidence
  * waiting             sent_followups still inside their expected-response window
  * auto_resolved       passive_verification proposals (auto_closeable +
                        possible_resolution) — what RB can close for Todd
  * carry_forward       open loops with future targets, ranked by proximity
  * tomorrow_setup      one recommended setup move for tomorrow morning

The settings.json contract pins the target firing time at 16:30 local.
Wiring that to LaunchAgent is outside this module — closeout.py provides
the deterministic compute; the scheduler invokes `--write --confirm`.

Per the smoke-test-mutations memory, the smoke never reaches the write
path in non-dry-run mode.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core


CLOSEOUTS_DIR = core.SYSTEM_DIR / "closeouts"


# -----------------------------------------------------------------------------
# Closed-today detection
# -----------------------------------------------------------------------------

# The loop ledger uses freeform close notes ("**closed** — Phone screen
# captured 2026-05-15; ..."). When the close note contains a date string and
# that date is today, treat the loop as closed-today. This is heuristic but
# matches operator convention.
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _close_line_dates(status_raw: str) -> list[date]:
    out: list[date] = []
    for m in _DATE_RE.findall(status_raw or ""):
        try:
            out.append(date.fromisoformat(m))
        except ValueError:
            continue
    return out


def _closed_today_loops(today: date) -> list[dict]:
    """Loops whose close-note carries today's date (operator convention)."""
    out: list[dict] = []
    for L in core.parse_loop_ledger():
        if not L.closed:
            continue
        dates = _close_line_dates(L.status_raw)
        if today in dates:
            out.append({
                "id": L.id,
                "party": L.party,
                "description": L.description,
                "target": L.target.isoformat(),
                "close_note": L.status_raw,
            })
    return out


# -----------------------------------------------------------------------------
# Bucket composition
# -----------------------------------------------------------------------------

def _slipped(report: dict, today: date) -> list[dict]:
    loops = report.get("loops") or {}
    overdue = loops.get("overdue") or []
    due_today = loops.get("due_today") or []
    return [
        {
            "id": L.get("id"),
            "party": L.get("party"),
            "description": L.get("description"),
            "target": str(L.get("target")),
            "days_overdue": (today - date.fromisoformat(str(L.get("target")))).days
                            if L.get("target") else 0,
        }
        for L in (overdue + due_today)
    ]


def _waiting(report: dict) -> list[dict]:
    """Sent followups inside their expected-response window."""
    em = report.get("email") or {}
    out: list[dict] = []
    for sf in em.get("sent_followups") or []:
        if sf.get("response_status") != "awaiting_response":
            continue
        recipient = ""
        if sf.get("matched_contacts"):
            recipient = (sf["matched_contacts"][0] or {}).get("name") or ""
        if not recipient and (sf.get("to") or []):
            r0 = sf["to"][0] or {}
            recipient = r0.get("name") or r0.get("email") or "(recipient)"
        out.append({
            "recipient": recipient or "(recipient)",
            "subject": (sf.get("subject") or "")[:80],
            "sent_at": sf.get("sent_at"),
            "expected_response_by": sf.get("expected_response_by"),
            "business_days_since_sent": sf.get("business_days_since_sent"),
            "thread_id": sf.get("thread_id"),
        })
    return out


def _auto_resolved(report: dict, today: date) -> dict:
    """Read passive_verification proposals — same data the brief uses."""
    try:
        import passive_verification as pv  # type: ignore
        scan = pv.scan(report=report, today=today)
        return {
            "auto_closeable": scan.get("auto_closeable") or [],
            "possible_resolution": scan.get("possible_resolution") or [],
        }
    except Exception:  # noqa: BLE001
        return {"auto_closeable": [], "possible_resolution": []}


def _carry_forward(report: dict, today: date) -> list[dict]:
    """Open loops with future target dates — ranked by proximity."""
    loops = report.get("loops") or {}
    rows: list[dict] = []
    for bucket in ("this_week", "future"):
        for L in loops.get(bucket) or []:
            target_str = str(L.get("target"))
            try:
                target_d = date.fromisoformat(target_str)
            except ValueError:
                continue
            rows.append({
                "id": L.get("id"),
                "party": L.get("party"),
                "description": L.get("description"),
                "target": target_str,
                "days_until": (target_d - today).days,
            })
    rows.sort(key=lambda r: r["days_until"])
    return rows[:8]


def _tomorrow_setup(report: dict, today: date) -> Optional[dict]:
    """Pick one recommended setup move for tomorrow morning.

    Selection order (highest to lowest):
      1. Any meeting on tomorrow's calendar with a known attendee or
         active-thread tie — propose generating its prep brief tonight.
      2. The top high-boost active thread without a meeting tied to it —
         propose the next concrete move.
      3. Top relationship signal from the last 24h that wasn't acted on yet.

    Returns None when nothing crosses the bar.
    """
    cal = report.get("calendar") or {}
    tomorrow = today + timedelta(days=1)
    for ev in cal.get("tomorrow") or []:
        attendees = ev.get("attendees_matched") or []
        threads = ev.get("matched_threads") or []
        if any(a.get("id") for a in attendees) or threads or attendees:
            try:
                import meeting_prep as mp  # type: ignore
                artifact_rel = str(mp.artifact_path_for(ev).relative_to(core.PROJECT_DIR))
            except Exception:  # noqa: BLE001
                artifact_rel = None
            return {
                "type": "meeting_prep",
                "reason": (
                    f"Tomorrow's calendar has '{ev.get('title')}' "
                    f"at {ev.get('start','')[11:16]} — generate prep brief tonight."
                ),
                "event_id": ev.get("id"),
                "artifact_path": artifact_rel,
                "date": tomorrow.isoformat(),
            }

    high_threads = [
        t for t in report.get("active_threads") or []
        if t.get("boost_for_brief") == "high"
    ]
    if high_threads:
        t = high_threads[0]
        return {
            "type": "thread_move",
            "reason": (
                f"Top high-boost thread is '{t.get('title')}' — set a concrete "
                "next move before tomorrow's brief opens."
            ),
            "thread_id": t.get("id"),
            "target_close": str(t.get("target_close") or ""),
            "date": tomorrow.isoformat(),
        }

    rsr = report.get("relationship_signals") or {}
    high_signals = [
        s for s in rsr.get("signals") or []
        if s.get("strategic_relevance") == "high"
    ]
    if high_signals:
        s = high_signals[0]
        return {
            "type": "follow_through",
            "reason": (
                f"Highest unactioned signal: {s.get('name')} — "
                f"{s.get('recommended_action') or 'follow up'}."
            ),
            "entity": s.get("name"),
            "date": tomorrow.isoformat(),
        }
    return None


# -----------------------------------------------------------------------------
# Top-level builder
# -----------------------------------------------------------------------------

def build_closeout(today: Optional[date] = None,
                  report: Optional[dict] = None) -> dict:
    """Compose the structured closeout payload (six buckets + meta)."""
    today = today or date.today()
    if report is None:
        # Local import to avoid a cycle when daily_brief calls back into us.
        import daily_brief  # type: ignore
        report = daily_brief.build_report(today)

    closed_today = _closed_today_loops(today)
    slipped = _slipped(report, today)
    waiting = _waiting(report)
    auto_resolved = _auto_resolved(report, today)
    carry_forward = _carry_forward(report, today)
    tomorrow_setup = _tomorrow_setup(report, today)

    # RB-9.64: Goal completion tracking — advance weekly outcomes when linked loops close.
    # When a loop that maps to a weekly outcome is closed today, advance that outcome
    # from "active" → "advanced". This is the bridge between loop execution and
    # weekly goal tracking without requiring manual status updates.
    outcome_advancements: list[dict] = []
    try:
        import weekly_planning as wp  # type: ignore
        plan = wp.load_plan()
        if plan and wp.is_current(plan, today) and closed_today:
            mutated = False
            for outcome in plan.get("outcomes", []):
                if outcome.get("status") != "active":
                    continue
                # Match closed loops to this outcome by keyword overlap in title
                outcome_title_lower = outcome.get("title", "").lower()
                outcome_kw = set(outcome_title_lower.split())
                for loop in closed_today:
                    loop_title_lower = (loop.get("title") or loop.get("subject") or "").lower()
                    # Match if 2+ significant words overlap (skip stop words)
                    _stop = {"the", "a", "an", "and", "or", "for", "to", "of", "in", "with", "at"}
                    loop_kw = set(loop_title_lower.split()) - _stop
                    shared = (outcome_kw - _stop) & loop_kw
                    if len(shared) >= 2:
                        outcome["status"] = "advanced"
                        outcome["advanced_at"] = today.isoformat()
                        outcome["advanced_by_loop"] = loop.get("id") or loop.get("title", "")[:60]
                        outcome_advancements.append({
                            "outcome_id": outcome.get("outcome_id"),
                            "title": outcome.get("title"),
                            "loop": outcome["advanced_by_loop"],
                        })
                        mutated = True
                        break
            if mutated:
                wp.save_plan(plan)
    except Exception:  # noqa: BLE001
        pass

    counts = {
        "closed_today": len(closed_today),
        "slipped": len(slipped),
        "waiting": len(waiting),
        "auto_closeable": len(auto_resolved.get("auto_closeable") or []),
        "possible_resolution": len(auto_resolved.get("possible_resolution") or []),
        "carry_forward": len(carry_forward),
        "outcomes_advanced": len(outcome_advancements),  # RB-9.64
    }

    return {
        "contract": "closeout_v1",
        "today": today.isoformat(),
        "weekday": today.strftime("%A"),
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "target_time_local": "16:30",
        "counts": counts,
        "closed_today": closed_today,
        "slipped": slipped,
        "waiting": waiting,
        "auto_resolved": auto_resolved,
        "carry_forward": carry_forward,
        "tomorrow_setup": tomorrow_setup,
        "outcome_advancements": outcome_advancements,  # RB-9.64: loops → weekly goals
        "artifact_path": str((CLOSEOUTS_DIR / f"{today.isoformat()}.md").relative_to(core.PROJECT_DIR)),
        "source_refs": [
            "loop_ledger.md",
            "email_overlay.sent_followups",
            "passive_verification.scan",
            "active_threads.yaml",
            "calendar_overlay",
            "relationship_signals.signals",
        ],
    }


# -----------------------------------------------------------------------------
# Markdown rendering
# -----------------------------------------------------------------------------

def render_closeout_md(payload: dict) -> str:
    today = payload.get("today", "")
    weekday = payload.get("weekday", "")
    counts = payload.get("counts") or {}
    out: list[str] = []
    out.append(f"# Closeout — {weekday}, {today}\n")
    out.append(f"*Generated {payload.get('generated_at','')} (target time {payload.get('target_time_local','')})*\n")

    out.append("## Snapshot\n")
    out.append(
        f"- **Closed today:** {counts.get('closed_today', 0)}"
    )
    out.append(
        f"- **Slipped (target ≤ today, still open):** {counts.get('slipped', 0)}"
    )
    out.append(
        f"- **Waiting (inside response window):** {counts.get('waiting', 0)}"
    )
    out.append(
        f"- **Auto-closeable (evidence supports closure):** {counts.get('auto_closeable', 0)}"
    )
    out.append(
        f"- **Possible resolution (review recommended):** {counts.get('possible_resolution', 0)}"
    )
    out.append(
        f"- **Carry-forward (open, future target):** {counts.get('carry_forward', 0)}"
    )
    out.append("")

    closed = payload.get("closed_today") or []
    if closed:
        out.append("## Closed today\n")
        for L in closed:
            out.append(f"- **{L['id']}** — {L['party']}: {L['description'][:140]}")
        out.append("")

    slipped = payload.get("slipped") or []
    if slipped:
        out.append("## Slipped\n")
        for L in slipped:
            d = L.get("days_overdue") or 0
            label = "due today" if d == 0 else f"{d}d overdue"
            out.append(f"- **{L['id']}** — {L['party']} ({label}): {L['description'][:120]}")
        out.append("")

    waiting = payload.get("waiting") or []
    if waiting:
        out.append("## Waiting\n")
        for w in waiting[:8]:
            out.append(
                f"- {w['recipient']} — '{w['subject']}' "
                f"(sent {w['sent_at'][:10] if w.get('sent_at') else '—'}; "
                f"expected by {w.get('expected_response_by') or '—'})"
            )
        out.append("")

    ar = payload.get("auto_resolved") or {}
    auto_ok = ar.get("auto_closeable") or []
    if auto_ok:
        out.append("## Auto-closeable (RB can close these)\n")
        for p in auto_ok:
            out.append(f"- **{p['loop_id']}** — {p['party']}: {p['proposed_close_reason']}")
        out.append("")

    possible = ar.get("possible_resolution") or []
    if possible:
        out.append("## Possible resolution (review recommended)\n")
        for p in possible:
            out.append(f"- **{p['loop_id']}** — {p['party']}: {p['proposed_close_reason']}")
        out.append("")

    cf = payload.get("carry_forward") or []
    if cf:
        out.append("## Carry-forward\n")
        for L in cf:
            out.append(
                f"- **{L['id']}** — {L['party']} (target {L['target']}, in {L['days_until']}d): "
                f"{L['description'][:100]}"
            )
        out.append("")

    setup = payload.get("tomorrow_setup")
    if setup:
        out.append("## Tomorrow setup\n")
        out.append(f"- ({setup['type']}) {setup['reason']}")
        if setup.get("artifact_path"):
            out.append(f"  - target artifact: {setup['artifact_path']}")
        out.append("")
    else:
        out.append("## Tomorrow setup\n")
        out.append("- (no setup move proposed — quiet board into tomorrow)\n")

    out.append("---")
    out.append(f"*Sources: {', '.join(payload.get('source_refs') or [])}*")
    return "\n".join(out) + "\n"


# -----------------------------------------------------------------------------
# Write-back (snapshot + write)
# -----------------------------------------------------------------------------

def write_closeout(payload: dict, *, dry_run: bool = True) -> dict:
    md = render_closeout_md(payload)
    today_iso = payload.get("today", date.today().isoformat())
    target = CLOSEOUTS_DIR / f"{today_iso}.md"
    rel = str(target.relative_to(core.PROJECT_DIR))
    if dry_run:
        return {"ok": True, "dry_run": True, "path": rel, "bytes": len(md.encode("utf-8")), "preview": md}
    CLOSEOUTS_DIR.mkdir(parents=True, exist_ok=True)
    if target.exists():
        import shutil
        core.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        snap = core.SNAPSHOTS_DIR / (
            target.stem + f".pre-closeout-{datetime.now().strftime('%Y%m%d-%H%M%S')}" + target.suffix
        )
        shutil.copy2(target, snap)
    target.write_text(md, encoding="utf-8")
    return {"ok": True, "dry_run": False, "path": rel, "bytes": len(md.encode("utf-8"))}


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", help="ISO date for the closeout (default: today).")
    p.add_argument("--write", action="store_true",
                   help="Materialize the closeout artifact under system/closeouts/. Requires --confirm.")
    p.add_argument("--confirm", action="store_true",
                   help="Second-factor flag required with --write.")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of markdown.")
    p.add_argument("--smoke", action="store_true",
                   help="In-memory regression — no I/O, no mutations.")
    args = p.parse_args()

    if args.smoke:
        return _smoke()

    today = date.fromisoformat(args.date) if args.date else date.today()
    payload = build_closeout(today=today)
    if args.write:
        if not args.confirm:
            print("ERROR: --write requires --confirm.", file=sys.stderr)
            return 2
        result = write_closeout(payload, dry_run=False)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"wrote {result['path']} ({result['bytes']} bytes)")
        return 0

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(render_closeout_md(payload))
    return 0


# -----------------------------------------------------------------------------
# Smoke
# -----------------------------------------------------------------------------

def _smoke() -> int:
    """In-memory regression: synthetic report, no disk reads or writes."""
    failures: list[str] = []

    def ck(cond: bool, msg: str) -> None:
        mark = "OK" if cond else "FAIL"
        print(f"  {mark}   {msg}")
        if not cond:
            failures.append(msg)

    today = date(2026, 5, 21)

    # Stub the loop ledger reader so closed_today doesn't hit disk.
    from rb_core import Loop
    real_parse = core.parse_loop_ledger
    core.parse_loop_ledger = lambda *a, **kw: [  # type: ignore
        Loop(id="L-test-closed-1", opened=date(2026, 5, 18),
             party="Amy Spytko",
             description="Send the proposal draft.",
             target=date(2026, 5, 21),
             status_raw="**closed** — sent 2026-05-21; Amy confirmed receipt.",
             closed=True),
        Loop(id="L-test-closed-2", opened=date(2026, 5, 15),
             party="Other person",
             description="Old closure.",
             target=date(2026, 5, 17),
             status_raw="**closed** — 2026-05-20; resolved.",
             closed=True),
        Loop(id="L-test-open-1", opened=date(2026, 5, 17),
             party="Becky Cattie",
             description="Monitor response aging.",
             target=date(2026, 5, 26),
             status_raw="open", closed=False),
    ]

    # Synthetic report dict (mirrors what daily_brief.build_report would emit).
    synthetic_report = {
        "today": today.isoformat(),
        "active_threads": [
            {"id": "AT-x", "title": "Test thread", "boost_for_brief": "high",
             "target_close": "2026-06-01", "people": ["amy-spytko"]},
        ],
        "loops": {
            "overdue": [{
                "id": "L-test-overdue", "party": "Dave Richards",
                "description": "Send daily text.",
                "target": "2026-05-19",
            }],
            "due_today": [{
                "id": "L-test-due", "party": "Patrick Nelson",
                "description": "Schedule follow-up.",
                "target": today.isoformat(),
            }],
            "this_week": [{
                "id": "L-test-this-week", "party": "Bob Gibson",
                "description": "Toast watchlist.",
                "target": "2026-05-25",
            }],
            "future": [{
                "id": "L-test-future", "party": "Ed Gartner",
                "description": "Send outline.",
                "target": "2026-06-10",
            }],
        },
        "email": {
            "sent_followups": [
                {
                    "thread_id": "T-A", "subject": "Pending reply",
                    "sent_at": "2026-05-20T12:00:00+00:00",
                    "matched_contacts": [{"id": "amy-spytko", "name": "Amy Spytko"}],
                    "response_window_business_days": [3, 5],
                    "business_days_since_sent": 1,
                    "expected_response_by": "2026-05-27",
                    "response_status": "awaiting_response",
                },
                {
                    "thread_id": "T-B", "subject": "Already overdue",
                    "sent_at": "2026-05-10T12:00:00+00:00",
                    "matched_contacts": [{"id": "x", "name": "X"}],
                    "response_window_business_days": [3, 5],
                    "business_days_since_sent": 8,
                    "expected_response_by": "2026-05-15",
                    "response_status": "response_overdue",
                },
            ],
        },
        "calendar": {
            "today": [],
            "tomorrow": [
                {
                    "id": "ev-tom-1",
                    "title": "Strategic prep call",
                    "start": "2026-05-22T14:00:00-05:00",
                    "attendees_matched": [{"id": "amy-spytko", "name": "Amy Spytko"}],
                    "matched_threads": ["AT-x"],
                },
            ],
            "this_week": [],
        },
        "relationship_signals": {
            "signals": [
                {"name": "Ish Singh", "strategic_relevance": "high",
                 "signal_strength": "high",
                 "recommended_action": "Schedule Maho deep-dive."},
            ],
        },
        "interaction": {"matched_contacts": []},
    }

    try:
        payload = build_closeout(today=today, report=synthetic_report)
    finally:
        core.parse_loop_ledger = real_parse  # type: ignore

    counts = payload["counts"]
    ck(payload["contract"] == "closeout_v1", "payload carries contract version")
    ck(payload["today"] == today.isoformat(), "today passed through")
    ck(counts["closed_today"] == 1,
       f"one loop closed today (got {counts['closed_today']})")
    ck(payload["closed_today"][0]["id"] == "L-test-closed-1",
       "closed_today filtered to the 2026-05-21 close note")

    ck(counts["slipped"] == 2,
       f"slipped = overdue (1) + due_today (1) = 2 (got {counts['slipped']})")
    ck(payload["slipped"][0]["days_overdue"] >= 0,
       "slipped days_overdue computed")

    ck(counts["waiting"] == 1,
       f"only awaiting_response items counted in waiting (got {counts['waiting']})")
    ck(payload["waiting"][0]["recipient"] == "Amy Spytko",
       "waiting recipient name pulled from matched_contacts")

    ck(counts["carry_forward"] == 2,
       f"carry_forward = this_week (1) + future (1) (got {counts['carry_forward']})")
    ck(payload["carry_forward"][0]["days_until"] < payload["carry_forward"][1]["days_until"],
       "carry_forward sorted by proximity")

    setup = payload.get("tomorrow_setup")
    ck(setup and setup.get("type") == "meeting_prep",
       "tomorrow_setup picks the meeting prep when a meeting exists")
    ck(setup and "Strategic prep call" in (setup.get("reason") or ""),
       "tomorrow_setup names the meeting")

    # Render check
    md = render_closeout_md(payload)
    ck("# Closeout — Thursday, 2026-05-21" in md, "markdown title with weekday + date")
    ck("## Snapshot" in md, "snapshot section present")
    ck("## Closed today" in md, "closed today section present")
    ck("## Slipped" in md, "slipped section present")
    ck("## Waiting" in md, "waiting section present")
    ck("## Carry-forward" in md, "carry-forward section present")
    ck("## Tomorrow setup" in md, "tomorrow setup section present")
    ck("Amy Spytko" in md, "named contact appears in rendered closeout")

    # write_closeout in dry_run must not touch disk
    result = write_closeout(payload, dry_run=True)
    ck(result.get("dry_run") is True, "write_closeout dry_run flagged true")
    ck(result["path"].startswith("system/closeouts/"),
       f"closeout path is under system/closeouts/ (got {result['path']!r})")
    ck("preview" in result, "dry_run preview included")

    # Tomorrow setup fallback chain: no meetings → thread; no thread → signal
    no_meeting_report = dict(synthetic_report)
    no_meeting_report["calendar"] = {"today": [], "tomorrow": [], "this_week": []}
    core.parse_loop_ledger = lambda *a, **kw: []  # type: ignore
    try:
        payload2 = build_closeout(today=today, report=no_meeting_report)
    finally:
        core.parse_loop_ledger = real_parse  # type: ignore
    ck(payload2.get("tomorrow_setup", {}).get("type") == "thread_move",
       "tomorrow_setup falls back to thread_move when no meetings")

    no_thread_report = dict(no_meeting_report)
    no_thread_report["active_threads"] = []
    core.parse_loop_ledger = lambda *a, **kw: []  # type: ignore
    try:
        payload3 = build_closeout(today=today, report=no_thread_report)
    finally:
        core.parse_loop_ledger = real_parse  # type: ignore
    ck(payload3.get("tomorrow_setup", {}).get("type") == "follow_through",
       "tomorrow_setup falls back to follow_through when no meetings/threads")

    no_signal_report = dict(no_thread_report)
    no_signal_report["relationship_signals"] = {"signals": []}
    core.parse_loop_ledger = lambda *a, **kw: []  # type: ignore
    try:
        payload4 = build_closeout(today=today, report=no_signal_report)
    finally:
        core.parse_loop_ledger = real_parse  # type: ignore
    ck(payload4.get("tomorrow_setup") is None,
       "tomorrow_setup is None when nothing crosses the bar (no synthesis)")

    print(f"--- closeout smoke complete: {len(failures)} failure(s) ---")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
