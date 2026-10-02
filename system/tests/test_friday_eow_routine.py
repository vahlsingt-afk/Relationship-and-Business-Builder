"""
test_friday_eow_routine.py

RB-2026-08-24, gap #9b: no general file-freshness checker existed anywhere
in this codebase -- confirmed live when network_map.md/intro_brokers.md sat
untouched for ~3 months with nothing flagging it. This covers the new
Friday close-out routine: weekly plan review, loop review (reusing existing
rb_core/eolms primitives, never reinventing due-date math), the freshness
check itself, and the backup step. Every test is fully tmp_path-isolated --
none of these touch real production files.
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import rb_core as core  # noqa: E402
import friday_eow_routine as fer  # noqa: E402


SAMPLE_LEDGER = """# Loop Ledger

| ID | Opened | Person/Company | Loop | Closure target | Status |
|---|---|---|---|---|---|
| L-2026-01-01-001 | 2026-01-01 | Test Contact | Overdue test loop. | 2026-01-15 | open |
| L-2026-01-01-002 | 2026-01-01 | Test Contact | Future test loop. | 2099-01-01 | open |

## Closed / abandoned
"""


class TestReviewWeeklyPlan(unittest.TestCase):
    def test_reads_plan_and_scorecard(self, ):
        with patch.object(fer, "WEEKLY_PLAN_PATH", self._write_tmp(
                {"week_of": "2026-08-24", "status": "active"}, "plan.json")), \
             patch.object(fer, "WEEKLY_SCORECARD_DRAFT_PATH", self._write_tmp(
                {"week_of": "2026-08-24", "status": "draft_pending_confirmation",
                 "wins": ["a", "b"], "misses": ["c"], "overall_score": 7.5}, "scorecard.json")):
            result = fer.review_weekly_plan()
        self.assertEqual(result["plan_status"], "active")
        self.assertEqual(result["wins"], 2)
        self.assertEqual(result["misses"], 1)
        self.assertEqual(result["overall_score"], 7.5)

    def test_missing_files_do_not_crash(self):
        with patch.object(fer, "WEEKLY_PLAN_PATH", Path("/tmp/does-not-exist-plan.json")), \
             patch.object(fer, "WEEKLY_SCORECARD_DRAFT_PATH", Path("/tmp/does-not-exist-scorecard.json")):
            result = fer.review_weekly_plan()
        self.assertIsNone(result["plan_status"])
        self.assertEqual(result["wins"], 0)

    def _write_tmp(self, data, name):
        import tempfile
        p = Path(tempfile.mkdtemp()) / name
        p.write_text(json.dumps(data))
        return p


class TestReviewLoops(unittest.TestCase):
    def test_l_namespace_overdue_surfaced(self):
        import tempfile
        ledger_path = Path(tempfile.mkdtemp()) / "loop_ledger.md"
        ledger_path.write_text(SAMPLE_LEDGER)
        with patch.object(core, "LOOP_LEDGER_PATH", ledger_path), \
             patch.object(fer.eolms, "_load", return_value=[]):
            result = fer.review_loops(today=date(2026, 8, 24))
        self.assertEqual(len(result["l_overdue"]), 1)
        self.assertEqual(result["l_overdue"][0]["id"], "L-2026-01-01-001")
        self.assertIsNone(result["error"])

    def test_el_namespace_stale_surfaced(self):
        stale_loop = MagicMock()
        stale_loop.id = "EL-2026-01-01-001"
        stale_loop.title = "Stale test loop"
        stale_loop.status = "active"
        stale_loop.days_since_activity = 90
        stale_loop.dormancy_threshold.return_value = 45
        # RB-2026-08-24: past the fixed 45d filter AND its own (lower) 45d
        # dormancy threshold -- past_own_dormancy_threshold must be True.

        fresh_loop = MagicMock()
        fresh_loop.id = "EL-2026-01-01-002"
        fresh_loop.status = "active"
        fresh_loop.days_since_activity = 3
        fresh_loop.dormancy_threshold.return_value = 45

        import tempfile
        ledger_path = Path(tempfile.mkdtemp()) / "loop_ledger.md"
        ledger_path.write_text("# Loop Ledger\n\n| ID | Opened | Person/Company | Loop | Closure target | Status |\n|---|---|---|---|---|---|\n\n## Closed / abandoned\n")
        with patch.object(core, "LOOP_LEDGER_PATH", ledger_path), \
             patch.object(fer.eolms, "_load", return_value=[stale_loop, fresh_loop]):
            result = fer.review_loops(today=date(2026, 8, 24))
        self.assertEqual(len(result["el_stale"]), 1)
        self.assertEqual(result["el_stale"][0]["id"], "EL-2026-01-01-001")
        self.assertTrue(result["el_stale"][0]["past_own_dormancy_threshold"])

    def test_past_fixed_filter_but_under_own_higher_dormancy_threshold(self):
        """RB-2026-08-24 real case caught in the first live run: a loop
        (category-based dormancy_threshold of 90d, e.g. an RB/business
        strategic-initiative category) at 53d since activity is correctly
        included (53 > the fixed 45d EOLMS_STALENESS_WARNING_DAYS filter)
        but has NOT passed its own, higher, per-loop threshold (53 < 90) --
        past_own_dormancy_threshold must be False, not True, or the report
        reads as self-contradictory ("53d, threshold 90d" with no flag)."""
        loop = MagicMock()
        loop.id = "EL-2026-07-02-004"
        loop.title = "RB — Intelligence Platform"
        loop.status = "active"
        loop.days_since_activity = 53
        loop.dormancy_threshold.return_value = 90

        import tempfile
        ledger_path = Path(tempfile.mkdtemp()) / "loop_ledger.md"
        ledger_path.write_text("# Loop Ledger\n\n| ID | Opened | Person/Company | Loop | Closure target | Status |\n|---|---|---|---|---|---|\n\n## Closed / abandoned\n")
        with patch.object(core, "LOOP_LEDGER_PATH", ledger_path), \
             patch.object(fer.eolms, "_load", return_value=[loop]):
            result = fer.review_loops(today=date(2026, 8, 24))

        self.assertEqual(len(result["el_stale"]), 1)
        self.assertFalse(result["el_stale"][0]["past_own_dormancy_threshold"])

    def test_one_namespace_failure_does_not_block_the_other(self):
        with patch.object(core, "LOOP_LEDGER_PATH", Path("/tmp/does-not-exist-ledger.md")), \
             patch.object(fer.eolms, "_load", return_value=[]):
            result = fer.review_loops(today=date(2026, 8, 24))
        self.assertIsNotNone(result["error"])
        self.assertEqual(result["el_stale"], [])  # EL- side still ran cleanly


class TestCheckFreshness(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self.tmpdir.name)
        self.fresh_file = self.tmp_root / "fresh.json"
        self.fresh_file.write_text("{}")
        self.stale_file = self.tmp_root / "stale.md"
        self.stale_file.write_text("stale")
        import os
        import time as _time
        old_dt = date.today() - timedelta(days=30)
        old_time = _time.mktime(old_dt.timetuple())
        os.utime(self.stale_file, (old_time, old_time))
        self._orig_files = fer.FRESHNESS_FILES
        self._orig_registry = fer.CUSTOMERS_PROSPECTS_REGISTRY_PATH
        fer.FRESHNESS_FILES = [self.fresh_file, self.stale_file, self.tmp_root / "missing.json"]
        fer.CUSTOMERS_PROSPECTS_REGISTRY_PATH = self.tmp_root / "registry.json"
        (self.tmp_root / "registry.json").write_text(json.dumps({"registry": []}))

    def tearDown(self):
        fer.FRESHNESS_FILES = self._orig_files
        fer.CUSTOMERS_PROSPECTS_REGISTRY_PATH = self._orig_registry
        self.tmpdir.cleanup()

    def test_fresh_file_not_flagged(self):
        findings = fer.check_freshness(today=date.today())
        fresh = next(f for f in findings if "fresh.json" in f["target"])
        self.assertFalse(fresh["stale"])

    def test_stale_file_flagged(self):
        findings = fer.check_freshness(today=date.today())
        stale = next(f for f in findings if "stale.md" in f["target"])
        self.assertTrue(stale["stale"])
        self.assertGreater(stale["days_since_updated"], 7)

    def test_missing_file_flagged_stale(self):
        findings = fer.check_freshness(today=date.today())
        missing = next(f for f in findings if "missing.json" in f["target"])
        self.assertTrue(missing["stale"])
        self.assertEqual(missing["reason"], "missing")

    def test_blue_sheet_last_review_date_checked(self):
        fer.CUSTOMERS_PROSPECTS_REGISTRY_PATH.write_text(json.dumps({"registry": [
            {"account_id": "acct-test", "workbook_path": "accounts/test/current/x.xlsx",
             "last_review_date": (date.today() - timedelta(days=20)).isoformat()},
            {"account_id": "acct-unactivated", "workbook_path": None,
             "last_review_date": None},
        ]}))
        findings = fer.check_freshness(today=date.today())
        bs_findings = [f for f in findings if "customers_prospects" in f["target"]]
        self.assertEqual(len(bs_findings), 1)  # unactivated account correctly skipped
        self.assertTrue(bs_findings[0]["stale"])
        self.assertIn("acct-test", bs_findings[0]["target"])


class TestReviewReviewQueues(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp_root = Path(tempfile.mkdtemp())
        self.q1 = self.tmp_root / "q1.json"
        self.q2 = self.tmp_root / "q2.json"
        self.q3 = self.tmp_root / "q3_missing.json"
        self._orig_paths = fer.REVIEW_QUEUE_PATHS
        fer.REVIEW_QUEUE_PATHS = [self.q1, self.q2, self.q3]

    def tearDown(self):
        fer.REVIEW_QUEUE_PATHS = self._orig_paths

    def test_recent_item_not_flagged(self):
        self.q1.write_text(json.dumps({"pending_reviews": [
            {"account_id": "acct-fresh", "kind": "signal_notification", "reason": "r",
             "status": "pending", "queued_at": "2026-08-20T00:00:00Z"},
        ]}))
        self.q2.write_text(json.dumps({"pending_reviews": []}))
        findings = fer.review_review_queues(today=date(2026, 8, 24))
        fresh = next(f for f in findings if f["item"] == "acct-fresh")
        self.assertFalse(fresh["stale"])
        self.assertEqual(fresh["days_pending"], 4)

    def test_old_item_flagged_stale(self):
        self.q1.write_text(json.dumps({"pending_reviews": [
            {"account_id": "acct-old", "kind": "signal_notification", "reason": "r",
             "status": "pending", "queued_at": "2026-08-01T00:00:00Z"},
        ]}))
        self.q2.write_text(json.dumps({"pending_reviews": []}))
        findings = fer.review_review_queues(today=date(2026, 8, 24))
        old = next(f for f in findings if f["item"] == "acct-old")
        self.assertTrue(old["stale"])
        self.assertEqual(old["days_pending"], 23)

    def test_missing_queued_at_flagged_stale_not_silently_passed(self):
        """The blue_sheets queue's pre-existing entries were written before
        this timestamp existed at all -- a missing queued_at must read as
        stale/unknown, never as fresh."""
        self.q1.write_text(json.dumps({"pending_reviews": [
            {"account_id": "acct-no-timestamp", "kind": "signal_notification",
             "reason": "r", "status": "pending"},
        ]}))
        self.q2.write_text(json.dumps({"pending_reviews": []}))
        findings = fer.review_review_queues(today=date(2026, 8, 24))
        item = next(f for f in findings if f["item"] == "acct-no-timestamp")
        self.assertTrue(item["stale"])
        self.assertIsNone(item["days_pending"])

    def test_resolved_item_not_surfaced(self):
        self.q1.write_text(json.dumps({"pending_reviews": [
            {"account_id": "acct-done", "kind": "signal_notification", "reason": "r",
             "status": "resolved", "queued_at": "2026-01-01T00:00:00Z"},
        ]}))
        self.q2.write_text(json.dumps({"pending_reviews": []}))
        findings = fer.review_review_queues(today=date(2026, 8, 24))
        self.assertFalse(any(f["item"] == "acct-done" for f in findings))

    def test_missing_queue_file_does_not_crash(self):
        self.q1.write_text(json.dumps({"pending_reviews": []}))
        self.q2.write_text(json.dumps({"pending_reviews": []}))
        # q3 intentionally left missing on disk
        findings = fer.review_review_queues(today=date(2026, 8, 24))
        self.assertEqual(findings, [])

    def test_vendor_slug_and_competitor_slug_identify_item(self):
        self.q1.write_text(json.dumps({"pending_reviews": [
            {"vendor_slug": "worldpay", "kind": "score_review_suggested", "reason": "r",
             "status": "pending", "queued_at": "2026-08-01T00:00:00Z"},
        ]}))
        self.q2.write_text(json.dumps({"pending_reviews": [
            {"competitor_slug": "toast", "kind": "staleness_review", "reason": "r",
             "status": "pending", "queued_at": "2026-08-01T00:00:00Z"},
        ]}))
        findings = fer.review_review_queues(today=date(2026, 8, 24))
        items = {f["item"] for f in findings}
        self.assertIn("worldpay", items)
        self.assertIn("toast", items)


class TestBackup(unittest.TestCase):
    def test_copies_existing_files_and_reports_missing(self):
        import tempfile
        tmp_root = Path(tempfile.mkdtemp())
        existing = tmp_root / "baseline_index.json"
        existing.write_text("{}")
        missing = tmp_root / "does_not_exist.json"
        dest_root = tmp_root / "_backups"

        with patch.object(fer, "BACKUP_FILES", [existing, missing]), \
             patch.object(fer, "BACKUPS_DIR", dest_root), \
             patch.object(fer, "CUSTOMERS_PROSPECTS_REGISTRY_PATH", tmp_root / "no_registry.json"):
            result = fer.backup(today=date(2026, 8, 24))

        self.assertEqual(result["copied_count"], 1)
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(result["failed"][0]["reason"], "does not exist")
        dest_dir = Path(result["dest_dir"])
        # src's parent isn't core.SYSTEM_DIR here (it's a tmp dir), so the
        # real namespacing rule prefixes the dest filename with the parent
        # dir name -- check by suffix match rather than the exact composite
        # name, which would make this test brittle to tmp dir naming.
        matches = list(dest_dir.glob("*baseline_index.json"))
        self.assertEqual(len(matches), 1)

    def test_missing_registry_does_not_block_backup_of_static_files(self):
        import tempfile
        tmp_root = Path(tempfile.mkdtemp())
        existing = tmp_root / "weekly_plan.json"
        existing.write_text("{}")

        with patch.object(fer, "BACKUP_FILES", [existing]), \
             patch.object(fer, "BACKUPS_DIR", tmp_root / "_backups"), \
             patch.object(fer, "CUSTOMERS_PROSPECTS_REGISTRY_PATH", tmp_root / "no_such_registry.json"):
            result = fer.backup(today=date(2026, 8, 24))

        self.assertEqual(result["copied_count"], 1)


LOOP_LEDGER_FOR_CLOSEOUT = """# Loop Ledger

