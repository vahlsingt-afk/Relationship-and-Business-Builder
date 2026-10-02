"""
test_weekly_planning_save_snapshot.py

RB-2026-08-24: weekly_planning.save_plan() had no pre-write snapshot at all
-- confirmed live when a testing mistake promoted a real weekly-plan draft
~8 hours before its intended end-of-day-Monday timing, and there was no way
to recover the prior state of weekly_plan.json. Every other mutation path
in this codebase (mutations.py's loop/baseline/thread writers) snapshots
before writing; this covers the fix that brings weekly_plan.json in line.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import weekly_planning as wp  # noqa: E402


class TestSavePlanSnapshot(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        self.snapshots_dir = Path(self.tmpdir.name) / "_snapshots"
        self._orig_snapshots_dir = wp.SNAPSHOTS_DIR
        wp.SNAPSHOTS_DIR = self.snapshots_dir

    def tearDown(self):
        wp.SNAPSHOTS_DIR = self._orig_snapshots_dir
        self.tmpdir.cleanup()

    def test_existing_plan_is_snapshotted_before_overwrite(self):
        plan_path = Path(self.tmpdir.name) / "weekly_plan.json"
        plan_path.write_text(json.dumps({"week_of": "2026-08-17", "status": "active"}))

        wp.save_plan({"week_of": "2026-08-24", "status": "active"}, path=plan_path)

        snapshots = list(self.snapshots_dir.glob("weekly_plan.*.json"))
        self.assertEqual(len(snapshots), 1)
        snapshot_content = json.loads(snapshots[0].read_text())
        self.assertEqual(snapshot_content["week_of"], "2026-08-17")  # the OLD content, preserved

        # And the real write still happened.
        self.assertEqual(json.loads(plan_path.read_text())["week_of"], "2026-08-24")

    def test_no_prior_file_means_no_snapshot_attempted(self):
        plan_path = Path(self.tmpdir.name) / "weekly_plan.json"
        self.assertFalse(plan_path.exists())

        wp.save_plan({"week_of": "2026-08-24", "status": "active"}, path=plan_path)

        self.assertFalse(self.snapshots_dir.exists())
        self.assertTrue(plan_path.exists())

    def test_snapshot_failure_never_blocks_the_save(self):
        plan_path = Path(self.tmpdir.name) / "weekly_plan.json"
        plan_path.write_text(json.dumps({"week_of": "2026-08-17"}))

        with patch("shutil.copy2", side_effect=OSError("disk full")):
            wp.save_plan({"week_of": "2026-08-24"}, path=plan_path)

        self.assertEqual(json.loads(plan_path.read_text())["week_of"], "2026-08-24")


if __name__ == "__main__":
    unittest.main()
