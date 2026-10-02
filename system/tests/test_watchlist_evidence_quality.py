"""
test_watchlist_evidence_quality.py

Regression coverage for two watchlist_intelligence data-quality issues found
while building Technology Radar (system/scripts/render_daily_brief.py):

1. _compute_watchlist_intelligence's press-release and entity-alert evidence
   text embedded a raw tracking/redirect URL directly in the sentence
   ("... (Source: Press Wire, https://news.google.com/rss/articles/CBMi...)"),
   making why_it_matters an unreadable wall of text for every entity sourced
   from a press release. The "Monitored {type}. Evidence: ..." wrapper was
   also pure boilerplate — being monitored is already implied by appearing
   in the watchlist at all.

2. Rollup items (extras.entity_type == "rollup", built for the ~140-entity
   "No Change" mandatory-coverage bucket) use extras.entity_name to hold the
   human-readable CATEGORY label ("AI (Restaurant & Horizontal)", "POS"),
   with the real company list in extras.no_change_entities. Daily Brief's
   _render_technology_radar treated entity_name uniformly for both
   individual entities and rollups, so category labels leaked into the
   "No material updates this cycle" line as if they were companies
   (e.g. "POS, Payments, Investors & Board-Level, Toast, Square").
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db  # noqa: E402
import render_daily_brief as rdb  # noqa: E402


class TestWatchlistEvidenceIsReadable(unittest.TestCase):
    def test_press_release_evidence_has_no_embedded_url(self):
        import tempfile
        report = {"relationship_signals": {"signals": []}}
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            alerts_path = cache_dir / "entity_alerts_cache.json"
            alerts_path.write_text(json.dumps({
                "entities": {
                    "Domino's": {
                        "press_release_items": [{
                            "title": "Domino's Pizza China Deepens Market Leadership",
                            "source": "PR Newswire",
                            "url": "https://news.google.com/rss/articles/CBMijwJBVV95cUxOV2UxVUsxalBZUE4",
                        }],
                    },
                },
            }), encoding="utf-8")
            with patch.object(db.core, "CACHE_DIR", cache_dir):
                items = db._compute_watchlist_intelligence(report, {})

        dominos_items = [i for i in items if (i.get("extras") or {}).get("entity_name") == "Domino's"]
        self.assertTrue(dominos_items, "expected Domino's (a mandatory restaurant brand) to appear")
        why = dominos_items[0].get("why_it_matters") or ""
        self.assertNotIn("http", why)
        self.assertIn("Domino's Pizza China Deepens Market Leadership", why)
        self.assertIn("PR Newswire", why)
        self.assertNotIn("Monitored", why)

    def test_why_it_matters_has_no_monitored_boilerplate_wrapper(self):
        """_compute_watchlist_intelligence's why_it_matters must be the raw
        evidence directly, not wrapped in 'Monitored {type}. Evidence: ...'"""
        report = {"relationship_signals": {"signals": [{"tier": "inner", "name": "Test Person"}]}}
        sections = {
            "new_intelligence_today": [
                {"title": "Test Person mentioned in a signal", "summary": "", "disposition": "monitor"},
            ],
        }
        items = db._compute_watchlist_intelligence(report, sections)
        person_items = [i for i in items if (i.get("extras") or {}).get("entity_name") == "Test Person"]
        self.assertTrue(person_items)
        why = person_items[0].get("why_it_matters") or ""
        self.assertNotIn("Monitored", why)
        self.assertNotIn("Evidence:", why)


class TestWatchlistEvidenceUrlCaptured(unittest.TestCase):
    """RB-DEFECT-2026-07-10e: the real press-release/alert URL was stripped
    out of why_it_matters (correctly, per the fix above) but never captured
    anywhere else -- so Section F / Technology Radar bullets had no way to
    be read at all, unlike news-story sections elsewhere in the brief which
    render [title](url). extras.source_url now carries the real link
    through so the renderer can build a proper markdown link."""

    def test_press_release_url_captured_in_extras(self):
        import tempfile
        report = {"relationship_signals": {"signals": []}}
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            alerts_path = cache_dir / "entity_alerts_cache.json"
            alerts_path.write_text(json.dumps({
                "entities": {
                    "Domino's": {
                        "press_release_items": [{
                            "title": "Domino's Pizza China Deepens Market Leadership",
                            "source": "PR Newswire",
                            "url": "https://prnewswire.com/dominos-china",
                        }],
                    },
                },
            }), encoding="utf-8")
            with patch.object(db.core, "CACHE_DIR", cache_dir):
                items = db._compute_watchlist_intelligence(report, {})

        dominos_items = [i for i in items if (i.get("extras") or {}).get("entity_name") == "Domino's"]
        self.assertTrue(dominos_items)
        self.assertEqual(
            dominos_items[0]["extras"]["source_url"], "https://prnewswire.com/dominos-china",
        )

    def test_no_url_when_no_press_release_or_alert_hit(self):
        report = {"relationship_signals": {"signals": [{"tier": "inner", "name": "Test Person"}]}}
        sections = {
            "new_intelligence_today": [
                {"title": "Test Person mentioned in a signal", "summary": "", "disposition": "monitor"},
            ],
        }
        items = db._compute_watchlist_intelligence(report, sections)
        person_items = [i for i in items if (i.get("extras") or {}).get("entity_name") == "Test Person"]
        self.assertTrue(person_items)
        self.assertIsNone(person_items[0]["extras"].get("source_url"))


class TestTechnologyRadarRollupExpansion(unittest.TestCase):
    """RB-2026-07-20: the quiet-entity rollup was collapsed from a per-entity
    name list to a single count line -- a 150+-name sentence proved nothing
    a CEO needs proven and read as pure noise.

    RB-DEFECT-2026-07-27 superseded that count line entirely: assessed
    against the Intelligence Brief's own Section F (Watchlist) for overlap,
    the count line was found to be an exact duplicate of noise Todd asked
    to remove from Section F too. Technology Radar is now escalations-only
    -- rollup/quiet items (whether a category rollup or an individual
    Relevant Activity/No Change entity) contribute nothing to its output at
    all, not even a count."""

    def test_rollup_item_contributes_nothing(self):
        sections = {
            "watchlist_intelligence": [
                {
                    "title": "POS — No Change (2 scanned, no material developments)",
                    "extras": {
                        "entity_name": "POS",
                        "entity_type": "rollup",
                        "watchlist_status": "No Change",
                        "no_change_entities": ["Toast", "Square"],
                    },
                },
            ],
        }
        from datetime import date
        out = rdb._render_technology_radar(sections, date(2026, 7, 7))
        self.assertEqual(out, "")

    def test_individual_quiet_entity_contributes_nothing(self):
        sections = {
            "watchlist_intelligence": [
                {
                    "title": "Toast — Relevant Activity",
                    "extras": {
                        "entity_name": "Toast",
                        "entity_type": "company",
                        "watchlist_status": "Relevant Activity",
                    },
                },
            ],
        }
        from datetime import date
        out = rdb._render_technology_radar(sections, date(2026, 7, 7))
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
