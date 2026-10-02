from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
import baseline_research_gate as gate


def test_empty_account_shell_is_not_an_established_baseline(monkeypatch):
    monkeypatch.setattr(gate.abb, "resolve_account", lambda name: ("newco", True))
    monkeypatch.setattr(gate.abb, "retrieve_existing_intelligence", lambda slug: {
        "dossier": {"account": {"executive_summary": "", "leadership": {"confirmed": []},
                                  "technology_stack": [], "strategic_position": {}}},
        "ecosystem_relationships": [],
    })
    result = gate._account_coverage("NewCo")
    assert result["established"] is False
    assert result["score"] == 0


def test_gate_uses_dynamic_budget_and_dedupes(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "REQUESTS_PATH", tmp_path / "requests.jsonl")
    monkeypatch.setattr(gate, "RECEIPTS_PATH", tmp_path / "receipts.jsonl")
    monkeypatch.setattr(gate, "CACHE_PATH", tmp_path / "gate.json")
    graph = {"entities": [
        {"id": "brand-a", "name": "A", "entity_type": "brand", "rank": 20},
        {"id": "brand-b", "name": "B", "entity_type": "brand", "rank": 5},
    ]}
    monkeypatch.setattr(gate.ei, "_read_graph", lambda: graph)
    monkeypatch.setattr(gate.cascade, "_entities_with_new_intelligence_today", lambda d, g: {
        "A": {"material": True, "entity_id": "brand-a", "sources": [{"type": "entity_alert"}]},
        "B": {"material": True, "entity_id": "brand-b", "sources": [
            {"type": "ecosystem_signal", "id": "sig-2026-09-14-brand-b-leadership-change"}]},
        "C": {"material": False, "entity_id": None, "sources": []},
    })
    monkeypatch.setattr(gate, "_baseline_coverage", lambda name, entity: {
        "established": False, "score": 0, "dimensions": [], "slug": None})

    first = gate.run(date(2026, 9, 14))
    second = gate.run(date(2026, 9, 14))
    assert first["urgent_requests_queued"] == 2
    assert {row["entity_name"] for row in first["queued"]} == {"A", "B"}
    assert all("opportunity_score" in row for row in first["queued"])
    assert second["urgent_requests_queued"] == 0
    rows = [json.loads(line) for line in gate.REQUESTS_PATH.read_text().splitlines()]
    assert len(rows) == 2


def test_recent_90_day_receipt_satisfies_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "RECEIPTS_PATH", tmp_path / "receipts.jsonl")
    gate.RECEIPTS_PATH.write_text(json.dumps({"entity_name": "NewCo", "completed_at": "2026-08-01"}) + "\n")
    assert gate._recent_research("NewCo", date(2026, 9, 14)) is True


def test_gate_resolves_name_only_feeder_to_exact_graph_entity(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "REQUESTS_PATH", tmp_path / "requests.jsonl")
    monkeypatch.setattr(gate, "RECEIPTS_PATH", tmp_path / "receipts.jsonl")
    monkeypatch.setattr(gate, "CACHE_PATH", tmp_path / "gate.json")
    monkeypatch.setattr(gate.ei, "_read_graph", lambda: {"entities": [
        {"id": "brand-auntie-annes", "name": "Auntie Anne's", "entity_type": "brand", "rank": 40}]})
    monkeypatch.setattr(gate.cascade, "_entities_with_new_intelligence_today", lambda d, g: {
        "Auntie Anne's": {"material": True, "entity_id": None, "sources": [{"type": "entity_alert"}]}})
    monkeypatch.setattr(gate, "_baseline_coverage", lambda name, entity: {
        "established": False, "score": 0, "dimensions": [], "slug": None})
    result = gate.run(date(2026, 9, 14))
    assert result["queued"][0]["entity_id"] == "brand-auntie-annes"
    assert result["queued"][0]["entity_type"] == "brand"


def test_scan_roster_alone_is_not_newsworthy(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "REQUESTS_PATH", tmp_path / "requests.jsonl")
    monkeypatch.setattr(gate, "RECEIPTS_PATH", tmp_path / "receipts.jsonl")
    monkeypatch.setattr(gate, "CACHE_PATH", tmp_path / "gate.json")
    monkeypatch.setattr(gate.ei, "_read_graph", lambda: {"entities": []})
    monkeypatch.setattr(gate.cascade, "_entities_with_new_intelligence_today", lambda d, g: {
        "RosterCo": {"material": True, "entity_id": None,
                     "sources": [{"type": "technomic_promoted", "tier": "tier1"}]}})
    result = gate.run(date(2026, 9, 14))
    assert result["baseline_gaps_found"] == 0
    assert result["urgent_requests_queued"] == 0


def test_old_ecosystem_history_is_not_treated_as_new_intelligence(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "REQUESTS_PATH", tmp_path / "requests.jsonl")
    monkeypatch.setattr(gate, "RECEIPTS_PATH", tmp_path / "receipts.jsonl")
    monkeypatch.setattr(gate, "CACHE_PATH", tmp_path / "gate.json")
    monkeypatch.setattr(gate.ei, "_read_graph", lambda: {"entities": [
        {"id": "brand-old", "name": "OldCo", "entity_type": "brand", "rank": 10}]})
    monkeypatch.setattr(gate.cascade, "_entities_with_new_intelligence_today", lambda d, g: {
        "OldCo": {"material": True, "entity_id": "brand-old", "sources": [
            {"type": "ecosystem_signal", "id": "sig-2019-01-02-brand-old-leadership-change"}]}})
    result = gate.run(date(2026, 9, 14))
    assert result["baseline_gaps_found"] == 0
    assert result["urgent_requests_queued"] == 0
