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


_COVERAGE_BUCKETS = ("restaurant_brand", "restaurant_technology_company", "franchisee")

# 2026-10-03 (CLAUDE_HANDOFF_RB_HUNTER_GATHERER_END_TO_END_DEFECTS, Defect
# 4): confirmed live -- one global sort put restaurant brands at ranks
# 1-7, franchisee organizations/discovery at ranks 8-15, and the first
# competitor (of several with real open gaps) at rank 16, starving
# competitor research for many cycles under a top-to-bottom consumer.
# A franchisee-discovery row's own entity_type is "restaurant_brand"
# (the target IS the brand, per _rank_franchisees above), but the
# handoff doc groups "franchisee discovery or franchisee-organization
# profiles" as one bucket distinct from brand research -- so bucketing
# here keys off suggested_playbook, not raw entity_type.
_PLAYBOOK_TO_BUCKET = {
    "enterprise_account_profile": "restaurant_brand",
    "technology_stack_reconstruction": "restaurant_brand",
    "competitive_positioning": "restaurant_technology_company",
    "franchisee_discovery": "franchisee",
    "franchisee_organization_profile": "franchisee",
}

# Reserve this many of the EARLIEST queue slots, round-robin across the
# three buckets, before falling back to the plain global sort for
# everything else -- guarantees every enabled family appears within the
# first few ranks instead of being pushed down an arbitrary number of
# places by raw strategic_value. Explicit and auditable (allocation_
# policy below), not a silent reshuffle of the underlying ranking: within
# each bucket, rows keep the exact same relative order the global sort
# would have given them.
_RESERVED_WINDOW_PER_BUCKET = 3


def _coverage_bucket(row: dict) -> str:
    return _PLAYBOOK_TO_BUCKET.get(row["suggested_playbook"], "restaurant_brand")


def _global_sort_key(row: dict):
    return (
        -row["strategic_value"], -row["gap_count"],
        -(row["context"].get("top10_category_count") or 0), row["display_name"].casefold(),
    )


def _allocate_coverage(rows: list[dict]) -> list[dict]:
    """Guarantee each bucket's top _RESERVED_WINDOW_PER_BUCKET rows (by its
    own strategic_value order) a seat in the reserved window, then sort
    that whole reserved set by the normal global key -- so a trivial pool
    (e.g. one brand, one competitor) still ranks purely by strategic_value
    exactly as before (nothing to balance, nothing changes), while a large
    pool where many brands/franchisees outrank every competitor still
    guarantees up to _RESERVED_WINDOW_PER_BUCKET competitors land inside
    the window instead of being pushed down behind all of them. Everyone
    else falls back to the plain global sort, unchanged from before this
    policy existed. No persisted state is needed -- unlike deep_research_
    coverage.py's cumulative cross-run allocator, this queue is
    regenerated fresh from the full candidate pool on every run rather
    than incrementally consumed across days, so a purely positional
    reservation within a single build() call is sufficient."""
    by_bucket: dict[str, list[dict]] = {b: [] for b in _COVERAGE_BUCKETS}
    for row in rows:
        by_bucket[_coverage_bucket(row)].append(row)
    for bucket_rows in by_bucket.values():
        bucket_rows.sort(key=_global_sort_key)

    reserved: list[dict] = []
    reserved_keys: set[str] = set()
    for bucket in _COVERAGE_BUCKETS:
        for row in by_bucket[bucket][:_RESERVED_WINDOW_PER_BUCKET]:
            row = dict(row)
            row["coverage_bucket"] = bucket
            row["coverage_allocation"] = "reserved"
            reserved.append(row)
            reserved_keys.add(row["target_key"])
    reserved.sort(key=_global_sort_key)

    remainder = []
    for bucket in _COVERAGE_BUCKETS:
        for row in by_bucket[bucket]:
            if row["target_key"] in reserved_keys:
                continue
            row = dict(row)
            row["coverage_bucket"] = bucket
            row["coverage_allocation"] = "ranked"
            remainder.append(row)
    remainder.sort(key=_global_sort_key)
    return reserved + remainder


def build(*, limit: int = 100) -> dict:
    graph = ei._read_graph()
    by_id = ei._index_by_id(graph.get("entities") or [])
    rows = _rank_brands(graph, by_id) + _rank_competitors(graph, by_id) + _rank_franchisees(graph, by_id)
    rows = _allocate_coverage(rows)
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
            "profile targets, which have no ecosystem_intelligence.json entity of their own. "
            "Within each coverage bucket this ordering is authoritative and untouched -- see "
            "allocation_policy for how the three buckets are then interleaved."
        ),
        "allocation_policy": {
            "reserved_window_per_bucket": _RESERVED_WINDOW_PER_BUCKET,
            "buckets": list(_COVERAGE_BUCKETS),
            "bucket_definition": (
                "restaurant_brand = enterprise_account_profile/technology_stack_reconstruction; "
                "restaurant_technology_company = competitive_positioning; "
                "franchisee = franchisee_discovery or franchisee_organization_profile"
            ),
            "note": (
                "Each bucket's own top reserved_window_per_bucket rows (by strategic_value) are "
                "guaranteed a seat in the reserved window, then that whole reserved set is sorted "
                "by strategic_value together -- a small/balanced pool ranks exactly as before "
                "(nothing to guarantee), while a large pool where many brands/franchisees outrank "
                "every competitor still guarantees up to reserved_window_per_bucket competitors "
                "land inside the window instead of being starved behind all of them. Every "
                "remaining position falls back to the plain global strategic_value sort across "
                "all buckets, unchanged from before this policy existed."
            ),
        },
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
