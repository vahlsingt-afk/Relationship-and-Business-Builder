#!/usr/bin/env python3
"""
drr_score.py — Dynamic Relationship Relevance scoring prototype.

This is a v0 prototype. See `RB_DRR_Specification_and_Evaluation.docx` for the
full spec. The intent is to surface a single comparable score per contact
that captures: recency, tier, density of cross-Circle membership, completeness
of the record, and breadth of evidence (`sources` count).

Component weights live in `rb_core.drr_score`. Change them there.

Usage:
    python3 drr_score.py                     # top 50 by score
    python3 drr_score.py --limit 100
    python3 drr_score.py --class RC          # filter by signal_class
    python3 drr_score.py --id donnie-boivin  # explain one entry
    python3 drr_score.py --json
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
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--class", dest="cls", choices=["VC", "NPR", "LMI", "LKI", "RC"])
    p.add_argument("--id", help="Explain a single entry by id")
    p.add_argument("--json", action="store_true")
    p.add_argument("--cache", action="store_true",
                   help="Write the top-N table to system/.cache/drr_score.json")
    args = p.parse_args()

    today = date.fromisoformat(args.date) if args.date else date.today()
    baseline = core.load_baseline()

    if args.id:
        match = [e for e in baseline if e.get("id") == args.id]
        if not match:
            print(f"no entry with id={args.id!r}", file=sys.stderr)
            return 1
        scored = core.drr_score(match[0], today)
        print(json.dumps(scored, indent=2))
        return 0

    rows = [core.drr_score(e, today) for e in baseline]
    if args.cls:
        rows = [r for r in rows if r["signal_class"] == args.cls]
    rows.sort(key=lambda r: r["score"], reverse=True)
    rows = rows[: args.limit]

    if args.cache:
        core.write_cache(
            "drr_score",
            {
                "as_of": today.isoformat(),
                "class_filter": args.cls,
                "limit": args.limit,
                "rows": rows,
            },
            source="drr_score.py",
        )

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    print(f"DRR (prototype) — top {len(rows)} as of {today}")
    print(f"{'Score':>5}  {'Class':<4}  {'Tier':<16}  Name")
    print("-" * 72)
    for r in rows:
        print(
            f"{r['score']:>5.1f}  {r['signal_class'] or '—':<4}  "
            f"{(r['rc_tier'] or '—'):<16}  {r['name']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
