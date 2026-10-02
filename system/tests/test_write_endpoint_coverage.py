"""
test_write_endpoint_coverage.py — RB-DEFECT-061 follow-up

closeLoop, closeThread, and touchContact had zero test coverage of any kind
before this file — confirmed by grepping every existing test file. These are
3 of the 6 write operations newly given explicit GPT routing instructions in
RB-DEFECT-061 (the fix for the GPT fabricating a "Done, closed" receipt with
no backing API call). Since the GPT will now be calling these more often,
the underlying endpoints deserve the same safety net every other write
operation in this codebase already has.

Isolation follows the existing pattern in test_thread_opportunity_sync.py:
patch the relevant core.*_PATH constants to a tempdir and no-op
mutations.snapshot — no real file is ever read or written by this file.
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
sys.path.insert(0, str(ROOT / "system" / "api"))

from datetime import date  # noqa: E402

import rb_core as core  # noqa: E402
import mutations  # noqa: E402
import eolms  # noqa: E402


def _make_eloop(**overrides) -> core.ELoop:
    today = date.today()
    defaults = dict(
        id="EL-TEST-001", title="Test EOLMS loop", category="action", status="active",
        priority="medium", created_at=today, updated_at=today, last_activity=today,
        confidence="high",
    )
    defaults.update(overrides)
    return core.ELoop(**defaults)

SAMPLE_LEDGER = """# Loop Ledger

| ID | Opened | Person/Company | Loop | Closure target | Status |
|---|---|---|---|---|---|
| L-2026-01-01-001 | 2026-01-01 | Test Contact | Test loop description. | 2026-01-15 | open |

## Closed / abandoned
"""

SAMPLE_THREADS = {
    "version": 1,
    "threads": [
        {
            "id": "T-2026-01-test-thread",
            "title": "Test thread",
            "opened": "2026-01-01",
            "status": "open",
            "type": "networking",
            "people": [],
            "companies": [],
        }
    ],
}

SAMPLE_BASELINE = [
    {"id": "test-contact", "name": "Test Contact", "last_touch": "2026-01-01", "sources": []},
]

# Matches conftest.py's RB_API_KEY test-session default. Without this header,
# every request here 401s (or, before RB_API_KEY was defaulted for tests,
# 503s) before ever reaching the route handlers this file exists to cover.
_AUTH_HEADERS = {"x-api-key": "test-key"}


class TestCloseLoopEndpoint(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.tmpdir.name) / "loop_ledger.md"
        self.ledger_path.write_text(SAMPLE_LEDGER)

        import server  # noqa: E402
        self.server = server
        self._patches = [
            patch.object(core, "LOOP_LEDGER_PATH", self.ledger_path),
            patch.object(mutations, "snapshot", lambda path, tag: path),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmpdir.cleanup()

    def test_close_existing_loop_persists(self):
        from fastapi.testclient import TestClient
        client = TestClient(self.server.app)
        resp = client.post("/loops/close", json={"id": "L-2026-01-01-001", "reason": "test closure"}, headers=_AUTH_HEADERS)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "id": "L-2026-01-01-001"})

        # Verify it actually persisted, not just that the HTTP call returned 200.
        reloaded = self.ledger_path.read_text()
        self.assertIn("**closed** — test closure", reloaded)

    def test_close_nonexistent_loop_returns_400(self):
        from fastapi.testclient import TestClient
        client = TestClient(self.server.app)
        resp = client.post("/loops/close", json={"id": "L-9999-99-99-999", "reason": "nope"}, headers=_AUTH_HEADERS)
        self.assertEqual(resp.status_code, 400)
        # Ledger must be untouched on failure.
        self.assertEqual(self.ledger_path.read_text(), SAMPLE_LEDGER)

    def test_close_already_closed_loop_returns_400(self):
        from fastapi.testclient import TestClient
        client = TestClient(self.server.app)
        first = client.post("/loops/close", json={"id": "L-2026-01-01-001", "reason": "first close"}, headers=_AUTH_HEADERS)
        self.assertEqual(first.status_code, 200)
        second = client.post("/loops/close", json={"id": "L-2026-01-01-001", "reason": "second close"}, headers=_AUTH_HEADERS)
        self.assertEqual(second.status_code, 400)


class TestCloseLoopRoutesElIdsToEolms(unittest.TestCase):
    """RB-2026-08-24: closeLoop only ever wrote to loop_ledger.md, so an EL-
    id (an EOLMS-only loop, never migrated into the ledger) always failed
    with "id not found" even though it's a perfectly real, open loop. Caught
    live when RBB correctly gave an EL- id for exactly this case. /loops/close
    must route by id prefix to whichever store actually owns that id."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.loops_path = Path(self.tmpdir.name) / "loops.json"
        self.archive_dir = Path(self.tmpdir.name) / "archive"

        import server  # noqa: E402
        self.server = server
        self._patches = [
            patch.object(core, "EOLMS_PATH", self.loops_path),
            patch.object(core, "EOLMS_ARCHIVE_DIR", self.archive_dir),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmpdir.cleanup()

    def test_close_by_el_id_persists_to_eolms_not_ledger(self):
        from fastapi.testclient import TestClient
        loop = _make_eloop(id="EL-2026-05-08-007", title="Toast career watch")
        self.loops_path.write_text(json.dumps([loop.to_dict()], indent=2))

        client = TestClient(self.server.app)
        resp = client.post("/loops/close", json={
            "id": "EL-2026-05-08-007", "reason": "Not in an active career search.",
        }, headers=_AUTH_HEADERS)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "id": "EL-2026-05-08-007"})

        reloaded = core.load_eloops(self.loops_path)
        self.assertEqual(reloaded[0].status, "completed")

    def test_close_by_unknown_el_id_returns_400(self):
        from fastapi.testclient import TestClient
        self.loops_path.write_text(json.dumps([], indent=2))
        client = TestClient(self.server.app)
        resp = client.post("/loops/close", json={"id": "EL-9999-99-99-999", "reason": "nope"}, headers=_AUTH_HEADERS)
        self.assertEqual(resp.status_code, 400)


