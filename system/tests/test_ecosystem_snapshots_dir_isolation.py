#!/usr/bin/env python3
"""
test_ecosystem_snapshots_dir_isolation.py — RB-DEFECT (2026-09-27).

Regression coverage for the SNAPSHOTS_DIR leak: ecosystem_intelligence.py's
_write_graph() reads core.ECOSYSTEM_INTELLIGENCE_PATH (isolated per-test by
this file, deliberately the ONLY thing it isolates) but copies the pre-write
snapshot to the separate core.SNAPSHOTS_DIR constant, which used to go
unisolated in 29 test files and leaked thousands of tiny test-fixture
snapshots into the real system/_snapshots/ directory. The default isolation
now lives in conftest.py's autouse `_isolate_ecosystem_snapshots_dir`
fixture -- this test does NOT repeat that patch itself, so a regression in
the conftest fixture (or its removal) would show up here as a new file
appearing under the real directory.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import ecosystem_intelligence as ei  # noqa: E402

REAL_SNAPSHOTS_DIR = Path(__file__).resolve().parents[1] / "_snapshots"


def _graph(entity_id: str) -> dict:
    return {
        "version": 1,
        "contract": "rb_ecosystem_intelligence_v1",
        "last_updated": "2026-09-27",
        "domain_packs": ["restaurants"],
        "entities": [
            {"id": entity_id, "name": entity_id, "entity_type": "brand", "status": "active",
             "aliases": [], "attributes": {}, "sources": [],
             "confidence": {"level": "high"}, "domains": ["restaurants"]}
        ],
        "relationships": [], "signals": [], "assessments": [], "sources": [],
        "user_relevance": [], "strategic_recommendations": [],
    }


class EcosystemSnapshotsDirIsolationTest(unittest.TestCase):
    """Exercises a real _write_graph() snapshot-copy without patching
    SNAPSHOTS_DIR itself, to prove the conftest-level default isolation is
    what's protecting the real directory -- not a test-local patch."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="rb_snapshots_isolation_test_")
        self._graph_path = Path(self._tmpdir.name) / "ecosystem_intelligence.json"
        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._graph_path

    def tearDown(self):
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        self._tmpdir.cleanup()

    def test_write_graph_snapshot_never_touches_real_snapshots_dir(self):
        # Sanity check: the conftest autouse fixture must actually be active
        # for this test to mean anything -- if SNAPSHOTS_DIR ever points at
        # the real directory here, the isolation itself is broken.
        self.assertNotEqual(
            Path(ei.core.SNAPSHOTS_DIR).resolve(), REAL_SNAPSHOTS_DIR.resolve(),
            "core.SNAPSHOTS_DIR is pointed at the real directory -- the "
            "conftest autouse isolation fixture is missing or broken.",
        )

        real_before = set(REAL_SNAPSHOTS_DIR.glob("ecosystem_intelligence.pre-write-*.json")) \
            if REAL_SNAPSHOTS_DIR.exists() else set()
        # The isolated SNAPSHOTS_DIR is a shared per-SESSION tmp directory
        # (same convention as conftest.py's other _RB_TEST_*_PATH runtime
        # files) -- other tests earlier in this run may have already written
        # into it, so track new arrivals rather than assuming it starts empty.
        isolated_dir = Path(ei.core.SNAPSHOTS_DIR)
        isolated_before = set(isolated_dir.glob("ecosystem_intelligence.pre-write-*.json")) \
            if isolated_dir.exists() else set()

        # First write creates the graph file (no snapshot: nothing to copy
        # yet). Second write is the one that exercises the snapshot-copy
        # branch in _write_graph().
        ei._write_graph(_graph("brand-test-one"))
        ei._write_graph(_graph("brand-test-two"))

        real_after = set(REAL_SNAPSHOTS_DIR.glob("ecosystem_intelligence.pre-write-*.json")) \
            if REAL_SNAPSHOTS_DIR.exists() else set()
        self.assertEqual(
            real_before, real_after,
            "a new file appeared under the REAL system/_snapshots/ directory "
            "during an isolated _write_graph() call -- the SNAPSHOTS_DIR "
            "leak has regressed.",
        )

        # And confirm the snapshot mechanism actually ran (into wherever
        # SNAPSHOTS_DIR is currently isolated to), so the assertion above is
        # proving real isolation and not just a no-op.
        isolated_after = set(isolated_dir.glob("ecosystem_intelligence.pre-write-*.json"))
        self.assertEqual(len(isolated_after - isolated_before), 1)

    def test_rapid_successive_writes_never_collide_on_snapshot_filename(self):
        """RB defect 2026-10-08: the snapshot tag used to be second-
        resolution (strftime %Y%m%d-%H%M%S) -- two _write_graph() calls
        landing in the same wall-clock second produced the identical
        filename, so the second shutil.copy2() silently overwrote the
        first snapshot instead of adding a new one. Confirmed live: this is
        exactly what made this test file order-dependent in the full suite
        (another test's snapshot, written in the same second, occupied the
        filename this test's own snapshot needed). Deterministic here --
        write enough times in a tight loop that a second-resolution bug
        would collide essentially every run, regardless of what else ran
        before it."""
        isolated_dir = Path(ei.core.SNAPSHOTS_DIR)
        before = set(isolated_dir.glob("ecosystem_intelligence.pre-write-*.json")) \
            if isolated_dir.exists() else set()

        WRITES = 20
        ei._write_graph(_graph("brand-collision-seed"))  # creates the file; no snapshot yet
        for i in range(WRITES):
            ei._write_graph(_graph(f"brand-collision-{i}"))

        after = set(isolated_dir.glob("ecosystem_intelligence.pre-write-*.json"))
        self.assertEqual(
            len(after - before), WRITES,
            "two rapid writes produced the same snapshot filename -- the "
            "microsecond-resolution tag fix has regressed.",
        )


if __name__ == "__main__":
    unittest.main()
