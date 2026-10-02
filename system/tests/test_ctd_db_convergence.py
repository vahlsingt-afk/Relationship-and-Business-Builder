"""
test_ctd_db_convergence.py — RB 9.39 / Sprint E-3
Tests for cross-time convergence layer in connect_the_dots.

Sprint E-3 adds _compute_ctd_db_convergence() to daily_brief.py which queries
IntelligenceDB for entities and entity pairs that appear across multiple
intelligence items over 30 days. This is stronger signal than same-cycle
convergence because it spans multiple brief cycles and source types.

Test groups:
  CTDD1 (4):  find_convergences behavior — multi-source entities qualify,
              single-source entities excluded, item count thresholds,
              signal_types and sources present in result
  CTDD2 (4):  cross_entity_convergence + edge cases — entity pairs with
              shared items qualify, pairs below threshold excluded,
              empty DB returns empty list, suppressed items excluded

Note: _compute_ctd_db_convergence() is in daily_brief.py which is expensive
to import. These tests exercise the IntelligenceDB methods directly with
realistic data scenarios, verifying the query contract the helper relies on.
This tests the foundation — the daily_brief wiring is covered by integration.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import intelligence_db as idb
from intelligence_db import IntelligenceDB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_db() -> IntelligenceDB:
    """Open an IntelligenceDB backed by a temp file."""
    tmp = tempfile.mktemp(suffix=".db")
    db = IntelligenceDB(Path(tmp))
    db.open()
    return db


def _add(db: IntelligenceDB, title: str, source: str,
         entities: list[str], gathered_date: str | None = None,
         source_type: str = "web_scan",
         lifecycle: str = "new") -> str:
    """Add a minimal item with entity tags."""
    tags = [{"type": "entity", "value": e} for e in entities]
    tags.append({"type": "signal_type", "value": "general"})
    item_id = db.add_item(
        title=title,
        content=title,
        source_name=source,
        source_type=source_type,
        confidence="medium",
        tags=tags,
    )
    if lifecycle != "new":
        db.update_lifecycle(item_id, lifecycle)
    # Backdate by patching gathered_date if provided
    if gathered_date:
        db._db.execute(
            "UPDATE intelligence_items SET gathered_date = ? WHERE id = ?",
            (gathered_date, item_id),
        )
        db._db.commit()
    return item_id


# ---------------------------------------------------------------------------
# CTDD1 — find_convergences behavior
# ---------------------------------------------------------------------------

class TestCTDD1_FindConvergences(unittest.TestCase):
    """CTDD1: find_convergences returns the right entities under the right conditions."""

    def test_CTDD1a_multi_source_entity_qualifies(self):
        """Entity appearing in 2+ items across 2 distinct sources is returned."""
        db = _tmp_db()
        try:
            _add(db, "PAR Technology expands to Europe", "Restaurant Dive",
                 ["PAR Technology", "TASK Group"])
            _add(db, "PAR Technology Q3 revenue beat", "Restaurant Business Online",
                 ["PAR Technology"])
            convs = db.find_convergences(min_entity_hits=2, days=30)
            entities = [c["entity"] for c in convs]
            self.assertIn("PAR Technology", entities)
        finally:
            db.close()

    def test_CTDD1b_single_source_entity_still_qualifies_by_count(self):
        """Entity with 2+ items from same source qualifies on count (source filter is caller's job)."""
        db = _tmp_db()
        try:
            _add(db, "Toast raises Series D", "Restaurant Dive", ["Toast"])
            _add(db, "Toast announces kiosk product", "Restaurant Dive", ["Toast"])
            convs = db.find_convergences(min_entity_hits=2, days=30)
            entities = [c["entity"] for c in convs]
            # DB finds convergence on count; caller filters on source count
            self.assertIn("Toast", entities)
            toast_conv = next(c for c in convs if c["entity"] == "Toast")
            # sources list tells caller how many source_names are represented
            self.assertEqual(len(toast_conv["sources"]), 1)  # same source — caller can filter
        finally:
            db.close()

    def test_CTDD1c_entity_below_threshold_excluded(self):
        """Entity with only 1 item is not returned at min_entity_hits=2."""
        db = _tmp_db()
        try:
            _add(db, "Olo announces new integration", "Restaurant Dive", ["Olo"])
            convs = db.find_convergences(min_entity_hits=2, days=30)
            entities = [c["entity"] for c in convs]
            self.assertNotIn("Olo", entities)
        finally:
            db.close()

    def test_CTDD1d_result_includes_signal_types_and_sources(self):
        """Each convergence dict contains signal_types and sources fields."""
        db = _tmp_db()
        try:
            # Two items for the same entity across two sources
            item1 = _add(db, "Square acquires Lightspeed", "Restaurant Dive",
                         ["Square", "Lightspeed"])
            item2 = _add(db, "Square POS expansion update", "Nation's Restaurant News",
                         ["Square"])
            # Tag item1 with acquisition signal
            db.tag_item(item1, "signal_type", "acquisition")
            convs = db.find_convergences(min_entity_hits=2, days=30)
            square_conv = next((c for c in convs if c["entity"] == "Square"), None)
            self.assertIsNotNone(square_conv)
            self.assertIn("signal_types", square_conv)
            self.assertIn("sources", square_conv)
            self.assertIsInstance(square_conv["signal_types"], list)
            self.assertIsInstance(square_conv["sources"], list)
            self.assertEqual(len(square_conv["sources"]), 2)
        finally:
            db.close()


# ---------------------------------------------------------------------------
# CTDD2 — cross_entity_convergence + edge cases
# ---------------------------------------------------------------------------

class TestCTDD2_CrossEntityConvergence(unittest.TestCase):
    """CTDD2: cross_entity_convergence finds entity pairs and handles edge cases."""

    def test_CTDD2a_entity_pair_with_shared_items_qualifies(self):
        """Entity pair co-appearing in 2+ items is returned by cross_entity_convergence."""
        db = _tmp_db()
        try:
            _add(db, "McDonald's deploys PAR POS system", "Restaurant Dive",
                 ["McDonald's", "PAR Technology"])
            _add(db, "PAR Technology and McDonald's extend partnership", "QSR Magazine",
                 ["PAR Technology", "McDonald's"])
            pairs = db.cross_entity_convergence(days=30, min_shared_items=2)
            pair_entities = {
                frozenset([p["entity_a"], p["entity_b"]]) for p in pairs
            }
            expected = frozenset(["McDonald's", "PAR Technology"])
            self.assertIn(expected, pair_entities)
        finally:
            db.close()

    def test_CTDD2b_pair_below_threshold_excluded(self):
        """Entity pair with only 1 shared item is excluded at min_shared_items=2."""
        db = _tmp_db()
        try:
            _add(db, "Starbucks partners with DoorDash", "Restaurant Dive",
                 ["Starbucks", "DoorDash"])
            pairs = db.cross_entity_convergence(days=30, min_shared_items=2)
            pair_entities = {
                frozenset([p["entity_a"], p["entity_b"]]) for p in pairs
            }
            self.assertNotIn(frozenset(["Starbucks", "DoorDash"]), pair_entities)
        finally:
            db.close()

    def test_CTDD2c_empty_db_returns_empty_list(self):
        """find_convergences and cross_entity_convergence return [] on empty DB."""
        db = _tmp_db()
        try:
            self.assertEqual(db.find_convergences(), [])
            self.assertEqual(db.cross_entity_convergence(), [])
        finally:
            db.close()

    def test_CTDD2d_suppressed_items_excluded_from_convergence(self):
        """Suppressed items are not counted in convergence queries."""
        db = _tmp_db()
        try:
            item1 = _add(db, "HungerRush raises funding", "Restaurant Technology News",
                         ["HungerRush"])
            item2 = _add(db, "HungerRush CEO interview", "Restaurant Dive",
                         ["HungerRush"])
            # Before suppression: should appear in convergences
            convs_before = db.find_convergences(min_entity_hits=2, days=30)
            entities_before = [c["entity"] for c in convs_before]
            self.assertIn("HungerRush", entities_before)
            # Suppress both items
            db.suppress_item(item1)
            db.suppress_item(item2)
            # After suppression: should not appear
            convs_after = db.find_convergences(min_entity_hits=2, days=30)
            entities_after = [c["entity"] for c in convs_after]
            self.assertNotIn("HungerRush", entities_after)
        finally:
            db.close()


# ---------------------------------------------------------------------------
# CTDD3 — min_distinct_days filtering and entity-link-strength confidence
# (RB-DEFECT-2026-09-18, handoff acceptance test 10: "Unrelated restaurant
# articles do not create false entity convergences" -- and the specific
# Burger King/Firehouse Subs same-day-burst evidence)
# ---------------------------------------------------------------------------

class TestCTDD3_MinDistinctDaysAndConfidence(unittest.TestCase):
    def test_CTDD3a_default_behavior_unchanged_same_day_items_still_returned(self):
        """min_distinct_days defaults to 1 -- existing callers/tests that
        never asked for filtering keep seeing the raw signal, same as
        before this defect's fix (backward compatible)."""
        db = _tmp_db()
        try:
            _add(db, "Burger King same-day story A", "Feed A", ["Burger King"])
            _add(db, "Burger King same-day story B", "Feed B", ["Burger King"])
            convs = db.find_convergences(min_entity_hits=2, days=30)
            entities = [c["entity"] for c in convs]
            self.assertIn("Burger King", entities)
        finally:
            db.close()

    def test_CTDD3b_same_day_burst_excluded_when_min_distinct_days_enforced(self):
        """The exact confirmed-live bug: a same-day burst of many same-day
        items from many feeds must NOT qualify as a convergence once the
        caller asks for min_distinct_days=2 (what query_engine.py now
        passes, matching daily_brief.py/intelligence_assessment.py's own
        post-hoc filter)."""
        db = _tmp_db()
        try:
            for i in range(6):
                _add(db, f"Burger King same-day story {i}", f"Feed {i}", ["Burger King"])
            convs = db.find_convergences(min_entity_hits=2, days=30, min_distinct_days=2)
            entities = [c["entity"] for c in convs]
            self.assertNotIn("Burger King", entities)
        finally:
            db.close()

    def test_CTDD3c_genuine_multi_day_pattern_still_qualifies(self):
        """A real sustained pattern (spread across distinct days) is not
        collateral damage from the same-day-burst fix.

        Inserts both items first (while both still land on "today", so
        _generate_id's per-day counter can't collide), then backdates each
        afterward -- _add()'s own insert-then-backdate-immediately pattern
        would reuse the same id when called back-to-back for the same
        entity, since backdating one item away from "today" resets the
        counter the very next insert reads."""
        db = _tmp_db()
        try:
            id1 = _add(db, "Firehouse Subs story day 1", "Feed A", ["Firehouse Subs"])
            id2 = _add(db, "Firehouse Subs story day 2", "Feed B", ["Firehouse Subs"])
            for item_id, days_ago in ((id1, 5), (id2, 2)):
                db._db.execute(
                    "UPDATE intelligence_items SET gathered_date = ? WHERE id = ?",
                    ((date.today() - timedelta(days=days_ago)).isoformat(), item_id),
                )
            db._db.commit()
            convs = db.find_convergences(min_entity_hits=2, days=30, min_distinct_days=2)
            entities = [c["entity"] for c in convs]
            self.assertIn("Firehouse Subs", entities)
        finally:
            db.close()

    def test_CTDD3d_high_item_count_weak_link_gets_low_confidence_not_high(self):
        """Handoff requirement: a high article count must not create a
        high-confidence convergence when the entity links are weak (here:
        every item is same-day, same-source -- item_count is high, but
        source diversity and day-spread are both minimal)."""
        db = _tmp_db()
        try:
            for i in range(10):
                _add(db, f"Tim Hortons same-day item {i}", "Single Feed", ["Tim Hortons"])
            convs = db.find_convergences(min_entity_hits=2, days=30)
            conv = next(c for c in convs if c["entity"] == "Tim Hortons")
            self.assertEqual(conv["item_count"], 10)
            self.assertEqual(conv["confidence"], "low")

            # Contrast: genuine multi-source, multi-day pattern -> "high".
            # Same insert-first-then-backdate ordering as CTDD3c, for the
            # same id-collision reason.
            wendys_ids = [
                _add(db, f"Wendy's story {i}", f"Feed {i}", ["Wendy's"])
                for i in range(3)
            ]
            for item_id, days_ago in zip(wendys_ids, [9, 6, 3]):
                db._db.execute(
                    "UPDATE intelligence_items SET gathered_date = ? WHERE id = ?",
                    ((date.today() - timedelta(days=days_ago)).isoformat(), item_id),
                )
            db._db.commit()
            convs2 = db.find_convergences(min_entity_hits=2, days=30)
            wendys = next(c for c in convs2 if c["entity"] == "Wendy's")
            self.assertEqual(wendys["confidence"], "high")
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
