"""
test_ecosystem_migration.py — Unified Restaurant-Tech Graph request (2026-07-31)
Phase 2: workbook-to-graph migration tool.

Covers ecosystem_intelligence.py's reconcile_workbook_row(), the normalization
helpers it depends on, brand entity resolution (including the real
Checkers/Checkers & Rally's entity-fragmentation case), and idempotent
source_assertions[] merging.

Test groups:
  EM1: deployment_status / ai_application normalization
  EM2: working-profile / pilot / invalid-customer row detection
  EM3: source_assertions merge idempotency
  EM4: brand entity resolution (exact, alias/token-superset, continuity, ambiguous)
  EM5: reconcile_workbook_row outcome classification (all 11 outcomes)
  EM6: the named Hi Auto / Presto / Checkers correction, live against the
       real workbook + real graph
"""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "system" / "scripts" / "ecosystem_intelligence.py"
REAL_GRAPH = ROOT / "system" / "ecosystem_intelligence.json"
REAL_WORKBOOK = (
    ROOT / "outputs" / "019fb7fc-6359-7272-a00b-0a57f9dd8a55"
    / "Restaurant_Tech_Full_1500_With_Canonical_Stack_2026-07-31.xlsx"
)

spec = importlib.util.spec_from_file_location("ecosystem_intelligence", SCRIPT)
ei = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ei)


def _empty_graph() -> dict:
    return {"sources": [], "entities": [], "relationships": [], "signals": [], "assessments": [], "user_relevance": [], "strategic_recommendations": []}


def _brand(id_: str, name: str) -> dict:
    return {"id": id_, "name": name, "entity_type": "brand", "subtype": "restaurant_brand", "aliases": [], "domains": ["restaurants"]}


def _row(**overrides) -> dict:
    base = {
        "vendor": "TestVendor",
        "customer": "Test Brand",
        "tech_category": "POS Software",
        "product_module": "Test Product",
        "ai_application": "None evidenced",
        "relationship_scope": "Enterprise deployment",
        "deployment_status": "Enterprise-wide deployment",
        "deployment_detail": "Test detail.",
        "lifecycle_current_state": "Current / recent evidence",
        "confidence": "0.9",
        "evidence_type": "primary_operator_statement",
        "verification_status": "substantiated",
        "evidence_date": "2026-07-31",
        "source_url": "https://example.com/evidence",
        "notes": "Test note.",
    }
    base.update(overrides)
    return base


class DeploymentStatusNormalizationTest(unittest.TestCase):
    """EM1."""

    def test_direct_match_passes_through(self):
        self.assertEqual(ei._norm_deployment_status("Significant deployed footprint"), "significant_deployed_footprint")

    def test_enterprise_deployment_alias_maps_to_enterprise_wide(self):
        self.assertEqual(ei._norm_deployment_status("Enterprise deployment"), "enterprise_wide_deployment")

    def test_working_profile_alias(self):
        self.assertEqual(ei._norm_deployment_status("Working profile — public source required"), "working_profile_public_source_required")

    def test_historical_reseller_relationship_direct_match(self):
        self.assertEqual(
            ei._norm_deployment_status("Historical reseller relationship — superseded"),
            "historical_reseller_relationship_superseded",
        )

    def test_none_input_returns_none(self):
        self.assertIsNone(ei._norm_deployment_status(None))
        self.assertIsNone(ei._norm_deployment_status(""))

    def test_unrecognized_value_returns_none_not_a_guess(self):
        self.assertIsNone(ei._norm_deployment_status("Some made up status nobody wrote"))


class AiApplicationNormalizationTest(unittest.TestCase):
    """EM1."""

    def test_none_evidenced_maps_to_none_not_other(self):
        self.assertIsNone(ei._norm_ai_application("None evidenced"))

    def test_voice_ai_direct_match(self):
        self.assertEqual(ei._norm_ai_application("Voice AI"), "voice_ai")

    def test_unrecognized_real_value_maps_to_other(self):
        self.assertEqual(ei._norm_ai_application("Some Novel AI Thing"), "other")


