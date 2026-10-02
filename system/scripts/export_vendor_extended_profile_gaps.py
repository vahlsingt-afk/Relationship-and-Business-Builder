#!/usr/bin/env python3
"""
export_vendor_extended_profile_gaps.py — RB-2026-09-28.

Closes the gap Team Portal's "Competitor Snapshot" card was showing
honest-empty for every tracked vendor (Todd: "we have no information on
products, strengths, key customers, recent news or trends"). Exports a
gap list scoped SPECIFICALLY to competitor_intelligence_common.py's
EXTENDED_PROFILE_FIELDS (products/strengths/key_customers/recent_news/
trends) -- the exact fields get_competitor_extended_profile()/Team
Portal's Competitor Snapshot reads, via competitor_intelligence.
add_extended_profile_finding() (the writer built alongside this script).

NOT the same thing as export_competitor_research_gaps.py /
import_competitor_research_pack.py (a separate, already-running "research
pack" cycle-tracking system, confirmed live: 66 of 200 competitors have
been through its Cycle 01 as of 2026-09-28, but it writes to
`latest_research_pack_status`/`gap_audit` -- a different schema Team
Portal's Competitor Snapshot never reads). Deliberately kept separate
rather than guessing at how to hook into that system's internals without
understanding it fully -- a real reconciliation opportunity for later
(that pipeline's already-gathered findings could plausibly promote into
these fields too), flagged, not attempted here.

Usage:
    python3 export_vendor_extended_profile_gaps.py --output PATH [--only-gaps]
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

_GAP_FIELDS = ("products", "strengths", "key_customers", "recent_news")


def _competitor_record(slug: str, display_name: str) -> dict:
    try:
        data = cic.load_competitor(slug)
    except FileNotFoundError:
        return {"competitor_slug": slug, "display_name": display_name, "error": "not found on disk", "coverage": "none"}
    comp = cic.get_extended_profile(data["competitor"])

    known = {f: [e.get("value") for e in (comp.get(f) or [])] for f in _GAP_FIELDS}
    known["trends"] = (comp.get("trends") or {}).get("value")

    gaps = [f for f in _GAP_FIELDS if not known[f]]
    if not known["trends"]:
        gaps.append("trends")

    return {
        "competitor_slug": slug,
        "display_name": comp.get("display_name", display_name),
        "aliases": comp.get("aliases") or [],
        "primary_category": comp.get("primary_category"),
        "known_products": known["products"],
        "known_strengths": known["strengths"],
        "known_key_customers": known["key_customers"],
        "known_recent_news": known["recent_news"],
        "known_trends": known["trends"],
        "research_gaps": gaps,
        "coverage": "none" if len(gaps) >= 4 else ("partial" if gaps else "well_covered"),
    }


_MISSION = (
    "This file is a snapshot of RBB's tracked restaurant-technology-vendor (competitor) intelligence -- "
    "specifically the products/strengths/key_customers/recent_news/trends fields (Team Portal's 'Competitor "
    "Snapshot' card). Each vendor entry lists what's already confirmed plus a research_gaps checklist. Your "
    "mission: for vendors with coverage \"none\" or \"partial\", find public, sourced evidence to close those "
    "specific gaps -- official product/pricing pages, press releases, analyst/trade coverage (Restaurant "
    "Business, QSR Magazine, Nation's Restaurant News, PYMNTS), named customer case studies, earnings calls. "
    "Do not re-research a category a vendor already has covered unless you have a materially newer or "
    "conflicting source. Cite a source_url for every finding -- unsourced claims can't be used. Return "
    "findings in the shape documented in system/research/vendor_research_instructions.md so they can be "
    "re-ingested with vendor_extended_profile_ingest.py."
)


def export_vendor_extended_profile_gaps(*, only_gaps: bool = False) -> dict:
    reg = cic.load_registry()
    all_records = [
        _competitor_record(e.get("competitor_slug", ""), e.get("display_name", ""))
        for e in reg.get("registry", []) if e.get("competitor_slug")
    ]
    coverage_summary = {
        "total_competitors": len(all_records),
        "none": sum(1 for r in all_records if r.get("coverage") == "none"),
        "partial": sum(1 for r in all_records if r.get("coverage") == "partial"),
        "well_covered": sum(1 for r in all_records if r.get("coverage") == "well_covered"),
    }
    records = [r for r in all_records if r.get("coverage") != "well_covered"] if only_gaps else all_records

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mission": _MISSION,
        "coverage_summary": coverage_summary,
        "competitors": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", required=True)
    parser.add_argument("--only-gaps", action="store_true")
    args = parser.parse_args()

    result = export_vendor_extended_profile_gaps(only_gaps=args.only_gaps)
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(result['competitors'])} competitor records to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
