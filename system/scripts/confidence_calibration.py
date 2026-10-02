#!/usr/bin/env python3
"""
confidence_calibration.py — source-type → confidence calibration
(2026-09-25, Confidence-Based Auto-Recording).

One shared lookup so every automated writer (tech_stack_relationship_
promotion.py, ownership_promotion.py, executive_move_promotion.py, the
earnings/press bridge in earnings_monitor.py, and ecosystem_intelligence.py's
own conflict engine) derives a numeric confidence the same way, instead of
each guessing its own band. Reuses relationship_classification.py's own
_CONFIDENCE_BAND_TO_SCHEMA table directly (same low/low_medium/medium/
medium_high/high/very_high bands, same 0-1 score scale already populated on
381 of 419 real relationships in ecosystem_intelligence.json) rather than
inventing a second, potentially-inconsistent scale.

Todd's explicit direction (2026-09-25): additive, sourced intelligence
should record itself automatically, weighted by how strong the source is --
a company's own announcement outweighs named trade press, which outweighs
an unnamed rumor or an indirect signal like a job posting. "Fuzzy is okay"
-- this calibration is deliberately a source-TYPE lookup, not a claim to
have assessed any individual article's actual reliability.
"""
from __future__ import annotations

# Confidence band -> (schema level, numeric 0-1 score). This is the ONE
# canonical table for both this module and relationship_classification.py
# (which imports CONFIDENCE_BAND_TO_SCHEMA from here, not the other way
# around -- it originally lived there, but relationship_classification.py
# itself imports ecosystem_intelligence.py, and ecosystem_intelligence.py's
# own conflict engine needs this table too (Phase 2), which would have been
# a circular import. This module has zero dependencies on either, by
# design, so anything can import it safely.
CONFIDENCE_BAND_TO_SCHEMA: dict[str, tuple[str, float]] = {
    "low": ("low", 0.2),
    "low_medium": ("low", 0.4),
    "medium": ("medium", 0.5),
    "medium_high": ("medium", 0.7),
    "high": ("high", 0.85),
    "very_high": ("critical", 0.95),
}

# Source-type vocabulary -> confidence band (looked up in CONFIDENCE_BAND_TO_SCHEMA).
# Deliberately a small, explicit, hand-reviewed table -- add a new source_type
# here only after checking it actually belongs in this tier, not casually.
_SOURCE_TYPE_BANDS: dict[str, str] = {
    # Primary/official: the company's own words, or a regulatory filing.
    "company_press_release": "very_high",
    "sec_filing": "very_high",
    "earnings_release": "very_high",
    "official_announcement": "very_high",
    "direct_confirmation": "very_high",
    # Named trade press citing an official announcement or on-the-record source.
    "trade_press_named_source": "medium",
    "trade_press": "medium",
    # tech_stack_relationship_promotion.py's own vocabulary (2026-09-25):
    # "credible_trade_reporting" is its default when a mined signal's own
    # source has no more specific source_type -- treated the same as plain
    # trade press. "operator_context" is a real line from Todd's own
    # account_intelligence/*.md notes -- his own firsthand observation, not
    # speculation, so it sits at "high" rather than "medium".
    "credible_trade_reporting": "medium",
    "operator_context": "high",
    # Speculation, unnamed sourcing, analyst rumor.
    "trade_press_unnamed_source": "low",
    "analyst_rumor": "low",
    "speculation": "low",
    # Indirect signal -- a job posting, a LinkedIn mention, an aggregator
    # summarizing something with no primary link.
    "job_posting": "low",
    "aggregator_mention": "low",
    "indirect_signal": "low",
}

DEFAULT_BAND = "low"


def score_for_source(source_type: str | None) -> tuple[str, float]:
    """(level, score) for a source_type string. Unknown/missing source_type
    falls back to DEFAULT_BAND ("low") -- an unrecognized or unstated source
    never gets the benefit of the doubt."""
    band = _SOURCE_TYPE_BANDS.get((source_type or "").strip().lower(), DEFAULT_BAND)
    return CONFIDENCE_BAND_TO_SCHEMA[band]


def confidence_block(source_type: str | None, *, rationale: str = "") -> dict:
    """A ready-to-use confidence dict matching ecosystem_intelligence.json's
    real schema shape ({level, score, rationale, review_after}) -- callers
    that build a full relationship dict can use this directly."""
    level, score = score_for_source(source_type)
    return {"level": level, "score": score, "rationale": rationale, "review_after": None}


# ---------------------------------------------------------------------------
# Shared "is this new claim strong enough to overwrite what's on file?" rule.
# Originally lived only in ecosystem_intelligence.py's check_relationship_
# conflict() (Phase 2); moved here so ownership_promotion.py/executive_move_
# promotion.py (Phase 6, the two queues that can actually overwrite an
# existing value) apply the exact same margin/threshold instead of each
# re-deriving their own. ecosystem_intelligence.py keeps its own
# _SUPERSEDE_SCORE_MARGIN/_HIGH_CONFIDENCE_THRESHOLD names as aliases onto
# these so existing call sites (migrate_conflicting_relationships.py) don't
# need to change.
# ---------------------------------------------------------------------------
SUPERSEDE_SCORE_MARGIN = 0.15
# 0.9, not the "high" band's 0.85 -- many real relationships already sit
# exactly at 0.85 (confirmed live: a 0.95 official-confirmation claim vs an
# existing 0.85 claim must still supersede even though 0.95-0.85=0.10 misses
# the margin above; 0.9 is squarely in "very_high"/critical territory, well
# past plain "high", so this only fires for genuinely top-tier confidence).
HIGH_CONFIDENCE_THRESHOLD = 0.9


def should_supersede(new_score: float, existing_score: float) -> bool:
    """True when a new claim's confidence score is strong enough to
    overwrite/supersede an existing one at existing_score -- either a
    meaningful margin above it, or itself top-tier while the existing
    value isn't."""
    return new_score >= existing_score + SUPERSEDE_SCORE_MARGIN or (
        new_score >= HIGH_CONFIDENCE_THRESHOLD and existing_score < HIGH_CONFIDENCE_THRESHOLD
    )
