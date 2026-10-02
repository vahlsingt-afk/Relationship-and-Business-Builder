"""
test_world_national_no_duplicate_story.py

Regression coverage: the same real-world event (Iran/Khamenei funeral) was
covered by two different articles from two different feeds — one from BBC
World News (no US mention -> classified world) and one from NPR News (body
mentioned "U.S.-Israeli strikes" -> classified national) -- and both
rendered, once each, in A: World Headlines and B: National Headlines. Three
compounding bugs:

1. _story_anchor_nouns unconditionally skipped the first regex-matched
   proper noun assuming it was sentence-start capitalization. When the
   title's true first word didn't match the regex (too short, e.g. "The"),
   the first *match* found later ("Iran") got wrongly dropped -- breaking
   noun-overlap matching between the BBC and NPR headlines about the same
   event, since "Iran" was the only noun they had in common.
2. A and B each built a fresh, section-local cluster registry, so even with
   correct noun extraction there was no way to notice the same story had
   already been rendered in the other section.
3. The world/national scope classifier used "does the text mention the US
   anywhere" as its national signal, so a story about Iran that merely
   *mentions* US involvement got misclassified as national.

Fixed: _story_anchor_nouns only skips the first match when it actually is
the sentence-start word; _render_headline_section accepts a shared
cluster_state dict so A and B in the real render() call block each other's
duplicate stories; and title-level foreign-subject keywords now override an
incidental US mention when classifying scope.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402


def _item(title: str, url: str, summary: str = "") -> dict:
    return {
        "title": title,
        "summary": summary,
        "extras": {"source_name": "Test Source", "source_url": url, "pub_date": "2026-07-06"},
    }


class TestStoryAnchorNounsFirstWordFix(unittest.TestCase):
    def test_true_anchor_noun_not_dropped_when_first_word_too_short_to_match(self):
        title = "'The spectacle Iran wants the world to see': Lyse Doucet in Tehran"
        nouns = rib._story_anchor_nouns(title)
        self.assertIn("Iran", nouns)

    def test_sentence_start_capitalization_still_skipped(self):
        title = "Diesel sees biggest monthly fall in 26 years"
        nouns = rib._story_anchor_nouns(title)
        self.assertNotIn("Diesel", nouns)

    def test_two_articles_about_same_event_share_anchor_noun(self):
        t1 = "'The spectacle Iran wants the world to see': Lyse Doucet in Tehran"
        t2 = "Huge crowds of mourners join a funeral procession for Iran's Ayatollah Ali Khamenei"
        overlap = rib._story_anchor_nouns(t1) & rib._story_anchor_nouns(t2)
        self.assertTrue(overlap, f"Expected shared anchor noun between {t1!r} and {t2!r}")


class TestForeignSubjectOverridesIncidentalUSMention(unittest.TestCase):
    def test_iran_story_mentioning_us_strikes_is_not_national(self):
        items = [_item(
            "Huge crowds of mourners join a funeral procession for Iran's Ayatollah Ali Khamenei",
            "https://example.com/npr-khamenei",
            summary="Iran holds a funeral procession for Ayatollah Ali Khamenei more than four "
                    "months after he was killed in U.S.-Israeli strikes.",
        )]
        rendered = {}
        national_out = rib._render_headline_section(
            items, "B: National Headlines", "national", date(2026, 7, 6), rendered, scope_filter="us")
        self.assertNotIn("Khamenei", national_out)


class TestCrossSectionDuplicateStorySuppressed(unittest.TestCase):
    def test_same_event_from_two_articles_only_renders_once_across_world_and_national(self):
        world_article = _item(
            "'The spectacle Iran wants the world to see': Lyse Doucet in Tehran",
            "https://example.com/bbc-tehran",
        )
        national_leaning_article = _item(
            "Huge crowds of mourners join a funeral procession for Iran's Ayatollah Ali Khamenei",
            "https://example.com/npr-khamenei",
            summary="Iran holds a funeral procession for Ayatollah Ali Khamenei more than four "
                    "months after he was killed in U.S.-Israeli strikes.",
        )
        items = [world_article, national_leaning_article]
        rendered = {}
        cluster_state = {"sig_words": [], "counts": []}

        world_out = rib._render_headline_section(
            items, "A: World Headlines", "world", date(2026, 7, 6), rendered,
            scope_filter="world", cluster_state=cluster_state)
        national_out = rib._render_headline_section(
            items, "B: National Headlines", "national", date(2026, 7, 6), rendered,
            scope_filter="us", cluster_state=cluster_state)

        # Both articles are about Iran/Khamenei, so with the foreign-subject
        # fix both classify as world -- confirm the story appears in World...
        self.assertTrue("Iran" in world_out or "Khamenei" in world_out)
        # ...and does not also appear, in any form, in National.
        self.assertNotIn("Khamenei", national_out)
        self.assertNotIn("Tehran", national_out)


class TestRestaurantIndustryAndTechShareClusterState(unittest.TestCase):
    """RB-DEFECT-2026-07-10i: C: Restaurant Industry and D: Restaurant
    Technology each got their own fresh, section-local cluster_state (unlike
    A/B, which share one) -- so the "cap the same story to 2 renders" dedup
    only ever saw its own section's half of the pool. A story with 2 C-side
    articles and a 3rd D-side article about the identical event rendered 3
    times total (2 capped within C alone + 1 capped within D alone) instead
    of being capped at 2 across the combined C+D pool, same as A/B already
    enforce for world/national. Observed live: a Taco Bell drive-thru voice
    AI rollout covered by NRN (Section C) and separately by Restaurant
    Technology News / Restaurant Dive (Section D)."""

    def test_third_outlet_covering_same_story_capped_across_c_and_d(self):
        industry_articles = [
            _item("Taco Bell's drive-thru voice AI expands to nearly 900 restaurants",
                  "https://www.nrn.com/quick-service/taco-bell-drive-thru-ai"),
            _item("Taco Bell revs up drive-thru AI deployment",
                  "https://www.restaurantdive.com/news/taco-bell-ai-deployment"),
        ]
        tech_articles = [
            _item("Taco Bell Expands Drive-Thru Voice AI to Nearly 900 Restaurants Across the U.S.",
                  "https://restauranttechnologynews.com/taco-bell-drive-thru-ai"),
        ]
        rendered = {}
        cluster_state = {"sig_words": [], "counts": []}

        # C and D draw from separate source pools in the real pipeline
        # (restaurant_industry_headlines vs. _select_restaurant_tech_items) --
        # each call only sees its own pool's copy of the story.
        c_out = rib._render_headline_section(
            industry_articles, "C: Restaurant Industry", "restaurant", date(2026, 7, 10), rendered,
            cluster_state=cluster_state)
        d_out = rib._render_headline_section(
            tech_articles, "D: Restaurant Technology", "restaurant_tech", date(2026, 7, 10), rendered,
            cluster_state=cluster_state)

        total_renders = c_out.count("Taco Bell") + d_out.count("Taco Bell")
        self.assertLessEqual(total_renders, 2,
                              "same story should be capped at 2 renders across C+D combined")


if __name__ == "__main__":
    unittest.main()
