#!/usr/bin/env python3
"""RB-DEFECT-2026-07-09b: fetch_google.py's fetch_calendar() gained support
for secondary/subscribed calendars (e.g. an employer's Outlook calendar
published via ICS and subscribed to under the same Google account) so
those events merge in alongside the account's primary calendar, tagged
with source_calendar_id/source_calendar_label."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import fetch_google as fg  # noqa: E402


def test_tag_events_with_source_adds_fields_without_mutating_input():
    normalized = {"events": [{"id": "ev-1", "title": "Standup"}]}
    tagged = fg._tag_events_with_source(
        normalized, calendar_id="cal-123", label="Global Payments (Outlook ICS subscription)",
    )
    assert tagged == [{
        "id": "ev-1", "title": "Standup",
        "source_calendar_id": "cal-123",
        "source_calendar_label": "Global Payments (Outlook ICS subscription)",
    }]
    # Original dict inside `normalized` must be untouched (defensive copy).
    assert "source_calendar_id" not in normalized["events"][0]


def test_tag_events_with_source_empty_events_list():
    assert fg._tag_events_with_source({"events": []}, calendar_id="x", label="y") == []
    assert fg._tag_events_with_source({}, calendar_id="x", label="y") == []


class _FakeEventsResource:
    def __init__(self, by_calendar: dict[str, list[dict]]):
        self._by_calendar = by_calendar

    def list(self, *, calendarId, **kwargs):  # noqa: N803 — matches Google API kw
        return _FakeRequest(self._by_calendar.get(calendarId, []))


class _FakeRequest:
    def __init__(self, items: list[dict]):
        self._items = items

    def execute(self):
        return {"items": self._items, "nextPageToken": None}


class _FakeService:
    def __init__(self, by_calendar: dict[str, list[dict]]):
        self._events = _FakeEventsResource(by_calendar)

    def events(self):
        return self._events


def test_fetch_calendar_merges_primary_and_secondary_events(monkeypatch):
    primary_events = [{
        "id": "primary-1", "summary": "Personal errand",
        "start": {"dateTime": "2026-07-10T09:00:00-05:00"},
        "end": {"dateTime": "2026-07-10T09:30:00-05:00"},
    }]
    secondary_events = [{
        "id": "gp-1", "summary": "GP standup",
        "start": {"dateTime": "2026-07-10T10:00:00-05:00"},
        "end": {"dateTime": "2026-07-10T10:30:00-05:00"},
    }]
    by_calendar = {
        "primary": primary_events,
        "gp-calendar-id": secondary_events,
    }

    monkeypatch.setattr(fg, "_ensure_creds", lambda account_id, allow_consent=True: object())
    monkeypatch.setattr(
        "googleapiclient.discovery.build",
        lambda *a, **k: _FakeService(by_calendar),
    )

    result = fg.fetch_calendar(
        7, account_id="personal",
        secondary_calendars=[{"id": "gp-calendar-id", "label": "Global Payments (Outlook ICS subscription)"}],
    )

    ids = {ev["id"]: ev for ev in result["events"]}
    assert set(ids) == {"primary-1", "gp-1"}
    assert "source_calendar_id" not in ids["primary-1"]
    assert ids["gp-1"]["source_calendar_id"] == "gp-calendar-id"
    assert ids["gp-1"]["source_calendar_label"] == "Global Payments (Outlook ICS subscription)"


def test_fetch_calendar_with_no_secondary_calendars_is_unchanged(monkeypatch):
    primary_events = [{
        "id": "primary-1", "summary": "Personal errand",
        "start": {"dateTime": "2026-07-10T09:00:00-05:00"},
        "end": {"dateTime": "2026-07-10T09:30:00-05:00"},
    }]
    monkeypatch.setattr(fg, "_ensure_creds", lambda account_id, allow_consent=True: object())
    monkeypatch.setattr(
        "googleapiclient.discovery.build",
        lambda *a, **k: _FakeService({"primary": primary_events}),
    )

    result = fg.fetch_calendar(7, account_id="personal")
    assert [ev["id"] for ev in result["events"]] == ["primary-1"]
