"""
test_cos_bottom_line_gp_dots_pointer.py

RB-2026-08-25: live in the 2026-08-25 Daily Brief -- CoS Bottom Line rendered
"**Stripe tips toward staying private -- Stripe is already escalated on your
watchlist -- this headline touches a situation you're already in, not
adjacent to it.** is the signal worth tracking today." A run-on with a
grammar error (lowercase "is" right after a period) where the GP/Genius Dot
Connections bullet's full reasoning got quoted back in its entirety and then
had a trailing clause blindly appended.

Root cause: _top_gp_dot_connection_line() split the dot-text on sentence
boundaries to shorten it to "one clause," but when the dot-text (already a
concise single sentence, per an earlier quality pass) has only one sentence,
there's no boundary to split on and the whole thing came back unshortened.
_top_gp_dot_connection_line() now returns only the bullet's headline; the
caller (_render_cos_bottom_line) builds a "see GP/Genius Dot Connections
above" pointer instead of quoting reasoning that's already fully rendered
earlier in the same document -- also directly implementing "every word on
the page has to earn its place" (repeating a whole paragraph is never that).
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

_ORDINARY_DAY = date(2026, 8, 25)

_GP_DOTS_MARKDOWN = (
    "## GP/Genius: Dot Connections\n\n"
    "*What today's signals mean for your Genius/Worldpay territory.*\n\n"
    "- **[Stripe tips toward staying private](https://example.com/stripe)** — "
    "Stripe is already escalated on your watchlist — this headline touches "
    "a situation you're already in, not adjacent to it.\n"
)


class TestTopGpDotConnectionLineReturnsHeadlineOnly(unittest.TestCase):
    def test_returns_only_the_headline_not_the_reasoning(self):
        line = rdb._top_gp_dot_connection_line(_GP_DOTS_MARKDOWN)
        self.assertEqual(line, "Stripe tips toward staying private")
        self.assertNotIn("already escalated", line)

    def test_empty_markdown_returns_empty_string(self):
        self.assertEqual(rdb._top_gp_dot_connection_line(""), "")

    def test_markdown_with_no_matching_bullet_returns_empty_string(self):
        self.assertEqual(rdb._top_gp_dot_connection_line("## Some Other Section\n\nplain text"), "")


class TestCosBottomLinePointsRatherThanRepeatsGpDots(unittest.TestCase):
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

    def _sections(self):
        return {
            "strategic_industry_signals": [],  # empty -- forces the GP-dots fallback path
            "decision_queue": [],
            "horizon_watch": [],
            "communication_intelligence": [{"summary": "Loops overdue: 0."}],
        }

    def test_no_grammar_break_and_no_full_reasoning_repeated(self):
        out = rdb._render_cos_bottom_line(self._sections(), _ORDINARY_DAY, gp_dots_markdown=_GP_DOTS_MARKDOWN)
        self.assertIn("Stripe tips toward staying private", out)
        self.assertIn("see GP/Genius Dot Connections above", out)
        # The exact live bug: reasoning text quoted back, then a lowercase
        # clause glued on right after its own period.
        self.assertNotIn("already in, not adjacent to it. is the signal", out)
        self.assertNotIn("already escalated on your watchlist", out)

    def test_no_gp_dots_and_no_strategic_signal_has_no_signal_sentence(self):
        out = rdb._render_cos_bottom_line(self._sections(), _ORDINARY_DAY, gp_dots_markdown="")
        self.assertNotIn("is the signal worth tracking today", out)
        self.assertNotIn("see GP/Genius Dot Connections above", out)

    def test_strategic_signal_present_still_cited_in_full_not_as_pointer(self):
        """strategic_industry_signals has no other rendered section in this
        document -- unlike the GP-dots case, stating it here in full (it's
        already just a short title, not a paragraph) is correct, not bloat."""
        sections = self._sections()
        sections["strategic_industry_signals"] = [
            {"title": "Multiple-source convergence: New PAR Technology contract signal",
             "extras": {"is_actually_stale": False}},
        ]
        out = rdb._render_cos_bottom_line(sections, _ORDINARY_DAY, gp_dots_markdown=_GP_DOTS_MARKDOWN)
        self.assertIn("New PAR Technology contract signal", out)
        self.assertIn("is the signal worth tracking today", out)
        self.assertNotIn("Stripe", out)  # GP-dots fallback never reached


if __name__ == "__main__":
    unittest.main()
