from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db  # noqa: E402
import render_daily_brief as rdb  # noqa: E402


JOSH_HISTORY = (
    "LinkedIn reply 2026-08-31: Josh asked for Traffic Manager and DMB material. "
    "Next: confirm email and send collateral. "
    "[Re-dated 2026-09-10 from 2026-09-10: Collateral was sent 2026-09-09 "
    "and Josh confirmed receipt. Follow up next week to schedule a late-September meeting.]"
)


class TestCurrentLoopState(unittest.TestCase):
    def test_latest_dated_note_replaces_stale_original_action(self):
        current = db._current_loop_description(JOSH_HISTORY)
        self.assertTrue(current.startswith("Collateral was sent 2026-09-09"))
        self.assertIn("Follow up next week", current)
        self.assertNotIn("confirm email and send collateral", current)

    def test_cos_action_uses_latest_loop_state(self):
        sections = {
            "decision_layer": [],
            "loops_and_obligations": [{
                "title": "Overdue: L-2026-08-31-001 — Josh Wesolowski / McDonald's",
                "summary": JOSH_HISTORY,
                "recommended_action": "Close or re-date L-2026-08-31-001.",
                "extras": {"party": "Josh Wesolowski", "loop_bucket": "overdue"},
            }],
            "w2_intelligence": [],
            "capacity_plan": [],
        }
        result = db._compute_cos_today(sections, {"source_health": {"sources": {}}})
        action = result[0]["extras"]["actions"][0]["action"]
        self.assertIn("Collateral was sent", action)
        self.assertIn("Follow up next week", action)
        self.assertNotIn("confirm email", action)

    def test_self_audit_loop_never_becomes_a_cos_action(self):
        """RB-2026-09-24: real incident -- self_audit_sweep.py's "RB
        self-audit" loops carry raw internal diagnostic text ("2 write
        op(s) receiving traffic with zero confirmed mutations: closeThread,
        refreshSources") straight into their description, which Priority 1
        took near-verbatim as CoS Action #1. A real relationship loop due
        the same day must win the slot instead."""
        self_audit_desc = (
            "Self-audit findings: 2 write op(s) receiving traffic with zero "
            "confirmed mutations: closeThread, refreshSources; 1 JPR recording(s) "
            "sitting unqueued or unprocessed"
        )
        sections = {
            "decision_layer": [],
            "loops_and_obligations": [
                {
                    "title": "Due today: L-2026-09-21-001 — RB self-audit",
                    "summary": self_audit_desc,
                    "recommended_action": "Review self-audit findings.",
                    "extras": {"party": "RB self-audit", "loop_bucket": "due_today"},
                },
                {
                    "title": "Overdue: L-2026-08-31-001 — Josh Wesolowski / McDonald's",
                    "summary": JOSH_HISTORY,
                    "recommended_action": "Close or re-date L-2026-08-31-001.",
                    "extras": {"party": "Josh Wesolowski", "loop_bucket": "overdue"},
                },
            ],
            "w2_intelligence": [],
            "capacity_plan": [],
        }
        result = db._compute_cos_today(sections, {"source_health": {"sources": {}}})
        action = result[0]["extras"]["actions"][0]["action"]
        self.assertNotIn("closeThread", action)
        self.assertNotIn("write op(s)", action)
        self.assertIn("Collateral was sent", action)

    def test_self_audit_only_yields_no_priority_1_action_rather_than_a_raw_dump(self):
        """When self-audit is the ONLY due/overdue loop, Priority 1 must be
        skipped entirely (consistent with this function's own documented
        no-padding philosophy) -- never fall back to showing the raw
        diagnostic text just to fill the slot."""
        sections = {
            "decision_layer": [],
            "loops_and_obligations": [{
                "title": "Due today: L-2026-09-21-001 — RB self-audit",
                "summary": "Self-audit findings: 2 write op(s) receiving traffic with zero confirmed mutations",
                "recommended_action": "Review self-audit findings.",
                "extras": {"party": "RB self-audit", "loop_bucket": "due_today"},
            }],
            "w2_intelligence": [],
            "capacity_plan": [],
        }
        result = db._compute_cos_today(sections, {"source_health": {"sources": {}}})
        actions = result[0]["extras"]["actions"] if result else []
        for action in actions:
            self.assertNotIn("write op(s)", action["action"])


class TestDayAheadAliasDedup(unittest.TestCase):
    def test_same_meeting_title_aliases_collapse_to_one_prep_item(self):
        report = {
            "today": "2026-09-20",
            "calendar": {
                "today": [],
                "tomorrow": [
                    {"title": "GP - Genius Restaurant Team - Bookings Review (Monday)",
                     "start": "2026-09-21T11:00:00", "description": ""},
                    {"title": "[GP] Genius Restaurant Team - Bookings Review",
                     "start": "2026-09-21T11:00:00", "description": ""},
                    {"title": "Genius Restaurant Team - Bookings Review",
                     "start": "2026-09-21T11:00:00", "description": ""},
                ],
                "this_week": [],
            },
            "baseline": [],
            "all_threads": [],
        }
        items = db._compute_day_ahead(report, {})
        self.assertEqual(1, len(items))
        self.assertIn("Bookings Review", items[0]["title"])


class TestDotConnectionEvidence(unittest.TestCase):
    def test_false_acquisition_tag_does_not_create_ma_claim(self):
        dot = rdb._infer_dot(
            "acquisition",
            "[🏢 ACQUISITION] Square Doubles Down on Its Renewed Interest in ISOs as a Sales Channel",
            {"watchlist": {"square": "Square"}},
        )
        self.assertNotIn("M&A activity", dot)
        self.assertIn("watchlist", dot)


if __name__ == "__main__":
    unittest.main()
