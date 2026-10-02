#!/usr/bin/env python3
"""
test_contacts_ingest_overwrite_safety.py

Follow-up to the 2026-09-18 intelligence-cycle repair (manual_relationship_
intake.py and refresh_sources.py both silently overwrote canonical contact
fields without any conflict gate -- see system/CLAUDE_HANDOFF_INTELLIGENCE_
CYCLE_REPAIR_2026-09-18.md). An audit of the remaining ingestion paths that
write directly to baseline_index.json (contacts_ingest.py, hubspot_ingest.py,
whatsapp_ingest.py) found none of them currently overwrite a non-null
existing value for phone/email -- contacts_ingest.py's ingest_file() gates
both behind `if phones and not match.get("phone")` / `if ... and not
match.get("email")` (net-new-fill only). But that safety was IMPLICIT and
untested: nothing in the existing suite constructed an existing contact with
a non-null phone/email and a conflicting new value to prove the gate holds.
Per the audit's own conclusion, an unannotated `if not match.get(...)`
condition is exactly the kind of thing a future refactor could silently
regress with no test catching it. This file closes that gap.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import contacts_ingest as ci  # noqa: E402
import rb_core as core  # noqa: E402


def _isolate(monkeypatch, tmp_path: Path) -> Path:
    baseline_path = tmp_path / "baseline_index.json"
    monkeypatch.setattr(core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(ci, "DELTAS_DIR", tmp_path / "deltas")
    monkeypatch.setattr(ci, "LATEST_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(ci, "IDENTITY_MAP_PATH", tmp_path / "identity_map.json")
    monkeypatch.setattr(ci, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(ci, "RESOLVED_PATH", tmp_path / "resolved.json")
    monkeypatch.setattr(ci, "MANIFEST_PATH", tmp_path / "manifest.json")
    return baseline_path


def test_existing_phone_not_overwritten_by_conflicting_import(tmp_path, monkeypatch):
    """RB-CONTACTS-012: a contact with a phone already on file must keep it
    even when an imported .vcf/.csv row carries a DIFFERENT number for the
    same person -- contacts_ingest.py has no confirmation-required path for
    this (unlike hubspot_ingest.py's explicit CONFLICT-note gate for
    current_company), so the only thing preventing a silent clobber today
    is the `not match.get("phone")` null-check itself."""
    baseline = [
        {"id": "existing-person", "name": "Existing Person", "current_company": "Old Co",
         "email": "existing@example.com", "phone": "312-555-0100", "sources": [], "signal_class": "VC",
         "circles": [], "tags": [], "notes": ""},
    ]
    baseline_path = _isolate(monkeypatch, tmp_path)
    baseline_path.write_text(json.dumps(baseline))

    contacts_csv = tmp_path / "contacts.csv"
    contacts_csv.write_text(
        "name,phone,email\nExisting Person,646-555-0199,different@example.com\n"
    )

    result = ci.ingest_file(contacts_csv, dry_run=False)

    assert result["ok"] is True
    saved = json.loads(baseline_path.read_text())
    entry = next(e for e in saved if e["id"] == "existing-person")
    assert entry["phone"] == "312-555-0100"
    assert entry["email"] == "existing@example.com"


def test_null_phone_and_email_still_fill_from_import(tmp_path, monkeypatch):
    """The companion positive case -- confirms the fix above doesn't
    accidentally block the legitimate net-new-fill path."""
    baseline = [
        {"id": "existing-person", "name": "Existing Person", "current_company": "Old Co",
         "email": None, "phone": None, "sources": [], "signal_class": "VC",
         "circles": [], "tags": [], "notes": ""},
    ]
    baseline_path = _isolate(monkeypatch, tmp_path)
    baseline_path.write_text(json.dumps(baseline))

    contacts_csv = tmp_path / "contacts.csv"
    contacts_csv.write_text(
        "name,phone,email\nExisting Person,312-555-0100,existing@example.com\n"
    )

    result = ci.ingest_file(contacts_csv, dry_run=False)

    assert result["ok"] is True
    saved = json.loads(baseline_path.read_text())
    entry = next(e for e in saved if e["id"] == "existing-person")
    assert entry["phone"]
    assert entry["email"]
