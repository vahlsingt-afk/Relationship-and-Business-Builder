"""
test_team_franchisee_finder.py — Team Portal Franchisee Finder slice
(ROADMAP.md, 2026-10-02/03).

Isolated from real production data via ffc.ROOT, same pattern as
test_franchisee_finder_common.py / test_franchisee_finder_api.py.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import franchisee_finder_common as ffc  # noqa: E402
import team_franchisee_finder as tff  # noqa: E402


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


class TestSearchOrganizations(_IsolatedRootMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        _seed_org("flynn-group", "Flynn Group", brands=[("Pizza Hut", 1321, 80), ("Taco Bell", 307, 65)],
                  headquarters="San Francisco, CA")
        _seed_org("solo-group", "Solo Group", brands=[("Subway", 12, 65)])

    def test_q_matches_org_name(self):
        orgs = tff.search_organizations("flynn")
        self.assertEqual([o["org_slug"] for o in orgs], ["flynn-group"])

    def test_q_matches_brand_name_reverse_direction(self):
        # Spec section 12: the same search box must work franchisee->portfolio
        # AND brand->franchisee.
        orgs = tff.search_organizations("subway")
        self.assertEqual([o["org_slug"] for o in orgs], ["solo-group"])

    def test_min_units_filter(self):
        orgs = tff.search_organizations(None, min_units=1000)
        self.assertEqual([o["org_slug"] for o in orgs], ["flynn-group"])

    def test_multi_brand_only_filter(self):
        orgs = tff.search_organizations(None, multi_brand_only=True)
        self.assertEqual([o["org_slug"] for o in orgs], ["flynn-group"])

    def test_no_query_or_filter_returns_everything(self):
        orgs = tff.search_organizations()
        self.assertEqual({o["org_slug"] for o in orgs}, {"flynn-group", "solo-group"})


class TestGetOrganizationProfile(_IsolatedRootMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        _seed_org("flynn-group", "Flynn Group", brands=[("Pizza Hut", 1321, 80)])

    def test_returns_full_profile_with_evidence(self):
        result = tff.get_organization_profile("flynn-group")
        self.assertEqual(result["organization"]["display_name"], "Flynn Group")
        self.assertIn("evidence", result)

    def test_missing_org_raises_not_found_error(self):
        with self.assertRaises(tff.NotFoundError):
            tff.get_organization_profile("does-not-exist")


if __name__ == "__main__":
    unittest.main()
