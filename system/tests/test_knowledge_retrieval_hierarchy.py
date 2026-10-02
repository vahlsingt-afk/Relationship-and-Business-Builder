"""
test_knowledge_retrieval_hierarchy.py — RB 9.40 / DEFECT-025
5-Tier Knowledge Retrieval Hierarchy, Constitutional Failure Rule, Source Declaration.

DEFECT-025 addressed (Critical):
  Micro Graph access failure and knowledge-tier retrieval order violation.
  System bypasses Micro Graph for brand/entity queries and falls back to base model
  or external search — returning public estimates instead of proprietary intelligence.

Root cause: retrieval hierarchy inverted. Current behavior: Question → Generic knowledge →
  Search → Assistant response. Required: Tier 1 (Micro) → Tier 2 (Macro) → Tier 3 (Baseline)
  → Tier 4 (Artifacts) → Tier 5 (External, last resort).

Constitutional failure rule: getMicroGraphSummary unavailable ≠ license to use base model.
  It must be labeled [RETRIEVAL FAILURE] and escalated to the user.

Test groups:
  KR1 (6):  _knowledge_retrieval_hierarchy_spec — structure, tiers, sequence keys
  KR2 (6):  build_canonical_brief integration — top-level key, tier count, rendering_rules presence
  KR3 (6):  rendering_rules behavioral contracts — Tier 1 mandate, constitutional failure label,
            retrieval sequence, source declaration, external-last rule
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db

# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

_SYNTHETIC_REPORT: dict = {
    "today": "2026-05-30",
    "relationship_signals": {
        "signals": [
            {"name": "Alice Chen", "tier": "inner", "drr_score": 0.82},
        ],
        "stale_sources": [],
    },
    "loops": {},
    "active_threads": [],
    "market_signals": {"signals": [], "freshness_status": "ok"},
    "strategic_memory": {},
    "daily_prep_summary": {
        "totals": {"inner": 1, "broader": 0, "dormant_valuable": 0},
        "source_health": {"sources": {"email": {"status": "fresh"}}},
        "signals": [],
        "overdue_loops": [],
        "stale_sources": [],
        "meeting_prep": [],
    },
}


# ---------------------------------------------------------------------------
# KR1 — _knowledge_retrieval_hierarchy_spec structure
# ---------------------------------------------------------------------------

class KR1HierarchySpecStructureTests(unittest.TestCase):
    """Unit tests for _knowledge_retrieval_hierarchy_spec()."""

    def setUp(self):
        self.spec = db._knowledge_retrieval_hierarchy_spec()

    def test_KR1a_returns_dict(self):
        """_knowledge_retrieval_hierarchy_spec returns a dict."""
        self.assertIsInstance(self.spec, dict)

    def test_KR1b_has_five_tiers(self):
        """'tiers' key contains exactly 5 tier dicts numbered 1–5."""
        tiers = self.spec.get("tiers")
        self.assertIsInstance(tiers, list)
        self.assertEqual(len(tiers), 5,
                         f"Expected 5 tiers, got {len(tiers)}")
        tier_numbers = sorted(t["tier"] for t in tiers)
        self.assertEqual(tier_numbers, [1, 2, 3, 4, 5])

    def test_KR1c_required_tier_names_present(self):
        """All 5 required tier names are present."""
        required = {
            "micro_graph",
            "macro_graph",
            "baseline_knowledge",
            "artifact_repository",
            "external_sources",
        }
        actual = {t["name"] for t in self.spec.get("tiers") or []}
        missing = required - actual
        self.assertEqual(missing, set(),
                         f"Missing tier names: {missing}")

    def test_KR1d_has_retrieval_sequence_with_seven_steps(self):
        """'retrieval_sequence' key contains exactly 7 steps."""
        sequence = self.spec.get("retrieval_sequence")
        self.assertIsInstance(sequence, list)
        self.assertEqual(len(sequence), 7,
                         f"Expected 7 retrieval steps, got {len(sequence)}")

    def test_KR1e_has_constitutional_failure_rule(self):
        """'constitutional_failure_rule' key exists and has required sub-keys."""
        cfr = self.spec.get("constitutional_failure_rule")
        self.assertIsNotNone(cfr, "constitutional_failure_rule key must exist")
        self.assertIn("trigger", cfr)
        self.assertIn("required_response", cfr)
        self.assertIn("forbidden", cfr)
        # Must mention the failure label format
        required_resp = cfr["required_response"].lower()
        self.assertTrue(
            "retrieval failure" in required_resp,
            f"constitutional_failure_rule.required_response must mention RETRIEVAL FAILURE: {cfr['required_response']!r}"
        )

    def test_KR1f_tier1_micro_graph_has_constitutional_rule_and_failure_label(self):
        """Tier 1 (micro_graph) has a constitutional_rule and failure_label."""
        tiers = self.spec.get("tiers") or []
        tier1 = next((t for t in tiers if t["tier"] == 1), None)
        self.assertIsNotNone(tier1, "Tier 1 must exist")
        self.assertIn("constitutional_rule", tier1,
                      "Tier 1 must have a constitutional_rule")
        self.assertIn("failure_label", tier1,
                      "Tier 1 must have a failure_label for retrieval failures")
        # failure_label must mention RETRIEVAL FAILURE
        self.assertIn("RETRIEVAL FAILURE", tier1["failure_label"])


# ---------------------------------------------------------------------------
# KR2 — build_canonical_brief integration
# ---------------------------------------------------------------------------

class KR2BriefIntegrationTests(unittest.TestCase):
    """Integration tests for knowledge_retrieval_hierarchy in the canonical brief."""

    def setUp(self):
        self.brief = db.build_canonical_brief(_SYNTHETIC_REPORT)

    def test_KR2a_knowledge_retrieval_hierarchy_is_top_level_key(self):
        """knowledge_retrieval_hierarchy is a top-level key in the brief return dict."""
        self.assertIn("knowledge_retrieval_hierarchy", self.brief,
                      "knowledge_retrieval_hierarchy must be a top-level key in the brief")

    def test_KR2b_hierarchy_has_five_tiers(self):
        """Top-level knowledge_retrieval_hierarchy has 5 tiers."""
        hier = self.brief.get("knowledge_retrieval_hierarchy") or {}
        tiers = hier.get("tiers") or []
        self.assertEqual(len(tiers), 5,
                         f"Expected 5 tiers in brief.knowledge_retrieval_hierarchy, got {len(tiers)}")

    def test_KR2c_hierarchy_has_seven_step_sequence(self):
        """Top-level knowledge_retrieval_hierarchy has 7-step retrieval_sequence."""
        hier = self.brief.get("knowledge_retrieval_hierarchy") or {}
        sequence = hier.get("retrieval_sequence") or []
        self.assertEqual(len(sequence), 7,
                         f"Expected 7 steps in retrieval_sequence, got {len(sequence)}")

    def test_KR2d_rendering_rules_mention_tier_1_or_micro_graph(self):
        """rendering_rules reference Tier 1 or Micro Graph retrieval."""
        rules = " ".join(self.brief.get("rendering_rules") or []).lower()
        self.assertTrue(
            "tier 1" in rules or "micro graph" in rules or "micro_graph" in rules,
            "rendering_rules must reference Tier 1 / Micro Graph retrieval"
        )

    def test_KR2e_rendering_rules_mention_constitutional_failure(self):
        """rendering_rules mention 'constitutional failure' or 'retrieval failure'."""
        rules = " ".join(self.brief.get("rendering_rules") or []).lower()
        self.assertTrue(
            "constitutional failure" in rules or "retrieval failure" in rules,
            "rendering_rules must mention constitutional/retrieval failure"
        )

    def test_KR2f_hierarchy_spec_matches_standalone_function(self):
        """Brief's knowledge_retrieval_hierarchy matches _knowledge_retrieval_hierarchy_spec()."""
        standalone = db._knowledge_retrieval_hierarchy_spec()
        brief_hier = self.brief.get("knowledge_retrieval_hierarchy") or {}
        self.assertEqual(
            brief_hier.get("contract"),
            standalone.get("contract"),
        )
        self.assertEqual(
            len(brief_hier.get("tiers") or []),
            len(standalone.get("tiers") or []),
        )


