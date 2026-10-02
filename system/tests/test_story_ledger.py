"""
test_story_ledger.py

RB-DEFECT-2026-07-20 Phase 2: StoryLedger replaces three previously-separate
dedup registries (render_intelligence_brief.py's rendered_headlines.json
corporate branch + module-level _rendered_this_run/_rendered_subjects_this_run,
and daily_brief.py's watchlist_new_activity_seen.json) with one entity+event-
keyed ledger. A story, once told, is suppressed for STORY_RETIREMENT_DAYS
regardless of how many new URLs/outlets cover it afterward -- not just
within a rolling per-URL grace window.

Pure unit tests, independent of rendering (per the approved plan).
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import rb_core as core  # noqa: E402


def _key(entity="wonder", event="funding_round", disambiguator=("$650m",)):
    return (entity, event, frozenset(disambiguator))


class TestStoryLedgerBasics(unittest.TestCase):
    def test_brand_new_story_renders(self):
        ledger = core.StoryLedger()
        key = _key()
        self.assertTrue(ledger.should_render(key, date(2026, 7, 17)))

    def test_record_creates_story_and_url_index_entry(self):
        ledger = core.StoryLedger()
        key = _key()
        story_id = ledger.record(key, "https://a.com/1", "Wonder raises $650M", date(2026, 7, 17))
        self.assertIn(story_id, ledger.stories)
        self.assertEqual(ledger.url_index["https://a.com/1"], story_id)
        story = ledger.url_to_story("https://a.com/1")
        self.assertEqual(story["first_seen"], "2026-07-17")
        self.assertEqual(story["render_count"], 1)

    def test_same_story_different_url_is_not_a_new_development(self):
        ledger = core.StoryLedger()
        key = _key()
        ledger.record(key, "https://a.com/1", "Wonder raises $650M", date(2026, 7, 17))
        # A different outlet's coverage is corroboration, not a new story.
        self.assertFalse(ledger.should_render(key, date(2026, 7, 19)))

    def test_same_story_different_url_past_active_window_suppressed(self):
        ledger = core.StoryLedger()
        key = _key()
        ledger.record(key, "https://a.com/1", "Wonder raises $650M", date(2026, 7, 17))
        # The story remains suppressed within STORY_RETIREMENT_DAYS (12).
        self.assertFalse(ledger.should_render(key, date(2026, 7, 23)))

    def test_new_url_for_already_tracked_story_maps_to_same_story_id(self):
        ledger = core.StoryLedger()
        key = _key()
        story_id_1 = ledger.record(key, "https://a.com/1", "Wonder raises $650M", date(2026, 7, 17))
        # Even though should_render() would say False here, record() must
        # still be called by the caller to keep the ledger's evidence
        # up to date -- confirm it maps the new URL to the SAME story.
        story_id_2 = ledger.record(key, "https://b.com/2", "Wonder tops $9B, raises $650M", date(2026, 7, 20))
        self.assertEqual(story_id_1, story_id_2)
        self.assertEqual(ledger.url_index["https://b.com/2"], story_id_1)
        story = ledger.stories[story_id_1]
        self.assertEqual(story["first_seen"], "2026-07-17")  # not reset
        self.assertIn("https://a.com/1", story["urls"])
        self.assertIn("https://b.com/2", story["urls"])
        self.assertEqual(story["render_count"], 2)

    def test_story_fully_retired_treated_as_forgotten_and_renders_again(self):
        ledger = core.StoryLedger()
        key = _key()
        ledger.record(key, "https://a.com/1", "Wonder raises $650M", date(2026, 7, 1))
        # STORY_RETIREMENT_DAYS default is 12 -- day 15 is past it
        self.assertTrue(ledger.should_render(key, date(2026, 7, 16)))

    def test_recording_after_retirement_starts_fresh_cycle_not_backdated(self):
        ledger = core.StoryLedger()
        key = _key()
        old_story_id = ledger.record(key, "https://a.com/1", "Wonder raises $650M", date(2026, 7, 1))
        new_story_id = ledger.record(key, "https://c.com/3", "Wonder raises $650M again", date(2026, 7, 16))
        self.assertNotEqual(old_story_id, new_story_id)
        new_story = ledger.stories[new_story_id]
        self.assertEqual(new_story["first_seen"], "2026-07-16")
        # the old story record is preserved (audit trail), not deleted
        self.assertIn(old_story_id, ledger.stories)


class TestStoryLedgerIdentityMatching(unittest.TestCase):
    def test_different_entities_never_merge(self):
        ledger = core.StoryLedger()
        ledger.record(_key(entity="wonder"), "https://a.com/1", "Wonder raises $650M", date(2026, 7, 17))
        self.assertTrue(ledger.should_render(_key(entity="gotab", event="acquisition", disambiguator=("fishbowl",)), date(2026, 7, 17)))

    def test_different_disambiguator_no_overlap_never_merges(self):
        """$600M and $650M rounds for the same company must stay distinct."""
        ledger = core.StoryLedger()
        ledger.record(_key(disambiguator=("$600m",)), "https://a.com/1", "Wonder $600M round", date(2026, 7, 10))
        self.assertTrue(ledger.should_render(_key(disambiguator=("$650m",)), date(2026, 7, 17)))

    def test_overlapping_disambiguator_merges_via_loose_match(self):
        """An extra detail one outlet includes ("series d") that another
        omits must still resolve to the same story via token overlap."""
        ledger = core.StoryLedger()
        key_a = _key(disambiguator=("$650m", "$9b"))
        story_id_a = ledger.record(key_a, "https://a.com/1", "Wonder tops $9B, raises $650M", date(2026, 7, 17))
        key_b = _key(disambiguator=("$650m", "$9b", "series d"))
        found = ledger.lookup(key_b)
        self.assertIsNotNone(found)
        self.assertEqual(found[0], story_id_a)

    def test_url_to_story_returns_none_for_unknown_url(self):
        ledger = core.StoryLedger()
        self.assertIsNone(ledger.url_to_story("https://never-seen.com/x"))


class TestStoryLedgerSerialization(unittest.TestCase):
    def test_round_trips_through_to_dict(self):
        ledger = core.StoryLedger()
        key = _key()
        ledger.record(key, "https://a.com/1", "Wonder raises $650M", date(2026, 7, 17))
        rebuilt = core.StoryLedger(ledger.to_dict())
        self.assertEqual(rebuilt.stories, ledger.stories)
        self.assertEqual(rebuilt.url_index, ledger.url_index)
        self.assertIsNotNone(rebuilt.url_to_story("https://a.com/1"))

    def test_empty_ledger_constructs_cleanly(self):
        ledger = core.StoryLedger({})
        self.assertEqual(ledger.stories, {})
        self.assertEqual(ledger.url_index, {})
        self.assertIsNone(ledger.url_to_story("https://x.com"))


class TestStoryLedgerMergeInto(unittest.TestCase):
    """RB-DEFECT-2026-07-20 Phase 3: merge_into() lets a caller record a url
    against an already-identified story_id found by some means other than
    resolve_story_identity() -- specifically, an LLM tie-break confirming an
    otherwise-unresolvable title describes the same event as an existing
    story."""

    def test_merge_into_existing_story_adds_url_and_bumps_render_count(self):
        ledger = core.StoryLedger()
        key = _key()
        story_id = ledger.record(key, "https://a.com/1", "Wonder raises $650M", date(2026, 7, 17))
        ledger.merge_into(story_id, "https://b.com/2", date(2026, 7, 18))
        story = ledger.stories[story_id]
        self.assertIn("https://b.com/2", story["urls"])
        self.assertEqual(story["render_count"], 2)
        self.assertEqual(story["last_seen"], "2026-07-18")
        self.assertEqual(ledger.url_index["https://b.com/2"], story_id)

    def test_merge_into_unknown_story_id_is_a_no_op(self):
        ledger = core.StoryLedger()
        ledger.merge_into("nonexistent", "https://b.com/2", date(2026, 7, 18))
        self.assertEqual(ledger.stories, {})
        self.assertNotIn("https://b.com/2", ledger.url_index)


class TestFindTiebreakCandidates(unittest.TestCase):
    def test_shared_significant_word_surfaces_as_candidate(self):
        ledger = core.StoryLedger()
        key = _key(entity="wonder", event="acquisition", disambiguator=("mighty", "quinn"))
        ledger.record(key, "https://a.com/1", "Wonder acquires Mighty Quinn's BBQ", date(2026, 7, 10))
        candidates = core.find_tiebreak_candidates(
            "The chicken wars heat up, and Wonder's Mighty Quinn's deal closes",
            ledger, date(2026, 7, 12),
        )
        self.assertEqual(len(candidates), 1)

    def test_no_shared_word_yields_no_candidates(self):
        ledger = core.StoryLedger()
        key = _key(entity="wonder", event="acquisition", disambiguator=("mighty", "quinn"))
        ledger.record(key, "https://a.com/1", "Wonder acquires Mighty Quinn's BBQ", date(2026, 7, 10))
        candidates = core.find_tiebreak_candidates(
            "Completely unrelated headline about a different topic entirely",
            ledger, date(2026, 7, 12),
        )
        self.assertEqual(candidates, [])

    def test_retired_story_excluded_from_candidates(self):
        ledger = core.StoryLedger()
        key = _key(entity="wonder", event="acquisition", disambiguator=("mighty", "quinn"))
        ledger.record(key, "https://a.com/1", "Wonder acquires Mighty Quinn's BBQ", date(2026, 7, 1))
        candidates = core.find_tiebreak_candidates(
            "Wonder's Mighty Quinn's deal, revisited",
            ledger, date(2026, 7, 20),  # 19 days later, past STORY_RETIREMENT_DAYS (12)
        )
        self.assertEqual(candidates, [])

    def test_empty_title_yields_no_candidates(self):
        ledger = core.StoryLedger()
        self.assertEqual(core.find_tiebreak_candidates("", ledger, date(2026, 7, 12)), [])


if __name__ == "__main__":
    unittest.main()
