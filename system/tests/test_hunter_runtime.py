from __future__ import annotations

import copy
import json
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import hunter  # noqa: E402
from test_hunter_contract import _valid_packet  # noqa: E402


def _codes(packet: dict) -> set[str]:
    return {error["code"] for error in hunter.validate_packet(packet)["errors"]}


def test_playbook_plan_resolves_depth_budget_and_payload():
    result = hunter.plan("technology_replacement_lifecycle", "forensic")
    assert result["depth"] == "forensic"
    assert result["budget"]["max_pages"] == 100
    assert result["payload_schema"] == "rb.technology_lifecycle_research.v1"
    assert "deployment_phasing" in result["required_modules"]


def test_resource_plan_allows_chat_when_codex_usage_is_unavailable():
    result = hunter.resource_plan(
        "standard", five_hour_used_pct=None, weekly_used_pct=None,
        hours_to_weekly_reset=None,
    )
    assert result["status"] == "authorized"
    assert result["chat_research_status"] == "authorized"
    assert result["codex_work_status"] == "blocked"
    assert result["max_targets"] is None


def test_codex_limit_does_not_block_chat_research():
    result = hunter.resource_plan(
        "standard", five_hour_used_pct=76, weekly_used_pct=20,
        hours_to_weekly_reset=72,
    )
    assert result["status"] == "authorized"
    assert result["chat_research_status"] == "authorized"
    assert result["codex_work_status"] == "blocked"


def test_resource_plan_prefers_cheap_deep_research_and_bounds_batch():
    result = hunter.resource_plan(
        "deep", five_hour_used_pct=20, weekly_used_pct=25,
        hours_to_weekly_reset=72, deep_research_available=True,
    )
    assert result["status"] == "authorized"
    assert result["preferred_execution_tier"] == "chatgpt_deep_research_economy"
    assert result["max_targets"] is None
    assert result["recommended_batch_targets"] == 2
    assert result["five_hour_capacity_ceiling_pct"] <= 40
    assert result["reset_credit_allowed"] is False


def test_codex_reserve_violation_does_not_block_chat_research():
    result = hunter.resource_plan(
        "standard", five_hour_used_pct=65, weekly_used_pct=20,
        hours_to_weekly_reset=72,
    )
    assert result["status"] == "authorized"
    assert result["codex_work_status"] == "blocked"


def test_no_chat_path_requires_codex_capacity():
    result = hunter.resource_plan(
        "standard", five_hour_used_pct=None, weekly_used_pct=None,
        hours_to_weekly_reset=None, deep_research_available=False,
    )
    assert result["status"] == "blocked"
    assert result["chat_research_status"] == "unavailable"
    assert result["codex_work_status"] == "blocked"


def test_valid_packet_passes_semantic_gate():
    assert hunter.validate_packet(_valid_packet())["valid"] is True


def test_benchmark_logo_wall_cannot_prove_customer():
    packet = _valid_packet()
    packet["source_ledger"][0]["source_type"] = "logo wall"
    packet["findings"][0]["evidence_status"] = "supported"
    assert "discovery_source_overpromoted" in _codes(packet)


def test_benchmark_announcement_cannot_prove_deployment():
    packet = _valid_packet()
    packet["source_ledger"][0]["source_type"] = "press release"
    packet["findings"][0].update({
        "claim": "The platform was deployed across 500 restaurants.",
        "scope": "enterprise",
        "evidence_status": "supported",
    })
    assert "announcement_not_deployment" in _codes(packet)


def test_benchmark_franchisee_evidence_cannot_be_brand_wide():
    packet = _valid_packet()
    packet["findings"][0].update({
        "claim": "One franchisee installed the product.",
        "scope": "brand-wide",
    })
    assert "scope_text_conflict" in _codes(packet)


def test_benchmark_old_case_study_cannot_be_current_verified():
    packet = _valid_packet()
    packet["findings"][0].update({
        "as_of": "2019-01-01",
        "temporal_status": "current_verified",
    })
    assert "stale_claim_marked_current" in _codes(packet)


def test_benchmark_integration_page_cannot_prove_customer_use():
    packet = _valid_packet()
    packet["source_ledger"][0]["source_type"] = "integration page"
    packet["findings"][0]["claim"] = "The restaurant uses the integration."
    assert "integration_not_customer_proof" in _codes(packet)


