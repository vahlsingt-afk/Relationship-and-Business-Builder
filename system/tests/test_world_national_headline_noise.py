"""
test_world_national_headline_noise.py

Regression coverage for two related World/National Headlines defects
observed live on 2026-07-05:

1. Sell-side analyst-note headlines ("Piper Sandler Initiates Visa Inc. (V)
   With Overweight Rating", "UBS Reaffirms Buy on CBRE Group") passed the
   positive relevance gate because they're keyword-rich in exactly the
   domains it targets (payments, fintech, banking, AI) — turning World/
   National Headlines into a sell-side research feed instead of a front
   page. _ANALYST_NOTE_PATTERN blocks these by firm-name-at-start + a
   rating-note verb, independent of the exhaustive noise phrase list.

2. Once analyst-note spam is filtered, the real cause of "why is it all
   finance news" becomes visible: BBC/NPR/Axios's small daily pool was
   getting exhausted by the global 7-day cross-day dedup window, leaving
   nothing else to fill the section for most of the week. World/national
   now uses a shorter (3-day) dedup window than restaurant/tech sections,
   since it's a daily-front-page framing, not a weekly-digest one.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402


class TestAnalystNotePattern(unittest.TestCase):
    def test_blocks_known_analyst_note_shapes(self):
        blocked_titles = [
            "Piper Sandler Initiates Visa Inc. (V) With Overweight Rating on Payments Strength",
            "BofA Highlights MercadoLibre Inc. (MELI) Credit Card Growth and Fintech Expansion",
            "UBS Reaffirms Buy on CBRE Group (CBRE) as AI and Data Center Trends Support Growth",
            "Goldman Sachs and Bernstein Assess Fiserv Inc. (FISV) Following CEO Transition",
            "Cantor Fitzgerald Highlights Remitly Global (RELY)'s Long-Term Growth in Digital Remittances",
        ]
        for title in blocked_titles:
            with self.subTest(title=title):
                self.assertIsNotNone(rib._ANALYST_NOTE_PATTERN.search(title.lower()), title)

    def test_keeps_real_world_news(self):
        kept_titles = [
            "Singapore seizes $42m mansion over Nvidia chip smuggling",
            "US blocks long-term renewal of North American trade deal",
            "Why the expected fight over the North American trade deal never kicked off",
        ]
        for title in kept_titles:
            with self.subTest(title=title):
                self.assertIsNone(rib._ANALYST_NOTE_PATTERN.search(title.lower()), title)

    def test_keeps_real_corporate_action_not_shaped_like_a_note(self):
        """A genuine acquisition headline that doesn't start with a bank/firm
        name shouldn't be caught by the analyst-note pattern (it may still be
        filtered or ranked elsewhere on relevance grounds, but not as 'noise')."""
        title = "Public Storage (PSA) Expands Into Canada With the Acquisition of Public Storage Canada"
        self.assertIsNone(rib._ANALYST_NOTE_PATTERN.search(title.lower()))


class TestWorldNationalDedupWindow(unittest.TestCase):
    def test_world_national_uses_shorter_window_than_default(self):
        self.assertLess(rib.WORLD_NATIONAL_DEDUP_WINDOW_DAYS, rib.DEDUP_WINDOW_DAYS)

    def test_item_rendered_4_days_ago_is_fresh_again_for_world_but_not_restaurant(self):
        url = "https://example.com/story"
        today = date(2026, 7, 5)
        rendered = {url: {"last_rendered": (today - timedelta(days=4)).isoformat()}}

        # World/national: 4 days ago is outside the 3-day window — not a duplicate.
        self.assertFalse(rib._is_duplicate(url, dict(rendered), today, is_corporate=False,
                                            window_days=rib.WORLD_NATIONAL_DEDUP_WINDOW_DAYS))
        # Restaurant sections: 4 days ago is still inside the default 7-day window.
        self.assertTrue(rib._is_duplicate(url, dict(rendered), today, is_corporate=False,
                                           window_days=rib.DEDUP_WINDOW_DAYS))


class TestAnalystNoteFilterAppliesToRestaurantTech(unittest.TestCase):
    """Analyst notes reach Section D too via the what_todd_doesnt_know_yet
    fallback pool, whose _TECH_KEYWORDS match is loose enough ("integration",
    "digital" alone qualify) to let a Yahoo Finance stock note through as if
    it were restaurant tech news. Observed live: "Cantor Fitzgerald Highlights
    Remitly Global (RELY)'s ... Digital Remittances" rendered in D: Restaurant
    Technology, a company with zero restaurant relevance."""

    def test_analyst_note_blocked_in_restaurant_tech_section(self):
        items = [{
            "title": "Cantor Fitzgerald Highlights Remitly Global (RELY)'s Long-Term Growth in Digital Remittances",
            "extras": {"source_url": "https://finance.yahoo.com/x", "source_name": "Yahoo Finance News",
                       "pub_date": "2026-07-04"},
        }]
        out = rib._render_headline_section(items, "D: Restaurant Technology", "restaurant_tech",
                                            date(2026, 7, 5), {})
        self.assertNotIn("Remitly", out)

    def test_real_earnings_headline_still_passes_restaurant_tech(self):
        """A self-reported earnings/product headline (not a sell-side note)
        should not be caught by the analyst-note pattern just because it
        mentions a payments company."""
        items = [{
            "title": "PayPal Holdings (PYPL) Posts Solid Q1 Results, Expands WeChat Pay Integration",
            "extras": {"source_url": "https://finance.yahoo.com/y", "source_name": "Yahoo Finance News",
                       "pub_date": "2026-07-04"},
        }]
        out = rib._render_headline_section(items, "D: Restaurant Technology", "restaurant_tech",
                                            date(2026, 7, 5), {})
        self.assertIn("PayPal", out)


class TestTickerExplainerPattern(unittest.TestCase):
    """RB-DEFECT-2026-07-12: "How Norfolk Southern (NSC) Is Navigating
    Regulatory Review to Advance Its Transformational Rail Merger Strategy"
    is a Yahoo Finance/Motley-Fool-style syndicated single-ticker explainer
    template, not a hard-news report -- it passed the relevance gate (and
    even earned an [ACQUISITION] badge, since "merger" appears in the
    title) purely because the template is keyword-rich, then rendered as a
    US domestic railroad's regulatory filing under "World Headlines.\""""

    def test_blocks_known_ticker_explainer_shape(self):
        blocked_titles = [
            "How Norfolk Southern (NSC) Is Navigating Regulatory Review to Advance Its "
            "Transformational Rail Merger Strategy",
            "How Toast Inc (TOST) Is Positioning Itself to Capture Enterprise Restaurant "
            "Demand With Its Platform Strategy",
        ]
        for title in blocked_titles:
            with self.subTest(title=title):
                self.assertIsNotNone(rib._TICKER_EXPLAINER_PATTERN.search(title), title)

    def test_keeps_real_news_even_with_a_ticker_in_parens(self):
        kept_titles = [
            "Norfolk Southern (NSC) to acquire Canadian Pacific in $30 billion deal",
            "EasyJet agrees to surprise takeover bid as rival US firm swoops in",
        ]
        for title in kept_titles:
            with self.subTest(title=title):
                self.assertIsNone(rib._TICKER_EXPLAINER_PATTERN.search(title), title)

    def test_dropped_end_to_end_in_world_section_despite_acquisition_badge(self):
        item = {
            "title": "[🏢 ACQUISITION] How Norfolk Southern (NSC) Is Navigating Regulatory Review "
                     "to Advance Its Transformational Rail Merger Strategy",
            "extras": {"source_url": "https://finance.yahoo.com/x", "source_name": "Yahoo Finance News",
                       "pub_date": "2026-07-11", "signal_badge": "[🏢 ACQUISITION]"},
        }
        out = rib._fmt_headline(item, date(2026, 7, 12), "world", {})
        self.assertIsNone(out)


