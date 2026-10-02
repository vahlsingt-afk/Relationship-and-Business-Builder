#!/usr/bin/env python3
"""
test_confidence_calibration.py — Confidence-Based Auto-Recording (2026-09-25).

score_for_source() is the one shared source-type -> confidence lookup every
automated writer in this phase reuses. Covers: known source types land in
the right band, unknown/missing source types fall back to "low" (never the
benefit of the doubt), and the bands are the exact ones already reused from
relationship_classification.py's _CONFIDENCE_BAND_TO_SCHEMA (same 0-1 scale
already populated on real ecosystem_intelligence.json relationships).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import confidence_calibration as cc  # noqa: E402


def test_primary_official_sources_are_very_high_confidence():
    for source_type in ("company_press_release", "sec_filing", "earnings_release",
                         "official_announcement", "direct_confirmation"):
        level, score = cc.score_for_source(source_type)
        assert level == "critical"
        assert score == 0.95


def test_named_trade_press_is_medium_confidence():
    level, score = cc.score_for_source("trade_press_named_source")
    assert level == "medium"
    assert score == 0.5
    assert cc.score_for_source("trade_press") == (level, score)


def test_speculation_and_unnamed_sourcing_is_low_confidence():
    for source_type in ("trade_press_unnamed_source", "analyst_rumor", "speculation"):
        level, score = cc.score_for_source(source_type)
        assert level == "low"
        assert score == 0.2


def test_indirect_signals_are_low_confidence():
    for source_type in ("job_posting", "aggregator_mention", "indirect_signal"):
        level, score = cc.score_for_source(source_type)
        assert level == "low"
        assert score == 0.2


def test_unknown_source_type_falls_back_to_low_not_a_guess():
    assert cc.score_for_source("some_source_type_never_seen_before") == ("low", 0.2)


def test_missing_source_type_falls_back_to_low():
    assert cc.score_for_source(None) == ("low", 0.2)
    assert cc.score_for_source("") == ("low", 0.2)


def test_source_type_lookup_is_case_and_whitespace_insensitive():
    assert cc.score_for_source("  Company_Press_Release  ") == cc.score_for_source("company_press_release")


def test_confidence_block_matches_real_schema_shape():
    block = cc.confidence_block("sec_filing", rationale="Q3 8-K filing.")
    assert set(block.keys()) == {"level", "score", "rationale", "review_after"}
    assert block["level"] == "critical"
    assert block["score"] == 0.95
    assert block["rationale"] == "Q3 8-K filing."
    assert block["review_after"] is None


def test_confidence_block_default_rationale_is_empty_string():
    block = cc.confidence_block("job_posting")
    assert block["rationale"] == ""


def test_bands_reused_directly_from_relationship_classification_not_reinvented():
    from relationship_classification import _CONFIDENCE_BAND_TO_SCHEMA
    assert cc.score_for_source("company_press_release") == _CONFIDENCE_BAND_TO_SCHEMA["very_high"]
    assert cc.score_for_source("job_posting") == _CONFIDENCE_BAND_TO_SCHEMA["low"]
