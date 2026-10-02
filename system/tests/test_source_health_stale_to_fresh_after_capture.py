"""
test_source_health_stale_to_fresh_after_capture.py

Handoff acceptance test 8: "Source health changes from stale to fresh after
a successful late capture." refresh_sources._health_status() already had the
staleness-threshold comparison this needs (age_hours > threshold -> stale);
this test exists because nothing in the suite previously proved the exact
scenario the handoff describes end to end: a source stuck stale earlier in
the day, then a manual capture (e.g. GP Outlook, or a LinkedIn export) lands
and updates last_refreshed_at, and the NEXT health computation reports it
fresh again rather than staying falsely stale until the next scheduled run.

This is exactly what post_capture_cascade.py's source_health_recompute step
(refresh_sources.py --health-only) exists to guarantee gets re-run
immediately after a manual capture, instead of waiting for tomorrow's 4 AM
scan (see post_capture_cascade.py's module docstring and
system/tests/test_post_capture_cascade.py's REQUIRED_STAGES).
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import refresh_sources as rs  # noqa: E402


class TestSourceHealthStaleToFreshAfterCapture(unittest.TestCase):
    def test_source_reported_stale_before_capture(self):
        """A GP source last refreshed 30h ago (past the 26h email/calendar
        threshold) is reported stale -- this is the 'before' half of the
        handoff scenario (evidence: 'GP calendar was stale because the
        later manual GP capture did not trigger downstream reconstruction')."""
        now = datetime.now(timezone.utc)
        stale_last_refreshed = (now - timedelta(hours=30)).isoformat()
        status, reason = rs._health_status(
            rs.STATUS_REFRESHED, "calendar:global-payments", stale_last_refreshed, now,
        )
        self.assertEqual(status, rs.STATUS_STALE)
        self.assertIn("threshold", reason)

    def test_source_reported_fresh_after_capture_updates_last_refreshed_at(self):
        """The exact same source, immediately after a manual capture updates
        last_refreshed_at to just now, is reported fresh -- no waiting for
        the next scheduled scan."""
        now = datetime.now(timezone.utc)
        fresh_last_refreshed = (now - timedelta(minutes=5)).isoformat()
        status, reason = rs._health_status(
            rs.STATUS_REFRESHED, "calendar:global-payments", fresh_last_refreshed, now,
        )
        self.assertEqual(status, rs.STATUS_REFRESHED)
        self.assertIsNone(reason)

    def test_stale_to_fresh_transition_within_one_write_source_health_cycle(self):
        """End-to-end through write_source_health() itself (not just the
        pure _health_status() comparison): a source stale at generation time
        T1, recomputed at T2 after last_refreshed_at advances past T1,
        flips from stale to refreshed in the persisted health doc."""
        import tempfile
        from unittest.mock import patch

        tmp = Path(tempfile.mkdtemp(prefix="source_health_stale_fresh_test_"))
        health_path = tmp / "source_health.json"
        orig_health_path = rs.SOURCE_HEALTH_PATH
        rs.SOURCE_HEALTH_PATH = health_path
        try:
            now = datetime.now(timezone.utc)
            stale_ts = (now - timedelta(hours=30)).isoformat()
            fresh_ts = now.isoformat()

            with patch.object(rs, "_configured_health_sources",
                               return_value={"calendar:global-payments": {"tier": 1, "kind": "calendar"}}), \
                 patch.object(rs, "_item_count_from_inbox", return_value=5), \
                 patch.object(rs, "_last_refreshed_at", return_value=stale_ts):
                before = rs.write_source_health([{"source": "calendar:global-payments", "status": rs.STATUS_REFRESHED}])
            self.assertEqual(before["sources"]["calendar:global-payments"]["status"], rs.STATUS_STALE)

            with patch.object(rs, "_configured_health_sources",
                               return_value={"calendar:global-payments": {"tier": 1, "kind": "calendar"}}), \
                 patch.object(rs, "_item_count_from_inbox", return_value=6), \
                 patch.object(rs, "_last_refreshed_at", return_value=fresh_ts):
                after = rs.write_source_health([{"source": "calendar:global-payments", "status": rs.STATUS_REFRESHED}])
            self.assertEqual(after["sources"]["calendar:global-payments"]["status"], rs.STATUS_REFRESHED)
        finally:
            rs.SOURCE_HEALTH_PATH = orig_health_path


if __name__ == "__main__":
    unittest.main()
