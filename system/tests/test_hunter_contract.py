from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


SYSTEM_DIR = Path(__file__).resolve().parent.parent
SCHEMA_PATH = SYSTEM_DIR / "schemas" / "hunter_research_packet.schema.json"


def _valid_packet() -> dict:
    return {
        "schema": "rb.hunter_research_packet.v1",
        "packet_id": "hunter-20261002-120000-example",
        "research_agent": "Hunter",
        "cycle": {
            "cycle_type": "candidate_validation",
            "playbook": "customer_deployment_validation",
            "depth": "forensic",
            "objective": "Validate one public customer claim.",
            "requested_by": "test",
            "prior_state_as_of": "2026-10-02T11:59:00Z",
            "known_gap_ids": ["gap-customer-currentness"],
            "discovery_domains": ["customer deployments", "product changes"],
            "resource_plan": {
                "status": "authorized",
                "reason": "ChatGPT Deep Research is available.",
                "chat_research_status": "authorized",
                "codex_work_status": "authorized",
                "codex_work_reason": "Codex usage permits bounded support.",
                "capacity_snapshot": {
                    "captured_at": "2026-10-02T11:58:00Z",
                    "five_hour_used_pct": 20,
                    "weekly_used_pct": 25,
                    "hours_to_weekly_reset": 72,
                },
                "preferred_execution_tier": "chatgpt_deep_research_economy",
                "max_targets": None,
                "recommended_batch_targets": 1,
                "five_hour_capacity_ceiling_pct": 20,
                "reset_credit_allowed": False,
            },
            "budget": {"max_pages": 10},
            "exit_criteria": ["Claim checked"],
        },
        "targets": [{
            "target_key": "competitor:example",
            "display_name": "Example",
            "entity_type": "competitor",
            "priority": "explicit",
        }],
        "started_at": "2026-10-02T12:00:00Z",
        "completed_at": "2026-10-02T12:05:00Z",
        "status": "complete",
        "public_sources_only": True,
        "research_modules": [{"name": "customers", "required": True, "status": "complete", "notes": None}],
        "questions": ["Is the customer claim supported?"],
        "source_ledger": [{
            "source_id": "src-example",
            "url": "https://example.com/case-study",
            "canonical_url": "https://example.com/case-study",
            "title": "Case study",
            "publisher": "Example",
            "source_type": "case study",
            "source_owner": "vendor",
            "access_tier": "free_public",
            "directness": "direct",
            "evidence_chain_id": "chain-vendor-case-study",
            "published_at": "2026-09-01",
            "accessed_at": "2026-10-02T12:01:00Z",
            "access_status": "accessible",
            "productive": True,
            "archive_url": None,
            "notes": None,
        }],
        "query_ledger": [{
            "query": "Example restaurant customer case study",
            "purpose": "Find direct evidence",
            "executed_at": "2026-10-02T12:00:30Z",
            "productive": True,
            "notes": None,
        }],
        "findings": [{
            "finding_id": "f-example-customer",
            "target_key": "competitor:example",
            "module": "customers",
            "claim": "The vendor reports a restaurant customer relationship.",
            "contribution_type": "fills_known_gap",
            "gap_ids": ["gap-customer-currentness"],
            "novelty_rationale": None,
            "evidence_status": "reported",
            "confidence_pct": 80,
            "observed_at": "2026-10-02T12:02:00Z",
            "as_of": "2026-09-01",
            "temporal_status": "current_probable",
            "scope": "unknown deployment scope",
            "commercial_relevance": "competitive_relevance",
            "source_ids": ["src-example"],
            "is_vendor_claim": True,
            "is_inference": False,
            "inference_premises": [],
            "limitations": "No operator-controlled corroboration.",
            "recommended_follow_up": "Seek a customer-controlled source.",
        }],
        "gap_outcomes": [{
            "gap_id": "gap-customer-currentness",
            "status": "partially_filled",
            "finding_ids": ["f-example-customer"],
            "reason": "Vendor evidence found; operator corroboration remains missing.",
        }],
        "change_events": [],
        "mutation_proposals": [],
        "cos_handoffs": [],
        "paid_source_recommendations": [],
        "negative_findings": [],
        "conflicts": [],
        "unanswered_questions": ["Is the deployment current?"],
        "method_feedback": {
            "productive_patterns": ["Named case-study query"],
            "unproductive_patterns": [],
            "proposed_method_changes": [],
            "next_cycle_recommendations": ["Search operator site"],
        },
        "quality": {
            "pages_opened": 1,
            "productive_pages": 1,
            "primary_source_pages": 0,
            "findings_count": 1,
            "findings_with_sources": 1,
            "required_modules_complete": True,
            "validation_status": "valid",
        },
        "resource_usage": {
            "selected_execution_tier": "chatgpt_deep_research_economy",
            "chatgpt_deep_research_calls": 1,
            "codex_model_calls": 1,
            "premium_reasoning_calls": 0,
            "targets_attempted": 1,
            "targets_completed": 1,
            "budget_outcome": "within_budget",
            "escalation_reasons": [],
        },
        "payload_schema": "rb.competitor_platform_research.v1",
        "payload": {"findings": []},
    }


def _errors(packet: dict):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(packet))


def test_hunter_packet_contract_accepts_valid_packet():
    assert _errors(_valid_packet()) == []


def test_hunter_packet_requires_public_source_boundary():
    packet = _valid_packet()
    packet["public_sources_only"] = False
    assert any(list(error.absolute_path) == ["public_sources_only"] for error in _errors(packet))


def test_hunter_finding_requires_source_reference():
    packet = _valid_packet()
    packet["findings"][0]["source_ids"] = []
    assert any(list(error.absolute_path)[-1:] == ["source_ids"] for error in _errors(packet))
