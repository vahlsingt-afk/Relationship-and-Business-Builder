#!/usr/bin/env python3
"""
interaction_overlay.py — overlay phone/text events against baseline.

Reads system/inbox/messages.json + system/inbox/calls.json (typically
produced by fetch_apple_messages.py and fetch_apple_calls.py) and
matches handles against baseline by phone (normalized) or email.

Outputs:
  - Per-contact interaction summary (messages_in/out, calls_in/out/missed,
    last interaction timestamp)
  - Proposed `last_touch` updates inferred from real events
  - Unmatched recurring handles (3+ events from a number not in baseline)

Usage:
    python3 interaction_overlay.py
    python3 interaction_overlay.py --json
    python3 interaction_overlay.py --cache
    python3 interaction_overlay.py --apply-last-touch  # actually mutate baseline
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
    p.add_argument("--date", help="ISO date (default: system date).")
    p.add_argument("--recent-days", type=int, default=30, dest="recent_days")
    p.add_argument("--json", action="store_true")
    p.add_argument("--cache", action="store_true")
    p.add_argument("--apply-last-touch", action="store_true",
                   help="Apply the proposed last_touch updates via mutations.touch. "
                        "Requires confirmation per contact.")
    args = p.parse_args()
    today = date.fromisoformat(args.date) if args.date else date.today()
    overlay = core.interaction_overlay(today=today, recent_days=args.recent_days)

    if args.cache:
        core.write_cache("interaction_overlay", overlay, source="interaction_overlay.py")

    if args.json:
        print(json.dumps(overlay, indent=2, default=str))
        return 0

    if not overlay.get("fetched_at"):
        print(f"No messages.json or calls.json present in {core.INBOX_DIR.relative_to(core.PROJECT_DIR)}.")
        print("Run fetch_apple_messages.py and fetch_apple_calls.py first (Mac only).")
        return 0

    totals = overlay["totals"]
    print(f"Interaction overlay (fetched {overlay['fetched_at']}, stale={overlay['stale']})")
    print(f"  {totals['messages_in_window']} message events, "
          f"{totals['calls_in_window']} call events in window.")
    print(f"  {totals['matched_count']} contact(s) matched, "
          f"{totals['unmatched_count']} events from unmatched handles.")

    if overlay["matched_contacts"]:
        print(f"\n## MATCHED CONTACTS — direct interaction in last {args.recent_days}d")
        print(f"  {'Contact':<28} {'Class/Tier':<14} {'Msgs(in/out)':<14} {'Calls(in/out/x)':<16} {'Last':<11}")
        for c in overlay["matched_contacts"][:15]:
            tier = c.get("rc_tier") or "-"
            print(f"  {c['name'][:28]:<28} "
                  f"{c['signal_class']}/{tier:<10} "
                  f"{c['messages_in']}/{c['messages_out']:<10} "
                  f"{c['calls_in']}/{c['calls_out']}/{c['calls_missed']:<10} "
                  f"{(c.get('last_interaction_at') or '')[:10]}")

    if overlay["proposed_last_touch_updates"]:
        print(f"\n## PROPOSED last_touch UPDATES ({len(overlay['proposed_last_touch_updates'])})")
        for u in overlay["proposed_last_touch_updates"][:20]:
            gap = u.get("gap_days_since_current")
            gap_label = f"{gap}d stale" if gap else "no prior last_touch"
            print(f"  {u['name']:<28} {u.get('current_last_touch') or '(none)':<12} → "
                  f"{u['proposed_last_touch']:<12} ({gap_label})  evidence: {u['evidence']}")

    if overlay["unmatched_recurring_handles"]:
        print(f"\n## UNMATCHED RECURRING HANDLES (3+ events, not in baseline)")
        for u in overlay["unmatched_recurring_handles"][:15]:
            print(f"  {u['events']:>4}x  {u['handle']:<22} last={(u.get('last_at') or '')[:10]}  "
                  f"services={','.join(u['services'])}")

    if args.apply_last_touch:
        if not overlay["proposed_last_touch_updates"]:
            print("\nNo updates to apply.")
            return 0
        print(f"\nApplying {len(overlay['proposed_last_touch_updates'])} last_touch updates...")
        import mutations
        applied = 0
        for u in overlay["proposed_last_touch_updates"]:
            ns = type("Ns", (), {
                "id": u["contact_id"],
                "date": u["proposed_last_touch"],
                "source": "apple_interaction_overlay",
                "dry_run": False,
            })()
            rc = mutations.cmd_touch(ns)
            if rc == 0:
                applied += 1
        print(f"Applied {applied}/{len(overlay['proposed_last_touch_updates'])} updates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
