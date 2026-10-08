"""
test_self_audit_sweep.py — RB-2026-08-28.

self_audit_sweep.py closes the gap named in the same day's strategic
assessment: RB's own self-check tools (KB consistency, mutation
reconciliation, JPR capture completeness) already existed but never ran on
a schedule or surfaced to Todd -- each wrote a cache file nobody read
unless they went looking. This wires them into one sweep that opens/closes
a standing loop_ledger.md entry, which daily_brief.py already renders.

Tests mock the three finding-source functions directly (each is
independently testable/tested elsewhere -- validate_kb_consistency,
mutation_reconciliation, jpr_recordings_index) and focus on this module's
own real logic: aggregation and idempotent loop open/update/close.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import self_audit_sweep as sas  # noqa: E402
import rb_core as core  # noqa: E402
import mutations  # noqa: E402


class _IsolatedLedgerMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        ledger_path = Path(self._tmpdir.name) / "loop_ledger.md"
        ledger_path.write_text(
            "# Loop Ledger\n\n"
            "| ID | Opened | Party | Description | Target | Status |\n"
            "|---|---|---|---|---|---|\n",
            encoding="utf-8",
        )
        self._orig_ledger_path = core.LOOP_LEDGER_PATH
        core.LOOP_LEDGER_PATH = ledger_path
        self._orig_snapshots_dir = core.SNAPSHOTS_DIR
        core.SNAPSHOTS_DIR = Path(self._tmpdir.name) / "_snapshots"

    def tearDown(self):
        core.LOOP_LEDGER_PATH = self._orig_ledger_path
        core.SNAPSHOTS_DIR = self._orig_snapshots_dir
        self._tmpdir.cleanup()


class TestReachabilityFindings(unittest.TestCase):
    """RB-2026-10-08, self-healing check #1: _reachability_findings()
    delegates to render_reachability_check.run_all_checks() and never lets
    an exception there take down the whole sweep -- same "best-effort,
    never silently ignored" discipline the other checks already have."""

    def test_delegates_to_run_all_checks_findings(self):
        with patch.object(sas.rrc, "run_all_checks", return_value={"findings": ["x unreachable"], "clean": False}):
            self.assertEqual(sas._reachability_findings(), ["x unreachable"])

    def test_clean_report_yields_no_findings(self):
        with patch.object(sas.rrc, "run_all_checks", return_value={"findings": [], "clean": True}):
            self.assertEqual(sas._reachability_findings(), [])

    def test_exception_becomes_a_finding_not_a_crash(self):
        with patch.object(sas.rrc, "run_all_checks", side_effect=RuntimeError("boom")):
            findings = sas._reachability_findings()
        self.assertEqual(len(findings), 1)
        self.assertIn("could not run", findings[0])


class TestCollectFindings(unittest.TestCase):
    # RB-2026-09-15: _test_suite_findings() is always mocked here -- without
    # this, collect_findings() (skip_test_suite defaults to False) would
    # actually spawn the real ~6-7min pytest subprocess from INSIDE a pytest
    # run, self-referentially, every time this test file runs.
    def test_clean_when_all_sources_empty(self):
        with patch.object(sas, "_kb_findings", return_value=[]), \
             patch.object(sas, "_mutation_reconciliation_findings", return_value=[]), \
             patch.object(sas, "_jpr_findings", return_value=[]), \
             patch.object(sas, "_reachability_findings", return_value=[]), \
             patch.object(sas, "_test_suite_findings", return_value=[]):
            result = sas.collect_findings()
        self.assertTrue(result["clean"])
        self.assertEqual(result["all_findings"], [])

    def test_aggregates_findings_from_all_sources(self):
        with patch.object(sas, "_kb_findings", return_value=["kb issue"]), \
             patch.object(sas, "_mutation_reconciliation_findings", return_value=["silent op issue"]), \
             patch.object(sas, "_jpr_findings", return_value=["jpr issue"]), \
             patch.object(sas, "_reachability_findings", return_value=["reachability issue"]), \
             patch.object(sas, "_test_suite_findings", return_value=["test suite issue"]):
            result = sas.collect_findings()
        self.assertFalse(result["clean"])
        self.assertEqual(len(result["all_findings"]), 5)
        self.assertIn("kb issue", result["all_findings"])
        self.assertIn("reachability issue", result["all_findings"])
        self.assertIn("test suite issue", result["all_findings"])

    def test_skip_test_suite_flag_bypasses_the_real_subprocess(self):
        with patch.object(sas, "_kb_findings", return_value=[]), \
             patch.object(sas, "_mutation_reconciliation_findings", return_value=[]), \
             patch.object(sas, "_jpr_findings", return_value=[]), \
             patch.object(sas, "_reachability_findings", return_value=[]), \
             patch.object(sas.subprocess, "run") as mock_run:
            result = sas.collect_findings(skip_test_suite=True)
        mock_run.assert_not_called()
        self.assertTrue(result["clean"])


