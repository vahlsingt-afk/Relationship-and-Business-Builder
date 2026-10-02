"""
test_micro_graph_builder.py — Tests for the generalized micro graph ETL builder.

RB 9.25C: micro_graph_builder.py builds stub artifacts into micro graph files.

Test groups:
  MB1 (10): Registry + artifact lookup
  MB2 (12): Text entity extraction
  MB3 (12): JSON entity extraction
  MB4 (10): CSV entity extraction
  MB5 (10): GraphBuilder mechanics (dedup, merge, referential integrity)
  MB6 (8):  Dry-run / needs-confirm modes
  MB7 (10): Write + confirm (tmpdir isolation)
  MB8 (8):  Activation / index file content
  MB9 (8):  Global index update
  MB10 (7): Multi-source stacking (two enrichments)
  MB11 (6): Error cases (missing artifact, bad JSON, empty CSV)
  MB12 (3): CLI smoke (--list, stdin, --json)
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BUILDER_PATH = ROOT / "system" / "scripts" / "micro_graph_builder.py"
REAL_REGISTRY = ROOT / "system" / "artifacts" / "registry.json"
REAL_GLOBAL_INDEX = ROOT / "system" / "graphs" / "micro" / "index.json"


# ── Load builder module ───────────────────────────────────────────────────────

def _load_builder():
    module_name = "micro_graph_builder_test_module"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, BUILDER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


mb = _load_builder()


# ── Fixtures ──────────────────────────────────────────────────────────────────

STUB_REGISTRY: dict = {
    "contract": "rb_intelligence_artifact_registry_v1",
    "version": 1,
    "updated_at": "2026-05-29",
    "artifacts": [
        {
            "artifact_id": "micro_graph:par_technology",
            "artifact_type": "micro_graph",
            "name": "PAR Technology",
            "entity": "PAR Technology",
            "entity_aliases": ["par technology", "par tech", "par corp", "brink pos"],
            "status": "stub",
            "confidence": "low",
            "freshness_date": None,
            "queryable_via": None,
            "graph_slug": None,
            "graph_path": None,
            "index_path": None,
            "source_lineage": [],
            "node_count": 0,
            "edge_count": 0,
            "enrichment_count": 0,
            "last_enriched": None,
            "scope_domains": ["POS deployments", "customer list", "competitive positioning"],
            "triage_trigger_terms": ["par technology", "par tech", "par corp", "brink pos"],
            "question_domains": ["customer_footprint", "deployment_topology", "product_suite"],
            "notes": ["Stub entry — artifact not yet built."],
        },
        {
            "artifact_id": "micro_graph:toast_pos",
            "artifact_type": "micro_graph",
            "name": "Toast POS",
            "entity": "Toast",
            "entity_aliases": ["toast", "toast pos", "toasttab", "TOST"],
            "status": "stub",
            "confidence": "low",
            "freshness_date": None,
            "queryable_via": None,
            "graph_slug": None,
            "graph_path": None,
            "index_path": None,
            "source_lineage": [],
            "node_count": 0,
            "edge_count": 0,
            "enrichment_count": 0,
            "last_enriched": None,
            "scope_domains": ["POS customer footprint", "competitive positioning"],
            "triage_trigger_terms": ["toast", "toasttab", "tost"],
            "question_domains": ["customer_footprint", "competitive_positioning"],
            "notes": [],
        },
    ],
    "stub_count": 2,
    "active_count": 0,
}

PAR_TEXT = (
    "PAR Technology announced strong Q3 results. Brink POS is now deployed at 14,000 "
    "enterprise restaurant locations across North America. Bob Terwilliger joined PAR "
    "as VP of Enterprise Sales. Yum Brands recently selected Brink POS for global "
    "deployment. McDonald's International also evaluating the platform. "
    "Dave Wilson leads the PAR Technology engineering team."
)

PAR_JSON_CUSTOMERS = {
    "customers": [
        {"name": "Yum Brands", "segment": "enterprise", "locations": 50000},
        {"name": "Dine Brands", "segment": "enterprise", "locations": 1700},
        {"name": "Arcos Dorados", "segment": "enterprise", "locations": 2300},
    ]
}

PAR_CSV = (
    "name,segment,locations,country\n"
    "Yum Brands,enterprise,50000,US\n"
    "Dine Brands,enterprise,1700,US\n"
    "Shake Shack,smb,500,US\n"
)


def _make_temp_registry(content: dict) -> tuple[Path, Path]:
    """Returns (tmp_dir, registry_path). Caller must clean up tmp_dir."""
    tmp_dir = Path(tempfile.mkdtemp())
    reg_path = tmp_dir / "registry.json"
    reg_path.write_text(json.dumps(content))
    return tmp_dir, reg_path


def _make_temp_global_index(tmp_dir: Path) -> Path:
    idx_path = tmp_dir / "global_index.json"
    if REAL_GLOBAL_INDEX.exists():
        shutil.copy(REAL_GLOBAL_INDEX, idx_path)
    else:
        idx_path.write_text(json.dumps({
            "contract": "rb_micro_graph_presence_index_v1",
            "graphs": [],
            "version": 1,
        }))
    return idx_path


def _make_graph_base(tmp_dir: Path) -> Path:
    g = tmp_dir / "graphs" / "micro"
    g.mkdir(parents=True, exist_ok=True)
    return g


def _build_par(
    text: str = PAR_TEXT,
    source_type: str = "text",
    write: bool = False,
    confirm: bool = False,
    registry_override: dict | None = None,
) -> tuple[dict, Path, Path, Path]:
    """Run build_graph for PAR Technology in an isolated tmpdir.
    Returns (result, tmp_dir, registry_path, graph_base_dir)."""
    reg = registry_override if registry_override is not None else STUB_REGISTRY
    tmp_dir, reg_path = _make_temp_registry(reg)
    global_idx = _make_temp_global_index(tmp_dir)
    graph_base = _make_graph_base(tmp_dir)
    result = mb.build_graph(
        artifact_id="micro_graph:par_technology",
        text=text,
        source_type=source_type,
        source_name="test_source.txt",
        registry_path=reg_path,
        graph_base_dir=graph_base,
        global_index_path=global_idx,
        write=write,
        confirm=confirm,
    )
    return result, tmp_dir, reg_path, graph_base


# ── MB1: Registry + artifact lookup ───────────────────────────────────────────

class TestRegistryLookup(unittest.TestCase):
    """MB1: Registry loading and artifact resolution."""

    def test_MB1a_loads_real_registry(self):
        """MB1a: Real registry loads without error."""
        reg = mb._load_registry(REAL_REGISTRY)
        self.assertIn("artifacts", reg)
        self.assertIsInstance(reg["artifacts"], list)

    def test_MB1b_get_artifact_returns_dict(self):
        """MB1b: _get_artifact returns dict for known id."""
        art = mb._get_artifact(STUB_REGISTRY, "micro_graph:par_technology")
        self.assertIsNotNone(art)
        self.assertIsInstance(art, dict)

    def test_MB1c_get_artifact_returns_none_for_unknown(self):
        """MB1c: _get_artifact returns None for unknown id."""
        result = mb._get_artifact(STUB_REGISTRY, "micro_graph:does_not_exist")
        self.assertIsNone(result)

    def test_MB1d_entity_name_correct(self):
        """MB1d: Entity name from registry is PAR Technology."""
        art = mb._get_artifact(STUB_REGISTRY, "micro_graph:par_technology")
        self.assertEqual(art["entity"], "PAR Technology")

    def test_MB1e_real_registry_has_par_stub(self):
        """MB1e: Real registry has PAR Technology as stub."""
        reg = mb._load_registry(REAL_REGISTRY)
        art = mb._get_artifact(reg, "micro_graph:par_technology")
        self.assertIsNotNone(art)
        self.assertEqual(art.get("status"), "stub")

    def test_MB1f_real_registry_has_at_least_two_stubs(self):
        """MB1f: Real registry has at least 2 stub artifacts (PAR, Toast; GP and FC promoted to building)."""
        reg = mb._load_registry(REAL_REGISTRY)
        stubs = [a for a in reg["artifacts"] if a.get("status") == "stub"]
        self.assertGreaterEqual(len(stubs), 2,
            "Registry must have at least 2 stubs (PAR Technology, Toast POS)")

    def test_MB1g_missing_registry_raises(self):
        """MB1g: _load_registry raises FileNotFoundError for missing path."""
        with self.assertRaises(FileNotFoundError):
            mb._load_registry(Path("/does/not/exist/registry.json"))

    def test_MB1h_all_four_stubs_have_required_fields(self):
        """MB1h: All 4 real registry stubs have required fields."""
        reg = mb._load_registry(REAL_REGISTRY)
        required = {"artifact_id", "artifact_type", "entity", "status",
                    "triage_trigger_terms", "scope_domains"}
        for a in reg["artifacts"]:
            if a.get("status") == "stub":
                for field in required:
                    self.assertIn(field, a, f"{a.get('artifact_id')} missing {field}")

    def test_MB1i_build_graph_not_found(self):
        """MB1i: build_graph returns not_found for unknown artifact_id."""
        tmp_dir, reg_path = _make_temp_registry(STUB_REGISTRY)
        try:
            result = mb.build_graph(
                artifact_id="micro_graph:does_not_exist",
                text="some text",
                registry_path=reg_path,
            )
            self.assertEqual(result["status"], "not_found")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_MB1j_not_found_includes_error_message(self):
        """MB1j: not_found result has a non-empty 'error' field."""
        tmp_dir, reg_path = _make_temp_registry(STUB_REGISTRY)
        try:
            result = mb.build_graph(
                artifact_id="micro_graph:bogus",
                text="text",
                registry_path=reg_path,
            )
            self.assertIn("error", result)
            self.assertGreater(len(result["error"]), 0)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ── MB2: Text extraction ──────────────────────────────────────────────────────

class TestTextExtraction(unittest.TestCase):
    """MB2: _extract_from_text entity extraction."""

    ART = STUB_REGISTRY["artifacts"][0]  # PAR Technology
    SRC = "src-test-001"

    def _extract(self, text: str) -> mb.GraphBuilder:
        return mb._extract_from_text(text, self.ART, self.SRC)

    def test_MB2a_anchor_node_always_present(self):
        """MB2a: Anchor entity node is always added regardless of text."""
        g = self._extract("nothing about par here at all")
        self.assertIn("company:par-technology", g.nodes)

    def test_MB2b_anchor_node_subtype(self):
        """MB2b: Anchor entity node has subtype='anchor_entity'."""
        g = self._extract("irrelevant text")
        anchor = g.nodes["company:par-technology"]
        self.assertEqual(anchor.get("subtype"), "anchor_entity")

    def test_MB2c_anchor_node_high_confidence(self):
        """MB2b: Anchor node confidence is 'high'."""
        g = self._extract("x")
        anchor = g.nodes["company:par-technology"]
        self.assertEqual(anchor["confidence"]["level"], "high")

    def test_MB2d_company_mention_extracted(self):
        """MB2d: 'Yum Brands' mention produces a company node."""
        g = self._extract("Yum Brands recently selected Brink POS.")
        ids = list(g.nodes.keys())
        self.assertTrue(any("yum" in nid for nid in ids),
                        f"No yum node found in {ids}")

    def test_MB2e_person_mention_extracted(self):
        """MB2e: 'Bob Terwilliger' mention produces a person node."""
        g = self._extract(PAR_TEXT)
        ids = list(g.nodes.keys())
        self.assertTrue(any("bob" in nid and "terwilliger" in nid for nid in ids),
                        f"No Bob Terwilliger node in {ids}")

    def test_MB2f_person_node_type(self):
        """MB2f: Person nodes have type='person'."""
        g = self._extract("Dave Wilson leads the team.")
        people = [n for n in g.nodes.values() if n["type"] == "person"]
        self.assertGreater(len(people), 0)

    def test_MB2g_empty_text_only_anchor(self):
        """MB2g: Empty text produces exactly the anchor node."""
        g = self._extract("")
        self.assertEqual(len(g.nodes), 1)
        self.assertIn("company:par-technology", g.nodes)

    def test_MB2h_short_stopwords_not_nodes(self):
        """MB2h: Common stop words ('And', 'The') are not extracted as company nodes."""
        g = self._extract("The quick brown fox And the lazy dog.")
        node_names = {n["name"].lower() for n in g.nodes.values()}
        self.assertNotIn("and", node_names)
        self.assertNotIn("the", node_names)

    def test_MB2i_node_source_recorded(self):
        """MB2i: Every node's source list contains the source_id."""
        g = self._extract(PAR_TEXT)
        for node_id, node in g.nodes.items():
            self.assertIn(self.SRC, node["sources"],
                          f"Node {node_id} missing source {self.SRC}")

    def test_MB2j_stats_returns_correct_counts(self):
        """MB2j: stats() reports node_count == len(nodes)."""
        g = self._extract(PAR_TEXT)
        stats = g.stats()
        self.assertEqual(stats["node_count"], len(g.nodes))
        self.assertEqual(stats["edge_count"], len(g.edges))

    def test_MB2k_stats_node_types_dict(self):
        """MB2k: stats() node_types is a dict."""
        g = self._extract(PAR_TEXT)
        self.assertIsInstance(g.stats()["node_types"], dict)

    def test_MB2l_multiple_person_mentions_distinct_nodes(self):
        """MB2l: Two distinct person names produce two person nodes."""
        text = "Bob Terwilliger leads sales. Dave Wilson manages engineering."
        g = self._extract(text)
        people = [n for n in g.nodes.values() if n["type"] == "person"]
        names = {p["name"] for p in people}
        self.assertIn("Bob Terwilliger", names)
        self.assertIn("Dave Wilson", names)


