"""
test_transcript_summarizer.py

Regression coverage for transcript_summarizer.summarize_transcript(), the
LLM-based replacement for Capture Intelligence's keyword-trigger-only
extraction. The old approach's only attempt at "who was this about"
(intelligence_triage.py's capitalized-word regex) produced garbage like
"Possible named subjects: Cool, Well, Yeah, Yeah, Yeah, God." on a real
transcript -- filler words mistaken for people's names -- which is why
render_intelligence_brief.py has to strip that clause entirely, leaving
only generic trigger-word counts as the rendered Capture Intelligence
content. This module calls a real chat-completion model to read the
transcript and extract people/companies/topics/decisions/action items.

Best-effort by design: no API key, no `openai` package, a too-short
transcript, or any API failure must all degrade to None (never raise),
since callers fall back to the existing trigger-based rendering.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import transcript_summarizer as ts  # noqa: E402

_LONG_TRANSCRIPT = " ".join(["word"] * 40) + (
    " Todd talked with Jeff Coffland about the Global Payments integration timeline. "
    "They agreed Jeff will send the enterprise account list by Friday."
)


def _mock_openai_response(payload: dict):
    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = json.dumps(payload)
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_response
    return mock_client


class TestSummarizeTranscriptGating(unittest.TestCase):
    def test_empty_transcript_returns_none(self):
        self.assertIsNone(ts.summarize_transcript(""))
        self.assertIsNone(ts.summarize_transcript("   "))

    def test_short_transcript_below_word_threshold_returns_none(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            self.assertIsNone(ts.summarize_transcript("just a quick hello"))

    def test_no_api_key_returns_none_without_calling_openai(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("openai.OpenAI") as mock_openai_cls:
                result = ts.summarize_transcript(_LONG_TRANSCRIPT)
        self.assertIsNone(result)
        mock_openai_cls.assert_not_called()

    def test_openai_package_missing_returns_none(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch.dict(sys.modules, {"openai": None}):
                result = ts.summarize_transcript(_LONG_TRANSCRIPT)
        self.assertIsNone(result)


class TestSummarizeTranscriptSuccess(unittest.TestCase):
    def test_returns_structured_fields_from_model_response(self):
        payload = {
            "people_mentioned": ["Jeff Coffland"],
            "companies_mentioned": ["Global Payments"],
            "topics": ["Enterprise account handoff"],
            "decisions": ["Jeff will send the account list"],
            "action_items": ["Jeff Coffland to send enterprise account list by Friday"],
            "why_it_matters": "A concrete handoff commitment with a deadline.",
        }
        mock_client = _mock_openai_response(payload)
        # RB-2026-08-28: _canonicalize() reads real baseline_index.json
        # (via _known_entities(), by design -- see its own docstring) to
        # correct transcription noise against known names. This test is
        # about structured-field pass-through, not canonicalization, so it
        # must not depend on whatever companies happen to be in real
        # production data -- confirmed live: baseline_index.json apparently
        # now contains a company close enough to fuzzy-match "Global
        # Payments" to "Global Payments Inc.", which is _canonicalize()
        # working correctly, not a bug, but it broke this unrelated test.
        with patch.object(ts, "_known_entities", return_value=[]):
            with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
                with patch("openai.OpenAI", return_value=mock_client):
                    result = ts.summarize_transcript(_LONG_TRANSCRIPT)
        self.assertIsNotNone(result)
        self.assertEqual(result["people_mentioned"], ["Jeff Coffland"])
        self.assertEqual(result["companies_mentioned"], ["Global Payments"])
        self.assertIn("Enterprise account handoff", result["topics"])
        self.assertIn("Jeff will send the account list", result["decisions"])
        self.assertTrue(result["action_items"])
        self.assertIn("deadline", result["why_it_matters"])

    def test_filler_words_never_reach_people_mentioned_when_model_excludes_them(self):
        """The model is instructed to exclude filler like 'yeah'/'cool' --
        this locks in that the field is passed through as-is (trusting the
        model's own filtering) rather than re-introducing the old regex."""
        payload = {
            "people_mentioned": [],
            "companies_mentioned": [],
            "topics": ["Casual catch-up"],
            "decisions": [],
            "action_items": [],
            "why_it_matters": "Casual conversation, no follow-up needed.",
        }
        mock_client = _mock_openai_response(payload)
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = ts.summarize_transcript(_LONG_TRANSCRIPT)
        self.assertEqual(result["people_mentioned"], [])
        self.assertEqual(result["why_it_matters"], "Casual conversation, no follow-up needed.")

    def test_word_count_override_bypasses_recount(self):
        payload = {"people_mentioned": [], "companies_mentioned": [], "topics": [],
                   "decisions": [], "action_items": [], "why_it_matters": "x"}
        mock_client = _mock_openai_response(payload)
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = ts.summarize_transcript("short text", word_count=3440)
        self.assertIsNotNone(result)


class TestSummarizeTranscriptFailureModes(unittest.TestCase):
    def test_api_exception_returns_none(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("network error")
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = ts.summarize_transcript(_LONG_TRANSCRIPT)
        self.assertIsNone(result)

    def test_non_json_response_returns_none(self):
        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_message.content = "not valid json at all"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = ts.summarize_transcript(_LONG_TRANSCRIPT)
        self.assertIsNone(result)

    def test_non_dict_json_response_returns_none(self):
        mock_client = _mock_openai_response_list = MagicMock()
        mock_message = MagicMock()
        mock_message.content = json.dumps(["not", "a", "dict"])
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = ts.summarize_transcript(_LONG_TRANSCRIPT)
        self.assertIsNone(result)

    def test_malformed_field_types_degrade_gracefully(self):
        """A model response where a list field comes back as a string (or
        similar) must not crash -- just treat it as empty."""
        payload = {
            "people_mentioned": "Jeff Coffland",  # wrong type: string, not list
            "companies_mentioned": None,
            "topics": ["Real topic"],
            "decisions": 42,
            "action_items": [],
            "why_it_matters": None,
        }
        mock_client = _mock_openai_response(payload)
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = ts.summarize_transcript(_LONG_TRANSCRIPT)
        self.assertIsNotNone(result)
        self.assertEqual(result["people_mentioned"], [])
        self.assertEqual(result["companies_mentioned"], [])
        self.assertEqual(result["decisions"], [])
        self.assertEqual(result["why_it_matters"], "")
        self.assertEqual(result["topics"], ["Real topic"])

    def test_action_items_as_objects_flattened_not_leaked_as_raw_dict(self):
        """RB-2026-08-25: live in the 2026-08-25 Intelligence Brief -- gpt-4o-mini
        returned action_items as {"owner": ..., "task": ...} objects despite the
        prompt asking for plain strings, and the raw Python dict repr
        ("{'owner': 'unknown', 'task': '...'}") rendered straight into the
        reader-facing brief. Must flatten to plain text instead."""
        payload = {
            "people_mentioned": [],
            "companies_mentioned": [],
            "topics": [],
            "decisions": [],
            "action_items": [
                {"owner": "unknown", "task": "Follow back up with FSTAC"},
                {"owner": "Todd", "task": "Build out blue sheet for Five Guys"},
                "Plain string action item still supported",
                {"owner": "none", "task": ""},  # no task -> dropped entirely
            ],
            "why_it_matters": None,
        }
        mock_client = _mock_openai_response(payload)
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = ts.summarize_transcript(_LONG_TRANSCRIPT)
        self.assertIsNotNone(result)
        self.assertEqual(result["action_items"], [
            "Follow back up with FSTAC",
            "Todd: Build out blue sheet for Five Guys",
            "Plain string action item still supported",
        ])
        for item in result["action_items"]:
            self.assertNotIn("{", item)
            self.assertNotIn("'owner'", item)


class TestContentLabel(unittest.TestCase):
    """RB-2026-09-11: content_label added so pasted articles/notes (queued
    via queueCaptureText, capture_type "pasted_content") don't get run
    through a prompt that assumes a spoken meeting/call."""

    def test_default_content_label_is_meeting_call_transcript(self):
        payload = {"people_mentioned": [], "companies_mentioned": [], "topics": [],
                   "decisions": [], "action_items": [], "why_it_matters": "x"}
        mock_client = _mock_openai_response(payload)
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                ts.summarize_transcript(_LONG_TRANSCRIPT)
        messages = mock_client.chat.completions.create.call_args.kwargs["messages"]
        self.assertIn("meeting/call transcript", messages[0]["content"])
        self.assertIn("meeting/call transcript", messages[1]["content"])

    def test_custom_content_label_reaches_both_prompts(self):
        payload = {"people_mentioned": [], "companies_mentioned": [], "topics": [],
                   "decisions": [], "action_items": [], "why_it_matters": "x"}
        mock_client = _mock_openai_response(payload)
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                ts.summarize_transcript(
                    _LONG_TRANSCRIPT, content_label="pasted article, web page, or note",
                )
        messages = mock_client.chat.completions.create.call_args.kwargs["messages"]
        self.assertIn("pasted article, web page, or note", messages[0]["content"])
        self.assertIn("pasted article, web page, or note", messages[1]["content"])
        self.assertNotIn("meeting/call transcript", messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
