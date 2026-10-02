"""
test_cos_opening_loop_trend.py

Regression coverage: CoS Opening's overdue-loop callout unconditionally said
"the list is getting longer, not shorter" any time loops_overdue crossed 10
— with no actual trend check against a prior count. It kept asserting this
even on a day the count had measurably shrunk (22 -> 18 after real loop
closures), directly contradicting the progress shown a few lines away in
This Week's Plan ("4/22 loops resolved") and undermining trust in the whole
brief — exactly the kind of thing that makes "intelligence gathering and
loop closures do not seem to be working" a reasonable read, even though
loops were in fact being closed.

Fixed: _load_prior_loops_overdue() reads yesterday's rendered daily brief
for its overdue count, and the CoS Opening note now states whichever
direction (down/up/flat) the data actually supports, falling back to a
plain count (no direction claim) when no prior brief is available.
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


def _sections_with_loops_overdue(n: int) -> dict:
    return {
        "communication_intelligence": [{
            "summary": f"Loops overdue: {n}. Emails awaiting your response: 2. "
                       f"Follow-ups overdue: 1. Meetings needing prep: 0.",
        }],
        "strategic_industry_signals": [],
        "day_ahead": [],
        "calendar_intelligence": [],
    }


class TestLoadPriorLoopsOverdue(unittest.TestCase):
    def test_returns_none_when_no_prior_brief(self):
        with patch.object(rdb, "BRIEFS_DIR", Path("/nonexistent/path")):
            self.assertIsNone(rdb._load_prior_loops_overdue(date(2026, 7, 7)))

    def test_parses_count_from_prior_brief(self, ):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            briefs_dir = Path(tmpdir)
            (briefs_dir / "2026-07-06-daily-brief.md").write_text(
                "some text\n**18 overdue loops** (Dave Richards...) — review in RB\n",
                encoding="utf-8",
            )
            with patch.object(rdb, "BRIEFS_DIR", briefs_dir):
                self.assertEqual(rdb._load_prior_loops_overdue(date(2026, 7, 7)), 18)


class TestCosOpeningTrendClaim(unittest.TestCase):
    def test_shrinking_count_says_down_not_getting_longer(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            briefs_dir = Path(tmpdir)
            (briefs_dir / "2026-07-06-daily-brief.md").write_text(
                "**22 overdue loops** (...) — review in RB\n", encoding="utf-8"
            )
            with patch.object(rdb, "BRIEFS_DIR", briefs_dir):
                out = rdb._render_cos_judgment(_sections_with_loops_overdue(18), date(2026, 7, 7))
        self.assertNotIn("getting longer, not shorter", out)
        self.assertIn("down from 22 yesterday", out)

    def test_growing_count_says_up(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            briefs_dir = Path(tmpdir)
            (briefs_dir / "2026-07-06-daily-brief.md").write_text(
                "**14 overdue loops** (...) — review in RB\n", encoding="utf-8"
            )
            with patch.object(rdb, "BRIEFS_DIR", briefs_dir):
                out = rdb._render_cos_judgment(_sections_with_loops_overdue(18), date(2026, 7, 7))
        self.assertIn("up from 14 yesterday", out)

    def test_no_prior_data_makes_no_direction_claim(self):
        with patch.object(rdb, "BRIEFS_DIR", Path("/nonexistent/path")):
            out = rdb._render_cos_judgment(_sections_with_loops_overdue(18), date(2026, 7, 7))
        self.assertNotIn("getting longer, not shorter", out)
        self.assertNotIn("down from", out)
        self.assertNotIn("up from", out)
        self.assertIn("18 loops overdue", out)


if __name__ == "__main__":
    unittest.main()
