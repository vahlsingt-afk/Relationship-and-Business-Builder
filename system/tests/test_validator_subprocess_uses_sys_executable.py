"""
test_validator_subprocess_uses_sys_executable.py — RB-2026-08-28.

Real, high-impact incident: mutations.py's _validate_baseline_or_rollback()
and _validate_operators_or_rollback(), plus two call sites in
ecosystem_intelligence.py, spawned the schema validator via a bare
"python3" string. That resolves via PATH, not necessarily the same
interpreter/environment actually running the calling process. In
production (api-server launched with PYTHONNOUSERSITE=1 + a vendored
PYTHONPATH, specifically to keep prod isolated from ambient dev
packages), "python3" on PATH resolved to /usr/bin/python3 -- which
couldn't see the vendored jsonschema at all. Every subprocess call failed
with "jsonschema not installed", which _validate_*_or_rollback() couldn't
distinguish from a real schema failure -- so EVERY touchContact call
rolled back, silently, for as long as this had been broken. Confirmed
live: touching a real contact returned 500 "baseline validation failed...
rolled back" every time, with the real cause buried in api-server's error
log, not surfaced anywhere the model or Todd would see.

Fixed by using sys.executable (guaranteed to be the same interpreter this
process was actually launched with) instead of a bare "python3". This
test guards two things: (1) the specific fixed call sites still use
sys.executable, so a future edit can't silently reintroduce the bare
string, and (2) no OTHER bare ["python3", ...] subprocess call sites exist
anywhere in system/scripts or system/api -- the same mistake was found in
three different files, so a source-wide sweep is the only check that
actually closes this class of bug rather than one instance of it.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "system" / "scripts"
API_DIR = ROOT / "system" / "api"

# A literal "python3" (or "python") as the first element of a subprocess
# argv list -- the exact shape of the real bug. Deliberately narrow (first
# list element only) to avoid false positives on strings that merely
# mention "python3" in a comment, docstring, or error message.
_BARE_PYTHON3_ARGV_RE = re.compile(r'\[\s*["\']python3?["\']\s*,')


def _all_py_files() -> list[Path]:
    return sorted(SCRIPTS_DIR.glob("*.py")) + sorted(API_DIR.glob("*.py"))


class TestNoBarePython3SubprocessCalls(unittest.TestCase):
    def test_no_source_file_spawns_a_bare_python3_subprocess(self):
        offenders = []
        for f in _all_py_files():
            text = f.read_text(encoding="utf-8", errors="replace")
            for m in _BARE_PYTHON3_ARGV_RE.finditer(text):
                line_no = text[:m.start()].count("\n") + 1
                offenders.append(f"{f.relative_to(ROOT)}:{line_no}")
        self.assertEqual(
            offenders, [],
            f"Found bare ['python3', ...] subprocess call(s), which resolve via PATH "
            f"instead of the interpreter/environment actually running this process -- "
            f"the exact bug that made every touchContact call silently roll back in "
            f"production. Use sys.executable instead: {offenders}",
        )


class TestFixedCallSitesUseSysExecutable(unittest.TestCase):
    """Specific regression guard for the three real files this incident
    touched, in addition to the source-wide sweep above."""

    def test_mutations_py_baseline_and_operators_validators(self):
        text = (SCRIPTS_DIR / "mutations.py").read_text(encoding="utf-8")
        self.assertIn("[sys.executable, str(SCHEMA_VALIDATOR)]", text)

    def test_ecosystem_intelligence_py_validator_call_sites(self):
        """RB-2026-09-27: the call-site shape changed (both sites now pass
        an explicit target path + --schema instead of --ecosystem-only --
        see the matching comment in _write_graph() for why), so this checks
        the same two invariants the old exact-string match did: sys.executable
        is used (not a bare "python3"), and no call site still relies on
        --ecosystem-only's hardcoded default, which can't see a test's
        monkeypatched core.ECOSYSTEM_INTELLIGENCE_PATH."""
        text = (SCRIPTS_DIR / "ecosystem_intelligence.py").read_text(encoding="utf-8")
        self.assertEqual(
            text.count('[sys.executable, str(VALIDATOR), str(core.ECOSYSTEM_INTELLIGENCE_PATH), "--schema", str(SCHEMA_PATH)]'), 2,
            "Expected both ecosystem_intelligence.py validator call sites (the write "
            "path and the smoke-test CLI) to use sys.executable and pass an explicit "
            "target path + schema, rather than --ecosystem-only's hardcoded default.",
        )
        self.assertNotIn(
            '"--ecosystem-only"', text,
            "ecosystem_intelligence.py should no longer rely on --ecosystem-only's "
            "hardcoded default path in its own validator call sites; it can't see a "
            "test's monkeypatched core.ECOSYSTEM_INTELLIGENCE_PATH across the "
            "subprocess boundary.",
        )

    def test_validate_baseline_py_cli_entrypoint(self):
        text = (SCRIPTS_DIR / "validate_baseline.py").read_text(encoding="utf-8")
        self.assertIn("[sys.executable, str(SCHEMA_VALIDATOR)]", text)


if __name__ == "__main__":
    unittest.main()
