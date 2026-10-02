"""
test_entity_convergence_render.py — RB-2026-08-28.

Tests render_intelligence_brief.py's _render_entity_convergence() (Section
I+), which reads entity_convergence_scan.py's persisted result and renders
it -- rendering never re-runs the scan itself. See
test_entity_convergence_scan.py for the scan/state-tracking logic itself.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402


class TestEntityConvergenceRender(unittest.TestCase):
    def test_no_scan_result_yet(self):
        out = rib._render_entity_convergence(None)
        self.assertIn("## I+: Entity Signal Convergence", out)
        self.assertIn("not available", out)

    def test_clean_scan_no_findings(self):
        """RB-DEFECT-2026-09-18 (intelligence-cycle repair, reporting
        contract 'suppress normal zeros'): a clean scan with nothing to
        report renders nothing at all -- not a '17 tracked entities
        scanned, no convergent pattern' filler line. Repeated-zero health
        detection (a source that stops finding anything at all) belongs in
        the watchdog, not this section, per the function's own docstring."""
        out = rib._render_entity_convergence({
            "generated_at": "2026-08-29T02:00:00+00:00",
            "entities_scanned": 17,
            "new_findings": [],
            "persisting_findings": [],
        })
        self.assertEqual(out, "")

    def test_new_finding_shows_full_detail(self):
        out = rib._render_entity_convergence({
            "generated_at": "x", "entities_scanned": 17,
            "new_findings": [{
                "entity": "PAR Technology", "dominant_pattern": "exit_positioning",
                "pattern_confidence": "medium", "signal_count": 5,
                "synthesis_hypothesis": "Combined read: PAR Technology shows exit-positioning signals.",
                "opportunity_or_risk": "Watch for timeline compression.",
                "first_seen": "2026-08-28",
            }],
            "persisting_findings": [],
        })
        self.assertIn("PAR Technology", out)
        self.assertIn("Exit-positioning", out)
        self.assertIn("medium confidence", out)
        self.assertIn("5 signals", out)
        self.assertIn("Combined read: PAR Technology", out)
        self.assertIn("Watch for timeline compression.", out)

    def test_persisting_finding_shows_brief_mention_not_full_detail(self):
        """Same discipline already proven for watchlist escalation decay and
        loop-ledger repeat suppression -- a persisting classification must
        not re-render the full hypothesis/opportunity-risk paragraph every
        single day."""
        out = rib._render_entity_convergence({
            "generated_at": "x", "entities_scanned": 17,
            "new_findings": [],
            "persisting_findings": [{
                "entity": "Toast", "dominant_pattern": "growth_mode",
                "pattern_confidence": "high", "signal_count": 8,
                "synthesis_hypothesis": "This full paragraph must not repeat every day.",
                "opportunity_or_risk": "Neither must this.",
                "first_seen": "2026-08-20",
            }],
        })
        self.assertIn("Toast", out)
        self.assertIn("Growth mode", out)
        self.assertIn("since 2026-08-20", out)
        self.assertNotIn("This full paragraph must not repeat every day.", out)
        self.assertNotIn("Neither must this.", out)

    def test_multiple_new_findings_all_render(self):
        out = rib._render_entity_convergence({
            "generated_at": "x", "entities_scanned": 17,
            "new_findings": [
                {"entity": "PAR Technology", "dominant_pattern": "exit_positioning",
                 "pattern_confidence": "high", "signal_count": 6, "first_seen": "2026-08-28"},
                {"entity": "Chipotle Mexican Grill", "dominant_pattern": "distress",
                 "pattern_confidence": "medium", "signal_count": 4, "first_seen": "2026-08-28"},
            ],
            "persisting_findings": [],
        })
        self.assertIn("PAR Technology", out)
        self.assertIn("Chipotle Mexican Grill", out)

    def test_unknown_pattern_key_falls_back_to_raw_value(self):
        """A pattern not in the label map (shouldn't happen given the scan's
        own ACTIONABLE_PATTERNS filter, but the renderer must not crash on
        an unexpected value) renders the raw string rather than erroring."""
        out = rib._render_entity_convergence({
            "generated_at": "x", "entities_scanned": 1,
            "new_findings": [{
                "entity": "Test Co", "dominant_pattern": "some_new_pattern",
                "pattern_confidence": "high", "signal_count": 3, "first_seen": "2026-08-28",
            }],
            "persisting_findings": [],
        })
        self.assertIn("some_new_pattern", out)


if __name__ == "__main__":
    unittest.main()
