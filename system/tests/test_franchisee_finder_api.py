"""
test_franchisee_finder_api.py — Franchisee Finder Phase 1 read-only
endpoints (listFranchiseeOrganizations/getFranchiseeProfile/
queryFranchiseesByBrand).

Calls server.py's route functions directly (bypassing _auth via patch, and
isolating ff_common.ROOT to a tmp dir) -- same pattern as
test_competitor_bulk_import.py.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "system" / "api"))

import franchisee_finder_common as ffc  # noqa: E402


class _IsolatedRootMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_root = ffc.ROOT
        ffc.ROOT = Path(self._tmpdir.name)

    def tearDown(self):
        ffc.ROOT = self._orig_root
        self._tmpdir.cleanup()


def _seed_org(slug: str, display_name: str, *, brands=None, headquarters=None):
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
    ffc.save_organization(slug, org)
    ffc.register_organization(slug, display_name)


class TestListFranchiseeOrganizations(_IsolatedRootMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        _seed_org("flynn-group", "Flynn Group", brands=[("Pizza Hut", 1321, 80), ("Arby's", 358, 65)], headquarters="San Francisco, CA")
        _seed_org("solo-group", "Solo Group", brands=[("Subway", 12, 65)])

    def test_lists_all_by_default(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.get_franchisee_organizations_list(min_units=None, multi_brand_only=False, x_api_key=None)
        self.assertEqual(result["organization_count"], 2)
        slugs = {o["org_slug"] for o in result["organizations"]}
        self.assertEqual(slugs, {"flynn-group", "solo-group"})

    def test_multi_brand_only_filters(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.get_franchisee_organizations_list(min_units=None, multi_brand_only=True, x_api_key=None)
        self.assertEqual(result["organization_count"], 1)
        self.assertEqual(result["organizations"][0]["org_slug"], "flynn-group")

    def test_min_units_filters(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.get_franchisee_organizations_list(min_units=100, multi_brand_only=False, x_api_key=None)
        self.assertEqual(result["organization_count"], 1)
        self.assertEqual(result["organizations"][0]["org_slug"], "flynn-group")

    def test_sorted_by_total_units_descending(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.get_franchisee_organizations_list(min_units=None, multi_brand_only=False, x_api_key=None)
        totals = [o["total_identified_units"] for o in result["organizations"]]
        self.assertEqual(totals, sorted(totals, reverse=True))

    def test_headquarters_surfaced(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.get_franchisee_organizations_list(min_units=None, multi_brand_only=False, x_api_key=None)
        flynn = next(o for o in result["organizations"] if o["org_slug"] == "flynn-group")
        self.assertEqual(flynn["headquarters"], "San Francisco, CA")


class TestGetFranchiseeProfile(_IsolatedRootMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        _seed_org("flynn-group", "Flynn Group", brands=[("Pizza Hut", 1321, 80)])

    def test_returns_full_profile(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.get_franchisee_profile("flynn-group", x_api_key=None)
        self.assertEqual(result["organization"]["display_name"], "Flynn Group")
        self.assertIn("evidence", result)

    def test_missing_org_raises_404_with_helpful_detail(self):
        import server
        from fastapi import HTTPException
        with patch.object(server, "_auth", lambda *a, **k: None):
            with self.assertRaises(HTTPException) as cm:
                server.get_franchisee_profile("does-not-exist", x_api_key=None)
        self.assertEqual(cm.exception.status_code, 404)
        self.assertIn("listFranchiseeOrganizations", cm.exception.detail)


class TestQueryFranchiseesByBrand(_IsolatedRootMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        _seed_org("flynn-group", "Flynn Group", brands=[("Taco Bell", 307, 65), ("Pizza Hut", 1321, 80)])
        _seed_org("sun-holdings", "Sun Holdings", brands=[("Taco Bell", 50, 65)])
        _seed_org("no-taco-bell-group", "No Taco Bell Group", brands=[("Subway", 12, 65)])

    def test_finds_all_matching_organizations(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.get_franchisees_by_brand("Taco Bell", x_api_key=None)
        self.assertEqual(result["match_count"], 2)
        slugs = {o["org_slug"] for o in result["organizations"]}
        self.assertEqual(slugs, {"flynn-group", "sun-holdings"})

    def test_case_insensitive(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.get_franchisees_by_brand("taco bell", x_api_key=None)
        self.assertEqual(result["match_count"], 2)

    def test_no_match_returns_empty_not_error(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.get_franchisees_by_brand("Nonexistent Brand", x_api_key=None)
        self.assertEqual(result["match_count"], 0)
        self.assertEqual(result["organizations"], [])

    def test_sorted_by_unit_count_descending(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.get_franchisees_by_brand("Taco Bell", x_api_key=None)
        units = [o["brand_relationship"]["unit_count"]["value"] for o in result["organizations"]]
        self.assertEqual(units, sorted(units, reverse=True))


if __name__ == "__main__":
    unittest.main()
