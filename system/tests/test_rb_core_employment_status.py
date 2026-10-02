#!/usr/bin/env python3
"""Regression coverage for the rb_core.py::_resolve_target employment_status
guard (RB ended-role cleanup, 2026-08-06). A person with
employment_status=no_stated_current_role must never resolve as a company
insider for "who do I know at X?" queries — the Richard Heyman/Scooter's
Coffee pattern — even as defense-in-depth alongside the current_company
null check. An active contact at the same company (Tyler Marpes) must still
resolve normally."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import rb_core as core  # noqa: E402


def _richard() -> dict:
    return {
        "id": "richard-heyman", "name": "Richard Heyman",
        "current_company": None, "current_role": None,
        "employment_status": "no_stated_current_role",
        "last_known_company": "Scooter's Coffee",
        "last_known_role": "EVP | Chief Technology & Innovation Officer",
        "tags": ["linkedin_no_stated_current_role"],
    }


def _tyler() -> dict:
    return {
        "id": "tyler-marpes", "name": "Tyler Marpes",
        "current_company": "Scooter's Coffee", "current_role": "VP Technology",
        "employment_status": "stated_current_role",
        "tags": [],
    }


def test_no_stated_current_role_person_never_resolves_company_alone():
    """With only Richard in the baseline, 'Scooter's Coffee' resolves as
    unknown — his current_company is already null, so this passes even
    without the guard, but pins the expected behavior."""
    resolved = core._resolve_target("Scooter's Coffee", [_richard()])
    assert resolved["type"] == "unknown"


def test_active_contact_resolves_company_even_with_stale_peer_present():
    """Tyler still resolves Scooter's Coffee as an active company match
    when Richard (no_stated_current_role) is also in the baseline."""
    resolved = core._resolve_target("Scooter's Coffee", [_richard(), _tyler()])
    assert resolved["type"] == "company"
    assert resolved["company"] == "Scooter's Coffee"


def test_repopulated_current_company_still_excluded_by_employment_status_guard():
    """Defense-in-depth: even if a future bug repopulates current_company on
    a no_stated_current_role entry without updating employment_status in
    lockstep, _resolve_target must still exclude that person. With only the
    buggy Richard record present, the company must NOT resolve."""
    richard = _richard()
    richard["current_company"] = "Scooter's Coffee"  # simulate the bug
    resolved = core._resolve_target("Scooter's Coffee", [richard])
    assert resolved["type"] == "unknown"
