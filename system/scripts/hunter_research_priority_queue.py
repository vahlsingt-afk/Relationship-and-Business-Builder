#!/usr/bin/env python3
"""hunter_research_priority_queue.py — 2026-10-02.

The judgment/execution split Todd asked for: RBB's own intelligence cycle
(this script, run at the end of morning_pipeline.py's scan, right after
intelligence_coverage_matrix.py) decides WHICH companies matter most and
leaves that order in a local file; Hunter's automations (Codex directing
ChatGPT) just work the list in order -- they stop re-deriving their own
priority from a narrow slice of the gap data each run.

Reuses the real "CoS judgment" signal that already exists and already
runs every cycle -- baseline_research_gate._strategic_value(), the exact
function intelligence_coverage_matrix.py uses to rank its own pursuit/
radar lanes (watch_list membership, Technomic chain-size rank tier,
vendor bonus) -- rather than inventing a second, competing notion of
"important." The one deliberate extension: for competitors, this also
surfaces real top-10-per-tech-category placement (hunter_competitor_
category_targets.py's own ranking) as context, not folded silently into
the score, since that is real market-position evidence _strategic_value
doesn't see.

intelligence_coverage_matrix.py's own pool is narrower than this (only
entities already in watch_list/account_research/competitor registries) --
this script instead ranks the FULL universe Hunter's gap manifests
already cover (every tracked restaurant brand, every tracked competitor),
which is what "next 100 companies to research" means when most of them
aren't being watched yet.

Usage:
    python3 hunter_research_priority_queue.py [--limit 100] [--output PATH]
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
import baseline_research_gate as gate  # noqa: E402
import hunter_gap_manifest as hgm  # noqa: E402
import hunter_competitor_category_targets as hcc  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402
import franchisee_intelligence_common as fic  # noqa: E402

SCHEMA = "rb.hunter_research_priority_queue.v1"
QUEUE_PATH = core.CACHE_DIR / "hunter_priority_queue.json"

_TECH_STACK_FIELDS = {"technology_stack"}


def _brand_playbook(gaps: list[dict]) -> str:
    fields = {g["field"] for g in gaps}
    if fields and fields <= _TECH_STACK_FIELDS:
        return "technology_stack_reconstruction"
    return "enterprise_account_profile"


def _rank_brands(graph: dict, by_id: dict) -> list[dict]:
    manifest = hgm.build_manifest(universe="brands")
    rows = []
    for target in manifest["targets"]:
        if not target["gaps"]:
            continue
        entity_id = target["target_key"].removeprefix("company:")
        entity = by_id.get(entity_id)
        strategic_value = gate._strategic_value(entity, graph) if entity else 0
        rows.append({
            "target_key": target["target_key"],
            "display_name": target["display_name"],
            "entity_type": "restaurant_brand",
            "strategic_value": strategic_value,
            "gap_count": len(target["gaps"]),
            "gap_fields": sorted({g["field"] for g in target["gaps"]}),
            "suggested_playbook": _brand_playbook(target["gaps"]),
            "context": {"unit_count": (target["current_state"] or {}).get("unit_count")},
        })
    return rows


def _rank_competitors(graph: dict, by_id: dict) -> list[dict]:
    manifest = hgm.build_manifest(universe="competitors")
    placements = hcc.top_competitors_by_category(graph, top=10, by_id=by_id)
    rows = []
    for target in manifest["targets"]:
        if not target["gaps"]:
            continue
        slug = target["target_key"].removeprefix("competitor:")
        try:
            comp = cic.load_competitor(slug)["competitor"]
        except FileNotFoundError:
            comp = {}
        vendor_entity_id = comp.get("vendor_entity_id")
        entity = by_id.get(vendor_entity_id) if vendor_entity_id else None
        strategic_value = gate._strategic_value(entity, graph) if entity else 0
        category_placements = placements.get(vendor_entity_id, {}).get("placements") if vendor_entity_id else None
        rows.append({
            "target_key": target["target_key"],
            "display_name": target["display_name"],
            "entity_type": "restaurant_technology_company",
            "strategic_value": strategic_value,
            "gap_count": len(target["gaps"]),
            "gap_fields": sorted({g["field"] for g in target["gaps"]}),
            "suggested_playbook": "competitive_positioning",
            "context": {
                "top10_category_count": len(category_placements) if category_placements else 0,
                "top10_best_rank": min((p["rank"] for p in category_placements), default=None) if category_placements else None,
            },
        })
    return rows


def _franchisee_org_strategic_value(org: dict) -> int:
    """Parallel to baseline_research_gate._strategic_value(), for
    franchisee organizations -- which have no ecosystem_intelligence.json
    entity of their own to score. Uses the one real, comparable signal
    this domain has: total_identified_units (tiered the same shape as
    _strategic_value's brand-rank tiers), plus a multi-brand-operator
    bonus mirroring that function's own vendor bonus. Deliberately kept
    OUT of baseline_research_gate.py itself -- that function's contract is
    specifically about real ecosystem_intelligence.json entities; a
    franchisee organization is a different kind of thing with a different
    evidence basis, and conflating the two would make _strategic_value's
    own behavior for brands/vendors harder to reason about."""
    score = 0
    units_raw = (org.get("total_identified_units") or {}).get("value")
    try:
        units = int(str(units_raw).replace(",", "")) if units_raw else 0
    except ValueError:
        units = 0
    if units >= 500:
        score += 45
    elif units >= 100:
        score += 30
    elif units >= 20:
        score += 15
    elif units > 0:
        score += 5
    if org.get("hierarchy_level") == "multi_brand_franchisee_group":
        score += 15
    return min(100, score)


def _rank_franchisees(graph: dict, by_id: dict) -> list[dict]:
    manifest = hgm.build_manifest(universe="franchisees")
    rows = []
    for target in manifest["targets"]:
        if not target["gaps"]:
            continue
        if target["entity_type"] == "restaurant_brand":
            # Discovery gap -- the target IS the brand itself; reuse its
            # real strategic_value exactly like _rank_brands does.
            entity_id = (target["current_state"] or {}).get("brand_entity_id")
            entity = by_id.get(entity_id) if entity_id else None
            strategic_value = gate._strategic_value(entity, graph) if entity else 0
            suggested_playbook = "franchisee_discovery"
            context = {"rank": (target["current_state"] or {}).get("rank")}
        else:
            slug = target["target_key"].removeprefix("franchisee:")
            try:
                org = fic.load_franchisee(slug)["organization"]
            except FileNotFoundError:
                org = {}
            strategic_value = _franchisee_org_strategic_value(org)
            suggested_playbook = "franchisee_organization_profile"
            context = {
                "confidence_tier": target["current_state"].get("confidence_tier"),
                "total_identified_units": (org.get("total_identified_units") or {}).get("value"),
            }
        rows.append({
            "target_key": target["target_key"],
            "display_name": target["display_name"],
            "entity_type": target["entity_type"],
            "strategic_value": strategic_value,
            "gap_count": len(target["gaps"]),
            "gap_fields": sorted({g["field"] for g in target["gaps"]}),
            "suggested_playbook": suggested_playbook,
            "context": context,
        })
    return rows


def build(*, limit: int = 100) -> dict:
    graph = ei._read_graph()
    by_id = ei._index_by_id(graph.get("entities") or [])
    rows = _rank_brands(graph, by_id) + _rank_competitors(graph, by_id) + _rank_franchisees(graph, by_id)
    rows.sort(key=lambda r: (
        -r["strategic_value"], -r["gap_count"],
        -(r["context"].get("top10_category_count") or 0), r["display_name"].casefold(),
    ))
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    limited = rows[:max(0, limit)]
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ranking_method": (
            "strategic_value desc, then open gap_count desc, then real top-10-tech-category "
            "placement count desc (competitors only, context signal, never folded into "
            "strategic_value itself), then name. strategic_value is baseline_research_gate."
            "_strategic_value() (watch_list membership, Technomic chain-size rank tier, vendor "
            "bonus) for brands/competitors/franchise-discovery targets (the target IS a real "
            "brand entity in every case), and a parallel total-identified-units/multi-brand-"
            "operator-bonus proxy (_franchisee_org_strategic_value()) for franchisee_organization "
            "profile targets, which have no ecosystem_intelligence.json entity of their own."
        ),
        "candidate_pool_count": len(rows),
        "limit": limit,
        "queue": limited,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Stack-rank the next N companies/brands for Hunter to research")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output")
    args = parser.parse_args()
    report = build(limit=args.limit)
    rendered = json.dumps(report, indent=2, default=str) + "\n"
    output_path = Path(args.output) if args.output else QUEUE_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
