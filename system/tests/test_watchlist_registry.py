"""
test_watchlist_registry.py

RB-2026-09-05, watchlist auto-expansion scoping: entity_alerts.py and
daily_brief.py used to carry two independent hardcoded copies of the same
~155-entity mandatory watchlist, kept in sync by hand with nothing
enforcing they stayed identical (RB-DEFECT-2026-08-19). Migrated both to
read system/watchlist_registry.json through this shared module -- this is
the single real write target watchlist_promotion.py's confirmed
promotions land in.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import watchlist_registry as wr  # noqa: E402


def _sample_registry() -> dict:
    return {
        "schema_version": "1.0",
        "last_updated": "2026-01-01",
        "restaurant_brands": ["McDonald's", "Wendy's"],
        "restaurant_tech": {
            "restaurant_tech_pos": ["Toast", "PAR Technology"],
            "restaurant_tech_payments": ["Stripe"],
        },
    }


class TestRealRegistryFile(unittest.TestCase):
    """The real system/watchlist_registry.json, read-only checks."""

    def test_loads_and_has_expected_shape(self):
        registry = wr.load_registry()
        self.assertIn("restaurant_brands", registry)
        self.assertIn("restaurant_tech", registry)
        self.assertIsInstance(registry["restaurant_brands"], list)
        self.assertIsInstance(registry["restaurant_tech"], dict)

    def test_real_brand_and_vendor_present(self):
        all_names = wr.mandatory_all()
        self.assertIn("McDonald's", all_names)
        self.assertIn("Toast", all_names)

    def test_no_duplicate_names_in_mandatory_all(self):
        all_names = wr.mandatory_all()
        self.assertEqual(len(all_names), len(set(all_names)))


class TestRegistryHelpersAgainstIsolatedFile(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tmpdir.name) / "watchlist_registry.json"
        self.path.write_text(json.dumps(_sample_registry()), encoding="utf-8")
        self._orig_path = wr.REGISTRY_PATH
        wr.REGISTRY_PATH = self.path

    def tearDown(self):
        wr.REGISTRY_PATH = self._orig_path
        self._tmpdir.cleanup()

    def test_mandatory_restaurant_brands(self):
        self.assertEqual(wr.mandatory_restaurant_brands(), ["McDonald's", "Wendy's"])

    def test_mandatory_restaurant_tech_flattens_categories(self):
        flat = wr.mandatory_restaurant_tech()
        self.assertEqual(set(flat), {"Toast", "PAR Technology", "Stripe"})

    def test_mandatory_all_combines_both(self):
        self.assertEqual(
            set(wr.mandatory_all()),
            {"McDonald's", "Wendy's", "Toast", "PAR Technology", "Stripe"},
        )

    def test_add_restaurant_brand_persists(self):
        wr.add_restaurant_brand("Sweetgreen")
        reloaded = wr.load_registry()
        self.assertIn("Sweetgreen", reloaded["restaurant_brands"])

    def test_add_restaurant_brand_is_idempotent(self):
        wr.add_restaurant_brand("Wendy's")
        reloaded = wr.load_registry()
        self.assertEqual(reloaded["restaurant_brands"].count("Wendy's"), 1)

    def test_add_restaurant_tech_new_category(self):
        wr.add_restaurant_tech("NewVendorCo", category="restaurant_tech_ai")
        reloaded = wr.load_registry()
        self.assertIn("NewVendorCo", reloaded["restaurant_tech"]["restaurant_tech_ai"])

    def test_add_restaurant_tech_rejects_duplicate_across_categories(self):
        """A vendor already tracked under a DIFFERENT category must not be
        added a second time just because a caller names a new category."""
        wr.add_restaurant_tech("Toast", category="restaurant_tech_ai")
        reloaded = wr.load_registry()
        self.assertNotIn("Toast", reloaded["restaurant_tech"].get("restaurant_tech_ai", []))
        self.assertIn("Toast", reloaded["restaurant_tech"]["restaurant_tech_pos"])

    def test_save_registry_updates_last_updated(self):
        registry = wr.load_registry()
        registry["last_updated"] = "2020-01-01"
        wr.save_registry(registry)
        reloaded = wr.load_registry()
        self.assertNotEqual(reloaded["last_updated"], "2020-01-01")


if __name__ == "__main__":
    unittest.main()
