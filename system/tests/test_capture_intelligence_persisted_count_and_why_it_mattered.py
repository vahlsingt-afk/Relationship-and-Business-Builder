"""
test_capture_intelligence_persisted_count_and_why_it_mattered.py

Regression coverage for RB-DEFECT-2026-07-08: server.py's /captures/{id}/submit
and /captures/process_all read `triage_result.get("stream_count", 0)` to
build processing_result.triage_stream_count -- but intelligence_triage.
triage_input() has never returned a key called "stream_count" (only
"type_count"), so that read always silently fell back to 0. Every capture
with real, non-noise intelligence -- even one with persisted_count: 7 --
was permanently marked as having zero detected streams, so
_render_capture_intelligence's signal_items filter (checking only
triage_stream_count > 0) always fell through to the raw-transcript-snippet
fallback, even for captures RB had genuinely extracted structured
intelligence from.

Fixed in two places:
  1. server.py now reads triage_result["type_count"] (the key that actually
     exists) instead of the nonexistent "stream_count".
  2. _render_capture_intelligence's signal detection also checks
     persisted_count > 0, so captures processed *before* the server.py fix
     (which already have a correct persisted_count from IntelligenceDB
     writes) immediately render correctly too, without needing reprocessing.
  3. server.py now also stores a `persisted_items` list (intelligence_type +
     extracted_summary per item) so the brief can show *why* a capture
     mattered instead of a bare stream count.
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


def _write_capture(processed_dir: Path, name: str, processed_at: str, *,
                    transcript: str = "", processing_result: dict) -> None:
    (processed_dir / f"{name}.json").write_text(json.dumps({
        "file_id": name,
        "capture_type": "meeting",
        "title_hint": name,
        "word_count": len(transcript.split()) if transcript else 0,
        "transcript": transcript,
        "processed_at": processed_at,
        "processing_result": processing_result,
    }))


class TestPersistedCountRecognizedAsSignal(unittest.TestCase):
    """The exact real-world case: triage_stream_count stuck at 0 forever
    (the key-name bug), but persisted_count correctly shows real intelligence
    was extracted and written to IntelligenceDB."""

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

    def test_zero_triage_stream_count_but_nonzero_persisted_count_is_signal(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _write_capture(
            self.processed_dir, "real-capture", recent,
            transcript="A long real meeting transcript with genuine content.",
            processing_result={"triage_stream_count": 0, "persisted_count": 7, "noise_only": False},
        )
        out = rib._render_capture_intelligence()
        self.assertNotIn("here's what was actually said", out)
        self.assertNotIn("No intelligence streams extracted from recent captures.", out)
        self.assertIn("7 intelligence stream", out)

    def test_persisted_items_render_as_why_it_mattered_not_bare_count(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _write_capture(
            self.processed_dir, "detailed-capture", recent,
            transcript="Discussed the Q3 renewal and a new relationship signal.",
            processing_result={
                "triage_stream_count": 2,
                "persisted_count": 2,
                "persisted_items": [
                    {"intelligence_type": "relationship_signal",
                     "extracted_summary": "Todd and Jeff Wayman discussed renewing the Q3 contract."},
                    {"intelligence_type": "loop_reference",
                     "extracted_summary": "Todd needs to send Jeff a follow-up by Friday."},
                ],
            },
        )
        out = rib._render_capture_intelligence()
        self.assertIn("relationship signal", out.lower())
        self.assertIn("Todd and Jeff Wayman discussed renewing the Q3 contract.", out)
        self.assertIn("Todd needs to send Jeff a follow-up by Friday.", out)

    def test_capture_without_persisted_items_falls_back_to_bare_count(self):
        """Captures processed before this fix don't have persisted_items —
        must not crash, must fall back gracefully."""
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _write_capture(
            self.processed_dir, "old-capture", recent,
            transcript="An older capture processed before detailed tracking existed.",
            processing_result={"triage_stream_count": 0, "persisted_count": 3},
        )
        out = rib._render_capture_intelligence()
        self.assertIn("3 intelligence stream", out)
        self.assertIn("detail not available", out)

    def test_truly_zero_signal_capture_still_falls_back_to_transcript_snippet(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _write_capture(
            self.processed_dir, "noise-capture", recent,
            transcript="just testing the recorder, ignore this one",
            processing_result={"triage_stream_count": 0, "persisted_count": 0, "noise_only": True},
        )
        out = rib._render_capture_intelligence()
        self.assertIn("here's what was actually said", out)
        self.assertIn("just testing the recorder", out)


class TestRawTriageTypesAndGarbageNamesCleaned(unittest.TestCase):
    """RB-DEFECT-2026-07-10: this section rendered intelligence_triage.py's
    raw classification internals verbatim -- "ri event: Person-level RI
    signal detected: 5 trigger(s): joined, meeting with, promoted. Possible
    named subjects: Hey Dolor, Thank, Day, Compliance, Yep, Yep." "Hey
    Dolor"/"Thank"/"Yep, Yep" are transcript filler words the triage
    engine's name heuristic mistook for people's names -- not real
    information, and "ri event"/"micro graph enrichment" are internal type
    labels, not reader-facing prose."""

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

    def test_ri_event_type_label_translated(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _write_capture(
            self.processed_dir, "ri-capture", recent,
            transcript="Meeting notes.",
            processing_result={
                "triage_stream_count": 1, "persisted_count": 1,
                "persisted_items": [{
                    "intelligence_type": "ri_event",
                    "extracted_summary": (
                        "Person-level RI signal detected: 5 trigger(s): joined, meeting "
                        "with, promoted. Possible named subjects: Hey Dolor, Thank, Day, "
                        "Compliance, Yep, Yep."
                    ),
                }],
            },
        )
        out = rib._render_capture_intelligence()
        self.assertIn("Relationship signal", out)
        self.assertNotIn("ri_event", out)
        self.assertNotIn("ri event:", out)
        self.assertNotIn("Possible named subjects", out)
        self.assertNotIn("Hey Dolor", out)
        self.assertNotIn("Yep, Yep", out)
        self.assertIn("5 trigger(s): joined, meeting with, promoted", out)

    def test_micro_graph_enrichment_type_label_translated(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _write_capture(
            self.processed_dir, "mg-capture", recent,
            transcript="Meeting notes.",
            processing_result={
                "triage_stream_count": 1, "persisted_count": 1,
                "persisted_items": [{
                    "intelligence_type": "micro_graph_enrichment",
                    "extracted_summary": "Entity-scoped data detected for existing artifact 'micro_graph:mcdonalds_us_ops'.",
                }],
            },
        )
        out = rib._render_capture_intelligence()
        self.assertIn("Company data enrichment", out)
        self.assertNotIn("micro_graph_enrichment", out)
        self.assertNotIn("micro graph enrichment:", out)

    def test_unmapped_type_falls_back_to_underscore_replacement(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _write_capture(
            self.processed_dir, "other-capture", recent,
            transcript="Meeting notes.",
            processing_result={
                "triage_stream_count": 1, "persisted_count": 1,
                "persisted_items": [{
                    "intelligence_type": "some_future_type",
                    "extracted_summary": "A summary with no named-subjects clause.",
                }],
            },
        )
        out = rib._render_capture_intelligence()
        self.assertIn("some future type", out)


if __name__ == "__main__":
    unittest.main()
