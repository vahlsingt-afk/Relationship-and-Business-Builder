"""test_franchisee_intelligence.py — Franchisee Finder Phase 1 (2026-10-02).

Isolated against a temp ROOT, same _IsolatedRootMixin pattern as
test_competitor_intelligence.py -- never touches real franchisee data.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import franchisee_intelligence as fi  # noqa: E402
import franchisee_intelligence_common as fic  # noqa: E402


class _IsolatedRootMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_root = fic.ROOT
        fic.ROOT = Path(self._tmpdir.name)

    def tearDown(self):
        fic.ROOT = self._orig_root
        self._tmpdir.cleanup()


class TestCreateAndLoad(_IsolatedRootMixin):
    def test_create_and_load_shell(self):
        result = fi.create_franchisee("ABC Restaurant Group")
        self.assertEqual(result["franchisee_slug"], "abc-restaurant-group")
        data = fic.load_franchisee("abc-restaurant-group")
        self.assertEqual(data["organization"]["display_name"], "ABC Restaurant Group")
        self.assertEqual(data["organization"]["hierarchy_level"], "unknown")
        self.assertEqual(data["evidence"], [])

    def test_registry_records_new_franchisee(self):
        fi.create_franchisee("ABC Restaurant Group")
        reg = fic.load_registry()
        self.assertEqual(len(reg["registry"]), 1)
        self.assertEqual(reg["registry"][0]["franchisee_slug"], "abc-restaurant-group")

    def test_missing_franchisee_raises(self):
        with self.assertRaises(FileNotFoundError):
            fic.load_franchisee("never-created")


class TestExtendedProfileFinding(_IsolatedRootMixin):
    def setUp(self):
        super().setUp()
        fi.create_franchisee("ABC Restaurant Group")

    def test_list_field_appends(self):
        fi.add_extended_profile_finding("abc-restaurant-group", "brand_relationships", "Taco Bell -- 84 units", confidence="high")
        org = fic.load_franchisee("abc-restaurant-group")["organization"]
        self.assertEqual(len(org["brand_relationships"]), 1)
        self.assertEqual(org["brand_relationships"][0]["value"], "Taco Bell -- 84 units")

    def test_scalar_field_is_replaced_not_appended(self):
        fi.add_extended_profile_finding("abc-restaurant-group", "headquarters", "Dallas, TX")
        org = fic.load_franchisee("abc-restaurant-group")["organization"]
        self.assertEqual(org["headquarters"]["value"], "Dallas, TX")
        self.assertIsInstance(org["headquarters"], dict)

    def test_total_identified_units_is_scalar(self):
        fi.add_extended_profile_finding("abc-restaurant-group", "total_identified_units", "127")
        org = fic.load_franchisee("abc-restaurant-group")["organization"]
        self.assertEqual(org["total_identified_units"]["value"], "127")

    def test_dedupes_identical_value_in_list_field(self):
        fi.add_extended_profile_finding("abc-restaurant-group", "legal_entities", "ABC Taco LLC")
        result = fi.add_extended_profile_finding("abc-restaurant-group", "legal_entities", "ABC Taco LLC")
        self.assertTrue(result["deduped"])
        org = fic.load_franchisee("abc-restaurant-group")["organization"]
        self.assertEqual(len(org["legal_entities"]), 1)

    def test_invalid_field_rejected(self):
        with self.assertRaises(ValueError):
            fi.add_extended_profile_finding("abc-restaurant-group", "not_a_real_field", "value")

    def test_empty_value_rejected(self):
        with self.assertRaises(ValueError):
            fi.add_extended_profile_finding("abc-restaurant-group", "legal_entities", "   ")


class TestEvidence(_IsolatedRootMixin):
    def setUp(self):
        super().setUp()
        fi.create_franchisee("ABC Restaurant Group")

    def test_add_evidence_appends(self):
        fi.add_franchisee_evidence("abc-restaurant-group", "Local press: new unit opening", category="unit_count")
        data = fic.load_franchisee("abc-restaurant-group")
        self.assertEqual(len(data["evidence"]), 1)
        self.assertEqual(data["evidence"][0]["category"], "unit_count")

    def test_invalid_category_rejected(self):
        with self.assertRaises(ValueError):
            fi.add_franchisee_evidence("abc-restaurant-group", "note", category="not_a_real_category")


class TestHierarchyAndConfidenceTier(_IsolatedRootMixin):
    def setUp(self):
        super().setUp()
        fi.create_franchisee("ABC Restaurant Group")

    def test_set_hierarchy_level(self):
        fi.set_hierarchy_level("abc-restaurant-group", "multi_brand_franchisee_group")
        org = fic.load_franchisee("abc-restaurant-group")["organization"]
        self.assertEqual(org["hierarchy_level"], "multi_brand_franchisee_group")

    def test_invalid_hierarchy_level_rejected(self):
        with self.assertRaises(ValueError):
            fi.set_hierarchy_level("abc-restaurant-group", "not_a_real_level")

    def test_set_confidence_tier(self):
        fi.set_confidence_tier("abc-restaurant-group", "high")
        org = fic.load_franchisee("abc-restaurant-group")["organization"]
        self.assertEqual(org["confidence_tier"], "high")

    def test_invalid_confidence_tier_rejected(self):
        with self.assertRaises(ValueError):
            fi.set_confidence_tier("abc-restaurant-group", "medium")


class TestFindSimilarFranchisees(_IsolatedRootMixin):
    def setUp(self):
        super().setUp()
        fi.create_franchisee("ABC Restaurant Group")
        fi.create_franchisee("Sun Holdings")
        fi.create_franchisee("Flynn Group")

    def test_finds_substring_variant(self):
        names = [m["display_name"] for m in fi.find_similar_franchisees("ABC Group")]
        self.assertIn("ABC Restaurant Group", names)

    def test_finds_filler_word_variant(self):
        names = [m["display_name"] for m in fi.find_similar_franchisees("Flynn Restaurant Group")]
        self.assertIn("Flynn Group", names)

    def test_finds_exact_match(self):
        names = [m["display_name"] for m in fi.find_similar_franchisees("Sun Holdings")]
        self.assertIn("Sun Holdings", names)

    def test_no_match_for_unrelated_name(self):
        self.assertEqual(fi.find_similar_franchisees("Totally Different Co"), [])

    def test_empty_registry_returns_empty(self):
        import franchisee_intelligence_common as fic2
        fic2.save_registry({"registry": []})
        self.assertEqual(fi.find_similar_franchisees("ABC Group"), [])


class TestRegistrySelfHeal(_IsolatedRootMixin):
    def test_corrupted_registry_self_heals_from_disk(self):
        fi.create_franchisee("ABC Restaurant Group")
        fic.registry_path().write_text("{not valid json", encoding="utf-8")
        reg = fic.load_registry()
        self.assertEqual(len(reg["registry"]), 1)
        self.assertEqual(reg["registry"][0]["franchisee_slug"], "abc-restaurant-group")


if __name__ == "__main__":
    unittest.main()
