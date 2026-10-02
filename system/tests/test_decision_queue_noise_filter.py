"""
test_decision_queue_noise_filter.py

RB-2026-07-20: internal RB data-hygiene/config-reconciliation tasks were
reaching the CEO-facing Decision Queue and CoS Bottom Line under a generic
title ("One decision needed") -- the actual identifying content ("Confirm
or correct the operator relationship proximity in strategic_operators.yaml.")
only ever appeared in the detail text (why_it_matters/summary/
recommended_action), which the existing noise filter never checked (title
only). Confirmed live in the 2026-07-20 brief. Fixed by checking title +
detail text together, and adding the specific phrases that slipped through.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_daily_brief as rdb  # noqa: E402


def _housekeeping_item():
    """The exact shape of the item confirmed live in the 2026-07-20 brief."""
    return {
        "title": "One decision needed",
        "why_it_matters": "RB should ask for the smallest clarification that improves future automation.",
        "recommended_action": "Confirm or correct the operator relationship proximity in strategic_operators.yaml.",
        "extras": {},
    }


def _real_decision_item():
    return {
        "title": "Open waiting loop — Knobloch Danielle",
        "why_it_matters": "",
        "recommended_action": "Open loop for Knobloch Danielle (waiting) due 2026-08-04.",
        "extras": {},
    }


class TestDecisionQueueNoiseFilter(unittest.TestCase):
    def test_generic_title_with_housekeeping_detail_is_suppressed(self):
        sections = {"decision_queue": [_housekeeping_item(), _real_decision_item()]}
        out = rdb._render_decision_queue(sections)
        self.assertNotIn("One decision needed", out)
        self.assertNotIn("strategic_operators.yaml", out)
        self.assertIn("Knobloch Danielle", out)

    def test_only_housekeeping_items_renders_no_open_decisions_note(self):
        sections = {"decision_queue": [_housekeeping_item()]}
        out = rdb._render_decision_queue(sections)
        self.assertNotIn("One decision needed", out)


class TestCosBottomLineNoiseFilter(unittest.TestCase):
    def test_housekeeping_item_never_becomes_top_action(self):
        sections = {"decision_queue": [_housekeeping_item(), _real_decision_item()]}
        out = rdb._render_cos_bottom_line(sections, date(2026, 7, 20))
        self.assertNotIn("strategic_operators.yaml", out)
        self.assertNotIn("One decision needed", out)
        self.assertIn("Knobloch Danielle", out)


if __name__ == "__main__":
    unittest.main()
