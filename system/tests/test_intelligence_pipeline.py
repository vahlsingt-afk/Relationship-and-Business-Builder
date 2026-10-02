"""
test_intelligence_pipeline.py — RB 9.39 / DEFECT-022+023+024
Intelligence Pipeline Spec, Mutation/Loop Distinction, and Strategic Intent Detection.

DEFECT-022 addressed:
  Screenshot/artifact ingestion doesn't trigger full 7-phase CoS pipeline.
  GPT summarizes and asks "what would you like to do?" instead of auto-executing.

DEFECT-023 addressed:
  Intelligence ingestion stops at analysis. Mutations not auto-executed, loops not offered.
  Mutation vs. loop distinction not codified: mutations = auto; loops = ask permission.

DEFECT-024 addressed:
  CoS doesn't elevate tactical questions to strategic context.
  Level 1-5 synthesis pattern not enforced: direct answer → strategic objective →
  opportunity connections → intelligence gaps → next actions.

Test groups:
  IP1 (6):  _intelligence_pipeline_spec — structure, phases, contract keys
  IP2 (6):  build_canonical_brief integration — top-level key, spec keys, rendering_rules presence
  IP3 (6):  rendering_rules behavioral contracts — pipeline mandate, output format,
            mutation/loop distinction, Level 1-5 pattern, strategic intent detection
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SYNTHETIC_REPORT: dict = {
    "today": "2026-05-30",
    "relationship_signals": {
        "signals": [
            {"name": "Alice Chen", "tier": "inner", "drr_score": 0.82},
            {"name": "Bob Gibson", "tier": "inner", "drr_score": 0.75},
        ],
        "stale_sources": [],
    },
    "loops": {},
    "active_threads": [],
    "market_signals": {"signals": [], "freshness_status": "ok"},
    "strategic_memory": {},
    "daily_prep_summary": {
        "totals": {"inner": 2, "broader": 0, "dormant_valuable": 0},
        "source_health": {"sources": {"email": {"status": "fresh"}}},
        "signals": [],
        "overdue_loops": [],
        "stale_sources": [],
        "meeting_prep": [],
    },
}


# ---------------------------------------------------------------------------
# IP1 — _intelligence_pipeline_spec structure
# ---------------------------------------------------------------------------

class IP1PipelineSpecStructureTests(unittest.TestCase):
    """Unit tests for _intelligence_pipeline_spec()."""

    def setUp(self):
        self.spec = db._intelligence_pipeline_spec()

    def test_IP1a_returns_dict(self):
        """_intelligence_pipeline_spec returns a dict."""
        self.assertIsInstance(self.spec, dict)

    def test_IP1b_has_phases_list_of_seven(self):
        """'phases' key contains exactly 7 phase dicts."""
        phases = self.spec.get("phases")
        self.assertIsInstance(phases, list)
        self.assertEqual(len(phases), 7,
                         f"Expected 7 phases, got {len(phases)}: {[p.get('name') for p in phases]}")

    def test_IP1c_all_required_phase_names_present(self):
        """All 7 required phase names are present in the spec."""
        required = {
            "content_intelligence_extraction",
            "cos_evaluation",
            "knowledge_graph_mutation",
            "watchlist_correlation",
            "content_engine_activation",
            "opportunity_impact_assessment",
            "daily_brief_integration",
        }
        actual = {p["name"] for p in self.spec.get("phases") or []}
        missing = required - actual
        self.assertEqual(missing, set(),
                         f"Missing phase names: {missing}")

    def test_IP1d_has_output_contract_with_format(self):
        """'output_contract' key exists and has a 'format' list."""
        contract = self.spec.get("output_contract")
        self.assertIsNotNone(contract, "output_contract key must exist")
        fmt = contract.get("format")
        self.assertIsInstance(fmt, list)
        self.assertGreater(len(fmt), 0, "output_contract.format must be non-empty")

    def test_IP1e_mutation_vs_loop_distinction_present(self):
        """'mutation_vs_loop_distinction' has 'mutations' and 'loops' keys."""
        distinction = self.spec.get("mutation_vs_loop_distinction")
        self.assertIsNotNone(distinction,
                             "mutation_vs_loop_distinction key must exist")
        self.assertIn("mutations", distinction)
        self.assertIn("loops", distinction)
        # Mutations must say automatic / no approval
        mutations_text = distinction["mutations"].lower()
        self.assertTrue(
            "automatic" in mutations_text or "auto" in mutations_text,
            f"mutations clause must state automatic execution: {distinction['mutations']!r}"
        )
        # Loops must say permission / ask
        loops_text = distinction["loops"].lower()
        self.assertTrue(
            "permission" in loops_text or "ask" in loops_text,
            f"loops clause must require permission: {distinction['loops']!r}"
        )

    def test_IP1f_strategic_intent_detection_has_five_levels(self):
        """'strategic_intent_detection' has exactly 5 levels numbered 1–5."""
        sid = self.spec.get("strategic_intent_detection")
        self.assertIsNotNone(sid, "strategic_intent_detection key must exist")
        levels = sid.get("levels")
        self.assertIsInstance(levels, list)
        self.assertEqual(len(levels), 5,
                         f"Expected 5 levels, got {len(levels)}")
        level_numbers = sorted(lvl["level"] for lvl in levels)
        self.assertEqual(level_numbers, [1, 2, 3, 4, 5],
                         f"Level numbers must be 1–5: {level_numbers}")


# ---------------------------------------------------------------------------
# IP2 — build_canonical_brief integration
# ---------------------------------------------------------------------------

class IP2BriefIntegrationTests(unittest.TestCase):
    """Integration tests for intelligence_pipeline_spec in the canonical brief."""

    def setUp(self):
        self.brief = db.build_canonical_brief(_SYNTHETIC_REPORT)

    def test_IP2a_intelligence_pipeline_spec_is_top_level_key(self):
        """intelligence_pipeline_spec is a top-level key in the brief return dict."""
        self.assertIn("intelligence_pipeline_spec", self.brief,
                      "intelligence_pipeline_spec must be a top-level key in the brief")

    def test_IP2b_pipeline_spec_has_seven_phases(self):
        """Top-level intelligence_pipeline_spec has 7 phases."""
        spec = self.brief.get("intelligence_pipeline_spec") or {}
        phases = spec.get("phases") or []
        self.assertEqual(len(phases), 7,
                         f"Expected 7 phases in brief.intelligence_pipeline_spec, got {len(phases)}")

    def test_IP2c_pipeline_spec_has_output_contract(self):
        """Top-level intelligence_pipeline_spec has output_contract."""
        spec = self.brief.get("intelligence_pipeline_spec") or {}
        self.assertIn("output_contract", spec)

    def test_IP2d_pipeline_spec_has_mutation_vs_loop_distinction(self):
        """Top-level intelligence_pipeline_spec has mutation_vs_loop_distinction."""
        spec = self.brief.get("intelligence_pipeline_spec") or {}
        self.assertIn("mutation_vs_loop_distinction", spec)

    def test_IP2e_pipeline_spec_has_strategic_intent_detection(self):
        """Top-level intelligence_pipeline_spec has strategic_intent_detection."""
        spec = self.brief.get("intelligence_pipeline_spec") or {}
        self.assertIn("strategic_intent_detection", spec)

    def test_IP2f_pipeline_spec_matches_standalone_function(self):
        """Brief's intelligence_pipeline_spec matches _intelligence_pipeline_spec() output."""
        standalone = db._intelligence_pipeline_spec()
        brief_spec = self.brief.get("intelligence_pipeline_spec") or {}
        # Phase count must match
        self.assertEqual(
            len(brief_spec.get("phases") or []),
            len(standalone.get("phases") or []),
        )
        # Contract key must match
        self.assertEqual(
            brief_spec.get("contract"),
            standalone.get("contract"),
        )