# ── MB3: JSON extraction ──────────────────────────────────────────────────────

class TestJsonExtraction(unittest.TestCase):
    """MB3: _extract_from_json entity extraction."""

    ART = STUB_REGISTRY["artifacts"][0]  # PAR Technology
    SRC = "src-json-001"

    def _extract(self, data) -> mb.GraphBuilder:
        return mb._extract_from_json(data, self.ART, self.SRC)

    def test_MB3a_anchor_always_present(self):
        """MB3a: Anchor node present regardless of data."""
        g = self._extract({})
        self.assertIn("company:par-technology", g.nodes)

    def test_MB3b_customers_list_ingested(self):
        """MB3b: dict with 'customers' key ingests each entry as a customer node."""
        g = self._extract(PAR_JSON_CUSTOMERS)
        customer_nodes = [n for n in g.nodes.values() if n["type"] == "customer"]
        self.assertEqual(len(customer_nodes), 3)

    def test_MB3c_customer_names_correct(self):
        """MB3c: Customer node names match input."""
        g = self._extract(PAR_JSON_CUSTOMERS)
        names = {n["name"] for n in g.nodes.values() if n["type"] == "customer"}
        self.assertIn("Yum Brands", names)
        self.assertIn("Dine Brands", names)

    def test_MB3d_customer_edge_added(self):
        """MB3d: An edge 'has_customer' is added from anchor to each customer."""
        g = self._extract(PAR_JSON_CUSTOMERS)
        self.assertGreater(len(g.edges), 0)
        edge_types = {e["type"] for e in g.edges.values()}
        self.assertIn("has_customer", edge_types)

    def test_MB3e_list_input_ingested(self):
        """MB3e: A plain list of dicts is ingested."""
        data = [
            {"name": "Yum Brands", "locations": 50000},
            {"name": "Dine Brands", "locations": 1700},
        ]
        g = self._extract(data)
        self.assertEqual(len(g.nodes) - 1, 2)  # 2 customers + anchor

    def test_MB3f_direct_graph_import(self):
        """MB3f: Dict with 'nodes'/'edges' performs direct graph import."""
        direct = {
            "nodes": [
                {"id": "company:acme", "type": "company", "name": "Acme Corp",
                 "attributes": {}, "sources": [], "confidence": {"level": "high"}},
            ],
            "edges": [],
        }
        g = self._extract(direct)
        self.assertIn("company:acme", g.nodes)

    def test_MB3g_empty_list_only_anchor(self):
        """MB3g: Empty list produces only anchor node."""
        g = self._extract([])
        self.assertEqual(len(g.nodes), 1)

    def test_MB3h_empty_dict_only_anchor(self):
        """MB3h: Empty dict produces only anchor node."""
        g = self._extract({})
        self.assertEqual(len(g.nodes), 1)

    def test_MB3i_node_source_recorded(self):
        """MB3i: Nodes from JSON contain the source_id."""
        g = self._extract(PAR_JSON_CUSTOMERS)
        for nid, node in g.nodes.items():
            self.assertIn(self.SRC, node["sources"], f"{nid} missing source")

    def test_MB3j_node_attributes_preserved(self):
        """MB3j: Scalar attributes from JSON record are stored on the node."""
        data = {"customers": [{"name": "Yum Brands", "segment": "enterprise"}]}
        g = self._extract(data)
        yum = next(n for n in g.nodes.values() if n["name"] == "Yum Brands")
        self.assertEqual(yum["attributes"].get("segment"), "enterprise")

    def test_MB3k_contacts_key_produces_person_nodes(self):
        """MB3k: 'contacts' key produces person-type nodes."""
        data = {"contacts": [{"name": "Alice Smith", "email": "alice@co.com"}]}
        g = self._extract(data)
        people = [n for n in g.nodes.values() if n["type"] == "person"]
        self.assertGreater(len(people), 0)

    def test_MB3l_bad_json_returns_error(self):
        """MB3l: Invalid JSON text returns status=error from build_graph."""
        tmp_dir, reg_path = _make_temp_registry(STUB_REGISTRY)
        graph_base = _make_graph_base(tmp_dir)
        global_idx = _make_temp_global_index(tmp_dir)
        try:
            result = mb.build_graph(
                artifact_id="micro_graph:par_technology",
                text="{invalid json}}}",
                source_type="json",
                registry_path=reg_path,
                graph_base_dir=graph_base,
                global_index_path=global_idx,
            )
            self.assertEqual(result["status"], "error")
            self.assertIn("JSON", result["error"])
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ── MB4: CSV extraction ───────────────────────────────────────────────────────

