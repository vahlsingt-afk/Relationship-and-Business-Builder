#!/usr/bin/env python3
"""
Tests for mutation_report.py — canonical ingest mutation-report format
(RB-DEFECT-064 Phase 3).

Test IDs and coverage:

RB-MUTREPORT-001: all canonical fields render in the header block
RB-MUTREPORT-002: None fields render as "not computed", not a fake zero
RB-MUTREPORT-003: float confidence renders as a percentage
RB-MUTREPORT-004: string confidence label renders as-is
RB-MUTREPORT-005: knowledge_sources_updated defaults to 1
RB-MUTREPORT-006: not_computed_reasons bullets are appended after the block
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import mutation_report as mr  # noqa: E402


def _report(**overrides):
    defaults = dict(
        source_label="HubSpot CRM Export", date="2026-07-07", people_imported=5,
        existing_people_updated=2, new_people_created=2, knowledge_mutations_applied=4,
        confidence=0.987,
    )
    defaults.update(overrides)
    return mr.MutationReport(**defaults)


def test_all_canonical_fields_present():
    """RB-MUTREPORT-001."""
    md = _report(duplicate_candidates=1, companies_added=3, relationship_links_created=6).render_markdown()
    for line in (
        "Knowledge Sources Updated: 1",
        "People Imported: 5",
        "Existing People Updated: 2",
        "New People Created: 2",
        "Duplicate Candidates: 1",
        "Companies Added: 3",
        "Relationship Links Created: 6",
        "Knowledge Mutations Applied: 4",
    ):
        assert line in md, f"missing {line!r}"


def test_none_fields_render_as_not_computed():
    """RB-MUTREPORT-002."""
    md = _report().render_markdown()
    assert "Duplicate Candidates: not computed" in md
    assert "Companies Added: not computed" in md
    assert "Relationship Links Created: not computed" in md


def test_float_confidence_renders_as_percentage():
    """RB-MUTREPORT-003."""
    md = _report(confidence=0.5).render_markdown()
    assert "Confidence: 50.0%" in md


def test_string_confidence_renders_as_is():
    """RB-MUTREPORT-004."""
    md = _report(confidence="high").render_markdown()
    assert "Confidence: high" in md


def test_knowledge_sources_updated_defaults_to_one():
    """RB-MUTREPORT-005."""
    report = _report()
    assert report.knowledge_sources_updated == 1


def test_not_computed_reasons_appended():
    """RB-MUTREPORT-006."""
    md = _report(not_computed_reasons=["Relationship-graph stage is Phase 4+."]).render_markdown()
    assert "Relationship-graph stage is Phase 4+." in md
