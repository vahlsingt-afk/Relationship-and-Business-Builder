"""
test_migrate_to_story_ledger.py

RB-DEFECT-2026-07-20 Phase 2d (shadow-run validation): replay() rebuilds a
StoryLedger from the existing URL-keyed rendered_headlines.json registry.
The real-data run (2026-07-20) confirmed the target outcome: all 9 "Wonder"
registry entries collapse into exactly 3 distinct stories (an earlier $600M
pre-IPO round, a Mighty Quinn's BBQ acquisition, and a later $650M Series D
round) -- not 9 fragmented entries, and not incorrectly merged into 1.
These tests cover the same shape with a small synthetic registry so the
behavior is pinned down independent of the live cache file's contents.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import migrate_to_story_ledger as migrate  # noqa: E402


def _entry(title: str, first_rendered: str, is_corporate: bool = True) -> dict:
    return {"title": title, "first_rendered": first_rendered, "last_rendered": first_rendered,
            "is_corporate": is_corporate, "render_count": 1}


class TestReplay(unittest.TestCase):
    def test_fragmented_urls_for_same_event_collapse_to_one_story(self):
        registry = {
            "https://a.com/1": _entry(
                "[💰 FUNDING] Wonder is valued at more than $9B after latest fundraise", "2026-07-17"),
            "https://b.com/2": _entry(
                "[💰 FUNDING] Wonder raises $650M in Series D funding at $9B valuation", "2026-07-17"),
            "https://c.com/3": _entry(
                "[💰 FUNDING] Wonder Raises $650 Million at $9 Billion Valuation", "2026-07-18"),
        }
        ledger, unresolved = migrate.replay(registry)
        self.assertEqual(unresolved, [])
        wonder_stories = [s for s in ledger.stories.values() if s["entity_key"] == "wonder"]
        self.assertEqual(len(wonder_stories), 1)
        self.assertEqual(wonder_stories[0]["render_count"], 3)

    def test_genuinely_different_events_stay_separate(self):
        registry = {
            "https://a.com/1": _entry("[💰 FUNDING] Wonder plans another $600M funding round", "2026-07-10"),
            "https://b.com/2": _entry("[🏢 ACQUISITION] Wonder acquires Mighty Quinn's BBQ", "2026-07-10"),
            "https://c.com/3": _entry("[💰 FUNDING] Wonder tops $9B valuation, raises $650M", "2026-07-17"),
        }
        ledger, unresolved = migrate.replay(registry)
        self.assertEqual(unresolved, [])
        wonder_stories = [s for s in ledger.stories.values() if s["entity_key"] == "wonder"]
        self.assertEqual(len(wonder_stories), 3)

    def test_unresolvable_title_reported_not_silently_dropped(self):
        registry = {
            "https://a.com/1": _entry(
                "[💰 FUNDING] The chicken wars heat up, value menu overload, and Wonder's IPO plans",
                "2026-07-14"),
        }
        ledger, unresolved = migrate.replay(registry)
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(len(ledger.stories), 0)

    def test_non_corporate_entries_excluded_from_replay(self):
        registry = {
            "https://a.com/1": _entry("Diners are trading down or trading up", "2026-07-10", is_corporate=False),
        }
        ledger, unresolved = migrate.replay(registry)
        self.assertEqual(ledger.stories, {})
        self.assertEqual(unresolved, [])

    def test_replay_orders_by_first_rendered_not_dict_order(self):
        """A story's first_seen must reflect the earliest coverage even if
        dict iteration order (insertion order) puts a later entry first."""
        registry = {
            "https://later.com/2": _entry("[🏢 ACQUISITION] Acme Corp acquires Widget Co", "2026-07-15"),
            "https://earlier.com/1": _entry("[🏢 ACQUISITION] Acme Corp acquires Widget Co", "2026-07-10"),
        }
        ledger, _ = migrate.replay(registry)
        stories = list(ledger.stories.values())
        self.assertEqual(len(stories), 1)
        self.assertEqual(stories[0]["first_seen"], "2026-07-10")


if __name__ == "__main__":
    unittest.main()
