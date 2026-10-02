"""
test_identity_match_api.py — GET /identity/candidates, POST /identity/{id}/confirm|reject

Covers the API surface a Custom GPT calls when the operator answers an
identity-confirmation question in chat ("yes, David Drinan is the Blackthorn
guy"). See identity_match_review.py for the underlying scan/confirm/reject
logic and its own dedicated tests.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "system" / "api"))

try:
    from fastapi.testclient import TestClient
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

import rb_core as core  # noqa: E402
import identity_match_review as imr  # noqa: E402


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


_CANDIDATE_ID = "david-drinan::david@blackthornstrategicadvisors.com"


def _seed_candidate() -> None:
    imr._save_store({"candidates": {
        _CANDIDATE_ID: {
            "id": _CANDIDATE_ID,
            "baseline_id": "david-drinan",
            "baseline_name": "David Drinan",
            "baseline_company": "Blackthorn Strategic Advisors",
            "sender_name": "David Drinan",
            "sender_email": "david@blackthornstrategicadvisors.com",
            "account": "bridgepoint",
            "thread_id": "t1",
            "thread_subject": "Introduction",
            "last_message_at": "Tue, 30 Jun 2026 17:34:22 -0400",
            "status": "proposed_pending_confirmation",
            "first_seen": "2026-07-01",
        },
    }})


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestIdentityMatchEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server  # noqa: PLC0415
        cls.server = server
        cls.client = TestClient(server.app, headers={"x-api-key": "test-key"})

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="rb_identity_api_"))
        system_dir = self._tmp / "system"
        (system_dir / "inbox").mkdir(parents=True)

        self._orig_baseline_path = core.BASELINE_PATH
        self._orig_cache_path = imr.CACHE_PATH
        core.BASELINE_PATH = system_dir / "baseline_index.json"
        imr.CACHE_PATH = system_dir / ".cache" / "identity_match_candidates.json"

        _write_json(core.BASELINE_PATH, [
            {"id": "david-drinan", "name": "David Drinan",
             "current_company": "Blackthorn Strategic Advisors",
             "email": None, "notes": ""},
        ])
        _seed_candidate()

    def tearDown(self):
        core.BASELINE_PATH = self._orig_baseline_path
        imr.CACHE_PATH = self._orig_cache_path

    def test_list_pending_candidates(self):
        resp = self.client.get("/identity/candidates")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["pending"][0]["baseline_id"], "david-drinan")

    def test_confirm_writes_email_onto_baseline_and_resolves_candidate(self):
        resp = self.client.post(f"/identity/{_CANDIDATE_ID}/confirm")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["confirmed"])

        baseline = json.loads(core.BASELINE_PATH.read_text(encoding="utf-8"))
        drinan = baseline[0]
        self.assertEqual(drinan["email"], "david@blackthornstrategicadvisors.com")
        self.assertIn("Email identity confirmed", drinan["notes"])

        self.assertEqual(self.client.get("/identity/candidates").json()["count"], 0)

    def test_confirm_unknown_id_returns_404(self):
        resp = self.client.post("/identity/does-not-exist/confirm")
        self.assertEqual(resp.status_code, 404)

    def test_confirm_already_resolved_returns_404(self):
        first = self.client.post(f"/identity/{_CANDIDATE_ID}/confirm")
        self.assertEqual(first.status_code, 200)
        second = self.client.post(f"/identity/{_CANDIDATE_ID}/confirm")
        self.assertEqual(second.status_code, 404)

    def test_reject_does_not_touch_baseline(self):
        resp = self.client.post(f"/identity/{_CANDIDATE_ID}/reject")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["rejected"])

        baseline = json.loads(core.BASELINE_PATH.read_text(encoding="utf-8"))
        self.assertIsNone(baseline[0]["email"])

        self.assertEqual(self.client.get("/identity/candidates").json()["count"], 0)


if __name__ == "__main__":
    unittest.main()
