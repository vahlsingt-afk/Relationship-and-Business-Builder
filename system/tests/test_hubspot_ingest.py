#!/usr/bin/env python3
"""
Tests for hubspot_ingest.py — HubSpot CRM export ingest (RB-DEFECT-064 Phase 1).

Test IDs and coverage:

RB-HUBINGEST-001: exact email match enhances the existing entry, no duplicate created
RB-HUBINGEST-002: unique-name match with a conflicting company logs a conflict, does not overwrite
RB-HUBINGEST-003: ambiguous name (2+ baseline entries share it, no email) is a duplicate candidate
RB-HUBINGEST-004: unmatched row creates a new VC-class baseline entry with the source tag
RB-HUBINGEST-005: rows with neither name nor email are skipped
RB-HUBINGEST-006: companies_added excludes companies already present in baseline
RB-HUBINGEST-007: dry_run=True computes the report without touching baseline_path
RB-HUBINGEST-008: real write path snapshots first and writes the delta report (tmp paths only)
RB-HUBINGEST-009: a non-HubSpot CSV refuses to ingest via classify() gate
RB-HUBINGEST-010: an existing contact with no last_touch on file is surfaced as dormant when updated
RB-HUBINGEST-011: a new contact's company with a viable existing insider surfaces as a warm-intro candidate
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import hubspot_ingest as hi  # noqa: E402
import rb_core as core  # noqa: E402

HEADERS = [
    "First Name", "Last Name", "Email", "Phone Number", "Company Name",
    "Job Title", "City", "State/Region", "Contact owner", "Lead Status",
    "Marketing contact status",
]


def _write_csv(tmp_path: Path, rows: list[dict], name: str = "hubspot-crm-exports-contacts.csv") -> Path:
    path = tmp_path / name
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        for row in rows:
            full = {h: "" for h in HEADERS}
            full.update(row)
            writer.writerow(full)
    return path


def _baseline() -> list[dict]:
    return [
        {
            "id": "existing-email-match", "name": "Existing Emailed", "current_company": None,
            "current_role": None, "email": "match@example.com", "phone": None,
            "sources": ["linkedin_export_2026-01-01"], "signal_class": "VC",
            "circles": [], "tags": [], "notes": "",
        },
        {
            "id": "existing-name-match", "name": "Name Only Match", "current_company": "Old Co",
            "current_role": "Analyst", "email": None, "phone": None,
            "sources": ["linkedin_export_2026-01-01"], "signal_class": "LMI",
            "circles": [], "tags": [], "notes": "",
        },
        {
            "id": "dup-a", "name": "Ambiguous Name", "current_company": "Co A",
            "current_role": None, "email": None, "phone": None,
            "sources": [], "signal_class": "VC", "circles": [], "tags": [], "notes": "",
        },
        {
            "id": "dup-b", "name": "Ambiguous Name", "current_company": "Co B",
            "current_role": None, "email": None, "phone": None,
            "sources": [], "signal_class": "VC", "circles": [], "tags": [], "notes": "",
        },
    ]


def _run(tmp_path, rows, **kwargs):
    baseline_path = tmp_path / "baseline_index.json"
    baseline_path.write_text(json.dumps(_baseline()))
    csv_path = _write_csv(tmp_path, rows)
    kwargs.setdefault("threads", [])
    result = hi.ingest(csv_path, ingest_date="2026-07-07", baseline_path=baseline_path, **kwargs)
    return result, baseline_path


def test_email_match_enhances_without_duplicate(tmp_path, monkeypatch):
    """RB-HUBINGEST-001."""
    monkeypatch.setattr(core, "SNAPSHOTS_DIR", tmp_path / "_snapshots")
    monkeypatch.setattr(hi, "DELTAS_DIR", tmp_path / "deltas")
    monkeypatch.setattr(hi, "LATEST_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(core, "CACHE_DIR", tmp_path / "cache")

    result, baseline_path = _run(tmp_path, [
        {"First Name": "Existing", "Last Name": "Emailed", "Email": "match@example.com", "Phone Number": "555-0100"},
    ])

    assert result["ok"] is True
    assert result["existing_people_updated"] == 1
    assert result["new_people_created"] == 0
    written = json.loads(baseline_path.read_text())
    assert len(written) == len(_baseline())
    entry = next(e for e in written if e["id"] == "existing-email-match")
    assert entry["phone"] == "555-0100"
    assert "hubspot_crm_export_2026-07-07" in entry["sources"]


def test_name_match_conflict_not_overwritten(tmp_path, monkeypatch):
    """RB-HUBINGEST-002."""
    monkeypatch.setattr(core, "SNAPSHOTS_DIR", tmp_path / "_snapshots")
    monkeypatch.setattr(hi, "DELTAS_DIR", tmp_path / "deltas")
    monkeypatch.setattr(hi, "LATEST_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(core, "CACHE_DIR", tmp_path / "cache")

    result, baseline_path = _run(tmp_path, [
        {"First Name": "Name", "Last Name": "Only Match", "Company Name": "New Co"},
    ])

    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["canonical"] == "Old Co"
    written = json.loads(baseline_path.read_text())
    entry = next(e for e in written if e["id"] == "existing-name-match")
    assert entry["current_company"] == "Old Co"
    assert "CONFLICT" in entry["notes"]


def test_ambiguous_name_is_duplicate_candidate(tmp_path, monkeypatch):
    """RB-HUBINGEST-003."""
    monkeypatch.setattr(core, "SNAPSHOTS_DIR", tmp_path / "_snapshots")
    monkeypatch.setattr(hi, "DELTAS_DIR", tmp_path / "deltas")
    monkeypatch.setattr(hi, "LATEST_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(core, "CACHE_DIR", tmp_path / "cache")

    result, _ = _run(tmp_path, [
        {"First Name": "Ambiguous", "Last Name": "Name", "Company Name": "Co C"},
    ])

    assert result["duplicate_candidates"] == 1
    assert result["new_people_created"] == 0
    assert result["existing_people_updated"] == 0


def test_unmatched_row_creates_new_vc_entry(tmp_path, monkeypatch):
    """RB-HUBINGEST-004."""
    monkeypatch.setattr(core, "SNAPSHOTS_DIR", tmp_path / "_snapshots")
    monkeypatch.setattr(hi, "DELTAS_DIR", tmp_path / "deltas")
    monkeypatch.setattr(hi, "LATEST_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(core, "CACHE_DIR", tmp_path / "cache")

    result, baseline_path = _run(tmp_path, [
        {"First Name": "Brand", "Last Name": "New", "Email": "brand.new@example.com", "Company Name": "Fresh Co"},
    ])

    assert result["new_people_created"] == 1
    written = json.loads(baseline_path.read_text())
    new_entry = next(e for e in written if e["name"] == "Brand New")
    assert new_entry["signal_class"] == "VC"
    assert new_entry["rc_tier"] is None
    assert new_entry["sources"] == ["hubspot_crm_export_2026-07-07"]


def test_rows_with_no_identifying_data_are_skipped(tmp_path, monkeypatch):
    """RB-HUBINGEST-005."""
    monkeypatch.setattr(core, "SNAPSHOTS_DIR", tmp_path / "_snapshots")
    monkeypatch.setattr(hi, "DELTAS_DIR", tmp_path / "deltas")
    monkeypatch.setattr(hi, "LATEST_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(core, "CACHE_DIR", tmp_path / "cache")

    result, baseline_path = _run(tmp_path, [
        {"Company Name": "No Name Or Email Co"},
    ])

    assert result["new_people_created"] == 0
    written = json.loads(baseline_path.read_text())
    assert len(written) == len(_baseline())


def test_companies_added_excludes_known_companies(tmp_path, monkeypatch):
    """RB-HUBINGEST-006."""
    monkeypatch.setattr(core, "SNAPSHOTS_DIR", tmp_path / "_snapshots")
    monkeypatch.setattr(hi, "DELTAS_DIR", tmp_path / "deltas")
    monkeypatch.setattr(hi, "LATEST_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(core, "CACHE_DIR", tmp_path / "cache")

    result, _ = _run(tmp_path, [
        {"First Name": "Existing", "Last Name": "Emailed", "Email": "match@example.com", "Company Name": "Old Co"},
        {"First Name": "Fresh", "Last Name": "Face", "Email": "fresh@example.com", "Company Name": "Truly New Co"},
    ])

    assert "Old Co" not in result["companies_added"]
    assert "Truly New Co" in result["companies_added"]


def test_dry_run_does_not_write_baseline(tmp_path, monkeypatch):
    """RB-HUBINGEST-007."""
    monkeypatch.setattr(core, "SNAPSHOTS_DIR", tmp_path / "_snapshots")
    monkeypatch.setattr(hi, "DELTAS_DIR", tmp_path / "deltas")
    monkeypatch.setattr(hi, "LATEST_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(core, "CACHE_DIR", tmp_path / "cache")

    result, baseline_path = _run(tmp_path, [
        {"First Name": "Brand", "Last Name": "New", "Email": "brand.new@example.com"},
    ], dry_run=True)

    assert result["dry_run"] is True
    written = json.loads(baseline_path.read_text())
    assert len(written) == len(_baseline())  # unchanged
    assert not (tmp_path / "_snapshots").exists()


def test_real_write_snapshots_and_writes_report(tmp_path, monkeypatch):
    """RB-HUBINGEST-008: exercises the real write path, entirely against tmp_path."""
    snapshots_dir = tmp_path / "_snapshots"
    deltas_dir = tmp_path / "deltas"
    monkeypatch.setattr(core, "SNAPSHOTS_DIR", snapshots_dir)
    monkeypatch.setattr(hi, "DELTAS_DIR", deltas_dir)
    monkeypatch.setattr(hi, "LATEST_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(core, "CACHE_DIR", tmp_path / "cache")

    result, _ = _run(tmp_path, [
        {"First Name": "Brand", "Last Name": "New", "Email": "brand.new@example.com"},
    ])

    assert result["ok"] is True
    assert snapshots_dir.exists()
    assert list(snapshots_dir.glob("*pre-hubspot-ingest*"))
    assert deltas_dir.exists()
    assert list(deltas_dir.glob("hubspot_crm_export_*.md"))
    assert (tmp_path / "latest.json").exists()


def test_non_hubspot_csv_refuses_to_ingest(tmp_path, monkeypatch):
    """RB-HUBINGEST-009."""
    monkeypatch.setattr(core, "SNAPSHOTS_DIR", tmp_path / "_snapshots")
    monkeypatch.setattr(hi, "DELTAS_DIR", tmp_path / "deltas")
    monkeypatch.setattr(hi, "LATEST_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(core, "CACHE_DIR", tmp_path / "cache")

    baseline_path = tmp_path / "baseline_index.json"
    baseline_path.write_text(json.dumps(_baseline()))
    csv_path = tmp_path / "random_notes.csv"
    csv_path.write_text("Topic,Detail\nrestaurant tech,notes\n")

    result = hi.ingest(csv_path, baseline_path=baseline_path)
    assert result["ok"] is False
    assert json.loads(baseline_path.read_text()) == _baseline()


def test_dormant_relationship_resurfaced_on_update(tmp_path, monkeypatch):
    """RB-HUBINGEST-010."""
    monkeypatch.setattr(core, "SNAPSHOTS_DIR", tmp_path / "_snapshots")
    monkeypatch.setattr(hi, "DELTAS_DIR", tmp_path / "deltas")
    monkeypatch.setattr(hi, "LATEST_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(core, "CACHE_DIR", tmp_path / "cache")

    # "existing-email-match" in _baseline() has no last_touch field at all.
    result, _ = _run(tmp_path, [
        {"First Name": "Existing", "Last Name": "Emailed", "Email": "match@example.com", "Phone Number": "555-0100"},
    ])

    dormant = result["dormant_relationships_resurfaced"]
    assert len(dormant) == 1
    assert dormant[0]["id"] == "existing-email-match"
    assert dormant[0]["days_since_last_touch"] is None

    report_text = Path(hi.DELTAS_DIR, "hubspot_crm_export_2026-07-07.md").read_text()
    assert "Dormant relationships resurfaced" in report_text
    assert "Existing Emailed" in report_text


def test_warm_intro_candidate_surfaced_for_new_contact(tmp_path, monkeypatch):
    """RB-HUBINGEST-011."""
    monkeypatch.setattr(core, "SNAPSHOTS_DIR", tmp_path / "_snapshots")
    monkeypatch.setattr(hi, "DELTAS_DIR", tmp_path / "deltas")
    monkeypatch.setattr(hi, "LATEST_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(core, "CACHE_DIR", tmp_path / "cache")

    baseline = _baseline() + [{
        "id": "acme-insider", "name": "Acme Insider", "current_company": "Acme Corp",
        "current_role": "VP", "email": None, "phone": None, "sources": [],
        "signal_class": "RC", "rc_tier": "inner", "rc_state": "active",
        "circles": [], "tags": [], "notes": "", "last_touch": "2026-06-01",
    }]
    baseline_path = tmp_path / "baseline_index.json"
    baseline_path.write_text(json.dumps(baseline))
    csv_path = _write_csv(tmp_path, [
        {"First Name": "Brand", "Last Name": "New", "Email": "brand.new@example.com", "Company Name": "Acme Corp"},
    ])

    result = hi.ingest(csv_path, ingest_date="2026-07-07", baseline_path=baseline_path, threads=[])

    warm_intros = result["warm_intro_candidates"]
    assert len(warm_intros) == 1
    assert warm_intros[0]["new_contact"] == "Brand New"
    assert warm_intros[0]["broker_name"] == "Acme Insider"

    report_text = Path(hi.DELTAS_DIR, f"hubspot_crm_export_2026-07-07.md").read_text()
    assert "Warm introduction candidates" in report_text
    assert "Acme Insider" in report_text