class TestMortgageRateTrackerPattern(unittest.TestCase):
    """RB-DEFECT-2026-07-12: Yahoo Finance publishes this exact templated
    title daily regardless of any actual news event -- an evergreen rate
    tracker, not news. It passed the relevance gate because "interest rate"
    is a listed macro keyword and the title contains "interest rates.\""""

    def test_blocks_daily_mortgage_rate_template(self):
        self.assertIsNotNone(rib._MORTGAGE_RATE_TRACKER_PATTERN.search(
            "Mortgage and refinance interest rates today, Saturday, July 11: Rates moving lower today"
        ))

    def test_keeps_real_fed_rate_news(self):
        self.assertIsNone(rib._MORTGAGE_RATE_TRACKER_PATTERN.search(
            "Federal Reserve raises interest rates by 50 basis points, citing inflation risk"
        ))

    def test_dropped_end_to_end_in_world_section(self):
        item = {
            "title": "Mortgage and refinance interest rates today, Saturday, July 11: Rates moving lower today",
            "extras": {"source_url": "https://finance.yahoo.com/personal-finance/mortgages/article/x",
                       "source_name": "Yahoo Finance News", "pub_date": "2026-07-11"},
        }
        out = rib._fmt_headline(item, date(2026, 7, 12), "world", {})
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
