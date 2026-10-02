#!/usr/bin/env python3
"""
calendar_overlay.py — match `system/inbox/calendar.json` against baseline.

Produces a structured overlay listing today's / tomorrow's / this-week's
events with the matched baseline entries per attendee. The daily brief
consumes this; you can also call it standalone.

Usage:
    python3 calendar_overlay.py             # text report
    python3 calendar_overlay.py --json
    python3 calendar_overlay.py --date 2026-05-15
    python3 calendar_overlay.py --cache
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="ISO date for 'today' (default: system date)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--cache", action="store_true",
                   help="Write to system/.cache/calendar_overlay.json")
    args = p.parse_args()
    today = date.fromisoformat(args.date) if args.date else date.today()
    overlay = core.calendar_overlay(today)
    if args.cache:
        core.write_cache("calendar_overlay", overlay, source="calendar_overlay.py")
    if args.json:
        print(json.dumps(overlay, indent=2, default=str))
        return 0

    if not overlay.get("fetched_at"):
        print("No calendar data found in system/inbox/.")
        print("Run a fetcher first (see system/inbox/README.md).")
        return 0
    accts = overlay.get("accounts_seen") or []
    readiness = overlay.get("source_readiness") or {}
    print(f"Calendar overlay for {today} (fetched {overlay['fetched_at']}, "
          f"stale={overlay['stale']}, accounts={','.join(accts)})")
    if readiness.get("status") != "ready":
        missing = ",".join(readiness.get("missing_accounts") or [])
        print(f"Source readiness: {readiness.get('status')} "
              f"(coverage={readiness.get('coverage')}, missing={missing or '-'})")
        print(readiness.get("guidance") or "Refresh or connect missing calendar feeds before over-trusting empty results.")
    for bucket in ("today", "tomorrow", "this_week"):
        events = overlay[bucket]
        if not events:
            continue
        print(f"\n## {bucket.replace('_',' ').upper()} ({len(events)})")
        for ev in events:
            time = (ev.get("start") or "")[11:16]
            acct = f"[{ev.get('account_id','-')}]"
            print(f"  {time} {acct}  {ev['title']}")
            for a in ev["attendees_matched"]:
                if a["id"]:
                    tags = (f" [active threads: {','.join(a['matched_threads'])}]"
                            if a["matched_threads"] else "")
                    print(f"    ✓ {a['name']} ({a['signal_class']}/{a['rc_tier'] or '-'}) — {a['email']}{tags}")
                else:
                    print(f"    · {a['email']} — no baseline match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
