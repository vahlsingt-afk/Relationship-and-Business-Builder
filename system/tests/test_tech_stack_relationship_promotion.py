"""
test_tech_stack_relationship_promotion.py — RB-2026-08-31.

Coverage for tech_stack_relationship_promotion.py, the review-first
promotion engine built to expand ecosystem_intelligence.json's real
tech-stack coverage (confirmed live at ~17% of tracked brands) -- see the
module's own docstring for full context. Isolated from real production
data throughout (own tmp graph, own tmp candidate store, mocked schema
validator subprocess -- same isolation pattern as
test_master_account_plan.py's ecosystem-graph tests).
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "system" / "scripts"

spec = importlib.util.spec_from_file_location("ecosystem_intelligence", SCRIPTS_DIR / "ecosystem_intelligence.py")
ei = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ei)

spec2 = importlib.util.spec_from_file_location("ecosystem_brief", SCRIPTS_DIR / "ecosystem_brief.py")
eb = importlib.util.module_from_spec(spec2)
assert spec2.loader is not None
spec2.loader.exec_module(eb)

import sys  # noqa: E402
sys.modules["ecosystem_intelligence"] = ei
sys.modules["ecosystem_brief"] = eb
sys.path.insert(0, str(SCRIPTS_DIR))

spec3 = importlib.util.spec_from_file_location(
    "tech_stack_relationship_promotion", SCRIPTS_DIR / "tech_stack_relationship_promotion.py"
)
promotion = importlib.util.module_from_spec(spec3)
assert spec3.loader is not None
spec3.loader.exec_module(promotion)


def _graph_with(entities=None, relationships=None, signals=None, sources=None) -> dict:
    return {
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-08-31",
        "entities": entities or [], "relationships": relationships or [], "signals": signals or [],
        "sources": sources or [], "assessments": [], "user_relevance": [], "strategic_recommendations": [],
    }


def _all_candidates() -> list:
    """Every candidate regardless of status -- 2026-09-25 (Confidence-Based
    Auto-Recording): a candidate with a real category is now auto-applied
    (status "confirmed", confirmed_by "system:...") immediately, so
    promotion.pending_candidates() alone no longer shows it. Tests that
    care about a candidate's OWN fields (category, evidence_date,
    conflict_preview, brand/vendor names) rather than specifically its
    pending-ness should read from here instead."""
    return list(promotion._load_store()["candidates"].values())


BLAZE = {"id": "brand-blaze-pizza", "name": "Blaze Pizza", "entity_type": "brand", "aliases": [],
         "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]}
READY = {"id": "brand-ready", "name": "Ready", "entity_type": "brand", "aliases": [],
         "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]}
QU = {"id": "vendor-qu", "name": "Qu", "entity_type": "vendor", "aliases": [],
      "attributes": {"primary_category": "bi_analytics"}, "sources": [], "confidence": {}, "domains": ["restaurants"]}
ORACLE = {"id": "vendor-oracle", "name": "Oracle", "entity_type": "vendor", "aliases": [],
          "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]}


class _IsolatedGraphMixin:
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        tmp = Path(self._tmpdir)
        self._graph_path = tmp / "ecosystem_intelligence.json"
        self._snap_dir = tmp / "_snapshots"
        self._store_path = tmp / "tech_stack_relationship_proposals.json"
        self._ai_dir = tmp / "account_intelligence"
        self._ai_dir.mkdir(parents=True)

        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        self._orig_snap_dir = ei.core.SNAPSHOTS_DIR
        self._orig_store_path = promotion.STORE_PATH
        self._orig_ai_dir = promotion.ACCOUNT_INTELLIGENCE_DIR
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._graph_path
        ei.core.SNAPSHOTS_DIR = self._snap_dir
        promotion.STORE_PATH = self._store_path
        promotion.ACCOUNT_INTELLIGENCE_DIR = self._ai_dir

        self._patch_validator = unittest.mock.patch("subprocess.run")
        mock_run = self._patch_validator.start()
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

    def tearDown(self):
        self._patch_validator.stop()
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        ei.core.SNAPSHOTS_DIR = self._orig_snap_dir
        promotion.STORE_PATH = self._orig_store_path
        promotion.ACCOUNT_INTELLIGENCE_DIR = self._orig_ai_dir
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_graph(self, graph: dict) -> None:
        self._graph_path.write_text(json.dumps(graph), encoding="utf-8")

    def _write_doc(self, name: str, text: str) -> None:
        (self._ai_dir / name).write_text(text, encoding="utf-8")


class TestInferCategoryFromText(unittest.TestCase):
    def test_stated_category_detected(self):
        self.assertEqual(promotion._infer_category_from_text("They deployed a new point of sale system."), "pos")
        self.assertEqual(promotion._infer_category_from_text("Rolled out a new loyalty program."), "loyalty")

    def test_no_category_mentioned_returns_none(self):
        self.assertIsNone(promotion._infer_category_from_text("Qu LinkedIn post named several customer brands."))


class TestNameInTextWordBoundary(unittest.TestCase):
    """RB-2026-08-31: confirmed live -- a plain substring check matched the
    real brand 'Ready' inside 'already' on every line using that common
    word. This is the regression guard."""

    def test_short_brand_name_does_not_match_inside_unrelated_word(self):
        self.assertFalse(promotion._name_in_text("Ready", "the deal was already made"))

    def test_short_brand_name_matches_as_a_real_word(self):
        # _name_in_text expects an already-lowercased text_lower, same
        # contract as every real call site (scan functions pass line_lower).
        self.assertTrue(promotion._name_in_text("Ready", "ready is a real brand"))

    def test_too_short_name_never_matches(self):
        self.assertFalse(promotion._name_in_text("Qu", "the vendor Qu was mentioned"))


class TestScanExistingSignals(_IsolatedGraphMixin, unittest.TestCase):
    def test_proposes_candidate_with_no_category_when_signal_names_none(self):
        graph = _graph_with(
            entities=[BLAZE, QU],
            signals=[{
                "id": "sig-test-1", "signal_type": "vendor_claimed_customer_relationship",
                "summary": "Qu LinkedIn post named Blaze Pizza as a customer brand.",
                "entities": ["vendor-qu", "brand-blaze-pizza"], "sources": [],
            }],
        )
        self._write_graph(graph)
        result = promotion.scan_existing_signals()
        self.assertEqual(result["new_candidates"], 1)
        pending = promotion.pending_candidates()
        self.assertEqual(len(pending), 1)
        # RB-2026-08-31: must NOT fall back to vendor.attributes.primary_category
        # ("bi_analytics" for Qu) -- the signal itself states no category.
        self.assertIsNone(pending[0]["category"])
        self.assertEqual(pending[0]["category_confidence"], "unknown")

    def test_skips_when_any_relationship_already_exists_for_brand_vendor_pair(self):
        graph = _graph_with(
            entities=[BLAZE, QU],
            relationships=[{
                "id": "rel-1", "from_entity_id": "brand-blaze-pizza", "to_entity_id": "vendor-qu",
                "relationship_type": "uses_vendor_for_category", "category": "pos", "status": "active",
            }],
            signals=[{
                "id": "sig-test-2", "signal_type": "vendor_claimed_customer_relationship",
                "summary": "Qu LinkedIn post named Blaze Pizza as a customer brand.",
                "entities": ["vendor-qu", "brand-blaze-pizza"], "sources": [],
            }],
        )
        self._write_graph(graph)
        result = promotion.scan_existing_signals()
        self.assertEqual(result["new_candidates"], 0)

    def test_uses_stated_category_when_signal_summary_names_one(self):
        graph = _graph_with(
            entities=[BLAZE, QU],
            signals=[{
                "id": "sig-test-3", "signal_type": "vendor_relationship_formed",
                "summary": "Blaze Pizza selects Qu for its point of sale rollout.",
                "entities": ["vendor-qu", "brand-blaze-pizza"], "sources": [],
            }],
        )
        self._write_graph(graph)
        promotion.scan_existing_signals()
        candidates = _all_candidates()
        self.assertEqual(candidates[0]["category"], "pos")
        self.assertEqual(candidates[0]["category_confidence"], "stated_in_signal")
        # 2026-09-25: a real category auto-applies immediately.
        self.assertEqual(candidates[0]["status"], "confirmed")
        self.assertEqual(candidates[0]["confirmed_by"], "system:tech_stack_relationship_promotion")

    def test_idempotent_rescan_does_not_duplicate(self):
        graph = _graph_with(
            entities=[BLAZE, QU],
            signals=[{
                "id": "sig-test-4", "signal_type": "vendor_claimed_customer_relationship",
                "summary": "Qu named Blaze Pizza as a customer.",
                "entities": ["vendor-qu", "brand-blaze-pizza"], "sources": [],
            }],
        )
        self._write_graph(graph)
        promotion.scan_existing_signals()
        result2 = promotion.scan_existing_signals()
        self.assertEqual(result2["new_candidates"], 0)
        self.assertEqual(len(promotion.pending_candidates()), 1)

    def test_signal_event_at_becomes_evidence_date(self):
        """RB-2026-09-08: a signal's own real event_at must survive onto the
        candidate as evidence_date -- previously dropped entirely."""
        graph = _graph_with(
            entities=[BLAZE, QU],
            signals=[{
                "id": "sig-test-5", "signal_type": "vendor_claimed_customer_relationship",
                "summary": "Qu named Blaze Pizza as a customer.", "event_at": "2025-03-10",
                "entities": ["vendor-qu", "brand-blaze-pizza"], "sources": [],
            }],
        )
        self._write_graph(graph)
        promotion.scan_existing_signals()
        pending = promotion.pending_candidates()
        self.assertEqual(pending[0]["evidence_date"], "2025-03-10")

    def test_conflict_preview_flags_existing_rival_vendor(self):
        """RB-2026-09-08: a candidate proposing a DIFFERENT vendor for a
        category some other vendor already actively holds must carry a
        conflict_preview -- previously invisible until after confirmation."""
        graph = _graph_with(
            entities=[BLAZE, QU, ORACLE],
            relationships=[{
                "id": "rel-existing", "from_entity_id": "brand-blaze-pizza", "to_entity_id": "vendor-oracle",
                "relationship_type": "uses_vendor_for_category", "category": "pos", "status": "active",
                "updated_at": "2026-08-01T00:00:00+00:00",
            }],
            signals=[{
                "id": "sig-test-6", "signal_type": "vendor_relationship_formed",
                "summary": "Blaze Pizza selects Qu for its point of sale rollout.",
                "entities": ["vendor-qu", "brand-blaze-pizza"], "sources": [],
            }],
        )
        self._write_graph(graph)
        promotion.scan_existing_signals()
        candidates = _all_candidates()
        preview = candidates[0]["conflict_preview"]
        self.assertTrue(preview["conflict"])
        self.assertEqual(preview["rival_vendor_name"], "Oracle")
        # 2026-09-25 (Confidence-Based Auto-Recording): no more
        # "requires_confirmation" -- neither side has an explicit confidence
        # score here, so both default to the same "medium" band and the
        # preview correctly shows the new claim would be recorded alongside
        # the incumbent (not silently promoted over it, but not blocked
        # either). This candidate still auto-applies (it's not blocked
        # either way) -- see resolve_and_upsert_relationship's real write.
        self.assertEqual(preview["resolution"], "recorded_alongside")
        self.assertEqual(candidates[0]["status"], "confirmed")

    def test_conflict_preview_absent_when_no_rival(self):
        graph = _graph_with(
            entities=[BLAZE, QU],
            signals=[{
                "id": "sig-test-7", "signal_type": "vendor_relationship_formed",
                "summary": "Blaze Pizza selects Qu for its point of sale rollout.",
                "entities": ["vendor-qu", "brand-blaze-pizza"], "sources": [],
            }],
        )
        self._write_graph(graph)
        promotion.scan_existing_signals()
        candidates = _all_candidates()
        self.assertFalse(candidates[0]["conflict_preview"]["conflict"])


class TestScanAccountIntelligenceDocs(_IsolatedGraphMixin, unittest.TestCase):
    def test_real_table_does_not_cross_contaminate_unrelated_rows(self):
        """RB-2026-08-31: confirmed live -- a markdown table with no blank
        lines between rows was matched as one paragraph, cross-pairing
        every brand in any row with every vendor in any other row. Line-
        level matching must keep each row isolated."""
        graph = _graph_with(entities=[BLAZE, READY, ORACLE])
        self._write_doc("test-table.md", (
            "| Account | Notes |\n"
            "|---|---|\n"
            "| **Blaze Pizza** | uses Oracle Simphony for point of sale. |\n"
            "| **Ready** | unrelated row, no vendor mentioned here. |\n"
        ))
        self._write_graph(graph)
        promotion.scan_account_intelligence_docs()
        candidates = _all_candidates()
        pairs = {(c["brand_name"], c["vendor_name"]) for c in candidates}
        self.assertIn(("Blaze Pizza", "Oracle"), pairs)
        self.assertNotIn(("Ready", "Oracle"), pairs)  # never actually stated

    def test_prospective_language_excluded(self):
        graph = _graph_with(entities=[BLAZE, ORACLE])
        self._write_doc("test-prospective.md",
                         "Blaze Pizza is in process of migrating to Oracle, open to discussing options.\n")
        self._write_graph(graph)
        promotion.scan_account_intelligence_docs()
        self.assertEqual(promotion.pending_candidates(), [])

    def test_own_company_vendor_excluded(self):
        genius = {"id": "vendor-genius", "name": "Genius", "entity_type": "vendor", "aliases": [],
                  "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]}
        graph = _graph_with(entities=[BLAZE, genius])
        self._write_doc("test-own-company.md", "Blaze Pizza uses Genius for point of sale.\n")
        self._write_graph(graph)
        promotion.scan_account_intelligence_docs()
        self.assertEqual(promotion.pending_candidates(), [])

    def test_word_boundary_prevents_substring_false_positive_end_to_end(self):
        graph = _graph_with(entities=[READY, ORACLE])
        self._write_doc("test-already.md", "The deal was already made and uses Oracle systems broadly.\n")
        self._write_graph(graph)
        promotion.scan_account_intelligence_docs()
        pending = promotion.pending_candidates()
        self.assertFalse(any(c["brand_name"] == "Ready" for c in pending))

    def test_filename_date_prefix_becomes_evidence_date(self):
        """RB-2026-09-08: account_intelligence/*.md's real YYYY-MM-DD-<slug>
        naming convention is a genuine evidence_date, threaded through
        rather than dropped."""
        graph = _graph_with(entities=[BLAZE, ORACLE])
        self._write_doc("2026-06-01-blaze-pizza-notes.md", "Blaze Pizza uses Oracle for point of sale.\n")
        self._write_graph(graph)
        promotion.scan_account_intelligence_docs()
        candidates = _all_candidates()
        self.assertEqual(candidates[0]["evidence_date"], "2026-06-01")

    def test_filename_without_date_prefix_leaves_evidence_date_none(self):
        graph = _graph_with(entities=[BLAZE, ORACLE])
        self._write_doc("notes-no-date.md", "Blaze Pizza uses Oracle for point of sale.\n")
        self._write_graph(graph)
        promotion.scan_account_intelligence_docs()
        candidates = _all_candidates()
        self.assertIsNone(candidates[0]["evidence_date"])


class TestRecordProposal(_IsolatedGraphMixin, unittest.TestCase):
    def _seed_pending(self, category="pos", evidence_date=None):
        graph = _graph_with(entities=[BLAZE, ORACLE])
        self._write_graph(graph)
        store = {"candidates": {
            "brand-blaze-pizza::vendor-oracle::pos": {
                "candidate_id": "brand-blaze-pizza::vendor-oracle::pos",
                "status": "proposed_pending_confirmation",
                "brand_id": "brand-blaze-pizza", "brand_name": "Blaze Pizza",
                "vendor_id": "vendor-oracle", "vendor_name": "Oracle",
                "category": category, "category_confidence": "stated_in_text",
                "source_type": "operator_context", "source_title": "test.md", "source_url": None,
                "evidence_excerpt": "Blaze Pizza uses Oracle for point of sale.",
                "origin": "account_intelligence_scan", "origin_ref": "test.md#L1",
                "evidence_date": evidence_date,
                "supporting_refs": [], "detected_at": "2026-08-31T00:00:00+00:00",
                "resolved_at": None, "resolution_note": None,
            }
        }}
        self._store_path.write_text(json.dumps(store), encoding="utf-8")

    def test_confirm_threads_evidence_date_into_source_published_at(self):
        """RB-2026-09-08: evidence_date must survive all the way to the
        graph's own source record's published_at -- previously always None
        regardless of the real evidence date."""
        self._seed_pending(evidence_date="2026-07-15")
        promotion.record_proposal("brand-blaze-pizza::vendor-oracle::pos", confirmed=True)
        graph = json.loads(self._graph_path.read_text())
        src = next(s for s in graph["sources"] if s["title"] == "test.md")
        self.assertEqual(src["published_at"], "2026-07-15")

    def test_confirm_without_evidence_date_leaves_published_at_none(self):
        """No evidence_date on file (the pre-fix candidate shape, or a
        genuinely unknown date) must not fabricate one."""
        self._seed_pending(evidence_date=None)
        promotion.record_proposal("brand-blaze-pizza::vendor-oracle::pos", confirmed=True)
        graph = json.loads(self._graph_path.read_text())
        src = next(s for s in graph["sources"] if s["title"] == "test.md")
        self.assertIsNone(src["published_at"])

    def test_confirm_writes_real_relationship_to_graph(self):
        self._seed_pending()
        result = promotion.record_proposal("brand-blaze-pizza::vendor-oracle::pos", confirmed=True)
        self.assertTrue(result["confirmed"])
        graph = json.loads(self._graph_path.read_text())
        rels = [r for r in graph["relationships"] if r["relationship_type"] == "uses_vendor_for_category"]
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]["from_entity_id"], "brand-blaze-pizza")
        self.assertEqual(rels[0]["to_entity_id"], "vendor-oracle")
        self.assertEqual(rels[0]["category"], "pos")

    def test_reject_marks_rejected_without_touching_graph(self):
        self._seed_pending()
        before = self._graph_path.read_text()
        result = promotion.record_proposal("brand-blaze-pizza::vendor-oracle::pos", confirmed=False)
        self.assertTrue(result["rejected"])
        self.assertEqual(self._graph_path.read_text(), before)

    def test_cannot_resolve_twice(self):
        self._seed_pending()
        promotion.record_proposal("brand-blaze-pizza::vendor-oracle::pos", confirmed=True)
        result = promotion.record_proposal("brand-blaze-pizza::vendor-oracle::pos", confirmed=True)
        self.assertIn("error", result)

    def test_unknown_category_blocks_confirm(self):
        self._seed_pending(category=None)
        result = promotion.record_proposal("brand-blaze-pizza::vendor-oracle::pos", confirmed=True)
        self.assertIn("error", result)

    def test_unknown_candidate_id_errors(self):
        result = promotion.record_proposal("not-a-real-id", confirmed=True)
        self.assertIn("error", result)


class TestProposeResearchFinding(_IsolatedGraphMixin, unittest.TestCase):
    def test_proposes_new_vendor_not_yet_a_known_entity(self):
        graph = _graph_with(entities=[BLAZE])
        self._write_graph(graph)
        result = promotion.propose_research_finding(
            "brand-blaze-pizza", "BrandNewVendor",
            "Blaze Pizza press release: adopts BrandNewVendor for its new loyalty program.",
            source_url="https://example.com/a",
        )
        self.assertTrue(result["proposed"])
        pending = promotion.pending_candidates()
        self.assertEqual(pending[0]["vendor_id"], "vendor-brandnewvendor")
        self.assertEqual(pending[0]["category"], "loyalty")

    def test_unknown_brand_id_errors(self):
        graph = _graph_with(entities=[])
        self._write_graph(graph)
        result = promotion.propose_research_finding("brand-does-not-exist", "SomeVendor", "some evidence")
        self.assertIn("error", result)

    def test_own_company_vendor_rejected(self):
        graph = _graph_with(entities=[BLAZE])
        self._write_graph(graph)
        result = promotion.propose_research_finding("brand-blaze-pizza", "Genius", "Blaze Pizza uses Genius.")
        self.assertIn("error", result)

    def test_empty_evidence_rejected(self):
        graph = _graph_with(entities=[BLAZE])
        self._write_graph(graph)
        result = promotion.propose_research_finding("brand-blaze-pizza", "SomeVendor", "   ")
        self.assertIn("error", result)

    def test_evidence_date_threaded_onto_candidate(self):
        """RB-2026-09-08: the weekend research pipeline's whole real gap --
        propose_research_finding() had no way to carry the real-world
        evidence date at all."""
        graph = _graph_with(entities=[BLAZE])
        self._write_graph(graph)
        promotion.propose_research_finding(
            "brand-blaze-pizza", "BrandNewVendor",
            "Blaze Pizza press release: adopts BrandNewVendor for its new loyalty program.",
            evidence_date="2026-05-20",
        )
        pending = promotion.pending_candidates()
        self.assertEqual(pending[0]["evidence_date"], "2026-05-20")

    def test_invalid_evidence_date_rejected(self):
        graph = _graph_with(entities=[BLAZE])
        self._write_graph(graph)
        result = promotion.propose_research_finding(
            "brand-blaze-pizza", "SomeVendor", "Blaze Pizza adopts SomeVendor.",
            evidence_date="not-a-date",
        )
        self.assertIn("error", result)

    def test_omitted_evidence_date_stays_none(self):
        graph = _graph_with(entities=[BLAZE])
        self._write_graph(graph)
        promotion.propose_research_finding("brand-blaze-pizza", "SomeVendor", "Blaze Pizza adopts SomeVendor.")
        pending = promotion.pending_candidates()
        self.assertIsNone(pending[0]["evidence_date"])

    def test_already_covered_relationship_rejected(self):
        graph = _graph_with(
            entities=[BLAZE, ORACLE],
            relationships=[{
                "id": "rel-1", "from_entity_id": "brand-blaze-pizza", "to_entity_id": "vendor-oracle",
                "relationship_type": "uses_vendor_for_category", "category": "pos", "status": "active",
            }],
        )
        self._write_graph(graph)
        result = promotion.propose_research_finding(
            "brand-blaze-pizza", "Oracle", "Blaze Pizza uses Oracle for point of sale.",
        )
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
