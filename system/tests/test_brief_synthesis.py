"""
test_brief_synthesis.py

Regression coverage for brief_synthesis.synthesize_signals(), the LLM-based
replacement for the generic "Competitive or market signal relevant to your
Genius/Worldpay territory -- assess customer impact." / "Review the N
gathered intelligence items for X..." boilerplate that GP/Genius Dot
Connections and Connect the Dots fell back to whenever a signal had no
deterministic role-context match.

Best-effort by design, mirroring transcript_summarizer.py: no API key, no
`openai` package, no items, or any API failure must all degrade to None
(never raise), since callers fall back to the existing deterministic text.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import brief_synthesis as bs  # noqa: E402

_ROLE_SUMMARY = "Todd is a restaurant-technology and payments executive."
_ITEMS = [{"key": "multi_source:&pizza", "title": "&pizza",
           "evidence": "36 signals across 6 sources over 30 days."}]


def _mock_openai_response_raw(raw_content: str):
    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = raw_content
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_response
    return mock_client


def _mock_openai_response(payload: dict):
    return _mock_openai_response_raw(json.dumps(payload))


class TestSynthesizeSignalsGating(unittest.TestCase):
    def test_empty_items_returns_none_without_calling_openai(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI") as mock_openai_cls:
                result = bs.synthesize_signals([], _ROLE_SUMMARY)
        self.assertIsNone(result)
        mock_openai_cls.assert_not_called()

    def test_no_api_key_returns_none_without_calling_openai(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("openai.OpenAI") as mock_openai_cls:
                result = bs.synthesize_signals(_ITEMS, _ROLE_SUMMARY)
        self.assertIsNone(result)
        mock_openai_cls.assert_not_called()

    def test_openai_package_missing_returns_none(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            with patch.dict(sys.modules, {"openai": None}):
                result = bs.synthesize_signals(_ITEMS, _ROLE_SUMMARY)
        self.assertIsNone(result)

    def test_items_missing_key_or_title_are_dropped_pre_call(self):
        items = [{"key": "", "title": "no key", "evidence": "e"},
                 {"key": "k2", "title": "", "evidence": "e"}]
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI") as mock_openai_cls:
                result = bs.synthesize_signals(items, _ROLE_SUMMARY)
        self.assertIsNone(result)
        mock_openai_cls.assert_not_called()


class TestSynthesizeSignalsSuccess(unittest.TestCase):
    def test_successful_call_returns_key_to_why_action_mapping(self):
        payload = {"multi_source:&pizza": {
            "why": "Sustained coverage signals unit growth worth tracking.",
            "action": "Flag for next territory review.",
        }}
        mock_client = _mock_openai_response(payload)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = bs.synthesize_signals(_ITEMS, _ROLE_SUMMARY)
        self.assertEqual(result, {"multi_source:&pizza": {
            "why": "Sustained coverage signals unit growth worth tracking.",
            "action": "Flag for next territory review.",
        }})

    def test_missing_action_defaults_to_no_action_warranted(self):
        payload = {"multi_source:&pizza": {"why": "Worth tracking."}}
        mock_client = _mock_openai_response(payload)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = bs.synthesize_signals(_ITEMS, _ROLE_SUMMARY)
        self.assertEqual(result["multi_source:&pizza"]["action"], "No specific action warranted; awareness only.")

    def test_call_uses_expected_model_and_json_response_format(self):
        payload = {"multi_source:&pizza": {"why": "text", "action": "text"}}
        mock_client = _mock_openai_response(payload)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                bs.synthesize_signals(_ITEMS, _ROLE_SUMMARY)
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        self.assertEqual(call_kwargs["model"], bs.MODEL)
        self.assertEqual(call_kwargs["response_format"], {"type": "json_object"})

    def test_empty_why_is_dropped_from_result(self):
        payload = {"multi_source:&pizza": {"why": "   ", "action": "x"}}
        mock_client = _mock_openai_response(payload)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = bs.synthesize_signals(_ITEMS, _ROLE_SUMMARY)
        self.assertIsNone(result)

    def test_non_dict_value_is_skipped(self):
        payload = {"multi_source:&pizza": "a plain string, not the {why, action} shape"}
        mock_client = _mock_openai_response(payload)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = bs.synthesize_signals(_ITEMS, _ROLE_SUMMARY)
        self.assertIsNone(result)


class TestGenericHedgeGuard(unittest.TestCase):
    """RB-2026-09-05: confirmed live, twice, that the system prompt's own
    explicit anti-generic instructions don't reliably stop the model from
    shipping the same hedge pattern with different wording -- a real
    live-generated sentence for a bare COO-hire announcement ("...may
    signal a strategic shift towards enhancing operational capabilities,
    which could lead to new technology needs...") is structurally
    identical to the already-banned Fiserv/Jana Partners example the
    prompt names explicitly. Prompt text alone isn't a sufficient guard;
    _is_generic_hedge is the deterministic backstop -- these two literal
    live-generated sentences must never reach the caller."""

    _LIVE_GENERIC_COO_HIRE = (
        "Caribou Coffee's appointment of Jane Smith as COO, who has "
        "experience from a competing chain, may signal a strategic shift "
        "towards enhancing operational capabilities, which could lead to "
        "new technology needs that your solutions can address."
    )
    _LIVE_GENERIC_STAKE_CHANGE = (
        "Jana Partners' significant reduction of its stake in Fiserv could "
        "indicate a lack of confidence in Fiserv's future performance, "
        "potentially impacting its competitive positioning and "
        "partnerships in the payments space."
    )
    _SPECIFIC_FUNDING_SENTENCE = (
        "Toast's $150M Series F, earmarked for kitchen display and "
        "inventory modules aimed at the fast-casual segment, means a "
        "40-person engineering push into AI-based demand forecasting -- "
        "watch for that same capability appearing in Toast RFP responses "
        "against your own kitchen tech."
    )

    def test_live_generated_coo_hire_sentence_is_flagged(self):
        self.assertTrue(bs._is_generic_hedge(self._LIVE_GENERIC_COO_HIRE))

    def test_live_generated_stake_change_sentence_is_flagged(self):
        self.assertTrue(bs._is_generic_hedge(self._LIVE_GENERIC_STAKE_CHANGE))

    def test_specific_evidence_grounded_sentence_is_not_flagged(self):
        self.assertFalse(bs._is_generic_hedge(self._SPECIFIC_FUNDING_SENTENCE))

    def test_synthesize_signals_drops_generic_hedge_key_entirely(self):
        payload = {"multi_source:&pizza": {"why": self._LIVE_GENERIC_COO_HIRE, "action": "x"}}
        mock_client = _mock_openai_response(payload)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = bs.synthesize_signals(_ITEMS, _ROLE_SUMMARY)
        self.assertIsNone(result)

    def test_synthesize_signals_keeps_specific_key_alongside_dropped_generic_one(self):
        payload = {
            "multi_source:&pizza": {"why": self._LIVE_GENERIC_COO_HIRE, "action": "x"},
            "toast-funding": {"why": self._SPECIFIC_FUNDING_SENTENCE, "action": "y"},
        }
        mock_client = _mock_openai_response(payload)
        items = _ITEMS + [{"key": "toast-funding", "title": "Toast Series F",
                            "evidence": "Toast raised $150M Series F."}]
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = bs.synthesize_signals(items, _ROLE_SUMMARY)
        self.assertNotIn("multi_source:&pizza", result)
        self.assertIn("toast-funding", result)


class TestSynthesizeSignalsFailureModes(unittest.TestCase):
    def test_malformed_json_response_returns_none(self):
        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_message.content = "not valid json{{{"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = bs.synthesize_signals(_ITEMS, _ROLE_SUMMARY)
        self.assertIsNone(result)

    def test_non_dict_json_response_returns_none(self):
        mock_client = _mock_openai_response_raw("[1, 2, 3]")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = bs.synthesize_signals(_ITEMS, _ROLE_SUMMARY)
        self.assertIsNone(result)

    def test_api_exception_returns_none(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("network error")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI", return_value=mock_client):
                result = bs.synthesize_signals(_ITEMS, _ROLE_SUMMARY)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