class TestCsvExtraction(unittest.TestCase):
    """MB4: _extract_from_csv entity extraction."""

    ART = STUB_REGISTRY["artifacts"][0]  # PAR Technology
    SRC = "src-csv-001"

    def _extract(self, text: str) -> mb.GraphBuilder:
        return mb._extract_from_csv(text, self.ART, self.SRC)

    def test_MB4a_anchor_always_present(self):
        """MB4a: Anchor entity node always present."""
        g = self._extract(PAR_CSV)
        self.assertIn("company:par-technology", g.nodes)

    def test_MB4b_customer_nodes_created(self):
        """MB4b: Three CSV rows → three customer nodes (+ anchor)."""
        g = self._extract(PAR_CSV)
        customers = [n for n in g.nodes.values() if n["type"] == "customer"]
        self.assertEqual(len(customers), 3)

    def test_MB4c_customer_names(self):
        """MB4c: Customer names match CSV rows."""
        g = self._extract(PAR_CSV)
        names = {n["name"] for n in g.nodes.values() if n["type"] == "customer"}
        self.assertIn("Yum Brands", names)
        self.assertIn("Shake Shack", names)

    def test_MB4d_edges_created(self):
        """MB4d: has_customer edges from anchor to each customer."""
        g = self._extract(PAR_CSV)
        self.assertEqual(len(g.edges), 3)

    def test_MB4e_csv_attributes_preserved(self):
        """MB4e: CSV columns (segment, country) stored as node attributes."""
        g = self._extract(PAR_CSV)
        yum = next(n for n in g.nodes.values() if n["name"] == "Yum Brands")
        self.assertIn("segment", yum["attributes"])
        self.assertEqual(yum["attributes"]["segment"], "enterprise")

    def test_MB4f_tab_delimiter_detected(self):
        """MB4f: Tab-delimited CSV is parsed correctly."""
        tsv = "name\tsegment\nYum Brands\tenterprise\nShake Shack\tsmb\n"
        g = self._extract(tsv)
        customers = [n for n in g.nodes.values() if n["type"] == "customer"]
        self.assertEqual(len(customers), 2)

    def test_MB4g_empty_csv_only_anchor(self):
        """MB4g: Empty text produces only anchor node."""
        g = self._extract("")
        self.assertEqual(len(g.nodes), 1)

    def test_MB4h_header_only_no_rows_only_anchor(self):
        """MB4h: Header-only CSV produces only anchor node."""
        g = self._extract("name,segment,locations\n")
        self.assertEqual(len(g.nodes), 1)

    def test_MB4i_person_type_detected_by_email_column(self):
        """MB4i: CSV with 'email' column produces person-type nodes."""
        person_csv = "name,email,role\nAlice Smith,alice@co.com,VP\n"
        g = self._extract(person_csv)
        people = [n for n in g.nodes.values() if n["type"] == "person"]
        self.assertGreater(len(people), 0)

    def test_MB4j_source_id_in_all_nodes(self):
        """MB4j: source_id in every node's sources list."""
        g = self._extract(PAR_CSV)
        for nid, node in g.nodes.items():
            self.assertIn(self.SRC, node["sources"], f"{nid} missing source")


