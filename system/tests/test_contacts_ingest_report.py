#!/usr/bin/env python3
"""
Tests for contacts_ingest.py's mutation report (RB-DEFECT-064 Phase 3).

Before this, contacts_ingest.py wrote zero markdown delta report — only a
JSON cache. Test IDs and coverage:

RB-CONTACTSREPORT-001: _render_report emits the canonical mutation-report block
RB-CONTACTSREPORT-002: new_people_created is always 0 (this pipeline never creates baseline entries)
RB-CONTACTSREPORT-003: companies_added/relationship_links_created render as "not computed", with reasons
RB-CONTACTSREPORT-004: ingest_file (real run, tmp paths only) writes a delta report to DELTAS_DIR
RB-CONTACTSREPORT-005: ingest_file with dry_run=True does not write a delta report
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import contacts_ingest as ci  # noqa: E402
import rb_core as core  # noqa: E402


def _result(**overrides):
    base = {
        "trust_stats": {
            "total_parsed": 10, "matched_network_entities": 6, "probable_network_entities": 2,
            "unmatched": 2, "baseline_mutations": 3, "employer_changes_detected": 1,
            "confidence": "high",
        },
        "cos_assessment": {"notable_changes": ["Jane Doe moved from Old Co to New Co"]},
    }
    base.update(overrides)
    return base


def test_render_report_has_canonical_block():
    """RB-CONTACTSREPORT-001."""
    md = ci._render_report(_result(), date(2026, 7, 7))
    for line in (
        "# Apple Contacts Export Ingest — 2026-07-07",
        "People Imported: 10",
        "Existing People Updated: 6",
        "Duplicate Candidates: 2",
        "Knowledge Mutations Applied: 3",
        "Confidence: high",
    ):
        assert line in md, f"missing {line!r}"


def test_new_people_created_always_zero():
    """RB-CONTACTSREPORT-002."""
    md = ci._render_report(_result(), date(2026, 7, 7))
    assert "New People Created: 0" in md


def test_unwritten_fields_render_not_computed_with_reasons():
    """RB-CONTACTSREPORT-003."""
    md = ci._render_report(_result(), date(2026, 7, 7))
    assert "Companies Added: not computed" in md
    assert "Relationship Links Created: not computed" in md
    assert "never writes company data to baseline" in md
    assert "1 employer change(s) detected" in md


def test_ingest_file_writes_delta_report(tmp_path, monkeypatch):
    """RB-CONTACTSREPORT-004: exercises the real write path, entirely against tmp_path."""
    baseline = [
        {"id": "existing-person", "name": "Existing Person", "current_company": "Old Co",
         "email": None, "phone": None, "sources": [], "signal_class": "VC",
         "circles": [], "tags": [], "notes": ""},
    ]
    baseline_path = tmp_path / "baseline_index.json"
    import json
    baseline_path.write_text(json.dumps(baseline))

    contacts_csv = tmp_path / "contacts.csv"
    contacts_csv.write_text("name,phone,email\nExisting Person,555-0100,existing@example.com\n")

    deltas_dir = tmp_path / "deltas"
    monkeypatch.setattr(core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(ci, "DELTAS_DIR", deltas_dir)
    monkeypatch.setattr(ci, "LATEST_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(ci, "IDENTITY_MAP_PATH", tmp_path / "identity_map.json")
    monkeypatch.setattr(ci, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(ci, "RESOLVED_PATH", tmp_path / "resolved.json")
    monkeypatch.setattr(ci, "MANIFEST_PATH", tmp_path / "manifest.json")

    result = ci.ingest_file(contacts_csv, dry_run=False)

    assert result["ok"] is True
    assert deltas_dir.exists()
    reports = list(deltas_dir.glob("apple_contacts_export_*.md"))
    assert len(reports) == 1
    assert "Mutation Report" in reports[0].read_text()


def test_ingest_file_dry_run_writes_no_report(tmp_path, monkeypatch):
    """RB-CONTACTSREPORT-005."""
    baseline = [
        {"id": "existing-person", "name": "Existing Person", "current_company": "Old Co",
         "email": None, "phone": None, "sources": [], "signal_class": "VC",
         "circles": [], "tags": [], "notes": ""},
    ]
    baseline_path = tmp_path / "baseline_index.json"
    import json
    baseline_path.write_text(json.dumps(baseline))

    contacts_csv = tmp_path / "contacts.csv"
    contacts_csv.write_text("name,phone,email\nExisting Person,555-0100,existing@example.com\n")

    deltas_dir = tmp_path / "deltas"
    monkeypatch.setattr(core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(ci, "DELTAS_DIR", deltas_dir)
    monkeypatch.setattr(ci, "LATEST_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(ci, "IDENTITY_MAP_PATH", tmp_path / "identity_map.json")
    monkeypatch.setattr(ci, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(ci, "RESOLVED_PATH", tmp_path / "resolved.json")
    monkeypatch.setattr(ci, "MANIFEST_PATH", tmp_path / "manifest.json")

    result = ci.ingest_file(contacts_csv, dry_run=True)

    assert result["ok"] is True
    assert not deltas_dir.exists()
