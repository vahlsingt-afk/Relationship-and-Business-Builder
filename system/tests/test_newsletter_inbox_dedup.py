"""
test_newsletter_inbox_dedup.py

Regression coverage: D+ Newsletter Inbox showed two editions of the same
publication ("Payments Dive" Jul 02 and Jul 04) in a single brief, consuming
2 of the 3 MAX_NEWSLETTERS slots and crowding out a different publication
entirely. Root cause: _compute_newsletter_intelligence's 3-day fetch lookback
lets a delayed/missed edition remain "eligible" for days after publication,
and the renderer had no per-publication cap — only a cross-day dedup on
exact (source, pub_date) pairs, which doesn't help when it's the first time
either edition is actually rendered.

_render_newsletter_inbox now keeps only the newest edition per publication
per brief (seen_sources_this_run) and sorts by date within each priority
tier so the newest edition is the one that wins the slot.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402


def _newsletter_item(source: str, pub_date: str, article_title: str) -> dict:
    # RB-2026-08-28: RB-DEFECT-2026-08-14 added MIN_ARTICLES_PER_NEWSLETTER=3
    # (skip a whole edition if it clears junk/relevance filtering with fewer
    # than 3 real articles -- avoids a "roundup" newsletter rendering with
    # one lonely link). This helper predates that floor and only ever
    # provided one article, so every test using it started silently
    # rendering "" regardless of what it was actually testing. Padded with
    # two on-topic filler articles so the floor is cleared without changing
    # any test's actual assertions (article_title stays first/primary).
    base = f"https://example.com/{source}/{pub_date}".replace(" ", "-")
    return {
        "title": source,
        "extras": {
            "source_name": source,
            "pub_date": pub_date,
            "articles": [
                {"title": article_title, "url": base},
                {"title": "Restaurant technology roundup", "url": f"{base}-filler-1"},
                {"title": "Payments industry update", "url": f"{base}-filler-2"},
            ],
        },
    }


class TestNewsletterInboxDedup(unittest.TestCase):
    def test_only_newest_edition_of_a_publication_renders(self):
        sections = {
            "newsletter_intelligence": [
                _newsletter_item("Payments Dive", "Jul 02, 2026", "How prepaid cards drive business growth"),
                _newsletter_item("Payments Dive", "Jul 04, 2026", "Prepare for AI-Powered Payments"),
            ]
        }
        out = rib._render_newsletter_inbox(sections, prior_state=None)
        self.assertEqual(out.count("**Payments Dive**"), 1)
        self.assertIn("Jul 04, 2026", out)
        self.assertNotIn("Jul 02, 2026", out)
        self.assertIn("Prepare for AI-Powered Payments", out)
        self.assertNotIn("How prepaid cards drive business growth", out)

    def test_duplicate_editions_dont_crowd_out_a_different_publication(self):
        sections = {
            "newsletter_intelligence": [
                _newsletter_item("Payments Dive", "Jul 02, 2026", "Article A"),
                _newsletter_item("Payments Dive", "Jul 04, 2026", "Article B"),
                _newsletter_item("Restaurant Dive", "Jul 04, 2026", "Article C"),
                # Not a known industry source name, so (per the D+ relevance
                # gate) it needs a genuinely on-topic article to qualify at all.
                _newsletter_item("CStore Decisions", "Jul 02, 2026", "New POS system rollout for convenience stores"),
            ]
        }
        out = rib._render_newsletter_inbox(sections, prior_state=None)
        # 3 distinct publications should fill all MAX_NEWSLETTERS=3 slots,
        # not 2 slots burned on the same publication.
        self.assertIn("**Payments Dive**", out)
        self.assertIn("**Restaurant Dive**", out)
        self.assertIn("**CStore Decisions**", out)


if __name__ == "__main__":
    unittest.main()
