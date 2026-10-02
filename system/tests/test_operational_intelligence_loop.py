"""
test_operational_intelligence_loop.py — RB 9.16 Sprint tests.

Covers Deliverables 1–7:
  1. Vendor relationship seed structure
  2. Confidence model (two-state decay + sticky-tech lock)
  3. Watch list schema and CLI
  4. Ecosystem brief section structure
  5. CoS activation signal classification
  6. Interrupt queue logic
  7. API response shapes (getMicroGraphIndex / getWatchList)
"""
import importlib.util
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "system" / "scripts" / "ecosystem_intelligence.py"
GRAPH_PATH = ROOT / "system" / "ecosystem_intelligence.json"
MICRO_INDEX_PATH = ROOT / "system" / "graphs" / "micro" / "index.json"

# ---------------------------------------------------------------------------
# Load ecosystem_intelligence module
# ---------------------------------------------------------------------------
spec = importlib.util.spec_from_file_location("ecosystem_intelligence", SCRIPT)
ei = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ei)


def _empty_graph() -> dict:
    return {
        "sources": [],
        "entities": [],
        "relationships": [],
        "signals": [],
        "assessments": [],
        "user_relevance": [],
        "strategic_recommendations": [],
        "watch_list": [],
    }


# ---------------------------------------------------------------------------
# 1. Vendor relationship seed
# ---------------------------------------------------------------------------
class VendorSeedTests(unittest.TestCase):
    def setUp(self):
        if not GRAPH_PATH.exists():
            self.skipTest("ecosystem_intelligence.json not present")
        self.graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))

    def test_graph_has_entities(self):
        self.assertGreater(len(self.graph.get("entities", [])), 0)

    def test_brand_entities_have_required_fields(self):
        brand_entities = [e for e in self.graph["entities"] if e.get("entity_type") == "brand"]
        self.assertGreater(len(brand_entities), 0, "Expected at least one brand entity")
        for e in brand_entities[:5]:
            self.assertIn("id", e, f"Brand entity missing id: {e}")
            self.assertIn("name", e, f"Brand entity missing name: {e}")

    def test_relationships_have_provenance(self):
        rels = self.graph.get("relationships", [])
        if not rels:
            self.skipTest("No relationships in graph yet")
        # Relationships use from_entity_id / to_entity_id (ecosystem schema v2+)
        for rel in rels[:5]:
            self.assertIn("from_entity_id", rel, f"Relationship missing from_entity_id: {rel}")
            self.assertIn("to_entity_id", rel, f"Relationship missing to_entity_id: {rel}")
            self.assertIn("relationship_type", rel)

    def test_confidence_posture_values_are_valid(self):
        rels = self.graph.get("relationships", [])
        # evidence_posture is the top-level field; confidence.posture is optional
        valid_postures = {"provisional", "partially_substantiated", "substantiated"}
        for rel in rels:
            posture = rel.get("evidence_posture")
            if posture:
                self.assertIn(posture, valid_postures,
                              f"Invalid evidence_posture '{posture}' in relationship {rel.get('id')}")


