"""
test_micro_graph_routing.py — RB-DEFECT-005 micro graph presence index and routing tests.

Verifies:
  - Micro graph presence index exists and is correctly structured
  - Trust hierarchy enforces correct priority ordering
  - getMicroGraphIndex endpoint returns expected shape
  - Company-specific question domains are correctly registered
  - McDonald's operational topology questions route to the micro graph
  - Implicit trigger terms match expected activation vocabulary
"""
import importlib.util
import json
import unittest
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
MICRO_INDEX_PATH = ROOT / "system" / "graphs" / "micro" / "index.json"
GRAPH_REGISTRY_PATH = ROOT / "system" / "graphs" / "index.json"
MCDONALDS_GRAPH_PATH = ROOT / "system" / "graphs" / "micro" / "mcdonalds_us_ops"


class MicroGraphIndexFileTests(unittest.TestCase):
    """Tests against the presence index file on disk."""

    def setUp(self):
        if not MICRO_INDEX_PATH.exists():
            self.skipTest("system/graphs/micro/index.json not present")
        self.index = json.loads(MICRO_INDEX_PATH.read_text(encoding="utf-8"))

    def test_contract_field(self):
        self.assertEqual(self.index["contract"], "rb_micro_graph_presence_index_v1")

    def test_version_is_positive_int(self):
        self.assertGreater(self.index.get("version", 0), 0)

    def test_graphs_is_list(self):
        self.assertIsInstance(self.index["graphs"], list)

    def test_mcdonalds_graph_registered(self):
        ids = [g["graph_id"] for g in self.index["graphs"]]
        self.assertIn("micro_ecosystem:mcdonalds_us_ops", ids)

    def test_trust_hierarchy_ordering(self):
        h = self.index["trust_hierarchy"]
        self.assertIn("user_artifact", h)
        self.assertIn("rb_micro_graph", h)
        self.assertIn("general_model_knowledge", h)
        # user_artifact must be first (highest trust)
        self.assertEqual(h[0], "user_artifact")
        # general_model_knowledge must be last (lowest trust)
        self.assertEqual(h[-1], "general_model_knowledge")
        # rb_micro_graph must rank above general_model_knowledge
        self.assertLess(h.index("rb_micro_graph"), h.index("general_model_knowledge"))

    def test_mcdonalds_entry_completeness(self):
        entry = next(
            (g for g in self.index["graphs"]
             if g.get("graph_id") == "micro_ecosystem:mcdonalds_us_ops"),
            None,
        )
        self.assertIsNotNone(entry)
        required = [
            "graph_id", "graph_slug", "graph_type", "available", "entity_name",
            "entity_aliases", "scope_domains", "question_domains",
            "implicit_trigger_terms", "confidence_summary",
            "freshness_date", "routing_action",
        ]
        for field in required:
            self.assertIn(field, entry, f"McDonald's entry missing field: {field}")

    def test_mcdonalds_question_domains_cover_key_intents(self):
        entry = next(
            (g for g in self.index["graphs"]
             if g.get("graph_id") == "micro_ecosystem:mcdonalds_us_ops"),
            None,
        )
        self.assertIsNotNone(entry)
        qd = entry["question_domains"]
        required_domains = [
            "franchisee_count",
            "store_count",
            "operator_structure",
            "deployment_topology",
            "pos_system",
        ]
        for domain in required_domains:
            self.assertIn(domain, qd, f"Question domain missing: {domain}")

    def test_mcdonalds_implicit_triggers_include_nsn(self):
        entry = next(
            (g for g in self.index["graphs"]
             if g.get("graph_id") == "micro_ecosystem:mcdonalds_us_ops"),
            None,
        )
        self.assertIsNotNone(entry)
        triggers_lower = [t.lower() for t in entry["implicit_trigger_terms"]]
        self.assertIn("nsn", triggers_lower, "NSN must be an implicit trigger for McDonald's graph")

    def test_routing_action_is_getMicroGraphSummary(self):
        entry = next(
            (g for g in self.index["graphs"]
             if g.get("graph_id") == "micro_ecosystem:mcdonalds_us_ops"),
            None,
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry["routing_action"], "getMicroGraphSummary")

    def test_available_flag_is_true(self):
        entry = next(
            (g for g in self.index["graphs"]
             if g.get("graph_id") == "micro_ecosystem:mcdonalds_us_ops"),
            None,
        )
        self.assertIsNotNone(entry)
        self.assertTrue(entry["available"])


