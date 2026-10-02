"""
test_rbb_chat_capture_fast_path.py — RB-2026-09-20.

Phase 1 (rbb-chat fast-path) of
RBB_TOKEN_EFFICIENT_ARCHITECTURE_SCOPE_2026-09-19.md: a message starting
with CAPTURE_FAST_PATH_PREFIX ("capture:") must relay straight to
queueCaptureText's real HTTP call via _execute_operation, with zero OpenAI
model calls -- not even a cheap classification one. A message that doesn't
match the prefix must fall through to the normal _run_chat_turn
orchestration loop unchanged.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "system" / "api"))

os.environ.setdefault("OPENAI_API_KEY", "test-key-not-real")

import rbb_chat  # noqa: E402


class TestFastPathTriggerDetection(unittest.TestCase):
    def test_matches_prefix_case_insensitive(self):
        self.assertEqual(rbb_chat._fast_path_capture_text("capture: hello world"), "hello world")
        self.assertEqual(rbb_chat._fast_path_capture_text("Capture: hello"), "hello")
        self.assertEqual(rbb_chat._fast_path_capture_text("CAPTURE:   hello  "), "hello")

    def test_leading_whitespace_before_prefix_still_matches(self):
        self.assertEqual(rbb_chat._fast_path_capture_text("   capture: hi"), "hi")

    def test_non_matching_message_returns_none(self):
        self.assertIsNone(rbb_chat._fast_path_capture_text("what's Toast's phone number?"))
        self.assertIsNone(rbb_chat._fast_path_capture_text("please capture this for me"))

    def test_prefix_with_no_content_returns_empty_string_not_none(self):
        # Empty string (falsy) is still a distinct match from None --
        # _run_capture_fast_path must treat "" as "trigger matched, nothing
        # to relay", not "trigger did not match at all".
        self.assertEqual(rbb_chat._fast_path_capture_text("capture:"), "")
        self.assertEqual(rbb_chat._fast_path_capture_text("capture:   "), "")


class TestCaptureFastPathExecution(unittest.TestCase):
    def setUp(self):
        self.audit_patch = patch.object(rbb_chat.audit_log, "append_event", MagicMock())
        self.audit_patch.start()

    def tearDown(self):
        self.audit_patch.stop()

    def test_relays_real_text_to_queue_capture_text_with_pasted_content_type(self):
        with patch.object(
            rbb_chat, "_execute_operation",
            MagicMock(return_value=(200, {"status": "pending", "file_id": "abc123"})),
        ) as mock_exec:
            result = rbb_chat._run_capture_fast_path("a real observation about Toast", caller="user")

        mock_exec.assert_called_once_with(
            "queueCaptureText", {"text": "a real observation about Toast", "capture_type": "pasted_content"}
        )
        self.assertIn("Captured", result.reply)
        self.assertIn("abc123", result.reply)
        self.assertFalse(result.persist_response_id)
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0]["operation_id"], "queueCaptureText")
        self.assertEqual(result.tool_calls[0]["status"], 200)

    def test_dedup_response_reports_already_queued(self):
        with patch.object(
            rbb_chat, "_execute_operation",
            MagicMock(return_value=(200, {"status": "already_queued", "file_id": "abc123"})),
        ):
            result = rbb_chat._run_capture_fast_path("the same text as before", caller="user")

        self.assertIn("Already queued", result.reply)
        self.assertIn("abc123", result.reply)

    def test_failure_status_reported_not_swallowed(self):
        with patch.object(
            rbb_chat, "_execute_operation",
            MagicMock(return_value=(400, {"detail": "text must not be empty."})),
        ):
            result = rbb_chat._run_capture_fast_path("whatever", caller="user")

        self.assertIn("Capture failed", result.reply)
        self.assertIn("400", result.reply)

    def test_empty_text_never_calls_execute_operation(self):
        with patch.object(rbb_chat, "_execute_operation", MagicMock()) as mock_exec:
            result = rbb_chat._run_capture_fast_path("", caller="user")

        mock_exec.assert_not_called()
        self.assertEqual(result.tool_calls, [])
        self.assertFalse(result.persist_response_id)
        self.assertIn("Nothing to capture", result.reply)

    def test_audit_tool_call_records_deterministic_trigger_reason(self):
        with patch.object(
            rbb_chat, "_execute_operation",
            MagicMock(return_value=(200, {"status": "pending", "file_id": "xyz"})),
        ), patch.object(rbb_chat, "_audit_tool_call", MagicMock()) as mock_audit:
            rbb_chat._run_capture_fast_path("something to capture", caller="user")

        mock_audit.assert_called_once()
        _, kwargs = mock_audit.call_args
        self.assertEqual(kwargs.get("reason"), "capture_fast_path_deterministic_trigger")
        self.assertEqual(kwargs.get("caller"), "user")


class TestPostChatRouting(unittest.TestCase):
    """Confirm post_chat's branch decision itself -- a capture-prefixed
    message must never reach _run_chat_turn (i.e. never touch the OpenAI
    client), and a normal message must still reach it exactly as before."""

    def setUp(self):
        self.auth_patch = patch.object(rbb_chat, "_auth", MagicMock())
        self.auth_patch.start()
        self.audit_patch = patch.object(rbb_chat.audit_log, "append_event", MagicMock())
        self.audit_patch.start()
        self.history_patch = patch.multiple(
            rbb_chat.history,
            get_current_conversation_id=MagicMock(return_value="conv-fast-path-test"),
            get_last_response_id=MagicMock(return_value=None),
            append_turn=MagicMock(),
            estimate_size_chars=MagicMock(return_value=0),
        )
        self.history_patch.start()

    def tearDown(self):
        self.auth_patch.stop()
        self.audit_patch.stop()
        self.history_patch.stop()

    def test_capture_message_never_invokes_orchestration_loop(self):
        with patch.object(rbb_chat, "_run_chat_turn", MagicMock()) as mock_turn, patch.object(
            rbb_chat, "_execute_operation",
            MagicMock(return_value=(200, {"status": "pending", "file_id": "fp1"})),
        ):
            response = rbb_chat.post_chat(rbb_chat.ChatRequest(message="capture: real content here"))

        mock_turn.assert_not_called()
        self.assertIn("Captured", response.reply)

    def test_normal_message_still_uses_orchestration_loop(self):
        fake_result = rbb_chat._TurnResult(reply="a real answer", response_id="resp-1", tool_calls=[])
        with patch.object(rbb_chat, "_run_chat_turn", MagicMock(return_value=fake_result)) as mock_turn:
            response = rbb_chat.post_chat(rbb_chat.ChatRequest(message="what's Toast's phone number?"))

        mock_turn.assert_called_once()
        self.assertEqual(response.reply, "a real answer")

    def test_fast_path_response_id_not_persisted_to_history(self):
        with patch.object(
            rbb_chat, "_execute_operation",
            MagicMock(return_value=(200, {"status": "pending", "file_id": "fp2"})),
        ):
            rbb_chat.post_chat(rbb_chat.ChatRequest(message="capture: keep this out of the chain"))

        append_calls = rbb_chat.history.append_turn.call_args_list
        assistant_call = [c for c in append_calls if c.args[1] == "assistant"][0]
        self.assertIsNone(assistant_call.kwargs.get("response_id"))


if __name__ == "__main__":
    unittest.main()
