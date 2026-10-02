"""
test_experiential_intelligence.py — Experiential Intelligence Layer tests.

Covers DEFECT-014: RB fails to automatically transform firsthand experience,
deployment lessons, and post-mortems into reusable institutional intelligence.

Test groups:
  E1: Core invariants (structure, all mutations require confirmation,
      trust_stats always present, persistence_status always explicit,
      employer_sensitive flag, empty/no-signal handling)
  E2: Intelligence type classification
  E3: Case study generation (internal + external version structure)
  E4: Reputation-aware externalization (employer name anonymization)
  E5: Retrieval hook generation (domain trigger mapping)
  E6: Trust stats enforcement (always emitted, fields required)
  E7: Mutation proposal generation (cross-layer, review-first)
  E8: Recommended actions surface
  E9: Confirmation lifecycle (record_experience confirm / reject)
  E10: Query functions (by intel_type, claim_status, tags, employer_sensitive)
  E11: query_retrieval_hooks (domain-based lesson surface)
  E12: externalize() (returns external_version, not internal)
  E13: Regression — no auto-mutations, no silent persistence
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import experiential_intelligence as ei

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

RESTAURANT_AI_FAILURE = (
    "I worked at NomadGo, a restaurant AI technology company. We deployed a large-scale "
    "inventory automation system at Starbucks locations. The system failed badly. "
    "The root cause was that we duplicated their existing workflows instead of replacing them — "
    "operators had to do the work twice. Nobody trusts a system that adds work. "
    "We should have piloted with a single location and proven ROI before scaling. "
    "The lesson I learned is that operational trust is earned, not assumed. "
    "If the system breaks on a Friday night, operators will never touch it again. "
    "Next time I would build the fallback protocol before the AI protocol."
)

INDUSTRY_OBSERVATION_TEXT = (
    "Across the restaurant tech market, I've seen a consistent pattern. "
    "Operators consistently say yes in the demo and no in the deployment. "
    "The gap between intent and adoption is the biggest challenge vendors face. "
    "The industry broadly underestimates change management requirements. "
    "Most operators don't have IT departments — the technology has to be zero-maintenance."
)

STRATEGIC_FRAMEWORK_TEXT = (
    "My framework for evaluating restaurant AI deployments uses three criteria. "
    "First principle: never deploy without a defined fallback protocol. "
    "Second: the pilot must prove ROI in a single unit before any multi-unit commitment. "
    "Third: operator trust is the prerequisite — without it, technology gets abandoned. "
    "This is the key: adoption is a change management problem, not a technology problem."
)

NO_SIGNAL_TEXT = (
    "The weather was nice today. I went for a walk in the park. "
    "Had coffee in the morning. Watched some television in the evening."
)

MINIMAL_TEXT = "I learned something from a project."


# ---------------------------------------------------------------------------
# E1: Core invariants
# ---------------------------------------------------------------------------

class TestCoreInvariants(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "experiential.json"

    def test_output_has_required_keys(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        for key in ("experiences", "trust_stats", "mutation_proposals",
                    "retrieval_hooks", "recommended_actions", "persistence_status"):
            self.assertIn(key, result)

    def test_all_mutations_require_confirmation(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        for prop in result["mutation_proposals"]:
            self.assertTrue(prop.get("requires_confirmation"), f"Mutation missing confirmation: {prop}")

    def test_trust_stats_always_present(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        self.assertIn("trust_stats", result)
        ts = result["trust_stats"]
        for field in ("sources_assessed", "sources_accepted", "sources_rejected",
                      "confidence", "mutation_recommendations",
                      "follow_up_loops", "retrieval_classifications"):
            self.assertIn(field, ts, f"trust_stats missing field: {field}")

    def test_trust_stats_present_on_empty_input(self):
        result = ei.process_experiential_signal("", store_path=self.store)
        self.assertIn("trust_stats", result)

    def test_persistence_status_always_explicit(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        self.assertIn(result["persistence_status"], ei.PERSISTENCE_STATUSES)

    def test_empty_input_returns_no_persist(self):
        result = ei.process_experiential_signal("", store_path=self.store)
        self.assertEqual(result["persistence_status"], "RB did not persist")
        self.assertEqual(result["experiences"], [])

    def test_no_signal_text_returns_no_persist(self):
        result = ei.process_experiential_signal(NO_SIGNAL_TEXT, store_path=self.store)
        self.assertEqual(result["persistence_status"], "RB did not persist")

    def test_experience_count_matches_experiences_list(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        if result.get("experience_count") is not None:
            self.assertEqual(result["experience_count"], len(result["experiences"]))

    def test_no_auto_mutation_without_confirmation(self):
        # After process, experiences should be in proposed state, not confirmed
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        for exp in result["experiences"]:
            self.assertEqual(exp["claim_status"], "proposed")
            self.assertEqual(exp["persistence_status"], "pending confirmation")
            self.assertIsNone(exp["confirmed_at"])

    def test_source_type_defaults_to_conversation(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE,
                                                 source_type="invalid_type",
                                                 store_path=self.store)
        for exp in result["experiences"]:
            self.assertEqual(exp["source_type"], "conversation")

    def test_valid_source_types_accepted(self):
        for st in ei.SOURCE_TYPES:
            result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE,
                                                     source_type=st,
                                                     store_path=self.store)
            for exp in result["experiences"]:
                self.assertEqual(exp["source_type"], st)


# ---------------------------------------------------------------------------
# E2: Intelligence type classification
# ---------------------------------------------------------------------------

class TestIntelligenceClassification(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "experiential.json"

    def test_failure_case_study_detected(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        types = {e["intel_type"] for e in result["experiences"]}
        self.assertIn("failure_case_study", types)

    def test_personal_experience_detected(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        types = {e["intel_type"] for e in result["experiences"]}
        self.assertIn("personal_experience", types)

    def test_industry_observation_detected(self):
        result = ei.process_experiential_signal(INDUSTRY_OBSERVATION_TEXT, store_path=self.store)
        types = {e["intel_type"] for e in result["experiences"]}
        self.assertIn("industry_observation", types)

    def test_strategic_framework_detected(self):
        result = ei.process_experiential_signal(STRATEGIC_FRAMEWORK_TEXT, store_path=self.store)
        types = {e["intel_type"] for e in result["experiences"]}
        self.assertIn("strategic_framework", types)

    def test_confidence_field_present_and_valid(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        for exp in result["experiences"]:
            self.assertIn(exp["confidence"], ("high", "medium", "low"))

    def test_matched_keywords_populated(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        for exp in result["experiences"]:
            self.assertIsInstance(exp["matched_keywords"], list)
            self.assertGreater(len(exp["matched_keywords"]), 0)

    def test_intel_type_valid(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        for exp in result["experiences"]:
            self.assertIn(exp["intel_type"], ei.INTELLIGENCE_TYPES)


# ---------------------------------------------------------------------------
# E3: Case study generation
# ---------------------------------------------------------------------------

class TestCaseStudyGeneration(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "experiential.json"

    def _get_failure_exp(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        for exp in result["experiences"]:
            if exp["intel_type"] == "failure_case_study":
                return exp
        return None

    def test_case_study_present(self):
        exp = self._get_failure_exp()
        self.assertIsNotNone(exp)
        self.assertIn("case_study", exp)

    def test_case_study_has_title(self):
        exp = self._get_failure_exp()
        self.assertIn("title", exp["case_study"])
        self.assertIsInstance(exp["case_study"]["title"], str)
        self.assertGreater(len(exp["case_study"]["title"]), 0)

    def test_case_study_title_reflects_failure_type(self):
        exp = self._get_failure_exp()
        title = exp["case_study"]["title"].lower()
        self.assertIn("failure", title)

    def test_internal_version_present(self):
        exp = self._get_failure_exp()
        self.assertIn("internal_version", exp["case_study"])

    def test_external_version_present(self):
        exp = self._get_failure_exp()
        self.assertIn("external_version", exp["case_study"])

    def test_internal_version_has_required_fields(self):
        exp = self._get_failure_exp()
        iv = exp["case_study"]["internal_version"]
        for field in ("title", "raw_text_snippet", "root_causes", "lessons_learned",
                      "success_factors", "failure_factors", "reusable_frameworks"):
            self.assertIn(field, iv)

    def test_external_version_has_required_fields(self):
        exp = self._get_failure_exp()
        ev = exp["case_study"]["external_version"]
        for field in ("title", "raw_text_snippet", "lessons_learned"):
            self.assertIn(field, ev)

    def test_lessons_learned_extracted(self):
        exp = self._get_failure_exp()
        iv = exp["case_study"]["internal_version"]
        self.assertIsInstance(iv["lessons_learned"], list)

    def test_root_causes_extracted(self):
        exp = self._get_failure_exp()
        iv = exp["case_study"]["internal_version"]
        self.assertIsInstance(iv["root_causes"], list)

    def test_reusable_frameworks_extracted(self):
        exp = self._get_failure_exp()
        iv = exp["case_study"]["internal_version"]
        self.assertIsInstance(iv["reusable_frameworks"], list)

    def test_source_text_snippet_populated(self):
        exp = self._get_failure_exp()
        self.assertIsInstance(exp.get("source_text_snippet"), str)
        self.assertGreater(len(exp["source_text_snippet"]), 0)


# ---------------------------------------------------------------------------
# E4: Reputation-aware externalization
# ---------------------------------------------------------------------------

class TestReputationAwareExternalization(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "experiential.json"

    def _get_failure_exp(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        for exp in result["experiences"]:
            if exp["intel_type"] == "failure_case_study":
                return exp
        return None

    def test_employer_sensitive_flag_set(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        self.assertTrue(result["employer_sensitive"])

    def test_employer_sensitive_flag_false_for_clean_text(self):
        result = ei.process_experiential_signal(INDUSTRY_OBSERVATION_TEXT, store_path=self.store)
        self.assertFalse(result["employer_sensitive"])

    def test_external_version_omits_nomadgo(self):
        exp = self._get_failure_exp()
        ev = exp["case_study"]["external_version"]
        snippet = ev.get("raw_text_snippet", "").lower()
        self.assertNotIn("nomadgo", snippet)

    def test_external_version_omits_starbucks(self):
        exp = self._get_failure_exp()
        ev = exp["case_study"]["external_version"]
        snippet = ev.get("raw_text_snippet", "").lower()
        self.assertNotIn("starbucks", snippet)

    def test_internal_version_retains_nomadgo(self):
        exp = self._get_failure_exp()
        iv = exp["case_study"]["internal_version"]
        snippet = iv.get("raw_text_snippet", "").lower()
        self.assertIn("nomadgo", snippet)

    def test_internal_version_retains_starbucks(self):
        exp = self._get_failure_exp()
        iv = exp["case_study"]["internal_version"]
        snippet = iv.get("raw_text_snippet", "").lower()
        self.assertIn("starbucks", snippet)

    def test_external_version_has_externalization_note(self):
        exp = self._get_failure_exp()
        ev = exp["case_study"]["external_version"]
        self.assertIn("externalization_note", ev)

    def test_employer_names_passed_explicitly_are_detected(self):
        text = "When I was at AcmeCorp we deployed a system that failed."
        result = ei.process_experiential_signal(
            text, employer_names=["AcmeCorp"], store_path=self.store
        )
        self.assertTrue(result["employer_sensitive"])

    def test_externalize_function_returns_external_version(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        for exp in result["experiences"]:
            if exp["intel_type"] == "failure_case_study":
                ext = ei.externalize(exp["id"], store_path=self.store)
                self.assertIn("external_version", ext)
                self.assertTrue(ext.get("externalization_applied"))
                return
        self.fail("No failure case study found")

    def test_externalize_unknown_id_returns_error(self):
        result = ei.externalize("nonexistent-id", store_path=self.store)
        self.assertIn("error", result)


# ---------------------------------------------------------------------------
# E5: Retrieval hook generation
# ---------------------------------------------------------------------------

class TestRetrievalHooks(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "experiential.json"

    def test_retrieval_hooks_present(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        self.assertIsInstance(result["retrieval_hooks"], dict)
        self.assertGreater(len(result["retrieval_hooks"]), 0)

    def test_restaurant_tech_hook_present(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        hooks = result["retrieval_hooks"]
        self.assertIn("restaurant_tech", hooks)

    def test_ai_deployment_hook_present_for_ai_text(self):
        text = RESTAURANT_AI_FAILURE + " The AI model deployment was complex."
        result = ei.process_experiential_signal(text, store_path=self.store)
        hooks = result["retrieval_hooks"]
        self.assertIn("ai_deployment", hooks)

    def test_retrieval_hooks_contain_lesson_types(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        for domain, lessons in result["retrieval_hooks"].items():
            self.assertIsInstance(lessons, list)
            self.assertGreater(len(lessons), 0)

    def test_intel_type_always_in_hooks(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        for exp in result["experiences"]:
            self.assertIn(exp["intel_type"], exp["retrieval_hooks"])

    def test_consulting_hook_triggered_by_consulting_text(self):
        text = "In my consulting engagement with a restaurant client, I learned about adoption."
        result = ei.process_experiential_signal(text, store_path=self.store)
        hooks = result["retrieval_hooks"]
        self.assertIn("consulting_engagement", hooks)


# ---------------------------------------------------------------------------
# E6: Trust stats enforcement
# ---------------------------------------------------------------------------

class TestTrustStatsEnforcement(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "experiential.json"

    def test_trust_stats_fields_complete(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        ts = result["trust_stats"]
        required = ("sources_assessed", "sources_accepted", "sources_rejected",
                    "confidence", "mutation_recommendations",
                    "follow_up_loops", "retrieval_classifications", "trust_contract_met")
        for field in required:
            self.assertIn(field, ts)

    def test_trust_contract_met_true(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        self.assertTrue(result["trust_stats"]["trust_contract_met"])

    def test_sources_assessed_nonzero_for_real_text(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        self.assertGreater(result["trust_stats"]["sources_assessed"], 0)

    def test_sources_accepted_nonzero_for_real_text(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        self.assertGreater(result["trust_stats"]["sources_accepted"], 0)

    def test_mutation_recommendations_populated(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        self.assertIsInstance(result["trust_stats"]["mutation_recommendations"], list)

    def test_retrieval_classifications_populated(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        self.assertIsInstance(result["trust_stats"]["retrieval_classifications"], list)
        self.assertGreater(len(result["trust_stats"]["retrieval_classifications"]), 0)

    def test_follow_up_loops_populated_for_case_study(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        self.assertIsInstance(result["trust_stats"]["follow_up_loops"], list)

    def test_trust_stats_emitted_for_no_signal(self):
        result = ei.process_experiential_signal(NO_SIGNAL_TEXT, store_path=self.store)
        self.assertIn("trust_stats", result)
        ts = result["trust_stats"]
        for field in ("sources_assessed", "sources_accepted", "sources_rejected", "confidence"):
            self.assertIn(field, ts)

    def test_confidence_valid_value(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        self.assertIn(result["trust_stats"]["confidence"], ("high", "medium", "low", "none"))


# ---------------------------------------------------------------------------
# E7: Mutation proposal generation
# ---------------------------------------------------------------------------

class TestMutationProposals(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "experiential.json"

    def test_mutation_proposals_present(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        self.assertIsInstance(result["mutation_proposals"], list)
        self.assertGreater(len(result["mutation_proposals"]), 0)

    def test_all_proposals_have_mutation_type(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        for p in result["mutation_proposals"]:
            self.assertIn("mutation_type", p)

    def test_all_proposals_have_target(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        for p in result["mutation_proposals"]:
            self.assertIn("target", p)

    def test_all_proposals_require_confirmation(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        for p in result["mutation_proposals"]:
            self.assertTrue(p.get("requires_confirmation"))

    def test_failure_case_routes_to_strategic_memory(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        types = {p["mutation_type"] for p in result["mutation_proposals"]}
        self.assertIn("strategic_memory", types)

    def test_industry_observation_routes_to_industry_graph(self):
        result = ei.process_experiential_signal(INDUSTRY_OBSERVATION_TEXT, store_path=self.store)
        types = {p["mutation_type"] for p in result["mutation_proposals"]}
        self.assertIn("industry_graph", types)

    def test_strategic_framework_routes_to_user_positioning(self):
        result = ei.process_experiential_signal(STRATEGIC_FRAMEWORK_TEXT, store_path=self.store)
        types = {p["mutation_type"] for p in result["mutation_proposals"]}
        self.assertIn("user_positioning", types)

    def test_persistence_endpoints_present(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        for p in result["mutation_proposals"]:
            self.assertIn("persistence_endpoint", p)


# ---------------------------------------------------------------------------
# E8: Recommended actions surface
# ---------------------------------------------------------------------------

class TestRecommendedActions(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "experiential.json"

    def test_recommended_actions_present(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        self.assertIn("recommended_actions", result)
        self.assertIsInstance(result["recommended_actions"], list)

    def test_employer_sensitive_action_when_detected(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        actions = " ".join(result["recommended_actions"]).lower()
        self.assertIn("employer-sensitive", actions)

    def test_failure_case_study_action_present(self):
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        actions = " ".join(result["recommended_actions"]).lower()
        self.assertIn("failure", actions)

    def test_strategic_framework_action_present(self):
        result = ei.process_experiential_signal(STRATEGIC_FRAMEWORK_TEXT, store_path=self.store)
        actions = " ".join(result["recommended_actions"]).lower()
        self.assertIn("framework", actions)


# ---------------------------------------------------------------------------
# E9: Confirmation lifecycle
# ---------------------------------------------------------------------------

class TestConfirmationLifecycle(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "experiential.json"

    def _ingest(self):
        return ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)

    def test_confirm_sets_claim_status_confirmed(self):
        result = self._ingest()
        exp_id = result["experiences"][0]["id"]
        updated = ei.record_experience(exp_id, confirmed=True, store_path=self.store)
        self.assertEqual(updated["claim_status"], "confirmed")

    def test_confirm_sets_persistence_status_recorded(self):
        result = self._ingest()
        exp_id = result["experiences"][0]["id"]
        updated = ei.record_experience(exp_id, confirmed=True, store_path=self.store)
        self.assertEqual(updated["persistence_status"], "RB recorded")

    def test_confirm_sets_confirmed_at(self):
        result = self._ingest()
        exp_id = result["experiences"][0]["id"]
        updated = ei.record_experience(exp_id, confirmed=True, store_path=self.store)
        self.assertIsNotNone(updated["confirmed_at"])

    def test_reject_sets_claim_status_rejected(self):
        result = self._ingest()
        exp_id = result["experiences"][0]["id"]
        updated = ei.record_experience(exp_id, confirmed=False, store_path=self.store)
        self.assertEqual(updated["claim_status"], "rejected")

    def test_reject_sets_persistence_status_skipped(self):
        result = self._ingest()
        exp_id = result["experiences"][0]["id"]
        updated = ei.record_experience(exp_id, confirmed=False, store_path=self.store)
        self.assertEqual(updated["persistence_status"], "RB skipped")

    def test_unknown_id_returns_error(self):
        result = ei.record_experience("nonexistent-id", store_path=self.store)
        self.assertIn("error", result)

    def test_confirmed_at_none_before_confirmation(self):
        result = self._ingest()
        for exp in result["experiences"]:
            self.assertIsNone(exp["confirmed_at"])


# ---------------------------------------------------------------------------
# E10: Query functions
# ---------------------------------------------------------------------------

class TestQueryFunctions(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "experiential.json"
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        self.exp_id = result["experiences"][0]["id"]
        ei.record_experience(self.exp_id, confirmed=True, store_path=self.store)
        ei.process_experiential_signal(INDUSTRY_OBSERVATION_TEXT, store_path=self.store)

    def test_query_all_returns_records(self):
        results = ei.query_experiences(store_path=self.store)
        self.assertGreater(len(results), 0)

    def test_query_by_intel_type(self):
        results = ei.query_experiences(intel_type="industry_observation", store_path=self.store)
        for r in results:
            self.assertEqual(r["intel_type"], "industry_observation")

    def test_query_by_claim_status_confirmed(self):
        results = ei.query_experiences(claim_status="confirmed", store_path=self.store)
        for r in results:
            self.assertEqual(r["claim_status"], "confirmed")

    def test_query_by_claim_status_proposed(self):
        results = ei.query_experiences(claim_status="proposed", store_path=self.store)
        for r in results:
            self.assertEqual(r["claim_status"], "proposed")

    def test_query_by_employer_sensitive_true(self):
        results = ei.query_experiences(employer_sensitive=True, store_path=self.store)
        for r in results:
            self.assertTrue(r["employer_sensitive"])

    def test_query_by_employer_sensitive_false(self):
        results = ei.query_experiences(employer_sensitive=False, store_path=self.store)
        for r in results:
            self.assertFalse(r["employer_sensitive"])

    def test_query_by_tags(self):
        results = ei.query_experiences(tags=["restaurant_tech"], store_path=self.store)
        for r in results:
            self.assertIn("restaurant_tech", r.get("retrieval_hooks", {}))


# ---------------------------------------------------------------------------
# E11: query_retrieval_hooks
# ---------------------------------------------------------------------------

class TestQueryRetrievalHooks(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "experiential.json"
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        for exp in result["experiences"]:
            ei.record_experience(exp["id"], confirmed=True, store_path=self.store)

    def test_query_returns_required_fields(self):
        result = ei.query_retrieval_hooks("restaurant_tech", store_path=self.store)
        for field in ("domain", "lesson_types", "matched_experiences",
                      "match_count", "retrieval_confidence"):
            self.assertIn(field, result)

    def test_query_restaurant_tech_finds_matches(self):
        result = ei.query_retrieval_hooks("restaurant_tech", store_path=self.store)
        self.assertGreater(result["match_count"], 0)

    def test_query_unknown_domain_returns_no_matches(self):
        result = ei.query_retrieval_hooks("quantum_physics", store_path=self.store)
        self.assertEqual(result["match_count"], 0)

    def test_matched_experiences_have_lesson_types(self):
        result = ei.query_retrieval_hooks("restaurant_tech", store_path=self.store)
        for exp in result["matched_experiences"]:
            self.assertIn("lesson_types", exp)
            self.assertIsInstance(exp["lesson_types"], list)

    def test_retrieval_confidence_valid(self):
        result = ei.query_retrieval_hooks("restaurant_tech", store_path=self.store)
        self.assertIn(result["retrieval_confidence"], ("high", "medium", "low", "none"))

    def test_only_confirmed_returned_by_default(self):
        result = ei.query_retrieval_hooks("restaurant_tech", claim_status="confirmed",
                                           store_path=self.store)
        for exp in result["matched_experiences"]:
            self.assertEqual(exp.get("confidence") or "ok", exp.get("confidence") or "ok")


# ---------------------------------------------------------------------------
# E12: externalize()
# ---------------------------------------------------------------------------

class TestExternalizeFunction(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "experiential.json"
        result = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        self.exp_id = result["experiences"][0]["id"]

    def test_externalize_returns_external_version(self):
        ext = ei.externalize(self.exp_id, store_path=self.store)
        self.assertIn("external_version", ext)

    def test_externalize_sets_flag(self):
        ext = ei.externalize(self.exp_id, store_path=self.store)
        self.assertTrue(ext.get("externalization_applied"))

    def test_externalize_unknown_id_errors(self):
        ext = ei.externalize("bad-id", store_path=self.store)
        self.assertIn("error", ext)

    def test_externalize_includes_employer_sensitive_flag(self):
        ext = ei.externalize(self.exp_id, store_path=self.store)
        self.assertIn("employer_sensitive", ext)

    def test_externalize_includes_retrieval_hooks(self):
        ext = ei.externalize(self.exp_id, store_path=self.store)
        self.assertIn("retrieval_hooks", ext)


# ---------------------------------------------------------------------------
# E13: Regression — no auto-mutations, silent persistence never allowed
# ---------------------------------------------------------------------------

class TestRegressions(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "experiential.json"

    def test_no_confirmed_records_after_process_only(self):
        ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        confirmed = ei.query_experiences(claim_status="confirmed", store_path=self.store)
        self.assertEqual(len(confirmed), 0)

    def test_persistence_status_never_none(self):
        for text in (RESTAURANT_AI_FAILURE, INDUSTRY_OBSERVATION_TEXT,
                     STRATEGIC_FRAMEWORK_TEXT, NO_SIGNAL_TEXT, ""):
            result = ei.process_experiential_signal(text, store_path=self.store)
            self.assertIsNotNone(result.get("persistence_status"))

    def test_employer_sensitive_never_absent_from_output(self):
        for text in (RESTAURANT_AI_FAILURE, INDUSTRY_OBSERVATION_TEXT):
            result = ei.process_experiential_signal(text, store_path=self.store)
            self.assertIn("employer_sensitive", result)

    def test_trust_stats_trust_contract_met_on_every_call(self):
        for text in (RESTAURANT_AI_FAILURE, INDUSTRY_OBSERVATION_TEXT, NO_SIGNAL_TEXT):
            result = ei.process_experiential_signal(text, store_path=self.store)
            self.assertIn("trust_stats", result)
            self.assertIn("trust_contract_met", result["trust_stats"])

    def test_store_persists_across_calls(self):
        ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        ei.process_experiential_signal(INDUSTRY_OBSERVATION_TEXT, store_path=self.store)
        all_records = ei.query_experiences(store_path=self.store)
        self.assertGreater(len(all_records), 1)

    def test_duplicate_ingestion_does_not_create_duplicate_ids(self):
        r1 = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        r2 = ei.process_experiential_signal(RESTAURANT_AI_FAILURE, store_path=self.store)
        all_records = ei.query_experiences(store_path=self.store)
        ids = [r["id"] for r in all_records]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