class TestTestSuiteFindings(unittest.TestCase):
    """RB-2026-09-15: the fourth, broadest check -- the full pytest suite.
    Every test here mocks subprocess.run; none may invoke a real pytest
    subprocess (that would make this test file itself take ~6-7 minutes)."""

    def _completed(self, returncode: int, stdout: str = "", stderr: str = ""):
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    def test_skip_returns_empty_without_running_subprocess(self):
        with patch.object(sas.subprocess, "run") as mock_run:
            result = sas._test_suite_findings(skip=True)
        mock_run.assert_not_called()
        self.assertEqual(result, [])

    def test_passing_suite_returns_no_findings(self):
        with patch.object(sas.subprocess, "run",
                           return_value=self._completed(0, "4830 passed, 1 skipped in 394.83s")):
            result = sas._test_suite_findings()
        self.assertEqual(result, [])

    def test_failing_suite_reports_summary_and_names(self):
        stdout = (
            "=========================== short test summary info ============================\n"
            "FAILED system/tests/test_brief_quality_checks.py::test_no_cross_day_repeat_flags_repeated_url\n"
            "FAILED system/tests/test_brief_quality_checks.py::test_check_brief_integration_passes_on_full_clean_fixture\n"
            "2 failed, 4797 passed, 1 skipped, 7 warnings in 327.79s (0:05:30)\n"
        )
        with patch.object(sas.subprocess, "run", return_value=self._completed(1, stdout)):
            result = sas._test_suite_findings()
        self.assertEqual(len(result), 1)
        self.assertIn("2 failed", result[0])
        self.assertIn("test_no_cross_day_repeat_flags_repeated_url", result[0])

    def test_many_failures_truncates_named_list(self):
        stdout = "\n".join(f"FAILED system/tests/test_x.py::test_{i}" for i in range(8))
        stdout += "\n8 failed, 100 passed in 60s\n"
        with patch.object(sas.subprocess, "run", return_value=self._completed(1, stdout)):
            result = sas._test_suite_findings()
        self.assertIn("...", result[0])

    def test_timeout_is_itself_a_finding_not_silently_ignored(self):
        with patch.object(sas.subprocess, "run",
                           side_effect=sas.subprocess.TimeoutExpired(cmd="pytest", timeout=900)):
            result = sas._test_suite_findings()
        self.assertEqual(len(result), 1)
        self.assertIn("did not complete", result[0])

    def test_subprocess_error_is_itself_a_finding_not_silently_ignored(self):
        with patch.object(sas.subprocess, "run", side_effect=OSError("pytest not found")):
            result = sas._test_suite_findings()
        self.assertEqual(len(result), 1)
        self.assertIn("could not be run", result[0])


