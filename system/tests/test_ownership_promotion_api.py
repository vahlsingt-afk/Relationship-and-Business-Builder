"""
test_ownership_promotion_api.py — RB-2026-09-11.

API-layer coverage for the M&A/ownership-change capture endpoints:
  GET  /ownership/proposals   (getOwnershipChangeProposals)
  POST /ownership/research    (reportOwnershipFinding)
  POST /confirm kind="ownership_change" (confirmProposal dispatch)

Same direct-function-call + monkeypatch isolation pattern as
test_priority_account_publisher_api.py/test_tech_stack_relationship_api.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from system.api import server


@pytest.fixture()
def isolated_ownership(tmp_path: Path, monkeypatch):
    graph_path = tmp_path / "ecosystem_intelligence.json"
    store_path = tmp_path / "ownership_promotion_candidates.json"

    graph_path.write_text(json.dumps({
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-11",
        "domain_packs": ["restaurants"],
        "entities": [
            {"id": "brand-del-taco", "name": "Del Taco", "entity_type": "brand", "status": "active", "aliases": [],
             "attributes": {}, "sources": [], "confidence": {"level": "high"}, "domains": ["restaurants"]},
        ],
        "relationships": [], "signals": [], "sources": [],
        "assessments": [], "user_relevance": [], "strategic_recommendations": [],
    }), encoding="utf-8")

    monkeypatch.setattr(server, "_auth", lambda *a, **k: None)
    op = server.ownership_promotion
    monkeypatch.setattr(op.ei.core, "ECOSYSTEM_INTELLIGENCE_PATH", graph_path)
    monkeypatch.setattr(op, "STORE_PATH", store_path)
    return {"graph_path": graph_path, "store_path": store_path}


def _seed_pending_candidate(op, cid="brand-del-taco::sig-test", proposed_owner_name="Yadav Enterprises"):
    store = {"candidates": {cid: {
        "candidate_id": cid, "status": "proposed_pending_confirmation",
        "entity_id": "brand-del-taco", "entity_name": "Del Taco",
        "proposed_owner_name": proposed_owner_name,
        "proposed_owner_confidence": "extracted_from_text" if proposed_owner_name else "unknown",
        "source_type": "credible_reporting", "source_title": "test", "source_url": None,
        "evidence_excerpt": "Del Taco Begins New Growth Era Following Acquisition by Yadav Enterprises.",
        "origin": "signal_scan", "origin_ref": "sig-test", "evidence_date": "2025-12-22",
        "detected_at": "2026-09-11T00:00:00Z", "resolved_at": None,
    }}}
    op._save_store(store)
    return cid


def test_get_endpoint_lists_pending_candidate(isolated_ownership):
    op = server.ownership_promotion
    _seed_pending_candidate(op)
    result = server.get_ownership_change_proposals(x_api_key=None)
    assert len(result["proposals"]) == 1
    assert result["proposals"][0]["entity_name"] == "Del Taco"


def test_research_endpoint_proposes_and_lists_pending(isolated_ownership):
    result = server.post_report_ownership_finding(
        server.OwnershipResearchBody(
            entity_id="brand-del-taco",
            evidence_text="Confirmed via SEC filing: Yadav Enterprises closed the Del Taco acquisition.",
            owner_name="Yadav Enterprises",
        ),
        x_api_key=None,
    )
    assert result["proposed"] is True
    listing = server.get_ownership_change_proposals(x_api_key=None)
    assert len(listing["proposals"]) == 1
    assert listing["proposals"][0]["proposed_owner_name"] == "Yadav Enterprises"
    assert listing["proposals"][0]["proposed_owner_confidence"] == "stated_by_caller"


def test_research_endpoint_rejects_unknown_entity(isolated_ownership):
    with pytest.raises(HTTPException) as exc_info:
        server.post_report_ownership_finding(
            server.OwnershipResearchBody(entity_id="brand-does-not-exist", evidence_text="some evidence"),
            x_api_key=None,
        )
    assert exc_info.value.status_code == 400


def test_confirm_proposal_dispatches_ownership_change_kind(isolated_ownership):
    op = server.ownership_promotion
    cid = _seed_pending_candidate(op)

    result = server.confirm_proposal(
        server.ConfirmProposalBody(kind="ownership_change", id=cid, confirmed=True),
        x_api_key=None,
    )
    assert result["confirmed"] is True
    assert result["owner_name"] == "Yadav Enterprises"

    graph = json.loads(isolated_ownership["graph_path"].read_text())
    entity = next(e for e in graph["entities"] if e["id"] == "brand-del-taco")
    assert entity["owner_name"] == "Yadav Enterprises"

    assert server.get_ownership_change_proposals(x_api_key=None)["proposals"] == []


def test_confirm_proposal_with_explicit_owner_name_overrides_prefilled(isolated_ownership):
    op = server.ownership_promotion
    cid = _seed_pending_candidate(op, proposed_owner_name="Wrong Company")

    result = server.confirm_proposal(
        server.ConfirmProposalBody(kind="ownership_change", id=cid, confirmed=True, owner_name="Yadav Enterprises"),
        x_api_key=None,
    )
    assert result["owner_name"] == "Yadav Enterprises"


def test_confirm_proposal_without_owner_name_blocked(isolated_ownership):
    op = server.ownership_promotion
    cid = _seed_pending_candidate(op, proposed_owner_name=None)

    with pytest.raises(HTTPException) as exc_info:
        server.confirm_proposal(
            server.ConfirmProposalBody(kind="ownership_change", id=cid, confirmed=True),
            x_api_key=None,
        )
    assert exc_info.value.status_code == 404


def test_confirm_proposal_rejects_ownership_change_kind(isolated_ownership):
    op = server.ownership_promotion
    cid = _seed_pending_candidate(op)

    result = server.confirm_proposal(
        server.ConfirmProposalBody(kind="ownership_change", id=cid, confirmed=False),
        x_api_key=None,
    )
    assert result["rejected"] is True
    assert server.get_ownership_change_proposals(x_api_key=None)["proposals"] == []


def test_confirm_proposal_unknown_id_returns_404(isolated_ownership):
    with pytest.raises(HTTPException) as exc_info:
        server.confirm_proposal(
            server.ConfirmProposalBody(kind="ownership_change", id="not-a-real-id", confirmed=True),
            x_api_key=None,
        )
    assert exc_info.value.status_code == 404
