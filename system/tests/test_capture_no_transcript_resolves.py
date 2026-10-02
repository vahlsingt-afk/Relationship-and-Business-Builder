"""
test_capture_no_transcript_resolves.py

Regression coverage: a capture whose audio was never transcribed
(transcript_available: false, transcript: "") could never leave the pending
queue. Both submitCapture (POST /captures/{id}/submit) and processAllCaptures
(POST /captures/process_all) detected the empty transcript and returned
status "skipped" without ever calling capture_ingest.mark_processed — so the
file stayed in captures/pending/ forever. Since whisper already ran (or
wasn't configured) at ingest time, that transcript can never later become
non-empty; the item would resurface as "pending" in the Capture Intelligence
brief section every single day no matter how many times the GPT called
either endpoint.

Observed live: cap-3268cc70c60b3579 ("18-23-31.m4a", queued 2026-07-02,
transcript_available=False) was one of "3 pending" captures Todd reported
should already be resolved — this is the specific item that could never
resolve through the normal flow.

Fix: both endpoints now call mark_processed(file_id, result={"skipped": True,
"reason": "no_transcript_available"}) before returning, moving the file to
captures/processed/ so it stops recurring as unresolved backlog.
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

import capture_ingest  # noqa: E402


def _write_pending(pending_dir: Path, file_id: str, transcript: str = "") -> None:
    pending_dir.mkdir(parents=True, exist_ok=True)
    (pending_dir / f"{file_id}.json").write_text(json.dumps({
        "file_id": file_id,
        "queued_at": "2026-07-02T09:03:09+00:00",
        "source_id": "just_press_record",
        "source_label": "Just Press Record",
        "capture_type": "meeting",
        "title_hint": "no transcript capture",
        "transcription_method": "whisper_local",
        "transcript": transcript,
        "word_count": 0,
        "transcript_available": bool(transcript),
        "status": "pending",
    }), encoding="utf-8")


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestCaptureNoTranscriptResolves(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server  # noqa: PLC0415
        cls.server = server
        cls.client = TestClient(server.app, headers={"x-api-key": "test-key"})

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="rb_capture_no_transcript_"))
        self._orig_pending = capture_ingest.PENDING_DIR
        self._orig_processed = capture_ingest.PROCESSED_DIR
        self._orig_registry = capture_ingest.REGISTRY_PATH
        capture_ingest.PENDING_DIR = self._tmp / "pending"
        capture_ingest.PROCESSED_DIR = self._tmp / "processed"
        capture_ingest.REGISTRY_PATH = self._tmp / ".registry.json"

    def tearDown(self):
        capture_ingest.PENDING_DIR = self._orig_pending
        capture_ingest.PROCESSED_DIR = self._orig_processed
        capture_ingest.REGISTRY_PATH = self._orig_registry

    def test_submit_capture_resolves_no_transcript_item(self):
        _write_pending(capture_ingest.PENDING_DIR, "cap-no-transcript")

        resp = self.client.post("/captures/cap-no-transcript/submit")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "skipped")

        self.assertIsNone(capture_ingest.get_pending("cap-no-transcript"))
        processed_path = capture_ingest.PROCESSED_DIR / "cap-no-transcript.json"
        self.assertTrue(processed_path.exists())
        data = json.loads(processed_path.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "processed")

    def test_process_all_captures_resolves_no_transcript_item(self):
        _write_pending(capture_ingest.PENDING_DIR, "cap-no-transcript-2")

        resp = self.client.post("/captures/process_all")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["skipped"], 1)

        self.assertIsNone(capture_ingest.get_pending("cap-no-transcript-2"))
        self.assertTrue((capture_ingest.PROCESSED_DIR / "cap-no-transcript-2.json").exists())

    def test_submit_capture_with_real_transcript_still_processes_normally(self):
        _write_pending(capture_ingest.PENDING_DIR, "cap-has-transcript", transcript="Close the loops tomorrow.")

        resp = self.client.post("/captures/cap-has-transcript/submit")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "processed")
        self.assertIsNone(capture_ingest.get_pending("cap-has-transcript"))


if __name__ == "__main__":
    unittest.main()
