"""
test_watchlist_promotion_api.py — RB-2026-09-05.

API-layer coverage for the watchlist auto-expansion endpoints:
  GET  /watchlist/promotion-candidates  (getWatchlistPromotionCandidates)
  POST /confirm kind="watchlist_promotion" (confirmProposal dispatch)

Same direct-function-call + monkeypatch isolation pattern as
test_tech_stack_relationship_api.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from system.api import server


@pytest.fixture()
def isolated_watchlist(tmp_path: Path, monkeypatch):
    registry_path = tmp_path / "watchlist_registry.json"
    registry_path.write_text(json.dumps({
        "schema_version": "1.0", "last_updated": "2026-01-01",
        "restaurant_brands": ["McDonald's"],
        "restaurant_tech": {"restaurant_tech_pos": ["Toast"]},
    }), encoding="utf-8")

    graph_path = tmp_path / "ecosystem_intelligence.json"
    graph_path.write_text(json.dumps({
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-05",
        "entities": [
            {"id": "vendor-sevenrooms", "name": "SevenRooms", "entity_type": "vendor", "aliases": [],
             "attributes": {"primary_category": "loyalty"}, "sources": [], "confidence": {},
             "domains": ["restaurants"]},
        ],
        "relationships": [
            {"from_entity_id": "brand-a", "to_entity_id": "vendor-sevenrooms", "status": "active"},
            {"from_entity_id": "brand-b", "to_entity_id": "vendor-sevenrooms", "status": "active"},
        ],
        "signals": [], "sources": [], "assessments": [], "user_relevance": [],
        "strategic_recommendations": [],
    }), encoding="utf-8")

    history_path = tmp_path / "technomic_watchlist_history.jsonl"
    store_path = tmp_path / "watchlist_promotion_candidates.json"

    # RB-SECURITY-2026-09-03 made server._auth() fail *closed* on an unset
    # API_KEY -- bypass _auth() itself, same pattern the tech-stack API
    # test uses.
    monkeypatch.setattr(server, "_auth", lambda *a, **k: None)
    monkeypatch.setattr(server.watchlist_promotion.wr, "REGISTRY_PATH", registry_path)
    monkeypatch.setattr(server.watchlist_promotion, "STORE_PATH", store_path)
    monkeypatch.setattr(server.watchlist_promotion.tws, "HISTORY_PATH", history_path)
    monkeypatch.setattr(server.watchlist_promotion.core, "ECOSYSTEM_INTELLIGENCE_PATH", graph_path)
    return {"registry_path": registry_path, "graph_path": graph_path, "history_path": history_path}


def test_scan_and_list_pending_vendor_candidate(isolated_watchlist):
    """Confidence-Based Auto-Recording Phase 5 (2026-09-25): confirming a
    watchlist candidate is purely additive (nothing to overwrite), so
    scan_vendor_candidates() now auto-applies it in the same call --
    nothing is left pending to list."""
    result = server.watchlist_promotion.scan_vendor_candidates()
    assert result["new_candidates"] == 1
    assert result["auto_applied"] == 1

    listing = server.get_watchlist_promotion_candidates(x_api_key=None)
    assert listing["candidates"] == []

    registry = json.loads(isolated_watchlist["registry_path"].read_text())
    assert "SevenRooms" in registry["restaurant_tech"]["restaurant_tech_other"]


def _seed_pending_vendor_candidate(isolated_watchlist):
    """Seeds a still-pending candidate directly, bypassing scan_vendor_
    candidates()'s own auto-apply, so confirmProposal's manual dispatch
    path can be tested against a real pending id."""
    wp = server.watchlist_promotion
    store = wp._load_store()
    wp._add_candidate(
        store, category="vendor", name="SevenRooms",
        reason="test", evidence={"active_relationship_count": 2},
    )
    wp._save_store(store)
    return wp._candidate_id("vendor", "SevenRooms")


def test_confirm_proposal_dispatches_watchlist_promotion_kind(isolated_watchlist):
    candidate_id = _seed_pending_vendor_candidate(isolated_watchlist)

    result = server.confirm_proposal(
        server.ConfirmProposalBody(kind="watchlist_promotion", id=candidate_id, confirmed=True),
        x_api_key=None,
    )
    assert result["confirmed"] is True

    registry = json.loads(isolated_watchlist["registry_path"].read_text())
    assert "SevenRooms" in registry["restaurant_tech"]["restaurant_tech_other"]
    assert server.get_watchlist_promotion_candidates(x_api_key=None)["candidates"] == []


def test_confirm_proposal_rejects_watchlist_promotion_kind(isolated_watchlist):
    candidate_id = _seed_pending_vendor_candidate(isolated_watchlist)

    result = server.confirm_proposal(
        server.ConfirmProposalBody(kind="watchlist_promotion", id=candidate_id, confirmed=False),
        x_api_key=None,
    )
    assert result["rejected"] is True
    registry = json.loads(isolated_watchlist["registry_path"].read_text())
    assert "SevenRooms" not in registry["restaurant_tech"].get("restaurant_tech_other", [])


def test_confirm_proposal_unknown_watchlist_id_returns_404(isolated_watchlist):
    with pytest.raises(HTTPException) as exc_info:
        server.confirm_proposal(
            server.ConfirmProposalBody(kind="watchlist_promotion", id="vendor::NoSuchThing", confirmed=True),
            x_api_key=None,
        )
    assert exc_info.value.status_code == 404
