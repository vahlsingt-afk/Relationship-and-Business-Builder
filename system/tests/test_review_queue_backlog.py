"""
test_review_queue_backlog.py — Strategic-assessment Finding 2, 2026-09-19.

_compute_review_queue_backlog() aggregates all 6 review-first promotion
scanners' pending_candidates() into one "how much is waiting on your
confirm, across every queue, and how old is the oldest one" view -- before
this, only executive-move/ownership got any brief visibility at all (same-
day-fresh detections only, via _compute_leadership_ownership_same_day), and
the other four queues (tech-stack relationship, watchlist promotion,
priority-account publisher, job postings) had zero visibility outside each
script's own `... pending` CLI. This is pure aggregation of data every one
of the six scripts already computes -- no new detection.
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db  # noqa: E402
import tech_stack_relationship_promotion as tsrp  # noqa: E402
import watchlist_promotion as wlp  # noqa: E402
import priority_account_publisher_scan as paps  # noqa: E402
import ownership_promotion as ownp  # noqa: E402
import executive_move_promotion as emp  # noqa: E402
import job_postings_promotion as jpp  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _days_ago_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _store_with(*candidates: dict) -> dict:
    return {"candidates": {c["candidate_id"]: c for c in candidates}}


def _candidate(cid: str, detected_at: str) -> dict:
    return {"candidate_id": cid, "status": "proposed_pending_confirmation", "detected_at": detected_at}


_ALL_MODULES = [tsrp, wlp, paps, ownp, emp, jpp]


class TestReviewQueueBacklog(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self.tmpdir.name)
        self._orig_store_paths = {}
        self._orig_flags = {}
        for i, module in enumerate(_ALL_MODULES):
            self._orig_store_paths[module] = module.STORE_PATH
            module.STORE_PATH = tmp / f"store_{i}.json"
        for flag in (
            "_HAS_TECH_STACK_RELATIONSHIP_PROMOTION", "_HAS_WATCHLIST_PROMOTION",
            "_HAS_PRIORITY_ACCOUNT_PUBLISHER_SCAN", "_HAS_LEADERSHIP_OWNERSHIP_PROMOTION",
            "_HAS_JOB_POSTINGS_PROMOTION",
        ):
            self._orig_flags[flag] = getattr(db, flag)
            setattr(db, flag, True)
        db._tsrp, db._wlp, db._paps, db._ownp, db._emp, db._jpp = tsrp, wlp, paps, ownp, emp, jpp

    def tearDown(self):
        for module, path in self._orig_store_paths.items():
            module.STORE_PATH = path
        for flag, value in self._orig_flags.items():
            setattr(db, flag, value)
        self.tmpdir.cleanup()

    def _write_store(self, module, *candidates: dict) -> None:
        module.STORE_PATH.write_text(json.dumps(_store_with(*candidates)))

    def test_all_queues_empty_reports_clean(self):
        items = db._compute_review_queue_backlog({}, {})
        self.assertEqual(len(items), 1)
        self.assertIn("nothing pending", items[0]["title"])
        self.assertEqual(items[0]["disposition"], "ignore")

    def test_single_queue_with_one_recent_candidate_is_monitor_not_urgent(self):
        self._write_store(tsrp, _candidate("c1", _now_iso()))
        items = db._compute_review_queue_backlog({}, {})
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertIn("1 item(s) across 1 queue(s)", item["title"])
        self.assertEqual(item["disposition"], "monitor")
        self.assertIn("tech-stack relationship", item["summary"])

    def test_aggregates_across_multiple_queues(self):
        self._write_store(tsrp, _candidate("c1", _now_iso()))
        self._write_store(wlp, _candidate("c2", _now_iso()), _candidate("c3", _now_iso()))
        self._write_store(jpp, _candidate("c4", _now_iso()))
        item = db._compute_review_queue_backlog({}, {})[0]
        self.assertEqual(item["extras"]["total_pending"], 4)
        self.assertIn("3 queue(s)", item["title"])

    def test_oldest_candidate_past_threshold_escalates_to_act_today(self):
        self._write_store(paps, _candidate("c1", _days_ago_iso(5)))
        item = db._compute_review_queue_backlog({}, {})[0]
        self.assertEqual(item["disposition"], "act_today")
        self.assertEqual(item["freshness"], "stale")
        self.assertIn("priority-account publisher match", item["summary"])
        self.assertEqual(item["extras"]["oldest_days"], 5)

    def test_oldest_candidate_under_threshold_stays_monitor(self):
        self._write_store(ownp, _candidate("c1", _days_ago_iso(1)))
        item = db._compute_review_queue_backlog({}, {})[0]
        self.assertEqual(item["disposition"], "monitor")
        self.assertEqual(item["freshness"], "fresh")

    def test_oldest_across_queues_is_correctly_identified(self):
        self._write_store(emp, _candidate("c1", _days_ago_iso(1)))
        self._write_store(jpp, _candidate("c2", _days_ago_iso(7)))
        item = db._compute_review_queue_backlog({}, {})[0]
        self.assertEqual(item["extras"]["oldest_days"], 7)
        self.assertIn("job posting", item["summary"])

    def test_confirmed_or_rejected_candidates_are_not_counted(self):
        tsrp.STORE_PATH.write_text(json.dumps({"candidates": {
            "c1": {"candidate_id": "c1", "status": "confirmed", "detected_at": _now_iso()},
            "c2": {"candidate_id": "c2", "status": "rejected", "detected_at": _now_iso()},
        }}))
        items = db._compute_review_queue_backlog({}, {})
        self.assertIn("nothing pending", items[0]["title"])

    def test_one_broken_queue_does_not_break_the_whole_aggregation(self):
        """A malformed store file alone doesn't prove this -- each module's
        own _load_store() already swallows JSON errors internally. Forces a
        genuine exception from one module's pending_candidates() directly,
        so this actually exercises the aggregator's own per-module guard."""
        self._write_store(wlp, _candidate("c1", _now_iso()))
        with patch.object(tsrp, "pending_candidates", side_effect=RuntimeError("boom")):
            item = db._compute_review_queue_backlog({}, {})[0]
        self.assertEqual(item["extras"]["total_pending"], 1)

    def test_disabled_module_flag_excludes_its_queue(self):
        db._HAS_TECH_STACK_RELATIONSHIP_PROMOTION = False
        self._write_store(tsrp, _candidate("c1", _now_iso()))
        items = db._compute_review_queue_backlog({}, {})
        self.assertIn("nothing pending", items[0]["title"])


if __name__ == "__main__":
    main()
