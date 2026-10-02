#!/usr/bin/env python3
"""Regression coverage for career_support_view.py (RB ended-role cleanup,
2026-08-06, remaining-work item 5). Richard Heyman's pattern must appear
here — this is where a no_stated_current_role person is queryable as a
career-support candidate — while never asserting unemployment."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import career_support_view as cs_view  # noqa: E402


def _baseline() -> list[dict]:
    return [
        {
            "id": "richard-heyman", "name": "Richard Heyman",
            "current_company": None, "current_role": None,
            "employment_status": "no_stated_current_role",
            "last_known_company": "Scooter's Coffee",
            "last_known_role": "EVP | Chief Technology & Innovation Officer",
            "last_known_role_dates": "ended March 2025",
            "employment_end_date": "2025-03-01",
            "employment_status_observed_at": "2026-08-06T00:00:00+00:00",
            "tags": ["linkedin_no_stated_current_role"],
            "signal_class": "LMI", "rc_tier": None,
            "last_touch": "2024-09-16",
            "relationship_health": {"drr_score": 30.0},
            "circles": [],
        },
        {
            "id": "tyler-marpes", "name": "Tyler Marpes",
            "current_company": "Scooter's Coffee", "current_role": "VP Technology",
            "employment_status": "stated_current_role",
            "tags": [], "signal_class": "LKI", "rc_tier": None,
            "last_touch": None, "circles": [],
        },
        {
            "id": "dateless-contact", "name": "Dateless Contact",
            "current_company": "Some Co", "current_role": "Manager",
            "employment_status": "current_role_date_unavailable",
            "tags": [], "signal_class": "VC", "rc_tier": None,
            "last_touch": None, "circles": [],
        },
    ]


def test_only_no_stated_current_role_people_appear():
    rep = cs_view.build_view(_baseline(), today=date(2026, 8, 6))
    assert rep["candidate_count"] == 1
    names = [c["name"] for c in rep["candidates"]]
    assert names == ["Richard Heyman"]
    assert "Tyler Marpes" not in names
    assert "Dateless Contact" not in names


def test_preserves_last_known_fields_and_never_asserts_current():
    rep = cs_view.build_view(_baseline(), today=date(2026, 8, 6))
    richard = rep["candidates"][0]
    assert richard["last_known_company"] == "Scooter's Coffee"
    assert richard["last_known_role"] == "EVP | Chief Technology & Innovation Officer"
    assert "current_company" not in richard
    assert "current_role" not in richard


def test_markdown_never_asserts_unemployment_and_escapes_pipes():
    rep = cs_view.build_view(_baseline(), today=date(2026, 8, 6))
    md = cs_view.render_markdown(rep)
    assert "Richard Heyman" in md
    assert "no stated current role" in md.lower()
    # The word "unemployed" may only appear inside the guardrail that warns
    # against asserting it — never as a direct claim about a person.
    for line in md.splitlines():
        if "unemployed" in line.lower() or "job seek" in line.lower():
            assert "never" in line.lower() or "without" in line.lower(), line
    # The role contains a literal '|' that must be escaped, not break the table.
    assert "EVP \\| Chief Technology" in md
