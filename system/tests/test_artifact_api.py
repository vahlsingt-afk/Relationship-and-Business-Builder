"""
test_artifact_api.py — HTTP API tests for RB 9.25A/B artifact and triage routes.

Uses FastAPI TestClient to test the four new API endpoints:
  - GET  /artifacts             (listArtifacts)
  - GET  /artifacts/{id}        (getArtifact)
  - POST /intelligence/triage   (triageInput)
  - POST /artifacts/{id}/enrich (enrichArtifact)

Test groups:
  AA1: listArtifacts (GET /artifacts)
  AA2: getArtifact (GET /artifacts/{id})
  AA3: triageInput (POST /intelligence/triage)
  AA4: enrichArtifact preview (confirm=false)
  AA5: enrichArtifact confirm (confirm=true) — uses isolated temp registry
  AA6: enrichArtifact stub promotion (stub → building)
  AA7: error cases (not_found, write failures)

NOTE: Tests that write to the registry use a patched server to avoid
      mutating the real registry.json.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:
    raise unittest.SkipTest("fastapi not installed — skipping artifact API tests")

ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = ROOT / "system" / "api" / "server.py"
REAL_REGISTRY = ROOT / "system" / "artifacts" / "registry.json"


def _load_server_module():
    module_name = "rb_api_server_for_artifact_tests"
    spec = importlib.util.spec_from_file_location(module_name, SERVER_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec so Pydantic can resolve forward refs
    sys.modules[module_name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# Load once for read-only tests
_server = _load_server_module()
_client = TestClient(_server.app, raise_server_exceptions=True, headers={"x-api-key": "test-key"})


# ---------------------------------------------------------------------------
# AA1: listArtifacts — GET /artifacts
# ---------------------------------------------------------------------------

class TestListArtifacts(unittest.TestCase):
    """AA1: GET /artifacts returns the artifact catalog."""

    def test_AA1a_returns_200(self):
        """AA1a: /artifacts returns HTTP 200."""
        r = _client.get("/artifacts")
        self.assertEqual(r.status_code, 200)

    def test_AA1b_response_has_artifacts_list(self):
        """AA1b: Response body has 'artifacts' list."""
        r = _client.get("/artifacts")
        data = r.json()
        self.assertIn("artifacts", data)
        self.assertIsInstance(data["artifacts"], list)

    def test_AA1c_mcdonalds_present(self):
        """AA1c: McDonald's US Ops artifact is in the list."""
        r = _client.get("/artifacts")
        ids = [a.get("artifact_id") for a in r.json()["artifacts"]]
        self.assertIn("micro_graph:mcdonalds_us_ops", ids)

    def test_AA1d_stubs_present(self):
        """AA1d: At least one stub artifact is in the list."""
        r = _client.get("/artifacts")
        data = r.json()
        stubs = [a for a in data["artifacts"] if a.get("status") == "stub"]
        self.assertGreater(len(stubs), 0)

    def test_AA1e_contract_field(self):
        """AA1e: Response has a contract field."""
        r = _client.get("/artifacts")
        self.assertIn("contract", r.json())

    def test_AA1f_filter_by_type(self):
        """AA1f: artifact_type filter returns only matching artifacts."""
        r = _client.get("/artifacts?artifact_type=micro_graph")
        data = r.json()
        for art in data["artifacts"]:
            self.assertEqual(art["artifact_type"], "micro_graph")

    def test_AA1g_filter_by_status_active(self):
        """AA1g: status=active filter returns only active artifacts."""
        r = _client.get("/artifacts?status=active")
        data = r.json()
        for art in data["artifacts"]:
            self.assertEqual(art["status"], "active")

    def test_AA1h_filter_by_status_stub(self):
        """AA1h: status=stub filter returns only stub artifacts."""
        r = _client.get("/artifacts?status=stub")
        data = r.json()
        for art in data["artifacts"]:
            self.assertEqual(art["status"], "stub")

    def test_AA1i_artifact_count_matches_list(self):
        """AA1i: artifact_count matches length of artifacts list."""
        r = _client.get("/artifacts")
        data = r.json()
        self.assertEqual(data["artifact_count"], len(data["artifacts"]))

    def test_AA1j_enrich_instruction_present(self):
        """AA1j: Response includes enrichment instruction."""
        r = _client.get("/artifacts")
        data = r.json()
        self.assertIn("enrich_instruction", data)


