"""test_import_brand_company_profile_research.py — 2026-10-03.

RB live incident: Charleys' enterprise_account_profile packet finalized
clean but every one of its mutation_proposals came back unhandled --
hunter_change_dispatch.py never had a real writer for the brand
company-profile payload. This importer closes that gap.

Isolated against a temp brand_profile_common.ROOT and a monkeypatched
resolve_brand_entity (never touches real production brand profiles or
the real ecosystem_intelligence.json). mutation_policy's receipts path is
already redirected globally by conftest.py.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import brand_profile_common as bpc  # noqa: E402
import import_brand_company_profile_research as ibcp  # noqa: E402
import tech_stack_relationship_promotion as tsrp  # noqa: E402


def _entity(brand_id="brand-charleys-philly-steaks", name="Charleys Philly Steaks"):
    return {"id": brand_id, "name": name, "entity_type": "brand", "attributes": {}}


def _packet(records, findings=None, sources=None):
    return {
        "schema": "rb.hunter_research_packet.v1",
        "payload_schema": "rb.brand_company_profile.v1",
        "payload": {"records": records},
        "findings": findings or [],
        "source_ledger": sources or [{"source_id": "src-001", "url": "https://example.com/charleys"}],
    }


def _record(record_type, **overrides):
    base = {"record_type": record_type, "target_key": "company:brand-charleys-philly-steaks", "source_ids": ["src-001"], "finding_ids": []}
    base.update(overrides)
    return base


class _IsolatedMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_root = bpc.ROOT
        bpc.ROOT = Path(self._tmpdir.name)
        self._resolve_patch = patch.object(bpc, "resolve_brand_entity", return_value=_entity())
        self._resolve_patch.start()
        self._tsrp_tmpdir = tempfile.TemporaryDirectory()
        self._tsrp_store_patch = patch.object(tsrp, "STORE_PATH", Path(self._tsrp_tmpdir.name) / "candidates.json")
        self._tsrp_store_patch.start()
        self._graph_patch = patch.object(ibcp.ei, "_read_graph", return_value={"entities": [_entity()], "relationships": []})
        self._graph_patch.start()

    def tearDown(self):
        self._graph_patch.stop()
        self._tsrp_store_patch.stop()
        self._tsrp_tmpdir.cleanup()
        self._resolve_patch.stop()
        bpc.ROOT = self._orig_root
        self._tmpdir.cleanup()

    def _profile(self):
        return bpc.load_profile("brand-charleys-philly-steaks")


class TestRecordValidation(_IsolatedMixin):
    def test_unknown_record_type_is_rejected(self):
        record = _record("not_a_real_type")
        result = ibcp.import_records(_packet([record]), dry_run=False)
        self.assertEqual(result["records_malformed"], 1)
        self.assertEqual(result["applied"], 0)

    def test_missing_target_key_is_rejected(self):
        record = _record("company_identity")
        del record["target_key"]
        result = ibcp.import_records(_packet([record]), dry_run=False)
        self.assertEqual(result["records_malformed"], 1)


class TestIdentityUnresolved(_IsolatedMixin):
    def test_non_company_target_routes_to_identity_unresolved(self):
        record = _record("company_identity", target_key="competitor:some-vendor")
        result = ibcp.import_records(_packet([record]), dry_run=False)
        self.assertEqual(result["identity_unresolved"], 1)
        self.assertEqual(result["applied"], 0)

    def test_unknown_brand_entity_routes_to_identity_unresolved_not_creation(self):
        with patch.object(bpc, "resolve_brand_entity", side_effect=bpc.NotFoundError("no such brand")):
            record = _record("company_identity")
            result = ibcp.import_records(_packet([record]), dry_run=False)
        self.assertEqual(result["identity_unresolved"], 1)


class TestCompanyIdentity(_IsolatedMixin):
    def test_applies_legal_entity_headquarters_and_ownership(self):
        record = _record("company_identity", legal_entity="Gosh Enterprises, Inc.",
                          headquarters="Columbus, Ohio", ownership_type="private", owner="Charley Shin")
        result = ibcp.import_records(_packet([record]), dry_run=False)
        self.assertEqual(result["applied"], 3)
        profile = self._profile()
        self.assertEqual(profile["identity"]["legal_entity"]["value"], "Gosh Enterprises, Inc.")
        self.assertEqual(profile["identity"]["hq_city_state"]["value"], "Columbus, Ohio")
        self.assertIn("private", profile["identity"]["parent_ownership"]["value"])
        self.assertIn("Charley Shin", profile["identity"]["parent_ownership"]["value"])

    def test_dry_run_writes_nothing(self):
        record = _record("company_identity", legal_entity="Gosh Enterprises, Inc.")
        ibcp.import_records(_packet([record]), dry_run=True)
        self.assertIsNone(self._profile())


class TestFootprintSnapshot(_IsolatedMixin):
    def test_applies_net_new_footprint_values(self):
        record = _record("footprint_snapshot", as_of="2025-12-31", us_and_territories_total=826,
                          us_and_territories_franchised=766, us_and_territories_company_owned=60)
        result = ibcp.import_records(_packet([record]), dry_run=False)
        self.assertEqual(result["applied"], 3)
        profile = self._profile()
        self.assertEqual(profile["footprint"]["total_units"]["value"], 826)
        self.assertEqual(profile["footprint"]["franchised_units"]["value"], 766)

    def test_human_reviewed_footprint_field_is_never_overwritten(self):
        with patch.object(bpc, "resolve_brand_entity", return_value=_entity()):
            profile = bpc.get_profile("brand-charleys-philly-steaks", persist=True)
            profile["footprint"]["total_units"] = bpc.field(999, last_reviewed_by="human:todd")
            bpc.save_profile("brand-charleys-philly-steaks", profile)
        record = _record("footprint_snapshot", us_and_territories_total=826)
        result = ibcp.import_records(_packet([record]), dry_run=False)
        self.assertEqual(result["applied"], 0)
        self.assertEqual(self._profile()["footprint"]["total_units"]["value"], 999)


class TestLeadershipSnapshot(_IsolatedMixin):
    def test_independently_verified_finding_goes_to_confirmed_bucket(self):
        record = _record("leadership_snapshot", people=[{"name": "Charley Shin", "title": "CEO"}], finding_ids=["f-001"])
        findings = [{"finding_id": "f-001", "finding_type": "independently_verified"}]
        result = ibcp.import_records(_packet([record], findings=findings), dry_run=False)
        self.assertEqual(result["applied"], 1)
        profile = self._profile()
        self.assertEqual(len(profile["leadership"]["confirmed"]), 1)
        self.assertEqual(profile["leadership"]["confirmed"][0]["name"], "Charley Shin")
        self.assertEqual(profile["leadership"]["reported_unverified"], [])

    def test_marketplace_reported_finding_goes_to_reported_unverified_bucket(self):
        record = _record("leadership_snapshot", people=[{"name": "Howard Fickel", "title": "CFO"}], finding_ids=["f-002"])
        findings = [{"finding_id": "f-002", "finding_type": "marketplace_reported"}]
        result = ibcp.import_records(_packet([record], findings=findings), dry_run=False)
        self.assertEqual(result["applied"], 1)
        profile = self._profile()
        self.assertEqual(len(profile["leadership"]["reported_unverified"]), 1)
        self.assertEqual(profile["leadership"]["confirmed"], [])

    def test_dedupes_same_name_across_runs(self):
        record = _record("leadership_snapshot", people=[{"name": "Charley Shin", "title": "CEO"}], finding_ids=["f-001"])
        findings = [{"finding_id": "f-001", "finding_type": "independently_verified"}]
        ibcp.import_records(_packet([record], findings=findings), dry_run=False)
        result = ibcp.import_records(_packet([record], findings=findings), dry_run=False)
        self.assertEqual(result["applied"], 0)
        self.assertEqual(len(self._profile()["leadership"]["confirmed"]), 1)


class TestWholeRecordScalars(_IsolatedMixin):
    def test_franchise_disclosure_applies_as_one_structured_value(self):
        record = _record("franchise_disclosure", issuance_date="2026-04-28",
                          technology_mandates=["Approved POS required"], governance="HQ-approved suppliers")
        result = ibcp.import_records(_packet([record]), dry_run=False)
        self.assertEqual(result["applied"], 1)
        value = self._profile()["franchise_disclosure"]["value"]
        self.assertEqual(value["technology_mandates"], ["Approved POS required"])
        self.assertEqual(value["governance"], "HQ-approved suppliers")
        self.assertNotIn("target_key", value)  # bookkeeping keys excluded from the stored value

    def test_financial_operating_snapshot_applies_as_one_structured_value(self):
        record = _record("financial_operating_snapshot", as_of="2025-12-31",
                          franchisor_financials={"revenue": 53047475})
        result = ibcp.import_records(_packet([record]), dry_run=False)
        self.assertEqual(result["applied"], 1)
        value = self._profile()["financial_operating_health"]["value"]
        self.assertEqual(value["franchisor_financials"]["revenue"], 53047475)

    def test_conflicting_undated_value_requires_review_no_overwrite(self):
        record1 = _record("franchise_disclosure", issuance_date="2026-04-28", governance="Original governance text")
        ibcp.import_records(_packet([record1]), dry_run=False)
        record2 = _record("franchise_disclosure", issuance_date="2026-04-28", governance="Conflicting governance text")
        result = ibcp.import_records(_packet([record2]), dry_run=False)
        self.assertEqual(result["applied"], 0)
        self.assertEqual(self._profile()["franchise_disclosure"]["value"]["governance"], "Original governance text")


class TestTechnologyRecords(_IsolatedMixin):
    def test_technology_relationship_becomes_a_pending_candidate_not_a_direct_write(self):
        record = _record("technology_relationship", category="pos", vendor="PAR Technology", product="Brink POS")
        result = ibcp.import_records(_packet([record]), dry_run=False)
        self.assertEqual(result["applied"], 1)  # a new pending candidate counts as "applied" (real durable state change)
        store = tsrp._load_store()
        candidates = list(store["candidates"].values())
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["vendor_name"], "PAR Technology")
        self.assertEqual(candidates[0]["brand_id"], "brand-charleys-philly-steaks")
        self.assertEqual(candidates[0]["status"], "proposed_pending_confirmation")
        # never a direct canonical graph write -- no brand_profile.json touched by this path
        self.assertIsNone(self._profile())

    def test_technology_observation_also_becomes_a_pending_candidate(self):
        record = _record("technology_observation", category="digital_menu_board", vendor="The Howard Company")
        result = ibcp.import_records(_packet([record]), dry_run=False)
        self.assertEqual(result["applied"], 1)
        store = tsrp._load_store()
        self.assertEqual(len(store["candidates"]), 1)

    def test_incomplete_technology_record_is_skipped_not_fabricated(self):
        record = _record("technology_relationship", category="pos")  # no vendor
        result = ibcp.import_records(_packet([record]), dry_run=False)
        self.assertEqual(result["applied"], 0)
        self.assertEqual(result["queued_for_review"], 1)

    def test_dry_run_creates_no_candidate(self):
        record = _record("technology_relationship", category="pos", vendor="PAR Technology")
        ibcp.import_records(_packet([record]), dry_run=True)
        store = tsrp._load_store()
        self.assertEqual(store.get("candidates", {}), {})


class TestRealCharleysPacketShape(_IsolatedMixin):
    def test_all_seven_real_record_types_route_without_malformed_or_unresolved(self):
        records = [
            _record("company_identity", legal_entity="Gosh Enterprises, Inc.", headquarters="Columbus, OH", ownership_type="private", owner="Charley Shin"),
            _record("footprint_snapshot", as_of="2025-12-31", us_and_territories_total=826),
            _record("leadership_snapshot", people=[{"name": "Charley Shin", "title": "CEO"}]),
            _record("franchise_disclosure", issuance_date="2026-04-28", governance="HQ-approved suppliers"),
            _record("financial_operating_snapshot", as_of="2025-12-31", franchisor_financials={"revenue": 1}),
            _record("technology_relationship", category="pos", vendor="PAR Technology"),
            _record("technology_observation", category="digital_menu_board", vendor="The Howard Company"),
        ]
        result = ibcp.import_records(_packet(records), dry_run=False)
        self.assertEqual(result["records_malformed"], 0)
        self.assertEqual(result["identity_unresolved"], 0)
        self.assertEqual(result["records_received"], 7)
        self.assertEqual(set(result["by_record_type"].keys()), {
            "company_identity", "footprint_snapshot", "leadership_snapshot",
            "franchise_disclosure", "financial_operating_snapshot",
            "technology_relationship", "technology_observation",
        })


if __name__ == "__main__":
    unittest.main()
