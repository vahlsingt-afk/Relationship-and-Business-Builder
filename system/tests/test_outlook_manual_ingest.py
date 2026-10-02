#!/usr/bin/env python3
"""
Tests for outlook_manual_ingest.py — manual Outlook (.ics/.eml) export
ingester for the Global Payments account, added 2026-07-29 after confirming
New Outlook for Mac has no working AppleScript bridge and no local readable
mail/calendar store.

Test IDs and coverage:

RB-OUTLOOK-001: parse_ics extracts UID/summary/location/description
RB-OUTLOOK-002: parse_ics maps a known TZID to a fixed UTC offset
RB-OUTLOOK-003: parse_ics passes through a Z-suffixed UTC datetime unchanged (as offset)
RB-OUTLOOK-004: parse_ics handles an all-day VALUE=DATE event
RB-OUTLOOK-005: parse_ics reassembles a folded (line-continuation) ATTENDEE
RB-OUTLOOK-006: parse_ics parses multiple VEVENTs in one file
RB-OUTLOOK-007: parse_eml extracts sender/subject/date/snippet/message-id
RB-OUTLOOK-008: parse_eml falls back to a content hash when Message-ID is missing
RB-OUTLOOK-009: _merge_by_key upserts on matching id, keeps unrelated existing items
RB-OUTLOOK-010: ingest_ics_file writes normalized events to calendar.global-payments.json
RB-OUTLOOK-011: ingest_eml_file writes normalized threads to email.global-payments.json
RB-OUTLOOK-012: ingest_ics_file dry_run leaves the target file untouched
RB-OUTLOOK-013: re-ingesting the same event id updates rather than duplicates
RB-OUTLOOK-014: run_ingest_new skips files already recorded in the manifest
RB-OUTLOOK-015: ingest_file dispatches by extension and rejects unsupported ones
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import outlook_manual_ingest as om  # noqa: E402
import rb_core as core  # noqa: E402


ICS_BASIC = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:event-001
SUMMARY:Catch up with Amy Spytko
DTSTART;TZID=Central Standard Time:20260730T150000
DTEND;TZID=Central Standard Time:20260730T153000
LOCATION:Teams Meeting
DESCRIPTION:Quick sync
ORGANIZER;CN=Todd Vahlsing:mailto:tv74852@globalpayments.com
END:VEVENT
END:VCALENDAR
"""

ICS_FOLDED_ATTENDEE = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:event-folded
SUMMARY:Long attendee test
DTSTART:20260730T150000Z
DTEND:20260730T153000Z
ATTENDEE;CN=Amy Spytko;PARTSTAT=ACCEPTED:mailto:amy.spytko@globalpayments.
 com
END:VEVENT
END:VCALENDAR
"""

ICS_ALL_DAY = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:event-allday
SUMMARY:Company Holiday
DTSTART;VALUE=DATE:20260904
DTEND;VALUE=DATE:20260905
END:VEVENT
END:VCALENDAR
"""

