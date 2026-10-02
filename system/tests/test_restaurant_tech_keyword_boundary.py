"""
test_restaurant_tech_keyword_boundary.py

Regression coverage: Section D (Restaurant Technology) let through two
generic BBC world-news stories with zero restaurant relevance — "ITV sells
media and entertainment arm to Sky for £1.6bn" and "Wegovy weight loss pill
now available in UK" — because _select_restaurant_tech_items's keyword
check did naive bare substring matching. "ai" matched anywhere inside
"entertainment" (ent-ER-TAI-nment) and "available" (av-AI-lable); "pos"
would similarly match "purpose", "exposed", etc. Fixed by requiring a
word-boundary match, and extracted into a standalone function so this is
testable without exercising the full render() pipeline.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402


def _item(title: str, domain: str = "world_national") -> dict:
    return {
        "title": title,
        "extras": {"source_url": "https://example.com/x", "domain": domain},
    }


class TestRestaurantTechKeywordBoundary(unittest.TestCase):
    def test_ai_substring_false_positives_excluded(self):
        sections = {
            "what_todd_doesnt_know_yet": [
                _item("ITV sells media and entertainment arm to Sky for £1.6bn"),
                _item("Wegovy weight loss pill now available in UK - here's what you need to know"),
            ],
        }
        items = rib._select_restaurant_tech_items(sections)
        self.assertEqual(items, [])

    def test_real_restaurant_tech_keywords_still_match(self):
        sections = {
            "what_todd_doesnt_know_yet": [
                _item("Toast launches new AI ordering kiosk for restaurants"),
                _item("PAR Technology unveils new POS integration for QSR operators"),
                _item("Restaurant Brands adopts Grubhub for delivery"),
            ],
        }
        items = rib._select_restaurant_tech_items(sections)
        self.assertEqual(len(items), 3)

    def test_domain_classified_item_included_regardless_of_title(self):
        sections = {
            "restaurant_technology_headlines": [
                _item("Some headline with no matching keyword at all", domain="restaurant_technology"),
            ],
        }
        items = rib._select_restaurant_tech_items(sections)
        self.assertEqual(len(items), 1)

    def test_junk_tracking_url_excluded(self):
        item = _item("Toast AI ordering kiosk update")
        item["extras"]["source_url"] = "https://mail.google.com/mail/click1.foo"
        sections = {"what_todd_doesnt_know_yet": [item]}
        items = rib._select_restaurant_tech_items(sections)
        self.assertEqual(items, [])

    def test_watchlist_status_item_excluded_even_when_entity_name_matches_keyword(self):
        """RB-DEFECT-2026-07-24: "Serve Robotics — Relevant Activity" (a
        zero-content watchlist status item, just an earnings-call-date
        press release) leaked into Section D purely because the watched
        entity's own NAME contains a listed tech keyword ("robotics") --
        duplicating a signal already shown in Section F: Watchlist.
        watchlist_status is the reliable signature of a watchlist-sourced
        item and must be excluded from this catch-all outright."""
        item = _item("Serve Robotics — Relevant Activity")
        item["extras"]["watchlist_status"] = "Relevant Activity"
        item["extras"]["entity_name"] = "Serve Robotics"
        sections = {"what_todd_doesnt_know_yet": [item]}
        items = rib._select_restaurant_tech_items(sections)
        self.assertEqual(items, [])


class TestRestaurantTechGeneralSignalTypeGate(unittest.TestCase):
    """RB-DEFECT-2026-07-08: "Jersey Mike's rapid growth in 4 charts" (a pure
    sales/growth financial story, systemwide sales CAGR data) rendered in
    Section D purely because trusting restaurant_technology_headlines
    section membership at face value (the earlier keyword-boundary fix
    above) stopped keyword-checking it entirely -- the upstream feed tags
    everything from that source as domain=restaurant_technology regardless
    of whether the content is actually about technology. Only the
    lowest-confidence "general" signal_type bucket needs a content check;
    real events (acquisition/funding/customer_win/deployment, evidenced by a
    signal_badge) are trusted as before."""

    def _general_item(self, title: str) -> dict:
        return {
            "title": title,
            "extras": {
                "source_url": "https://example.com/x",
                "domain": "restaurant_technology",
                "signal_type": "general",
            },
        }

    def test_general_signal_type_with_no_tech_content_excluded(self):
        sections = {"restaurant_technology_headlines": [
            self._general_item("Jersey Mike's rapid growth in 4 charts"),
        ]}
        items = rib._select_restaurant_tech_items(sections)
        self.assertEqual(items, [])

    def test_general_signal_type_with_real_tech_content_still_included(self):
        sections = {"restaurant_technology_headlines": [
            self._general_item("ezCater Helps Restaurants Capture Workplace Catering Demand Through a More Connected Food Platform"),
        ]}
        items = rib._select_restaurant_tech_items(sections)
        self.assertEqual(len(items), 1)

    def test_non_general_signal_type_trusted_without_keyword_check(self):
        item = {
            "title": "Jersey Mike's files for IPO",
            "extras": {
                "source_url": "https://example.com/y",
                "domain": "restaurant_technology",
                "signal_type": "funding_round",
                "signal_badge": "[💰 FUNDING]",
            },
        }
        sections = {"restaurant_technology_headlines": [item]}
        items = rib._select_restaurant_tech_items(sections)
        self.assertEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
