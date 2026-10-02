#!/usr/bin/env python3
"""
test_hubspot_ingest_scan_manifest.py

RB-DEFECT-2026-09-18: hubspot_ingest.py's scan() had no file-hash/manifest
dedup at all -- unlike its siblings contacts_ingest.py and whatsapp_ingest.py,
which both already track processed files by content hash. The scheduled
pipeline step (hubspot_ingest_scan --scan, morning_pipeline.py, unconditional,
no --dry-run) therefore re-ingested every CSV still sitting in system/inbox/
crm_exports/ on every run. Combined with _apply_update()'s per-field null-
check guards, net-new fields became harmless no-ops on re-ingest once
already set -- but a genuinely unresolved company conflict re-appended its
CONFLICT note, unbounded, once per scheduled run, for as long as the file
remained in the inbox.

Test IDs:

RB-HUBSCAN-001: a first scan() processes a new CSV and records it in the manifest
RB-HUBSCAN-002: a second scan() with the same (unchanged) file is a no-op
RB-HUBSCAN-003: a changed file (different content, different hash) IS reprocessed
RB-HUBSCAN-004: dry_run=True never updates the manifest, so nothing is skipped later
RB-HUBSCAN-005: even bypassing the manifest (two direct ingest() calls, simulating
                a genuinely different re-export that still shows the same stale
                conflict), the conflict note itself does not duplicate
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


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        for row in rows:
            full = {h: "" for h in HEADERS}
            full.update(row)
            writer.writerow(full)


def _baseline() -> list[dict]:
    return [{
        "id": "name-only-match", "name": "Name Only Match", "current_company": "Old Co",
        "current_role": "Analyst", "email": None, "phone": None,
        "sources": ["linkedin_export_2026-01-01"], "signal_class": "LMI",
        "circles": [], "tags": [], "notes": "",
    }]


def _isolate(tmp_path: Path, monkeypatch) -> Path:
    baseline_path = tmp_path / "baseline_index.json"
    baseline_path.write_text(json.dumps(_baseline()))
    exports_dir = tmp_path / "crm_exports"
    exports_dir.mkdir()
    monkeypatch.setattr(hi, "EXPORTS_DIR", exports_dir)
    monkeypatch.setattr(hi, "MANIFEST_PATH", tmp_path / "hubspot_ingest_manifest.json")
    monkeypatch.setattr(core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(core, "SNAPSHOTS_DIR", tmp_path / "_snapshots")
    monkeypatch.setattr(hi, "DELTAS_DIR", tmp_path / "deltas")
    monkeypatch.setattr(hi, "LATEST_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(core, "CACHE_DIR", tmp_path / "cache")
    return exports_dir


def test_first_scan_processes_new_csv_and_records_manifest(tmp_path, monkeypatch):
    """RB-HUBSCAN-001."""
    exports_dir = _isolate(tmp_path, monkeypatch)
    _write_csv(exports_dir / "export1.csv", [
        {"First Name": "New", "Last Name": "Person", "Email": "new@example.com"},
    ])

    results = hi.scan(dry_run=False)

    assert len(results) == 1
    assert results[0]["ok"] is True
    manifest = json.loads(hi.MANIFEST_PATH.read_text())
    assert len(manifest["processed"]) == 1


def test_second_scan_of_same_unchanged_file_is_a_noop(tmp_path, monkeypatch):
    """RB-HUBSCAN-002: the exact confirmed-live bug -- an unresolved conflict
    must not get a duplicate note appended on every scheduled re-run of the
    same file."""
    exports_dir = _isolate(tmp_path, monkeypatch)
    _write_csv(exports_dir / "export1.csv", [
        {"First Name": "Name", "Last Name": "Only Match", "Company Name": "New Co"},
    ])

    first = hi.scan(dry_run=False)
    assert len(first) == 1
    assert len(first[0]["conflicts"]) == 1

    second = hi.scan(dry_run=False)
    assert second == []  # nothing new to process

    baseline = json.loads(core.BASELINE_PATH.read_text())
    entry = next(e for e in baseline if e["id"] == "name-only-match")
    assert entry["notes"].count("CONFLICT") == 1


def test_changed_file_content_is_reprocessed(tmp_path, monkeypatch):
    """RB-HUBSCAN-003."""
    exports_dir = _isolate(tmp_path, monkeypatch)
    csv_path = exports_dir / "export1.csv"
    _write_csv(csv_path, [
        {"First Name": "New", "Last Name": "Person", "Email": "new@example.com"},
    ])
    hi.scan(dry_run=False)

    # Same filename, different content -> different hash -> reprocessed.
    _write_csv(csv_path, [
        {"First Name": "New", "Last Name": "Person", "Email": "new@example.com"},
        {"First Name": "Another", "Last Name": "Person", "Email": "another@example.com"},
    ])
    second = hi.scan(dry_run=False)

    assert len(second) == 1
    assert second[0]["ok"] is True
    manifest = json.loads(hi.MANIFEST_PATH.read_text())
    assert len(manifest["processed"]) == 2  # both hashes recorded


def test_dry_run_never_updates_manifest(tmp_path, monkeypatch):
    """RB-HUBSCAN-004."""
    exports_dir = _isolate(tmp_path, monkeypatch)
    _write_csv(exports_dir / "export1.csv", [
        {"First Name": "New", "Last Name": "Person", "Email": "new@example.com"},
    ])

    hi.scan(dry_run=True)
    assert not hi.MANIFEST_PATH.exists()

    # A real run afterward still processes it (nothing was skipped).
    real = hi.scan(dry_run=False)
    assert len(real) == 1


def test_conflict_note_does_not_duplicate_across_separate_ingest_calls(tmp_path, monkeypatch):
    """RB-HUBSCAN-005: independent of the manifest fix -- even a genuinely
    different re-export (bypassing scan()'s dedup entirely, via two direct
    ingest() calls) that still shows the same stale company disagreement
    must not pile up identical CONFLICT notes."""
    exports_dir = _isolate(tmp_path, monkeypatch)
    csv_path = exports_dir / "export1.csv"
    _write_csv(csv_path, [
        {"First Name": "Name", "Last Name": "Only Match", "Company Name": "New Co"},
    ])

    hi.ingest(csv_path, ingest_date="2026-09-18", baseline_path=core.BASELINE_PATH, threads=[])
    hi.ingest(csv_path, ingest_date="2026-09-19", baseline_path=core.BASELINE_PATH, threads=[])

    baseline = json.loads(core.BASELINE_PATH.read_text())
    entry = next(e for e in baseline if e["id"] == "name-only-match")
    assert entry["notes"].count("CONFLICT") == 1
