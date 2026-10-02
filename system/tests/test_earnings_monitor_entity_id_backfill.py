#!/usr/bin/env python3
"""
test_earnings_monitor_entity_id_backfill.py — RB-2026-09-08.

3-store unification Phase 3: market_signals_earnings.jsonl's write path
(_build_row(), via _resolve_company_entity_id()) and its one-time backfill
(backfill_entity_ids_in_market_signals()) both canonicalize the "company"
string against a real entity_id.

IMPORTANT isolation note: _resolve_company_entity_id() reads
ecosystem_intelligence.json through _graph_for_entity_resolution(), an
lru_cache-memoized function (process-lifetime cache, by design -- see that
function's own docstring). A bare module-level cache would let one test's
real-data read silently leak into a later, differently-isolated test
depending on file execution order within the same pytest process (this
exact risk is why it's an lru_cache, not a bare global) -- every test here
calls .cache_clear() in setUp AND tearDown so neither this file's own
isolation nor test_earnings_monitor.py's (unisolated, real-data) EM5 tests
can contaminate each other regardless of collection order.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import earnings_monitor as em  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402


def _entity(entity_id, name, entity_type, aliases=None):
    return {"id": entity_id, "name": name, "entity_type": entity_type,
            "aliases": aliases or [], "attributes": {}, "sources": [],
            "confidence": {}, "domains": ["restaurants"]}


class _IsolatedFixtureMixin(unittest.TestCase):
    def setUp(self):
        em._graph_for_entity_resolution.cache_clear()
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp_root = Path(self._tmpdir.name)

        self._orig_output_path = em.OUTPUT_PATH
        self._orig_snapshots_dir = em.SNAPSHOTS_DIR
        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        em.OUTPUT_PATH = tmp_root / "market_signals_earnings.jsonl"
        em.SNAPSHOTS_DIR = tmp_root / "_snapshots"
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = tmp_root / "ecosystem_intelligence.json"

        graph = {
            "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-08",
            "entities": [
                _entity("brand-starbucks", "Starbucks", "brand"),
                _entity("vendor-toast", "Toast", "vendor"),
                _entity("vendor-ncr", "NCR", "vendor", aliases=["NCR Voyix"]),
                _entity("vendor-ncr-voyix", "NCR Voyix", "vendor"),
            ],
            "relationships": [], "signals": [], "sources": [], "assessments": [],
            "user_relevance": [], "strategic_recommendations": [],
        }
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH.write_text(json.dumps(graph), encoding="utf-8")

    def tearDown(self):
        em._graph_for_entity_resolution.cache_clear()
        em.OUTPUT_PATH = self._orig_output_path
        em.SNAPSHOTS_DIR = self._orig_snapshots_dir
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        self._tmpdir.cleanup()


class TestStripCorporateSuffix(unittest.TestCase):
    """Pure function, no I/O -- no isolation fixture needed."""

    def test_strips_a_single_suffix(self):
        self.assertEqual(em._strip_corporate_suffix("Starbucks Corp"), "Starbucks")

    def test_strips_a_comma_suffix(self):
        self.assertEqual(em._strip_corporate_suffix("BJ's Restaurants, Inc."), "BJ's Restaurants")

    def test_leaves_a_name_with_no_suffix_unchanged(self):
        self.assertEqual(em._strip_corporate_suffix("Toast"), "Toast")

    def test_never_strips_a_category_word(self):
        """Real, confirmed case: "Domino's Pizza Inc" strips only "Inc",
        never "Pizza" -- that's a category descriptor, not legal
        boilerplate, and stripping it would risk a false match."""
        self.assertEqual(em._strip_corporate_suffix("Domino's Pizza Inc"), "Domino's Pizza")


class TestResolveCompanyEntityId(_IsolatedFixtureMixin):
    def test_resolves_an_exact_match(self):
        self.assertEqual(em._resolve_company_entity_id("Toast"), "vendor-toast")

    def test_resolves_via_corporate_suffix_fallback(self):
        self.assertEqual(em._resolve_company_entity_id("Starbucks Corp"), "brand-starbucks")

    def test_returns_none_for_genuinely_unresolvable_name(self):
        self.assertIsNone(em._resolve_company_entity_id("Totally Fake Co, LLC"))

    def test_returns_none_for_genuine_ambiguity_even_after_stripping(self):
        self.assertIsNone(em._resolve_company_entity_id("NCR Voyix"))


class TestBuildRowStampsEntityId(_IsolatedFixtureMixin):
    def _row(self, company_name: str) -> dict:
        return em._build_row(
            company={"name": company_name, "ticker": "TST", "strategic_relevance": "medium", "side": "vendor_supply", "category": "pos"},
            title="Test title", url="https://example.com/1", published_at="2026-09-08",
            source_name="Test Source", source_type="sec_edgar_8k", sig_type="earnings_release",
        )

    def test_build_row_stamps_a_resolved_entity_id(self):
        self.assertEqual(self._row("Toast")["entity_id"], "vendor-toast")

    def test_build_row_stamps_none_for_unresolvable_name(self):
        self.assertIsNone(self._row("Totally Fake Co")["entity_id"])


class TestBackfillEntityIds(_IsolatedFixtureMixin):
    def _write_output(self, rows: list[dict]) -> None:
        em.OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        em.OUTPUT_PATH.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8",
        )

    def test_resolves_and_preserves_other_fields(self):
        self._write_output([{"company": "Toast", "title": "x", "url": "https://example.com/1"}])
        result = em.backfill_entity_ids_in_market_signals()
        self.assertEqual(result["updated"], ["Toast"])
        rows = [json.loads(l) for l in em.OUTPUT_PATH.read_text().splitlines()]
        self.assertEqual(rows[0]["entity_id"], "vendor-toast")
        self.assertEqual(rows[0]["title"], "x")
        self.assertEqual(rows[0]["url"], "https://example.com/1")

    def test_resolves_multiple_rows_for_the_same_company_once(self):
        self._write_output([
            {"company": "Toast", "title": "a", "url": "https://example.com/1"},
            {"company": "Toast", "title": "b", "url": "https://example.com/2"},
        ])
        em.backfill_entity_ids_in_market_signals()
        rows = [json.loads(l) for l in em.OUTPUT_PATH.read_text().splitlines()]
        self.assertEqual(rows[0]["entity_id"], "vendor-toast")
        self.assertEqual(rows[1]["entity_id"], "vendor-toast")

    def test_unresolvable_gets_entity_id_none(self):
        self._write_output([{"company": "Totally Fake Co", "title": "x", "url": "https://example.com/1"}])
        result = em.backfill_entity_ids_in_market_signals()
        self.assertEqual(result["unresolved"], ["Totally Fake Co"])
        rows = [json.loads(l) for l in em.OUTPUT_PATH.read_text().splitlines()]
        self.assertIsNone(rows[0]["entity_id"])

    def test_already_stamped_row_is_skipped(self):
        self._write_output([{"company": "Toast", "entity_id": "some-prior-value", "title": "x", "url": "https://example.com/1"}])
        result = em.backfill_entity_ids_in_market_signals()
        self.assertEqual(result["checked"], 0)
        rows = [json.loads(l) for l in em.OUTPUT_PATH.read_text().splitlines()]
        self.assertEqual(rows[0]["entity_id"], "some-prior-value")

    def test_dry_run_writes_nothing(self):
        self._write_output([{"company": "Toast", "title": "x", "url": "https://example.com/1"}])
        before = em.OUTPUT_PATH.read_text()
        em.backfill_entity_ids_in_market_signals(dry_run=True)
        after = em.OUTPUT_PATH.read_text()
        self.assertEqual(before, after)

    def test_writes_a_snapshot_backup_before_mutating(self):
        self._write_output([{"company": "Toast", "title": "x", "url": "https://example.com/1"}])
        em.backfill_entity_ids_in_market_signals()
        backups = list(em.SNAPSHOTS_DIR.glob("market_signals_earnings.pre-entity-id-backfill-*.jsonl"))
        self.assertEqual(len(backups), 1)
        self.assertIn('"entity_id"', em.OUTPUT_PATH.read_text())
        self.assertNotIn('"entity_id"', backups[0].read_text())


if __name__ == "__main__":
    unittest.main()