ICS_MULTI = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:multi-1
SUMMARY:First
DTSTART:20260730T150000Z
DTEND:20260730T153000Z
END:VEVENT
BEGIN:VEVENT
UID:multi-2
SUMMARY:Second
DTSTART:20260731T150000Z
DTEND:20260731T153000Z
END:VEVENT
END:VCALENDAR
"""


def _eml_bytes(message_id: str | None = "abc123@example.com") -> bytes:
    header_id = f"Message-ID: <{message_id}>\n" if message_id else ""
    return (
        "From: Dale McKee <dale.mckee@example.com>\n"
        "To: Todd Vahlsing <tv74852@globalpayments.com>\n"
        "Subject: Re: PAR capture follow up\n"
        "Date: Wed, 29 Jul 2026 08:56:00 -0500\n"
        f"{header_id}"
        'Content-Type: text/plain; charset="utf-8"\n'
        "\n"
        "Understood. Just earlier I saw the timeline shift.\n"
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# .ics parsing
# ---------------------------------------------------------------------------

def test_parse_ics_basic_fields():
    """RB-OUTLOOK-001."""
    events = om.parse_ics(ICS_BASIC)
    assert len(events) == 1
    ev = events[0]
    assert ev["id"] == "event-001"
    assert ev["summary"] == "Catch up with Amy Spytko"
    assert ev["location"] == "Teams Meeting"
    assert ev["description"] == "Quick sync"
    assert ev["organizer"] == {"email": "tv74852@globalpayments.com", "name": "Todd Vahlsing"}


def test_parse_ics_tzid_offset_mapping():
    """RB-OUTLOOK-002."""
    events = om.parse_ics(ICS_BASIC)
    assert events[0]["start"]["dateTime"] == "2026-07-30T15:00:00-06:00"
    assert events[0]["end"]["dateTime"] == "2026-07-30T15:30:00-06:00"


def test_parse_ics_utc_z_suffix():
    """RB-OUTLOOK-003."""
    events = om.parse_ics(ICS_FOLDED_ATTENDEE)
    assert events[0]["start"]["dateTime"] == "2026-07-30T15:00:00+00:00"


def test_parse_ics_all_day_event():
    """RB-OUTLOOK-004."""
    events = om.parse_ics(ICS_ALL_DAY)
    assert events[0]["start"]["dateTime"] == "2026-09-04"
    assert events[0]["end"]["dateTime"] == "2026-09-05"


def test_parse_ics_folded_attendee_line():
    """RB-OUTLOOK-005."""
    events = om.parse_ics(ICS_FOLDED_ATTENDEE)
    attendees = events[0]["attendees"]
    assert len(attendees) == 1
    assert attendees[0]["email"] == "amy.spytko@globalpayments.com"
    assert attendees[0]["name"] == "Amy Spytko"
    assert attendees[0]["responseStatus"] == "accepted"


def test_parse_ics_multiple_vevents():
    """RB-OUTLOOK-006."""
    events = om.parse_ics(ICS_MULTI)
    assert [e["id"] for e in events] == ["multi-1", "multi-2"]


# ---------------------------------------------------------------------------
# .eml parsing
# ---------------------------------------------------------------------------

def test_parse_eml_basic_fields():
    """RB-OUTLOOK-007."""
    thread = om.parse_eml(_eml_bytes())
    assert thread["id"] == "abc123@example.com"
    msg = thread["messages"][0]
    assert msg["sender"] == "Dale McKee <dale.mckee@example.com>"
    assert msg["subject"] == "Re: PAR capture follow up"
    assert msg["date"] == "Wed, 29 Jul 2026 08:56:00 -0500"
    assert "timeline shift" in msg["snippet"]
    assert msg["toRecipients"] == ["Todd Vahlsing <tv74852@globalpayments.com>"]


def test_parse_eml_missing_message_id_falls_back_to_hash():
    """RB-OUTLOOK-008."""
    raw = _eml_bytes(message_id=None)
    thread = om.parse_eml(raw)
    assert thread["id"] and "@" not in thread["id"]
    assert len(thread["id"]) == 16


# ---------------------------------------------------------------------------
# Merge semantics
# ---------------------------------------------------------------------------

def test_merge_by_key_upserts_and_preserves_unrelated():
    """RB-OUTLOOK-009."""
    existing = [{"id": "a", "v": 1}, {"id": "b", "v": 1}]
    new = [{"id": "b", "v": 2}, {"id": "c", "v": 1}]
    merged = om._merge_by_key(existing, new, "id")
    by_id = {m["id"]: m for m in merged}
    assert by_id["a"]["v"] == 1
    assert by_id["b"]["v"] == 2
    assert by_id["c"]["v"] == 1
    assert len(merged) == 3


# ---------------------------------------------------------------------------
# File-writing ingest (isolated via tmp_path)
# ---------------------------------------------------------------------------

def test_ingest_ics_file_writes_calendar_json(tmp_path, monkeypatch):
    """RB-OUTLOOK-010."""
    monkeypatch.setattr(core, "INBOX_DIR", tmp_path / "inbox")
    ics_path = tmp_path / "sample.ics"
    ics_path.write_text(ICS_BASIC)

    result = om.ingest_ics_file(ics_path, dry_run=False)
    assert result["ok"] and result["kind"] == "calendar"

    target = core.calendar_path_for(om.ACCOUNT_ID)
    data = json.loads(target.read_text())
    assert len(data["events"]) == 1
    assert data["events"][0]["id"] == "event-001"
    assert data["events"][0]["source"] == "outlook_manual_export"


def test_ingest_eml_file_writes_email_json(tmp_path, monkeypatch):
    """RB-OUTLOOK-011."""
    monkeypatch.setattr(core, "INBOX_DIR", tmp_path / "inbox")
    eml_path = tmp_path / "sample.eml"
    eml_path.write_bytes(_eml_bytes())

    result = om.ingest_eml_file(eml_path, dry_run=False)
    assert result["ok"] and result["kind"] == "email"

    target = core.email_path_for(om.ACCOUNT_ID)
    data = json.loads(target.read_text())
    assert len(data["threads"]) == 1
    assert data["threads"][0]["thread_id"] == "abc123@example.com"
    assert data["threads"][0]["source"] == "outlook_manual_export"


def test_ingest_ics_file_dry_run_writes_nothing(tmp_path, monkeypatch):
    """RB-OUTLOOK-012."""
    monkeypatch.setattr(core, "INBOX_DIR", tmp_path / "inbox")
    ics_path = tmp_path / "sample.ics"
    ics_path.write_text(ICS_BASIC)

    om.ingest_ics_file(ics_path, dry_run=True)
    assert not core.calendar_path_for(om.ACCOUNT_ID).exists()


def test_reingesting_same_event_id_updates_not_duplicates(tmp_path, monkeypatch):
    """RB-OUTLOOK-013."""
    monkeypatch.setattr(core, "INBOX_DIR", tmp_path / "inbox")
    ics_path = tmp_path / "sample.ics"
    ics_path.write_text(ICS_BASIC)
    om.ingest_ics_file(ics_path, dry_run=False)

    updated = ICS_BASIC.replace("Catch up with Amy Spytko", "Catch up with Amy Spytko (moved)")
    ics_path.write_text(updated)
    om.ingest_ics_file(ics_path, dry_run=False)

    data = json.loads(core.calendar_path_for(om.ACCOUNT_ID).read_text())
    assert len(data["events"]) == 1
    assert data["events"][0]["title"] == "Catch up with Amy Spytko (moved)"


def test_run_ingest_new_skips_already_processed_files(tmp_path, monkeypatch):
    """RB-OUTLOOK-014."""
    monkeypatch.setattr(core, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr(core, "CACHE_DIR", tmp_path / "cache")
    exports_dir = tmp_path / "outlook_exports"
    exports_dir.mkdir()
    monkeypatch.setattr(om, "EXPORTS_DIR", exports_dir)
    monkeypatch.setattr(om, "MANIFEST_PATH", tmp_path / "cache" / "outlook_manual_ingest_manifest.json")

    (exports_dir / "one.ics").write_text(ICS_BASIC)

    first = om.run_ingest_new(dry_run=False)
    assert first["files_processed"] == 1

    second = om.run_ingest_new(dry_run=False)
    assert second["files_processed"] == 0


def test_ingest_file_rejects_unsupported_extension(tmp_path):
    """RB-OUTLOOK-015."""
    path = tmp_path / "notes.txt"
    path.write_text("not a calendar or email export")
    result = om.ingest_file(path, dry_run=True)
    assert result["ok"] is False
    assert "unsupported extension" in result["error"]
