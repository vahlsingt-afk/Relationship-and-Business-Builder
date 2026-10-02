#!/usr/bin/env python3
"""
Regression tests confirming the settings.json capture_sources schema
extension (content_access + permission_profile, Metadata-First Connector
RB Phase 1) is additive-only and doesn't break existing source loading.

Test IDs and coverage:

RB-CAPSRC-001: every real capture_sources.sources[] entry resolves a valid content_access via source_permission
RB-CAPSRC-002: every real capture_sources.sources[] entry passes validate_source_permission with no errors
RB-CAPSRC-003: capture_ingest._load_sources() still returns the same source count/ids as before the schema extension
RB-CAPSRC-004: metadata_mode documentation block is present with the expected default
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import rb_core as core  # noqa: E402
import source_permission as sp  # noqa: E402
import capture_ingest  # noqa: E402


def _real_sources():
    settings = core.load_settings()
    return settings.get("capture_sources", {}).get("sources", [])


def test_every_source_resolves_valid_content_access():
    """RB-CAPSRC-001."""
    settings = core.load_settings()
    for entry in _real_sources():
        resolved = sp.load_source_permission(entry["id"], settings=settings)
        assert resolved.content_access in sp.CONTENT_ACCESS_LEVELS


def test_every_source_passes_validation():
    """RB-CAPSRC-002."""
    for entry in _real_sources():
        errors = sp.validate_source_permission(entry)
        assert errors == [], f"{entry.get('id')}: {errors}"


def test_load_sources_unchanged_by_schema_extension():
    """RB-CAPSRC-003: capture_ingest's own source loader (which predates this
    feature and only reads id/enabled/connector/folder/patterns) must still
    return exactly the enabled, folder-connector sources it did before the
    content_access/permission_profile fields were added, proving those
    fields are inert additions from its point of view. API connectors and
    disabled sources are excluded by _load_sources itself."""
    settings = core.load_settings()
    real_sources = settings.get("capture_sources", {}).get("sources", [])
    expected_ids = {
        s["id"] for s in real_sources
        if s.get("enabled") and s.get("connector", "folder") == "folder"
    }
    assert expected_ids == {"just_press_record", "custom", "chatgpt_intelligence_drop", "icloud_screenshots"}

    sources = capture_ingest._load_sources()
    ids = {s.get("id") for s in sources}
    assert ids == expected_ids


def test_metadata_mode_doc_block_present():
    """RB-CAPSRC-004."""
    settings = core.load_settings()
    metadata_mode = settings.get("capture_sources", {}).get("metadata_mode")
    assert metadata_mode is not None
    assert metadata_mode.get("default_content_access") == "metadata_only"
    assert metadata_mode.get("enforced_by") == "system/scripts/source_permission.py"


def test_chatgpt_drop_resolves_inside_project_independent_of_cwd():
    source = next(s for s in _real_sources() if s["id"] == "chatgpt_intelligence_drop")
    expected = core.SYSTEM_DIR / "inbox" / "chatgpt_intelligence_drop"
    assert capture_ingest._expand_folder(source["folder"]) == expected.resolve()
    assert expected.is_dir()
