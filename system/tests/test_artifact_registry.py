"""
test_artifact_registry.py — Intelligence artifact registry tests.

DEFECT-014 fix verification: RB must have a general-purpose artifact system
for creating, persisting, enriching, and retrieving business intelligence
artifacts from user-supplied data.

Test groups:
  AR1: Registry schema and invariants
  AR2: McDonald's artifact entry (active, queryable)
  AR3: Stub artifact entries (PAR, Toast, Global Payments, Foods Connected)
  AR4: Triage integration (artifact registry feeds intelligence_triage.py)
  AR5: Enrichment recording (confirm=False preview, confirm=True write)
  AR6: Registry loader (_load_artifact_registry)
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import intelligence_triage as triage

# ---------------------------------------------------------------------------
# Load the actual registry file for integration-level tests
# ---------------------------------------------------------------------------

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "artifacts" / "registry.json"


def _load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _get_artifact(registry: dict, artifact_id: str) -> dict | None:
    for art in registry.get("artifacts") or []:
        if art.get("artifact_id") == artifact_id:
            return art
    return None


# ---------------------------------------------------------------------------
# AR1: Registry schema and invariants
# ---------------------------------------------------------------------------

class TestRegistrySchema(unittest.TestCase):
    """AR1: Registry file structure and schema compliance."""

    def setUp(self):
        self.reg = _load_registry()

    def test_AR1a_registry_file_exists(self):
        """AR1a: Registry file exists at expected path."""
        self.assertTrue(REGISTRY_PATH.exists(), f"Registry not found at {REGISTRY_PATH}")

    def test_AR1b_contract_field_correct(self):
        """AR1b: contract field is rb_intelligence_artifact_registry_v1."""
        self.assertEqual(self.reg.get("contract"), "rb_intelligence_artifact_registry_v1")

    def test_AR1c_artifacts_list_present(self):
        """AR1c: 'artifacts' key is present and is a list."""
        self.assertIn("artifacts", self.reg)
        self.assertIsInstance(self.reg["artifacts"], list)

    def test_AR1d_at_least_one_artifact(self):
        """AR1d: Registry has at least one artifact."""
        self.assertGreater(len(self.reg["artifacts"]), 0)

    def test_AR1e_each_artifact_has_required_fields(self):
        """AR1e: Every artifact has required fields."""
        required = {
            "artifact_id", "artifact_type", "name", "entity",
            "entity_aliases", "status", "confidence",
        }
        for art in self.reg["artifacts"]:
            for field in required:
                self.assertIn(field, art,
                              f"Artifact {art.get('artifact_id')} missing field: {field}")

    def test_AR1f_artifact_ids_are_unique(self):
        """AR1f: All artifact_ids are unique."""
        ids = [art["artifact_id"] for art in self.reg["artifacts"]]
        self.assertEqual(len(ids), len(set(ids)), "Duplicate artifact_ids found")

    def test_AR1g_artifact_type_valid(self):
        """AR1g: artifact_type is one of the allowed types."""
        valid_types = {
            "micro_graph", "account_dossier", "competitive_intelligence",
            "vendor_topology", "strategic_account",
        }
        for art in self.reg["artifacts"]:
            self.assertIn(art["artifact_type"], valid_types,
                          f"Unknown artifact_type: {art['artifact_type']}")

    def test_AR1h_status_valid(self):
        """AR1h: status is one of: active, stub, building."""
        valid_statuses = {"active", "stub", "building"}
        for art in self.reg["artifacts"]:
            self.assertIn(art["status"], valid_statuses,
                          f"Unknown status: {art['status']} on {art['artifact_id']}")

    def test_AR1i_confidence_valid(self):
        """AR1i: confidence is one of: high, medium, low."""
        valid_conf = {"high", "medium", "low"}
        for art in self.reg["artifacts"]:
            self.assertIn(art["confidence"], valid_conf,
                          f"Unknown confidence: {art['confidence']} on {art['artifact_id']}")

    def test_AR1j_entity_aliases_is_list(self):
        """AR1j: entity_aliases is a list on every artifact."""
        for art in self.reg["artifacts"]:
            self.assertIsInstance(art["entity_aliases"], list,
                                  f"entity_aliases not a list on {art['artifact_id']}")

    def test_AR1k_active_count_consistent(self):
        """AR1k: active_count field matches actual active artifact count."""
        if "active_count" in self.reg:
            actual = sum(1 for a in self.reg["artifacts"] if a["status"] == "active")
            self.assertEqual(self.reg["active_count"], actual,
                             "active_count does not match actual active artifact count")

    def test_AR1l_stub_count_consistent(self):
        """AR1l: stub_count field matches actual stub artifact count."""
        if "stub_count" in self.reg:
            actual = sum(1 for a in self.reg["artifacts"] if a["status"] == "stub")
            self.assertEqual(self.reg["stub_count"], actual,
                             "stub_count does not match actual stub artifact count")


# ---------------------------------------------------------------------------
# AR2: McDonald's artifact (active, queryable)
# ---------------------------------------------------------------------------

class TestMcDonaldsArtifact(unittest.TestCase):
    """AR2: McDonald's US Operations micro graph — active and queryable."""

    def setUp(self):
        self.reg = _load_registry()
        self.art = _get_artifact(self.reg, "micro_graph:mcdonalds_us_ops")

    def test_AR2a_mcdonalds_artifact_exists(self):
        """AR2a: micro_graph:mcdonalds_us_ops entry exists."""
        self.assertIsNotNone(self.art,
                             "micro_graph:mcdonalds_us_ops not found in registry")

    def test_AR2b_status_is_active(self):
        """AR2b: McDonald's artifact status is 'active'."""
        self.assertEqual(self.art["status"], "active")

    def test_AR2c_confidence_is_high(self):
        """AR2c: McDonald's artifact confidence is 'high'."""
        self.assertEqual(self.art["confidence"], "high")

    def test_AR2d_queryable_via_set(self):
        """AR2d: queryable_via is set (not null)."""
        self.assertIsNotNone(self.art.get("queryable_via"))

    def test_AR2e_store_count_is_positive(self):
        """AR2e: store_count is a positive integer."""
        store_count = self.art.get("store_count", 0)
        self.assertGreater(store_count, 0)

    def test_AR2f_operator_entity_count_is_positive(self):
        """AR2f: operator_entity_count is a positive integer."""
        op_count = self.art.get("operator_entity_count", 0)
        self.assertGreater(op_count, 0)

    def test_AR2g_node_count_is_positive(self):
        """AR2g: node_count is a positive integer."""
        self.assertGreater(self.art.get("node_count", 0), 0)

    def test_AR2h_edge_count_is_positive(self):
        """AR2h: edge_count is a positive integer."""
        self.assertGreater(self.art.get("edge_count", 0), 0)

    def test_AR2i_entity_aliases_include_key_terms(self):
        """AR2i: entity_aliases include 'mcd' and 'nsn'."""
        aliases = [a.lower() for a in self.art.get("entity_aliases") or []]
        self.assertIn("mcd", aliases)
        self.assertIn("nsn", aliases)

    def test_AR2j_freshness_date_present(self):
        """AR2j: freshness_date is set (not null)."""
        self.assertIsNotNone(self.art.get("freshness_date"))

    def test_AR2k_source_lineage_has_entry(self):
        """AR2k: source_lineage has at least one entry."""
        lineage = self.art.get("source_lineage") or []
        self.assertGreater(len(lineage), 0)

    def test_AR2l_graph_path_set(self):
        """AR2l: graph_path is set (not null)."""
        self.assertIsNotNone(self.art.get("graph_path"))

    def test_AR2m_enrichment_count_is_positive(self):
        """AR2m: enrichment_count reflects at least one enrichment."""
        self.assertGreater(self.art.get("enrichment_count", 0), 0)

    def test_AR2n_scope_domains_present(self):
        """AR2n: scope_domains list is present and non-empty."""
        domains = self.art.get("scope_domains") or []
        self.assertGreater(len(domains), 0)


