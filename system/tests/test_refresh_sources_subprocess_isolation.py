"""
test_refresh_sources_subprocess_isolation.py — RB defect 2026-10-08.

self_audit_sweep.py has flagged refreshSources (/sources/refresh) SILENT
since instrumentation began 2026-08-25 -- zero confirmed mutations despite
real traffic. Root-caused: its only two real calls (2026-09-14T12:22/12:24)
were both killed with exit code -15 (SIGTERM) within ~2 minutes, well
inside the 600s subprocess timeout -- the child inherited the API server's
process group, so a signal sent to that group (e.g. the server itself
being restarted, as happens on every deploy) killed the still-running
pipeline subprocess too, even though neither the request nor the
subprocess ever failed or timed out on its own terms.

Fix: every subprocess.run() call inside post_sources_refresh() now passes
start_new_session=True, giving the child its own process group so a
server-process signal no longer propagates to it. This test asserts that
kwarg is actually passed on all three call sites (full_pipeline morning_
pipeline.py, Google fetch, and refresh_sources.py), without ever spawning
a real subprocess.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "system" / "api"))

_AUTH_HEADERS = {"x-api-key": "test-key"}


def _completed(returncode: int = 0, stdout: str = "{}", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class TestRefreshSourcesSubprocessIsolation(unittest.TestCase):
    def setUp(self):
        import server  # noqa: E402
        self.server = server

    def test_full_pipeline_call_uses_start_new_session(self):
        from fastapi.testclient import TestClient
        client = TestClient(self.server.app)
        with patch.object(self.server.subprocess, "run", return_value=_completed(0, "{}")) as mock_run:
            resp = client.post(
                "/sources/refresh",
                json={"sources": ["email"], "confirm": True, "full_pipeline": True},
                headers=_AUTH_HEADERS,
            )
        self.assertEqual(resp.status_code, 200)
        mock_run.assert_called_once()
        self.assertTrue(mock_run.call_args.kwargs.get("start_new_session"))

    def test_non_full_pipeline_call_uses_start_new_session(self):
        # "calendar" also triggers the Google-fetch call (needs_google), so
        # this exercises both of this branch's subprocess.run() call sites.
        from fastapi.testclient import TestClient
        client = TestClient(self.server.app)
        with patch.object(self.server.subprocess, "run", return_value=_completed(0)) as mock_run, \
             patch.object(self.server.daily_brief, "build_report", return_value={}):
            resp = client.post(
                "/sources/refresh",
                json={"sources": ["calendar"], "confirm": True, "full_pipeline": False},
                headers=_AUTH_HEADERS,
            )
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(mock_run.call_count, 1)
        for call in mock_run.call_args_list:
            self.assertTrue(call.kwargs.get("start_new_session"))

    def test_google_fetch_call_uses_start_new_session(self):
        from fastapi.testclient import TestClient
        client = TestClient(self.server.app)
        calls = []

        def _fake_run(*args, **kwargs):
            calls.append(kwargs)
            return _completed(0)

        with patch.object(self.server.subprocess, "run", side_effect=_fake_run):
            resp = client.post(
                "/sources/refresh",
                json={"sources": ["email", "calendar"], "confirm": True, "full_pipeline": False},
                headers=_AUTH_HEADERS,
            )
        self.assertEqual(resp.status_code, 200)
        # Google fetch + refresh_sources.py -- both calls must be isolated.
        self.assertEqual(len(calls), 2)
        for kwargs in calls:
            self.assertTrue(kwargs.get("start_new_session"))

    def test_a_server_restart_signal_no_longer_propagates_to_the_child_pgid(self):
        """Confirms start_new_session actually changes the child's process
        group -- the real mechanism that was missing, not just a kwarg
        asserted in isolation from what it does."""
        import subprocess as real_subprocess
        proc = real_subprocess.Popen(
            [sys.executable, "-c", "import os; print(os.getpgid(0))"],
            stdout=real_subprocess.PIPE, text=True, start_new_session=True,
        )
        out, _ = proc.communicate(timeout=5)
        child_pgid = int(out.strip())
        import os
        self.assertNotEqual(child_pgid, os.getpgid(0), "child must be in its own process group")


if __name__ == "__main__":
    unittest.main()
