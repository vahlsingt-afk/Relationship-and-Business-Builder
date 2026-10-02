"""
test_watchlist_delta_trust.py

Originally covered two related trust issues in the watchlist numbers:

1. Section F ("F: Watchlist") said "100 entities scanned. No material
   developments." on a brief whose own Watchlist Delta Summary reported
   Escalated: 2 | New: 14 | Relevant: 38 alongside that same "No change: 100"
   — i.e. 54 entities *did* have activity, but Section F's wording implied
   100 was the entire scanned universe and nothing happened at all. Fixed at
   the time by having the "No Change" count say so explicitly ("100 other
   entities unchanged since last scan").

2. "Watchlist Delta Summary" reported bare counts ("Escalated: 2") with no
   way to see which companies, and its recommended_action pointed at a
   "Watchlist Intelligence" section that isn't rendered anywhere in the
   brief. The summary now names the escalated/new-activity entities inline
   and the recommended_action is self-contained.

RB-DEFECT-2026-07-27 superseded fix #1 above: Todd explicitly asked for the
"N other entities unchanged since last scan" count (and the "Relevant
Activity" name rollup alongside it) to be removed entirely — he doesn't need
the scanned/unchanged roster in the brief and can ask who's on the watchlist
directly if he wants it. Section F now surfaces only escalations, genuinely
new activity, and price movers.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402

# _render_watchlist_rollup reads the live market_signals_earnings.jsonl file
# for the given `today` with no injection point. Pin to a date far outside
# any realistic test data so results are deterministic regardless of what's
# actually in that file on the day this test happens to run.
_NO_PRICE_DATA_DATE = date(2099, 1, 1)


class TestWatchlistRollupWording(unittest.TestCase):
    def test_no_change_only_data_shows_quiet_day_not_a_count(self):
        sections = {
            "watchlist_intelligence": [
                {"extras": {"watchlist_status": "No Change", "no_change_count": 100}},
            ]
        }
        out = rib._render_watchlist_rollup(sections, today=_NO_PRICE_DATA_DATE)
        self.assertNotIn("other entities unchanged", out)
        self.assertNotIn("entities scanned. No material developments", out)
        self.assertIn("No escalations or new watchlist activity today.", out)

    def test_price_movers_still_show_without_a_no_change_count(self):
        sections = {
            "watchlist_intelligence": [
                {"extras": {"watchlist_status": "No Change", "no_change_count": 100}},
            ]
        }
        import json
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            system_dir = Path(tmp)
            (system_dir / "inbox").mkdir()
            signal = {
                "_price_watch": True,
                "ticker": "PAR",
                "company": "PAR Technology",
                "title": "PAR Technology (PAR) — 📈 ↑5.0%",
                "published_at": _NO_PRICE_DATA_DATE.isoformat(),
                "pct_change": 5.0,
            }
            (system_dir / "inbox" / "market_signals_earnings.jsonl").write_text(
                json.dumps(signal) + "\n"
            )
            with patch.object(rib.core, "SYSTEM_DIR", system_dir):
                out = rib._render_watchlist_rollup(sections, today=_NO_PRICE_DATA_DATE)
        self.assertIn("PAR Technology", out)
        self.assertNotIn("other entities unchanged", out)

    def test_no_watchlist_data_at_all_distinct_from_quiet_day(self):
        out = rib._render_watchlist_rollup({}, today=_NO_PRICE_DATA_DATE)
        self.assertIn("No watchlist data.", out)


if __name__ == "__main__":
    unittest.main()
