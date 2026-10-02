"""
test_wiring_gaps.py — RB 9.23
Tests for the three previously unwired intelligence processing endpoints:
  processInsight       (POST /insight/intake)   — DEFECT-009 fix
  processRelationshipIntake (POST /relationship/intake) — DEFECT-011 (already wired, verify)
  processMacroSignal   (POST /macro/signal)     — DEFECT-012 (already wired, verify)

Also verifies:
  - All three operation IDs are present in openapi_gpt.yaml (budget audit)
  - triage stream target_endpoints point to the correct (new) routes

Test groups:
  WG1 (4):  processInsight — structure, noise, mutation proposals, confirms write path
  WG2 (2):  processRelationshipIntake — present and reachable via API
  WG3 (2):  processMacroSignal — present and reachable via API
  WG4 (3):  GPT YAML budget — new ops present, retired ops absent, total == 29
  WG5 (3):  triage target_endpoint routing — macro→/macro/signal,
             ri→/relationship/intake, strategic→/insight/intake
  WG6 (4):  RB-DEFECT-037 — Active Opportunity Pipeline (processOpportunityUpdate,
             getOpportunityPipeline, triage routing to career_pipeline_update)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "system" / "api"))

try:
    from fastapi.testclient import TestClient
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

import intelligence_triage as triage


# ---------------------------------------------------------------------------
# WG1 — processInsight endpoint (DEFECT-009)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestWG1_ProcessInsight(unittest.TestCase):
    """WG1: POST /insight/intake (processInsight) wires insight_intake.process_text()."""

    @classmethod
    def setUpClass(cls):
        import server
        cls.client = TestClient(server.app)

    def _post(self, text: str, **kwargs) -> dict:
        payload = {"text": text, **kwargs}
        resp = self.client.post(
            "/insight/intake", json=payload, headers={"x-api-key": "test-key"}
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def test_WG1a_returns_required_keys(self):
        """WG1a: processInsight returns insights, mutation_proposals, persistence_status."""
        data = self._post(
            "My view is that restaurant tech consolidation is accelerating — "
            "the real opportunity is in payments integration."
        )
        for key in ("insights", "mutation_proposals", "retrieval_tags",
                    "persistence_status"):
            self.assertIn(key, data, f"processInsight missing key: {key}")

    def test_WG1b_insights_classified_for_thesis_text(self):
        """WG1b: Strategic thesis text produces at least one classified insight."""
        data = self._post(
            "I believe PAR Technology is undervalued. "
            "My thesis is that their operator flywheel creates a moat competitors can't match."
        )
        self.assertGreater(len(data["insights"]), 0,
                           "Expected at least one insight for clear thesis text")

    def test_WG1c_mutation_proposals_require_confirmation(self):
        """WG1c: Every mutation proposal in processInsight requires confirmation."""
        data = self._post(
            "My strategic view: Global Payments is positioned to win restaurant payments. "
            "I think this validates our investment thesis."
        )
        for proposal in data["mutation_proposals"]:
            self.assertTrue(
                proposal.get("requires_confirmation", True),
                f"Proposal missing requires_confirmation: {proposal}",
            )

    def test_WG1d_noise_text_returns_not_persisted(self):
        """WG1d: Noise text returns persistence_status='RB did not persist'."""
        data = self._post("ok sounds good talk later")
        self.assertEqual(data["persistence_status"], "RB did not persist")

    def test_WG1e_missing_text_returns_422(self):
        """WG1e: Missing required 'text' field returns HTTP 422."""
        import server
        client = TestClient(server.app)
        resp = client.post(
            "/insight/intake",
            json={"source_type": "paste"},
            headers={"x-api-key": "test-key"},
        )
        self.assertEqual(resp.status_code, 422)


# ---------------------------------------------------------------------------
# WG2 — processRelationshipIntake endpoint (DEFECT-011, verify reachable)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestWG2_ProcessRelationshipIntake(unittest.TestCase):
    """WG2: POST /relationship/intake (processRelationshipIntake) is reachable.

    RB-DEFECT-042 (part 3): WG2a hits /relationship/intake with a named
    entity, which writes a 'proposed' row to the real interaction_ledger.json
    via relationship_intake.process_relationship_thread(). Patch
    INTERACTION_LEDGER_PATH to a tempfile for this class.
    """

    @classmethod
    def setUpClass(cls):
        import server
        cls.client = TestClient(server.app)

    def setUp(self):
        import tempfile
        import unittest.mock
        import relationship_intake as ri
        self._tmpdir = tempfile.TemporaryDirectory()
        self._ledger_path = Path(self._tmpdir.name) / "interaction_ledger.json"
        self._patcher = unittest.mock.patch.object(
            ri, "INTERACTION_LEDGER_PATH", self._ledger_path
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def test_WG2a_returns_cos_surface_for_named_entity(self):
        """WG2a: processRelationshipIntake returns interactions and cos_surface."""
        resp = self.client.post(
            "/relationship/intake",
            json={
                "text": (
                    "Had a great call with Oliver Ostertag at PAR Technology. "
                    "He confirmed the pilot launch is on track for Q4."
                ),
                "entity_name": "Oliver Ostertag",
                "entity_org": "PAR Technology",
                "source_type": "meeting_note",
            },
            headers={"x-api-key": "test-key"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        for key in ("interactions", "mutation_proposals", "persistence_status"):
            self.assertIn(key, data, f"processRelationshipIntake missing key: {key}")

    def test_WG2b_no_entity_returns_not_persisted(self):
        """WG2b: Text with no detectable entity returns not_persisted."""
        resp = self.client.post(
            "/relationship/intake",
            json={"text": "The market is shifting rapidly this quarter."},
            headers={"x-api-key": "test-key"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("RB did not persist", data.get("persistence_status", ""))


# ---------------------------------------------------------------------------
# WG3 — processMacroSignal endpoint (DEFECT-012, verify reachable)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestWG3_ProcessMacroSignal(unittest.TestCase):
    """WG3: POST /macro/signal (processMacroSignal) is reachable."""

    @classmethod
    def setUpClass(cls):
        import server
        cls.client = TestClient(server.app)

    def test_WG3a_returns_behavioral_signals_for_macro_text(self):
        """WG3a: processMacroSignal returns behavioral_signals for market content."""
        resp = self.client.post(
            "/macro/signal",
            json={
                "text": (
                    "Consumer spending at QSR is down 4% — trade-down behavior accelerating. "
                    "Drive-thru traffic fell while value menu orders surged. "
                    "Operators are seeing affordability stress across all dayparts."
                ),
                "source_type": "newsletter",
            },
            headers={"x-api-key": "test-key"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        for key in ("behavioral_signals", "mutation_proposals", "persistence_status"):
            self.assertIn(key, data, f"processMacroSignal missing key: {key}")

    def test_WG3b_noise_returns_not_persisted(self):
        """WG3b: Non-macro text returns not_persisted."""
        resp = self.client.post(
            "/macro/signal",
            json={"text": "Hello, how are you doing today?"},
            headers={"x-api-key": "test-key"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("RB did not persist", data.get("persistence_status", ""))


# ---------------------------------------------------------------------------
# WG4 — GPT YAML budget: new ops present, retired ops absent, total == 29
# RB 9.42: queryEngine replaces 7 fragmented query ops (net -6, 30 → 24)
# RB 9.6x: DEFECT-009/011/012 reconciliation retires 8 zero-usage ops, adds
#   triageInput, manualRelationshipIntake, getThesisConvergence, queryContacts
#   (29 -> 25), then re-adds processRelationshipIntake/processMacroSignal so
#   triageInput's routing isn't a dead end (25 -> 27)
# RB-DEFECT-037: adds processOpportunityUpdate/getOpportunityPipeline so
#   triageInput's career_pipeline_update routing isn't a dead end (27 -> 29)
# ---------------------------------------------------------------------------

class TestWG4_GPTYAMLBudget(unittest.TestCase):
    """WG4: the GPT exposes only consolidated operational-control actions."""

    @classmethod
    def setUpClass(cls):
        try:
            import yaml
            gpt_yaml_path = ROOT / "system" / "api" / "openapi_gpt.yaml"
            spec = yaml.safe_load(gpt_yaml_path.read_text(encoding="utf-8"))
            cls.operation_ids: set[str] = set()
            for path_item in (spec.get("paths") or {}).values():
                for method_item in path_item.values():
                    if isinstance(method_item, dict):
                        op_id = method_item.get("operationId")
                        if op_id:
                            cls.operation_ids.add(op_id)
            cls._yaml_loaded = True
        except Exception as exc:
            cls._yaml_loaded = False
            cls._yaml_error = str(exc)

    def _skip_if_no_yaml(self):
        if not self._yaml_loaded:
            self.skipTest(f"Could not load openapi_gpt.yaml: {self._yaml_error}")

    def test_WG4a_consolidated_operations_present(self):
        """WG4a: universal ingestion, query, refresh, and brief actions are present."""
        self._skip_if_no_yaml()
        for op in ("uploadAndIngestFile", "queryEngine", "refreshSources", "getDailyBrief"):
            self.assertIn(op, self.operation_ids, f"Op missing from YAML: {op}")

    def test_WG4b_retired_operations_absent(self):
        """WG4b: evaluatePassiveIntelligence and the 7 fragmented query ops replaced
        by queryEngine are absent from the YAML.
        Note: triageInput was previously retired but re-added in RB 9.43 (DEFECT-015)
        and remains live (referenced by custom_gpt_instructions_compact_8k.md).
        Note: refreshSources was previously retired but re-added in RB 9.56 to enable
        autonomous source recovery when brief_status=DEGRADED.
        Note (DEFECT-009/011/012 reconciliation): ingestLinkedInProfile,
        getJobIntelligence, toggleJobSearch, getIntelligenceCollection,
        getIntelligenceCollectionAudit, getSmsExemptHandles, addSmsExemptHandle,
        and removeSmsExemptHandle were retired from the GPT subset to free budget
        for triageInput, manualRelationshipIntake, getThesisConvergence, and
        queryContacts (referenced by live Instructions but previously absent).
        These 8 had zero references in any GPT-facing instructions/knowledge file
        and zero/near-zero request.log usage. Routes remain live in the full
        internal API (openapi.yaml) and reachable directly (e.g. the LinkedIn
        profile bookmarklet POSTs to /contacts/ingest_linkedin_profile from the
        browser, bypassing GPT Actions entirely).
        Note (DEFECT-011/012 re-reconciliation): processRelationshipIntake and
        processMacroSignal were previously retired here, but STATUS.md claimed
        they were "Custom GPT action in openapi_gpt.yaml" (false) — triageInput's
        processing_order routes ri_event/macro_signal to these endpoints, so
        without them triageInput's routing was a dead end. Both are re-added
        (25 -> 27, still <= 30)."""
        self._skip_if_no_yaml()
        retired = (
            "evaluatePassiveIntelligence",
            "queryRelationships", "queryWhoMattersNow", "queryMacroSignals",
            "queryMacroArtifacts", "queryMacroEntities", "queryInsights", "getIntelligence",
            # Internal processing routes are selected by universal ingestion.
            "processInsight",
            "classifyArtifact", "ingestLinkedInExport", "ingestLinkedInCSV",
            "ingestLinkedInExtended", "applyIntelligenceMutations",
            # DEFECT-009/011/012 reconciliation retirements (RB 9.6x)
            "ingestLinkedInProfile", "getJobIntelligence", "toggleJobSearch",
            "getIntelligenceCollection", "getIntelligenceCollectionAudit",
            "getSmsExemptHandles", "addSmsExemptHandle", "removeSmsExemptHandle",
        )
        for op in retired:
            self.assertNotIn(op, self.operation_ids,
                             f"Retired op still in YAML: {op}")

    def test_WG4b2_query_engine_present(self):
        """WG4b2: queryEngine is present in the YAML (RB 9.42)."""
        self._skip_if_no_yaml()
        self.assertIn("queryEngine", self.operation_ids,
                      "queryEngine missing from YAML")

    def test_WG4c_total_operation_count_is_30(self):
        """WG4c: GPT surface is small enough for reliable tool selection
        and within the 30-op Custom GPT Actions hard platform limit
        (DEFECT-009/011/012 reconciliation + RB-DEFECT-037, RB 9.6x:
        29 -> 25 -> 27 -> 29; RB 9.90 (RB-DEFECT-046 Slice 3) adds
        getCompanyIntelligenceFile: 29 -> 30, at the cap; RB-DEFECT-2026-07-06
        retires confirmIdentityMatch/rejectIdentityMatch in favor of a single
        generic confirmProposal action covering identity matches plus four
        proposal types that had no GPT action at all: 30 -> 29, one slot
        free again; RB-DEFECT-2026-07-09 adds ingestExecutiveDeclaration --
        referenced by custom_gpt_instructions_compact_8k.md's CEO-declaration
        routing rule since RB-DEFECT-062 (2026-07-03) but never actually
        added to the curated subset, so the model was told to call a tool it
        was never given. Confirmed root cause of Life Lens never updating
        (zero calls to /ingest/executive_declaration, ever): 29 -> 30, at
        the cap again."""
        self._skip_if_no_yaml()
        self.assertEqual(len(self.operation_ids), 30,
                         f"Expected 30 ops, got {len(self.operation_ids)}: "
                         f"{sorted(self.operation_ids)}")


# ---------------------------------------------------------------------------
# WG5 — triage stream target_endpoint routing
# ---------------------------------------------------------------------------

class TestWG5_TriageRouting(unittest.TestCase):
    """WG5: triage_input() streams point to the correct wired endpoints (RB 9.23)."""

    REGISTRY = {
        "contract": "rb_intelligence_artifact_registry_v1",
        "artifacts": [],
    }

    def _stream(self, result, intelligence_type: str) -> dict | None:
        for s in result["identified_types"]:
            if s["intelligence_type"] == intelligence_type:
                return s
        return None

    def test_WG5a_macro_routes_to_macro_signal(self):
        """WG5a: macro_signal stream target_endpoint is /macro/signal."""
        result = triage.triage_input(
            "Consumer traffic down sharply — affordability stress and trade-down behavior "
            "accelerating across all QSR dayparts. Drive-thru visits fell 6%.",
            registry=self.REGISTRY,
        )
        s = self._stream(result, triage.TYPE_MACRO)
        self.assertIsNotNone(s, "No macro_signal stream returned")
        self.assertEqual(s["target_endpoint"], "/macro/signal")

    def test_WG5b_ri_routes_to_relationship_intake(self):
        """WG5b: ri_event stream target_endpoint is /relationship/intake."""
        result = triage.triage_input(
            "Had a meeting with Oliver Ostertag yesterday. "
            "He confirmed the new pilot launch and asked to reconnect next week.",
            registry=self.REGISTRY,
        )
        s = self._stream(result, triage.TYPE_RI)
        self.assertIsNotNone(s, "No ri_event stream returned")
        self.assertEqual(s["target_endpoint"], "/relationship/intake")

    def test_WG5c_strategic_routes_to_insight_intake(self):
        """WG5c: strategic_memory stream target_endpoint is /insight/intake."""
        result = triage.triage_input(
            "This validates my thesis that restaurant tech is consolidating. "
            "My strategic view: the winner will be whoever controls payments at the POS.",
            registry=self.REGISTRY,
        )
        s = self._stream(result, triage.TYPE_STRATEGIC)
        self.assertIsNotNone(s, "No strategic_memory stream returned")
        self.assertEqual(s["target_endpoint"], "/insight/intake")


# ---------------------------------------------------------------------------
# WG6 — RB-DEFECT-037: Active Opportunity Pipeline
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestWG6_OpportunityPipeline(unittest.TestCase):
    """WG6: POST /opportunity/update (processOpportunityUpdate) and
    GET /opportunity/pipeline (getOpportunityPipeline) are wired."""

    @classmethod
    def setUpClass(cls):
        import server
        cls.client = TestClient(server.app)

    def test_WG6a_verbal_offer_detected_with_company(self):
        """WG6a: verbal-offer text with explicit company is detected and proposes a mutation."""
        resp = self.client.post(
            "/opportunity/update",
            json={
                "text": "I received a verbal offer from Global Payments and the offer "
                        "package is under evaluation.",
                "company": "Global Payments",
            },
            headers={"x-api-key": "test-key"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data["detected"])
        self.assertEqual(data["opportunity"]["stage"], "offer_verbal")
        self.assertEqual(data["persistence_status"], "pending confirmation")
        self.assertTrue(data["mutation_proposals"])
        self.assertTrue(data["mutation_proposals"][0]["requires_confirmation"])

    def test_WG6b_no_signal_returns_not_persisted(self):
        """WG6b: text with no stage/company signal returns RB did not persist."""
        resp = self.client.post(
            "/opportunity/update",
            json={"text": "Had a nice lunch today."},
            headers={"x-api-key": "test-key"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["persistence_status"], "RB did not persist")

    def test_WG6c_get_pipeline_returns_contract(self):
        """WG6c: getOpportunityPipeline returns the rb_opportunity_pipeline_v1 contract."""
        resp = self.client.get(
            "/opportunity/pipeline", headers={"x-api-key": "test-key"}
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        for key in ("contract", "opportunities", "count", "generated_at"):
            self.assertIn(key, data, f"getOpportunityPipeline missing key: {key}")
        self.assertEqual(data["contract"], "rb_opportunity_pipeline_v1")

    def test_WG6d_career_pipeline_routes_to_opportunity_update(self):
        """WG6d: triage routes career_pipeline_update streams to /opportunity/update."""
        result = triage.triage_input(
            "Foods Connected confirmed I'm one of the final two candidates, "
            "with final interviews scheduled June 23-24.",
            registry={"contract": "rb_intelligence_artifact_registry_v1", "artifacts": []},
        )
        streams = [s for s in result["identified_types"]
                   if s["intelligence_type"] == triage.TYPE_CAREER]
        self.assertTrue(streams, "No career_pipeline_update stream returned")
        self.assertEqual(streams[0]["target_endpoint"], "/opportunity/update")
        self.assertIn(triage.TYPE_CAREER, result["trust_stats"]["processing_order"])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
