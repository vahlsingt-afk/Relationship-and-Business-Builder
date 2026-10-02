"""
test_llm_assist.py

Regression coverage for llm_assist.py's two Phase 3 functions
(same_story_tiebreak, is_low_signal). Both are best-effort exactly like
transcript_summarizer.py: no API key, no `openai` package, or any API
failure must all degrade to None (never raise) -- callers treat None as
"no signal," not as a negative answer, and fall back to whatever mechanical
check they already had.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import llm_assist  # noqa: E402


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


class TestSameStoryTiebreakGating(unittest.TestCase):
    def test_empty_titles_return_none_without_calling_openai(self):
        with patch("openai.OpenAI") as mock_openai_cls:
            self.assertIsNone(llm_assist.same_story_tiebreak("", "Something"))
            self.assertIsNone(llm_assist.same_story_tiebreak("Something", ""))
        mock_openai_cls.assert_not_called()

    def test_no_api_key_returns_none_without_calling_openai(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("openai.OpenAI") as mock_openai_cls:
                result = llm_assist.same_story_tiebreak("Headline A", "Headline B")
        self.assertIsNone(result)
        mock_openai_cls.assert_not_called()

    def test_openai_package_missing_returns_none(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch.dict(sys.modules, {"openai": None}):
                result = llm_assist.same_story_tiebreak("Headline A", "Headline B")
        self.assertIsNone(result)


class TestSameStoryTiebreakSuccess(unittest.TestCase):
    def test_same_event_true(self):
        mock_client = _mock_openai_response({"same_event": True})
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = llm_assist.same_story_tiebreak(
                    "Wonder is valued at more than $9B after latest fundraise",
                    "Wonder tops $9B valuation, raises $650M",
                )
        self.assertTrue(result)

    def test_same_event_false(self):
        mock_client = _mock_openai_response({"same_event": False})
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = llm_assist.same_story_tiebreak(
                    "Wonder plans another $600M funding round",
                    "Wonder tops $9B valuation, raises $650M",
                )
        self.assertFalse(result)


class TestSameStoryTiebreakFailureModes(unittest.TestCase):
    def test_api_exception_returns_none(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("network error")
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = llm_assist.same_story_tiebreak("A", "B")
        self.assertIsNone(result)

    def test_non_json_response_returns_none(self):
        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_message.content = "not valid json"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = llm_assist.same_story_tiebreak("A", "B")
        self.assertIsNone(result)

    def test_missing_field_returns_none(self):
        mock_client = _mock_openai_response({"unexpected_field": True})
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = llm_assist.same_story_tiebreak("A", "B")
        self.assertIsNone(result)

    def test_wrong_field_type_returns_none(self):
        mock_client = _mock_openai_response({"same_event": "yes"})
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = llm_assist.same_story_tiebreak("A", "B")
        self.assertIsNone(result)


class TestIsLowSignalGating(unittest.TestCase):
    def test_empty_title_returns_none_without_calling_openai(self):
        with patch("openai.OpenAI") as mock_openai_cls:
            self.assertIsNone(llm_assist.is_low_signal(""))
        mock_openai_cls.assert_not_called()

    def test_no_api_key_returns_none(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("openai.OpenAI") as mock_openai_cls:
                result = llm_assist.is_low_signal("12th straight Red Sox win — MLB Morning Lineup")
        self.assertIsNone(result)
        mock_openai_cls.assert_not_called()


class TestIsLowSignalSuccess(unittest.TestCase):
    def test_sports_digest_flagged_low_signal(self):
        mock_client = _mock_openai_response({"low_signal": True})
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = llm_assist.is_low_signal(
                    "12th straight Red Sox win might be wildest yet — MLB Morning Lineup"
                )
        self.assertTrue(result)

    def test_business_content_not_flagged(self):
        mock_client = _mock_openai_response({"low_signal": False})
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = llm_assist.is_low_signal("Q3 renewal terms — Ryan Hildebrand")
        self.assertFalse(result)


class TestIsLowSignalFailureModes(unittest.TestCase):
    def test_api_exception_returns_none(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("network error")
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = llm_assist.is_low_signal("Some subject line")
        self.assertIsNone(result)

    def test_wrong_field_type_returns_none(self):
        mock_client = _mock_openai_response({"low_signal": "true"})
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = llm_assist.is_low_signal("Some subject line")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
