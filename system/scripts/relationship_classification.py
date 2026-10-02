#!/usr/bin/env python3
"""
relationship_classification.py — Restaurant Tech Relationship Classification Model.

Classifies vendor-to-brand relationships in ecosystem_intelligence.json into one
of six levels of strategic significance (pilot -> strategic platform), so that
"vendor X is a customer of brand Y" claims (logos, vendor lists, case studies)
are not treated as equivalent to system-wide enterprise deployments.

See system/SCHEMAS.md ("Relationship Classification Model") for the taxonomy
and the heuristic rules implemented here.

CLI:
    python3 system/scripts/relationship_classification.py classify-all --dry-run
    python3 system/scripts/relationship_classification.py classify-all
    python3 system/scripts/relationship_classification.py classify-all --force
    python3 system/scripts/relationship_classification.py classify --vendor PAR --brand "Burger King"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import ecosystem_intelligence as eco  # noqa: E402

# Levels follow system/SCHEMAS.md "Relationship Classification Model".
LEVELS: dict[int, dict[str, Any]] = {
    1: {
        "name": "pilot",
        "label": "Pilot",
        "description": "Limited test, proof of concept, evaluation deployment, or temporary trial.",
        "confidence_band": "low",
    },
    2: {
        "name": "single_franchisee",
        "label": "Single Franchisee",
        "description": "One operator group, limited store count, no franchisor involvement.",
        "confidence_band": "low_medium",
    },
    3: {
        "name": "multi_franchisee_adoption",
        "label": "Multi-Franchisee Adoption",
        "description": "Multiple operator groups, repeatable deployment pattern, growing footprint.",
        "confidence_band": "medium",
    },
    4: {
        "name": "preferred_vendor",
        "label": "Preferred Vendor",
        "description": "Recommended by franchisor, approved integration partner, conference participation, preferred pricing.",
        "confidence_band": "medium_high",
    },
    5: {
        "name": "standardized_platform",
        "label": "Standardized Platform",
        "description": "Corporate standard, required for new locations, mandated conversion path, system-wide deployment.",
        "confidence_band": "high",
    },
    6: {
        "name": "strategic_platform",
        "label": "Strategic Platform",
        "description": "Mission-critical infrastructure, deep integration into operating model, joint roadmap development, difficult to replace.",
        "confidence_band": "very_high",
    },
}

LEVEL_BY_NAME = {v["name"]: k for k, v in LEVELS.items()}

# Confidence bands map to the schema's confidence.level enum (low/medium/high/critical),
# with `score` carrying the finer-grained position within that band. Owned by
# confidence_calibration.py (2026-09-25) -- shared with ecosystem_intelligence
# .py's conflict engine and every automated writer, not just this module's own
# classification logic. Kept as _CONFIDENCE_BAND_TO_SCHEMA here (not renamed at
# every call site) so nothing else in this file needs to change.
from confidence_calibration import CONFIDENCE_BAND_TO_SCHEMA as _CONFIDENCE_BAND_TO_SCHEMA  # noqa: E402

# Claim types with no operational evidence -- never auto-classified.
UNCLASSIFIABLE_CLAIM_TYPES = {"logo_or_customer_page", "reference_only", "unknown", None}


def _confidence_block(band: str, rationale: str) -> dict[str, Any]:
    level, score = _CONFIDENCE_BAND_TO_SCHEMA[band]
    return {"level": level, "score": score, "rationale": rationale, "review_after": None}


def _classification(level: int, rationale: str, basis: list[str], today: str) -> dict[str, Any]:
    spec = LEVELS[level]
    return {
        "level": level,
        "level_name": spec["name"],
        "confidence": _confidence_block(spec["confidence_band"], rationale),
        "rationale": rationale,
        "basis": basis,
        "last_assessed": today,
    }


def estimate_penetration_pct(deployed_units: float | None, brand_unit_count: float | None) -> float | None:
    """Best-effort system penetration estimate when penetration_pct is not provided directly."""
    if not deployed_units or not brand_unit_count:
        return None
    if brand_unit_count <= 0:
        return None
    pct = (deployed_units / brand_unit_count) * 100.0
    return round(min(pct, 100.0), 1)


def classify_relationship(rel: dict[str, Any], brand_attrs: dict[str, Any] | None = None, *, today: str | None = None) -> dict[str, Any] | None:
    """Return a relationship_classification block, or None if evidence is insufficient.

    Heuristic only -- never auto-assigns level 6 (strategic_platform), which
    requires manual analyst confirmation. See system/SCHEMAS.md.
    """
    today = today or eco._today()
    deploy = rel.get("deployment") or {}
    claim_type = rel.get("deployment_claim_type") or deploy.get("deployment_claim_type")
    vendor_role = rel.get("vendor_role")
    stage = deploy.get("stage")
    deployed_units = deploy.get("deployed_units")
    scope_unit_count = rel.get("scope_unit_count") or deploy.get("scope_unit_count")
    customer_operator = rel.get("customer_operator") or deploy.get("customer_operator")
    estimated_franchise_groups = deploy.get("estimated_franchise_groups")
    corporate_vs_franchise = deploy.get("corporate_vs_franchise")
    penetration_pct = deploy.get("penetration_pct")
    brand_unit_count = (brand_attrs or {}).get("unit_count")

    if penetration_pct is None:
        penetration_pct = estimate_penetration_pct(deployed_units, brand_unit_count)

    basis: list[str] = []
    if claim_type:
        basis.append(f"deployment_claim_type={claim_type}")
    if vendor_role:
        basis.append(f"vendor_role={vendor_role}")
    if stage:
        basis.append(f"stage={stage}")
    if scope_unit_count:
        basis.append(f"scope_unit_count={scope_unit_count}")
    if estimated_franchise_groups:
        basis.append(f"estimated_franchise_groups={estimated_franchise_groups}")
    if penetration_pct is not None:
        basis.append(f"penetration_pct={penetration_pct}")
    if corporate_vs_franchise:
        basis.append(f"corporate_vs_franchise={corporate_vs_franchise}")

    # Insufficient evidence -- marketing penetration only, leave unclassified.
    if claim_type in UNCLASSIFIABLE_CLAIM_TYPES:
        return None

    # Level 1: Pilot.
    if claim_type == "pilot" or vendor_role == "pilot" or stage == "pilot":
        return _classification(
            1,
            "Deployment evidence describes a pilot, proof of concept, or trial with no committed scale.",
            basis,
            today,
        )

    # Level 5: Standardized Platform -- system-wide, corporate-mandated, or near-full penetration.
    if claim_type == "systemwide_deployment" or stage in {"full_deployment", "systemwide_deployment"}:
        if corporate_vs_franchise == "corporate_mandated" or penetration_pct is None or penetration_pct >= 80:
            return _classification(
                5,
                "Deployment evidence describes a system-wide or corporate-mandated rollout at high system penetration.",
                basis,
                today,
            )
        # Systemwide claim but penetration evidence suggests it's still rolling out broadly --
        # treat as multi-franchisee adoption pending higher penetration confirmation.
        return _classification(
            3,
            "Deployment claimed as system-wide but observed penetration is below 80%; treated as a broad multi-franchisee rollout pending further confirmation.",
            basis,
            today,
        )

    # Level 4: Preferred Vendor -- approved/recommended status, not tied to a specific operator.
    if claim_type == "approved_vendor" or vendor_role in {"approved_hardware_vendor", "payment_device_vendor"}:
        return _classification(
            4,
            "Deployment evidence describes franchisor-approved or preferred-vendor status rather than a specific operator deployment.",
            basis,
            today,
        )

    # Levels 2/3: franchisee-scoped deployments -- distinguish single vs multi operator.
    if claim_type in {"limited_operator_deployment", "franchisee_deployment", "partial_deployment"} or vendor_role in {
        "franchisee_deployment",
        "regional_deployment",
    }:
        is_multi = (estimated_franchise_groups or 0) > 1
        if not is_multi and penetration_pct is not None and penetration_pct >= 25:
            is_multi = True
        if is_multi:
            return _classification(
                3,
                "Deployment evidence indicates multiple franchise/operator groups or a meaningfully growing footprint.",
                basis,
                today,
            )
        if customer_operator or scope_unit_count or claim_type == "limited_operator_deployment":
            return _classification(
                2,
                "Deployment evidence is scoped to a single operator/franchisee group with limited store count and no franchisor involvement indicated.",
                basis,
                today,
            )
        return _classification(
            3,
            "Deployment evidence indicates an active partial rollout across the franchise system without a single named operator.",
            basis,
            today,
        )

    # Reseller/service-provider relationships and anything else with operational evidence
    # but no clear store-count signal -- leave unclassified rather than guess.
    return None


def _brand_attrs(graph: dict, rel: dict) -> dict[str, Any]:
    by_id = eco._index_by_id(graph["entities"])
    brand = by_id.get(rel.get("from_entity_id"), {})
    return brand.get("attributes") or {}


def _should_overwrite(existing: dict[str, Any] | None, new: dict[str, Any], force: bool) -> bool:
    if existing is None:
        return True
    if force:
        return True
    # Sticky: never overwrite a manually-assigned level 6, or any classification
    # that already carries higher confidence than the heuristic would assign.
    if existing.get("level") == 6:
        return False
    existing_score = (existing.get("confidence") or {}).get("score") or 0
    new_score = (new.get("confidence") or {}).get("score") or 0
    return new_score >= existing_score


def classify_all(args) -> int:
    graph = eco._read_graph()
    today = eco._today()
    changes: list[dict[str, Any]] = []
    for rel in graph["relationships"]:
        if rel.get("relationship_type") != "uses_vendor_for_category":
            continue
        proposed = classify_relationship(rel, _brand_attrs(graph, rel), today=today)
        if proposed is None:
            continue
        existing = rel.get("relationship_classification")
        if not _should_overwrite(existing, proposed, args.force):
            continue
        if existing == proposed:
            continue
        changes.append({
            "relationship_id": rel["id"],
            "from": existing,
            "to": proposed,
        })
        if not args.dry_run:
            rel["relationship_classification"] = proposed

    if args.dry_run:
        print(json.dumps({"would_change": len(changes), "changes": changes}, indent=2))
        return 0

    if not changes:
        print(json.dumps({"changed": 0}, indent=2))
        return 0

    eco._write_graph(graph)
    print(json.dumps({"changed": len(changes), "changes": changes}, indent=2))
    return 0


def classify_one(args) -> int:
    graph = eco._read_graph()
    by_id = eco._index_by_id(graph["entities"])
    vendor_text = args.vendor.lower()
    brand_text = args.brand.lower()
    for rel in graph["relationships"]:
        if rel.get("relationship_type") != "uses_vendor_for_category":
            continue
        vendor = by_id.get(rel.get("to_entity_id"), {})
        brand = by_id.get(rel.get("from_entity_id"), {})
        if vendor_text not in str(vendor.get("name", "")).lower():
            continue
        if brand_text not in str(brand.get("name", "")).lower():
            continue
        result = classify_relationship(rel, brand.get("attributes") or {})
        print(json.dumps({
            "relationship_id": rel["id"],
            "vendor": vendor.get("name"),
            "brand": brand.get("name"),
            "existing": rel.get("relationship_classification"),
            "proposed": result,
        }, indent=2))
        return 0
    print(json.dumps({"error": "no matching relationship found"}))
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restaurant tech relationship classification")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("classify-all", help="Classify all uses_vendor_for_category relationships")
    p.add_argument("--dry-run", action="store_true", help="Print proposed changes without writing")
    p.add_argument("--force", action="store_true", help="Overwrite existing classifications, including manual level 6")
    p.set_defaults(func=classify_all)

    p = sub.add_parser("classify", help="Preview classification for a single vendor/brand relationship")
    p.add_argument("--vendor", required=True)
    p.add_argument("--brand", required=True)
    p.set_defaults(func=classify_one)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
