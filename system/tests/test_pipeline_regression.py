"""
test_pipeline_regression.py — RB 9.24 regression suite

Prevents two classes of morning-pipeline breakage discovered on 2026-05-29:

  PR1 — CLI contract drift: refresh_all.py calls scripts with flags (e.g.
         --cache) that the scripts don't support, causing non-zero exit codes
         that fail the pipeline silently.

  PR2 — Unhashable source_refs: relationship_signals.py emits evidence as
         list[dict]; passive_ri_ingest.py must normalise to list[str] before
         calling dict.fromkeys() deduplication.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_module(script_name: str):
    """Import a scripts/ module by filename without executing __main__ guards."""
    path = SCRIPTS_DIR / script_name
    spec = importlib.util.spec_from_file_location(script_name.replace(".py", ""), path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _help_text(script_name: str) -> str:
    """Return the --help output for a script (exits 0 for argparse scripts)."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script_name), "--help"],
        capture_output=True,
        text=True,
    )
    return result.stdout + result.stderr


# ---------------------------------------------------------------------------
# PR1 — CLI contract tests
# ---------------------------------------------------------------------------

# Every (script, flag) pair that refresh_all.py passes at runtime.
# If a script gains or loses a flag in refresh_all.py, add/remove it here so
# the contract stays explicit.
_CACHE_SCRIPTS = [
    "validate_baseline.py",
    "legacy_transfer_watch.py",
    "source_watch.py",
    "relationship_signals.py",
    "passive_email_intelligence.py",
    "strategic_operators.py",
    "passive_ri_ingest.py",
    "daily_brief.py",
    "action_drafts.py",
    "gap_detection.py",
    "loop_parser.py",
    "network_gap.py",
    "drr_score.py",
]

_JSON_SCRIPTS = [
    "validate_baseline.py",
    "legacy_transfer_watch.py",
    "source_watch.py",
    "relationship_signals.py",
    "passive_email_intelligence.py",
    "strategic_operators.py",
    "gap_detection.py",
    "loop_parser.py",
    "network_gap.py",
    "drr_score.py",
]


class TestRefreshAllCLIContracts(unittest.TestCase):
    """PR1 — every flag refresh_all passes must be recognised by the target script."""

    def _assert_flag(self, script: str, flag: str) -> None:
        help_out = _help_text(script)
        self.assertIn(
            flag,
            help_out,
            msg=(
                f"{script} does not advertise '{flag}' in --help output.\n"
                f"refresh_all.py calls this script with '{flag}' — add the "
                f"argument to {script}'s argparse block to prevent pipeline failures."
            ),
        )

    # --cache contract -------------------------------------------------------

    def test_PR1a_validate_baseline_accepts_cache(self):
        self._assert_flag("validate_baseline.py", "--cache")

    def test_PR1b_legacy_transfer_watch_accepts_cache(self):
        self._assert_flag("legacy_transfer_watch.py", "--cache")

    def test_PR1c_source_watch_accepts_cache(self):
        self._assert_flag("source_watch.py", "--cache")

    def test_PR1d_relationship_signals_accepts_cache(self):
        self._assert_flag("relationship_signals.py", "--cache")

    def test_PR1e_passive_email_intelligence_accepts_cache(self):
        self._assert_flag("passive_email_intelligence.py", "--cache")

    def test_PR1f_strategic_operators_accepts_cache(self):
        self._assert_flag("strategic_operators.py", "--cache")

    def test_PR1g_passive_ri_ingest_accepts_cache(self):
        self._assert_flag("passive_ri_ingest.py", "--cache")

    def test_PR1h_daily_brief_accepts_cache(self):
        self._assert_flag("daily_brief.py", "--cache")

    def test_PR1i_action_drafts_accepts_cache(self):
        """action_drafts.py was the specific script that broke 2026-05-29 pipeline."""
        self._assert_flag("action_drafts.py", "--cache")

    def test_PR1j_gap_detection_accepts_cache(self):
        self._assert_flag("gap_detection.py", "--cache")

    def test_PR1k_loop_parser_accepts_cache(self):
        self._assert_flag("loop_parser.py", "--cache")

    def test_PR1l_network_gap_accepts_cache(self):
        self._assert_flag("network_gap.py", "--cache")

    def test_PR1m_drr_score_accepts_cache(self):
        self._assert_flag("drr_score.py", "--cache")

    # --json contract --------------------------------------------------------

    def test_PR1n_validate_baseline_accepts_json(self):
        self._assert_flag("validate_baseline.py", "--json")

    def test_PR1o_relationship_signals_accepts_json(self):
        self._assert_flag("relationship_signals.py", "--json")

    def test_PR1p_gap_detection_accepts_json(self):
        self._assert_flag("gap_detection.py", "--json")

    def test_PR1q_action_drafts_does_not_receive_json(self):
        """action_drafts is called with --cache only (no --json); confirm --help exits 0."""
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "action_drafts.py"), "--help"],
            capture_output=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg="action_drafts.py --help should exit 0 (argparse contract).",
        )

    # refresh_all itself -----------------------------------------------------

    def test_PR1r_refresh_all_help_exits_zero(self):
        """refresh_all.py --help must exit 0 so pipeline health-checks work."""
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "refresh_all.py"), "--help"],
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0)

    def test_PR1s_refresh_all_script_list_matches_expectation(self):
        """Guard against scripts being added to refresh_all without updating this test."""
        import ast

        source = (SCRIPTS_DIR / "refresh_all.py").read_text()
        tree = ast.parse(source)

        # Collect all string literals ending in .py referenced as list elements
        # under the commands list literal (heuristic: look for all str constants
        # that name .py files appearing in the file).
        py_refs: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.s, str):
                if node.s.endswith(".py") and "/" not in node.s:
                    py_refs.add(node.s)

        # Every script we test must actually appear in refresh_all.py source.
        for script in _CACHE_SCRIPTS:
            self.assertIn(
                script,
                py_refs,
                msg=(
                    f"{script} is in _CACHE_SCRIPTS but not found in "
                    f"refresh_all.py — remove it from the test list."
                ),
            )