class TestAddLoopEndpoint(unittest.TestCase):
    """RB-2026-08-24: mutations.cmd_loop_add() calls date.fromisoformat(target)
    with no validation -- a non-ISO target (e.g. an RBB assertion doc that
    filled in "No target date specified" because no date was given) crashed
    the endpoint with an unhandled 500 instead of a clean 400. Caught live
    when the Drive assertion bridge's first real end-to-end test hit exactly
    this case."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.tmpdir.name) / "loop_ledger.md"
        self.ledger_path.write_text(SAMPLE_LEDGER)

        import server  # noqa: E402
        self.server = server
        self._patches = [
            patch.object(core, "LOOP_LEDGER_PATH", self.ledger_path),
            patch.object(mutations, "snapshot", lambda path, tag: path),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmpdir.cleanup()

    def test_add_loop_with_valid_target_persists(self):
        from fastapi.testclient import TestClient
        client = TestClient(self.server.app)
        resp = client.post("/loops", json={
            "party": "Test Contact", "description": "New loop", "target": "2026-09-01",
        }, headers=_AUTH_HEADERS)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("New loop", self.ledger_path.read_text())

    def test_add_loop_with_non_iso_target_returns_400_not_500(self):
        from fastapi.testclient import TestClient
        client = TestClient(self.server.app)
        resp = client.post("/loops", json={
            "party": "Test Contact", "description": "New loop",
            "target": "No target date specified",
        }, headers=_AUTH_HEADERS)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("target", resp.json()["detail"])
        # Ledger must be untouched on a rejected request.
        self.assertEqual(self.ledger_path.read_text(), SAMPLE_LEDGER)

    def test_add_loop_with_non_iso_opened_returns_400_not_500(self):
        from fastapi.testclient import TestClient
        client = TestClient(self.server.app)
        resp = client.post("/loops", json={
            "party": "Test Contact", "description": "New loop",
            "target": "2026-09-01", "opened": "not a date",
        }, headers=_AUTH_HEADERS)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("opened", resp.json()["detail"])
        self.assertEqual(self.ledger_path.read_text(), SAMPLE_LEDGER)


class TestCloseThreadEndpoint(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.threads_path = Path(self.tmpdir.name) / "active_threads.yaml"
        import yaml
        self.threads_path.write_text(yaml.safe_dump(SAMPLE_THREADS, sort_keys=False))

        import server  # noqa: E402
        self.server = server
        self._patches = [
            patch.object(core, "ACTIVE_THREADS_PATH", self.threads_path),
            patch.object(mutations, "snapshot", lambda path, tag: path),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmpdir.cleanup()

    def test_close_existing_thread_persists(self):
        from fastapi.testclient import TestClient
        client = TestClient(self.server.app)
        resp = client.post("/threads/close", json={"id": "T-2026-01-test-thread", "reason": "test closure"}, headers=_AUTH_HEADERS)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "id": "T-2026-01-test-thread"})

        import yaml
        reloaded = yaml.safe_load(self.threads_path.read_text())
        thread = next(t for t in reloaded["threads"] if t["id"] == "T-2026-01-test-thread")
        self.assertEqual(thread["status"], "closed")
        self.assertEqual(thread["close_reason"], "test closure")

    def test_close_nonexistent_thread_returns_400(self):
        from fastapi.testclient import TestClient
        client = TestClient(self.server.app)
        resp = client.post("/threads/close", json={"id": "T-does-not-exist"}, headers=_AUTH_HEADERS)
        self.assertEqual(resp.status_code, 400)


class TestTouchContactEndpoint(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.baseline_path = Path(self.tmpdir.name) / "baseline_index.json"
        self.baseline_path.write_text(json.dumps(SAMPLE_BASELINE, indent=2))
        self.cards_dir = Path(self.tmpdir.name) / "cards"
        self.cards_dir.mkdir()

        import server  # noqa: E402
        self.server = server
        self._patches = [
            patch.object(core, "BASELINE_PATH", self.baseline_path),
            patch.object(core, "CARDS_DIR", self.cards_dir),
            # load_baseline()'s `path` default is bound at function-definition
            # time, so patching core.BASELINE_PATH alone wouldn't redirect a
            # no-argument call — force the read through the same tempfile.
            patch.object(core, "load_baseline", lambda path=None: json.loads(self.baseline_path.read_text())),
            patch.object(mutations, "snapshot", lambda path, tag: path),
            # _validate_baseline_or_rollback shells out to a subprocess that
            # validates the *real* baseline_index.json regardless of any
            # in-process patch — skip it here rather than depend on/slow down
            # this test with an out-of-process validation of unrelated data.
            patch.object(mutations, "_validate_baseline_or_rollback", lambda snapshot_path: 0),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmpdir.cleanup()

    def test_touch_existing_contact_persists(self):
        from fastapi.testclient import TestClient
        client = TestClient(self.server.app)
        resp = client.post("/touch", json={"id": "test-contact", "date": "2026-07-03", "source": "test"}, headers=_AUTH_HEADERS)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["last_touch"], "2026-07-03")
        self.assertTrue(body["baseline_updated"])

        reloaded = json.loads(self.baseline_path.read_text())
        self.assertEqual(reloaded[0]["last_touch"], "2026-07-03")
        self.assertIn("test", reloaded[0]["sources"])

    def test_touch_nonexistent_contact_returns_error(self):
        from fastapi.testclient import TestClient
        client = TestClient(self.server.app)
        resp = client.post("/touch", json={"id": "no-such-contact"}, headers=_AUTH_HEADERS)
        self.assertGreaterEqual(resp.status_code, 400)
        # Baseline must be untouched on failure.
        reloaded = json.loads(self.baseline_path.read_text())
        self.assertEqual(reloaded, SAMPLE_BASELINE)


if __name__ == "__main__":
    unittest.main()
