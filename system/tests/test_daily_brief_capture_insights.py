"""
test_daily_brief_capture_insights.py

Regression coverage: transcript_summarizer.py's summarize_transcript()
computes a why_it_matters field for every LLM-processed capture (stored at
processing_result.llm_summary.why_it_matters) -- a real CoS-style synthesis
of why a conversation deserves attention. render_intelligence_brief.py's
_render_capture_intelligence() deliberately excludes it (correct, per
INTELLIGENCE_BRIEF_CANONICAL.md: it's RB's editorial judgment, not a
transcript fact), and its own comment claimed the field "belongs in the
Daily Brief" instead. Confirmed live 2026-08-28 that render_daily_brief.py
never referenced llm_summary/why_it_matters at all -- its only capture
section, _render_captures(), covers the PENDING queue, a different data
source and concept from already-processed captures.

_render_capture_insights() is the fix: a new Daily Brief section, parallel
to but distinct from _render_captures(), that surfaces why_it_matters for
recently-processed captures. It deliberately does NOT re-render
people/companies/topics/decisions/action items -- those already appear in
the Intelligence Brief's Capture Intelligence section for the same
captures, and repeating them here would violate the "every word earns its
place" editorial standard.
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

import render_daily_brief as rdb  # noqa: E402
import capture_ingest  # noqa: E402


def _write_capture(processed_dir: Path, name: str, processed_at: str, *,
                    llm_summary: dict | None = None) -> None:
    (processed_dir / f"{name}.json").write_text(json.dumps({
        "file_id": name,
        "capture_type": "meeting",
        "title_hint": name,
        "word_count": 3440,
        "processed_at": processed_at,
        "processing_result": {
            "triage_stream_count": 1,
            "persisted_count": 1,
            "llm_summary": llm_summary,
        },
    }))


class TestDailyBriefCaptureInsights(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        self.processed_dir = Path(self.tmpdir.name)
        self._patch = patch.object(capture_ingest, "PROCESSED_DIR", self.processed_dir)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.tmpdir.cleanup()

    def _recent_ts(self) -> str:
        return (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    def _stale_ts(self) -> str:
        return (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()

    def test_why_it_matters_renders_in_daily_brief(self):
        _write_capture(
            self.processed_dir, "jeff-call", self._recent_ts(),
            llm_summary={
                "people_mentioned": ["Jeff Coffland"],
                "topics": ["Enterprise account handoff"],
                "why_it_matters": "A concrete handoff commitment with a deadline.",
            },
        )
        out = rdb._render_capture_insights()
        self.assertIn("Recent Capture Insights", out)
        self.assertIn("A concrete handoff commitment with a deadline.", out)
        self.assertIn("jeff-call", out)

    def test_people_companies_topics_not_duplicated_here(self):
        """Those fields already render in the Intelligence Brief's Capture
        Intelligence section for the same capture -- repeating them in the
        Daily Brief would violate the no-redundant-content standard."""
        _write_capture(
            self.processed_dir, "jeff-call", self._recent_ts(),
            llm_summary={
                "people_mentioned": ["Jeff Coffland"],
                "companies_mentioned": ["Global Payments"],
                "topics": ["Enterprise account handoff"],
                "decisions": ["Jeff will send the account list"],
                "action_items": ["Jeff Coffland to send enterprise account list by Friday"],
                "why_it_matters": "A concrete handoff commitment with a deadline.",
            },
        )
        out = rdb._render_capture_insights()
        self.assertNotIn("Jeff Coffland", out)
        self.assertNotIn("Global Payments", out)
        self.assertNotIn("Enterprise account handoff", out)
        self.assertNotIn("**People:**", out)
        self.assertNotIn("**Companies:**", out)
        self.assertNotIn("**Decisions:**", out)
        self.assertNotIn("**Action items:**", out)

    def test_no_op_casual_conversation_text_is_suppressed(self):
        """'Casual conversation, no follow-up needed.' conveys nothing
        actionable -- must not render as if it were a real insight."""
        _write_capture(
            self.processed_dir, "small-talk", self._recent_ts(),
            llm_summary={
                "topics": ["Weekend catch-up"],
                "why_it_matters": "Casual conversation, no follow-up needed.",
            },
        )
        out = rdb._render_capture_insights()
        self.assertEqual(out, "")

    def test_no_llm_summary_renders_nothing(self):
        """Best-effort degradation: no API key / failed call / too short --
        must not error or render an empty/placeholder section."""
        _write_capture(self.processed_dir, "no-summary-call", self._recent_ts(), llm_summary=None)
        out = rdb._render_capture_insights()
        self.assertEqual(out, "")

    def test_stale_capture_is_not_reported_as_fresh(self):
        """Same 36h freshness discipline as the Intelligence Brief's
        Capture Intelligence section -- a capture processed days ago must
        not repeat forever as if it were new insight."""
        _write_capture(
            self.processed_dir, "old-call", self._stale_ts(),
            llm_summary={"why_it_matters": "This should not appear."},
        )
        out = rdb._render_capture_insights()
        self.assertEqual(out, "")

    def test_mix_of_stale_and_fresh_only_reports_fresh(self):
        _write_capture(
            self.processed_dir, "old-call", self._stale_ts(),
            llm_summary={"why_it_matters": "Stale insight, must not appear."},
        )
        _write_capture(
            self.processed_dir, "new-call", self._recent_ts(),
            llm_summary={"why_it_matters": "Fresh insight, should appear."},
        )
        out = rdb._render_capture_insights()
        self.assertIn("Fresh insight, should appear.", out)
        self.assertNotIn("Stale insight, must not appear.", out)

    def test_no_processed_captures_at_all_renders_nothing(self):
        out = rdb._render_capture_insights()
        self.assertEqual(out, "")

    def test_multiple_fresh_captures_all_render(self):
        _write_capture(
            self.processed_dir, "call-a", self._recent_ts(),
            llm_summary={"why_it_matters": "First real insight."},
        )
        _write_capture(
            self.processed_dir, "call-b", self._recent_ts(),
            llm_summary={"why_it_matters": "Second real insight."},
        )
        out = rdb._render_capture_insights()
        self.assertIn("First real insight.", out)
        self.assertIn("Second real insight.", out)


if __name__ == "__main__":
    unittest.main()
