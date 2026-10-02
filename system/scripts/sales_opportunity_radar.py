#!/usr/bin/env python3
"""Unify exposure, buying-window hypotheses, and outcome calibration.

All outputs are analytical caches. No hypothesis becomes a canonical claim and
no opportunity stage changes without the existing review/authorization paths.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import competitive_vulnerability as vulnerability
import ecosystem_intelligence as ei
import entity_identity
import rb_core as core

CACHE_PATH = core.CACHE_DIR / "sales_opportunity_radar.json"
STATE_PATH = core.CACHE_DIR / "sales_opportunity_radar_state.json"
COVERAGE_PATH = core.CACHE_DIR / "intelligence_coverage_matrix.json"
BLUE_SHEET_REGISTRY = core.SYSTEM_DIR.parent / "blue_sheets" / "_portfolio" / "blue_sheet_registry.json"

SOURCE_RELIABILITY = {
    "operator_filing_earnings_investor": 0.95,
    "primary_operator_statement": 0.90,
    "vendor_filing_earnings_investor": 0.85,
    "primary_vendor_announcement": 0.75,
    "credible_trade_reporting_direct_attribution": 0.80,
    "credible_trade_reporting": 0.70,
    "google_news_search": 0.60,
    "linkedin": 0.55,
}


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _resolve_entity(name: str, entities: list[dict]) -> dict | None:
    exact = entity_identity.find_entity(name, entities)
    if exact:
        return exact
    normalized = re.sub(r"[^a-z0-9]+", "", str(name or "").casefold())
    matches = [
        entity for entity in entities
        if any(re.sub(r"[^a-z0-9]+", "", term.casefold()) == normalized
               for term in entity_identity.identity_terms(entity))
    ]
    return matches[0] if len(matches) == 1 else None


def _confidence_score(value) -> float:
    if isinstance(value, dict):
        if isinstance(value.get("score"), (int, float)):
            return float(value["score"])
        value = value.get("level")
    return {"high": 0.85, "medium": 0.65, "low": 0.40}.get(str(value or "").lower(), 0.50)


def build_exposure_graph(graph: dict) -> dict:
    by_id = {row.get("id"): row for row in graph.get("entities") or []}
    brands: dict[str, dict] = {}
    for rel in graph.get("relationships") or []:
        if rel.get("relationship_type") != "uses_vendor_for_category":
            continue
        brand = by_id.get(rel.get("from_entity_id")) or {}
        vendor = by_id.get(rel.get("to_entity_id")) or {}
        if (brand.get("entity_type") or brand.get("type")) != "brand" or not vendor:
            continue
        source_assertions = rel.get("source_assertions") or []
        reliability = max([
            SOURCE_RELIABILITY.get(str(src.get("source_authority") or src.get("source_type") or ""), 0.50)
            for src in source_assertions
        ] or [_confidence_score(rel.get("confidence"))])
        row = brands.setdefault(brand["id"], {
            "brand_id": brand["id"], "brand_name": brand.get("name"), "vendors": [],
        })
        row["vendors"].append({
            "vendor_id": vendor.get("id"), "vendor_name": vendor.get("name"),
            "category": rel.get("category"), "product": rel.get("product"),
            "status": rel.get("status"), "deployment_stage": (rel.get("deployment") or {}).get("stage"),
            "risk": rel.get("risk") or "unknown", "confidence": round(reliability, 2),
            "relationship_id": rel.get("id"),
        })
    rows = list(brands.values())
    for row in rows:
        row["vendors"].sort(key=lambda vendor: (vendor.get("category") or "", vendor.get("vendor_name") or ""))
    rows.sort(key=lambda row: row.get("brand_name") or "")
    return {"brand_count": len(rows), "relationship_count": sum(len(row["vendors"]) for row in rows), "brands": rows}


def _hypothesis_for(brand: dict, exposures: dict, missing: list[str]) -> dict:
    categories = list(dict.fromkeys(brand.get("potential_categories") or []))
    vendors = exposures.get(brand.get("entity_id")) or []
    active_vendors = [row for row in vendors if row.get("status") in {"active", "current"}]
    incumbent_text = ", ".join(sorted({row.get("vendor_name") for row in active_vendors if row.get("vendor_name")})) or "incumbent not yet verified"
    score = int(brand.get("vulnerability_score") or 0)
    evidence = [signal.get("title") for signal in (brand.get("signals") or [])[:3] if signal.get("title")]
    return {
        "entity_id": brand.get("entity_id"), "entity": brand.get("entity"),
        "hypothesis_type": "buying_window",
        "hypothesis": f"{brand.get('entity')} may be entering a technology-evaluation window; current exposure: {incumbent_text}.",
        "evaluation_likelihood_score": score,
        "potential_categories": categories,
        "incumbent_exposure": active_vendors,
        "supporting_evidence": evidence,
        "missing_evidence": missing,
        "disconfirming_questions": [
            "Is there evidence the current platform is contractually secure or recently renewed?",
            "Is the change limited to staffing rather than a funded technology program?",
            "Has the incumbent publicly confirmed an active rollout or expansion?",
        ],
        "confidence": brand.get("confidence") or "low",
        "posture": "act_today" if score >= 70 else "research_first" if score >= 35 else "monitor",
    }


def _active_sales_ids(entities: list[dict]) -> set[str]:
    registry = _load(BLUE_SHEET_REGISTRY)
    ids = set()
    for row in registry.get("accounts") or registry.get("registry") or []:
        if row.get("status") in {"active", "current"}:
            names = list(row.get("aliases") or [])
            names.append(str(row.get("account_id") or "").removeprefix("acct-").replace("-", " "))
            for name in names:
                matched = _resolve_entity(name, entities)
                if matched:
                    ids.add(matched.get("id"))
                    break
    return ids


def _calibrate(hypotheses: list[dict], today: date, entities: list[dict]) -> dict:
    prior = _load(STATE_PATH)
    states = prior.get("entities") or {}
    active_sales = _active_sales_ids(entities)
    current_ids = set()
    for row in hypotheses:
        entity_id = row.get("entity_id") or row.get("entity")
        current_ids.add(entity_id)
        legacy_key = row.get("entity")
        legacy = states.pop(legacy_key, {}) if legacy_key != entity_id else {}
        state = states.setdefault(entity_id, legacy or {"first_seen": today.isoformat(), "observations": 0})
        state.update({
            "entity": row.get("entity"), "last_seen": today.isoformat(),
            "observations": int(state.get("observations") or 0) + 1,
            "max_score": max(int(state.get("max_score") or 0), int(row.get("evaluation_likelihood_score") or 0)),
            "latest_score": row.get("evaluation_likelihood_score"),
            "outcome": "active_pursuit" if entity_id in active_sales else state.get("outcome") or "unresolved",
        })
    for entity_id, state in states.items():
        if entity_id not in current_ids and state.get("outcome") == "unresolved":
            state["quiet_cycles"] = int(state.get("quiet_cycles") or 0) + 1
    payload = {"contract": "rb_sales_radar_calibration_v1", "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "entities": states}
    STATE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    resolved = [row for row in states.values() if row.get("outcome") != "unresolved"]
    return {"tracked_candidates": len(states), "resolved_outcomes": len(resolved), "active_pursuit_outcomes": sum(row.get("outcome") == "active_pursuit" for row in resolved)}


def build(today: date | None = None) -> dict:
    today = today or date.today()
    graph = ei._read_graph()
    exposure = build_exposure_graph(graph)
    exposure_by_brand = {row["brand_id"]: row["vendors"] for row in exposure["brands"]}
    coverage = _load(COVERAGE_PATH)
    missing_by_id = {row.get("entity_id"): row.get("dimensions_missing") or [] for row in coverage.get("entities") or []}
    vuln = vulnerability.build_report(today=today)
    graph_entities = graph.get("entities") or []
    hypothesis_inputs = []
    for row in vuln.get("brands") or []:
        enriched = dict(row)
        if not enriched.get("entity_id"):
            matched = _resolve_entity(enriched.get("entity") or "", graph_entities)
            enriched["entity_id"] = (matched or {}).get("id")
        hypothesis_inputs.append(enriched)
    hypotheses = [_hypothesis_for(row, exposure_by_brand, missing_by_id.get(row.get("entity_id"), [])) for row in hypothesis_inputs]
    calibration = _calibrate(hypotheses, today, graph_entities)
    evaluated_ids = {row.get("entity_id") for row in hypotheses}
    negative_evidence = [
        {"entity_id": row.get("entity_id"), "entity": row.get("entity_name"), "finding": "checked_no_elevated_buying_window_signal", "date": today.isoformat()}
        for row in coverage.get("entities") or [] if row.get("entity_id") not in evaluated_ids
    ]
    return {
        "contract": "rb_sales_opportunity_radar_v1", "date": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "exposure_graph": exposure, "buying_window_hypotheses": hypotheses,
        "negative_evidence": negative_evidence, "calibration": calibration,
        "policy": "hypotheses_and_scores_only; canonical claims and opportunity stages remain review-first",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    args = parser.parse_args()
    report = build(date.fromisoformat(args.date) if args.date else None)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "contract": report["contract"], "date": report["date"],
        "brands_with_vendor_exposure": report["exposure_graph"]["brand_count"],
        "vendor_relationships": report["exposure_graph"]["relationship_count"],
        "buying_window_hypotheses": len(report["buying_window_hypotheses"]),
        "negative_evidence_records": len(report["negative_evidence"]),
        "calibration": report["calibration"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
