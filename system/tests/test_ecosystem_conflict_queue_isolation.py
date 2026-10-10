#!/usr/bin/env python3
"""
test_ecosystem_conflict_queue_isolation.py — RB-DEFECT (2026-10-09).

Regression coverage for the CONFLICT_QUEUE_PATH leak: ecosystem_
intelligence.py's _write_conflict_record() reads core.CONFLICT_QUEUE_PATH --
a SEPARATE module-level constant from core.ECOSYSTEM_INTELLIGENCE_PATH, the
only path test_tech_stack_relationship_promotion.py's _IsolatedGraphMixin
isolates. Confirmed live: every pytest run through that fixture's
resolve_and_upsert_relationship() calls wrote a synthetic brand-blaze-pizza/
vendor-oracle "existing claim" conflict record into the REAL
system/inbox/ecosystem/conflict_queue.jsonl (65 polluted entries accumulated
this way before cleanup) -- the exact same shape of leak as the SNAPSHOTS_DIR
defect (see test_ecosystem_snapshots_dir_isolation.py). The default isolation
now lives in conftest.py's autouse `_isolate_ecosystem_conflict_queue`
fixture -- this test does NOT repeat that patch itself (beyond isolating the
graph, which is unrelated to the leak being tested), so a regression in the
conftest fixture would show up here as a new entry appearing in the real
file.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import ecosystem_intelligence as ei  # noqa: E402

REAL_CONFLICT_QUEUE_PATH = (
    Path(__file__).resolve().parents[1] / "inbox" / "ecosystem" / "conflict_queue.jsonl"
)


def _empty_graph() -> dict:
    return {
        "version": 1,
        "contract": "rb_ecosystem_intelligence_v1",
        "last_updated": "2026-10-09",
        "domain_packs": ["restaurants"],
        "entities": [], "relationships": [], "signals": [], "assessments": [],
        "sources": [], "user_relevance": [], "strategic_recommendations": [],
    }


class EcosystemConflictQueueIsolationTest(unittest.TestCase):
    """Exercises a real resolve_and_upsert_relationship() conflict write
    without patching CONFLICT_QUEUE_PATH itself, to prove the conftest-level
    default isolation is what's protecting the real file -- not a test-local
    patch (deliberately mirroring _IsolatedGraphMixin's own shape: isolate
    the graph, leave the conflict queue to the conftest default)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="rb_conflict_queue_isolation_test_")
        self._graph_path = Path(self._tmpdir.name) / "ecosystem_intelligence.json"
        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._graph_path

    def tearDown(self):
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        self._tmpdir.cleanup()

    def test_resolve_and_upsert_conflict_write_never_touches_real_queue(self):
        # Sanity check: the conftest autouse fixture must actually be active
        # for this test to mean anything.
        self.assertNotEqual(
            Path(ei.core.CONFLICT_QUEUE_PATH).resolve(), REAL_CONFLICT_QUEUE_PATH.resolve(),
            "core.CONFLICT_QUEUE_PATH is pointed at the real file -- the "
            "conftest autouse isolation fixture is missing or broken.",
        )

        real_before = REAL_CONFLICT_QUEUE_PATH.read_text().splitlines() \
            if REAL_CONFLICT_QUEUE_PATH.exists() else []

        isolated_path = Path(ei.core.CONFLICT_QUEUE_PATH)
        isolated_before = isolated_path.read_text().splitlines() \
            if isolated_path.exists() else []

        graph = _empty_graph()
        graph["entities"].extend([
            {"id": "brand-isolation-test", "name": "Isolation Test Brand"},
            {"id": "vendor-a", "name": "Vendor A"},
            {"id": "vendor-b", "name": "Vendor B"},
        ])
        existing = {
            "id": "rel-isolation-test-pos-vendor-a",
            "relationship_type": "uses_vendor_for_category",
            "from_entity_id": "brand-isolation-test", "to_entity_id": "vendor-a",
            "category": "pos", "status": "evaluating",
            "confidence": {"level": "medium", "score": 0.5}, "sources": [],
        }
        graph["relationships"].append(existing)
        incoming = {
            "id": "rel-isolation-test-pos-vendor-b",
            "relationship_type": "uses_vendor_for_category",
            "from_entity_id": "brand-isolation-test", "to_entity_id": "vendor-b",
            "category": "pos", "status": "active",
            "confidence": {"level": "high", "score": 0.9}, "sources": [],
        }

        outcome = ei.resolve_and_upsert_relationship(graph, incoming)
        self.assertEqual(outcome["conflict"]["resolution"], "auto_superseded")

        real_after = REAL_CONFLICT_QUEUE_PATH.read_text().splitlines() \
            if REAL_CONFLICT_QUEUE_PATH.exists() else []
        self.assertEqual(
            real_before, real_after,
            "a new line appeared in the REAL "
            "system/inbox/ecosystem/conflict_queue.jsonl during an isolated "
            "resolve_and_upsert_relationship() call -- the CONFLICT_QUEUE_PATH "
            "leak has regressed.",
        )

        # And confirm the write mechanism actually ran (into wherever
        # CONFLICT_QUEUE_PATH is currently isolated to), so the assertion
        # above is proving real isolation and not just a no-op.
        isolated_after = isolated_path.read_text().splitlines()
        new_lines = [ln for ln in isolated_after if ln not in isolated_before]
        self.assertEqual(len(new_lines), 1)
        record = json.loads(new_lines[0])
        self.assertEqual(record["brand_id"], "brand-isolation-test")
        self.assertEqual(record["resolution"], "auto_superseded")


if __name__ == "__main__":
    unittest.main()
