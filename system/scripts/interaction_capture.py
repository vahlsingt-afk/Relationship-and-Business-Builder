#!/usr/bin/env python3
"""
interaction_capture.py — durable, content-free interaction facts.

`email_overlay()`/`calendar_overlay()` match raw email/calendar content
against baseline. That raw content is classified `source_cache` in
`retention_policy.py` — a deliberate 72h "refresh/debug" window, not
permanent storage. Once it rolls over, the fact that an interaction
happened is otherwise lost.

This module distills each matched interaction into a durable `ri_events`
fact — contact id + date + channel only, **no subject/body/description** —
so "who did I email/meet with N months ago" stays answerable after the raw
cache expires, without retaining message content indefinitely. This is the
privacy-consistent alternative to extending raw email/calendar retention.

CLI:
    python3 interaction_capture.py [--dry-run]
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import ri_events  # noqa: E402


def _emit_interaction_event(contact_id: str, contact_name: str | None, *, event_at: str, channel: str, source_id: str | None) -> ri_events.AppendResult:
    event = {
        "event_at": event_at,
        "event_at_confidence": "high",
        "source": {
            "type": "passive_signal",
            "id": f"interaction_capture:{channel}:{contact_id}:{source_id or 'unknown'}:{event_at}",
        },
        "entities": {
            "people": [{"id": contact_id, "raw": contact_name, "decision": "matched_existing", "matched_id": contact_id}],
            "companies": [],
        },
        "dedupe": {"decision": "new_event"},
        "signal": {"type": "interaction_occurred", "channel": channel},
        "persistence": {"status": "persisted"},
    }
    return ri_events.append(event)


def capture_email_interactions(
    *,
    baseline: list[dict] | None = None,
    threads: list[dict] | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Distill email_overlay()'s from_baseline threads into durable facts.

    from_baseline is not date-windowed by calendar_overlay's today/tomorrow/
    this_week convention — it already covers the entire fetched window, so
    no additional widening is needed here.
    """
    overlay = core.email_overlay(baseline=baseline, threads=threads)
    captured: list[dict] = []
    for row in overlay.get("from_baseline") or []:
        match = row.get("match") or {}
        cid = match.get("id")
        # last_message_at is not guaranteed ISO 8601 — Gmail thread lists
        # return RFC 2822 ("Tue, 16 Jun 2026 17:12:04 -0500") when fetched
        # without full message expansion. Naive string-slicing silently
        # produces garbage dates for that format.
        event_at = core.parse_message_date(row.get("last_message_at"))
        if not cid or not event_at:
            continue
        captured.append({
            "contact_id": cid, "name": match.get("name"),
            "event_at": event_at, "channel": "email", "source_id": row.get("thread_id"),
        })
    if not dry_run:
        for c in captured:
            _emit_interaction_event(c["contact_id"], c["name"], event_at=c["event_at"], channel="email", source_id=c["source_id"])
    return captured


def capture_calendar_interactions(
    *,
    baseline: list[dict] | None = None,
    threads: list[dict] | None = None,
    today: date | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Distill calendar_overlay()'s all_with_baseline_match into durable facts.

    Uses all_with_baseline_match (every matched event in the fetched window,
    past or future), not the today/tomorrow/this_week display buckets, so
    past meetings are captured before the raw source_cache expires.
    """
    today = today or date.today()
    overlay = core.calendar_overlay(today, baseline=baseline, threads=threads)
    captured: list[dict] = []
    for row in overlay.get("all_with_baseline_match") or []:
        event_at = core.parse_message_date(row.get("start"))
        if not event_at:
            continue
        for m in row.get("attendees_matched") or []:
            cid = m.get("id")
            if not cid:
                continue
            captured.append({
                "contact_id": cid, "name": m.get("name"),
                "event_at": event_at, "channel": "calendar", "source_id": row.get("id"),
            })
    if not dry_run:
        for c in captured:
            _emit_interaction_event(c["contact_id"], c["name"], event_at=c["event_at"], channel="calendar", source_id=c["source_id"])
    return captured


def interactions_for_contact(contact_id: str, *, since: str | None = None) -> list[dict]:
    """Durable interaction history for one contact — answers "who/when did I
    last interact with X" after the raw email/calendar cache has expired."""
    events = ri_events.load_events(entity_id=contact_id, since=since)
    return [e for e in events if (e.get("signal") or {}).get("type") == "interaction_occurred"]


def most_recent_interaction_by_contact(*, since: str | None = None) -> dict[str, str]:
    """One pass over ri_events, not one call per contact — callers scoring
    hundreds/thousands of contacts (e.g. campaign_engine's recent_engagement
    dimension) should use this instead of interactions_for_contact() in a
    loop. Returns {contact_id: most_recent_event_at_iso_date}."""
    events = ri_events.load_events(source_type="passive_signal", since=since)
    latest: dict[str, str] = {}
    for e in events:
        if (e.get("signal") or {}).get("type") != "interaction_occurred":
            continue
        event_at = e.get("event_at") or ""
        for p in (e.get("entities") or {}).get("people") or []:
            cid = p.get("matched_id") or p.get("id")
            if cid and event_at > latest.get(cid, ""):
                latest[cid] = event_at
    return latest


def capture_all(*, dry_run: bool = False) -> dict[str, Any]:
    baseline = core.load_baseline()
    threads = [t for t in core.load_active_threads() if t.get("status") == "open"]
    email_events = capture_email_interactions(baseline=baseline, threads=threads, dry_run=dry_run)
    calendar_events = capture_calendar_interactions(baseline=baseline, threads=threads, dry_run=dry_run)
    return {
        "ok": True,
        "email_facts_captured": len(email_events),
        "calendar_facts_captured": len(calendar_events),
        "dry_run": dry_run,
    }


def main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Capture durable, content-free interaction facts from email/calendar overlays")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = capture_all(dry_run=args.dry_run)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
