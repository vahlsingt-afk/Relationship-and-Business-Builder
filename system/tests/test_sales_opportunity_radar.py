from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
import sales_opportunity_radar as radar


def test_exposure_graph_links_brand_to_incumbent_vendor():
    graph = {
        "entities": [
            {"id": "brand-a", "name": "Brand A", "entity_type": "brand"},
            {"id": "vendor-v", "name": "Vendor V", "entity_type": "vendor"},
        ],
        "relationships": [{
            "id": "rel-1", "from_entity_id": "brand-a", "to_entity_id": "vendor-v",
            "relationship_type": "uses_vendor_for_category", "category": "pos",
            "status": "active", "confidence": {"level": "high"},
        }],
    }
    result = radar.build_exposure_graph(graph)
    assert result["brand_count"] == 1
    assert result["relationship_count"] == 1
    assert result["brands"][0]["vendors"][0]["vendor_name"] == "Vendor V"


def test_hypothesis_keeps_disconfirming_questions_and_incumbent():
    brand = {"entity_id": "brand-a", "entity": "Brand A", "vulnerability_score": 55, "signals": []}
    row = radar._hypothesis_for(brand, {"brand-a": [{"vendor_name": "Vendor V", "status": "active"}]}, ["contract_timing"])
    assert row["posture"] == "research_first"
    assert "Vendor V" in row["hypothesis"]
    assert row["disconfirming_questions"]


def test_identity_alias_can_resolve_vulnerability_name():
    entities = [{"id": "brand-chipotle", "name": "Chipotle Mexican Grill", "aliases": ["Chipotle"]}]
    assert radar.entity_identity.find_entity("Chipotle", entities)["id"] == "brand-chipotle"


def test_punctuation_insensitive_resolution_is_unambiguous():
    entities = [{"id": "brand-yum", "name": "Yum! Brands", "aliases": []}]
    assert radar._resolve_entity("Yum Brands", entities)["id"] == "brand-yum"


def test_active_blue_sheet_alias_maps_to_graph_entity(tmp_path, monkeypatch):
    registry = tmp_path / "blue_sheets.json"
    registry.write_text('{"registry":[{"account_id":"acct-mcdonalds","aliases":["McDonalds"],"status":"active"}]}')
    monkeypatch.setattr(radar, "BLUE_SHEET_REGISTRY", registry)
    entities = [{"id": "brand-mcdonald-s", "name": "McDonald's", "aliases": ["McDonalds"]}]
    assert radar._active_sales_ids(entities) == {"brand-mcdonald-s"}
