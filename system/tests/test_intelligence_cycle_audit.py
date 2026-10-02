from datetime import date
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
import intelligence_cycle_audit as audit


def test_complete_signal_to_downstream_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "CACHE_PATH", tmp_path / "audit.json")
    ramifications = {"material_events_assessed": 1, "ramifications": [{
        "ramification_id": "ram-1", "signal_id": "signal-1", "entity_name": "Brand A",
        "source_refs": ["source-1"], "baseline_status": "established",
        "review_lens": "leadership_and_decision_network", "recommended_follow_up": "Verify remit.",
        "affected_artifacts": [
            {"artifact": "daily_intelligence_report", "action": "report_current_event"},
            {"artifact": "account_plan_or_blue_sheet", "action": "review_if_active"},
        ],
    }]}
    downstream = {"items": [{"impact_id": "impact-1", "source_id": "ram-1",
                              "target_artifact": "account_plan_or_blue_sheet",
                              "proposed_action": "review_if_active", "status": "pending_review"}]}
    result = audit.build(ramifications=ramifications, downstream=downstream, today=date(2026, 9, 14))
    assert result["status"] == "complete"
    assert result["complete_traces"] == 1


def test_missing_evidence_and_disposition_degrades_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "CACHE_PATH", tmp_path / "audit.json")
    ramifications = {"material_events_assessed": 1, "ramifications": [{
        "ramification_id": "ram-1", "signal_id": "signal-1", "entity_name": "Brand A",
        "source_refs": [], "baseline_status": "established", "review_lens": "financial",
        "recommended_follow_up": "Compare guidance.",
        "affected_artifacts": [{"artifact": "account_plan_or_blue_sheet", "action": "review_if_active"}],
    }]}
    result = audit.build(ramifications=ramifications, downstream={"items": []}, today=date(2026, 9, 14))
    assert result["status"] == "degraded"
    assert set(result["traces"][0]["missing_stages"]) == {
        "source_refs", "downstream:account_plan_or_blue_sheet"}


def test_quiet_day_is_explicit_not_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "CACHE_PATH", tmp_path / "audit.json")
    result = audit.build(ramifications={"ramifications": []}, downstream={}, today=date(2026, 9, 14))
    assert result["status"] == "no_material_events"
    assert result["incomplete_traces"] == 0
