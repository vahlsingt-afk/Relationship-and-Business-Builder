#!/usr/bin/env python3
"""
social_overlay.py — match `system/inbox/social.feed.json` against baseline + threads.

Usage:
    python3 social_overlay.py             # text report
    python3 social_overlay.py --json
    python3 social_overlay.py --cache     # write system/.cache/social_overlay.json
    python3 social_overlay.py --recent-days 7
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    p.add_argument("--cache", action="store_true")
    p.add_argument("--recent-days", type=int, default=14, dest="recent_days")
    args = p.parse_args()
    overlay = core.social_overlay(recent_days=args.recent_days)
    if args.cache:
        core.write_cache("social_overlay", overlay, source="social_overlay.py")
    if args.json:
        print(json.dumps(overlay, indent=2, default=str))
        return 0
    if not overlay.get("fetched_at"):
        print(f"No social feed data found at {core.SOCIAL_FEED_PATH.relative_to(core.PROJECT_DIR)}.")
        print("Capture some posts first (see system/inbox/README.md).")
        return 0
    print(f"Social overlay (fetched {overlay['fetched_at']}, source={overlay.get('source')}, "
          f"stale={overlay['stale']})")
    if overlay["from_baseline"]:
        print(f"\n## POSTS FROM BASELINE CONTACTS ({len(overlay['from_baseline'])})")
        for p in overlay["from_baseline"][:20]:
            tier = p["match"].get("rc_tier") or "-"
            tag = (" [thread: " + ",".join(p["matched_threads"]) + "]") if p.get("matched_threads") else ""
            print(f"  {(p.get('posted_at') or '')[:10]} {p['match']['name']} ({p['match']['signal_class']}/{tier}){tag}")
            print(f"     ❝ {p['text'][:140]}…")
    if overlay["active_thread_company_hits"]:
        print(f"\n## ACTIVE-THREAD COMPANY HITS — author not in baseline ({len(overlay['active_thread_company_hits'])})")
        for p in overlay["active_thread_company_hits"][:10]:
            print(f"  {(p.get('posted_at') or '')[:10]} {p['author_name']} — companies: {','.join(p['active_thread_company_hits'])}")
            print(f"     ❝ {p['text'][:140]}…")
    if overlay["topic_signal"]:
        print(f"\n## TOPIC SIGNAL — companies in 2+ posts in the window")
        for t in overlay["topic_signal"]:
            print(f"  {t['post_count']}x  {t['company']}")
    if overlay["authors_not_in_baseline"]:
        print(f"\n## AUTHORS NOT IN BASELINE (recurring or active-thread)")
        for a in overlay["authors_not_in_baseline"][:10]:
            print(f"  {a['count']}x  {a['name']}  cos={a['company_hits']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
