#!/usr/bin/env python3
"""RB-DEFECT-064 Phase 5: contacts_ingest.py wired to post_ingest_intelligence.py
for the same dormancy Stage 6 signal hubspot_ingest.py and linkedin_ingest.py
already surface. No warm_intro_candidates here -- Apple Contacts import never
auto-creates new baseline entries (unmatched/probable rows go to the
reconciliation queue for operator confirmation), so there's no newly-created
contact set to score for a broker."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import contacts_ingest as ci  # noqa: E402
import rb_core as core  # noqa: E402


def test_dormant_relationship_resurfaced_on_phone_match(tmp_path, monkeypatch):
    baseline = [
        {"id": "dormant-person", "name": "Dormant Person", "current_company": "Old Co",
         "phone": "5855550100", "email": None, "sources": [], "signal_class": "VC",
         "circles": [], "tags": [], "notes": "", "last_touch": None},
    ]
    baseline_path = tmp_path / "baseline_index.json"
    baseline_path.write_text(json.dumps(baseline))

    contacts_csv = tmp_path / "contacts.csv"
    contacts_csv.write_text("name,phone,email\nDormant Person,585-555-0100,dormant@example.com\n")

    monkeypatch.setattr(core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(ci, "DELTAS_DIR", tmp_path / "deltas")
    monkeypatch.setattr(ci, "LATEST_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(ci, "IDENTITY_MAP_PATH", tmp_path / "identity_map.json")
    monkeypatch.setattr(ci, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(ci, "RESOLVED_PATH", tmp_path / "resolved.json")
    monkeypatch.setattr(ci, "MANIFEST_PATH", tmp_path / "manifest.json")

    result = ci.ingest_file(contacts_csv, dry_run=True)

    dormant = result["post_ingest_intelligence"]["dormant_relationships_resurfaced"]
    assert len(dormant) == 1
    assert dormant[0]["id"] == "dormant-person"
    assert dormant[0]["days_since_last_touch"] is None


def test_no_dormant_signal_for_fresh_contact(tmp_path, monkeypatch):
    baseline = [
        {"id": "fresh-person", "name": "Fresh Person", "current_company": "New Co",
         "phone": "5855550200", "email": None, "sources": [], "signal_class": "VC",
         "circles": [], "tags": [], "notes": "", "last_touch": "2026-07-08"},
    ]
    baseline_path = tmp_path / "baseline_index.json"
    baseline_path.write_text(json.dumps(baseline))

    contacts_csv = tmp_path / "contacts.csv"
    contacts_csv.write_text("name,phone,email\nFresh Person,585-555-0200,fresh@example.com\n")

    monkeypatch.setattr(core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(ci, "DELTAS_DIR", tmp_path / "deltas")
    monkeypatch.setattr(ci, "LATEST_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(ci, "IDENTITY_MAP_PATH", tmp_path / "identity_map.json")
    monkeypatch.setattr(ci, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(ci, "RESOLVED_PATH", tmp_path / "resolved.json")
    monkeypatch.setattr(ci, "MANIFEST_PATH", tmp_path / "manifest.json")

    result = ci.ingest_file(contacts_csv, dry_run=True)

    assert result["post_ingest_intelligence"]["dormant_relationships_resurfaced"] == []


def test_delta_report_renders_post_ingest_intelligence_section(tmp_path, monkeypatch):
    baseline = [
        {"id": "dormant-person", "name": "Dormant Person", "current_company": "Old Co",
         "phone": "5855550100", "email": None, "sources": [], "signal_class": "VC",
         "circles": [], "tags": [], "notes": "", "last_touch": None},
    ]
    baseline_path = tmp_path / "baseline_index.json"
    baseline_path.write_text(json.dumps(baseline))

    contacts_csv = tmp_path / "contacts.csv"
    contacts_csv.write_text("name,phone,email\nDormant Person,585-555-0100,dormant@example.com\n")

    deltas_dir = tmp_path / "deltas"
    monkeypatch.setattr(core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(ci, "DELTAS_DIR", deltas_dir)
    monkeypatch.setattr(ci, "LATEST_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(ci, "IDENTITY_MAP_PATH", tmp_path / "identity_map.json")
    monkeypatch.setattr(ci, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(ci, "RESOLVED_PATH", tmp_path / "resolved.json")
    monkeypatch.setattr(ci, "MANIFEST_PATH", tmp_path / "manifest.json")

    result = ci.ingest_file(contacts_csv, dry_run=False)
    report_path = Path(core.PROJECT_DIR / result["report_path"])

    text = report_path.read_text()
    assert "Post-Ingest Intelligence" in text
    assert "Dormant relationships resurfaced" in text
    assert "Dormant Person" in text
