#!/usr/bin/env python3
"""franchisee_finder_seed_import.py — Franchisee Finder Phase 1 (2026-10-02).

"Franchisee lists are hard to get" (Todd) -- but RBB already has two real,
unconsolidated partial lists nobody had pulled together into one queryable,
evidence-based record before this domain existed:

  1. 47 real `multi_brand_franchisee_operator` entities already in
     ecosystem_intelligence.json (multibrand_franchisee_operator_ingest.py,
     2026-09-27), each with real sourced `operates` relationships to
     brands (277 total, FRANdata-sourced, confidence scores attached) and
     a `deep_research_profile.evidence_ledger`/`current_leadership` with
     real findings (headquarters, leadership, technology notes, ...).
     This is genuinely researched intelligence, imported here at its real
     confidence -- not downgraded just because it predates this domain.
  2. system/franchisee_hierarchy.json -- a static one-time Franchise Times
     "2026 Restaurant 200" extract (name/location/revenue/brand+unit
     breakdown), explicitly flagged by FRANCHISEE_FINDER_SPEC.md as a
     "candidate seed/import, not the schema to build on." Imported at LOW
     confidence (it's an unsourced third-party ranking snapshot, not a
     primary/FDD source) -- real intelligence, but weaker provenance than
     source #1.

Matched by franchisee_intelligence.find_similar_franchisees() so the same
real organization (e.g. Flynn Group appears in both sources) gets ONE
record with both evidence trails, never two silently duplicate shells.
Idempotent: reruns update existing shells' evidence rather than
duplicating (add_extended_profile_finding() already dedupes identical
list values; scalar fields are intentionally overwritten by a rerun with
the same source, which is a no-op in practice).

Usage:
    python3 franchisee_finder_seed_import.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import franchisee_intelligence as fi  # noqa: E402
import franchisee_intelligence_common as fic  # noqa: E402

HIERARCHY_PATH = core.SYSTEM_DIR / "franchisee_hierarchy.json"

# franchisee_hierarchy.json's own hierarchy_level vocabulary doesn't
# match this domain's VALID_HIERARCHY_LEVELS 1:1 -- "franchisee_group"
# there (81 of 200 rows) means a single-brand multi-unit operator, this
# domain's "multi_unit_single_brand".
_SEED_HIERARCHY_LEVEL_MAP = {
    "multi_brand_franchisee_group": "multi_brand_franchisee_group",
    "franchisee_group": "multi_unit_single_brand",
}

_EVIDENCE_LEDGER_CATEGORY_MAP = {
    "technology": "technology", "headquarters": "headquarters", "leadership": "leadership",
    "business": "other", "procurement": "other", "ai_inventory": "technology",
    "history": "other", "technology_history": "technology", "boh_supply_chain": "other",
    "data_ai": "technology",
}


def _confidence_bucket(score) -> str:
    """Maps a 0-100 numeric confidence (ecosystem graph convention) or a
    0-1 float (relationship confidence.score convention) onto this
    domain's low/medium/high/critical vocabulary."""
    if score is None:
        return "medium"
    value = float(score)
    if value <= 1.0:
        value *= 100
    if value >= 90:
        return "critical"
    if value >= 70:
        return "high"
    if value >= 40:
        return "medium"
    return "low"


