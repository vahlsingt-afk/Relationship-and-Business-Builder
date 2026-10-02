#!/usr/bin/env python3
"""Regression coverage for the Richard Heyman / Scooter's Coffee pattern:
a dated/operator-confirmed no_stated_current_role state must survive a later
plain Connections.csv export that still lists the stale company/title, since
Connections.csv carries no employment dates and cannot resolve the state on
its own. See CLAUDE_HANDOFF_LINKEDIN_ENDED_ROLE_CURRENT_COMPANY_CLEANUP_
2026-08-06.md, remaining-work item 3 / acceptance test 2."""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import linkedin_ingest as li  # noqa: E402


def _write_stale_connections_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "Connections.csv",
            "LinkedIn export\nGenerated for test\n"
            "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
            "Richard,Heyman,https://www.linkedin.com/in/rheyman,,"
            "Scooter's Coffee,EVP | Chief Technology & Innovation Officer,11 Sep 2024\n",
        )
        zf.writestr("messages.csv", "FROM,TO,DATE,SUBJECT,CONTENT\n")


def _richard_entry() -> dict:
    return {
        "id": "richard-heyman",
        "name": "Richard Heyman",
        "current_company": None,
        "current_role": None,
        "employment_status": "no_stated_current_role",
        "employment_status_source": "operator_confirmed",
        "employment_status_observed_at": "2026-08-06T00:00:00+00:00",
        "employment_end_date": "2025-03-01",
        "employment_date_confidence": "operator_confirmed",
        "last_known_company": "Scooter's Coffee",
        "last_known_role": "EVP | Chief Technology & Innovation Officer",
        "last_known_role_dates": "ended March 2025",
        "linkedin_url": "https://www.linkedin.com/in/rheyman",
        "sources": ["linkedin_export_2026-07-29"],
        "signal_class": "LMI",
        "last_touch": None,
        "circles": [],
        "tags": ["linkedin_no_stated_current_role"],
        "notes": "[2026-08-06] Authoritative correction from Todd Vahlsing: "
                 "Scooter's Coffee role ended March 2025, no successor listed.",
    }


def test_stale_connections_export_does_not_restore_no_stated_current_role(monkeypatch, tmp_path):
    baseline_path = tmp_path / "baseline_index.json"
    snapshot_dir = tmp_path / "_snapshots"
    deltas_dir = tmp_path / "deltas"
    cache_path = tmp_path / ".cache" / "linkedin_ingest_latest.json"
    zip_path = tmp_path / "linkedin_export.zip"
    _write_stale_connections_zip(zip_path)

    baseline = [_richard_entry()]
    baseline_path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(li.core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(li.core, "SNAPSHOTS_DIR", snapshot_dir)
    monkeypatch.setattr(li.core, "load_baseline", lambda path=baseline_path: json.loads(baseline_path.read_text(encoding="utf-8")))
    monkeypatch.setattr(li, "DELTAS_DIR", deltas_dir)
    monkeypatch.setattr(li, "LINKEDIN_CACHE_PATH", cache_path)

    result = li.ingest(zip_path, ingest_date="2026-08-10", dry_run=True)

    counts = result["headline_counts"]
    assert counts["company_changes"] == 0
    assert counts["role_changes"] == 0
    assert counts["conflicts"] == 0
    assert counts["employment_status_suppressed"] == 1
    suppressed = result["employment_status_suppressed"]
    assert len(suppressed) == 1
    assert suppressed[0]["name"] == "Richard Heyman"
    assert suppressed[0]["stale_export_company"] == "Scooter's Coffee"
    assert suppressed[0]["canonical_status"] == "no_stated_current_role"

    # Reconciliation report (handoff remaining-work item 6): the suppression
    # shows up under stale_conflict_suppressed, not dates_unavailable, and
    # is visible in the rendered markdown, not just the JSON delta.
    recon = result["employment_reconciliation"]
    assert len(recon["stale_conflict_suppressed"]) == 1
    assert recon["dates_unavailable"] == []
    assert recon["new_current_role"] == []
    assert recon["ended_no_successor"] == []
    assert "Employment Status Reconciliation" in result["summary_markdown"]
    assert "Richard Heyman" in result["summary_markdown"]


def test_active_contact_still_updates_normally(monkeypatch, tmp_path):
    """Control case: an entry without a protected employment_status keeps
    behaving exactly as before — company/role changes still apply."""
    baseline_path = tmp_path / "baseline_index.json"
    snapshot_dir = tmp_path / "_snapshots"
    deltas_dir = tmp_path / "deltas"
    cache_path = tmp_path / ".cache" / "linkedin_ingest_latest.json"
    zip_path = tmp_path / "linkedin_export.zip"
    _write_stale_connections_zip(zip_path)

    entry = _richard_entry()
    entry["current_company"] = "Old Co"
    entry["current_role"] = "Old Role"
    entry["employment_status"] = "stated_current_role"
    entry["tags"] = []
    baseline = [entry]
    baseline_path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(li.core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(li.core, "SNAPSHOTS_DIR", snapshot_dir)
    monkeypatch.setattr(li.core, "load_baseline", lambda path=baseline_path: json.loads(baseline_path.read_text(encoding="utf-8")))
    monkeypatch.setattr(li, "DELTAS_DIR", deltas_dir)
    monkeypatch.setattr(li, "LINKEDIN_CACHE_PATH", cache_path)

    result = li.ingest(zip_path, ingest_date="2026-08-10", dry_run=True)

    assert result["employment_status_suppressed"] == []
    assert result["headline_counts"]["company_changes"] == 1
    assert result["headline_counts"]["role_changes"] == 1

    # Reconciliation report: an undated Connections.csv-driven change lands
    # under dates_unavailable, not stale_conflict_suppressed.
    recon = result["employment_reconciliation"]
    assert recon["stale_conflict_suppressed"] == []
    assert len(recon["dates_unavailable"]) == 1
    assert recon["dates_unavailable"][0]["name"] == "Richard Heyman"
