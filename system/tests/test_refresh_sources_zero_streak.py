"""
test_refresh_sources_zero_streak.py

RB-DEFECT-2026-09-18 (handoff §"Source-health and self-healing", acceptance
test 9: "A repeated-zero source eventually alerts; a normal single zero
remains suppressed"): refresh_sources.write_source_health() previously had
no concept of "ran fine but returned nothing" distinct from "genuinely
quiet day" -- a source could return 0 items forever and never be flagged,
because item_count wasn't compared against any history. This covers the new
consecutive_zero_runs / zero_streak_alert fields and their consumption in
source_health_report.build_report().
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import refresh_sources as rs  # noqa: E402
import source_health_report as shr  # noqa: E402


class TestZeroStreakTracking(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="refresh_sources_zero_streak_test_"))
        self.health_path = self.tmp / "source_health.json"
        self._orig_health_path = rs.SOURCE_HEALTH_PATH
        rs.SOURCE_HEALTH_PATH = self.health_path

    def tearDown(self):
        rs.SOURCE_HEALTH_PATH = self._orig_health_path

    def _run_one_cycle(self, *, item_count: int) -> dict:
        fresh_now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with patch.object(rs, "_configured_health_sources",
                           return_value={"test_source": {"tier": 3, "kind": "market_signals"}}), \
             patch.object(rs, "_item_count_from_inbox", return_value=item_count), \
             patch.object(rs, "_last_refreshed_at", return_value=fresh_now):
            return rs.write_source_health([{"source": "test_source", "status": rs.STATUS_REFRESHED}])

    def test_single_zero_run_is_not_alerted(self):
        health = self._run_one_cycle(item_count=0)
        row = health["sources"]["test_source"]
        self.assertEqual(row["consecutive_zero_runs"], 1)
        self.assertFalse(row["zero_streak_alert"])

    def test_repeated_zero_eventually_alerts(self):
        for _ in range(rs.ZERO_STREAK_ALERT_THRESHOLD - 1):
            health = self._run_one_cycle(item_count=0)
            self.assertFalse(health["sources"]["test_source"]["zero_streak_alert"])

        health = self._run_one_cycle(item_count=0)
        row = health["sources"]["test_source"]
        self.assertEqual(row["consecutive_zero_runs"], rs.ZERO_STREAK_ALERT_THRESHOLD)
        self.assertTrue(row["zero_streak_alert"])

    def test_nonzero_run_resets_the_streak(self):
        self._run_one_cycle(item_count=0)
        self._run_one_cycle(item_count=0)
        health = self._run_one_cycle(item_count=5)
        row = health["sources"]["test_source"]
        self.assertEqual(row["consecutive_zero_runs"], 0)
        self.assertFalse(row["zero_streak_alert"])

    def test_source_health_report_suppresses_single_zero_but_surfaces_streak(self):
        for _ in range(rs.ZERO_STREAK_ALERT_THRESHOLD - 1):
            self._run_one_cycle(item_count=0)

        self._orig_shr_path = shr.HEALTH_PATH
        shr.HEALTH_PATH = self.health_path
        try:
            report = shr.build_report()
            self.assertNotIn("zero_streak", report["groups"])  # below threshold: suppressed

            self._run_one_cycle(item_count=0)  # crosses threshold
            report = shr.build_report()
            self.assertIn("zero_streak", report["groups"])
            self.assertEqual(report["groups"]["zero_streak"][0]["source"], "test_source")
            self.assertEqual(
                report["groups"]["zero_streak"][0]["consecutive_zero_runs"],
                rs.ZERO_STREAK_ALERT_THRESHOLD,
            )
        finally:
            shr.HEALTH_PATH = self._orig_shr_path


if __name__ == "__main__":
    unittest.main()
