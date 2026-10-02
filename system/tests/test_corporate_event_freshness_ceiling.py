"""
test_corporate_event_freshness_ceiling.py

Regression coverage: corporate-event headlines (M&A, funding, earnings) were
exempt from the freshness gate with no age ceiling at all — "PAR Technology
acquires TASK Group for $200M" (dated 2026-06-01, sourced from "SEC EDGAR
Recent 8-K Filings") rendered in Section D of the 2026-07-06 brief, 35 days
after the event, presented identically to same-day stories. Root cause: the
corporate-event exemption in both render_intelligence_brief.py's _is_fresh
and daily_brief.py's _is_fresh_headline had no upper bound — "delayed a few
days" and "over a month old" were treated the same.

Fixed: corporate events now get a longer window than ordinary headlines
(_CORPORATE_EVENT_MAX_AGE_DAYS = 21) instead of an unbounded one.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402
import daily_brief as db  # noqa: E402
import rb_core as core  # noqa: E402


class TestRenderIntelligenceBriefCorporateEventCeiling(unittest.TestCase):
    def test_old_corporate_event_beyond_ceiling_is_stale(self):
        today = date(2026, 7, 6)
        pub_date = (today - timedelta(days=35)).isoformat()
        self.assertFalse(rib._is_fresh(pub_date, today, is_corporate=True))

    def test_corporate_event_within_ceiling_is_fresh(self):
        today = date(2026, 7, 6)
        pub_date = (today - timedelta(days=14)).isoformat()
        self.assertTrue(rib._is_fresh(pub_date, today, is_corporate=True))

    def test_ordinary_headline_still_uses_short_window(self):
        today = date(2026, 7, 6)
        pub_date = (today - timedelta(days=14)).isoformat()
        self.assertFalse(rib._is_fresh(pub_date, today, is_corporate=False))

    def test_par_task_acquisition_dropped_from_section_d(self):
        item = {
            "title": "[\U0001f3e2 ACQUISITION] PAR Technology acquires TASK Group for $200M",
            "summary": "PAR Technology announced it will acquire TASK Group, expanding its POS footprint.",
            "extras": {
                "source_name": "SEC EDGAR Recent 8-K Filings",
                "source_url": "https://example.com/par-task",
                "pub_date": "2026-06-01",
                "signal_badge": "ACQUISITION",
                "domain": "restaurant_technology",
            },
        }
        out = rib._render_headline_section(
            [item], "D: Restaurant Technology", "restaurant_tech", date(2026, 7, 6), {})
        self.assertNotIn("TASK Group", out)


class TestDailyBriefCorporateEventCeiling(unittest.TestCase):
    def test_old_corporate_event_beyond_ceiling_excluded(self):
        today = date(2026, 7, 6)
        item = {
            "title": "Acme Corp acquires Widget Co for $200M",
            "summary": "An acquisition announcement.",
            "extras": {"pub_date": (today - timedelta(days=35)).isoformat()},
        }
        self.assertFalse(db._is_fresh_headline(item, today))

    def test_corporate_event_within_ceiling_included(self):
        today = date(2026, 7, 6)
        item = {
            "title": "Acme Corp acquires Widget Co for $200M",
            "summary": "An acquisition announcement.",
            "extras": {"pub_date": (today - timedelta(days=14)).isoformat()},
        }
        self.assertTrue(db._is_fresh_headline(item, today))


class TestFreshnessConstantsShareOneDefinition(unittest.TestCase):
    """RB-DEFECT-2026-07-20: daily_brief.py and render_intelligence_brief.py
    each independently defined their own _CORPORATE_EVENT_MAX_AGE_DAYS
    constant and _is_fresh*/_is_fresh_headline implementation of the same
    rule -- two copies already able to drift out of sync with each other,
    the same disease as the dedup-logic duplication found the same day. Both
    now source from rb_core.is_fresh_pub_date's shared constants; this test
    guards against a second copy being reintroduced."""

    def test_render_intelligence_brief_sources_from_core(self):
        self.assertEqual(rib._CORPORATE_EVENT_MAX_AGE_DAYS, core.CORPORATE_EVENT_MAX_AGE_DAYS)
        self.assertEqual(rib.FRESHNESS_GATE_DAYS, core.HEADLINE_FRESHNESS_DAYS)

    def test_both_modules_agree_on_a_boundary_case(self):
        today = date(2026, 7, 20)
        pub_date = (today - timedelta(days=core.CORPORATE_EVENT_MAX_AGE_DAYS)).isoformat()
        item = {
            "title": "Acme Corp acquires Widget Co for $200M",
            "summary": "An acquisition announcement.",
            "extras": {"pub_date": pub_date},
        }
        self.assertEqual(
            rib._is_fresh(pub_date, today, is_corporate=True),
            db._is_fresh_headline(item, today),
        )
        self.assertTrue(rib._is_fresh(pub_date, today, is_corporate=True))


if __name__ == "__main__":
    unittest.main()
