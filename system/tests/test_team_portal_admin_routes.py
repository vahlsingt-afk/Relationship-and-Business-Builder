"""
test_team_portal_admin_routes.py

FastAPI TestClient coverage for the /api/admin/* routes (require_owner
gating, member lifecycle through the API, one-time token reveal) and the
_UsageLogger middleware (one JSONL line per request, correct member_id
attribution including for blocked/suspended/revoked attempts).
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "system" / "api"))

import yaml  # noqa: E402

try:
    from fastapi.testclient import TestClient
    import team_portal_api
    import team_portal_admin as tpa  # noqa: E402
    import team_portal_usage_log as tpul  # noqa: E402
    _FASTAPI_OK = True
except ImportError:  # pragma: no cover
    _FASTAPI_OK = False


@unittest.skipUnless(_FASTAPI_OK, "fastapi not installed")
class TestAdminRoutes(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        tmp = Path(self.tmpdir.name)

        self.manifest_path = tmp / "manifest.yaml"
        self.creds_path = tmp / "creds.json"
        self.usage_log_dir = tmp / "usage_log"

        self.owner_token = "test-token-owner"
        self.member_token = "test-token-member"
        self.creds_path.write_text(json.dumps({
            "todd": {"token_hash": hashlib.sha256(self.owner_token.encode()).hexdigest(),
                     "created_at": "2026-09-29T00:00:00+00:00", "revoked_at": None, "suspended_at": None},
            "jsmith": {"token_hash": hashlib.sha256(self.member_token.encode()).hexdigest(),
                       "created_at": "2026-09-29T00:00:00+00:00", "revoked_at": None, "suspended_at": None},
        }))
        self.manifest_path.write_text(yaml.safe_dump({
            "members": [
                {"id": "todd", "name": "Todd Vahlsing", "email": "todd@example.com", "role": "team_member",
                 "added_at": "2026-09-29T00:00:00+00:00", "revoked_at": None, "suspended_at": None,
                 "is_owner": True, "plan": "internal"},
                {"id": "jsmith", "name": "Jane Smith", "email": "jane@example.com", "role": "team_member",
                 "added_at": "2026-09-29T00:00:00+00:00", "revoked_at": None, "suspended_at": None,
                 "plan": "internal"},
            ],
        }))

        # team_portal_api.py and team_portal_admin.py each hold their own
        # module-level MANIFEST_PATH/CREDENTIALS_PATH constants -- both
        # need patching so auth and the admin routes agree on one store.
        self._patches = [
            patch.object(team_portal_api, "MANIFEST_PATH", self.manifest_path),
            patch.object(team_portal_api, "CREDENTIALS_PATH", self.creds_path),
            patch.object(tpa, "MANIFEST_PATH", self.manifest_path),
            patch.object(tpa, "CREDENTIALS_PATH", self.creds_path),
            patch.object(tpul, "USAGE_LOG_DIR", self.usage_log_dir),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

        self.client = TestClient(team_portal_api.app)
        self.owner_auth = {"Authorization": f"Bearer {self.owner_token}"}
        self.member_auth = {"Authorization": f"Bearer {self.member_token}"}

    def _usage_lines(self):
        if not self.usage_log_dir.exists():
            return []
        lines = []
        for path in self.usage_log_dir.glob("*.jsonl"):
            lines.extend(json.loads(l) for l in path.read_text().splitlines() if l.strip())
        return lines

    # --- require_owner gating ------------------------------------------

    def test_admin_routes_require_auth_at_all(self):
        resp = self.client.get("/api/admin/members")
        self.assertEqual(resp.status_code, 401)

    def test_non_owner_gets_403_on_admin_routes(self):
        resp = self.client.get("/api/admin/members", headers=self.member_auth)
        self.assertEqual(resp.status_code, 403)

    def test_owner_can_list_members(self):
        resp = self.client.get("/api/admin/members", headers=self.owner_auth)
        self.assertEqual(resp.status_code, 200)
        ids = [m["id"] for m in resp.json()["members"]]
        self.assertIn("todd", ids)
        self.assertIn("jsmith", ids)

    # --- member lifecycle through the API -------------------------------

    def test_add_member_returns_token_once(self):
        resp = self.client.post("/api/admin/members", headers=self.owner_auth, json={
            "id": "newperson", "name": "New Person", "email": "new@example.com",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn("token", resp.json())
        # Confirm they can now actually authenticate with it.
        new_token = resp.json()["token"]
        resp2 = self.client.get("/api/admin/members", headers={"Authorization": f"Bearer {new_token}"})
        self.assertEqual(resp2.status_code, 403)  # real credential, just not an owner

    def test_add_member_duplicate_id_is_422(self):
        resp = self.client.post("/api/admin/members", headers=self.owner_auth, json={
            "id": "jsmith", "name": "Dup", "email": "dup@example.com",
        })
        self.assertEqual(resp.status_code, 422)

    def test_suspend_then_member_is_locked_out_immediately(self):
        resp = self.client.get("/api/brands/search?q=x", headers=self.member_auth)
        self.assertEqual(resp.status_code, 200)

        resp = self.client.post("/api/admin/members/jsmith/suspend", headers=self.owner_auth)
        self.assertEqual(resp.status_code, 200)

        resp = self.client.get("/api/brands/search?q=x", headers=self.member_auth)
        self.assertEqual(resp.status_code, 401)

    def test_unsuspend_restores_access_with_same_key(self):
        self.client.post("/api/admin/members/jsmith/suspend", headers=self.owner_auth)
        resp = self.client.post("/api/admin/members/jsmith/unsuspend", headers=self.owner_auth)
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get("/api/brands/search?q=x", headers=self.member_auth)
        self.assertEqual(resp.status_code, 200)

    def test_revoke_then_member_is_locked_out_immediately(self):
        resp = self.client.post("/api/admin/members/jsmith/revoke", headers=self.owner_auth)
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get("/api/brands/search?q=x", headers=self.member_auth)
        self.assertEqual(resp.status_code, 401)

    def test_rotate_invalidates_old_token_and_returns_new_one(self):
        resp = self.client.post("/api/admin/members/jsmith/rotate", headers=self.owner_auth)
        self.assertEqual(resp.status_code, 200)
        new_token = resp.json()["token"]
        self.assertNotEqual(new_token, self.member_token)

        old = self.client.get("/api/brands/search?q=x", headers=self.member_auth)
        self.assertEqual(old.status_code, 401)
        new = self.client.get("/api/brands/search?q=x", headers={"Authorization": f"Bearer {new_token}"})
        self.assertEqual(new.status_code, 200)

    def test_unknown_member_id_actions_are_422(self):
        for action in ("suspend", "unsuspend", "revoke", "rotate"):
            resp = self.client.post(f"/api/admin/members/does-not-exist/{action}", headers=self.owner_auth)
            self.assertEqual(resp.status_code, 422, f"action={action}")

    def test_rotate_all_invalidates_both_tokens_and_returns_both(self):
        resp = self.client.post("/api/admin/members/rotate-all", headers=self.owner_auth)
        self.assertEqual(resp.status_code, 200)
        tokens = resp.json()["tokens"]
        self.assertEqual(set(tokens.keys()), {"todd", "jsmith"})
        self.assertNotIn(self.owner_token, tokens.values())
        self.assertNotIn(self.member_token, tokens.values())

        # Old tokens -- including the owner's own -- are dead now.
        old_owner = self.client.get("/api/admin/members", headers=self.owner_auth)
        self.assertEqual(old_owner.status_code, 401)
        old_member = self.client.get("/api/brands/search?q=x", headers=self.member_auth)
        self.assertEqual(old_member.status_code, 401)

        # New ones work.
        new_owner_auth = {"Authorization": f"Bearer {tokens['todd']}"}
        new_owner = self.client.get("/api/admin/members", headers=new_owner_auth)
        self.assertEqual(new_owner.status_code, 200)
        new_member_auth = {"Authorization": f"Bearer {tokens['jsmith']}"}
        new_member = self.client.get("/api/brands/search?q=x", headers=new_member_auth)
        self.assertEqual(new_member.status_code, 200)

    def test_rotate_all_requires_owner(self):
        resp = self.client.post("/api/admin/members/rotate-all", headers=self.member_auth)
        self.assertEqual(resp.status_code, 403)

    # --- usage logging middleware ----------------------------------------

    def test_request_is_logged_with_correct_member_and_status(self):
        self.client.get("/api/brands/search?q=abc", headers=self.member_auth)
        lines = self._usage_lines()
        matching = [l for l in lines if l["route"] == "/api/brands/search"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["member_id"], "jsmith")
        self.assertEqual(matching[0]["status_code"], 200)
        self.assertIn("q=abc", matching[0]["query"])

    def test_revoked_token_attempt_still_logs_member_id_before_401(self):
        self.client.post("/api/admin/members/jsmith/revoke", headers=self.owner_auth)
        self.client.get("/api/brands/search?q=x", headers=self.member_auth)
        lines = self._usage_lines()
        matching = [l for l in lines if l["route"] == "/api/brands/search" and l["status_code"] == 401]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["member_id"], "jsmith")

    def test_request_with_no_credential_at_all_logs_member_id_none(self):
        self.client.get("/api/admin/members")
        lines = self._usage_lines()
        matching = [l for l in lines if l["route"] == "/api/admin/members" and l["status_code"] == 401]
        self.assertEqual(len(matching), 1)
        self.assertIsNone(matching[0]["member_id"])

    def test_admin_usage_route_summarizes_correctly(self):
        self.client.get("/api/brands/search?q=x", headers=self.member_auth)
        self.client.get("/api/brands/search?q=y", headers=self.member_auth)
        resp = self.client.get("/api/admin/usage", headers=self.owner_auth)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["summary"]["jsmith"]["request_count"], 2)


if __name__ == "__main__":
    unittest.main()
