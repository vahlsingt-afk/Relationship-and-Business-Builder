"""
test_multibrand_franchisee_operator_ingest.py — RB-2026-09-27.

Coverage for multibrand_franchisee_operator_ingest.py, which introduces
new entities (a brand subtype, multi_brand_franchisee_operator -- Todd,
2026-09-27: "treated as a type of brand for our purposes") and a new
relationship type (operates) into the graph -- see the module's own
docstring for the full design rationale. Isolated from real production
data throughout (own tmp graph, own tmp candidate store), same pattern as
test_ownership_promotion.py / test_deep_research_dataset_ingest.py.

RB-2026-09-27: confirmed live against the real 47-operator dataset that
ecosystem_intelligence.py's own _write_graph() validates the graph AFTER
writing it to disk, with no rollback on failure -- a schema-invalid entity
(this script's first version omitted the required `status` and
`confidence.level` fields) landed on disk before the validator complained.
Worse, the validator subprocess it spawns always checks the REAL
production system/ecosystem_intelligence.json by a hardcoded path,
completely ignoring an isolated test's monkeypatched
core.ECOSYSTEM_INTELLIGENCE_PATH -- so this suite's own use of
ei._write_graph() was never actually catching the bug (see
TestSchemaValidation below, which runs the real validator directly against
this test's own isolated file instead, to close that gap for this script
specifically; the deeper, general fix is flagged separately, not attempted
here).
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
sys.path.insert(0, str(SCRIPTS_DIR))
mfoi = _load("multibrand_franchisee_operator_ingest")


def _graph_with(entities=None, relationships=None) -> dict:
    return {
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-27",
        "domain_packs": ["restaurants"],
        "entities": entities or [], "relationships": relationships or [], "signals": [],
        "sources": [], "assessments": [], "user_relevance": [], "strategic_recommendations": [],
    }


def _brand(entity_id, name, aliases=None, subtype="restaurant_brand"):
    return {"id": entity_id, "name": name, "entity_type": "brand", "subtype": subtype, "status": "active",
            "aliases": aliases or [], "attributes": {}, "sources": [],
            "confidence": {"level": "high"}, "domains": ["restaurants"]}


def _vendor(entity_id, name):
    return {"id": entity_id, "name": name, "entity_type": "vendor", "status": "active", "aliases": [],
            "attributes": {}, "sources": [], "confidence": {"level": "high"}, "domains": ["restaurants"]}


def _dataset_with(records: list[dict]) -> dict:
    return {"dataset_name": "Test Dataset", "version": "test-v1", "records": records}


class TestMultiBrandFranchiseeOperatorIngest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        tmp = Path(self._tmpdir)
        self._graph_path = tmp / "ecosystem_intelligence.json"
        self._store_path = tmp / "operator_candidates.json"
        self._dataset_path = tmp / "dataset.json"

        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        self._orig_store_path = mfoi.STORE_PATH
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._graph_path
        mfoi.STORE_PATH = self._store_path

    def tearDown(self):
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        mfoi.STORE_PATH = self._orig_store_path
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

    # ---- operator creation --------------------------------------------------

    def test_net_new_operator_is_created_as_a_brand_subtype(self):
        """Todd, 2026-09-27: a multi-brand franchisee operator is a type of
        brand for RB's purposes -- entity_type "brand", distinguished only
        by subtype, so it stays queryable/countable alongside every other
        brand rather than forking into an unqueried parallel category."""
        self._write_graph(_graph_with())
        path = self._write_dataset(_dataset_with([
            {"operator_name": "Flynn Group", "brands": [], "profile": {}},
        ]))
        result = mfoi.ingest(path)
        self.assertEqual(result["counts"]["operators_created"], 1)
        graph = self._read_graph()
        entity = self._entity(graph, "operator-flynn-group")
        self.assertEqual(entity["entity_type"], "brand")
        self.assertEqual(entity["subtype"], "multi_brand_franchisee_operator")
        self.assertEqual(entity["name"], "Flynn Group")

    def test_multi_name_operator_split_into_canonical_plus_aliases(self):
        self._write_graph(_graph_with())
        path = self._write_dataset(_dataset_with([
            {"operator_name": "Yadav Enterprises / JIB Management", "brands": [], "profile": {}},
        ]))
        mfoi.ingest(path)
        graph = self._read_graph()
        entity = self._entity(graph, "operator-yadav-enterprises")
        self.assertEqual(entity["name"], "Yadav Enterprises")
        self.assertEqual(entity["aliases"], ["JIB Management"])

    def test_rerun_reuses_existing_operator_not_a_duplicate(self):
        self._write_graph(_graph_with())
        path = self._write_dataset(_dataset_with([
            {"operator_name": "Flynn Group", "brands": [], "profile": {}},
        ]))
        mfoi.ingest(path)
        result2 = mfoi.ingest(path)
        self.assertEqual(result2["counts"]["operators_created"], 0)
        self.assertEqual(result2["counts"]["operators_existing"], 1)
        graph = self._read_graph()
        flynn_entities = [e for e in graph["entities"] if e["name"] == "Flynn Group"]
        self.assertEqual(len(flynn_entities), 1)

    def test_name_collision_with_vendor_is_skipped(self):
        """A franchisee operator name colliding with an existing vendor
        entity must never be silently merged into it."""
        self._write_graph(_graph_with(entities=[_vendor("vendor-flynn-group", "Flynn Group")]))
        path = self._write_dataset(_dataset_with([
            {"operator_name": "Flynn Group", "brands": [], "profile": {}},
        ]))
        result = mfoi.ingest(path)
        self.assertEqual(result["counts"]["operators_name_collision_skipped"], 1)
        self.assertIn("Flynn Group", result["name_collisions"])
        graph = self._read_graph()
        self.assertEqual(len(graph["entities"]), 1)  # nothing new created

    def test_name_collision_with_ordinary_restaurant_brand_is_skipped(self):
        """Now that operators are ALSO entity_type "brand", the collision
        check must key off subtype, not entity_type alone -- an operator
        name colliding with a real restaurant brand (subtype
        restaurant_brand) must still be skipped, never conflated with it."""
        self._write_graph(_graph_with(entities=[_brand("brand-flynn-group", "Flynn Group")]))
        path = self._write_dataset(_dataset_with([
            {"operator_name": "Flynn Group", "brands": [], "profile": {}},
        ]))
        result = mfoi.ingest(path)
        self.assertEqual(result["counts"]["operators_name_collision_skipped"], 1)
        self.assertIn("Flynn Group", result["name_collisions"])
        graph = self._read_graph()
        self.assertEqual(len(graph["entities"]), 1)

    # ---- operates relationships -----------------------------------------------

    def test_operates_relationship_created_for_resolved_brand(self):
        self._write_graph(_graph_with(entities=[_brand("brand-wendys", "Wendy's")]))
        path = self._write_dataset(_dataset_with([
            {"operator_name": "Flynn Group", "brands": ["Wendy's"], "source_url": "https://example.com", "profile": {}},
        ]))
        result = mfoi.ingest(path)
        self.assertEqual(result["counts"]["operates_relationships_applied"], 1)
        graph = self._read_graph()
        rels = [r for r in graph["relationships"] if r["relationship_type"] == "operates"]
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]["from_entity_id"], "operator-flynn-group")
        self.assertEqual(rels[0]["to_entity_id"], "brand-wendys")

    def test_brands_list_never_resolves_to_another_operator_entity(self):
        """Now that operators share entity_type "brand" with real
        restaurant concepts, a name in one operator's brands[] list that
        happens to match ANOTHER operator entity must be treated as
        unresolved, never as a real "operates" relationship to that other
        operator."""
        self._write_graph(_graph_with())
        path = self._write_dataset(_dataset_with([
            {"operator_name": "Sun Holdings", "brands": [], "profile": {}},
            {"operator_name": "Flynn Group", "brands": ["Sun Holdings"], "profile": {}},
        ]))
        result = mfoi.ingest(path)
        self.assertIn("Sun Holdings", result["unresolved_brands"])
        graph = self._read_graph()
        self.assertEqual(graph["relationships"], [])

    def test_unresolved_brand_is_reported_not_guessed(self):
        self._write_graph(_graph_with())
        path = self._write_dataset(_dataset_with([
            {"operator_name": "Flynn Group", "brands": ["Totally Unknown Brand"], "profile": {}},
        ]))
        result = mfoi.ingest(path)
        self.assertEqual(result["counts"]["brands_unresolved"], 1)
        self.assertIn("Totally Unknown Brand", result["unresolved_brands"])
        graph = self._read_graph()
        self.assertEqual(graph["relationships"], [])

    def test_rerun_does_not_duplicate_operates_relationship(self):
        self._write_graph(_graph_with(entities=[_brand("brand-wendys", "Wendy's")]))
        path = self._write_dataset(_dataset_with([
            {"operator_name": "Flynn Group", "brands": ["Wendy's"], "profile": {}},
        ]))
        mfoi.ingest(path)
        result2 = mfoi.ingest(path)
        self.assertEqual(result2["counts"]["operates_relationships_unchanged"], 1)
        self.assertEqual(result2["counts"]["operates_relationships_applied"], 0)
        graph = self._read_graph()
        self.assertEqual(len(graph["relationships"]), 1)

    # ---- leadership merge -----------------------------------------------------

    def test_leadership_net_new_person_applied_with_confidence(self):
        self._write_graph(_graph_with())
        path = self._write_dataset(_dataset_with([
            {"operator_name": "Flynn Group", "brands": [], "profile": {"leadership": {"people": [
                {"name": "Greg Flynn", "title": "Founder, Chairman & CEO", "confidence": 100,
                 "source_url": "https://flynn.com/our-leaders/"},
            ]}}},
        ]))
        result = mfoi.ingest(path)
        self.assertEqual(result["counts"]["leadership_applied"], 1)
        graph = self._read_graph()
        entity = self._entity(graph, "operator-flynn-group")
        roster = entity["attributes"]["deep_research_profile"]["current_leadership"]
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster[0]["name"], "Greg Flynn")
        self.assertEqual(roster[0]["confidence"], 100)

    def test_leadership_conflicting_title_routes_to_review(self):
        self._write_graph(_graph_with())
        path = self._write_dataset(_dataset_with([
            {"operator_name": "Flynn Group", "brands": [], "profile": {"leadership": {"people": [
                {"name": "Greg Flynn", "title": "Founder & CEO", "confidence": 100, "source_url": "https://a.com"},
            ]}}},
        ]))
        mfoi.ingest(path)  # first pass: applied net-new

        path2 = self._write_dataset(_dataset_with([
            {"operator_name": "Flynn Group", "brands": [], "profile": {"leadership": {"people": [
                {"name": "Greg Flynn", "title": "Executive Chairman", "confidence": 100, "source_url": "https://b.com"},
            ]}}},
        ]))
        result = mfoi.ingest(path2)
        self.assertEqual(result["counts"]["leadership_queued"], 1)
        self.assertEqual(result["counts"]["leadership_applied"], 0)
        graph = self._read_graph()
        entity = self._entity(graph, "operator-flynn-group")
        roster = entity["attributes"]["deep_research_profile"]["current_leadership"]
        self.assertEqual(roster[0]["title"], "Founder & CEO")  # unchanged
        pending = mfoi.pending_candidates()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["proposed"]["title"], "Executive Chairman")

    def test_leadership_matching_person_is_noop(self):
        self._write_graph(_graph_with())
        path = self._write_dataset(_dataset_with([
            {"operator_name": "Flynn Group", "brands": [], "profile": {"leadership": {"people": [
                {"name": "Greg Flynn", "title": "Founder & CEO", "confidence": 100, "source_url": "https://a.com"},
            ]}}},
        ]))
        mfoi.ingest(path)
        result2 = mfoi.ingest(path)
        self.assertEqual(result2["counts"]["leadership_unchanged"], 1)
        self.assertEqual(result2["counts"]["leadership_applied"], 0)
        self.assertEqual(mfoi.pending_candidates(), [])

    # ---- evidence ledger --------------------------------------------------

    def test_evidence_ledger_entries_appended_net_new(self):
        self._write_graph(_graph_with())
        path = self._write_dataset(_dataset_with([
            {"operator_name": "Flynn Group", "brands": [], "profile": {"evidence_ledger": [
                {"category": "headquarters", "finding": "HQ in San Francisco.", "confidence": 100,
                 "source_url": "https://flynn.com", "access_date": "2026-09-27"},
            ]}},
        ]))
        result = mfoi.ingest(path)
        self.assertEqual(result["counts"]["evidence_ledger_entries_added"], 1)
        graph = self._read_graph()
        entity = self._entity(graph, "operator-flynn-group")
        ledger = entity["attributes"]["deep_research_profile"]["evidence_ledger"]
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["finding"], "HQ in San Francisco.")

    def test_evidence_ledger_rerun_does_not_duplicate_entries(self):
        self._write_graph(_graph_with())
        path = self._write_dataset(_dataset_with([
            {"operator_name": "Flynn Group", "brands": [], "profile": {"evidence_ledger": [
                {"category": "headquarters", "finding": "HQ in San Francisco.", "confidence": 100,
                 "source_url": "https://flynn.com", "access_date": "2026-09-27"},
            ]}},
        ]))
        mfoi.ingest(path)
        result2 = mfoi.ingest(path)
        self.assertEqual(result2["counts"]["evidence_ledger_entries_added"], 0)
        graph = self._read_graph()
        entity = self._entity(graph, "operator-flynn-group")
        self.assertEqual(len(entity["attributes"]["deep_research_profile"]["evidence_ledger"]), 1)

    # ---- confirm / reject ----------------------------------------------------

    def test_confirm_applies_the_queued_leadership_update(self):
        self._write_graph(_graph_with())
        path = self._write_dataset(_dataset_with([
            {"operator_name": "Flynn Group", "brands": [], "profile": {"leadership": {"people": [
                {"name": "Greg Flynn", "title": "Founder & CEO", "confidence": 100, "source_url": "https://a.com"},
            ]}}},
        ]))
        mfoi.ingest(path)
        path2 = self._write_dataset(_dataset_with([
            {"operator_name": "Flynn Group", "brands": [], "profile": {"leadership": {"people": [
                {"name": "Greg Flynn", "title": "Executive Chairman", "confidence": 100, "source_url": "https://b.com"},
            ]}}},
        ]))
        mfoi.ingest(path2)
        cid = mfoi.pending_candidates()[0]["candidate_id"]
        result = mfoi.record_proposal(cid, confirmed=True)
        self.assertEqual(result["status"], "confirmed")
        graph = self._read_graph()
        entity = self._entity(graph, "operator-flynn-group")
        roster = entity["attributes"]["deep_research_profile"]["current_leadership"]
        self.assertEqual(roster[0]["title"], "Executive Chairman")
        self.assertEqual(mfoi.pending_candidates(), [])

    # ---- schema validation --------------------------------------------------

    def test_created_operator_and_relationship_pass_real_schema_validation(self):
        """RB-2026-09-27: ei._write_graph()'s own validator subprocess
        can't see this test's isolated graph path (it always checks the
        real production file by a hardcoded path -- see this file's module
        docstring), so it provides no real coverage here. Run the actual
        validator directly against this test's own output instead -- this
        is what caught the original bug (a created entity missing the
        schema's required `status` and `confidence.level` fields) live,
        after the sibling tests above had all already passed."""
        self._write_graph(_graph_with(entities=[_brand("brand-wendys", "Wendy's")]))
        path = self._write_dataset(_dataset_with([
            {"operator_name": "Flynn Group", "brands": ["Wendy's"], "confidence": 96,
             "source_type": "Test source", "profile": {"leadership": {"people": [
                 {"name": "Greg Flynn", "title": "Founder & CEO", "confidence": 100, "source_url": "https://a.com"},
             ]}}},
        ]))
        mfoi.ingest(path)

        schema_path = ROOT / "system" / "schemas" / "ecosystem_intelligence.schema.json"
        validator_path = ROOT / "system" / "schemas" / "validate.py"
        result = subprocess.run(
            [sys.executable, str(validator_path), str(self._graph_path), "--schema", str(schema_path)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    # ---- dry run ------------------------------------------------------------

    def test_dry_run_mutates_nothing(self):
        self._write_graph(_graph_with(entities=[_brand("brand-wendys", "Wendy's")]))
        path = self._write_dataset(_dataset_with([
            {"operator_name": "Flynn Group", "brands": ["Wendy's"], "profile": {"leadership": {"people": [
                {"name": "Greg Flynn", "title": "Founder & CEO", "confidence": 100, "source_url": "https://a.com"},
            ]}}, },
        ]))
        graph_before = self._graph_path.read_text()
        result = mfoi.ingest(path, dry_run=True)
        self.assertEqual(result["counts"]["operators_created"], 1)
        self.assertEqual(result["counts"]["operates_relationships_applied"], 1)
        self.assertEqual(result["counts"]["leadership_applied"], 1)
        self.assertEqual(self._graph_path.read_text(), graph_before)
        self.assertFalse(mfoi.STORE_PATH.exists())


if __name__ == "__main__":
    unittest.main()
