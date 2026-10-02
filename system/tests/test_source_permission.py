#!/usr/bin/env python3
"""
Tests for source_permission.py — Metadata-First Connector permission profile
(RB Phase 1).

Test IDs and coverage:

RB-SRCPERM-001: absent source defaults to metadata_only, read toggles on, content toggles off
RB-SRCPERM-002: explicit content_access + toggles are respected
RB-SRCPERM-003: unknown content_access value falls back to metadata_only
RB-SRCPERM-004: validator flags metadata_only + content-touching toggle=true
RB-SRCPERM-005: validator passes a consistent persistent-access entry
RB-SRCPERM-006: compliance precondition soft-check — no employers dir → not met, not blocking
RB-SRCPERM-007: compliance precondition soft-check — active profile.yaml → met, not blocking
RB-SRCPERM-008: gate_elevation — unconfirmed elevation above metadata_only is not allowed
RB-SRCPERM-009: gate_elevation — confirmed elevation is allowed even without an EGL profile
RB-SRCPERM-010: gate_elevation — metadata_only never requires confirmation
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import source_permission as sp  # noqa: E402


def test_absent_source_defaults(monkeypatch):
    """RB-SRCPERM-001."""
    resolved = sp.load_source_permission(
        "__nonexistent__", settings={"capture_sources": {"sources": []}}
    )
    assert resolved.content_access == "metadata_only"
    for toggle in sp.READ_TOGGLES:
        assert resolved.permission_profile[toggle] is True
    for toggle in sp.CONTENT_TOGGLES:
        assert resolved.permission_profile[toggle] is False


def test_explicit_declaration_respected():
    """RB-SRCPERM-002."""
    settings = {
        "capture_sources": {
            "sources": [
                {
                    "id": "outlook_work",
                    "content_access": "persistent",
                    "permission_profile": {"automatic_body_reading": True},
                }
            ]
        }
    }
    resolved = sp.load_source_permission("outlook_work", settings=settings)
    assert resolved.content_access == "persistent"
    assert resolved.permission_profile["automatic_body_reading"] is True
    # Untouched toggles keep their safe default.
    assert resolved.permission_profile["long_term_content_storage"] is False


def test_unknown_content_access_falls_back():
    """RB-SRCPERM-003."""
    settings = {
        "capture_sources": {
            "sources": [{"id": "weird_source", "content_access": "full_access"}]
        }
    }
    resolved = sp.load_source_permission("weird_source", settings=settings)
    assert resolved.content_access == "metadata_only"


def test_validator_flags_inconsistent_metadata_only():
    """RB-SRCPERM-004."""
    bad_entry = {
        "id": "bad_source",
        "content_access": "metadata_only",
        "permission_profile": {"automatic_body_reading": True},
    }
    errors = sp.validate_source_permission(bad_entry)
    assert errors, "expected validation errors for metadata_only + automatic_body_reading=true"


def test_validator_passes_consistent_persistent_entry():
    """RB-SRCPERM-005."""
    good_entry = {
        "id": "good_source",
        "content_access": "persistent",
        "permission_profile": {"automatic_body_reading": True, "long_term_content_storage": True},
    }
    assert sp.validate_source_permission(good_entry) == []


def test_compliance_precondition_no_employers_dir(monkeypatch, tmp_path):
    """RB-SRCPERM-006: real repo state today has no system/employers/ directory."""
    monkeypatch.setattr(sp, "EMPLOYERS_DIR", tmp_path / "employers")
    result = sp.check_compliance_precondition()
    assert result.precondition_met is False
    assert result.blocking is False


def test_compliance_precondition_active_profile(monkeypatch, tmp_path):
    """RB-SRCPERM-007."""
    employers_dir = tmp_path / "employers"
    employer_dir = employers_dir / "acme-corp"
    employer_dir.mkdir(parents=True)
    (employer_dir / "profile.yaml").write_text("employer_id: acme-corp\nstatus: active\n")
    monkeypatch.setattr(sp, "EMPLOYERS_DIR", employers_dir)

    result = sp.check_compliance_precondition()
    assert result.precondition_met is True
    assert result.blocking is False

    result_by_id = sp.check_compliance_precondition(employer_id="acme-corp")
    assert result_by_id.precondition_met is True
    assert result_by_id.blocking is False


def test_gate_elevation_unconfirmed_not_allowed():
    """RB-SRCPERM-008."""
    result = sp.gate_elevation("outlook_work", "persistent", confirmed=False)
    assert result.allowed is False


def test_gate_elevation_confirmed_allowed_without_egl(monkeypatch, tmp_path):
    """RB-SRCPERM-009: elevation must not hard-block on a missing EGL profile."""
    monkeypatch.setattr(sp, "EMPLOYERS_DIR", tmp_path / "employers")
    result = sp.gate_elevation("outlook_work", "persistent", confirmed=True)
    assert result.allowed is True
    assert result.warnings  # surfaces the missing-EGL-profile warning, but doesn't block


def test_gate_elevation_metadata_only_no_confirmation_needed():
    """RB-SRCPERM-010."""
    result = sp.gate_elevation("outlook_work", "metadata_only", confirmed=False)
    assert result.allowed is True
    assert result.requires_confirmation is False
