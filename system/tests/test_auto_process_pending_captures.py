"""
test_auto_process_pending_captures.py

Regression coverage: capture_ingest.py's --scan step only queues new
transcripts as pending; the actual intelligence-extraction step (transcript
-> intelligence_triage -> persisted intelligence -> mark_processed)
previously only ran via a live Custom GPT chat command calling
POST /captures/{id}/submit or POST /captures/process_all -- captures could
sit pending across multiple brief cycles if that command was never said
(confirmed live: 2 captures pending for multiple days).

Fixed by extracting the processing loop into a plain function,
server.process_all_pending_captures(), callable directly with no HTTP/auth
dependency, and adding morning_pipeline.py step "capture_process_all" (via
system/scripts/process_pending_captures.py) that runs it automatically
right after the scan steps queue new captures. The processAllCaptures HTTP
route and the new script both delegate to the same function -- no
duplicated logic.
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

import capture_ingest  # noqa: E402


def _write_pending(pending_dir: Path, file_id: str, transcript: str = "A real transcript.") -> None:
    pending_dir.mkdir(parents=True, exist_ok=True)
    (pending_dir / f"{file_id}.json").write_text(json.dumps({
        "file_id": file_id,
        "queued_at": "2026-07-09T09:00:00+00:00",
        "source_id": "just_press_record",
        "source_label": "Just Press Record",
        "capture_type": "meeting",
        "title_hint": f"Test capture {file_id}",
        "transcript": transcript,
        "word_count": len(transcript.split()),
        "transcript_available": True,
        "status": "pending",
    }), encoding="utf-8")


class TestProcessAllPendingCapturesCallableDirectly(unittest.TestCase):
    """The core fix: this function must be callable with zero HTTP/auth
    machinery, since morning_pipeline.py invokes it as a plain subprocess,
    not through the FastAPI app."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="rb_auto_process_captures_"))
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

    def test_pending_capture_gets_processed_and_removed_from_queue(self):
        import server
        _write_pending(capture_ingest.PENDING_DIR, "cap-auto-1")

        result = server.process_all_pending_captures()

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["errors"], 0)
        self.assertIsNone(capture_ingest.get_pending("cap-auto-1"))
        self.assertTrue((capture_ingest.PROCESSED_DIR / "cap-auto-1.json").exists())

    def test_multiple_pending_captures_all_processed(self):
        import server
        _write_pending(capture_ingest.PENDING_DIR, "cap-auto-a")
        _write_pending(capture_ingest.PENDING_DIR, "cap-auto-b")

        result = server.process_all_pending_captures()

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["processed"], 2)
        self.assertIsNone(capture_ingest.get_pending("cap-auto-a"))
        self.assertIsNone(capture_ingest.get_pending("cap-auto-b"))

    def test_no_transcript_capture_skipped_not_stuck(self):
        import server
        _write_pending(capture_ingest.PENDING_DIR, "cap-no-transcript", transcript="")

        result = server.process_all_pending_captures()

        self.assertEqual(result["skipped"], 1)
        self.assertIsNone(capture_ingest.get_pending("cap-no-transcript"))


class TestProcessAllCapturesHttpRouteDelegatesToSameFunction(unittest.TestCase):
    """The HTTP route (processAllCaptures) must be a thin wrapper around
    process_all_pending_captures(), not a second copy of the logic --
    otherwise the two can drift (which is exactly how the stream_count key
    bug from RB-DEFECT-2026-07-08 needed fixing in two places)."""

    def test_route_calls_shared_function(self):
        import server
        with patch.object(server, "process_all_pending_captures", return_value={"sentinel": True}) as mock_fn, \
             patch.object(server, "_auth", lambda *a, **k: None):
            result = server.post_process_all_captures(x_api_key=None)
        mock_fn.assert_called_once()
        self.assertEqual(result, {"sentinel": True})


class TestMorningPipelineWiresAutoProcessing(unittest.TestCase):
    """The pipeline must actually call the new auto-processing step -- a
    passing unit test for process_all_pending_captures() alone wouldn't
    catch a forgotten wiring step in morning_pipeline.py."""

    def test_capture_process_all_step_present_after_scan_steps(self):
        pipeline_src = (ROOT / "system" / "scripts" / "morning_pipeline.py").read_text(encoding="utf-8")
        self.assertIn("capture_process_all", pipeline_src)
        self.assertIn("process_pending_captures.py", pipeline_src)
        # Must run after both scan steps queue new captures, not before.
        scan_idx = pipeline_src.index('_step("capture_ingest_scan"')
        process_idx = pipeline_src.index('_step("capture_process_all"')
        self.assertLess(scan_idx, process_idx)

    def test_competitor_review_runs_after_capture_processing(self):
        pipeline_src = (ROOT / "system" / "scripts" / "morning_pipeline.py").read_text(encoding="utf-8")
        process_idx = pipeline_src.index('_step("capture_process_all"')
        review_idx = pipeline_src.index('_step("competitor_intelligence_review_scan"')
        brief_idx = pipeline_src.index('_step("write_today_and_manifest"')
        self.assertLess(process_idx, review_idx)
        self.assertLess(review_idx, brief_idx)


if __name__ == "__main__":
    unittest.main()