class RowClassificationHelpersTest(unittest.TestCase):
    """EM2."""

    def test_working_profile_detected_via_evidence_type(self):
        row = _row(evidence_type="User-provided working profile", verification_status="Source capture required")
        self.assertTrue(ei._is_working_profile_row(row))

    def test_working_profile_detected_via_deployment_status(self):
        row = _row(deployment_status="Working profile — public source required", evidence_type="primary_operator_statement")
        self.assertTrue(ei._is_working_profile_row(row))

    def test_normal_sourced_row_is_not_a_working_profile(self):
        row = _row(evidence_type="primary_operator_statement", verification_status="substantiated", deployment_status="Enterprise-wide deployment")
        self.assertFalse(ei._is_working_profile_row(row))

    def test_pilot_only_detected(self):
        row = _row(deployment_status="Pilot only")
        self.assertTrue(ei._is_pilot_row(row))

    def test_product_name_masquerading_as_customer_rejected(self):
        self.assertTrue(ei._looks_like_product_not_customer("Acrelec timer solution", "Acrelec", "Acrelec timer solution"))

    def test_real_customer_name_accepted(self):
        self.assertFalse(ei._looks_like_product_not_customer("McDonald's", "Acrelec", "Acrelec timer solution"))

    def test_blank_customer_rejected(self):
        self.assertTrue(ei._looks_like_product_not_customer("", "Acrelec", None))


class WorkbookVendorRoleInferenceTest(unittest.TestCase):
    """EM2b: confirmed live against the real workbook -- HP and PAR Technology
    are tagged "Approved hardware vendor" for McDonald's POS, a genuinely
    different role from NewPOS's system-of-record claim. Falling back to the
    generic category-based inference collapsed all three onto the same
    "system_of_record_pos" role, producing a distinct rel_id collision that
    read as HP/PAR "updating" NewPOS's relationship rather than being their
    own coexisting hardware-vendor edges."""

    def test_approved_hardware_vendor_detected_from_deployment_status_text(self):
        row = _row(deployment_status="Approved hardware vendor — installed scope unconfirmed")
        self.assertEqual(ei._infer_workbook_vendor_role(row, "pos", "Some Terminal"), "approved_hardware_vendor")

    def test_reseller_detected(self):
        row = _row(deployment_status="Historical reseller relationship — superseded")
        self.assertEqual(ei._infer_workbook_vendor_role(row, "drive_thru_ai", "X"), "hardware_reseller_service_provider")

    def test_falls_back_to_generic_inference_when_no_explicit_role_text(self):
        row = _row(deployment_status="Enterprise-wide deployment")
        self.assertEqual(ei._infer_workbook_vendor_role(row, "pos", "NewPOS"), "system_of_record_pos")

    def test_hp_and_par_get_distinct_rel_id_from_newpos_for_same_brand_and_category(self):
        graph = _empty_graph()
        graph["entities"].append(_brand("brand-mcdonald-s", "McDonald's"))
        newpos_outcome, newpos_rel, _ = ei.reconcile_workbook_row(
            _row(vendor="NewPOS", customer="McDonald's", tech_category="POS Software",
                 deployment_status="Enterprise-wide deployment", evidence_type="primary_operator_statement",
                 verification_status="substantiated"),
            graph, {},
        )
        graph["relationships"].append(newpos_rel)
        hp_outcome, hp_rel, _ = ei.reconcile_workbook_row(
            _row(vendor="HP", customer="McDonald's", tech_category="POS Software",
                 deployment_status="Approved hardware vendor — installed scope unconfirmed",
                 evidence_type="operator_context", verification_status="provisional"),
            graph, {},
        )
        self.assertEqual(newpos_outcome, "new_relationship")
        self.assertEqual(hp_outcome, "new_relationship", "HP must be its own new edge, not mistaken for an update to NewPOS's relationship")
        self.assertNotEqual(hp_rel["id"], newpos_rel["id"])


class SourceAssertionMergeTest(unittest.TestCase):
    """EM3: guards against _upsert_relationship's shallow-merge overwriting
    source_assertions[] wholesale on every re-run."""

    def test_first_run_creates_single_assertion(self):
        merged = ei._merge_source_assertions(None, [{"source_id": "src-a", "discovered_at": "2026-07-31", "posture": "current"}])
        self.assertEqual(len(merged), 1)

    def test_rerunning_same_assertion_does_not_duplicate(self):
        assertion = {"source_id": "src-a", "url": "https://x", "discovered_at": "2026-07-31", "posture": "current"}
        first = ei._merge_source_assertions(None, [assertion])
        second = ei._merge_source_assertions(first, [dict(assertion, discovered_at="2026-08-01")])
        self.assertEqual(len(second), 1, "same source re-asserting the same claim must not duplicate")

    def test_different_source_appends_rather_than_replaces(self):
        first = ei._merge_source_assertions(None, [{"source_id": "src-a", "discovered_at": "2026-07-31", "posture": "current"}])
        second = ei._merge_source_assertions(first, [{"source_id": "src-b", "discovered_at": "2026-08-01", "posture": "current"}])
        self.assertEqual(len(second), 2)
        self.assertEqual({a["source_id"] for a in second}, {"src-a", "src-b"})

    def test_same_source_with_a_different_claim_is_preserved_not_overwritten(self):
        """A source correcting its own prior claim (e.g. updated location count)
        is a distinct assertion worth keeping, not a duplicate to collapse."""
        first = ei._merge_source_assertions(None, [{
            "source_id": "src-a", "discovered_at": "2026-01-01", "posture": "current", "live_locations": 100,
        }])
        second = ei._merge_source_assertions(first, [{
            "source_id": "src-a", "discovered_at": "2026-07-31", "posture": "current", "live_locations": 300,
        }])
        self.assertEqual(len(second), 2)


