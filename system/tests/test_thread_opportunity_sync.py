"""
test_thread_opportunity_sync.py — RB-DEFECT-040 / RB 9.70 item 2

Regression coverage for `mutations.py thread-update`:

  1. A new `thread-update` subcommand exists to edit an existing
     `active_threads.yaml` entry's `current_state` / `status` /
     `boost_score` / `boost_for_brief` without hand-editing YAML
     (previously the only way to do this, as happened for
     T-2026-05-genius-global-payments on 2026-06-12).

  2. When `--opportunity-stage` is given on a thread whose `type` is in
     OPPORTUNITY_TYPES, the command also upserts
     `system/tracked_opportunities.json` via
     `opportunity_pipeline.process_opportunity_update(apply=True)` — so
     `active_threads.yaml` and `tracked_opportunities.json` move together
     instead of silently diverging (the root cause of
     `active_opportunity_pipeline` returning 0 items despite an active,
     just-changed opportunity).
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

import mutations  # noqa: E402
import opportunity_pipeline as op  # noqa: E402
import rb_core as core  # noqa: E402


SAMPLE_THREADS = {
    "version": 1,
    "threads": [
        {
            "id": "T-2026-05-genius-global-payments",
            "title": "Genius / Global Payments role conversation",
            "opened": "2026-05-12",
            "status": "open",
            "type": "job_opportunity",
            "people": ["mike-schwartz"],
            "companies": ["Global Payments Inc.", "Genius", "Xenial"],
            "context": "Job opportunity thread.",
            "current_state": "Candidate Evaluation stage.",
            "boost_for_brief": "high",
            "boost_score": 1.4,
        },
        {
            "id": "T-2026-06-perfect-hire-advisory",
            "title": "Perfect Hire — Strategic Advisor / Equity Opportunity",
            "opened": "2026-06-12",
            "status": "open",
            "type": "business_engagement",
            "people": ["matt"],
            "companies": ["Perfect Hire"],
            "context": "Advisory conversation.",
            "current_state": "Pre-confirmation.",
            "boost_for_brief": "medium",
            "boost_score": 1.2,
        },
    ],
}


def _make_args(**kwargs) -> object:
    """Build a minimal argparse.Namespace-like object for cmd_thread_update."""
    defaults = dict(
        id=None,
        current_state=None,
        status=None,
        boost_score=None,
        boost_for_brief=None,
        opportunity_stage=None,
        opportunity_company=None,
        opportunity_role=None,
        dry_run=False,
    )
    defaults.update(kwargs)

    class Args:
        pass

    a = Args()
    for k, v in defaults.items():
        setattr(a, k, v)
    return a


class TestThreadUpdate(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.threads_path = Path(self.tmpdir.name) / "active_threads.yaml"
        self.opps_path = Path(self.tmpdir.name) / "tracked_opportunities.json"

        import yaml
        self.threads_path.write_text(yaml.safe_dump(SAMPLE_THREADS, sort_keys=False))

        self._patches = [
            patch.object(core, "ACTIVE_THREADS_PATH", self.threads_path),
            patch.object(mutations, "snapshot", lambda path, tag: path),  # no-op snapshot
            patch.object(op, "TRACKED_OPPORTUNITIES_PATH", self.opps_path),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmpdir.cleanup()

    def test_update_current_state_and_boost(self):
        args = _make_args(
            id="T-2026-05-genius-global-payments",
            current_state="STATUS CHANGE: offer stage.",
            boost_score=1.8,
        )
        rc = mutations.cmd_thread_update(args)
        self.assertEqual(rc, 0)

        data, _ = mutations._read_threads_file()
        t = next(t for t in data["threads"] if t["id"] == "T-2026-05-genius-global-payments")
        self.assertEqual(t["current_state"], "STATUS CHANGE: offer stage.")
        self.assertEqual(t["boost_score"], 1.8)

    def test_unknown_thread_id_errors(self):
        args = _make_args(id="T-does-not-exist", current_state="x")
        rc = mutations.cmd_thread_update(args)
        self.assertEqual(rc, 1)

    def test_opportunity_stage_sync_on_job_opportunity_thread(self):
        """Mirrors the 2026-06-12 Global Payments case: a job_opportunity
        thread's stage change should also land in tracked_opportunities.json
        so _active_opportunity_pipeline_items() (which reads
        opportunity_pipeline.recent_changes) sees it."""
        args = _make_args(
            id="T-2026-05-genius-global-payments",
            current_state="Received a verbal offer; evaluating package.",
            boost_score=1.8,
            opportunity_stage="offer_verbal",
        )
        rc = mutations.cmd_thread_update(args)
        self.assertEqual(rc, 0)

        # active_threads.yaml updated
        data, _ = mutations._read_threads_file()
        t = next(t for t in data["threads"] if t["id"] == "T-2026-05-genius-global-payments")
        self.assertEqual(t["current_state"], "Received a verbal offer; evaluating package.")

        # tracked_opportunities.json upserted
        self.assertTrue(self.opps_path.exists())
        store = json.loads(self.opps_path.read_text())
        opps = store["opportunities"]
        self.assertEqual(len(opps), 1)
        opp = opps[0]
        self.assertEqual(opp["stage"], "offer_verbal")
        # Company taken from thread.companies[0]
        self.assertEqual(opp["company"], "Global Payments Inc.")

        # recent_changes() (used by the brief) now sees it
        recent = op.recent_changes(within_days=1, store_path=self.opps_path)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["stage"], "offer_verbal")

    def test_opportunity_stage_on_business_engagement_thread(self):
        """business_engagement is included in OPPORTUNITY_TYPES so career-
        adjacent threads like the Perfect Hire advisory conversation can also
        sync into tracked_opportunities.json."""
        args = _make_args(
            id="T-2026-06-perfect-hire-advisory",
            opportunity_stage="target_identified",
            opportunity_company="Perfect Hire",
        )
        rc = mutations.cmd_thread_update(args)
        self.assertEqual(rc, 0)

        store = json.loads(self.opps_path.read_text())
        self.assertEqual(len(store["opportunities"]), 1)
        self.assertEqual(store["opportunities"][0]["company"], "Perfect Hire")

    def test_opportunity_stage_requires_company(self):
        """A thread with no companies[] and no --opportunity-company should
        error rather than silently doing nothing."""
        # Mutate the fixture to strip companies.
        data, _ = mutations._read_threads_file()
        for t in data["threads"]:
            if t["id"] == "T-2026-06-perfect-hire-advisory":
                t["companies"] = []
        mutations._write_threads_file(data)

        args = _make_args(
            id="T-2026-06-perfect-hire-advisory",
            opportunity_stage="target_identified",
        )
        rc = mutations.cmd_thread_update(args)
        self.assertEqual(rc, 2)
        self.assertFalse(self.opps_path.exists())

    def test_opportunity_stage_rejected_for_non_opportunity_type(self):
        # chapter_activation / role_search / account_pursuit aren't in
        # OPPORTUNITY_TYPES; ensure the guard rejects them with exit code 2.
        data, _ = mutations._read_threads_file()
        data["threads"].append({
            "id": "T-2026-06-non-opportunity",
            "title": "Some other thread",
            "status": "open",
            "type": "chapter_activation",
            "companies": ["Acme"],
            "current_state": "n/a",
        })
        mutations._write_threads_file(data)

        args = _make_args(
            id="T-2026-06-non-opportunity",
            opportunity_stage="target_identified",
        )
        rc = mutations.cmd_thread_update(args)
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