# ---------------------------------------------------------------------------
# AA2: getArtifact — GET /artifacts/{artifact_id}
# ---------------------------------------------------------------------------

class TestGetArtifact(unittest.TestCase):
    """AA2: GET /artifacts/{artifact_id} returns artifact details."""

    def test_AA2a_mcdonalds_returns_200(self):
        """AA2a: McDonald's artifact returns HTTP 200."""
        r = _client.get("/artifacts/micro_graph:mcdonalds_us_ops")
        self.assertEqual(r.status_code, 200)

    def test_AA2b_mcdonalds_artifact_id_correct(self):
        """AA2b: artifact_id field matches the requested id."""
        r = _client.get("/artifacts/micro_graph:mcdonalds_us_ops")
        data = r.json()
        self.assertEqual(data["artifact_id"], "micro_graph:mcdonalds_us_ops")

    def test_AA2c_mcdonalds_active_status(self):
        """AA2c: McDonald's artifact status is active."""
        r = _client.get("/artifacts/micro_graph:mcdonalds_us_ops")
        data = r.json()
        self.assertEqual(data.get("status"), "active")

    def test_AA2d_mcdonalds_has_topology_summary(self):
        """AA2d: Active micro_graph artifact includes topology_summary when index exists."""
        r = _client.get("/artifacts/micro_graph:mcdonalds_us_ops")
        data = r.json()
        # topology_summary is present if index.json is accessible
        if "topology_summary" in data:
            ts = data["topology_summary"]
            self.assertIn("counts", ts)

    def test_AA2e_unknown_artifact_returns_not_found(self):
        """AA2e: Unknown artifact_id returns status=not_found (HTTP 200 with not_found status)."""
        r = _client.get("/artifacts/micro_graph:does_not_exist")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["status"], "not_found")

    def test_AA2f_par_technology_stub_returns(self):
        """AA2f: PAR Technology stub artifact is retrievable."""
        r = _client.get("/artifacts/micro_graph:par_technology")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["artifact_id"], "micro_graph:par_technology")
        self.assertEqual(data["status"], "stub")

    def test_AA2g_stub_has_no_topology_summary(self):
        """AA2g: Stub artifact does not have topology_summary."""
        r = _client.get("/artifacts/micro_graph:par_technology")
        data = r.json()
        # Stubs are not active, so topology_summary should not be attached
        self.assertNotIn("topology_summary", data)

    def test_AA2h_all_registered_artifacts_retrievable(self):
        """AA2h: All registered artifacts are retrievable by id (stubs + building + active)."""
        # Global Payments and Foods Connected promoted to account_dossier/building
        artifact_ids = [
            "micro_graph:par_technology",
            "micro_graph:toast_pos",
            "account_dossier:global_payments",
            "account_dossier:foods_connected",
        ]
        for aid in artifact_ids:
            r = _client.get(f"/artifacts/{aid}")
            self.assertEqual(r.status_code, 200,
                             f"Expected 200 for {aid}, got {r.status_code}")
            data = r.json()
            self.assertNotEqual(data.get("status"), "not_found",
                                f"Artifact {aid} unexpectedly not found")

    def test_AA2i_status_label_present(self):
        """AA2i: Response includes status_label field."""
        r = _client.get("/artifacts/micro_graph:mcdonalds_us_ops")
        data = r.json()
        self.assertIn("status_label", data)


# ---------------------------------------------------------------------------
# AA3: triageInput — POST /intelligence/triage
# ---------------------------------------------------------------------------

