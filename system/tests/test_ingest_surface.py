"""
test_ingest_surface.py — CoS mutation surface formatter tests (RB 9.25).

Covers:
  S1: format_surface() — type detection and section presence
  S2: format_surface() — experience result formatting
  S3: format_surface() — macro result formatting
  S4: format_surface() — relationship result formatting
  S5: format_surface() — insight result formatting
  S6: Trust stats enforcement — always present, never omitted
  S7: Employer sensitivity warning — rendered when flag is set
  S8: Mutation proposals — always rendered as pending
  S9: format_confirm() — confirm and reject acknowledgment format
  S10: format_retrieval() — retrieval hook query formatting
  S11: Edge cases — empty/None result, unknown type, no signals
  S12: Behavioral intelligence layer in cos_judgment
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ingest_surface as surf
import cos_judgment


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

EXPERIENCE_RESULT = {
    "experiences": [
        {
            "id": "exp-test-001",
            "intel_type": "failure_case_study",
            "confidence": "high",
            "matched_keywords": ["failed", "root cause"],
            "claim_status": "proposed",
            "persistence_status": "pending confirmation",
            "confirmed_at": None,
            "source_type": "conversation",
            "employer_sensitive": True,
            "retrieval_hooks": {
                "restaurant_tech": ["operational reality lessons"],
                "failure_case_study": ["direct experience record"],
            },
            "case_study": {
                "title": "Operational Failure Case Study: Restaurant Tech Deployment",
                "internal_version": {
                    "root_causes": ["Workflow duplication — operators did everything twice."],
                    "lessons_learned": ["Operational trust must be earned before scale."],
                    "reusable_frameworks": [],
                    "success_factors": [],
                    "failure_factors": ["Trust was never established."],
                    "employer_sensitive": True,
                    "employer_names_detected": ["NomadGo"],
                },
                "external_version": {
                    "lessons_learned": ["In one large-scale restaurant deployment, trust was not established."],
                    "externalization_note": "Company names generalized.",
                },
            },
            "proposed_mutations": [
                {
                    "mutation_type": "strategic_memory",
                    "target": "system/strategic_memory.json",
                    "operation": "add",
                    "requires_confirmation": True,
                    "persistence_endpoint": "POST /experiential/confirm",
                },
            ],
        }
    ],
    "trust_stats": {
        "sources_assessed": 1,
        "sources_accepted": 1,
        "sources_rejected": 0,
        "confidence": "high",
        "mutation_recommendations": ["strategic_memory"],
        "follow_up_loops": ["Confirm root causes before mutation"],
        "retrieval_classifications": ["restaurant_tech"],
        "trust_contract_met": True,
    },
    "mutation_proposals": [
        {
            "mutation_type": "strategic_memory",
            "target": "system/strategic_memory.json",
            "operation": "add",
            "requires_confirmation": True,
            "persistence_endpoint": "POST /experiential/confirm",
        }
    ],
    "retrieval_hooks": {
        "restaurant_tech": ["operational reality lessons"],
        "failure_case_study": ["direct experience record"],
    },
    "recommended_actions": [
        "Confirm failure case study and lock internal version before externalization",
        "Employer-sensitive entities detected — review external_version before external surfacing",
    ],
    "persistence_status": "pending confirmation",
    "experience_count": 1,
    "employer_sensitive": True,
}

MACRO_RESULT = {
    "behavioral_signals": [
        {
            "signal_type": "consumer_hesitation",
            "confidence": "high",
            "matched_keywords": ["drove away", "parking lot"],
            "evidence_sentences": ["People drove away from Five Guys."],
        }
    ],
    "behavioral_artifacts": [
        {
            "name": "Parking Lot Hesitation",
            "definition": "Pre-purchase abandonment before restaurant entry.",
            "strategic_significance": "Leading indicator of premium concept pressure.",
            "leading_indicators": ["parking lot abandonment", "drive-away without entry"],
        }
    ],
    "entity_risk_mutations": [
        {
            "entity": "Five Guys",
            "risk_dimensions": {"pricing_ceiling_pressure": "rising", "operational_flexibility": "low"},
        }
    ],
    "tech_implications": [
        {"implication": "Frequency and retention tech urgency elevated.", "daily_brief_layers": ["restaurant_tech_spending_risk"]}
    ],
    "ri_mutation": None,
    "daily_brief_layers": ["consumer_sentiment", "fast_casual_pressure"],
    "mutation_proposals": [
        {"mutation_type": "behavioral_intelligence", "target": "system/behavioral_intelligence.json", "operation": "add", "requires_confirmation": True}
    ],
    "cos_surface": {"summary": "Consumer hesitation signal detected."},
    "persistence_status": "pending confirmation",
    "signal_count": 1,
    "artifact_count": 1,
    "entity_mutation_count": 1,
    "tech_implication_count": 1,
    "mutation_proposal_count": 1,
}

RELATIONSHIP_RESULT = {
    "interactions": [
        {
            "id": "ri-test-001",
            "entity_name": "Sarah Chen",
            "entity_org": "Olo",
            "entity_role": "CTO",
            "signal_type": "direct_inquiry",
            "trust_delta": 2,
            "relationship_state": "warm",
            "strategic_classification": "Executive Strategic Contact",
            "recommended_posture": "Consistent, value-first engagement.",
            "claim_status": "proposed",
        }
    ],
    "mutation_proposals": [
        {"mutation_type": "contact_upsert", "target": "baseline_index.json", "operation": "add", "requires_confirmation": True}
    ],
    "cos_surface": {"entity_name": "Sarah Chen", "strategic_classification": "Executive Strategic Contact"},
    "persistence_status": "pending confirmation",
}

INSIGHT_RESULT = {
    "insights": [
        {
            "id": "ii-test-001",
            "insight_type": "industry_trend",
            "confidence": "high",
            "claim": "Restaurant operators face structural retention challenges.",
            "claim_status": "proposed",
            "future_use_tags": ["strategic_memory", "daily_brief", "restaurant_tech"],
        }
    ],
    "mutation_proposals": [
        {"mutation_type": "industry_graph", "target": "system/graphs/industry_intelligence.json", "operation": "add", "requires_confirmation": True}
    ],
    "retrieval_tags": ["strategic_memory", "daily_brief", "restaurant_tech"],
    "persistence_status": "pending confirmation",
    "insight_count": 1,
    "mutation_proposal_count": 1,
}

NO_SIGNAL_RESULT = {
    "experiences": [],
    "trust_stats": {
        "sources_assessed": 1,
        "sources_accepted": 0,
        "sources_rejected": 1,
        "confidence": "none",
        "mutation_recommendations": [],
        "follow_up_loops": [],
        "retrieval_classifications": [],
        "trust_contract_met": True,
    },
    "mutation_proposals": [],
    "retrieval_hooks": {},
    "recommended_actions": [],
    "persistence_status": "RB did not persist",
    "experience_count": 0,
    "employer_sensitive": False,
}


# ---------------------------------------------------------------------------
# S1: Type detection and section presence
# ---------------------------------------------------------------------------

class TestTypeDetection(unittest.TestCase):

    def test_experience_type_detected(self):
        result = surf.format_surface(EXPERIENCE_RESULT)
        self.assertIn("EXPERIENTIAL INTELLIGENCE", result)

    def test_macro_type_detected(self):
        result = surf.format_surface(MACRO_RESULT)
        self.assertIn("MACRO BEHAVIORAL INTELLIGENCE", result)

    def test_relationship_type_detected(self):
        result = surf.format_surface(RELATIONSHIP_RESULT)
        self.assertIn("RELATIONSHIP INTELLIGENCE", result)

    def test_insight_type_detected(self):
        result = surf.format_surface(INSIGHT_RESULT)
        self.assertIn("STRATEGIC INSIGHT", result)

    def test_none_result_no_crash(self):
        result = surf.format_surface(None)
        self.assertIn("ERROR", result)

    def test_empty_dict_no_crash(self):
        result = surf.format_surface({})
        self.assertIsInstance(result, str)


# ---------------------------------------------------------------------------
# S2: Experience result formatting
# ---------------------------------------------------------------------------

class TestExperienceFormatting(unittest.TestCase):

    def setUp(self):
        self.result = surf.format_surface(EXPERIENCE_RESULT)

    def test_contains_intel_type(self):
        self.assertIn("failure_case_study", self.result)

    def test_contains_case_study_title(self):
        self.assertIn("Operational Failure Case Study", self.result)

    def test_contains_lesson(self):
        self.assertIn("trust", self.result.lower())

    def test_contains_root_cause(self):
        self.assertIn("Workflow duplication", self.result)

    def test_contains_retrieval_hooks(self):
        self.assertIn("restaurant_tech", self.result)

    def test_contains_recommended_actions(self):
        self.assertIn("RECOMMENDED ACTIONS", self.result)

    def test_contains_employer_warning(self):
        self.assertIn("EMPLOYER SENSITIVITY", self.result)


# ---------------------------------------------------------------------------
# S3: Macro result formatting
# ---------------------------------------------------------------------------

class TestMacroFormatting(unittest.TestCase):

    def setUp(self):
        self.result = surf.format_surface(MACRO_RESULT)

    def test_contains_signal_type(self):
        self.assertIn("consumer_hesitation", self.result)

    def test_contains_artifact_name(self):
        self.assertIn("Parking Lot Hesitation", self.result)

    def test_contains_entity(self):
        self.assertIn("Five Guys", self.result)

    def test_contains_brief_layers(self):
        self.assertIn("consumer_sentiment", self.result)

    def test_no_employer_warning_when_not_sensitive(self):
        self.assertNotIn("EMPLOYER SENSITIVITY", self.result)


# ---------------------------------------------------------------------------
# S4: Relationship result formatting
# ---------------------------------------------------------------------------

class TestRelationshipFormatting(unittest.TestCase):

    def setUp(self):
        self.result = surf.format_surface(RELATIONSHIP_RESULT)

    def test_contains_entity_name(self):
        self.assertIn("Sarah Chen", self.result)

    def test_contains_signal_type(self):
        self.assertIn("direct_inquiry", self.result)

    def test_contains_trust_delta(self):
        self.assertIn("trust Δ+2", self.result)

    def test_contains_classification(self):
        self.assertIn("Executive Strategic Contact", self.result)

    def test_contains_posture(self):
        self.assertIn("Consistent", self.result)


# ---------------------------------------------------------------------------
# S5: Insight result formatting
# ---------------------------------------------------------------------------

class TestInsightFormatting(unittest.TestCase):

    def setUp(self):
        self.result = surf.format_surface(INSIGHT_RESULT)

    def test_contains_insight_type(self):
        self.assertIn("industry_trend", self.result)

    def test_contains_claim(self):
        self.assertIn("retention", self.result)

    def test_contains_retrieval_tags(self):
        self.assertIn("restaurant_tech", self.result)


# ---------------------------------------------------------------------------
# S6: Trust stats enforcement
# ---------------------------------------------------------------------------

class TestTrustStatsEnforcement(unittest.TestCase):

    def test_trust_stats_present_in_experience(self):
        result = surf.format_surface(EXPERIENCE_RESULT)
        self.assertIn("TRUST STATS", result)
        self.assertIn("Sources assessed", result)
        self.assertIn("Trust contract met: yes", result)

    def test_trust_stats_present_in_macro(self):
        result = surf.format_surface(MACRO_RESULT)
        # Macro doesn't have trust_stats in this fixture — should show missing warning
        self.assertIn("TRUST STATS", result)

    def test_missing_trust_stats_shows_warning(self):
        result_no_ts = dict(EXPERIENCE_RESULT)
        result_no_ts = {k: v for k, v in EXPERIENCE_RESULT.items() if k != "trust_stats"}
        output = surf.format_surface(result_no_ts)
        self.assertIn("TRUST STATS", output)
        self.assertIn("missing", output.lower())

    def test_trust_stats_confidence_shown(self):
        result = surf.format_surface(EXPERIENCE_RESULT)
        self.assertIn("Confidence: high", result)

    def test_retrieval_domains_shown(self):
        result = surf.format_surface(EXPERIENCE_RESULT)
        self.assertIn("Retrieval domains active", result)


# ---------------------------------------------------------------------------
# S7: Employer sensitivity warning
# ---------------------------------------------------------------------------

class TestEmployerSensitivityWarning(unittest.TestCase):

    def test_warning_shown_when_sensitive(self):
        result = surf.format_surface(EXPERIENCE_RESULT)
        self.assertIn("EMPLOYER SENSITIVITY DETECTED", result)

    def test_warning_not_shown_when_clean(self):
        result = surf.format_surface(NO_SIGNAL_RESULT)
        self.assertNotIn("EMPLOYER SENSITIVITY", result)

    def test_warning_includes_externalize_note(self):
        result = surf.format_surface(EXPERIENCE_RESULT)
        self.assertIn("externalize", result.lower())


# ---------------------------------------------------------------------------
# S8: Mutation proposals always rendered as pending
# ---------------------------------------------------------------------------

class TestMutationProposalsFormatting(unittest.TestCase):

    def test_mutation_proposals_shown(self):
        result = surf.format_surface(EXPERIENCE_RESULT)
        self.assertIn("MUTATION PROPOSALS", result)

    def test_requires_confirmation_shown(self):
        result = surf.format_surface(EXPERIENCE_RESULT)
        self.assertIn("requires_confirmation: yes", result)

    def test_pending_state_explicit_not_confirmed(self):
        # The surface must never imply a record was persisted before user confirmation.
        # "pending confirmation" must be present; "RB recorded" must not appear.
        result = surf.format_surface(EXPERIENCE_RESULT)
        self.assertIn("pending confirmation", result)
        self.assertNotIn("RB recorded", result)

    def test_mutation_count_shown(self):
        result = surf.format_surface(EXPERIENCE_RESULT)
        self.assertIn("1 pending", result)


# ---------------------------------------------------------------------------
# S9: format_confirm()
# ---------------------------------------------------------------------------

class TestFormatConfirm(unittest.TestCase):

    CONFIRMED_RECORD = {
        "id": "exp-test-001",
        "claim_status": "confirmed",
        "persistence_status": "RB recorded",
        "confirmed_at": "2026-06-01T12:00:00+00:00",
    }

    REJECTED_RECORD = {
        "id": "exp-test-002",
        "claim_status": "rejected",
        "persistence_status": "RB skipped",
        "confirmed_at": None,
    }

    ERROR_RECORD = {"error": "Experience exp-bad not found."}

    def test_confirm_shows_confirmed(self):
        result = surf.format_confirm("experience", self.CONFIRMED_RECORD)
        self.assertIn("confirmed", result.lower())
        self.assertIn("RB recorded", result)

    def test_reject_shows_rejected(self):
        result = surf.format_confirm("experience", self.REJECTED_RECORD)
        self.assertIn("rejected", result.lower())
        self.assertIn("RB skipped", result)

    def test_error_handled(self):
        result = surf.format_confirm("experience", self.ERROR_RECORD)
        self.assertIn("ERROR", result)
        self.assertIn("not found", result)

    def test_type_label_shown(self):
        result = surf.format_confirm("insight", self.CONFIRMED_RECORD)
        self.assertIn("INSIGHT", result)


# ---------------------------------------------------------------------------
# S10: format_retrieval()
# ---------------------------------------------------------------------------

class TestFormatRetrieval(unittest.TestCase):

    RETRIEVAL_RESULT = {
        "domain": "restaurant_tech",
        "lesson_types": ["operational reality lessons", "vendor lessons"],
        "matched_experiences": [
            {
                "id": "exp-001",
                "intel_type": "failure_case_study",
                "case_study_title": "Operational Failure Case Study: Restaurant Tech Deployment",
                "lesson_types": ["operational reality lessons"],
                "confidence": "high",
                "employer_sensitive": True,
            }
        ],
        "match_count": 1,
        "retrieval_confidence": "medium",
    }

    NO_MATCH_RESULT = {
        "domain": "quantum_physics",
        "lesson_types": [],
        "matched_experiences": [],
        "match_count": 0,
        "retrieval_confidence": "none",
    }

    def test_retrieval_shows_domain(self):
        result = surf.format_retrieval("restaurant_tech", self.RETRIEVAL_RESULT)
        self.assertIn("RESTAURANT_TECH", result)

    def test_retrieval_shows_match_count(self):
        result = surf.format_retrieval("restaurant_tech", self.RETRIEVAL_RESULT)
        self.assertIn("1 record", result)

    def test_retrieval_shows_case_study_title(self):
        result = surf.format_retrieval("restaurant_tech", self.RETRIEVAL_RESULT)
        self.assertIn("Operational Failure Case Study", result)

    def test_employer_sensitive_flagged(self):
        result = surf.format_retrieval("restaurant_tech", self.RETRIEVAL_RESULT)
        self.assertIn("employer-sensitive", result)

    def test_no_match_returns_clean_message(self):
        result = surf.format_retrieval("quantum_physics", self.NO_MATCH_RESULT)
        self.assertIn("No confirmed prior lessons", result)
        self.assertNotIn("None", result)


# ---------------------------------------------------------------------------
# S11: Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):

    def test_no_signal_surface_no_crash(self):
        result = surf.format_surface(NO_SIGNAL_RESULT)
        self.assertIsInstance(result, str)
        self.assertIn("RB did not persist", result)

    def test_none_no_crash(self):
        result = surf.format_surface(None)
        self.assertIn("ERROR", result)

    def test_empty_dict_no_crash(self):
        result = surf.format_surface({})
        self.assertIsInstance(result, str)

    def test_format_always_returns_string(self):
        for fixture in (EXPERIENCE_RESULT, MACRO_RESULT, RELATIONSHIP_RESULT,
                        INSIGHT_RESULT, NO_SIGNAL_RESULT, {}, None):
            result = surf.format_surface(fixture)
            self.assertIsInstance(result, str)

    def test_result_always_ends_with_newline(self):
        for fixture in (EXPERIENCE_RESULT, MACRO_RESULT, RELATIONSHIP_RESULT, INSIGHT_RESULT):
            result = surf.format_surface(fixture)
            self.assertTrue(result.endswith("\n"))


# ---------------------------------------------------------------------------
# S12: Behavioral intelligence layer in cos_judgment
# ---------------------------------------------------------------------------

class TestBehavioralIntelligenceLayer(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bi_path = Path(self.tmp) / "behavioral_intelligence.json"
        self.ei_path = Path(self.tmp) / "entity_intelligence.json"

    def _write_bi(self, records):
        self.bi_path.write_text(json.dumps({"records": records}))

    def _write_ei(self, records):
        self.ei_path.write_text(json.dumps({"records": records}))

    def test_empty_stores_returns_unavailable(self):
        result = cos_judgment.build_behavioral_intelligence_layer(
            behavioral_path=self.bi_path, entity_path=self.ei_path
        )
        self.assertFalse(result["available"])
        self.assertEqual(result["signal_count"], 0)

    def test_empty_store_files_returns_empty(self):
        self._write_bi([])
        self._write_ei([])
        result = cos_judgment.build_behavioral_intelligence_layer(
            behavioral_path=self.bi_path, entity_path=self.ei_path
        )
        self.assertEqual(result["label"], "empty")

    def test_confirmed_signals_returned(self):
        self._write_bi([
            {"id": "bs-001", "record_type": "behavioral_signal",
             "signal_type": "consumer_hesitation", "confidence": "high",
             "claim_status": "confirmed", "daily_brief_layers": ["consumer_sentiment"]},
        ])
        self._write_ei([])
        result = cos_judgment.build_behavioral_intelligence_layer(
            behavioral_path=self.bi_path, entity_path=self.ei_path
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["label"], "active")
        self.assertEqual(result["signal_count"], 1)
        self.assertIn("consumer_sentiment", result["brief_layer_summary"])

    def test_proposed_signals_excluded(self):
        self._write_bi([
            {"id": "bs-001", "record_type": "behavioral_signal",
             "signal_type": "affordability_stress", "confidence": "medium",
             "claim_status": "proposed", "daily_brief_layers": []},
        ])
        self._write_ei([])
        result = cos_judgment.build_behavioral_intelligence_layer(
            behavioral_path=self.bi_path, entity_path=self.ei_path
        )
        self.assertEqual(result["signal_count"], 0)

    def test_confirmed_artifacts_classified_separately(self):
        self._write_bi([
            {"id": "ba-001", "record_type": "behavioral_artifact",
             "name": "Parking Lot Hesitation", "claim_status": "confirmed",
             "daily_brief_layers": ["fast_casual_pressure"]},
        ])
        self._write_ei([])
        result = cos_judgment.build_behavioral_intelligence_layer(
            behavioral_path=self.bi_path, entity_path=self.ei_path
        )
        self.assertEqual(result["artifact_count"], 1)
        self.assertEqual(result["signal_count"], 0)

    def test_confirmed_entities_returned(self):
        self._write_bi([])
        self._write_ei([
            {"entity_id": "five-guys", "entity": "Five Guys",
             "risk_dimensions": {"pricing_ceiling_pressure": "rising"},
             "claim_status": "confirmed"},
        ])
        result = cos_judgment.build_behavioral_intelligence_layer(
            behavioral_path=self.bi_path, entity_path=self.ei_path
        )
        self.assertEqual(result["entity_count"], 1)

    def test_build_all_includes_behavioral_intelligence(self):
        # build_all() should include the behavioral_intelligence key
        # Use a minimal report to avoid dependency issues
        report = {
            "loops": {"closed": [], "open_past_target": [], "due_soon": [], "open_ok": []},
            "crossings": [],
            "source_freshness": {},
        }
        result = cos_judgment.build_all(report, __import__("datetime").date.today())
        self.assertIn("behavioral_intelligence", result)
        bi = result["behavioral_intelligence"]
        self.assertIn("available", bi)
        self.assertIn("signal_count", bi)

    def test_note_present_when_unavailable(self):
        result = cos_judgment.build_behavioral_intelligence_layer(
            behavioral_path=self.bi_path, entity_path=self.ei_path
        )
        self.assertIn("note", result)
        self.assertIsNotNone(result["note"])


if __name__ == "__main__":
    unittest.main()
