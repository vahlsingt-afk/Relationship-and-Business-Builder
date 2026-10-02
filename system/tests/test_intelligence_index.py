#!/usr/bin/env python3
"""
test_intelligence_index.py — RB-2026-08-27.

Isolated against a disposable index/log file pair (monkeypatched module
paths), never the real system/intelligence_index.json -- this module's own
INDEX_PATH/UPDATE_LOG_PATH are plain module-level Path constants (same
constraint as account_research_common.py), so isolation means swapping
those constants for the duration of each test, not injecting a root param.
"""
from __future__ import annotations

import shutil
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import intelligence_index as ix  # noqa: E402


class TestApostropheNormalization(unittest.TestCase):
    """The real bug found while building this: itriage._tokens() keeps an
    apostrophe inline ("mcdonald's"), but real filenames/aliases drop it
    entirely ("mcdonalds") -- without normalization "McDonald's" would
    never match its own real files."""

    def test_normalize_strips_straight_apostrophe(self):
        self.assertEqual(ix._normalize_for_tokens("McDonald's"), "McDonalds")

    def test_normalize_strips_curly_apostrophe(self):
        self.assertEqual(ix._normalize_for_tokens("McDonald’s"), "McDonalds")

    def test_normalize_leaves_plain_names_unaffected(self):
        self.assertEqual(ix._normalize_for_tokens("Cafe Rio"), "Cafe Rio")