# ---------------------------------------------------------------------------
# KR3 — rendering_rules behavioral contracts
# ---------------------------------------------------------------------------

class KR3RenderingRulesBehavioralTests(unittest.TestCase):
    """Verify rendering_rules encode the DEFECT-025 behavioral contracts."""

    def setUp(self):
        brief = db.build_canonical_brief(_SYNTHETIC_REPORT)
        self.rules = " ".join(brief.get("rendering_rules") or [])
        self.rules_lower = self.rules.lower()

    def test_KR3a_rules_mandate_tier1_check_before_base_model(self):
        """rendering_rules say to check Tier 1 (Micro Graph) before any other source."""
        combined = self.rules_lower
        self.assertTrue(
            ("tier 1" in combined and "micro" in combined)
            or "getmicrographsummary" in combined,
            "rendering_rules must mandate Tier 1 (Micro Graph) check before other sources"
        )

    def test_KR3b_rules_prohibit_base_model_substitution_for_known_entity(self):
        """rendering_rules prohibit substituting base model when Micro Graph exists."""
        combined = self.rules_lower
        self.assertTrue(
            "do not" in combined or "constitutional failure" in combined,
            "rendering_rules must prohibit base model substitution for known entity"
        )
        # Must say something about NOT using base model when graph exists
        self.assertTrue(
            "base model" in combined,
            "rendering_rules must explicitly mention base model constraint"
        )

    def test_KR3c_rules_specify_retrieval_failure_label(self):
        """rendering_rules specify the [RETRIEVAL FAILURE: ...] label format."""
        self.assertIn("retrieval failure", self.rules_lower,
                      "rendering_rules must specify the [RETRIEVAL FAILURE] label")

    def test_KR3d_rules_require_exhausting_internal_tiers_before_external(self):
        """rendering_rules require exhausting Tiers 1–4 before external/Tier 5."""
        combined = self.rules_lower
        self.assertTrue(
            "tier 5" in combined or "external" in combined or "last resort" in combined,
            "rendering_rules must specify that external sources are last resort"
        )

    def test_KR3e_rules_require_response_declaration(self):
        """rendering_rules require source declaration on every entity response."""
        combined = self.rules_lower
        self.assertTrue(
            "sources consulted" in combined or "coverage" in combined,
            "rendering_rules must require response declaration (sources, coverage, confidence, gaps)"
        )

    def test_KR3f_rules_name_micro_graph_as_primary_for_brand_queries(self):
        """rendering_rules identify micro_graph / getMicroGraphSummary as primary for brand queries."""
        combined = self.rules_lower
        self.assertTrue(
            "getmicrographsummary" in combined or "micro graph" in combined,
            "rendering_rules must name getMicroGraphSummary as primary for brand/operator queries"
        )
        # Also check that it applies to operator/brand queries specifically
        self.assertTrue(
            "brand" in combined or "operator" in combined or "entity" in combined,
            "rendering_rules must specify the trigger context (brand, operator, entity)"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
