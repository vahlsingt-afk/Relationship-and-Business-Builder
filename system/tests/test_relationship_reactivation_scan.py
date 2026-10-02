"""
test_relationship_reactivation_scan.py — RB-2026-09-11.

Coverage for relationship_reactivation_scan.py, the read-only scan that
surfaces untiered baseline contacts whose current_company now matches a
tracked customers_prospects account or ecosystem_intelligence.json
vendor/brand -- a currently-dormant relationship that just became newly
relevant. No mutation, no candidate store; pure surfacing with the same
new-vs-persisting decay dedup as entity_convergence_scan.py. Isolated from
real production data throughout (own tmp baseline, tmp graph, mocked
registry, own tmp state/result paths), same pattern as
test_entity_convergence_scan.py / test_ownership_promotion.py.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import relationship_reactivation_scan as rrs  # noqa: E402


def _entity(entity_id, name, entity_type="brand", aliases=None):
    return {"id": entity_id, "name": name, "entity_type": entity_type,
            "aliases": aliases or [], "attributes": {}, "sources": [],
            "confidence": {}, "domains": ["restaurants"]}


def _graph_with(entities=None):
    return {
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-11",
        "entities": entities or [], "relationships": [], "signals": [],
        "sources": [], "assessments": [], "user_relevance": [], "strategic_recommendations": [],
    }


def _contact(contact_id, name, *, current_company="", rc_tier=None):
    return {
        "id": contact_id, "name": name, "current_company": current_company,
        "current_role": None, "location": None, "linkedin_url": None, "email": None,
        "phone": None, "sources": [], "linkedin_connected_on": None, "signal_class": "VC",
        "rc_state": None, "rc_tier": rc_tier, "last_touch": None, "circles": [], "tags": [],
        "notes": None, "relationship_health": {},
    }


def _registry(entries):
    return {"registry": entries}


def _account(account_id, aliases):
    return {"account_id": account_id, "aliases": aliases, "status": "active_engagement"}


DEL_TACO = _entity("brand-del-taco", "Del Taco", aliases=["Del Taco Restaurants"])
PAR = _entity("company-par-technology", "PAR Technology", entity_type="vendor", aliases=["PAR"])
MCDONALDS = _entity("brand-mcdonald-s", "McDonald's", aliases=["McDonald's Corporation", "McDonalds"])


class _IsolatedMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)

        self._orig_baseline_path = rrs.core.BASELINE_PATH
        self._orig_graph_path = rrs.ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        self._orig_state = rrs.STATE_PATH
        self._orig_result = rrs.RESULT_PATH

        self._baseline_path = tmp / "baseline_index.json"
        self._graph_path = tmp / "ecosystem_intelligence.json"
        rrs.core.BASELINE_PATH = self._baseline_path
        rrs.ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._graph_path
        rrs.STATE_PATH = tmp / "state.json"
        rrs.RESULT_PATH = tmp / "result.json"

        self._registry = _registry([])
        self._baseline: list[dict] = []
        self._graph = _graph_with([])

    def tearDown(self):
        rrs.core.BASELINE_PATH = self._orig_baseline_path
        rrs.ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        rrs.STATE_PATH = self._orig_state
        rrs.RESULT_PATH = self._orig_result
        self._tmpdir.cleanup()

    def _write_baseline(self, contacts: list[dict]) -> None:
        self._baseline_path.write_text(json.dumps(contacts), encoding="utf-8")

    def _write_graph(self, entities: list[dict]) -> None:
        self._graph_path.write_text(json.dumps(_graph_with(entities)), encoding="utf-8")

    def _run_scan(self, registry: dict) -> dict:
        with patch.object(rrs.cpc, "load_registry", return_value=registry):
            return rrs.run_scan()


class TestPriorityAccountMatch(_IsolatedMixin, unittest.TestCase):
    def test_priority_account_match_found(self):
        self._write_baseline([_contact("c1", "Al Test", current_company="Del Taco Restaurants")])
        self._write_graph([DEL_TACO])
        registry = _registry([_account("acct-del-taco", ["Del Taco", "Del Taco Restaurants"])])
        result = self._run_scan(registry)
        self.assertEqual(len(result["new_findings"]), 1)
        finding = result["new_findings"][0]
        self.assertEqual(finding["match_type"], "priority_account")
        self.assertEqual(finding["matched_id"], "acct-del-taco")

    def test_priority_account_match_outranks_ecosystem_match_for_same_company(self):
        # "Del Taco Restaurants" resolves in BOTH the priority index and the
        # ecosystem graph -- priority_account must win, not ecosystem_brand.
        self._write_baseline([_contact("c1", "Al Test", current_company="Del Taco Restaurants")])
        self._write_graph([DEL_TACO])
        registry = _registry([_account("acct-del-taco", ["Del Taco Restaurants"])])
        result = self._run_scan(registry)
        self.assertEqual(result["new_findings"][0]["match_type"], "priority_account")

    def test_display_name_prefers_non_slug_alias(self):
        # Real registry entries mix a bare slug ("churchs-chicken") with the
        # proper display name in no fixed order -- confirmed live.
        self._write_baseline([_contact("c1", "Brie L", current_company="Church's Texas Chicken")])
        self._write_graph([])
        registry = _registry([_account("acct-churchs-texas-chicken",
                                        ["churchs-chicken", "Church's Texas Chicken"])])
        result = self._run_scan(registry)
        self.assertEqual(result["new_findings"][0]["matched_name"], "Church's Texas Chicken")

    def test_pre_engagement_account_also_matches(self):
        # Reactivation candidates matter for prospects being researched too,
        # not just active_engagement accounts -- no engagement_tier filter.
        self._write_baseline([_contact("c1", "Al Test", current_company="Red Robin")])
        self._write_graph([])
        registry = _registry([{"account_id": "acct-red-robin", "aliases": ["Red Robin"],
                                "status": "pre_engagement"}])
        result = self._run_scan(registry)
        self.assertEqual(len(result["new_findings"]), 1)
        self.assertEqual(result["new_findings"][0]["match_type"], "priority_account")


class TestEcosystemMatch(_IsolatedMixin, unittest.TestCase):
    def test_vendor_match_typed_correctly(self):
        self._write_baseline([_contact("c1", "Cara T", current_company="PAR Technology")])
        self._write_graph([PAR])
        result = self._run_scan(_registry([]))
        self.assertEqual(len(result["new_findings"]), 1)
        self.assertEqual(result["new_findings"][0]["match_type"], "ecosystem_vendor")
        self.assertEqual(result["new_findings"][0]["matched_id"], "company-par-technology")

    def test_brand_match_typed_correctly(self):
        self._write_baseline([_contact("c1", "Brian G", current_company="McDonald's")])
        self._write_graph([MCDONALDS])
        result = self._run_scan(_registry([]))
        self.assertEqual(result["new_findings"][0]["match_type"], "ecosystem_brand")

    def test_alias_match_resolves_same_as_canonical_name(self):
        self._write_baseline([_contact("c1", "Jason G", current_company="McDonald's Corporation")])
        self._write_graph([MCDONALDS])
        result = self._run_scan(_registry([]))
        self.assertEqual(len(result["new_findings"]), 1)
        self.assertEqual(result["new_findings"][0]["matched_id"], "brand-mcdonald-s")

    def test_no_match_for_irrelevant_company(self):
        self._write_baseline([_contact("c1", "Random Person", current_company="Southern Tier Bookkeeping")])
        self._write_graph([DEL_TACO, PAR, MCDONALDS])
        result = self._run_scan(_registry([]))
        self.assertEqual(result["new_findings"], [])
        self.assertEqual(result["persisting_findings"], [])


class TestTierExclusion(_IsolatedMixin, unittest.TestCase):
    def test_inner_tier_excluded(self):
        self._write_baseline([_contact("c1", "Inner Person", current_company="McDonald's", rc_tier="inner")])
        self._write_graph([MCDONALDS])
        result = self._run_scan(_registry([]))
        self.assertEqual(result["new_findings"], [])

    def test_broader_tier_excluded(self):
        self._write_baseline([_contact("c1", "Broader Person", current_company="McDonald's", rc_tier="broader")])
        self._write_graph([MCDONALDS])
        result = self._run_scan(_registry([]))
        self.assertEqual(result["new_findings"], [])

    def test_dormant_valuable_tier_included(self):
        self._write_baseline([_contact("c1", "Dormant Person", current_company="McDonald's",
                                        rc_tier="dormant_valuable")])
        self._write_graph([MCDONALDS])
        result = self._run_scan(_registry([]))
        self.assertEqual(len(result["new_findings"]), 1)

    def test_untiered_contact_included(self):
        self._write_baseline([_contact("c1", "Untiered Person", current_company="McDonald's", rc_tier=None)])
        self._write_graph([MCDONALDS])
        result = self._run_scan(_registry([]))
        self.assertEqual(len(result["new_findings"]), 1)


class TestBlankCompany(_IsolatedMixin, unittest.TestCase):
    def test_missing_current_company_skipped(self):
        self._write_baseline([_contact("c1", "No Company Person", current_company="")])
        self._write_graph([MCDONALDS])
        result = self._run_scan(_registry([]))
        self.assertEqual(result["new_findings"], [])

    def test_whitespace_only_current_company_skipped(self):
        self._write_baseline([_contact("c1", "Whitespace Person", current_company="   ")])
        self._write_graph([MCDONALDS])
        result = self._run_scan(_registry([]))
        self.assertEqual(result["new_findings"], [])


class TestNewVsPersistingState(_IsolatedMixin, unittest.TestCase):
    def test_first_ever_finding_is_new(self):
        self._write_baseline([_contact("c1", "Brian G", current_company="McDonald's")])
        self._write_graph([MCDONALDS])
        result = self._run_scan(_registry([]))
        self.assertEqual(len(result["new_findings"]), 1)
        self.assertEqual(result["new_findings"][0]["first_seen"], result["generated_at"][:10])

    def test_same_match_next_run_is_persisting_not_new(self):
        self._write_baseline([_contact("c1", "Brian G", current_company="McDonald's")])
        self._write_graph([MCDONALDS])
        first = self._run_scan(_registry([]))
        second = self._run_scan(_registry([]))
        self.assertEqual(len(first["new_findings"]), 1)
        self.assertEqual(len(second["new_findings"]), 0)
        self.assertEqual(len(second["persisting_findings"]), 1)

    def test_persisting_finding_keeps_original_first_seen_date(self):
        self._write_baseline([_contact("c1", "Brian G", current_company="McDonald's")])
        self._write_graph([MCDONALDS])
        first = self._run_scan(_registry([]))
        second = self._run_scan(_registry([]))
        self.assertEqual(
            second["persisting_findings"][0]["first_seen"],
            first["new_findings"][0]["first_seen"],
        )

    def test_match_type_change_resurfaces_as_new(self):
        # A contact who moves from an ecosystem brand to a priority account
        # should read as new information again, not silently stay persisting
        # under the old match_type.
        self._write_baseline([_contact("c1", "Mover Person", current_company="McDonald's")])
        self._write_graph([MCDONALDS])
        self._run_scan(_registry([]))

        self._write_baseline([_contact("c1", "Mover Person", current_company="McDonald's")])
        registry2 = _registry([_account("acct-mcdonalds", ["McDonald's"])])
        second = self._run_scan(registry2)
        self.assertEqual(len(second["new_findings"]), 1)
        self.assertEqual(second["new_findings"][0]["match_type"], "priority_account")
        self.assertEqual(len(second["persisting_findings"]), 0)


class TestResultPersistence(_IsolatedMixin, unittest.TestCase):
    def test_result_written_to_result_path(self):
        self._write_baseline([_contact("c1", "Brian G", current_company="McDonald's")])
        self._write_graph([MCDONALDS])
        self._run_scan(_registry([]))
        self.assertTrue(rrs.RESULT_PATH.exists())
        saved = json.loads(rrs.RESULT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["new_findings"]), 1)

    def test_load_last_result_reads_what_was_saved(self):
        self._write_baseline([_contact("c1", "Brian G", current_company="McDonald's")])
        self._write_graph([MCDONALDS])
        self._run_scan(_registry([]))
        loaded = rrs.load_last_result()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["contacts_scanned"], 1)

    def test_load_last_result_returns_none_when_never_run(self):
        self.assertIsNone(rrs.load_last_result())


class TestRanking(_IsolatedMixin, unittest.TestCase):
    def test_priority_account_ranked_before_ecosystem_matches(self):
        self._write_baseline([
            _contact("c1", "Vendor Person", current_company="PAR Technology"),
            _contact("c2", "Priority Person", current_company="Del Taco Restaurants"),
            _contact("c3", "Brand Person", current_company="McDonald's"),
        ])
        self._write_graph([DEL_TACO, PAR, MCDONALDS])
        registry = _registry([_account("acct-del-taco", ["Del Taco Restaurants"])])
        result = self._run_scan(registry)
        match_types = [f["match_type"] for f in result["new_findings"]]
        self.assertEqual(match_types, ["priority_account", "ecosystem_vendor", "ecosystem_brand"])


if __name__ == "__main__":
    unittest.main()
