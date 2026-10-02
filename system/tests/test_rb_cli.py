"""
test_rb_cli.py — RB-2026-08-28.

rb_cli.py is the real bridge for Codex: Codex has trusted shell access to
this repo but structurally cannot call Custom GPT Actions (a confirmed
OpenAI platform limitation), and the Custom GPT the prior guidance pointed
to instead has now been retired. This gives Codex the exact same operation
set the Trusted Chat Client uses (via rbb_chat_tools.py's already-verified
routing table -- no duplicate/divergent routing logic), invoked directly
over HTTP.

Tests mock the network layer (urllib) and the secrets file -- never hit a
real server or a real credentials file.
"""
from __future__ import annotations

import argparse
import json
import sys
import unittest
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import rb_cli  # noqa: E402


class TestLoadApiKey(unittest.TestCase):
    def test_reads_key_from_secrets_file(self):
        content = 'OTHER_VAR=x\nRB_API_KEY="real-key-value"\n'
        with patch.object(rb_cli, "SECRETS_PATH", MagicMock(exists=lambda: True, read_text=lambda encoding: content)):
            key = rb_cli._load_api_key()
        self.assertEqual(key, "real-key-value")

    def test_handles_export_prefix_and_unquoted_value(self):
        content = "export RB_API_KEY=unquoted-key\n"
        with patch.object(rb_cli, "SECRETS_PATH", MagicMock(exists=lambda: True, read_text=lambda encoding: content)):
            key = rb_cli._load_api_key()
        self.assertEqual(key, "unquoted-key")

    def test_missing_file_exits(self):
        with patch.object(rb_cli, "SECRETS_PATH", MagicMock(exists=lambda: False)):
            with self.assertRaises(SystemExit):
                rb_cli._load_api_key()

    def test_missing_key_in_file_exits(self):
        content = "SOME_OTHER_VAR=x\n"
        with patch.object(rb_cli, "SECRETS_PATH", MagicMock(exists=lambda: True, read_text=lambda encoding: content)):
            with self.assertRaises(SystemExit):
                rb_cli._load_api_key()


class TestCmdList(unittest.TestCase):
    def test_lists_every_real_operation_one_line_each(self):
        buf = StringIO()
        with patch("sys.stdout", buf):
            rc = rb_cli.cmd_list(argparse.Namespace())
        self.assertEqual(rc, 0)
        lines = [l for l in buf.getvalue().splitlines() if l.strip()]
        self.assertIn("getAccountStatus", buf.getvalue())
        self.assertEqual(len(lines), len(rb_cli.tools.build_tools_and_operations()[0]))

    def test_no_description_contains_a_raw_newline(self):
        buf = StringIO()
        with patch("sys.stdout", buf):
            rb_cli.cmd_list(argparse.Namespace())
        for line in buf.getvalue().splitlines():
            self.assertNotIn("\\n", line)


class TestCmdDescribe(unittest.TestCase):
    def test_describes_a_real_operation(self):
        buf = StringIO()
        with patch("sys.stdout", buf):
            rc = rb_cli.cmd_describe(argparse.Namespace(operation_id="getAccountStatus"))
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["tool"]["name"], "getAccountStatus")
        self.assertIn("account_slug", payload["tool"]["parameters"]["properties"])
        self.assertEqual(payload["routing"]["method"], "GET")

    def test_unknown_operation_errors(self):
        buf = StringIO()
        with patch("sys.stderr", buf):
            rc = rb_cli.cmd_describe(argparse.Namespace(operation_id="notARealOp"))
        self.assertEqual(rc, 1)
        self.assertIn("unknown operation", buf.getvalue())


class _FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self):
        return self._body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestCmdCall(unittest.TestCase):
    def setUp(self):
        self._key_patch = patch.object(rb_cli, "_load_api_key", return_value="test-key")
        self._key_patch.start()

    def tearDown(self):
        self._key_patch.stop()

    def test_get_op_builds_correct_url_and_prints_response(self):
        captured_request = {}

        def fake_urlopen(req, timeout=None):
            captured_request["url"] = req.full_url
            captured_request["method"] = req.get_method()
            captured_request["headers"] = req.headers
            return _FakeResponse(200, '{"ok": true}')

        buf = StringIO()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen), patch("sys.stdout", buf):
            rc = rb_cli.cmd_call(argparse.Namespace(
                operation_id="getAccountStatus", arguments='{"account_slug": "pollo-campero"}',
            ))
        self.assertEqual(rc, 0)
        self.assertEqual(captured_request["url"], "http://127.0.0.1:8765/accounts/pollo-campero")
        self.assertEqual(captured_request["method"], "GET")
        self.assertIn("HTTP 200", buf.getvalue())
        self.assertIn('"ok": true', buf.getvalue())

    def test_post_op_sends_real_body(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["method"] = req.get_method()
            return _FakeResponse(200, '{"ok": true}')

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), patch("sys.stdout", StringIO()):
            rc = rb_cli.cmd_call(argparse.Namespace(
                operation_id="addCompetitiveNote",
                arguments='{"competitor_slug": "toast", "note": "real note text"}',
            ))
        self.assertEqual(rc, 0)
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["body"]["note"], "real note text")

    def test_never_sends_extra_unrecognized_arguments_as_body(self):
        """Confirms only declared body_param_names pass through -- a typo'd
        or hallucinated argument name is silently dropped, not sent."""
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeResponse(200, '{"ok": true}')

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), patch("sys.stdout", StringIO()):
            rb_cli.cmd_call(argparse.Namespace(
                operation_id="addCompetitiveNote",
                arguments='{"competitor_slug": "toast", "note": "x", "made_up_field": "y"}',
            ))
        self.assertNotIn("made_up_field", captured["body"])

    def test_unknown_operation_errors_before_any_network_call(self):
        with patch("urllib.request.urlopen") as mock_urlopen, patch("sys.stderr", StringIO()):
            rc = rb_cli.cmd_call(argparse.Namespace(operation_id="notARealOp", arguments="{}"))
        self.assertEqual(rc, 1)
        mock_urlopen.assert_not_called()

    def test_invalid_json_arguments_errors_before_any_network_call(self):
        with patch("urllib.request.urlopen") as mock_urlopen, patch("sys.stderr", StringIO()):
            rc = rb_cli.cmd_call(argparse.Namespace(operation_id="getAccountStatus", arguments="{not valid json"))
        self.assertEqual(rc, 1)
        mock_urlopen.assert_not_called()

    def test_http_error_surfaces_status_and_body_not_silently_swallowed(self):
        import urllib.error

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 404, "Not Found", {}, BytesIO(b'{"detail": "no such account"}'),
            )

        buf = StringIO()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen), patch("sys.stdout", buf):
            rc = rb_cli.cmd_call(argparse.Namespace(
                operation_id="getAccountStatus", arguments='{"account_slug": "does-not-exist"}',
            ))
        self.assertEqual(rc, 1)
        self.assertIn("HTTP 404", buf.getvalue())
        self.assertIn("no such account", buf.getvalue())

    def test_api_key_header_set_but_never_printed(self):
        def fake_urlopen(req, timeout=None):
            self.assertEqual(req.headers.get("X-api-key"), "test-key")
            return _FakeResponse(200, "{}")

        buf = StringIO()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen), patch("sys.stdout", buf):
            rb_cli.cmd_call(argparse.Namespace(operation_id="getAccountStatus", arguments='{"account_slug": "x"}'))
        self.assertNotIn("test-key", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