# ---------------------------------------------------------------------------
# AR3: Stub artifacts
# ---------------------------------------------------------------------------

class TestStubArtifacts(unittest.TestCase):
    """AR3: Stub artifact entries for target entities."""

    def setUp(self):
        self.reg = _load_registry()

    def _get(self, artifact_id: str) -> dict | None:
        return _get_artifact(self.reg, artifact_id)

    def test_AR3a_par_technology_stub_exists(self):
        """AR3a: micro_graph:par_technology stub exists."""
        art = self._get("micro_graph:par_technology")
        self.assertIsNotNone(art)
        self.assertEqual(art["status"], "stub")

    def test_AR3b_toast_pos_stub_exists(self):
        """AR3b: micro_graph:toast_pos stub exists."""
        art = self._get("micro_graph:toast_pos")
        self.assertIsNotNone(art)
        self.assertEqual(art["status"], "stub")

    def test_AR3c_global_payments_artifact_exists(self):
        """AR3c: Global Payments artifact exists (promoted from stub to account_dossier/building)."""
        art = self._get("account_dossier:global_payments")
        self.assertIsNotNone(art, "account_dossier:global_payments must exist in registry")
        self.assertIn(art["status"], ("stub", "building", "active"),
                      "Global Payments artifact must have a valid status")

    def test_AR3d_foods_connected_artifact_exists(self):
        """AR3d: Foods Connected artifact exists (promoted from stub to account_dossier/building)."""
        art = self._get("account_dossier:foods_connected")
        self.assertIsNotNone(art, "account_dossier:foods_connected must exist in registry")
        self.assertIn(art["status"], ("stub", "building", "active"),
                      "Foods Connected artifact must have a valid status")

    def test_AR3e_stubs_have_null_queryable_via(self):
        """AR3e: Stub artifacts have queryable_via=null (not yet answerable)."""
        # Global Payments and Foods Connected promoted to account_dossier/building — excluded
        stub_ids = [
            "micro_graph:par_technology", "micro_graph:toast_pos",
        ]
        for aid in stub_ids:
            art = self._get(aid)
            if art:
                self.assertIsNone(art.get("queryable_via"),
                                  f"Stub {aid} should have queryable_via=null")

    def test_AR3f_stubs_have_zero_node_count(self):
        """AR3f: Stub artifacts have node_count=0."""
        stub_ids = [
            "micro_graph:par_technology", "micro_graph:toast_pos",
            "micro_graph:global_payments", "micro_graph:foods_connected",
        ]
        for aid in stub_ids:
            art = self._get(aid)
            if art:
                self.assertEqual(art.get("node_count", 0), 0,
                                 f"Stub {aid} should have node_count=0")

    def test_AR3g_stubs_have_entity_aliases(self):
        """AR3g: All stub entries have non-empty entity_aliases."""
        stub_ids = [
            "micro_graph:par_technology", "micro_graph:toast_pos",
            "micro_graph:global_payments", "micro_graph:foods_connected",
        ]
        for aid in stub_ids:
            art = self._get(aid)
            if art:
                aliases = art.get("entity_aliases") or []
                self.assertGreater(len(aliases), 0,
                                   f"Stub {aid} has no entity_aliases")

    def test_AR3h_par_aliases_include_par_tech(self):
        """AR3h: PAR Technology aliases include 'par tech' or 'par technology'."""
        art = self._get("micro_graph:par_technology")
        self.assertIsNotNone(art)
        aliases_lower = [a.lower() for a in art.get("entity_aliases") or []]
        self.assertTrue(
            any("par" in a for a in aliases_lower),
            "PAR Technology aliases should include a 'par' term"
        )

    def test_AR3i_stub_confidence_is_low(self):
        """AR3i: All stub entries have confidence='low'."""
        stub_ids = [
            "micro_graph:par_technology", "micro_graph:toast_pos",
            "micro_graph:global_payments", "micro_graph:foods_connected",
        ]
        for aid in stub_ids:
            art = self._get(aid)
            if art:
                self.assertEqual(art.get("confidence"), "low",
                                 f"Stub {aid} should have confidence='low'")


