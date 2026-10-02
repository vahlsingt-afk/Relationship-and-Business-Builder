"""
test_intelligence_action_queue_api.py — RB-2026-09-15 (Codex handoff item #3).

API-layer coverage for GET /intelligence-action-queue (getIntelligenceActionQueue):
the Daily Brief's Part 2 only ever surfaced the top 8 ranked items, with no
read operation to see the rest of the queue or filter it by action_class.
Same direct-function-call + monkeypatch isolation pattern as
test_watchlist_promotion_api.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from system.api import server


def _queue_payload(items: list[dict]) -> dict:
    return {
        "contract": "rb_intelligence_action_queue_v1", "date": "2026-09-15",
        "generated_at": "2026-09-15T09:00:00+00:00",
        "total_pending": len(items),
        "action_class_counts": {},
        "items": items,
        "negative_evidence": {"buying_window_checks_without_signal": 0, "pages_checked_without_change": 0},
        "policy": "recommendations_only",
    }


@pytest.fixture()
def isolated_queue(tmp_path: Path, monkeypatch):
    cache_dir = tmp_path / ".cache"
    cache_dir.mkdir()
    monkeypatch.setattr(server, "_auth", lambda *a, **k: None)
    monkeypatch.setattr(server.core, "CACHE_DIR", cache_dir)
    # server.iaq's CACHE_PATH/STATE_PATH were bound at import time from the
    # REAL core.CACHE_DIR -- patching core.CACHE_DIR above does not move
    # them, so resolve() (used by the POST endpoint) needs its own patch.
    monkeypatch.setattr(server.iaq, "CACHE_PATH", cache_dir / "intelligence_action_queue.json")
    monkeypatch.setattr(server.iaq, "STATE_PATH", cache_dir / "intelligence_action_queue_state.json")
    return cache_dir


def test_missing_cache_returns_404(isolated_queue):
    with pytest.raises(HTTPException) as exc_info:
        server.get_intelligence_action_queue(action_class=None, status=None, limit=50, x_api_key=None)
    assert exc_info.value.status_code == 404


def test_returns_full_queue_by_default(isolated_queue):
    items = [
        {"queue_id": "iaq-1", "entity": "Toast", "action_class": "research_further", "rank": 1},
        {"queue_id": "iaq-2", "entity": "Olo", "action_class": "monitor", "rank": 2},
    ]
    (isolated_queue / "intelligence_action_queue.json").write_text(
        json.dumps(_queue_payload(items)), encoding="utf-8")

    result = server.get_intelligence_action_queue(action_class=None, status=None, limit=50, x_api_key=None)
    assert result["returned"] == 2
    assert [item["queue_id"] for item in result["items"]] == ["iaq-1", "iaq-2"]


def test_filters_by_action_class(isolated_queue):
    items = [
        {"queue_id": "iaq-1", "entity": "Toast", "action_class": "research_further", "rank": 1},
        {"queue_id": "iaq-2", "entity": "Olo", "action_class": "monitor", "rank": 2},
    ]
    (isolated_queue / "intelligence_action_queue.json").write_text(
        json.dumps(_queue_payload(items)), encoding="utf-8")

    result = server.get_intelligence_action_queue(action_class="monitor", status=None, limit=50, x_api_key=None)
    assert result["returned"] == 1
    assert result["items"][0]["queue_id"] == "iaq-2"


def test_respects_limit(isolated_queue):
    items = [{"queue_id": f"iaq-{i}", "entity": f"E{i}", "action_class": "monitor", "rank": i} for i in range(5)]
    (isolated_queue / "intelligence_action_queue.json").write_text(
        json.dumps(_queue_payload(items)), encoding="utf-8")

    result = server.get_intelligence_action_queue(action_class=None, status=None, limit=2, x_api_key=None)
    assert result["returned"] == 2
    assert result["total_pending"] == 5  # original count preserved, only the returned slice is trimmed


def test_filters_by_status(isolated_queue):
    items = [
        {"queue_id": "iaq-1", "entity": "Toast", "action_class": "research_further", "rank": 1, "status": "pending_review"},
        {"queue_id": "iaq-2", "entity": "Olo", "action_class": "monitor", "rank": 2, "status": "resolved"},
    ]
    (isolated_queue / "intelligence_action_queue.json").write_text(
        json.dumps(_queue_payload(items)), encoding="utf-8")

    result = server.get_intelligence_action_queue(action_class=None, status="resolved", limit=50, x_api_key=None)
    assert result["returned"] == 1
    assert result["items"][0]["queue_id"] == "iaq-2"


class TestResolveEndpoint:
    """API-layer coverage for POST /intelligence-action-queue/{queue_id}/resolve
    (resolveIntelligenceActionQueueItem) — the write path outcome
    calibration (Codex handoff item #5) needs to durably record Todd's
    disposition on a queue item."""

    def test_resolve_unknown_queue_id_returns_404(self, isolated_queue):
        (isolated_queue / "intelligence_action_queue.json").write_text(
            json.dumps(_queue_payload([])), encoding="utf-8")
        with pytest.raises(HTTPException) as exc_info:
            server.post_resolve_intelligence_action_queue_item(
                "iaq-doesnotexist", "accepted", note="", x_api_key=None)
        assert exc_info.value.status_code == 404

    def test_resolve_invalid_disposition_returns_422(self, isolated_queue):
        items = [{"queue_id": "iaq-1", "entity": "Toast", "action_class": "research_further",
                  "item_type": "competitor_review", "priority_score": 55, "rank": 1}]
        (isolated_queue / "intelligence_action_queue.json").write_text(
            json.dumps(_queue_payload(items)), encoding="utf-8")
        with pytest.raises(HTTPException) as exc_info:
            server.post_resolve_intelligence_action_queue_item(
                "iaq-1", "maybe", note="", x_api_key=None)
        assert exc_info.value.status_code == 422

    def test_resolve_records_disposition_and_persists(self, isolated_queue):
        items = [{"queue_id": "iaq-1", "entity": "Toast", "action_class": "research_further",
                  "item_type": "competitor_review", "priority_score": 55, "rank": 1}]
        (isolated_queue / "intelligence_action_queue.json").write_text(
            json.dumps(_queue_payload(items)), encoding="utf-8")

        result = server.post_resolve_intelligence_action_queue_item(
            "iaq-1", "accepted", note="Calling Toast's champion this week.", x_api_key=None)
        assert result["ok"] is True
        assert result["entry"]["disposition"] == "accepted"
        assert result["entry"]["entity"] == "Toast"

        # Durable: readable back from STATE_PATH directly, independent of
        # whatever the next intelligence_action_queue.build() does.
        state = json.loads((isolated_queue / "intelligence_action_queue_state.json").read_text())
        assert state["resolutions"]["iaq-1"]["disposition"] == "accepted"
        assert state["resolutions"]["iaq-1"]["note"] == "Calling Toast's champion this week."