def test_syndicated_pages_count_as_one_evidence_chain():
    packet = _valid_packet()
    duplicate = copy.deepcopy(packet["source_ledger"][0])
    duplicate.update({
        "source_id": "src-syndicated-copy",
        "url": "https://news.example.net/copied-release",
        "canonical_url": "https://news.example.net/copied-release",
    })
    packet["source_ledger"].append(duplicate)
    packet["findings"][0]["source_ids"].append("src-syndicated-copy")
    packet["quality"].update({"pages_opened": 2, "productive_pages": 2, "findings_with_sources": 1})
    scores = hunter.score_packet(packet)
    assert scores["independent_evidence_chains"] == 1


def test_every_known_gap_requires_an_outcome():
    packet = _valid_packet()
    packet["gap_outcomes"] = []
    assert "missing_gap_outcome" in _codes(packet)


def test_new_discovery_requires_novelty_against_rbb_state():
    packet = _valid_packet()
    packet["findings"][0].update({
        "contribution_type": "new_data_point",
        "gap_ids": [],
        "novelty_rationale": None,
    })
    assert "new_data_without_novelty" in _codes(packet)


def test_known_gap_finding_must_reference_supplied_gap():
    packet = _valid_packet()
    packet["findings"][0]["gap_ids"] = ["gap-invented"]
    assert "unknown_gap_reference" in _codes(packet)


def test_change_and_cos_cross_references_are_validated():
    packet = _valid_packet()
    packet["change_events"] = [{
        "change_event_id": "chg-example",
        "target_key": "competitor:example",
        "change_type": "product",
        "summary": "A product changed.",
        "prior_state": "old",
        "new_state": "new",
        "effective_at": "2026-09-01",
        "observed_at": "2026-10-02T12:02:00Z",
        "materiality": "high",
        "finding_ids": ["f-does-not-exist"],
        "source_ids": ["src-example"],
        "confidence_pct": 80,
    }]
    assert "change_unknown_finding" in _codes(packet)
    assert "material_change_unrouted" in _codes(packet)


def test_paid_source_cannot_support_a_finding():
    packet = _valid_packet()
    packet["source_ledger"][0]["access_tier"] = "paid_subscription"
    packet["source_ledger"][0]["access_status"] = "paywalled"
    packet["source_ledger"][0]["productive"] = False
    packet["quality"]["productive_pages"] = 0
    assert "nonfree_source_supports_finding" in _codes(packet)


def test_paid_recommendation_only_for_unresolved_known_gap():
    packet = _valid_packet()
    packet["gap_outcomes"][0]["status"] = "filled"
    packet["paid_source_recommendations"] = [{
        "recommendation_id": "paid-example",
        "source_name": "Paid Database",
        "source_url": "https://paid.example.com",
        "unresolved_gap_ids": ["gap-customer-currentness"],
        "unique_value": "A private installed-base census unavailable in the checked public record.",
        "decision_improved": "Would improve exact market sizing.",
        "free_sources_checked": ["src-example"],
        "free_query_families_tried": ["operator filings", "vendor case studies"],
        "recommendation_status": "recommended_not_accessed",
    }]
    assert "paid_recommendation_for_filled_gap" in _codes(packet)


def test_context_builder_returns_prior_gaps_without_mutating(monkeypatch, tmp_path):
    coverage = tmp_path / "coverage.json"
    coverage.write_text(json.dumps({"targets": {"company:brand-a": {"name": "A", "last_covered_at": None}}}))
    drop = tmp_path / "drop"
    drop.mkdir()
    (drop / "packet.json").write_text(json.dumps({
        "packet_id": "hunter-old",
        "targets": ["company:brand-a"],
        "unanswered_questions": ["Who owns it?"],
        "conflicts": [{"resolution_status": "unresolved", "topic": "owner"}],
        "source_ledger": [
            {"url": "https://dead.example/x", "productive": False},
            {"url": "https://dead.example/y", "productive": False},
        ],
    }))
    monkeypatch.setattr(hunter, "COVERAGE_PATH", coverage)
    monkeypatch.setattr(hunter, "DROP_DIR", drop)
    result = hunter.build_context(["company:brand-a"])
    assert result["targets"]["company:brand-a"]["name"] == "A"
    assert result["repeatedly_unproductive_domains"] == ["dead.example"]
    assert result["unanswered_questions"] == ["Who owns it?"]


def test_feedback_receipt_is_append_only(monkeypatch, tmp_path):
    path = tmp_path / "feedback.jsonl"
    monkeypatch.setattr(hunter, "FEEDBACK_PATH", path)
    hunter.record_feedback("hunter-one", "partially_accepted", 2, 1, 1, "scope corrected")
    receipt = json.loads(path.read_text().strip())
    assert receipt["findings_accepted"] == 2
    assert receipt["findings_corrected"] == 1
