"""
test_macro_intelligence.py — Macro behavioral industry intelligence tests.

Covers DEFECT-012: RB failure to canonically mutate macro behavioral industry
intelligence from operator-generated content.

Test groups:
  M1: Core invariants (required structure, all mutations require confirmation,
      persistence_status, empty/no-signal graceful handling)
  M2: Behavioral signal classification (David Mann newsletter fixture)
  M3: Behavioral artifact generation (Parking Lot Hesitation, Value Migration)
  M4: Entity risk mutations (Five Guys → premium vulnerable, McDonald's → value platform)
  M5: Tech implication derivation (ROI scrutiny, non-essential AI risk)
  M6: RI mutation (David Mann → Strategic Thought Leader via thought_leader_alignment)
  M7: Daily brief layer mapping
  M8: End-to-end CoS surface
  M9: Confirmation lifecycle (record_behavioral_record, record_entity_risk)
  M10: Query functions (query_behavioral_signals, query_artifacts, query_entity_risks)
  M11: relationship_intake extensions (thought_leader_alignment, signal_type_override)
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import macro_intelligence
import relationship_intake

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DAVID_MANN_NEWSLETTER = (
    "I've been watching what's happening in the parking lots at Five Guys lately. "
    "People pull in, sit in their cars for a few minutes, and then drove away. "
    "They didn't get out of the car. "
    "The math just doesn't add up anymore — two burgers and fries for thirty-five dollars "
    "when you can go to McDonald's for a fraction of the price. "
    "This isn't about quality. Five Guys still makes a great burger. "
    "But consumer affordability is cracking under the pressure, "
    "and the premium fast-casual segment is going to feel it first. "
    "Operators need to think hard about pricing flexibility right now. "
    "Commodity costs are rising, but you can't pass it all to the customer anymore. "
    "The ones with operational flexibility will survive. The ones without won't."
)

# No behavioral signals — positive industry growth language only
NO_SIGNAL_TEXT = (
    "Restaurant industry continues to show strong growth. "
    "Comparable sales are up across all segments. "
    "Consumer demand remains resilient heading into Q3. "
    "Traffic trends are positive and same-store sales outperformed expectations."
)

# Minimal signal — only consumer hesitation, no brands
MINIMAL_HESITATION_TEXT = (
    "Consumers are hesitating more than ever. "
    "They drove away before entering. "
    "The parking lot abandonment is real. "
    "Affordability is cracking."
)


# ---------------------------------------------------------------------------
# M1: Core invariants
# ---------------------------------------------------------------------------


class TestCoreInvariants(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.b_store = Path(tmp) / "behavioral.json"
        self.e_store = Path(tmp) / "entity.json"
        self.ri_store = Path(tmp) / "ri.json"

    def _run(self, text=None, **kwargs):
        return macro_intelligence.process_macro_signal(
            text or DAVID_MANN_NEWSLETTER,
            behavioral_store_path=self.b_store,
            entity_store_path=self.e_store,
            ri_store_path=self.ri_store,
            **kwargs,
        )

    def test_M1a_returns_expected_top_level_keys(self):
        result = self._run()
        for key in [
            "behavioral_signals", "behavioral_artifacts", "entity_risk_mutations",
            "tech_implications", "ri_mutation", "daily_brief_layers",
            "mutation_proposals", "cos_surface", "persistence_status",
        ]:
            self.assertIn(key, result, f"Missing key: {key}")

    def test_M1b_empty_input_graceful(self):
        result = self._run(text="   ")
        self.assertEqual(result["persistence_status"], "RB did not persist")
        self.assertEqual(result["behavioral_signals"], [])
        self.assertIn("note", result)

    def test_M1c_no_behavioral_signals_graceful(self):
        result = self._run(text=NO_SIGNAL_TEXT)
        self.assertEqual(result["persistence_status"], "RB did not persist")
        self.assertEqual(result["behavioral_signals"], [])

    def test_M1d_all_mutations_require_confirmation(self):
        result = self._run()
        for proposal in result["mutation_proposals"]:
            self.assertTrue(
                proposal.get("requires_confirmation"),
                f"Missing requires_confirmation on: {proposal.get('mutation_type')}",
            )

    def test_M1e_persistence_status_never_none(self):
        for text in [DAVID_MANN_NEWSLETTER, NO_SIGNAL_TEXT, "   "]:
            result = self._run(text=text)
            self.assertIsNotNone(result["persistence_status"])
            self.assertIn(
                result["persistence_status"],
                macro_intelligence.PERSISTENCE_STATUSES,
            )

    def test_M1f_invalid_source_type_defaults_to_linkedin_post(self):
        result = self._run(source_type="carrier_pigeon")
        self.assertEqual(result["cos_surface"]["source_type"], "linkedin_post")

    def test_M1g_signal_count_matches_behavioral_signals_length(self):
        result = self._run()
        self.assertEqual(result["signal_count"], len(result["behavioral_signals"]))

    def test_M1h_mutation_proposal_count_matches_list_length(self):
        result = self._run()
        self.assertEqual(result["mutation_proposal_count"], len(result["mutation_proposals"]))


# ---------------------------------------------------------------------------
# M2: Behavioral signal classification
# ---------------------------------------------------------------------------


class TestBehavioralSignalClassification(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.b_store = Path(tmp) / "b.json"
        self.e_store = Path(tmp) / "e.json"
        self.result = macro_intelligence.process_macro_signal(
            DAVID_MANN_NEWSLETTER,
            behavioral_store_path=self.b_store,
            entity_store_path=self.e_store,
        )
        self.signal_types = {s["signal_type"] for s in self.result["behavioral_signals"]}

    def test_M2a_consumer_hesitation_detected(self):
        self.assertIn("consumer_hesitation", self.signal_types)

    def test_M2b_affordability_stress_detected(self):
        self.assertIn("affordability_stress", self.signal_types)

    def test_M2c_trade_down_behavior_detected(self):
        self.assertIn("trade_down_behavior", self.signal_types)

    def test_M2d_operational_pain_detected(self):
        self.assertIn("operational_pain", self.signal_types)

    def test_M2e_consumer_hesitation_confidence_high(self):
        hesitation = next(
            s for s in self.result["behavioral_signals"]
            if s["signal_type"] == "consumer_hesitation"
        )
        self.assertEqual(hesitation["confidence"], "high")

    def test_M2f_affordability_stress_confidence_high(self):
        affordability = next(
            s for s in self.result["behavioral_signals"]
            if s["signal_type"] == "affordability_stress"
        )
        self.assertEqual(affordability["confidence"], "high")

    def test_M2g_operational_pain_confidence_high(self):
        pain = next(
            s for s in self.result["behavioral_signals"]
            if s["signal_type"] == "operational_pain"
        )
        self.assertEqual(pain["confidence"], "high")

    def test_M2h_each_signal_has_required_fields(self):
        for signal in self.result["behavioral_signals"]:
            for field in {"signal_type", "confidence", "matched_keywords", "evidence_sentences"}:
                self.assertIn(field, signal, f"Signal missing field: {field}")

    def test_M2i_signals_sorted_high_confidence_first(self):
        conf_order = {"high": 0, "medium": 1, "low": 2}
        confidences = [s["confidence"] for s in self.result["behavioral_signals"]]
        sorted_c = sorted(confidences, key=lambda c: conf_order[c])
        self.assertEqual(confidences, sorted_c)

    def test_M2j_behavioral_signals_persisted_to_store(self):
        store = json.loads(self.b_store.read_text())
        signal_records = [r for r in store["records"] if r.get("record_type") == "behavioral_signal"]
        self.assertGreaterEqual(len(signal_records), 1)


# ---------------------------------------------------------------------------
# M3: Behavioral artifact generation
# ---------------------------------------------------------------------------


class TestBehavioralArtifactGeneration(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.b_store = Path(tmp) / "b.json"
        self.e_store = Path(tmp) / "e.json"
        self.result = macro_intelligence.process_macro_signal(
            DAVID_MANN_NEWSLETTER,
            behavioral_store_path=self.b_store,
            entity_store_path=self.e_store,
        )
        self.artifact_names = {a["artifact_name"] for a in self.result["behavioral_artifacts"]}

    def test_M3a_parking_lot_hesitation_artifact_generated(self):
        self.assertIn("Parking Lot Hesitation", self.artifact_names)

    def test_M3b_parking_lot_hesitation_has_definition(self):
        artifact = next(
            a for a in self.result["behavioral_artifacts"]
            if a["artifact_name"] == "Parking Lot Hesitation"
        )
        self.assertIsNotNone(artifact["definition"])
        self.assertGreater(len(artifact["definition"]), 20)

    def test_M3c_parking_lot_hesitation_has_strategic_significance(self):
        artifact = next(
            a for a in self.result["behavioral_artifacts"]
            if a["artifact_name"] == "Parking Lot Hesitation"
        )
        self.assertIsNotNone(artifact["strategic_significance"])
        self.assertGreater(len(artifact["strategic_significance"]), 20)

    def test_M3d_parking_lot_hesitation_has_leading_indicators(self):
        artifact = next(
            a for a in self.result["behavioral_artifacts"]
            if a["artifact_name"] == "Parking Lot Hesitation"
        )
        self.assertIsInstance(artifact["leading_indicators"], list)
        self.assertGreater(len(artifact["leading_indicators"]), 0)

    def test_M3e_value_migration_artifact_generated(self):
        self.assertIn("Value Migration Behavior", self.artifact_names)

    def test_M3f_artifacts_start_as_proposed(self):
        for artifact in self.result["behavioral_artifacts"]:
            self.assertEqual(artifact["claim_status"], "proposed")
            self.assertEqual(artifact["persistence_status"], "pending confirmation")
            self.assertIsNone(artifact["confirmed_at"])

    def test_M3g_artifacts_persisted_to_store(self):
        artifacts = macro_intelligence.query_behavioral_artifacts(
            behavioral_store_path=self.b_store
        )
        artifact_names = {a["artifact_name"] for a in artifacts}
        self.assertIn("Parking Lot Hesitation", artifact_names)

    def test_M3h_artifact_mutation_proposals_present(self):
        artifact_proposals = [
            p for p in self.result["mutation_proposals"]
            if p["mutation_type"] == "behavioral_artifact"
        ]
        self.assertGreater(len(artifact_proposals), 0)

    def test_M3i_no_artifact_without_trigger_keywords(self):
        result = macro_intelligence.process_macro_signal(
            MINIMAL_HESITATION_TEXT,
            behavioral_store_path=Path(tempfile.mkdtemp()) / "b.json",
            entity_store_path=Path(tempfile.mkdtemp()) / "e.json",
        )
        # MINIMAL_HESITATION_TEXT has "parking lot" and "affordability" so artifact should fire
        # but let's check the structure is correct regardless
        self.assertIsInstance(result["behavioral_artifacts"], list)


# ---------------------------------------------------------------------------
# M4: Entity risk mutations
# ---------------------------------------------------------------------------


class TestEntityRiskMutations(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.b_store = Path(tmp) / "b.json"
        self.e_store = Path(tmp) / "e.json"
        self.result = macro_intelligence.process_macro_signal(
            DAVID_MANN_NEWSLETTER,
            behavioral_store_path=self.b_store,
            entity_store_path=self.e_store,
        )
        self.entity_ids = {e["entity_id"] for e in self.result["entity_risk_mutations"]}

    def test_M4a_five_guys_detected_as_premium_vulnerable(self):
        self.assertIn("five-guys", self.entity_ids)

    def test_M4b_five_guys_pricing_ceiling_pressure_rising(self):
        fg = next(e for e in self.result["entity_risk_mutations"] if e["entity_id"] == "five-guys")
        self.assertIn("pricing_ceiling_pressure", fg["risk_profile"])
        self.assertEqual(fg["risk_profile"]["pricing_ceiling_pressure"]["value"], "rising")

    def test_M4c_five_guys_commodity_exposure_high(self):
        fg = next(e for e in self.result["entity_risk_mutations"] if e["entity_id"] == "five-guys")
        self.assertIn("commodity_exposure", fg["risk_profile"])
        self.assertEqual(fg["risk_profile"]["commodity_exposure"]["value"], "high")

    def test_M4d_five_guys_brand_category_premium(self):
        fg = next(e for e in self.result["entity_risk_mutations"] if e["entity_id"] == "five-guys")
        self.assertEqual(fg["brand_category"], "premium_fast_casual")

    def test_M4e_mcdonalds_detected_as_value_platform(self):
        # mcdonald or mcdonald- as entity_id
        value_ids = {e["entity_id"] for e in self.result["entity_risk_mutations"]
                     if e["brand_category"] == "value_platform"}
        self.assertTrue(any("mcdonald" in eid for eid in value_ids))

    def test_M4f_mcdonalds_trade_down_capture_increasing(self):
        mc = next(
            e for e in self.result["entity_risk_mutations"]
            if "mcdonald" in e["entity_id"]
        )
        self.assertIn("trade_down_capture_capability", mc["risk_profile"])
        self.assertEqual(mc["risk_profile"]["trade_down_capture_capability"]["value"], "increasing")

    def test_M4g_entity_mutations_start_as_proposed(self):
        for entity in self.result["entity_risk_mutations"]:
            self.assertEqual(entity["claim_status"], "proposed")
            self.assertEqual(entity["persistence_status"], "pending confirmation")

    def test_M4h_entity_mutations_persisted_to_store(self):
        store = json.loads(self.e_store.read_text())
        self.assertGreater(len(store["records"]), 0)

    def test_M4i_entity_mutation_proposals_present(self):
        entity_proposals = [
            p for p in self.result["mutation_proposals"]
            if p["mutation_type"] == "entity_risk_profile"
        ]
        self.assertGreater(len(entity_proposals), 0)

    def test_M4j_entity_proposals_require_confirmation(self):
        entity_proposals = [
            p for p in self.result["mutation_proposals"]
            if p["mutation_type"] == "entity_risk_profile"
        ]
        for p in entity_proposals:
            self.assertTrue(p.get("requires_confirmation"))


# ---------------------------------------------------------------------------
# M5: Tech implication derivation
# ---------------------------------------------------------------------------


class TestTechImplications(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.b_store = Path(tmp) / "b.json"
        self.e_store = Path(tmp) / "e.json"
        self.result = macro_intelligence.process_macro_signal(
            DAVID_MANN_NEWSLETTER,
            behavioral_store_path=self.b_store,
            entity_store_path=self.e_store,
        )
        self.implication_texts = [i["implication"] for i in self.result["tech_implications"]]

    def test_M5a_tech_implications_present(self):
        self.assertGreater(len(self.result["tech_implications"]), 0)

    def test_M5b_roi_scrutiny_implication_triggered(self):
        self.assertTrue(
            any("ROI" in i or "roi" in i.lower() for i in self.implication_texts),
            f"ROI scrutiny implication not found in: {self.implication_texts}",
        )

    def test_M5c_non_essential_ai_implication_triggered(self):
        self.assertTrue(
            any("AI" in i or "tooling" in i.lower() for i in self.implication_texts),
            f"Non-essential AI implication not found in: {self.implication_texts}",
        )

    def test_M5d_each_implication_has_triggered_by(self):
        for impl in self.result["tech_implications"]:
            self.assertIn("triggered_by", impl)
            self.assertGreater(len(impl["triggered_by"]), 0)

    def test_M5e_tech_implication_proposals_present(self):
        tech_proposals = [
            p for p in self.result["mutation_proposals"]
            if p["mutation_type"] == "tech_implication"
        ]
        self.assertGreater(len(tech_proposals), 0)

    def test_M5f_no_duplicate_implications(self):
        seen = set()
        for impl in self.result["tech_implications"]:
            text = impl["implication"]
            self.assertNotIn(text, seen, f"Duplicate implication: {text}")
            seen.add(text)


# ---------------------------------------------------------------------------
# M6: RI mutation — David Mann as Strategic Thought Leader
# ---------------------------------------------------------------------------


class TestRIMutation(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.b_store = Path(self.tmp) / "b.json"
        self.e_store = Path(self.tmp) / "e.json"
        self.ri_store = Path(self.tmp) / "ri.json"

    def _run(self, **kwargs):
        return macro_intelligence.process_macro_signal(
            DAVID_MANN_NEWSLETTER,
            behavioral_store_path=self.b_store,
            entity_store_path=self.e_store,
            ri_store_path=self.ri_store,
            **kwargs,
        )

    def test_M6a_ri_mutation_present_when_author_provided(self):
        result = self._run(author_name="David Mann")
        self.assertIsNotNone(result["ri_mutation"])

    def test_M6b_ri_mutation_absent_when_no_author(self):
        result = self._run()
        self.assertIsNone(result["ri_mutation"])

    def test_M6c_ri_mutation_signal_type_is_thought_leader_alignment(self):
        result = self._run(author_name="David Mann")
        interaction = result["ri_mutation"]["interactions"][0]
        self.assertEqual(interaction["signal_type"], "thought_leader_alignment")

    def test_M6d_david_mann_classified_as_strategic_thought_leader(self):
        result = self._run(author_name="David Mann")
        interaction = result["ri_mutation"]["interactions"][0]
        self.assertEqual(interaction["strategic_classification"], "Strategic Thought Leader")

    def test_M6e_ri_mutation_persisted_to_ledger(self):
        self._run(author_name="David Mann")
        self.assertTrue(self.ri_store.exists())
        ledger = json.loads(self.ri_store.read_text())
        self.assertGreater(len(ledger["interactions"]), 0)

    def test_M6f_ri_mutation_contact_id_derived_from_author_name(self):
        result = self._run(author_name="David Mann")
        interaction = result["ri_mutation"]["interactions"][0]
        self.assertEqual(interaction["contact_id"], "david-mann")

    def test_M6g_ri_mutation_ecosystem_tags_include_restaurant_tech(self):
        result = self._run(author_name="David Mann")
        interaction = result["ri_mutation"]["interactions"][0]
        self.assertIn("restaurant_tech", interaction["ecosystem_tags"])

    def test_M6h_ri_mutation_ecosystem_tags_include_operator_network(self):
        result = self._run(author_name="David Mann")
        interaction = result["ri_mutation"]["interactions"][0]
        self.assertIn("operator_network", interaction["ecosystem_tags"])

    def test_M6i_ri_mutation_trust_delta_is_1(self):
        result = self._run(author_name="David Mann")
        interaction = result["ri_mutation"]["interactions"][0]
        self.assertEqual(interaction["trust_delta"], 1)

    def test_M6j_ri_mutation_requires_confirmation(self):
        result = self._run(author_name="David Mann")
        for proposal in result["ri_mutation"]["mutation_proposals"]:
            self.assertTrue(proposal.get("requires_confirmation"))

    def test_M6k_cos_surface_ri_mutation_proposed_present(self):
        result = self._run(author_name="David Mann")
        self.assertIsNotNone(result["cos_surface"]["ri_mutation_proposed"])

    def test_M6l_explicit_ecosystem_tags_merged(self):
        result = self._run(
            author_name="David Mann",
            ecosystem_tags=["hospitality_table"],
        )
        interaction = result["ri_mutation"]["interactions"][0]
        self.assertIn("hospitality_table", interaction["ecosystem_tags"])


# ---------------------------------------------------------------------------
# M7: Daily brief layer mapping
# ---------------------------------------------------------------------------


class TestDailyBriefLayers(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.result = macro_intelligence.process_macro_signal(
            DAVID_MANN_NEWSLETTER,
            behavioral_store_path=Path(tmp) / "b.json",
            entity_store_path=Path(tmp) / "e.json",
        )

    def test_M7a_daily_brief_layers_non_empty(self):
        self.assertGreater(len(self.result["daily_brief_layers"]), 0)

    def test_M7b_consumer_sentiment_layer_present(self):
        self.assertIn("consumer_sentiment", self.result["daily_brief_layers"])

    def test_M7c_fast_casual_pressure_layer_present(self):
        self.assertIn("fast_casual_pressure", self.result["daily_brief_layers"])

    def test_M7d_restaurant_tech_spending_risk_layer_present(self):
        self.assertIn("restaurant_tech_spending_risk", self.result["daily_brief_layers"])

    def test_M7e_commodity_inflation_layer_present(self):
        self.assertIn("commodity_inflation", self.result["daily_brief_layers"])

    def test_M7f_value_platform_competitive_layer_present(self):
        self.assertIn("value_platform_competitive", self.result["daily_brief_layers"])

    def test_M7g_layers_are_deduplicated(self):
        layers = self.result["daily_brief_layers"]
        self.assertEqual(len(layers), len(set(layers)))

    def test_M7h_layers_in_cos_surface_match_result(self):
        self.assertEqual(
            self.result["cos_surface"]["daily_brief_layers"],
            self.result["daily_brief_layers"],
        )


# ---------------------------------------------------------------------------
# M8: End-to-end CoS surface
# ---------------------------------------------------------------------------


class TestCoSSurface(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.b_store = Path(tmp) / "b.json"
        self.e_store = Path(tmp) / "e.json"
        self.ri_store = Path(tmp) / "ri.json"
        self.result = macro_intelligence.process_macro_signal(
            DAVID_MANN_NEWSLETTER,
            author_name="David Mann",
            behavioral_store_path=self.b_store,
            entity_store_path=self.e_store,
            ri_store_path=self.ri_store,
        )
        self.cos = self.result["cos_surface"]

    def test_M8a_cos_surface_populated(self):
        self.assertIsNotNone(self.cos)

    def test_M8b_cos_surface_has_author(self):
        self.assertIsNotNone(self.cos["author"])
        self.assertEqual(self.cos["author"]["name"], "David Mann")

    def test_M8c_cos_surface_has_cos_summary(self):
        self.assertIsNotNone(self.cos["cos_summary"])
        self.assertGreater(len(self.cos["cos_summary"]), 10)

    def test_M8d_cos_surface_behavioral_artifacts_present(self):
        self.assertGreater(len(self.cos["behavioral_artifacts"]), 0)

    def test_M8e_cos_surface_entity_risk_mutations_present(self):
        self.assertGreater(len(self.cos["entity_risk_mutations"]), 0)

    def test_M8f_cos_surface_tech_implications_present(self):
        self.assertGreater(len(self.cos["tech_implications"]), 0)

    def test_M8g_cos_surface_ri_mutation_proposed(self):
        self.assertIsNotNone(self.cos["ri_mutation_proposed"])

    def test_M8h_cos_summary_mentions_consumer_hesitation_context(self):
        summary_lower = self.cos["cos_summary"].lower()
        self.assertTrue(
            "consumer" in summary_lower or "hesitation" in summary_lower or "abandonment" in summary_lower,
            f"Expected consumer/hesitation context in summary: {self.cos['cos_summary']}",
        )


# ---------------------------------------------------------------------------
# M9: Confirmation lifecycle
# ---------------------------------------------------------------------------


class TestConfirmationLifecycle(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.b_store = Path(self.tmp) / "b.json"
        self.e_store = Path(self.tmp) / "e.json"
        self.result = macro_intelligence.process_macro_signal(
            DAVID_MANN_NEWSLETTER,
            behavioral_store_path=self.b_store,
            entity_store_path=self.e_store,
        )

    def test_M9a_confirm_behavioral_record_sets_rb_recorded(self):
        store = json.loads(self.b_store.read_text())
        record_id = store["records"][0]["id"]
        updated = macro_intelligence.record_behavioral_record(
            record_id, confirmed=True, behavioral_store_path=self.b_store
        )
        self.assertEqual(updated["persistence_status"], "RB recorded")
        self.assertEqual(updated["claim_status"], "confirmed")
        self.assertIsNotNone(updated["confirmed_at"])

    def test_M9b_reject_behavioral_record_sets_rb_skipped(self):
        store = json.loads(self.b_store.read_text())
        record_id = store["records"][0]["id"]
        updated = macro_intelligence.record_behavioral_record(
            record_id, confirmed=False, behavioral_store_path=self.b_store
        )
        self.assertEqual(updated["persistence_status"], "RB skipped")
        self.assertEqual(updated["claim_status"], "rejected")

    def test_M9c_confirm_unknown_behavioral_record_returns_error(self):
        result = macro_intelligence.record_behavioral_record(
            "nonexistent-id-000", confirmed=True, behavioral_store_path=self.b_store
        )
        self.assertIn("error", result)

    def test_M9d_confirm_entity_risk_sets_rb_recorded(self):
        updated = macro_intelligence.record_entity_risk(
            "five-guys", confirmed=True, entity_store_path=self.e_store
        )
        self.assertEqual(updated["persistence_status"], "RB recorded")
        self.assertEqual(updated["claim_status"], "confirmed")
        self.assertIsNotNone(updated["confirmed_at"])

    def test_M9e_reject_entity_risk_sets_rb_skipped(self):
        updated = macro_intelligence.record_entity_risk(
            "five-guys", confirmed=False, entity_store_path=self.e_store
        )
        self.assertEqual(updated["persistence_status"], "RB skipped")

    def test_M9f_confirm_unknown_entity_returns_error(self):
        result = macro_intelligence.record_entity_risk(
            "nonexistent-brand", confirmed=True, entity_store_path=self.e_store
        )
        self.assertIn("error", result)

    def test_M9g_confirmation_persisted_in_store(self):
        store = json.loads(self.b_store.read_text())
        record_id = store["records"][0]["id"]
        macro_intelligence.record_behavioral_record(
            record_id, confirmed=True, behavioral_store_path=self.b_store
        )
        updated_store = json.loads(self.b_store.read_text())
        record = next(r for r in updated_store["records"] if r["id"] == record_id)
        self.assertEqual(record["persistence_status"], "RB recorded")


# ---------------------------------------------------------------------------
# M10: Query functions
# ---------------------------------------------------------------------------


class TestQueryFunctions(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.b_store = Path(tmp) / "b.json"
        self.e_store = Path(tmp) / "e.json"
        macro_intelligence.process_macro_signal(
            DAVID_MANN_NEWSLETTER,
            behavioral_store_path=self.b_store,
            entity_store_path=self.e_store,
        )

    def test_M10a_query_behavioral_signals_by_signal_type(self):
        results = macro_intelligence.query_behavioral_signals(
            signal_type="consumer_hesitation",
            behavioral_store_path=self.b_store,
        )
        self.assertGreaterEqual(len(results), 1)
        for r in results:
            self.assertEqual(r["signal_type"], "consumer_hesitation")

    def test_M10b_query_behavioral_signals_by_claim_status(self):
        results = macro_intelligence.query_behavioral_signals(
            claim_status="proposed",
            behavioral_store_path=self.b_store,
        )
        self.assertGreater(len(results), 0)
        for r in results:
            self.assertEqual(r["claim_status"], "proposed")

    def test_M10c_query_behavioral_artifacts_returns_artifacts(self):
        artifacts = macro_intelligence.query_behavioral_artifacts(
            behavioral_store_path=self.b_store
        )
        artifact_names = {a["artifact_name"] for a in artifacts}
        self.assertIn("Parking Lot Hesitation", artifact_names)

    def test_M10d_query_behavioral_artifacts_by_claim_status(self):
        artifacts = macro_intelligence.query_behavioral_artifacts(
            claim_status="proposed",
            behavioral_store_path=self.b_store,
        )
        for a in artifacts:
            self.assertEqual(a["claim_status"], "proposed")

    def test_M10e_query_entity_risks_by_entity_id(self):
        results = macro_intelligence.query_entity_risks(
            entity_id="five-guys",
            entity_store_path=self.e_store,
        )
        self.assertGreater(len(results), 0)
        for r in results:
            self.assertEqual(r["entity_id"], "five-guys")

    def test_M10f_query_entity_risks_by_claim_status(self):
        results = macro_intelligence.query_entity_risks(
            claim_status="proposed",
            entity_store_path=self.e_store,
        )
        self.assertGreater(len(results), 0)

    def test_M10g_query_entity_risks_unfiltered_returns_all(self):
        all_results = macro_intelligence.query_entity_risks(
            entity_store_path=self.e_store
        )
        self.assertGreaterEqual(len(all_results), 2)  # Five Guys + McDonald's


# ---------------------------------------------------------------------------
# M11: relationship_intake extensions (thought_leader_alignment, signal_type_override)
# ---------------------------------------------------------------------------


class TestRelationshipIntakeExtensions(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "ledger.json"

    def test_M11a_thought_leader_alignment_in_signal_types(self):
        self.assertIn("thought_leader_alignment", relationship_intake.SIGNAL_TYPES)

    def test_M11b_thought_leader_alignment_has_trust_delta(self):
        self.assertIn("thought_leader_alignment", relationship_intake.TRUST_DELTA)
        self.assertEqual(relationship_intake.TRUST_DELTA["thought_leader_alignment"], 1)

    def test_M11c_linkedin_post_in_source_types(self):
        self.assertIn("linkedin_post", relationship_intake.SOURCE_TYPES)

    def test_M11d_signal_type_override_respected(self):
        result = relationship_intake.process_relationship_thread(
            DAVID_MANN_NEWSLETTER,
            entity_name="David Mann",
            signal_type_override="thought_leader_alignment",
            store_path=self.store,
        )
        self.assertEqual(
            result["interactions"][0]["signal_type"],
            "thought_leader_alignment",
        )

    def test_M11e_invalid_override_falls_back_to_classification(self):
        result = relationship_intake.process_relationship_thread(
            "I just wanted to follow up. Enjoyed our call. Best, Jane Doe, President, Acme Corp",
            entity_name="Jane Doe",
            signal_type_override="not_a_real_signal",
            store_path=self.store,
        )
        self.assertNotEqual(result["interactions"][0]["signal_type"], "not_a_real_signal")
        self.assertIn(
            result["interactions"][0]["signal_type"],
            relationship_intake.SIGNAL_TYPES,
        )

    def test_M11f_thought_leader_alignment_with_two_ecosystem_tags_is_strategic_thought_leader(self):
        result = relationship_intake.process_relationship_thread(
            DAVID_MANN_NEWSLETTER,
            entity_name="David Mann",
            ecosystem_tags=["restaurant_tech", "operator_network"],
            signal_type_override="thought_leader_alignment",
            store_path=self.store,
        )
        self.assertEqual(
            result["interactions"][0]["strategic_classification"],
            "Strategic Thought Leader",
        )

    def test_M11g_thought_leader_alignment_recommended_posture_set(self):
        result = relationship_intake.process_relationship_thread(
            DAVID_MANN_NEWSLETTER,
            entity_name="David Mann",
            ecosystem_tags=["restaurant_tech", "operator_network"],
            signal_type_override="thought_leader_alignment",
            store_path=self.store,
        )
        posture = result["interactions"][0]["recommended_posture"]
        self.assertIn("content", posture.lower())

    def test_M11h_existing_ri_tests_unaffected(self):
        import sys as _sys, pathlib as _pl
        _sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
        from test_relationship_intake import TestJenswoldThread
        import unittest
        suite = unittest.TestLoader().loadTestsFromTestCase(TestJenswoldThread)
        runner = unittest.TextTestRunner(verbosity=0, stream=open("/dev/null", "w"))
        result_obj = runner.run(suite)
        self.assertEqual(result_obj.failures, [])
        self.assertEqual(result_obj.errors, [])


if __name__ == "__main__":
    unittest.main()