class TestApplyLoopUpdate(_IsolatedLedgerMixin, unittest.TestCase):
    def test_opens_loop_when_findings_exist(self):
        result = {"clean": False, "all_findings": ["4 write ops silent"]}
        action = sas.apply_loop_update(result)
        self.assertEqual(action, "opened")
        self.assertTrue(sas._find_open_self_audit_loop_id())

    def test_true_no_op_when_findings_are_identical(self):
        sas.apply_loop_update({"clean": False, "all_findings": ["issue A"]})
        action = sas.apply_loop_update({"clean": False, "all_findings": ["issue A"]})
        self.assertEqual(action, "no_action_already_open")
        # Still exactly one row -- no duplicate/spam entries.
        text = mutations._read_ledger()
        self.assertEqual(text.count(sas.DESCRIPTION_MARKER), 1)

    def test_refreshes_description_when_findings_change_while_open(self):
        """A stale description (e.g. 4 ops flagged, 3 since fixed) must not
        keep reporting resolved issues as still-open -- real gap found live
        2026-08-28: touchContact/confirmProposal/processMacroSignal were
        fixed within hours of the loop opening, but only closeThread was
        still genuinely silent by the time anyone would read the loop."""
        sas.apply_loop_update({"clean": False, "all_findings": ["issue A", "issue B", "issue C"]})
        action = sas.apply_loop_update({"clean": False, "all_findings": ["issue C"]})
        self.assertEqual(action, "refreshed")
        text = mutations._read_ledger()
        self.assertIn("issue C", text)
        self.assertNotIn("issue A", text)
        self.assertNotIn("issue B", text)
        # Still exactly one row -- refreshing in place, not adding a new one.
        self.assertEqual(text.count(sas.DESCRIPTION_MARKER), 1)

    def test_refresh_preserves_target_date(self):
        """Refreshing the description must not reset the urgency clock."""
        sas.apply_loop_update({"clean": False, "all_findings": ["issue A"]})
        text_before = mutations._read_ledger()
        target_before = [l for l in text_before.splitlines() if sas.DESCRIPTION_MARKER in l][0].split("|")[5].strip()

        sas.apply_loop_update({"clean": False, "all_findings": ["issue A", "issue B"]})
        text_after = mutations._read_ledger()
        target_after = [l for l in text_after.splitlines() if sas.DESCRIPTION_MARKER in l][0].split("|")[5].strip()

        self.assertEqual(target_before, target_after)

    def test_dry_run_refresh_never_writes_ledger(self):
        sas.apply_loop_update({"clean": False, "all_findings": ["issue A"]})
        original = mutations._read_ledger()
        action = sas.apply_loop_update({"clean": False, "all_findings": ["issue A", "issue B"]}, dry_run=True)
        self.assertEqual(action, "refreshed")
        self.assertEqual(mutations._read_ledger(), original)

    def test_closes_loop_when_findings_clear(self):
        sas.apply_loop_update({"clean": False, "all_findings": ["issue A"]})
        self.assertTrue(sas._find_open_self_audit_loop_id())
        action = sas.apply_loop_update({"clean": True, "all_findings": []})
        self.assertEqual(action, "closed")
        self.assertFalse(sas._find_open_self_audit_loop_id())

    def test_no_op_when_clean_and_no_loop_exists(self):
        action = sas.apply_loop_update({"clean": True, "all_findings": []})
        self.assertEqual(action, "no_action_already_clean")

    def test_dry_run_never_writes_ledger(self):
        original = mutations._read_ledger()
        sas.apply_loop_update({"clean": False, "all_findings": ["issue A"]}, dry_run=True)
        self.assertEqual(mutations._read_ledger(), original)

    def test_reopens_after_a_clean_cycle_if_new_findings_appear(self):
        sas.apply_loop_update({"clean": False, "all_findings": ["issue A"]})
        sas.apply_loop_update({"clean": True, "all_findings": []})
        self.assertFalse(sas._find_open_self_audit_loop_id())
        action = sas.apply_loop_update({"clean": False, "all_findings": ["issue B (new)"]})
        self.assertEqual(action, "opened")
        self.assertTrue(sas._find_open_self_audit_loop_id())


class TestRecurrenceDetection(_IsolatedLedgerMixin, unittest.TestCase):
    """RB-2026-09-19: a real, live gap -- the identical closeThread/
    refreshSources finding was closed twice (2026-09-04, 2026-09-11)
    without ever being verified fixed, and reopened both times with no
    indication anywhere that this was a repeat. See module docstring on
    _prior_self_audit_recurrences() for full context."""

    def _close_open_loop(self, reason: str) -> None:
        # RB-2026-10-08: cmd_loop_close() now refuses to close a self-audit
        # loop on an ordinary (unverified-belief) reason -- these tests are
        # deliberately simulating a human closing one without automated
        # confirmation, which is exactly the scenario the override prefix
        # exists for.
        loop_id = sas._find_open_self_audit_loop_id()
        args = SimpleNamespace(id=loop_id, reason=f"{mutations.SELF_AUDIT_OVERRIDE_PREFIX} {reason}", dry_run=False)
        rc = mutations.cmd_loop_close(args)
        assert rc == 0, f"expected close to succeed with override prefix, got rc={rc}"

    def test_no_recurrence_annotation_on_a_genuinely_first_finding(self):
        action = sas.apply_loop_update({"clean": False, "all_findings": ["issue A"]})
        self.assertEqual(action, "opened")
        text = mutations._read_ledger()
        self.assertNotIn("RECURRING", text)

    def test_reopening_an_identical_finding_after_closure_is_flagged_recurring(self):
        sas.apply_loop_update({"clean": False, "all_findings": ["closeThread, refreshSources silent"]})
        self._close_open_loop("Todd believes this was addressed in development this week.")
        self.assertFalse(sas._find_open_self_audit_loop_id())

        action = sas.apply_loop_update({"clean": False, "all_findings": ["closeThread, refreshSources silent"]})
        self.assertEqual(action, "opened")
        text = mutations._read_ledger()
        self.assertIn("RECURRING x2", text)
        self.assertIn("closeThread, refreshSources silent", text)

    def test_recurrence_count_increments_across_multiple_closures(self):
        sas.apply_loop_update({"clean": False, "all_findings": ["same finding"]})
        self._close_open_loop("closed 1")
        sas.apply_loop_update({"clean": False, "all_findings": ["same finding"]})
        self._close_open_loop("closed 2")

        sas.apply_loop_update({"clean": False, "all_findings": ["same finding"]})
        text = mutations._read_ledger()
        self.assertIn("RECURRING x3", text)

    def test_a_different_finding_is_not_treated_as_a_recurrence(self):
        sas.apply_loop_update({"clean": False, "all_findings": ["issue A"]})
        self._close_open_loop("fixed for real")
        action = sas.apply_loop_update({"clean": False, "all_findings": ["completely unrelated issue B"]})
        self.assertEqual(action, "opened")
        text = mutations._read_ledger()
        self.assertNotIn("RECURRING", text)

    def test_recurrence_names_the_specific_prior_loop_ids(self):
        sas.apply_loop_update({"clean": False, "all_findings": ["same finding"]})
        first_id = sas._find_open_self_audit_loop_id()
        self._close_open_loop("closed 1")

        sas.apply_loop_update({"clean": False, "all_findings": ["same finding"]})
        text = mutations._read_ledger()
        self.assertIn(first_id, text)


