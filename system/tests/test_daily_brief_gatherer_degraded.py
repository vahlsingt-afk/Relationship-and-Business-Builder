"""
test_daily_brief_gatherer_degraded.py

Covers daily_brief.py's _compute_intelligence_assessment_summary() handling
of phase_2_gatherer's degraded-state contract (Gatherer gap-closure build,
2026-10): an empty or thin `changes` list must never be rendered as a quiet
24 hours when Gatherer's own source_health says otherwise.

Per the explicit user constraint for this build ("gatherer does not create
any gaps in intelligence needed for RBB... world and national news,
industry articles and press releases are still needed"), these tests also
confirm the degraded-state handling is scoped to the Gatherer section alone
and does not touch phase_1_web-derived sections.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db  # noqa: E402


def _base_assessment(**overrides) -> dict:
    assessment = {
        "trust_stats": {
            "sources_accepted": 15, "sources_rejected": 0,
            "items_fetched": 230, "items_new": 230,
            "email_headlines_classified": 3,
            "convergences_detected": 0, "mutation_proposals": 0,
            "confidence": "medium",
        },
        "phase_2_gatherer": {},
        "phase_3_convergences": {"multi_source": []},
        "phase_4_proposals": {"proposals": []},
    }
    assessment.update(overrides)
    return assessment


class TestGathererDegradedStateHandling(unittest.TestCase):
    def test_degraded_with_no_changes_emits_explicit_notice(self):
        report = {"intelligence_assessment": _base_assessment(phase_2_gatherer={
            "status": "degraded",
            "changes": [],
            "hunter_escalations": [],
            "source_health": {"status": "degraded", "expected_sources": 5, "succeeded_sources": 3,
                               "failed_sources": 2, "failed_source_names": ["QSR Magazine", "Nation's Restaurant News"]},
        })}
        items = db._compute_intelligence_assessment_summary(report)
        notice = next((i for i in items if "GATHERER DEGRADED" in (i.get("title") or "")), None)
        self.assertIsNotNone(notice, "expected an explicit degraded-state notice item")
        self.assertIn("QSR Magazine", notice["summary"])
        self.assertIn("does not mean a quiet 24 hours", notice["summary"])
        self.assertEqual(notice["confidence"], "low")

    def test_degraded_note_appears_on_header_item_even_with_changes(self):
        report = {"intelligence_assessment": _base_assessment(phase_2_gatherer={
            "status": "degraded",
            "changes": [{"change_id": "gchg-1", "title": "Brand A deploys", "entities": ["Brand A"],
                         "signal_type": "deployment", "materiality": 90, "source": "Example",
                         "observed_at": "2026-10-02T12:00:00Z", "confidence": "medium"}],
            "hunter_escalations": [],
            "source_health": {"status": "degraded", "expected_sources": 5, "succeeded_sources": 4,
                               "failed_sources": 1, "failed_source_names": ["QSR Magazine"]},
        })}
        items = db._compute_intelligence_assessment_summary(report)
        header = items[0]
        self.assertIn("degraded", header["summary"])
        self.assertIn("gatherer_status", header["extras"])
        self.assertEqual(header["extras"]["gatherer_status"], "degraded")
        # Not emptied -- a real change still renders its own item.
        self.assertFalse(any("GATHERER DEGRADED" in (i.get("title") or "") for i in items))

    def test_ok_status_with_no_changes_emits_no_degraded_notice(self):
        report = {"intelligence_assessment": _base_assessment(phase_2_gatherer={
            "status": "ok",
            "changes": [],
            "hunter_escalations": [],
            "source_health": {"status": "ok", "expected_sources": 5, "succeeded_sources": 5,
                               "failed_sources": 0, "failed_source_names": []},
        })}
        items = db._compute_intelligence_assessment_summary(report)
        self.assertFalse(any("GATHERER DEGRADED" in (i.get("title") or "") for i in items))
        self.assertNotIn("degraded", items[0]["summary"])

    def test_missing_gatherer_block_defaults_to_unknown_without_crashing(self):
        report = {"intelligence_assessment": _base_assessment(phase_2_gatherer={})}
        items = db._compute_intelligence_assessment_summary(report)
        # "unknown" is not "degraded" -- no false-alarm notice without real signal.
        self.assertFalse(any("GATHERER DEGRADED" in (i.get("title") or "") for i in items))


if __name__ == "__main__":
    unittest.main()
