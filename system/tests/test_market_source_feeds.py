#!/usr/bin/env python3
"""Tests for market_source_feeds.py — RB-9.11 sprint.

Test IDs:
- RB-MARKET-FEEDER-FIXTURE-001: fixture rows emit normalized market_signals rows
- RB-MARKET-FEEDER-HEALTH-001: source health gaps are reported, not silently replaced
- RB-MARKET-FEEDER-SHAPE-001: output shape maps to market_signals.normalize_item()
- RB-MARKET-FEEDER-DRAGONTAIL-001: Pizza Hut/Dragontail fixture row is present + classified
- RB-MARKET-FEEDER-WATCHLIST-001: watchlist-priority sources are prioritized
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import market_source_feeds as msf  # noqa: E402
import ecosystem_intelligence as eco  # noqa: E402


REQUIRED_FIELDS = {
    "title", "url", "source_name", "source_type", "published_at",
    "company", "side", "category", "signal_type",
    "pain_point_or_priority", "strategic_relevance", "confidence",
}


def test_fixture_rows_emit_normalized_market_signals_rows():
    result = msf.run(use_fixtures=True, fetch_live=False, save_output=False)
    rows = result["rows"]
    assert rows, "Expected fixture rows; got none"
    for row in rows:
        missing = REQUIRED_FIELDS - set(row.keys())
        assert not missing, f"Row missing required fields: {missing}"
        assert row["strategic_relevance"] in ("high", "medium", "low", "none")
        assert row["confidence"] in ("high", "medium", "low")
        assert row["side"] in ("vendor_supply", "operator_demand")


def test_fixture_dragontail_row_present_and_classified():
    result = msf.run(use_fixtures=True, fetch_live=False, save_output=False)
    rows = result["rows"]
    dragontail_rows = [
        r for r in rows
        if "dragontail" in r.get("title", "").lower() or "pizza hut" in r.get("title", "").lower()
    ]
    assert dragontail_rows, "Expected at least one Pizza Hut/Dragontail fixture row"
    row = dragontail_rows[0]
    assert row["category"] == "restaurant_ai"
    assert row["signal_type"] == "lawsuit"
    assert row["strategic_relevance"] == "high"
    combined = row["title"] + " " + row["pain_point_or_priority"]
    ai_keywords = {"ai", "automation", "franchisee", "operator", "trust", "implementation"}
    assert any(kw in combined.lower() for kw in ai_keywords)


def test_failed_live_sources_reported_as_health_gaps_not_synthesized(tmp_path):
    bad_source = {
        "slug": "test_bad_source",
        "name": "Test Bad Source",
        "source_type": "vertical_trade",
        "side": "operator_demand",
        "rss_url": "http://localhost:19999/nonexistent.rss",
        "category_hints": ["restaurant_ai"],
        "watchlist_priority": False,
    }
    health = msf.SourceHealth()
    rows = msf.fetch_source(bad_source, health)
    assert rows == [], "Failed source must return empty rows"
    assert health.results
    failure = health.results[0]
    assert failure["status"] in ("failed", "filtered_empty")
    assert failure["fixture_used"] is False


def test_refresh_market_sources_returns_standard_envelope(tmp_path):
    result = msf.run(
        use_fixtures=True, fetch_live=False, save_health=True, save_output=True,
        health_path=tmp_path / "health.json", output_path=tmp_path / "feed.jsonl",
    )
    assert (tmp_path / "health.json").exists()
    health_data = json.loads((tmp_path / "health.json").read_text())
    assert "sources" in health_data and "generated_at" in health_data
    assert (tmp_path / "feed.jsonl").exists()
    lines = (tmp_path / "feed.jsonl").read_text().splitlines()
    assert lines
    first = json.loads(lines[0])
    assert REQUIRED_FIELDS.issubset(set(first.keys()))


def test_all_fixture_rows_have_valid_source_type():
    valid_types = {
        "vertical_trade", "mainstream", "public_company_primary",
        "company_blog", "linkedin_social", "event_signal",
        "legal_business_news", "enterprise_restaurant_technology",
    }
    result = msf.run(use_fixtures=True, fetch_live=False)
    for row in result["rows"]:
        assert row["source_type"] in valid_types, f"Invalid source_type: {row['source_type']}"


def test_watchlist_priority_sources_are_flagged():
    watchlist_slugs = {s["slug"] for s in msf.SOURCES if s.get("watchlist_priority")}
    assert "restaurant_dive" in watchlist_slugs
    assert "restaurant_business" in watchlist_slugs
    assert "nations_restaurant_news" in watchlist_slugs
    assert "qsr_magazine" in watchlist_slugs
    assert "hospitality_technology" in watchlist_slugs


def test_global_payments_fixture_row_classified_high_relevance():
    result = msf.run(use_fixtures=True, fetch_live=False)
    gp_rows = [
        r for r in result["rows"]
        if "global payments" in r.get("company", "").lower()
        or "genius" in r.get("company", "").lower()
    ]
    assert gp_rows, "Expected Global Payments / Genius fixture row"
    assert gp_rows[0]["strategic_relevance"] == "high"


def test_expanded_operator_and_provider_trigger_vocabulary():
    cases = [
        (
            "Applebee's closes 25 underperforming restaurant locations",
            "location_closure",
            "unit_growth",
        ),
        (
            "Wingstop opens 40 new restaurants under a development agreement",
            "location_opening",
            "unit_growth",
        ),
        (
            "Dine Brands acquires regional franchise operator",
            "merger_acquisition",
            "m_and_a",
        ),
        (
            "Restaurant group announces strategic investment from private equity firm",
            "investment_announcement",
            "m_and_a",
        ),
        (
            "Toast selected by QSR operator for enterprise POS deployment",
            "provider_win",
            "pos",
        ),
        (
            "Olo updates ordering platform with new loyalty integration module",
            "product_update",
            "loyalty",
        ),
        (
            "Global Payments names new CFO ahead of earnings call",
            "c_suite_change",
            "leadership",
        ),
        (
            "Starbucks reports weak traffic and same-store sales in quarterly earnings",
            "earnings_signal",
            "unit_growth",
        ),
    ]
    for text, signal_type, category in cases:
        assert msf._classify_signal_type(text) == signal_type
        assert msf._classify_category(text, []) == category


def test_contract_renewal_and_expansion_classified():
    cases = [
        "PAR Technology renews its agreement with a national QSR chain",
        "Olo expands its rollout to 400 additional locations",
        "Toast extends its partnership with a regional pizza brand for another three years",
    ]
    for text in cases:
        assert msf._classify_signal_type(text) == "contract_renewal_expansion", text


def test_vendor_churn_loss_classified():
    cases = [
        "Restaurant chain switches away from its legacy POS provider",
        "Operator replaces incumbent vendor with a new AI platform",
        "QSR brand ends its relationship with a longtime payments provider",
    ]
    for text in cases:
        assert msf._classify_signal_type(text) == "vendor_churn_loss", text


def test_bare_sales_drop_not_misclassified_as_churn():
    result = msf._classify_signal_type("Company reports same-store sales drop in fourth quarter results")
    assert result != "vendor_churn_loss"


def test_named_vs_unnamed_provider_win_distinguished_via_company_field():
    named = msf._extract_company("Chipotle selects Toast for enterprise POS deployment")
    unnamed = msf._extract_company("A major QSR operator selects a new POS provider")
    assert "Toast" in named.split(" / ")
    assert unnamed == ""


def test_ai_application_classification():
    cases = [
        ("Wendy's rolls out voice AI ordering at the drive-thru", "voice_ai"),
        ("New computer vision system detects order accuracy issues in the kitchen", "computer_vision"),
        ("Operator deploys ai-driven upsell recommendation engine at checkout", "ai_personalization_next_best_action"),
        ("Chain announces autonomous delivery robots for last-mile orders", "robotics_autonomous_systems"),
    ]
    for text, expected in cases:
        assert msf._classify_ai_application(text) == expected, text
    assert msf._classify_ai_application("Restaurant chain closes 12 underperforming locations") is None


def test_deployment_stage_hint_contracted_vs_live():
    contracted = msf._classify_deployment_stage_hint(
        "Wingstop signs an agreement with Toast and plans to roll out the platform next year"
    )
    live = msf._classify_deployment_stage_hint(
        "Wingstop's Toast deployment is now live across all corporate locations"
    )
    neither = msf._classify_deployment_stage_hint("Wingstop opens 40 new restaurants this quarter")
    assert contracted == "contracted_deployment_pending"
    assert live == "active_rollout"
    assert neither is None
    assert contracted in eco.WORKBOOK_DEPLOYMENT_STATUS_VALUES
    assert live in eco.WORKBOOK_DEPLOYMENT_STATUS_VALUES


def test_ai_application_field_present_on_built_rows():
    source = {
        "slug": "test_source", "name": "Test Source", "source_type": "vertical_trade",
        "side": "operator_demand", "category_hints": ["restaurant_ai"],
    }
    row = msf._build_row(
        "Wendy's rolls out voice AI ordering at the drive-thru",
        "https://example.com/1", "2026-07-31", "", source,
    )
    assert row["ai_application"] == "voice_ai"

    no_ai_row = msf._build_row(
        "Restaurant chain closes 12 underperforming locations",
        "https://example.com/2", "2026-07-31", "", source,
    )
    assert no_ai_row["ai_application"] is None
