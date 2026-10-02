"""
test_lifecycle_dedup.py

Regression coverage: lifecycle_transition entries in "What Changed Today"
(career phase, opportunity mutations) are meant to be one-shot forever, but
_load_prior_brief_state only scans the last 7 days of rendered intelligence
briefs. A fact shown once (e.g. offer-accepted on 2026-06-21) and never
repeated within that window ages out of the 7-day lookback and gets
re-rendered as if it were new, once more than 7 days have passed since it
last appeared. Observed live: "Career phase: Onboarding — Global Payments"
resurfaced on 2026-07-05 having last appeared well over a week earlier.

_render_what_changed now also checks a permanent store
(RENDERED_LIFECYCLE_PATH / persistent_lifecycle dict) with no expiry.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402


def _lifecycle_sections(text: str = "Career phase: Onboarding — Global Payments") -> dict:
    return {
        "personal_intelligence_delta": [
            {
                "title": text,
                "summary": "Job search closed. Active candidate pipeline suppressed.",
                "extras": {"delta_source": "lifecycle_transition"},
            }
        ],
    }


class TestLifecycleDedup(unittest.TestCase):
    def test_first_render_shows_entry_and_records_it(self):
        persistent: dict = {}
        out = rib._render_what_changed(_lifecycle_sections(), date(2026, 7, 5), persistent_lifecycle=persistent)
        self.assertIn("Career phase", out)
        self.assertEqual(len(persistent), 1)

    def test_persistent_store_suppresses_beyond_7_day_window(self):
        """The core regression: no prior_state (as if >7 days have passed since
        the entry was last seen in a rendered brief) — only the permanent
        store should be able to suppress it now."""
        persistent: dict = {}
        # Day 1: renders and gets recorded.
        rib._render_what_changed(_lifecycle_sections(), date(2026, 6, 21), persistent_lifecycle=persistent)
        self.assertEqual(len(persistent), 1)

        # Day 15 (well past the old 7-day window), with empty prior_state
        # (simulating that the 7-day rolling scan no longer covers day 1) —
        # the persistent store alone must still suppress it.
        out = rib._render_what_changed(
            _lifecycle_sections(), date(2026, 7, 5), prior_state=None, persistent_lifecycle=persistent
        )
        self.assertNotIn("Career phase", out)

    def test_changed_lifecycle_text_is_not_suppressed(self):
        """A genuinely different lifecycle fact (different text) must still render."""
        persistent: dict = {}
        rib._render_what_changed(_lifecycle_sections("Career phase: Onboarding — Global Payments"),
                                  date(2026, 6, 21), persistent_lifecycle=persistent)
        out = rib._render_what_changed(
            _lifecycle_sections("Career phase: Ramped — Global Payments"),
            date(2026, 8, 1), persistent_lifecycle=persistent,
        )
        self.assertIn("Ramped", out)


if __name__ == "__main__":
    unittest.main()
