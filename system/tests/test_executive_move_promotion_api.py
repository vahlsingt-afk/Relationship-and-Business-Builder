"""
test_executive_move_promotion_api.py — RB-2026-09-11.

API-layer coverage for the executive-move capture endpoints:
  GET  /executive-moves/proposals (getExecutiveMoveProposals)
  POST /confirm kind="executive_move" (confirmProposal dispatch)

Same direct-function-call + monkeypatch isolation pattern as
test_ownership_promotion_api.py/test_priority_account_publisher_api.py.
"""
from __future__ import annotations

import json
import unittest.mock
from pathlib import Path

import pytest
from fastapi import HTTPException

from system.api import server


@pytest.fixture()
def isolated_exec_moves(tmp_path: Path, monkeypatch):
    graph_path = tmp_path / "ecosystem_intelligence.json"
    store_path = tmp_path / "executive_move_candidates.json"
    baseline_path = tmp_path / "baseline_index.json"
    snap_dir = tmp_path / "_snapshots"

    graph_path.write_text(json.dumps({
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-11",
        "entities": [
            {"id": "brand-dairy-queen", "name": "Dairy Queen", "entity_type": "brand", "aliases": [],
             "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]},
        ],
        "relationships": [], "signals": [], "sources": [],
        "assessments": [], "user_relevance": [], "strategic_recommendations": [],
    }), encoding="utf-8")
    baseline_path.write_text("[]", encoding="utf-8")

    monkeypatch.setattr(server, "_auth", lambda *a, **k: None)
    emp = server.executive_move_promotion
    monkeypatch.setattr(emp.ei.core, "ECOSYSTEM_INTELLIGENCE_PATH", graph_path)
    monkeypatch.setattr(emp, "STORE_PATH", store_path)
    monkeypatch.setattr(emp.core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(emp.core, "SNAPSHOTS_DIR", snap_dir)

    patch_validator = unittest.mock.patch("mutations.subprocess.run")
    mock_run = patch_validator.start()
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = ""
    mock_run.return_value.stderr = ""
    yield {"graph_path": graph_path, "store_path": store_path, "baseline_path": baseline_path}
    patch_validator.stop()


def _seed_pending_candidate(emp, cid="brand-dairy-queen::sig-test", proposed_name="Phil Crawford", matched_contact_id=None):
    store = {"candidates": {cid: {
        "candidate_id": cid, "status": "proposed_pending_confirmation",
        "entity_id": "brand-dairy-queen", "entity_name": "Dairy Queen",
        "proposed_name": proposed_name, "proposed_title": "chief technology officer",
        "proposed_confidence": "extracted_from_text" if proposed_name else "unknown",
        "action": "update_existing_contact" if matched_contact_id else "create_new_contact",
        "matched_contact_id": matched_contact_id,
        "source_type": "credible_reporting", "source_title": "test", "source_url": None,
        "evidence_excerpt": "Dairy Queen names Phil Crawford chief technology officer.",
        "origin": "signal_scan", "origin_ref": "sig-test", "evidence_date": "2026-01-10",
        "detected_at": "2026-09-11T00:00:00Z", "resolved_at": None,
    }}}
    emp._save_store(store)
    return cid


def test_get_endpoint_lists_pending_candidate(isolated_exec_moves):
    emp = server.executive_move_promotion
    _seed_pending_candidate(emp)
    result = server.get_executive_move_proposals(x_api_key=None)
    assert len(result["proposals"]) == 1
    assert result["proposals"][0]["entity_name"] == "Dairy Queen"


def test_confirm_proposal_creates_new_contact(isolated_exec_moves):
    emp = server.executive_move_promotion
    cid = _seed_pending_candidate(emp)

    result = server.confirm_proposal(
        server.ConfirmProposalBody(kind="executive_move", id=cid, confirmed=True),
        x_api_key=None,
    )
    assert result["confirmed"] is True
    assert result["action"] == "create_new_contact"

    baseline = json.loads(isolated_exec_moves["baseline_path"].read_text())
    assert len(baseline) == 1
    assert baseline[0]["name"] == "Phil Crawford"
    assert baseline[0]["current_company"] == "Dairy Queen"

    assert server.get_executive_move_proposals(x_api_key=None)["proposals"] == []


def test_confirm_proposal_updates_existing_contact(isolated_exec_moves):
    isolated_exec_moves["baseline_path"].write_text(json.dumps([
        {"id": "phil-crawford", "name": "Phil Crawford", "signal_class": "LMI", "sources": [],
         "current_company": "Old Employer", "current_role": "Old Title", "email": None},
    ]), encoding="utf-8")
    emp = server.executive_move_promotion
    cid = _seed_pending_candidate(emp, matched_contact_id="phil-crawford")

    result = server.confirm_proposal(
        server.ConfirmProposalBody(kind="executive_move", id=cid, confirmed=True),
        x_api_key=None,
    )
    assert result["action"] == "update_existing_contact"
    assert result["contact_id"] == "phil-crawford"

    baseline = json.loads(isolated_exec_moves["baseline_path"].read_text())
    assert len(baseline) == 1
    assert baseline[0]["current_company"] == "Dairy Queen"


def test_confirm_proposal_with_explicit_contact_id_overrides_match(isolated_exec_moves):
    isolated_exec_moves["baseline_path"].write_text(json.dumps([
        {"id": "wrong-person", "name": "Wrong Person", "signal_class": "LMI", "sources": [], "email": None},
        {"id": "phil-crawford", "name": "Phil Crawford", "signal_class": "LMI", "sources": [], "email": None},
    ]), encoding="utf-8")
    emp = server.executive_move_promotion
    cid = _seed_pending_candidate(emp, matched_contact_id="wrong-person")

    result = server.confirm_proposal(
        server.ConfirmProposalBody(kind="executive_move", id=cid, confirmed=True, contact_id="phil-crawford"),
        x_api_key=None,
    )
    assert result["contact_id"] == "phil-crawford"


def test_confirm_proposal_without_name_blocked(isolated_exec_moves):
    emp = server.executive_move_promotion
    cid = _seed_pending_candidate(emp, proposed_name=None)

    with pytest.raises(HTTPException) as exc_info:
        server.confirm_proposal(
            server.ConfirmProposalBody(kind="executive_move", id=cid, confirmed=True),
            x_api_key=None,
        )
    assert exc_info.value.status_code == 404


def test_confirm_proposal_rejects_executive_move_kind(isolated_exec_moves):
    emp = server.executive_move_promotion
    cid = _seed_pending_candidate(emp)

    result = server.confirm_proposal(
        server.ConfirmProposalBody(kind="executive_move", id=cid, confirmed=False),
        x_api_key=None,
    )
    assert result["rejected"] is True
    assert server.get_executive_move_proposals(x_api_key=None)["proposals"] == []


def test_confirm_proposal_unknown_id_returns_404(isolated_exec_moves):
    with pytest.raises(HTTPException) as exc_info:
        server.confirm_proposal(
            server.ConfirmProposalBody(kind="executive_move", id="not-a-real-id", confirmed=True),
            x_api_key=None,
        )
    assert exc_info.value.status_code == 404
