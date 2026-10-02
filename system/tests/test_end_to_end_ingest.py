"""
test_end_to_end_ingest.py — End-to-end intelligence pipeline integration tests (RB 9.26).

Simulates the full flow the live Custom GPT would execute:
  share experience → ingest → surface → confirm → retrieve → externalize

Tests the complete chain across all four intelligence paths to verify that
no data is lost, corrupted, or silently dropped between modules.

Covers:
  E2E-1: Full experience flow (ingest → surface → confirm → retrieve → externalize)
  E2E-2: Full macro flow (ingest → surface → confirm → daily brief layer)
  E2E-3: Full relationship flow (ingest → surface → confirm → query)
  E2E-4: Full insight flow (ingest → surface → confirm → query)
  E2E-5: Daily brief with confirmed behavioral intelligence
  E2E-6: Pre-flight check validates full stack
  E2E-7: Cross-module trust contract — no path returns without persistence_status
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import experiential_intelligence as ei
import macro_intelligence as mi
import relationship_intake as ri
import insight_intake as ii
import ingest_surface as surf
import cos_judgment
import mutations


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

EXPERIENCE_TEXT = (
    "I worked at NomadGo on a restaurant AI deployment at Starbucks. "
    "The system failed because we duplicated existing workflows instead of replacing them. "
    "Operators had to enter data twice. The root cause was that trust was never established "
    "before we scaled. The lesson: earn operational trust in a single unit before multi-unit "
    "rollout. Never assume operators will adopt a system that adds work."
)

MACRO_TEXT = (
    "Saw people pulling into the Five Guys parking lot, sitting a few minutes, and driving away. "
    "They did not get out of the car. The math just does not add up — two burgers costs $35 "
    "when McDonald's is a fraction of the price. Affordability is cracking in the premium segment."
)

RELATIONSHIP_TEXT = (
    "Had a great call with Maria Santos, CTO at Olo. She reached out proactively to explore "
    "collaboration on restaurant AI adoption frameworks. Direct inquiry — very warm signal."
)

INSIGHT_TEXT = (
    "Restaurant operators are structurally resistant to AI because the adoption cost is front-loaded "
    "while the value is back-loaded. The thesis: loyalty-as-infrastructure is the right positioning "
    "for restaurant AI — not operational efficiency. This is a contrarian take that deserves "
    "thought leadership development."
)


# ---------------------------------------------------------------------------
# E2E-1: Full experience flow
# ---------------------------------------------------------------------------

class TestExperienceE2E(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "ei.json"

    def test_full_experience_flow(self):
        # Step 1: Ingest
        result = ei.process_experiential_signal(
            EXPERIENCE_TEXT,
            source_type="deployment_debrief",
            employer_names=["NomadGo", "Starbucks"],
            store_path=self.store,
        )
        self.assertGreater(len(result["experiences"]), 0)
        self.assertEqual(result["persistence_status"], "pending confirmation")
        self.assertTrue(result["trust_stats"]["trust_contract_met"])
        self.assertTrue(result["employer_sensitive"])

        exp_id = result["experiences"][0]["id"]

        # Step 2: Surface
        surface = surf.format_surface(result)
        self.assertIn("EXPERIENTIAL INTELLIGENCE", surface)
        self.assertIn("TRUST STATS", surface)
        self.assertIn("EMPLOYER SENSITIVITY", surface)
        self.assertIn("pending confirmation", surface)
        self.assertNotIn("RB recorded", surface)

        # Step 3: Confirm
        confirmed = ei.record_experience(exp_id, confirmed=True, store_path=self.store)
        self.assertEqual(confirmed["claim_status"], "confirmed")
        self.assertEqual(confirmed["persistence_status"], "RB recorded")
        self.assertIsNotNone(confirmed["confirmed_at"])

        confirm_surface = surf.format_confirm("experience", confirmed)
        self.assertIn("confirmed", confirm_surface.lower())
        self.assertIn("RB recorded", confirm_surface)

        # Step 4: Retrieve
        retrieve_result = ei.query_retrieval_hooks("restaurant_tech", store_path=self.store)
        self.assertGreater(retrieve_result["match_count"], 0)
        retrieve_surface = surf.format_retrieval("restaurant_tech", retrieve_result)
        self.assertIn("RESTAURANT_TECH", retrieve_surface)

        # Step 5: Externalize
        ext = ei.externalize(exp_id, store_path=self.store)
        self.assertIn("external_version", ext)
        self.assertTrue(ext["externalization_applied"])
        ext_snippet = str(ext["external_version"]).lower()
        self.assertNotIn("nomadgo", ext_snippet)
        self.assertNotIn("starbucks", ext_snippet)

    def test_no_data_lost_through_confirm(self):
        result = ei.process_experiential_signal(EXPERIENCE_TEXT, store_path=self.store)
        exp_id = result["experiences"][0]["id"]

        # Confirm — then retrieve — should get the same ID back
        ei.record_experience(exp_id, confirmed=True, store_path=self.store)
        confirmed_records = ei.query_experiences(claim_status="confirmed", store_path=self.store)
        ids = [r["id"] for r in confirmed_records]
        self.assertIn(exp_id, ids)

    def test_rejected_does_not_appear_in_confirmed(self):
        result = ei.process_experiential_signal(EXPERIENCE_TEXT, store_path=self.store)
        exp_id = result["experiences"][0]["id"]
        ei.record_experience(exp_id, confirmed=False, store_path=self.store)
        confirmed_records = ei.query_experiences(claim_status="confirmed", store_path=self.store)
        self.assertEqual(len(confirmed_records), 0)


# ---------------------------------------------------------------------------
# E2E-2: Full macro flow
# ---------------------------------------------------------------------------

class TestMacroE2E(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bstore = Path(self.tmp) / "bi.json"
        self.estore = Path(self.tmp) / "ei.json"
        self.ri_store = Path(self.tmp) / "ri.json"

    def test_full_macro_flow(self):
        # Step 1: Ingest
        result = mi.process_macro_signal(
            MACRO_TEXT,
            source_type="linkedin_post",
            author_name="David Mann",
            author_org="Restaurant Group",
            author_role="Principal",
            behavioral_store_path=self.bstore,
            entity_store_path=self.estore,
            ri_store_path=self.ri_store,
        )
        self.assertIn("behavioral_signals", result)
        self.assertIn("persistence_status", result)
        self.assertIn(result["persistence_status"], mi.PERSISTENCE_STATUSES)
        signals = result.get("behavioral_signals") or []
        self.assertGreater(len(signals), 0)

        # Step 2: Surface
        surface = surf.format_surface(result)
        self.assertIn("MACRO BEHAVIORAL INTELLIGENCE", surface)
        self.assertIn("consumer_hesitation", surface)

        # Step 3: Confirm a behavioral record
        stored = json.loads(self.bstore.read_text()).get("records", [])
        if stored:
            record_id = stored[0]["id"]
            confirmed = mi.record_behavioral_record(
                record_id, confirmed=True, behavioral_store_path=self.bstore
            )
            self.assertEqual(confirmed["claim_status"], "confirmed")

        # Step 4: Verify daily brief layer reads confirmed records
        bi_layer = cos_judgment.build_behavioral_intelligence_layer(
            behavioral_path=self.bstore, entity_path=self.estore
        )
        if stored:
            self.assertTrue(bi_layer["available"])
            self.assertGreater(bi_layer["signal_count"] + bi_layer["artifact_count"], 0)

    def test_author_ri_mutation_present_when_author_given(self):
        result = mi.process_macro_signal(
            MACRO_TEXT,
            author_name="David Mann",
            author_org="Restaurant Group",
            author_role="Principal",
            ri_store_path=self.ri_store,
        )
        self.assertIsNotNone(result.get("ri_mutation"))

    def test_no_ri_mutation_without_author(self):
        result = mi.process_macro_signal(MACRO_TEXT)
        # ri_mutation should be None or have no cos_surface when no author
        ri = result.get("ri_mutation")
        if ri:
            self.assertFalse(ri.get("cos_surface"))


# ---------------------------------------------------------------------------
# E2E-3: Full relationship flow
# ---------------------------------------------------------------------------

class TestRelationshipE2E(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "ri.json"

    def test_full_relationship_flow(self):
        # Step 1: Ingest
        result = ri.process_relationship_thread(
            RELATIONSHIP_TEXT,
            entity_name="Maria Santos",
            entity_org="Olo",
            entity_role="CTO",
            source_type="phone",
            store_path=self.store,
        )
        interactions = result.get("interactions") or []
        self.assertGreater(len(interactions), 0)
        self.assertEqual(result["persistence_status"], "pending confirmation")

        interaction_id = interactions[0]["id"]

        # Step 2: Surface
        surface = surf.format_surface(result)
        self.assertIn("RELATIONSHIP INTELLIGENCE", surface)
        self.assertIn("Maria Santos", surface)

        # Step 3: Confirm
        # RB-2026-08-28: confirming now also attempts to create a baseline
        # entry for a not-yet-known contact_id -- mock so this end-to-end
        # test doesn't write a fake "Maria Santos" into whatever real
        # baseline_index.json this process happens to see.
        with patch.object(mutations, "cmd_contact_add", return_value=0):
            confirmed = ri.record_interaction(interaction_id, confirmed=True, store_path=self.store)
        self.assertEqual(confirmed["claim_status"], "confirmed")

        # Step 4: Query
        confirmed_records = ri.query_interactions(
            claim_status="confirmed", store_path=self.store
        )
        ids = [r["id"] for r in confirmed_records]
        self.assertIn(interaction_id, ids)

    def test_thought_leader_alignment_override(self):
        result = ri.process_relationship_thread(
            RELATIONSHIP_TEXT,
            entity_name="Maria Santos",
            entity_org="Olo",
            entity_role="CTO",
            signal_type_override="thought_leader_alignment",
            store_path=self.store,
        )
        for interaction in result.get("interactions", []):
            self.assertEqual(interaction["signal_type"], "thought_leader_alignment")


# ---------------------------------------------------------------------------
# E2E-4: Full insight flow
# ---------------------------------------------------------------------------

class TestInsightE2E(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "ii.json"

    def test_full_insight_flow(self):
        # Step 1: Ingest
        result = ii.process_text(
            INSIGHT_TEXT,
            source_type="conversation",
            store_path=self.store,
        )
        insights = result.get("insights") or []
        self.assertGreater(len(insights), 0)
        self.assertEqual(result["persistence_status"], "pending confirmation")

        insight_id = insights[0]["id"]

        # Step 2: Surface
        surface = surf.format_surface(result)
        self.assertIn("STRATEGIC INSIGHT", surface)
        self.assertIn("TRUST STATS", surface)

        # Step 3: Confirm
        confirmed = ii.record_insight(insight_id, confirmed=True, store_path=self.store)
        self.assertEqual(confirmed["claim_status"], "confirmed")
        self.assertEqual(confirmed["persistence_status"], "RB recorded")

        # Step 4: Query
        confirmed_records = ii.query_insights(claim_status="confirmed", store_path=self.store)
        ids = [r["id"] for r in confirmed_records]
        self.assertIn(insight_id, ids)

    def test_defect_009_regression(self):
        # DEFECT-009: A conversational insight MUST produce mutation proposals.
        # This regression test must never be removed or relaxed.
        result = ii.process_text(INSIGHT_TEXT, store_path=self.store)
        if result.get("insights"):
            self.assertGreater(
                len(result.get("mutation_proposals", [])), 0,
                "DEFECT-009 regression: insights detected but no mutation proposals emitted"
            )


# ---------------------------------------------------------------------------
# E2E-5: Daily brief with confirmed behavioral intelligence
# ---------------------------------------------------------------------------

class TestDailyBriefBehavioralIntelligence(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bstore = Path(self.tmp) / "bi.json"
        self.estore = Path(self.tmp) / "ei.json"

    def _write_confirmed_signal(self):
        self.bstore.write_text(json.dumps({
            "records": [
                {
                    "id": "bs-e2e-001",
                    "record_type": "behavioral_signal",
                    "signal_type": "consumer_hesitation",
                    "confidence": "high",
                    "matched_keywords": ["parking lot", "drove away"],
                    "evidence_sentences": ["People drove away from Five Guys."],
                    "claim_status": "confirmed",
                    "confirmed_at": "2026-06-01T10:00:00+00:00",
                    "daily_brief_layers": ["consumer_sentiment", "fast_casual_pressure"],
                    "persistence_status": "RB recorded",
                }
            ]
        }))
        self.estore.write_text(json.dumps({"records": []}))

    def test_empty_store_produces_unavailable_layer(self):
        result = cos_judgment.build_behavioral_intelligence_layer(
            behavioral_path=self.bstore, entity_path=self.estore
        )
        self.assertFalse(result["available"])
        self.assertIn("note", result)
        self.assertIsNotNone(result["note"])

    def test_confirmed_signal_surfaces_in_layer(self):
        self._write_confirmed_signal()
        result = cos_judgment.build_behavioral_intelligence_layer(
            behavioral_path=self.bstore, entity_path=self.estore
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["label"], "active")
        self.assertEqual(result["signal_count"], 1)
        self.assertIn("consumer_sentiment", result["brief_layer_summary"])
        self.assertIn("fast_casual_pressure", result["brief_layer_summary"])

    def test_behavioral_intelligence_renders_in_today_md(self):
        import daily_brief
        self._write_confirmed_signal()
        # Patch cos_judgment to use our test stores
        original = cos_judgment.build_behavioral_intelligence_layer
        cos_judgment.build_behavioral_intelligence_layer = lambda: \
            original(behavioral_path=self.bstore, entity_path=self.estore)
        try:
            report = {"behavioral_intelligence": cos_judgment.build_behavioral_intelligence_layer()}
            output = daily_brief._render_behavioral_intelligence_md(report)
            rendered = "\n".join(output)
            self.assertIn("Behavioral Intelligence", rendered)
            self.assertIn("consumer_hesitation", rendered)
        finally:
            cos_judgment.build_behavioral_intelligence_layer = original

    def test_behavioral_intelligence_renders_unavailable_cleanly(self):
        import daily_brief
        report = {"behavioral_intelligence": {
            "available": False,
            "label": "source_unavailable",
            "note": "No behavioral intelligence stores found.",
        }}
        output = daily_brief._render_behavioral_intelligence_md(report)
        rendered = "\n".join(output)
        self.assertIn("Behavioral Intelligence", rendered)
        self.assertIn("source_unavailable", rendered)
        self.assertNotIn("None", rendered)


# ---------------------------------------------------------------------------
# E2E-6: Pre-flight check validates full stack
# ---------------------------------------------------------------------------

class TestPreFlightCheck(unittest.TestCase):

    def test_pre_flight_module_imports(self):
        # Simulate the import checks from pre_flight_check.py
        import importlib
        modules = [
            "experiential_intelligence",
            "macro_intelligence",
            "relationship_intake",
            "insight_intake",
            "ingest_surface",
            "cos_judgment",
            "daily_brief",
            "rb_core",
        ]
        for mod in modules:
            try:
                importlib.import_module(mod)
            except ImportError as e:
                self.fail(f"Module {mod} failed to import: {e}")

    def test_pre_flight_server_routes_present(self):
        server_path = Path(__file__).resolve().parent.parent / "api" / "server.py"
        server_text = server_path.read_text()
        required = [
            "/ingest/experience",
            "/ingest/experience/confirm",
            "/ingest/experience/retrieve",
            "/ingest/macro",
            "/ingest/macro/confirm",
            "/ingest/relationship",
            "/ingest/relationship/confirm",
            "/ingest/insight",
            "/ingest/insight/confirm",
        ]
        for route in required:
            self.assertIn(route, server_text, f"Route missing from server.py: {route}")

    def test_pre_flight_openapi_paths_present(self):
        spec_path = Path(__file__).resolve().parent.parent / "api" / "openapi.yaml"
        spec_text = spec_path.read_text()
        required = [
            "/ingest/experience",
            "/ingest/macro",
            "/ingest/relationship",
            "/ingest/insight",
        ]
        for path in required:
            self.assertIn(path, spec_text, f"Path missing from openapi.yaml: {path}")

    def test_pre_flight_trigger_rules_present(self):
        rules_path = Path(__file__).resolve().parent.parent / "INGEST_TRIGGER_RULES.md"
        self.assertTrue(rules_path.exists(), "INGEST_TRIGGER_RULES.md not found")
        text = rules_path.read_text()
        for section in ("POST /ingest/experience", "POST /ingest/macro",
                        "POST /ingest/relationship", "POST /ingest/insight"):
            self.assertIn(section, text)

    def test_pre_flight_daily_brief_wired(self):
        db_path = Path(__file__).resolve().parent.parent / "scripts" / "daily_brief.py"
        db_text = db_path.read_text()
        self.assertIn('"behavioral_intelligence": cos_blocks["behavioral_intelligence"]', db_text)
        self.assertIn("def _render_behavioral_intelligence_md", db_text)
        self.assertIn("_render_behavioral_intelligence_md(report)", db_text)


# ---------------------------------------------------------------------------
# E2E-7: Cross-module trust contract
# ---------------------------------------------------------------------------

class TestCrossModuleTrustContract(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_all_ingest_paths_return_persistence_status(self):
        tmp = self.tmp
        paths = [
            lambda: ei.process_experiential_signal(EXPERIENCE_TEXT, store_path=Path(tmp) / "ei.json"),
            lambda: mi.process_macro_signal(MACRO_TEXT),
            lambda: ri.process_relationship_thread(RELATIONSHIP_TEXT, entity_name="Maria Santos", store_path=Path(tmp) / "ri.json"),
            lambda: ii.process_text(INSIGHT_TEXT, store_path=Path(tmp) / "ii.json"),
        ]
        for fn in paths:
            result = fn()
            self.assertIn("persistence_status", result,
                          f"persistence_status missing from {fn}")
            self.assertIsNotNone(result["persistence_status"])

    def test_experience_returns_trust_stats(self):
        tmp = self.tmp
        result = ei.process_experiential_signal(
            EXPERIENCE_TEXT, store_path=Path(tmp) / "ei_ts.json"
        )
        self.assertIn("trust_stats", result, "trust_stats missing from experience")
        ts = result["trust_stats"]
        self.assertIn("trust_contract_met", ts)
        self.assertTrue(ts["trust_contract_met"])

    def test_no_auto_confirms_across_all_paths(self):
        tmp = self.tmp
        for fn, item_key in [
            (lambda: ei.process_experiential_signal(EXPERIENCE_TEXT, store_path=Path(tmp) / "ei_nc.json"), "experiences"),
            (lambda: ri.process_relationship_thread(RELATIONSHIP_TEXT, entity_name="Maria Santos", store_path=Path(tmp) / "ri_nc.json"), "interactions"),
            (lambda: ii.process_text(INSIGHT_TEXT, store_path=Path(tmp) / "ii_nc.json"), "insights"),
        ]:
            result = fn()
            items = result.get(item_key) or []
            for item in items:
                self.assertIn(item.get("claim_status"), ("proposed",),
                              f"Item was auto-confirmed in {item_key}: {item}")

    def test_format_surface_always_returns_string_for_all_ingest_types(self):
        tmp = self.tmp
        results = [
            ei.process_experiential_signal(EXPERIENCE_TEXT, store_path=Path(tmp) / "ei_sf.json"),
            mi.process_macro_signal(MACRO_TEXT),
            ri.process_relationship_thread(RELATIONSHIP_TEXT, entity_name="Maria Santos", store_path=Path(tmp) / "ri_sf.json"),
            ii.process_text(INSIGHT_TEXT, store_path=Path(tmp) / "ii_sf.json"),
        ]
        for result in results:
            output = surf.format_surface(result)
            self.assertIsInstance(output, str)
            self.assertGreater(len(output), 0)
            self.assertTrue(output.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
