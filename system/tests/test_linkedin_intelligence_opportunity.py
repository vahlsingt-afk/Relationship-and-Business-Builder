"""
test_linkedin_intelligence_opportunity.py — RB 9.38 / DEFECT-021
LinkedIn Intelligence Acquisition Workflow.

DEFECT-021 addressed:
  Daily Brief does not identify LinkedIn as a missing intelligence source.
  CoS should proactively offer LinkedIn monitoring when contextual evidence
  suggests it would improve future briefings.

Gap triggers (both required):
  - LinkedIn NOT connected as an active source in source_health
  - AND: manual LinkedIn signals submitted today OR watchlist_no_change >= 3

Test groups:
  LI1 (6):  _compute_linkedin_gap — detection logic, triggers, scores
  LI2 (6):  _linkedin_intelligence_opportunity — CTA structure, contextual framing
  LI3 (6):  build_canonical_brief integration — section presence, display order, rendering rules
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SOURCE_HEALTH_WITH_LINKEDIN = {
    "sources": {
        "email": {"status": "fresh"},
        "linkedin": {"status": "fresh"},
        "calendar": {"status": "stale"},
    }
}

_SOURCE_HEALTH_WITHOUT_LINKEDIN = {
    "sources": {
        "email": {"status": "fresh"},
        "calendar": {"status": "stale"},
    }
}

_MANUAL_LINKEDIN_ITEM = {
    "title": "Noah Glass on AI in restaurants",
    "summary": "Noah Glass shared an interesting linkedin post about AI vendor claims.",
    "disposition": "monitor",
    "grounding": "manual_user_provided",
    "freshness": "fresh",
    "source_refs": ["linkedin:noah_glass_post_2026_05_30"],
    "confidence": "medium",
}

_NON_LINKEDIN_MANUAL_ITEM = {
    "title": "Conversation with Bob Gibson",
    "summary": "Had a call with Bob Gibson about strategy.",
    "disposition": "monitor",
    "grounding": "manual_user_provided",
    "freshness": "fresh",
    "source_refs": ["active_threads.yaml"],
    "confidence": "medium",
}


def _sections_with_manual_linkedin():
    return {
        "new_intelligence_today": [_MANUAL_LINKEDIN_ITEM],
        "overnight_delta_intelligence": [],
        "autonomous_discovery_evidence": [],
        "email_intelligence_harvest": [],
        "relationship_operational_signal_review": [],
        "watchlist_intelligence": [],
        "resource_verification_and_freshness_status": [],
        "linkedin_intelligence_opportunity": [],
        "decision_queue": [],
        "trust_metrics": [],
        "suppressed_today": [],
        "source_audit": [],
        "active_knowledge_assets": [],
    }


def _sections_with_watchlist_misses(miss_count: int):
    watchlist = [
        {
            "title": f"Entity {i} — No Change",
            "extras": {"watchlist_status": "No Change", "entity_name": f"Entity {i}"},
        }
        for i in range(miss_count)
    ]
    return {
        "new_intelligence_today": [],
        "overnight_delta_intelligence": [],
        "autonomous_discovery_evidence": [],
        "email_intelligence_harvest": [],
        "relationship_operational_signal_review": [],
        "watchlist_intelligence": watchlist,
        "resource_verification_and_freshness_status": [],
        "linkedin_intelligence_opportunity": [],
        "decision_queue": [],
        "trust_metrics": [],
        "suppressed_today": [],
        "source_audit": [],
        "active_knowledge_assets": [],
    }


# ---------------------------------------------------------------------------
# LI1 — _compute_linkedin_gap
# ---------------------------------------------------------------------------

class LI1ComputeLinkedInGapTests(unittest.TestCase):
    """Unit tests for _compute_linkedin_gap()."""

    def test_LI1a_no_gap_when_linkedin_connected(self):
        """gap_detected is False when LinkedIn is connected as a fresh source."""
        sections = _sections_with_manual_linkedin()
        result = db._compute_linkedin_gap(sections, _SOURCE_HEALTH_WITH_LINKEDIN)
        self.assertFalse(result["gap_detected"],
                         "No gap when LinkedIn is already connected")
        self.assertTrue(result["linkedin_connected"])

    def test_LI1b_no_gap_when_no_trigger_signals(self):
        """gap_detected is False when LinkedIn not connected but no evidence it would help."""
        sections = _sections_with_watchlist_misses(0)  # 0 misses, no manual signals
        result = db._compute_linkedin_gap(sections, _SOURCE_HEALTH_WITHOUT_LINKEDIN)
        self.assertFalse(result["gap_detected"],
                         "No gap when no manual signals and no watchlist misses")

    def test_LI1c_gap_detected_with_manual_linkedin_signals(self):
        """gap_detected is True when LinkedIn not connected AND manual LinkedIn signals present."""
        sections = _sections_with_manual_linkedin()
        result = db._compute_linkedin_gap(sections, _SOURCE_HEALTH_WITHOUT_LINKEDIN)
        self.assertTrue(result["gap_detected"])
        self.assertEqual(result["manual_linkedin_signal_count"], 1)

    def test_LI1d_gap_detected_with_watchlist_misses(self):
        """gap_detected is True when LinkedIn not connected AND watchlist_no_change >= 3."""
        sections = _sections_with_watchlist_misses(4)
        result = db._compute_linkedin_gap(sections, _SOURCE_HEALTH_WITHOUT_LINKEDIN)
        self.assertTrue(result["gap_detected"])
        self.assertEqual(result["watchlist_no_change_count"], 4)

    def test_LI1e_gap_reasons_are_informative(self):
        """gap_reasons include specific descriptions of what triggered the gap."""
        sections = _sections_with_manual_linkedin()
        result = db._compute_linkedin_gap(sections, _SOURCE_HEALTH_WITHOUT_LINKEDIN)
        combined = " ".join(result["gap_reasons"]).lower()
        self.assertIn("linkedin", combined)
        # Should mention manual signals
        self.assertTrue(
            "manually" in combined or "manual" in combined or "signal" in combined,
            f"gap_reasons should mention manual signals: {result['gap_reasons']}"
        )

    def test_LI1f_non_linkedin_manual_items_not_counted(self):
        """Manual items without linkedin in source_refs or summary are not counted."""
        sections = {
            "new_intelligence_today": [_NON_LINKEDIN_MANUAL_ITEM],
            "overnight_delta_intelligence": [],
            "autonomous_discovery_evidence": [],
            "email_intelligence_harvest": [],
            "relationship_operational_signal_review": [],
            "watchlist_intelligence": [],
            "resource_verification_and_freshness_status": [],
        }
        result = db._compute_linkedin_gap(sections, _SOURCE_HEALTH_WITHOUT_LINKEDIN)
        self.assertEqual(result["manual_linkedin_signal_count"], 0,
                         "Non-LinkedIn manual items must not inflate manual_linkedin_signal_count")
        self.assertFalse(result["gap_detected"],
                         "Non-LinkedIn manual item should not trigger gap")


# ---------------------------------------------------------------------------
# LI2 — _linkedin_intelligence_opportunity
# ---------------------------------------------------------------------------

class LI2LinkedInOpportunityTests(unittest.TestCase):
    """Unit tests for _linkedin_intelligence_opportunity()."""

    def test_LI2a_returns_empty_when_no_gap(self):
        """Returns empty list when LinkedIn is already connected."""
        sections = _sections_with_manual_linkedin()
        result = db._linkedin_intelligence_opportunity(sections, _SOURCE_HEALTH_WITH_LINKEDIN)
        self.assertEqual(result, [])

    def test_LI2b_returns_one_item_when_gap_detected(self):
        """Returns exactly one CTA item when gap is detected."""
        sections = _sections_with_manual_linkedin()
        result = db._linkedin_intelligence_opportunity(sections, _SOURCE_HEALTH_WITHOUT_LINKEDIN)
        self.assertEqual(len(result), 1)

    def test_LI2c_item_has_ask_todd_disposition(self):
        """The CTA item has disposition='ask_todd'."""
        sections = _sections_with_manual_linkedin()
        result = db._linkedin_intelligence_opportunity(sections, _SOURCE_HEALTH_WITHOUT_LINKEDIN)
        self.assertEqual(result[0].get("disposition"), "ask_todd")

    def test_LI2d_extras_has_what_will_be_scanned_and_produced(self):
        """extras has what_will_be_scanned and what_will_be_produced lists."""
        sections = _sections_with_manual_linkedin()
        result = db._linkedin_intelligence_opportunity(sections, _SOURCE_HEALTH_WITHOUT_LINKEDIN)
        extras = result[0].get("extras") or {}
        self.assertIn("what_will_be_scanned", extras)
        self.assertIn("what_will_be_produced", extras)
        self.assertIsInstance(extras["what_will_be_scanned"], list)
        self.assertIsInstance(extras["what_will_be_produced"], list)
        self.assertGreater(len(extras["what_will_be_scanned"]), 0)
        self.assertGreater(len(extras["what_will_be_produced"]), 0)

    def test_LI2e_summary_contextual_for_manual_trigger(self):
        """When manual LinkedIn signals triggered the gap, summary mentions manual signals."""
        sections = _sections_with_manual_linkedin()
        result = db._linkedin_intelligence_opportunity(sections, _SOURCE_HEALTH_WITHOUT_LINKEDIN)
        summary = (result[0].get("summary") or "").lower()
        self.assertTrue(
            "manually" in summary or "manual" in summary or "shared" in summary,
            f"Summary should mention manual signals when that's the trigger: {summary!r}"
        )

    def test_LI2f_extras_has_contextual_prompt_and_action_options(self):
        """extras has contextual_prompt and action_options for the [Enable]/[Not Now] CTA."""
        sections = _sections_with_manual_linkedin()
        result = db._linkedin_intelligence_opportunity(sections, _SOURCE_HEALTH_WITHOUT_LINKEDIN)
        extras = result[0].get("extras") or {}
        self.assertIn("contextual_prompt", extras)
        self.assertIn("action_options", extras)
        action_labels = [str(o).lower() for o in extras.get("action_options") or []]
        self.assertTrue(
            any("enable" in label for label in action_labels),
            "action_options must include an Enable option"
        )
        self.assertTrue(
            any("not" in label or "later" in label for label in action_labels),
            "action_options must include a Not Now option"
        )


# ---------------------------------------------------------------------------
# LI3 — build_canonical_brief integration
# ---------------------------------------------------------------------------

class LI3BriefIntegrationTests(unittest.TestCase):
    """Integration tests for linkedin_intelligence_opportunity in the canonical brief."""

    _SYNTHETIC_REPORT: dict = {
        "today": "2026-05-30",
        "relationship_signals": {
            "signals": [
                {"name": "Alice Chen", "tier": "inner", "drr_score": 0.82},
                {"name": "Bob Gibson", "tier": "inner", "drr_score": 0.75},
                {"name": "Carol Y", "tier": "inner", "drr_score": 0.71},
                {"name": "Dave M", "tier": "inner", "drr_score": 0.68},
            ],
            "stale_sources": [],
        },
        "loops": {},
        "active_threads": [],
        "market_signals": {"signals": [], "freshness_status": "ok"},
        "strategic_memory": {},
        "daily_prep_summary": {
            "totals": {"inner": 4, "broader": 0, "dormant_valuable": 0},
            "source_health": _SOURCE_HEALTH_WITHOUT_LINKEDIN,
            "signals": [],
            "overdue_loops": [],
            "stale_sources": [],
            "meeting_prep": [],
        },
    }

    def test_LI3a_section_exists_in_brief(self):
        """linkedin_intelligence_opportunity section key exists in every brief."""
        brief = db.build_canonical_brief(self._SYNTHETIC_REPORT)
        self.assertIn("linkedin_intelligence_opportunity", brief.get("sections") or {})

    def test_LI3b_section_in_brief_display_order(self):
        """linkedin_intelligence_opportunity is in brief_display_order."""
        brief = db.build_canonical_brief(self._SYNTHETIC_REPORT)
        order = brief.get("brief_display_order") or []
        self.assertIn("linkedin_intelligence_opportunity", order)

    def test_LI3c_appears_before_decision_queue_in_display_order(self):
        """linkedin_intelligence_opportunity is rendered before decision_queue."""
        brief = db.build_canonical_brief(self._SYNTHETIC_REPORT)
        order = brief.get("brief_display_order") or []
        li_pos = order.index("linkedin_intelligence_opportunity") if "linkedin_intelligence_opportunity" in order else -1
        dq_pos = order.index("decision_queue") if "decision_queue" in order else -1
        self.assertGreaterEqual(li_pos, 0)
        self.assertGreaterEqual(dq_pos, 0)
        self.assertLess(li_pos, dq_pos,
                        "linkedin_intelligence_opportunity must appear before decision_queue")

    def test_LI3d_rendering_rules_mention_linkedin_opportunity(self):
        """rendering_rules describe how to render linkedin_intelligence_opportunity."""
        brief = db.build_canonical_brief(self._SYNTHETIC_REPORT)
        rules = " ".join(brief.get("rendering_rules") or []).lower()
        self.assertIn("linkedin_intelligence_opportunity", rules)

    def test_LI3e_not_in_decision_queue_when_populated(self):
        """linkedin_intelligence_opportunity items do NOT appear in decision_queue."""
        # Build a report where the gap would be detected (4 inner contacts = 4 watchlist
        # misses once watchlist is populated with No Change status)
        brief = db.build_canonical_brief(self._SYNTHETIC_REPORT)
        li_section = (brief.get("sections") or {}).get("linkedin_intelligence_opportunity") or []
        dq_section = (brief.get("sections") or {}).get("decision_queue") or []
        # If the LI section has items, their titles must not duplicate in decision_queue
        li_titles = {item.get("title") for item in li_section}
        dq_titles = {item.get("title") for item in dq_section}
        overlap = li_titles & dq_titles
        self.assertEqual(overlap, set(),
                         f"LinkedIn opportunity titles must not duplicate in decision_queue: {overlap}")

    def test_LI3f_rendering_rules_say_skip_empty_sections(self):
        """rendering_rules say to skip empty brief_display_order sections."""
        brief = db.build_canonical_brief(self._SYNTHETIC_REPORT)
        rules = " ".join(brief.get("rendering_rules") or []).lower()
        self.assertIn("skip", rules,
                      "rendering_rules must instruct GPT to skip empty sections")


if __name__ == "__main__":
    unittest.main(verbosity=2)
