#!/usr/bin/env python3
"""Regression tests for RB-MULTI-SOURCE-SIGNAL-CONVERGENCE-001."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import strategic_events as se  # noqa: E402


PIZZA_NEWS = {
    "title": "Pizza Hut franchisee lawsuit tied to Dragontail AI rollout",
    "source_name": "Restaurant trade news",
    "source_type": "vertical_trade",
    "published_at": "2026-05-25",
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
    "company": "Smooth Commerce / Pizza Hut / Yum Brands / Dragontail / Chaac Pizza Northeast",
    "text": (
        "Brian Deck, Chair & CEO at Smooth Commerce, says the Pizza Hut / Yum "
        "Brands / Dragontail lawsuit with Chaac Pizza Northeast shows AI must "
        "survive Friday night operations and franchisee trust."
    ),
    "strategic_relevance": "high",
    "confidence": "high",
}

STARBUCKS = {
    "title": "Starbucks dumps AI-powered inventory counting tool due to errors",
    "source_name": "PYMNTS",
    "source_type": "press_pickup",
    "published_at": "2026-05-22",
    "company": "Starbucks",
    "category": "restaurant_ai",
    "signal_type": "pilot_rollback",
    "pain_point_or_priority": (
        "AI inventory counting errors create operational trust and adoption risk."
    ),
    "strategic_relevance": "medium",
    "confidence": "medium",
}


def test_pizza_hut_dragontail_sources_converge_into_one_event(tmp_path):
    path = tmp_path / "strategic_events.json"

    result = se.ingest([PIZZA_NEWS, PIZZA_LINKEDIN], confirm=True, store_path=path)

    assert result["persistence_status"] == "persisted"
    assert result["proof_stats"]["captured"] == 2
    assert result["proof_stats"]["recorded"] == 1
    assert result["proof_stats"]["updated"] == 1
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
    report = se.build_report(
        market_report={"all_ranked": [PIZZA_NEWS, STARBUCKS]},
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
    assert themes["operational_ai_trust"]["trend_status"] == "validated_market_trend"
    assert set(themes["operational_ai_trust"]["source_channels"]) >= {
        "linkedin_social",
        "restaurant_trade_news",
        "legal_business_news",
    }


def test_duplicate_evidence_is_not_double_counted(tmp_path):
    path = tmp_path / "strategic_events.json"

    first = se.ingest([PIZZA_NEWS], confirm=True, store_path=path)
    second = se.ingest([PIZZA_NEWS], confirm=True, store_path=path)

    assert first["proof_stats"]["recorded"] == 1
    assert second["proof_stats"]["deduped"] == 1
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert len(stored["events"]) == 1
    assert len(stored["events"][0]["evidence"]) == 1
