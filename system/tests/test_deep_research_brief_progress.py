from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
import render_intelligence_brief as renderer


def test_deep_research_progress_reports_coverage_prior_day_and_failures(tmp_path, monkeypatch):
    coverage = tmp_path / "coverage.json"
    receipts = tmp_path / "receipts.jsonl"
    coverage.write_text(json.dumps({"targets": {
        "competitor:toast": {
            "kind": "competitor", "name": "Toast", "active": True,
            "coverage_count": 1, "last_covered_at": "2026-09-30T18:00:00+00:00",
        },
        "competitor:ncr": {
            "kind": "competitor", "name": "NCR Voyix", "active": True,
            "coverage_count": 0, "last_covered_at": None,
        },
        "company:mcd": {
            "kind": "company", "name": "McDonald's", "active": True,
            "coverage_count": 1, "last_covered_at": "2026-09-30T20:00:00+00:00",
        },
        "company:subway": {
            "kind": "company", "name": "Subway", "active": True,
            "coverage_count": 0, "last_covered_at": None,
        },
    }}), encoding="utf-8")
    receipts.write_text(json.dumps({
        "batch_id": "dr-failed", "ended_at": "2026-09-30T23:00:00+00:00",
        "status": "blocked", "blocker": "ChatGPT export failed validation",
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(renderer, "DEEP_RESEARCH_COVERAGE_PATH", coverage)
    monkeypatch.setattr(renderer, "ADAPTIVE_RESEARCH_RECEIPTS_PATH", receipts)

    text = renderer._render_deep_research_baseline_progress(date(2026, 10, 1))

    assert "2 of 4 targets (50.0%)" in text
    assert "competitors 1/2 (50.0%)" in text
    assert "restaurant brands 1/2 (50.0%)" in text
    assert "McDonald's, Toast" in text
    assert "dr-failed: ChatGPT export failed validation" in text
    assert "initial coverage" in text


def test_deep_research_progress_reports_no_prior_day_activity(tmp_path, monkeypatch):
    coverage = tmp_path / "coverage.json"
    coverage.write_text(json.dumps({"targets": {}}), encoding="utf-8")
    monkeypatch.setattr(renderer, "DEEP_RESEARCH_COVERAGE_PATH", coverage)
    monkeypatch.setattr(renderer, "ADAPTIVE_RESEARCH_RECEIPTS_PATH", tmp_path / "missing.jsonl")

    text = renderer._render_deep_research_baseline_progress(date(2026, 10, 1))

    assert "Researched September 30:** None recorded." in text
    assert "Research failures:** None recorded." in text
