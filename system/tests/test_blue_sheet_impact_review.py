"""
test_blue_sheet_impact_review.py

RB-2026-08-23: impact_review.py existed as a standalone library with zero
test coverage, verified once by hand against the live Pollo Campero folder
(build_dry_run_scenario / GAP_REPORT_2026-08-21.md item 4) then reverted.
This turns that manual dry run into an automated, repeatable test against a
synthetic fixture account, not the live folder -- so it can run in CI
without touching real customer data.

render_mod.render()/validate_mod.validate() are mocked here rather than
exercised for real: their own correctness (openpyxl template rendering) is
a separate concern from process_event()'s split/apply/queue/log logic,
which is what this file actually tests.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "blue_sheets" / "_engine"))

import common  # noqa: E402
import impact_review as ir  # noqa: E402


def _fixture_account_dir(tmp_path: Path, slug: str, *, activated: bool = True) -> Path:
    acct_dir = tmp_path / "accounts" / slug
    (acct_dir / "logs").mkdir(parents=True)
    (tmp_path / "_portfolio").mkdir(parents=True)

    common.save_json(acct_dir / "account.json", {
        "account_id": f"acct-{slug}",
        "buying_influences": [{"name": "Diego Haro", "title": "Controller", "role_etuc": "influencer"}],
        "technology_stack": [],
    })
    common.save_json(acct_dir / "brand_profile.json", {"brand_id": f"brand-{slug}"})
    common.save_json(acct_dir / "actions.json", [])
    common.save_json(acct_dir / "contradictions.json", [])
    common.save_json(acct_dir / "source_index.json", {})
    (acct_dir / "evidence.jsonl").write_text("", encoding="utf-8")
    (acct_dir / "logs" / "change_log.jsonl").write_text("", encoding="utf-8")

    common.save_json(tmp_path / "_portfolio" / "customers_prospects_registry.json", {
        "registry": [{
            "account_id": f"acct-{slug}",
            "workbook_path": f"accounts/{slug}/current/x.xlsx" if activated else None,
        }],
    })
    common.save_json(tmp_path / "_portfolio" / "review_queue.json", {"pending_reviews": []})
    return acct_dir


def _event_evidence(evidence_id: str) -> dict:
    return {
        "evidence_id": evidence_id, "account_id": "acct-test", "opportunity_ids": [],
        "source_type": "email_inbound", "durable_source_id": "TEST-FIXTURE-NOT-REAL",
        "source_author": "Test Author", "participants": ["Test Author"],
        "event_date": "2026-08-23", "ingestion_date": "2026-08-23",
        "excerpt": "Synthetic test event.", "extracted_claims": [],
        "evidence_class": "direct_customer_correspondence", "confidence": "high",
        "scope": "account", "limitations": "", "contradiction_links": [],
        "processing_version": "test",
    }


def test_refuses_to_touch_an_unactivated_account(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CUSTOMERS_PROSPECTS_ROOT", tmp_path)
    monkeypatch.setattr(common, "ROOT", tmp_path)  # review_queue.json/change_log.jsonl still derive from ROOT
    _fixture_account_dir(tmp_path, "unactivated-co", activated=False)

    result = ir.process_event("unactivated-co", [], _event_evidence("ev-1"), apply=True)
    assert result["activated"] is False
    assert "not activated" in result["blocked"]


def test_dry_run_splits_safe_vs_governance_gated_without_writing(tmp_path, monkeypatch):
    """Mirrors the real dry-run scenario: a factual title change (safe,
    registry-sourced) alongside hearsay about buying-influence authority
    (governance-gated -- role_etuc is judgment-sensitive per spec Section 8)."""
    monkeypatch.setattr(common, "CUSTOMERS_PROSPECTS_ROOT", tmp_path)
    monkeypatch.setattr(common, "ROOT", tmp_path)  # review_queue.json/change_log.jsonl still derive from ROOT
    acct_dir = _fixture_account_dir(tmp_path, "test-co")

    changes = [
        ir.ProposedChange(
            json_path="buying_influences[0].title", target_file="account.json",
            new_value="Finance Manager", new_status="confirmed", new_confidence="high",
            evidence_id="ev-1", as_of="2026-08-23",
            reason="Diego confirmed his own title change directly.",
        ),
        ir.ProposedChange(
            json_path="buying_influences[0].role_etuc", target_file="account.json",
            new_value="economic_buyer", new_status="hypothesis", new_confidence="low",
            evidence_id="ev-1", as_of="2026-08-23",
            reason="Third-party hearsay about another person's authority -- not a direct EBI claim.",
        ),
    ]

    result = ir.process_event("test-co", changes, _event_evidence("ev-1"), apply=False)

    assert result["activated"] is True
    assert result["blocked"] is None
    assert len(result["safe_applied"]) == 1
    assert result["safe_applied"][0]["path"] == "buying_influences[0].title"
    assert len(result["proposed"]) == 1
    assert result["proposed"][0]["path"] == "buying_influences[0].role_etuc"

    # apply=False must write nothing -- not evidence, not account.json, not the queue.
    assert (acct_dir / "evidence.jsonl").read_text() == ""
    account_after = common.load_json(acct_dir / "account.json")
    assert account_after["buying_influences"][0]["title"] == "Controller"


def test_apply_true_writes_evidence_and_queues_gated_change_for_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CUSTOMERS_PROSPECTS_ROOT", tmp_path)
    monkeypatch.setattr(common, "ROOT", tmp_path)  # review_queue.json/change_log.jsonl still derive from ROOT
    acct_dir = _fixture_account_dir(tmp_path, "test-co")

    changes = [
        ir.ProposedChange(
            json_path="buying_influences[0].role_etuc", target_file="account.json",
            new_value="economic_buyer", new_status="hypothesis", new_confidence="low",
            evidence_id="ev-1", as_of="2026-08-23",
            reason="Third-party hearsay -- gated.",
        ),
    ]

    with patch.object(ir, "render_mod") as fake_render, patch.object(ir, "validate_mod") as fake_validate:
        result = ir.process_event("test-co", changes, _event_evidence("ev-1"), apply=True)

    # A purely-gated change list has nothing to safe-apply, so render/validate
    # should never even be invoked (process_event only calls them when
    # safe_applied is non-empty).
    fake_render.render.assert_not_called()
    fake_validate.validate.assert_not_called()

    assert result["blocked"] is None
    assert result["safe_applied"] == []
    assert len(result["proposed"]) == 1

    evidence = common.load_jsonl(acct_dir / "evidence.jsonl")
    assert len(evidence) == 1
    assert evidence[0]["evidence_id"] == "ev-1"

    review_queue = common.load_json(tmp_path / "_portfolio" / "review_queue.json")
    assert len(review_queue["pending_reviews"]) == 1
    assert review_queue["pending_reviews"][0]["path"] == "buying_influences[0].role_etuc"
    assert review_queue["pending_reviews"][0]["status"] == "pending"

    # Governance-gated change must NOT have been written to account.json.
    account_after = common.load_json(acct_dir / "account.json")
    assert account_after["buying_influences"][0]["role_etuc"] == "influencer"


def test_apply_true_with_safe_change_renders_and_validates_then_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CUSTOMERS_PROSPECTS_ROOT", tmp_path)
    monkeypatch.setattr(common, "ROOT", tmp_path)  # review_queue.json/change_log.jsonl still derive from ROOT
    acct_dir = _fixture_account_dir(tmp_path, "test-co")

    changes = [
        ir.ProposedChange(
            json_path="buying_influences[0].title", target_file="account.json",
            new_value="Finance Manager", new_status="confirmed", new_confidence="high",
            evidence_id="ev-1", as_of="2026-08-23", reason="Direct confirmation.",
        ),
    ]

    with patch.object(ir, "render_mod") as fake_render, patch.object(ir, "validate_mod") as fake_validate:
        fake_validate.validate.return_value = ([], True)
        result = ir.process_event("test-co", changes, _event_evidence("ev-1"), apply=True)

    fake_render.render.assert_called_once_with("test-co")
    fake_validate.validate.assert_called_once_with("test-co")
    assert result["blocked"] is None

    account_after = common.load_json(acct_dir / "account.json")
    assert account_after["buying_influences"][0]["title"] == "Finance Manager"

    change_log = common.load_jsonl(acct_dir / "logs" / "change_log.jsonl")
    assert len(change_log) == 1
    assert change_log[0]["auto_applied"] == ["buying_influences[0].title"]


def test_failed_post_apply_validation_is_surfaced_not_silently_swallowed(tmp_path, monkeypatch):
    """Section 15: never publish a partially corrupted workbook -- a
    validation failure after a safe apply must come back as `blocked`, loud,
    not disappear."""
    monkeypatch.setattr(common, "CUSTOMERS_PROSPECTS_ROOT", tmp_path)
    monkeypatch.setattr(common, "ROOT", tmp_path)  # review_queue.json/change_log.jsonl still derive from ROOT
    _fixture_account_dir(tmp_path, "test-co")

    changes = [
        ir.ProposedChange(
            json_path="buying_influences[0].title", target_file="account.json",
            new_value="Finance Manager", new_status="confirmed", new_confidence="high",
            evidence_id="ev-1", as_of="2026-08-23", reason="Direct confirmation.",
        ),
    ]

    with patch.object(ir, "render_mod") as fake_render, patch.object(ir, "validate_mod") as fake_validate:
        fake_validate.validate.return_value = (["formula error in N6"], False)
        result = ir.process_event("test-co", changes, _event_evidence("ev-1"), apply=True)

    assert result["blocked"] is not None
    assert "validation failed" in result["blocked"]
    assert result["safe_applied"] == []