class GraphRegistryConsistencyTests(unittest.TestCase):
    """Cross-check micro/index.json against the main graphs/index.json."""

    def setUp(self):
        if not MICRO_INDEX_PATH.exists():
            self.skipTest("system/graphs/micro/index.json not present")
        if not GRAPH_REGISTRY_PATH.exists():
            self.skipTest("system/graphs/index.json not present")
        self.micro_index = json.loads(MICRO_INDEX_PATH.read_text(encoding="utf-8"))
        self.registry = json.loads(GRAPH_REGISTRY_PATH.read_text(encoding="utf-8"))

    def test_all_micro_index_graphs_in_registry(self):
        registry_ids = {g["graph_id"] for g in self.registry.get("graphs", [])}
        for g in self.micro_index["graphs"]:
            self.assertIn(
                g["graph_id"], registry_ids,
                f"Micro index graph {g['graph_id']} not found in main registry",
            )

    def test_registry_has_mcdonalds(self):
        registry_ids = [g["graph_id"] for g in self.registry.get("graphs", [])]
        self.assertIn("micro_ecosystem:mcdonalds_us_ops", registry_ids)


class McDonaldsGraphFilesTests(unittest.TestCase):
    """Verify the McDonald's micro graph directory structure is intact."""

    def setUp(self):
        if not MCDONALDS_GRAPH_PATH.exists():
            self.skipTest("McDonald's micro graph directory not present")

    def test_required_files_exist(self):
        required_files = ["index.json", "activation.json", "graph.json", "sources.json"]
        for fname in required_files:
            fpath = MCDONALDS_GRAPH_PATH / fname
            self.assertTrue(fpath.exists(), f"Required micro graph file missing: {fname}")

    def test_activation_json_has_primary_terms(self):
        activation = json.loads((MCDONALDS_GRAPH_PATH / "activation.json").read_text())
        self.assertIn("primary_terms", activation)
        terms_lower = [t.lower() for t in activation["primary_terms"]]
        self.assertIn("mcdonalds", terms_lower)
        self.assertIn("nsn", terms_lower)

    def test_graph_json_is_parseable(self):
        # Just verify it loads without error (full graph may be large)
        graph_path = MCDONALDS_GRAPH_PATH / "graph.json"
        try:
            data = json.loads(graph_path.read_text(encoding="utf-8"))
            self.assertIsInstance(data, dict)
        except json.JSONDecodeError as exc:
            self.fail(f"graph.json is not valid JSON: {exc}")


class RoutingLogicTests(unittest.TestCase):
    """Test the implicit routing decision logic that the GPT should follow.

    These tests verify the index produces the right signals for routing decisions
    — they do not call the GPT; they verify the data the GPT reads.
    """

    def setUp(self):
        if not MICRO_INDEX_PATH.exists():
            self.skipTest("system/graphs/micro/index.json not present")
        self.index = json.loads(MICRO_INDEX_PATH.read_text(encoding="utf-8"))

    def _find_graph_for_term(self, term: str) -> "Optional[dict]":
        """Simulate: does any micro graph have this term as an implicit trigger?"""
        term_lower = term.lower()
        for g in self.index["graphs"]:
            triggers = [t.lower() for t in g.get("implicit_trigger_terms", [])]
            if term_lower in triggers:
                return g
        return None

    def _find_graph_for_domain(self, domain: str) -> "Optional[dict]":
        """Simulate: does any micro graph cover this question domain?"""
        for g in self.index["graphs"]:
            if domain in g.get("question_domains", []):
                return g
        return None

    def test_nsn_term_routes_to_mcdonalds_graph(self):
        g = self._find_graph_for_term("nsn")
        self.assertIsNotNone(g, "NSN must trigger the McDonald's micro graph")
        self.assertEqual(g["graph_id"], "micro_ecosystem:mcdonalds_us_ops")

    def test_storetech_term_routes_to_mcdonalds_graph(self):
        g = self._find_graph_for_term("storetech")
        self.assertIsNotNone(g)
        self.assertEqual(g["graph_id"], "micro_ecosystem:mcdonalds_us_ops")

    def test_franchisee_count_question_routes_to_mcdonalds_graph(self):
        g = self._find_graph_for_domain("franchisee_count")
        self.assertIsNotNone(g, "franchisee_count question must be routable to a micro graph")
        self.assertEqual(g["graph_id"], "micro_ecosystem:mcdonalds_us_ops")

    def test_deployment_topology_routes_to_mcdonalds_graph(self):
        g = self._find_graph_for_domain("deployment_topology")
        self.assertIsNotNone(g)
        self.assertEqual(g["graph_id"], "micro_ecosystem:mcdonalds_us_ops")

    def test_unknown_company_has_no_micro_graph(self):
        # Wendy's is in the ecosystem graph but not (yet) a micro graph
        g = self._find_graph_for_term("wendys")
        self.assertIsNone(g, "Wendy's should NOT have a micro graph entry yet")


