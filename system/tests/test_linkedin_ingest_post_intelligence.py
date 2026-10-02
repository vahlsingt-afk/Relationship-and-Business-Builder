#!/usr/bin/env python3
"""RB-DEFECT-064 Phase 5: linkedin_ingest.py wired to post_ingest_intelligence.py
for the same dormancy/warm-intro Stage 6 signal hubspot_ingest.py already
surfaces -- not HubSpot-only."""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import linkedin_ingest as li  # noqa: E402


def _write_fixture_zip(path: Path, rows: list[str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "Connections.csv",
            "LinkedIn export\nGenerated for test\n"
            "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
            + "".join(rows),
        )


def _setup(monkeypatch, tmp_path, baseline):
    baseline_path = tmp_path / "baseline_index.json"
    baseline_path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(li.core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(li.core, "SNAPSHOTS_DIR", tmp_path / "_snapshots")
    monkeypatch.setattr(
        li.core, "load_baseline",
        lambda path=baseline_path: json.loads(baseline_path.read_text(encoding="utf-8")),
    )
    monkeypatch.setattr(li, "DELTAS_DIR", tmp_path / "deltas")
    monkeypatch.setattr(li, "LINKEDIN_CACHE_PATH", tmp_path / ".cache" / "linkedin_ingest_latest.json")
    return baseline_path


def test_dormant_relationship_resurfaced_on_match(monkeypatch, tmp_path):
    baseline = [{
        "id": "dormant-existing",
        "name": "Dormant Existing",
        "current_company": "Old Co",
        "current_role": "Manager",
        "linkedin_url": "https://www.linkedin.com/in/dormant-existing",
        "sources": ["linkedin_export_2026-05-01"],
        "signal_class": "LKI",
        "last_touch": None,
        "tags": [], "notes": "",
    }]
    _setup(monkeypatch, tmp_path, baseline)

    zip_path = tmp_path / "linkedin_export.zip"
    _write_fixture_zip(zip_path, [
        "Dormant,Existing,https://www.linkedin.com/in/dormant-existing,,Old Co,Manager,20 May 2026\n",
    ])

    result = li.ingest(zip_path, ingest_date="2026-07-09", dry_run=True, threads=[])

    dormant = result["delta_intelligence"]["post_ingest_intelligence"]["dormant_relationships_resurfaced"]
    assert len(dormant) == 1
    assert dormant[0]["id"] == "dormant-existing"
    assert dormant[0]["days_since_last_touch"] is None


def test_warm_intro_candidate_surfaced_for_new_connection(monkeypatch, tmp_path):
    baseline = [{
        "id": "acme-insider", "name": "Acme Insider", "current_company": "Acme Corp",
        "current_role": "VP", "linkedin_url": None, "email": None, "phone": None,
        "sources": [], "signal_class": "RC", "rc_tier": "inner", "rc_state": "active",
        "circles": [], "tags": [], "notes": "", "last_touch": "2026-06-01",
    }]
    _setup(monkeypatch, tmp_path, baseline)

    zip_path = tmp_path / "linkedin_export.zip"
    _write_fixture_zip(zip_path, [
        "Brand,New,https://www.linkedin.com/in/brand-new,,Acme Corp,Analyst,01 Jul 2026\n",
    ])

    result = li.ingest(zip_path, ingest_date="2026-07-09", dry_run=True, threads=[])

    warm_intros = result["delta_intelligence"]["post_ingest_intelligence"]["warm_intro_candidates"]
    assert len(warm_intros) == 1
    assert warm_intros[0]["new_contact"] == "Brand New"
    assert warm_intros[0]["broker_name"] == "Acme Insider"


def test_ingest_from_csv_text_also_surfaces_post_ingest_intelligence(monkeypatch, tmp_path):
    baseline = [{
        "id": "dormant-existing",
        "name": "Dormant Existing",
        "current_company": "Old Co",
        "current_role": "Manager",
        "linkedin_url": "https://www.linkedin.com/in/dormant-existing",
        "sources": ["linkedin_export_2026-05-01"],
        "signal_class": "LKI",
        "last_touch": None,
        "tags": [], "notes": "",
    }]
    _setup(monkeypatch, tmp_path, baseline)

    csv_text = (
        "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
        "Dormant,Existing,https://www.linkedin.com/in/dormant-existing,,Old Co,Manager,20 May 2026\n"
    )
    result = li.ingest_from_csv_text(csv_text, ingest_date="2026-07-09", dry_run=True, threads=[])

    dormant = result["delta_intelligence"]["post_ingest_intelligence"]["dormant_relationships_resurfaced"]
    assert len(dormant) == 1
    assert dormant[0]["id"] == "dormant-existing"
