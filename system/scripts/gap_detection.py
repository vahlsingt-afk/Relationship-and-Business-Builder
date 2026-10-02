#!/usr/bin/env python3
"""
gap_detection.py — RC / card / contact-field gap report.

Scans baseline_index.json and `system/cards/` and emits a structured gap report.

Usage:
    python3 gap_detection.py              # text report to stdout
    python3 gap_detection.py --json       # machine-readable
    python3 gap_detection.py --strict     # exit 1 if any gap found
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core


def build_report() -> dict:
    baseline = core.load_baseline()
    have_cards = core.list_card_ids()
    missing_cards = core.rcs_without_cards(baseline)
    contact_gaps = core.contact_field_gaps(baseline)
    orphan_cards = sorted(
        have_cards - {e["id"] for e in baseline if e.get("signal_class") == "RC"}
    )
    no_last_touch = [
        {"id": e["id"], "name": e["name"], "tier": e.get("rc_tier")}
        for e in baseline
        if e.get("signal_class") == "RC" and not e.get("last_touch")
    ]
    return {
        "rcs_without_cards": missing_cards,
        "orphan_cards": orphan_cards,
        "contact_field_gaps": contact_gaps,
        "rcs_without_last_touch": no_last_touch,
        "totals": {
            "rcs_without_cards": len(missing_cards),
            "orphan_cards": len(orphan_cards),
            "contact_field_gaps": len(contact_gaps),
            "rcs_without_last_touch": len(no_last_touch),
        },
    }


def render(rep: dict) -> str:
    out = ["# RC / Card / Contact Gap Report", ""]
    out.append("## RCs without a card file")
    if rep["rcs_without_cards"]:
        for r in rep["rcs_without_cards"]:
            out.append(f"- {r['name']} ({r['tier']}) — id `{r['id']}`")
    else:
        out.append("- (none)")
    out.append("")
    out.append("## Orphan card files (no RC in baseline)")
    if rep["orphan_cards"]:
        for c in rep["orphan_cards"]:
            out.append(f"- `{c}.md`")
    else:
        out.append("- (none)")
    out.append("")
    out.append("## RCs without `last_touch`")
    if rep["rcs_without_last_touch"]:
        for r in rep["rcs_without_last_touch"]:
            out.append(f"- {r['name']} ({r['tier']}) — id `{r['id']}`")
    else:
        out.append("- (none)")
    out.append("")
    out.append("## Contact-field gaps (active RCs missing email or phone)")
    if rep["contact_field_gaps"]:
        for g in rep["contact_field_gaps"]:
            out.append(f"- {g['name']} ({g['tier']}) — missing: {', '.join(g['missing'])}")
    else:
        out.append("- (none)")
    out.append("")
    t = rep["totals"]
    out.append(
        f"_Totals: {t['rcs_without_cards']} missing cards, "
        f"{t['orphan_cards']} orphan cards, "
        f"{t['rcs_without_last_touch']} no last_touch, "
        f"{t['contact_field_gaps']} contact-field gaps._"
    )
    return "\n".join(out) + "\n"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    p.add_argument("--strict", action="store_true",
                   help="Exit 1 if any gap found.")
    p.add_argument("--cache", action="store_true",
                   help="Write to system/.cache/gap_detection.json")
    args = p.parse_args()
    rep = build_report()
    if args.cache:
        core.write_cache("gap_detection", rep, source="gap_detection.py")
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print(render(rep))
    if args.strict and any(rep["totals"].values()):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
