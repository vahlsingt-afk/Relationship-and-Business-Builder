"""
test_opportunity_pipeline.py — RB-DEFECT-037

Unit tests for opportunity_pipeline.py (the Active Opportunity Pipeline
persistence module) and integration tests for its two API endpoints
(processOpportunityUpdate, getOpportunityPipeline).

Test groups:
  OP1 (5): helper functions — _classify_stage, _extract_company,
           _extract_candidate_position, _extract_key_dates,
           _opportunity_id_from_company
  OP2 (6): process_opportunity_update — empty input, no-signal, no-company,
           new opportunity, existing-opportunity update with what_changed,
           apply=True persistence
  OP3 (3): query_pipeline — active_only filtering, sort order, contract
  OP4 (2): recent_changes — within-window filtering
  OP5 (2): API integration — POST /opportunity/update, GET /opportunity/pipeline
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "system" / "api"))

import opportunity_pipeline as op  # noqa: E402

try:
    from fastapi.testclient import TestClient
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False


# ---------------------------------------------------------------------------
# OP1 — helper functions
# ---------------------------------------------------------------------------

class TestOP1_Helpers(unittest.TestCase):
    def test_OP1a_classify_stage_verbal_offer(self):
        self.assertEqual(
            op._classify_stage("I received a verbal offer from Acme."),
            "offer_verbal",
        )

    def test_OP1b_classify_stage_final_round(self):
        self.assertEqual(
            op._classify_stage("They confirmed I'm one of the final two candidates."),
            "final_round",
        )

    def test_OP1c_classify_stage_none_for_unrelated_text(self):
        self.assertIsNone(op._classify_stage("Had a nice lunch today."))

    def test_OP1d_extract_company_known_companies_first(self):
        company = op._extract_company(
            "Got an update from Acme and from Foods Connected today.",
            known_companies=["Foods Connected"],
        )
        self.assertEqual(company, "Foods Connected")

    def test_OP1e_extract_company_from_pattern(self):
        company = op._extract_company("I received a verbal offer from Global Payments.")
        self.assertEqual(company, "Global Payments")

    def test_OP1f_extract_candidate_position(self):
        pos = op._extract_candidate_position(
            "Foods Connected confirmed I'm one of the final two candidates."
        )
        self.assertIsNotNone(pos)
        self.assertIn("final two candidates", pos.lower())

    def test_OP1g_extract_key_dates(self):
        dates = op._extract_key_dates(
            "Final interviews scheduled June 23-24."
        )
        self.assertTrue(dates)
        self.assertEqual(dates[0]["date_text"], "June 23-24")

    def test_OP1h_opportunity_id_from_company(self):
        self.assertEqual(op._opportunity_id_from_company("Global Payments"), "global-payments")
        self.assertEqual(op._opportunity_id_from_company("Foods Connected, Inc."), "foods-connected-inc")


# ---------------------------------------------------------------------------
# OP2 — process_opportunity_update
# ---------------------------------------------------------------------------

class TestOP2_ProcessOpportunityUpdate(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self._tmpdir.name) / "tracked_opportunities.json"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_OP2a_empty_text_not_persisted(self):
        result = op.process_opportunity_update("", store_path=self.store_path)
        self.assertFalse(result["detected"])
        self.assertEqual(result["persistence_status"], "RB did not persist")
        self.assertFalse(self.store_path.exists())

    def test_OP2b_no_signal_not_persisted(self):
        result = op.process_opportunity_update("Had a nice lunch today.", store_path=self.store_path)
        self.assertFalse(result["detected"])
        self.assertEqual(result["persistence_status"], "RB did not persist")

    def test_OP2c_stage_without_company_not_persisted(self):
        result = op.process_opportunity_update(
            "I received a verbal offer.", store_path=self.store_path
        )
        self.assertTrue(result["detected"])
        self.assertIsNone(result["opportunity"])
        self.assertEqual(result["persistence_status"], "RB did not persist")

    def test_OP2d_new_opportunity_pending_confirmation(self):
        result = op.process_opportunity_update(
            "I received a verbal offer from Global Payments and the offer "
            "package is under evaluation.",
            company="Global Payments",
            store_path=self.store_path,
        )
        self.assertTrue(result["detected"])
        self.assertEqual(result["opportunity"]["company"], "Global Payments")
        self.assertEqual(result["opportunity"]["stage"], "offer_verbal")
        self.assertEqual(result["persistence_status"], "pending confirmation")
        self.assertIn("New tracked opportunity", result["what_changed"])
        self.assertFalse(self.store_path.exists())

    def test_OP2e_apply_true_persists_to_store(self):
        result = op.process_opportunity_update(
            "I received a verbal offer from Global Payments.",
            company="Global Payments",
            apply=True,
            store_path=self.store_path,
        )
        self.assertEqual(result["persistence_status"], "applied")
        self.assertTrue(self.store_path.exists())
        store = json.loads(self.store_path.read_text())
        self.assertEqual(len(store["opportunities"]), 1)
        self.assertEqual(store["opportunities"][0]["company"], "Global Payments")

    def test_OP2f_existing_opportunity_update_records_what_changed(self):
        op.process_opportunity_update(
            "I received a verbal offer from Global Payments.",
            company="Global Payments",
            apply=True,
            store_path=self.store_path,
        )
        result = op.process_opportunity_update(
            "Negotiating compensation package with Global Payments now.",
            company="Global Payments",
            apply=True,
            store_path=self.store_path,
        )
        self.assertEqual(result["opportunity"]["stage"], "negotiating")
        self.assertIn("Stage", result["what_changed"])
        store = json.loads(self.store_path.read_text())
        self.assertEqual(len(store["opportunities"]), 1)
        self.assertEqual(len(store["opportunities"][0]["history"]), 2)


# ---------------------------------------------------------------------------
# OP3 — query_pipeline
# ---------------------------------------------------------------------------

class TestOP3_QueryPipeline(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self._tmpdir.name) / "tracked_opportunities.json"
        op.process_opportunity_update(
            "I received a verbal offer from Global Payments.",
            company="Global Payments", apply=True, store_path=self.store_path,
        )
        op.process_opportunity_update(
            "I declined the offer from Acme.",
            company="Acme", apply=True, store_path=self.store_path,
        )

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_OP3a_active_only_excludes_terminal_stages(self):
        result = op.query_pipeline(active_only=True, store_path=self.store_path)
        companies = {o["company"] for o in result["opportunities"]}
        self.assertIn("Global Payments", companies)
        self.assertNotIn("Acme", companies)

    def test_OP3b_active_only_false_includes_all(self):
        result = op.query_pipeline(active_only=False, store_path=self.store_path)
        companies = {o["company"] for o in result["opportunities"]}
        self.assertIn("Global Payments", companies)
        self.assertIn("Acme", companies)

    def test_OP3c_contract_and_count(self):
        result = op.query_pipeline(active_only=False, store_path=self.store_path)
        self.assertEqual(result["contract"], "rb_opportunity_pipeline_v1")
        self.assertEqual(result["count"], 2)


# ---------------------------------------------------------------------------
# OP4 — recent_changes
# ---------------------------------------------------------------------------

class TestOP4_RecentChanges(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self._tmpdir.name) / "tracked_opportunities.json"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_OP4a_recent_change_surfaced(self):
        op.process_opportunity_update(
            "I received a verbal offer from Global Payments.",
            company="Global Payments", apply=True, store_path=self.store_path,
        )
        recent = op.recent_changes(within_days=1, store_path=self.store_path)
        self.assertEqual(len(recent), 1)
        self.assertIn("recent_history", recent[0])

    def test_OP4b_no_changes_returns_empty(self):
        recent = op.recent_changes(within_days=1, store_path=self.store_path)
        self.assertEqual(recent, [])


# ---------------------------------------------------------------------------
# OP5 — API integration
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestOP5_APIIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.client = TestClient(server.app)

    def test_OP5a_process_opportunity_update_endpoint(self):
        resp = self.client.post(
            "/opportunity/update",
            json={
                "text": "Foods Connected confirmed I'm one of the final two candidates, "
                        "with final interviews scheduled June 23-24.",
                "company": "Foods Connected",
            },
            headers={"x-api-key": "test-key"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data["detected"])
        self.assertEqual(data["opportunity"]["stage"], "final_round")
        self.assertEqual(data["opportunity"]["company"], "Foods Connected")
        self.assertTrue(data["opportunity"]["key_dates"])

    def test_OP5b_get_opportunity_pipeline_endpoint(self):
        resp = self.client.get("/opportunity/pipeline", headers={"x-api-key": "test-key"})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["contract"], "rb_opportunity_pipeline_v1")
        self.assertIn("opportunities", data)


if __name__ == "__main__":
    unittest.main()
