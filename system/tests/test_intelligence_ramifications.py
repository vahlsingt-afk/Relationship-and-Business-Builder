from __future__ import annotations
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
import intelligence_ramifications as ir


def test_material_signal_creates_evidence_linked_ramification(tmp_path, monkeypatch):
    graph = {"entities": [{"id": "brand-a", "name": "Brand A", "entity_type": "brand"}], "signals": [{
        "id": "sig-1", "captured_at": "2026-09-14T10:00:00Z", "event_at": "2026-09-14",
        "signal_type": "exec-change", "summary": "Brand A named Jane Doe CEO.",
        "entities": ["brand-a"], "confidence": {"level": "high"}, "source_ids": ["src-1"],
    }]}
    monkeypatch.setattr(ir.ei, "_read_graph", lambda: graph)
    monkeypatch.setattr(ir.eb, "_is_material_signal", lambda confidence, signal_type: True)
    monkeypatch.setattr(ir.brg, "_baseline_coverage", lambda name, entity: {
        "established": False, "dimensions": ["company_profile"]})
    monkeypatch.setattr(ir, "_artifact_impacts", lambda *args: [{"artifact": "daily_intelligence_report", "action": "report"}])
    monkeypatch.setattr(ir, "CACHE_PATH", tmp_path / "ramifications.json")
    result = ir.build(date(2026, 9, 14))
    row = result["ramifications"][0]
    assert row["baseline_status"] == "insufficient"
    assert row["review_lens"] == "leadership_and_decision_network"
    assert row["source_refs"] == ["src-1"]
    assert result["ramifications_generated"] == 1


def test_nonmaterial_signal_is_excluded(tmp_path, monkeypatch):
    monkeypatch.setattr(ir.ei, "_read_graph", lambda: {"entities": [], "signals": [{
        "id": "sig-1", "captured_at": "2026-09-14", "signal_type": "general",
        "entities": ["brand-a"], "confidence": {"level": "low"}}]})
    monkeypatch.setattr(ir.eb, "_is_material_signal", lambda confidence, signal_type: False)
    monkeypatch.setattr(ir, "CACHE_PATH", tmp_path / "ramifications.json")
    assert ir.build(date(2026, 9, 14))["ramifications"] == []


def test_historical_item_captured_today_is_not_current_ramification(tmp_path, monkeypatch):
    monkeypatch.setattr(ir.ei, "_read_graph", lambda: {"entities": [], "signals": [{
        "id": "sig-old", "captured_at": "2026-09-14", "event_at": "2019-01-01",
        "signal_type": "acquisition", "entities": ["brand-a"],
        "confidence": {"level": "high"}}]})
    monkeypatch.setattr(ir.eb, "_is_material_signal", lambda confidence, signal_type: True)
    monkeypatch.setattr(ir, "CACHE_PATH", tmp_path / "ramifications.json")
    result = ir.build(date(2026, 9, 14))
    assert result["ramifications"] == []
    assert result["historical_events_excluded"] == 1