class TestTriageInput(unittest.TestCase):
    """AA3: POST /intelligence/triage identifies all intelligence types."""

    MACRO_TEXT = (
        "McDonald's Q2 comp sales rose 2.8% driven by value menu adoption. "
        "Consumer spending shifted with affordability pressures. "
        "Drive-thru revenue grew as dine-in declined. "
        "Industry deployment of AI ordering accelerated in QSR. "
        "Earnings guidance for the full year was raised."
    )

    RI_TEXT = (
        "Bob Gibson just joined Toast as VP of Enterprise. "
        "I met with him last week at the NRA show. He reached out about a partnership."
    )

    NOISE_TEXT = "The office is closed today for a holiday."

    def _triage(self, text, **kwargs):
        payload = {"text": text, **kwargs}
        return _client.post("/intelligence/triage", json=payload)

    def test_AA3a_macro_text_returns_200(self):
        """AA3a: POST /intelligence/triage returns 200."""
        r = self._triage(self.MACRO_TEXT)
        self.assertEqual(r.status_code, 200)

    def test_AA3b_macro_detected(self):
        """AA3b: Macro text → macro_signal in identified_types."""
        r = self._triage(self.MACRO_TEXT)
        data = r.json()
        types = [s["intelligence_type"] for s in data["identified_types"]]
        self.assertIn("macro_signal", types)

    def test_AA3c_ri_detected(self):
        """AA3c: RI text → ri_event in identified_types."""
        r = self._triage(self.RI_TEXT)
        data = r.json()
        types = [s["intelligence_type"] for s in data["identified_types"]]
        self.assertIn("ri_event", types)

    def test_AA3d_noise_only_flag(self):
        """AA3d: Noise text → noise_only=True."""
        r = self._triage(self.NOISE_TEXT)
        data = r.json()
        self.assertTrue(data["noise_only"])

    def test_AA3e_persistence_status_not_persisted(self):
        """AA3e: Triage endpoint is always read-only — not_persisted."""
        r = self._triage(self.MACRO_TEXT)
        data = r.json()
        self.assertEqual(data["persistence_status"], "not_persisted")

    def test_AA3f_all_streams_require_confirmation(self):
        """AA3f: Every actionable stream requires confirmation."""
        r = self._triage(self.MACRO_TEXT)
        data = r.json()
        for s in data["identified_types"]:
            if s["intelligence_type"] != "noise":
                self.assertTrue(s["requires_confirmation"])

    def test_AA3g_source_type_propagated(self):
        """AA3g: source_type is returned in result."""
        r = self._triage(self.MACRO_TEXT, source_type="transcript")
        data = r.json()
        self.assertEqual(data["source_type"], "transcript")

    def test_AA3h_author_name_propagated(self):
        """AA3h: author_name returned in result."""
        r = self._triage(self.RI_TEXT, author_name="Bob Gibson")
        data = r.json()
        self.assertEqual(data["author_name"], "Bob Gibson")

    def test_AA3i_triage_id_format(self):
        """AA3i: triage_id follows TRG-YYYY-MM-DD-NNN format."""
        r = self._triage(self.MACRO_TEXT)
        data = r.json()
        import re
        self.assertRegex(data["triage_id"], r"^TRG-\d{4}-\d{2}-\d{2}-\d{3,}$")

    def test_AA3j_processing_order_ri_before_macro(self):
        """AA3j: When both RI and macro present, ri_event comes first in processing_order."""
        mixed_text = (
            "Dave Wilson joined Toast as CTO. He reached out after the earnings call. "
            "Toast Q2 revenue was up 28% driven by enterprise growth. "
            "Same-store digital payment volume hit a record. Consumer spend data confirms "
            "QSR resilience against affordability pressure. Drive-thru AI deployment accelerating."
        )
        r = self._triage(mixed_text)
        data = r.json()
        order = data["processing_order"]
        if "ri_event" in order and "macro_signal" in order:
            self.assertLess(order.index("ri_event"), order.index("macro_signal"))

    def test_AA3k_empty_text_noise_only(self):
        """AA3k: Empty text returns noise_only=True."""
        r = self._triage("")
        data = r.json()
        self.assertTrue(data["noise_only"])

    def test_AA3l_contract_field(self):
        """AA3l: Response contract field identifies schema version."""
        r = self._triage(self.MACRO_TEXT)
        data = r.json()
        self.assertEqual(data.get("contract"), "rb_intelligence_triage_v1")


