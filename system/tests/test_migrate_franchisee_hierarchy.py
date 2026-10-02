"""
test_migrate_franchisee_hierarchy.py — Franchisee Finder Phase 1 seed import.

Uses small synthetic fh_operator/eco_entity/graph fixtures throughout --
never reads the real system/franchisee_hierarchy.json or
system/ecosystem_intelligence.json, so this stays fast and independent of
how those real files evolve. The real-file wiring (_load_ecosystem_operators,
FRANCHISEE_HIERARCHY_PATH) is intentionally not exercised here.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import migrate_franchisee_hierarchy as m  # noqa: E402


def _fh_operator(name="Acme Group", location="Dallas, TX", brands=None):
    return {
        "name": name, "location": location,
        "brands": brands if brands is not None else [{"brand": "Taco Bell", "unit_count": 84}],
        "total_units": sum(b["unit_count"] for b in (brands or [{"unit_count": 84}])),
    }


def _eco_entity(name="Acme Group", eid="operator-acme-group", aliases=None, hq_finding=None,
                leadership=None, operates_edges=None):
    return {
        "id": eid, "name": name, "aliases": aliases or [],
        "attributes": {
            "deep_research_profile": {
                "evidence_ledger": [hq_finding] if hq_finding else [],
                "current_leadership": leadership or [],
            }
        },
        "operates_edges": operates_edges or [],
    }


class TestMatchKeys(unittest.TestCase):
    def test_includes_name_and_aliases(self):
        keys = m._match_keys("Acme Group", ["Acme Co", "AG Holdings"])
        self.assertIn("acme-group", keys)
        self.assertIn("acme-co", keys)
        self.assertIn("ag-holdings", keys)

    def test_empty_alias_list(self):
        keys = m._match_keys("Acme Group", None)
        self.assertEqual(keys, {"acme-group"})


class TestPairOperators(unittest.TestCase):
    def test_exact_name_match(self):
        fh = [_fh_operator(name="Acme Group")]
        eco = [_eco_entity(name="Acme Group")]
        pairs = m._pair_operators(fh, eco)
        self.assertEqual(len(pairs), 1)
        fh_op, eco_ent = pairs[0]
        self.assertIsNotNone(fh_op)
        self.assertIsNotNone(eco_ent)

    def test_alias_match(self):
        fh = [_fh_operator(name="AG Holdings")]
        eco = [_eco_entity(name="Acme Group", aliases=["AG Holdings"])]
        pairs = m._pair_operators(fh, eco)
        self.assertEqual(len(pairs), 1)
        fh_op, eco_ent = pairs[0]
        self.assertEqual(fh_op["name"], "AG Holdings")
        self.assertEqual(eco_ent["name"], "Acme Group")

    def test_fh_only_operator_unmatched(self):
        fh = [_fh_operator(name="Solo Group")]
        eco: list = []
        pairs = m._pair_operators(fh, eco)
        self.assertEqual(len(pairs), 1)
        fh_op, eco_ent = pairs[0]
        self.assertEqual(fh_op["name"], "Solo Group")
        self.assertIsNone(eco_ent)

    def test_eco_only_entity_unmatched(self):
        fh: list = []
        eco = [_eco_entity(name="Eco Only Group")]
        pairs = m._pair_operators(fh, eco)
        self.assertEqual(len(pairs), 1)
        fh_op, eco_ent = pairs[0]
        self.assertIsNone(fh_op)
        self.assertEqual(eco_ent["name"], "Eco Only Group")

    def test_each_fh_operator_consumed_at_most_once(self):
        """Two eco entities whose alias sets both contain the SAME fh key
        must not both claim it -- the second gets no fh match, not a
        duplicate pairing of one real fh operator."""
        fh = [_fh_operator(name="Shared Name")]
        eco = [
            _eco_entity(name="Eco One", eid="operator-eco-one", aliases=["Shared Name"]),
            _eco_entity(name="Eco Two", eid="operator-eco-two", aliases=["Shared Name"]),
        ]
        pairs = m._pair_operators(fh, eco)
        self.assertEqual(len(pairs), 2)
        fh_matches = [p[0] for p in pairs if p[0] is not None]
        self.assertEqual(len(fh_matches), 1)


class TestBuildOrganization(unittest.TestCase):
    def _graph(self, brand_entities=None):
        return {"entities": (brand_entities or []), "relationships": []}

    def test_fh_only_seeds_unit_count_single_source_confidence(self):
        fh_op = _fh_operator(name="Acme Group", location="", brands=[{"brand": "Taco Bell", "unit_count": 84}])
        org, evidence = m.build_organization(fh_op, None, self._graph())
        self.assertEqual(org["display_name"], "Acme Group")
        self.assertIsNone(org["linked_graph_entity_id"])
        rel = org["brand_relationships"][0]
        self.assertEqual(rel["unit_count"]["value"], 84)
        self.assertEqual(rel["unit_count"]["confidence_pct"], m.SINGLE_SOURCE_CONFIDENCE_PCT)
        self.assertEqual(rel["unit_count"]["status"], "inferred")
        self.assertEqual(rel["unit_count"]["evidence_ids"], ["ff-ev-acme-group-0001"])
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["evidence_id"], "ff-ev-acme-group-0001")

    def test_fh_location_seeds_low_confidence_headquarters(self):
        fh_op = _fh_operator(name="Acme Group", location="Dallas, TX")
        org, _ = m.build_organization(fh_op, None, self._graph())
        self.assertEqual(org["headquarters"]["value"], "Dallas, TX")
        self.assertEqual(org["headquarters"]["confidence_pct"], m.LOCATION_AS_HQ_CONFIDENCE_PCT)
        self.assertEqual(org["headquarters"]["status"], "inferred")

    def test_eco_headquarters_preferred_over_fh_location(self):
        fh_op = _fh_operator(name="Acme Group", location="Dallas, TX")
        hq_finding = {"finding": "123 Main St, Austin, TX", "confidence": 100,
                      "source_url": "https://acme.com/about", "access_date": "2026-09-27"}
        eco_ent = _eco_entity(name="Acme Group", hq_finding={"category": "headquarters", **hq_finding})
        org, _ = m.build_organization(fh_op, eco_ent, self._graph())
        self.assertEqual(org["headquarters"]["value"], "123 Main St, Austin, TX")
        self.assertEqual(org["headquarters"]["confidence_pct"], 100)
        self.assertEqual(org["headquarters"]["status"], "confirmed")

    def test_leadership_from_eco_entity(self):
        eco_ent = _eco_entity(name="Acme Group", leadership=[
            {"name": "Jane Smith", "title": "CEO", "confidence": 100, "source_url": "https://acme.com"},
        ])
        org, _ = m.build_organization(None, eco_ent, self._graph())
        self.assertEqual(len(org["people"]), 1)
        self.assertEqual(org["people"][0]["name"], "Jane Smith")
        self.assertEqual(org["people"][0]["role_category"], "ceo")
        self.assertEqual(org["people"][0]["evidence_ids"], ["ff-ev-acme-group-0001"])

    def test_corroborated_brand_gets_confidence_bump_and_two_evidence_ids(self):
        brand_entity = {"id": "brand-taco-bell", "name": "Taco Bell", "entity_type": "brand", "aliases": []}
        fh_op = _fh_operator(name="Acme Group", location="", brands=[{"brand": "Taco Bell", "unit_count": 84}])
        eco_ent = _eco_entity(name="Acme Group", operates_edges=[
            {"from_entity_id": "operator-acme-group", "to_entity_id": "brand-taco-bell",
             "relationship_type": "operates", "sources": ["https://frandata.com/acme"],
             "confidence": {"score": 0.9}},
        ])
        org, evidence = m.build_organization(fh_op, eco_ent, self._graph([brand_entity]))
        rel = org["brand_relationships"][0]
        self.assertEqual(rel["brand_name"], "Taco Bell")
        self.assertEqual(rel["brand_entity_id"], "brand-taco-bell")
        self.assertEqual(rel["unit_count"]["confidence_pct"], m.CORROBORATED_CONFIDENCE_PCT)
        self.assertEqual(rel["unit_count"]["status"], "confirmed")
        self.assertEqual(len(rel["unit_count"]["evidence_ids"]), 2)
        self.assertEqual(len(evidence), 2)

    def test_eco_only_brand_has_unresolved_unit_count(self):
        brand_entity = {"id": "brand-chick-fil-a", "name": "Chick-fil-A", "entity_type": "brand", "aliases": []}
        eco_ent = _eco_entity(name="Acme Group", operates_edges=[
            {"from_entity_id": "operator-acme-group", "to_entity_id": "brand-chick-fil-a",
             "relationship_type": "operates", "sources": [], "confidence": {"score": 0.8}},
        ])
        org, _ = m.build_organization(None, eco_ent, self._graph([brand_entity]))
        rel = org["brand_relationships"][0]
        self.assertEqual(rel["brand_name"], "Chick-fil-A")
        self.assertIsNone(rel["unit_count"]["value"])
        self.assertIsNone(rel["unit_count"]["confidence_pct"])
        self.assertEqual(rel["unit_count"]["status"], "unresolved")

    def test_total_identified_units_sums_only_resolved_counts(self):
        brand_entity = {"id": "brand-chick-fil-a", "name": "Chick-fil-A", "entity_type": "brand", "aliases": []}
        fh_op = _fh_operator(name="Acme Group", brands=[{"brand": "Taco Bell", "unit_count": 84}])
        eco_ent = _eco_entity(name="Acme Group", operates_edges=[
            {"from_entity_id": "operator-acme-group", "to_entity_id": "brand-chick-fil-a",
             "relationship_type": "operates", "sources": [], "confidence": {"score": 0.8}},
        ])
        org, _ = m.build_organization(fh_op, eco_ent, self._graph([brand_entity]))
        self.assertEqual(org["total_identified_units"], 84)

    def test_linked_graph_entity_id_set_when_eco_entity_present(self):
        eco_ent = _eco_entity(name="Acme Group", eid="operator-acme-group")
        org, _ = m.build_organization(None, eco_ent, self._graph())
        self.assertEqual(org["linked_graph_entity_id"], "operator-acme-group")

    def test_overall_profile_quality_medium_when_both_sources(self):
        fh_op = _fh_operator(name="Acme Group")
        eco_ent = _eco_entity(name="Acme Group")
        org, _ = m.build_organization(fh_op, eco_ent, self._graph())
        self.assertEqual(org["research_status"]["overall_profile_quality"], "medium")

    def test_overall_profile_quality_low_when_single_source(self):
        fh_op = _fh_operator(name="Acme Group")
        org, _ = m.build_organization(fh_op, None, self._graph())
        self.assertEqual(org["research_status"]["overall_profile_quality"], "low")


class TestRoleCategory(unittest.TestCase):
    def test_ceo_variants(self):
        self.assertEqual(m._role_category("Founder, Chairman & CEO"), "ceo")
        self.assertEqual(m._role_category("Chief Executive Officer"), "ceo")

    def test_coo_variants(self):
        self.assertEqual(m._role_category("President & COO"), "coo")
        self.assertEqual(m._role_category("Chief Operating Officer"), "coo")

    def test_technology_leader(self):
        self.assertEqual(m._role_category("SVP, Brand Technology"), "technology_leader")

    def test_other_fallback(self):
        self.assertEqual(m._role_category("Chief Transformation Officer"), "other")


if __name__ == "__main__":
    unittest.main()