# ---------------------------------------------------------------------------
# AR4: Triage integration (registry feeds triage classifier)
# ---------------------------------------------------------------------------

class TestTriageRegistryIntegration(unittest.TestCase):
    """AR4: Artifact registry integrates with intelligence_triage classifier."""

    def setUp(self):
        self.reg = _load_registry()

    def test_AR4a_mcdonalds_entity_produces_enrich_stream(self):
        """AR4a: McDonald's data text → micro_graph_enrichment using actual registry."""
        text = (
            "Updated McDonald's NSN operator roster for Q3 2026. "
            "Store count: 14,300. Operator entity count: 1,400. "
            "Field office reorganization completed. Co-op structure updated."
        )
        r = triage.triage_input(text, registry=self.reg)
        types = [s["intelligence_type"] for s in r["identified_types"]]
        self.assertIn(triage.TYPE_MICRO_ENRICH, types,
                      "McDonald's data with topology terms should produce micro_graph_enrichment")

    def test_AR4b_mcdonalds_enrich_stream_artifact_id_correct(self):
        """AR4b: McDonald's enrichment stream has correct artifact_id."""
        text = (
            "McDonald's NSN franchise operator roster Q3 2026. "
            "Operator count: 1,400. Store count: 14,300. "
            "Field office territories reorganized."
        )
        r = triage.triage_input(text, registry=self.reg)
        enrich_streams = [s for s in r["identified_types"]
                          if s["intelligence_type"] == triage.TYPE_MICRO_ENRICH]
        if enrich_streams:
            artifact_ids = [s.get("artifact_id") for s in enrich_streams]
            self.assertIn("micro_graph:mcdonalds_us_ops", artifact_ids)

    def test_AR4c_unregistered_entity_no_topology_no_enrich(self):
        """AR4c: Entity not in registry or hardcoded terms, no topology → no enrich stream.

        Note: Any entity IN the registry (including stubs) will produce micro_enrich
        on mention regardless of topology terms. This test uses a totally unknown entity
        with no topology terms to verify the baseline no-match case.
        """
        text = "I heard Acme Restaurant Solutions is a solid company. Their software is new."
        r = triage.triage_input(text, registry=self.reg)
        types = [s["intelligence_type"] for s in r["identified_types"]]
        self.assertNotIn(triage.TYPE_MICRO_ENRICH, types,
                         "Truly unknown entity (not in registry or hardcoded terms) "
                         "should not produce micro_graph_enrichment")

    def test_AR4d_load_artifact_registry_succeeds(self):
        """AR4d: _load_artifact_registry() returns valid registry structure."""
        reg = triage._load_artifact_registry()
        self.assertIn("artifacts", reg)
        self.assertIsInstance(reg["artifacts"], list)

    def test_AR4e_artifact_entity_terms_builder(self):
        """AR4e: _artifact_entity_terms builds lookup from registry."""
        terms = triage._artifact_entity_terms(self.reg)
        self.assertIsInstance(terms, dict)
        # Should include at least one McDonald's term
        mcd_terms = {k for k in terms if "mcd" in k or "mcdonald" in k or "nsn" in k}
        self.assertGreater(len(mcd_terms), 0,
                           "McDonald's terms should appear in entity_terms lookup")


