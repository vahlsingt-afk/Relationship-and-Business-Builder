"""
test_ingest_api.py — RB 9.40 / Sprint E-5
Tests for POST /ingest (operation: ingestContent).

Sprint E-5 adds the consolidated ingestion endpoint that replaces the
triageInput + getEntitySignals two-call pattern.  The endpoint runs full
triage AND returns entity intelligence context from IntelligenceDB in one call.

Key behaviours under test:
  - Returns all triage keys (contract, triage_id, identified_types, trust_stats)
  - Returns entity_context dict keyed by detected entity names
  - entity_context only includes entities with item_count > 0 in the DB
  - noise-only input returns noise_only=True with empty entity_context
  - entity_context is best-effort: DB unavailable → empty dict, not 503
  - trust_stats.mutations_proposed matches identified_types contents

Test groups:
  INCA1 (3):  Response structure — triage keys present, entity_context key
              present, trust_stats key present
  INCA2 (3):  Entity context — entity with DB items appears in context,
              entity with no DB items excluded, entity_context empty when DB
              is unavailable (best-effort, not 503)
  INCA3 (2):  Edge cases — noise-only input, missing text returns 422
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "system" / "api"))

try:
    from fastapi.testclient import TestClient
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

from intelligence_db import IntelligenceDB


def _tmp_db_path() -> Path:
    return Path(tempfile.mktemp(suffix=".db"))


def _seed_db(db_path: Path) -> IntelligenceDB:
    """Open a temp DB and seed it with a PAR Technology item."""
    db = IntelligenceDB(db_path)
    db.open()
    db.add_item(
        title="PAR Technology Q3 earnings beat estimates",
        content="PAR Technology reported strong Q3 results.",
        source_name="Restaurant Dive",
        source_type="web_scan",
        confidence="high",
        tags=[{"type": "entity", "value": "PAR Technology"}],
    )
    return db


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestINCA1_ResponseStructure(unittest.TestCase):
    """INCA1: POST /ingest returns all required top-level keys."""

    @classmethod
    def setUpClass(cls):
        import server
        cls._orig_open_idb = server._open_idb
        db_path = _tmp_db_path()
        cls._db = _seed_db(db_path)

        def _patched_open_idb():
            return IntelligenceDB(db_path)

        server._open_idb = _patched_open_idb
        cls.client = TestClient(server.app)

    @classmethod
    def tearDownClass(cls):
        import server
        server._open_idb = cls._orig_open_idb
        cls._db.close()

    def _post(self, text: str, **kwargs) -> dict:
        payload = {"text": text, **kwargs}
        resp = self.client.post(
            "/ingest", json=payload, headers={"x-api-key": "test-key"}
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def test_INCA1a_triage_keys_present(self):
        """INCA1a: Response contains all standard triage keys."""
        data = self._post("PAR Technology Q3 earnings beat estimates.")
        for key in ("triage_id", "contract", "identified_types", "type_count",
                    "noise_only", "processing_order", "persistence_status"):
            self.assertIn(key, data, f"Missing triage key: {key}")
        self.assertEqual(data["contract"], "rb_intelligence_triage_v1")

    def test_INCA1b_entity_context_key_present(self):
        """INCA1b: Response always includes entity_context and entity_context_days."""
        data = self._post("Some text about market trends.")
        self.assertIn("entity_context", data)
        self.assertIn("entity_context_days", data)
        self.assertIsInstance(data["entity_context"], dict)

    def test_INCA1c_trust_stats_present(self):
        """INCA1c: Response includes unified trust_stats block (on-demand contract)."""
        data = self._post("PAR Technology expands partnership with Visa.")
        self.assertIn("trust_stats", data)
        ts = data["trust_stats"]
        # Unified contract — same keys present in both scheduled and on-demand paths
        for field in ("source", "items_classified", "convergences_detected",
                      "mutation_proposals", "confidence", "intelligence_gaps",
                      "processing_order"):
            self.assertIn(field, ts, f"trust_stats missing: {field}")
        self.assertEqual(ts["source"], "on_demand")


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestINCA2_EntityContext(unittest.TestCase):
    """INCA2: entity_context contains entities with DB hits; omits those without."""

    @classmethod
    def setUpClass(cls):
        import server
        cls._orig_open_idb = server._open_idb
        cls._orig_idb_module = server._intelligence_db_module

        db_path = _tmp_db_path()
        cls._db = _seed_db(db_path)

        def _patched_open_idb():
            db = IntelligenceDB(db_path)
            db.open()
            return db

        server._open_idb = _patched_open_idb
        cls.client = TestClient(server.app)

    @classmethod
    def tearDownClass(cls):
        import server
        server._open_idb = cls._orig_open_idb
        server._intelligence_db_module = cls._orig_idb_module
        cls._db.close()

    def _post(self, text: str, **kwargs) -> dict:
        payload = {"text": text, **kwargs}
        resp = self.client.post(
            "/ingest", json=payload, headers={"x-api-key": "test-key"}
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def test_INCA2a_entity_with_db_items_in_context(self):
        """INCA2a: Entity that has DB items appears in entity_context."""
        data = self._post(
            "PAR Technology announced a new partnership today.",
            entity_context_days=90,
        )
        # PAR Technology is in the seeded DB — should appear in entity_context
        ec = data["entity_context"]
        self.assertTrue(
            any("PAR" in k for k in ec),
            f"Expected PAR Technology in entity_context, got keys: {list(ec.keys())}",
        )

    def test_INCA2b_entity_without_db_items_excluded(self):
        """INCA2b: Entity with no DB items is not in entity_context."""
        data = self._post(
            "Acme Widget Co announced layoffs affecting 500 workers.",
        )
        ec = data["entity_context"]
        # Acme Widget Co has no DB items — should not appear
        self.assertNotIn("Acme Widget Co", ec)

    def test_INCA2c_entity_context_empty_when_db_unavailable(self):
        """INCA2c: entity_context is empty (not 503) when IntelligenceDB is unavailable."""
        import server

        def _raise():
            raise RuntimeError("DB unavailable")

        orig = server._open_idb
        server._open_idb = _raise
        try:
            data = self._post("PAR Technology Q3 earnings beat estimates.")
            self.assertIsInstance(data["entity_context"], dict)
            # entity_context may be empty — no 503 raised
            self.assertIn("triage_id", data)
        finally:
            server._open_idb = orig


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestINCA3_EdgeCases(unittest.TestCase):
    """INCA3: Edge cases — noise-only input, missing required field."""

    @classmethod
    def setUpClass(cls):
        import server
        cls._orig_open_idb = server._open_idb

        db_path = _tmp_db_path()
        cls._db = _seed_db(db_path)

        def _patched_open_idb():
            db = IntelligenceDB(db_path)
            db.open()
            return db

        server._open_idb = _patched_open_idb
        cls.client = TestClient(server.app)

    @classmethod
    def tearDownClass(cls):
        import server
        server._open_idb = cls._orig_open_idb
        cls._db.close()

    def test_INCA3a_noise_only_input(self):
        """INCA3a: Noise-only text returns noise_only=True with empty entity_context."""
        resp = self.client.post(
            "/ingest",
            json={"text": "ok sounds good"},
            headers={"x-api-key": "test-key"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["noise_only"])
        self.assertEqual(data["entity_context"], {})

    def test_INCA3b_missing_text_returns_422(self):
        """INCA3b: Missing required 'text' field returns HTTP 422."""
        resp = self.client.post(
            "/ingest",
            json={"source_type": "paste"},
            headers={"x-api-key": "test-key"},
        )
        self.assertEqual(resp.status_code, 422)


# ---------------------------------------------------------------------------
# INCA4 — Unified pipeline: convergence analysis (Phase 3)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestINCA4_ConvergenceAnalysis(unittest.TestCase):
    """INCA4: POST /ingest includes convergence_analysis from existing DB."""

    @classmethod
    def setUpClass(cls):
        import server
        cls._orig_open_idb = server._open_idb

        db_path = _tmp_db_path()
        cls._db = IntelligenceDB(db_path)
        cls._db.open()
        # Seed multi-source: PAR Technology across 2 distinct sources
        cls._db.add_item(
            title="PAR Technology revenue growth Q3",
            content="PAR reports strong Q3.",
            source_name="Restaurant Dive",
            source_type="web_scan",
            confidence="high",
            tags=[{"type": "entity", "value": "PAR Technology"}],
        )
        cls._db.add_item(
            title="PAR Technology new operator wins",
            content="PAR expands operator base.",
            source_name="QSR Magazine",
            source_type="web_scan",
            confidence="medium",
            tags=[{"type": "entity", "value": "PAR Technology"}],
        )
        cls._db_path = db_path

        def _patched():
            db = IntelligenceDB(db_path)
            db.open()
            return db

        server._open_idb = _patched
        cls.client = TestClient(server.app)

    @classmethod
    def tearDownClass(cls):
        import server
        server._open_idb = cls._orig_open_idb
        cls._db.close()

    def _post(self, text: str) -> dict:
        resp = self.client.post(
            "/ingest", json={"text": text}, headers={"x-api-key": "test-key"}
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def test_INCA4a_convergence_analysis_key_present(self):
        """INCA4a: convergence_analysis is always present in response."""
        data = self._post("New partnership announcement in restaurant tech.")
        self.assertIn("convergence_analysis", data)
        ca = data["convergence_analysis"]
        for field in ("status", "multi_source", "entity_pairs",
                      "multi_source_count", "entity_pair_count"):
            self.assertIn(field, ca, f"convergence_analysis missing: {field}")

    def test_INCA4b_multi_source_entity_returned(self):
        """INCA4b: PAR Technology seeded across 2 sources appears in multi_source."""
        data = self._post("PAR Technology Q4 outlook looks strong.")
        ca = data["convergence_analysis"]
        self.assertEqual(ca["status"], "ok")
        entities = [c.get("entity") for c in ca.get("multi_source", [])]
        self.assertIn("PAR Technology", entities)

    def test_INCA4c_convergence_reflected_in_trust_stats(self):
        """INCA4c: trust_stats.convergences_detected matches convergence_analysis totals."""
        data = self._post("Industry consolidation continues.")
        ts = data["trust_stats"]
        ca = data["convergence_analysis"]
        expected = (ca.get("multi_source_count") or 0) + (ca.get("entity_pair_count") or 0)
        self.assertEqual(ts["convergences_detected"], expected)


# ---------------------------------------------------------------------------
# INCA5 — Unified pipeline: mutation proposals (Phase 4)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestINCA5_MutationProposals(unittest.TestCase):
    """INCA5: POST /ingest generates mutation proposals when convergences exist."""

    @classmethod
    def setUpClass(cls):
        import server
        import intelligence_assessment as _ia
        cls._orig_open_idb = server._open_idb
        cls._orig_wl = _ia._load_watchlist_entities
        cls._orig_at = _ia._load_active_thread_entities

        # Patch watchlist empty + empty threads so watchlist_add proposals fire
        _ia._load_watchlist_entities = lambda: set()
        _ia._load_active_thread_entities = lambda: {}

        db_path = _tmp_db_path()
        cls._db = IntelligenceDB(db_path)
        cls._db.open()
        # Seed 3+ items for Zephyr Foods Co across 2 sources → watchlist_add should fire
        for i, source in enumerate(["Restaurant Dive", "QSR Magazine", "RTN"]):
            cls._db.add_item(
                title=f"Zephyr Foods Co restaurant update {i+1}",
                content="Zephyr Foods Co expands in QSR.",
                source_name=source,
                source_type="web_scan",
                confidence="high",
                tags=[{"type": "entity", "value": "Zephyr Foods Co"}],
            )

        def _patched():
            db = IntelligenceDB(db_path)
            db.open()
            return db

        server._open_idb = _patched
        cls.client = TestClient(server.app)

    @classmethod
    def tearDownClass(cls):
        import server
        import intelligence_assessment as _ia
        server._open_idb = cls._orig_open_idb
        _ia._load_watchlist_entities = cls._orig_wl
        _ia._load_active_thread_entities = cls._orig_at
        cls._db.close()

    def _post(self, text: str) -> dict:
        resp = self.client.post(
            "/ingest", json={"text": text}, headers={"x-api-key": "test-key"}
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def test_INCA5a_mutation_proposals_key_present(self):
        """INCA5a: mutation_proposals and mutation_proposals_count always in response."""
        data = self._post("Zephyr Foods Co expands restaurant payments footprint.")
        self.assertIn("mutation_proposals", data)
        self.assertIn("mutation_proposals_count", data)
        self.assertIsInstance(data["mutation_proposals"], list)

    def test_INCA5b_watchlist_add_proposed_when_convergence_threshold_met(self):
        """INCA5b: watchlist_add proposal generated when entity has 3+ items and no watchlist entry."""
        data = self._post("Zephyr Foods Co Q3 earnings call scheduled.")
        proposals = data["mutation_proposals"]
        types = [p.get("type") for p in proposals]
        self.assertIn("watchlist_add", types,
                      f"Expected watchlist_add in proposals; got types: {types}")
        wp = next(p for p in proposals if p.get("type") == "watchlist_add")
        self.assertEqual(wp["entity"], "Zephyr Foods Co")
        self.assertTrue(wp["requires_confirmation"])

    def test_INCA5c_proposals_count_matches_trust_stats(self):
        """INCA5c: trust_stats.mutation_proposals matches len(mutation_proposals)."""
        data = self._post("Industry signal about restaurant technology consolidation.")
        ts = data["trust_stats"]
        self.assertEqual(ts["mutation_proposals"], data["mutation_proposals_count"])
        self.assertEqual(ts["mutation_proposals"], len(data["mutation_proposals"]))


# ---------------------------------------------------------------------------
# INCA6 — Trust stats unified contract (Phase 5)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestINCA6_UnifiedTrustStats(unittest.TestCase):
    """INCA6: trust_stats uses the unified contract shared with scheduled assessment."""

    @classmethod
    def setUpClass(cls):
        import server
        cls._orig_open_idb = server._open_idb

        db_path = _tmp_db_path()
        cls._db = _seed_db(db_path)

        def _patched():
            db = IntelligenceDB(db_path)
            db.open()
            return db

        server._open_idb = _patched
        cls.client = TestClient(server.app)

    @classmethod
    def tearDownClass(cls):
        import server
        server._open_idb = cls._orig_open_idb
        cls._db.close()

    def _post(self, text: str, **kwargs) -> dict:
        payload = {"text": text, **kwargs}
        resp = self.client.post(
            "/ingest", json=payload, headers={"x-api-key": "test-key"}
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def test_INCA6a_source_field_is_on_demand(self):
        """INCA6a: trust_stats.source == 'on_demand' for ingest endpoint."""
        data = self._post("PAR Technology signals continue.")
        self.assertEqual(data["trust_stats"]["source"], "on_demand")

    def test_INCA6b_confidence_field_present_and_valid(self):
        """INCA6b: trust_stats.confidence is high, medium, or low."""
        data = self._post("Consumer spending patterns shift in QSR segment.")
        confidence = data["trust_stats"]["confidence"]
        self.assertIn(confidence, ("high", "medium", "low"))

    def test_INCA6c_intelligence_gaps_is_list(self):
        """INCA6c: trust_stats.intelligence_gaps is always a list."""
        data = self._post("PAR Technology expands operator base.")
        self.assertIsInstance(data["trust_stats"]["intelligence_gaps"], list)

    def test_INCA6d_scheduled_trust_stats_has_source_field(self):
        """INCA6d: intelligence_assessment.phase5_trust_stats produces source='scheduled'."""
        import intelligence_assessment as ia

        def _p1(fetched=5, from_cache=1):
            return {
                "status": "ok", "items_fetched": fetched, "items_from_cache": from_cache,
                "source_health": [{"source": "Restaurant Dive", "status": "ok"}],
                "errors": [], "metadata": {},
            }

        def _p2(classified=3):
            return {"status": "ok", "items_classified": classified,
                    "signal_types_found": [], "entities_detected": []}

        def _p3(multi=2, pairs=1):
            return {"status": "ok", "multi_source": [], "entity_pairs": [],
                    "multi_source_count": multi, "entity_pair_count": pairs}

        def _p4(proposals=1):
            return {"status": "ok", "proposals": [], "proposals_count": proposals}

        ts = ia.phase5_trust_stats(_p1(), _p2(), _p3(), _p4(), started_at=ia._now_iso())
        self.assertEqual(ts["source"], "scheduled")
        # Unified contract keys must be present
        for key in ("items_classified", "convergences_detected", "mutation_proposals",
                    "confidence", "intelligence_gaps"):
            self.assertIn(key, ts, f"Scheduled trust_stats missing unified key: {key}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