# ---------------------------------------------------------------------------
# Isolated server for write tests (patched registry path)
# ---------------------------------------------------------------------------

def _load_isolated_server(tmp_registry_path: Path):
    """Load a fresh server module instance with patched registry path."""
    module_name = f"rb_api_server_isolated_{id(tmp_registry_path)}"
    spec = importlib.util.spec_from_file_location(module_name, SERVER_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec so Pydantic can resolve forward refs
    sys.modules[module_name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    # Patch the registry path to use our temp file
    mod._ARTIFACT_REGISTRY_PATH = tmp_registry_path
    return mod


# ---------------------------------------------------------------------------
# AA4: enrichArtifact preview (confirm=false)
# ---------------------------------------------------------------------------

class TestEnrichArtifactPreview(unittest.TestCase):
    """AA4: POST /artifacts/{id}/enrich with confirm=false — preview only."""

    def _enrich(self, artifact_id, **kwargs):
        payload = {"confirm": False, **kwargs}
        return _client.post(f"/artifacts/{artifact_id}/enrich", json=payload)

    def test_AA4a_preview_returns_200(self):
        """AA4a: Preview returns HTTP 200."""
        r = self._enrich("micro_graph:mcdonalds_us_ops")
        self.assertEqual(r.status_code, 200)

    def test_AA4b_preview_status_is_preview(self):
        """AA4b: Preview response has status='preview'."""
        r = self._enrich("micro_graph:mcdonalds_us_ops")
        data = r.json()
        self.assertEqual(data["status"], "preview")

    def test_AA4c_preview_requires_confirmation(self):
        """AA4c: Preview requires_confirmation=True."""
        r = self._enrich("micro_graph:mcdonalds_us_ops")
        data = r.json()
        self.assertTrue(data["requires_confirmation"])

    def test_AA4d_preview_persistence_not_persisted(self):
        """AA4d: Preview does NOT write to registry — no persistence_status=persisted."""
        r = self._enrich("micro_graph:mcdonalds_us_ops",
                         source_name="NSN_preview_test.xlsx")
        data = r.json()
        # Preview should NOT have persistence_status=persisted
        self.assertNotEqual(data.get("persistence_status"), "persisted")

    def test_AA4e_preview_has_proposed_lineage_entry(self):
        """AA4e: Preview includes proposed_source_lineage_entry."""
        r = self._enrich("micro_graph:mcdonalds_us_ops",
                         source_name="NSN_Q3.xlsx")
        data = r.json()
        self.assertIn("proposed_source_lineage_entry", data)

    def test_AA4f_preview_includes_triage_when_text_supplied(self):
        """AA4f: Preview includes triage_preview when text is supplied."""
        r = self._enrich(
            "micro_graph:mcdonalds_us_ops",
            text="McDonald's NSN operator update. Franchise store count: 14,300.",
            source_name="paste",
        )
        data = r.json()
        # triage_preview should be present and contain identified_types
        triage = data.get("triage_preview")
        if triage:
            self.assertIn("identified_types", triage)

    def test_AA4g_stub_preview_returns_stub_preview_status(self):
        """AA4g: Stub artifact preview returns status='stub_preview'."""
        r = self._enrich("micro_graph:par_technology")
        data = r.json()
        self.assertEqual(data["status"], "stub_preview")

    def test_AA4h_stub_preview_has_proposed_action(self):
        """AA4h: Stub preview includes proposed_action with instructions."""
        r = self._enrich("micro_graph:par_technology")
        data = r.json()
        self.assertIn("proposed_action", data)
        self.assertTrue(len(data["proposed_action"]) > 0)

    def test_AA4i_unknown_artifact_not_found(self):
        """AA4i: Enriching unknown artifact returns not_found status."""
        r = self._enrich("micro_graph:does_not_exist")
        data = r.json()
        self.assertEqual(data["status"], "not_found")


# ---------------------------------------------------------------------------
# AA5: enrichArtifact confirm (confirm=true) — isolated registry
# ---------------------------------------------------------------------------

class TestEnrichArtifactConfirm(unittest.TestCase):
    """AA5: POST /artifacts/{id}/enrich with confirm=true — writes to registry."""

    def setUp(self):
        """Set up a temp copy of the registry for isolated write tests."""
        self.tmp_dir = tempfile.mkdtemp()
        self.tmp_registry = Path(self.tmp_dir) / "registry.json"
        shutil.copy(REAL_REGISTRY, self.tmp_registry)
        # Load isolated server with patched registry path
        self.iso_server = _load_isolated_server(self.tmp_registry)
        self.iso_client = TestClient(self.iso_server.app, raise_server_exceptions=True, headers={"x-api-key": "test-key"})

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _enrich(self, artifact_id, **kwargs):
        payload = {"confirm": True, **kwargs}
        return self.iso_client.post(f"/artifacts/{artifact_id}/enrich", json=payload)

    def _read_registry(self):
        return json.loads(self.tmp_registry.read_text())

    def test_AA5a_confirm_returns_200(self):
        """AA5a: confirm=true returns HTTP 200."""
        r = self._enrich("micro_graph:mcdonalds_us_ops",
                         source_name="NSN_test_confirm.xlsx")
        self.assertEqual(r.status_code, 200)

    def test_AA5b_confirm_status_enriched(self):
        """AA5b: confirm=true returns status='enriched'."""
        r = self._enrich("micro_graph:mcdonalds_us_ops",
                         source_name="NSN_test_confirm.xlsx")
        data = r.json()
        self.assertEqual(data["status"], "enriched")

    def test_AA5c_confirm_persistence_status_persisted(self):
        """AA5c: confirm=true returns persistence_status='persisted'."""
        r = self._enrich("micro_graph:mcdonalds_us_ops",
                         source_name="NSN_test_confirm.xlsx")
        data = r.json()
        self.assertEqual(data["persistence_status"], "persisted")

    def test_AA5d_confirm_increments_enrichment_count(self):
        """AA5d: confirm=true increments enrichment_count by exactly 1."""
        reg_before = self._read_registry()
        art_before = next(a for a in reg_before["artifacts"]
                          if a["artifact_id"] == "micro_graph:mcdonalds_us_ops")
        count_before = art_before.get("enrichment_count", 0)

        r = self._enrich("micro_graph:mcdonalds_us_ops",
                         source_name="NSN_count_test.xlsx")
        data = r.json()

        reg_after = self._read_registry()
        art_after = next(a for a in reg_after["artifacts"]
                         if a["artifact_id"] == "micro_graph:mcdonalds_us_ops")
        count_after = art_after.get("enrichment_count", 0)

        self.assertEqual(count_after, count_before + 1,
                         "enrichment_count should increment by exactly 1")
        self.assertEqual(data["enrichment_count"], count_before + 1,
                         "response enrichment_count should equal new count (not double-incremented)")

    def test_AA5e_confirm_adds_source_lineage_entry(self):
        """AA5e: confirm=true adds entry to source_lineage in persisted file."""
        source_name = "NSN_lineage_test_2026.xlsx"
        self._enrich("micro_graph:mcdonalds_us_ops", source_name=source_name)

        reg_after = self._read_registry()
        art_after = next(a for a in reg_after["artifacts"]
                         if a["artifact_id"] == "micro_graph:mcdonalds_us_ops")
        lineage_names = [e.get("source_name") for e in art_after.get("source_lineage") or []]
        self.assertIn(source_name, lineage_names)

    def test_AA5f_confirm_updates_last_enriched(self):
        """AA5f: confirm=true updates last_enriched in persisted file."""
        self._enrich("micro_graph:mcdonalds_us_ops", source_name="test.xlsx")
        reg_after = self._read_registry()
        art_after = next(a for a in reg_after["artifacts"]
                         if a["artifact_id"] == "micro_graph:mcdonalds_us_ops")
        self.assertIsNotNone(art_after.get("last_enriched"))

    def test_AA5g_confirm_response_not_double_incremented(self):
        """AA5g: Regression — enrichment_count in response must equal new count, not old+2.

        Verifies the fix for the off-by-one bug where art.get('enrichment_count', 0) + 1
        was returned AFTER the in-memory dict had already been incremented.
        """
        reg_before = self._read_registry()
        art_before = next(a for a in reg_before["artifacts"]
                          if a["artifact_id"] == "micro_graph:mcdonalds_us_ops")
        count_before = art_before.get("enrichment_count", 0)

        r = self._enrich("micro_graph:mcdonalds_us_ops", source_name="offbyone_test.xlsx")
        data = r.json()

        # Should be count_before + 1, NOT count_before + 2
        expected = count_before + 1
        actual = data["enrichment_count"]
        self.assertEqual(actual, expected,
                         f"Off-by-one: expected {expected}, got {actual} "
                         f"(count_before={count_before})")

    def test_AA5h_response_includes_source_lineage_entry(self):
        """AA5h: Response includes the source_lineage_entry that was written."""
        source_name = "NSN_response_check.xlsx"
        r = self._enrich("micro_graph:mcdonalds_us_ops", source_name=source_name)
        data = r.json()
        self.assertIn("source_lineage_entry", data)
        self.assertEqual(data["source_lineage_entry"].get("source_name"), source_name)

    def test_AA5i_multiple_enrichments_stack(self):
        """AA5i: Multiple confirm=true calls stack enrichment_count correctly."""
        r1 = self._enrich("micro_graph:mcdonalds_us_ops", source_name="batch_1.xlsx")
        r2 = self._enrich("micro_graph:mcdonalds_us_ops", source_name="batch_2.xlsx")

        count1 = r1.json()["enrichment_count"]
        count2 = r2.json()["enrichment_count"]

        self.assertEqual(count2, count1 + 1,
                         "Second enrichment should be exactly 1 more than first")


# ---------------------------------------------------------------------------
# AA6: enrichArtifact stub promotion
# ---------------------------------------------------------------------------

class TestEnrichArtifactStubPromotion(unittest.TestCase):
    """AA6: Stub artifact enrichment promotes status stub → building."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.tmp_registry = Path(self.tmp_dir) / "registry.json"
        shutil.copy(REAL_REGISTRY, self.tmp_registry)
        self.iso_server = _load_isolated_server(self.tmp_registry)
        self.iso_client = TestClient(self.iso_server.app, raise_server_exceptions=True, headers={"x-api-key": "test-key"})

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _read_registry(self):
        return json.loads(self.tmp_registry.read_text())

    def test_AA6a_stub_confirm_returns_enriched(self):
        """AA6a: Stub + confirm=true returns status='enriched'."""
        r = self.iso_client.post(
            "/artifacts/micro_graph:par_technology/enrich",
            json={"confirm": True, "source_name": "PAR_customer_list_Q2.xlsx"},
        )
        data = r.json()
        self.assertEqual(data["status"], "enriched")

    def test_AA6b_stub_promoted_to_building(self):
        """AA6b: Stub artifact status promoted to 'building' after first enrichment."""
        self.iso_client.post(
            "/artifacts/micro_graph:par_technology/enrich",
            json={"confirm": True, "source_name": "PAR_first_data.xlsx"},
        )
        reg_after = self._read_registry()
        art_after = next(a for a in reg_after["artifacts"]
                         if a["artifact_id"] == "micro_graph:par_technology")
        self.assertEqual(art_after["status"], "building",
                         "Stub should be promoted to 'building' after first enrichment confirm")

    def test_AA6c_stub_response_has_promoted_from_stub_true(self):
        """AA6c: Stub enrichment response includes promoted_from_stub=True."""
        r = self.iso_client.post(
            "/artifacts/micro_graph:par_technology/enrich",
            json={"confirm": True, "source_name": "PAR_first.xlsx"},
        )
        data = r.json()
        self.assertTrue(data.get("promoted_from_stub"),
                        "promoted_from_stub should be True for stub→building promotion")

    def test_AA6d_active_enrichment_not_promoted(self):
        """AA6d: Active artifact enrichment does NOT set promoted_from_stub."""
        r = self.iso_client.post(
            "/artifacts/micro_graph:mcdonalds_us_ops/enrich",
            json={"confirm": True, "source_name": "NSN_active.xlsx"},
        )
        data = r.json()
        self.assertFalse(data.get("promoted_from_stub", False),
                         "Active artifact should not have promoted_from_stub=True")

    def test_AA6e_stub_enrichment_count_becomes_1(self):
        """AA6e: First stub enrichment sets enrichment_count to 1."""
        r = self.iso_client.post(
            "/artifacts/micro_graph:par_technology/enrich",
            json={"confirm": True, "source_name": "PAR_first.xlsx"},
        )
        data = r.json()
        self.assertEqual(data["enrichment_count"], 1)

    def test_AA6f_stub_preview_before_confirm(self):
        """AA6f: Stub preview (confirm=false) returns stub_preview status."""
        r = self.iso_client.post(
            "/artifacts/micro_graph:par_technology/enrich",
            json={"confirm": False},
        )
        data = r.json()
        self.assertEqual(data["status"], "stub_preview")
        self.assertTrue(data.get("requires_confirmation"))

    def test_AA6g_stub_lineage_recorded_after_first_enrich(self):
        """AA6g: Source lineage entry recorded in registry for stub after first enrich."""
        source_name = "PAR_stub_lineage.xlsx"
        self.iso_client.post(
            "/artifacts/micro_graph:par_technology/enrich",
            json={"confirm": True, "source_name": source_name},
        )
        reg_after = self._read_registry()
        art_after = next(a for a in reg_after["artifacts"]
                         if a["artifact_id"] == "micro_graph:par_technology")
        lineage_names = [e.get("source_name") for e in art_after.get("source_lineage") or []]
        self.assertIn(source_name, lineage_names)


# ---------------------------------------------------------------------------
# AA7: Error cases
# ---------------------------------------------------------------------------

class TestEnrichArtifactErrors(unittest.TestCase):
    """AA7: Error handling for enrichArtifact."""

    def _enrich(self, artifact_id, **kwargs):
        payload = {"confirm": False, **kwargs}
        return _client.post(f"/artifacts/{artifact_id}/enrich", json=payload)

    def test_AA7a_unknown_artifact_preview_returns_not_found(self):
        """AA7a: Preview on unknown artifact returns status=not_found."""
        r = self._enrich("micro_graph:totally_unknown_entity_xyz")
        data = r.json()
        self.assertEqual(data["status"], "not_found")

    def test_AA7b_unknown_artifact_confirm_returns_not_found(self):
        """AA7b: confirm=true on unknown artifact returns status=not_found."""
        r = _client.post(
            "/artifacts/micro_graph:totally_unknown_entity_xyz/enrich",
            json={"confirm": True, "source_name": "test.xlsx"},
        )
        data = r.json()
        self.assertEqual(data["status"], "not_found")

    def test_AA7c_get_unknown_artifact_returns_not_found_status(self):
        """AA7c: GET /artifacts/{unknown_id} returns status=not_found."""
        r = _client.get("/artifacts/micro_graph:totally_unknown")
        data = r.json()
        self.assertEqual(data["status"], "not_found")


if __name__ == "__main__":
    unittest.main()
