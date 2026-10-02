"""
test_technomic_watchlist_history.py

RB-2026-09-05, watchlist auto-expansion: technomic_watchlist_scan.py's
PROMOTED_PATH was overwritten every scan day with no memory of prior days
-- a brand could be "reported" for months with no way to tell "keeps
coming up" from "showed up once." HISTORY_PATH is the new append-only log
watchlist_promotion.py's repeated-appearance promotion bar reads. This
covers just the new append behavior -- run_scan()'s existing detection/
classification logic is unchanged and untested here.

Network calls (_fetch_press_releases) and graph signal-writing
(_match_signals_to_entities/_write_signal_to_graph) are mocked -- this is
about the history log, not press-release detection.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import rb_core as core  # noqa: E402
import technomic_watchlist_scan as tws  # noqa: E402
import entity_alerts as ea  # noqa: E402
import ecosystem_brief as eb  # noqa: E402


def _brand(id_, name, rank):
    return {"id": id_, "name": name, "entity_type": "brand", "aliases": [],
            "attributes": {"rank": rank}, "sources": [], "confidence": {}, "domains": ["restaurants"]}


class TestHistoryAppend(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self.graph_path = tmp / "ecosystem_intelligence.json"
        self.graph_path.write_text(json.dumps({
            "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-05",
            "entities": [_brand("brand-x", "Zzyzx Test Brand Co", 1)],
            "relationships": [], "signals": [], "sources": [], "assessments": [],
            "user_relevance": [], "strategic_recommendations": [],
        }), encoding="utf-8")

        self.history_path = tmp / "technomic_watchlist_history.jsonl"
        self.promoted_path = tmp / "technomic_watchlist_promoted.json"
        self.ea_cache_path = tmp / "entity_alerts_cache.json"
        self.rotation_path = tmp / "technomic_scan_rotation.json"

        self._patches = [
            patch.object(core, "ECOSYSTEM_INTELLIGENCE_PATH", self.graph_path),
            patch.object(tws, "HISTORY_PATH", self.history_path),
            patch.object(tws, "PROMOTED_PATH", self.promoted_path),
            patch.object(ea, "CACHE_PATH", self.ea_cache_path),
            patch.object(tws, "ROTATION_PATH", self.rotation_path),
            patch.object(ea, "_fetch_press_releases", return_value=[
                {"title": "Zzyzx Test Brand Co announces new funding round", "url": "https://example.com/1",
                 "pub_date": "2026-09-05T00:00:00Z", "source": "PR Newswire", "press_release": True},
            ]),
            patch.object(tws.time, "sleep", lambda *_a, **_k: None),
            # Real headline-text materiality classification is
            # ecosystem_brief.py's own concern, already covered by its own
            # tests -- mocked here to a fixed, known-material match so this
            # test is purely about HISTORY_PATH's append behavior, not
            # entangled with real classification logic.
            patch.object(tws.eb, "_match_signals_to_entities", return_value=[{
                "signal_class": "funding_event", "confidence": "high",
                "entity_id": "brand-x", "event_at": "2026-09-05",
                "intelligence_metadata": {"graph_mutation_eligibility": "eligible"},
            }]),
            patch.object(tws.eb, "_write_signal_to_graph", lambda *a, **k: None),
            patch.object(tws.eb, "_write_activation_assessment", lambda *a, **k: None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmpdir.cleanup()

    def test_material_finding_appends_a_history_row(self):
        tws.run_scan(date(2026, 9, 5), tier1_only=True, quiet=True)
        self.assertTrue(self.history_path.exists())
        rows = [json.loads(l) for l in self.history_path.read_text().splitlines() if l.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Zzyzx Test Brand Co")
        self.assertEqual(rows[0]["date"], "2026-09-05")
        self.assertEqual(rows[0]["tier"], "tier1")

    def test_history_is_append_only_across_runs(self):
        tws.run_scan(date(2026, 9, 5), tier1_only=True, quiet=True)
        tws.run_scan(date(2026, 9, 6), tier1_only=True, quiet=True)
        rows = [json.loads(l) for l in self.history_path.read_text().splitlines() if l.strip()]
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["date"] for r in rows], ["2026-09-05", "2026-09-06"])

    def test_same_day_rerun_does_not_duplicate_history_row(self):
        """existing_promoted's own same-day dedup (PROMOTED_PATH already
        merges rather than clobbers on a same-day double-run) must also
        keep HISTORY_PATH from getting a duplicate row for the same brand
        on the same day."""
        tws.run_scan(date(2026, 9, 5), tier1_only=True, quiet=True)
        tws.run_scan(date(2026, 9, 5), tier1_only=True, quiet=True)
        rows = [json.loads(l) for l in self.history_path.read_text().splitlines() if l.strip()]
        self.assertEqual(len(rows), 1)

    def test_dry_run_does_not_write_history(self):
        tws.run_scan(date(2026, 9, 5), tier1_only=True, quiet=True, dry_run=True)
        self.assertFalse(self.history_path.exists())

    def test_promoted_entry_carries_the_real_entity_id(self):
        """RB-2026-09-08, 3-store unification Phase 3: `entity` in
        run_scan()'s roster loop is already the real graph entity dict, so
        promoted.append() should use its own real "id" directly -- no
        name-based resolution, and no risk of it ever being wrong for a
        graph entity that's already correctly identified upstream."""
        tws.run_scan(date(2026, 9, 5), tier1_only=True, quiet=True)
        promoted = json.loads(self.promoted_path.read_text())
        self.assertEqual(promoted["entities"][0]["entity_id"], "brand-x")
        self.assertEqual(promoted["entities"][0]["name"], "Zzyzx Test Brand Co")


if __name__ == "__main__":
    unittest.main()
