#!/usr/bin/env python3
"""Build the Radar + Pursuit intelligence coverage and research-gap matrix.

This is read-only with respect to canonical intelligence. It inventories every
watched entity, account-research record, and tracked vendor, then publishes a
single cache the scheduler and Daily Brief can use to choose the next research
target. Broad Radar coverage is preserved; Pursuit entities simply receive a
higher refresh priority.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

import baseline_research_gate as gate
import competitor_intelligence_common as cic
import ecosystem_intelligence as ei
import rb_core as core

CACHE_PATH = core.CACHE_DIR / "intelligence_coverage_matrix.json"
ACCOUNT_REGISTRY = core.SYSTEM_DIR / "account_research" / "_portfolio" / "account_research_registry.json"

BRAND_DIMENSIONS = (
    "business_context", "leadership", "technology", "strategy",
    "ownership_financial", "footprint_growth", "contract_timing",
    "procurement_rfp", "hiring", "relationships", "buying_triggers", "risks",
)
VENDOR_DIMENSIONS = (
    "market_position", "products_and_categories", "source_evidence",
    "competitive_posture", "leadership", "financial_health", "customer_exposure",
    "implementation_risk", "product_direction",
)


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _entity_pool(graph: dict) -> list[dict]:
    by_id = {row.get("id"): row for row in graph.get("entities") or []}
    selected: dict[str, dict] = {}
    for watch in graph.get("watch_list") or []:
        entity = by_id.get(watch.get("entity_id"))
        if entity:
            selected[entity["id"]] = entity
    registry = _load(ACCOUNT_REGISTRY)
    for row in registry.get("accounts") or registry.get("registry") or []:
        account_id = row.get("account_id") or ""
        slug = account_id.removeprefix("acct-")
        entity = by_id.get("brand-" + slug)
        if entity:
            selected[entity["id"]] = entity
    for row in cic.load_registry().get("registry") or []:
        try:
            profile = cic.load_competitor(row.get("competitor_slug") or "")
        except (FileNotFoundError, ValueError):
            continue
        competitor = profile.get("competitor") or {}
        entity = by_id.get(competitor.get("vendor_entity_id"))
        if entity:
            selected[entity["id"]] = entity
    return list(selected.values())


def build(today: date | None = None) -> dict:
    today = today or date.today()
    graph = ei._read_graph()
    rows = []
    for entity in _entity_pool(graph):
        name = entity.get("name") or entity.get("id")
        coverage = gate._baseline_coverage(name, entity)
        present = set(coverage.get("dimensions") or [])
        desired = VENDOR_DIMENSIONS if (entity.get("entity_type") or entity.get("type")) == "vendor" else BRAND_DIMENSIONS
        strategic_value = gate._strategic_value(entity, graph)
        lane = "pursuit" if strategic_value >= 30 else "radar"
        completeness = round(100 * len(present & set(desired)) / len(desired))
        rows.append({
            "entity_id": entity.get("id"), "entity_name": name,
            "entity_type": entity.get("entity_type") or entity.get("type") or "unknown",
            "research_lane": lane, "strategic_value": strategic_value,
            "coverage_pct": completeness, "dimensions_present": sorted(present),
            "dimensions_missing": [dimension for dimension in desired if dimension not in present],
        })
    rows.sort(key=lambda row: (row["research_lane"] != "pursuit", row["coverage_pct"], -row["strategic_value"], row["entity_name"].casefold()))
    return {
        "contract": "rb_intelligence_coverage_matrix_v1",
        "date": today.isoformat(), "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entity_count": len(rows),
        "pursuit_count": sum(row["research_lane"] == "pursuit" for row in rows),
        "radar_count": sum(row["research_lane"] == "radar" for row in rows),
        "fully_covered_count": sum(row["coverage_pct"] == 100 for row in rows),
        "entities": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    args = parser.parse_args()
    report = build(date.fromisoformat(args.date) if args.date else None)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
