"""
test_render_reachability_check.py — RB defect 2026-10-08, self-healing check #1.

render_reachability_check.py exists because the exact same defect class --
a function computes real data but nothing reachable from the live code
path ever calls it -- was found by hand three separate times in one
session (pending_mutations, review-queue backlog, the Group B sections).
These tests exercise the checker itself against small synthetic fixture
files (never the real render_daily_brief.py, so this suite stays fast and
independent of that file's own ongoing changes) and confirm it would have
actually caught the pattern, not just that it runs without error.
"""
from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_reachability_check as rrc  # noqa: E402


class _IsolatedFixtureMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write(self, source: str) -> Path:
        path = self.tmp / "fixture.py"
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        return path


class TestBasicReachability(_IsolatedFixtureMixin, unittest.TestCase):
    def test_a_function_called_before_the_early_return_is_reachable(self):
        path = self._write("""
            def _render_a(sections):
                return "a"

            def _render_b(sections):
                return "b"

            def render(sections):
                out = []
                out.append(_render_a(sections))
                return "\\n".join(out)

                out.append(_render_b(sections))
        """)
        result = rrc.check_file(path, "render", "_render_")
        self.assertTrue(result["ok"])
        self.assertNotIn("_render_a", result["unreachable"])
        self.assertIn("_render_b", result["unreachable"])

    def test_this_is_the_exact_pattern_that_bit_us_live(self):
        """Reproduces the real shape: a correct, well-tested function that's
        simply never called from the live path because of an early return
        left over from a prior refactor -- exactly pending_mutations/
        review-queue-backlog/Group B."""
        path = self._write("""
            def _render_pending_confirmations(sections):
                items = sections.get("pending_mutations", [])
                if not items:
                    return ""
                return "## Pending Confirmations"

            def render(sections, dry_run=False):
                compact_parts = ["# Brief"]
                compact_parts.append(_render_decision_queue(sections))
                markdown = "\\n".join(compact_parts)
                return markdown

                # Old verbose path -- unreachable, left after the compact rewrite.
                pending_conf = _render_pending_confirmations(sections)
                if pending_conf:
                    compact_parts.append(pending_conf)

            def _render_decision_queue(sections):
                return "## Decision Queue"
        """)
        result = rrc.check_file(path, "render", "_render_")
        self.assertTrue(result["ok"])
        self.assertIn("_render_pending_confirmations", result["unreachable"])
        self.assertNotIn("_render_decision_queue", result["unreachable"])

    def test_multi_hop_reachability_through_a_helper(self):
        """A candidate called only by another function, which is itself
        called live, must still count as reachable -- not just direct
        render() call sites."""
        path = self._write("""
            def _render_leaf(sections):
                return "leaf"

            def _render_wrapper(sections):
                return _render_leaf(sections)

            def render(sections):
                out = _render_wrapper(sections)
                return out
        """)
        result = rrc.check_file(path, "render", "_render_")
        self.assertNotIn("_render_leaf", result["unreachable"])
        self.assertNotIn("_render_wrapper", result["unreachable"])

    def test_return_nested_inside_an_if_is_ordinary_control_flow_not_an_early_exit(self):
        """A `return` inside an `if` block is conditional control flow, not
        the kind of unconditional top-level early return that makes
        everything after it in the function body dead -- must not be
        mistaken for one."""
        path = self._write("""
            def _render_a(sections):
                return "a"

            def _render_b(sections):
                return "b"

            def render(sections, flag=False):
                if flag:
                    return "early"
                out = []
                out.append(_render_a(sections))
                out.append(_render_b(sections))
                return "\\n".join(out)
        """)
        result = rrc.check_file(path, "render", "_render_")
        self.assertEqual(result["unreachable"], [])

    def test_missing_entry_function_reports_a_clear_error_not_a_crash(self):
        path = self._write("""
            def _render_a(sections):
                return "a"
        """)
        result = rrc.check_file(path, "render", "_render_")
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"])

    def test_no_candidates_matching_prefix_is_clean(self):
        path = self._write("""
            def render(sections):
                return "ok"
        """)
        result = rrc.check_file(path, "render", "_render_")
        self.assertTrue(result["ok"])
        self.assertEqual(result["total_candidates"], 0)
        self.assertEqual(result["unreachable"], [])


