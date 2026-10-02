"""
test_cos_bottom_line_gp_milestone.py

Regression coverage: the CoS Bottom Line's GP-milestone-day branch (days_to_gp
in (0, 1) — the day before and the day of Global Payments start) was a single
hardcoded sentence with zero named signals or context: "Tomorrow is Day 1.
Nothing on today's list should be harder than getting a good night's sleep."
This directly fails DAILY_BRIEF_CANONICAL.md's own explicit anti-test: "If the
CoS Bottom Line could appear in any executive's briefing without changing a
word, the brief has failed." That sentence could open literally any new job
at any company — it never named Global Payments, never named a signal from
today's brief, never named a specific action.

Fixed: both the day-before and day-of branches now build the same
named-signal sentences every other CoS Bottom Line branch does (sector
rally / strategic signal, top decision-queue action, horizon watch item),
just framed for the milestone.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_daily_brief as rdb  # noqa: E402


def _sections_with_signal_and_action():
    return {
        "strategic_industry_signals": [
            {"title": "Multiple-source convergence: Operational AI implementation risk in restaurants"},
        ],
        "decision_queue": [
            {"title": "Prep needed: Zafar and Todd Vahlsing", "extras": {}},
        ],
        "horizon_watch": [
            {"title": "[PRE-EARNINGS] PAR Technology (PAR) reports August 08 — 32 DAYS",
             "extras": {"horizon_date": "Aug 08"}},
        ],
        "communication_intelligence": [{"summary": "Loops overdue: 3."}],
    }


class TestCosBottomLineGPMilestone(unittest.TestCase):
    def setUp(self):
        self._patch = patch.object(rdb, "_load_weekly_plan", return_value=None)
        self._patch.start()
        self._mkt_patch = patch.object(
            rdb, "_get_market_context",
            return_value={"up_movers": [], "down_movers": [], "sector_rally": False, "rally_count": 0},
        )
        self._mkt_patch.start()

    def tearDown(self):
        self._patch.stop()
        self._mkt_patch.stop()

    def test_day_before_gp_names_real_signal_and_context(self):
        out = rdb._render_cos_bottom_line(_sections_with_signal_and_action(), date(2026, 7, 6))
        self.assertIn("Tomorrow is Day 1 at Global Payments.", out)
        self.assertIn("Operational AI implementation risk in restaurants", out)
        self.assertIn("Prep needed: Zafar and Todd Vahlsing", out)
        self.assertNotIn("good night's sleep", out)

    def test_gp_start_day_itself_names_real_signal_and_context(self):
        out = rdb._render_cos_bottom_line(_sections_with_signal_and_action(), date(2026, 7, 7))
        self.assertIn("Today is Day 1 at Global Payments.", out)
        self.assertIn("Operational AI implementation risk in restaurants", out)
        self.assertIn("PAR Technology", out)

    def test_milestone_day_with_no_data_still_names_the_milestone(self):
        """Even with nothing else to say, the line must still be specific to
        Global Payments/Day 1 -- never a content-free platitude."""
        out = rdb._render_cos_bottom_line({}, date(2026, 7, 7))
        self.assertIn("Today is Day 1 at Global Payments.", out)
        self.assertIn("Confirm start logistics and walk in ready.", out)


if __name__ == "__main__":
    unittest.main()
