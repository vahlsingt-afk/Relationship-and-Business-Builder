"""
test_world_national_headline_cap_scope_split.py

Regression coverage: world_national_headlines feeds TWO downstream sections
(A: World Headlines, B: National Headlines) via a scope split at render
time, but daily_brief.py's RB-DEFECT-044 "top 10" cap used to truncate the
COMBINED pool to 10 items before that split ever happened. Confirmed in a
real run on 2026-07-07: 100 fresh candidates existed, but all 10 top-ranked
survivors classified as "world" scope on a pure signal_weight/pub_date
ranking, leaving Section B (National) with zero items despite genuine fresh
US news being available in the discarded 90.

Fixed by splitting the pool by scope (rb_core.classify_world_national_scope)
BEFORE capping, and capping each scope's pool independently to 10 -- so
world_national_headlines can hold up to 20 items total, and neither
downstream section is starved by how the two scopes happen to rank against
each other.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db  # noqa: E402
import rb_core as core  # noqa: E402


def _world_item(n: int, weight: int = 90) -> dict:
    return {
        "title": f"China test-launches missile number {n}",
        "extras": {"signal_weight": weight, "pub_date": "2026-07-07", "source_url": f"https://example.com/world-{n}"},
    }


def _us_item(n: int, weight: int = 10) -> dict:
    return {
        "title": f"Congress debates federal budget item {n}",
        "extras": {"signal_weight": weight, "pub_date": "2026-07-01", "source_url": f"https://example.com/us-{n}"},
    }


class TestClassifyWorldNationalScope(unittest.TestCase):
    """rb_core.classify_world_national_scope is shared by daily_brief.py's
    pre-render cap and render_intelligence_brief.py's render-time scope
    filter — they must agree, or the cap can split items into buckets the
    renderer then reclassifies differently."""

    def test_foreign_subject_overrides_incidental_us_mention(self):
        item = {
            "title": "Huge crowds of mourners join a funeral procession for Iran's Ayatollah Ali Khamenei",
            "summary": "Iran holds a funeral procession more than four months after he was killed in U.S.-Israeli strikes.",
        }
        self.assertEqual(core.classify_world_national_scope(item), "world")

    def test_domestic_us_story_classified_us(self):
        item = {"title": "Congress debates federal budget item", "summary": ""}
        self.assertEqual(core.classify_world_national_scope(item), "us")

    def test_plain_foreign_story_classified_world(self):
        item = {"title": "China test-launches a ballistic missile in the South Pacific", "summary": ""}
        self.assertEqual(core.classify_world_national_scope(item), "world")

    def test_domestic_story_with_no_explicit_us_keyword_defaults_to_us(self):
        """RB-DEFECT-2026-07-13: a story with neither a foreign-subject
        match NOR an explicit US-keyword hit used to fall through to
        "world" by default -- so purely domestic stories with no textual
        US marker rendered under "A: World Headlines." Observed live:
        "The SpaceX IPO made history..." (a US company, no foreign
        subject, no US keyword), "US Senator Mitch McConnell says absence
        due to fall and pneumonia" ("senate" doesn't substring-match
        "senator", "u.s." with periods doesn't match "US" without them),
        and "Mark Cuban has strong words on AI companies and job losses"
        (a US businessman, no textual US marker at all)."""
        titles = [
            "The SpaceX IPO made history. One month on has it lost momentum?",
            "US Senator Mitch McConnell says absence due to fall and pneumonia",
            "Mark Cuban has strong words on AI companies and job losses",
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertEqual(
                    core.classify_world_national_scope({"title": title, "summary": ""}), "us", title,
                )

    def test_genuine_world_story_with_us_involvement_still_classified_world(self):
        """The foreign-subject override must still win even when a US actor
        is directly involved -- Iran/Hormuz war coverage stays "world"."""
        item = {"title": "US and Iran trade fire as tensions rise over Strait of Hormuz", "summary": ""}
        self.assertEqual(core.classify_world_national_scope(item), "world")


class TestWorldNationalCapDoesNotStarveEitherScope(unittest.TestCase):
    def test_high_weight_world_items_do_not_crowd_out_all_us_items(self):
        # 15 high-weight world items + 15 low-weight US items. Under the old
        # combined-pool-then-cap-to-10 logic, the top 10 by weight would all
        # be world items, leaving National with 0.
        items = [_world_item(i, weight=90) for i in range(15)] + [_us_item(i, weight=10) for i in range(15)]
        sections = {"world_national_headlines": list(items)}
        db._cap_headline_sections(sections)
        result = sections["world_national_headlines"]

        world_ct = sum(1 for i in result if core.classify_world_national_scope(i) == "world")
        us_ct = sum(1 for i in result if core.classify_world_national_scope(i) == "us")
        self.assertGreater(us_ct, 0, "National scope was fully starved by the shared cap")
        self.assertLessEqual(world_ct, 10)
        self.assertLessEqual(us_ct, 10)

    def test_each_scope_capped_at_ten_independently(self):
        items = [_world_item(i) for i in range(20)] + [_us_item(i) for i in range(20)]
        sections = {"world_national_headlines": list(items)}
        db._cap_headline_sections(sections)
        result = sections["world_national_headlines"]

        world_ct = sum(1 for i in result if core.classify_world_national_scope(i) == "world")
        us_ct = sum(1 for i in result if core.classify_world_national_scope(i) == "us")
        self.assertEqual(world_ct, 10)
        self.assertEqual(us_ct, 10)

    def test_other_headline_sections_unaffected_single_pool_cap(self):
        items = [_us_item(i, weight=i) for i in range(15)]
        sections = {"restaurant_industry_headlines": list(items)}
        db._cap_headline_sections(sections)
        self.assertEqual(len(sections["restaurant_industry_headlines"]), 10)


if __name__ == "__main__":
    unittest.main()
