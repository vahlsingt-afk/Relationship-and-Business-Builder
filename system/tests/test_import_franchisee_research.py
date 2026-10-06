"""test_import_franchisee_research.py — Franchisee Finder Phase 1 (2026-10-02).

Isolated against a temp franchisee_intelligence_common.ROOT, same pattern
as test_import_competitor_platform_research.py. mutation_policy's receipts
path is already redirected globally by conftest.py for the whole test run.
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
import import_franchisee_research as ifr  # noqa: E402


def _profile_finding(**overrides) -> dict:
    base = {
        "target": "franchisee:abc-restaurant-group", "field": "headquarters", "value": "Dallas, TX",
        "finding_type": "independently_verified", "source_url": "https://example.com/abc",
        "source_type": "trade_press", "confidence": "high", "observed_at": "2026-09-27",
    }
    base.update(overrides)
    return base


def _discovery_finding(**overrides) -> dict:
    base = {
        "proposed_name": "ABC Restaurant Group", "discovered_from_brand": "brand-taco-bell",
        "evidence": "FDD Item 20 lists ABC Restaurant Group as a Taco Bell franchisee",
        "source_url": "https://example.com/fdd", "confidence": "medium", "observed_at": "2026-09-27",
    }
    base.update(overrides)
    return base


class _IsolatedRootMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_root = fic.ROOT
        fic.ROOT = Path(self._tmpdir.name)

    def tearDown(self):
        fic.ROOT = self._orig_root
        self._tmpdir.cleanup()

    def _org(self, slug="abc-restaurant-group"):
        return fic.load_franchisee(slug)["organization"]


class TestProfileFindingValidation(_IsolatedRootMixin):
    def test_missing_required_key_is_rejected_without_blocking_others(self):
        fi.create_franchisee("ABC Restaurant Group")
        good = _profile_finding()
        bad = _profile_finding(source_url=None)
        result = ifr.import_profile_findings({"findings": [bad, good]}, dry_run=False)
        self.assertEqual(result["findings_malformed"], 1)
        self.assertEqual(result["applied"], 1)

    def test_unknown_field_is_rejected(self):
        fi.create_franchisee("ABC Restaurant Group")
        finding = _profile_finding(field="not_a_real_field")
        result = ifr.import_profile_findings({"findings": [finding]}, dry_run=False)
        self.assertEqual(result["findings_malformed"], 1)
        self.assertEqual(result["applied"], 0)

    def test_target_must_be_franchisee_prefixed(self):
        fi.create_franchisee("ABC Restaurant Group")
        finding = _profile_finding(target="competitor:abc-restaurant-group")
        result = ifr.import_profile_findings({"findings": [finding]}, dry_run=False)
        self.assertEqual(result["findings_malformed"], 1)


class TestProfileFindingNeverCreatesOrganization(_IsolatedRootMixin):
    def test_unknown_slug_routes_to_identity_unresolved_not_creation(self):
        finding = _profile_finding(target="franchisee:never-created")
        result = ifr.import_profile_findings({"findings": [finding]}, dry_run=False)
        self.assertEqual(result["identity_unresolved"], 1)
        self.assertEqual(result["applied"], 0)
        with self.assertRaises(FileNotFoundError):
            fic.load_franchisee("never-created")


class TestProfileFindingApply(_IsolatedRootMixin):
    def setUp(self):
        super().setUp()
        fi.create_franchisee("ABC Restaurant Group")

    def test_net_new_scalar_field_applies(self):
        finding = _profile_finding(field="headquarters", value="Dallas, TX")
        result = ifr.import_profile_findings({"findings": [finding]}, dry_run=False)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(self._org()["headquarters"]["value"], "Dallas, TX")

    def test_net_new_list_field_applies(self):
        finding = _profile_finding(field="legal_entities", value="ABC Taco LLC")
        result = ifr.import_profile_findings({"findings": [finding]}, dry_run=False)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(self._org()["legal_entities"][0]["value"], "ABC Taco LLC")

    def test_conflicting_undated_scalar_requires_review_no_overwrite(self):
        fi.add_extended_profile_finding("abc-restaurant-group", "headquarters", "Dallas, TX", as_of="2026-09-01")
        finding = _profile_finding(field="headquarters", value="Austin, TX", observed_at="2026-09-01")
        result = ifr.import_profile_findings({"findings": [finding]}, dry_run=False)
        self.assertEqual(result["applied"], 0)
        self.assertEqual(result["queued_for_review"], 1)
        self.assertEqual(self._org()["headquarters"]["value"], "Dallas, TX")

    def test_newer_dated_scalar_auto_applies_as_successor(self):
        fi.add_extended_profile_finding("abc-restaurant-group", "headquarters", "Dallas, TX", as_of="2026-09-01")
        finding = _profile_finding(field="headquarters", value="Austin, TX", observed_at="2026-09-20")
        result = ifr.import_profile_findings({"findings": [finding]}, dry_run=False)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(self._org()["headquarters"]["value"], "Austin, TX")

    def test_dry_run_writes_nothing(self):
        finding = _profile_finding(field="headquarters", value="Dallas, TX")
        ifr.import_profile_findings({"findings": [finding]}, dry_run=True)
        self.assertIsNone(self._org()["headquarters"]["value"])


class TestDiscoveryFindingValidation(_IsolatedRootMixin):
    def test_missing_required_key_is_rejected(self):
        bad = _discovery_finding(evidence=None)
        result = ifr.import_discovery_findings({"discovered_organizations": [bad]}, dry_run=False)
        self.assertEqual(result["findings_malformed"], 1)


class TestDiscoveryNeverSilentlyMerges(_IsolatedRootMixin):
    def test_similar_existing_name_routes_to_review_not_auto_merge(self):
        fi.create_franchisee("ABC Restaurant Group")
        finding = _discovery_finding(proposed_name="ABC Group")  # similar, not identical
        result = ifr.import_discovery_findings({"discovered_organizations": [finding]}, dry_run=False)
        self.assertEqual(result["applied"], 0)
        self.assertEqual(result["queued_for_review"], 1)
        # no second organization silently created
        self.assertEqual(len(fic.load_registry()["registry"]), 1)

    def test_explicit_matched_slug_enriches_existing_org_not_creates_new(self):
        fi.create_franchisee("ABC Restaurant Group")
        finding = _discovery_finding(proposed_name="ABC Restaurant Group (Taco Bell division)",
                                      matched_existing_franchisee_slug="abc-restaurant-group")
        result = ifr.import_discovery_findings({"discovered_organizations": [finding]}, dry_run=False)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(len(fic.load_registry()["registry"]), 1)
        evidence = fic.load_franchisee("abc-restaurant-group")["evidence"]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["category"], "discovery")

    def test_fabricated_matched_slug_that_does_not_exist_is_not_trusted(self):
        finding = _discovery_finding(proposed_name="Totally New Org", matched_existing_franchisee_slug="does-not-exist")
        result = ifr.import_discovery_findings({"discovered_organizations": [finding]}, dry_run=False)
        self.assertEqual(result["applied"], 1)
        # created as a genuinely new org under its own real slug, not the fabricated one
        self.assertTrue(len(fic.load_registry()["registry"]) == 1)
        with self.assertRaises(FileNotFoundError):
            fic.load_franchisee("does-not-exist")


class TestDiscoveryCreatesNetNewOrganization(_IsolatedRootMixin):
    def test_genuinely_new_name_creates_shell_with_source_discovery(self):
        finding = _discovery_finding(proposed_name="XYZ Franchise Holdings")
        result = ifr.import_discovery_findings({"discovered_organizations": [finding]}, dry_run=False)
        self.assertEqual(result["applied"], 1)
        org = fic.load_franchisee("xyz-franchise-holdings")["organization"]
        self.assertEqual(org["source_discovery"]["method"], "hunter_franchisee_discovery")
        self.assertEqual(org["source_discovery"]["discovered_from_brand"], "brand-taco-bell")

    def test_dry_run_creates_nothing(self):
        finding = _discovery_finding(proposed_name="XYZ Franchise Holdings")
        ifr.import_discovery_findings({"discovered_organizations": [finding]}, dry_run=True)
        self.assertEqual(fic.load_registry()["registry"], [])


if __name__ == "__main__":
    unittest.main()
