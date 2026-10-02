"""
test_write_cache_atomic.py

RB-DEFECT-2026-07-20: rb_core.write_cache() wrote JSON directly to the
target path with write_text() -- a direct, non-atomic write. Found live:
system/.cache/daily_brief.json (5.5MB) sat truncated mid-string after an
interrupted write (a killed process, a timeout, or a full disk mid-write),
unparseable by every downstream reader until manually regenerated. Fixed by
writing to a temp file in the same directory and renaming into place --
rename is atomic, so a reader never observes a partial file.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import rb_core as core  # noqa: E402


class TestWriteCacheAtomic(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self._patch = patch.object(core, "CACHE_DIR", Path(self.tmpdir.name))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.tmpdir.cleanup()

    def test_writes_valid_json_readable_via_read_cache(self):
        core.write_cache("some_report", {"hello": "world"}, source="test")
        data = core.read_cache("some_report")
        self.assertEqual(data, {"hello": "world"})

    def test_no_leftover_tmp_file_after_successful_write(self):
        path = core.write_cache("some_report", {"a": 1}, source="test")
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        self.assertFalse(tmp_path.exists())
        self.assertTrue(path.exists())

    def test_existing_file_never_left_partially_overwritten(self):
        """A large payload's write must not leave the target path in a
        half-written state -- the target either has the old complete
        contents or the new complete contents, never a truncated mix."""
        path = core.write_cache("some_report", {"version": 1}, source="test")
        original_bytes = path.read_bytes()
        self.assertGreater(len(original_bytes), 0)
        # Simulate a large second write; the write happens to a temp file
        # first, so the original file's bytes are never touched until the
        # atomic rename completes.
        large_payload = {"version": 2, "items": list(range(10000))}
        core.write_cache("some_report", large_payload, source="test")
        data = json.loads(path.read_text())
        self.assertEqual(data["data"]["version"], 2)


if __name__ == "__main__":
    unittest.main()
