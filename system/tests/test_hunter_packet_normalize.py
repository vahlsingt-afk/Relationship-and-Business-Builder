from __future__ import annotations

import sys
import json
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import hunter_cycle  # noqa: E402
import hunter_packet_normalize  # noqa: E402


def _job():
    return {
        "directive": {
            "prepared_at": "2026-10-02T13:00:00Z",
            "packet_requirements": {
                "prior_state_as_of": "2026-10-02T12:59:00Z",
                "known_gap_ids": ["gap:company:brand-example:leadership"],
                "discovery_domains": ["leadership"],
                "payload_schema": "rb.brand_company_profile.v1",
            },
            "plan": {
                "playbook": "enterprise_account_profile",
                "depth": "standard",
                "primary_mission": "Fill gaps.",
                "required_modules": ["leadership"],
                "budget": {"max_pages": 10},
                "exit_criteria": ["Leadership checked"],
            },
            "resource_plan": {
                "status": "authorized", "reason": "available", "chat_research_status": "authorized",
                "codex_work_status": "blocked", "codex_work_reason": "not needed",
                "capacity_snapshot": {"captured_at": "2026-10-02T12:58:00Z", "five_hour_used_pct": None, "weekly_used_pct": None, "hours_to_weekly_reset": None},
                "preferred_execution_tier": "chatgpt_deep_research_economy", "max_targets": None,
                "recommended_batch_targets": 1, "five_hour_capacity_ceiling_pct": 0,
                "weekly_reserve_pct": None, "five_hour_reserve_pct": None, "reset_credit_allowed": False,
            },
            "gap_manifest": {"targets": [{"target_key": "company:brand-example", "display_name": "Example", "entity_type": "restaurant_brand", "priority": "enterprise_primary"}]},
        },
        "before_snapshot": {"targets": {}},
    }


def test_normalizer_uses_job_owned_envelope_and_aliases():
    packet, receipt = hunter_packet_normalize.normalize(_job(), {
        "packet_id": "example-1", "created_at": "2026-10-02T13:05:00Z", "status": "completed",
        "source_ledger": [{"source_id": "S001", "canonical_url": "https://example.com", "productive": True}],
        "findings": [{"finding_id": "F001", "source_ids": ["S001"]}],
        "research_modules": ["leadership"], "payload": {"collection": "records", "items": [{"target_key": "company:brand-example"}]},
    })
    assert receipt["applied"] is True
    assert packet["packet_id"].startswith("hunter-")
    assert packet["targets"][0]["target_key"] == "company:brand-example"
    assert packet["source_ledger"][0]["source_id"] == "src-s001"
    assert packet["findings"][0]["source_ids"] == ["src-s001"]
    assert packet["research_modules"][0]["name"] == "leadership"
    assert packet["payload"]["records"][0]["target_key"] == "company:brand-example"


def test_finalize_reports_malformed_packet_instead_of_crashing():
    receipt = hunter_cycle.finalize(_job(), {"research_modules": ["leadership"]})
    assert receipt["ok"] is False
    assert receipt["validation"]["valid"] is False
    assert receipt["normalization"]["applied"] is True


def test_packet_artifact_loader_accepts_json_txt_and_markdown(tmp_path):
    packet = {"packet_id": "hunter-example", "findings": []}
    direct = tmp_path / "response.txt"
    direct.write_text(json.dumps(packet), encoding="utf-8")
    assert hunter_cycle.load_packet_artifact(direct) == packet

    markdown = tmp_path / "response.md"
    markdown.write_text("Research result:\n```json\n" + json.dumps(packet) + "\n```\n", encoding="utf-8")
    assert hunter_cycle.load_packet_artifact(markdown) == packet


def test_packet_artifact_loader_rejects_missing_json_object(tmp_path):
    artifact = tmp_path / "response.txt"
    artifact.write_text("No structured packet was returned.", encoding="utf-8")
    try:
        hunter_cycle.load_packet_artifact(artifact)
    except ValueError as error:
        assert "no valid JSON object" in str(error)
    else:
        raise AssertionError("invalid Hunter artifact should fail closed")
