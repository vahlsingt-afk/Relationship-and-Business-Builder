"""
test_mutation_proposal_rendering_auto_apply.py

RB 2026-08-27: watchlist_add proposals can now auto-apply (see
intelligence_assessment.py), and a genuinely new (never-tracked) entity gets
its own new_competitor_entity proposal type (Todd's "Menu Tiger" request --
"it should generate a mutation and we should open a file on Tiger to begin
building an intelligence file"). Covers daily_brief.py's
_compute_intelligence_assessment_summary, the compute-layer function that
turns phase4 proposals into brief items -- the render-layer tests in
test_intelligence_assessment_summary_render.py construct already-built items
directly and never exercised this function's own title/why_it_matters logic.

The core thing under test: an auto_applied proposal must never claim it
still "requires confirmation" -- that would assert persistence didn't
happen, the exact class of bug this whole session was about avoiding.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db  # noqa: E402


def _report_with_proposals(proposals: list[dict]) -> dict:
    return {
        "intelligence_assessment": {
            "trust_stats": {
                "sources_accepted": 15, "sources_rejected": 0,
                "items_fetched": 230, "items_new": 230,
                "email_headlines_classified": 3,
                "convergences_detected": 20, "mutation_proposals": len(proposals),
                "confidence": "medium",
            },
            "phase_3_convergences": {"multi_source": []},
            "phase_4_proposals": {"proposals": proposals},
        },
    }


class TestAutoAppliedWatchlistAddRendering(unittest.TestCase):
    def test_auto_applied_proposal_does_not_claim_confirmation_needed(self):
        report = _report_with_proposals([{
            "type": "watchlist_add", "entity": "Burger King",
            "rationale": "Burger King has 363 items across 6 sources in 30 days but is not on your watchlist.",
            "signal_types": ["general"], "item_count": 363, "sources": ["RTN"],
            "confidence": "high", "is_recurring": False,
            "requires_confirmation": False, "auto_applied": True,
            "suggested_entity_id": None,
        }])
        items = db._compute_intelligence_assessment_summary(report)
        prop_item = next(i for i in items if "Burger King" in (i.get("title") or ""))
        self.assertIn("[ADDED]", prop_item["title"])
        self.assertNotIn("Requires your confirmation", prop_item["why_it_matters"])
        self.assertNotIn("[PROPOSED]", prop_item["title"])

    def test_non_applied_proposal_still_claims_confirmation_needed(self):
        report = _report_with_proposals([{
            "type": "watchlist_add", "entity": "Global Payments",
            "rationale": "Global Payments has 4 items across 2 sources in 30 days but is not on your watchlist.",
            "signal_types": ["general"], "item_count": 4, "sources": ["RTN", "PYMNTS"],
            "confidence": "medium", "is_recurring": False,
            "requires_confirmation": True, "auto_applied": False,
            "suggested_entity_id": None,
        }])
        items = db._compute_intelligence_assessment_summary(report)
        prop_item = next(i for i in items if "Global Payments" in (i.get("title") or ""))
        self.assertIn("[PROPOSED]", prop_item["title"])
        self.assertIn("Requires your confirmation", prop_item["why_it_matters"])

    def test_new_competitor_entity_proposal_renders_distinctly(self):
        report = _report_with_proposals([{
            "type": "new_competitor_entity", "entity": "Menu Tiger",
            "rationale": (
                "Menu Tiger has 6 items across 3 sources in 30 days and does not exist "
                "anywhere in the intelligence graph -- a sustained pattern around a "
                "genuinely untracked company, not just an unwatched known one."
            ),
            "signal_types": ["product_launch"], "item_count": 6,
            "sources": ["Restaurant Technology News", "QSR Magazine", "Fast Casual"],
            "confidence": "high", "is_recurring": False,
            "requires_confirmation": True, "auto_applied": False,
            "suggested_entity_id": "brand-menu-tiger",
        }])
        items = db._compute_intelligence_assessment_summary(report)
        prop_item = next(i for i in items if "Menu Tiger" in (i.get("title") or ""))
        self.assertIn("[NEW COMPETITOR DETECTED]", prop_item["title"])
        self.assertIn("brand-menu-tiger", prop_item["recommended_action"])
        self.assertIn("open an intelligence file", prop_item["recommended_action"])
        self.assertIn("Requires your confirmation", prop_item["why_it_matters"])


if __name__ == "__main__":
    unittest.main()