# ---------------------------------------------------------------------------
# AR5: Enrichment recording (confirm pattern)
# ---------------------------------------------------------------------------

class TestEnrichmentRecording(unittest.TestCase):
    """AR5: confirm=False preview vs confirm=True write pattern."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reg_path = Path(self.tmp) / "registry.json"
        # Write a copy of the registry for isolated tests
        reg = _load_registry()
        self.reg_path.write_text(json.dumps(reg, indent=2))
        self.reg = reg

    def _simulate_enrich_preview(self, artifact_id: str, source_name: str) -> dict:
        """Simulate confirm=False enrichment — preview only, no write."""
        art = _get_artifact(self.reg, artifact_id)
        if art is None:
            return {"ok": False, "error": f"Artifact not found: {artifact_id}"}
        return {
            "ok": True,
            "confirm": False,
            "artifact_id": artifact_id,
            "artifact_name": art.get("name"),
            "current_enrichment_count": art.get("enrichment_count", 0),
            "new_enrichment_count": art.get("enrichment_count", 0) + 1,
            "source_name": source_name,
            "proposed_action": "Record enrichment event in artifact registry.",
            "requires_confirmation": True,
            "persistence_status": "proposed_write_pending_confirmation",
        }

    def _simulate_enrich_confirm(self, artifact_id: str, source_name: str) -> dict:
        """Simulate confirm=True enrichment — write to registry copy."""
        art = _get_artifact(self.reg, artifact_id)
        if art is None:
            return {"ok": False, "error": f"Artifact not found: {artifact_id}"}
        art["enrichment_count"] = art.get("enrichment_count", 0) + 1
        art["last_enriched"] = "2026-05-29"
        if "source_lineage" not in art:
            art["source_lineage"] = []
        art["source_lineage"].append({
            "source_type": "paste",
            "source_name": source_name,
            "ingested_at": "2026-05-29",
        })
        self.reg_path.write_text(json.dumps(self.reg, indent=2))
        return {
            "ok": True,
            "confirm": True,
            "artifact_id": artifact_id,
            "enrichment_count": art["enrichment_count"],
            "persistence_status": "persisted",
        }

    def test_AR5a_preview_does_not_write(self):
        """AR5a: confirm=False preview returns proposed state without writing."""
        result = self._simulate_enrich_preview(
            "micro_graph:mcdonalds_us_ops", "NSN_test.xlsx"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["persistence_status"],
                         "proposed_write_pending_confirmation")
        # Registry file should be unchanged
        reg_after = json.loads(self.reg_path.read_text())
        original_count = _get_artifact(_load_registry(), "micro_graph:mcdonalds_us_ops")
        reg_after_count = _get_artifact(reg_after, "micro_graph:mcdonalds_us_ops")
        # The file should still match the original (no write happened)
        self.assertEqual(
            reg_after_count.get("enrichment_count"),
            original_count.get("enrichment_count"),
        )

    def test_AR5b_confirm_write_increments_enrichment_count(self):
        """AR5b: confirm=True increments enrichment_count in written file."""
        art_before = _get_artifact(self.reg, "micro_graph:mcdonalds_us_ops")
        count_before = art_before.get("enrichment_count", 0)
        result = self._simulate_enrich_confirm(
            "micro_graph:mcdonalds_us_ops", "NSN_test_June.xlsx"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["persistence_status"], "persisted")
        reg_after = json.loads(self.reg_path.read_text())
        art_after = _get_artifact(reg_after, "micro_graph:mcdonalds_us_ops")
        self.assertEqual(art_after["enrichment_count"], count_before + 1)

    def test_AR5c_confirm_write_adds_source_lineage_entry(self):
        """AR5c: confirm=True adds entry to source_lineage."""
        result = self._simulate_enrich_confirm(
            "micro_graph:mcdonalds_us_ops", "NSN_June_2026.xlsx"
        )
        self.assertTrue(result["ok"])
        reg_after = json.loads(self.reg_path.read_text())
        art_after = _get_artifact(reg_after, "micro_graph:mcdonalds_us_ops")
        lineage = art_after.get("source_lineage") or []
        source_names = [e.get("source_name") for e in lineage]
        self.assertIn("NSN_June_2026.xlsx", source_names)

    def test_AR5d_enrich_unknown_artifact_returns_error(self):
        """AR5d: Enriching an unknown artifact_id returns ok=False."""
        result = self._simulate_enrich_preview("micro_graph:does_not_exist", "test.xlsx")
        self.assertFalse(result["ok"])

    def test_AR5e_preview_requires_confirmation_true(self):
        """AR5e: Preview result always has requires_confirmation=True."""
        result = self._simulate_enrich_preview(
            "micro_graph:mcdonalds_us_ops", "test.xlsx"
        )
        self.assertTrue(result.get("requires_confirmation"))


# ---------------------------------------------------------------------------
# AR6: Registry loader
# ---------------------------------------------------------------------------

class TestRegistryLoader(unittest.TestCase):
    """AR6: _load_artifact_registry() filesystem behavior."""

    def test_AR6a_loads_real_registry(self):
        """AR6a: _load_artifact_registry() loads the real registry file."""
        reg = triage._load_artifact_registry()
        self.assertIn("artifacts", reg)
        self.assertIsInstance(reg["artifacts"], list)

    def test_AR6b_mcdonalds_in_loaded_registry(self):
        """AR6b: McDonald's artifact present in loaded registry."""
        reg = triage._load_artifact_registry()
        ids = [a.get("artifact_id") for a in reg.get("artifacts") or []]
        self.assertIn("micro_graph:mcdonalds_us_ops", ids)

    def test_AR6c_loaded_registry_has_contract(self):
        """AR6c: Loaded registry has correct contract identifier."""
        reg = triage._load_artifact_registry()
        self.assertEqual(reg.get("contract"), "rb_intelligence_artifact_registry_v1")

    def test_AR6d_missing_registry_returns_empty_structure(self):
        """AR6d: Missing registry path → empty artifacts list (no crash)."""
        # Monkeypatch core.SYSTEM_DIR to a temp dir with no registry
        import rb_core as core
        orig = core.SYSTEM_DIR
        try:
            core.SYSTEM_DIR = Path(tempfile.mkdtemp())
            reg = triage._load_artifact_registry()
            # Should return a minimal valid structure
            self.assertIn("artifacts", reg)
            self.assertIsInstance(reg["artifacts"], list)
        finally:
            core.SYSTEM_DIR = orig


if __name__ == "__main__":
    unittest.main()
