"""
test_rbb_chat_empty_reply_fallback.py — RB-2026-08-28.

Real incident: a real chat turn made 4 tool calls (a wrong capture id, two
failed uploadAndIngestFile attempts from formatting issues, then a
successful one that persisted 2 real mutations) and MAX_TOOL_TURNS (then 4)
was exhausted exactly on the round whose follow-up call was itself another
function_call, not text -- resp.output_text was empty by definition, and
Todd saw the literal placeholder "(no response text — check tool call
results)" despite real, successful work having happened.

Fix: _run_chat_turn now (1) tries one forced tool_choice="none" follow-up
to get the model's own honest wrap-up, and (2) if that also fails or comes
back empty, falls back to a deterministic summary built from
tool_calls_made (already-recorded real operation_ids/status codes) rather
than a silent, uninformative placeholder.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "system" / "api"))

os.environ.setdefault("OPENAI_API_KEY", "test-key-not-real")

import rbb_chat  # noqa: E402


def _fn_call_item(name: str, call_id: str, arguments: str = "{}"):
    return SimpleNamespace(type="function_call", name=name, call_id=call_id, arguments=arguments)


def _response(*, output=None, output_text=None, resp_id="resp-test"):
    return SimpleNamespace(output=output or [], output_text=output_text, id=resp_id)


class TestEmptyReplyFallback(unittest.TestCase):
    def setUp(self):
        self.audit_patch = patch.object(rbb_chat.audit_log, "append_event", MagicMock())
        self.audit_patch.start()
        self.exec_patch = patch.object(
            rbb_chat, "_execute_operation",
            MagicMock(return_value=(200, {"ok": True, "mutations_applied": 2})),
        )
        self.exec_patch.start()

    def tearDown(self):
        self.audit_patch.stop()
        self.exec_patch.stop()

    def test_nudge_call_recovers_real_reply_when_loop_exhausts_on_a_function_call(self):
        """Loop exhausts MAX_TOOL_TURNS while every follow-up is itself
        another function_call (never naturally reaching text) -- the nudge
        call (tool_choice='none') should supply the final reply."""
        initial = _response(output=[_fn_call_item("uploadAndIngestFile", "call-0")])
        # Every in-loop follow-up is ALSO a function_call -- simulates the
        # real incident's retry pattern exhausting the whole budget.
        looping_followups = [
            _response(output=[_fn_call_item("uploadAndIngestFile", f"call-{i}")])
            for i in range(rbb_chat.MAX_TOOL_TURNS)
        ]
        nudge_reply = _response(output_text="Uploaded and ingested successfully -- 2 mutations applied.")

        create_calls = [initial] + looping_followups + [nudge_reply]
        mock_client = MagicMock()
        mock_client.responses.create.side_effect = create_calls

        with patch.object(rbb_chat, "_openai_client", mock_client):
            result = rbb_chat._run_chat_turn("do the thing", None, "conv-1")

        self.assertEqual(result.reply, "Uploaded and ingested successfully -- 2 mutations applied.")
        # Confirm the nudge call was made with tool_choice="none"
        last_call_kwargs = mock_client.responses.create.call_args_list[-1].kwargs
        self.assertEqual(last_call_kwargs.get("tool_choice"), "none")

    def test_deterministic_fallback_when_nudge_also_comes_back_empty(self):
        """If the nudge call itself returns no text (or raises), fall back
        to a real summary built from tool_calls_made -- never the old
        silent, uninformative placeholder when real tool calls happened."""
        initial = _response(output=[_fn_call_item("uploadAndIngestFile", "call-0")])
        looping_followups = [
            _response(output=[_fn_call_item("uploadAndIngestFile", f"call-{i}")])
            for i in range(rbb_chat.MAX_TOOL_TURNS)
        ]
        empty_nudge = _response(output_text=None)

        create_calls = [initial] + looping_followups + [empty_nudge]
        mock_client = MagicMock()
        mock_client.responses.create.side_effect = create_calls

        with patch.object(rbb_chat, "_openai_client", mock_client):
            result = rbb_chat._run_chat_turn("do the thing", None, "conv-2")

        self.assertNotEqual(result.reply, "(no response text — check tool call results)")
        self.assertIn("uploadAndIngestFile", result.reply)
        self.assertIn("200", result.reply)
        self.assertTrue(len(result.tool_calls) > 0)

    def test_normal_text_reply_unaffected(self):
        """The common case -- model replies with real text without
        exhausting the loop -- must be unchanged."""
        initial = _response(output=[_fn_call_item("getDailyBrief", "call-0")])
        final = _response(output_text="Here is your brief summary.")
        mock_client = MagicMock()
        mock_client.responses.create.side_effect = [initial, final]

        with patch.object(rbb_chat, "_openai_client", mock_client):
            result = rbb_chat._run_chat_turn("give me the brief", None, "conv-3")

        self.assertEqual(result.reply, "Here is your brief summary.")
        # Only 2 calls: initial + the one in-loop follow-up that had real text.
        self.assertEqual(mock_client.responses.create.call_count, 2)


if __name__ == "__main__":
    unittest.main()
