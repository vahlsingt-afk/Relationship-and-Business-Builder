"""
test_api_rb9_validation.py — HTTP API integration tests for RB 9.27–9.29 endpoints.

Uses FastAPI TestClient to verify the endpoints added in:
  - RB 9.27: GET  /entities/{entity_name}/signals  (getEntitySignals)
  - RB 9.29: POST /earnings/watchlist              (manageEarningsWatchlist)
  - RB 9.31: POST /relationship/intake             (processRelationshipIntake)
  - RB 9.31: POST /macro/signal                    (processMacroSignal)
  - RB 9.32: GET  /earnings/watchlist              (getEarningsWatchlist)

Test groups:
  AA8 (8):  getEntitySignals — response schema, unknown entity, days param, contract field
  AA9 (8):  manageEarningsWatchlist — add/remove/auto_scan preview, confirm guards,
            missing-name error, schema fields
  AA10 (8): processRelationshipIntake — text required, entity extraction, proposals,
            persistence_status, apply=false, signal taxonomy, cos_surface, unknown entity
  AA11 (8): processMacroSignal — text required, behavioral signal detection, tech
            implications, entity risk, cos_surface, persistence_status, unknown input
  AA12 (4): getEarningsWatchlist — company count, field presence, watch_priority filter,
            ticker field
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:
    raise unittest.SkipTest("fastapi not installed — skipping API validation tests")

ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = ROOT / "system" / "api" / "server.py"


def _load_server_module():
    module_name = "rb_api_server_for_rb9_tests"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, SERVER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


_server = _load_server_module()
_client = TestClient(_server.app, raise_server_exceptions=True, headers={"x-api-key": "test-key"})


# =============================================================================
# AA8 — getEntitySignals (GET /entities/{entity_name}/signals)
# =============================================================================

class TestAA8GetEntitySignals(unittest.TestCase):
    """AA8 — getEntitySignals returns a well-formed synthesis response."""

    def test_known_entity_returns_200(self):
        resp = _client.get("/entities/PAR Technology/signals")
        self.assertEqual(resp.status_code, 200)

    def test_unknown_entity_returns_200_not_404(self):
        """Unknown entity should return 200 with unknown pattern, not a 404."""
        resp = _client.get("/entities/NoSuchCompanyXYZ/signals")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn(data.get("dominant_pattern"), ("unknown", "stable"))

    def test_response_has_contract_field(self):
        resp = _client.get("/entities/PAR Technology/signals")
        data = resp.json()
        self.assertEqual(data.get("contract"), "rb_entity_signal_synthesis_v1")

    def test_response_has_required_schema_fields(self):
        resp = _client.get("/entities/PAR Technology/signals")
        data = resp.json()
        for field in (
            "entity", "signal_count", "dominant_pattern", "pattern_confidence",
            "synthesis_hypothesis", "opportunity_or_risk", "active_threads",
            "watch_list_status", "baseline_contacts", "pattern_scores", "signals",
        ):
            self.assertIn(field, data, msg=f"Missing field: {field}")

    def test_signal_count_is_integer(self):
        resp = _client.get("/entities/PAR Technology/signals")
        data = resp.json()
        self.assertIsInstance(data.get("signal_count"), int)

    def test_dominant_pattern_is_valid_taxonomy(self):
        resp = _client.get("/entities/PAR Technology/signals")
        data = resp.json()
        valid = {
            "exit_positioning", "growth_mode", "distress", "consolidation",
            "competitive_shift", "transition", "stable", "unknown",
        }
        self.assertIn(data.get("dominant_pattern"), valid)

    def test_days_param_accepted(self):
        resp = _client.get("/entities/Olo/signals?days=30")
        self.assertEqual(resp.status_code, 200)

    def test_days_param_out_of_range_422(self):
        resp = _client.get("/entities/PAR Technology/signals?days=6")  # below min=7
        self.assertEqual(resp.status_code, 422)


# =============================================================================
# AA9 — manageEarningsWatchlist (POST /earnings/watchlist)
# =============================================================================

class TestAA9ManageEarningsWatchlist(unittest.TestCase):
    """AA9 — manageEarningsWatchlist previews correctly; confirm=true applies mutations."""

    def test_add_preview_returns_200(self):
        resp = _client.post("/earnings/watchlist", json={
            "action": "add",
            "name": "Test Corp Validation",
            "confirm": False,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(
            data.get("persistence_status"), "proposed_write_pending_confirmation"
        )
        self.assertTrue(data.get("proposed"))

    def test_add_missing_name_returns_400(self):
        resp = _client.post("/earnings/watchlist", json={
            "action": "add",
            "confirm": False,
        })
        self.assertEqual(resp.status_code, 400)

    def test_remove_preview_returns_200(self):
        resp = _client.post("/earnings/watchlist", json={
            "action": "remove",
            "name": "Global Payments",
            "confirm": False,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(
            data.get("persistence_status"), "proposed_write_pending_confirmation"
        )

    def test_remove_missing_name_returns_400(self):
        resp = _client.post("/earnings/watchlist", json={
            "action": "remove",
            "confirm": False,
        })
        self.assertEqual(resp.status_code, 400)

    def test_auto_scan_preview_returns_candidates(self):
        resp = _client.post("/earnings/watchlist", json={
            "action": "auto_scan",
            "threshold": 3,
            "confirm": False,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(
            data.get("persistence_status"), "proposed_write_pending_confirmation"
        )
        self.assertIn("candidates", data)
        self.assertIn("candidates_count", data)

    def test_unknown_action_returns_400(self):
        resp = _client.post("/earnings/watchlist", json={
            "action": "invalidaction",
            "confirm": False,
        })
        self.assertEqual(resp.status_code, 422)  # Pydantic enum validation

    def test_response_has_action_field(self):
        resp = _client.post("/earnings/watchlist", json={
            "action": "auto_scan",
            "confirm": False,
        })
        data = resp.json()
        self.assertEqual(data.get("action"), "auto_scan")

    def test_auto_scan_threshold_param(self):
        resp = _client.post("/earnings/watchlist", json={
            "action": "auto_scan",
            "threshold": 5,
            "confirm": False,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("threshold"), 5)


# =============================================================================
# AA10 — processRelationshipIntake (POST /relationship/intake)
# =============================================================================

class TestAA10ProcessRelationshipIntake(unittest.TestCase):
    """AA10 — processRelationshipIntake returns structured CoS mutation surface.

    RB-DEFECT-042 (part 2): /relationship/intake calls
    relationship_intake.process_relationship_thread() without store_path,
    so every test here was appending 'proposed' rows to the real
    interaction_ledger.json. Patch INTERACTION_LEDGER_PATH to a tempfile for
    the duration of this class so the real ledger stays clean.
    """

    def setUp(self):
        import tempfile
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

    def test_basic_intake_returns_200(self):
        resp = _client.post("/relationship/intake", json={
            "text": "Had a great call with Sarah Chen at Olo. She's interested in the pilot.",
            "entity_name": "Sarah Chen",
            "entity_org": "Olo",
        })
        self.assertEqual(resp.status_code, 200)

    def test_empty_text_returns_200_with_note(self):
        resp = _client.post("/relationship/intake", json={
            "text": "",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Empty input should not crash — returns note
        self.assertIn("persistence_status", data)

    def test_response_has_persistence_status(self):
        resp = _client.post("/relationship/intake", json={
            "text": "Bob Gibson confirmed the Toast meeting for next week.",
            "entity_name": "Bob Gibson",
            "entity_org": "Toast",
        })
        data = resp.json()
        self.assertIn("persistence_status", data)

    def test_response_has_required_fields(self):
        resp = _client.post("/relationship/intake", json={
            "text": "Meeting with Oliver Ostertag at PAR went well.",
            "entity_name": "Oliver Ostertag",
            "entity_org": "PAR Technology",
        })
        data = resp.json()
        for field in ("interactions", "mutation_proposals", "persistence_status"):
            self.assertIn(field, data, msg=f"Missing field: {field}")

    def test_mutation_proposals_is_list(self):
        resp = _client.post("/relationship/intake", json={
            "text": "Just spoke with Michael Scott at Dunder Mifflin about the deal.",
            "entity_name": "Michael Scott",
        })
        data = resp.json()
        self.assertIsInstance(data.get("mutation_proposals"), list)

    def test_text_required_missing_returns_422(self):
        resp = _client.post("/relationship/intake", json={
            "entity_name": "Bob",
        })
        self.assertEqual(resp.status_code, 422)

    def test_apply_false_no_write(self):
        """apply=false must not persist anything — persistence_status must not be persisted."""
        resp = _client.post("/relationship/intake", json={
            "text": "Todd met with Anna Jones at Global Payments about partnerships.",
            "entity_name": "Anna Jones",
            "entity_org": "Global Payments",
            "apply": False,
        })
        data = resp.json()
        ps = data.get("persistence_status", "")
        self.assertNotEqual(ps, "persisted", msg="apply=false must not return persisted")

    def test_source_type_param_accepted(self):
        resp = _client.post("/relationship/intake", json={
            "text": "LinkedIn message from Dan Price.",
            "entity_name": "Dan Price",
            "source_type": "linkedin_message",
        })
        self.assertEqual(resp.status_code, 200)


# =============================================================================
# AA11 — processMacroSignal (POST /macro/signal)
# =============================================================================

class TestAA11ProcessMacroSignal(unittest.TestCase):
    """AA11 — processMacroSignal classifies behavioral signals and returns CoS layer.

    RB-DEFECT-042 (part 3): test_author_context_accepted passes author_name
    through /macro/signal, which produces an ri_mutation written via
    relationship_intake.process_relationship_thread() to the real
    interaction_ledger.json. Patch INTERACTION_LEDGER_PATH for this class too.
    """

    def setUp(self):
        import tempfile
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

    def test_basic_signal_returns_200(self):
        resp = _client.post("/macro/signal", json={
            "text": (
                "Consumers are trading down from full-service restaurants. "
                "Value menu visits up 18% as diners feel the affordability squeeze."
            ),
            "source_type": "linkedin_post",
        })
        self.assertEqual(resp.status_code, 200)

    def test_empty_text_returns_200_with_note(self):
        resp = _client.post("/macro/signal", json={"text": ""})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("persistence_status", data)

    def test_text_required_missing_returns_422(self):
        resp = _client.post("/macro/signal", json={"source_type": "linkedin_post"})
        self.assertEqual(resp.status_code, 422)

    def test_response_has_required_fields(self):
        resp = _client.post("/macro/signal", json={
            "text": "Drive-thru friction is at an all-time high. Guests are leaving mid-queue.",
            "source_type": "linkedin_post",
        })
        data = resp.json()
        for field in (
            "behavioral_signals", "tech_implications", "mutation_proposals",
            "persistence_status",
        ):
            self.assertIn(field, data, msg=f"Missing field: {field}")

    def test_behavioral_signals_is_list(self):
        resp = _client.post("/macro/signal", json={
            "text": "Affordability stress is reshaping restaurant traffic. Value is winning.",
        })
        data = resp.json()
        self.assertIsInstance(data.get("behavioral_signals"), list)

    def test_tech_implications_is_list(self):
        resp = _client.post("/macro/signal", json={
            "text": (
                "McDonald's Q1 showed AI drive-thru orders outperforming manual. "
                "Speed of service is back in focus."
            ),
        })
        data = resp.json()
        self.assertIsInstance(data.get("tech_implications"), list)

    def test_no_signal_returns_note(self):
        """Generic text with no behavioral signals should return a note, not crash."""
        resp = _client.post("/macro/signal", json={
            "text": "The weather today is nice.",
            "source_type": "linkedin_post",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("persistence_status", data)

    def test_author_context_accepted(self):
        resp = _client.post("/macro/signal", json={
            "text": "Seeing real affordability stress across the quick-service segment.",
            "source_type": "linkedin_post",
            "author_name": "Sarah Chen",
            "author_org": "Olo",
            "author_role": "VP Sales",
        })
        self.assertEqual(resp.status_code, 200)


# =============================================================================
# AA12 — getEarningsWatchlist (GET /earnings/watchlist)
# =============================================================================

class TestAA12GetEarningsWatchlist(unittest.TestCase):
    """AA12 — getEarningsWatchlist returns the current earnings calendar."""

    def test_returns_200(self):
        resp = _client.get("/earnings/watchlist")
        self.assertEqual(resp.status_code, 200)

    def test_has_companies_list(self):
        resp = _client.get("/earnings/watchlist")
        data = resp.json()
        self.assertIn("companies", data)
        self.assertIsInstance(data["companies"], list)

    def test_company_count_reasonable(self):
        resp = _client.get("/earnings/watchlist")
        data = resp.json()
        count = len(data.get("companies", []))
        self.assertGreaterEqual(count, 1, "Should have at least 1 company")

    def test_watch_only_filter(self):
        resp_all = _client.get("/earnings/watchlist")
        resp_watch = _client.get("/earnings/watchlist?watch_only=true")
        all_data = resp_all.json()
        watch_data = resp_watch.json()
        self.assertLessEqual(
            len(watch_data.get("companies", [])),
            len(all_data.get("companies", [])),
        )


# =============================================================================
# AA13 — getCompanyEarningsHistory (GET /entities/{entity_name}/earnings-history)
# 2026-08-10 feature request: durable cross-quarter earnings record.
# =============================================================================

class TestAA13GetCompanyEarningsHistory(unittest.TestCase):
    def test_returns_200_for_known_and_unknown_entity(self):
        resp = _client.get("/entities/PAR Technology/earnings-history")
        self.assertEqual(resp.status_code, 200)
        resp2 = _client.get("/entities/NoSuchCompanyXYZ/earnings-history")
        self.assertEqual(resp2.status_code, 200)

    def test_response_has_required_schema_fields(self):
        resp = _client.get("/entities/PAR Technology/earnings-history")
        data = resp.json()
        for field in ("entity_name", "events", "event_count", "generated_at"):
            self.assertIn(field, data, msg=f"Missing field: {field}")
        self.assertIsInstance(data["events"], list)

    def test_unknown_entity_returns_empty_events_not_error(self):
        resp = _client.get("/entities/NoSuchCompanyXYZ/earnings-history")
        data = resp.json()
        self.assertEqual(data["events"], [])
        self.assertEqual(data["event_count"], 0)

    def test_limit_param_respected(self):
        resp = _client.get("/entities/PAR Technology/earnings-history?limit=2")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertLessEqual(len(data["events"]), 2)

    def test_event_records_have_expected_fields_when_present(self):
        resp = _client.get("/entities/PAR Technology/earnings-history?limit=50")
        data = resp.json()
        for event in data["events"]:
            for field in ("company", "event_date", "title", "signal_dimensions",
                          "source_url", "excerpt", "_source_hash"):
                self.assertIn(field, event, msg=f"Missing field in event record: {field}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
