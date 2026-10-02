#!/usr/bin/env python3
"""
loop_parser.py — parse loop_ledger.md into structured records.

Reads the markdown table in `system/loop_ledger.md` and outputs JSON or a
bucketed text report (overdue / due today / this week / future / closed)
for a given `today` date.

Usage:
    python3 loop_parser.py                       # bucketed text report for today
    python3 loop_parser.py --date 2026-05-15
    python3 loop_parser.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="ISO date for 'today' (default: system date)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--cache", action="store_true",
                   help="Write to system/.cache/loop_parser.json")
    args = p.parse_args()
    today = date.fromisoformat(args.date) if args.date else date.today()
    loops = core.parse_loop_ledger()
    buckets = core.loops_by_status(loops, today)

    payload = {
        "today": today.isoformat(),
        "buckets": {
            k: [
                {
                    **asdict(L),
                    "opened": L.opened.isoformat(),
                    "target": L.target.isoformat(),
                }
                for L in v
            ]
            for k, v in buckets.items()
        },
        "totals": {k: len(v) for k, v in buckets.items()},
    }
    if args.cache:
        core.write_cache("loop_parser", payload, source="loop_parser.py")

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0

    print(f"Loop report for {today} ({today.strftime('%A')})")
    print(f"  overdue:   {len(buckets['overdue'])}")
    print(f"  due_today: {len(buckets['due_today'])}")
    print(f"  this_week: {len(buckets['this_week'])}")
    print(f"  future:    {len(buckets['future'])}")
    print(f"  closed:    {len(buckets['closed'])}")
    print()
    for bucket in ("overdue", "due_today", "this_week"):
        if not buckets[bucket]:
            continue
        print(f"## {bucket.replace('_', ' ').upper()}")
        for L in buckets[bucket]:
            print(f"  {L.target} | {L.id} | {L.party}")
            print(f"            {L.description[:120]}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
