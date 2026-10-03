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


def _seed_org(slug: str, display_name: str, *, brands=None, headquarters=None, aliases=None):
    ffc.create_organization_shell(slug, display_name)
    org = ffc.load_organization(slug)["organization"]
    org["brand_relationships"] = []
    for bname, unit_count, confidence_pct in (brands or []):
        org["brand_relationships"].append(
            ffc.brand_relationship(bname, unit_count, confidence_pct=confidence_pct, status="confirmed")
        )
    org["total_identified_units"] = sum((b[1] or 0) for b in (brands or []))
    if headquarters:
        org["headquarters"] = ffc.assertion_field(headquarters, confidence_pct=90, status="confirmed")
    if aliases:
        org["aliases"] = aliases
    ffc.save_organization(slug, org)
    ffc.register_organization(slug, display_name)


class TestListOrganizations(_IsolatedRootMixin, unittest.TestCase):
    """Shared query logic (moved out of server.py so the Team Portal and
    server.py's listFranchiseeOrganizations call one implementation, not
    two that can drift -- see ROADMAP.md's Franchisee Finder Team Portal
    scoping entry, 2026-10-02/03)."""

    def setUp(self):
        super().setUp()
        _seed_org("flynn-group", "Flynn Group", brands=[("Pizza Hut", 1321, 80), ("Arby's", 358, 65)],
                  headquarters="San Francisco, CA", aliases=["Flynn Restaurant Group"])
        _seed_org("solo-group", "Solo Group", brands=[("Subway", 12, 65)])

    def test_lists_all_by_default(self):
        orgs = ffc.list_organizations()
        self.assertEqual(len(orgs), 2)
        self.assertEqual({o["org_slug"] for o in orgs}, {"flynn-group", "solo-group"})

    def test_multi_brand_only_filters(self):
        orgs = ffc.list_organizations(multi_brand_only=True)
        self.assertEqual([o["org_slug"] for o in orgs], ["flynn-group"])

    def test_min_units_filters(self):
        orgs = ffc.list_organizations(min_units=100)
        self.assertEqual([o["org_slug"] for o in orgs], ["flynn-group"])

    def test_sorted_by_total_units_descending(self):
        orgs = ffc.list_organizations()
        totals = [o["total_identified_units"] for o in orgs]
        self.assertEqual(totals, sorted(totals, reverse=True))

    def test_q_matches_display_name(self):
        orgs = ffc.list_organizations(q="flynn")
        self.assertEqual([o["org_slug"] for o in orgs], ["flynn-group"])

    def test_q_matches_alias_case_insensitive(self):
        orgs = ffc.list_organizations(q="FLYNN RESTAURANT")
        self.assertEqual([o["org_slug"] for o in orgs], ["flynn-group"])

    def test_q_matches_brand_name(self):
        orgs = ffc.list_organizations(q="subway")
        self.assertEqual([o["org_slug"] for o in orgs], ["solo-group"])

    def test_q_no_match_returns_empty(self):
        self.assertEqual(ffc.list_organizations(q="nonexistent brand"), [])

    def test_q_combines_with_other_filters(self):
        # Matches the brand "Pizza Hut" but flynn-group fails the min_units
        # filter at a higher threshold -- both conditions must hold.
        orgs = ffc.list_organizations(q="pizza hut", min_units=10000)
        self.assertEqual(orgs, [])


class TestFindOrganizationsByBrand(_IsolatedRootMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        _seed_org("flynn-group", "Flynn Group", brands=[("Taco Bell", 307, 65), ("Pizza Hut", 1321, 80)])
        _seed_org("sun-holdings", "Sun Holdings", brands=[("Taco Bell", 50, 65)])
        _seed_org("no-taco-bell-group", "No Taco Bell Group", brands=[("Subway", 12, 65)])

    def test_finds_all_matching_organizations(self):
        matches = ffc.find_organizations_by_brand("Taco Bell")
        self.assertEqual({m["org_slug"] for m in matches}, {"flynn-group", "sun-holdings"})

    def test_case_insensitive(self):
        matches = ffc.find_organizations_by_brand("taco bell")
        self.assertEqual(len(matches), 2)

    def test_no_match_returns_empty_not_error(self):
        self.assertEqual(ffc.find_organizations_by_brand("Nonexistent Brand"), [])

    def test_sorted_by_unit_count_descending(self):
        matches = ffc.find_organizations_by_brand("Taco Bell")
        units = [m["brand_relationship"]["unit_count"]["value"] for m in matches]
        self.assertEqual(units, sorted(units, reverse=True))


if __name__ == "__main__":
    unittest.main()