# ---------------------------------------------------------------------------
# 2. Confidence model — two-state decay and sticky-tech lock
# ---------------------------------------------------------------------------
class ConfidenceModelTests(unittest.TestCase):
    def test_sticky_tech_categories_constant_exists(self):
        self.assertTrue(hasattr(ei, "STICKY_TECH_CATEGORIES"), "STICKY_TECH_CATEGORIES constant missing")
        self.assertIn("pos", ei.STICKY_TECH_CATEGORIES)
        self.assertIn("payments", ei.STICKY_TECH_CATEGORIES)

    def test_promote_confidence_function_exists(self):
        self.assertTrue(hasattr(ei, "promote_confidence"), "promote_confidence function missing")

    def test_check_staleness_function_exists(self):
        self.assertTrue(hasattr(ei, "check_staleness"), "check_staleness function missing")

    def test_check_staleness_flags_expired_provisional(self):
        """A provisional relationship past review_after should be flagged stale."""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        graph = _empty_graph()
        graph["entities"].append({"id": "brand-test-a", "name": "Test A", "entity_type": "brand"})
        graph["entities"].append({"id": "vendor-test-b", "name": "Test B", "entity_type": "vendor"})
        graph["relationships"].append({
            "id": "rel-001",
            "from_entity_id": "brand-test-a",
            "to_entity_id": "vendor-test-b",
            "relationship_type": "uses_pos",
            "category": "pos",
            "evidence_posture": "provisional",
            "confidence": {
                "level": "low",
                "review_after": yesterday,
            },
            "staleness_flag": None,
        })
        updated = ei._check_staleness_on_graph(graph, date.today())
        rel = next(r for r in updated["relationships"] if r["id"] == "rel-001")
        self.assertTrue(rel.get("staleness_flag"), "Expected staleness_flag=True for expired provisional relationship")

    def test_check_staleness_does_not_flag_substantiated_sticky(self):
        """Substantiated + sticky-tech relationship should not be flagged stale regardless of date."""
        graph = _empty_graph()
        graph["entities"].append({"id": "brand-test-c", "name": "Test C", "entity_type": "brand"})
        graph["entities"].append({"id": "vendor-test-d", "name": "Test D", "entity_type": "vendor"})
        graph["relationships"].append({
            "id": "rel-002",
            "from_entity_id": "brand-test-c",
            "to_entity_id": "vendor-test-d",
            "relationship_type": "uses_pos",
            "category": "pos",
            "evidence_posture": "substantiated",
            "confidence": {
                "level": "high",
                "review_after": None,
            },
            "staleness_flag": None,
        })
        updated = ei._check_staleness_on_graph(graph, date.today())
        rel = next(r for r in updated["relationships"] if r["id"] == "rel-002")
        self.assertFalse(rel.get("staleness_flag"), "Substantiated+sticky relationship must not be flagged stale")


# ---------------------------------------------------------------------------
# 3. Watch list schema and CLI functions
# ---------------------------------------------------------------------------
class WatchListTests(unittest.TestCase):
    def test_watch_list_cmd_function_exists(self):
        self.assertTrue(hasattr(ei, "watch_list_cmd"), "watch_list_cmd function missing")

    def test_watch_list_structure_in_graph(self):
        if not GRAPH_PATH.exists():
            self.skipTest("ecosystem_intelligence.json not present")
        graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
        self.assertIn("watch_list", graph, "watch_list key missing from graph root")
        self.assertIsInstance(graph["watch_list"], list)

    def test_watch_list_entries_have_valid_tiers(self):
        if not GRAPH_PATH.exists():
            self.skipTest("ecosystem_intelligence.json not present")
        graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
        valid_tiers = {"tier_1", "tier_2", "tier_3"}
        for entry in graph["watch_list"]:
            self.assertIn("entity_id", entry, f"Watch list entry missing entity_id: {entry}")
            self.assertIn("priority", entry, f"Watch list entry missing priority: {entry}")
            self.assertIn(entry["priority"], valid_tiers, f"Invalid tier: {entry['priority']}")

    def test_watch_list_interrupt_eligible_field(self):
        if not GRAPH_PATH.exists():
            self.skipTest("ecosystem_intelligence.json not present")
        graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
        for entry in graph["watch_list"]:
            self.assertIn("interrupt_eligible", entry,
                          f"Watch list entry missing interrupt_eligible: {entry}")
            self.assertIsInstance(entry["interrupt_eligible"], bool)


