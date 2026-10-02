#!/usr/bin/env python3
"""test_mutation_reconciliation.py — the recording-engine trust-stat sweep
added 2026-08-25 (RB founding principle: input must be assessed and mutate
the right artifact before the CoS may speak; this is the independent check
that it actually happened, since 2026-08-25's live test proved the model
narrating success is not sufficient evidence on its own).

Test IDs:
  MR1 — instrumented op with HTTP traffic and zero confirmed mutations -> silent
  MR2 — instrumented op with a matching mutation_executed inside cadence -> healthy
  MR3 — instrumented op with a mutation_executed outside cadence -> stale
  MR4 — non-instrumented op -> not_yet_instrumented regardless of traffic
  MR5 — instrumented op with zero traffic and zero mutations -> no_activity_observed
  MR6 — calls before instrumented_since are excluded from the silent verdict
        (regression test for the false-positive bug caught live on 2026-08-25)
  MR7 — mutation_rejected events never count as a confirmed mutation
  MR8 — mutation_proposed counts as confirmed activity (review-first ops like
        processMacroSignal are always-proposal-by-design; judging them on
        mutation_executed alone would falsely flag a healthy endpoint SILENT)
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))


def _fmt_request_line(dt: datetime, method: str, path: str, status: int = 200) -> str:
    return f"{dt.strftime('%Y-%m-%d %H:%M:%S')},000 {method} {path}? auth=YES status={status} 10ms\n"


def _audit_line(event_type: str, item_summary: str, dt: datetime) -> str:
    return json.dumps({
        "event_type": event_type,
        "timestamp": dt.isoformat(timespec="seconds"),
        "data_class": "intelligence",
        "item_summary": item_summary,
        "reason": "test",
        "outcome": event_type,
    }) + "\n"


class MutationReconciliationTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.request_log = self.tmp_path / "request.log"
        self.audit_dir = self.tmp_path / "audit"
        self.audit_dir.mkdir()

        self._old_env = {
            "RB_REQUEST_LOG_PATH": os.environ.get("RB_REQUEST_LOG_PATH"),
            "RB_AUDIT_DIR": os.environ.get("RB_AUDIT_DIR"),
        }
        os.environ["RB_REQUEST_LOG_PATH"] = str(self.request_log)
        os.environ["RB_AUDIT_DIR"] = str(self.audit_dir)

        import audit_log as al
        import mutation_reconciliation as mr
        importlib.reload(al)
        importlib.reload(mr)
        self.al = al
        self.mr = mr

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _write_request_lines(self, lines: list[str]) -> None:
        with self.request_log.open("a", encoding="utf-8") as fh:
            fh.writelines(lines)

    def _write_audit_lines(self, lines: list[str]) -> None:
        partition = datetime.now(timezone.utc).strftime("%Y-%m")
        path = self.audit_dir / f"{partition}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.writelines(lines)


class MR1_Silent(MutationReconciliationTestBase):
    def test_traffic_with_zero_confirmed_mutations_is_silent(self):
        now = datetime.now(timezone.utc)
        self.mr.MONITORED_OPS["touchContact"]["instrumented_since"] = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        self._write_request_lines([_fmt_request_line(now, "POST", "/touch")])
        report = self.mr.build_report(days=30)
        self.assertEqual(report["ops"]["touchContact"]["status"], "silent")


class MR2_Healthy(MutationReconciliationTestBase):
    def test_confirmed_mutation_inside_cadence_is_healthy(self):
        now = datetime.now(timezone.utc)
        self.mr.MONITORED_OPS["touchContact"]["instrumented_since"] = (now - timedelta(days=5)).strftime("%Y-%m-%d")
        self._write_request_lines([_fmt_request_line(now, "POST", "/touch")])
        self._write_audit_lines([_audit_line("mutation_executed", "touchContact: id=x date=2026-08-25", now)])
        report = self.mr.build_report(days=30)
        self.assertEqual(report["ops"]["touchContact"]["status"], "healthy")


class MR3_Stale(MutationReconciliationTestBase):
    def test_confirmed_mutation_outside_cadence_is_stale(self):
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=60)
        self.mr.MONITORED_OPS["touchContact"]["instrumented_since"] = (now - timedelta(days=90)).strftime("%Y-%m-%d")
        self.mr.MONITORED_OPS["touchContact"]["cadence_days"] = 14
        self._write_request_lines([_fmt_request_line(old, "POST", "/touch")])
        self._write_audit_lines([_audit_line("mutation_executed", "touchContact: id=x date=old", old)])
        report = self.mr.build_report(days=90)
        self.assertEqual(report["ops"]["touchContact"]["status"], "stale")


class MR4_NotYetInstrumented(MutationReconciliationTestBase):
    def test_uninstrumented_op_reports_that_status_regardless_of_traffic(self):
        now = datetime.now(timezone.utc)
        self._write_request_lines([_fmt_request_line(now, "POST", "/manual_relationship_intake")])
        report = self.mr.build_report(days=30)
        self.assertEqual(report["ops"]["manualRelationshipIntake"]["status"], "not_yet_instrumented")


class MR5_NoActivity(MutationReconciliationTestBase):
    def test_zero_traffic_zero_mutations_is_no_activity_observed(self):
        report = self.mr.build_report(days=30)
        self.assertEqual(report["ops"]["touchContact"]["status"], "no_activity_observed")


class MR6_PreInstrumentationBacklogExcluded(MutationReconciliationTestBase):
    def test_calls_before_instrumented_since_do_not_trigger_a_false_silent(self):
        """Regression test: the first live run of this script (2026-08-25)
        flagged closeLoop/ingestExecutiveDeclaration as SILENT purely because
        they had pre-instrumentation call history — a false positive, fixed
        by gating the verdict on calls_since_instrumented, not raw calls."""
        now = datetime.now(timezone.utc)
        before_instrumentation = now - timedelta(days=10)
        self.mr.MONITORED_OPS["touchContact"]["instrumented_since"] = now.strftime("%Y-%m-%d")
        self._write_request_lines([_fmt_request_line(before_instrumentation, "POST", "/touch")])
        report = self.mr.build_report(days=30)
        self.assertEqual(report["ops"]["touchContact"]["status"], "no_activity_observed")
        self.assertEqual(report["ops"]["touchContact"]["http_calls_since_instrumented"], 0)
        self.assertEqual(report["ops"]["touchContact"]["http_calls_window"], 1)


class MR7_RejectedNotConfirmed(MutationReconciliationTestBase):
    def test_mutation_rejected_does_not_count_as_confirmed(self):
        now = datetime.now(timezone.utc)
        self.mr.MONITORED_OPS["touchContact"]["instrumented_since"] = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        self._write_request_lines([_fmt_request_line(now, "POST", "/touch", status=400)])
        self._write_audit_lines([_audit_line("mutation_rejected", "touchContact: id=bad-id", now)])
        report = self.mr.build_report(days=30)
        self.assertEqual(report["ops"]["touchContact"]["audit_mutations_executed"], 0)
        self.assertEqual(report["ops"]["touchContact"]["audit_mutations_rejected"], 1)
        self.assertEqual(report["ops"]["touchContact"]["status"], "silent")


class MR8_ProposedCountsAsHealthy(MutationReconciliationTestBase):
    def test_mutation_proposed_prevents_a_false_silent_verdict(self):
        """processMacroSignal is proposal-only by design (the real write
        happens later via confirmProposal). A working call there produces
        mutation_proposed, never mutation_executed — that must read as
        healthy, not as a false SILENT alarm."""
        now = datetime.now(timezone.utc)
        self.mr.MONITORED_OPS["processMacroSignal"]["instrumented_since"] = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        self._write_request_lines([_fmt_request_line(now, "POST", "/macro/signal")])
        self._write_audit_lines([_audit_line("mutation_proposed", "processMacroSignal: source_type=linkedin_post status=pending confirmation", now)])
        report = self.mr.build_report(days=30)
        self.assertEqual(report["ops"]["processMacroSignal"]["status"], "healthy")
        self.assertEqual(report["ops"]["processMacroSignal"]["audit_mutations_proposed"], 1)
        self.assertEqual(report["ops"]["processMacroSignal"]["audit_mutations_executed"], 0)


if __name__ == "__main__":
    unittest.main()
