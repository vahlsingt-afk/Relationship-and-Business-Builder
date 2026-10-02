"""
test_newsletter_best_edition_and_cross_section_dedup.py

Regression coverage for two RB-DEFECT-2026-07-08 fixes in
render_intelligence_brief.py's D+ Newsletter Inbox:

1. Best-edition-per-publication selection. When a publication has multiple
   eligible editions in the lookback window, the old newest-first sort let a
   content-free edition (e.g. a "Premium Report"/sponsored-survey teaser with
   0 real articles, just a body_summary) claim the publication's one-slot
   limit ahead of an older-but-still-eligible edition that actually had real
   articles. Observed live: "QSR Magazine" rendered a linkless survey teaser
   from Jul 07 while a 13-article edition from Jul 06 sat unused.

2. Cross-section URL dedup. A newsletter article whose URL was already
   rendered standalone in an earlier headline section (e.g. Section D
   covering "Taco Bell revs up drive-thru AI deployment" from Restaurant
   Dive, then the Restaurant Dive newsletter D+ entry linking the same
   article again) repeated the same story twice in one brief. Headline
   sections register every rendered URL into _rendered_this_run; D+ now
   checks and contributes to that same registry.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402


def _edition(source: str, pub_date: str, articles: list[dict] | None = None,
             body_summary: str = "") -> dict:
    return {
        "title": source,
        "extras": {
            "source_name": source,
            "pub_date": pub_date,
            "articles": articles or [],
            "body_summary": body_summary,
        },
    }


class TestBestEditionPerPublication(unittest.TestCase):
    def test_content_free_newer_edition_does_not_win_over_older_edition_with_articles(self):
        older_with_articles = _edition(
            "QSR Magazine", "Jul 06, 2026",
            articles=[
                {"title": "Experienced Operators Look to Coffee to Diversify Portfolios", "url": "https://example.com/a"},
                {"title": "The Smart Safe Built Specifically for Quick Service", "url": "https://example.com/b"},
                {"title": "Why Less Data Can Lead to Smarter Restaurant Decisions", "url": "https://example.com/c"},
            ],
        )
        newer_empty = _edition(
            "QSR Magazine", "Jul 07, 2026",
            articles=[],
            body_summary="The 2026 State of Restaurant Resilience Survey by QSR and Global Payments.",
        )
        sections = {"newsletter_intelligence": [newer_empty, older_with_articles]}
        out = rib._render_newsletter_inbox(sections, prior_state=None)
        self.assertIn("**QSR Magazine**", out)
        self.assertIn("Jul 06, 2026", out)
        self.assertIn("Smart Safe Built Specifically for Quick Service", out)
        self.assertNotIn("Restaurant Resilience Survey", out)

    def test_edition_with_more_articles_wins_regardless_of_date(self):
        fewer_but_newer = _edition(
            "Restaurant Dive", "Jul 07, 2026",
            articles=[{"title": "Single Article Edition", "url": "https://example.com/single"}],
        )
        more_but_older = _edition(
            "Restaurant Dive", "Jul 05, 2026",
            articles=[
                {"title": "First Real Article About Restaurant Tech", "url": "https://example.com/1"},
                {"title": "Second Real Article About Restaurant Tech", "url": "https://example.com/2"},
                {"title": "Third Real Article About Restaurant Tech", "url": "https://example.com/3"},
            ],
        )
        sections = {"newsletter_intelligence": [fewer_but_newer, more_but_older]}
        out = rib._render_newsletter_inbox(sections, prior_state=None)
        self.assertIn("First Real Article About Restaurant Tech", out)
        self.assertNotIn("Single Article Edition", out)


class TestCrossSectionUrlDedup(unittest.TestCase):
    def setUp(self):
        rib._rendered_this_run = set()

    def tearDown(self):
        rib._rendered_this_run = set()

    def test_article_already_rendered_in_headline_section_is_skipped_in_newsletter(self):
        # RB-2026-08-28: RB-DEFECT-2026-08-14 added
        # MIN_ARTICLES_PER_NEWSLETTER=3 -- after the shared_url article is
        # correctly filtered as already-rendered, only 1 article survived,
        # under the floor, so the whole edition (not just the dedup logic
        # this test actually exercises) started rendering "". Padded with
        # two more genuinely-distinct filler articles so 3 survive dedup.
        shared_url = "https://www.restaurantdive.com/news/taco-bell-omilia-drive-thru-ai-deployment/824564/"
        rib._rendered_this_run.add(shared_url)

        sections = {
            "newsletter_intelligence": [
                _edition("Restaurant Dive", "Jul 07, 2026", articles=[
                    {"title": "Taco Bell revs up drive-thru AI deployment", "url": shared_url},
                    {"title": "A genuinely different restaurant tech article", "url": "https://example.com/other"},
                    {"title": "Restaurant technology roundup", "url": "https://example.com/filler-1"},
                    {"title": "Payments industry update", "url": "https://example.com/filler-2"},
                ]),
            ]
        }
        out = rib._render_newsletter_inbox(sections, prior_state=None)
        self.assertNotIn("Taco Bell revs up drive-thru AI deployment", out)
        self.assertIn("A genuinely different restaurant tech article", out)

    def test_newsletter_articles_register_into_rendered_this_run(self):
        # RB-2026-08-28: same MIN_ARTICLES_PER_NEWSLETTER=3 fix as above --
        # this test only cares whether `url` registers into
        # _rendered_this_run, which requires the edition to actually render.
        url = "https://example.com/newsletter-exclusive"
        sections = {
            "newsletter_intelligence": [
                _edition("Restaurant Dive", "Jul 07, 2026", articles=[
                    {"title": "Newsletter Exclusive Restaurant Tech Story", "url": url},
                    {"title": "Restaurant technology roundup", "url": "https://example.com/filler-1"},
                    {"title": "Payments industry update", "url": "https://example.com/filler-2"},
                ]),
            ]
        }
        rib._render_newsletter_inbox(sections, prior_state=None)
        self.assertIn(url, rib._rendered_this_run)

    def test_tracking_wrapped_duplicate_of_already_rendered_url_is_skipped(self):
        """RB-DEFECT-2026-07-10i: the exact-URL check above misses a
        newsletter's own click-tracking redirect for an article whose
        direct publisher URL was already rendered in an earlier section --
        different URL string, same destination once the tracking wrapper's
        base64-encoded path segment is decoded. Observed live: Section D
        linked restaurantdive.com/news/international-dairy-queen-hires-...
        directly, then the Restaurant Dive newsletter's own D+ entry linked
        the identical article again via a link.restaurantdive.com/click/...
        tracking redirect that decodes to that same URL."""
        import base64
        direct_url = "https://www.restaurantdive.com/news/international-dairy-queen-hires-phil-crawford-chief-technology-officer/824612/"
        rib._rendered_this_run.add(direct_url)
        encoded = base64.urlsafe_b64encode(direct_url.encode()).decode().rstrip("=")
        wrapped_url = f"https://link.restaurantdive.com/click/46488028.33251/{encoded}/deadbeef"

        # RB-2026-08-28: RB-DEFECT-2026-08-14 added
        # MIN_ARTICLES_PER_NEWSLETTER=3 -- padded with two more filler
        # articles so 3 survive the tracking-wrapped-duplicate filter this
        # test actually exercises (same reasoning as the two tests above).
        sections = {
            "newsletter_intelligence": [
                _edition("Restaurant Dive", "Jul 08, 2026", articles=[
                    {"title": "Dairy Queen hires Shake Shack vet to modernize tech", "url": wrapped_url},
                    {"title": "A genuinely different restaurant tech article", "url": "https://example.com/other"},
                    {"title": "Restaurant technology roundup", "url": "https://example.com/filler-1"},
                    {"title": "Payments industry update", "url": "https://example.com/filler-2"},
                ]),
            ]
        }
        out = rib._render_newsletter_inbox(sections, prior_state=None)
        self.assertNotIn("Dairy Queen hires Shake Shack vet", out)
        self.assertIn("A genuinely different restaurant tech article", out)


if __name__ == "__main__":
    unittest.main()
