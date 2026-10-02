#!/usr/bin/env python3
"""
Tests for interaction_capture.py — durable, content-free interaction facts
distilled from email_overlay()/calendar_overlay() before the raw
source_cache (72h, per retention_policy.py) rolls over.

Test IDs:

RB-INTCAP-001: email_overlay from_baseline rows become durable interaction_occurred events
RB-INTCAP-002: emitted events carry no subject/body/description content
RB-INTCAP-003: calendar_overlay all_with_baseline_match rows become durable events, including past events
RB-INTCAP-004: dry_run=True computes without appending to ri_events
RB-INTCAP-005: interactions_for_contact() returns only that contact's events via ri_events entity filter
RB-INTCAP-006: ri_events.load_events(entity_id=...) matches on entities.people[].matched_id
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import interaction_capture as icap  # noqa: E402
import ri_events  # noqa: E402


def _patch_ri_events(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ri_events, "EVENTS_DIR", tmp_path / "ri_events")
    monkeypatch.setattr(ri_events, "CACHE_DIR", tmp_path / "ri_cache")
    monkeypatch.setattr(ri_events, "INDEX_PATH", tmp_path / "ri_cache" / "ri_events_index.json")


def test_email_interactions_captured_as_durable_events(tmp_path, monkeypatch):
    """RB-INTCAP-001."""
    _patch_ri_events(monkeypatch, tmp_path)
    overlay = {
        "from_baseline": [
            {
                "thread_id": "t1", "subject": "Q3 renewal", "last_message_at": "2026-06-01T10:00:00Z",
                "match": {"id": "jane-doe", "name": "Jane Doe"},
            },
        ],
    }
    monkeypatch.setattr(icap.core, "email_overlay", lambda **kw: overlay)

    captured = icap.capture_email_interactions()
    assert len(captured) == 1
    events = ri_events.load_events(entity_id="jane-doe")
    assert len(events) == 1
    assert events[0]["signal"]["type"] == "interaction_occurred"
    assert events[0]["signal"]["channel"] == "email"
    assert events[0]["event_at"] == "2026-06-01"


def test_captured_events_carry_no_content(tmp_path, monkeypatch):
    """RB-INTCAP-002."""
    _patch_ri_events(monkeypatch, tmp_path)
    overlay = {
        "from_baseline": [
            {
                "thread_id": "t1", "subject": "confidential deal terms", "last_message_at": "2026-06-01T10:00:00Z",
                "match": {"id": "jane-doe", "name": "Jane Doe"},
            },
        ],
    }
    monkeypatch.setattr(icap.core, "email_overlay", lambda **kw: overlay)

    icap.capture_email_interactions()
    events = ri_events.load_events(entity_id="jane-doe")
    dumped = str(events[0])
    assert "confidential deal terms" not in dumped
    assert "subject" not in events[0]["signal"]


def test_calendar_interactions_captured_including_past_events(tmp_path, monkeypatch):
    """RB-INTCAP-003."""
    _patch_ri_events(monkeypatch, tmp_path)
    overlay = {
        "all_with_baseline_match": [
            {
                "id": "ev1", "title": "Old meeting", "start": "2025-01-15T09:00:00Z",
                "attendees_matched": [{"id": "john-smith", "name": "John Smith"}],
            },
        ],
    }
    monkeypatch.setattr(icap.core, "calendar_overlay", lambda *a, **kw: overlay)

    captured = icap.capture_calendar_interactions()
    assert len(captured) == 1
    events = ri_events.load_events(entity_id="john-smith")
    assert events[0]["event_at"] == "2025-01-15"
    assert events[0]["signal"]["channel"] == "calendar"


def test_dry_run_does_not_append(tmp_path, monkeypatch):
    """RB-INTCAP-004."""
    _patch_ri_events(monkeypatch, tmp_path)
    overlay = {
        "from_baseline": [
            {"thread_id": "t1", "last_message_at": "2026-06-01T10:00:00Z", "match": {"id": "jane-doe", "name": "Jane Doe"}},
        ],
    }
    monkeypatch.setattr(icap.core, "email_overlay", lambda **kw: overlay)

    icap.capture_email_interactions(dry_run=True)
    assert ri_events.load_events(entity_id="jane-doe") == []


def test_interactions_for_contact_scopes_to_one_entity(tmp_path, monkeypatch):
    """RB-INTCAP-005."""
    _patch_ri_events(monkeypatch, tmp_path)
    overlay = {
        "from_baseline": [
            {"thread_id": "t1", "last_message_at": "2026-06-01T10:00:00Z", "match": {"id": "jane-doe", "name": "Jane Doe"}},
            {"thread_id": "t2", "last_message_at": "2026-06-02T10:00:00Z", "match": {"id": "john-smith", "name": "John Smith"}},
        ],
    }
    monkeypatch.setattr(icap.core, "email_overlay", lambda **kw: overlay)

    icap.capture_email_interactions()
    jane_events = icap.interactions_for_contact("jane-doe")
    assert len(jane_events) == 1
    assert jane_events[0]["entities"]["people"][0]["matched_id"] == "jane-doe"


def test_ri_events_entity_filter_matches_matched_id(tmp_path, monkeypatch):
    """RB-INTCAP-006."""
    _patch_ri_events(monkeypatch, tmp_path)
    event = {
        "event_at": "2026-06-01",
        "event_at_confidence": "high",
        "source": {"type": "passive_signal", "id": "test:jane-doe"},
        "entities": {"people": [{"id": "jane-doe", "matched_id": "jane-doe", "decision": "matched_existing"}], "companies": []},
        "dedupe": {"decision": "new_event"},
        "signal": {"type": "interaction_occurred", "channel": "email"},
        "persistence": {"status": "persisted"},
    }
    ri_events.append(event)

    assert len(ri_events.load_events(entity_id="jane-doe")) == 1
    assert len(ri_events.load_events(entity_id="someone-else")) == 0
