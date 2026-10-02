"""
test_refresh_sources_market_feeds.py

RB-2026-08-24: market_source_feeds.py --fetch (the RSS trade-press feeder
that writes system/inbox/market_signals_feed.jsonl) was never called by
anything automated -- confirmed via the intelligence pipeline map research
pass -- even though market_signals.py already merges that exact file in.
This covers the fix: refresh_market_feeds() now runs before refresh_market()
whenever --market (or --all) is passed to refresh_sources.py.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import refresh_sources as rs  # noqa: E402


class TestRefreshMarketFeeds(unittest.TestCase):
    def test_refresh_market_feeds_calls_fetch_with_save_health(self):
        with patch.object(rs, "run", return_value=(0, "", "")) as mocked_run:
            result = rs.refresh_market_feeds("python3")

        mocked_run.assert_called_once()
        label, cmd = mocked_run.call_args.args
        self.assertIn("market_source_feeds.py", cmd[1])
        self.assertIn("--fetch", cmd)
        self.assertIn("--save-health", cmd)
        self.assertEqual(result["status"], rs.STATUS_REFRESHED)

    def test_refresh_market_feeds_reports_failed_status_on_nonzero_exit(self):
        with patch.object(rs, "run", return_value=(1, "", "boom")):
            result = rs.refresh_market_feeds("python3")
        self.assertEqual(result["status"], rs.STATUS_FAILED)

    def test_market_flag_calls_feeds_before_market_signals(self):
        """refresh_market_feeds() must run before refresh_market() so
        market_signals.py has a populated market_signals_feed.jsonl to
        merge in when it reads the inbox."""
        call_order: list[str] = []

        def _fake_run(label, cmd):
            call_order.append(Path(cmd[1]).name)
            return (0, "", "")

        with patch.object(rs, "run", side_effect=_fake_run), \
             patch.object(rs, "INBOX_DIR", ROOT / "system" / "inbox"), \
             patch("sys.argv", ["refresh_sources.py", "--market"]):
            rs.main()

        self.assertIn("market_source_feeds.py", call_order)
        # refresh_market() itself may skip-with-no-subprocess-call if
        # market_signals.json doesn't exist -- what matters here is that
        # IF market_signals.py was invoked, the feed fetch came first.
        if "market_signals.py" in call_order:
            self.assertLess(
                call_order.index("market_source_feeds.py"),
                call_order.index("market_signals.py"),
            )


class TestRefreshRestaurantTechWorkbook(unittest.TestCase):
    def _settings(self, path="/tmp/current-tech-stack.xlsx", enabled=True):
        return {
            "daily_briefing": {
                "source_refresh_contract": {
                    "restaurant_tech_workbook": {
                        "enabled": enabled,
                        "path": path,
                    }
                }
            }
        }

    def test_missing_configured_workbook_skips_without_subprocess(self):
        with patch.object(rs.core, "load_settings", return_value=self._settings("/tmp/no-such-workbook.xlsx")), \
             patch.object(rs, "run") as mocked_run:
            result = rs.refresh_restaurant_tech_workbook("python3")

        mocked_run.assert_not_called()
        self.assertEqual(result["status"], rs.STATUS_SKIPPED_NO_RAW_INPUT)
        self.assertEqual(result["source"], "restaurant_tech_workbook")

    def test_refresh_restaurant_tech_workbook_ingests_configured_path(self):
        workbook = ROOT / "system" / "tests" / "fixtures" / "current-tech-stack.xlsx"
        with patch.object(rs.core, "load_settings", return_value=self._settings(str(workbook))), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(rs, "run", return_value=(0, "", "")) as mocked_run:
            result = rs.refresh_restaurant_tech_workbook("python3")

        mocked_run.assert_called_once()
        label, cmd = mocked_run.call_args.args
        self.assertIn("tech_stack_workbook_sync.py", cmd[1])
        self.assertEqual(cmd[2], "ingest")
        self.assertEqual(cmd[3], str(workbook))
        self.assertIn("--confirm", cmd)
        self.assertEqual(result["status"], rs.STATUS_REFRESHED)

    def test_all_flag_includes_restaurant_tech_workbook(self):
        with patch.object(rs, "refresh_messages", return_value={"source": "messages", "status": rs.STATUS_REFRESHED}), \
             patch.object(rs, "refresh_calls", return_value={"source": "calls", "status": rs.STATUS_REFRESHED}), \
             patch.object(rs, "refresh_interaction_overlay", return_value={"source": "interaction_overlay", "status": rs.STATUS_REFRESHED}), \
             patch.object(rs, "refresh_email_or_calendar", return_value=[]), \
             patch.object(rs, "refresh_social", return_value=[]), \
             patch.object(rs, "refresh_earnings", return_value={"source": "earnings", "status": rs.STATUS_REFRESHED}), \
             patch.object(rs, "refresh_market_feeds", return_value={"source": "market_feeds", "status": rs.STATUS_REFRESHED}), \
             patch.object(rs, "refresh_market", return_value={"source": "market_signals", "status": rs.STATUS_REFRESHED}), \
             patch.object(rs, "refresh_watch_scan", return_value={"source": "watch_scan", "status": rs.STATUS_REFRESHED}), \
             patch.object(rs, "refresh_ecosystem_intelligence", return_value={"source": "ecosystem_intelligence", "status": rs.STATUS_REFRESHED}), \
             patch.object(rs, "refresh_restaurant_tech_workbook", return_value={"source": "restaurant_tech_workbook", "status": rs.STATUS_REFRESHED}) as mocked_workbook, \
             patch("sys.argv", ["refresh_sources.py", "--all"]):
            rc = rs.main()

        self.assertEqual(rc, 0)
        mocked_workbook.assert_called_once()


if __name__ == "__main__":
    unittest.main()
