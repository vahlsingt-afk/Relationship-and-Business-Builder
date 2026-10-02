"""
test_post_capture_cascade.py — coverage for system/scripts/post_capture_cascade.py,
built for the 2026-09-18 intelligence-cycle repair. Maps to handoff acceptance
test #7 ("A manual GP capture triggers all required downstream stages") and
#12 ("A post-brief material capture causes a refreshed brief or explicit
amendment").

Root cause this closes: outlook_gui_capture.py (GP Outlook manual capture)
wrote raw files and returned -- no downstream stage ever ran automatically
for an ad hoc/late capture. This tests the cascade orchestrator in isolation
(no real subprocess calls -- every step's command is captured and stubbed).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import post_capture_cascade as pcc  # noqa: E402
import refresh_sources as rs  # noqa: E402


REQUIRED_STAGES = {
    "source_health_recompute",
    "cross_source_last_touch",
    "interaction_event_ledger",
    "meeting_prep",
    "loop_autopilot_morning",
    "daily_brief_cache_rebuild",
}


class TestCascadeStages(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="post_capture_cascade_test_"))
        self._orig_receipts = pcc.CASCADE_RECEIPTS_PATH
        self._orig_latest = pcc.CASCADE_LATEST_PATH
        self._orig_published = pcc.PUBLISHED_DIR
        pcc.CASCADE_RECEIPTS_PATH = self.tmp / "post_capture_cascade_receipts.jsonl"
        pcc.CASCADE_LATEST_PATH = self.tmp / "post_capture_cascade_latest.json"
        pcc.PUBLISHED_DIR = self.tmp / "published" / "daily"

    def tearDown(self):
        pcc.CASCADE_RECEIPTS_PATH = self._orig_receipts
        pcc.CASCADE_LATEST_PATH = self._orig_latest
        pcc.PUBLISHED_DIR = self._orig_published

    def test_handoff7_all_required_downstream_stages_run(self):
        """Acceptance test 7: a manual capture triggers every required
        downstream stage -- source-health, interaction ledger, last-touch
        projection, meeting-prep, loops, and a brief-cache refresh."""
        with patch.object(pcc, "_run", return_value={"returncode": 0, "stdout_tail": "", "stderr_tail": ""}), \
             patch.object(rs, "apply_cross_source_last_touch", return_value={"status": rs.STATUS_REFRESHED, "applied": 2}):
            receipt = pcc.run_cascade(today=date(2026, 9, 18), trigger="gp_outlook_manual_capture")

        self.assertTrue(receipt["ok"], receipt)
        ran_stages = {s["name"] for s in receipt["steps"]}
        self.assertEqual(ran_stages, REQUIRED_STAGES, f"missing: {REQUIRED_STAGES - ran_stages}")
        self.assertEqual(receipt["steps_passed"], len(REQUIRED_STAGES))
        self.assertEqual(receipt["steps_failed"], 0)

    def test_stage_order_ledger_before_meeting_prep_and_loops(self):
        """The exact ordering bug identified in morning_pipeline.py (meeting_
        prep/loops run before that day's own capture ingestion even in the
        scheduled path) must not repeat here: interaction_event_ledger must
        run before meeting_prep and loop_autopilot."""
        with patch.object(pcc, "_run", return_value={"returncode": 0, "stdout_tail": "", "stderr_tail": ""}), \
             patch.object(rs, "apply_cross_source_last_touch", return_value={"status": rs.STATUS_REFRESHED, "applied": 0}):
            receipt = pcc.run_cascade(today=date(2026, 9, 18), trigger="test")

        order = [s["name"] for s in receipt["steps"]]
        self.assertLess(order.index("interaction_event_ledger"), order.index("meeting_prep"))
        self.assertLess(order.index("interaction_event_ledger"), order.index("loop_autopilot_morning"))
        self.assertLess(order.index("meeting_prep"), order.index("daily_brief_cache_rebuild"))

    def test_a_failed_step_marks_cascade_not_ok(self):
        def fake_run(cmd):
            if "meeting_prep.py" in cmd[1]:
                return {"returncode": 1, "stdout_tail": "", "stderr_tail": "boom"}
            return {"returncode": 0, "stdout_tail": "", "stderr_tail": ""}

        with patch.object(pcc, "_run", side_effect=fake_run), \
             patch.object(rs, "apply_cross_source_last_touch", return_value={"status": rs.STATUS_REFRESHED}):
            receipt = pcc.run_cascade(today=date(2026, 9, 18), trigger="test")

        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["steps_failed"], 1)
        failed = [s for s in receipt["steps"] if s["status"] == "fail"]
        self.assertEqual(failed[0]["name"], "meeting_prep")

    def test_handoff12_post_brief_capture_flags_amendment_needed(self):
        """Acceptance test 12: a capture arriving after today's brief was
        already published must be surfaced as needing a refreshed brief or
        an explicit amendment -- not silently absorbed."""
        today = date(2026, 9, 18)
        day_dir = pcc.PUBLISHED_DIR / today.isoformat()
        day_dir.mkdir(parents=True)
        (day_dir / "brief.json").write_text(json.dumps({"date": today.isoformat()}), encoding="utf-8")

        with patch.object(pcc, "_run", return_value={"returncode": 0, "stdout_tail": "", "stderr_tail": ""}), \
             patch.object(rs, "apply_cross_source_last_touch", return_value={"status": rs.STATUS_REFRESHED}):
            receipt = pcc.run_cascade(today=today, trigger="gp_outlook_manual_capture")

        self.assertTrue(receipt["ok"])
        self.assertTrue(receipt["post_brief_amendment_needed"])
        self.assertTrue(receipt["recommended_actions"])

    def test_no_amendment_flag_when_brief_not_yet_published(self):
        with patch.object(pcc, "_run", return_value={"returncode": 0, "stdout_tail": "", "stderr_tail": ""}), \
             patch.object(rs, "apply_cross_source_last_touch", return_value={"status": rs.STATUS_REFRESHED}):
            receipt = pcc.run_cascade(today=date(2026, 9, 18), trigger="test")

        self.assertFalse(receipt["post_brief_amendment_needed"])
        self.assertEqual(receipt["recommended_actions"], [])

    def test_receipt_is_durable_and_loadable(self):
        with patch.object(pcc, "_run", return_value={"returncode": 0, "stdout_tail": "", "stderr_tail": ""}), \
             patch.object(rs, "apply_cross_source_last_touch", return_value={"status": rs.STATUS_REFRESHED}):
            pcc.run_cascade(today=date(2026, 9, 18), trigger="gp_outlook_manual_capture")
            pcc.run_cascade(today=date(2026, 9, 18), trigger="linkedin_manual_capture")

        self.assertTrue(pcc.CASCADE_RECEIPTS_PATH.exists())
        receipts = pcc.load_receipts()
        self.assertEqual(len(receipts), 2)
        self.assertEqual(receipts[0]["trigger"], "gp_outlook_manual_capture")
        self.assertEqual(receipts[1]["trigger"], "linkedin_manual_capture")
        self.assertTrue(pcc.CASCADE_LATEST_PATH.exists())
        latest = json.loads(pcc.CASCADE_LATEST_PATH.read_text())
        self.assertEqual(latest["trigger"], "linkedin_manual_capture")


if __name__ == "__main__":
    unittest.main()
