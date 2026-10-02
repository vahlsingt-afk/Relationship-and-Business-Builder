"""
test_intelligence_db.py — RB 9.38 / Sprint E-1
Tests for IntelligenceDB: SQLite-backed gathered intelligence store.

Test groups:
  IDB1 (6):  Schema & initialization — tables created, meta seeded,
             idempotent re-open, DB path resolves, constants complete
  IDB2 (6):  Write operations — add_item ID format, sequence, tag_item,
             tag_item_bulk, mark_communicated, update_lifecycle, suppress_item
  IDB3 (6):  Read/search — get_item, get_item_with_tags, search by entity,
             search by days, search by lifecycle, get_uncommunicated
  IDB4 (6):  Analysis — entity_summary counts, find_convergences,
             cross_entity_convergence, stats(), to_brief_item format
  IDB5 (6):  Edge cases — duplicate tag ignored, suppress increments counter,
             AND logic in search, missing ID returns None, relate_items,
             invalid lifecycle raises
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
# Test helpers
# ---------------------------------------------------------------------------

def _tmp_db() -> IntelligenceDB:
    """Open an in-memory IntelligenceDB (temp file so foreign keys work)."""
    tmp = tempfile.mktemp(suffix=".db")
    db = IntelligenceDB(Path(tmp))
    db.open()
    return db


def _add_sample(db: IntelligenceDB, **kwargs) -> str:
    """Add a minimal item, allow overrides via kwargs."""
    defaults = dict(
        title="PAR Technology acquires TASK Group",
        content="PAR Technology announced it will acquire TASK Group...",
        source_name="Restaurant Dive",
        source_type="web_scan",
        confidence="high",
        tags=[
            {"type": "entity", "value": "PAR Technology"},
            {"type": "entity", "value": "TASK Group"},
            {"type": "signal_type", "value": "acquisition"},
            {"type": "industry", "value": "restaurant-tech"},
        ],
    )
    defaults.update(kwargs)
    return db.add_item(**defaults)


# ---------------------------------------------------------------------------
# IDB1 — Schema & initialization
# ---------------------------------------------------------------------------

class TestIDB1_Schema(unittest.TestCase):
    """IDB1: Schema creation and meta seeding are correct and idempotent."""

    def setUp(self):
        self.db = _tmp_db()

    def tearDown(self):
        self.db.close()

    def test_IDB1a_tables_created(self):
        """All four tables exist after open()."""
        tables = {
            row[0]
            for row in self.db._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertIn("intelligence_items", tables)
        self.assertIn("intelligence_tags", tables)
        self.assertIn("intelligence_relations", tables)
        self.assertIn("meta", tables)

    def test_IDB1b_schema_version_seeded(self):
        """Meta table contains schema_version = SCHEMA_VERSION."""
        meta = self.db.meta()
        self.assertIn("schema_version", meta)
        self.assertEqual(int(meta["schema_version"]), idb.SCHEMA_VERSION)

    def test_IDB1c_created_at_seeded(self):
        """Meta table contains created_at timestamp."""
        meta = self.db.meta()
        self.assertIn("created_at", meta)
        self.assertTrue(meta["created_at"].startswith("20"))

    def test_IDB1d_idempotent_reopen(self):
        """Opening an already-initialised DB twice does not raise or duplicate meta."""
        # Re-open same path
        path = self.db._path
        self.db.close()
        db2 = IntelligenceDB(path)
        db2.open()
        meta = db2.meta()
        db2.close()
        # schema_version should appear exactly once
        self.assertEqual(meta.get("schema_version"), str(idb.SCHEMA_VERSION))

    def test_IDB1e_constants_non_empty(self):
        """TAG_TYPES, LIFECYCLE_STATES, CONFIDENCE_LEVELS, SOURCE_TYPES all populated."""
        self.assertGreater(len(idb.TAG_TYPES), 0)
        self.assertGreater(len(idb.LIFECYCLE_STATES), 0)
        self.assertGreater(len(idb.CONFIDENCE_LEVELS), 0)
        self.assertGreater(len(idb.SOURCE_TYPES), 0)

    def test_IDB1f_empty_stats(self):
        """stats() on an empty DB returns zero counts without error."""
        s = self.db.stats()
        self.assertEqual(s["total_items"], 0)
        self.assertEqual(s["items_today"], 0)
        self.assertEqual(s["uncommunicated_count"], 0)
        self.assertEqual(s["schema_version"], idb.SCHEMA_VERSION)


# ---------------------------------------------------------------------------
# IDB2 — Write operations
# ---------------------------------------------------------------------------

class TestIDB2_Writes(unittest.TestCase):
    """IDB2: add_item, tag operations, lifecycle updates, mark_communicated."""

    def setUp(self):
        self.db = _tmp_db()

    def tearDown(self):
        self.db.close()

    def test_IDB2a_add_item_returns_valid_id(self):
        """add_item returns an ID in INT-YYYY-MM-DD-NNN format."""
        item_id = _add_sample(self.db)
        today = date.today().isoformat()
        self.assertTrue(item_id.startswith(f"INT-{today}-"), item_id)
        self.assertRegex(item_id, r"INT-\d{4}-\d{2}-\d{2}-\d{3}")

    def test_IDB2b_id_sequence_increments(self):
        """Sequential adds on the same date produce incrementing IDs."""
        id1 = _add_sample(self.db, title="Item One")
        id2 = _add_sample(self.db, title="Item Two")
        seq1 = int(id1.split("-")[-1])
        seq2 = int(id2.split("-")[-1])
        self.assertEqual(seq2, seq1 + 1)

    def test_IDB2c_tag_item_single(self):
        """tag_item adds a single tag retrievable via get_item_with_tags."""
        item_id = _add_sample(self.db, tags=[])
        self.db.tag_item(item_id, "entity", "Olo")
        item = self.db.get_item_with_tags(item_id)
        tag_values = [t["value"] for t in item["tags"]]
        self.assertIn("Olo", tag_values)

    def test_IDB2d_tag_item_bulk_count(self):
        """tag_item_bulk returns count of tags applied."""
        item_id = _add_sample(self.db, tags=[])
        applied = self.db.tag_item_bulk(item_id, [
            {"type": "entity",      "value": "McDonald's"},
            {"type": "signal_type", "value": "competitive"},
            {"type": "industry",    "value": "QSR"},
        ])
        self.assertEqual(applied, 3)

    def test_IDB2e_mark_communicated_sets_date(self):
        """mark_communicated sets communicated_date on first call."""
        item_id = _add_sample(self.db)
        result = self.db.mark_communicated(item_id, "2026-06-01")
        self.assertTrue(result)
        item = self.db.get_item(item_id)
        self.assertEqual(item["communicated_date"], "2026-06-01")

    def test_IDB2f_update_lifecycle(self):
        """update_lifecycle changes the lifecycle_state field."""
        item_id = _add_sample(self.db)
        item_before = self.db.get_item(item_id)
        self.assertEqual(item_before["lifecycle_state"], "new")
        self.db.update_lifecycle(item_id, "dormant")
        item_after = self.db.get_item(item_id)
        self.assertEqual(item_after["lifecycle_state"], "dormant")

    def test_IDB2g_add_item_same_url_returns_existing_id_not_duplicate(self):
        """RB-DEFECT-2026-07-10c: a re-scan of the same article (same
        source_url) must not insert a second row -- confirmed live at scale
        (8,242 rows vs. 2,653 distinct URLs db-wide; one popular article
        duplicated 9x from repeated RSS re-scans), inflating every
        downstream signal-count/convergence computation."""
        url = "https://restaurantbusinessonline.com/caseys-pizza-hut"
        id1 = _add_sample(self.db, title="Casey's is likely stealing share from Pizza Hut", source_url=url)
        id2 = _add_sample(self.db, title="Casey's is likely stealing share from Pizza Hut", source_url=url)
        id3 = _add_sample(self.db, title="Casey's is likely stealing share from Pizza Hut", source_url=url)
        self.assertEqual(id1, id2)
        self.assertEqual(id2, id3)
        count = self.db._db.execute(
            "SELECT COUNT(*) FROM intelligence_items WHERE source_url = ?", (url,),
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_IDB2h_add_item_same_url_reapplies_new_tags(self):
        """A re-scan may carry a tag the first pass missed (e.g. an entity
        added to the classifier later) -- re-sighting must still apply it."""
        url = "https://example.com/gotab-fishbowl"
        item_id = _add_sample(self.db, title="GoTab Acquires Fishbowl", source_url=url, tags=[
            {"type": "entity", "value": "GoTab"},
        ])
        same_id = _add_sample(self.db, title="GoTab Acquires Fishbowl", source_url=url, tags=[
            {"type": "entity", "value": "Fishbowl"},
        ])
        self.assertEqual(item_id, same_id)
        item = self.db.get_item_with_tags(item_id)
        tag_values = {t["value"] for t in item["tags"]}
        self.assertIn("GoTab", tag_values)
        self.assertIn("Fishbowl", tag_values)

    def test_IDB2i_add_item_without_url_never_dedups(self):
        """Items with no source_url (e.g. manually-entered captures) have no
        stable identity to dedup on -- must always insert a new row."""
        id1 = _add_sample(self.db, title="Manual note one", source_url=None)
        id2 = _add_sample(self.db, title="Manual note two", source_url=None)
        self.assertNotEqual(id1, id2)

    def test_IDB2j_add_item_different_urls_never_dedup(self):
        """Different articles (different URLs) must never collide."""
        id1 = _add_sample(self.db, title="Article A", source_url="https://example.com/a")
        id2 = _add_sample(self.db, title="Article B", source_url="https://example.com/b")
        self.assertNotEqual(id1, id2)


# ---------------------------------------------------------------------------
# IDB3 — Read / search
# ---------------------------------------------------------------------------

class TestIDB3_Read(unittest.TestCase):
    """IDB3: Single-item reads and flexible search."""

    def setUp(self):
        self.db = _tmp_db()
        # Seed: PAR item (entity=PAR Technology, signal=acquisition)
        self.par_id = _add_sample(self.db)
        # Seed: Olo item (entity=Olo, signal=product-launch, monitoring state)
        self.olo_id = _add_sample(
            self.db,
            title="Olo launches Pay+",
            content="Olo announced Pay+ integration...",
            source_name="NRN",
            source_type="web_scan",
            confidence="medium",
            lifecycle_state="monitoring",
            tags=[
                {"type": "entity",      "value": "Olo"},
                {"type": "signal_type", "value": "product-launch"},
            ],
        )

    def tearDown(self):
        self.db.close()

    def test_IDB3a_get_item_returns_dict(self):
        """get_item returns a dict with required fields."""
        item = self.db.get_item(self.par_id)
        self.assertIsNotNone(item)
        for key in ("id", "title", "source_name", "confidence", "lifecycle_state"):
            self.assertIn(key, item)

    def test_IDB3b_get_item_with_tags_includes_tags(self):
        """get_item_with_tags includes tags list."""
        item = self.db.get_item_with_tags(self.par_id)
        self.assertIn("tags", item)
        self.assertIsInstance(item["tags"], list)
        tag_values = [t["value"] for t in item["tags"]]
        self.assertIn("PAR Technology", tag_values)

    def test_IDB3c_search_by_entity(self):
        """search(entity=...) returns only items tagged with that entity."""
        results = self.db.search(entity="PAR Technology")
        ids = [r["id"] for r in results]
        self.assertIn(self.par_id, ids)
        self.assertNotIn(self.olo_id, ids)

    def test_IDB3d_search_by_days(self):
        """search(days=7) returns items from the last week."""
        results = self.db.search(days=7)
        self.assertGreaterEqual(len(results), 2)

    def test_IDB3e_search_by_lifecycle_state(self):
        """search(lifecycle_state='monitoring') returns only monitoring items."""
        results = self.db.search(lifecycle_state="monitoring")
        for r in results:
            self.assertEqual(r["lifecycle_state"], "monitoring")
        ids = [r["id"] for r in results]
        self.assertIn(self.olo_id, ids)
        self.assertNotIn(self.par_id, ids)

    def test_IDB3f_get_uncommunicated_excludes_communicated(self):
        """get_uncommunicated excludes items already marked communicated."""
        self.db.mark_communicated(self.par_id)
        uncommunicated = self.db.get_uncommunicated()
        ids = [r["id"] for r in uncommunicated]
        self.assertNotIn(self.par_id, ids)
        self.assertIn(self.olo_id, ids)


# ---------------------------------------------------------------------------
# IDB4 — Analysis
# ---------------------------------------------------------------------------

class TestIDB4_Analysis(unittest.TestCase):
    """IDB4: entity_summary, find_convergences, cross_entity_convergence, stats."""

    def setUp(self):
        self.db = _tmp_db()
        # Two PAR Technology items
        self.par1 = _add_sample(self.db, title="PAR acquires TASK")
        self.par2 = _add_sample(
            self.db,
            title="PAR raises $50M",
            content="PAR Technology closed a $50M Series C...",
            source_name="NRN",
            source_type="web_scan",
            confidence="high",
            tags=[
                {"type": "entity",      "value": "PAR Technology"},
                {"type": "signal_type", "value": "funding"},
            ],
        )
        # One McDonald's + PAR co-mention
        self.joint = _add_sample(
            self.db,
            title="McDonald's selects PAR for kiosk program",
            content="McDonald's named PAR Technology as its kiosk partner...",
            source_name="QSR Magazine",
            source_type="web_scan",
            confidence="medium",
            tags=[
                {"type": "entity",      "value": "PAR Technology"},
                {"type": "entity",      "value": "McDonald's"},
                {"type": "signal_type", "value": "partnership"},
            ],
        )

    def tearDown(self):
        self.db.close()

    def test_IDB4a_entity_summary_counts(self):
        """entity_summary returns correct item_count and signal_types."""
        summary = self.db.entity_summary("PAR Technology")
        self.assertEqual(summary["entity"], "PAR Technology")
        self.assertEqual(summary["item_count"], 3)
        self.assertIn("acquisition", summary["signal_types"])
        self.assertIn("funding", summary["signal_types"])

    def test_IDB4b_find_convergences_detects_repeat_entity(self):
        """find_convergences finds PAR Technology with item_count >= 2."""
        convs = self.db.find_convergences(min_entity_hits=2, days=30)
        entities = [c["entity"] for c in convs]
        self.assertIn("PAR Technology", entities)

    def test_IDB4c_cross_entity_finds_pair(self):
        """cross_entity_convergence detects PAR Technology + McDonald's pair."""
        pairs = self.db.cross_entity_convergence(days=30)
        entity_pairs = [(p["entity_a"], p["entity_b"]) for p in pairs]
        # Pair is alphabetically ordered
        self.assertIn(("McDonald's", "PAR Technology"), entity_pairs)

    def test_IDB4g_find_convergences_distinct_days_same_day_burst(self):
        """RB-2026-08-25: all 3 setUp items for PAR Technology were added
        with no gathered_date override -- they all land on today, so
        distinct_days must be 1 despite item_count/source diversity being
        high enough to otherwise look "sustained." Confirmed live: entities
        with 300+ same-day items across many feeds were rendered as
        "sustained... not a one-off headline," which the actual evidence
        didn't support."""
        convs = self.db.find_convergences(min_entity_hits=2, days=30)
        par = next(c for c in convs if c["entity"] == "PAR Technology")
        self.assertEqual(par["distinct_days"], 1)

    def test_IDB4h_find_convergences_distinct_days_real_multi_day_pattern(self):
        """An entity with items genuinely spread across multiple calendar
        days gets distinct_days >= 2 -- the real "sustained pattern" case
        this field exists to distinguish from a same-day burst."""
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        _add_sample(self.db, title="Toast news day 1", source_name="Restaurant Dive",
                    gathered_date=yesterday,
                    tags=[{"type": "entity", "value": "Toast"}])
        _add_sample(self.db, title="Toast news day 2", source_name="NRN",
                    gathered_date=today,
                    tags=[{"type": "entity", "value": "Toast"}])
        convs = self.db.find_convergences(min_entity_hits=2, days=30)
        toast = next(c for c in convs if c["entity"] == "Toast")
        self.assertEqual(toast["distinct_days"], 2)

    def test_IDB4i_cross_entity_convergence_reports_distinct_days(self):
        """cross_entity_convergence's pairs also carry distinct_days -- the
        setUp pairing (PAR Technology + McDonald's) has one shared item, all
        added today, so distinct_days is 1."""
        pairs = self.db.cross_entity_convergence(days=30)
        pair = next(p for p in pairs if {p["entity_a"], p["entity_b"]} == {"PAR Technology", "McDonald's"})
        self.assertEqual(pair["distinct_days"], 1)

    def test_IDB4d_stats_returns_counts(self):
        """stats() returns non-zero total_items after seeding."""
        s = self.db.stats()
        self.assertEqual(s["total_items"], 3)
        self.assertIn("web_scan", s["items_by_source_type"])
        self.assertIn("high", s["items_by_confidence"])

    def test_IDB4e_to_brief_item_format(self):
        """to_brief_item converts a DB item to brief section format."""
        item = self.db.get_item(self.par1)
        brief_item = self.db.to_brief_item(item)
        self.assertIn("title", brief_item)
        self.assertIn("body", brief_item)
        self.assertIn("source", brief_item)
        self.assertIn("priority", brief_item)
        self.assertIn("extras", brief_item)
        self.assertIn("intel_id", brief_item["extras"])
        self.assertIn("confidence", brief_item["extras"])
        self.assertIn(brief_item["priority"], ("act_today", "monitor", "ignore"))

    def test_IDB4f_entity_summary_empty_for_unknown(self):
        """entity_summary returns item_count=0 for an entity with no items."""
        summary = self.db.entity_summary("Completely Unknown Entity XYZ", days=90)
        self.assertEqual(summary["item_count"], 0)
        self.assertEqual(summary["items"], [])


