"""
test_deep_research_dataset_ingest.py — RB-2026-09-27.

Coverage for deep_research_dataset_ingest.py, which routes the "RBB
Restaurant Account Intelligence Base" deep-research dataset's per-brand
findings to three different destinations by content shape (see the
module's own docstring). Isolated from real production data throughout
(own tmp graph, own tmp stores for every promotion module involved) --
same pattern as test_ownership_promotion.py / test_tech_stack_
relationship_promotion.py, since this module calls straight into both.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
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
    sys.modules[name] = module  # register before exec so intra-package imports resolve to this instance
    spec.loader.exec_module(module)
    return module


ei = _load("ecosystem_intelligence")
eb = _load("ecosystem_brief")
op = _load("ownership_promotion")
tsrp = _load("tech_stack_relationship_promotion")
sys.path.insert(0, str(SCRIPTS_DIR))
dri = _load("deep_research_dataset_ingest")


def _graph_with(entities=None, relationships=None) -> dict:
    return {
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-27",
        "domain_packs": ["restaurants"],
        "entities": entities or [], "relationships": relationships or [], "signals": [],
        "sources": [], "assessments": [], "user_relevance": [], "strategic_recommendations": [],
    }


def _brand(entity_id, name, attributes=None, aliases=None):
    return {"id": entity_id, "name": name, "entity_type": "brand", "status": "active", "aliases": aliases or [],
            "attributes": attributes or {}, "sources": [], "confidence": {"level": "high"}, "domains": ["restaurants"]}


def _vendor(entity_id, name, aliases=None):
    return {"id": entity_id, "name": name, "entity_type": "vendor", "status": "active", "aliases": aliases or [],
            "attributes": {}, "sources": [], "confidence": {"level": "high"}, "domains": ["restaurants"]}


def _leaf(value, confidence=90, status="confirmed", as_of="2026-09-01", scope="enterprise", sources=None):
    return {"value": value, "confidence": confidence, "status": status, "as_of": as_of,
            "scope": scope, "sources": sources or ["https://example.com/source"]}


def _dataset_with(records: list[dict]) -> dict:
    return {"dataset": {"name": "Test Dataset", "version": "test-v1", "records": records}}


class TestDeepResearchDatasetIngest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        tmp = Path(self._tmpdir)
        self._graph_path = tmp / "ecosystem_intelligence.json"
        self._op_store_path = tmp / "ownership_candidates.json"
        self._op_promoted_path = tmp / "ownership_promoted.json"
        self._op_ai_dir = tmp / "op_account_intelligence"
        self._op_ai_dir.mkdir()
        self._tsrp_store_path = tmp / "tech_stack_candidates.json"
        self._dri_store_path = tmp / "dri_attribute_candidates.json"
        self._dataset_path = tmp / "dataset.json"

        self._orig = {
            "ecosystem_path": ei.core.ECOSYSTEM_INTELLIGENCE_PATH,
            "op_store": op.STORE_PATH,
            "op_promoted": op.PROMOTED_PATH,
            "op_ai_dir": op.ACCOUNT_INTELLIGENCE_DIR,
            "tsrp_store": tsrp.STORE_PATH,
            "dri_store": dri.STORE_PATH,
        }
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._graph_path
        op.STORE_PATH = self._op_store_path
        op.PROMOTED_PATH = self._op_promoted_path
        op.ACCOUNT_INTELLIGENCE_DIR = self._op_ai_dir
        tsrp.STORE_PATH = self._tsrp_store_path
        dri.STORE_PATH = self._dri_store_path

    def tearDown(self):
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig["ecosystem_path"]
        op.STORE_PATH = self._orig["op_store"]
        op.PROMOTED_PATH = self._orig["op_promoted"]
        op.ACCOUNT_INTELLIGENCE_DIR = self._orig["op_ai_dir"]
        tsrp.STORE_PATH = self._orig["tsrp_store"]
        dri.STORE_PATH = self._orig["dri_store"]
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_graph(self, graph: dict) -> None:
        self._graph_path.write_text(json.dumps(graph), encoding="utf-8")

    def _write_dataset(self, dataset: dict) -> Path:
        self._dataset_path.write_text(json.dumps(dataset), encoding="utf-8")
        return self._dataset_path

    def _read_graph(self) -> dict:
        return json.loads(self._graph_path.read_text())

    # ---- brand resolution -------------------------------------------------

    def test_unresolved_brand_is_skipped_never_created(self):
        self._write_graph(_graph_with(entities=[]))
        path = self._write_dataset(_dataset_with([
            {"brand_name": "Totally Unknown Chain", "profile": {"scale_performance": {"fy2025": _leaf("$1B")}}},
        ]))
        result = dri.ingest(path)
        self.assertEqual(result["counts"]["brands_unresolved"], 1)
        self.assertEqual(result["counts"]["brands_resolved"], 0)
        self.assertIn("Totally Unknown Chain", result["unresolved_brands"])
        self.assertEqual(self._read_graph()["entities"], [])

    def test_resolving_to_a_vendor_entity_is_treated_as_unresolved(self):
        """A dataset brand_name that happens to collide with an existing
        VENDOR entity's name must never have restaurant-chain research
        data written onto it."""
        self._write_graph(_graph_with(entities=[_vendor("vendor-toast", "Toast")]))
        path = self._write_dataset(_dataset_with([
            {"brand_name": "Toast", "profile": {"scale_performance": {"fy2025": _leaf("$1B")}}},
        ]))
        result = dri.ingest(path)
        self.assertEqual(result["counts"]["brands_unresolved"], 1)

    # ---- generic attribute route -------------------------------------------

    def test_net_new_attribute_applies_immediately(self):
        self._write_graph(_graph_with(entities=[_brand("brand-wendys", "Wendy's")]))
        path = self._write_dataset(_dataset_with([
            {"brand_name": "Wendy's", "profile": {"scale_performance": {"fy2025": _leaf("$1.2B revenue")}}},
        ]))
        result = dri.ingest(path)
        self.assertEqual(result["counts"]["attributes_applied"], 1)
        self.assertEqual(result["counts"]["attributes_queued"], 0)
        graph = self._read_graph()
        entity = next(e for e in graph["entities"] if e["id"] == "brand-wendys")
        applied = entity["attributes"]["deep_research_profile"]["scale_performance"]["fy2025"]
        self.assertEqual(applied["value"], "$1.2B revenue")
        self.assertEqual(applied["confidence"], 90)

    def test_conflicting_attribute_routes_to_review_not_overwritten(self):
        existing_attrs = {"deep_research_profile": {"scale_performance": {"fy2025": _leaf("$1.0B revenue")}}}
        self._write_graph(_graph_with(entities=[_brand("brand-wendys", "Wendy's", attributes=existing_attrs)]))
        path = self._write_dataset(_dataset_with([
            {"brand_name": "Wendy's", "profile": {"scale_performance": {"fy2025": _leaf("$1.2B revenue")}}},
        ]))
        result = dri.ingest(path)
        self.assertEqual(result["counts"]["attributes_queued"], 1)
        self.assertEqual(result["counts"]["attributes_applied"], 0)
        # Original value must be untouched.
        graph = self._read_graph()
        entity = next(e for e in graph["entities"] if e["id"] == "brand-wendys")
        self.assertEqual(
            entity["attributes"]["deep_research_profile"]["scale_performance"]["fy2025"]["value"],
            "$1.0B revenue",
        )
        pending = dri.pending_candidates()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["proposed"]["value"], "$1.2B revenue")

    def test_matching_attribute_value_is_silent_noop(self):
        existing_attrs = {"deep_research_profile": {"scale_performance": {"fy2025": _leaf("$1.2B revenue")}}}
        self._write_graph(_graph_with(entities=[_brand("brand-wendys", "Wendy's", attributes=existing_attrs)]))
        path = self._write_dataset(_dataset_with([
            {"brand_name": "Wendy's", "profile": {"scale_performance": {"fy2025": _leaf("$1.2B revenue")}}},
        ]))
        result = dri.ingest(path)
        self.assertEqual(result["counts"]["attributes_unchanged"], 1)
        self.assertEqual(result["counts"]["attributes_applied"], 0)
        self.assertEqual(result["counts"]["attributes_queued"], 0)
        self.assertEqual(dri.pending_candidates(), [])

    def test_leadership_field_preserves_confidence_and_source(self):
        """Todd, 2026-09-27: leadership must carry confidence -- a
        decision-maker listing with no confidence attached isn't useful
        for identifying who actually has buying authority."""
        self._write_graph(_graph_with(entities=[_brand("brand-starbucks", "Starbucks")]))
        path = self._write_dataset(_dataset_with([
            {"brand_name": "Starbucks", "profile": {
                "leadership": {"ceo": _leaf("Brian Niccol, Chairman and CEO", confidence=98,
                                             sources=["https://about.starbucks.com/press/x"])},
            }},
        ]))
        dri.ingest(path)
        graph = self._read_graph()
        entity = next(e for e in graph["entities"] if e["id"] == "brand-starbucks")
        ceo = entity["attributes"]["deep_research_profile"]["leadership"]["ceo"]
        self.assertEqual(ceo["value"], "Brian Niccol, Chairman and CEO")
        self.assertEqual(ceo["confidence"], 98)
        self.assertEqual(ceo["sources"], ["https://about.starbucks.com/press/x"])

    # ---- ownership route ----------------------------------------------------

    def test_ownership_field_routes_to_ownership_promotion(self):
        self._write_graph(_graph_with(entities=[_brand("brand-del-taco", "Del Taco")]))
        path = self._write_dataset(_dataset_with([
            {"brand_name": "Del Taco", "profile": {"identity": {
                "ownership": _leaf(
                    "Acquired by Yadav Enterprises from Jack in the Box Inc.",
                    sources=[{"url": "https://investors.jackinthebox.com/x", "title": "Sale completed"}],
                ),
            }}},
        ]))
        result = dri.ingest(path)
        self.assertEqual(result["counts"]["ownership_proposed"], 1)
        # A real pending candidate must exist in ownership_promotion's OWN store.
        candidates = list(op._load_store()["candidates"].values())
        self.assertEqual(len(candidates), 1)
        self.assertIn("Yadav", candidates[0]["proposed_owner_name"] or "")

    # ---- technology route -----------------------------------------------------

    def test_technology_field_with_known_vendor_routes_to_tech_stack_promotion(self):
        self._write_graph(_graph_with(entities=[
            _brand("brand-texas-roadhouse", "Texas Roadhouse"),
            _vendor("vendor-ncr-voyix", "NCR Voyix"),
        ]))
        path = self._write_dataset(_dataset_with([
            {"brand_name": "Texas Roadhouse", "profile": {"technology": {
                "pos": _leaf("Runs NCR Voyix point-of-sale across all domestic locations"),
            }}},
        ]))
        result = dri.ingest(path)
        self.assertEqual(result["counts"]["technology_proposed"], 1)
        self.assertEqual(result["counts"]["technology_vendor_unidentified"], 0)
        candidates = list(tsrp._load_store()["candidates"].values())
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["vendor_name"], "NCR Voyix")

    def test_technology_finding_for_an_already_existing_relationship_is_reported_not_dropped(self):
        """Confirmed live against the real 250-brand dataset: several
        technology findings named a vendor already on file for that brand/
        category. propose_research_finding() correctly declines to
        duplicate the pending candidate -- this must surface in the
        ingest's own `errors` list, never silently disappear."""
        self._write_graph(_graph_with(
            entities=[
                _brand("brand-applebees", "Applebee's"),
                _vendor("vendor-toast", "Toast"),
            ],
            relationships=[{
                "id": "rel-brand-applebees-pos-vendor-toast", "from_entity_id": "brand-applebees",
                "to_entity_id": "vendor-toast", "relationship_type": "uses_vendor_for_category",
                "status": "active", "category": "pos",
            }],
        ))
        path = self._write_dataset(_dataset_with([
            # Real text from the actual dataset -- verified to infer
            # category "pos" via tech_stack_relationship_promotion's own
            # _infer_category_from_text(), matching the existing
            # relationship's category below.
            {"brand_name": "Applebee's", "profile": {"technology": {
                "pos_kds": _leaf(
                    "Toast selected for nationwide Applebee's POS, Toast Go handhelds, "
                    "KDS and Restaurant Management Suite Enterprise"
                ),
            }}},
        ]))
        result = dri.ingest(path)
        self.assertEqual(result["counts"]["technology_proposed"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["brand"], "Applebee's")
        self.assertIn("already on file", result["errors"][0]["error"])

    def test_technology_field_with_no_known_vendor_is_skipped(self):
        self._write_graph(_graph_with(entities=[_brand("brand-texas-roadhouse", "Texas Roadhouse")]))
        path = self._write_dataset(_dataset_with([
            {"brand_name": "Texas Roadhouse", "profile": {"technology": {
                "pos": _leaf("Customized in-house digital waitlist/order/payment platform"),
            }}},
        ]))
        result = dri.ingest(path)
        self.assertEqual(result["counts"]["technology_vendor_unidentified"], 1)
        self.assertEqual(result["counts"]["technology_proposed"], 0)
        self.assertEqual(list(tsrp._load_store()["candidates"].values()), [])

    def test_technology_field_with_ambiguous_vendors_is_skipped_not_guessed(self):
        # Both vendor names must clear tech_stack_relationship_promotion's
        # own 4-character noise-guard minimum (_MIN_NAME_LEN) -- a shorter
        # name like "Olo" is deliberately never matched at all, which would
        # make this look like a single-match (not ambiguous) case instead
        # of the genuine two-known-vendor ambiguity this test targets.
        self._write_graph(_graph_with(entities=[
            _brand("brand-jersey-mikes", "Jersey Mike's Subs"),
            _vendor("vendor-toast", "Toast"),
            _vendor("vendor-clover", "Clover"),
        ]))
        path = self._write_dataset(_dataset_with([
            {"brand_name": "Jersey Mike's Subs", "profile": {"technology": {
                "online_ordering": _leaf("Uses both Toast and Clover across different franchise regions"),
            }}},
        ]))
        result = dri.ingest(path)
        self.assertEqual(result["counts"]["technology_vendor_unidentified"], 1)
        self.assertEqual(result["counts"]["technology_proposed"], 0)

    # ---- confirm / reject ----------------------------------------------------

    def test_confirm_applies_the_queued_value(self):
        existing_attrs = {"deep_research_profile": {"scale_performance": {"fy2025": _leaf("$1.0B revenue")}}}
        self._write_graph(_graph_with(entities=[_brand("brand-wendys", "Wendy's", attributes=existing_attrs)]))
        path = self._write_dataset(_dataset_with([
            {"brand_name": "Wendy's", "profile": {"scale_performance": {"fy2025": _leaf("$1.2B revenue")}}},
        ]))
        dri.ingest(path)
        cid = dri.pending_candidates()[0]["candidate_id"]
        result = dri.record_proposal(cid, confirmed=True)
        self.assertEqual(result["status"], "confirmed")
        graph = self._read_graph()
        entity = next(e for e in graph["entities"] if e["id"] == "brand-wendys")
        self.assertEqual(
            entity["attributes"]["deep_research_profile"]["scale_performance"]["fy2025"]["value"],
            "$1.2B revenue",
        )
        self.assertEqual(dri.pending_candidates(), [])

    def test_reject_leaves_existing_value_untouched(self):
        existing_attrs = {"deep_research_profile": {"scale_performance": {"fy2025": _leaf("$1.0B revenue")}}}
        self._write_graph(_graph_with(entities=[_brand("brand-wendys", "Wendy's", attributes=existing_attrs)]))
        path = self._write_dataset(_dataset_with([
            {"brand_name": "Wendy's", "profile": {"scale_performance": {"fy2025": _leaf("$1.2B revenue")}}},
        ]))
        dri.ingest(path)
        cid = dri.pending_candidates()[0]["candidate_id"]
        result = dri.record_proposal(cid, confirmed=False)
        self.assertEqual(result["status"], "rejected")
        graph = self._read_graph()
        entity = next(e for e in graph["entities"] if e["id"] == "brand-wendys")
        self.assertEqual(
            entity["attributes"]["deep_research_profile"]["scale_performance"]["fy2025"]["value"],
            "$1.0B revenue",
        )
        self.assertEqual(dri.pending_candidates(), [])

    # ---- dry run ----------------------------------------------------------

    def test_dry_run_mutates_nothing(self):
        self._write_graph(_graph_with(entities=[_brand("brand-wendys", "Wendy's")]))
        path = self._write_dataset(_dataset_with([
            {"brand_name": "Wendy's", "profile": {"scale_performance": {"fy2025": _leaf("$1.2B revenue")}}},
        ]))
        graph_before = self._graph_path.read_text()
        result = dri.ingest(path, dry_run=True)
        self.assertEqual(result["counts"]["attributes_applied"], 1)
        self.assertEqual(self._graph_path.read_text(), graph_before)
        self.assertFalse(dri.STORE_PATH.exists())


if __name__ == "__main__":
    unittest.main()
