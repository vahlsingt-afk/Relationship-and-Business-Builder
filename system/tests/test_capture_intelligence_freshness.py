"""
test_capture_intelligence_freshness.py

Regression coverage, two generations of the same underlying problem:

1. Originally, Capture Intelligence re-described the same static 3 processed
   captures (from a single 2026-07-01 test session) every day for a week --
   "Processed: 3 meeting - 87 words captured" repeating verbatim -- because
   it selected the 20 most-recently-modified files in PROCESSED_DIR with no
   recency check at all. Fixed with a 36-hour processed_at lookback window.

2. RB-2026-09-21: that fix was itself wrong in a different way -- a 36h
   window is WIDER than the ~24h daily brief cadence, so a capture processed
   in roughly the last third of a day fell inside *two* consecutive days'
   windows and got reported twice. Confirmed live: several 2026-09-16 JPR
   recordings appeared in both the 2026-09-17 AND 2026-09-18 Intelligence
   Briefs. Fixed the same way capture_ingest.py's own _find_new_files()
   already fixed the identical class of bug once (RB-2026-08-28): a
   persisted "already reported" file_id set, not a time window. Age is no
   longer part of the freshness decision at all -- a capture is shown
   exactly once, the first time this function runs after it's processed,
   regardless of how old it is or how long ago it was processed.
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


def _write_capture(processed_dir: Path, name: str, processed_at: str, stream_count: int = 0) -> None:
    (processed_dir / f"{name}.json").write_text(json.dumps({
        "file_id": name,
        "capture_type": "meeting",
        "title_hint": name,
        "word_count": 10,
        "processed_at": processed_at,
        "processing_result": {"triage_stream_count": stream_count},
    }))


class TestCaptureIntelligenceFreshness(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        self.captures_dir = Path(self.tmpdir.name)
        self.processed_dir = self.captures_dir / "processed"
        self.processed_dir.mkdir()
        self._patch_processed = patch.object(capture_ingest, "PROCESSED_DIR", self.processed_dir)
        self._patch_captures = patch.object(capture_ingest, "CAPTURES_DIR", self.captures_dir)
        self._patch_processed.start()
        self._patch_captures.start()

    def tearDown(self):
        self._patch_processed.stop()
        self._patch_captures.stop()
        self.tmpdir.cleanup()

    def test_never_reported_capture_is_shown_regardless_of_age(self):
        """Age alone is no longer a suppression signal -- an old capture
        that has never appeared in this section before must still show the
        first time this function runs after it exists."""
        old = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
        _write_capture(self.processed_dir, "old-meeting", old, stream_count=2)
        out = rib._render_capture_intelligence()
        self.assertIn("Processed:", out)
        self.assertIn("old-meeting", out)

    def test_fresh_capture_is_reported(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _write_capture(self.processed_dir, "todays-meeting", recent, stream_count=3)
        out = rib._render_capture_intelligence()
        self.assertIn("Processed:", out)
        self.assertIn("todays-meeting", out)

    def test_already_reported_capture_is_not_shown_again(self):
        """The actual bug: a capture must appear exactly once across
        consecutive renders (simulating consecutive daily briefs), not
        twice just because it still falls inside some fixed time window."""
        recent = (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat()
        _write_capture(self.processed_dir, "yesterdays-meeting", recent, stream_count=1)

        first_render = rib._render_capture_intelligence()
        self.assertIn("yesterdays-meeting", first_render)

        second_render = rib._render_capture_intelligence()
        self.assertNotIn("yesterdays-meeting", second_render)
        self.assertIn("No captures processed since the last brief", second_render)

    def test_reporting_persists_shown_items_to_the_manifest(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _write_capture(self.processed_dir, "manifest-check", recent, stream_count=1)
        rib._render_capture_intelligence()

        reported_path = self.captures_dir / ".capture_intelligence_reported.json"
        self.assertTrue(reported_path.exists())
        data = json.loads(reported_path.read_text())
        self.assertIn("manifest-check", data["file_ids"])

    def test_mix_of_reported_and_unreported_only_shows_unreported(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _write_capture(self.processed_dir, "already-shown", recent, stream_count=1)
        rib._render_capture_intelligence()  # first render shows and reports it

        _write_capture(self.processed_dir, "brand-new", recent, stream_count=1)
        out = rib._render_capture_intelligence()

        self.assertIn("brand-new", out)
        self.assertNotIn("already-shown", out)
        self.assertIn("1 Recent", out)


if __name__ == "__main__":
    unittest.main()
