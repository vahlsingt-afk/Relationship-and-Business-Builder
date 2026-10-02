"""
test_learned_patterns.py — RB 9.84: RB-DEFECT-044 "Learned Patterns".

_record_prep_requirements_history() appends each day's
upcoming_preparation_requirements items to a rolling history log
(system/.cache/prep_requirements_history.json), grouped by recurring-meeting
category (mirroring the _PREP_TIME_RULES keyword groups). _compute_learned_patterns()
reads that history and surfaces a "Learned pattern: <category>" item once a
category has recurred on _LEARNED_PATTERN_MIN_OCCURRENCES distinct days, using
the mode prep-time estimate -- or a negative-confirmation fallback otherwise.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db  # noqa: E402
import rb_core as core  # noqa: E402


def _prep_item(title, prep_minutes):
    return db._canonical_item(
        title=title,
        summary="",
        recommended_action="",
        disposition="monitor",
        grounding="system_detected",
        freshness="fresh",
        source_refs=["calendar_overlay"],
        confidence="high",
        extras={"prep_minutes": prep_minutes},
    )


class TestLearnedPatternsCategory(unittest.TestCase):
    def test_interview_category(self):
        self.assertEqual(db._prep_item_category("Foods Connected Interview"), "Interview prep")

    def test_earnings_board_category(self):
        self.assertEqual(db._prep_item_category("Q2 Board Review"), "Earnings/board review")

    def test_sales_pipeline_category(self):
        self.assertEqual(db._prep_item_category("Weekly Sales Funnel Review"), "Sales/pipeline review")

    def test_unrecognized_title_is_other(self):
        self.assertEqual(db._prep_item_category("Team Standup"), "Other")


class TestLearnedPatternsHistoryAndCompute(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self._patcher = patch.object(core, "CACHE_DIR", Path(self.tmpdir.name))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self.tmpdir.cleanup()

    def _history_path(self):
        return Path(self.tmpdir.name) / "prep_requirements_history.json"

    def test_no_history_file_returns_insufficient_history(self):
        items = db._compute_learned_patterns({}, {})
        self.assertEqual(len(items), 1)
        self.assertIn("insufficient history", items[0]["title"])
        self.assertEqual(items[0]["extras"]["days_tracked"], 0)
        self.assertEqual(items[0]["disposition"], "ignore")

    def test_record_history_writes_file_and_excludes_other(self):
        prep_items = [
            _prep_item("Within 24 Hours — Foods Connected Interview", 30),
            _prep_item("Within 7 Days — Team Standup", 15),  # "Other" -- excluded
        ]
        db._record_prep_requirements_history({"today": "2026-06-14"}, prep_items)
        history = json.loads(self._history_path().read_text(encoding="utf-8"))
        day_entries = history["days"]["2026-06-14"]
        self.assertEqual(len(day_entries), 1)
        self.assertEqual(day_entries[0], {"category": "Interview prep", "prep_minutes": 30})

    def test_record_history_replaces_same_day_not_duplicates(self):
        prep_items = [_prep_item("Within 24 Hours — Acme Interview", 30)]
        db._record_prep_requirements_history({"today": "2026-06-14"}, prep_items)
        db._record_prep_requirements_history({"today": "2026-06-14"}, prep_items)
        history = json.loads(self._history_path().read_text(encoding="utf-8"))
        self.assertEqual(len(history["days"]), 1)
        self.assertEqual(len(history["days"]["2026-06-14"]), 1)

    def test_below_threshold_days_returns_insufficient_history(self):
        self._history_path().write_text(json.dumps({"days": {
            "2026-06-12": [{"category": "Interview prep", "prep_minutes": 30}],
            "2026-06-13": [{"category": "Interview prep", "prep_minutes": 30}],
        }}))
        items = db._compute_learned_patterns({}, {})
        self.assertEqual(len(items), 1)
        self.assertIn("insufficient history", items[0]["title"])
        self.assertEqual(items[0]["extras"]["days_tracked"], 2)

    def test_at_threshold_with_no_category_recurring_returns_no_patterns(self):
        self._history_path().write_text(json.dumps({"days": {
            "2026-06-10": [{"category": "Interview prep", "prep_minutes": 30}],
            "2026-06-11": [{"category": "Earnings/board review", "prep_minutes": 120}],
            "2026-06-12": [{"category": "Sales/pipeline review", "prep_minutes": 30}],
        }}))
        items = db._compute_learned_patterns({}, {})
        self.assertEqual(len(items), 1)
        self.assertIn("No recurring preparation patterns detected", items[0]["title"])
        self.assertEqual(items[0]["extras"]["days_tracked"], 3)

    def test_recurring_category_surfaces_learned_pattern(self):
        self._history_path().write_text(json.dumps({"days": {
            "2026-06-10": [{"category": "Sales/pipeline review", "prep_minutes": 30}],
            "2026-06-11": [{"category": "Sales/pipeline review", "prep_minutes": 30}],
            "2026-06-12": [{"category": "Sales/pipeline review", "prep_minutes": 30}],
            "2026-06-13": [{"category": "Interview prep", "prep_minutes": 30}],
        }}))
        items = db._compute_learned_patterns({}, {})
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertIn("Sales/pipeline review", item["title"])
        self.assertEqual(item["extras"]["category"], "Sales/pipeline review")
        self.assertEqual(item["extras"]["prep_minutes"], 30)
        self.assertEqual(item["extras"]["occurrences"], 3)
        self.assertEqual(item["extras"]["days_tracked"], 4)
        self.assertEqual(item["disposition"], "monitor")
        self.assertIn("30 min", item["recommended_action"])


if __name__ == "__main__":
    unittest.main()