| ID | Opened | Person/Company | Loop | Closure target | Status |
|---|---|---|---|---|---|
| L-2026-08-01-001 | 2026-08-01 | Test Contact | Still open. | 2026-08-20 | open |
| L-2026-08-01-002 | 2026-08-01 | Test Contact | Already closed. | 2026-08-20 | closed |

## Closed / abandoned
"""


class TestWeeklyCloseout(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp_root = Path(tempfile.mkdtemp())
        self.plan_path = self.tmp_root / "weekly_plan.json"
        self.carryover_path = self.tmp_root / "carryover.json"
        self.scorecard_path = self.tmp_root / "scorecard.json"
        self.ledger_path = self.tmp_root / "loop_ledger.md"
        self.ledger_path.write_text(LOOP_LEDGER_FOR_CLOSEOUT)
        self._patches = [
            patch.object(fer, "WEEKLY_PLAN_PATH", self.plan_path),
            patch.object(fer, "WEEKLY_PLAN_CARRYOVER_PATH", self.carryover_path),
            patch.object(fer, "WEEKLY_SCORECARD_DRAFT_PATH", self.scorecard_path),
            patch.object(core, "LOOP_LEDGER_PATH", self.ledger_path),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _write_plan(self, week_of, outcomes):
        self.plan_path.write_text(json.dumps({"week_of": week_of, "outcomes": outcomes}))

    def test_no_live_plan_skips(self):
        self.plan_path.write_text(json.dumps({"week_of": "2020-01-06", "outcomes": []}))
        result = fer.weekly_closeout(today=date(2026, 8, 28))
        self.assertIsNotNone(result["skipped_reason"])
        self.assertEqual(result["carryover_outcomes"], [])
        self.assertFalse(self.carryover_path.exists())

    def test_outcome_with_open_loop_carries_forward(self):
        self._write_plan("2026-08-24", [
            {"id": "outcome-a", "title": "Ship the thing", "success_criteria": "sc-a",
             "linked_opportunity_ids": ["opp-a"], "linked_loop_ids": ["L-2026-08-01-001"],
             "portfolio_allocation_pct": 40.0, "status": "active"},
        ])
        result = fer.weekly_closeout(today=date(2026, 8, 28))
        self.assertIsNone(result["skipped_reason"])
        self.assertEqual(len(result["carryover_outcomes"]), 1)
        co = result["carryover_outcomes"][0]
        self.assertEqual(co["id"], "outcome-a")
        self.assertEqual(co["still_open_loop_ids"], ["L-2026-08-01-001"])
        self.assertTrue(self.carryover_path.exists())
        on_disk = json.loads(self.carryover_path.read_text())
        self.assertEqual(on_disk["from_week_of"], "2026-08-24")
        self.assertEqual(len(on_disk["carryover_outcomes"]), 1)

    def test_outcome_with_only_closed_loop_not_carried(self):
        self._write_plan("2026-08-24", [
            {"id": "outcome-b", "title": "Already done", "success_criteria": "sc-b",
             "linked_opportunity_ids": [], "linked_loop_ids": ["L-2026-08-01-002"],
             "portfolio_allocation_pct": 20.0, "status": "active"},
        ])
        result = fer.weekly_closeout(today=date(2026, 8, 28))
        self.assertEqual(result["carryover_outcomes"], [])
        self.assertFalse(self.carryover_path.exists())

    def test_outcome_with_unknown_loop_id_not_carried(self):
        """A loop id that doesn't resolve in the ledger at all (typo, or a
        loop retired outside the normal close flow) must not be ASSUMED
        open -- that would manufacture a carryover from bad data."""
        self._write_plan("2026-08-24", [
            {"id": "outcome-c", "title": "Mystery loop", "success_criteria": "sc-c",
             "linked_opportunity_ids": [], "linked_loop_ids": ["L-does-not-exist"],
             "portfolio_allocation_pct": 10.0, "status": "active"},
        ])
        result = fer.weekly_closeout(today=date(2026, 8, 28))
        self.assertEqual(result["carryover_outcomes"], [])

    def test_outcome_with_no_linked_loops_not_carried(self):
        self._write_plan("2026-08-24", [
            {"id": "outcome-d", "title": "No loops here", "success_criteria": "sc-d",
             "linked_opportunity_ids": [], "linked_loop_ids": [],
             "portfolio_allocation_pct": 10.0, "status": "active"},
        ])
        result = fer.weekly_closeout(today=date(2026, 8, 28))
        self.assertEqual(result["carryover_outcomes"], [])

    def test_scorecard_ready_flag_true_when_draft_pending_matches_week(self):
        self._write_plan("2026-08-24", [])
        self.scorecard_path.write_text(json.dumps({
            "week_of": "2026-08-24", "status": "draft_pending_confirmation",
        }))
        result = fer.weekly_closeout(today=date(2026, 8, 28))
        self.assertTrue(result["scorecard_ready_for_confirmation"])

    def test_scorecard_ready_flag_false_when_week_mismatch(self):
        self._write_plan("2026-08-24", [])
        self.scorecard_path.write_text(json.dumps({
            "week_of": "2026-08-17", "status": "draft_pending_confirmation",
        }))
        result = fer.weekly_closeout(today=date(2026, 8, 28))
        self.assertFalse(result["scorecard_ready_for_confirmation"])

    def test_scorecard_ready_flag_false_when_already_confirmed(self):
        self._write_plan("2026-08-24", [])
        self.scorecard_path.write_text(json.dumps({
            "week_of": "2026-08-24", "status": "confirmed", "confirmed_at": "2026-08-28T20:00:00Z",
        }))
        result = fer.weekly_closeout(today=date(2026, 8, 28))
        self.assertFalse(result["scorecard_ready_for_confirmation"])

    def test_run_includes_weekly_closeout(self):
        """End-to-end wiring check: run() must actually call weekly_closeout()
        and fold its result into the top-level report, not just leave the
        function reachable but unwired."""
        self._write_plan("2026-08-24", [
            {"id": "outcome-a", "title": "Ship the thing", "success_criteria": "sc-a",
             "linked_opportunity_ids": [], "linked_loop_ids": ["L-2026-08-01-001"],
             "portfolio_allocation_pct": 40.0, "status": "active"},
        ])
        with patch.object(fer, "FRESHNESS_FILES", []), \
             patch.object(fer, "REVIEW_QUEUE_PATHS", []), \
             patch.object(fer, "CUSTOMERS_PROSPECTS_REGISTRY_PATH", self.tmp_root / "no_registry.json"), \
             patch.object(fer, "BACKUP_FILES", []), \
             patch.object(fer, "BACKUPS_DIR", self.tmp_root / "_backups"):
            result = fer.run(today=date(2026, 8, 28))
        self.assertIn("weekly_closeout", result)
        self.assertEqual(len(result["weekly_closeout"]["carryover_outcomes"]), 1)
        report = fer._render_report(result)
        self.assertIn("Weekly Close-out", report)
        self.assertIn("Ship the thing", report)


if __name__ == "__main__":
    unittest.main()
