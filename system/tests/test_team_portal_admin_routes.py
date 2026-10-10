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
    import hunter_mutation_review as hmr  # noqa: E402
    import identity_match_review as imr  # noqa: E402
    import rb_core as rbcore  # noqa: E402
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


@unittest.skipUnless(_FASTAPI_OK, "fastapi not installed")
class TestHunterMutationRoutes(unittest.TestCase):
    """RB-2026-10-10: the missing review step for Hunter's mutation
    proposal queue, exposed as a new Team Portal admin tab."""

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

        self.queue_path = tmp / "proposals.jsonl"
        self.resolutions_path = tmp / "resolutions.jsonl"
        self._patches = [
            patch.object(team_portal_api, "MANIFEST_PATH", self.manifest_path),
            patch.object(team_portal_api, "CREDENTIALS_PATH", self.creds_path),
            patch.object(tpa, "MANIFEST_PATH", self.manifest_path),
            patch.object(tpa, "CREDENTIALS_PATH", self.creds_path),
            patch.object(tpul, "USAGE_LOG_DIR", self.usage_log_dir),
            patch.object(hmr, "PROPOSAL_QUEUE_PATH", self.queue_path),
            patch.object(hmr, "RESOLUTIONS_PATH", self.resolutions_path),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

        self.client = TestClient(team_portal_api.app)
        self.owner_auth = {"Authorization": f"Bearer {self.owner_token}"}
        self.member_auth = {"Authorization": f"Bearer {self.member_token}"}

        self.queue_path.write_text(json.dumps({
            "schema": "rb.hunter_mutation_queue.v1", "queued_at": "2026-10-03T09:00:00+00:00",
            "packet_id": "pkt-1",
            "proposal": {"proposal_id": "mut-a", "target_key": "competitor:example",
                         "field_path": "leadership_event_history", "operation": "append_event",
                         "confidence_pct": 90, "existing_value": None, "new_value": {"x": 1}},
            "decision": {"decision_class": "auto_added_net_new"},
            "reason": "review required or no registered narrow writer",
        }) + "\n", encoding="utf-8")

    def test_requires_auth(self):
        resp = self.client.get("/api/admin/hunter-mutations")
        self.assertEqual(resp.status_code, 401)

    def test_non_owner_forbidden(self):
        resp = self.client.get("/api/admin/hunter-mutations", headers=self.member_auth)
        self.assertEqual(resp.status_code, 403)

    def test_owner_sees_the_pending_proposal(self):
        resp = self.client.get("/api/admin/hunter-mutations", headers=self.owner_auth)
        self.assertEqual(resp.status_code, 200)
        ids = [r["proposal"]["proposal_id"] for r in resp.json()["pending"]]
        self.assertEqual(ids, ["mut-a"])
        self.assertEqual(resp.json()["resolved"], [])

    def test_resolve_approve_records_the_authenticated_owner(self):
        resp = self.client.post(
            "/api/admin/hunter-mutations/mut-a/resolve",
            json={"decision": "approved", "note": "confirmed via LinkedIn"},
            headers=self.owner_auth,
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["decision"], "approved")
        self.assertEqual(body["resolved_by"], "todd")
        self.assertEqual(body["note"], "confirmed via LinkedIn")

        follow_up = self.client.get("/api/admin/hunter-mutations", headers=self.owner_auth)
        self.assertEqual(follow_up.json()["pending"], [])
        self.assertEqual(len(follow_up.json()["resolved"]), 1)

    def test_resolve_unknown_proposal_is_422_not_500(self):
        resp = self.client.post(
            "/api/admin/hunter-mutations/mut-ghost/resolve",
            json={"decision": "approved"},
            headers=self.owner_auth,
        )
        self.assertEqual(resp.status_code, 422)

    def test_resolve_is_gated_to_owners_too(self):
        resp = self.client.post(
            "/api/admin/hunter-mutations/mut-a/resolve",
            json={"decision": "approved"},
            headers=self.member_auth,
        )
        self.assertEqual(resp.status_code, 403)


@unittest.skipUnless(_FASTAPI_OK, "fastapi not installed")
class TestIdentityMatchRoutes(unittest.TestCase):
    """RB-2026-10-10: wiring identity_match_review.py's already-built,
    already-tested pending/confirm/reject logic into the Team Portal."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        tmp = Path(self.tmpdir.name)

        self.manifest_path = tmp / "manifest.yaml"
        self.creds_path = tmp / "creds.json"
        self.owner_token = "test-token-owner"
        self.creds_path.write_text(json.dumps({
            "todd": {"token_hash": hashlib.sha256(self.owner_token.encode()).hexdigest(),
                     "created_at": "2026-09-29T00:00:00+00:00", "revoked_at": None, "suspended_at": None},
        }))
        self.manifest_path.write_text(yaml.safe_dump({
            "members": [
                {"id": "todd", "name": "Todd Vahlsing", "email": "todd@example.com", "role": "team_member",
                 "added_at": "2026-09-29T00:00:00+00:00", "revoked_at": None, "suspended_at": None,
                 "is_owner": True, "plan": "internal"},
            ],
        }))

        self.baseline_path = tmp / "baseline.json"
        self.baseline_path.write_text(json.dumps([
            {"id": "rc-1", "name": "Jane Smith", "current_company": "Example Co", "email": None, "notes": ""},
        ]))
        self.cache_path = tmp / "identity_match_candidates.json"
        self.cache_path.write_text(json.dumps({"candidates": {
            "cand-1": {
                "id": "cand-1", "baseline_id": "rc-1", "baseline_name": "Jane Smith",
                "baseline_company": "Example Co", "sender_name": "Jane Smith",
                "sender_email": "jane@example.com", "account": "personal",
                "thread_id": "t-1", "thread_subject": "Catching up",
                "last_message_at": "2026-10-01T00:00:00+00:00",
                "status": "proposed_pending_confirmation", "first_seen": "2026-10-01",
            },
        }}))

        self._patches = [
            patch.object(team_portal_api, "MANIFEST_PATH", self.manifest_path),
            patch.object(team_portal_api, "CREDENTIALS_PATH", self.creds_path),
            patch.object(tpa, "MANIFEST_PATH", self.manifest_path),
            patch.object(tpa, "CREDENTIALS_PATH", self.creds_path),
            patch.object(tpul, "USAGE_LOG_DIR", tmp / "usage_log"),
            patch.object(imr, "CACHE_PATH", self.cache_path),
            patch.object(rbcore, "BASELINE_PATH", self.baseline_path),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

        self.client = TestClient(team_portal_api.app)
        self.owner_auth = {"Authorization": f"Bearer {self.owner_token}"}

    def test_requires_auth(self):
        resp = self.client.get("/api/admin/identity-matches")
        self.assertEqual(resp.status_code, 401)

    def test_owner_sees_the_pending_match(self):
        resp = self.client.get("/api/admin/identity-matches", headers=self.owner_auth)
        self.assertEqual(resp.status_code, 200)
        ids = [c["id"] for c in resp.json()["pending"]]
        self.assertEqual(ids, ["cand-1"])

    def test_confirm_links_the_email_and_clears_pending(self):
        resp = self.client.post(
            "/api/admin/identity-matches/cand-1/resolve", json={"action": "confirm"}, headers=self.owner_auth,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["confirmed"])
        baseline = json.loads(self.baseline_path.read_text())
        self.assertEqual(baseline[0]["email"], "jane@example.com")
        follow_up = self.client.get("/api/admin/identity-matches", headers=self.owner_auth)
        self.assertEqual(follow_up.json()["pending"], [])

    def test_unknown_action_is_422(self):
        resp = self.client.post(
            "/api/admin/identity-matches/cand-1/resolve", json={"action": "bogus"}, headers=self.owner_auth,
        )
        self.assertEqual(resp.status_code, 422)

    def test_unknown_candidate_is_422_not_500(self):
        resp = self.client.post(
            "/api/admin/identity-matches/cand-ghost/resolve", json={"action": "confirm"}, headers=self.owner_auth,
        )
        self.assertEqual(resp.status_code, 422)


@unittest.skipUnless(_FASTAPI_OK, "fastapi not installed")
class TestLoopRoutes(unittest.TestCase):
    """RB-2026-10-10: wiring server.py's own getLoops/closeLoop logic
    (called in-process, not over HTTP) into the Team Portal -- a close
    issued here must be exactly as governed as one issued through the GPT."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        tmp = Path(self.tmpdir.name)

        self.manifest_path = tmp / "manifest.yaml"
        self.creds_path = tmp / "creds.json"
        self.owner_token = "test-token-owner"
        self.creds_path.write_text(json.dumps({
            "todd": {"token_hash": hashlib.sha256(self.owner_token.encode()).hexdigest(),
                     "created_at": "2026-09-29T00:00:00+00:00", "revoked_at": None, "suspended_at": None},
        }))
        self.manifest_path.write_text(yaml.safe_dump({
            "members": [
                {"id": "todd", "name": "Todd Vahlsing", "email": "todd@example.com", "role": "team_member",
                 "added_at": "2026-09-29T00:00:00+00:00", "revoked_at": None, "suspended_at": None,
                 "is_owner": True, "plan": "internal"},
            ],
        }))

        self.ledger_path = tmp / "loop_ledger.md"
        self.ledger_path.write_text(
            "| L-2026-01-01-001 | 2026-01-01 | Test Party | Test loop description | 2026-01-08 | open |\n",
            encoding="utf-8",
        )
        self.state_path = tmp / "loop_state.json"
        self.state_path.write_text(json.dumps({"loops": {}}))

        self._patches = [
            patch.object(team_portal_api, "MANIFEST_PATH", self.manifest_path),
            patch.object(team_portal_api, "CREDENTIALS_PATH", self.creds_path),
            patch.object(tpa, "MANIFEST_PATH", self.manifest_path),
            patch.object(tpa, "CREDENTIALS_PATH", self.creds_path),
            patch.object(tpul, "USAGE_LOG_DIR", tmp / "usage_log"),
            patch.object(rbcore, "LOOP_LEDGER_PATH", self.ledger_path),
        ]
        # RB-2026-10-10: LOOP_STATE_PATH (the RB-DEFECT-074 state-overlay
        # feature) exists in the live working tree but isn't committed to
        # git yet -- a separate, pre-existing gap this test shouldn't
        # silently depend on. Patch it only when present, so this test
        # passes against both the live code and whatever's actually
        # committed.
        if hasattr(rbcore, "LOOP_STATE_PATH"):
            self._patches.append(patch.object(rbcore, "LOOP_STATE_PATH", self.state_path))
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

        self.client = TestClient(team_portal_api.app)
        self.owner_auth = {"Authorization": f"Bearer {self.owner_token}"}

    def test_requires_auth(self):
        resp = self.client.get("/api/admin/loops")
        self.assertEqual(resp.status_code, 401)

    def test_owner_sees_the_open_loop_bucketed(self):
        resp = self.client.get("/api/admin/loops", headers=self.owner_auth)
        self.assertEqual(resp.status_code, 200)
        buckets = resp.json()["buckets"]
        self.assertNotIn("closed", buckets)  # this tab is for what's still open
        all_ids = [l["id"] for rows in buckets.values() for l in rows]
        self.assertIn("L-2026-01-01-001", all_ids)

    def test_close_marks_it_closed_in_the_real_ledger(self):
        resp = self.client.post(
            "/api/admin/loops/L-2026-01-01-001/close", json={"reason": "done"}, headers=self.owner_auth,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        self.assertIn("closed", self.ledger_path.read_text().lower())
        follow_up = self.client.get("/api/admin/loops", headers=self.owner_auth)
        all_ids = [l["id"] for rows in follow_up.json()["buckets"].values() for l in rows]
        self.assertNotIn("L-2026-01-01-001", all_ids)

    def test_close_unknown_id_is_400_not_500(self):
        resp = self.client.post(
            "/api/admin/loops/L-does-not-exist/close", json={"reason": "done"}, headers=self.owner_auth,
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
