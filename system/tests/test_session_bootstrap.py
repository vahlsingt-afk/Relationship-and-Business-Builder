"""
test_session_bootstrap.py — RB 9.41
Session Bootstrap Verification Protocol.

Motivation:
  DEFECT-019 (RB 9.36) added source priority rules.
  DEFECT-025 (RB 9.40) added a 5-tier retrieval hierarchy and constitutional failure rule.
  Both sprints fixed the same class of failure: Micro Graph bypassed, GPT defaults to
  base model or external search. The rules were added but silent compliance isn't verifiable.

  The session bootstrap forces active compliance: the GPT must read active_knowledge_assets,
  knowledge_retrieval_hierarchy, intelligence_pipeline_spec, and section_confidence to produce
  the session initialization confirmation. The confirmation is observable — the user can
  verify correct configuration before asking the first question.

Test groups:
  SB1 (6):  _session_bootstrap_spec — structure, checklist items, format keys
  SB2 (6):  build_canonical_brief integration — top-level key, checklist, rendering_rules
  SB3 (6):  rendering_rules behavioral contracts — bootstrap mandate, format, degraded state,
            stale sources, missing keys, observable verification purpose
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
# SB1 — _session_bootstrap_spec structure
# ---------------------------------------------------------------------------

_REQUIRED_CHECKLIST_ITEMS = {
    "micro_graphs_mounted",
    "retrieval_hierarchy_active",
    "intelligence_pipeline_active",
    "strategic_intent_active",
    "brief_confidence",
    "stale_sources",
}


class SB1BootstrapSpecStructureTests(unittest.TestCase):
    """Unit tests for _session_bootstrap_spec()."""

    def setUp(self):
        self.spec = db._session_bootstrap_spec()

    def test_SB1a_returns_dict(self):
        """_session_bootstrap_spec returns a dict."""
        self.assertIsInstance(self.spec, dict)

    def test_SB1b_has_verification_checklist_with_required_items(self):
        """'verification_checklist' contains all 6 required check items."""
        checklist = self.spec.get("verification_checklist")
        self.assertIsInstance(checklist, list)
        actual_items = {item["item"] for item in checklist}
        missing = _REQUIRED_CHECKLIST_ITEMS - actual_items
        self.assertEqual(missing, set(),
                         f"Missing checklist items: {missing}")

    def test_SB1c_has_required_output_format_with_template(self):
        """'required_output_format' key exists and template contains required display elements."""
        fmt = self.spec.get("required_output_format")
        self.assertIsNotNone(fmt, "required_output_format key must exist")
        self.assertIn("template", fmt,
                      "required_output_format must have a 'template' key")
        template = fmt["template"]
        # Template must contain the key visible elements a user would see
        self.assertIn("MOUNTED", template,
                      "template must include MOUNTED status label for micro graphs")
        self.assertIn("Ready", template,
                      "template must end with 'Ready' confirmation to the user")
        # Template must cover the main protocol checks
        template_lower = template.lower()
        self.assertTrue(
            "micro graph" in template_lower or "micro graphs" in template_lower,
            "template must include Micro Graphs line"
        )
        self.assertTrue(
            "retrieval hierarchy" in template_lower or "tier 1" in template_lower,
            "template must include retrieval hierarchy line"
        )

    def test_SB1d_has_timing_rule_specifying_before_first_response(self):
        """'timing_rule' key specifies emission before first user response."""
        timing = self.spec.get("timing_rule")
        self.assertIsNotNone(timing, "timing_rule key must exist")
        timing_lower = timing.lower()
        self.assertTrue(
            "before" in timing_lower and ("query" in timing_lower or "response" in timing_lower),
            f"timing_rule must say 'before first user response': {timing!r}"
        )

    def test_SB1e_has_degraded_state_rules(self):
        """'degraded_state_rules' key exists and covers no-graphs and missing-spec scenarios."""
        degraded = self.spec.get("degraded_state_rules")
        self.assertIsNotNone(degraded, "degraded_state_rules key must exist")
        # Must cover the two most important degraded cases
        self.assertIn("no_micro_graphs", degraded,
                      "degraded_state_rules must cover no_micro_graphs scenario")
        self.assertIn("missing_spec_key", degraded,
                      "degraded_state_rules must cover missing_spec_key scenario")

    def test_SB1f_checklist_items_have_source_references(self):
        """Each checklist item declares which brief key to read."""
        checklist = self.spec.get("verification_checklist") or []
        for item in checklist:
            self.assertIn("source", item,
                          f"Checklist item '{item.get('item')}' must have a 'source' key")
            self.assertTrue(len(item["source"]) > 0,
                            f"Checklist item '{item.get('item')}' source must be non-empty")


# ---------------------------------------------------------------------------
# SB2 — build_canonical_brief integration
# ---------------------------------------------------------------------------

class SB2BriefIntegrationTests(unittest.TestCase):
    """Integration tests for session_bootstrap_spec in the canonical brief."""

    def setUp(self):
        self.brief = db.build_canonical_brief(_SYNTHETIC_REPORT)

    def test_SB2a_session_bootstrap_spec_is_top_level_key(self):
        """session_bootstrap_spec is a top-level key in the brief return dict."""
        self.assertIn("session_bootstrap_spec", self.brief,
                      "session_bootstrap_spec must be a top-level key in the brief")

    def test_SB2b_spec_has_all_required_checklist_items(self):
        """Top-level session_bootstrap_spec has all 6 required checklist items."""
        spec = self.brief.get("session_bootstrap_spec") or {}
        checklist = spec.get("verification_checklist") or []
        actual_items = {item["item"] for item in checklist}
        missing = _REQUIRED_CHECKLIST_ITEMS - actual_items
        self.assertEqual(missing, set(),
                         f"Missing checklist items in brief spec: {missing}")

    def test_SB2c_spec_has_required_output_format(self):
        """Top-level session_bootstrap_spec has required_output_format."""
        spec = self.brief.get("session_bootstrap_spec") or {}
        self.assertIn("required_output_format", spec)
        self.assertIn("template", spec["required_output_format"])

    def test_SB2d_rendering_rules_mandate_session_bootstrap(self):
        """rendering_rules contain a session bootstrap mandate."""
        rules = " ".join(self.brief.get("rendering_rules") or []).lower()
        self.assertTrue(
            "session bootstrap" in rules or "session initialized" in rules
            or "initialization" in rules,
            "rendering_rules must contain a session bootstrap mandate"
        )

    def test_SB2e_rendering_rules_specify_before_first_user_query(self):
        """rendering_rules specify bootstrap must occur before first user query."""
        rules = " ".join(self.brief.get("rendering_rules") or []).lower()
        self.assertTrue(
            "before" in rules and ("query" in rules or "response" in rules),
            "rendering_rules must specify bootstrap before first user query"
        )

    def test_SB2f_spec_matches_standalone_function(self):
        """Brief's session_bootstrap_spec matches _session_bootstrap_spec() output."""
        standalone = db._session_bootstrap_spec()
        brief_spec = self.brief.get("session_bootstrap_spec") or {}
        self.assertEqual(
            brief_spec.get("contract"),
            standalone.get("contract"),
        )
        self.assertEqual(
            len(brief_spec.get("verification_checklist") or []),
            len(standalone.get("verification_checklist") or []),
        )


