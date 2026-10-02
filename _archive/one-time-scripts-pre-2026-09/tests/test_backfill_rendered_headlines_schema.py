"""
test_backfill_rendered_headlines_schema.py

RB-DEFECT-2026-07-20: the cross-day republished-URL matcher
(_corporate_story_already_covered, added 2026-07-17) only ever sees
registry entries carrying is_corporate/anchor_nouns -- fields that fix
started writing going forward but never backfilled onto existing history.
Confirmed live: 5 of 9 "Wonder" entries in rendered_headlines.json predated
the fix and had neither field, invisible to the matcher.

backfill_rendered_headlines_schema.backfill() re-derives both fields from
each entry's stored title (same logic _mark_rendered already applies to new
entries) and must be idempotent -- a second pass changes nothing.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import backfill_rendered_headlines_schema as backfill_mod  # noqa: E402


class TestBackfillRenderedHeadlinesSchema(unittest.TestCase):
    def test_missing_fields_filled_in_for_corporate_entry(self):
        registry = {
            "https://www.nrn.com/emerging-chains/wonder-acquires-mighty-quinn-s-bbq": {
                "first_rendered": "2026-07-10", "last_rendered": "2026-07-13",
                "title": "[🏢 ACQUISITION] Wonder acquires Mighty Quinn's BBQ",
                "section": "restaurant", "render_count": 7,
            },
        }
        changed = backfill_mod.backfill(registry)
        self.assertEqual(changed, 1)
        entry = registry["https://www.nrn.com/emerging-chains/wonder-acquires-mighty-quinn-s-bbq"]
        self.assertTrue(entry["is_corporate"])
        self.assertIn("Mighty", entry["anchor_nouns"])
        self.assertIn("Quinn", entry["anchor_nouns"])

    def test_non_corporate_entry_left_untouched(self):
        registry = {
            "https://example.com/some-ordinary-headline": {
                "first_rendered": "2026-07-10", "last_rendered": "2026-07-13",
                "title": "Diners are trading down or trading up",
                "section": "restaurant", "render_count": 2,
            },
        }
        changed = backfill_mod.backfill(registry)
        self.assertEqual(changed, 0)
        entry = registry["https://example.com/some-ordinary-headline"]
        self.assertNotIn("is_corporate", entry)
        self.assertNotIn("anchor_nouns", entry)

    def test_entry_already_tagged_is_skipped(self):
        registry = {
            "https://example.com/already-tagged": {
                "first_rendered": "2026-07-17", "last_rendered": "2026-07-17",
                "title": "[💰 FUNDING] Some Co raises new round",
                "section": "restaurant", "render_count": 1,
                "is_corporate": True, "anchor_nouns": ["Some", "Co"],
            },
        }
        changed = backfill_mod.backfill(registry)
        self.assertEqual(changed, 0)

    def test_badge_stripped_title_with_no_extractable_anchor_nouns_left_untouched(self):
        """A corporate item whose badge was stripped before storage (as
        _fmt_headline does when _badge_is_justified rejects it) and whose
        only capitalized word is the sentence-leading company name (dropped
        by _story_anchor_nouns) can't be badge-detected OR backfilled with a
        useful signature -- must not crash, must not write an empty
        anchor_nouns list that would silently match everything."""
        registry = {
            "https://example.com/wonder-funding-no-other-nouns": {
                "first_rendered": "2026-07-17", "last_rendered": "2026-07-17",
                "title": "Wonder is valued at more than $9B after latest fundraise",
                "section": "restaurant", "render_count": 1,
            },
        }
        changed = backfill_mod.backfill(registry)
        self.assertEqual(changed, 0)
        entry = registry["https://example.com/wonder-funding-no-other-nouns"]
        self.assertNotIn("is_corporate", entry)

    def test_idempotent_second_pass_changes_nothing(self):
        registry = {
            "https://www.nrn.com/emerging-chains/wonder-acquires-mighty-quinn-s-bbq": {
                "first_rendered": "2026-07-10", "last_rendered": "2026-07-13",
                "title": "[🏢 ACQUISITION] Wonder acquires Mighty Quinn's BBQ",
                "section": "restaurant", "render_count": 7,
            },
            "https://example.com/some-ordinary-headline": {
                "first_rendered": "2026-07-10", "last_rendered": "2026-07-13",
                "title": "Diners are trading down or trading up",
                "section": "restaurant", "render_count": 2,
            },
        }
        first_pass = backfill_mod.backfill(registry)
        self.assertEqual(first_pass, 1)
        snapshot = {k: dict(v) for k, v in registry.items()}
        second_pass = backfill_mod.backfill(registry)
        self.assertEqual(second_pass, 0)
        self.assertEqual(registry, snapshot)


if __name__ == "__main__":
    unittest.main()