# =============================================================================
# MG6 — getMicroGraphSummary API: query_type structured retrieval (DEFECT-017)
# =============================================================================

try:
    import importlib.util as _ilu
    _SERVER_PATH = ROOT / "system" / "api" / "server.py"
    _spec = _ilu.spec_from_file_location("rb_api_server_mg_routing", str(_SERVER_PATH))
    _server_mod = _ilu.module_from_spec(_spec)
    assert _spec.loader is not None
    _spec.loader.exec_module(_server_mod)
    from fastapi.testclient import TestClient as _TC
    _HAS_SERVER = True
except Exception:
    _HAS_SERVER = False


@unittest.skipUnless(_HAS_SERVER, "fastapi/server not importable — skipping API tests")
class MicroGraphApiTests(unittest.TestCase):
    """MG6 — getMicroGraphSummary query_type structured retrieval (DEFECT-017).

    Tests call the live FastAPI app via TestClient. All tests use the real
    McDonald's micro graph index on disk; they skip if the file is absent.
    """

    @classmethod
    def setUpClass(cls):
        if not MCDONALDS_GRAPH_PATH.exists():
            raise unittest.SkipTest("McDonald's micro graph directory not present")
        cls.client = _TC(_server_mod.app, headers={"x-api-key": "test-key"})

    # ── default (no query_type) ───────────────────────────────────────────────

    def test_default_summary_status_found(self):
        resp = self.client.get("/graphs/micro_summary", params={"query": "McDonald's"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "found")

    def test_default_summary_has_answer(self):
        resp = self.client.get("/graphs/micro_summary", params={"query": "McDonald's"})
        data = resp.json()
        self.assertIn("answer", data)
        self.assertIsNotNone(data["answer"])
        self.assertIn("operator", data["answer"].lower())

    def test_default_summary_has_top_operator_entities(self):
        resp = self.client.get("/graphs/micro_summary", params={"query": "McDonald's"})
        data = resp.json()
        self.assertIn("top_operator_entities", data)
        self.assertIsInstance(data["top_operator_entities"], list)
        self.assertGreater(len(data["top_operator_entities"]), 0)

    def test_default_summary_query_type_field(self):
        resp = self.client.get("/graphs/micro_summary", params={"query": "McDonald's"})
        data = resp.json()
        self.assertEqual(data.get("query_type"), "summary")

    # ── operator_count ────────────────────────────────────────────────────────

    def test_operator_count_returns_answer(self):
        resp = self.client.get(
            "/graphs/micro_summary",
            params={"query": "McDonald's", "query_type": "operator_count"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "found")
        self.assertIn("answer", data)
        self.assertIsNotNone(data["answer"])

    def test_operator_count_answer_contains_counts(self):
        resp = self.client.get(
            "/graphs/micro_summary",
            params={"query": "McDonald's", "query_type": "operator_count"},
        )
        data = resp.json()
        # Answer template includes operator count numbers
        answer = data["answer"] or ""
        self.assertRegex(answer, r"\d+")  # at least one integer in the answer

    def test_operator_count_has_operator_tiers(self):
        resp = self.client.get(
            "/graphs/micro_summary",
            params={"query": "McDonald's", "query_type": "operator_count"},
        )
        data = resp.json()
        self.assertIn("operator_tiers", data)
        tiers = data["operator_tiers"]
        self.assertIn("enterprise_25_plus", tiers)
        self.assertIn("mid_tier_5_to_24", tiers)

    # ── largest_operators ─────────────────────────────────────────────────────

    def test_largest_operators_returns_list(self):
        resp = self.client.get(
            "/graphs/micro_summary",
            params={"query": "McDonald's", "query_type": "largest_operators"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("top_operator_entities", data)
        self.assertIsInstance(data["top_operator_entities"], list)

    def test_largest_operators_respects_limit(self):
        resp = self.client.get(
            "/graphs/micro_summary",
            params={"query": "McDonald's", "query_type": "largest_operators", "limit": 3},
        )
        data = resp.json()
        self.assertLessEqual(len(data["top_operator_entities"]), 3)

    def test_largest_operators_has_total_count(self):
        resp = self.client.get(
            "/graphs/micro_summary",
            params={"query": "McDonald's", "query_type": "largest_operators"},
        )
        data = resp.json()
        self.assertIn("total_operator_entities_with_stores", data)
        self.assertGreater(data["total_operator_entities_with_stores"], 0)

    # ── state_lookup ──────────────────────────────────────────────────────────

    def test_state_lookup_all_states(self):
        resp = self.client.get(
            "/graphs/micro_summary",
            params={"query": "McDonald's", "query_type": "state_lookup"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("state_distribution", data)
        self.assertIsInstance(data["state_distribution"], list)
        self.assertGreater(len(data["state_distribution"]), 0)

    def test_state_lookup_filtered_by_state(self):
        resp = self.client.get(
            "/graphs/micro_summary",
            params={"query": "McDonald's", "query_type": "state_lookup", "state": "TX"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("filter_state"), "TX")
        self.assertIsInstance(data["state_distribution"], list)
        # Texas has stores; should not be empty
        self.assertGreater(len(data["state_distribution"]), 0)
        # Store count should be significant for TX
        if data["state_distribution"]:
            self.assertGreater(data["state_distribution"][0]["store_count"], 100)

    def test_state_lookup_answer_is_string(self):
        resp = self.client.get(
            "/graphs/micro_summary",
            params={"query": "McDonald's", "query_type": "state_lookup", "state": "CA"},
        )
        data = resp.json()
        self.assertIsInstance(data.get("answer"), str)
        self.assertIn("CA", data["answer"])

    # ── coop_lookup ───────────────────────────────────────────────────────────

    def test_coop_lookup_returns_list(self):
        resp = self.client.get(
            "/graphs/micro_summary",
            params={"query": "McDonald's", "query_type": "coop_lookup"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("coop_distribution", data)
        self.assertIsInstance(data["coop_distribution"], list)
        self.assertGreater(len(data["coop_distribution"]), 0)

    def test_coop_lookup_has_total_coops(self):
        resp = self.client.get(
            "/graphs/micro_summary",
            params={"query": "McDonald's", "query_type": "coop_lookup"},
        )
        data = resp.json()
        self.assertIn("total_coops", data)
        self.assertGreater(data["total_coops"], 0)

    def test_coop_lookup_entry_has_required_fields(self):
        resp = self.client.get(
            "/graphs/micro_summary",
            params={"query": "McDonald's", "query_type": "coop_lookup"},
        )
        data = resp.json()
        if data["coop_distribution"]:
            entry = data["coop_distribution"][0]
            self.assertIn("coop_id", entry)
            self.assertIn("name", entry)
            self.assertIn("store_count", entry)

    # ── relationship_coverage ─────────────────────────────────────────────────

    def test_relationship_coverage_returns_contacts(self):
        resp = self.client.get(
            "/graphs/micro_summary",
            params={"query": "McDonald's", "query_type": "relationship_coverage"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("contacts", data)
        self.assertIsInstance(data["contacts"], list)

    def test_relationship_coverage_has_total_count(self):
        resp = self.client.get(
            "/graphs/micro_summary",
            params={"query": "McDonald's", "query_type": "relationship_coverage"},
        )
        data = resp.json()
        self.assertIn("total_contacts", data)
        # We know we have 111+ McDonald's contacts in baseline
        self.assertGreater(data["total_contacts"], 10)

    def test_relationship_coverage_answer_is_string(self):
        resp = self.client.get(
            "/graphs/micro_summary",
            params={"query": "McDonald's", "query_type": "relationship_coverage"},
        )
        data = resp.json()
        self.assertIsInstance(data.get("answer"), str)

    def test_relationship_coverage_contact_has_required_fields(self):
        resp = self.client.get(
            "/graphs/micro_summary",
            params={"query": "McDonald's", "query_type": "relationship_coverage"},
        )
        data = resp.json()
        if data.get("contacts"):
            contact = data["contacts"][0]
            for field in ("id", "name", "role", "company", "signal_class"):
                self.assertIn(field, contact, f"Contact missing field: {field}")

    # ── no-match / graceful failure ───────────────────────────────────────────

    def test_unknown_company_returns_registry_only(self):
        resp = self.client.get(
            "/graphs/micro_summary",
            params={"query": "Wendy's fictional brand xyz"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "registry_only")

    def test_no_query_with_single_graph_returns_found(self):
        """When there is exactly one micro graph, omitting query should return it."""
        resp = self.client.get("/graphs/micro_summary")
        self.assertEqual(resp.status_code, 200)
        # If registry has exactly 1 graph, status should be found; otherwise registry_only
        data = resp.json()
        self.assertIn(data["status"], ("found", "registry_only"))


if __name__ == "__main__":
    unittest.main()
