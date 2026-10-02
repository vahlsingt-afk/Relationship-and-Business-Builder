"""
test_capture_pasted_content.py — RB-2026-09-11.

Coverage for the deferred-content-queueing feature: queueCaptureText lets a
user paste an article/observation/note for processing on tomorrow's
automatic morning pass instead of right now, riding the exact pending ->
processed -> brief-reported pipeline already built for meeting recordings
(capture_ingest.py + server.process_all_pending_captures()/
post_capture_submit()). Three real gaps this closes, each covered below:

  1. capture_ingest.py always keyword-detected capture_type, whose fallback
     is "meeting" -- wrong for a pasted article. Fixed with an explicit
     capture_type override on _write_pending()/queue_text().
  2. No chat-reachable way to queue text existed at all -- only files and
     the Granola API path (queue_text() itself, unexposed). Fixed with
     POST /captures/queue-text (queueCaptureText).
  3. Both capture-processing paths hardcoded source_type="transcript" for
     intelligence_triage/IntelligenceDB and never told transcript_summarizer
     it might be reading an article, not a call. Fixed with
     server._capture_processing_context().
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


class _IsolatedCaptureDirsMixin:
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="rb_capture_pasted_content_"))
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


class TestCaptureTypeOverride(_IsolatedCaptureDirsMixin, unittest.TestCase):
    def test_write_pending_uses_explicit_capture_type_not_detection(self):
        # This text would keyword-detect as "phone_call" (contains "call",
        # under 300 words) if the override weren't honored.
        text = "We had a call about the renewal terms."
        out = capture_ingest._write_pending(
            "cap-override-1", {"id": "pasted_content", "label": "Pasted content"},
            text, "pre_transcribed", "2026-09-11T09:00:00+00:00",
            title_hint="Test", source_file_ref="pasted_content:abc",
            capture_type="pasted_content",
        )
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["capture_type"], "pasted_content")

    def test_write_pending_falls_back_to_detection_when_not_given(self):
        text = "We had a call about the renewal terms."
        out = capture_ingest._write_pending(
            "cap-override-2", {"id": "src", "label": "Src", "capture_type_hints": {}},
            text, "pre_transcribed", "2026-09-11T09:00:00+00:00",
            title_hint="Test", source_file_ref="src:abc",
        )
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["capture_type"], "phone_call")

    def test_queue_text_passes_capture_type_override_through(self):
        result = capture_ingest.queue_text(
            "A restaurant-tech article about POS vendors.",
            source_id="pasted_content", source_label="Pasted content",
            external_id="hash123", title_hint="Test article",
            capture_type="pasted_content",
        )
        self.assertEqual(result["status"], "queued")
        data = capture_ingest.get_pending(result["file_id"])
        self.assertEqual(data["capture_type"], "pasted_content")


class TestQueueCaptureTextEndpoint(_IsolatedCaptureDirsMixin, unittest.TestCase):
    def test_queue_text_endpoint_queues_with_pasted_content_type(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            body = server.QueueCaptureTextBody(
                text="Real article text about a restaurant brand's POS rollout.",
                title_hint="Test article", source_url="https://example.com/article",
            )
            result = server.post_queue_capture_text(body, x_api_key=None)
        self.assertEqual(result["status"], "queued")
        data = capture_ingest.get_pending(result["file_id"])
        self.assertEqual(data["capture_type"], "pasted_content")
        self.assertEqual(data["source_id"], "pasted_content")
        self.assertEqual(data["title_hint"], "Test article")
        self.assertEqual(data["source_metadata"]["source_url"], "https://example.com/article")

    def test_duplicate_paste_dedupes_via_content_hash(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            body = server.QueueCaptureTextBody(text="The exact same article text.")
            first = server.post_queue_capture_text(body, x_api_key=None)
            second = server.post_queue_capture_text(body, x_api_key=None)
        self.assertEqual(first["status"], "queued")
        self.assertEqual(second["status"], "already_queued")
        self.assertEqual(first["file_id"], second["file_id"])

    def test_title_hint_defaults_to_text_excerpt_when_omitted(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            body = server.QueueCaptureTextBody(text="No title given for this one.")
            result = server.post_queue_capture_text(body, x_api_key=None)
        data = capture_ingest.get_pending(result["file_id"])
        self.assertTrue(data["title_hint"])
        self.assertIn("No title given", data["title_hint"])

    def test_empty_text_rejected(self):
        import server
        from fastapi import HTTPException
        with patch.object(server, "_auth", lambda *a, **k: None):
            body = server.QueueCaptureTextBody(text="   ")
            with self.assertRaises(HTTPException):
                server.post_queue_capture_text(body, x_api_key=None)


class TestQueueCaptureTextTypeOverride(_IsolatedCaptureDirsMixin, unittest.TestCase):
    """RB-2026-09-19: queueCaptureText hardcoded capture_type="pasted_content"
    unconditionally -- correct default, but the ChatGPT Intelligence Drop
    capture source documents this endpoint as the hosted-Custom-GPT
    equivalent (no filesystem access) for deep-research evidence packets,
    which got mislabeled pasted_content with no way to say otherwise."""

    def test_deep_research_override_sets_capture_type_and_source(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            body = server.QueueCaptureTextBody(
                text="A real deep-research evidence packet about a vendor's POS rollout.",
                capture_type="deep_research",
            )
            result = server.post_queue_capture_text(body, x_api_key=None)
        data = capture_ingest.get_pending(result["file_id"])
        self.assertEqual(data["capture_type"], "deep_research")
        self.assertEqual(data["source_id"], "deep_research_hosted")

    def test_omitted_capture_type_still_defaults_to_pasted_content(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            body = server.QueueCaptureTextBody(text="No capture_type given for this one.")
            result = server.post_queue_capture_text(body, x_api_key=None)
        data = capture_ingest.get_pending(result["file_id"])
        self.assertEqual(data["capture_type"], "pasted_content")
        self.assertEqual(data["source_id"], "pasted_content")

    def test_invalid_capture_type_rejected(self):
        import server
        from fastapi import HTTPException
        with patch.object(server, "_auth", lambda *a, **k: None):
            body = server.QueueCaptureTextBody(text="Some text.", capture_type="meeting")
            with self.assertRaises(HTTPException):
                server.post_queue_capture_text(body, x_api_key=None)

    def test_deep_research_gets_paste_processing_context_not_transcript(self):
        """Confirms the new capture_type doesn't accidentally misroute into
        _capture_processing_context()'s meeting-shaped framing -- it isn't
        in _MEETING_LIKE_CAPTURE_TYPES, so it must fall into the same
        "paste" branch pasted_content already uses."""
        import server
        source_type, content_label = server._capture_processing_context("deep_research")
        self.assertEqual(source_type, "paste")
        self.assertEqual(content_label, "pasted article, web page, or note")


class TestCaptureProcessingContext(unittest.TestCase):
    """_capture_processing_context() is a pure lookup -- no isolation needed."""

    def test_meeting_like_types_get_transcript_source_type(self):
        import server
        for ct in ("meeting", "voice_note", "walking", "conference", "phone_call"):
            source_type, content_label = server._capture_processing_context(ct)
            self.assertEqual(source_type, "transcript")
            self.assertEqual(content_label, "meeting/call transcript")

    def test_pasted_content_gets_paste_source_type(self):
        import server
        source_type, content_label = server._capture_processing_context("pasted_content")
        self.assertEqual(source_type, "paste")
        self.assertEqual(content_label, "pasted article, web page, or note")

    def test_missing_capture_type_defaults_to_meeting_behavior(self):
        import server
        source_type, content_label = server._capture_processing_context(None)
        self.assertEqual(source_type, "transcript")
        self.assertEqual(content_label, "meeting/call transcript")


def _write_pending_raw(pending_dir: Path, file_id: str, capture_type: str, transcript: str) -> None:
    pending_dir.mkdir(parents=True, exist_ok=True)
    (pending_dir / f"{file_id}.json").write_text(json.dumps({
        "file_id": file_id,
        "queued_at": "2026-09-11T09:00:00+00:00",
        "source_id": "pasted_content",
        "source_label": "Pasted content",
        "capture_type": capture_type,
        "title_hint": f"Test capture {file_id}",
        "transcript": transcript,
        "word_count": len(transcript.split()),
        "transcript_available": True,
        "status": "pending",
    }), encoding="utf-8")


class TestProcessingBranchesByCaptureType(_IsolatedCaptureDirsMixin, unittest.TestCase):
    """Both processing paths (submitCapture and process_all_pending_captures)
    must derive source_type/content_label from the real capture_type, not
    hardcode meeting-shaped values -- verified by inspecting what actually
    reached intelligence_triage/transcript_summarizer, not just that
    processing completed without error."""

    def test_submit_capture_uses_paste_context_for_pasted_content(self):
        import server
        _write_pending_raw(capture_ingest.PENDING_DIR, "cap-pc-1", "pasted_content",
                            "A real pasted article about restaurant technology vendors.")
        with patch.object(server, "_auth", lambda *a, **k: None), \
             patch.object(server.intelligence_triage, "triage_input",
                           wraps=server.intelligence_triage.triage_input) as mock_triage, \
             patch.object(server.transcript_summarizer, "summarize_transcript",
                           return_value=None) as mock_summarize:
            server.post_capture_submit("cap-pc-1", x_api_key=None)
        self.assertEqual(mock_triage.call_args.kwargs["source_type"], "paste")
        self.assertEqual(mock_summarize.call_args.kwargs["content_label"],
                          "pasted article, web page, or note")

    def test_submit_capture_uses_transcript_context_for_meeting(self):
        import server
        _write_pending_raw(capture_ingest.PENDING_DIR, "cap-mtg-1", "meeting",
                            "A real meeting transcript discussing next steps.")
        with patch.object(server, "_auth", lambda *a, **k: None), \
             patch.object(server.intelligence_triage, "triage_input",
                           wraps=server.intelligence_triage.triage_input) as mock_triage, \
             patch.object(server.transcript_summarizer, "summarize_transcript",
                           return_value=None) as mock_summarize:
            server.post_capture_submit("cap-mtg-1", x_api_key=None)
        self.assertEqual(mock_triage.call_args.kwargs["source_type"], "transcript")
        self.assertEqual(mock_summarize.call_args.kwargs["content_label"], "meeting/call transcript")

    def test_process_all_pending_uses_paste_context_for_pasted_content(self):
        import server
        _write_pending_raw(capture_ingest.PENDING_DIR, "cap-pc-2", "pasted_content",
                            "A real pasted article about restaurant technology vendors.")
        with patch.object(server.intelligence_triage, "triage_input",
                           wraps=server.intelligence_triage.triage_input) as mock_triage, \
             patch.object(server.transcript_summarizer, "summarize_transcript",
                           return_value=None) as mock_summarize:
            server.process_all_pending_captures()
        self.assertEqual(mock_triage.call_args.kwargs["source_type"], "paste")
        self.assertEqual(mock_summarize.call_args.kwargs["content_label"],
                          "pasted article, web page, or note")

    def test_process_all_pending_uses_transcript_context_for_meeting(self):
        import server
        _write_pending_raw(capture_ingest.PENDING_DIR, "cap-mtg-2", "meeting",
                            "A real meeting transcript discussing next steps.")
        with patch.object(server.intelligence_triage, "triage_input",
                           wraps=server.intelligence_triage.triage_input) as mock_triage, \
             patch.object(server.transcript_summarizer, "summarize_transcript",
                           return_value=None) as mock_summarize:
            server.process_all_pending_captures()
        self.assertEqual(mock_triage.call_args.kwargs["source_type"], "transcript")
        self.assertEqual(mock_summarize.call_args.kwargs["content_label"], "meeting/call transcript")


if __name__ == "__main__":
    unittest.main()