class BrandEntityResolutionTest(unittest.TestCase):
    """EM4."""

    def test_exact_name_match(self):
        graph = _empty_graph()
        graph["entities"].append(_brand("brand-mcdonald-s", "McDonald's"))
        entity_id, is_new = ei._resolve_brand_entity_id("McDonald's", graph)
        self.assertEqual(entity_id, "brand-mcdonald-s")
        self.assertFalse(is_new)

    def test_unknown_brand_creates_new_slug_id(self):
        graph = _empty_graph()
        entity_id, is_new = ei._resolve_brand_entity_id("Totally New Brand", graph)
        self.assertEqual(entity_id, "brand-totally-new-brand")
        self.assertTrue(is_new)

    def test_token_subset_matches_single_superset_candidate(self):
        graph = _empty_graph()
        graph["entities"].append(_brand("brand-checkers-rally-s", "Checkers & Rally's"))
        entity_id, is_new = ei._resolve_brand_entity_id("Checkers", graph)
        self.assertEqual(entity_id, "brand-checkers-rally-s")
        self.assertFalse(is_new)

    def test_ambiguous_between_two_existing_brands_without_continuity_is_unresolved(self):
        graph = _empty_graph()
        graph["entities"].append(_brand("brand-checkers", "Checkers"))
        graph["entities"].append(_brand("brand-checkers-rally-s", "Checkers & Rally's"))
        entity_id, is_new = ei._resolve_brand_entity_id("Checkers", graph)
        self.assertIsNone(entity_id, "two plausible candidates with no continuity signal must not be silently guessed")

    def test_relationship_continuity_disambiguates_between_two_existing_brands(self):
        """This is the real Checkers/Checkers & Rally's case: an existing
        Presto relationship anchored to 'Checkers & Rally's' should win over
        the more literal same-name 'Checkers' entity, since continuity with
        known evidence is a stronger signal than a bare name match."""
        graph = _empty_graph()
        graph["entities"].append(_brand("brand-checkers", "Checkers"))
        graph["entities"].append(_brand("brand-checkers-rally-s", "Checkers & Rally's"))
        graph["relationships"].append({
            "id": "rel-brand-checkers-rally-s-drive-thru-ai-unknown-vendor-presto",
            "from_entity_id": "brand-checkers-rally-s",
            "to_entity_id": "vendor-presto",
            "category": "drive_thru_ai",
            "status": "active",
        })
        entity_id, is_new = ei._resolve_brand_entity_id(
            "Checkers", graph, vendor_category_hints=[("Presto", "drive_thru_ai")],
        )
        self.assertEqual(entity_id, "brand-checkers-rally-s")

    def test_sheet_wide_hints_resolve_a_row_with_no_continuity_of_its_own(self):
        """The actual bug this was built to fix: Hi Auto's row has no prior
        relationship of its own (it's the *new* vendor), so per-row continuity
        alone can't resolve it -- but Presto's row (same customer name) does,
        and gathering hints across the whole sheet lets that resolve Hi Auto's
        row too, consistently, in the same migration pass."""
        graph = _empty_graph()
        graph["entities"].append(_brand("brand-checkers", "Checkers"))
        graph["entities"].append(_brand("brand-checkers-rally-s", "Checkers & Rally's"))
        graph["relationships"].append({
            "id": "rel-brand-checkers-rally-s-drive-thru-ai-unknown-vendor-presto",
            "from_entity_id": "brand-checkers-rally-s",
            "to_entity_id": "vendor-presto",
            "category": "drive_thru_ai",
            "status": "active",
        })
        # Hi Auto's own hint alone has no continuity -- only Presto's does.
        all_hints = [("Hi Auto", "drive_thru_ai"), ("Presto", "drive_thru_ai")]
        entity_id, is_new = ei._resolve_brand_entity_id("Checkers", graph, vendor_category_hints=all_hints)
        self.assertEqual(entity_id, "brand-checkers-rally-s")


