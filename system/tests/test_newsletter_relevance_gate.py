"""
test_newsletter_relevance_gate.py

Regression coverage: D+ Newsletter Inbox is supposed to be restaurant/
hospitality/payments-tech news, but "not off-topic" (score >= 0, i.e. not
explicitly political/lifestyle) was being treated as sufficient to render
a newsletter. That let two personal-interest sources through once the
fetch-domain fix (needed for a genuine hospitality-industry newsletter)
widened what could reach the pipeline at all:

- "The Pour Over" (a Christian devotional/news roundup) — none of its
  article titles ("official split was actually more than a decade", "the
  first shots of the Revolutionary War") match any restaurant/tech keyword,
  but none matched an off-topic one either, so they scored 0 and passed.
- "Executive Recruit via LinkedIn" — "Mapping the Path from Technical
  Excellence to Strategic Leadership" scored positive only because "tech"
  matched as a bare substring inside "Technical" (fixed alongside this by
  word-boundary matching _article_relevance_score, same bug class as
  Section D's keyword check).

Fix: a newsletter edition only renders if its source is a known industry
publication OR at least one article has a genuinely positive relevance
score (not just a non-negative one).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402


def _newsletter_item(source: str, pub_date: str, articles: list[tuple[str, str]]) -> dict:
    return {
        "title": source,
        "extras": {
            "source_name": source,
            "pub_date": pub_date,
            "articles": [{"title": t, "url": u} for t, u in articles],
        },
    }


class TestArticleRelevanceScoreWordBoundary(unittest.TestCase):
    def test_technical_does_not_false_positive_on_tech(self):
        score = rib._article_relevance_score("Mapping the Path from Technical Excellence to Strategic Leadership")
        self.assertEqual(score, 0)

    def test_genuine_tech_keyword_scores_positive(self):
        score = rib._article_relevance_score("New AI ordering kiosk for restaurants")
        self.assertGreater(score, 0)

    def test_hospitality_keyword_scores_positive(self):
        score = rib._article_relevance_score("Hospitality Headline")
        self.assertGreater(score, 0)


class TestNewsletterPublicationRelevanceGate(unittest.TestCase):
    def test_pour_over_style_content_excluded(self):
        sections = {
            "newsletter_intelligence": [
                _newsletter_item("The Pour Over", "Jul 04, 2026", [
                    ("official split was actually more than a decade", "https://example.com/1"),
                    ("the first shots of the Revolutionary War", "https://example.com/2"),
                ]),
            ]
        }
        out = rib._render_newsletter_inbox(sections, prior_state=None)
        self.assertEqual(out, "")

    def test_recruiter_newsletter_excluded(self):
        sections = {
            "newsletter_intelligence": [
                _newsletter_item("Executive Recruit via LinkedIn", "Jul 06, 2026", [
                    ("Mapping the Path from Technical Excellence to Strategic Leadership",
                     "https://example.com/1"),
                ]),
            ]
        }
        out = rib._render_newsletter_inbox(sections, prior_state=None)
        self.assertEqual(out, "")

    def test_known_industry_source_included_even_with_generic_titles(self):
        # RB-2026-08-28: RB-DEFECT-2026-08-14 added
        # MIN_ARTICLES_PER_NEWSLETTER=3 -- an edition with real articles
        # under that floor is skipped entirely, so this test's original
        # single-article fixture started rendering "" for a reason unrelated
        # to the relevance gate it's actually testing. Padded to 3 articles;
        # the primary (generic-title) one stays first.
        sections = {
            "newsletter_intelligence": [
                _newsletter_item("Restaurant Dive", "Jul 04, 2026", [
                    ("Jersey Mike's files for IPO", "https://example.com/1"),
                    ("Restaurant technology roundup", "https://example.com/2"),
                    ("Payments industry update", "https://example.com/3"),
                ]),
            ]
        }
        out = rib._render_newsletter_inbox(sections, prior_state=None)
        self.assertIn("Restaurant Dive", out)

    def test_unknown_source_with_relevant_article_included(self):
        # RB-2026-08-28: same MIN_ARTICLES_PER_NEWSLETTER=3 fix as above.
        sections = {
            "newsletter_intelligence": [
                _newsletter_item("Michael 'schatzy' Schatzberg via LinkedIn", "Jul 04, 2026", [
                    ("Hospitality Headline", "https://example.com/1"),
                    ("Restaurant technology roundup", "https://example.com/2"),
                    ("Payments industry update", "https://example.com/3"),
                ]),
            ]
        }
        out = rib._render_newsletter_inbox(sections, prior_state=None)
        self.assertIn("Schatzberg", out)


if __name__ == "__main__":
    unittest.main()
