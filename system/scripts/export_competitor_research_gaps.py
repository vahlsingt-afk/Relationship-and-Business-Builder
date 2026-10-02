#!/usr/bin/env python3
"""
export_competitor_research_gaps.py — RB-2026-09-28.

Competitor-side counterpart to export_research_gaps.py (brand side):
exports a compact, hand-to-ChatGPT JSON snapshot of every tracked
competitor's current research state, so a deep-research pass can target
what's still missing instead of re-covering the 66 vendors already
refreshed by import_competitor_research_pack.py's first real cycle
(2026-09-28, "RBB_Competitor_Research_Cycle_01").

Coverage signal is deliberately simple and honest: a competitor that has
been through a research pack cycle (competitor.json carries
latest_research_pack_status, written only by import_competitor_research_
pack.py) is "cycle_covered" -- its own gap_audit.verification_needed list
(already on file) is surfaced here as next_verification_needed rather than
recomputed. A competitor that has NEVER been through a pack cycle is
"no_pack_yet" regardless of how much older evidence.jsonl content it has
(that evidence may be stale, unsourced-by-today's-standard, or was never
audited for completeness the way a pack's gap_audit does) -- these are the
priority for a new cycle. evidence_categories_on_file is included as
honest context, not as a substitute completeness signal.

Excludes nothing -- every registered competitor is listed -- but the
`priority` field is derived (no_pack_yet > cycle_covered) so a research
run can sort on it directly.

Usage:
    python3 export_competitor_research_gaps.py --output PATH [--only-gaps]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import competitor_intelligence_common as cic  # noqa: E402

_EVIDENCE_CATEGORY_SIGNAL = ["positioning", "strength", "weakness", "pricing", "reference_customer", "market_share"]

_MISSION = (
    "This file is a snapshot of RBB's tracked-competitor (restaurant-tech vendor) intelligence store. Each "
    "entry lists what's already confirmed on file plus, for a competitor already run through a research "
    "cycle, its own outstanding verification_needed checklist. Your mission: for every competitor with "
    "priority \"no_pack_yet\", and for \"cycle_covered\" ones only where next_verification_needed lists a "
    "real gap, find current, public, sourced evidence -- the vendor's own investor/press materials, named "
    "customer case studies, leadership pages, trade press (Restaurant Business, QSR Magazine, Nation's "
    "Restaurant News, PYMNTS). Do not re-research a competitor already marked cycle_covered unless you have "
    "a materially newer source than its last_research_pack_as_of date. Return findings using the exact pack "
    "schema documented in the instruction set this file ships with (one JSON object per line: entity/"
    "category/pack_phase=\"phase2_competitor\"/recommended_add/flag_acquired/as_of_date/verified_findings/"
    "cos_commentary/gap_audit) so they can be re-ingested by import_competitor_research_pack.py the same way "
    "Cycle 01 was."
)


def _competitor_record(slug: str, entry: dict) -> dict:
    try:
        data = cic.load_competitor(slug)
    except FileNotFoundError:
        return {
            "competitor_slug": slug,
            "display_name": entry.get("display_name", slug),
            "priority": "no_pack_yet",
            "error": "registry entry with no competitor.json on disk",
        }
    comp = data["competitor"]
    evidence = data["evidence"]
    pack_status = comp.get("latest_research_pack_status")
    categories_on_file = sorted({e.get("category") for e in evidence if e.get("category")})

    record = {
        "competitor_slug": comp.get("competitor_slug", slug),
        "display_name": comp.get("display_name", slug),
        "aliases": comp.get("aliases") or [],
        "vendor_entity_id": comp.get("vendor_entity_id"),
        "primary_category": comp.get("primary_category"),
        "competes_on": comp.get("competes_on") or [],
        "evidence_count": len(evidence),
        "last_evidence_date": comp.get("last_evidence_date"),
        "evidence_categories_on_file": categories_on_file,
        "missing_evidence_category_signal": [c for c in _EVIDENCE_CATEGORY_SIGNAL if c not in categories_on_file],
        "has_research_pack_cycle": pack_status is not None,
        "last_research_pack_as_of": (pack_status or {}).get("as_of_date"),
        "next_verification_needed": ((pack_status or {}).get("gap_audit") or {}).get("verification_needed", []),
    }
    record["priority"] = "cycle_covered" if pack_status is not None else "no_pack_yet"
    return record


def export_competitor_research_gaps(*, only_gaps: bool = False) -> dict:
    reg = cic.load_registry()
    records = [_competitor_record(e.get("competitor_slug", ""), e) for e in reg.get("registry", [])]
    records.sort(key=lambda r: (0 if r["priority"] == "no_pack_yet" else 1, r["display_name"]))

    coverage_summary = {
        "total_competitors": len(records),
        "no_pack_yet": sum(1 for r in records if r["priority"] == "no_pack_yet"),
        "cycle_covered": sum(1 for r in records if r["priority"] == "cycle_covered"),
    }
    if only_gaps:
        records = [
            r for r in records
            if r["priority"] == "no_pack_yet" or r.get("next_verification_needed")
        ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mission": _MISSION,
        "coverage_summary": coverage_summary,
        "competitors": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--only-gaps", action="store_true")
    args = parser.parse_args()

    data = export_competitor_research_gaps(only_gaps=args.only_gaps)
    Path(args.output).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {len(data['competitors'])} competitor records to {args.output}")
    print(json.dumps(data["coverage_summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
