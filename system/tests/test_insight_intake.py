"""
test_insight_intake.py — Mutation-proof insight intake tests (DEFECT-009, DEFECT-010).

Tests:
  I1a: process_text returns insights list
  I1b: each insight has required fields (id, insight_type, claim, persistence_status,
        future_use_tags, proposed_mutations)
  I1c: all mutations require confirmation — guardrail test
  I1d: new insights start as pending confirmation / proposed
  I1e: empty input returns graceful result with persistence_status=RB did not persist

  T10a: Toast / Regulars Report scenario extracts at least one insight
  T10b: industry_trend or market_signal detected
  T10c: vendor_positioning detected
  T10d: thought_leadership_theme detected
  T10e: industry_graph mutation proposed
  T10f: user_positioning mutation proposed
  T10g: thought_leadership reuse tag present
  T10h: restaurant_tech domain tag present
  T10i: persistence_status is not None
  T10j: insights retrievable by restaurant_tech tag after processing
  T10k: retrieval by insight type works for every detected type
  T10l: record_insight confirms → RB recorded
  T10m: record_insight rejects → RB skipped

  I9a: social_signal source type is preserved in insight record
  I9b: social signal with alignment text generates relationship_implication
         or thought_leadership_theme insight
  I9c: narrative convergence count increases with confirmed insights
"""
from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import insight_intake

# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #

TOAST_REGULARS_TEXT = (
    "The Toast Regulars Report reveals that restaurant retention economics are "
    "fundamentally about customer frequency, not just revenue per visit. "
    "A restaurant's top 10 percent of customers visit 3 to 5 times more often "
    "than average customers. "
    "This data shows that retention metrics should prioritize visit frequency "
    "over average check size. "
    "The implication for restaurant technology vendors is that loyalty solutions "
    "should not be positioned as marketing tools. "
    "They should be reframed as operational retention infrastructure — the same "
    "category as labor scheduling and inventory management. "
    "This reframe shifts the vendor's competitive positioning from 'nice to have' "
    "to 'operational necessity.' "
    "There is a thought leadership opportunity here: the thesis that retention "
    "in restaurants is an operational problem, not a marketing problem. "
    "This perspective is contrarian to how most loyalty platforms position themselves."
)

BOB_GIBSON_SOCIAL_SIGNAL = (
    "Bob Gibson at Toast posted about retention as an operational challenge — "
    "this aligns with the thesis that loyalty is infrastructure, not marketing. "
    "This is worth sharing with Bob and relevant to the Toast thread."
)

GENERIC_ENCOURAGEMENT_TEXT = (
    "Things look good today. Keep up the momentum. "
    "There are lots of promising signals and you should stay consistent."
)


# --------------------------------------------------------------------------- #
# Core invariant tests                                                         #
# --------------------------------------------------------------------------- #

