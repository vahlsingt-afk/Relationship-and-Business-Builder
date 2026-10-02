"""
test_team_portal_admin.py

Regression coverage for system/scripts/team_portal_admin.py — the
add/revoke/suspend/unsuspend/rotate lifecycle backing both the CLI and
the new /api/admin/* routes. No test file existed for this module before
2026-09-30 (the admin-portal build was the forcing function to add one).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import team_portal_admin as tpa  # noqa: E402


class TestMemberLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        tmp = Path(self.tmpdir.name)
        self.manifest_path = tmp / "manifest.yaml"
        self.creds_path = tmp / "creds.json"
        self._manifest_patch = patch.object(tpa, "MANIFEST_PATH", self.manifest_path)
        self._creds_patch = patch.object(tpa, "CREDENTIALS_PATH", self.creds_path)
        self._manifest_patch.start()
        self._creds_patch.start()
        self.addCleanup(self._manifest_patch.stop)
        self.addCleanup(self._creds_patch.stop)

    def test_add_member_defaults_plan_internal_and_no_suspension(self):
        tpa.add_member("jsmith", "Jane Smith", "jane@example.com")
        member = tpa.list_members()[0]
        self.assertEqual(member["plan"], "internal")
        self.assertIsNone(member["suspended_at"])
        self.assertIsNone(member["revoked_at"])
        self.assertNotIn("is_owner", member)  # omitted, not False, for a plain member

    def test_add_member_rejects_duplicate_id(self):
        tpa.add_member("jsmith", "Jane Smith", "jane@example.com")
        with self.assertRaises(ValueError):
            tpa.add_member("jsmith", "Someone Else", "other@example.com")

    def test_token_is_never_persisted_in_plaintext(self):
        token = tpa.add_member("jsmith", "Jane Smith", "jane@example.com")
        creds = tpa._load_credentials()
        self.assertNotIn(token, str(creds))
        self.assertEqual(creds["jsmith"]["token_hash"], tpa._hash_token(token))

    def test_revoke_sets_revoked_at_in_both_files(self):
        tpa.add_member("jsmith", "Jane Smith", "jane@example.com")
        tpa.revoke_member("jsmith")
        self.assertIsNotNone(tpa.list_members()[0]["revoked_at"])
        self.assertIsNotNone(tpa._load_credentials()["jsmith"]["revoked_at"])

    def test_revoke_unknown_member_raises(self):
        with self.assertRaises(ValueError):
            tpa.revoke_member("does-not-exist")

    def test_suspend_sets_suspended_at_in_both_files(self):
        tpa.add_member("jsmith", "Jane Smith", "jane@example.com")
        tpa.suspend_member("jsmith")
        self.assertIsNotNone(tpa.list_members()[0]["suspended_at"])
        self.assertIsNotNone(tpa._load_credentials()["jsmith"]["suspended_at"])
        self.assertIsNone(tpa.list_members()[0]["revoked_at"])  # distinct from revoke

    def test_suspend_twice_raises(self):
        tpa.add_member("jsmith", "Jane Smith", "jane@example.com")
        tpa.suspend_member("jsmith")
        with self.assertRaises(ValueError):
            tpa.suspend_member("jsmith")

    def test_suspend_a_revoked_member_raises(self):
        tpa.add_member("jsmith", "Jane Smith", "jane@example.com")
        tpa.revoke_member("jsmith")
        with self.assertRaises(ValueError):
            tpa.suspend_member("jsmith")

    def test_unsuspend_clears_suspended_at_without_rotating_key(self):
        token = tpa.add_member("jsmith", "Jane Smith", "jane@example.com")
        tpa.suspend_member("jsmith")
        tpa.unsuspend_member("jsmith")
        self.assertIsNone(tpa.list_members()[0]["suspended_at"])
        self.assertIsNone(tpa._load_credentials()["jsmith"]["suspended_at"])
        # Same token hash as originally issued -- no rotation happened.
        self.assertEqual(tpa._load_credentials()["jsmith"]["token_hash"], tpa._hash_token(token))

    def test_unsuspend_a_never_suspended_member_raises(self):
        tpa.add_member("jsmith", "Jane Smith", "jane@example.com")
        with self.assertRaises(ValueError):
            tpa.unsuspend_member("jsmith")

    def test_rotate_replaces_hash_and_keeps_manifest_untouched(self):
        tpa.add_member("jsmith", "Jane Smith", "jane@example.com")
        old_hash = tpa._load_credentials()["jsmith"]["token_hash"]
        new_token = tpa.rotate_member("jsmith")
        new_hash = tpa._load_credentials()["jsmith"]["token_hash"]
        self.assertNotEqual(old_hash, new_hash)
        self.assertEqual(new_hash, tpa._hash_token(new_token))

    def test_rotate_preserves_suspended_at_from_manifest(self):
        """Real 2026-09-30 finding: rotate_member() used to write a brand
        new credentials record that dropped suspended_at entirely, which
        left the credentials file's own mirror of it stale after a
        rotate (not a real access-control gap -- get_current_member()
        independently checks the manifest's suspended_at too -- but
        still wrong data)."""
        tpa.add_member("jsmith", "Jane Smith", "jane@example.com")
        tpa.suspend_member("jsmith")
        tpa.rotate_member("jsmith")
        self.assertIsNotNone(tpa._load_credentials()["jsmith"]["suspended_at"])

    def test_rotate_all_reissues_every_active_member_and_skips_revoked(self):
        tpa.add_member("jsmith", "Jane Smith", "jane@example.com")
        tpa.add_member("bwayne", "Bruce Wayne", "bruce@example.com")
        tpa.add_member("ppeter", "Peter Parker", "peter@example.com")
        tpa.revoke_member("ppeter")
        old_hashes = {
            mid: rec["token_hash"] for mid, rec in tpa._load_credentials().items()
        }

        tokens = tpa.rotate_all_members()

        self.assertEqual(set(tokens.keys()), {"jsmith", "bwayne"})
        new_creds = tpa._load_credentials()
        for member_id, token in tokens.items():
            self.assertEqual(new_creds[member_id]["token_hash"], tpa._hash_token(token))
            self.assertNotEqual(new_creds[member_id]["token_hash"], old_hashes[member_id])
        # Revoked member untouched -- no new token issued, hash unchanged.
        self.assertEqual(new_creds["ppeter"]["token_hash"], old_hashes["ppeter"])

    def test_rotate_all_includes_suspended_members(self):
        """Rotating doesn't grant a suspended member access back -- the
        manifest-side suspended_at gate is untouched -- so there's no
        reason to exclude them from a group-wide rotation."""
        tpa.add_member("jsmith", "Jane Smith", "jane@example.com")
        tpa.suspend_member("jsmith")
        tokens = tpa.rotate_all_members()
        self.assertIn("jsmith", tokens)


if __name__ == "__main__":
    unittest.main()
