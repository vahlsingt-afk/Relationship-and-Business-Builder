"""
test_world_national_source_diversity.py

Regression coverage: broadening _WORLD_RELEVANCE_KEYWORDS (see
test_world_headline_relevance_broadening.py) only changes which *fetched*
articles pass the render-time relevance gate — it can't surface a genuine
politics/war/labor-market story if no configured feed ever fetches one in
the first place. Before 2026-07-06, every source tagged world_national in
industry_sources.yaml was a business feed (BBC Business, NPR Business,
Yahoo Finance News, Axios Business), so World/National Headlines could only
ever reflect business coverage no matter how the keyword gate was tuned —
exactly Todd's complaint: "world news and national news is too narrowly
focused on business articles only."

Fix: added BBC World News and NPR News (general, not business-desk) as
world_national sources, confirmed live to surface genuine war/politics
coverage (Ukraine strikes, Iran, France, Bangladesh-India-China) alongside
the existing business feeds.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import web_scanner as ws  # noqa: E402


class TestWorldNationalSourceDiversity(unittest.TestCase):
    def test_general_news_sources_configured_alongside_business(self):
        cfg = ws.load_sources_config()
        world_sources = [s for s in cfg["sources"] if s.get("default_domain") == "world_national"]
        names = {s["name"] for s in world_sources}

        # At least one source must be general/world news, not business-desk —
        # otherwise the whole category is business coverage by construction.
        general_news_names = {"BBC World News", "NPR News"}
        self.assertTrue(
            general_news_names & names,
            f"Expected a general-news world_national source among {names}",
        )
        # The business feeds should still be present too — this is additive.
        self.assertIn("BBC Business", names)

    def test_general_news_urls_are_not_business_desk_paths(self):
        cfg = ws.load_sources_config()
        world_sources = {s["name"]: s["url"] for s in cfg["sources"] if s.get("default_domain") == "world_national"}
        for name in ("BBC World News", "NPR News"):
            if name in world_sources:
                self.assertNotIn("business", world_sources[name].lower())


if __name__ == "__main__":
    unittest.main()
