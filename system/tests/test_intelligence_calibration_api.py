"""
test_intelligence_calibration_api.py — RB-2026-09-15 (Codex handoff item #5,
outcome-correlation half).

API-layer coverage for GET /intelligence-calibration (getIntelligenceCalibration).
Same direct-function-call + monkeypatch isolation pattern as
test_intelligence_action_queue_api.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from system.api import server


@pytest.fixture()
def isolated_calibration(tmp_path: Path, monkeypatch):
    cache_dir = tmp_path / ".cache"
    cache_dir.mkdir()
    monkeypatch.setattr(server, "_auth", lambda *a, **k: None)
    monkeypatch.setattr(server.core, "CACHE_DIR", cache_dir)
    return cache_dir


def test_missing_cache_returns_404(isolated_calibration):
    with pytest.raises(HTTPException) as exc_info:
        server.get_intelligence_calibration(x_api_key=None)
    assert exc_info.value.status_code == 404


def test_returns_real_cached_report(isolated_calibration):
    payload = {
        "contract": "rb_intelligence_calibration_v1", "date": "2026-09-15",
        "total_resolutions": 2,
        "disposition_counts_by_item_type": {
            "buying_window_hypothesis": {"accepted": 1, "rejected": 1, "deferred": 0},
        },
        "outcome_correlation": {
            "scope": ["buying_window_hypothesis"],
            "accepted_with_known_outcome": 1,
            "accepted_confirmed_active_pursuit": 1,
            "rows": [],
        },
    }
    (isolated_calibration / "intelligence_calibration.json").write_text(
        json.dumps(payload), encoding="utf-8")

    result = server.get_intelligence_calibration(x_api_key=None)
    assert result["total_resolutions"] == 2
    assert result["outcome_correlation"]["accepted_confirmed_active_pursuit"] == 1


def test_unreadable_cache_returns_500(isolated_calibration):
    (isolated_calibration / "intelligence_calibration.json").write_text(
        "{not valid json", encoding="utf-8")
    with pytest.raises(HTTPException) as exc_info:
        server.get_intelligence_calibration(x_api_key=None)
    assert exc_info.value.status_code == 500
