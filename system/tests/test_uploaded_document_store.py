"""
test_uploaded_document_store.py — RB-2026-08-31.

Unit coverage for the store itself (save/load round trip, missing-id
behavior, overwrite-on-reingest), isolated from the full uploadAndIngestFile
pipeline -- see test_uploaded_document_retrieval.py for the end-to-end path.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import uploaded_document_store as uds  # noqa: E402


class TestUploadedDocumentStore(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_store_dir = uds.STORE_DIR
        uds.STORE_DIR = Path(self._tmpdir.name)

    def tearDown(self):
        uds.STORE_DIR = self._orig_store_dir
        self._tmpdir.cleanup()

    def test_save_then_load_round_trip(self):
        uds.save_document(
            "doc-1", filename="Test.docx", content_type="application/msword",
            extracted_text="Real extracted body text.",
            account_links=["pollo-campero"],
            sections=[{"heading": "Intro", "text": "Real extracted body text."}],
        )
        loaded = uds.load_document("doc-1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["filename"], "Test.docx")
        self.assertEqual(loaded["extracted_text"], "Real extracted body text.")
        self.assertEqual(loaded["char_count"], len("Real extracted body text."))
        self.assertEqual(loaded["account_links"], ["pollo-campero"])
        self.assertEqual(loaded["sections"][0]["heading"], "Intro")

    def test_missing_document_id_returns_none(self):
        self.assertIsNone(uds.load_document("does-not-exist"))

    def test_reingest_same_id_overwrites_not_duplicates(self):
        uds.save_document("doc-2", filename="v1.docx", content_type="application/msword", extracted_text="first version")
        uds.save_document("doc-2", filename="v1.docx", content_type="application/msword", extracted_text="second version")
        loaded = uds.load_document("doc-2")
        self.assertEqual(loaded["extracted_text"], "second version")
        self.assertEqual(len(list(uds.STORE_DIR.glob("doc-2*"))), 1)

    def test_defaults_for_optional_fields(self):
        rec = uds.save_document("doc-3", filename="x.pdf", content_type="application/pdf", extracted_text="text")
        self.assertEqual(rec["account_links"], [])
        self.assertEqual(rec["sections"], [])
        self.assertEqual(rec["warnings"], [])
        self.assertEqual(rec["extraction_status"], "ok")


if __name__ == "__main__":
    unittest.main()
