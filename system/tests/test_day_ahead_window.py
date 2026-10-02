"""
test_day_ahead_window.py

Regression coverage: _compute_day_ahead's docstring promises "events today
and the next 3 days," but it pulled from calendar_overlay's "this_week"
bucket (today+2 through today+7 -- see rb_core.calendar_overlay's week_end)
via this_week_events[:3] with no date filter. Taking the first 3
chronologically can include events much further out than 3 days if
nothing closer exists in that window.

Confirmed live: "SCN Guest Invitation Global Connections," a real calendar
event dated 6 days out, surfaced as a "[PREP]" priority item as if it
needed prep today. At the time, _compute_my_priorities also pulled its
calendar items directly from sections["day_ahead"], so this bug reached
both Day Ahead and My Priorities at once.

Fixed by filtering this_week_events to only today+2/today+3 before slicing,
matching the stated "next 3 days" contract (today=day0, tomorrow=day1).

RB-2026-08-25: _compute_my_priorities no longer pulls day_ahead items at
all (see test_my_priorities_no_longer_duplicates_day_ahead below) --
Decision Queue and Day Ahead itself already cover meeting-prep content
with more detail than a bare title line ever added here, and the same
event appearing under two independently-generated titles in My Priorities
was a real, separately-confirmed duplication bug.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db  # noqa: E402

_TODAY = date(2026, 7, 9)


def _event(title: str, days_out: int) -> dict:
    start = (_TODAY + timedelta(days=days_out)).isoformat() + "T09:00:00-05:00"
    return {"title": title, "start": start, "attendees": [], "description": ""}


class TestDayAheadWindow(unittest.TestCase):
    def test_event_six_days_out_excluded(self):
        """The exact live bug: a real event dated 6 days out must not be
        treated as needing prep today."""
        report = {
            "today": _TODAY.isoformat(),
            "calendar": {
                "today": [],
                "tomorrow": [],
                "this_week": [_event("SCN Guest Invitation Global Connections", 6)],
            },
            "baseline": [],
            "all_threads": [],
        }
        items = db._compute_day_ahead(report, {})
        titles = " ".join(i.get("title", "") for i in items)
        self.assertNotIn("SCN Guest Invitation", titles)

    def test_event_two_days_out_included(self):
        report = {
            "today": _TODAY.isoformat(),
            "calendar": {
                "today": [],
                "tomorrow": [],
                "this_week": [_event("Territory Planning Sync", 2)],
            },
            "baseline": [],
            "all_threads": [],
        }
        items = db._compute_day_ahead(report, {})
        titles = " ".join(i.get("title", "") for i in items)
        self.assertIn("Territory Planning Sync", titles)

    def test_event_three_days_out_included(self):
        report = {
            "today": _TODAY.isoformat(),
            "calendar": {
                "today": [],
                "tomorrow": [],
                "this_week": [_event("Quarterly Review", 3)],
            },
            "baseline": [],
            "all_threads": [],
        }
        items = db._compute_day_ahead(report, {})
        titles = " ".join(i.get("title", "") for i in items)
        self.assertIn("Quarterly Review", titles)

    def test_event_four_days_out_excluded(self):
        report = {
            "today": _TODAY.isoformat(),
            "calendar": {
                "today": [],
                "tomorrow": [],
                "this_week": [_event("Far Out Meeting", 4)],
            },
            "baseline": [],
            "all_threads": [],
        }
        items = db._compute_day_ahead(report, {})
        titles = " ".join(i.get("title", "") for i in items)
        self.assertNotIn("Far Out Meeting", titles)


class TestMyPrioritiesNoLongerDuplicatesDayAhead(unittest.TestCase):
    def test_my_priorities_no_longer_duplicates_day_ahead(self):
        """RB-2026-08-25: My Priorities used to pull day_ahead's [PREP]
        items directly ("CALENDAR PRIORITIES") -- a stripped-down,
        title-only duplicate of what Day Ahead (a dedicated section
        rendered right after) and Decision Queue (with a real action)
        already covered. Removed at the source: _compute_my_priorities no
        longer reads sections["day_ahead"] at all."""
        sections = {
            "day_ahead": [
                {"title": "[PREP] Territory Planning Sync",
                 "extras": {"event_title": "Territory Planning Sync"}},
            ],
        }
        items = db._compute_my_priorities(sections, {})
        titles = " ".join(i.get("title", "") for i in items)
        self.assertNotIn("Territory Planning Sync", titles)
        self.assertFalse(any((i.get("extras") or {}).get("priority_category") == "calendar" for i in items))


if __name__ == "__main__":
    unittest.main()
