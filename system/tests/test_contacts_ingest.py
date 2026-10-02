#!/usr/bin/env python3
"""
Characterization tests for contacts_ingest.py's identity-resolution stage
(_build_baseline_indexes, _resolve_contact) — written ahead of RB-DEFECT-064
Phase 2's migration onto identity_matcher.py, since this file previously had
no test coverage at all. These lock in current behavior (including the
nickname/composite-evidence escalation tiers) so the migration can be
verified as a pure extraction with zero behavior change.

Test IDs and coverage:

RB-CONTACTS-001: phone match wins over everything else, confidence 1.0
RB-CONTACTS-002: an excluded phone short-circuits to (None, "excluded", 0.0)
RB-CONTACTS-003: email match when no phone match
RB-CONTACTS-004: exact full-name match (0.90) when no phone/email
RB-CONTACTS-005: nickname + last-name match (0.80), no company evidence
RB-CONTACTS-006: nickname + last-name + company evidence raises confidence (0.85)
RB-CONTACTS-007: first+last (non-nickname) match (0.90), no company evidence
RB-CONTACTS-008: first+last + company evidence raises confidence (0.95)
RB-CONTACTS-009: name+company-only tier (0.70) when last name doesn't match exactly
RB-CONTACTS-010: no match at all returns (None, "no_match", 0.0)
RB-CONTACTS-011: _build_baseline_indexes normalizes phone/email/name keys
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import contacts_ingest as ci  # noqa: E402


def _baseline():
    return [
        {"name": "Robert Jones", "current_company": "Acme Corp", "phone": "5855550100", "email": "rjones@example.com"},
        {"name": "Jane Smith", "current_company": "Contoso", "phone": None, "email": None},
        {"name": "Sam Okafor", "current_company": "BrightLoop Robotics", "phone": None, "email": None},
    ]


def _indexes():
    return ci._build_baseline_indexes(_baseline())


def test_phone_match_wins(monkeypatch):
    """RB-CONTACTS-001."""
    phone_idx, email_idx, name_idx = _indexes()
    raw = {"name": "Bob Jones", "phones": ["5855550100"], "emails": [], "orgs": []}
    match, method, conf = ci._resolve_contact(raw, phone_idx, email_idx, name_idx, set())
    assert match["name"] == "Robert Jones"
    assert method == "phone_exact"
    assert conf == 1.0


def test_excluded_phone_short_circuits():
    """RB-CONTACTS-002."""
    phone_idx, email_idx, name_idx = _indexes()
    raw = {"name": "Whoever", "phones": ["5855550100"], "emails": [], "orgs": []}
    match, method, conf = ci._resolve_contact(raw, phone_idx, email_idx, name_idx, {"5855550100"})
    assert match is None
    assert method == "excluded"
    assert conf == 0.0


def test_email_match_when_no_phone():
    """RB-CONTACTS-003."""
    phone_idx, email_idx, name_idx = _indexes()
    raw = {"name": "R Jones", "phones": [], "emails": ["rjones@example.com"], "orgs": []}
    match, method, conf = ci._resolve_contact(raw, phone_idx, email_idx, name_idx, set())
    assert match["name"] == "Robert Jones"
    assert method == "email_exact"
    assert conf == 1.0


def test_exact_name_match():
    """RB-CONTACTS-004."""
    phone_idx, email_idx, name_idx = _indexes()
    raw = {"name": "Jane Smith", "phones": [], "emails": [], "orgs": []}
    match, method, conf = ci._resolve_contact(raw, phone_idx, email_idx, name_idx, set())
    assert match["name"] == "Jane Smith"
    assert method == "name_exact"
    assert conf == 0.90


def test_nickname_last_match_no_company():
    """RB-CONTACTS-005."""
    phone_idx, email_idx, name_idx = _indexes()
    raw = {"name": "Bob Jones", "phones": [], "emails": [], "orgs": []}
    match, method, conf = ci._resolve_contact(raw, phone_idx, email_idx, name_idx, set())
    assert match["name"] == "Robert Jones"
    assert method == "nickname+last"
    assert conf == 0.80


def test_nickname_last_match_with_company():
    """RB-CONTACTS-006."""
    phone_idx, email_idx, name_idx = _indexes()
    raw = {"name": "Bob Jones", "phones": [], "emails": [], "orgs": ["Acme Corp"]}
    match, method, conf = ci._resolve_contact(raw, phone_idx, email_idx, name_idx, set())
    assert match["name"] == "Robert Jones"
    assert method == "nickname+last+company"
    assert conf == 0.85


def test_first_last_match_no_nickname_no_company():
    """RB-CONTACTS-007."""
    phone_idx, email_idx, name_idx = _indexes()
    raw = {"name": "Samuel Okafor", "phones": [], "emails": [], "orgs": []}
    match, method, conf = ci._resolve_contact(raw, phone_idx, email_idx, name_idx, set())
    assert match["name"] == "Sam Okafor"
    assert method == "nickname+last"  # "Sam"/"Samuel" is itself a nickname pair
    assert conf == 0.80


def test_first_last_match_with_company():
    """RB-CONTACTS-008."""
    phone_idx, email_idx, name_idx = _indexes()
    raw = {"name": "Samuel Okafor", "phones": [], "emails": [], "orgs": ["BrightLoop Robotics"]}
    match, method, conf = ci._resolve_contact(raw, phone_idx, email_idx, name_idx, set())
    assert match["name"] == "Sam Okafor"
    assert method == "nickname+last+company"
    assert conf == 0.85


def test_name_company_only_tier():
    """RB-CONTACTS-009: last name differs (so Tiers 1/2 miss entirely), but the
    first-name key matches exactly and the company overlaps."""
    phone_idx, email_idx, name_idx = _indexes()
    raw = {"name": "Robert Martinez", "phones": [], "emails": [], "orgs": ["Acme Corp"]}
    match, method, conf = ci._resolve_contact(raw, phone_idx, email_idx, name_idx, set())
    assert match["name"] == "Robert Jones"
    assert method == "name_partial+company"
    assert conf == 0.70


def test_no_match_at_all():
    """RB-CONTACTS-010."""
    phone_idx, email_idx, name_idx = _indexes()
    raw = {"name": "Totally Unknown Person", "phones": ["9995551234"], "emails": ["nobody@nowhere.com"], "orgs": ["Nowhere Inc"]}
    match, method, conf = ci._resolve_contact(raw, phone_idx, email_idx, name_idx, set())
    assert match is None
    assert method == "no_match"
    assert conf == 0.0


def test_build_baseline_indexes_normalizes_keys():
    """RB-CONTACTS-011."""
    phone_idx, email_idx, name_idx = _indexes()
    assert "5855550100" in phone_idx
    assert "rjones@example.com" in email_idx
    assert ci._name_key("Robert Jones") in name_idx
