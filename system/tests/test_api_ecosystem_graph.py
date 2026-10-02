#!/usr/bin/env python3
# NOTE: This test requires a live FastAPI + server environment.
# Skipped automatically when fastapi is not installed (CI / minimal sandbox).
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    raise unittest.SkipTest("fastapi not installed — skipping live API tests")

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "system" / "api" / "server.py"

spec = importlib.util.spec_from_file_location("rb_api_server_for_ecosystem_tests", SERVER)
server = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(server)


def _write_graph(path: Path) -> None:
    graph = {
        "contract": "rb_ecosystem_intelligence_v1",
        "entities": [
            {
                "id": "brand-blaze-pizza",
                "name": "Blaze Pizza",
                "entity_type": "brand",
                "subtype": "restaurant_brand",
                "status": "active",
                "domains": ["restaurants"],
                "attributes": {
                    "rank": 128,
                    "segment": "LSR",
                    "subsegment": "FC",
                    "menu_type": "Pizza",
                    "technomic_latest_year": 2024,
                    "system_sales": 387400000,
                    "unit_count": 294,
                    "auv": 1315000,
                },
                "sources": ["src-technomic", "src-linkedin-qu"],
                "confidence": {"level": "medium"},
            },
            {
                "id": "vendor-qu",
                "name": "Qu",
                "entity_type": "vendor",
                "subtype": "restaurant_technology_vendor",
                "status": "active",
                "domains": ["restaurants"],
                "attributes": {},
                "sources": ["src-linkedin-qu"],
                "confidence": {"level": "medium"},
            },
        ],
        "relationships": [
            {
                "id": "rel-brand-blaze-pizza-pos-system-of-record-pos-logo-or-customer-page-vendor-qu",
                "relationship_type": "uses_vendor_for_category",
                "from_entity_id": "brand-blaze-pizza",
                "to_entity_id": "vendor-qu",
                "category": "pos",
                "vendor_role": "system_of_record_pos",
                "product": "Qu POS",
                "status": "active",
                "deployment": {
                    "stage": "unknown",
                    "scope": "system_of_record_pos",
                    "deployment_claim_type": "logo_or_customer_page",
                },
                "risk": "unknown",
                "evidence_posture": "provisional",
                "interpretation_scope": "Vendor-claimed relationship; deployment depth requires verification.",
                "confidence": {"level": "medium"},
                "sources": ["src-linkedin-qu"],
                "strategic_note": "Qu publicly named Blaze Pizza as a customer.",
            }
        ],
        "signals": [
            {
                "id": "sig-qu-blaze",
                "signal_type": "vendor_claimed_customer_relationship",
                "entities": ["vendor-qu", "brand-blaze-pizza"],
                "summary": "Qu named Blaze Pizza as a customer.",
                "sources": ["src-linkedin-qu"],
                "confidence": {"level": "medium"},
            }
        ],
        "assessments": [],
        "sources": [{"id": "src-technomic"}, {"id": "src-linkedin-qu"}],
        "user_relevance": [],
        "strategic_recommendations": [],
    }
    path.write_text(json.dumps(graph), encoding="utf-8")


def _client(monkeypatch, tmp_path):
    graph_path = tmp_path / "ecosystem_intelligence.json"
    _write_graph(graph_path)
    monkeypatch.setattr(server.core, "ECOSYSTEM_INTELLIGENCE_PATH", graph_path)
    return TestClient(server.app, headers={"x-api-key": "test-key"})


def test_ecosystem_graph_summary(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.get("/graphs/ecosystem", params={"query_type": "summary"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "found"
    assert payload["summary"]["entity_types"]["brand"] == 1
    assert payload["summary"]["relationship_categories"]["pos"] == 1


def test_ecosystem_graph_brand_profile(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.get("/graphs/ecosystem", params={"query_type": "brand", "query": "Blaze Pizza"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "found"
    assert payload["entity"]["name"] == "Blaze Pizza"
    assert payload["entity"]["metrics"]["unit_count"] == 294
    assert payload["vendor_relationship_count"] == 1
    assert payload["vendor_relationships"][0]["vendor"] == "Qu"
    assert payload["vendor_relationships"][0]["evidence_posture"] == "provisional"


def test_ecosystem_graph_vendor_query(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.get(
        "/graphs/ecosystem",
        params={"query_type": "vendor", "query": "Qu", "category": "pos"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "found"
    assert payload["count"] == 1
    assert payload["items"][0]["brand"] == "Blaze Pizza"
    assert payload["items"][0]["product"] == "Qu POS"