# ---------------------------------------------------------------------------
# 4. Ecosystem brief section structure
# ---------------------------------------------------------------------------
class EcosystemBriefSectionTests(unittest.TestCase):
    def setUp(self):
        try:
            spec2 = importlib.util.spec_from_file_location(
                "ecosystem_brief",
                ROOT / "system" / "scripts" / "ecosystem_brief.py",
            )
            self.eb = importlib.util.module_from_spec(spec2)
            spec2.loader.exec_module(self.eb)
        except Exception as exc:
            self.skipTest(f"ecosystem_brief.py import failed: {exc}")

    def test_build_section_returns_dict(self):
        result = self.eb.build_section(today=date.today())
        self.assertIsInstance(result, dict, "build_section must return a dict")

    def test_section_has_required_keys(self):
        result = self.eb.build_section(today=date.today())
        required_keys = [
            "watch_list_signals",
            "staleness_flags",
            "ambient_surface",
            "mutation_log",
            "needs_todd",
        ]
        for key in required_keys:
            self.assertIn(key, result, f"ecosystem brief section missing key: {key}")

    def test_section_values_are_lists(self):
        result = self.eb.build_section(today=date.today())
        for key in ("watch_list_signals", "staleness_flags", "ambient_surface",
                    "mutation_log", "needs_todd"):
            self.assertIsInstance(result[key], list, f"Expected list for {key}")


# ---------------------------------------------------------------------------
# 5. CoS activation signal classification
# ---------------------------------------------------------------------------
class SignalClassificationTests(unittest.TestCase):
    def setUp(self):
        try:
            spec2 = importlib.util.spec_from_file_location(
                "ecosystem_brief",
                ROOT / "system" / "scripts" / "ecosystem_brief.py",
            )
            self.eb = importlib.util.module_from_spec(spec2)
            spec2.loader.exec_module(self.eb)
        except Exception as exc:
            self.skipTest(f"ecosystem_brief.py import failed: {exc}")

    def test_classify_leadership_change(self):
        cls = self.eb._classify_signal("New CEO appointed at Wendy's", "Jane Smith named CEO")
        self.assertEqual(cls, "leadership_change")

    def test_classify_rfp_cycle_signal(self):
        cls = self.eb._classify_signal("Pizza chain issuing RFP for POS replacement", "")
        self.assertIn(cls, ("rfp_cycle_signal", "vendor_displacement"))

    def test_classify_extreme_pain(self):
        cls = self.eb._classify_signal("Restaurant chain system outage", "POS system failure causing operational disruption")
        self.assertIn(cls, ("extreme_pain", "vendor_displacement", "general_market_context"))

    def test_signal_class_rules_exist_for_activation_classes(self):
        self.assertTrue(hasattr(self.eb, "SIGNAL_CLASS_RULES"), "SIGNAL_CLASS_RULES constant missing")
        activation_classes = {"leadership_change", "rfp_cycle_signal", "extreme_pain", "vendor_displacement"}
        for cls in activation_classes:
            self.assertIn(cls, self.eb.SIGNAL_CLASS_RULES, f"Missing rule for {cls}")

    def test_interrupt_eligible_classes(self):
        interrupt_eligible = [
            cls for cls, (_, interrupt, _ambient) in self.eb.SIGNAL_CLASS_RULES.items()
            if interrupt
        ]
        self.assertGreater(len(interrupt_eligible), 0, "Expected at least one interrupt-eligible signal class")
        self.assertIn("leadership_change", interrupt_eligible)