# ── MB5: GraphBuilder mechanics ───────────────────────────────────────────────

class TestGraphBuilder(unittest.TestCase):
    """MB5: GraphBuilder node/edge dedup, merge, and referential integrity."""

    def setUp(self):
        self.g = mb.GraphBuilder("src-test")

    def test_MB5a_first_node_inserted(self):
        """MB5a: Adding a node inserts it into g.nodes."""
        self.g.node("company:acme", "company", "Acme")
        self.assertIn("company:acme", self.g.nodes)

    def test_MB5b_duplicate_node_no_duplicate_key(self):
        """MB5b: Calling node() twice with same id yields exactly 1 entry."""
        self.g.node("company:acme", "company", "Acme", attributes={"x": 1})
        self.g.node("company:acme", "company", "Acme", attributes={"y": 2})
        self.assertEqual(len(self.g.nodes), 1)

    def test_MB5c_duplicate_node_attributes_merged(self):
        """MB5c: Duplicate node call merges attributes from both calls."""
        self.g.node("company:acme", "company", "Acme", attributes={"x": 1})
        self.g.node("company:acme", "company", "Acme", attributes={"y": 2})
        attrs = self.g.nodes["company:acme"]["attributes"]
        self.assertIn("x", attrs)
        self.assertIn("y", attrs)

    def test_MB5d_edge_added_when_both_endpoints_exist(self):
        """MB5d: edge() adds an edge when both from/to nodes exist."""
        self.g.node("company:acme", "company", "Acme")
        self.g.node("company:bravo", "company", "Bravo")
        self.g.edge("partners_with", "company:acme", "company:bravo")
        self.assertEqual(len(self.g.edges), 1)

    def test_MB5e_edge_dropped_when_from_missing(self):
        """MB5e: edge() is a no-op when 'from' node is absent."""
        self.g.node("company:bravo", "company", "Bravo")
        self.g.edge("partners_with", "company:missing", "company:bravo")
        self.assertEqual(len(self.g.edges), 0)

    def test_MB5f_edge_dropped_when_to_missing(self):
        """MB5f: edge() is a no-op when 'to' node is absent."""
        self.g.node("company:acme", "company", "Acme")
        self.g.edge("partners_with", "company:acme", "company:missing")
        self.assertEqual(len(self.g.edges), 0)

    def test_MB5g_duplicate_edge_not_doubled(self):
        """MB5g: Adding the same edge twice yields exactly 1 edge entry."""
        self.g.node("company:a", "company", "A")
        self.g.node("company:b", "company", "B")
        self.g.edge("related_to", "company:a", "company:b")
        self.g.edge("related_to", "company:a", "company:b")
        self.assertEqual(len(self.g.edges), 1)

    def test_MB5h_to_graph_json_has_contract(self):
        """MB5h: to_graph_json() includes 'contract' field."""
        art = STUB_REGISTRY["artifacts"][0]
        src = {"source_id": "s1", "source_type": "text"}
        gj = self.g.to_graph_json(art, src)
        self.assertEqual(gj["contract"], "rb_micro_graph_v1")

    def test_MB5i_to_graph_json_node_count_correct(self):
        """MB5i: to_graph_json node_count matches len(nodes)."""
        art = STUB_REGISTRY["artifacts"][0]
        self.g.node("company:x", "company", "X")
        gj = self.g.to_graph_json(art, {"source_id": "s1"})
        self.assertEqual(gj["node_count"], 1)
        self.assertEqual(len(gj["nodes"]), 1)

    def test_MB5j_stats_empty_builder(self):
        """MB5j: Empty GraphBuilder stats returns zeros."""
        s = self.g.stats()
        self.assertEqual(s["node_count"], 0)
        self.assertEqual(s["edge_count"], 0)
        self.assertEqual(s["node_types"], {})
        self.assertEqual(s["edge_types"], {})


