"""
test_intelligence_assessment_summary_render.py

RB-2026-08-24: daily_brief.py has computed sections["intelligence_assessment_
summary"] (trust stats, sustained multi-source patterns, and review-first
watchlist_add/thread_intelligence mutation proposals from intelligence_
assessment.py's Phase 3/4) since Sprint F, but no renderer ever read that
section -- real, dated findings ("Burger King has 307 items across 6 sources
but isn't on your watchlist") were generated every day and never once
reached the actual Intelligence Brief. This covers the new renderer that
closes that gap.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402


def _header_item(summary: str) -> dict:
    return {
        "title": "Intelligence Assessment — 15/15 sources · 230 items · 20 patterns · 10 proposals",
        "summary": summary,
        "extras": {"trust_stats": {}, "convergences_detected": 20, "mutation_proposals": 10},
    }


def _pattern_item(entity: str, count: int) -> dict:
    return {
        "title": f"[SUSTAINED] {entity} — {count} signals across 6 sources",
        "extras": {"convergence_type": "sustained_multi_source", "entity": entity, "item_count": count},
    }


def _proposal_item(ptype: str, entity: str, action: str) -> dict:
    return {
        "title": f"[PROPOSED] Add {entity} to watchlist" if ptype == "watchlist_add"
                 else f"[PROPOSED] Update thread — {entity} intelligence",
        "recommended_action": action,
        "extras": {"proposal_type": ptype, "entity": entity, "requires_confirmation": True},
    }


class TestIntelligenceAssessmentSummaryRender(unittest.TestCase):
    def test_empty_sections_renders_nothing(self):
        self.assertEqual(rib._render_intelligence_assessment_summary({}), "")
        self.assertEqual(
            rib._render_intelligence_assessment_summary({"intelligence_assessment_summary": []}), "")

    def test_not_yet_run_fallback_item_renders_nothing(self):
        """The 'has not run today' placeholder item carries no trust_stats
        extras -- it's an ops message, not reader-facing intelligence, and
        must stay silent rather than clutter the brief."""
        sections = {"intelligence_assessment_summary": [{
            "title": "Daily Intelligence Assessment — not yet run",
            "summary": "intelligence_assessment.py has not run today.",
            "extras": {},
        }]}
        self.assertEqual(rib._render_intelligence_assessment_summary(sections), "")

    def test_header_only_renders_header_line(self):
        sections = {"intelligence_assessment_summary": [_header_item("15 accepted, 230 items fetched.")]}
        out = rib._render_intelligence_assessment_summary(sections)
        self.assertEqual(out, "")

    def test_watchlist_add_proposal_renders_with_action(self):
        sections = {"intelligence_assessment_summary": [
            _header_item("summary"),
            _proposal_item("watchlist_add", "Burger King",
                            "Call getWatchList to review current watchlist, then confirm adding Burger King."),
        ]}
        out = rib._render_intelligence_assessment_summary(sections)
        self.assertIn("Burger King", out)
        self.assertIn("getWatchList", out)

    def test_sustained_pattern_renders(self):
        sections = {"intelligence_assessment_summary": [
            _header_item("summary"),
            _pattern_item("Firehouse Subs", 300),
        ]}
        out = rib._render_intelligence_assessment_summary(sections)
        self.assertIn("Firehouse Subs", out)
        self.assertIn("Companies RB is now monitoring more closely", out)

    def test_thread_intelligence_proposal_renders(self):
        sections = {"intelligence_assessment_summary": [
            _header_item("summary"),
            _proposal_item("thread_intelligence", "PAR Technology",
                            "Review PAR Technology signals against thread T-2026-001."),
        ]}
        out = rib._render_intelligence_assessment_summary(sections)
        self.assertIn("PAR Technology", out)
        self.assertIn("T-2026-001", out)


if __name__ == "__main__":
    unittest.main()
