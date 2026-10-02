"""
test_render_day_ahead.py

RB-2026-08-25: _render_day_ahead's own "is this additive beyond My
Priorities" dedup unconditionally skips a non-calendar-event day_ahead item
if its bare title matches anything in sections["my_priorities"] (no
additive-content exception for that code path, unlike the calendar_event
loop just above it). Before the source-level fix in daily_brief.py's
_compute_my_priorities (which used to copy every [PREP] item from day_ahead
verbatim into my_priorities), this meant Day Ahead's rich content -- network
connections, related threads, the actual reason a meeting needs prep --
was suppressed for EVERY event it computed, every day: my_priorities always
had an exact-title duplicate, so the "already covered" check always fired.

No test existed for _render_day_ahead at all before this file. This locks
in the fix: My Priorities no longer duplicates day_ahead's titles, so Day
Ahead's additive content now actually reaches the reader.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_daily_brief as rdb  # noqa: E402


class TestDayAheadRendersFullContentNoLongerBlockedByMyPriorities(unittest.TestCase):
    def test_prep_item_with_network_connections_renders_when_my_priorities_empty(self):
        """The real, current shape: My Priorities no longer contains a
        matching title, so Day Ahead's own network-connection detail must
        actually reach the reader."""
        sections = {
            "day_ahead": [
                {"title": "[PREP] Territory Planning Sync",
                 "summary": "Time: 2026-08-25T14:00. Network connections: Jane Doe. "
                            "Related threads: T-active-thread-1",
                 "extras": {"event_title": "Territory Planning Sync"}},
            ],
            "my_priorities": [],  # RB-2026-08-25: no longer duplicated here
        }
        out = rdb._render_day_ahead(sections, date(2026, 8, 25))
        self.assertIn("Territory Planning Sync", out)
        self.assertIn("Network connections: Jane Doe", out)

    def test_still_suppressed_if_something_else_genuinely_duplicates_the_title(self):
        """The dedup logic itself is still correct and still needed for
        genuine duplicates from other sources -- only the specific
        my-priorities-always-copies-day-ahead case was the bug."""
        sections = {
            "day_ahead": [
                {"title": "[PREP] Territory Planning Sync",
                 "summary": "",  # no additive detail at all
                 "extras": {"event_title": "Territory Planning Sync"}},
            ],
            "my_priorities": [
                {"title": "Territory Planning Sync"},
            ],
        }
        out = rdb._render_day_ahead(sections, date(2026, 8, 25))
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
