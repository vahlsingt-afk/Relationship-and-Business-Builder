"""
test_conftest_api_key_isolation.py — RB-DEFECT (2026-09-16).

Live incident: the first unattended overnight run after self_audit_sweep.py
was wired to run the full test suite (2026-09-15) reported ~213 failures --
a suite that had just been confirmed clean (4860 passed, 1 pre-existing
failure) hours earlier. Root cause: conftest.py set RB_API_KEY via
os.environ.setdefault("RB_API_KEY", "test-key") -- fine for every other
test-isolation env var in that file (test-only names, never set outside a
test run), but wrong for this one. RB_API_KEY is also a REAL secret that
IS legitimately present in morning_pipeline.py's own environment (loaded by
run_with_secrets.py); self_audit_sweep.py runs this exact suite as a
subprocess of that pipeline, inheriting the real key. setdefault() silently
kept it, so every test using the hardcoded `headers={"x-api-key":
"test-key"}` (dozens of call sites) stopped authenticating and failed with
401 -- a suite-wide false alarm with no real code regression behind it.

This proves the fix end-to-end, not just in isolation: spawn a REAL nested
pytest subprocess with a real-looking RB_API_KEY already set in its
environment (reproducing the exact inherited-environment shape
self_audit_sweep.py's subprocess sees) and confirm the affected tests still
pass. A single fast, small API test file stands in for the ~213 that broke
live -- they all fail via the identical mechanism (server.API_KEY fixed at
import time from a real key, hardcoded "test-key" header no longer
matches), so one file exercising that exact code path is sufficient
evidence without re-running the whole suite as a nested subprocess.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "system" / "tests" / "test_api_ecosystem_graph.py"


def test_api_tests_pass_even_when_a_real_api_key_is_already_in_the_environment():
    # Inherit the real environment (as self_audit_sweep.py's subprocess.run
    # does from morning_pipeline.py) and override just RB_API_KEY -- the
    # exact shape of the live incident, not a stripped-down environment
    # that could fail for unrelated reasons.
    env = dict(os.environ)
    env["RB_API_KEY"] = "some-real-production-key-abc123"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(TARGET), "-q"],
        capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=60,
    )
    assert result.returncode == 0, (
        "conftest.py must force RB_API_KEY to 'test-key' for the test "
        "session regardless of what the launching process already set -- "
        f"got:\n{result.stdout}\n{result.stderr}"
    )
