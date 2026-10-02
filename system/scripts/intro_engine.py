#!/usr/bin/env python3
"""
intro_engine.py — find 1-3 broker paths to a target person or company.

Resolves the target string against the baseline (id, name, or company),
scores every candidate broker on DRR + proximity + active-thread context,
applies heuristic suppressions from `system/heuristics.md`, and returns
the top-N ranked paths with one-line reasons.

Usage:
    python3 intro_engine.py "Bob Gibson"
    python3 intro_engine.py "Toast"
    python3 intro_engine.py "bob-gibson" --limit 5
    python3 intro_engine.py "Toast" --json
    python3 intro_engine.py "Toast" --include-suppressed
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
    p.add_argument("target", help="Person id, person name, or company name.")
    p.add_argument("--limit", type=int, default=3,
                   help="Max broker paths to return.")
    p.add_argument("--date", help="ISO date (default: system date).")
    p.add_argument("--json", action="store_true")
    p.add_argument("--cache", action="store_true",
                   help="Write to system/.cache/intro_engine.<sanitized_target>.json")
    p.add_argument("--include-suppressed", action="store_true",
                   help="Show heuristic-blocked candidates (for debugging).")
    p.add_argument("--draft-ask", action="store_true",
                   help="Print the draft intro ask message for each broker.")
    args = p.parse_args()

    today = date.fromisoformat(args.date) if args.date else date.today()
    result = core.find_intro_paths(
        args.target,
        limit=args.limit,
        today=today,
        include_suppressed=args.include_suppressed,
    )

    if args.cache:
        sanitized = "".join(c if c.isalnum() else "-" for c in args.target.lower())[:48]
        core.write_cache(f"intro_engine.{sanitized}", result, source="intro_engine.py")

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    tr = result["target_resolved"]
    print(f"Target: {result['target']}  ({tr['type']})")
    if tr["type"] == "person" and tr.get("person"):
        p = tr["person"]
        print(f"  Resolved to: {p['name']} ({p.get('signal_class')}/{p.get('rc_tier') or '-'}) @ {p.get('current_company','—')}")
    elif tr["type"] == "company":
        print(f"  Resolved to company: {tr['name']}")
    else:
        print("  Unresolved — treating as free-text target.")

    if result["insiders"]:
        print(f"\n## INSIDERS at target company ({len(result['insiders'])})")
        for i in result["insiders"][:10]:
            print(f"  DRR {i['drr_score']:>5.1f}  {i['name']} ({i['signal_class']}/{i.get('rc_tier') or '-'})")

    if result["candidate_brokers"]:
        print(f"\n## TOP {len(result['candidate_brokers'])} BROKER PATHS")
        for i, c in enumerate(result["candidate_brokers"], 1):
            print(f"\n  {i}. {c['name']}  (composite {c['composite_score']}, DRR {c['drr_score']})")
            print(f"     {c['reason']}")
            sc = c.get("scorecard", {})
            if sc:
                print(f"     strength   : {sc.get('relationship_strength', '—')}")
                print(f"     maturity   : {sc.get('trust_maturity', '—')}")
                print(f"     equity risk: {sc.get('network_equity_risk', '—')}")
                posture = sc.get("recommended_posture", "—")
                posture_emoji = {
                    "ask": "✓ ask",
                    "nurture_first": "⚠ nurture first",
                    "do_not_ask": "✗ do not ask",
                    "direct_outreach": "→ direct outreach",
                }.get(posture, posture)
                print(f"     posture    : {posture_emoji}")
            for k, v in c["proximity"].items():
                if v:
                    print(f"       · {k}: {v}")
            if args.draft_ask and c.get("draft_ask"):
                print(f"\n     --- Draft ask ---")
                for line in c["draft_ask"].splitlines():
                    print(f"     {line}")
    else:
        print("\n(no broker candidates found)")

    if result.get("persistence_templates"):
        print(f"\n## PERSISTENCE TEMPLATES  ({len(result['persistence_templates'])} available)")
        for pt in result["persistence_templates"]:
            print(f"  broker: {pt['broker_name']}  posture: {pt['recommended_posture']}")
            print(f"    loop: {pt['loop_type']}  → confirm before writing to loop_ledger")

    if result["notes"]:
        print("\n## NOTES")
        for n in result["notes"]:
            print(f"  - {n}")

    if result["suppressed_count"]:
        print(f"\n_{result['suppressed_count']} candidate(s) suppressed by heuristics. "
              "Use --include-suppressed to inspect._")
        if args.include_suppressed:
            for c in result["suppressed"][:10]:
                print(f"    {c['name']}  reasons: {c['heuristic_flags']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
