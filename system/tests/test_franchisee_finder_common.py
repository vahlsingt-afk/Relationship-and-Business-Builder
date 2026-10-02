"""
test_franchisee_finder_common.py — Franchisee Finder Phase 1.

Isolated from real production data (own tmp ffc.ROOT), same pattern as
test_competitor_intelligence.py's _IsolatedRootMixin.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import franchisee_finder_common as ffc  # noqa: E402


class _IsolatedRootMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_root = ffc.ROOT
        ffc.ROOT = Path(self._tmpdir.name)

    def tearDown(self):
        ffc.ROOT = self._orig_root
        self._tmpdir.cleanup()


class TestOrganizationShellAndStorage(_IsolatedRootMixin, unittest.TestCase):
    def test_create_and_load_shell(self):
        d = ffc.create_organization_shell("acme-group", "Acme Group")
        self.assertTrue((d / "organization.json").exists())
        self.assertTrue((d / "evidence.jsonl").exists())
        data = ffc.load_organization("acme-group")
        self.assertEqual(data["organization"]["display_name"], "Acme Group")
        self.assertEqual(data["organization"]["org_id"], "ff-acme-group")
        self.assertIsNone(data["organization"]["linked_graph_entity_id"])
        self.assertEqual(data["evidence"], [])

    def test_missing_organization_raises(self):
        with self.assertRaises(FileNotFoundError):
            ffc.load_organization("does-not-exist")

    def test_registry_records_new_organization(self):
        ffc.create_organization_shell("acme-group", "Acme Group")
        ffc.register_organization("acme-group", "Acme Group")
        reg = ffc.load_registry()
        slugs = [e["org_slug"] for e in reg["registry"]]
        self.assertIn("acme-group", slugs)

    def test_register_organization_updates_existing_entry_not_duplicate(self):
        ffc.register_organization("acme-group", "Acme Group")
        ffc.register_organization("acme-group", "Acme Group Renamed")
        reg = ffc.load_registry()
        matches = [e for e in reg["registry"] if e["org_slug"] == "acme-group"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["display_name"], "Acme Group Renamed")

    def test_save_organization_updates_timestamp(self):
        ffc.create_organization_shell("acme-group", "Acme Group")
        org = ffc.load_organization("acme-group")["organization"]
        original_updated_at = org["updated_at"]
        org["display_name"] = "Acme Group Inc."
        ffc.save_organization("acme-group", org)
        reloaded = ffc.load_organization("acme-group")["organization"]
        self.assertEqual(reloaded["display_name"], "Acme Group Inc.")
        self.assertNotEqual(reloaded["updated_at"], "")
        self.assertIsNotNone(original_updated_at)

    def test_unsafe_slug_rejected(self):
        with self.assertRaises(ValueError):
            ffc.org_dir("../../etc", create=True)


class TestAddEvidence(_IsolatedRootMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        ffc.create_organization_shell("acme-group", "Acme Group")

    def test_add_evidence_assigns_sequential_ids(self):
        ev1 = ffc.add_evidence("acme-group", {"finding": "first"})
        ev2 = ffc.add_evidence("acme-group", {"finding": "second"})
        self.assertEqual(ev1["evidence_id"], "ff-ev-acme-group-0001")
        self.assertEqual(ev2["evidence_id"], "ff-ev-acme-group-0002")
        data = ffc.load_organization("acme-group")
        self.assertEqual(len(data["evidence"]), 2)

    def test_add_evidence_preserves_explicit_id(self):
        ev = ffc.add_evidence("acme-group", {"evidence_id": "ff-ev-acme-group-custom", "finding": "x"})
        self.assertEqual(ev["evidence_id"], "ff-ev-acme-group-custom")


class TestAssertionField(unittest.TestCase):
    def test_default_shape(self):
        field = ffc.assertion_field("Dallas, TX", confidence_pct=95, status="confirmed")
        self.assertEqual(field["value"], "Dallas, TX")
        self.assertEqual(field["confidence_pct"], 95)
        self.assertEqual(field["status"], "confirmed")
        self.assertEqual(field["evidence_ids"], [])

    def test_confidence_none_is_valid(self):
        field = ffc.assertion_field("unknown", confidence_pct=None)
        self.assertIsNone(field["confidence_pct"])

    def test_invalid_status_rejected(self):
        with self.assertRaises(ValueError):
            ffc.assertion_field("x", confidence_pct=50, status="made_up_status")

    def test_confidence_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            ffc.assertion_field("x", confidence_pct=101)
        with self.assertRaises(ValueError):
            ffc.assertion_field("x", confidence_pct=-1)


class TestBrandRelationship(unittest.TestCase):
    def test_brand_relationship_shape(self):
        rel = ffc.brand_relationship(
            "Taco Bell", 84, confidence_pct=70, source_url="https://example.com",
            unit_count_basis="Franchise Times", status="inferred",
        )
        self.assertEqual(rel["brand_name"], "Taco Bell")
        self.assertEqual(rel["unit_count"]["value"], 84)
        self.assertEqual(rel["unit_count"]["confidence_pct"], 70)
        self.assertEqual(len(rel["history"]), 1)
        self.assertEqual(rel["history"][0]["unit_count"], 84)

    def test_record_unit_count_change_appends_history_never_overwrites(self):
        rel = ffc.brand_relationship("Taco Bell", 78, confidence_pct=70, status="inferred", as_of="2027-01-01")
        rel2 = ffc.record_unit_count_change(rel, 84, date="2027-04-01", note="expansion")
        self.assertEqual(len(rel2["history"]), 2)
        self.assertEqual(rel2["history"][0]["unit_count"], 78)
        self.assertEqual(rel2["history"][1]["unit_count"], 84)
        self.assertEqual(rel2["unit_count"]["value"], 84)
        # original dict never mutated in place
        self.assertEqual(len(rel["history"]), 1)


if __name__ == "__main__":
    unittest.main()
