#!/usr/bin/env python3
"""
test_entity_alerts_entity_id_backfill.py — RB-2026-09-08.

3-store unification Phase 3: entity_alerts_cache.json's write path
(refresh()) and its one-time backfill (backfill_entity_ids()) both
canonicalize the cache's raw string keys against a real entity_id via
ecosystem_intelligence.py::_resolve_entity_id_any_type(). Isolated against
a disposable ea.CACHE_PATH and a disposable ecosystem graph path (same
gotcha as every other test this session that touches
ei._read_graph()/core.ECOSYSTEM_INTELLIGENCE_PATH -- see battle_card.py's
own test file for the full history) -- never touches the real cache or the
real ecosystem_intelligence.json.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import entity_alerts as ea  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402


def _entity(entity_id, name, entity_type, aliases=None):
    return {"id": entity_id, "name": name, "entity_type": entity_type,
            "aliases": aliases or [], "attributes": {}, "sources": [],
            "confidence": {}, "domains": ["restaurants"]}


class _IsolatedFixtureMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp_root = Path(self._tmpdir.name)

        self._orig_cache_path = ea.CACHE_PATH
        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        ea.CACHE_PATH = tmp_root / "entity_alerts_cache.json"
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = tmp_root / "ecosystem_intelligence.json"

        graph = {
            "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-08",
            "entities": [
                _entity("brand-mcdonald-s", "McDonald's", "brand"),
                _entity("vendor-test-rival", "Test Rival", "vendor"),
                _entity("vendor-ncr", "NCR", "vendor", aliases=["NCR Voyix"]),
                _entity("vendor-ncr-voyix", "NCR Voyix", "vendor"),
            ],
            "relationships": [], "signals": [], "sources": [], "assessments": [],
            "user_relevance": [], "strategic_recommendations": [],
        }
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH.write_text(json.dumps(graph), encoding="utf-8")

    def tearDown(self):
        ea.CACHE_PATH = self._orig_cache_path
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        self._tmpdir.cleanup()


class TestBackfillEntityIds(_IsolatedFixtureMixin):
    def _write_cache(self, entities: dict) -> None:
        ea.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ea.CACHE_PATH.write_text(json.dumps({
            "_generated_at": "2026-09-01T00:00:00+00:00", "_rotation_index": 0, "entities": entities,
        }), encoding="utf-8")

    def test_resolves_a_known_name_and_leaves_other_fields_untouched(self):
        self._write_cache({"McDonald's": {"last_checked": "2026-09-01T00:00:00+00:00", "items": [{"title": "x"}]}})
        result = ea.backfill_entity_ids()
        self.assertEqual(result["resolved"], ["McDonald's"])
        cache = json.loads(ea.CACHE_PATH.read_text())
        entry = cache["entities"]["McDonald's"]
        self.assertEqual(entry["entity_id"], "brand-mcdonald-s")
        self.assertEqual(entry["last_checked"], "2026-09-01T00:00:00+00:00")
        self.assertEqual(entry["items"], [{"title": "x"}])

    def test_unresolvable_name_gets_entity_id_none_not_a_guess(self):
        self._write_cache({"Totally Fake Company XYZ": {"last_checked": "2026-09-01T00:00:00+00:00", "items": []}})
        result = ea.backfill_entity_ids()
        self.assertEqual(result["unresolved"], ["Totally Fake Company XYZ"])
        cache = json.loads(ea.CACHE_PATH.read_text())
        self.assertIsNone(cache["entities"]["Totally Fake Company XYZ"]["entity_id"])

    def test_genuinely_ambiguous_name_gets_entity_id_none(self):
        self._write_cache({"NCR Voyix": {"last_checked": "2026-09-01T00:00:00+00:00", "items": []}})
        ea.backfill_entity_ids()
        cache = json.loads(ea.CACHE_PATH.read_text())
        self.assertIsNone(cache["entities"]["NCR Voyix"]["entity_id"])

    def test_dry_run_writes_nothing(self):
        self._write_cache({"McDonald's": {"last_checked": "2026-09-01T00:00:00+00:00", "items": []}})
        before = ea.CACHE_PATH.read_text()
        ea.backfill_entity_ids(dry_run=True)
        after = ea.CACHE_PATH.read_text()
        self.assertEqual(before, after)

    def test_already_stamped_entry_is_skipped_idempotent(self):
        self._write_cache({"McDonald's": {"entity_id": "some-prior-value", "last_checked": "x", "items": []}})
        ea.backfill_entity_ids()
        cache = json.loads(ea.CACHE_PATH.read_text())
        self.assertEqual(cache["entities"]["McDonald's"]["entity_id"], "some-prior-value")


class TestRefreshStampsEntityId(_IsolatedFixtureMixin):
    def test_refresh_stamps_entity_id_for_a_resolvable_entity(self):
        import unittest.mock as mock
        with mock.patch.object(ea, "MANDATORY_ALL", ["McDonald's"]), \
             mock.patch.object(ea, "MANDATORY_RESTAURANT_BRANDS", []), \
             mock.patch.object(ea, "_fetch_entity", return_value=[]):
            result = ea.refresh(scan_all=True)
        self.assertEqual(result["entities"]["McDonald's"]["entity_id"], "brand-mcdonald-s")

    def test_refresh_stamps_none_for_an_unresolvable_entity(self):
        import unittest.mock as mock
        with mock.patch.object(ea, "MANDATORY_ALL", ["Totally Fake Company XYZ"]), \
             mock.patch.object(ea, "MANDATORY_RESTAURANT_BRANDS", []), \
             mock.patch.object(ea, "_fetch_entity", return_value=[]):
            result = ea.refresh(scan_all=True)
        self.assertIsNone(result["entities"]["Totally Fake Company XYZ"]["entity_id"])


if __name__ == "__main__":
    unittest.main()
