"""
test_weekly_plan_summary_live_status.py

Regression coverage: on Mondays, "This Week's Plan" is rendered by
_render_weekly_plan_summary, which printed each outcome's frozen
success_criteria text (and a frozen risk description) verbatim with no
cross-check against live loop/thread state. _render_weekly_plan_progress
(Tue-Fri) and _render_weekly_plan_review (Sat) both already cross-check via
_score_outcome_progress, so a loop/thread-backed outcome reads its live
status on those days — but on Monday the same outcome kept reading as
"Resolve all 22 overdue loops" or "Close or defer Patrick Nelson / Matrix"
regardless of what had actually closed, because nothing here ever consulted
loop_ledger.md/active_threads.yaml.

Observed live: L-2026-05-26-002 (Patrick Nelson / Matrix Software Solutions)
closed 2026-07-03 in both loop_ledger.md and active_threads.yaml, and 4 of
the 22 loops tracked by "Clear the overdue loop backlog" had since closed —
but the 2026-06-28-drafted weekly_plan.json's static text never reflected
either fact, so the Monday brief kept telling Todd to do things he'd
already done and overstating an already-shrinking backlog.

Fix:
- _render_weekly_plan_summary now takes `sections` and, for any outcome
  backed by linked_loop_ids/linked_opportunity_ids, renders the live
  _score_outcome_progress detail (a real "N/M resolved" count, or the
  thread's actual close_reason) instead of the frozen success_criteria text.
  Manual (non-data-backed) outcomes still show their criteria text, since
  there's no live signal to replace it with.
- _score_outcome_progress now prefers a closed thread's close_reason over
  its pre-closure current_state snapshot, which used to make a closed
  thread's detail line read as if it were still open (e.g. "Thread closed:
  Two open loops: ...").
- The top "Risk" line's "N overdue loops" text (source: loop_ledger) is now
  swapped for the live loops_overdue count from _get_comm_context, the same
  canonical figure the rest of the brief already uses.
"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_daily_brief as rdb  # noqa: E402


def _loop_backed_plan():
    return {
        "week_of": "2026-06-22",
        "outcomes": [
            {
                "id": "outcome-loop-clearance",
                "title": "Clear the overdue loop backlog",
                "success_criteria": "Resolve all 22 overdue loops.",
                "linked_loop_ids": ["L-1", "L-2", "L-3", "L-4"],
                "portfolio_allocation_pct": 25.0,
            }
        ],
        "forcing_functions": [],
        "risks": [
            {
                "id": "risk-loop-debt-carry",
                "description": "22 overdue loops unresolved. Clear before GP week 1.",
                "severity": "high",
                "source": "loop_ledger",
            }
        ],
    }


def _thread_backed_plan():
    return {
        "week_of": "2026-06-22",
        "outcomes": [
            {
                "id": "outcome-patrick-nelson-close",
                "title": "Close or defer Patrick Nelson / Matrix",
                "success_criteria": "Bring Matrix Software Solutions to a defined conclusion this week.",
                "linked_opportunity_ids": ["T-2026-05-patrick-nelson"],
                "portfolio_allocation_pct": 10.0,
            }
        ],
        "forcing_functions": [],
        "risks": [],
    }


def _manual_plan():
    return {
        "week_of": "2026-06-22",
        "outcomes": [
            {
                "id": "outcome-notify-inner-circle",
                "title": "Notify inner circle of GP role",
                "success_criteria": "Personally reach out to top 10 inner-circle contacts.",
                "portfolio_allocation_pct": 20.0,
            }
        ],
        "forcing_functions": [],
        "risks": [],
    }


class TestWeeklyPlanSummaryLiveStatus(unittest.TestCase):
    def test_loop_outcome_shows_live_count_not_stale_criteria(self):
        loop_statuses = {"L-1": "closed", "L-2": "closed", "L-3": "open", "L-4": "open"}
        with patch.object(rdb, "_parse_loop_statuses", return_value=loop_statuses), \
             patch.object(rdb.core, "load_active_threads", return_value=[]):
            out = rdb._render_weekly_plan_summary(_loop_backed_plan(), sections={})

        self.assertIn("2/4 loops resolved — 2 remaining", out)
        self.assertNotIn("Resolve all 22 overdue loops.", out)

    def test_all_loops_closed_shows_resolved_checkmark(self):
        loop_statuses = {"L-1": "closed", "L-2": "closed", "L-3": "closed", "L-4": "closed"}
        with patch.object(rdb, "_parse_loop_statuses", return_value=loop_statuses), \
             patch.object(rdb.core, "load_active_threads", return_value=[]):
            out = rdb._render_weekly_plan_summary(_loop_backed_plan(), sections={})

        self.assertIn("✅", out)
        self.assertIn("All 4 loops resolved", out)

    def test_closed_thread_shows_close_reason_not_stale_state(self):
        thread = {
            "id": "T-2026-05-patrick-nelson",
            "status": "closed",
            "current_state": "Two open loops: L-2026-05-08-019 (schedule) and L-2026-05-08-020 (prep).",
            "close_reason": "Closed 2026-07-03 — Todd told the Custom GPT to close the Patrick Nelson loop on 2026-07-02, which claimed success but never actually persisted anything.",
        }
        with patch.object(rdb, "_parse_loop_statuses", return_value={}), \
             patch.object(rdb.core, "load_active_threads", return_value=[thread]):
            out = rdb._render_weekly_plan_summary(_thread_backed_plan(), sections={})

        self.assertIn("✅", out)
        self.assertIn("Closed 2026-07-03", out)
        self.assertNotIn("Two open loops", out)
        self.assertNotIn("Bring Matrix Software Solutions to a defined conclusion", out)

    def test_manual_outcome_still_shows_criteria_text(self):
        with patch.object(rdb, "_parse_loop_statuses", return_value={}), \
             patch.object(rdb.core, "load_active_threads", return_value=[]):
            out = rdb._render_weekly_plan_summary(_manual_plan(), sections={})

        self.assertIn("Personally reach out to top 10 inner-circle contacts.", out)

    def test_active_threads_risk_dropped_once_its_thread_closes(self):
        """RB-DEFECT-2026-07-27: a risk-decay-{thread_id} risk (e.g. "Perfect
        Hire — 31d since last contact") kept rendering days after Perfect
        Hire's underlying thread was closed, because -- unlike the outcomes
        above -- risks were never cross-checked against live thread status."""
        plan = {
            "week_of": "2026-07-27",
            "outcomes": [{
                "id": "outcome-placeholder",
                "title": "Placeholder outcome",
                "success_criteria": "Some manual criteria.",
                "portfolio_allocation_pct": 10.0,
            }],
            "forcing_functions": [],
            "risks": [
                {
                    "id": "risk-decay-T-2026-06-perfect-hire-advisory",
                    "title": "Opportunity momentum decay: Perfect Hire",
                    "description": "Perfect Hire — 31d since last contact. Without action this week, momentum risk increases.",
                    "severity": "high",
                    "source": "active_threads",
                },
            ],
        }
        closed_thread = {"id": "T-2026-06-perfect-hire-advisory", "status": "closed"}
        with patch.object(rdb, "_parse_loop_statuses", return_value={}), \
             patch.object(rdb.core, "load_active_threads", return_value=[closed_thread]):
            out = rdb._render_weekly_plan_summary(plan, sections={})

        self.assertNotIn("Perfect Hire", out)
        self.assertNotIn("**Risk:**", out)

    def test_active_threads_risk_still_shown_while_thread_open(self):
        plan = {
            "week_of": "2026-07-27",
            "outcomes": [{
                "id": "outcome-placeholder",
                "title": "Placeholder outcome",
                "success_criteria": "Some manual criteria.",
                "portfolio_allocation_pct": 10.0,
            }],
            "forcing_functions": [],
            "risks": [
                {
                    "id": "risk-decay-T-2026-06-perfect-hire-advisory",
                    "title": "Opportunity momentum decay: Perfect Hire",
                    "description": "Perfect Hire — 31d since last contact. Without action this week, momentum risk increases.",
                    "severity": "high",
                    "source": "active_threads",
                },
            ],
        }
        open_thread = {"id": "T-2026-06-perfect-hire-advisory", "status": "open"}
        with patch.object(rdb, "_parse_loop_statuses", return_value={}), \
             patch.object(rdb.core, "load_active_threads", return_value=[open_thread]):
            out = rdb._render_weekly_plan_summary(plan, sections={})

        self.assertIn("**Risk:** Perfect Hire — 31d since last contact", out)

    def test_risk_line_uses_live_overdue_count_not_stale_plan_number(self):
        sections = {
            "communication_intelligence": [
                {"summary": "Loops overdue: 18. Emails awaiting your response: 3."}
            ]
        }
        with patch.object(rdb, "_parse_loop_statuses", return_value={}), \
             patch.object(rdb.core, "load_active_threads", return_value=[]):
            out = rdb._render_weekly_plan_summary(_loop_backed_plan(), sections=sections)

        self.assertIn("**Risk:** 18 overdue loops unresolved.", out)
        self.assertNotIn("22 overdue loops unresolved", out)


class TestWeeklyPlanSummaryStaleGuard(unittest.TestCase):
    """RB-DEFECT-067 (product decision, 2026-08-20): "This Week's Plan"
    header is misleading on a Monday morning before that day's draft has
    been confirmed -- `plan` is still last week's, so labeling it "This
    Week's" states something false. Todd's call: still send the brief, but
    stop implying scoring/outcomes apply to a week they don't."""

    def test_stale_plan_on_monday_suppresses_outcomes_shows_confirm_prompt(self):
        from datetime import date
        stale_plan = {
            "week_of": "2026-08-10", "status": "active",
            "outcomes": [{"title": "Old outcome", "portfolio_allocation_pct": 100}],
        }
        out = rdb._render_weekly_plan_summary(stale_plan, sections={}, target_date=date(2026, 8, 17))
        self.assertIn("Still showing the plan for week of **2026-08-10**", out)
        self.assertNotIn("Old outcome", out)

    def test_current_plan_on_monday_renders_normally(self):
        from datetime import date
        current_plan = {
            "week_of": "2026-08-17", "status": "active",
            "outcomes": [{"title": "Fresh outcome", "portfolio_allocation_pct": 100}],
        }
        with patch.object(rdb, "_parse_loop_statuses", return_value={}), \
             patch.object(rdb.core, "load_active_threads", return_value=[]):
            out = rdb._render_weekly_plan_summary(
                current_plan, sections={}, target_date=date(2026, 8, 17),
            )
        self.assertNotIn("Still showing the plan for week of", out)

    def test_no_target_date_skips_staleness_check_entirely(self):
        """Backward compatible: callers that don't pass target_date= (the
        parameter didn't exist before this defect) get the original
        behavior, never a crash or an unexpected suppression."""
        stale_plan = {
            "week_of": "2026-08-10", "status": "active",
            "outcomes": [{"title": "Old outcome", "portfolio_allocation_pct": 100}],
        }
        with patch.object(rdb, "_parse_loop_statuses", return_value={}), \
             patch.object(rdb.core, "load_active_threads", return_value=[]):
            out = rdb._render_weekly_plan_summary(stale_plan, sections={})
        self.assertNotIn("Still showing the plan for week of", out)


class TestWeeklyPlanProgressStaleGuard(unittest.TestCase):
    """Same guard, Tue-Fri path (_render_weekly_plan_progress)."""

    def test_stale_plan_on_wednesday_pauses_scoring(self):
        from datetime import date
        stale_plan = {
            "week_of": "2026-08-10", "status": "active",
            "outcomes": [{"title": "Old outcome", "portfolio_allocation_pct": 100}],
        }
        out = rdb._render_weekly_plan_progress(stale_plan, date(2026, 8, 19), sections={})
        self.assertIn("Scoring paused", out)
        self.assertIn("week of **2026-08-10**", out)
        self.assertNotIn("Old outcome", out)

    def test_current_plan_on_wednesday_scores_normally(self):
        from datetime import date
        current_plan = {
            "week_of": "2026-08-17", "status": "active",
            "outcomes": [{"title": "Fresh outcome", "portfolio_allocation_pct": 100}],
        }
        with patch.object(rdb, "_parse_loop_statuses", return_value={}), \
             patch.object(rdb.core, "load_active_threads", return_value=[]):
            out = rdb._render_weekly_plan_progress(
                current_plan, date(2026, 8, 19), sections={},
            )
        self.assertNotIn("Scoring paused", out)
        self.assertIn("## Weekly Plan — Progress", out)


class TestMondayPlanDecisionGate(unittest.TestCase):
    """RB-DEFECT-067 (product decision, 2026-08-20): Todd chose to keep
    propose-then-confirm (not auto-adopt), but asked Monday's confirmation
    be made unavoidable rather than an easy-to-scroll-past line item."""

    ACTIVE = {
        "week_of": "2026-08-10", "status": "active",
        "generated_at": "2026-08-11T14:00:00Z",
    }
    DRAFT = {
        "week_of": "2026-08-17", "status": "draft_pending_confirmation",
        "generated_at": "2026-08-17T10:02:48Z",
    }

    def test_monday_with_pending_draft_produces_blocking_framed_gate(self):
        from datetime import date
        gate = rdb._render_monday_plan_decision_gate(self.ACTIVE, self.DRAFT, date(2026, 8, 17))
        self.assertIn("DECISION REQUIRED", gate)
        self.assertIn("2026-08-17", gate)
        self.assertIn("2026-08-10", gate)
        self.assertIn('confirm the weekly plan draft', gate)

    def test_generated_date_describes_active_plan_not_draft(self):
        """RB-DEFECT (2026-09-14): live incident -- this line read
        draft.get("generated_at") but used it in the sentence describing when
        the ACTIVE plan was generated, so the brief said the week-of-08-10
        plan was "generated 2026-08-17" (the draft's own generation date,
        copied from the sibling _render_weekly_plan_draft_alert where that
        reuse is correct) instead of its real 2026-08-11 generation date."""
        from datetime import date
        gate = rdb._render_monday_plan_decision_gate(self.ACTIVE, self.DRAFT, date(2026, 8, 17))
        self.assertIn("generated 2026-08-11", gate)
        self.assertNotIn("generated 2026-08-17", gate)

    def test_non_monday_never_produces_gate(self):
        from datetime import date
        for d in (date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20),
                  date(2026, 8, 21), date(2026, 8, 22), date(2026, 8, 23)):
            self.assertEqual(rdb._render_monday_plan_decision_gate(self.ACTIVE, self.DRAFT, d), "")

    def test_monday_with_no_pending_draft_produces_no_gate(self):
        from datetime import date
        self.assertEqual(rdb._render_monday_plan_decision_gate(self.ACTIVE, None, date(2026, 8, 17)), "")

    def test_monday_with_draft_already_matching_active_week_produces_no_gate(self):
        from datetime import date
        active_this_week = {"week_of": "2026-08-17", "status": "active"}
        already_confirmed = {"week_of": "2026-08-17", "status": "confirmed"}
        self.assertEqual(
            rdb._render_monday_plan_decision_gate(active_this_week, already_confirmed, date(2026, 8, 17)),
            "",
        )


if __name__ == "__main__":
    unittest.main()