class ReconcileWorkbookRowTest(unittest.TestCase):
    """EM5: every reconciliation outcome the request's classifier vocabulary
    requires, exercised directly against reconcile_workbook_row()."""

    def test_new_relationship(self):
        graph = _empty_graph()
        outcome, rel, assertion = ei.reconcile_workbook_row(_row(), graph, {})
        self.assertEqual(outcome, "new_relationship")
        self.assertIsNotNone(rel)
        self.assertEqual(rel["status"], "active")
        self.assertEqual(len(rel["source_assertions"]), 1)

    def test_invalid_customer_or_module_excluded_for_blank_customer(self):
        graph = _empty_graph()
        outcome, rel, _ = ei.reconcile_workbook_row(_row(customer=""), graph, {})
        self.assertEqual(outcome, "invalid_customer_or_module_excluded")
        self.assertIsNone(rel)

    def test_invalid_customer_or_module_excluded_for_product_as_customer(self):
        graph = _empty_graph()
        row = _row(vendor="Acrelec", customer="Acrelec timer solution", product_module="Acrelec timer solution")
        outcome, rel, _ = ei.reconcile_workbook_row(row, graph, {})
        self.assertEqual(outcome, "invalid_customer_or_module_excluded")

    def test_working_profile_not_promoted(self):
        graph = _empty_graph()
        row = _row(evidence_type="User-provided working profile", verification_status="Source capture required",
                    deployment_status="Working profile — public source required")
        outcome, rel, _ = ei.reconcile_workbook_row(row, graph, {})
        self.assertEqual(outcome, "working_profile_not_promoted")
        self.assertIsNone(rel, "a working profile must never be written as an active source-backed relationship")

    def test_pilot_not_promoted(self):
        graph = _empty_graph()
        row = _row(deployment_status="Pilot only")
        outcome, rel, _ = ei.reconcile_workbook_row(row, graph, {})
        self.assertEqual(outcome, "pilot_not_promoted")
        self.assertIsNone(rel)

    def test_entity_resolution_required_when_brand_ambiguous(self):
        graph = _empty_graph()
        graph["entities"].append(_brand("brand-test-brand", "Test Brand"))
        graph["entities"].append(_brand("brand-test-brand-alt", "Test Brand Alt"))
        row = _row(customer="Test Brand")
        # Force ambiguity by making both candidates token-superset matches of "Test Brand".
        graph["entities"][1]["name"] = "Test Brand Something"
        outcome, rel, _ = ei.reconcile_workbook_row(row, graph, {})
        self.assertEqual(outcome, "entity_resolution_required")
        self.assertIsNone(rel)

    def test_lifecycle_update_when_deployment_status_changes_on_existing_active_relationship(self):
        graph = _empty_graph()
        graph["entities"].append(_brand("brand-test-brand", "Test Brand"))
        rel_id = f"rel-brand-test-brand-pos-{ei._slug('system_of_record_pos')}-vendor-testvendor"
        graph["relationships"].append({
            "id": rel_id, "from_entity_id": "brand-test-brand", "to_entity_id": "vendor-testvendor",
            "category": "pos", "status": "active", "evidence_posture": "provisional",
            "deployment_status": "contracted_deployment_pending", "sources": ["src-old"],
            "source_assertions": [],
        })
        row = _row(deployment_status="Brand-wide deployment")
        outcome, rel, _ = ei.reconcile_workbook_row(row, graph, {})
        self.assertEqual(outcome, "lifecycle_update")
        self.assertEqual(rel["id"], rel_id)

    def test_duplicate_no_change_when_nothing_new(self):
        graph = _empty_graph()
        graph["entities"].append(_brand("brand-test-brand", "Test Brand"))
        row = _row()
        # First pass creates it for real.
        outcome1, rel1, assertion1 = ei.reconcile_workbook_row(row, graph, {})
        graph["relationships"].append(rel1)
        # Second pass with the identical row should be a no-op.
        outcome2, rel2, _ = ei.reconcile_workbook_row(row, graph, {})
        self.assertEqual(outcome2, "duplicate_no_change")
        self.assertIsNone(rel2)

    def test_preserves_stronger_existing_evidence(self):
        """A weaker workbook row must not downgrade an already-substantiated
        relationship's confidence/posture -- it still contributes its source
        assertion for history, but the stronger existing fields win."""
        graph = _empty_graph()
        graph["entities"].append(_brand("brand-test-brand", "Test Brand"))
        rel_id = f"rel-brand-test-brand-pos-{ei._slug('system_of_record_pos')}-vendor-testvendor"
        graph["relationships"].append({
            "id": rel_id, "from_entity_id": "brand-test-brand", "to_entity_id": "vendor-testvendor",
            "category": "pos", "status": "active", "evidence_posture": "substantiated",
            "confidence": {"level": "high"}, "deployment_status": "enterprise_wide_deployment",
            "sources": ["src-old"], "source_assertions": [],
        })
        weak_row = _row(evidence_type="vendor_logo_customer_page", confidence="0.3")
        outcome, rel, _ = ei.reconcile_workbook_row(weak_row, graph, {})
        self.assertIn(outcome, {"new_source_for_existing_relationship", "duplicate_no_change"})
        if rel is not None:
            self.assertEqual(rel["evidence_posture"], "substantiated")
            self.assertEqual(rel["confidence"], {"level": "high"})

    def test_supersedes_existing_relationship_marks_prior_active_as_historical(self):
        graph = _empty_graph()
        graph["entities"].append(_brand("brand-test-brand", "Test Brand"))
        rel_id = f"rel-brand-test-brand-{ei._slug('drive_thru_ai')}-{ei._slug('unknown')}-vendor-testvendor"
        graph["relationships"].append({
            "id": rel_id, "from_entity_id": "brand-test-brand", "to_entity_id": "vendor-testvendor",
            "category": "drive_thru_ai", "status": "active", "evidence_posture": "provisional",
            "deployment_status": "active_rollout", "sources": ["src-old"], "source_assertions": [],
        })
        row = _row(
            tech_category="Drive-Thru Voice AI",
            deployment_status="Historical reseller relationship — superseded",
            evidence_type="SEC filing", verification_status="SEC-corroborated historical relationship and end date",
        )
        outcome, rel, _ = ei.reconcile_workbook_row(row, graph, {})
        self.assertEqual(outcome, "supersedes_existing_relationship")
        self.assertEqual(rel["status"], "historical")
        self.assertEqual(rel["id"], rel_id)


