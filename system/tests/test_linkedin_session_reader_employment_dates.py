"""Employment-date regression tests for LinkedIn profile capture."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import linkedin_session_reader as reader  # noqa: E402


def test_ended_first_role_does_not_persist_as_current():
    state = reader._employment_state([
        {"title": "EVP | Chief Technology & Innovation Officer",
         "company": "Scooter's Coffee", "dates": "Jun 2021 - Mar 2025"},
    ])
    assert state["status"] == "no_stated_current_role"
    assert state["current_company"] is None
    assert state["current_role"] is None
    assert state["last_known_company"] == "Scooter's Coffee"
    assert state["last_known_dates"] == "Jun 2021 - Mar 2025"


def test_present_role_is_selected_even_when_not_first():
    state = reader._employment_state([
        {"title": "Advisor", "company": "Old Co", "dates": "2024 - 2025"},
        {"title": "VP Technology", "company": "Current Co", "dates": "Apr 2025 - Present"},
    ])
    assert state["status"] == "stated_current_role"
    assert state["current_company"] == "Current Co"
    assert state["current_role"] == "VP Technology"


def test_missing_dates_preserves_legacy_first_entry_fallback():
    state = reader._employment_state([
        {"title": "VP Technology", "company": "Acme", "dates": None},
    ])
    assert state["status"] == "current_role_date_unavailable"
    assert state["current_company"] == "Acme"


def test_profile_ingest_clears_stale_company_and_preserves_last_known(monkeypatch, tmp_path):
    baseline_path = tmp_path / "baseline_index.json"
    baseline_path.write_text(json.dumps([{
        "id": "richard-heyman",
        "name": "Richard Heyman",
        "linkedin_url": "https://www.linkedin.com/in/rheyman",
        "current_company": "Scooter's Coffee",
        "current_role": "EVP | Chief Technology & Innovation Officer",
    }]), encoding="utf-8")
    monkeypatch.setattr(reader.core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(reader.core, "load_baseline", lambda: json.loads(baseline_path.read_text()))

    result = reader.ingest_profile({
        "profile_url": "https://www.linkedin.com/in/rheyman",
        "slug": "rheyman",
        "name": "Richard Heyman",
        "experience": [{
            "title": "EVP | Chief Technology & Innovation Officer",
            "company": "Scooter's Coffee",
            "dates": "Jun 2021 - Mar 2025",
        }],
    }, dry_run=False)

    assert result["status"] == "matched_and_enriched"
    assert result["career_support_candidate"] is True
    updated = json.loads(baseline_path.read_text())[0]
    assert updated["current_company"] is None
    assert updated["current_role"] is None
    assert updated["employment_status"] == "no_stated_current_role"
    assert updated["last_known_company"] == "Scooter's Coffee"
    assert updated["last_known_role_dates"] == "Jun 2021 - Mar 2025"
    assert "linkedin_no_stated_current_role" in updated["tags"]