# ── MB6: Dry-run / needs-confirm ─────────────────────────────────────────────

class TestDryRun(unittest.TestCase):
    """MB6: build_graph with write=False (dry run) and write=True / confirm=False."""

    def tearDown(self):
        if hasattr(self, "_tmp_dir"):
            shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _run(self, write=False, confirm=False) -> dict:
        result, tmp_dir, reg_path, graph_base = _build_par(
            write=write, confirm=confirm
        )
        self._tmp_dir = tmp_dir
        return result

    def test_MB6a_dry_run_write_status(self):
        """MB6a: write=False → write_status='dry_run'."""
        result = self._run(write=False)
        self.assertEqual(result["write_status"], "dry_run")

    def test_MB6b_dry_run_no_files_written(self):
        """MB6b: dry_run does not create any graph files."""
        result, tmp_dir, reg_path, graph_base = _build_par(write=False)
        self._tmp_dir = tmp_dir
        graph_slug = result["graph_slug"]
        graph_dir = graph_base / graph_slug
        self.assertFalse(graph_dir.exists(), f"Graph dir should not exist: {graph_dir}")

    def test_MB6c_dry_run_has_artifacts_to_write(self):
        """MB6c: dry_run result includes 'artifacts_to_write' list."""
        result = self._run(write=False)
        self.assertIn("artifacts_to_write", result)
        self.assertIsInstance(result["artifacts_to_write"], list)
        self.assertGreater(len(result["artifacts_to_write"]), 0)

    def test_MB6d_dry_run_has_stats(self):
        """MB6d: dry_run result includes 'stats' with node_count >= 1."""
        result = self._run(write=False)
        self.assertIn("stats", result)
        self.assertGreaterEqual(result["stats"]["node_count"], 1)

    def test_MB6e_needs_confirm_write_status(self):
        """MB6e: write=True, confirm=False → write_status='needs_confirm'."""
        result = self._run(write=True, confirm=False)
        self.assertEqual(result["write_status"], "needs_confirm")

    def test_MB6f_needs_confirm_no_files_written(self):
        """MB6f: needs_confirm does not write files."""
        result, tmp_dir, reg_path, graph_base = _build_par(write=True, confirm=False)
        self._tmp_dir = tmp_dir
        graph_slug = result["graph_slug"]
        graph_dir = graph_base / graph_slug
        self.assertFalse(graph_dir.exists())

    def test_MB6g_dry_run_has_proposed_lineage_entry(self):
        """MB6g: dry_run result includes 'proposed_source_lineage_entry'."""
        result = self._run(write=False)
        self.assertIn("proposed_source_lineage_entry", result)
        entry = result["proposed_source_lineage_entry"]
        self.assertIn("source_id", entry)
        self.assertIn("source_type", entry)

    def test_MB6h_dry_run_proposed_artifact_status_building(self):
        """MB6h: dry_run shows proposed_artifact_status='building' for stubs."""
        result = self._run(write=False)
        self.assertEqual(result["proposed_artifact_status"], "building")


