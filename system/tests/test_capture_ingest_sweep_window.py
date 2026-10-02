"""
test_capture_ingest_sweep_window.py — RB-2026-08-28.

Real gap found during a JPR-capture audit Todd asked for: 5 real, fully
synced JPR recordings (2026-07-28 x2, 2026-08-13 x2 -- one 32MB, a real
substantive meeting -- 2026-08-14 x1) were never queued at all, sitting
silently unprocessed in the source folder. Root cause: _find_new_files()
combined two filters -- "not already in the dedup registry" AND "modified
within the last sweep_window_hours (default 24)" -- so any real gap in
sweep cadence longer than the window permanently orphaned files that fell
outside it. The registry-based dedup check alone is sufficient and has no
such failure mode: a file either gets picked up on the next sweep after it
appears, or (if a sweep was missed) whenever the next one actually runs,
regardless of how much time passed. Fixed by dropping the time-based
early-exit; `since` is kept in the signature (used only for sweep()'s own
reporting) to avoid a wider API change.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import capture_ingest as ci  # noqa: E402


def _touch_with_mtime(path: Path, content: bytes, mtime: datetime) -> None:
    path.write_bytes(content)
    ts = mtime.timestamp()
    os_utime = __import__("os").utime
    os_utime(path, (ts, ts))


class TestFindNewFilesIgnoresStaleWindow(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        # resolve() up front -- _find_new_files resolves the source folder
        # internally (_expand_folder), and macOS's /tmp is a symlink to
        # /private/tmp, so an unresolved path here would compute a
        # different _file_id than the one _find_new_files actually uses.
        self.folder = Path(self._tmpdir.name).resolve()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _source(self):
        return {"id": "test_source", "folder": str(self.folder), "patterns": ["**/*.m4a"]}

    def test_file_older_than_sweep_window_is_still_discovered(self):
        """The exact bug: a real, unprocessed file whose mtime is well
        outside a normal 24h sweep window must still be found, because the
        registry (not recency) is what determines 'already handled'."""
        old_file = self.folder / "2026-08-13" / "13-57-10.m4a"
        old_file.parent.mkdir(parents=True)
        old_mtime = datetime.now(timezone.utc) - timedelta(days=15)
        _touch_with_mtime(old_file, b"x" * 1000, old_mtime)

        registry = {"processed_ids": {}}
        since = datetime.now(timezone.utc) - timedelta(hours=24)

        found = ci._find_new_files(self._source(), registry, since)
        self.assertEqual([p.name for p in found], ["13-57-10.m4a"])

    def test_already_registered_file_is_not_rediscovered_regardless_of_age(self):
        """Dedup still works -- a file already in processed_ids is skipped
        whether it's old or new."""
        f = self.folder / "recent.m4a"
        _touch_with_mtime(f, b"x" * 1000, datetime.now(timezone.utc))
        fid = ci._file_id(f)

        registry = {"processed_ids": {fid: {"status": "processed"}}}
        since = datetime.now(timezone.utc) - timedelta(hours=24)

        found = ci._find_new_files(self._source(), registry, since)
        self.assertEqual(found, [])

    def test_multiple_stale_unregistered_files_all_discovered(self):
        """Regression shape for the real incident: several files across
        different old dates, none yet in the registry, must all surface in
        one sweep once the fix is applied -- not just the newest one."""
        dates_ago = [45, 40, 15, 14, 1]
        for i, days in enumerate(dates_ago):
            f = self.folder / f"file_{i}.m4a"
            _touch_with_mtime(f, b"x" * 1000, datetime.now(timezone.utc) - timedelta(days=days))

        registry = {"processed_ids": {}}
        since = datetime.now(timezone.utc) - timedelta(hours=24)

        found = ci._find_new_files(self._source(), registry, since)
        self.assertEqual(len(found), 5)

    def test_tiny_files_still_filtered_as_noise(self):
        """The existing size<10-byte noise filter must be unaffected by
        removing the time-window filter."""
        tiny = self.folder / "tiny.m4a"
        _touch_with_mtime(tiny, b"x", datetime.now(timezone.utc) - timedelta(days=30))

        registry = {"processed_ids": {}}
        since = datetime.now(timezone.utc) - timedelta(hours=24)

        found = ci._find_new_files(self._source(), registry, since)
        self.assertEqual(found, [])

    def test_missing_folder_returns_empty_not_error(self):
        source = {"id": "x", "folder": str(self.folder / "does-not-exist"), "patterns": ["**/*.m4a"]}
        registry = {"processed_ids": {}}
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        self.assertEqual(ci._find_new_files(source, registry, since), [])


if __name__ == "__main__":
    unittest.main()