class DeploymentClaimTypePopulationTest(unittest.TestCase):
    """EM7: reconcile_workbook_row() never set deployment_claim_type, which
    relationship_classification.py's classify_relationship() reads to place
    a relationship on the 6-level model -- every relationship built through
    this function (migrate-workbook AND import-phase2-evidence both call it)
    landed unclassified regardless of how specific the underlying evidence
    actually was. Confirmed live 2026-08-05: 365 of 381 relationships were
    unclassified after the Phase 2 vendor-first import wave, and running
    classify-all changed nothing because there was nothing for it to read."""

    def test_enterprise_wide_deployment_gets_systemwide_claim_type(self):
        graph = _empty_graph()
        row = _row(deployment_status="Enterprise-wide deployment", evidence_type="primary_operator_statement")
        _outcome, rel, _assertion = ei.reconcile_workbook_row(row, graph, {})
        self.assertEqual(rel["deployment_claim_type"], "systemwide_deployment")

    def test_pilot_only_gets_pilot_claim_type(self):
        # deployment_status="Pilot only" short-circuits to pilot_not_promoted
        # (never reaches deployment_claim_type assignment) -- exercise the
        # stage mapping directly via a non-excluded pilot-adjacent status
        # instead, using vendor_role="pilot" as the trigger.
        graph = _empty_graph()
        row = _row(deployment_status="Active rollout", relationship_scope="Pilot rollout underway")
        _outcome, rel, _assertion = ei.reconcile_workbook_row(row, graph, {})
        self.assertEqual(rel["deployment_claim_type"], "pilot")

    def test_case_study_without_explicit_scope_gets_reference_only_not_a_guess(self):
        """A case study that never cites a unit count is genuinely
        under-specified for the 5-level operational-scale ladder -- this
        must stay unclassifiable, not get force-fit into a level."""
        graph = _empty_graph()
        row = _row(evidence_type="Vendor case study", deployment_status="Deployed — scope not publicly disclosed")
        _outcome, rel, _assertion = ei.reconcile_workbook_row(row, graph, {})
        self.assertEqual(rel["deployment_claim_type"], "reference_only")

    def test_end_to_end_classify_relationship_now_places_it_on_the_ladder(self):
        """Round-trip through the real classifier, not just this module's
        own field, to prove the fix actually unblocks classification."""
        rc_spec = importlib.util.spec_from_file_location(
            "relationship_classification", ROOT / "system" / "scripts" / "relationship_classification.py",
        )
        rc = importlib.util.module_from_spec(rc_spec)
        assert rc_spec.loader is not None
        rc_spec.loader.exec_module(rc)

        graph = _empty_graph()
        row = _row(deployment_status="Enterprise-wide deployment", evidence_type="primary_operator_statement")
        _outcome, rel, _assertion = ei.reconcile_workbook_row(row, graph, {})
        classification = rc.classify_relationship(rel)
        self.assertIsNotNone(classification, "relationship should now be classifiable, not silently dropped")
        self.assertEqual(classification["level_name"], "standardized_platform")