# ── MB7: Write + confirm ──────────────────────────────────────────────────────

class TestWriteConfirm(unittest.TestCase):
    """MB7: build_graph with write=True, confirm=True — full persist path."""

    def setUp(self):
        result, self.tmp_dir, self.reg_path, self.graph_base = _build_par(
            write=True, confirm=True
        )
        self.result = result

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _graph_dir(self) -> Path:
        return self.graph_base / self.result["graph_slug"]

    def test_MB7a_write_status_written(self):
        """MB7a: write_status='written' after successful persist."""
        self.assertEqual(self.result["write_status"], "written")

    def test_MB7b_graph_json_created(self):
        """MB7b: graph.json file is created."""
        self.assertTrue((self._graph_dir() / "graph.json").exists())

    def test_MB7c_sources_json_created(self):
        """MB7c: sources.json file is created."""
        self.assertTrue((self._graph_dir() / "sources.json").exists())

    def test_MB7d_index_json_created(self):
        """MB7d: index.json file is created."""
        self.assertTrue((self._graph_dir() / "index.json").exists())

    def test_MB7e_activation_json_created(self):
        """MB7e: activation.json file is created."""
        self.assertTrue((self._graph_dir() / "activation.json").exists())

    def test_MB7f_graph_json_has_correct_schema(self):
        """MB7f: graph.json has 'contract', 'nodes', 'edges' fields."""
        gj = json.loads((self._graph_dir() / "graph.json").read_text())
        self.assertEqual(gj["contract"], "rb_micro_graph_v1")
        self.assertIn("nodes", gj)
        self.assertIn("edges", gj)

    def test_MB7g_graph_json_nodes_non_empty(self):
        """MB7g: graph.json has at least 1 node (anchor entity)."""
        gj = json.loads((self._graph_dir() / "graph.json").read_text())
        self.assertGreater(len(gj["nodes"]), 0)

    def test_MB7h_sources_json_is_list(self):
        """MB7h: sources.json is a JSON list."""
        sj = json.loads((self._graph_dir() / "sources.json").read_text())
        self.assertIsInstance(sj, list)
        self.assertGreater(len(sj), 0)

    def test_MB7i_artifacts_written_list_non_empty(self):
        """MB7i: result['artifacts_written'] lists all written files."""
        self.assertIn("artifacts_written", self.result)
        self.assertGreater(len(self.result["artifacts_written"]), 0)

    def test_MB7j_registry_updated_on_disk(self):
        """MB7j: Registry file on disk is updated after write."""
        reg = json.loads(self.reg_path.read_text())
        art = mb._get_artifact(reg, "micro_graph:par_technology")
        self.assertIsNotNone(art)
        self.assertEqual(art["status"], "building")


# ── MB8: Activation / index file content ─────────────────────────────────────

class TestActivationIndex(unittest.TestCase):
    """MB8: _build_activation and _build_index output correctness."""

    ART = STUB_REGISTRY["artifacts"][0]  # PAR Technology
    GRAPH_ID = "micro_ecosystem:par-technology"

    def test_MB8a_activation_has_default_state_dormant(self):
        """MB8a: activation default_state is 'dormant'."""
        act = mb._build_activation(self.ART, self.GRAPH_ID)
        self.assertEqual(act["default_state"], "dormant")

    def test_MB8b_activation_primary_terms_includes_entity(self):
        """MB8b: primary_terms includes the entity name (lowercased)."""
        act = mb._build_activation(self.ART, self.GRAPH_ID)
        entity_lower = self.ART["entity"].lower()
        self.assertIn(entity_lower, act["primary_terms"])

    def test_MB8c_activation_trigger_terms_present(self):
        """MB8c: primary_terms includes at least one triage_trigger_term."""
        act = mb._build_activation(self.ART, self.GRAPH_ID)
        for term in self.ART.get("triage_trigger_terms", [])[:3]:
            self.assertIn(term.lower(), act["primary_terms"])

    def test_MB8d_activation_query_intents_from_question_domains(self):
        """MB8d: query_intents comes from artifact's question_domains."""
        act = mb._build_activation(self.ART, self.GRAPH_ID)
        self.assertIsInstance(act["query_intents"], list)
        self.assertGreater(len(act["query_intents"]), 0)

    def test_MB8e_index_contract_field(self):
        """MB8e: index has contract='rb_micro_graph_index_v1'."""
        idx = mb._build_index(self.ART, self.GRAPH_ID, {"node_count": 5, "edge_count": 2})
        self.assertEqual(idx["contract"], "rb_micro_graph_index_v1")

    def test_MB8f_index_has_entity_name(self):
        """MB8f: index 'entity' matches artifact entity."""
        idx = mb._build_index(self.ART, self.GRAPH_ID, {})
        self.assertEqual(idx["entity"], self.ART["entity"])

    def test_MB8g_index_confidence_summary_has_node_count(self):
        """MB8g: index confidence_summary includes node_count."""
        idx = mb._build_index(self.ART, self.GRAPH_ID, {"node_count": 7, "edge_count": 3})
        self.assertEqual(idx["confidence_summary"]["node_count"], 7)

    def test_MB8h_index_has_freshness_date(self):
        """MB8h: index has freshness_date (today's date)."""
        idx = mb._build_index(self.ART, self.GRAPH_ID, {})
        self.assertRegex(idx["freshness_date"], r"^\d{4}-\d{2}-\d{2}$")


