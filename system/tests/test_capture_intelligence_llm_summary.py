"""
test_capture_intelligence_llm_summary.py

Regression coverage: _render_capture_intelligence()'s only rendered content
came from intelligence_triage's keyword-trigger classifiers -- "Relationship
signal: Person-level RI signal detected: 9 trigger(s): hired, joined, left,
network, reached out, reply." tells Todd nothing about who was discussed or
what came of it. The classifiers' one attempt at extracting names produced
"Possible named subjects: Cool, Well, Yeah, Yeah, Yeah, God." on a real
transcript (filler words mistaken for people), forcing that clause to be
stripped entirely and leaving only the generic trigger count.

Fixed by having server.py call transcript_summarizer.summarize_transcript()
(a real LLM read of the transcript) at processing time and store the result
as processing_result.llm_summary. When present, the brief renders the real
summary (people/companies/topics/decisions/action items/why it matters)
instead of the trigger-count bullets. Best-effort: captures processed
without an LLM summary (no API key configured, call failed, transcript too
short) fall back to the existing rendering unchanged.
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
                    llm_summary: dict | None = None,
                    persisted_items: list | None = None,
                    stream_count: int = 1) -> None:
    (processed_dir / f"{name}.json").write_text(json.dumps({
        "file_id": name,
        "capture_type": "meeting",
        "title_hint": name,
        "word_count": 3440,
        "transcript": "a real transcript",
        "processed_at": processed_at,
        "processing_result": {
            "triage_stream_count": stream_count,
            "persisted_count": stream_count,
            "persisted_items": persisted_items or [],
            "llm_summary": llm_summary,
        },
    }))


class TestCaptureIntelligenceLlmSummary(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        self.processed_dir = Path(self.tmpdir.name)
        self._patch = patch.object(capture_ingest, "PROCESSED_DIR", self.processed_dir)
        self._patch.start()
        # RB-2026-09-21: _render_capture_intelligence()'s "already reported"
        # manifest lives under capture_ingest.CAPTURES_DIR -- isolate it
        # alongside PROCESSED_DIR so this test's captures can't mark
        # themselves reported in (or be suppressed by) real production state
        # or another test's tmp dir.
        self._captures_patch = patch.object(capture_ingest, "CAPTURES_DIR", self.processed_dir)
        self._captures_patch.start()

    def tearDown(self):
        self._patch.stop()
        self._captures_patch.stop()
        self.tmpdir.cleanup()

    def _recent_ts(self) -> str:
        return (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    def test_llm_summary_renders_real_content_not_trigger_counts(self):
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
            persisted_items=[{
                "intelligence_type": "ri_event",
                "extracted_summary": "Person-level RI signal detected: 9 trigger(s): hired, joined.",
            }],
        )
        out = rib._render_capture_intelligence()
        self.assertIn("Jeff Coffland", out)
        self.assertIn("Global Payments", out)
        self.assertIn("Enterprise account handoff", out)
        self.assertIn("Jeff will send the account list", out)
        self.assertIn("send enterprise account list by Friday", out)
        # RB-2026-08-28: NOT a bug -- render_intelligence_brief.py's own
        # comment at the why_it_matters branch documents this as deliberate:
        # per INTELLIGENCE_BRIEF_CANONICAL.md, why_it_matters is RB's own
        # editorial judgment about a capture, not a transcript fact, and
        # stays Daily-Brief-only. The Intelligence Brief renders only what
        # was actually said (people/companies/topics/decisions/actions).
        self.assertNotIn("A concrete handoff commitment with a deadline.", out)
        # The old trigger-count line must not also render once a real
        # summary is available -- no double content for the same capture.
        self.assertNotIn("Person-level RI signal detected: 9 trigger(s)", out)

    def test_no_llm_summary_falls_back_to_persisted_items(self):
        """Best-effort degradation: no API key / failed call / too short --
        the existing trigger-based rendering must be completely unaffected."""
        _write_capture(
            self.processed_dir, "no-summary-call", self._recent_ts(),
            llm_summary=None,
            persisted_items=[{
                "intelligence_type": "ri_event",
                "extracted_summary": "Person-level RI signal detected: 9 trigger(s): hired, joined.",
            }],
        )
        out = rib._render_capture_intelligence()
        self.assertIn("Person-level RI signal detected: 9 trigger(s): hired, joined.", out)

    def test_llm_summary_only_renders_non_empty_fields(self):
        """A quiet/small-talk transcript with no people/companies/decisions
        must not render empty 'People:'/'Companies:' bullets."""
        _write_capture(
            self.processed_dir, "small-talk", self._recent_ts(),
            llm_summary={
                "people_mentioned": [],
                "companies_mentioned": [],
                "topics": ["Weekend catch-up"],
                "decisions": [],
                "action_items": [],
                "why_it_matters": "Casual conversation, no follow-up needed.",
            },
        )
        out = rib._render_capture_intelligence()
        self.assertIn("Weekend catch-up", out)
        # RB-2026-08-28: NOT a bug -- see the matching note in
        # test_llm_summary_renders_real_content_not_trigger_counts above.
        # why_it_matters is deliberately Daily-Brief-only.
        self.assertNotIn("Casual conversation, no follow-up needed.", out)
        self.assertNotIn("**People:**", out)
        self.assertNotIn("**Companies:**", out)
        self.assertNotIn("**Decisions:**", out)
        self.assertNotIn("**Action items:**", out)


if __name__ == "__main__":
    unittest.main()
