"""
test_story_tiebreak_wiring.py

RB-DEFECT-2026-07-20 Phase 3: for a title resolve_story_identity() couldn't
confidently key (e.g. a roundup headline with no clean leading subject),
_fmt_headline checks whether an LLM confirms it describes the same event as
an already-tracked story before falling back to the pre-Phase-2 anchor-noun
logic unchanged. llm_assist.same_story_tiebreak is mocked throughout --
these tests cover the wiring/control-flow, not the model call itself (see
test_llm_assist.py for that).
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402
import rb_core as core  # noqa: E402

# A title with no clean leading proper noun (starts with a stopword, so
# resolve_story_identity() can't derive an entity_key) but that still
# contains an ACQUISITION-justifying keyword ("acquires") so its badge
# survives _badge_is_justified and is_corp stays True -- same shape as the
# real "chicken wars" roundup example, just badge-preserving for this test.
_UNRESOLVABLE_TITLE = "[🏢 ACQUISITION] The buzz grows as Wonder acquires Mighty Quinn's BBQ"


def _seed_candidate_story(first_seen: date) -> str:
    key = core.resolve_story_identity("[🏢 ACQUISITION] Wonder acquires Mighty Quinn's BBQ")
    return rib._story_ledger.record(
        key, "https://a.com/original", "Wonder acquires Mighty Quinn's BBQ", first_seen)


class TestStoryTiebreakWiring(unittest.TestCase):
    def setUp(self):
        rib._rendered_this_run = set()
        rib._rendered_subjects_this_run = set()
        rib._rendered_story_ids_this_run = set()
        rib._story_ledger = core.StoryLedger()

    def _item(self, url="https://b.com/new", pub_date="2026-07-11"):
        return {
            "title": _UNRESOLVABLE_TITLE,
            "why_it_matters": "Coverage of the deal.",
            "extras": {
                "source_url": url, "source_name": "Some Outlet",
                "pub_date": pub_date, "signal_badge": "[🏢 ACQUISITION]",
            },
        }

    def test_llm_confirmed_match_within_display_window_merges_and_renders(self):
        story_id = _seed_candidate_story(date(2026, 7, 10))
        with patch.object(rib.llm_assist, "same_story_tiebreak", return_value=True) as mock_tb:
            out = rib._fmt_headline(self._item(), date(2026, 7, 11), "restaurant_industry", {})
        self.assertIsNotNone(out)
        mock_tb.assert_called_once()
        story = rib._story_ledger.stories[story_id]
        self.assertIn("https://b.com/new", story["urls"])
        self.assertEqual(story["render_count"], 2)

    def test_llm_confirmed_match_past_display_window_suppressed(self):
        # first_seen (07-01) to today (07-09) = 8 days -- past the display
        # window (corporate_grace, 1 day everywhere since RB-2026-09-25) but
        # still within the 12-day retirement window, so
        # find_tiebreak_candidates can still find it.
        _seed_candidate_story(date(2026, 7, 1))
        with patch.object(rib.llm_assist, "same_story_tiebreak", return_value=True):
            out = rib._fmt_headline(self._item(pub_date="2026-07-09"), date(2026, 7, 9), "restaurant_industry", {})
        self.assertIsNone(out)

    def test_llm_says_different_event_falls_through_to_old_path(self):
        _seed_candidate_story(date(2026, 7, 10))
        with patch.object(rib.llm_assist, "same_story_tiebreak", return_value=False) as mock_tb:
            out = rib._fmt_headline(self._item(), date(2026, 7, 11), "restaurant_industry", {})
        mock_tb.assert_called_once()
        # Falls through to the pre-Phase-2 path -- a brand-new URL with a
        # fresh pub_date renders normally as an unrelated item.
        self.assertIsNotNone(out)

    def test_llm_unavailable_none_falls_through_to_old_path_unchanged(self):
        """llm_assist returns None (no API key, no package, any failure) --
        this must be a pure no-op, not an error, and behavior must match
        exactly what it was before this Phase 3 addition existed."""
        _seed_candidate_story(date(2026, 7, 10))
        with patch.object(rib.llm_assist, "same_story_tiebreak", return_value=None) as mock_tb:
            out = rib._fmt_headline(self._item(), date(2026, 7, 11), "restaurant_industry", {})
        mock_tb.assert_called_once()
        self.assertIsNotNone(out)

    def test_no_candidates_never_calls_llm(self):
        """An unresolvable title with no textually-similar active story
        must not spend an API call at all."""
        _seed_candidate_story(date(2026, 7, 10))
        item = {
            "title": "The completely unrelated chain acquires a different rival entirely",
            "why_it_matters": "Some other event.",
            "extras": {"source_url": "https://c.com/x", "source_name": "Some Outlet",
                       "pub_date": "2026-07-11", "signal_badge": "[🏢 ACQUISITION]"},
        }
        with patch.object(rib.llm_assist, "same_story_tiebreak") as mock_tb:
            rib._fmt_headline(item, date(2026, 7, 11), "restaurant_industry", {})
        mock_tb.assert_not_called()

    def test_candidate_already_shown_this_run_suppressed_without_calling_llm(self):
        story_id = _seed_candidate_story(date(2026, 7, 10))
        rib._rendered_story_ids_this_run.add(story_id)
        with patch.object(rib.llm_assist, "same_story_tiebreak") as mock_tb:
            out = rib._fmt_headline(self._item(), date(2026, 7, 11), "restaurant_industry", {})
        self.assertIsNone(out)
        mock_tb.assert_not_called()

    def test_non_corporate_item_never_calls_llm(self):
        item = {
            "title": "The industry buzzes as something happens",
            "why_it_matters": "Just some news.",
            "extras": {"source_url": "https://d.com/x", "source_name": "Some Outlet",
                       "pub_date": "2026-07-11"},
        }
        with patch.object(rib.llm_assist, "same_story_tiebreak") as mock_tb:
            rib._fmt_headline(item, date(2026, 7, 11), "restaurant_industry", {})
        mock_tb.assert_not_called()


class TestSectionEStoryTiebreakWiring(unittest.TestCase):
    """Same LLM tie-break as C/D's fallback branch, wired into
    _render_earnings's independent dedup path."""

    def setUp(self):
        rib._rendered_this_run = set()
        rib._rendered_subjects_this_run = set()
        rib._rendered_story_ids_this_run = set()
        rib._story_ledger = core.StoryLedger()

    def _sections(self, url="https://b.com/new", pub_date="2026-07-11"):
        return {
            "watchlist_intelligence": [],
            "restaurant_industry_headlines": [{
                "title": _UNRESOLVABLE_TITLE,
                "extras": {"signal_badge": "[🏢 ACQUISITION]", "source_url": url, "pub_date": pub_date},
            }],
        }

    def test_llm_confirmed_match_within_display_window_merges_and_renders(self):
        story_id = _seed_candidate_story(date(2026, 7, 10))
        with patch.object(rib.llm_assist, "same_story_tiebreak", return_value=True):
            out = rib._render_earnings(self._sections(), date(2026, 7, 11))
        self.assertIn("Wonder", out)
        story = rib._story_ledger.stories[story_id]
        self.assertIn("https://b.com/new", story["urls"])

    def test_llm_confirmed_match_past_display_window_suppressed(self):
        _seed_candidate_story(date(2026, 7, 1))
        with patch.object(rib.llm_assist, "same_story_tiebreak", return_value=True):
            out = rib._render_earnings(
                self._sections(pub_date="2026-07-09"), date(2026, 7, 9))
        self.assertIn("No earnings reports or material corporate events this cycle.", out)

    def test_llm_unavailable_falls_through_to_old_path_unchanged(self):
        _seed_candidate_story(date(2026, 7, 10))
        with patch.object(rib.llm_assist, "same_story_tiebreak", return_value=None) as mock_tb:
            out = rib._render_earnings(self._sections(), date(2026, 7, 11))
        mock_tb.assert_called_once()
        self.assertIn("Wonder", out)

    def test_candidate_already_shown_this_run_suppressed_without_calling_llm(self):
        story_id = _seed_candidate_story(date(2026, 7, 10))
        rib._rendered_story_ids_this_run.add(story_id)
        with patch.object(rib.llm_assist, "same_story_tiebreak") as mock_tb:
            out = rib._render_earnings(self._sections(), date(2026, 7, 11))
        self.assertIn("No earnings reports or material corporate events this cycle.", out)
        mock_tb.assert_not_called()


if __name__ == "__main__":
    unittest.main()