class TestIndexAndLogSplit(unittest.TestCase):
    """Real filesystem I/O against a disposable temp dir -- never the real
    system/intelligence_index.json."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="rb-intel-index-test-")
        self._orig_index_path = ix.INDEX_PATH
        self._orig_log_path = ix.UPDATE_LOG_PATH
        ix.INDEX_PATH = Path(self._tmpdir) / "intelligence_index.json"
        ix.UPDATE_LOG_PATH = Path(self._tmpdir) / "intelligence_index_updates.jsonl"

    def tearDown(self):
        ix.INDEX_PATH = self._orig_index_path
        ix.UPDATE_LOG_PATH = self._orig_log_path
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_register_document_creates_findable_entry(self):
        ix.register_document(
            "McDonald's", "account_research_brief", "Background Brief: mcdonald-s",
            "system/account_research/accounts/mcdonald-s/briefs/current/Background_Brief.md",
            source_system="account_research", created_at="2026-08-27",
        )
        matches = ix.find("McDonald's")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["resource_type"], "account_research_brief")

    def test_registering_same_path_twice_upserts_not_duplicates(self):
        path = "system/account_research/accounts/cafe-rio/briefs/current/Background_Brief.md"
        ix.register_document("Cafe Rio", "account_research_brief", "v1", path, source_system="account_research")
        ix.register_document("Cafe Rio", "account_research_brief", "v1 refreshed", path, source_system="account_research")
        matches = ix.find("Cafe Rio")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["title"], "v1 refreshed")

    def test_log_update_does_not_create_an_index_entry(self):
        """The core index/log distinction Todd asked for: an update to
        existing intelligence must NOT get a new index entry."""
        path = "system/account_intelligence/2026-08-03-mcdonalds-genius-account-plan.md"
        ix.register_document("McDonald's", "account_intelligence_doc", "plan", path, source_system="account_intelligence")
        before = ix.find("McDonald's")
        ix.log_update("McDonald's", path, resource_type="account_intelligence_doc", note="revised probability table")
        after = ix.find("McDonald's")
        self.assertEqual(len(before), len(after), "log_update must not add an index entry")

    def test_log_update_is_append_only_and_readable(self):
        ix.log_update("Cafe Rio", "some/path.md", resource_type="account_intelligence_doc", note="first update")
        ix.log_update("Cafe Rio", "some/path.md", resource_type="account_intelligence_doc", note="second update")
        lines = ix.UPDATE_LOG_PATH.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(lines), 2)

    def test_find_no_match_returns_empty(self):
        ix.register_document("Cafe Rio", "account_research_record", "x", "some/path", source_system="account_research")
        self.assertEqual(ix.find("Some Totally Unrelated Brand"), [])

    def test_exact_name_ranks_before_partial_token_matches(self):
        ix.register_document("Golden Corral", "ecosystem_brand", "Golden Corral", "graph#golden-corral", source_system="ecosystem")
        ix.register_document("Golden Chick", "ecosystem_brand", "Golden Chick", "graph#golden-chick", source_system="ecosystem")
        ix.register_document("Chicken Express", "ecosystem_brand", "Chicken Express", "graph#chicken-express", source_system="ecosystem")

        matches = ix.find("Golden Chick")

        self.assertEqual(matches[0]["entity"], "Golden Chick")

    def test_create_account_intelligence_doc_writes_file_and_registers(self):
        # Deliberately NOT redirected to the temp dir: create_account_
        # intelligence_doc() computes its registered path via
        # path.relative_to(core.PROJECT_DIR), so the directory must stay a
        # real subdirectory of the project root for that call to succeed.
        # Real disposable file under the real account_intelligence/ dir,
        # cleaned up in the finally block -- same pattern used by
        # test_account_background_brief.py's isolated fixture.
        created_path = ix.ACCOUNT_INTELLIGENCE_DIR / f"{ix.datetime.now(ix.timezone.utc).strftime('%Y-%m-%d')}-rb-test-fixture-doc.md"
        try:
            entry = ix.create_account_intelligence_doc("RB Test Fixture Brand XYZ", "rb-test-fixture-doc", "# Test content\n")
            self.assertTrue(created_path.exists())
            matches = ix.find("RB Test Fixture Brand XYZ")
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["resource_type"], "account_intelligence_doc")
            self.assertEqual(entry["path"], str(created_path.relative_to(ix.core.PROJECT_DIR)))
        finally:
            created_path.unlink(missing_ok=True)


class TestRescanCustomersProspects(unittest.TestCase):
    """RB-2026-09-06: _rescan_customers_prospects() replaced the two prior
    separate rescans (_rescan_blue_sheets / _rescan_account_research) after
    the 3-store unification's Phase 2 step 1 migration. Read-only against
    the real customers_prospects_registry.json -- no module constant exists
    to redirect (same constraint _rescan_account_intelligence's own test
    above already works around), and this migration is real, already-shipped
    production data, not a fixture to fake."""

    def test_active_engagement_account_resolves_as_blue_sheet(self):
        entries = ix._rescan_customers_prospects()
        mcdonalds = [e for e in entries if e["path"] == "customers_prospects/accounts/mcdonalds"]
        self.assertEqual(len(mcdonalds), 1)
        self.assertEqual(mcdonalds[0]["resource_type"], "blue_sheet_account")
        self.assertEqual(mcdonalds[0]["source_system"], "blue_sheets")

    def test_pre_engagement_account_resolves_as_account_research(self):
        entries = ix._rescan_customers_prospects()
        cafe_rio = [e for e in entries if e["path"] == "customers_prospects/accounts/cafe-rio"]
        self.assertEqual(len(cafe_rio), 1)
        self.assertEqual(cafe_rio[0]["resource_type"], "account_research_record")
        self.assertEqual(cafe_rio[0]["source_system"], "account_research")

    def test_background_brief_entries_only_appear_when_the_file_exists(self):
        entries = ix._rescan_customers_prospects()
        briefs = [e for e in entries if e["resource_type"] == "account_research_brief"]
        for b in briefs:
            self.assertTrue((ix.core.PROJECT_DIR / b["path"]).exists())


class TestRescanTop1500Brands(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="rb-ecosystem-index-test-")
        self._orig_ecosystem_path = ix.ECOSYSTEM_PATH
        ix.ECOSYSTEM_PATH = Path(self._tmpdir) / "ecosystem_intelligence.json"

    def tearDown(self):
        ix.ECOSYSTEM_PATH = self._orig_ecosystem_path
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_ranked_brand_is_indexed_with_aliases_and_relationship_summary(self):
        ix.ECOSYSTEM_PATH.write_text(json.dumps({
            "entities": [{
                "id": "brand-golden-chick", "name": "Golden Chick",
                "entity_type": "brand", "subtype": "restaurant_brand",
                "aliases": ["Golden Chick Restaurants"],
                "attributes": {"rank": 149, "technomic_latest_year": 2024},
            }],
            "relationships": [{
                "from_entity_id": "brand-golden-chick",
                "to_entity_id": "vendor-qu", "category": "pos",
            }],
        }), encoding="utf-8")

        entries = ix._rescan_top_1500_brands()

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["brand_id"], "brand-golden-chick")
        self.assertEqual(entries[0]["rank"], 149)
        self.assertEqual(entries[0]["canonical_relationship_count"], 1)
        self.assertIn("golden", entries[0]["entity_tokens"])
        self.assertIn("vendor-qu", entries[0]["notes"])

    def test_unranked_and_out_of_range_brands_are_not_indexed(self):
        ix.ECOSYSTEM_PATH.write_text(json.dumps({
            "entities": [
                {"id": "brand-unranked", "name": "Unranked", "entity_type": "brand", "subtype": "restaurant_brand", "attributes": {}},
                {"id": "brand-1501", "name": "Outside", "entity_type": "brand", "subtype": "restaurant_brand", "attributes": {"rank": 1501, "technomic_latest_year": 2024}},
            ],
            "relationships": [],
        }), encoding="utf-8")

        self.assertEqual(ix._rescan_top_1500_brands(), [])


if __name__ == "__main__":
    unittest.main()