def _import_ecosystem_operators(graph: dict, by_id: dict, *, dry_run: bool) -> list[dict]:
    operators = [e for e in graph.get("entities") or [] if e.get("subtype") == "multi_brand_franchisee_operator"]
    results = []
    for op in operators:
        name = op.get("name") or op["id"]
        matches = fi.find_similar_franchisees(name)
        slug = matches[0]["franchisee_slug"] if matches else fi.slugify(name)
        action = "updated" if matches else "created"
        if not dry_run:
            if not matches:
                fi.create_franchisee(name, slug=slug)
            fi.set_hierarchy_level(slug, "multi_brand_franchisee_group")

        profile = (op.get("attributes") or {}).get("deep_research_profile") or {}
        brand_rels = []
        for rel in graph.get("relationships") or []:
            if rel.get("relationship_type") != "operates" or rel.get("from_entity_id") != op["id"]:
                continue
            brand_entity = by_id.get(rel.get("to_entity_id")) or {}
            brand_name = brand_entity.get("name") or rel.get("to_entity_id")
            confidence_info = rel.get("confidence") or {}
            value = f"{brand_name} -- confirmed operator (unit count not captured by this source; see franchisee_organization_profile gaps)"
            if not dry_run:
                fi.add_extended_profile_finding(
                    slug, "brand_relationships", value,
                    confidence=_confidence_bucket(confidence_info.get("score")),
                    source_url=(rel.get("sources") or [None])[0],
                    as_of=(rel.get("updated_at") or "")[:10] or None,
                    finding_type="independently_verified",
                    limitations_or_conflicts=confidence_info.get("rationale"),
                )
            brand_rels.append(brand_name)

        leadership = profile.get("current_leadership") or []
        for leader in leadership:
            value = f"{leader.get('name')} -- {leader.get('title')}"
            if not dry_run and leader.get("name") and leader.get("title"):
                fi.add_extended_profile_finding(
                    slug, "leadership", value,
                    confidence=_confidence_bucket(leader.get("confidence")),
                    source_url=leader.get("source_url"), as_of=(leader.get("recorded_at") or "")[:10] or None,
                    finding_type="independently_verified",
                )

        for entry in profile.get("evidence_ledger") or []:
            category = _EVIDENCE_LEDGER_CATEGORY_MAP.get(entry.get("category"), "other")
            finding = entry.get("finding") or ""
            if not finding:
                continue
            if not dry_run:
                fi.add_franchisee_evidence(
                    slug, finding, category=category,
                    source=entry.get("source_url"), confidence=_confidence_bucket(entry.get("confidence")),
                )
                if category == "headquarters":
                    fi.add_extended_profile_finding(
                        slug, "headquarters", finding,
                        confidence=_confidence_bucket(entry.get("confidence")),
                        source_url=entry.get("source_url"), as_of=(entry.get("access_date") or None),
                        finding_type="independently_verified",
                    )

        # total_identified_units deliberately not set from this source --
        # the operates relationships carry per-brand presence, not a real
        # unit count per brand, so there is no honest total to compute
        # here. Left for the franchisee_organization_profile playbook (or
        # the Franchise Times seed below, which does have one).

        results.append({"action": action, "slug": slug, "name": name, "brand_count": len(brand_rels), "source": "ecosystem_operates_relationships"})
    return results


def _import_hierarchy_seed(*, dry_run: bool) -> list[dict]:
    if not HIERARCHY_PATH.exists():
        return []
    data = json.loads(HIERARCHY_PATH.read_text(encoding="utf-8"))
    results = []
    for row in data.get("operators") or []:
        name = row.get("name")
        if not name:
            continue
        matches = fi.find_similar_franchisees(name)
        slug = matches[0]["franchisee_slug"] if matches else fi.slugify(name)
        action = "supplemented" if matches else "created"
        if not dry_run:
            if not matches:
                fi.create_franchisee(name, slug=slug)
                fi.set_hierarchy_level(slug, _SEED_HIERARCHY_LEVEL_MAP.get(row.get("hierarchy_level"), "unknown"))

            if row.get("location"):
                fi.add_extended_profile_finding(
                    slug, "headquarters", row["location"], confidence="low",
                    source_url=None, source_owner="Franchise Times 2026 Restaurant 200",
                    source_type="trade_press_ranking", finding_type="marketplace_reported",
                )
            if row.get("revenue_usd"):
                basis = "estimated" if row.get("rank_revenue_estimated") else "reported"
                fi.add_extended_profile_finding(
                    slug, "sales_estimate", f"${row['revenue_usd']:,} ({basis}, Franchise Times 2026 Restaurant 200, rank {row.get('rank')})",
                    confidence="low", source_owner="Franchise Times 2026 Restaurant 200",
                    source_type="trade_press_ranking", finding_type="marketplace_reported",
                )
            if row.get("total_units"):
                fi.add_extended_profile_finding(
                    slug, "total_identified_units", str(row["total_units"]),
                    confidence="low", source_owner="Franchise Times 2026 Restaurant 200",
                    source_type="trade_press_ranking", finding_type="marketplace_reported",
                )
            for brand_row in row.get("brands") or []:
                value = f"{brand_row.get('brand')} -- {brand_row.get('unit_count')} units (Franchise Times 2026 Restaurant 200, unverified)"
                fi.add_extended_profile_finding(
                    slug, "brand_relationships", value, confidence="low",
                    source_owner="Franchise Times 2026 Restaurant 200",
                    source_type="trade_press_ranking", finding_type="marketplace_reported",
                )
        results.append({"action": action, "slug": slug, "name": name, "brand_count": len(row.get("brands") or []), "source": "franchise_times_2026_restaurant_200_seed"})
    return results


def seed(*, dry_run: bool = False) -> dict:
    graph = ei._read_graph()
    by_id = ei._index_by_id(graph.get("entities") or [])
    ecosystem_results = _import_ecosystem_operators(graph, by_id, dry_run=dry_run)
    hierarchy_results = _import_hierarchy_seed(dry_run=dry_run)
    return {
        "dry_run": dry_run,
        "ecosystem_operators_processed": len(ecosystem_results),
        "hierarchy_seed_rows_processed": len(hierarchy_results),
        "ecosystem_results": ecosystem_results,
        "hierarchy_results": hierarchy_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed franchisee_finder from existing ecosystem operators + the Franchise Times 200 extract")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = seed(dry_run=args.dry_run)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
