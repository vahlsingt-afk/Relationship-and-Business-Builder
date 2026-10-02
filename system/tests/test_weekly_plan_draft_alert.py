"""
test_weekly_plan_draft_alert.py

Regression coverage for the weekly-plan staleness gap: weekly_plan_generator.py
writes a draft to weekly_plan_draft.json pending human confirmation (a
propose-then-confirm flow, deliberately not auto-promoted), but nothing
previously told the user a draft existed. Observed in production: an active
plan for week_of 2026-06-22 (generated 2026-06-28) was still being rendered
during the week of 2026-06-29 — the GP transition week — while a draft for
that week had been sitting confirmed-and-ignored since the day it was
generated.

_render_weekly_plan_draft_alert() is a pure function (no file I/O), so this
tests it directly against constructed active/draft dicts.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_daily_brief as rdb  # noqa: E402


class TestWeeklyPlanDraftAlert(unittest.TestCase):
    def test_newer_draft_than_active_produces_alert(self):
        active = {"week_of": "2026-06-22", "status": "active"}
        draft = {
            "week_of": "2026-06-29",
            "status": "draft_pending_confirmation",
            "generated_at": "2026-06-29T12:04:51Z",
        }
        alert = rdb._render_weekly_plan_draft_alert(active, draft)
        self.assertIn("2026-06-29", alert)
        self.assertIn("2026-06-22", alert)
        # RB-DEFECT-2026-08-17: the raw CLI invocation this used to assert
        # ("weekly_plan_generator.py --confirm") was a reader-facing internal-
        # instruction leak, fixed the same class of way as the meeting-prep
        # and mutations.py leaks -- this assertion encoded the superseded
        # behavior and needed updating, not the code reverting.
        self.assertIn('Say "confirm the weekly plan draft"', alert)
        self.assertNotIn("python3", alert)

    def test_draft_matching_active_week_produces_no_alert(self):
        active = {"week_of": "2026-06-29", "status": "active"}
        draft = {
            "week_of": "2026-06-29",
            "status": "draft_pending_confirmation",
            "generated_at": "2026-06-29T12:04:51Z",
        }
        self.assertEqual(rdb._render_weekly_plan_draft_alert(active, draft), "")

    def test_no_draft_produces_no_alert(self):
        active = {"week_of": "2026-06-22", "status": "active"}
        self.assertEqual(rdb._render_weekly_plan_draft_alert(active, None), "")

    def test_already_confirmed_draft_produces_no_alert(self):
        """A draft that's already been promoted (status flipped elsewhere)
        shouldn't still nag — only draft_pending_confirmation triggers this."""
        active = {"week_of": "2026-06-29", "status": "active"}
        draft = {"week_of": "2026-06-29", "status": "confirmed"}
        self.assertEqual(rdb._render_weekly_plan_draft_alert(active, draft), "")

    def test_no_active_plan_still_flags_pending_draft(self):
        draft = {
            "week_of": "2026-06-29",
            "status": "draft_pending_confirmation",
            "generated_at": "2026-06-29T12:04:51Z",
        }
        alert = rdb._render_weekly_plan_draft_alert(None, draft)
        self.assertIn("2026-06-29", alert)
        self.assertIn("none", alert)


class TestWeeklyPlanDraftAlertEscalation(unittest.TestCase):
    """RB-DEFECT-067: a Monday draft was still unconfirmed on Wednesday --
    the whole week's outcome-alignment scoring ran against the prior week's
    plan for 3 days with the warning reading identically the entire time.
    This alone can't force adoption (a product-policy decision, deliberately
    not decided unilaterally here -- see the defect report's "Monday
    adoption contract" question), but the warning must escalate rather than
    stay static while the underlying problem compounds."""

    ACTIVE = {"week_of": "2026-08-10", "status": "active"}
    DRAFT = {
        "week_of": "2026-08-17",
        "status": "draft_pending_confirmation",
        "generated_at": "2026-08-17T10:02:48Z",
    }

    def test_same_day_uses_standard_warning(self):
        from datetime import date
        alert = rdb._render_weekly_plan_draft_alert(self.ACTIVE, self.DRAFT, today=date(2026, 8, 17))
        self.assertIn("⚠ Weekly Plan Draft Awaiting Confirmation", alert)
        self.assertNotIn("STILL Unconfirmed", alert)

    def test_next_day_still_uses_standard_warning(self):
        """1 day pending isn't yet escalated -- the threshold is >=2 days,
        so a same-morning-next-day check (e.g. Tuesday for a Monday draft)
        doesn't read as alarming as a multi-day miss."""
        from datetime import date
        alert = rdb._render_weekly_plan_draft_alert(self.ACTIVE, self.DRAFT, today=date(2026, 8, 18))
        self.assertNotIn("STILL Unconfirmed", alert)

    def test_two_plus_days_pending_escalates(self):
        from datetime import date
        alert = rdb._render_weekly_plan_draft_alert(self.ACTIVE, self.DRAFT, today=date(2026, 8, 19))
        self.assertIn("🛑 Weekly Plan Draft STILL Unconfirmed (2 days)", alert)
        self.assertIn("2 days of daily recommendations have now been scored", alert)

    def test_escalation_day_count_keeps_growing(self):
        from datetime import date
        alert = rdb._render_weekly_plan_draft_alert(self.ACTIVE, self.DRAFT, today=date(2026, 8, 21))
        self.assertIn("STILL Unconfirmed (4 days)", alert)

    def test_no_today_param_falls_back_to_standard_warning(self):
        """Backward-compatible: callers that don't pass today= (or can't
        parse generated_at) get the original non-escalating warning, never
        a crash."""
        alert = rdb._render_weekly_plan_draft_alert(self.ACTIVE, self.DRAFT)
        self.assertIn("⚠ Weekly Plan Draft Awaiting Confirmation", alert)
        self.assertNotIn("STILL Unconfirmed", alert)


if __name__ == "__main__":
    unittest.main()