class TestProcessTextInvariants(unittest.TestCase):

    def test_returns_dict(self):
        """I1a: process_text returns a dict."""
        with tempfile.TemporaryDirectory() as td:
            result = insight_intake.process_text(
                "Restaurants are changing how they think about customer retention.",
                store_path=Path(td) / "sm.json",
            )
            self.assertIsInstance(result, dict)

    def test_returns_insights_list(self):
        """I1a: result has an insights list."""
        with tempfile.TemporaryDirectory() as td:
            result = insight_intake.process_text(
                "Restaurant technology vendors are shifting their platform positioning.",
                store_path=Path(td) / "sm.json",
            )
            self.assertIn("insights", result)
            self.assertIsInstance(result["insights"], list)

    def test_has_persistence_status(self):
        """I1b: result has persistence_status key."""
        with tempfile.TemporaryDirectory() as td:
            result = insight_intake.process_text(
                "Restaurant retention economics favor frequency over revenue.",
                store_path=Path(td) / "sm.json",
            )
            self.assertIn("persistence_status", result)

    def test_has_mutation_proposals(self):
        """I1b: result has mutation_proposals list."""
        with tempfile.TemporaryDirectory() as td:
            result = insight_intake.process_text(
                "Restaurant retention economics favor frequency over revenue.",
                store_path=Path(td) / "sm.json",
            )
            self.assertIn("mutation_proposals", result)
            self.assertIsInstance(result["mutation_proposals"], list)

    def test_empty_input_returns_graceful_result(self):
        """I1e: empty input does not raise, returns RB did not persist."""
        with tempfile.TemporaryDirectory() as td:
            result = insight_intake.process_text("", store_path=Path(td) / "sm.json")
            self.assertEqual(result["insights"], [])
            self.assertEqual(result["persistence_status"], "RB did not persist")

    def test_whitespace_only_input_returns_graceful_result(self):
        """I1e: whitespace input does not raise."""
        with tempfile.TemporaryDirectory() as td:
            result = insight_intake.process_text("   \n  ", store_path=Path(td) / "sm.json")
            self.assertEqual(result["insights"], [])

    def test_each_insight_has_id(self):
        """I1b: each insight has a non-empty id."""
        with tempfile.TemporaryDirectory() as td:
            result = insight_intake.process_text(
                TOAST_REGULARS_TEXT, store_path=Path(td) / "sm.json"
            )
            for insight in result["insights"]:
                self.assertIn("id", insight)
                self.assertTrue(insight["id"], "Insight id must be non-empty")

    def test_each_insight_has_valid_type(self):
        """I1b: each insight has insight_type in the allowed set."""
        with tempfile.TemporaryDirectory() as td:
            result = insight_intake.process_text(
                TOAST_REGULARS_TEXT, store_path=Path(td) / "sm.json"
            )
            for insight in result["insights"]:
                self.assertIn(
                    insight.get("insight_type"),
                    insight_intake.INSIGHT_TYPES,
                    f"Unknown insight_type: {insight.get('insight_type')}",
                )

    def test_each_insight_has_claim(self):
        """I1b: each insight has a non-empty claim."""
        with tempfile.TemporaryDirectory() as td:
            result = insight_intake.process_text(
                TOAST_REGULARS_TEXT, store_path=Path(td) / "sm.json"
            )
            for insight in result["insights"]:
                self.assertTrue(insight.get("claim"), "Insight claim must be non-empty")

    def test_each_insight_has_persistence_status(self):
        """I1b: each insight has a non-None persistence_status."""
        with tempfile.TemporaryDirectory() as td:
            result = insight_intake.process_text(
                TOAST_REGULARS_TEXT, store_path=Path(td) / "sm.json"
            )
            for insight in result["insights"]:
                self.assertIn("persistence_status", insight)
                self.assertIsNotNone(insight["persistence_status"])

    def test_each_insight_has_future_use_tags(self):
        """I1b: each insight has at least one future_use_tag."""
        with tempfile.TemporaryDirectory() as td:
            result = insight_intake.process_text(
                TOAST_REGULARS_TEXT, store_path=Path(td) / "sm.json"
            )
            for insight in result["insights"]:
                tags = insight.get("future_use_tags") or []
                self.assertGreater(
                    len(tags), 0,
                    f"Insight {insight.get('insight_type')} has no future_use_tags",
                )

    def test_all_mutations_require_confirmation(self):
        """I1c: no mutation may auto-apply — every mutation requires confirmation."""
        with tempfile.TemporaryDirectory() as td:
            result = insight_intake.process_text(
                TOAST_REGULARS_TEXT, store_path=Path(td) / "sm.json"
            )
            for mutation in result["mutation_proposals"]:
                self.assertTrue(
                    mutation.get("requires_confirmation") is True,
                    f"Mutation '{mutation.get('mutation_type')}' does not require confirmation. "
                    "No auto-mutations are permitted.",
                )

    def test_new_insights_start_as_pending_confirmation(self):
        """I1d: new insights have persistence_status=pending confirmation and claim_status=proposed."""
        with tempfile.TemporaryDirectory() as td:
            result = insight_intake.process_text(
                TOAST_REGULARS_TEXT, store_path=Path(td) / "sm.json"
            )
            for insight in result["insights"]:
                self.assertEqual(insight["persistence_status"], "pending confirmation")
                self.assertEqual(insight["claim_status"], "proposed")


# --------------------------------------------------------------------------- #
# Toast / Regulars Report scenario (DEFECT-010)                               #
# --------------------------------------------------------------------------- #

