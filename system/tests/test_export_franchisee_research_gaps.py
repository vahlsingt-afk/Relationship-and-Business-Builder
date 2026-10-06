"""test_export_franchisee_research_gaps.py — Franchisee Finder Phase 1 (2026-10-02)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import franchisee_intelligence as fi  # noqa: E402
import franchisee_intelligence_common as fic  # noqa: E402
import export_franchisee_research_gaps as efrg  # noqa: E402


def _graph():
    return {
        "entities": [
            {"id": "brand-taco-bell", "entity_type": "brand", "name": "Taco Bell", "attributes": {"rank": 10}},
            {"id": "brand-kfc", "entity_type": "brand", "name": "KFC", "attributes": {"rank": 20}},
            {"id": "brand-obscure", "entity_type": "brand", "name": "Obscure Diner", "attributes": {"rank": 900}},
            {"id": "operator-flynn-group", "entity_type": "brand", "subtype": "multi_brand_franchisee_operator", "name": "Flynn Group", "attributes": {}},
        ],
        "relationships": [],
    }


class _IsolatedRootMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_root = fic.ROOT
        fic.ROOT = Path(self._tmpdir.name)
        self._graph_patch = patch("export_franchisee_research_gaps.ei._read_graph", return_value=_graph())
        self._graph_patch.start()

    def tearDown(self):
        self._graph_patch.stop()
        fic.ROOT = self._orig_root
        self._tmpdir.cleanup()


class TestDiscoveryGaps(_IsolatedRootMixin):
    def test_brand_with_no_franchisee_coverage_is_a_gap(self):
        gaps = efrg.export_discovery_gaps(rank_max=500)
        names = {g["brand_name"] for g in gaps}
        self.assertIn("Taco Bell", names)
        self.assertIn("KFC", names)

    def test_multi_brand_franchisee_operator_subtype_is_excluded_from_brand_pool(self):
        gaps = efrg.export_discovery_gaps(rank_max=500)
        names = {g["brand_name"] for g in gaps}
        self.assertNotIn("Flynn Group", names)

    def test_rank_max_excludes_low_ranked_brands(self):
        gaps = efrg.export_discovery_gaps(rank_max=500)
        names = {g["brand_name"] for g in gaps}
        self.assertNotIn("Obscure Diner", names)

    def test_brand_with_registered_franchisee_coverage_is_not_a_gap(self):
        fi.create_franchisee("ABC Restaurant Group")
        fi.add_extended_profile_finding("abc-restaurant-group", "brand_relationships", "Taco Bell -- 84 units")
        gaps = efrg.export_discovery_gaps(rank_max=500)
        names = {g["brand_name"] for g in gaps}
        self.assertNotIn("Taco Bell", names)
        self.assertIn("KFC", names)


class TestProfileGaps(_IsolatedRootMixin):
    def test_fully_empty_organization_has_all_profile_gaps(self):
        fi.create_franchisee("ABC Restaurant Group")
        gaps = efrg.export_profile_gaps()
        self.assertEqual(len(gaps), 1)
        self.assertEqual(set(gaps[0]["missing_fields"]), set(efrg.PROFILE_GAP_FIELDS))

    def test_populated_fields_close_their_gaps(self):
        fi.create_franchisee("ABC Restaurant Group")
        fi.add_extended_profile_finding("abc-restaurant-group", "headquarters", "Dallas, TX")
        fi.add_extended_profile_finding("abc-restaurant-group", "ownership", "Privately held")
        fi.add_extended_profile_finding("abc-restaurant-group", "leadership", "Jane Smith -- CEO")
        fi.add_extended_profile_finding("abc-restaurant-group", "sales_estimate", "$500M estimated")
        fi.add_extended_profile_finding("abc-restaurant-group", "legal_entities", "ABC Taco LLC")
        fi.add_extended_profile_finding("abc-restaurant-group", "operating_geography", "Texas")
        fi.add_extended_profile_finding("abc-restaurant-group", "total_identified_units", "127")
        gaps = efrg.export_profile_gaps()
        self.assertEqual(gaps, [])

    def test_no_known_organizations_means_no_profile_gaps(self):
        self.assertEqual(efrg.export_profile_gaps(), [])


if __name__ == "__main__":
    unittest.main()