class TestAllowlistFiltering(_IsolatedFixtureMixin, unittest.TestCase):
    def test_allowlisted_function_is_acknowledged_not_an_active_finding(self):
        path = self._write("""
            def _render_a(sections):
                return "a"

            def _render_b(sections):
                return "b"

            def render(sections):
                return _render_a(sections)
        """)
        result = rrc.check_file(path, "render", "_render_", allowlist={"_render_b": "deliberately excluded"})
        self.assertIn("_render_b", result["acknowledged_unreachable"])
        self.assertEqual(result["unacknowledged_unreachable"], [])

    def test_unlisted_unreachable_function_is_an_active_finding(self):
        path = self._write("""
            def _render_a(sections):
                return "a"

            def _render_b(sections):
                return "b"

            def render(sections):
                return _render_a(sections)
        """)
        result = rrc.check_file(path, "render", "_render_", allowlist={})
        self.assertEqual(result["unacknowledged_unreachable"], ["_render_b"])


class TestRunAllChecksFindingsFormat(_IsolatedFixtureMixin, unittest.TestCase):
    """Exercises run_all_checks()'s findings-string format (the shape
    self_audit_sweep.py's collect_findings() consumes) against a real
    allowlist file on disk, isolated via ALLOWLIST_PATH patching."""

    def setUp(self):
        super().setUp()
        self.allowlist_path = self.tmp / "allowlist.json"
        self._orig_allowlist_path = rrc.ALLOWLIST_PATH
        rrc.ALLOWLIST_PATH = self.allowlist_path

    def tearDown(self):
        rrc.ALLOWLIST_PATH = self._orig_allowlist_path
        super().tearDown()

    def test_clean_when_everything_reachable_or_allowlisted(self):
        path = self._write("""
            def _render_a(sections):
                return "a"

            def render(sections):
                return _render_a(sections)
        """)
        self.allowlist_path.write_text(json.dumps({"fixture.py::render": {}}), encoding="utf-8")
        report = rrc.run_all_checks(targets=[{"file": path, "entry": "render", "prefix": "_render_"}])
        self.assertTrue(report["clean"])
        self.assertEqual(report["findings"], [])

    def test_unacknowledged_unreachable_produces_a_finding_string(self):
        path = self._write("""
            def _render_a(sections):
                return "a"

            def _render_b(sections):
                return "b"

            def render(sections):
                return _render_a(sections)
        """)
        self.allowlist_path.write_text(json.dumps({"fixture.py::render": {}}), encoding="utf-8")
        report = rrc.run_all_checks(targets=[{"file": path, "entry": "render", "prefix": "_render_"}])
        self.assertFalse(report["clean"])
        self.assertEqual(len(report["findings"]), 1)
        self.assertIn("_render_b", report["findings"][0])

    def test_missing_allowlist_file_does_not_crash_treated_as_empty(self):
        path = self._write("""
            def _render_a(sections):
                return "a"

            def render(sections):
                return _render_a(sections)
        """)
        # allowlist_path deliberately not written -- simulates a fresh checkout.
        report = rrc.run_all_checks(targets=[{"file": path, "entry": "render", "prefix": "_render_"}])
        self.assertTrue(report["clean"])


class TestRealAllowlistFileIsWellFormed(unittest.TestCase):
    """Guards the real, committed allowlist (not a fixture) -- every entry
    must still name an actually-defined function, so the allowlist can't
    silently drift from the code it's meant to describe (e.g. a function
    renamed or deleted, leaving a stale, meaningless allowlist entry)."""

    def test_every_allowlisted_name_is_a_real_function_in_render_daily_brief(self):
        if not rrc.ALLOWLIST_PATH.exists():
            self.skipTest("no committed allowlist file")
        data = json.loads(rrc.ALLOWLIST_PATH.read_text(encoding="utf-8"))
        target = next((t for t in rrc.DEFAULT_TARGETS if t["file"].name == "render_daily_brief.py"), None)
        if target is None:
            self.skipTest("render_daily_brief.py not a configured target")
        key = f"{target['file'].name}::{target['entry']}"
        entries = {k: v for k, v in data.get(key, {}).items()}
        result = rrc.check_file(target["file"], target["entry"], target["prefix"])
        self.assertTrue(result["ok"])
        import ast
        tree = ast.parse(target["file"].read_text(encoding="utf-8"))
        all_function_names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for name in entries:
            self.assertIn(name, all_function_names, f"allowlist entry {name!r} is not a real function anymore")


if __name__ == "__main__":
    unittest.main()
