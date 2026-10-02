"""
test_export_franchisee_research_gaps.py — 2026-10-02.

Real gap this closes: a Codex/ChatGPT-Project task running Hunter's
documented local prepare workflow had no local source for a franchisee
research target queue, so it reached for the live Trusted Chat API
instead and hit a DNS failure (that hostname only resolves when Todd's
Mac is running the tunnel). This export reads only system/
franchisee_finder/ -- no network call.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import franchisee_finder_common as ffc  # noqa: E402
import export_franchisee_research_gaps as efrg  # noqa: E402


class _IsolatedRootMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_root = ffc.ROOT
        ffc.ROOT = Path(self._tmpdir.name)

    def tearDown(self):
        ffc.ROOT = self._orig_root
        self._tmpdir.cleanup()


def _seed_org(slug: str, display_name: str, *, total_units: int, headquarters=None,
              ownership_structure=None, legal_entities=None, people=None, geographic_footprint=None,
              unresolved_brand=False):
    ffc.create_organization_shell(slug, display_name)
    org = ffc.load_organization(slug)["organization"]
    org["total_identified_units"] = total_units
    if headquarters:
        org["headquarters"] = ffc.assertion_field(headquarters, confidence_pct=90, status="confirmed")
    if ownership_structure:
        org["ownership"]["structure"] = ffc.assertion_field(ownership_structure, confidence_pct=80, status="confirmed")
    if legal_entities:
        org["legal_entities"] = legal_entities
    if people:
        org["people"] = people
    if geographic_footprint:
        org["geographic_footprint"] = geographic_footprint
    status = "unresolved" if unresolved_brand else "confirmed"
    org["brand_relationships"] = [ffc.brand_relationship("Taco Bell", total_units, confidence_pct=70, status=status)]
    ffc.save_organization(slug, org)
    ffc.register_organization(slug, display_name)


class TestExportFranchiseeResearchGaps(_IsolatedRootMixin, unittest.TestCase):
    def test_bare_org_has_all_gaps_and_none_coverage(self):
        _seed_org("flynn-group", "Flynn Group", total_units=2936)
        data = efrg.export_franchisee_research_gaps()
        org = data["organizations"][0]
        self.assertEqual(org["coverage"], "none")
        self.assertIn("headquarters", org["research_gaps"])
        self.assertIn("ownership", org["research_gaps"])
        self.assertIn("legal_entities", org["research_gaps"])
        self.assertIn("leadership", org["research_gaps"])
        self.assertIn("geographic_footprint", org["research_gaps"])

    def test_fully_populated_org_has_no_gaps(self):
        _seed_org(
            "flynn-group", "Flynn Group", total_units=2936,
            headquarters="San Francisco, CA", ownership_structure="privately_held",
            legal_entities=[{"name": "Flynn Restaurant Group LP"}],
            people=[{"name": "Greg Flynn", "title": "CEO"}],
            geographic_footprint=[ffc.assertion_field("California", confidence_pct=80, status="confirmed")],
        )
        data = efrg.export_franchisee_research_gaps()
        org = data["organizations"][0]
        self.assertEqual(org["research_gaps"], [])
        self.assertEqual(org["coverage"], "well_covered")

    def test_unresolved_brand_relationship_flags_unit_count_verification(self):
        _seed_org("flynn-group", "Flynn Group", total_units=2936, unresolved_brand=True)
        data = efrg.export_franchisee_research_gaps()
        self.assertIn("unit_count_verification", data["organizations"][0]["research_gaps"])

    def test_priority_threshold_default_90(self):
        _seed_org("big-group", "Big Group", total_units=91)
        _seed_org("small-group", "Small Group", total_units=90)
        data = efrg.export_franchisee_research_gaps()
        by_id = {o["id"]: o for o in data["organizations"]}
        self.assertEqual(by_id["big-group"]["priority"], "enterprise_primary")
        self.assertEqual(by_id["small-group"]["priority"], "secondary")

    def test_custom_unit_threshold(self):
        _seed_org("mid-group", "Mid Group", total_units=50)
        data = efrg.export_franchisee_research_gaps(unit_threshold=40)
        self.assertEqual(data["organizations"][0]["priority"], "enterprise_primary")

    def test_only_gaps_excludes_well_covered(self):
        _seed_org(
            "covered-group", "Covered Group", total_units=100,
            headquarters="Dallas, TX", ownership_structure="privately_held",
            legal_entities=[{"name": "x"}], people=[{"name": "y"}],
            geographic_footprint=[ffc.assertion_field("Texas", confidence_pct=80, status="confirmed")],
        )
        _seed_org("gappy-group", "Gappy Group", total_units=100)
        data = efrg.export_franchisee_research_gaps(only_gaps=True)
        names = {o["name"] for o in data["organizations"]}
        self.assertEqual(names, {"Gappy Group"})

    def test_sorted_by_priority_then_units_descending(self):
        _seed_org("small-enterprise", "Small Enterprise", total_units=95)
        _seed_org("large-enterprise", "Large Enterprise", total_units=2000)
        _seed_org("non-enterprise", "Non Enterprise", total_units=10)
        data = efrg.export_franchisee_research_gaps()
        names_in_order = [o["name"] for o in data["organizations"]]
        self.assertEqual(names_in_order, ["Large Enterprise", "Small Enterprise", "Non Enterprise"])

    def test_coverage_summary_counts(self):
        _seed_org("flynn-group", "Flynn Group", total_units=2936)
        data = efrg.export_franchisee_research_gaps()
        self.assertEqual(data["coverage_summary"]["total_organizations"], 1)
        self.assertEqual(data["coverage_summary"]["none"], 1)
        self.assertEqual(data["coverage_summary"]["enterprise_primary"], 1)


if __name__ == "__main__":
    unittest.main()