class TestToastReguarsReportScenario(unittest.TestCase):
    """
    The Toast / Regulars Report retention-economics fixture.

    This test class FAILS if RB returns conversational analysis only — without:
      - extracted durable insights
      - proposed graph mutations
      - proposed user-positioning mutations
      - thought-leadership reuse tags
      - persistence_status
      - retrieval proof
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = Path(self._tmpdir.name) / "sm.json"
        self.result = insight_intake.process_text(
            TOAST_REGULARS_TEXT,
            source_type="conversation",
            store_path=self.store,
        )

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_extracts_at_least_one_insight(self):
        """T10a: result must contain at least one durable insight."""
        self.assertGreater(
            len(self.result["insights"]), 0,
            "RB returned no durable insights from Toast / Regulars Report analysis. "
            "Insight died as prose.",
        )

    def test_extracts_industry_trend_or_market_signal(self):
        """T10b: retention economics signal must be classified."""
        types = {i["insight_type"] for i in self.result["insights"]}
        self.assertTrue(
            types & {"industry_trend", "market_signal"},
            f"No industry_trend or market_signal detected. Found types: {types}",
        )

    def test_extracts_vendor_positioning(self):
        """T10c: loyalty-as-infrastructure reframe must be classified as vendor_positioning."""
        types = {i["insight_type"] for i in self.result["insights"]}
        self.assertIn(
            "vendor_positioning",
            types,
            f"vendor_positioning not detected. Found types: {types}",
        )

    def test_extracts_thought_leadership_theme(self):
        """T10d: contrarian thesis must be classified as thought_leadership_theme."""
        types = {i["insight_type"] for i in self.result["insights"]}
        self.assertIn(
            "thought_leadership_theme",
            types,
            f"thought_leadership_theme not detected. Found types: {types}",
        )

    def test_has_proposed_industry_graph_mutations(self):
        """T10e: at least one industry_graph mutation must be proposed."""
        graph_mutations = [
            m for m in self.result["mutation_proposals"]
            if m.get("mutation_type") == "industry_graph"
        ]
        self.assertGreater(
            len(graph_mutations), 0,
            "No industry_graph mutation proposed. "
            "Retention economics insight died as prose without graph mutation.",
        )

    def test_has_proposed_user_positioning_mutations(self):
        """T10f: at least one user_positioning mutation must be proposed."""
        positioning_mutations = [
            m for m in self.result["mutation_proposals"]
            if m.get("mutation_type") == "user_positioning"
        ]
        self.assertGreater(
            len(positioning_mutations), 0,
            "No user_positioning mutation proposed. "
            "Vendor positioning insight died as prose without positioning mutation.",
        )

    def test_has_thought_leadership_reuse_tag(self):
        """T10g: thought_leadership must appear in retrieval_tags."""
        all_tags = self.result.get("retrieval_tags", [])
        self.assertIn(
            "thought_leadership",
            all_tags,
            f"No thought_leadership tag in retrieval_tags. Tags: {all_tags}",
        )

    def test_has_restaurant_tech_domain_tag(self):
        """T10h: restaurant_tech domain tag must be present."""
        all_tags = self.result.get("retrieval_tags", [])
        self.assertIn(
            "restaurant_tech",
            all_tags,
            f"No restaurant_tech tag. Tags: {all_tags}",
        )

    def test_persistence_status_is_not_none(self):
        """T10i: top-level persistence_status must not be None."""
        self.assertIsNotNone(self.result.get("persistence_status"))

    def test_retrieval_by_restaurant_tech_tag(self):
        """T10j: insights must be retrievable by restaurant_tech tag after processing."""
        retrieved = insight_intake.query_insights(
            tags=["restaurant_tech"],
            store_path=self.store,
        )
        self.assertGreater(
            len(retrieved), 0,
            "Insights not retrievable by restaurant_tech tag. "
            "Persistence proof failed.",
        )

    def test_retrieval_by_each_detected_type(self):
        """T10k: each detected insight type must be queryable from the store."""
        detected_types = {i["insight_type"] for i in self.result["insights"]}
        for t in detected_types:
            retrieved = insight_intake.query_insights(insight_type=t, store_path=self.store)
            self.assertGreater(
                len(retrieved), 0,
                f"Insight type '{t}' not retrievable from strategic memory after processing.",
            )

    def test_confirm_changes_persistence_status_to_recorded(self):
        """T10l: confirming an insight updates persistence_status to RB recorded."""
        first_id = self.result["insights"][0]["id"]
        updated = insight_intake.record_insight(first_id, confirmed=True, store_path=self.store)
        self.assertEqual(
            updated["persistence_status"], "RB recorded",
            f"Expected 'RB recorded', got '{updated.get('persistence_status')}'",
        )
        self.assertEqual(updated["claim_status"], "confirmed")

    def test_reject_changes_persistence_status_to_skipped(self):
        """T10m: rejecting an insight updates persistence_status to RB skipped."""
        first_id = self.result["insights"][0]["id"]
        updated = insight_intake.record_insight(first_id, confirmed=False, store_path=self.store)
        self.assertEqual(
            updated["persistence_status"], "RB skipped",
            f"Expected 'RB skipped', got '{updated.get('persistence_status')}'",
        )


# --------------------------------------------------------------------------- #
# Social signal source (DEFECT-009)                                            #
# --------------------------------------------------------------------------- #

class TestSocialSignalSource(unittest.TestCase):

    def test_social_signal_source_type_preserved(self):
        """I9a: social_signal source_type is stored in each insight record."""
        with tempfile.TemporaryDirectory() as td:
            result = insight_intake.process_text(
                BOB_GIBSON_SOCIAL_SIGNAL,
                source_type="social_signal",
                store_path=Path(td) / "sm.json",
            )
            for insight in result["insights"]:
                self.assertEqual(
                    insight["source_type"], "social_signal",
                    "source_type not preserved in insight record",
                )

    def test_alignment_signal_generates_relationship_or_thought_leadership_insight(self):
        """I9b: social signal containing alignment text generates rel implication or TL theme."""
        with tempfile.TemporaryDirectory() as td:
            result = insight_intake.process_text(
                BOB_GIBSON_SOCIAL_SIGNAL,
                source_type="social_signal",
                store_path=Path(td) / "sm.json",
            )
            types = {i["insight_type"] for i in result["insights"]}
            self.assertTrue(
                types & {"relationship_implication", "thought_leadership_theme"},
                f"No relationship_implication or thought_leadership_theme in {types}",
            )

    def test_narrative_convergence_increases_with_confirmed_insights(self):
        """I9c: narrative convergence count increases as insights are confirmed."""
        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "sm.json"

            # Before: no confirmed insights
            pre = insight_intake.query_narrative_convergence(
                ["retention_economics"], store_path=store
            )
            self.assertEqual(pre["confirmed_count"], 0)

            # Add and confirm two insights
            r1 = insight_intake.process_text(
                TOAST_REGULARS_TEXT, source_type="conversation", store_path=store
            )
            r2 = insight_intake.process_text(
                BOB_GIBSON_SOCIAL_SIGNAL, source_type="social_signal", store_path=store
            )
            retention_insights = insight_intake.query_insights(
                tags=["retention_economics"], store_path=store
            )
            for ins in retention_insights[:2]:
                insight_intake.record_insight(ins["id"], confirmed=True, store_path=store)

            post = insight_intake.query_narrative_convergence(
                ["retention_economics"], store_path=store
            )
            self.assertGreaterEqual(
                post["confirmed_count"], 1,
                "Confirmed count should increase after confirming retention_economics insights",
            )

    def test_narrative_convergence_returns_required_fields(self):
        """I9c: query_narrative_convergence returns structured result."""
        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "sm.json"
            result = insight_intake.query_narrative_convergence(
                ["restaurant_tech", "retention_economics"], store_path=store
            )
            for field in ("tag_query", "confirmed_count", "pending_count",
                          "total_count", "convergence_confidence"):
                self.assertIn(field, result, f"Missing field: {field}")


# --------------------------------------------------------------------------- #
# Query and storage                                                            #
# --------------------------------------------------------------------------- #

class TestQueryAndStorage(unittest.TestCase):

    def test_query_by_type_returns_only_matching_type(self):
        """Querying by type returns only insights of that type."""
        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "sm.json"
            insight_intake.process_text(TOAST_REGULARS_TEXT, store_path=store)
            industry = insight_intake.query_insights(
                insight_type="industry_trend", store_path=store
            )
            for ins in industry:
                self.assertEqual(ins["insight_type"], "industry_trend")

    def test_query_by_claim_status(self):
        """Querying by claim_status=proposed returns only proposed insights."""
        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "sm.json"
            result = insight_intake.process_text(TOAST_REGULARS_TEXT, store_path=store)
            proposed = insight_intake.query_insights(claim_status="proposed", store_path=store)
            self.assertEqual(len(proposed), len(result["insights"]))

    def test_insights_persist_across_calls(self):
        """Insights from two separate process_text calls both appear in the store."""
        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "sm.json"
            r1 = insight_intake.process_text(TOAST_REGULARS_TEXT, store_path=store)
            r2 = insight_intake.process_text(
                "Restaurant technology vendors are competing on retention infrastructure.",
                store_path=store,
            )
            all_insights = insight_intake.query_insights(store_path=store)
            expected = len(r1["insights"]) + len(r2["insights"])
            self.assertEqual(
                len(all_insights), expected,
                "Insights from both calls should be present in the store",
            )

    def test_record_insight_unknown_id_returns_error(self):
        """record_insight with unknown ID returns error dict, not exception."""
        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "sm.json"
            result = insight_intake.record_insight("no-such-id", confirmed=True, store_path=store)
            self.assertIn("error", result)

    def test_strategic_memory_json_is_valid(self):
        """Strategic memory file written by process_text is valid JSON with schema_version."""
        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "sm.json"
            insight_intake.process_text(TOAST_REGULARS_TEXT, store_path=store)
            raw = json.loads(store.read_text())
            self.assertIn("_schema_version", raw)
            self.assertIn("insights", raw)
            self.assertIsInstance(raw["insights"], list)


if __name__ == "__main__":
    unittest.main()
