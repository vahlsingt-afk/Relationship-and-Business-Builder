#!/usr/bin/env python3
"""Convert material evidence into structured, reviewable ramifications."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import account_background_brief as abb  # noqa: E402
import baseline_research_gate as brg  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402
import ecosystem_brief as eb  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import rb_core as core  # noqa: E402

CACHE_PATH = core.CACHE_DIR / "intelligence_ramifications.json"

LENSES = {
    "acquisition": ("ownership_and_vendor_continuity", "Verify ownership, leadership, contract, and platform-continuity effects."),
    "funding": ("investment_capacity_and_priorities", "Review whether capital changes technology priorities or expansion capacity."),
    "exec-change": ("leadership_and_decision_network", "Verify the executive, remit, prior relationships, and likely decision influence."),
    "leadership_change": ("leadership_and_decision_network", "Verify the executive, remit, prior relationships, and likely decision influence."),
    "financial": ("operating_pressure_and_investment_capacity", "Compare reported performance with the financial baseline and active opportunity timing."),
    "earnings_release": ("operating_pressure_and_investment_capacity", "Compare guidance, traffic, margins, and investment language with prior periods."),
    "vendor_relationship_formed": ("technology_stack_and_competitive_position", "Verify deployment scope, category, timing, and incumbent displacement."),
    "vendor_claimed_customer_relationship": ("technology_stack_and_competitive_position", "Verify whether the claim is enterprise-wide, franchised, piloted, or historical."),
    "partnership": ("ecosystem_dependency_and_access", "Verify scope and identify affected account, partner, and competitor relationships."),
    "closure": ("financial_and_operational_pressure", "Assess whether closures are isolated optimization or a broader account-risk signal."),
    "expansion": ("growth_capacity_and_deployment", "Assess rollout geography, franchise implications, and technology deployment requirements."),
}


def _norm(value: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in value).split())


def _competitor_names() -> set[str]:
    try:
        registry = cic.load_registry()
        return {_norm(row.get("display_name") or "") for row in registry.get("registry", [])}
    except Exception:
        return set()


def _artifact_impacts(name: str, entity: dict, signal_type: str) -> list[dict]:
    impacts = [{"artifact": "daily_intelligence_report", "action": "report_current_event"}]
    try:
        _slug, exists = abb.resolve_account(name)
    except Exception:
        exists = False
    if exists:
        impacts.append({"artifact": "account_background_brief", "action": "review_against_baseline"})
    if _norm(name) in _competitor_names():
        impacts.append({"artifact": "competitor_profile", "action": "queue_evidence_review"})
    if signal_type in {"vendor_relationship_formed", "vendor_claimed_customer_relationship", "partnership"}:
        impacts.append({"artifact": "ecosystem_technology_stack", "action": "verify_relationship_change"})
    if signal_type in {"acquisition", "funding", "exec-change", "leadership_change", "financial", "earnings_release"}:
        impacts.append({"artifact": "account_plan_or_blue_sheet", "action": "review_if_active"})
    return impacts


def build(today: date) -> dict:
    graph = ei._read_graph()
    entities = {e.get("id"): e for e in graph.get("entities", [])}
    rows = []
    historical_excluded = 0
    for signal in graph.get("signals", []):
        if str(signal.get("captured_at") or "")[:10] != today.isoformat():
            continue
        signal_type = str(signal.get("signal_type") or "general")
        confidence = str((signal.get("confidence") or {}).get("level") or "unknown")
        if not eb._is_material_signal(confidence, signal_type):
            continue
        event_at = str(signal.get("event_at") or "")[:10]
        if event_at:
            try:
                if not core.is_fresh_pub_date(date.fromisoformat(event_at), today, is_corporate=True):
                    historical_excluded += 1
                    continue
            except ValueError:
                pass
        lens, follow_up = LENSES.get(signal_type, (
            "baseline_change_review", "Compare the evidence with the existing baseline before changing downstream records."))
        entity_ids = [eid for eid in signal.get("entities", []) if eid]
        for entity_id in entity_ids:
            entity = entities.get(entity_id) or {}
            name = entity.get("name") or entity_id
            coverage = brg._baseline_coverage(name, entity)
            impacts = _artifact_impacts(name, entity, signal_type)
            priority = (30 if not coverage.get("established") else 0) + (20 if confidence == "high" else 10) + len(impacts) * 5
            rows.append({
                "ramification_id": f"ram-{signal.get('id')}-{entity_id}",
                "entity_id": entity_id, "entity_name": name,
                "signal_id": signal.get("id"), "signal_type": signal_type,
                "evidence_summary": signal.get("summary"), "event_at": signal.get("event_at"),
                "confidence": confidence,
                "baseline_status": "established" if coverage.get("established") else "insufficient",
                "baseline_dimensions": coverage.get("dimensions") or [],
                "change_classification": "baseline_gap_trigger" if not coverage.get("established") else "current_event_against_baseline",
                "review_lens": lens, "recommended_follow_up": follow_up,
                "affected_artifacts": impacts,
                "connected_entities": [entities.get(eid, {}).get("name", eid) for eid in entity_ids if eid != entity_id],
                "source_refs": signal.get("source_ids") or signal.get("sources") or [],
                "priority_score": priority,
            })
    rows.sort(key=lambda row: (-row["priority_score"], row["entity_name"].lower()))
    report = {"date": today.isoformat(), "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "material_events_assessed": len({row["signal_id"] for row in rows}),
              "ramifications_generated": len(rows),
              "baseline_gap_ramifications": sum(row["baseline_status"] == "insufficient" for row in rows),
              "historical_events_excluded": historical_excluded,
              "artifact_reviews_recommended": sum(len(row["affected_artifacts"]) for row in rows),
              "ramifications": rows}
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def build_section(today: date | None = None) -> dict:
    return build(today or date.today())
