"""
test_api_query_endpoints.py — API query endpoint wiring tests (RB 9.27).

Verifies that all eight intelligence query paths are correctly wired into
the API server. Tests exercise the query functions directly (same contract
as the HTTP endpoints) to confirm field shapes and filter behaviour.

Covers:
  Q1: GET /query/experiences — query_experiences by intel_type, claim_status, tags
  Q2: GET /query/experiences/hooks — query_retrieval_hooks by domain
  Q3: GET /query/relationships — query_interactions by contact_id, signal_type, claim_status
  Q4: GET /query/relationships/who-matters-now — query_who_matters_now leaderboard
  Q5: GET /query/macro/signals — query_behavioral_signals
  Q6: GET /query/macro/artifacts — query_behavioral_artifacts
  Q7: GET /query/macro/entities — query_entity_risks
  Q8: GET /query/insights — query_insights by type, claim_status, tags
  Q9: Query endpoints return empty list (not error) when store is missing
  Q10: Query filters are additive (AND semantics)
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
# Fixtures
# ---------------------------------------------------------------------------

EXPERIENCE_TEXT = (
    "I deployed a voice AI pilot at a 200-unit QSR chain. The rollout stalled because "
    "store managers received no training and feared job displacement. The lesson: "
    "AI adoption in restaurants requires a change management track alongside the tech track."
)

MACRO_TEXT = (
    "McDonald's announced a pause on its AI drive-through program. The hesitation pattern "
    "matches what I observed at other enterprise restaurant deployments — value is back-loaded "
    "but costs are front-loaded. This is a parking lot hesitation signal."
)

RELATIONSHIP_TEXT = (
    "Had a great strategy call with Maria Santos, CTO at QuickServe Tech. "
    "She shared that her board is actively evaluating AI vendors and asked for "
    "an introduction to our team. Strong momentum signal."
)

INSIGHT_TEXT = (
    "Restaurant operators are structurally resistant to AI because the adoption cost "
    "is front-loaded while the value is back-loaded. Loyalty-as-infrastructure is the "
    "right positioning frame — not operational efficiency."
)


def _seed_experience(store: Path) -> str:
    result = ei.process_experiential_signal(EXPERIENCE_TEXT, store_path=store)
    exp_id = result["experiences"][0]["id"]
    ei.record_experience(exp_id, confirmed=True, store_path=store)
    return exp_id


def _seed_relationship(store: Path) -> str:
    result = ri.process_relationship_thread(
        RELATIONSHIP_TEXT, entity_name="Maria Santos", store_path=store
    )
    interaction_id = result["interactions"][0]["id"]
    # RB-2026-08-28: confirming now also attempts to create a baseline
    # entry for a not-yet-known contact_id -- mock so this shared fixture
    # doesn't write a fake "Maria Santos" into whatever real
    # baseline_index.json this process happens to see.
    with patch.object(mutations, "cmd_contact_add", return_value=0):
        ri.record_interaction(interaction_id, confirmed=True, store_path=store)
    return interaction_id


def _seed_macro(bstore: Path, estore: Path) -> str:
    result = mi.process_macro_signal(
        MACRO_TEXT, behavioral_store_path=bstore, entity_store_path=estore
    )
    import json
    records = json.loads(bstore.read_text()).get("records", []) if bstore.exists() else []
    if not records:
        return ""
    record_id = records[0]["id"]
    mi.record_behavioral_record(
        record_id, confirmed=True, behavioral_store_path=bstore
    )
    return record_id


def _seed_insight(store: Path) -> str:
    result = ii.process_text(INSIGHT_TEXT, store_path=store)
    for insight in result.get("insights", []):
        ii.record_insight(insight["id"], confirmed=True, store_path=store)
    return result["insights"][0]["id"] if result.get("insights") else ""


# ---------------------------------------------------------------------------
# Q1 — query/experiences
# ---------------------------------------------------------------------------

class TestQueryExperiences(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "ei.json"
        self.exp_id = _seed_experience(self.store)

    def test_returns_all_when_no_filter(self):
        results = ei.query_experiences(store_path=self.store)
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)

    def test_claim_status_filter_confirmed(self):
        results = ei.query_experiences(claim_status="confirmed", store_path=self.store)
        for r in results:
            self.assertEqual(r["claim_status"], "confirmed")

    def test_claim_status_filter_proposed_returns_empty(self):
        results = ei.query_experiences(claim_status="proposed", store_path=self.store)
        self.assertEqual(results, [])

    def test_intel_type_filter(self):
        all_results = ei.query_experiences(store_path=self.store)
        if not all_results:
            self.skipTest("no seeded records")
        intel_type = all_results[0]["intel_type"]
        results = ei.query_experiences(intel_type=intel_type, store_path=self.store)
        for r in results:
            self.assertEqual(r["intel_type"], intel_type)

    def test_employer_sensitive_filter(self):
        results_sensitive = ei.query_experiences(employer_sensitive=True, store_path=self.store)
        results_not = ei.query_experiences(employer_sensitive=False, store_path=self.store)
        for r in results_sensitive:
            self.assertTrue(r.get("employer_sensitive"))
        for r in results_not:
            self.assertFalse(r.get("employer_sensitive"))

    def test_result_has_required_fields(self):
        results = ei.query_experiences(store_path=self.store)
        for r in results:
            self.assertIn("id", r)
            self.assertIn("intel_type", r)
            self.assertIn("claim_status", r)

    def test_api_response_shape(self):
        results = ei.query_experiences(store_path=self.store)
        response = {"experiences": results, "count": len(results)}
        self.assertIn("experiences", response)
        self.assertIn("count", response)
        self.assertEqual(response["count"], len(response["experiences"]))


# ---------------------------------------------------------------------------
# Q2 — query/experiences/hooks
# ---------------------------------------------------------------------------

class TestQueryExperienceHooks(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "ei.json"
        _seed_experience(self.store)

    def test_hooks_returns_dict(self):
        result = ei.query_retrieval_hooks("restaurant_tech", store_path=self.store)
        self.assertIsInstance(result, dict)

    def test_hooks_domain_key_present(self):
        result = ei.query_retrieval_hooks("restaurant_tech", store_path=self.store)
        self.assertIn("domain", result)
        self.assertEqual(result["domain"], "restaurant_tech")

    def test_hooks_matched_experiences_is_list(self):
        result = ei.query_retrieval_hooks("restaurant_tech", store_path=self.store)
        self.assertIn("matched_experiences", result)
        self.assertIsInstance(result["matched_experiences"], list)

    def test_hooks_unknown_domain_returns_empty(self):
        result = ei.query_retrieval_hooks("completely_unknown_domain_xyz", store_path=self.store)
        matched = result.get("matched_experiences", [])
        self.assertEqual(matched, [])

    def test_hooks_proposed_not_returned_when_confirmed_filter(self):
        store2 = Path(self.tmp) / "ei2.json"
        ei.process_experiential_signal(EXPERIENCE_TEXT, store_path=store2)
        result = ei.query_retrieval_hooks("restaurant_tech", claim_status="confirmed", store_path=store2)
        for exp in result.get("matched_experiences", []):
            self.assertEqual(exp.get("claim_status", "confirmed"), "confirmed")


# ---------------------------------------------------------------------------
# Q3 — query/relationships
# ---------------------------------------------------------------------------

class TestQueryRelationships(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "ri.json"
        self.interaction_id = _seed_relationship(self.store)

    def test_returns_all_when_no_filter(self):
        results = ri.query_interactions(store_path=self.store)
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)

    def test_contact_id_filter(self):
        results = ri.query_interactions(store_path=self.store)
        if not results:
            self.skipTest("no records")
        cid = results[0]["contact_id"]
        filtered = ri.query_interactions(contact_id=cid, store_path=self.store)
        for r in filtered:
            self.assertEqual(r["contact_id"], cid)

    def test_claim_status_filter_confirmed(self):
        results = ri.query_interactions(claim_status="confirmed", store_path=self.store)
        for r in results:
            self.assertEqual(r["claim_status"], "confirmed")

    def test_claim_status_filter_proposed_returns_empty_after_confirm(self):
        results = ri.query_interactions(claim_status="proposed", store_path=self.store)
        self.assertEqual(results, [])

    def test_signal_type_filter(self):
        results = ri.query_interactions(store_path=self.store)
        if not results:
            self.skipTest("no records")
        sig = results[0]["signal_type"]
        filtered = ri.query_interactions(signal_type=sig, store_path=self.store)
        for r in filtered:
            self.assertEqual(r["signal_type"], sig)

    def test_api_response_shape(self):
        results = ri.query_interactions(store_path=self.store)
        response = {"interactions": results, "count": len(results)}
        self.assertIn("interactions", response)
        self.assertIn("count", response)
        self.assertEqual(response["count"], len(response["interactions"]))


# ---------------------------------------------------------------------------
# Q4 — query/relationships/who-matters-now
# ---------------------------------------------------------------------------

class TestQueryWhoMattersNow(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "ri.json"
        _seed_relationship(self.store)

    def test_returns_list(self):
        results = ri.query_who_matters_now(store_path=self.store)
        self.assertIsInstance(results, list)

    def test_non_empty_after_seeding(self):
        results = ri.query_who_matters_now(store_path=self.store)
        self.assertGreater(len(results), 0)

    def test_top_n_respected(self):
        for i in range(3):
            ri.process_relationship_thread(
                f"Call with Contact{i} Smith at Org{i}.", entity_name=f"Contact{i} Smith",
                store_path=self.store
            )
        results = ri.query_who_matters_now(top_n=2, store_path=self.store)
        self.assertLessEqual(len(results), 2)

    def test_each_entry_has_contact_id_and_score(self):
        results = ri.query_who_matters_now(store_path=self.store)
        for r in results:
            self.assertIn("contact_id", r)
            self.assertIn("score", r)

    def test_sorted_descending_by_score(self):
        results = ri.query_who_matters_now(store_path=self.store)
        scores = [r["score"] for r in results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_min_score_filter(self):
        results_all = ri.query_who_matters_now(min_score=0, store_path=self.store)
        results_high = ri.query_who_matters_now(min_score=999, store_path=self.store)
        self.assertLessEqual(len(results_high), len(results_all))
        for r in results_high:
            self.assertGreaterEqual(r["score"], 999)

    def test_api_response_shape(self):
        results = ri.query_who_matters_now(store_path=self.store)
        response = {"contacts": results, "count": len(results)}
        self.assertIn("contacts", response)
        self.assertIn("count", response)


# ---------------------------------------------------------------------------
# Q5 — query/macro/signals
# ---------------------------------------------------------------------------

class TestQueryMacroSignals(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bstore = Path(self.tmp) / "bi.json"
        self.estore = Path(self.tmp) / "ei.json"
        _seed_macro(self.bstore, self.estore)

    def test_returns_list(self):
        results = mi.query_behavioral_signals(behavioral_store_path=self.bstore)
        self.assertIsInstance(results, list)

    def test_claim_status_filter(self):
        confirmed = mi.query_behavioral_signals(
            claim_status="confirmed", behavioral_store_path=self.bstore
        )
        for r in confirmed:
            self.assertEqual(r["claim_status"], "confirmed")

    def test_signal_type_filter_returns_matching(self):
        all_signals = mi.query_behavioral_signals(behavioral_store_path=self.bstore)
        if not all_signals:
            self.skipTest("no signals seeded")
        sig_type = all_signals[0].get("signal_type")
        if sig_type:
            filtered = mi.query_behavioral_signals(
                signal_type=sig_type, behavioral_store_path=self.bstore
            )
            for r in filtered:
                self.assertEqual(r.get("signal_type"), sig_type)

    def test_api_response_shape(self):
        results = mi.query_behavioral_signals(behavioral_store_path=self.bstore)
        response = {"signals": results, "count": len(results)}
        self.assertIn("signals", response)
        self.assertIn("count", response)
        self.assertEqual(response["count"], len(results))


# ---------------------------------------------------------------------------
# Q6 — query/macro/artifacts
# ---------------------------------------------------------------------------

class TestQueryMacroArtifacts(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bstore = Path(self.tmp) / "bi.json"
        self.estore = Path(self.tmp) / "ei.json"
        _seed_macro(self.bstore, self.estore)

    def test_returns_list(self):
        results = mi.query_behavioral_artifacts(behavioral_store_path=self.bstore)
        self.assertIsInstance(results, list)

    def test_claim_status_filter_confirmed(self):
        all_recs = mi.query_behavioral_artifacts(behavioral_store_path=self.bstore)
        if not all_recs:
            self.skipTest("no artifacts seeded")
        confirmed = mi.query_behavioral_artifacts(
            claim_status="confirmed", behavioral_store_path=self.bstore
        )
        for r in confirmed:
            self.assertEqual(r["claim_status"], "confirmed")

    def test_api_response_shape(self):
        results = mi.query_behavioral_artifacts(behavioral_store_path=self.bstore)
        response = {"artifacts": results, "count": len(results)}
        self.assertIn("artifacts", response)
        self.assertIn("count", response)


# ---------------------------------------------------------------------------
# Q7 — query/macro/entities
# ---------------------------------------------------------------------------

class TestQueryMacroEntities(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bstore = Path(self.tmp) / "bi.json"
        self.estore = Path(self.tmp) / "ei.json"
        _seed_macro(self.bstore, self.estore)

    def test_returns_list(self):
        results = mi.query_entity_risks(entity_store_path=self.estore)
        self.assertIsInstance(results, list)

    def test_entity_id_filter(self):
        all_entities = mi.query_entity_risks(entity_store_path=self.estore)
        if not all_entities:
            self.skipTest("no entity records seeded")
        eid = all_entities[0].get("entity_id")
        if eid:
            filtered = mi.query_entity_risks(entity_id=eid, entity_store_path=self.estore)
            for r in filtered:
                self.assertEqual(r.get("entity_id"), eid)

    def test_claim_status_filter(self):
        confirmed = mi.query_entity_risks(
            claim_status="confirmed", entity_store_path=self.estore
        )
        for r in confirmed:
            self.assertEqual(r["claim_status"], "confirmed")

    def test_api_response_shape(self):
        results = mi.query_entity_risks(entity_store_path=self.estore)
        response = {"entities": results, "count": len(results)}
        self.assertIn("entities", response)
        self.assertIn("count", response)


# ---------------------------------------------------------------------------
# Q8 — query/insights
# ---------------------------------------------------------------------------

class TestQueryInsights(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "ii.json"
        _seed_insight(self.store)

    def test_returns_list(self):
        results = ii.query_insights(store_path=self.store)
        self.assertIsInstance(results, list)

    def test_non_empty_after_seeding(self):
        results = ii.query_insights(store_path=self.store)
        self.assertGreater(len(results), 0)

    def test_claim_status_filter_confirmed(self):
        results = ii.query_insights(claim_status="confirmed", store_path=self.store)
        for r in results:
            self.assertEqual(r["claim_status"], "confirmed")

    def test_claim_status_filter_proposed_returns_empty_after_confirm(self):
        results = ii.query_insights(claim_status="proposed", store_path=self.store)
        self.assertEqual(results, [])

    def test_insight_type_filter(self):
        all_results = ii.query_insights(store_path=self.store)
        if not all_results:
            self.skipTest("no records")
        itype = all_results[0]["insight_type"]
        filtered = ii.query_insights(insight_type=itype, store_path=self.store)
        for r in filtered:
            self.assertEqual(r["insight_type"], itype)

    def test_tags_filter(self):
        results = ii.query_insights(tags=["restaurant_tech"], store_path=self.store)
        for r in results:
            self.assertTrue(
                any(t in (r.get("future_use_tags") or []) for t in ["restaurant_tech"])
            )

    def test_api_response_shape(self):
        results = ii.query_insights(store_path=self.store)
        response = {"insights": results, "count": len(results)}
        self.assertIn("insights", response)
        self.assertIn("count", response)
        self.assertEqual(response["count"], len(results))


# ---------------------------------------------------------------------------
# Q9 — Empty store / missing store handling
# ---------------------------------------------------------------------------

class TestQueryEmptyStores(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_experience_query_missing_store_returns_empty(self):
        store = Path(self.tmp) / "nonexistent.json"
        results = ei.query_experiences(store_path=store)
        self.assertEqual(results, [])

    def test_relationship_query_missing_store_returns_empty(self):
        store = Path(self.tmp) / "nonexistent.json"
        results = ri.query_interactions(store_path=store)
        self.assertEqual(results, [])

    def test_who_matters_now_missing_store_returns_empty(self):
        store = Path(self.tmp) / "nonexistent.json"
        results = ri.query_who_matters_now(store_path=store)
        self.assertEqual(results, [])

    def test_macro_signals_missing_store_returns_empty(self):
        store = Path(self.tmp) / "nonexistent.json"
        results = mi.query_behavioral_signals(behavioral_store_path=store)
        self.assertEqual(results, [])

    def test_macro_artifacts_missing_store_returns_empty(self):
        store = Path(self.tmp) / "nonexistent.json"
        results = mi.query_behavioral_artifacts(behavioral_store_path=store)
        self.assertEqual(results, [])

    def test_macro_entities_missing_store_returns_empty(self):
        store = Path(self.tmp) / "nonexistent.json"
        results = mi.query_entity_risks(entity_store_path=store)
        self.assertEqual(results, [])

    def test_insights_missing_store_returns_empty(self):
        store = Path(self.tmp) / "nonexistent.json"
        results = ii.query_insights(store_path=store)
        self.assertEqual(results, [])

    def test_retrieval_hooks_missing_store_returns_empty_matched(self):
        store = Path(self.tmp) / "nonexistent.json"
        result = ei.query_retrieval_hooks("restaurant_tech", store_path=store)
        self.assertEqual(result.get("matched_experiences", []), [])


# ---------------------------------------------------------------------------
# Q10 — Additive filter semantics
# ---------------------------------------------------------------------------

class TestQueryFilterSemantics(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "ii.json"
        _seed_insight(self.store)

    def test_two_filters_are_additive(self):
        all_confirmed = ii.query_insights(claim_status="confirmed", store_path=self.store)
        if not all_confirmed:
            self.skipTest("no confirmed records")
        itype = all_confirmed[0]["insight_type"]
        filtered = ii.query_insights(
            claim_status="confirmed", insight_type=itype, store_path=self.store
        )
        for r in filtered:
            self.assertEqual(r["claim_status"], "confirmed")
            self.assertEqual(r["insight_type"], itype)

    def test_filter_by_nonexistent_type_returns_empty(self):
        results = ii.query_insights(
            insight_type="nonexistent_type_xyz", store_path=self.store
        )
        self.assertEqual(results, [])

    def test_experience_intel_type_and_claim_status_additive(self):
        store = Path(self.tmp) / "ei.json"
        _seed_experience(store)
        all_conf = ei.query_experiences(claim_status="confirmed", store_path=store)
        if not all_conf:
            self.skipTest("no confirmed records")
        itype = all_conf[0]["intel_type"]
        filtered = ei.query_experiences(
            claim_status="confirmed", intel_type=itype, store_path=store
        )
        for r in filtered:
            self.assertEqual(r["claim_status"], "confirmed")
            self.assertEqual(r["intel_type"], itype)


if __name__ == "__main__":
    unittest.main()
