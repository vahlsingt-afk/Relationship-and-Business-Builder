#!/usr/bin/env python3
"""
test_intelligence_cascade.py — RB-2026-08-27/28.

Mirrors test_blue_sheet_coverage_hook.py's MagicMock-based isolation for
anything that would otherwise touch the real, shared blue_sheets/_portfolio/
files (review_queue.json, coverage_log.jsonl), and customers_prospects_
common's own monkeypatched-path-constant style (REFRESH_FLAGS_PATH is not
injectable) for the 24h SLA state. The real inner function
(_apply_customers_prospects_sync -- RB-2026-09-08, merges what were two
separate functions per the original 3-store unification plan's own step 4)
is called directly with synthetic data rather than through run_cascade()'s
full real-graph read, since it's already designed to take data in rather
than fetch it themselves. Each test still exercises only one of the two
internal behaviors (Blue Sheet vs. Account Research) by passing an empty
relationships list or entities dict for the side it isn't testing.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import intelligence_cascade as ic  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402


class TestIdempotency(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="rb-cascade-test-")
        self._orig_cache_path = ic.CACHE_PATH
        ic.CACHE_PATH = Path(self._tmpdir) / "intelligence_cascade.json"

    def tearDown(self):
        ic.CACHE_PATH = self._orig_cache_path
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_second_run_same_day_is_a_cache_hit(self):
        ic.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ic.CACHE_PATH.write_text(
            '{"date": "2026-08-28", "generated_at": "SENTINEL", "entities_with_new_intelligence": 0, '
            '"relationships_touched": 0, "blue_sheets_auto_synced": [], "blue_sheets_queued_for_review": [], '
            '"account_research_flagged_stale": [], "account_research_auto_processed": [], '
            '"coverage_gaps_today": [], "coverage_gaps_14d_pattern": []}',
            encoding="utf-8",
        )
        result = ic.run_cascade(date(2026, 8, 28))
        self.assertEqual(result["generated_at"], "SENTINEL", "must return the cached result, not re-run")

    def test_verify_reports_ok_only_for_todays_cache(self):
        ic.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ic.CACHE_PATH.write_text('{"date": "2026-08-27"}', encoding="utf-8")
        cached = ic._load_json(ic.CACHE_PATH, {})
        self.assertNotEqual(cached.get("date"), date(2026, 8, 28).isoformat())


class TestBlueSheetSync(unittest.TestCase):
    """MagicMock isolation, same style as test_blue_sheet_coverage_hook.py --
    never touches the real review_queue.json/coverage_log.jsonl.

    RB-2026-09-08: since the merge into _apply_customers_prospects_sync(),
    EVERY call also unconditionally runs the Account Research sweep phase
    (it reads cpc.REFRESH_FLAGS_PATH regardless of whether `entities` is
    empty) -- previously _apply_blue_sheet_sync() never touched that
    resource at all, so this class didn't need to isolate it. Passing an
    empty entities dict is not enough on its own; REFRESH_FLAGS_PATH must
    also be redirected to a disposable tmp path so a real pending flag in
    the live cache can't trigger a real, unmocked abb.generate_brief() call
    during what's meant to be a pure Blue Sheet test."""

    def setUp(self):
        self._orig_bs_common = ic.bs_common
        self._orig_ei_sync = ic.ei._sync_blue_sheet_technology_stack
        self.fake_common = MagicMock()
        ic.bs_common = self.fake_common

        self._tmpdir = tempfile.mkdtemp(prefix="rb-cascade-bs-test-")
        self._orig_flags_path = cpc.REFRESH_FLAGS_PATH
        cpc.REFRESH_FLAGS_PATH = Path(self._tmpdir) / "customers_prospects_refresh_flags.json"

    def tearDown(self):
        ic.bs_common = self._orig_bs_common
        ic.ei._sync_blue_sheet_technology_stack = self._orig_ei_sync
        cpc.REFRESH_FLAGS_PATH = self._orig_flags_path
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_unambiguous_match_calls_real_sync_function(self):
        self.fake_common.entity_id_to_slug.return_value = "pollo-campero"
        self.fake_common.is_activated.return_value = True
        self.fake_common.POSTURE_TO_STATUS = {"substantiated": "Confirmed"}
        self.fake_common.load_json.return_value = {"account_id": "acct-pollo-campero", "technology_stack": []}
        self.fake_common.match_technology_stack_row.return_value = 2
        ic.ei._sync_blue_sheet_technology_stack = MagicMock()

        rel = {"from_entity_id": "brand-pollo-campero", "category": "pos", "evidence_posture": "substantiated", "id": "rel-1"}
        result = ic._apply_customers_prospects_sync([rel], {}, "2026-08-28")

        ic.ei._sync_blue_sheet_technology_stack.assert_called_once()
        self.assertEqual(len(result["blue_sheets_auto_synced"]), 1)
        self.assertEqual(result["blue_sheets_queued_for_review"], [])
        self.fake_common.save_json.assert_not_called()  # no review_queue write on a clean auto-apply

    def test_ambiguous_match_writes_review_queue_never_auto_applies(self):
        self.fake_common.entity_id_to_slug.return_value = "pollo-campero"
        self.fake_common.is_activated.return_value = True
        self.fake_common.POSTURE_TO_STATUS = {"substantiated": "Confirmed"}
        self.fake_common.load_json.side_effect = [
            {"account_id": "acct-pollo-campero", "technology_stack": []},  # account.json read
            {"pending_reviews": []},  # review_queue.json read
        ]
        self.fake_common.match_technology_stack_row.return_value = None  # ambiguous/unmapped
        self.fake_common.ROOT = Path("/fake/root")
        ic.ei._sync_blue_sheet_technology_stack = MagicMock()

        rel = {"from_entity_id": "brand-pollo-campero", "category": "payments", "evidence_posture": "substantiated", "id": "rel-2"}
        result = ic._apply_customers_prospects_sync([rel], {}, "2026-08-28")

        ic.ei._sync_blue_sheet_technology_stack.assert_not_called()
        self.assertEqual(len(result["blue_sheets_queued_for_review"]), 1)
        self.fake_common.save_json.assert_called_once()
        written = self.fake_common.save_json.call_args.args[1]
        entry = written["pending_reviews"][0]
        self.assertEqual(entry["kind"], "signal_notification")
        self.assertIsNone(entry["path"])
        self.assertIsNone(entry["new_value"])  # never a guessed value

    def test_unactivated_account_logs_coverage_event_not_a_write(self):
        self.fake_common.entity_id_to_slug.return_value = "five-guys"
        self.fake_common.is_activated.return_value = False

        rel = {"from_entity_id": "brand-five-guys", "category": "pos", "evidence_posture": "provisional", "id": "rel-3"}
        result = ic._apply_customers_prospects_sync([rel], {}, "2026-08-28")

        self.fake_common.log_coverage_event.assert_called_once()
        self.assertEqual(len(result["coverage_gaps"]), 1)
        self.fake_common.save_json.assert_not_called()

    def test_non_brand_entity_is_skipped_entirely(self):
        self.fake_common.entity_id_to_slug.return_value = None  # e.g. a vendor-* entity
        rel = {"from_entity_id": "vendor-ncr", "category": "pos", "evidence_posture": "substantiated"}
        result = ic._apply_customers_prospects_sync([rel], {}, "2026-08-28")
        self.assertEqual(result, {
            "blue_sheets_auto_synced": [], "blue_sheets_queued_for_review": [], "coverage_gaps": [],
            "account_research_flagged_stale": [], "account_research_auto_processed": [],
        })
        self.fake_common.is_activated.assert_not_called()


