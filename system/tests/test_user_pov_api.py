"""
test_user_pov_api.py — User POV Registry Phase 1 API
(listPOVEntries/getPOVEntry/addPOVEntry/revisePOVEntry/retirePOVEntry/
attachPOVEvidence).

Calls server.py's route functions directly (bypassing _auth via patch,
isolating user_pov's store paths to a tmp dir) -- same pattern as the
other Phase 1 API test files this session.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "system" / "api"))

import user_pov as up  # noqa: E402


class _IsolatedRootMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self._orig = {
            "ROOT": up.ROOT, "REGISTRY_PATH": up.REGISTRY_PATH,
            "EVENTS_PATH": up.EVENTS_PATH, "EVIDENCE_LINKS_PATH": up.EVIDENCE_LINKS_PATH,
        }
        up.ROOT = tmp
        up.REGISTRY_PATH = tmp / "registry.json"
        up.EVENTS_PATH = tmp / "events.jsonl"
        up.EVIDENCE_LINKS_PATH = tmp / "evidence_links.jsonl"

    def tearDown(self):
        for name, path in self._orig.items():
            setattr(up, name, path)
        self._tmpdir.cleanup()


class TestListAndGetPOVEntries(_IsolatedRootMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        up.add_pov_entry("Entry A", "principle", "scope_a")
        up.add_pov_entry("Entry B", "hard_boundary", "scope_b")

    def test_list_all(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.get_pov_entries_list(scope=None, type=None, status=None, x_api_key=None)
        self.assertEqual(result["entry_count"], 2)

    def test_list_filters_by_type(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.get_pov_entries_list(scope=None, type="hard_boundary", status=None, x_api_key=None)
        self.assertEqual(result["entry_count"], 1)

    def test_invalid_type_rejected(self):
        import server
        from fastapi import HTTPException
        with patch.object(server, "_auth", lambda *a, **k: None):
            with self.assertRaises(HTTPException) as cm:
                server.get_pov_entries_list(scope=None, type="made_up", status=None, x_api_key=None)
        self.assertEqual(cm.exception.status_code, 422)

    def test_get_entry_detail(self):
        import server
        entry = up.list_pov_entries()[0]
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.get_pov_entry_detail(entry["pov_id"], x_api_key=None)
        self.assertEqual(result["entry"]["pov_id"], entry["pov_id"])
        self.assertEqual(result["evidence"], [])

    def test_get_unknown_entry_404s(self):
        import server
        from fastapi import HTTPException
        with patch.object(server, "_auth", lambda *a, **k: None):
            with self.assertRaises(HTTPException) as cm:
                server.get_pov_entry_detail("pov-fake", x_api_key=None)
        self.assertEqual(cm.exception.status_code, 404)


class TestAddPOVEntry(_IsolatedRootMixin, unittest.TestCase):
    def test_creates_entry(self):
        import server
        body = server.AddPOVEntryBody(statement="Test", type="principle", scope="test_scope")
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.post_add_pov_entry(body, x_api_key=None)
        self.assertTrue(result["ok"])
        self.assertEqual(len(up.list_pov_entries()), 1)

    def test_invalid_type_rejected_with_422(self):
        import server
        from fastapi import HTTPException
        body = server.AddPOVEntryBody(statement="Test", type="made_up", scope="test_scope")
        with patch.object(server, "_auth", lambda *a, **k: None):
            with self.assertRaises(HTTPException) as cm:
                server.post_add_pov_entry(body, x_api_key=None)
        self.assertEqual(cm.exception.status_code, 422)


class TestRevisePOVEntry(_IsolatedRootMixin, unittest.TestCase):
    def test_revises_and_supersedes(self):
        import server
        entry = up.add_pov_entry("original", "principle", "test_scope")
        body = server.RevisePOVEntryBody(new_statement="revised", conviction=None, reason="test")
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.post_revise_pov_entry(entry["pov_id"], body, x_api_key=None)
        self.assertEqual(result["entry"]["supersedes"], entry["pov_id"])

    def test_unknown_entry_404s(self):
        import server
        from fastapi import HTTPException
        body = server.RevisePOVEntryBody(new_statement="x", conviction=None, reason=None)
        with patch.object(server, "_auth", lambda *a, **k: None):
            with self.assertRaises(HTTPException) as cm:
                server.post_revise_pov_entry("pov-fake", body, x_api_key=None)
        self.assertEqual(cm.exception.status_code, 404)


class TestRetirePOVEntry(_IsolatedRootMixin, unittest.TestCase):
    def test_retires_entry(self):
        import server
        entry = up.add_pov_entry("x", "principle", "test_scope")
        body = server.RetirePOVEntryBody(reason="no longer applies")
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.post_retire_pov_entry(entry["pov_id"], body, x_api_key=None)
        self.assertEqual(result["entry"]["status"], "retired")


class TestAttachPOVEvidence(_IsolatedRootMixin, unittest.TestCase):
    def test_attaches_evidence(self):
        import server
        entry = up.add_pov_entry("x", "principle", "test_scope")
        body = server.AttachPOVEvidenceBody(relation="supports", evidence="real evidence", source_url=None, confidence="medium")
        with patch.object(server, "_auth", lambda *a, **k: None):
            result = server.post_attach_pov_evidence(entry["pov_id"], body, x_api_key=None)
        self.assertTrue(result["ok"])
        reloaded = up.get_pov_entry(entry["pov_id"])
        self.assertIn(result["evidence"]["evidence_id"], reloaded["supporting_evidence_ids"])

    def test_invalid_relation_rejected_with_422(self):
        import server
        from fastapi import HTTPException
        entry = up.add_pov_entry("x", "principle", "test_scope")
        body = server.AttachPOVEvidenceBody(relation="made_up", evidence="e", source_url=None, confidence="medium")
        with patch.object(server, "_auth", lambda *a, **k: None):
            with self.assertRaises(HTTPException) as cm:
                server.post_attach_pov_evidence(entry["pov_id"], body, x_api_key=None)
        self.assertEqual(cm.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
