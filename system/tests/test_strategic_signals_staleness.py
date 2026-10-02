"""
test_strategic_signals_staleness.py

Regression coverage: Strategic Signals rendered a convergence event as a
fresh, act-today signal — "Global Payments / Genius AI integration for
restaurant operators" — whose evidence hadn't changed since 2026-06-12
(last real evidence published 2026-05-13), on a brief dated 2026-07-05 (23
days later). Root cause: the render-time dedup only suppresses a signal if
its exact (title, evidence_count) fingerprint was rendered in the last 7
days — that tells you "did we already report this," not "is this actually
new." An event that simply missed the top-5 cut on recent days had no
fingerprint on file and rendered as if brand new, months-old evidence and
all. daily_brief.py now computes real staleness from the event's own
last_seen_at vs. today (extras.is_actually_stale / days_since_evidence);
_render_strategic_signals checks that independent of fingerprint history.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402


def _signal_item(title: str, evidence_count: int, is_stale: bool, days_since: int | None) -> dict:
    return {
        "title": f"Multiple-source convergence: {title}",
        "summary": f"validated_signal across {evidence_count} evidence item(s) and 3 source channel(s): company press.",
        "why_it_matters": "Reinforces the thesis.",
        "extras": {
            "is_actually_stale": is_stale,
            "days_since_evidence": days_since,
        },
        "source_refs": [],
    }


class TestStrategicSignalStaleness(unittest.TestCase):
    def test_stale_signal_with_no_fingerprint_history_does_not_render_as_new(self):
        """The exact bug: no prior_fingerprints at all (first time this
        title+count combo is seen by the renderer), but the event itself is
        23 days stale — must NOT render in the prominent position."""
        sections = {
            "strategic_industry_signals": [
                _signal_item("Global Payments / Genius AI integration for restaurant operators",
                             7, is_stale=True, days_since=23),
            ]
        }
        out = rib._render_strategic_signals(sections, prior_state=None, today=date(2026, 7, 5))
        # Must not render as a prominent, bolded "new" signal...
        self.assertNotIn("**Multiple-source convergence: Global Payments / Genius AI integration", out)
        # ...23 days stale is well past the retirement threshold, so it's named
        # only in the "Retired" footnote for transparency, not "Ongoing".
        self.assertIn("Retired", out)
        self.assertIn("Global Payments / Genius AI integration", out)

    def test_mildly_stale_signal_footnotes_as_ongoing_not_retired(self):
        sections = {
            "strategic_industry_signals": [
                _signal_item("Some other convergence", 5, is_stale=True, days_since=8),
            ]
        }
        out = rib._render_strategic_signals(sections, prior_state=None, today=date(2026, 7, 5))
        self.assertIn("Ongoing", out)
        self.assertIn("Some other convergence", out)

    def test_fresh_signal_still_renders_fully(self):
        """RB-DEFECT-2026-09-18 (intelligence-cycle repair, reporting
        contract 'suppress internal scoring jargon'): the internal pipeline
        label "Multiple-source convergence:" is stripped from the rendered
        title -- Todd reads the actual signal name, not the mechanism that
        produced it."""
        sections = {
            "strategic_industry_signals": [
                _signal_item("A brand new convergence", 3, is_stale=False, days_since=1),
            ]
        }
        out = rib._render_strategic_signals(sections, prior_state=None, today=date(2026, 7, 5))
        self.assertIn("**A brand new convergence**", out)
        self.assertNotIn("Multiple-source convergence:", out)


if __name__ == "__main__":
    unittest.main()
