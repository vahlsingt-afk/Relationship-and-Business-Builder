#!/usr/bin/env python3
"""
network_gap.py — score company clusters by inner-tier RC anchor presence.

A company-cluster is large but UNANCHORED when:
    - cluster size >= min_cluster (default 5)
    - cluster has zero inner-tier ACTIVE RC.

The `gap_score` for unanchored clusters equals the LKI count in that cluster —
LKIs are RC promotion candidates already qualified by at least one prior touch.

Usage:
    python3 network_gap.py                  # ranked text report
    python3 network_gap.py --min 10         # only clusters with ≥10 members
    python3 network_gap.py --json
    python3 network_gap.py --gaps-only      # only show anchor-gap rows
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
    p.add_argument("--min", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p.add_argument("--gaps-only", action="store_true")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--cache", action="store_true",
                   help="Write to system/.cache/network_gap.json")
    args = p.parse_args()

    rows = core.cluster_inner_anchor_score(core.load_baseline(), min_cluster=args.min)
    if args.gaps_only:
        rows = [r for r in rows if r["anchor_gap"]]
    rows = rows[: args.limit]

    if args.cache:
        core.write_cache(
            "network_gap",
            {
                "min_cluster": args.min,
                "limit": args.limit,
                "gaps_only": args.gaps_only,
                "rows": rows,
            },
            source="network_gap.py",
        )

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    print(f"Network gap scoring — cluster size ≥ {args.min} (top {args.limit})")
    print(f"{'Company':<45} {'Size':>5} {'AnyRC':>6} {'LKI':>4} {'Gap':>4} {'Anchor':>22}")
    print("-" * 95)
    for r in rows:
        anchor = "—" if r["anchor_gap"] else ", ".join(r["inner_rcs"])[:22]
        marker = "*" if r["anchor_gap"] else " "
        print(
            f"{marker}{r['company'][:44]:<44} "
            f"{r['total']:>5} {r['any_rc']:>6} {r['lki_count']:>4} "
            f"{r['gap_score']:>4} {anchor:>22}"
        )
    print()
    print("* = unanchored cluster (no inner-tier RC). gap_score = LKI count in cluster.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