# ── MB9: Global index update ──────────────────────────────────────────────────

class TestGlobalIndexUpdate(unittest.TestCase):
    """MB9: _update_global_index correctly adds/replaces entries."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.idx_path = self.tmp_dir / "global_index.json"

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _update(self, artifact=None, graph_id=None, graph_slug=None, stats=None):
        art = artifact or STUB_REGISTRY["artifacts"][0]
        gid = graph_id or "micro_ecosystem:par-technology"
        slug = graph_slug or "par-technology"
        s = stats or {"node_count": 5, "edge_count": 2}
        mb._update_global_index(art, gid, slug, s, self.idx_path)

    def test_MB9a_creates_index_if_absent(self):
        """MB9a: Creates global index file if it doesn't exist."""
        self.assertFalse(self.idx_path.exists())
        self._update()
        self.assertTrue(self.idx_path.exists())

    def test_MB9b_entry_added(self):
        """MB9b: Graph entry is added to global index."""
        self._update()
        idx = json.loads(self.idx_path.read_text())
        self.assertGreater(len(idx["graphs"]), 0)

    def test_MB9c_entry_graph_id_correct(self):
        """MB9c: Added entry has correct graph_id."""
        self._update()
        idx = json.loads(self.idx_path.read_text())
        gids = [g["graph_id"] for g in idx["graphs"]]
        self.assertIn("micro_ecosystem:par-technology", gids)

    def test_MB9d_entity_name_in_entry(self):
        """MB9d: Added entry includes entity_name."""
        self._update()
        idx = json.loads(self.idx_path.read_text())
        entry = next(g for g in idx["graphs"]
                     if g["graph_id"] == "micro_ecosystem:par-technology")
        self.assertEqual(entry["entity_name"], "PAR Technology")

    def test_MB9e_duplicate_update_replaces_not_appends(self):
        """MB9e: Calling update twice for same graph_id yields exactly 1 entry."""
        self._update()
        self._update()
        idx = json.loads(self.idx_path.read_text())
        matching = [g for g in idx["graphs"]
                    if g["graph_id"] == "micro_ecosystem:par-technology"]
        self.assertEqual(len(matching), 1)

    def test_MB9f_existing_mcd_entry_preserved(self):
        """MB9f: An existing McDonald's entry is preserved when PAR is added."""
        # Seed the index with McDonald's
        seed = {
            "contract": "rb_micro_graph_presence_index_v1",
            "graphs": [
                {"graph_id": "micro_ecosystem:mcdonalds_us_ops",
                 "entity_name": "McDonald's"},
            ],
            "version": 1,
        }
        self.idx_path.write_text(json.dumps(seed))
        self._update()
        idx = json.loads(self.idx_path.read_text())
        gids = {g["graph_id"] for g in idx["graphs"]}
        self.assertIn("micro_ecosystem:mcdonalds_us_ops", gids)
        self.assertIn("micro_ecosystem:par-technology", gids)

    def test_MB9g_available_flag_true(self):
        """MB9g: Added entry has available=True."""
        self._update()
        idx = json.loads(self.idx_path.read_text())
        entry = next(g for g in idx["graphs"]
                     if g["graph_id"] == "micro_ecosystem:par-technology")
        self.assertTrue(entry["available"])

    def test_MB9h_node_count_in_confidence_summary(self):
        """MB9h: confidence_summary includes node_count from stats."""
        self._update(stats={"node_count": 42, "edge_count": 10})
        idx = json.loads(self.idx_path.read_text())
        entry = next(g for g in idx["graphs"]
                     if g["graph_id"] == "micro_ecosystem:par-technology")
        self.assertEqual(entry["confidence_summary"]["node_count"], 42)


# ── MB10: Multi-source stacking ───────────────────────────────────────────────

