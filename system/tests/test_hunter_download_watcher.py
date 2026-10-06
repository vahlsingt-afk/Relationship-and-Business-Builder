"""test_hunter_download_watcher.py -- 2026-10-06.

Covers the Downloads-to-Drive packet watcher. Isolated to temp folders; never
touches the real Downloads or Drive inbox.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import hunter_download_watcher as hdw  # noqa: E402

ENVELOPE = {
    "schema": "rb.hunter_research_packet.v1",
    "packet_id": "hunter-test-packet-1",
    "targets": [{"target_key": "company:brand-test"}],
    "status": "partial",
}


class TestRepairAndFind(unittest.TestCase):
    def test_newline_inside_a_string_is_repaired(self):
        broken = '{"schema": "rb.hunter_research_packet.v1", "targets": [1], "note": "wrapped\nline"}'
        repaired = hdw.repair_string_newlines(broken)
        self.assertEqual(json.loads(repaired)["note"], "wrapped line")

    def test_structural_newlines_are_kept(self):
        text = '{\n  "a": 1\n}'
        self.assertEqual(json.loads(hdw.repair_string_newlines(text)), {"a": 1})

    def test_envelope_is_found_inside_surrounding_prose(self):
        text = "Here is the packet:\n" + json.dumps(ENVELOPE, indent=2) + "\nDone."
        self.assertEqual(hdw.find_envelope(text)["packet_id"], "hunter-test-packet-1")

    def test_bare_payload_without_envelope_is_not_a_packet(self):
        self.assertIsNone(hdw.find_envelope(json.dumps({"company": "Tim Hortons", "technology_stack": []})))


class TestSweepDownloads(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.downloads = tmp / "Downloads"
        self.drive = tmp / "Drive"
        self.downloads.mkdir()
        self._state_patch = unittest.mock.patch.object(hdw, "STATE", tmp / "state.json")
        self._state_patch.start()

    def tearDown(self):
        self._state_patch.stop()
        self._tmp.cleanup()

    def _run(self):
        return hdw.sweep_downloads(downloads=self.downloads, drive_inbox=self.drive,
                                   now=datetime.now(timezone.utc))

    def test_valid_envelope_json_is_copied_to_drive(self):
        (self.downloads / "packet.json").write_text(json.dumps(ENVELOPE), encoding="utf-8")
        result = self._run()
        self.assertEqual(len(result["copied"]), 1)
        self.assertTrue((self.drive / "hunter-test-packet-1.json").exists())

    def test_bare_payload_is_rejected_and_left_in_downloads(self):
        source = self.downloads / "report.json"
        source.write_text(json.dumps({"company": "Tim Hortons"}), encoding="utf-8")
        result = self._run()
        self.assertEqual(result["copied"], [])
        self.assertEqual(len(result["rejected"]), 1)
        self.assertTrue(source.exists())

    def test_same_file_is_not_copied_twice(self):
        (self.downloads / "packet.json").write_text(json.dumps(ENVELOPE), encoding="utf-8")
        self._run()
        second = self._run()
        self.assertEqual(second["copied"], [])
        self.assertEqual(len(second["skipped"]), 1)

    def test_packet_already_finalized_is_never_copied_again(self):
        receipts = self.downloads.parent / "processed"
        receipts.mkdir()
        (receipts / "hunter-test-packet-1.json.receipt.json").write_text(
            json.dumps({"packet_id": "hunter-test-packet-1"}), encoding="utf-8")
        with unittest.mock.patch.object(hdw, "PROCESSED_RECEIPT_DIRS", (receipts,)):
            (self.downloads / "packet.json").write_text(json.dumps(ENVELOPE), encoding="utf-8")
            result = self._run()
        self.assertEqual(result["copied"], [])
        self.assertFalse((self.drive / "hunter-test-packet-1.json").exists())

    def test_old_files_outside_lookback_are_ignored(self):
        import os, time
        old = self.downloads / "old.json"
        old.write_text(json.dumps(ENVELOPE), encoding="utf-8")
        past = time.time() - 10 * 86400
        os.utime(old, (past, past))
        result = self._run()
        self.assertEqual(result["copied"], [])
        self.assertFalse((self.drive / "hunter-test-packet-1.json").exists())


if __name__ == "__main__":
    import unittest.mock  # noqa: F401
    unittest.main()
