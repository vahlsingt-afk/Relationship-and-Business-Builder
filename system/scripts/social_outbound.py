#!/usr/bin/env python3
"""
social_outbound.py — overlay + recommendations for your own posts.

Tracks your posts + the engagement on them. Produces:
  - Recent post list with active-thread tags
  - Engagement-by-contact ranked by DRR-weighted composite
  - Engagement silence (contacts who used to engage but stopped)
  - Topic-engagement map
  - Post recommendations grounded in active threads + engagement history

Usage:
    python3 social_outbound.py             # overlay text report
    python3 social_outbound.py --recs      # post recommendations
    python3 social_outbound.py --json
    python3 social_outbound.py --cache
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
    p.add_argument("--date", help="ISO date (default: system date)")
    p.add_argument("--recent-days", type=int, default=30, dest="recent_days")
    p.add_argument("--recs", action="store_true",
                   help="Print post recommendations instead of the overlay.")
    p.add_argument("--json", action="store_true")
    p.add_argument("--cache", action="store_true")
    args = p.parse_args()
    today = date.fromisoformat(args.date) if args.date else date.today()

    if args.recs:
        recs = core.post_recommendations(today=today)
        if args.cache:
            core.write_cache("post_recommendations", recs, source="social_outbound.py")
        if args.json:
            print(json.dumps(recs, indent=2, default=str))
            return 0
        print(f"Post recommendations as of {today} ({len(recs)})")
        for i, r in enumerate(recs[:10], 1):
            print(f"\n  {i}. [{r['impact'].upper()}]  {r['title']}")
            print(f"     {r['rationale']}")
            if r["would_warm"]:
                print(f"     would warm: {', '.join(r['would_warm'][:8])}")
            if r["active_thread_ids"]:
                print(f"     threads: {', '.join(r['active_thread_ids'])}")
        return 0

    overlay = core.social_outbound_overlay(today=today, recent_days=args.recent_days)
    if args.cache:
        core.write_cache("social_outbound", overlay, source="social_outbound.py")
    if args.json:
        print(json.dumps(overlay, indent=2, default=str))
        return 0
    if not overlay.get("fetched_at"):
        print("No own-posts or engagement data yet.")
        print("Add some via:")
        print("  python3 system/scripts/mutations.py my-post-add ...")
        print("  python3 system/scripts/mutations.py engagement-add ...")
        return 0

    print(f"Social outbound overlay (fetched {overlay['fetched_at']}, stale={overlay['stale']})")

    if overlay["recent_posts"]:
        print(f"\n## YOUR RECENT POSTS ({len(overlay['recent_posts'])} in last {args.recent_days}d)")
        for p in overlay["recent_posts"][:10]:
            tag = (" [threads: " + ",".join(p["active_thread_ids"]) + "]") if p.get("active_thread_ids") else ""
            eng = p.get("engagement_totals") or {}
            print(f"  {(p.get('posted_at') or '')[:10]} {tag}")
            print(f"    ❝ {(p.get('text') or '')[:140]}…")
            print(f"    likes={eng.get('likes','—')} comments={eng.get('comments','—')} shares={eng.get('shares','—')}")

    if overlay["engagement_by_contact"]:
        print(f"\n## ENGAGEMENT BY CONTACT ({len(overlay['engagement_by_contact'])})")
        for c in overlay["engagement_by_contact"][:15]:
            tier = c.get("rc_tier") or "-"
            print(f"  composite {c['composite']:>5.1f}  {c['name']} ({c['signal_class']}/{tier})  "
                  f"like {c['total_likes']} / comment {c['total_comments']} / share {c['total_shares']}  "
                  f"last: {(c.get('last_engagement_at') or '')[:10]}")

    if overlay["engagement_silence"]:
        print(f"\n## ENGAGEMENT SILENCE (contacts who used to engage, went quiet >{args.recent_days}d)")
        for s in overlay["engagement_silence"][:10]:
            print(f"  {s['name']} ({s['signal_class']}/{s.get('rc_tier') or '-'})  "
                  f"last: {(s.get('last_engagement_at') or '')[:10]}  DRR {s['drr_score']}")

    if overlay["topic_engagement_map"]:
        print(f"\n## TOPIC ENGAGEMENT MAP")
        for t in overlay["topic_engagement_map"][:8]:
            print(f"  score {t['total_engagement_score']:>5.1f}  `{t['topic']}`  "
                  f"top engagers: {', '.join(e['id'] for e in t['top_engagers'])}")

    if overlay["active_thread_engagement"]:
        print(f"\n## ACTIVE-THREAD ENGAGEMENT ({len(overlay['active_thread_engagement'])})")
        for r in overlay["active_thread_engagement"][:10]:
            print(f"  {r['at'][:10]}  {r['type']} by {r['engager_name']}  "
                  f"threads: {','.join(r['active_thread_ids'])}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
