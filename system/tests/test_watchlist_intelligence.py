"""
test_watchlist_intelligence.py — RB 9.71: brief quality fixes for
watchlist_intelligence.

Covers two RB 9.70-deferred defects:

1. `_name_in()` used word-boundary-free substring matching, producing false
   "Escalation"/"Relevant Activity" classifications: "Hi Auto" matched the
   bare substring "auto" inside "Autonomous discovery gap" (an act_today
   item), and "GK Software" matched the bare word "software" in unrelated
   headlines once its 2-char "GK" token was dropped by the old `len(p) > 2`
   filter. Fixed via whole-word regex matching on every token (>= 2 chars).

2. The RB-INTEL-021 mandatory-coverage "No Change" rollups built by
   `_compute_watchlist_intelligence()` were being physically removed from
   the rendered brief by `intelligence_lifecycle.filter_suppressed_from_sections()`
   once they'd been seen on a prior day (DORMANT/ACKNOWLEDGED + low novelty).
   That defeats the coverage guarantee — "No Change" items are SUPPOSED to
   repeat every cycle. Fixed by adding "watchlist_intelligence" to
   `_NOVELTY_FILTER_EXEMPT`.
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db  # noqa: E402
import intelligence_lifecycle as il  # noqa: E402


def _item(title, summary="", disposition="monitor"):
    return db._canonical_item(
        title=title,
        summary=summary,
        why_it_matters="test",
        recommended_action="test",
        disposition=disposition,
        grounding="system_detected",
        freshness="fresh",
        source_refs=["test"],
        confidence="medium",
    )


class TestNameInWordBoundary(unittest.TestCase):
    """RB 9.71 item 2 — _name_in() word-boundary fix."""

    def _status(self, entity_name, sections):
        status, _evidence = db._watchlist_entity_status(entity_name, sections)
        return status

    def test_hi_auto_does_not_match_autonomous(self):
        sections = {
            "autonomous_discovery_evidence": [
                _item("Autonomous discovery gap", "no autonomous sources configured",
                      disposition="act_today"),
            ],
        }
        self.assertEqual(self._status("Hi Auto", sections), "No Change")

    def test_gk_software_does_not_match_bare_software_mention(self):
        sections = {
            "strategic_industry_signals": [
                _item("Vendor news", "A competitor announced new software for restaurants."),
            ],
        }
        self.assertEqual(self._status("GK Software", sections), "No Change")

    def test_gk_software_matches_full_name(self):
        sections = {
            "new_intelligence_today": [
                _item("GK Software announces partnership",
                      "GK Software Inc. expands its POS platform."),
            ],
        }
        self.assertEqual(self._status("GK Software", sections), "New Activity")

    def test_par_technology_true_positive_still_matches(self):
        sections = {
            "strategic_industry_signals": [
                _item("PAR Technology Corp announces Q2 results", "PAR Technology beat estimates."),
            ],
        }
        self.assertEqual(self._status("PAR Technology", sections), "Relevant Activity")

    def test_escalation_requires_act_today_and_name_match(self):
        sections = {
            "new_intelligence_today": [
                _item("PAR Technology wins new contract", "PAR Technology Corp wins big deal.",
                      disposition="act_today"),
            ],
        }
        self.assertEqual(self._status("PAR Technology", sections), "Escalation")


class TestWatchlistRollupSurvivesNoveltyFilter(unittest.TestCase):
    """RB 9.71 item 1 — watchlist_intelligence exempt from novelty filtering."""

    def test_watchlist_intelligence_in_novelty_filter_exempt(self):
        self.assertIn("watchlist_intelligence", il._NOVELTY_FILTER_EXEMPT)

    def test_dormant_no_change_rollup_survives_filter(self):
        # Build a store with a DORMANT, low-novelty rollup item already seen
        # on a prior day.
        store = il.IntelligenceStore({
            "contract": "rb_intelligence_store_v1",
            "version": 1,
            "last_advanced_date": None,
            "items": {},
        })
        today = date.today()
        first_seen = (today - timedelta(days=5)).isoformat()

        rollup = _item(
            "Restaurant Technology — No Change (64 entities, no material developments detected)",
            "Scanned 64 restaurant technology entities: ... No material news detected.",
            disposition="ignore",
        )
        escalation = _item(
            "Global Payments — Escalation", "Acquisition rumor.", disposition="act_today",
        )

        sections = {
            "watchlist_intelligence": [rollup, escalation],
            "other_eligible_section": [_item("Unrelated old item", disposition="ignore")],
        }

        # Run process_brief_items twice: first to record items, second
        # (simulating a later day) so the rollup advances toward DORMANT.
        il.process_brief_items(store, sections, today - timedelta(days=5))
        store.advance_cycle(today - timedelta(days=4))
        store.advance_cycle(today - timedelta(days=3))
        il.process_brief_items(store, sections, today)

        filtered_counts = il.filter_suppressed_from_sections(store, sections, today)

        # The rollup (and the act_today escalation) must survive even though
        # they may be DORMANT/low-novelty — watchlist_intelligence is exempt.
        titles = [i["title"] for i in sections["watchlist_intelligence"]]
        self.assertIn(rollup["title"], titles)
        self.assertIn(escalation["title"], titles)
        self.assertNotIn("watchlist_intelligence", filtered_counts)


class TestWatchlistNewActivityCrossDayDedup(unittest.TestCase):
    """RB-DEFECT-2026-07-15: _compute_watchlist_intelligence's press-release
    check (`pr_hits[0]`) had no memory of what was already reported, so the
    same press release kept getting flagged "New Activity" for an entity
    day after day as long as it stayed in entity_alerts_cache.json.
    Confirmed live: Domino's/PAR Technology/Agilysys/Revel/SoundHound all
    showed the byte-identical news.google.com URL as "New Activity" on both
    2026-07-14 and 2026-07-15. Track the last URL surfaced per entity in
    watchlist_new_activity_seen.json so a genuinely new press release still
    counts as new, but a repeat doesn't."""

    def _alerts_cache(self, tmp_path, entity, title, url):
        cache_dir = Path(tmp_path)
        (cache_dir / "entity_alerts_cache.json").write_text(json.dumps({
            "entities": {
                entity: {"press_release_items": [{"title": title, "source": "PR Newswire", "url": url}]},
            },
        }), encoding="utf-8")
        return cache_dir

    def test_same_press_release_not_flagged_new_twice(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = self._alerts_cache(
                tmpdir, "Domino's", "News from Domino's Pizza", "https://example.com/dominos-1")
            with patch.object(db.core, "CACHE_DIR", cache_dir):
                report = {"relationship_signals": {"signals": []}}
                items_day1 = db._compute_watchlist_intelligence(report, {})
                items_day2 = db._compute_watchlist_intelligence(report, {})

        def _status_for(items, name):
            for i in items:
                if (i.get("extras") or {}).get("entity_name") == name:
                    return (i.get("extras") or {}).get("watchlist_status")
            return None

        self.assertEqual(_status_for(items_day1, "Domino's"), "New Activity")
        self.assertNotEqual(_status_for(items_day2, "Domino's"), "New Activity")

    def test_genuinely_new_press_release_still_counts_as_new(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = self._alerts_cache(
                tmpdir, "Domino's", "News from Domino's Pizza", "https://example.com/dominos-1")
            with patch.object(db.core, "CACHE_DIR", cache_dir):
                report = {"relationship_signals": {"signals": []}}
                db._compute_watchlist_intelligence(report, {})
                # A different press release arrives the next day.
                (cache_dir / "entity_alerts_cache.json").write_text(json.dumps({
                    "entities": {
                        "Domino's": {"press_release_items": [
                            {"title": "Domino's Q3 Earnings", "source": "PR Newswire",
                             "url": "https://example.com/dominos-2"},
                        ]},
                    },
                }), encoding="utf-8")
                items_day2 = db._compute_watchlist_intelligence(report, {})

        status = next(
            (i.get("extras", {}).get("watchlist_status") for i in items_day2
             if (i.get("extras") or {}).get("entity_name") == "Domino's"), None)
        self.assertEqual(status, "New Activity")


if __name__ == "__main__":
    unittest.main()