# ---------------------------------------------------------------------------
# IDB5 — Edge cases
# ---------------------------------------------------------------------------

class TestIDB5_EdgeCases(unittest.TestCase):
    """IDB5: Duplicate handling, suppression counter, AND logic, error paths."""

    def setUp(self):
        self.db = _tmp_db()

    def tearDown(self):
        self.db.close()

    def test_IDB5a_duplicate_tag_silently_ignored(self):
        """Adding the same tag twice does not raise and produces one tag row."""
        item_id = _add_sample(self.db, tags=[])
        self.db.tag_item(item_id, "entity", "PAR Technology")
        self.db.tag_item(item_id, "entity", "PAR Technology")  # duplicate
        item = self.db.get_item_with_tags(item_id)
        par_tags = [t for t in item["tags"] if t["value"] == "PAR Technology"]
        self.assertEqual(len(par_tags), 1, "Duplicate tag should appear only once")

    def test_IDB5b_suppress_increments_counter(self):
        """suppress_item increments suppression_count each call."""
        item_id = _add_sample(self.db)
        self.db.suppress_item(item_id)
        item = self.db.get_item(item_id)
        self.assertEqual(item["lifecycle_state"], "suppressed")
        self.assertEqual(item["suppression_count"], 1)

    def test_IDB5c_search_and_logic_multiple_filters(self):
        """search() with entity + confidence filters returns intersection only."""
        _add_sample(self.db, title="High confidence PAR item", confidence="high",
                    tags=[{"type": "entity", "value": "PAR Technology"}])
        _add_sample(self.db, title="Low confidence PAR item", confidence="low",
                    tags=[{"type": "entity", "value": "PAR Technology"}])
        _add_sample(self.db, title="High confidence Olo item", confidence="high",
                    tags=[{"type": "entity", "value": "Olo"}])

        results = self.db.search(entity="PAR Technology", confidence="high")
        self.assertTrue(all(r["confidence"] == "high" for r in results))
        titles = [r["title"] for r in results]
        self.assertIn("High confidence PAR item", titles)
        self.assertNotIn("Low confidence PAR item", titles)
        self.assertNotIn("High confidence Olo item", titles)

    def test_IDB5d_get_item_returns_none_for_missing_id(self):
        """get_item returns None for a non-existent ID."""
        result = self.db.get_item("INT-9999-99-99-999")
        self.assertIsNone(result)

    def test_IDB5e_relate_items_stored_and_retrievable(self):
        """relate_items creates a retrievable relation between two items."""
        id1 = _add_sample(self.db, title="Item A")
        id2 = _add_sample(self.db, title="Item B")
        self.db.relate_items(id1, id2, "follow_up")
        relations = self.db.get_relations(id1)
        self.assertTrue(any(
            r["to_id"] == id2 and r["relation_type"] == "follow_up"
            for r in relations
        ), f"Expected follow_up relation to {id2} in {relations}")

    def test_IDB5f_invalid_lifecycle_raises(self):
        """update_lifecycle raises ValueError for an invalid state."""
        item_id = _add_sample(self.db)
        with self.assertRaises(ValueError):
            self.db.update_lifecycle(item_id, "totally_invalid_state")


if __name__ == "__main__":
    unittest.main()
