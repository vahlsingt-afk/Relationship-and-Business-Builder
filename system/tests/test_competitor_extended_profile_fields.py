"""
test_competitor_extended_profile_fields.py — Ecosystem Lookup Tool,
Competitors tab (Phase B). Covers the 6 new fields added to
competitor.json's schema (products, strengths, weaknesses, vulnerabilities,
key_customers, recent_news, trends) — none of these existed before this
project (confirmed by reading all 153 real competitor.json files during
scoping: zero had any of these fields).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import competitor_intelligence_common as cic  # noqa: E402


def test_new_shell_has_all_six_fields_empty():
    shell = cic._empty_competitor_json("test-vendor", "Test Vendor", None)
    for f in cic.EXTENDED_PROFILE_FIELDS:
        assert shell[f] == []
    assert shell["trends"]["status"] == "not_yet_researched"
    assert shell["trends"]["value"] is None


def test_extended_field_shape_matches_provenance_convention():
    f = cic.extended_field("Fast integration", confidence="medium", last_reviewed_by="human:todd")
    assert set(f.keys()) >= {"value", "status", "evidence_ids", "confidence", "as_of", "scope", "last_reviewed_by"}
    assert f["scope"] == "competitor"
    assert f["value"] == "Fast integration"


def test_extended_field_includes_source_url_only_when_given():
    with_url = cic.extended_field("x", source_url="https://example.com")
    without_url = cic.extended_field("x")
    assert with_url["source_url"] == "https://example.com"
    assert "source_url" not in without_url


def test_real_existing_competitor_files_are_missing_the_new_fields(tmp_path):
    """Regression pin for the real backward-compat gap: an existing (pre-
    project) competitor.json has none of these fields. A reader must
    tolerate this -- see get_extended_profile below."""
    shell = cic._empty_competitor_json("legacy-vendor", "Legacy Vendor", None)
    for f in cic.EXTENDED_PROFILE_FIELDS + ("trends",):
        del shell[f]
    # Confirms the fixture genuinely mimics a pre-project record.
    for f in cic.EXTENDED_PROFILE_FIELDS:
        assert f not in shell


def test_get_extended_profile_backfills_missing_fields_on_legacy_record():
    legacy = cic._empty_competitor_json("legacy-vendor", "Legacy Vendor", None)
    for f in cic.EXTENDED_PROFILE_FIELDS + ("trends",):
        del legacy[f]
    filled = cic.get_extended_profile(legacy)
    for f in cic.EXTENDED_PROFILE_FIELDS:
        assert filled[f] == []
    assert filled["trends"]["status"] == "not_yet_researched"


def test_get_extended_profile_never_touches_existing_populated_fields():
    populated = cic._empty_competitor_json("olo", "Olo", "vendor-olo")
    populated["strengths"] = [cic.extended_field("Fast integration")]
    populated["positioning_summary"] = "Already has real content."
    result = cic.get_extended_profile(populated)
    assert result["strengths"] == populated["strengths"]
    assert result["positioning_summary"] == "Already has real content."


# --- shareable view (Phase C) -----------------------------------------------

def test_shareable_extended_view_excludes_weaknesses_and_vulnerabilities():
    competitor = cic._empty_competitor_json("olo", "Olo", "vendor-olo")
    competitor["weaknesses"] = [cic.extended_field("Pricing complaints")]
    competitor["vulnerabilities"] = [cic.extended_field("Key exec departed")]
    competitor["strengths"] = [cic.extended_field("Fast integration")]
    view = cic.shareable_extended_view(competitor)
    assert "weaknesses" not in view
    assert "vulnerabilities" not in view
    assert view["strengths"] == competitor["strengths"]


def test_shareable_extended_view_excludes_todds_pov_and_vs_genius():
    competitor = cic._empty_competitor_json("olo", "Olo", "vendor-olo")
    competitor["todds_pov"] = "Private strategic read."
    competitor["vs_genius"]["genius_advantages"] = [{"point": "better support"}]
    view = cic.shareable_extended_view(competitor)
    assert "todds_pov" not in view
    assert "vs_genius" not in view


def test_shareable_extended_view_backfills_a_legacy_record_first():
    """Must not KeyError on a real, pre-project competitor.json missing
    every new field."""
    legacy = cic._empty_competitor_json("legacy-vendor", "Legacy Vendor", None)
    for f in cic.EXTENDED_PROFILE_FIELDS + ("trends",):
        del legacy[f]
    view = cic.shareable_extended_view(legacy)
    assert view["strengths"] == []
    assert view["trends"]["status"] == "not_yet_researched"