class TestMultiSourceStack(unittest.TestCase):
    """MB10: Two sequential enrichments stack in sources.json and registry."""

    def setUp(self):
        # First enrichment
        result1, self.tmp_dir, self.reg_path, self.graph_base = _build_par(
            text=PAR_TEXT, write=True, confirm=True
        )
        self.graph_slug = result1["graph_slug"]
        self.global_idx = self.tmp_dir / "global_index.json"

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _second_enrich(self) -> dict:
        reg = json.loads(self.reg_path.read_text())
        global_idx_path = self.tmp_dir / "global_index.json"
        return mb.build_graph(
            artifact_id="micro_graph:par_technology",
            text="Second source: additional PAR customers and partner ecosystem data.",
            source_type="text",
            source_name="second_source.txt",
            registry_path=self.reg_path,
            graph_base_dir=self.graph_base,
            global_index_path=global_idx_path,
            write=True,
            confirm=True,
        )

    def test_MB10a_second_write_succeeds(self):
        """MB10a: Second build_graph confirm=True returns write_status=written."""
        result2 = self._second_enrich()
        self.assertEqual(result2["write_status"], "written")

    def test_MB10b_sources_json_has_two_entries(self):
        """MB10b: sources.json has 2 entries after two enrichments."""
        self._second_enrich()
        sources_path = self.graph_base / self.graph_slug / "sources.json"
        sources = json.loads(sources_path.read_text())
        self.assertEqual(len(sources), 2)

    def test_MB10c_registry_enrichment_count_increments(self):
        """MB10c: Registry enrichment_count is 2 after two writes."""
        self._second_enrich()
        reg = json.loads(self.reg_path.read_text())
        art = mb._get_artifact(reg, "micro_graph:par_technology")
        self.assertEqual(art["enrichment_count"], 2)

    def test_MB10d_registry_source_lineage_has_two_entries(self):
        """MB10d: Registry source_lineage has 2 entries after two writes."""
        self._second_enrich()
        reg = json.loads(self.reg_path.read_text())
        art = mb._get_artifact(reg, "micro_graph:par_technology")
        self.assertEqual(len(art.get("source_lineage", [])), 2)

    def test_MB10e_status_stays_building(self):
        """MB10e: Status stays 'building' after second enrichment (not re-promoted)."""
        self._second_enrich()
        reg = json.loads(self.reg_path.read_text())
        art = mb._get_artifact(reg, "micro_graph:par_technology")
        self.assertEqual(art["status"], "building")

    def test_MB10f_graph_json_overwritten(self):
        """MB10f: Second write overwrites graph.json with latest extraction."""
        result2 = self._second_enrich()
        gj = json.loads((self.graph_base / self.graph_slug / "graph.json").read_text())
        # Latest source_record name should be second_source.txt
        self.assertEqual(gj["source_record"]["source_name"], "second_source.txt")

    def test_MB10g_global_index_still_has_one_entry_for_par(self):
        """MB10g: Global index has exactly 1 PAR entry after two writes."""
        self._second_enrich()
        idx_path = self.tmp_dir / "global_index.json"
        if idx_path.exists():
            idx = json.loads(idx_path.read_text())
            par_entries = [g for g in idx["graphs"]
                           if g["graph_id"] == "micro_ecosystem:par-technology"]
            self.assertEqual(len(par_entries), 1)


# ── MB11: Error cases ─────────────────────────────────────────────────────────

class TestErrorCases(unittest.TestCase):
    """MB11: Error handling for missing artifact, bad JSON, empty CSV."""

    def _run(self, artifact_id: str, text: str, source_type: str = "text") -> dict:
        tmp_dir, reg_path = _make_temp_registry(STUB_REGISTRY)
        graph_base = _make_graph_base(tmp_dir)
        global_idx = _make_temp_global_index(tmp_dir)
        try:
            return mb.build_graph(
                artifact_id=artifact_id,
                text=text,
                source_type=source_type,
                registry_path=reg_path,
                graph_base_dir=graph_base,
                global_index_path=global_idx,
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_MB11a_unknown_artifact_not_found(self):
        """MB11a: Unknown artifact_id → status='not_found'."""
        result = self._run("micro_graph:unknown_entity", "some text")
        self.assertEqual(result["status"], "not_found")

    def test_MB11b_bad_json_error(self):
        """MB11b: Malformed JSON text → status='error'."""
        result = self._run("micro_graph:par_technology", "not json {{{", "json")
        self.assertEqual(result["status"], "error")

    def test_MB11c_empty_csv_only_anchor(self):
        """MB11c: Empty CSV text → graph with only anchor node."""
        result = self._run("micro_graph:par_technology", "", "csv")
        # Should succeed (dry run), anchor node present
        self.assertNotIn("error", result)
        self.assertGreaterEqual(result["stats"]["node_count"], 1)

    def test_MB11d_not_found_result_has_artifact_id(self):
        """MB11d: not_found result echoes artifact_id."""
        result = self._run("micro_graph:bogus", "text")
        self.assertEqual(result["artifact_id"], "micro_graph:bogus")

    def test_MB11e_write_without_confirm_no_files(self):
        """MB11e: write=True, confirm=False leaves no graph files on disk."""
        tmp_dir, reg_path = _make_temp_registry(STUB_REGISTRY)
        graph_base = _make_graph_base(tmp_dir)
        global_idx = _make_temp_global_index(tmp_dir)
        try:
            result = mb.build_graph(
                artifact_id="micro_graph:par_technology",
                text=PAR_TEXT,
                registry_path=reg_path,
                graph_base_dir=graph_base,
                global_index_path=global_idx,
                write=True,
                confirm=False,
            )
            slug = result["graph_slug"]
            self.assertFalse((graph_base / slug).exists())
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_MB11f_source_record_sha256_present(self):
        """MB11f: source_record always has sha256_prefix."""
        src_id, rec = mb._build_source_record(None, "text", "paste", "hello", "micro_graph:par_technology")
        self.assertIn("sha256_prefix", rec)
        self.assertGreater(len(rec["sha256_prefix"]), 0)


# ── MB12: CLI smoke ───────────────────────────────────────────────────────────

class TestCliSmoke(unittest.TestCase):
    """MB12: CLI --list, --json, stdin smoke tests."""

    def test_MB12a_list_artifacts_runs(self):
        """MB12a: _list_artifacts() runs without error on the real registry."""
        # Just assert it doesn't raise
        import io as _io
        from contextlib import redirect_stdout
        f = _io.StringIO()
        with redirect_stdout(f):
            mb._list_artifacts(REAL_REGISTRY)
        output = f.getvalue()
        self.assertIn("micro_graph:par_technology", output)

    def test_MB12b_auto_source_type_csv(self):
        """MB12b: _auto_source_type detects csv from .csv extension."""
        p = Path("foo.csv")
        self.assertEqual(mb._auto_source_type(p), "csv")

    def test_MB12c_auto_source_type_json(self):
        """MB12c: _auto_source_type detects json from .json extension."""
        p = Path("bar.json")
        self.assertEqual(mb._auto_source_type(p), "json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