# ---------------------------------------------------------------------------
# SB3 — rendering_rules behavioral contracts
# ---------------------------------------------------------------------------

class SB3RenderingRulesBehavioralTests(unittest.TestCase):
    """Verify rendering_rules encode the session bootstrap behavioral contracts."""

    def setUp(self):
        brief = db.build_canonical_brief(_SYNTHETIC_REPORT)
        self.rules = " ".join(brief.get("rendering_rules") or [])
        self.rules_lower = self.rules.lower()

    def test_SB3a_rules_mandate_bootstrap_before_any_response(self):
        """rendering_rules say to emit bootstrap before any user query."""
        combined = self.rules_lower
        self.assertTrue(
            "before" in combined
            and ("any user query" in combined or "first output" in combined
                 or "before responding" in combined),
            "rendering_rules must mandate bootstrap before any user response"
        )

    def test_SB3b_rules_specify_micro_graph_listing_in_bootstrap(self):
        """rendering_rules specify listing mounted micro-graphs in bootstrap output."""
        combined = self.rules_lower
        self.assertTrue(
            "micro graph" in combined or "active_knowledge_assets" in combined,
            "rendering_rules must specify micro graph listing in bootstrap"
        )
        self.assertIn("mounted", combined,
                      "rendering_rules must reference MOUNTED status in bootstrap")

    def test_SB3c_rules_include_brief_confidence_in_bootstrap(self):
        """rendering_rules specify brief confidence in bootstrap output."""
        combined = self.rules_lower
        self.assertTrue(
            "brief confidence" in combined or "section_confidence" in combined,
            "rendering_rules must include brief confidence in bootstrap output"
        )

    def test_SB3d_rules_cover_degraded_state(self):
        """rendering_rules cover degraded session state (no graphs, missing spec keys)."""
        combined = self.rules_lower
        self.assertTrue(
            "degraded" in combined or "none mounted" in combined
            or "key missing" in combined,
            "rendering_rules must cover degraded session state scenarios"
        )

    def test_SB3e_rules_include_stale_sources_in_bootstrap(self):
        """rendering_rules specify stale sources disclosure in bootstrap output."""
        combined = self.rules_lower
        self.assertTrue(
            "stale sources" in combined or "stale source" in combined,
            "rendering_rules must specify stale sources disclosure in bootstrap"
        )

    def test_SB3f_rules_state_observable_verification_purpose(self):
        """rendering_rules explain that bootstrap enables observable verification."""
        combined = self.rules_lower
        self.assertTrue(
            "observable" in combined or "verifiable" in combined
            or "proof" in combined or "verify" in combined,
            "rendering_rules must state that bootstrap enables observable verification"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
