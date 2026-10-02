"""
test_value_wedge_api.py — RB-2026-09-25.

API-layer coverage for the Value Wedge and Genius Capability Library
endpoints:
  POST /value-wedges/{competitor_slug}      (createValueWedge)
  GET  /value-wedges/{competitor_slug}      (getValueWedge)
  POST /genius-capabilities/{category}      (addGeniusCapability)
  GET  /genius-capabilities/{category}      (getGeniusCapabilities)
  GET  /genius-capabilities                 (listAllGeniusCapabilities)

Same direct-function-call + monkeypatch isolation pattern as
test_watchlist_promotion_api.py / test_priority_account_publisher_api.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from system.api import server


@pytest.fixture()
def isolated_value_wedge(tmp_path: Path, monkeypatch):
    graph_path = tmp_path / "ecosystem_intelligence.json"
    graph_path.write_text(json.dumps({
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-25",
        "entities": [], "relationships": [], "signals": [], "sources": [],
        "assessments": [], "user_relevance": [], "strategic_recommendations": [],
    }), encoding="utf-8")

    cic_root = tmp_path / "competitor_intelligence"
    (cic_root / "_portfolio").mkdir(parents=True)
    (cic_root / "_portfolio" / "competitor_registry.json").write_text(
        json.dumps({"registry": [{"competitor_slug": "test-fixture-competitor", "display_name": "Test Fixture Competitor"}]}),
        encoding="utf-8",
    )
    comp_dir = cic_root / "competitors" / "test-fixture-competitor"
    comp_dir.mkdir(parents=True)
    (comp_dir / "competitor.json").write_text(json.dumps({
        "competitor_id": "comp-test-fixture-competitor", "competitor_slug": "test-fixture-competitor",
        "display_name": "Test Fixture Competitor", "vendor_entity_id": None,
        "competes_on": ["pos"], "positioning_summary": "", "todds_pov": "",
        "vs_genius": {"genius_advantages": [], "competitor_advantages": []},
        "category_battle_cards": {}, "last_evidence_date": None,
    }), encoding="utf-8")
    (comp_dir / "evidence.jsonl").write_text("", encoding="utf-8")

    monkeypatch.setattr(server, "_auth", lambda *a, **k: None)
    monkeypatch.setattr(server.value_wedge.compintel.ei.core, "ECOSYSTEM_INTELLIGENCE_PATH", graph_path)
    monkeypatch.setattr(server.value_wedge.cic, "ROOT", cic_root)
    monkeypatch.setattr(server.value_wedge.avc, "VAULT_ROOT", tmp_path / "artifact_vault")
    monkeypatch.setattr(server.value_wedge.intelligence_index, "INDEX_PATH", tmp_path / "intelligence_index.json")
    monkeypatch.setattr(server.value_wedge.intelligence_index, "UPDATE_LOG_PATH", tmp_path / "intelligence_index_updates.jsonl")
    monkeypatch.setattr(server.genius_capabilities, "GENIUS_CAPABILITIES_PATH", tmp_path / "genius_capabilities.json")
    return {"comp_dir": comp_dir}


def test_add_and_get_genius_capability(isolated_value_wedge):
    result = server.post_add_genius_capability(
        "pos", server.AddGeniusCapabilityBody(point="Real-time inventory sync", why_it_matters="eliminates manual reconciliation"),
        x_api_key=None,
    )
    assert result["ok"] is True
    listed = server.get_genius_capabilities("pos", x_api_key=None)
    assert len(listed["capabilities"]) == 1
    assert listed["capabilities"][0]["point"] == "Real-time inventory sync"


def test_add_genius_capability_rejects_unknown_category(isolated_value_wedge):
    with pytest.raises(HTTPException) as exc_info:
        server.post_add_genius_capability(
            "not_a_real_category", server.AddGeniusCapabilityBody(point="x"), x_api_key=None,
        )
    assert exc_info.value.status_code == 400


def test_list_all_genius_capabilities_returns_all_categories(isolated_value_wedge):
    result = server.get_all_genius_capabilities(x_api_key=None)
    assert len(result["capabilities"]) == 7
    assert result["capabilities"]["pos"] == []


def test_create_and_get_value_wedge(isolated_value_wedge):
    server.post_add_genius_capability(
        "pos", server.AddGeniusCapabilityBody(point="Real-time inventory sync"), x_api_key=None,
    )
    created = server.post_create_value_wedge(
        "test-fixture-competitor", server.CreateValueWedgeBody(generated_for=""), x_api_key=None,
    )
    assert created["competitor_slug"] == "test-fixture-competitor"
    assert "Real-time inventory sync" in created["markdown"]

    fetched = server.get_value_wedge_detail("test-fixture-competitor", x_api_key=None)
    assert fetched["markdown"] == created["markdown"]


def test_create_value_wedge_auto_creates_unknown_slug(isolated_value_wedge):
    result = server.post_create_value_wedge(
        "does-not-exist-xyz", server.CreateValueWedgeBody(generated_for=""), x_api_key=None,
    )
    assert result["competitor_slug"] == "does-not-exist-xyz"


def test_get_value_wedge_before_generation_returns_404(isolated_value_wedge):
    with pytest.raises(HTTPException) as exc_info:
        server.get_value_wedge_detail("test-fixture-competitor", x_api_key=None)
    assert exc_info.value.status_code == 404
