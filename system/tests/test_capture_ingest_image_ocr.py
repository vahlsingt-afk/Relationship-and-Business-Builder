"""
test_capture_ingest_image_ocr.py — RB-2026-09-20.

Phase 1 extension of the token-efficient architecture project: a screenshot
dropped into a watched folder (e.g. settings.json's new icloud_screenshots
source) should be OCR'd at batch-sweep time via a cheap vision-model call,
instead of relayed live through rbb-chat's /upload endpoint (a real model
call on every screenshot, whether or not it needs same-day processing).

Covers _extract_text()'s image dispatch, _ocr_image()'s real OpenAI call
shape and failure handling, and the capture_type default fix (image_ocr ->
"pasted_content", never _detect_capture_type()'s meeting-oriented keyword
fallback) in both sweep() and queue_file().
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import capture_ingest as ci  # noqa: E402


_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class TestExtractTextImageDispatch(unittest.TestCase):
    def test_image_suffix_routes_to_ocr_regardless_of_transcription_mode(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "screenshot.png"
            path.write_bytes(_TINY_PNG)
            with patch.object(ci, "_ocr_image", MagicMock(return_value="some ocr text")) as mock_ocr:
                text, method = ci._extract_text(path, "whisper_local")  # deliberately wrong mode

        mock_ocr.assert_called_once_with(path)
        self.assertEqual(text, "some ocr text")
        self.assertEqual(method, "image_ocr")

    def test_non_image_non_text_non_audio_suffix_still_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "weird.xyz"
            path.write_bytes(b"whatever")
            text, method = ci._extract_text(path, "pre_transcribed")

        self.assertEqual(text, "")
        self.assertEqual(method, "unavailable")


class TestOcrImage(unittest.TestCase):
    def test_raises_without_openai_api_key(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "shot.png"
            path.write_bytes(_TINY_PNG)
            env = dict(os.environ)
            env.pop("OPENAI_API_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(RuntimeError):
                    ci._ocr_image(path)

    def test_calls_cheap_model_with_base64_image_and_returns_output_text(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "shot.png"
            path.write_bytes(_TINY_PNG)

            fake_resp = SimpleNamespace(output_text="  Transcribed post text.  ")
            mock_client = MagicMock()
            mock_client.responses.create.return_value = fake_resp

            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
                 patch("openai.OpenAI", MagicMock(return_value=mock_client)):
                result = ci._ocr_image(path)

        self.assertEqual(result, "Transcribed post text.")
        call_kwargs = mock_client.responses.create.call_args.kwargs
        self.assertEqual(call_kwargs["model"], ci._OCR_MODEL)
        content = call_kwargs["input"][0]["content"]
        image_part = next(c for c in content if c["type"] == "input_image")
        self.assertTrue(image_part["image_url"].startswith("data:image/png;base64,"))


class TestSweepImageCaptureTypeDefault(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmpdir.name).resolve()
        # sweep()/queue_file() build their "pending" report field as
        # pending_path.relative_to(SYSTEM_DIR.parent) -- patch SYSTEM_DIR to
        # a fake path under the same tmp root so that call resolves cleanly
        # against the patched PENDING_DIR/CAPTURES_DIR below instead of the
        # real repo root.
        self.pending_dir = self.folder / "captures" / "pending"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _run_sweep(self, source_overrides=None):
        (self.folder / "shot.png").write_bytes(_TINY_PNG * 20)  # comfortably over the 10-byte noise filter
        source = {
            "id": "test_images",
            "label": "Test Images",
            "folder": str(self.folder),
            "patterns": ["**/*.png"],
            "transcription": "pre_transcribed",
        }
        if source_overrides:
            source.update(source_overrides)

        with patch.object(ci, "_load_sources", MagicMock(return_value=[source])), \
             patch.object(ci, "_ocr_image", MagicMock(return_value="some screenshot text")), \
             patch.object(ci, "SYSTEM_DIR", self.folder / "system_fake"), \
             patch.object(ci, "PENDING_DIR", self.pending_dir), \
             patch.object(ci, "CAPTURES_DIR", self.folder / "captures"), \
             patch.object(ci, "REGISTRY_PATH", self.folder / "captures" / ".registry.json"), \
             patch.object(ci.audit_log, "append_event", MagicMock()):
            return ci.sweep()

    def test_image_capture_defaults_to_pasted_content_not_meeting(self):
        result = self._run_sweep()
        self.assertEqual(result["files_queued"], 1)
        pending_files = list(self.pending_dir.glob("*.json"))
        self.assertEqual(len(pending_files), 1)
        payload = json.loads(pending_files[0].read_text())
        self.assertEqual(payload["capture_type"], "pasted_content")
        self.assertEqual(payload["transcription_method"], "image_ocr")
        self.assertEqual(payload["transcript"], "some screenshot text")

    def test_source_explicit_capture_type_still_wins_over_image_default(self):
        self._run_sweep(source_overrides={"capture_type": "deep_research"})
        pending_files = list(self.pending_dir.glob("*.json"))
        payload = json.loads(pending_files[0].read_text())
        self.assertEqual(payload["capture_type"], "deep_research")


class TestQueueFileImageCaptureTypeDefault(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmpdir.name).resolve()
        self.pending_dir = self.folder / "captures" / "pending"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_manually_queued_screenshot_defaults_to_pasted_content(self):
        path = self.folder / "manual_shot.png"
        path.write_bytes(_TINY_PNG)

        with patch.object(ci, "_load_sources", MagicMock(return_value=[])), \
             patch.object(ci, "_ocr_image", MagicMock(return_value="manual screenshot text")), \
             patch.object(ci, "SYSTEM_DIR", self.folder / "system_fake"), \
             patch.object(ci, "PENDING_DIR", self.pending_dir), \
             patch.object(ci, "CAPTURES_DIR", self.folder / "captures"), \
             patch.object(ci, "REGISTRY_PATH", self.folder / "captures" / ".registry.json"):
            ci.queue_file(path)

        pending_files = list(self.pending_dir.glob("*.json"))
        self.assertEqual(len(pending_files), 1)
        payload = json.loads(pending_files[0].read_text())
        self.assertEqual(payload["capture_type"], "pasted_content")
        self.assertEqual(payload["transcription_method"], "image_ocr")


if __name__ == "__main__":
    unittest.main()
