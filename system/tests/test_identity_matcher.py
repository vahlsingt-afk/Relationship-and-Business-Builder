#!/usr/bin/env python3
"""
Tests for identity_matcher.py — shared baseline identity-resolution
primitives (RB-DEFECT-064 Phase 2).

Test IDs and coverage:

RB-IDMATCH-001: norm_name collapses whitespace/case but keeps spaces
RB-IDMATCH-002: name_letters_key drops spaces and punctuation
RB-IDMATCH-003: linkedin_slug extracts and lowercases the last URL path segment
RB-IDMATCH-004: normalize_phone strips a leading +1 and non-digit characters
RB-IDMATCH-005: normalize_phone rejects too-short input
RB-IDMATCH-006: build_email_index — last entry wins on a duplicate email
RB-IDMATCH-007: build_phone_index normalizes before indexing
RB-IDMATCH-008: build_linkedin_url_index keys by slug, not full URL
RB-IDMATCH-009: build_name_index is one-to-many; match_unique_name resolves
    a lone match and flags ambiguity for 2+ matches without guessing
RB-IDMATCH-010: build_name_index_single is one-to-one, last-write-wins
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import identity_matcher as im  # noqa: E402


def test_norm_name_collapses_whitespace_and_case():
    """RB-IDMATCH-001."""
    assert im.norm_name("  John   Smith ") == "john smith"
    assert im.norm_name(None) == ""


def test_name_letters_key_drops_spaces_and_punctuation():
    """RB-IDMATCH-002."""
    assert im.name_letters_key("O'Brien, Jr.") == "obrienjr"
    assert im.name_letters_key("John Smith") == "johnsmith"


def test_linkedin_slug_extracts_last_segment():
    """RB-IDMATCH-003."""
    assert im.linkedin_slug("https://www.linkedin.com/in/Jane-Doe-123/") == "jane-doe-123"
    assert im.linkedin_slug(None) is None
    assert im.linkedin_slug("") is None


def test_normalize_phone_strips_country_code_and_punctuation():
    """RB-IDMATCH-004. Matches contacts_ingest.py's original _normalize_phone
    exactly, including its quirk: a leading '+' survives the digit-only
    strip, so an 11-digit match only fires when there's no '+' (a bare
    11-digit '1XXXXXXXXXX' string, not a '+1...'-prefixed one)."""
    assert im.normalize_phone("+1-585-555-0148") == "+15855550148"
    assert im.normalize_phone("15855550148") == "5855550148"
    assert im.normalize_phone("585-555-0148") == "5855550148"


def test_normalize_phone_rejects_too_short():
    """RB-IDMATCH-005."""
    assert im.normalize_phone("12345") == ""
    assert im.normalize_phone("") == ""
    assert im.normalize_phone(None) == ""


def test_build_email_index_last_write_wins():
    """RB-IDMATCH-006."""
    baseline = [
        {"name": "First", "email": "shared@example.com"},
        {"name": "Second", "email": "SHARED@example.com"},
    ]
    index = im.build_email_index(baseline)
    assert index["shared@example.com"]["name"] == "Second"


def test_build_phone_index_normalizes():
    """RB-IDMATCH-007."""
    baseline = [{"name": "A", "phone": "585.555.0148"}]
    index = im.build_phone_index(baseline)
    assert index["5855550148"]["name"] == "A"


def test_build_linkedin_url_index_keys_by_slug():
    """RB-IDMATCH-008."""
    baseline = [{"name": "A", "linkedin_url": "https://www.linkedin.com/in/jane-doe-123"}]
    index = im.build_linkedin_url_index(baseline)
    assert index["jane-doe-123"]["name"] == "A"
    assert "https://www.linkedin.com/in/jane-doe-123" not in index


def test_name_index_ambiguity_handling():
    """RB-IDMATCH-009."""
    baseline = [
        {"name": "Dan Mason", "id": "dan-mason-1"},
        {"name": "Dan Mason", "id": "dan-mason-2"},
        {"name": "Unique Person", "id": "unique-1"},
    ]
    name_index = im.build_name_index(baseline)

    match, ambiguous = im.match_unique_name("Unique Person", name_index)
    assert match["id"] == "unique-1"
    assert ambiguous is False

    match2, ambiguous2 = im.match_unique_name("Dan Mason", name_index)
    assert match2 is None
    assert ambiguous2 is True

    match3, ambiguous3 = im.match_unique_name("Nobody Here", name_index)
    assert match3 is None
    assert ambiguous3 is False


def test_build_name_index_single_last_write_wins():
    """RB-IDMATCH-010."""
    baseline = [
        {"name": "Dan Mason", "id": "dan-mason-1"},
        {"name": "Dan Mason", "id": "dan-mason-2"},
    ]
    index = im.build_name_index_single(baseline)
    assert index["danmason"]["id"] == "dan-mason-2"
