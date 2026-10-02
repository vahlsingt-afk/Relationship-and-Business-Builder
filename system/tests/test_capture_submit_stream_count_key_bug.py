"""
test_capture_submit_stream_count_key_bug.py

Regression coverage: POST /captures/{id}/submit (and /captures/process_all)
built processing_result["triage_stream_count"] from
triage_result.get("stream_count", 0) -- but intelligence_triage.
triage_input() has never returned a key called "stream_count", only
"type_count". The .get(..., 0) default silently absorbed the KeyError-shaped
mismatch, so triage_stream_count was permanently 0 for every capture ever
submitted, regardless of how much real intelligence was actually detected.
Confirmed live: a real capture (persisted_count: 7, meaning 7 non-noise
streams were written to IntelligenceDB) still showed triage_stream_count: 0
in its processed JSON, which made the Capture Intelligence brief section
treat it as signal-free and fall back to a raw transcript snippet instead of
showing what was actually extracted.

Both endpoints now read triage_result["type_count"] (the key that actually
exists) instead.
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


def _write_pending(pending_dir: Path, file_id: str, transcript: str) -> None:
    pending_dir.mkdir(parents=True, exist_ok=True)
    (pending_dir / f"{file_id}.json").write_text(json.dumps({
        "file_id": file_id,
        "queued_at": "2026-07-08T09:03:19+00:00",
        "source_id": "just_press_record",
        "source_label": "Just Press Record",
        "capture_type": "meeting",
        "title_hint": "real intelligence capture",
        "transcript": transcript,
        "word_count": len(transcript.split()),
        "transcript_available": True,
        "status": "pending",
    }), encoding="utf-8")


_FAKE_TRIAGE_RESULT = {
    "type_count": 2,
    "noise_only": False,
    "identified_types": [
        {"intelligence_type": "relationship_signal",
         "extracted_summary": "Todd and Jeff discussed the Q3 renewal.",
         "extracted_entities": ["Jeff Wayman"], "confidence": "high"},
        {"intelligence_type": "loop_reference",
         "extracted_summary": "Todd needs to follow up with Jeff by Friday.",
         "extracted_entities": [], "confidence": "medium"},
    ],
}


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestCaptureSubmitStreamCountKeyBug(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server  # noqa: PLC0415
        cls.server = server
        cls.client = TestClient(server.app, headers={"x-api-key": "test-key"})

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="rb_capture_stream_count_"))
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

    def test_triage_stream_count_reflects_actual_identified_types_not_zero(self):
        _write_pending(capture_ingest.PENDING_DIR, "cap-real-intel",
                        "Todd and Jeff discussed the Q3 renewal and a follow-up.")

        with patch.object(self.server.intelligence_triage, "triage_input",
                           return_value=dict(_FAKE_TRIAGE_RESULT)):
            resp = self.client.post("/captures/cap-real-intel/submit")

        self.assertEqual(resp.status_code, 200)
        processed_path = capture_ingest.PROCESSED_DIR / "cap-real-intel.json"
        self.assertTrue(processed_path.exists())
        data = json.loads(processed_path.read_text(encoding="utf-8"))
        self.assertEqual(data["processing_result"]["triage_stream_count"], 2)

    def test_process_all_captures_stream_count_also_fixed(self):
        _write_pending(capture_ingest.PENDING_DIR, "cap-real-intel-bulk",
                        "Todd and Jeff discussed the Q3 renewal and a follow-up.")

        with patch.object(self.server.intelligence_triage, "triage_input",
                           return_value=dict(_FAKE_TRIAGE_RESULT)):
            resp = self.client.post("/captures/process_all")

        self.assertEqual(resp.status_code, 200)
        processed_path = capture_ingest.PROCESSED_DIR / "cap-real-intel-bulk.json"
        self.assertTrue(processed_path.exists())
        data = json.loads(processed_path.read_text(encoding="utf-8"))
        self.assertEqual(data["processing_result"]["triage_stream_count"], 2)


if __name__ == "__main__":
    unittest.main()
