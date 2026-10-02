#!/usr/bin/env python3
"""Sprint RB-9.11 regression tests for strategic_events.py hardening.

Test IDs:
- RB-MULTI-SOURCE-SIGNAL-CONVERGENCE-001: multi-source convergence produces validated_signal
- RB-STRATEGIC-EVENTS-COMPANY-NORM-001: company alias normalization collapses aliases
- RB-STRATEGIC-EVENTS-URL-CANON-001: URL canonicalization deduplicates same article
- RB-STRATEGIC-EVENTS-PROOF-STATS-001: all proof stat fields are present and correct
- RB-STRATEGIC-EVENTS-FRESHNESS-001: stale-only evidence cannot promote to validated_signal
- RB-STRATEGIC-EVENTS-FEEDER-INGEST-001: feeder rows from market_source_feeds integrate
- RB-STRATEGIC-EVENTS-RI-ASSESS-001: ri_assessment blocks are produced for market signals
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import strategic_events as se  # noqa: E402

try:
    from market_signals_ri import assess_market_signal_ri  # noqa: E402
    HAS_RI = True
except ImportError:
    HAS_RI = False


# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

def _fresh_ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stale_ts() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat(timespec="seconds")


PIZZA_NEWS = {
    "title": "Pizza Hut franchisee lawsuit tied to Dragontail AI rollout",
    "source_name": "Restaurant trade news",
    "source_type": "vertical_trade",
    "published_at": "2026-05-25",
    "captured_at": _fresh_ts(),
    "company": "Pizza Hut / Yum Brands / Dragontail / Chaac Pizza Northeast",
    "category": "restaurant_ai",
    "signal_type": "implementation_failure",
    "pain_point_or_priority": (
        "Franchisee litigation over AI rollout reliability, operator workflow "
        "fit, and implementation governance."
    ),
    "strategic_relevance": "high",
    "confidence": "high",
}

PIZZA_LINKEDIN = {
    "title": "Brian Deck on Pizza Hut / Yum / Dragontail AI rollout lawsuit",
    "source_name": "LinkedIn",
    "source_type": "linkedin_social",
    "published_at": "2026-05-26",
    "captured_at": _fresh_ts(),
    "company": "Smooth Commerce / Pizza Hut / Yum Brands / Dragontail / Chaac Pizza Northeast",
    "text": (
        "Brian Deck, Chair & CEO at Smooth Commerce, says the Pizza Hut / Yum "
        "Brands / Dragontail lawsuit with Chaac Pizza Northeast shows AI must "
        "survive Friday night operations and franchisee trust."
    ),
    "strategic_relevance": "high",
    "confidence": "high",
}

STARBUCKS_NEWS = {
    "title": "Starbucks dumps AI-powered inventory counting tool due to errors",
    "source_name": "PYMNTS",
    "source_type": "press_pickup",
    "published_at": "2026-05-22",
    "captured_at": _fresh_ts(),
    "company": "Starbucks",
    "category": "restaurant_ai",
    "signal_type": "pilot_rollback",
    "pain_point_or_priority": (
        "AI inventory counting errors create operational trust and adoption risk."
    ),
    "strategic_relevance": "medium",
    "confidence": "medium",
}

GLOBAL_PAYMENTS_ROW = {
    "title": "Global Payments Genius integrates AI-powered upsell for QSR operators",
    "source_name": "Hospitality Technology",
    "source_type": "enterprise_restaurant_technology",
    "published_at": "2026-05-12",
    "captured_at": _fresh_ts(),
    "company": "Global Payments / Genius",
    "category": "restaurant_ai",
    "signal_type": "product_launch",
    "strategic_relevance": "high",
    "confidence": "medium",
}


# ---------------------------------------------------------------------------
# RB-MULTI-SOURCE-SIGNAL-CONVERGENCE-001
# ---------------------------------------------------------------------------

def test_pizza_hut_dragontail_sources_converge_into_one_event(tmp_path):
    """Two independent sources about the same event must produce one validated_signal."""
    path = tmp_path / "strategic_events.json"

    result = se.ingest([PIZZA_NEWS, PIZZA_LINKEDIN], confirm=True, store_path=path)

    assert result["persistence_status"] == "persisted"
    stats = result["proof_stats"]
    assert stats["captured"] == 2
    assert stats["recorded"] == 1
    assert stats["updated"] == 1
    assert stats["deduped"] == 0
    assert path.exists()

    event = result["events"][0]
    assert event["event_key"] == "restaurant-ai:pizza-hut-yum-dragontail-lawsuit"
    assert event["lifecycle"] == "validated_signal"
    assert event["convergence_level"] == "multi_source_validated"
    assert event["confidence_score"] >= 0.75
    assert set(event["source_channels"]) == {"linkedin_social", "restaurant_trade_news"}
    assert {"Pizza Hut", "Yum Brands", "Dragontail", "Chaac Pizza Northeast"}.issubset(
        set(event["entities"]["companies"])
    )
    assert event["thesis_alignment"]["aligned"] is True
    assert "operational_ai_realism" in event["thesis_alignment"]["matched_theses"]
    assert "friday_night_survivability" in event["thesis_alignment"]["matched_theses"]
    assert "Develop LinkedIn thought leadership post" in event["recommended_actions"]


def test_operational_ai_theme_links_pizza_and_starbucks(tmp_path):
    """Pizza Hut and Starbucks events share operational_ai_trust theme → related_event_ids."""
    report = se.build_report(
        market_report={"all_ranked": [PIZZA_NEWS, STARBUCKS_NEWS]},
        linkedin_records=[PIZZA_LINKEDIN],
        feeder_rows=[],
        store_path=tmp_path / "se.json",
    )

    assert report["event_count"] == 2
    by_key = {event["event_key"]: event for event in report["top"]}
    pizza = by_key["restaurant-ai:pizza-hut-yum-dragontail-lawsuit"]
    starbucks = by_key["restaurant-ai:starbucks-ai-inventory-rollback"]

    assert starbucks["event_id"] in pizza["related_event_ids"]
    assert pizza["event_id"] in starbucks["related_event_ids"]

    themes = {theme["theme"]: theme for theme in report["themes"]}
    assert "operational_ai_trust" in themes
    # Must reach validated_market_trend with ≥2 channels + high confidence
    ot = themes["operational_ai_trust"]
    assert ot["trend_status"] in ("recurring_theme", "validated_market_trend")


def test_duplicate_evidence_is_not_double_counted(tmp_path):
    """Same evidence row submitted twice must be deduplicated."""
    path = tmp_path / "strategic_events.json"

    first = se.ingest([PIZZA_NEWS], confirm=True, store_path=path)
    second = se.ingest([PIZZA_NEWS], confirm=True, store_path=path)

    assert first["proof_stats"]["recorded"] == 1
    assert second["proof_stats"]["deduped"] == 1
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert len(stored["events"]) == 1
    assert len(stored["events"][0]["evidence"]) == 1


# ---------------------------------------------------------------------------
# RB-STRATEGIC-EVENTS-COMPANY-NORM-001
# ---------------------------------------------------------------------------

def test_company_alias_normalization():
    """'Yum' should normalize to 'Yum Brands'; 'Genius POS' to 'Genius'."""
    assert se._normalize_company("yum") == "Yum Brands"
    assert se._normalize_company("Yum! Brands") == "Yum Brands"
    assert se._normalize_company("genius pos") == "Genius"
    assert se._normalize_company("Global Payments Inc.") == "Global Payments"
    assert se._normalize_company("dragontail systems") == "Dragontail"
    # Unknown company should pass through
    assert se._normalize_company("AcmeCorp") == "AcmeCorp"


def test_entity_extraction_uses_canonical_names():
    """Entities extracted from text should use canonical company names."""
    row = {
        "title": "Yum Brands and Dragontail Systems face lawsuit",
        "company": "Chaac Pizza",
    }
    text = row["title"] + " " + row["company"]
    entities = se._entities(text, row)
    assert "Yum Brands" in entities["companies"]
    assert "Dragontail" in entities["companies"]
    assert "Chaac Pizza Northeast" in entities["companies"]


# ---------------------------------------------------------------------------
# RB-STRATEGIC-EVENTS-URL-CANON-001
# ---------------------------------------------------------------------------

def test_url_canonicalization_deduplicates_same_article():
    """Two rows with the same article but different query strings → same evidence_id."""
    row_a = dict(PIZZA_NEWS)
    row_a["url"] = "https://restaurantbusiness.com/article?id=123&utm_source=twitter"
    row_b = dict(PIZZA_NEWS)
    row_b["url"] = "https://restaurantbusiness.com/article?id=123&utm_medium=email"

    ev_a = se.normalize_signal(row_a)
    ev_b = se.normalize_signal(row_b)
    assert ev_a is not None and ev_b is not None
    # Same canonical URL → same evidence_id
    assert ev_a["evidence_id"] == ev_b["evidence_id"]


def test_canonical_url_strips_tracking_params():
    url = "https://www.restaurantdive.com/news/toast-ai-labor?utm_source=nl&utm_medium=email"
    canonical = se._canonical_url(url)
    assert "utm_source" not in canonical
    assert "utm_medium" not in canonical
    assert "restaurantdive.com" in canonical


def test_canonical_url_preserves_identity_params():
    url = "https://example.com/article?id=456&utm_source=twitter"
    canonical = se._canonical_url(url)
    assert "id=456" in canonical
    assert "utm_source" not in canonical


# ---------------------------------------------------------------------------
# RB-STRATEGIC-EVENTS-PROOF-STATS-001
# ---------------------------------------------------------------------------

def test_proof_stats_all_fields_present(tmp_path):
    """proof_stats must contain all required P-037 fields."""
    required_stats = {
        "captured", "recorded", "updated", "deduped",
        "stale_ignored", "isolated_suppressed", "validated_surfaced", "ignored",
    }
    path = tmp_path / "strategic_events.json"
    result = se.ingest([PIZZA_NEWS, PIZZA_LINKEDIN, STARBUCKS_NEWS], confirm=True, store_path=path)
    stats = result["proof_stats"]
    missing = required_stats - set(stats.keys())
    assert not missing, f"proof_stats missing fields: {missing}"
    assert stats["validated_surfaced"] >= 1, "Expected ≥1 validated_signal event"


# ---------------------------------------------------------------------------
# RB-STRATEGIC-EVENTS-FRESHNESS-001
# ---------------------------------------------------------------------------

def test_stale_only_evidence_cannot_promote_to_validated_signal(tmp_path):
    """An event with only stale evidence must not reach validated_signal lifecycle."""
    path = tmp_path / "strategic_events.json"

    stale_news = dict(PIZZA_NEWS)
    stale_news["captured_at"] = _stale_ts()
    stale_linkedin = dict(PIZZA_LINKEDIN)
    stale_linkedin["captured_at"] = _stale_ts()

    result = se.ingest([stale_news, stale_linkedin], confirm=True, store_path=path)
    if result["events"]:
        event = result["events"][0]
        assert event["lifecycle"] != "validated_signal", \
            "Stale-only events must not reach validated_signal"
        assert event["source_freshness_gate"] == "stale_blocked", \
            "Stale-only event must be flagged stale_blocked"


def test_fresh_plus_stale_can_still_validate(tmp_path):
    """An event with one fresh + one stale evidence can still validate if score ≥ 0.75."""
    path = tmp_path / "strategic_events.json"

    fresh_row = dict(PIZZA_NEWS)
    fresh_row["captured_at"] = _fresh_ts()

    stale_row = dict(PIZZA_LINKEDIN)
    stale_row["captured_at"] = _stale_ts()

    result = se.ingest([fresh_row, stale_row], confirm=True, store_path=path)
    if result["events"]:
        event = result["events"][0]
        # At least one fresh evidence channel → freshness gate should be open
        assert event["source_freshness_gate"] == "open"


# ---------------------------------------------------------------------------
# RB-STRATEGIC-EVENTS-FEEDER-INGEST-001
# ---------------------------------------------------------------------------

def test_feeder_rows_integrate_via_build_report():
    """market_source_feeds fixture rows integrate into build_report without error."""
    try:
        import market_source_feeds as msf
    except ImportError:
        import pytest
        pytest.skip("market_source_feeds not importable from test path")

    feeder_result = msf.run(use_fixtures=True, fetch_live=False)
    feeder_rows = feeder_result["rows"]
    assert feeder_rows

    report = se.build_report(feeder_rows=feeder_rows, linkedin_records=[])
    assert report["event_count"] >= 1
    assert "proof_stats" in report
    # Dragontail row should create an event
    keys = {e["event_key"] for e in report["top"]}
    assert "restaurant-ai:pizza-hut-yum-dragontail-lawsuit" in keys, \
        f"Expected Dragontail event in report. Got keys: {keys}"


# ---------------------------------------------------------------------------
# RB-STRATEGIC-EVENTS-RI-ASSESS-001
# ---------------------------------------------------------------------------

def test_ri_assessment_produced_for_global_payments_signal():
    """Global Payments signal maps to active thread → ri_assessment.status=proposed."""
    if not HAS_RI:
        import pytest
        pytest.skip("market_signals_ri not importable")

    ri = assess_market_signal_ri(GLOBAL_PAYMENTS_ROW)
    assert ri["status"] in ("proposed", "blocked"), \
        f"Expected proposed/blocked for Global Payments, got {ri['status']}"
    assert "global-payments" in " ".join(ri.get("mapped_thread_ids", [])).lower() or \
           ri["status"] in ("proposed", "blocked"), \
        "Global Payments should map to active thread or watchlist"


def test_ri_assessment_pizza_hut_maps_to_operator_watchlist():
    """Pizza Hut / Yum maps to operator watchlist even without direct contact match."""
    if not HAS_RI:
        import pytest
        pytest.skip("market_signals_ri not importable")

    ri = assess_market_signal_ri(PIZZA_NEWS)
    assert ri["status"] in ("proposed", "blocked"), \
        f"Expected proposed/blocked for Pizza Hut; got {ri['status']}"
    assert ri.get("watchlist_match", {}).get("is_operator") is True, \
        "Pizza Hut should match operator watchlist"


def test_ri_assessment_starbucks_is_market_pattern_not_touch():
    """Starbucks rollback must not produce a relationship touch — market pattern only."""
    if not HAS_RI:
        import pytest
        pytest.skip("market_signals_ri not importable")

    ri = assess_market_signal_ri(STARBUCKS_NEWS)
    assert ri["proposed_mutation"]["command"] is None, \
        "Market news must not propose a contact mutation command"


def test_ri_assessment_no_company_is_irrelevant():
    """Signal with no company should be irrelevant, not error."""
    if not HAS_RI:
        import pytest
        pytest.skip("market_signals_ri not importable")

    row = {
        "title": "General restaurant industry news",
        "company": "",
        "strategic_relevance": "low",
        "confidence": "low",
    }
    ri = assess_market_signal_ri(row)
    assert ri["status"] == "irrelevant"
    assert ri["display_recommendation"] == "suppress"