@unittest.skipUnless(REAL_GRAPH.exists() and REAL_WORKBOOK.exists(), "requires the real graph and workbook on disk")
class LiveHiAutoPrestoCorrectionTest(unittest.TestCase):
    """EM6: the exact named correction from the implementation request.

    The workbook migration was confirmed against the live graph on
    2026-08-01 (system/_snapshots/workbook_migration_report-20260801-083222.json),
    so REAL_GRAPH now already reflects the corrected end state -- this test
    checks that end state directly, plus confirms re-running the same
    migration against it is a true no-op (idempotent, not a duplicate
    write)."""

    def test_graph_already_reflects_the_correction(self):
        graph = json.loads(REAL_GRAPH.read_text())
        by_id = ei._index_by_id(graph.get("entities") or [])
        hi_auto_rel = next(
            r for r in graph["relationships"]
            if r["to_entity_id"] == "vendor-hi-auto" and by_id.get(r["from_entity_id"], {}).get("name", "").startswith("Checkers")
        )
        presto_rel = next(
            r for r in graph["relationships"]
            if r["to_entity_id"] == "vendor-presto" and by_id.get(r["from_entity_id"], {}).get("name", "").startswith("Checkers")
        )

        self.assertEqual(hi_auto_rel["status"], "active")
        self.assertEqual(hi_auto_rel["ai_application"], "voice_ai")
        self.assertEqual(presto_rel["status"], "historical")
        self.assertEqual(
            hi_auto_rel["from_entity_id"], presto_rel["from_entity_id"],
            "Hi Auto and Presto must resolve to the same Checkers brand entity, "
            "not the two separate brand-checkers / brand-checkers-rally-s entities",
        )

    def test_rerunning_the_migration_against_the_corrected_graph_is_a_no_op(self):
        """RB-2026-08-29: the Presto/Checkers relationship legitimately
        advanced past this test's original 2026-08-01 snapshot -- the
        tech-stack workbook's "Evidence Ledger" sheet was ingested
        (tech_stack_workbook_sync.py), re-asserting the same real SEC
        filing with a fresher confidence score and a differently-worded but
        equivalent deployment_status. Re-running the OLD "Vendor Customer
        Lists" row against that now-updated relationship correctly reads as
        a lifecycle_update (real content differs from the stale row), not a
        no-op -- confirmed this isn't data loss: vendor_role, the brand
        entity resolution, and the original source_assertion are all intact
        (see tech_stack_workbook_sync.py's own regression tests). Hi Auto's
        row is untouched by that ingest and is still a true no-op."""
        graph = json.loads(REAL_GRAPH.read_text())
        by_id = ei._index_by_id(graph.get("entities") or [])
        rows = ei.load_rows(REAL_WORKBOOK, sheet_name="Vendor Customer Lists", required_header_keys=("vendor", "customer"))
        brand_hints = ei._brand_hints_by_customer_name(rows)

        hi_auto_row = next(r for r in rows if r.get("vendor") == "Hi Auto" and r.get("customer") == "Checkers")
        presto_row = next(r for r in rows if r.get("vendor") == "Presto" and r.get("customer") == "Checkers")

        hi_auto_outcome, _, _ = ei.reconcile_workbook_row(hi_auto_row, graph, by_id, brand_hints=brand_hints)
        presto_outcome, presto_rel, _ = ei.reconcile_workbook_row(presto_row, graph, by_id, brand_hints=brand_hints)

        self.assertEqual(hi_auto_outcome, "duplicate_no_change")
        self.assertEqual(presto_outcome, "lifecycle_update")
        self.assertEqual(presto_rel["vendor_role"], "hardware_reseller_service_provider")


if __name__ == "__main__":
    unittest.main()