class TestAccountResearchSLA(unittest.TestCase):
    """Real filesystem I/O against a disposable REFRESH_FLAGS_PATH -- never
    the real account_research_refresh_flags.json."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="rb-cascade-sla-test-")
        self._orig_flags_path = cpc.REFRESH_FLAGS_PATH
        cpc.REFRESH_FLAGS_PATH = Path(self._tmpdir) / "account_research_refresh_flags.json"
        self._orig_generate_brief = ic.abb.generate_brief
        self._orig_resolve_account = ic.abb.resolve_account
        self._orig_retrieve = ic.abb.retrieve_existing_intelligence
        self._orig_assess = ic.abb.assess_freshness

    def tearDown(self):
        cpc.REFRESH_FLAGS_PATH = self._orig_flags_path
        ic.abb.generate_brief = self._orig_generate_brief
        ic.abb.resolve_account = self._orig_resolve_account
        ic.abb.retrieve_existing_intelligence = self._orig_retrieve
        ic.abb.assess_freshness = self._orig_assess
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_flag_created_today_stays_pending_same_day(self):
        ic.abb.resolve_account = MagicMock(return_value=("test-brand", True))
        ic.abb.retrieve_existing_intelligence = MagicMock(return_value={})
        ic.abb.assess_freshness = MagicMock(return_value={"stale": [{"path": "x"}], "missing": []})
        ic.abb.generate_brief = MagicMock()

        today_str = date(2026, 8, 28).isoformat()
        result = ic._apply_customers_prospects_sync([], {"Test Brand": {}}, today_str)

        self.assertEqual(result["account_research_flagged_stale"], ["test-brand"])
        self.assertEqual(result["account_research_auto_processed"], [])
        ic.abb.generate_brief.assert_not_called()  # same-day flag must NOT trigger a regenerate

    def test_prior_day_flag_gets_auto_processed(self):
        flags = {"test-brand": {"first_flagged_at": "2026-08-27", "reason": "x", "status": "pending", "processed_at": None}}
        cpc.save_refresh_flags(flags)
        ic.abb.generate_brief = MagicMock()

        result = ic._apply_customers_prospects_sync([], {}, date(2026, 8, 28).isoformat())

        ic.abb.generate_brief.assert_called_once()
        self.assertEqual(result["account_research_auto_processed"], ["test-brand"])

    def test_clear_refresh_flag_is_idempotent_and_removes_entry(self):
        cpc.save_refresh_flags({"test-brand": {"first_flagged_at": "2026-08-27", "reason": "x", "status": "pending", "processed_at": None}})
        cpc.clear_refresh_flag("test-brand")
        self.assertNotIn("test-brand", cpc.load_refresh_flags())
        cpc.clear_refresh_flag("test-brand")  # second call must not raise

    def test_flag_needs_refresh_does_not_duplicate_an_existing_pending_flag(self):
        created_first = cpc.flag_needs_refresh("test-brand", reason="first")
        created_second = cpc.flag_needs_refresh("test-brand", reason="second")
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(cpc.load_refresh_flags()["test-brand"]["reason"], "first")


if __name__ == "__main__":
    unittest.main()
