"""
test_team_market_intelligence.py

Regression coverage for system/scripts/team_market_intelligence.py (Latest
News + Earnings Center on the Team Portal). The News allowlist test is the
core guarantee here: system/inbox/market_signals*.json items carry real
Todd-only fields (`why_this_matters_to_todd`, `recommended_action`,
`affected_relationships_or_threads` -- the last of which names Todd's own
internal account-research threads) alongside public-source fields on the
exact same record. This asserts none of those Todd-only fields, or their
values, ever reach the allowlisted output -- a positive whitelist test,
not just "the intended fields are present," same discipline as
test_team_portal.py's tech-stack whitelist tests.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import team_market_intelligence as tmi  # noqa: E402
import earnings_monitor  # noqa: E402

_TODD_PRIVATE = "TODD-PRIVATE: don't tip our hand to the account team on this one"


def _fixture_signal_item(**overrides) -> dict:
    item = {
        "title": "Fixture Co. announces new POS rollout",
        "url": "https://example.com/fixture-story",
        "source_name": "Fixture Wire",
        "source_type": "press_release",
        "source_quality": "medium",
        "published_at": "2026-09-28",
        "company": "Fixture Co.",
        "side": "operator_demand",
        "category": "pos",
        "signal_type": "unit_growth_velocity",
        "pain_point_or_priority": "Public: Fixture Co. is rolling out new POS hardware chain-wide.",
        "strategic_relevance": "high",
        "affected_relationships_or_threads": ["Fixture Co. account research", "Genius positioning"],
        "macro_force": "operator_growth",
        "restaurant_operator_impact": "Public: throughput and reliability focus.",
        "restaurant_tech_vendor_implication": "Public: vendors should emphasize uptime and rollout support.",
        "second_order_impact": _TODD_PRIVATE,
        "relationship_opportunity": _TODD_PRIVATE,
        "why_this_matters_to_todd": _TODD_PRIVATE,
        "timing_priority": "this_week",
        "recommended_action": _TODD_PRIVATE,
        "entity_search_terms": ["Fixture Co."],
    }
    item.update(overrides)
    return item


class TestLatestNewsFirewall(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        tmp = Path(self.tmpdir.name)

        self.signals_path = tmp / "market_signals.json"
        self.signals_path.write_text(json.dumps({
            "fetched_at": "2026-09-28T00:00:00Z", "source_note": "fixture",
            "items": [_fixture_signal_item()],
        }), encoding="utf-8")

        self.earnings_signals_path = tmp / "market_signals_earnings.jsonl"
        self.earnings_signals_path.write_text("", encoding="utf-8")

        self._signals_patch = patch.object(tmi, "MARKET_SIGNALS_PATH", self.signals_path)
        self._earnings_signals_patch = patch.object(tmi, "MARKET_SIGNALS_EARNINGS_PATH", self.earnings_signals_path)
        self._signals_patch.start()
        self._earnings_signals_patch.start()
        self.addCleanup(self._signals_patch.stop)
        self.addCleanup(self._earnings_signals_patch.stop)

    def test_news_item_never_contains_todd_private_content(self):
        result = tmi.get_latest_news(days=30)
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        blob = json.dumps(item)
        self.assertNotIn(_TODD_PRIVATE, blob)
        # Explicitly forbidden keys must never appear on the output object.
        for forbidden_key in (
            "why_this_matters_to_todd", "recommended_action", "timing_priority",
            "affected_relationships_or_threads", "relationship_opportunity",
            "second_order_impact", "entity_search_terms", "macro_force",
        ):
            self.assertNotIn(forbidden_key, item)
        # Public content must still come through -- whitelist, not a wipe.
        self.assertEqual(item["headline"], "Fixture Co. announces new POS rollout")
        self.assertIn("uptime", item["why_it_matters_to_gp"])

    def test_days_window_filters_out_old_items(self):
        old_item = _fixture_signal_item(published_at="2020-01-01")
        self.signals_path.write_text(json.dumps({
            "fetched_at": "2026-09-28T00:00:00Z", "source_note": "fixture", "items": [old_item],
        }), encoding="utf-8")
        result = tmi.get_latest_news(days=7)
        self.assertEqual(result["items"], [])

    def test_company_filter(self):
        result = tmi.get_latest_news(days=30, company="Nonexistent Co.")
        self.assertEqual(result["items"], [])
        result = tmi.get_latest_news(days=30, company="Fixture Co.")
        self.assertEqual(len(result["items"]), 1)

    def test_company_announcement_routed_to_press_releases_not_dropped(self):
        """Real 2026-09-29 finding: no record in either feed ever carries
        source_type "press_release" literally -- that branch was dead
        code and Press Releases was permanently empty. The real signal
        for a company's own announcement is vertical_trade_company_
        announcement."""
        announcement = _fixture_signal_item(
            source_type="vertical_trade_company_announcement",
            title="Fixture Co. launches new loyalty program",
        )
        self.signals_path.write_text(json.dumps({
            "fetched_at": "2026-09-28T00:00:00Z", "source_note": "fixture", "items": [announcement],
        }), encoding="utf-8")
        result = tmi.get_latest_news(days=30)
        self.assertIn("Press Releases", result["sections"])
        self.assertEqual(len(result["sections"]["Press Releases"]), 1)


class TestEarningsCenter(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        tmp = Path(self.tmpdir.name)

        self.calendar_path = tmp / "earnings_calendar.yaml"
        self.calendar_path.write_text(json.dumps({
            "version": 1, "updated_at": "2026-09-28",
            "companies": [
                {
                    "name": "Fixture Restaurant Co", "ticker": "FIX", "side": "operator_demand",
                    "category": "pos", "report_months": [1, 4, 7, 10],
                    "strategic_relevance": "high", "last_reported_date": "2026-09-25",
                    "notes": "TODD-PRIVATE triage note", "added_by": "Todd Vahlsing",
                },
            ],
        }), encoding="utf-8")

        self.history_path = tmp / "earnings_calls.jsonl"
        self.history_path.write_text("\n".join(json.dumps(r) for r in [
            {"company": "Fixture Restaurant Co", "event_date": "2026-09-25", "title": "8-K",
             "signal_dimensions": [], "source_url": "https://example.com/8k-index",
             "exhibit_url": "https://example.com/8k-earnings-release", "source_type": "sec_edgar_8k",
             "excerpt": "Public filing excerpt."},
            {"company": "Fixture Restaurant Co", "event_date": "2026-06-25", "title": "10-Q",
             "signal_dimensions": [], "source_url": "https://example.com/10q-index", "source_type": "sec_edgar_8k",
             "excerpt": "Public filing excerpt 2."},
        ]) + "\n", encoding="utf-8")

        self._cal_patch = patch.object(tmi, "EARNINGS_CALENDAR_PATH", self.calendar_path)
        self._hist_patch = patch.object(earnings_monitor, "EARNINGS_HISTORY_PATH", self.history_path)
        self._cal_patch.start()
        self._hist_patch.start()
        self.addCleanup(self._cal_patch.stop)
        self.addCleanup(self._hist_patch.stop)

    def test_calendar_summary_excludes_notes_and_added_by(self):
        center = tmi.get_earnings_center()
        self.assertEqual(len(center["recently_reported"]), 1)
        entry = center["recently_reported"][0]
        self.assertNotIn("notes", entry)
        self.assertNotIn("added_by", entry)
        self.assertEqual(entry["ticker"], "FIX")

    def test_company_detail_unknown_company_raises_not_found(self):
        with self.assertRaises(tmi.NotFoundError):
            tmi.get_earnings_company_detail("Does Not Exist Co")

    def test_company_detail_returns_history_and_trend(self):
        detail = tmi.get_earnings_company_detail("Fixture Restaurant Co")
        self.assertEqual(len(detail["history"]), 2)
        self.assertIn(detail["trend"]["status"], ("ok", "insufficient_history"))

    def test_history_report_url_prefers_exhibit_over_index_page(self):
        """Real 2026-09-29 feedback: the Earnings Center needs a link to
        the actual report/call summary, not the bare SEC filing-index
        page. report_url should be the exhibit (the real document) when
        one was resolved, falling back to the index page when it wasn't
        (e.g. a 10-Q with no separate earnings-release exhibit)."""
        detail = tmi.get_earnings_company_detail("Fixture Restaurant Co")
        with_exhibit = next(h for h in detail["history"] if h["event_date"] == "2026-09-25")
        without_exhibit = next(h for h in detail["history"] if h["event_date"] == "2026-06-25")
        self.assertEqual(with_exhibit["report_url"], "https://example.com/8k-earnings-release")
        self.assertEqual(without_exhibit["report_url"], "https://example.com/10q-index")
        self.assertIsNone(without_exhibit["exhibit_url"])

    def test_talking_points_generated_from_evidence_only(self):
        result = tmi.generate_account_talking_points("Fixture Restaurant Co")
        self.assertTrue(result["talking_points"])
        for point in result["talking_points"]:
            self.assertNotIn("TODD-PRIVATE", point)


class TestNewsCompaniesDropdown(unittest.TestCase):
    """Real 2026-09-29 feedback: the company filter must be a dropdown
    built from real tracked names (Global Payments must be selectable
    even with zero recent news), and must not misrepresent a name-
    adjacency-only entry like "Genius Sports" as a real restaurant-tech
    vendor."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        tmp = Path(self.tmpdir.name)

        self.calendar_path = tmp / "earnings_calendar.yaml"
        self.calendar_path.write_text(json.dumps({
            "version": 1, "updated_at": "2026-09-29",
            "companies": [
                {"name": "McDonald's", "side": "operator_demand", "strategic_relevance": "high"},
                {"name": "Global Payments", "side": "vendor_supply", "strategic_relevance": "high",
                 "watch_priority": True, "notes": "Todd's own employer's parent company."},
                {"name": "Genius Sports", "side": "vendor_supply", "strategic_relevance": "low",
                 "watch_priority": False, "notes": "Monitoring only -- adjacent payments/data analytics."},
            ],
        }), encoding="utf-8")
        self.signals_path = tmp / "market_signals.json"
        self.signals_path.write_text(json.dumps({
            "fetched_at": "2026-09-29T00:00:00Z", "source_note": "fixture", "items": [
                {"company": "One-Off Brand Not On Watchlist", "side": "operator_demand"},
            ],
        }), encoding="utf-8")
        self.earnings_signals_path = tmp / "market_signals_earnings.jsonl"
        self.earnings_signals_path.write_text("", encoding="utf-8")

        self._patches = [
            patch.object(tmi, "EARNINGS_CALENDAR_PATH", self.calendar_path),
            patch.object(tmi, "MARKET_SIGNALS_PATH", self.signals_path),
            patch.object(tmi, "MARKET_SIGNALS_EARNINGS_PATH", self.earnings_signals_path),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def test_global_payments_appears_with_zero_recent_news(self):
        companies = tmi.get_news_companies()
        self.assertIn("Global Payments", companies["restaurant_technology"])

    def test_adjacent_name_collision_entry_excluded_from_real_vendors(self):
        companies = tmi.get_news_companies()
        self.assertNotIn("Genius Sports", companies["restaurant_technology"])
        self.assertIn("Genius Sports", companies["other"])

    def test_operator_and_news_only_companies_included(self):
        companies = tmi.get_news_companies()
        self.assertIn("McDonald's", companies["restaurant_operators"])
        self.assertIn("One-Off Brand Not On Watchlist", companies["restaurant_operators"])


if __name__ == "__main__":
    unittest.main()