# ---------------------------------------------------------------------------
# PR2 — passive_ri_ingest source_refs normalisation
# ---------------------------------------------------------------------------

class TestNormalizeSourceRefs(unittest.TestCase):
    """PR2 — _normalize_source_refs must handle list[dict] from relationship_signals."""

    @classmethod
    def setUpClass(cls):
        # Load passive_ri_ingest without triggering __main__ side effects.
        sys.path.insert(0, str(SCRIPTS_DIR))
        import passive_ri_ingest as _mod
        cls._fn = staticmethod(_mod._normalize_source_refs)

    # Basic type handling ----------------------------------------------------

    def test_PR2a_empty_list_returns_empty(self):
        self.assertEqual(self._fn([]), [])

    def test_PR2b_none_returns_empty(self):
        self.assertEqual(self._fn(None), [])

    def test_PR2c_list_of_strings_unchanged(self):
        refs = ["gmail:thread_abc", "linkedin:post_xyz"]
        self.assertEqual(self._fn(refs), refs)

    def test_PR2d_dict_with_source_and_snippet(self):
        """Standard relationship_signals.py evidence dict format."""
        ev = {"source": "gmail", "thread_id": "abc123", "snippet": "Let's connect"}
        result = self._fn([ev])
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], str)
        self.assertIn("gmail", result[0])
        self.assertIn("Let's connect", result[0])

    def test_PR2e_dict_with_source_and_subject_no_snippet(self):
        ev = {"source": "gmail", "subject": "Re: Intro", "thread_id": "t99"}
        result = self._fn([ev])
        self.assertEqual(len(result), 1)
        self.assertIn("gmail", result[0])
        self.assertIn("Re: Intro", result[0])

    def test_PR2f_dict_with_source_only(self):
        ev = {"source": "linkedin"}
        result = self._fn([ev])
        self.assertEqual(result, ["linkedin"])

    def test_PR2g_dict_with_no_source(self):
        ev = {"thread_id": "orphan", "snippet": "orphaned snippet"}
        result = self._fn([ev])
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], str)

    def test_PR2h_mixed_str_and_dict(self):
        """Realistic mixed list from a partially-migrated pipeline run."""
        evidence = [
            "gmail:old_ref",
            {"source": "linkedin", "snippet": "New connection request"},
            "direct:manual_entry",
        ]
        result = self._fn(evidence)
        self.assertEqual(len(result), 3)
        self.assertTrue(all(isinstance(r, str) for r in result))
        self.assertEqual(result[0], "gmail:old_ref")
        self.assertEqual(result[2], "direct:manual_entry")

    def test_PR2i_snippet_truncated_at_60_chars(self):
        long_snippet = "x" * 100
        ev = {"source": "gmail", "snippet": long_snippet}
        result = self._fn([ev])
        # The extra part after "gmail:" should be at most 60 chars
        after_colon = result[0].split(":", 1)[1]
        self.assertLessEqual(len(after_colon), 60)

    # Hashability (the root cause of the 2026-05-29 failure) ----------------

    def test_PR2j_output_is_hashable(self):
        """Every returned string must survive dict.fromkeys() deduplication."""
        evidence = [
            {"source": "gmail", "snippet": "Hello"},
            {"source": "linkedin", "subject": "Re: Meeting"},
            "direct:string_ref",
        ]
        refs = self._fn(evidence)
        try:
            deduped = list(dict.fromkeys(refs))
        except TypeError as exc:
            self.fail(f"dict.fromkeys() failed on _normalize_source_refs output: {exc}")
        self.assertIsInstance(deduped, list)

    def test_PR2k_duplicate_dicts_deduplicated(self):
        """Two identical dict evidence entries should collapse to one after normalise+dedupe."""
        ev = {"source": "gmail", "snippet": "same signal"}
        refs = self._fn([ev, ev])
        deduped = list(dict.fromkeys(refs))
        self.assertEqual(len(deduped), 1)

    def test_PR2l_non_str_non_dict_falls_back_to_str(self):
        """Unexpected types (int, list) should not raise — convert via str()."""
        result = self._fn([42, ["nested", "list"]])
        self.assertEqual(len(result), 2)
        self.assertTrue(all(isinstance(r, str) for r in result))

    # Integration: call path from passive_ri_ingest --------------------------

    def test_PR2m_all_three_call_sites_use_normalize(self):
        """Verify _normalize_source_refs is used at the three sites fixed in RB 9.24."""
        source = (SCRIPTS_DIR / "passive_ri_ingest.py").read_text()

        # There should be at least 3 occurrences of the helper being called.
        count = source.count("_normalize_source_refs(")
        self.assertGreaterEqual(
            count,
            3,
            msg=(
                f"Expected _normalize_source_refs to be called at ≥3 sites "
                f"(lines ~412, ~505, ~846) but found {count} call(s). "
                f"A call site may have been removed, re-introducing the dict-hashability bug."
            ),
        )


if __name__ == "__main__":
    unittest.main()
