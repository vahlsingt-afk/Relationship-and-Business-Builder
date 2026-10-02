from datetime import date
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
import verify_public_intelligence_collection as verifier


def _paths(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier, "ASSESSMENT_PATH", tmp_path / "assessment.json")
    monkeypatch.setattr(verifier, "DISCOVERY_PATH", tmp_path / "discovery.json")
    monkeypatch.setattr(verifier, "RECEIPT_PATH", tmp_path / "receipt.json")


def test_collection_passes_with_scan_and_entity_discovery(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    verifier.ASSESSMENT_PATH.write_text(json.dumps({
        "assessment_date": "2026-09-14", "phase_1_web": {"status": "ok"},
        "trust_stats": {"sources_assessed": 15, "items_fetched": 230}}))
    verifier.DISCOVERY_PATH.write_text(json.dumps({
        "date": "2026-09-14", "entities_checked": ["Darden"], "candidates": [{"domain": "x.com"}]}))
    result = verifier.verify(date(2026, 9, 14))
    assert result["status"] == "pass"
    assert result["sources_scanned"] == 15


def test_collection_fails_when_execution_is_missing_not_when_news_is_zero(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    verifier.ASSESSMENT_PATH.write_text(json.dumps({
        "assessment_date": "2026-09-14", "phase_1_web": {"status": "ok"},
        "trust_stats": {"sources_assessed": 0, "items_fetched": 0}}))
    verifier.DISCOVERY_PATH.write_text(json.dumps({
        "date": "2026-09-14", "entities_checked": [], "candidates": []}))
    result = verifier.verify(date(2026, 9, 14))
    assert result["status"] == "fail"
    assert "zero_public_sources_scanned" in result["failures"]
    assert "zero_watchlist_entities_checked" in result["failures"]
