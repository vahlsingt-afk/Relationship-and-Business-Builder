#!/usr/bin/env python3
"""
export_franchisee_research_gaps.py — Franchisee Finder Phase 1 (2026-10-02).

Franchisee-side counterpart to export_research_gaps.py (brand) and
export_competitor_research_gaps.py (competitor), for hunter_gap_manifest.py
to build real Hunter targets from. Two real gap kinds, matching the two
new Hunter playbooks:

  - "discovery" gaps: a tracked restaurant brand with no franchisee
    organization on record operating it yet (no registered org's
    brand_relationships text mentions it). The franchisee_discovery
    playbook's job -- discover WHO franchises this brand at all.
  - "profile" gaps: an already-known franchisee organization missing a
    core Phase 1 field (headquarters, ownership, leadership, sales
    estimate, legal entities, operating geography, total identified
    units). The franchisee_organization_profile playbook's job -- deepen
    an org Hunter (or a seed import) already found.

Brand coverage matching is deliberately simple text containment (brand
display name, case-insensitive, found in some registered org's
brand_relationships values) -- Phase 1's field shape is free-text per
competitor_intelligence.py's own established convention (see
franchisee_intelligence_common.py's module docstring), so this is the
honest matching method available without a structured brand_entity_id on
every relationship yet.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import ecosystem_intelligence as ei  # noqa: E402
import franchisee_intelligence_common as fic  # noqa: E402

PROFILE_GAP_FIELDS = (
    "headquarters", "ownership", "leadership", "sales_estimate",
    "legal_entities", "operating_geography", "total_identified_units",
)


def _covered_brand_text_blob() -> str:
    """Lowercased concatenation of every registered org's brand_
    relationships values -- cheap substring-containment matching surface."""
    parts = []
    for entry in fic.load_registry().get("registry", []):
        slug = entry.get("franchisee_slug")
        if not slug:
            continue
        try:
            org = fic.load_franchisee(slug)["organization"]
        except FileNotFoundError:
            continue
        for leaf in org.get("brand_relationships") or []:
            value = (leaf.get("value") or "").lower()
            if value:
                parts.append(value)
    return " | ".join(parts)


def _tracked_brand_names(*, rank_max: int | None = None) -> list[dict]:
    """Every real tracked restaurant brand (excluding the multi_brand_
    franchisee_operator subtype -- those ARE franchisee organizations
    themselves, not brands needing franchisee discovery). Optionally
    capped to the top `rank_max` by Technomic rank, since every one of
    ~1700 brands plausibly has franchisees and running discovery against
    all of them at once is not a reasonable Phase 1 default."""
    graph = ei._read_graph()
    brands = [
        e for e in graph.get("entities") or []
        if e.get("entity_type") == "brand" and e.get("subtype") != "multi_brand_franchisee_operator"
    ]
    if rank_max is not None:
        def _in_range(e: dict) -> bool:
            rank = (e.get("attributes") or {}).get("rank")
            return isinstance(rank, (int, float)) and rank <= rank_max
        brands = [e for e in brands if _in_range(e)]
    return brands


def export_discovery_gaps(*, rank_max: int | None = 500) -> list[dict]:
    blob = _covered_brand_text_blob()
    gaps = []
    for brand in _tracked_brand_names(rank_max=rank_max):
        name = (brand.get("name") or "").strip()
        if not name:
            continue
        if name.lower() in blob:
            continue
        gaps.append({
            "target_key": f"franchise-discovery:{brand['id']}",
            "brand_entity_id": brand["id"],
            "brand_name": name,
            "rank": (brand.get("attributes") or {}).get("rank"),
        })
    return gaps


def export_profile_gaps() -> list[dict]:
    gaps = []
    for entry in fic.load_registry().get("registry", []):
        slug = entry.get("franchisee_slug")
        if not slug:
            continue
        try:
            org = fic.load_franchisee(slug)["organization"]
        except FileNotFoundError:
            continue
        profile = fic.get_extended_profile(org)
        missing = []
        for field in PROFILE_GAP_FIELDS:
            if field in fic.EXTENDED_SCALAR_FIELDS:
                if not profile[field].get("value"):
                    missing.append(field)
            else:
                if not profile[field]:
                    missing.append(field)
        if not missing:
            continue
        gaps.append({
            "target_key": f"franchisee:{slug}",
            "display_name": org.get("display_name") or slug,
            "confidence_tier": org.get("confidence_tier", "low"),
            "missing_fields": missing,
        })
    return gaps


def export_franchisee_research_gaps(*, only_gaps: bool = True, discovery_rank_max: int | None = 500) -> dict:
    discovery = export_discovery_gaps(rank_max=discovery_rank_max)
    profile = export_profile_gaps()
    return {
        "schema": "rb.franchisee_research_gaps.v1",
        "discovery_candidates_scanned": len(_tracked_brand_names(rank_max=discovery_rank_max)),
        "discovery_gaps": discovery,
        "known_organization_count": len(fic.load_registry().get("registry", [])),
        "profile_gaps": profile,
    }


def main() -> int:
    import argparse
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    parser.add_argument("--discovery-rank-max", type=int, default=500)
    args = parser.parse_args()
    data = export_franchisee_research_gaps(discovery_rank_max=args.discovery_rank_max)
    rendered = json.dumps(data, indent=2, default=str) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
