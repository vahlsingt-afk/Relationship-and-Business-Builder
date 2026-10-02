"""
test_tech_stack_relationship_api.py — RB-2026-08-31.

API-layer coverage for the 3 new tech-stack coverage-expansion endpoints:
  GET  /tech-stack/proposals    (getTechStackRelationshipProposals)
  POST /tech-stack/research     (researchBrandTechStack)
  POST /confirm kind="tech_stack_relationship" (confirmProposal dispatch)

Same direct-function-call + monkeypatch isolation pattern as
test_uploaded_document_retrieval.py earlier this session -- calls the real
FastAPI route functions, isolated from real production data.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from system.api import server


@pytest.fixture()
def isolated_tech_stack(tmp_path: Path, monkeypatch):
    graph_path = tmp_path / "ecosystem_intelligence.json"
    snap_dir = tmp_path / "_snapshots"
    store_path = tmp_path / "tech_stack_relationship_proposals.json"

    graph_path.write_text(json.dumps({
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-08-31",
        "entities": [
            {"id": "brand-blaze-pizza", "name": "Blaze Pizza", "entity_type": "brand", "aliases": [],
             "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]},
            {"id": "vendor-oracle", "name": "Oracle", "entity_type": "vendor", "aliases": [],
             "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]},
        ],
        "relationships": [], "signals": [], "sources": [],
        "assessments": [], "user_relevance": [], "strategic_recommendations": [],
    }), encoding="utf-8")

    # RB-SECURITY-2026-09-03 made server._auth() fail *closed* on an unset
    # API_KEY -- bypass _auth() itself rather than setting API_KEY=None,
    # same pattern test_vendor_list.py uses for rbb_chat._auth_flexible.
    monkeypatch.setattr(server, "_auth", lambda *a, **k: None)
    monkeypatch.setattr(server.tech_stack_relationship_promotion.ei.core, "ECOSYSTEM_INTELLIGENCE_PATH", graph_path)
    monkeypatch.setattr(server.tech_stack_relationship_promotion.ei.core, "SNAPSHOTS_DIR", snap_dir)
    monkeypatch.setattr(server.tech_stack_relationship_promotion, "STORE_PATH", store_path)
    monkeypatch.setattr(server.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    return graph_path


def test_research_endpoint_proposes_and_lists_pending(isolated_tech_stack):
    result = server.post_research_brand_tech_stack(
        server.TechStackResearchBody(
            brand_id="brand-blaze-pizza", vendor_name="Oracle",
            evidence_text="Blaze Pizza press release: adopts Oracle for point of sale.",
            source_url="https://example.com/x",
        ),
        x_api_key=None,
    )
    assert result["proposed"] is True

    listing = server.get_tech_stack_relationship_proposals(x_api_key=None)
    assert len(listing["proposals"]) == 1
    assert listing["proposals"][0]["vendor_name"] == "Oracle"
    assert listing["proposals"][0]["category"] == "pos"


def test_research_endpoint_threads_evidence_date_to_candidate(isolated_tech_stack):
    """RB-2026-09-08: evidence_date must survive the real API layer (not
    just the underlying function) all the way onto the pending candidate."""
    result = server.post_research_brand_tech_stack(
        server.TechStackResearchBody(
            brand_id="brand-blaze-pizza", vendor_name="Oracle",
            evidence_text="Blaze Pizza press release: adopts Oracle for point of sale.",
            evidence_date="2026-04-02",
        ),
        x_api_key=None,
    )
    assert result["proposed"] is True
    listing = server.get_tech_stack_relationship_proposals(x_api_key=None)
    assert listing["proposals"][0]["evidence_date"] == "2026-04-02"


def test_research_endpoint_rejects_unknown_brand(isolated_tech_stack):
    with pytest.raises(HTTPException) as exc_info:
        server.post_research_brand_tech_stack(
            server.TechStackResearchBody(
                brand_id="brand-does-not-exist", vendor_name="Oracle", evidence_text="some evidence",
            ),
            x_api_key=None,
        )
    assert exc_info.value.status_code == 400


def test_confirm_proposal_dispatches_tech_stack_relationship_kind(isolated_tech_stack):
    server.post_research_brand_tech_stack(
        server.TechStackResearchBody(
            brand_id="brand-blaze-pizza", vendor_name="Oracle",
            evidence_text="Blaze Pizza uses Oracle for point of sale.",
        ),
        x_api_key=None,
    )
    candidate_id = server.get_tech_stack_relationship_proposals(x_api_key=None)["proposals"][0]["candidate_id"]

    result = server.confirm_proposal(
        server.ConfirmProposalBody(kind="tech_stack_relationship", id=candidate_id, confirmed=True),
        x_api_key=None,
    )
    assert result["confirmed"] is True

    graph = json.loads(isolated_tech_stack.read_text())
    rels = [r for r in graph["relationships"] if r["relationship_type"] == "uses_vendor_for_category"]
    assert len(rels) == 1
    assert rels[0]["from_entity_id"] == "brand-blaze-pizza"

    # No longer pending after confirm.
    assert server.get_tech_stack_relationship_proposals(x_api_key=None)["proposals"] == []


def test_confirm_proposal_rejects_tech_stack_relationship_kind(isolated_tech_stack):
    server.post_research_brand_tech_stack(
        server.TechStackResearchBody(
            brand_id="brand-blaze-pizza", vendor_name="Oracle",
            evidence_text="Blaze Pizza uses Oracle for point of sale.",
        ),
        x_api_key=None,
    )
    candidate_id = server.get_tech_stack_relationship_proposals(x_api_key=None)["proposals"][0]["candidate_id"]

    result = server.confirm_proposal(
        server.ConfirmProposalBody(kind="tech_stack_relationship", id=candidate_id, confirmed=False),
        x_api_key=None,
    )
    assert result["rejected"] is True
    assert server.get_tech_stack_relationship_proposals(x_api_key=None)["proposals"] == []


def test_confirm_proposal_unknown_id_returns_404(isolated_tech_stack):
    with pytest.raises(HTTPException) as exc_info:
        server.confirm_proposal(
            server.ConfirmProposalBody(kind="tech_stack_relationship", id="not-a-real-id", confirmed=True),
            x_api_key=None,
        )
    assert exc_info.value.status_code == 404
