#!/usr/bin/env python3
"""
export_franchisee_research_gaps.py — 2026-10-02.

Exports a compact, hand-to-ChatGPT-Deep-Research JSON snapshot of every
franchisee organization Franchisee Finder has on file (system/
franchisee_finder/), plus an explicit per-organization gap checklist --
same shape and purpose as export_research_gaps.py's brand export, built
to plug into hunter_gap_manifest.py's universe selection the same way.

Real gap this closes: Franchisee Finder Phase 1 (system/design/
FRANCHISEE_FINDER_SPEC.md) seeded 219 organizations from two public
sources, but has zero legal-entity, ownership-structure, or geographic-
footprint data for any of them (neither seed source carries it -- see
CANONICAL_REGISTRY.yaml's franchisee_finder domain known_gap). There was
previously no way to hand Hunter a prioritized research queue for this
domain without a live call to the Trusted Chat API, which only resolves
when Todd's Mac is running the Cloudflare tunnel -- a real incident
(2026-10-02) showed a Codex/ChatGPT-Project task blocked on exactly that
DNS failure. This export is 100% local (reads only system/
franchisee_finder/), matching hunter_cycle.py's documented network-free
prepare workflow.

Priority ("enterprise_primary" vs "secondary") is total_identified_units
> --unit-threshold (default 90) -- the real ">90-location target queue"
criterion named in the incident that prompted this.

Usage:
    python3 export_franchisee_research_gaps.py --output PATH [--only-gaps] [--unit-threshold 90]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import franchisee_finder_common as ffc  # noqa: E402

DEFAULT_UNIT_THRESHOLD = 90


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _org_gaps(org: dict) -> list[str]:
    gaps = []
    if not org.get("headquarters"):
        gaps.append("headquarters")
    if not (org.get("ownership") or {}).get("structure"):
        gaps.append("ownership")
    if not org.get("legal_entities"):
        gaps.append("legal_entities")
    if not org.get("people"):
        gaps.append("leadership")
    if not org.get("geographic_footprint"):
        gaps.append("geographic_footprint")
    unresolved_brands = [
        rel.get("brand_name") for rel in org.get("brand_relationships") or []
        if (rel.get("unit_count") or {}).get("status") == "unresolved"
    ]
    if unresolved_brands:
        gaps.append("unit_count_verification")
    return gaps


def _org_record(org: dict, *, unit_threshold: int) -> dict:
    total_units = org.get("total_identified_units") or 0
    gaps = _org_gaps(org)
    brand_names = [rel.get("brand_name") for rel in org.get("brand_relationships") or []]
    return {
        "id": org["org_slug"],
        "name": org.get("display_name"),
        "linked_graph_entity_id": org.get("linked_graph_entity_id"),
        "total_identified_units": total_units,
        "brand_count": len(brand_names),
        "brands": brand_names,
        "headquarters": (org.get("headquarters") or {}).get("value"),
        "research_gaps": gaps,
        "coverage": "none" if len(gaps) >= 4 else ("partial" if gaps else "well_covered"),
        "priority": "enterprise_primary" if total_units > unit_threshold else "secondary",
    }


_MISSION = (
    "This file is a snapshot of RBB's Franchisee Finder intelligence domain -- restaurant franchisee "
    "organizations (multi-brand groups and large foodservice contractors), each with a research_gaps "
    "checklist of categories still missing evidence (headquarters, ownership, legal_entities, leadership, "
    "geographic_footprint, unit_count_verification). Your mission: for organizations with priority "
    "\"enterprise_primary\" (more than {threshold} identified locations) and coverage \"none\" or \"partial\", "
    "find public, sourced evidence to close those specific gaps -- company websites, press releases, FDD "
    "filings, trade press, LinkedIn leadership pages, state corporate registries. Do not re-research a "
    "category an organization already has covered unless you have a materially newer or conflicting source. "
    "Cite a source_url for every finding -- unsourced claims can't be used."
)


def export_franchisee_research_gaps(*, only_gaps: bool = False, unit_threshold: int = DEFAULT_UNIT_THRESHOLD) -> dict:
    reg = ffc.load_registry()
    all_records = []
    for row in reg.get("registry", []):
        slug = row.get("org_slug") or ""
        try:
            org = ffc.load_organization(slug)["organization"]
        except FileNotFoundError:
            continue
        all_records.append(_org_record(org, unit_threshold=unit_threshold))

    coverage_summary = {
        "total_organizations": len(all_records),
        "none": sum(1 for r in all_records if r["coverage"] == "none"),
        "partial": sum(1 for r in all_records if r["coverage"] == "partial"),
        "well_covered": sum(1 for r in all_records if r["coverage"] == "well_covered"),
        "enterprise_primary": sum(1 for r in all_records if r["priority"] == "enterprise_primary"),
    }
    records = [r for r in all_records if r["coverage"] != "well_covered"] if only_gaps else all_records
    records.sort(key=lambda r: (0 if r["priority"] == "enterprise_primary" else 1, -r["total_identified_units"]))

    return {
        "generated_at": _now_iso(),
        "mission": _MISSION.format(threshold=unit_threshold),
        "coverage_summary": coverage_summary,
        "organizations": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--only-gaps", action="store_true")
    parser.add_argument("--unit-threshold", type=int, default=DEFAULT_UNIT_THRESHOLD)
    args = parser.parse_args()

    data = export_franchisee_research_gaps(only_gaps=args.only_gaps, unit_threshold=args.unit_threshold)
    Path(args.output).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {len(data['organizations'])} franchisee organization records to {args.output}")
    print(json.dumps(data["coverage_summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