# ---------------------------------------------------------------------------
# 6. Interrupt queue
# ---------------------------------------------------------------------------
class InterruptQueueTests(unittest.TestCase):
    def test_check_interrupt_queue_function_exists(self):
        self.assertTrue(hasattr(ei, "check_interrupt_queue"), "check_interrupt_queue function missing")

    def test_interrupt_queue_file_is_valid_jsonl(self):
        # RB-2026-08-28: these field names (entity_id, signal_class) never
        # matched check_interrupt_queue()'s real, working record schema --
        # confirmed against both the live production file and the writer
        # code (ecosystem_intelligence.py's check_interrupt_queue, ~line
        # 3538): the real fields are entity_ids (a list -- a signal can
        # touch more than one entity) and signal_type. This test's actual
        # intent (every real interrupt record is valid JSON with a real
        # signal id, entity reference(s), and a signal classification)
        # still holds -- only the two field names were ever wrong.
        queue_path = ROOT / "system" / "inbox" / "ecosystem" / "interrupt_queue.jsonl"
        if not queue_path.exists():
            return  # no queue yet is acceptable
        for i, line in enumerate(queue_path.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                self.assertIn("signal_id", record, f"Interrupt record {i} missing signal_id")
                self.assertIn("entity_ids", record, f"Interrupt record {i} missing entity_ids")
                self.assertIn("signal_type", record, f"Interrupt record {i} missing signal_type")
            except json.JSONDecodeError as exc:
                self.fail(f"Interrupt queue line {i} is not valid JSON: {exc}")

    def test_interrupt_records_have_tier_field(self):
        # RB-2026-08-28: "watch_list_tier" was never part of the real record
        # schema -- tier-1 gating happens at write time (check_interrupt_queue
        # only ever queues a signal touching a tier_1-priority entity in the
        # first place; see its tier1_ids filter), not stored redundantly on
        # each queued record. Confirmed against the real writer code and the
        # live production file -- this was speculative/stale coverage, not a
        # field that regressed. Replaced with a check for what the real
        # schema actually uses to convey the same "why did this qualify"
        # information: a real, non-empty entity reference.
        queue_path = ROOT / "system" / "inbox" / "ecosystem" / "interrupt_queue.jsonl"
        if not queue_path.exists():
            return
        for line in queue_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            self.assertTrue(record.get("entity_ids"),
                             f"Interrupt record missing/empty entity_ids: {record}")


# ---------------------------------------------------------------------------
# 7. Micro graph index structure
# ---------------------------------------------------------------------------
class MicroGraphIndexTests(unittest.TestCase):
    def setUp(self):
        if not MICRO_INDEX_PATH.exists():
            self.skipTest("system/graphs/micro/index.json not present")
        self.index = json.loads(MICRO_INDEX_PATH.read_text(encoding="utf-8"))

    def test_index_has_contract_field(self):
        self.assertEqual(
            self.index.get("contract"),
            "rb_micro_graph_presence_index_v1",
        )

    def test_index_has_graphs_list(self):
        self.assertIn("graphs", self.index)
        self.assertIsInstance(self.index["graphs"], list)

    def test_mcdonalds_graph_is_present(self):
        graph_ids = [g["graph_id"] for g in self.index["graphs"]]
        self.assertIn("micro_ecosystem:mcdonalds_us_ops", graph_ids)

    def test_mcdonalds_entry_has_question_domains(self):
        entry = next(
            (g for g in self.index["graphs"]
             if g.get("graph_id") == "micro_ecosystem:mcdonalds_us_ops"),
            None,
        )
        self.assertIsNotNone(entry)
        self.assertIn("question_domains", entry)
        self.assertIn("franchisee_count", entry["question_domains"])

    def test_mcdonalds_entry_has_routing_action(self):
        entry = next(
            (g for g in self.index["graphs"]
             if g.get("graph_id") == "micro_ecosystem:mcdonalds_us_ops"),
            None,
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry.get("routing_action"), "getMicroGraphSummary")

    def test_trust_hierarchy_present(self):
        self.assertIn("trust_hierarchy", self.index)
        hierarchy = self.index["trust_hierarchy"]
        self.assertIn("rb_micro_graph", hierarchy)
        self.assertIn("general_model_knowledge", hierarchy)
        # rb_micro_graph must rank higher than general_model_knowledge
        self.assertLess(
            hierarchy.index("rb_micro_graph"),
            hierarchy.index("general_model_knowledge"),
        )


if __name__ == "__main__":
    unittest.main()
