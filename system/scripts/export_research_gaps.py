#!/usr/bin/env python3
"""
export_research_gaps.py — RB-2026-09-27.

Exports a compact, hand-to-ChatGPT JSON snapshot of every tracked
restaurant brand's current research state plus an explicit per-brand
gap checklist, so a deep-research pass can target what's still missing
instead of re-covering ground the three deep-research ingest scripts
(deep_research_dataset_ingest.py, multibrand_franchisee_operator_ingest.py,
deep_account_intelligence_v56_ingest.py) already answered.

Deliberately excludes:
  - Todd's personal customers_prospects account-brief content (strategy,
    notes, sales approach, bottom line, recommended approach) -- this
    export reads ONLY ecosystem_intelligence.json, never customers_
    prospects/, so there is no path for that content to leak in.
  - This repo's own internal plumbing: entity ids, source-provenance
    arrays, packet_id/recorded_at bookkeeping, confidence rationale
    text, snapshot/versioning metadata. Only public-evidence-shaped
    facts go out -- name, Technomic-sourced scale (already public
    industry data), and whatever the deep-research attribute namespace
    holds, stripped to its value/confidence/source_url.

Excludes multi_brand_franchisee_operator brand-subtype entities -- this
export's gap-checklist shape (scale/technology/leadership/franchise
disclosure) matches the Top-500-style single-brand dataset, not the
operator dataset's own shape; operators can get their own export later
if a research gap opens up there.

Usage:
    python3 export_research_gaps.py --output PATH [--only-gaps]

    --only-gaps restricts the "brands" list to brands with at least one
    missing category (drops well-covered brands entirely) -- useful for
    keeping the handoff file small once initial coverage is decent.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402

_NAMESPACE = "deep_research_profile"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _strip_leaf(leaf) -> object:
    """Keep only the fields useful for research context (value/confidence/
    source), drop internal bookkeeping (packet_id, recorded_at)."""
    if isinstance(leaf, dict) and "value" in leaf:
        out = {"value": leaf.get("value")}
        if leaf.get("confidence") is not None:
            out["confidence"] = leaf["confidence"]
        if leaf.get("source_url"):
            out["source_url"] = leaf["source_url"]
        return out
    return leaf


def _strip_evidence_item(item: dict) -> dict:
    out = {"finding": item.get("finding")}
    for key in ("category", "topic", "confidence", "source_url", "source_date", "reported_or_effective_date"):
        if item.get(key) is not None:
            out[key] = item[key]
    return out


def _strip_person(person: dict) -> dict:
    out = {"name": person.get("name"), "title": person.get("title")}
    for key in ("function", "confidence", "source_url"):
        if person.get(key) is not None:
            out[key] = person[key]
    return out


def _active_tech_stack(brand_id: str, graph: dict) -> list[dict]:
    by_id = ei._index_by_id(graph.get("entities") or [])
    out = []
    for rel in graph.get("relationships") or []:
        if rel.get("from_entity_id") != brand_id or rel.get("relationship_type") != "uses_vendor_for_category":
            continue
        if rel.get("status") != "active":
            continue
        vendor = by_id.get(rel.get("to_entity_id")) or {}
        out.append({"vendor": vendor.get("name"), "category": rel.get("category")})
    return out


def _brand_record(entity: dict, graph: dict) -> dict:
    attrs = entity.get("attributes") or {}
    profile = attrs.get(_NAMESPACE) or {}
    tech_stack = _active_tech_stack(entity["id"], graph)

    leadership = profile.get("current_leadership") or []
    scale = profile.get("scale_snapshot", {}).get("units")
    canonical_technology = profile.get("canonical_technology") or {}
    franchise_disclosure = profile.get("franchise_disclosure", {}).get("findings")
    evidence_count = (
        len(profile.get("deep_pass_public_evidence") or [])
        + len(profile.get("account_profile_evidence") or [])
        + len(profile.get("evidence_ledger") or [])
    )

    gaps = []
    if not leadership:
        gaps.append("leadership")
    if not scale and not entity.get("attributes", {}).get("unit_count"):
        gaps.append("scale")
    if not canonical_technology and not tech_stack:
        gaps.append("technology_stack")
    if not franchise_disclosure:
        gaps.append("franchise_disclosure")
    if evidence_count == 0:
        gaps.append("general_evidence")

    return {
        "id": entity["id"],
        "name": entity.get("name"),
        "aliases": entity.get("aliases") or [],
        "rank": attrs.get("rank"),
        "segment": attrs.get("segment"),
        "subsegment": attrs.get("subsegment"),
        "unit_count": attrs.get("unit_count"),
        "system_sales_usd": attrs.get("system_sales"),
        "known_leadership": [_strip_person(p) for p in leadership],
        "known_scale": _strip_leaf(scale) if scale else None,
        "known_technology": {k: _strip_leaf(v) for k, v in canonical_technology.items()},
        "known_active_tech_stack_relationships": tech_stack,
        "known_franchise_disclosure": franchise_disclosure,
        "evidence_items_on_file": evidence_count,
        "research_gaps": gaps,
        "coverage": "none" if len(gaps) >= 4 else ("partial" if gaps else "well_covered"),
    }


_MISSION = (
    "This file is a snapshot of RBB's restaurant-brand intelligence graph. Each brand entry lists what's "
    "already confirmed on file plus a research_gaps checklist of categories still missing evidence "
    "(leadership, scale, technology_stack, franchise_disclosure, general_evidence). Your mission: for brands "
    "with coverage \"none\" or \"partial\", find public, sourced evidence to close those specific gaps -- "
    "official leadership pages, FDD/franchise-disclosure filings, press releases, vendor case studies, trade "
    "press. Do not re-research a category a brand already has covered unless you have a materially newer or "
    "conflicting source. Cite a source_url for every finding -- unsourced claims can't be used. Return findings "
    "in the same per-category shape shown here (value/confidence/source_url for scale and technology; "
    "name/title/source_url for leadership) so they can be re-ingested the same way this file was produced."
)


def export_research_gaps(*, only_gaps: bool = False, rank_min: int | None = None, rank_max: int | None = None) -> dict:
    graph = ei._read_graph()
    brands = [
        e for e in graph.get("entities") or []
        if e.get("entity_type") == "brand" and e.get("subtype") != "multi_brand_franchisee_operator"
    ]
    if rank_min is not None or rank_max is not None:
        def _in_range(e: dict) -> bool:
            rank = (e.get("attributes") or {}).get("rank")
            if rank is None:
                return False
            if rank_min is not None and rank < rank_min:
                return False
            if rank_max is not None and rank > rank_max:
                return False
            return True
        brands = [e for e in brands if _in_range(e)]
    all_records = [_brand_record(e, graph) for e in brands]
    coverage_summary = {
        "total_brands": len(all_records),
        "none": sum(1 for r in all_records if r["coverage"] == "none"),
        "partial": sum(1 for r in all_records if r["coverage"] == "partial"),
        "well_covered": sum(1 for r in all_records if r["coverage"] == "well_covered"),
    }
    records = [r for r in all_records if r["coverage"] != "well_covered"] if only_gaps else all_records

    return {
        "generated_at": _now_iso(),
        "mission": _MISSION,
        "coverage_summary": coverage_summary,
        "brands": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--only-gaps", action="store_true")
    parser.add_argument("--rank-min", type=int, default=None)
    parser.add_argument("--rank-max", type=int, default=None)
    args = parser.parse_args()

    data = export_research_gaps(only_gaps=args.only_gaps, rank_min=args.rank_min, rank_max=args.rank_max)
    Path(args.output).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {len(data['brands'])} brand records to {args.output}")
    print(json.dumps(data["coverage_summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
