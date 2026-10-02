"""
test_api_ingest_endpoints.py — API ingest endpoint wiring tests (RB 9.24).

Verifies that all four intelligence module ingest paths are correctly wired
into the API server. Tests exercise the ingest functions directly (not via
HTTP) to confirm the wiring contract, not the HTTP layer.

Covers:
  A1: Experience ingest — process_experiential_signal via API contract
  A2: Experience confirm — record_experience via API contract
  A3: Experience retrieve — query_retrieval_hooks via API contract
  A4: Experience externalize — externalize via API contract
  A5: Macro ingest — process_macro_signal via API contract
  A6: Macro confirm — record_behavioral_record via API contract
  A7: Relationship ingest — process_relationship_thread via API contract
  A8: Relationship confirm — record_interaction via API contract
  A9: Insight ingest — process_text via API contract (DEFECT-009 closure)
  A10: Insight confirm — record_insight via API contract
  A11: Cross-layer trust contract — all ingestion events emit persistence_status
  A12: All confirm paths reject unknown IDs without crashing
"""
from __future__ import annotations

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
import mutations

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

EXPERIENCE_TEXT = (
    "I worked at NomadGo, a restaurant AI company. We deployed inventory automation "
    "at Starbucks and it failed. The root cause was workflow duplication — "
    "operators had to do everything twice. Trust was never established. "
    "The lesson is that operational trust must be earned before scale."
)

MACRO_TEXT = (
    "People drove into Five Guys parking lots, sat for a minute, and drove away. "
    "They didn't get out of the car. The math just doesn't add up anymore — "
    "a meal for two is thirty-five dollars when McDonald's costs a fraction. "
    "Consumer affordability is cracking. Operators cannot pass costs anymore."
)

RELATIONSHIP_TEXT = (
    "Had a productive meeting with Sarah Chen, CTO at Olo. She is interested "
    "in collaborating on the restaurant tech thesis. She reached out proactively."
)

INSIGHT_TEXT = (
    "Restaurant operators face structural retention challenges. Customer frequency "
    "economics are shifting as inflation reduces discretionary spend. "
    "The thesis here is that loyalty-as-infrastructure is the positioning opportunity."
)


# ---------------------------------------------------------------------------
# A1: Experience ingest
# ---------------------------------------------------------------------------

class TestExperienceIngest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "ei.json"

    def _call(self, text=EXPERIENCE_TEXT, source_type="conversation",
              employer_names=None, context=None):
        return ei.process_experiential_signal(
            text=text,
            source_type=source_type,
            employer_names=employer_names,
            context=context,
            store_path=self.store,
        )

    def test_returns_experiences(self):
        result = self._call()
        self.assertIn("experiences", result)
        self.assertGreater(len(result["experiences"]), 0)

    def test_returns_trust_stats(self):
        result = self._call()
        self.assertIn("trust_stats", result)
        self.assertTrue(result["trust_stats"]["trust_contract_met"])

    def test_returns_mutation_proposals(self):
        result = self._call()
        self.assertIn("mutation_proposals", result)
        self.assertIsInstance(result["mutation_proposals"], list)

    def test_returns_retrieval_hooks(self):
        result = self._call()
        self.assertIn("retrieval_hooks", result)
        self.assertIsInstance(result["retrieval_hooks"], dict)

    def test_returns_recommended_actions(self):
        result = self._call()
        self.assertIn("recommended_actions", result)

    def test_persistence_status_pending(self):
        result = self._call()
        self.assertEqual(result["persistence_status"], "pending confirmation")

    def test_employer_sensitive_detected(self):
        result = self._call()
        self.assertTrue(result["employer_sensitive"])

    def test_employer_names_explicit(self):
        result = self._call(
            text="When I was at AcmeTech we deployed a system that failed.",
            employer_names=["AcmeTech"],
        )
        self.assertTrue(result["employer_sensitive"])

    def test_all_mutations_require_confirmation(self):
        result = self._call()
        for p in result["mutation_proposals"]:
            self.assertTrue(p.get("requires_confirmation"))

    def test_empty_text_returns_no_persist(self):
        result = self._call(text="")
        self.assertEqual(result["persistence_status"], "RB did not persist")
        self.assertIn("trust_stats", result)

    def test_source_type_invalid_defaults_to_conversation(self):
        result = self._call(source_type="garbage")
        for exp in result["experiences"]:
            self.assertEqual(exp["source_type"], "conversation")

    def test_case_study_has_internal_and_external_versions(self):
        result = self._call()
        for exp in result["experiences"]:
            cs = exp.get("case_study", {})
            self.assertIn("internal_version", cs)
            self.assertIn("external_version", cs)


