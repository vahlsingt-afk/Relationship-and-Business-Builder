"""
test_cos_bottom_line_stale_signal_suppressed.py

RB-DEFECT-2026-07-27: assessed the Daily Brief for overlap with the
Intelligence Brief. CoS Bottom Line's default branch cited
strategic_industry_signals[0] unconditionally as "**{signal}** is the
signal worth tracking today" -- even when that signal is the SAME one the
Intelligence Brief's own Section I already reported, explicitly flagged as
"no new evidence" / stale. Restating a stale signal as "worth tracking
today" duplicated content already sent, with zero new information.

daily_brief.py's strategic_industry_signals computation already tags each
item with extras.is_actually_stale (recomputed live from days_since_evidence,
independent of the frozen upstream lifecycle field) -- the Bottom Line now
only cites the top signal when it's not stale.
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

# Well past the GP-milestone and Monday-countdown branches, so this hits
# the plain default signal_line path.
_ORDINARY_DAY = date(2026, 7, 27)


class TestCosBottomLineStaleSignalSuppressed(unittest.TestCase):
    def setUp(self):
        self._patches = [
            patch.object(rdb, "_load_weekly_plan", return_value=None),
            patch.object(
                rdb, "_get_market_context",
                return_value={"up_movers": [], "down_movers": [], "sector_rally": False, "rally_count": 0},
            ),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_stale_signal_not_cited_as_worth_tracking_today(self):
        sections = {
            "strategic_industry_signals": [
                {
                    "title": "Multiple-source convergence: Operational AI implementation risk in restaurants",
                    "extras": {"is_actually_stale": True},
                },
            ],
            "decision_queue": [],
            "horizon_watch": [],
            "communication_intelligence": [{"summary": "Loops overdue: 0."}],
        }
        out = rdb._render_cos_bottom_line(sections, _ORDINARY_DAY)
        self.assertNotIn("Operational AI implementation risk", out)
        self.assertNotIn("is the signal worth tracking today", out)

    def test_fresh_signal_still_cited(self):
        sections = {
            "strategic_industry_signals": [
                {
                    "title": "Multiple-source convergence: New PAR Technology contract signal",
                    "extras": {"is_actually_stale": False},
                },
            ],
            "decision_queue": [],
            "horizon_watch": [],
            "communication_intelligence": [{"summary": "Loops overdue: 0."}],
        }
        out = rdb._render_cos_bottom_line(sections, _ORDINARY_DAY)
        self.assertIn("New PAR Technology contract signal", out)
        self.assertIn("is the signal worth tracking today", out)

    def test_signal_missing_staleness_field_defaults_to_fresh(self):
        """Backward compatibility: an item with no extras/is_actually_stale
        at all (e.g. an older cache shape) must still be treated as fresh,
        not silently suppressed."""
        sections = {
            "strategic_industry_signals": [
                {"title": "Multiple-source convergence: Some signal"},
            ],
            "decision_queue": [],
            "horizon_watch": [],
            "communication_intelligence": [{"summary": "Loops overdue: 0."}],
        }
        out = rdb._render_cos_bottom_line(sections, _ORDINARY_DAY)
        self.assertIn("Some signal", out)


if __name__ == "__main__":
    unittest.main()
