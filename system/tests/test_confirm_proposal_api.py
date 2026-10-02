"""
test_confirm_proposal_api.py — POST /confirm (confirmProposal)

Regression coverage for the unified confirm/reject action. Background:
Todd tried to reject an identity match from the live Custom GPT and it
failed — pasting a raw "POST /identity/.../reject" string gave the GPT
nothing to bridge to its own tools. Fixing that rendering surfaced a bigger
gap: confirmRelationshipInteraction (needed for relationship-intake
proposals like the Jeff Wayman voice-memo case) was never in the GPT's
30-op action list at all — nor were confirmInsight, confirmMacroRecord,
confirmMacroEntity, or confirmExperience. Only identity-match confirm/reject
were reachable, as two separate operations.

confirmProposal consolidates all six "proposed, awaiting operator decision"
surfaces into one GPT action (`{kind, id, confirmed}`), replacing the two
identity-match operations with one and adding the previously-unreachable
four — net one fewer op used, strictly more capability.
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

try:
    from fastapi.testclient import TestClient
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

import rb_core as core  # noqa: E402
import identity_match_review as imr  # noqa: E402
import relationship_intake as ri  # noqa: E402


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestConfirmProposalDispatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server  # noqa: PLC0415
        cls.server = server
        cls.client = TestClient(server.app, headers={"x-api-key": "test-key"})

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="rb_confirm_proposal_"))
        system_dir = self._tmp / "system"
        (system_dir / "inbox").mkdir(parents=True)

        self._orig_baseline_path = core.BASELINE_PATH
        self._orig_imr_cache_path = imr.CACHE_PATH
        self._orig_ledger_path = ri.INTERACTION_LEDGER_PATH
        core.BASELINE_PATH = system_dir / "baseline_index.json"
        imr.CACHE_PATH = system_dir / ".cache" / "identity_match_candidates.json"
        ri.INTERACTION_LEDGER_PATH = system_dir / "interaction_ledger.json"

    def tearDown(self):
        core.BASELINE_PATH = self._orig_baseline_path
        imr.CACHE_PATH = self._orig_imr_cache_path
        ri.INTERACTION_LEDGER_PATH = self._orig_ledger_path

    def test_identity_match_kind_confirms(self):
        candidate_id = "david-drinan::david@blackthornstrategicadvisors.com"
        _write_json(core.BASELINE_PATH, [
            {"id": "david-drinan", "name": "David Drinan", "email": None, "notes": ""},
        ])
        imr._save_store({"candidates": {
            candidate_id: {
                "id": candidate_id, "baseline_id": "david-drinan",
                "baseline_name": "David Drinan", "baseline_company": "Blackthorn",
                "sender_name": "David Drinan",
                "sender_email": "david@blackthornstrategicadvisors.com",
                "account": "bridgepoint", "thread_id": "t1",
                "thread_subject": "Introduction",
                "last_message_at": "Tue, 30 Jun 2026 17:34:22 -0400",
                "status": "proposed_pending_confirmation", "first_seen": "2026-07-01",
            },
        }})
        resp = self.client.post("/confirm", json={
            "kind": "identity_match", "id": candidate_id, "confirmed": True,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["confirmed"])
        baseline = json.loads(core.BASELINE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(baseline[0]["email"], "david@blackthornstrategicadvisors.com")

    def test_identity_match_kind_rejects(self):
        candidate_id = "jeff-waman::jeff@example.com"
        _write_json(core.BASELINE_PATH, [{"id": "jeff-waman", "name": "Jeff Waman", "email": None, "notes": ""}])
        imr._save_store({"candidates": {
            candidate_id: {
                "id": candidate_id, "baseline_id": "jeff-waman",
                "baseline_name": "Jeff Waman", "baseline_company": None,
                "sender_name": "Jeff Waman", "sender_email": "jeff@example.com",
                "account": "personal", "thread_id": "t2", "thread_subject": "Hi",
                "last_message_at": "Tue, 30 Jun 2026 17:34:22 -0400",
                "status": "proposed_pending_confirmation", "first_seen": "2026-07-01",
            },
        }})
        resp = self.client.post("/confirm", json={
            "kind": "identity_match", "id": candidate_id, "confirmed": False,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["rejected"])
        baseline = json.loads(core.BASELINE_PATH.read_text(encoding="utf-8"))
        self.assertIsNone(baseline[0]["email"])

    def test_identity_match_kind_confirms_without_writing_email(self):
        """RB-DEFECT-2026-08-20: Todd confirmed a LinkedIn-invite identity
        match ("yes it's him") but explicitly did not want the platform
        relay address (invitations@linkedin.com) written on as his email —
        neither confirm(writes email) nor reject(not the same person) fit.
        write_email=false routes to confirm_without_email instead."""
        candidate_id = "manuel-rehm::invitations@linkedin.com"
        _write_json(core.BASELINE_PATH, [
            {"id": "manuel-rehm", "name": "Manuel Rehm", "email": None, "notes": ""},
        ])
        imr._save_store({"candidates": {
            candidate_id: {
                "id": candidate_id, "baseline_id": "manuel-rehm",
                "baseline_name": "Manuel Rehm", "baseline_company": "Liebherr Group",
                "sender_name": "Manuel Rehm", "sender_email": "invitations@linkedin.com",
                "account": "personal", "thread_id": "t3", "thread_subject": "I want to connect",
                "last_message_at": "Tue, 18 Aug 2026 14:56:58 +0000 (UTC)",
                "status": "proposed_pending_confirmation", "first_seen": "2026-08-20",
            },
        }})
        resp = self.client.post("/confirm", json={
            "kind": "identity_match", "id": candidate_id, "confirmed": True,
            "write_email": False, "reason": "LinkedIn invite-notification relay, not his address",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["confirmed_no_email"])
        baseline = json.loads(core.BASELINE_PATH.read_text(encoding="utf-8"))
        self.assertIsNone(baseline[0]["email"])
        self.assertIn("not added as their email", baseline[0]["notes"])

    def test_relationship_kind_confirms(self):
        """The motivating case: a relationship-intake proposal (e.g. the Jeff
        Wayman voice-memo interaction) is now reachable from the GPT at all."""
        _write_json(ri.INTERACTION_LEDGER_PATH, {"interactions": [
            {"id": "ri-1", "contact_id": "jeff-wayman", "claim_status": "proposed",
             "persistence_status": "pending confirmation"},
        ]})
        resp = self.client.post("/confirm", json={
            "kind": "relationship", "id": "ri-1", "confirmed": True,
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["claim_status"], "confirmed")
        self.assertEqual(body["persistence_status"], "RB recorded")

    def test_relationship_kind_rejects(self):
        _write_json(ri.INTERACTION_LEDGER_PATH, {"interactions": [
            {"id": "ri-2", "contact_id": "someone", "claim_status": "proposed",
             "persistence_status": "pending confirmation"},
        ]})
        resp = self.client.post("/confirm", json={
            "kind": "relationship", "id": "ri-2", "confirmed": False,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["claim_status"], "rejected")

    def test_unknown_id_returns_404(self):
        _write_json(ri.INTERACTION_LEDGER_PATH, {"interactions": []})
        resp = self.client.post("/confirm", json={
            "kind": "relationship", "id": "does-not-exist", "confirmed": True,
        })
        self.assertEqual(resp.status_code, 404)

    def test_invalid_kind_returns_422(self):
        """kind is a closed Literal set — FastAPI/Pydantic rejects an unknown
        value before it ever reaches the dispatch logic."""
        resp = self.client.post("/confirm", json={
            "kind": "not_a_real_kind", "id": "x", "confirmed": True,
        })
        self.assertEqual(resp.status_code, 422)

    def test_each_kind_dispatches_to_its_own_record_function(self):
        """Structural check that all six kinds route to the right underlying
        function, without needing to seed all four remaining data stores."""
        dispatch_targets = {
            "insight": "insight_intake.record_insight",
            "macro_record": "macro_intelligence.record_behavioral_record",
            "macro_entity": "macro_intelligence.record_entity_risk",
            "experience": "experiential_intelligence.record_experience",
        }
        for kind, target in dispatch_targets.items():
            with self.subTest(kind=kind):
                with patch(f"server.{target}", return_value={"ok": True}) as mocked:
                    resp = self.client.post("/confirm", json={
                        "kind": kind, "id": "some-id", "confirmed": True,
                    })
                    self.assertEqual(resp.status_code, 200)
                    self.assertEqual(resp.json(), {"ok": True})
                    mocked.assert_called_once_with("some-id", confirmed=True)


if __name__ == "__main__":
    unittest.main()