# ---------------------------------------------------------------------------
# A2: Experience confirm
# ---------------------------------------------------------------------------

class TestExperienceConfirm(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "ei.json"
        result = ei.process_experiential_signal(EXPERIENCE_TEXT, store_path=self.store)
        self.exp_id = result["experiences"][0]["id"]

    def test_confirm_sets_confirmed(self):
        updated = ei.record_experience(self.exp_id, confirmed=True, store_path=self.store)
        self.assertEqual(updated["claim_status"], "confirmed")
        self.assertEqual(updated["persistence_status"], "RB recorded")

    def test_reject_sets_rejected(self):
        updated = ei.record_experience(self.exp_id, confirmed=False, store_path=self.store)
        self.assertEqual(updated["claim_status"], "rejected")
        self.assertEqual(updated["persistence_status"], "RB skipped")

    def test_unknown_id_returns_error(self):
        result = ei.record_experience("bad-id", store_path=self.store)
        self.assertIn("error", result)


# ---------------------------------------------------------------------------
# A3: Experience retrieve
# ---------------------------------------------------------------------------

class TestExperienceRetrieve(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "ei.json"
        result = ei.process_experiential_signal(EXPERIENCE_TEXT, store_path=self.store)
        for exp in result["experiences"]:
            ei.record_experience(exp["id"], confirmed=True, store_path=self.store)

    def test_retrieve_restaurant_tech(self):
        result = ei.query_retrieval_hooks("restaurant_tech", store_path=self.store)
        self.assertIn("domain", result)
        self.assertEqual(result["domain"], "restaurant_tech")
        self.assertIn("lesson_types", result)
        self.assertGreater(result["match_count"], 0)

    def test_retrieve_unknown_domain_no_crash(self):
        result = ei.query_retrieval_hooks("quantum_physics", store_path=self.store)
        self.assertEqual(result["match_count"], 0)
        self.assertEqual(result["retrieval_confidence"], "none")

    def test_retrieve_returns_retrieval_confidence(self):
        result = ei.query_retrieval_hooks("restaurant_tech", store_path=self.store)
        self.assertIn(result["retrieval_confidence"], ("high", "medium", "low", "none"))


# ---------------------------------------------------------------------------
# A4: Experience externalize
# ---------------------------------------------------------------------------

class TestExperienceExternalize(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "ei.json"
        result = ei.process_experiential_signal(EXPERIENCE_TEXT, store_path=self.store)
        self.exp_id = result["experiences"][0]["id"]

    def test_externalize_returns_external_version(self):
        ext = ei.externalize(self.exp_id, store_path=self.store)
        self.assertIn("external_version", ext)

    def test_externalize_removes_nomadgo(self):
        ext = ei.externalize(self.exp_id, store_path=self.store)
        snippet = str(ext.get("external_version", {})).lower()
        self.assertNotIn("nomadgo", snippet)

    def test_externalize_unknown_id_returns_error(self):
        result = ei.externalize("bad-id", store_path=self.store)
        self.assertIn("error", result)

    def test_externalize_flag_set(self):
        ext = ei.externalize(self.exp_id, store_path=self.store)
        self.assertTrue(ext.get("externalization_applied"))


# ---------------------------------------------------------------------------
# A5: Macro ingest
# ---------------------------------------------------------------------------

class TestMacroIngest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ri_store = Path(self.tmp) / "ri.json"

    def _call(self, text=MACRO_TEXT, source_type="linkedin_post",
              author_name=None, author_org=None, author_role=None):
        return mi.process_macro_signal(
            text=text,
            source_type=source_type,
            author_name=author_name,
            author_org=author_org,
            author_role=author_role,
            ri_store_path=self.ri_store,
        )

    def test_returns_behavioral_signals(self):
        result = self._call()
        self.assertIn("behavioral_signals", result)

    def test_returns_persistence_status(self):
        result = self._call()
        self.assertIn("persistence_status", result)
        self.assertIn(result["persistence_status"], mi.PERSISTENCE_STATUSES)

    def test_consumer_hesitation_detected(self):
        result = self._call()
        types = {s["signal_type"] for s in result.get("behavioral_signals", [])}
        self.assertIn("consumer_hesitation", types)

    def test_returns_mutation_proposals(self):
        result = self._call()
        self.assertIn("mutation_proposals", result)

    def test_all_mutations_require_confirmation(self):
        result = self._call()
        for p in result.get("mutation_proposals", []):
            self.assertTrue(p.get("requires_confirmation"))

    def test_author_ri_mutation_when_author_present(self):
        result = self._call(
            author_name="David Mann",
            author_org="Restaurant Consulting Group",
            author_role="Principal",
        )
        self.assertIsNotNone(result.get("ri_mutation"))

    def test_empty_text_returns_no_persist(self):
        result = self._call(text="")
        self.assertEqual(result["persistence_status"], "RB did not persist")

    def test_cos_surface_present(self):
        result = self._call()
        self.assertIn("cos_surface", result)

    def test_daily_brief_layers_present(self):
        result = self._call()
        self.assertIn("daily_brief_layers", result)


# ---------------------------------------------------------------------------
# A6: Macro confirm
# ---------------------------------------------------------------------------

class TestMacroConfirm(unittest.TestCase):

    def setUp(self):
        import json as _json
        self.tmp = tempfile.mkdtemp()
        self.bstore = Path(self.tmp) / "bi.json"
        mi.process_macro_signal(
            MACRO_TEXT, behavioral_store_path=self.bstore,
            entity_store_path=Path(self.tmp) / "ei.json",
        )
        # IDs live in the store, not in the summary output
        recs = _json.loads(self.bstore.read_text()).get("records", []) if self.bstore.exists() else []
        self.record_id = recs[0]["id"] if recs else None

    def test_confirm_sets_confirmed(self):
        if not self.record_id:
            self.skipTest("No behavioral records generated")
        updated = mi.record_behavioral_record(
            self.record_id, confirmed=True, behavioral_store_path=self.bstore
        )
        self.assertEqual(updated["claim_status"], "confirmed")

    def test_reject_sets_rejected(self):
        if not self.record_id:
            self.skipTest("No behavioral records generated")
        updated = mi.record_behavioral_record(
            self.record_id, confirmed=False, behavioral_store_path=self.bstore
        )
        self.assertEqual(updated["claim_status"], "rejected")

    def test_unknown_id_returns_error(self):
        result = mi.record_behavioral_record("bad-id", behavioral_store_path=self.bstore)
        self.assertIn("error", result)


# ---------------------------------------------------------------------------
# A7: Relationship ingest
# ---------------------------------------------------------------------------

class TestRelationshipIngest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "ri.json"

    def _call(self, text=RELATIONSHIP_TEXT, entity_name="Sarah Chen",
              entity_org="Olo", entity_role="CTO", source_type="email",
              signal_type_override=None):
        return ri.process_relationship_thread(
            text=text,
            entity_name=entity_name,
            entity_org=entity_org,
            entity_role=entity_role,
            source_type=source_type,
            signal_type_override=signal_type_override,
            store_path=self.store,
        )

    def test_returns_interactions(self):
        result = self._call()
        self.assertIn("interactions", result)

    def test_returns_persistence_status(self):
        result = self._call()
        self.assertIn("persistence_status", result)
        self.assertIn(result["persistence_status"], ri.PERSISTENCE_STATUSES)

    def test_returns_mutation_proposals(self):
        result = self._call()
        self.assertIn("mutation_proposals", result)

    def test_all_mutations_require_confirmation(self):
        result = self._call()
        for p in result.get("mutation_proposals", []):
            self.assertTrue(p.get("requires_confirmation"))

    def test_returns_cos_surface(self):
        result = self._call()
        self.assertIn("cos_surface", result)

    def test_signal_type_override_accepted(self):
        result = self._call(signal_type_override="thought_leader_alignment")
        for interaction in result.get("interactions", []):
            self.assertEqual(interaction.get("signal_type"), "thought_leader_alignment")

    def test_empty_text_returns_no_persist(self):
        result = self._call(text="")
        self.assertEqual(result["persistence_status"], "RB did not persist")

    def test_interactions_pending_before_confirm(self):
        result = self._call()
        for interaction in result.get("interactions", []):
            self.assertEqual(interaction.get("claim_status"), "proposed")


# ---------------------------------------------------------------------------
# A8: Relationship confirm
# ---------------------------------------------------------------------------

class TestRelationshipConfirm(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "ri.json"
        result = ri.process_relationship_thread(
            RELATIONSHIP_TEXT,
            entity_name="Sarah Chen",
            entity_org="Olo",
            entity_role="CTO",
            store_path=self.store,
        )
        interactions = result.get("interactions", [])
        self.interaction_id = interactions[0]["id"] if interactions else None

    def test_confirm_sets_confirmed(self):
        if not self.interaction_id:
            self.skipTest("No interaction records generated")
        # RB-2026-08-28: confirming now also attempts to create a baseline
        # entry for a not-yet-known contact_id -- mock so this test of the
        # ledger-status half doesn't write a fake "Sarah Chen" into
        # whatever real baseline_index.json this process happens to see.
        with patch.object(mutations, "cmd_contact_add", return_value=0):
            updated = ri.record_interaction(self.interaction_id, confirmed=True, store_path=self.store)
        self.assertEqual(updated["claim_status"], "confirmed")

    def test_reject_sets_rejected(self):
        if not self.interaction_id:
            self.skipTest("No interaction records generated")
        updated = ri.record_interaction(self.interaction_id, confirmed=False, store_path=self.store)
        self.assertEqual(updated["claim_status"], "rejected")

    def test_unknown_id_returns_error(self):
        result = ri.record_interaction("bad-id", store_path=self.store)
        self.assertIn("error", result)


# ---------------------------------------------------------------------------
# A9: Insight ingest (DEFECT-009 closure)
# ---------------------------------------------------------------------------

class TestInsightIngest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "ii.json"

    def _call(self, text=INSIGHT_TEXT, source_type="conversation"):
        return ii.process_text(text=text, source_type=source_type, store_path=self.store)

    def test_returns_insights(self):
        result = self._call()
        self.assertIn("insights", result)

    def test_returns_persistence_status(self):
        result = self._call()
        self.assertIn("persistence_status", result)
        self.assertIn(result["persistence_status"], ii.PERSISTENCE_STATUSES)

    def test_returns_mutation_proposals(self):
        result = self._call()
        self.assertIn("mutation_proposals", result)

    def test_all_mutations_require_confirmation(self):
        result = self._call()
        for p in result.get("mutation_proposals", []):
            self.assertTrue(p.get("requires_confirmation"))

    def test_returns_retrieval_tags(self):
        result = self._call()
        self.assertIn("retrieval_tags", result)

    def test_industry_trend_detected(self):
        result = self._call()
        types = {i["insight_type"] for i in result.get("insights", [])}
        self.assertTrue(types & {"industry_trend", "market_signal", "thought_leadership_theme"})

    def test_empty_text_returns_no_persist(self):
        result = self._call(text="")
        self.assertEqual(result["persistence_status"], "RB did not persist")

    def test_insights_pending_before_confirm(self):
        result = self._call()
        for insight in result.get("insights", []):
            self.assertEqual(insight.get("claim_status"), "proposed")

    def test_defect_009_closure_conversational_signal_gets_mutation_path(self):
        # This is the DEFECT-009 regression: a conversational signal MUST
        # produce at least one mutation proposal if insights are detected
        result = self._call(INSIGHT_TEXT)
        if result.get("insights"):
            self.assertGreater(len(result.get("mutation_proposals", [])), 0,
                               "DEFECT-009: insight detected but no mutation proposals emitted")


# ---------------------------------------------------------------------------
# A10: Insight confirm
# ---------------------------------------------------------------------------

class TestInsightConfirm(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "ii.json"
        result = ii.process_text(INSIGHT_TEXT, store_path=self.store)
        insights = result.get("insights", [])
        self.insight_id = insights[0]["id"] if insights else None

    def test_confirm_sets_confirmed(self):
        if not self.insight_id:
            self.skipTest("No insight records generated")
        updated = ii.record_insight(self.insight_id, confirmed=True, store_path=self.store)
        self.assertEqual(updated["claim_status"], "confirmed")
        self.assertEqual(updated["persistence_status"], "RB recorded")

    def test_reject_sets_rejected(self):
        if not self.insight_id:
            self.skipTest("No insight records generated")
        updated = ii.record_insight(self.insight_id, confirmed=False, store_path=self.store)
        self.assertEqual(updated["claim_status"], "rejected")
        self.assertEqual(updated["persistence_status"], "RB skipped")

    def test_unknown_id_returns_error(self):
        result = ii.record_insight("bad-id", store_path=self.store)
        self.assertIn("error", result)


# ---------------------------------------------------------------------------
# A11: Cross-layer trust contract
# ---------------------------------------------------------------------------

class TestCrossLayerTrustContract(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_experience_always_has_persistence_status(self):
        for text in (EXPERIENCE_TEXT, ""):
            r = ei.process_experiential_signal(text, store_path=Path(self.tmp) / "ei.json")
            self.assertIsNotNone(r.get("persistence_status"))
            self.assertIn("trust_stats", r)

    def test_macro_always_has_persistence_status(self):
        for text in (MACRO_TEXT, ""):
            r = mi.process_macro_signal(text)
            self.assertIsNotNone(r.get("persistence_status"))

    def test_relationship_always_has_persistence_status(self):
        for text in (RELATIONSHIP_TEXT, ""):
            r = ri.process_relationship_thread(text, store_path=Path(self.tmp) / "ri.json")
            self.assertIsNotNone(r.get("persistence_status"))

    def test_insight_always_has_persistence_status(self):
        for text in (INSIGHT_TEXT, ""):
            r = ii.process_text(text, store_path=Path(self.tmp) / "ii.json")
            self.assertIsNotNone(r.get("persistence_status"))

    def test_no_auto_confirm_after_any_ingest(self):
        ei_result = ei.process_experiential_signal(EXPERIENCE_TEXT, store_path=Path(self.tmp) / "ei.json")
        for exp in ei_result.get("experiences", []):
            self.assertIn(exp["claim_status"], ("proposed",))

        ri_result = ri.process_relationship_thread(RELATIONSHIP_TEXT, entity_name="Sarah Chen", store_path=Path(self.tmp) / "ri.json")
        for interaction in ri_result.get("interactions", []):
            self.assertIn(interaction["claim_status"], ("proposed",))

        ii_result = ii.process_text(INSIGHT_TEXT, store_path=Path(self.tmp) / "ii.json")
        for insight in ii_result.get("insights", []):
            self.assertIn(insight["claim_status"], ("proposed",))


# ---------------------------------------------------------------------------
# A12: All confirm paths reject unknown IDs without crashing
# ---------------------------------------------------------------------------

class TestConfirmPathsSafeOnBadId(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_experience_bad_id(self):
        r = ei.record_experience("nonexistent", store_path=Path(self.tmp) / "ei.json")
        self.assertIn("error", r)

    def test_macro_bad_id(self):
        r = mi.record_behavioral_record("nonexistent", behavioral_store_path=Path(self.tmp) / "bi.json")
        self.assertIn("error", r)

    def test_macro_entity_bad_id(self):
        r = mi.record_entity_risk("nonexistent", entity_store_path=Path(self.tmp) / "ent.json")
        self.assertIn("error", r)

    def test_relationship_bad_id(self):
        r = ri.record_interaction("nonexistent", store_path=Path(self.tmp) / "ri.json")
        self.assertIn("error", r)

    def test_insight_bad_id(self):
        r = ii.record_insight("nonexistent", store_path=Path(self.tmp) / "ii.json")
        self.assertIn("error", r)


if __name__ == "__main__":
    unittest.main()
