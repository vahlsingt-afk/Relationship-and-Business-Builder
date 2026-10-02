"""
test_gp_intel_wrapped_url_dedup.py

Regression coverage: RB-DEFECT-2026-08-12 -- "Toast aims to drive AI into
dining" rendered in both D+ Newsletter Inbox and K: GP/Genius Field
Intelligence on 2026-08-12. D+'s own link for the article was a
click-tracking redirect (link.restaurantdive.com/click/...) wrapping the
real destination as a base64 path segment; K found the same story via its
direct restaurantdive.com URL through its own headline-pool scan. Different
strings, same story -- D+ already resolves wrapped links before comparing
against _rendered_this_run (see _resolve_wrapped_url), but K's three dedup
checks (headline pools, newsletter articles, price-watch signals) never did,
so an exact-string comparison against the wrapped URL sitting in
_rendered_this_run silently failed to catch the direct-URL duplicate.
"""
from __future__ import annotations

import base64
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402


def _wrap(direct_url: str) -> str:
    b64 = base64.urlsafe_b64encode(direct_url.encode()).decode().rstrip("=")
    return f"https://link.restaurantdive.com/click/123.456/{b64}/deadbeef"


class TestGpIntelWrappedUrlDedup(unittest.TestCase):
    DIRECT_URL = "https://www.restaurantdive.com/news/toast-aims-to-drive-ai-into-dining/827503/"

    def setUp(self):
        self._orig = rib._rendered_this_run

    def tearDown(self):
        rib._rendered_this_run = self._orig

    def test_direct_url_skipped_when_wrapped_form_already_rendered(self):
        """D+ rendered the wrapped tracking link first (real-world order:
        D+ runs before K); K's headline-pool scan must recognize the direct
        URL as the same story via _resolve_wrapped_url, not just exact match."""
        rib._rendered_this_run = {_wrap(self.DIRECT_URL)}
        sections = {
            "restaurant_technology_headlines": [
                {"title": "Toast aims to drive AI into dining",
                 "why_it_matters": "Toast executives discuss AI adoption.",
                 "extras": {"source_url": self.DIRECT_URL, "source_name": "Restaurant Dive",
                            "pub_date": "2026-08-10", "entities": ["Toast"]}},
            ],
        }
        out = rib._render_gp_intel(sections, date(2026, 8, 12))
        self.assertNotIn("Toast aims to drive AI into dining", out)

    def test_unrelated_direct_url_not_suppressed(self):
        """Sanity check the fix isn't over-broad: an unrelated URL must
        still render normally."""
        rib._rendered_this_run = {_wrap(self.DIRECT_URL)}
        sections = {
            "restaurant_technology_headlines": [
                {"title": "Global Payments unveils new POS platform for restaurants",
                 "why_it_matters": "A completely different, unrelated story about Global Payments.",
                 "extras": {"source_url": "https://www.restaurantdive.com/news/some-other-story/999999/",
                            "source_name": "Restaurant Dive", "pub_date": "2026-08-12",
                            "entities": ["Global Payments"]}},
            ],
        }
        out = rib._render_gp_intel(sections, date(2026, 8, 12))
        self.assertIn("Global Payments unveils new POS platform", out)

    def test_newsletter_article_wrapped_url_deduped_against_direct(self):
        """The reverse direction: K's own newsletter-article scan finds the
        wrapped link, but the direct URL was already rendered earlier
        (e.g. by section D) -- must also resolve before comparing."""
        rib._rendered_this_run = {self.DIRECT_URL}
        sections = {
            "newsletter_intelligence": [
                {"extras": {
                    "source_name": "Restaurant Dive",
                    "articles": [
                        {"title": "Toast aims to drive AI into dining", "url": _wrap(self.DIRECT_URL)},
                    ],
                }},
            ],
        }
        out = rib._render_gp_intel(sections, date(2026, 8, 12))
        self.assertNotIn("Toast aims to drive AI into dining", out)


if __name__ == "__main__":
    unittest.main()