# ---------------------------------------------------------------------------
# IP3 — rendering_rules behavioral contracts
# ---------------------------------------------------------------------------

class IP3RenderingRulesBehavioralTests(unittest.TestCase):
    """Verify rendering_rules encode the specific behavioral contracts for DEFECT-022/023/024."""

    def setUp(self):
        brief = db.build_canonical_brief(_SYNTHETIC_REPORT)
        self.rules = " ".join(brief.get("rendering_rules") or [])
        self.rules_lower = self.rules.lower()

    def test_IP3a_rendering_rules_forbid_ask_what_to_do(self):
        """rendering_rules must instruct GPT not to ask 'what would you like to do' after artifact."""
        # The rule must reference the failure mode ("what would you like to do" or equivalent)
        self.assertTrue(
            "what would you like to do" in self.rules_lower
            or "do not summarize" in self.rules_lower
            or "automatically execute" in self.rules_lower,
            "rendering_rules must address the 'ask what to do' failure mode for artifacts"
        )

    def test_IP3b_rendering_rules_describe_output_format_sections(self):
        """rendering_rules describe the canonical pipeline output format (mutations + loops)."""
        self.assertIn("graph mutations executed", self.rules_lower,
                      "rendering_rules must specify 'Graph Mutations Executed' output section")
        self.assertIn("relationship relevance", self.rules_lower,
                      "rendering_rules must specify 'Relationship Relevance' output section")

    def test_IP3c_rendering_rules_include_loop_permission_language(self):
        """rendering_rules mandate asking permission before opening a tracking loop."""
        combined = self.rules_lower
        self.assertTrue(
            "would you like me to open" in combined or "ask permission" in combined
            or "loop creation" in combined,
            "rendering_rules must require loop creation permission"
        )

    def test_IP3d_rendering_rules_describe_level_1_through_5(self):
        """rendering_rules encode the Level 1-5 strategic intent detection pattern."""
        combined = self.rules_lower
        self.assertIn("level 1", combined,
                      "rendering_rules must reference Level 1 of strategic intent detection")
        self.assertIn("level 5", combined,
                      "rendering_rules must reference Level 5 (recommended next actions)")

    def test_IP3e_rendering_rules_distinguish_mutations_from_loops(self):
        """rendering_rules encode the mutation vs. loop distinction."""
        combined = self.rules_lower
        self.assertIn("automatic", combined,
                      "rendering_rules must state mutations are automatic")
        self.assertTrue(
            "permission" in combined or "ask" in combined,
            "rendering_rules must require permission for loop creation"
        )
        self.assertTrue(
            "mutation" in combined and "loop" in combined,
            "rendering_rules must mention both mutations and loops"
        )

    def test_IP3f_rendering_rules_address_strategic_intent_for_tactical_questions(self):
        """rendering_rules specify that tactical questions must be elevated to strategic context."""
        combined = self.rules_lower
        self.assertTrue(
            "never stop at level 1" in combined or "stop at level 1" in combined
            or "strategic" in combined,
            "rendering_rules must address strategic elevation of tactical questions"
        )
        # The rule must warn against stopping at Level 1
        self.assertIn("level 1", combined,
                      "rendering_rules must mention Level 1 as the failure mode to avoid")


if __name__ == "__main__":
    unittest.main(verbosity=2)
