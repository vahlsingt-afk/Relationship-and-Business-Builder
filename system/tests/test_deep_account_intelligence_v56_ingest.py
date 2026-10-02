"""
test_deep_account_intelligence_v56_ingest.py — RB-2026-09-27.

Coverage for deep_account_intelligence_v56_ingest.py -- the "RBB Top 500
Restaurant Intelligence Expansion" ingest. Isolated from real production
data throughout (own tmp graph, own tmp candidate store), same pattern as
test_deep_research_dataset_ingest.py / test_multibrand_franchisee_operator_
ingest.py. Includes a direct-real-validator test (see TestSchemaValidation)
for the same reason those sibling suites do: ei._write_graph()'s own
validator subprocess is blind to an isolated test's monkeypatched
core.ECOSYSTEM_INTELLIGENCE_PATH (it always checks the real production
file by a hardcoded path), so it provides no real schema coverage here on
its own.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "system" / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ei = _load("ecosystem_intelligence")
tsrp = _load("tech_stack_relationship_promotion")
sys.path.insert(0, str(SCRIPTS_DIR))
dai = _load("deep_account_intelligence_v56_ingest")


def _graph_with(entities=None, relationships=None) -> dict:
    return {
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-27",
        "domain_packs": ["restaurants"],
        "entities": entities or [], "relationships": relationships or [], "signals": [],
        "sources": [], "assessments": [], "user_relevance": [], "strategic_recommendations": [],
    }


def _brand(entity_id, name, attributes=None, aliases=None):
    return {"id": entity_id, "name": name, "entity_type": "brand", "subtype": "restaurant_brand",
            "status": "active", "aliases": aliases or [], "attributes": attributes or {}, "sources": [],
            "confidence": {"level": "high"}, "domains": ["restaurants"]}


def _vendor(entity_id, name):
    return {"id": entity_id, "name": name, "entity_type": "vendor", "status": "active", "aliases": [],
            "attributes": {}, "sources": [], "confidence": {"level": "high"}, "domains": ["restaurants"]}


def _dataset(records: list[dict]) -> dict:
    return {"dataset": {"name": "Test Dataset", "version": "test-v56", "records": records}}


class TestDeepAccountIntelligenceV56Ingest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        tmp = Path(self._tmpdir)
        self._graph_path = tmp / "ecosystem_intelligence.json"
        self._store_path = tmp / "acct56_candidates.json"
        self._dataset_path = tmp / "dataset.json"

        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        self._orig_store_path = dai.STORE_PATH
        self._orig_tsrp_store = tsrp.STORE_PATH
        self._tsrp_store_path = tmp / "tech_stack_candidates.json"
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._graph_path
        dai.STORE_PATH = self._store_path
        tsrp.STORE_PATH = self._tsrp_store_path

    def tearDown(self):
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        dai.STORE_PATH = self._orig_store_path
        tsrp.STORE_PATH = self._orig_tsrp_store
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_graph(self, graph: dict) -> None:
        self._graph_path.write_text(json.dumps(graph), encoding="utf-8")

    def _write_dataset(self, dataset: dict) -> Path:
        self._dataset_path.write_text(json.dumps(dataset), encoding="utf-8")
        return self._dataset_path

    def _read_graph(self) -> dict:
        return json.loads(self._graph_path.read_text())

    def _entity(self, graph: dict, entity_id: str) -> dict:
        return next(e for e in graph["entities"] if e["id"] == entity_id)

    # ---- brand resolution ---------------------------------------------------

    def test_unresolved_brand_is_skipped_never_created(self):
        self._write_graph(_graph_with())
        path = self._write_dataset(_dataset([{"brand_name": "Totally Unknown Chain", "scale": {"units": 500}}]))
        result = dai.ingest(path)
        self.assertEqual(result["counts"]["brands_unresolved"], 1)
        self.assertEqual(self._read_graph()["entities"], [])

    # ---- scale ----------------------------------------------------------------

    def test_scale_net_new_applies_immediately(self):
        self._write_graph(_graph_with(entities=[_brand("brand-subway", "Subway")]))
        path = self._write_dataset(_dataset([
            {"brand_name": "Subway", "canonical_metadata": {"last_verified": "2026-05-27"},
             "scale": {"units": 19502, "franchisee_owned_units": 19502,
                       "company_owned_units": 0, "ownership_confidence": 1}},
        ]))
        result = dai.ingest(path)
        self.assertEqual(result["counts"]["scale_applied"], 1)
        graph = self._read_graph()
        entity = self._entity(graph, "brand-subway")
        scale = entity["attributes"]["deep_research_profile"]["scale_snapshot"]["units"]
        self.assertEqual(scale["value"]["units"], 19502)
        # RB-2026-09-27, Todd: "confidence levels, data date...are
        # important" -- scale/canonical_technology carry no field-level
        # date of their own in this dataset, so canonical_metadata.
        # last_verified (the record's real verification date) must be
        # captured as as_of rather than left silently absent.
        self.assertEqual(scale["as_of"], "2026-05-27")

    def test_scale_conflicting_value_routes_to_review_not_overwritten(self):
        existing = {"deep_research_profile": {"scale_snapshot": {"units": {"value": {"units": 100}}}}}
        self._write_graph(_graph_with(entities=[_brand("brand-subway", "Subway", attributes=existing)]))
        path = self._write_dataset(_dataset([{"brand_name": "Subway", "scale": {"units": 19502}}]))
        result = dai.ingest(path)
        self.assertEqual(result["counts"]["scale_queued"], 1)
        self.assertEqual(result["counts"]["scale_applied"], 0)
        graph = self._read_graph()
        entity = self._entity(graph, "brand-subway")
        self.assertEqual(entity["attributes"]["deep_research_profile"]["scale_snapshot"]["units"]["value"], {"units": 100})
        pending = dai.pending_candidates()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["proposed"]["value"], {"units": 19502})

    def test_scale_matching_value_is_noop(self):
        existing = {"deep_research_profile": {"scale_snapshot": {"units": {"value": {"units": 19502}}}}}
        self._write_graph(_graph_with(entities=[_brand("brand-subway", "Subway", attributes=existing)]))
        path = self._write_dataset(_dataset([{"brand_name": "Subway", "scale": {"units": 19502}}]))
        result = dai.ingest(path)
        self.assertEqual(result["counts"]["scale_unchanged"], 1)
        self.assertEqual(dai.pending_candidates(), [])

    # ---- canonical_technology ---------------------------------------------------

    def test_canonical_technology_net_new_applies_and_proposes_known_vendor(self):
        self._write_graph(_graph_with(entities=[
            _brand("brand-subway", "Subway"),
            _vendor("vendor-oracle", "Oracle"),
        ]))
        path = self._write_dataset(_dataset([
            {"brand_name": "Subway", "canonical_metadata": {"last_verified": "2026-05-27"},
             "canonical_technology": {
                "POS": {"value": "Oracle — Oracle MICROS POS", "confidence": 0.86, "status": "canonical_evidence"},
            }},
        ]))
        result = dai.ingest(path)
        self.assertEqual(result["counts"]["canonical_technology_applied"], 1)
        self.assertEqual(result["counts"]["technology_vendor_proposed"], 1)
        graph = self._read_graph()
        entity = self._entity(graph, "brand-subway")
        stored = entity["attributes"]["deep_research_profile"]["canonical_technology"]["POS"]
        self.assertEqual(stored["value"], "Oracle — Oracle MICROS POS")
        self.assertEqual(stored["as_of"], "2026-05-27")
        candidates = list(tsrp._load_store()["candidates"].values())
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["vendor_name"], "Oracle")

    def test_canonical_technology_conflicting_value_routes_to_review(self):
        existing = {"deep_research_profile": {"canonical_technology": {"POS": {"value": "In-house custom POS"}}}}
        self._write_graph(_graph_with(entities=[_brand("brand-subway", "Subway", attributes=existing)]))
        path = self._write_dataset(_dataset([
            {"brand_name": "Subway", "canonical_technology": {"POS": {"value": "Oracle MICROS", "confidence": 0.9}}},
        ]))
        result = dai.ingest(path)
        self.assertEqual(result["counts"]["canonical_technology_queued"], 1)
        graph = self._read_graph()
        entity = self._entity(graph, "brand-subway")
        self.assertEqual(entity["attributes"]["deep_research_profile"]["canonical_technology"]["POS"]["value"], "In-house custom POS")

    # ---- franchise_disclosure ---------------------------------------------------

    def test_franchise_disclosure_net_new_applies(self):
        self._write_graph(_graph_with(entities=[_brand("brand-subway", "Subway")]))
        path = self._write_dataset(_dataset([
            {"brand_name": "Subway", "franchise_disclosure": {
                "confidence": 99, "hyperlink": "https://franchiseevidence.com/franchise/subway/",
                "document_year": 2026,
                "findings": {"pos": "SubwayPOS", "restaurant_technology_fee": "~$75/month"},
            }},
        ]))
        result = dai.ingest(path)
        self.assertEqual(result["counts"]["franchise_disclosure_applied"], 1)
        graph = self._read_graph()
        entity = self._entity(graph, "brand-subway")
        stored = entity["attributes"]["deep_research_profile"]["franchise_disclosure"]["findings"]
        # RB-2026-09-27: `value` must be the findings dict directly, NOT
        # the whole franchise_disclosure wrapper -- a first version of
        # this test asserted stored["value"]["findings"]["pos"] (nested
        # one level too deep), which passed even though the real ingest
        # script was storing the whole wrapper as `value`. Caught live
        # when the brief rendered a raw Python dict dump instead of clean
        # fee/tech facts -- fixed in both the ingest script and here.
        self.assertEqual(stored["value"]["pos"], "SubwayPOS")
        self.assertEqual(stored["value"]["restaurant_technology_fee"], "~$75/month")
        self.assertEqual(stored["confidence"], 99)
        self.assertEqual(stored["as_of"], "2026")
        self.assertEqual(stored["source_url"], "https://franchiseevidence.com/franchise/subway/")

    # ---- current_leadership ------------------------------------------------------

    def test_current_leadership_net_new_person_applied(self):
        self._write_graph(_graph_with(entities=[_brand("brand-subway", "Subway")]))
        path = self._write_dataset(_dataset([
            {"brand_name": "Subway", "current_leadership": [
                {"name": "Jonathan Fitzpatrick", "title": "Chief Executive Officer", "function": "CEO",
                 "status": "confirmed_current", "confidence": 100, "source_url": "https://newsroom.subway.com/Leadership"},
            ]},
        ]))
        result = dai.ingest(path)
        self.assertEqual(result["counts"]["current_leadership_applied"], 1)
        graph = self._read_graph()
        entity = self._entity(graph, "brand-subway")
        roster = entity["attributes"]["deep_research_profile"]["current_leadership"]
        self.assertEqual(roster[0]["name"], "Jonathan Fitzpatrick")

    def test_current_leadership_conflicting_title_routes_to_review(self):
        self._write_graph(_graph_with(entities=[_brand("brand-subway", "Subway")]))
        path = self._write_dataset(_dataset([
            {"brand_name": "Subway", "current_leadership": [
                {"name": "Jonathan Fitzpatrick", "title": "Chief Executive Officer", "status": "confirmed_current"},
            ]},
        ]))
        dai.ingest(path)
        path2 = self._write_dataset(_dataset([
            {"brand_name": "Subway", "current_leadership": [
                {"name": "Jonathan Fitzpatrick", "title": "Executive Chairman", "status": "confirmed_current"},
            ]},
        ]))
        result = dai.ingest(path2)
        self.assertEqual(result["counts"]["current_leadership_queued"], 1)
        self.assertEqual(result["counts"]["current_leadership_applied"], 0)
        graph = self._read_graph()
        entity = self._entity(graph, "brand-subway")
        roster = entity["attributes"]["deep_research_profile"]["current_leadership"]
        self.assertEqual(roster[0]["title"], "Chief Executive Officer")  # unchanged
        self.assertEqual(len(dai.pending_candidates()), 1)

    def test_current_leadership_matching_person_is_noop(self):
        self._write_graph(_graph_with(entities=[_brand("brand-subway", "Subway")]))
        path = self._write_dataset(_dataset([
            {"brand_name": "Subway", "current_leadership": [
                {"name": "Jonathan Fitzpatrick", "title": "Chief Executive Officer", "status": "confirmed_current"},
            ]},
        ]))
        dai.ingest(path)
        result2 = dai.ingest(path)
        self.assertEqual(result2["counts"]["current_leadership_unchanged"], 1)
        self.assertEqual(result2["counts"]["current_leadership_applied"], 0)
        self.assertEqual(dai.pending_candidates(), [])

    # ---- evidence ledgers ---------------------------------------------------------

    def test_deep_pass_public_evidence_appended_net_new_and_deduped_on_rerun(self):
        self._write_graph(_graph_with(entities=[_brand("brand-subway", "Subway")]))
        path = self._write_dataset(_dataset([
            {"brand_name": "Subway", "deep_pass_public_evidence": [
                {"topic": "technology_stack", "finding": "2026 FDD requires SubwayPOS.",
                 "confidence": 96, "source_url": "https://franchiseevidence.com/franchise/subway/"},
            ]},
        ]))
        result = dai.ingest(path)
        self.assertEqual(result["counts"]["deep_pass_public_evidence_added"], 1)
        result2 = dai.ingest(path)
        self.assertEqual(result2["counts"]["deep_pass_public_evidence_added"], 0)
        graph = self._read_graph()
        entity = self._entity(graph, "brand-subway")
        self.assertEqual(len(entity["attributes"]["deep_research_profile"]["deep_pass_public_evidence"]), 1)

    def test_account_profile_fresh_evidence_appended_net_new(self):
        self._write_graph(_graph_with(entities=[_brand("brand-subway", "Subway")]))
        path = self._write_dataset(_dataset([
            {"brand_name": "Subway", "deep_account_profile": {"fresh_evidence": [
                {"category": "payments", "finding": "FreedomPay selected 2025.", "source_url": "https://x.com"},
            ]}},
        ]))
        result = dai.ingest(path)
        self.assertEqual(result["counts"]["account_profile_evidence_added"], 1)
        graph = self._read_graph()
        entity = self._entity(graph, "brand-subway")
        self.assertEqual(len(entity["attributes"]["deep_research_profile"]["account_profile_evidence"]), 1)

    # ---- confirm / reject ----------------------------------------------------------

    def test_confirm_applies_queued_scalar_value(self):
        existing = {"deep_research_profile": {"scale_snapshot": {"units": {"value": {"units": 100}}}}}
        self._write_graph(_graph_with(entities=[_brand("brand-subway", "Subway", attributes=existing)]))
        path = self._write_dataset(_dataset([{"brand_name": "Subway", "scale": {"units": 19502}}]))
        dai.ingest(path)
        cid = dai.pending_candidates()[0]["candidate_id"]
        result = dai.record_proposal(cid, confirmed=True)
        self.assertEqual(result["status"], "confirmed")
        graph = self._read_graph()
        entity = self._entity(graph, "brand-subway")
        self.assertEqual(entity["attributes"]["deep_research_profile"]["scale_snapshot"]["units"]["value"], {"units": 19502})

    def test_confirm_applies_queued_leadership_update(self):
        self._write_graph(_graph_with(entities=[_brand("brand-subway", "Subway")]))
        path = self._write_dataset(_dataset([
            {"brand_name": "Subway", "current_leadership": [{"name": "Jonathan Fitzpatrick", "title": "CEO", "status": "confirmed_current"}]},
        ]))
        dai.ingest(path)
        path2 = self._write_dataset(_dataset([
            {"brand_name": "Subway", "current_leadership": [{"name": "Jonathan Fitzpatrick", "title": "Executive Chairman", "status": "confirmed_current"}]},
        ]))
        dai.ingest(path2)
        cid = dai.pending_candidates()[0]["candidate_id"]
        result = dai.record_proposal(cid, confirmed=True)
        self.assertEqual(result["status"], "confirmed")
        graph = self._read_graph()
        entity = self._entity(graph, "brand-subway")
        roster = entity["attributes"]["deep_research_profile"]["current_leadership"]
        self.assertEqual(roster[0]["title"], "Executive Chairman")

    # ---- dry run -----------------------------------------------------------------

    def test_dry_run_mutates_nothing(self):
        self._write_graph(_graph_with(entities=[_brand("brand-subway", "Subway")]))
        path = self._write_dataset(_dataset([
            {"brand_name": "Subway", "scale": {"units": 19502},
             "current_leadership": [{"name": "Jonathan Fitzpatrick", "title": "CEO"}],
             "deep_pass_public_evidence": [{"finding": "x", "source_url": "https://x.com"}]},
        ]))
        graph_before = self._graph_path.read_text()
        result = dai.ingest(path, dry_run=True)
        self.assertEqual(result["counts"]["scale_applied"], 1)
        self.assertEqual(result["counts"]["current_leadership_applied"], 1)
        self.assertEqual(result["counts"]["deep_pass_public_evidence_added"], 1)
        self.assertEqual(self._graph_path.read_text(), graph_before)
        self.assertFalse(dai.STORE_PATH.exists())

    # ---- schema validation ----------------------------------------------------------

    def test_created_attributes_pass_real_schema_validation(self):
        """Same discipline as the sibling ingest suites: ei._write_graph()'s
        own validator subprocess can't see this test's isolated graph path
        (hardcoded to check the real production file), so it provides no
        real coverage here -- run the actual validator directly against
        this test's own output instead."""
        self._write_graph(_graph_with(entities=[
            _brand("brand-subway", "Subway"),
            _vendor("vendor-oracle", "Oracle"),
        ]))
        path = self._write_dataset(_dataset([
            {"brand_name": "Subway",
             "scale": {"units": 19502, "franchisee_owned_units": 19502, "company_owned_units": 0, "ownership_confidence": 1},
             "canonical_technology": {"POS": {"value": "Oracle — Oracle MICROS POS", "confidence": 0.86}},
             "franchise_disclosure": {"confidence": 99, "hyperlink": "https://x.com", "findings": {"pos": "SubwayPOS"}},
             "current_leadership": [{"name": "Jonathan Fitzpatrick", "title": "Chief Executive Officer", "confidence": 100}],
             "deep_pass_public_evidence": [{"finding": "x", "source_url": "https://x.com", "confidence": 96}],
             "franchise_recruitment_evidence": [{"finding": "y", "source_url": "https://y.com"}],
             "deep_account_profile": {"fresh_evidence": [{"finding": "z", "source_url": "https://z.com"}]},
             },
        ]))
        dai.ingest(path)

        schema_path = ROOT / "system" / "schemas" / "ecosystem_intelligence.schema.json"
        validator_path = ROOT / "system" / "schemas" / "validate.py"
        result = subprocess.run(
            [sys.executable, str(validator_path), str(self._graph_path), "--schema", str(schema_path)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