class TestSelfAuditCloseGate(_IsolatedLedgerMixin, unittest.TestCase):
    """RB-2026-10-08: RBB_STRATEGIC_ASSESSMENT_2026-09-19.md Finding 1 --
    mutations.cmd_loop_close() must refuse to close a self-audit loop on an
    ordinary (belief-based) reason, and must allow it either via
    self_audit_sweep's own verified-clean reason string or an explicit
    human override prefix."""

    def _open_self_audit_loop(self) -> str:
        sas.apply_loop_update({"clean": False, "all_findings": ["issue A"]})
        return sas._find_open_self_audit_loop_id()

    def test_ordinary_reason_is_rejected_with_gate_return_code(self):
        loop_id = self._open_self_audit_loop()
        args = SimpleNamespace(id=loop_id, reason="I believe this was fixed.", dry_run=False)
        rc = mutations.cmd_loop_close(args)
        self.assertEqual(rc, 2)
        # Loop must still be open -- the close must not have been applied.
        self.assertEqual(sas._find_open_self_audit_loop_id(), loop_id)

    def test_verified_clean_reason_closes_it(self):
        loop_id = self._open_self_audit_loop()
        args = SimpleNamespace(id=loop_id, reason=mutations.SELF_AUDIT_VERIFIED_CLEAN_REASON, dry_run=False)
        rc = mutations.cmd_loop_close(args)
        self.assertEqual(rc, 0)
        self.assertIsNone(sas._find_open_self_audit_loop_id())

    def test_override_prefix_closes_it(self):
        loop_id = self._open_self_audit_loop()
        args = SimpleNamespace(
            id=loop_id, reason=f"{mutations.SELF_AUDIT_OVERRIDE_PREFIX} accepting the risk for now", dry_run=False,
        )
        rc = mutations.cmd_loop_close(args)
        self.assertEqual(rc, 0)
        self.assertIsNone(sas._find_open_self_audit_loop_id())

    def test_override_prefix_match_is_case_insensitive(self):
        loop_id = self._open_self_audit_loop()
        args = SimpleNamespace(id=loop_id, reason="unverified override: accepting the risk", dry_run=False)
        rc = mutations.cmd_loop_close(args)
        self.assertEqual(rc, 0)

    def test_ordinary_non_self_audit_loop_is_unaffected(self):
        args = SimpleNamespace(
            id=None, opened="2026-10-08", party="Test Contact",
            description="A normal loop, not self-audit.", target="2026-10-15", dry_run=False,
        )
        mutations.cmd_loop_add(args)
        loops = core.parse_loop_ledger(path=core.LOOP_LEDGER_PATH)
        loop_id = next(L.id for L in loops if not L.closed)
        close_args = SimpleNamespace(id=loop_id, reason="Handled on the call today.", dry_run=False)
        rc = mutations.cmd_loop_close(close_args)
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
