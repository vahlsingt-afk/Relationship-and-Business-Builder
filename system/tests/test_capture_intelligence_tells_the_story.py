"""
test_capture_intelligence_tells_the_story.py

Regression coverage: Capture Intelligence showed "Processed: 2 meeting ·
1 voice_note · 65 words captured" followed by "No intelligence streams
extracted from recent captures" even though the underlying transcripts had
real, actionable content — Todd asking "can you send a reminder ... to
remind me to close the open loops tomorrow" and "I just ordered the tub and
shower surround for the bathroom project. Let's update the to-do list."
Neither matched intelligence_triage's narrow set of recognized types
(executive declaration, relationship signal, strategic signal, etc.), so
both were classified as noise with triage_stream_count=0 — true per
triage's definition, but the brief then said nothing happened at all, which
doesn't match reality: Todd said something real, RB just didn't recognize
it as a *formal* intelligence type.

Fix: when there are zero stream-bearing captures but a real transcript
exists (already stored in the processed capture JSON), show a snippet of
what was actually said instead of only declaring the absence of a
structured stream.
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402
import capture_ingest  # noqa: E402


def _write_capture(processed_dir: Path, name: str, processed_at: str,
                    transcript: str = "", stream_count: int = 0, capture_type: str = "voice_note") -> None:
    (processed_dir / f"{name}.json").write_text(json.dumps({
        "file_id": name,
        "capture_type": capture_type,
        "title_hint": name,
        "word_count": len(transcript.split()) if transcript else 0,
        "transcript": transcript,
        "processed_at": processed_at,
        "processing_result": {"triage_stream_count": stream_count},
    }))


class TestCaptureIntelligenceTellsTheStory(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        self.processed_dir = Path(self.tmpdir.name)
        self._patch = patch.object(capture_ingest, "PROCESSED_DIR", self.processed_dir)
        self._patch.start()
        # RB-2026-09-21: isolate the "already reported" manifest's dir too
        # (see test_capture_intelligence_freshness.py) -- otherwise it falls
        # back to real production system/captures/.
        self._captures_patch = patch.object(capture_ingest, "CAPTURES_DIR", self.processed_dir)
        self._captures_patch.start()

    def tearDown(self):
        self._patch.stop()
        self._captures_patch.stop()
        self.tmpdir.cleanup()

    def test_zero_stream_capture_with_real_transcript_shows_snippet(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _write_capture(
            self.processed_dir, "loops-reminder", recent,
            transcript="can you send a reminder to remind me to close the open loops tomorrow",
            stream_count=0,
        )
        out = rib._render_capture_intelligence()
        self.assertIn("here's what was actually said", out)
        self.assertIn("close the open loops tomorrow", out)
        self.assertNotIn("No intelligence streams extracted from recent captures.", out)

    def test_zero_stream_capture_with_no_transcript_still_shows_generic_line(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _write_capture(self.processed_dir, "empty-capture", recent, transcript="", stream_count=0)
        out = rib._render_capture_intelligence()
        self.assertIn("No intelligence streams extracted from recent captures.", out)

    def test_capture_with_real_streams_still_shows_stream_count_not_snippet(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _write_capture(
            self.processed_dir, "meeting-with-signal", recent,
            transcript="Discussed the Q3 roadmap with the vendor.",
            stream_count=2,
        )
        out = rib._render_capture_intelligence()
        self.assertIn("2 intelligence streams", out)
        self.assertNotIn("here's what was actually said", out)


if __name__ == "__main__":
    unittest.main()
