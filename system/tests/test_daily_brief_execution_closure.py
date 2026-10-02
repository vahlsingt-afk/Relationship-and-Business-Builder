"""
test_daily_brief_execution_closure.py — RB 9.20 Execution Closure tests (T4).

Tests:
  T4a: every overdue loop gets an execution option
  T4b: every crossing without a loop gets an open_loop option
  T4c: action field is from the allowed enum
  T4d: write-like actions have requires_confirmation=True
  T4e: target and reason fields are non-empty
  T4f: LinkedIn outreach items produce open_outreach_loop options
"""
from __future__ import annotations
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import cos_judgment as cj
from cos_judgment import EXECUTION_ACTIONS, WRITE_ACTIONS

TODAY = date(2026, 5, 28)


def _loop(loop_id: str, party: str, target: date, bucket: str = "overdue") -> tuple[str, dict]:
    return bucket, {
        "id": loop_id,
        "party": party,
        "description": f"Follow up with {party}",
        "target": target.isoformat(),
        "closed": False,
    }


def _crossing(name: str, tier: str = "inner", overage: int = 35) -> dict:
    return {
        "name": name,
        "id": name.lower().replace(" ", "-"),
        "tier": tier,
        "overage": overage,
        "last_touch": (TODAY - timedelta(days=overage + 30)).isoformat(),
        "days_ago": overage + 30,
        "company": None,
        "circles": [],
    }


def _report_with(overdue=None, crossings=None, threads=None, loops_extra=None) -> dict:
    overdue = overdue or []
    crossings = crossings or []
    threads = threads or []
    loops_extra = loops_extra or {}
    return {
        "today": TODAY.isoformat(),
        "loops": {
            "overdue": overdue,
            "due_today": [],
            "this_week": [],
            "future": [],
            "closed": [],
            **loops_extra,
        },
        "crossings": crossings,
        "active_threads": threads,
        "drr_top": [],
        "social": {},
        "email": {},
        "calendar": {},
    }


def _build_options(report: dict, linkedin_delta=None) -> list[dict]:
    result = cj.build_all(report, TODAY)
    # If linkedin_delta provided, rebuild with it
    if linkedin_delta is not None:
        sf = result["source_freshness"]
        cjb = result["cos_judgment"]
        winh = result["what_is_not_happening"]
        return cj.build_execution_options(report, TODAY, cjb, winh, linkedin_delta)
    return result["execution_options"]


class TestExecutionClosureActions(unittest.TestCase):

    def test_overdue_loop_gets_execution_option(self):
        """T4a: each overdue loop produces at least one execution option."""
        overdue_loops = [
            {"id": "L-001", "party": "Alice", "description": "Send proposal", "target": (TODAY - timedelta(days=3)).isoformat(), "closed": False},
            {"id": "L-002", "party": "Bob", "description": "Follow up", "target": (TODAY - timedelta(days=1)).isoformat(), "closed": False},
        ]
        report = _report_with(overdue=overdue_loops)
        options = _build_options(report)
        # Each overdue loop should have a matching option
        overdue_option_recs = [o for o in options if "overdue" in o.get("recommendation_id", "")]
        self.assertGreaterEqual(len(overdue_option_recs), 2, "Expected escalate options for 2 overdue loops")

    def test_all_actions_from_allowed_enum(self):
        """T4c: every action in execution_options is from EXECUTION_ACTIONS."""
        overdue_loops = [
            {"id": "L-003", "party": "Carol", "description": "Check in", "target": (TODAY - timedelta(days=2)).isoformat(), "closed": False},
        ]
        crossings = [_crossing("Dave"), _crossing("Eve")]
        report = _report_with(overdue=overdue_loops, crossings=crossings)
        options = _build_options(report)
        for opt in options:
            self.assertIn(
                opt["action"],
                EXECUTION_ACTIONS,
                f"Action '{opt['action']}' not in allowed enum"
            )

    def test_write_actions_require_confirmation(self):
        """T4d: write-like actions always have requires_confirmation=True."""
        overdue_loops = [
            {"id": "L-004", "party": "Frank", "description": "Open loop", "target": (TODAY - timedelta(days=5)).isoformat(), "closed": False},
        ]
        crossings = [_crossing("Grace")]
        report = _report_with(overdue=overdue_loops, crossings=crossings)
        options = _build_options(report)
        for opt in options:
            if opt["action"] in WRITE_ACTIONS:
                self.assertTrue(
                    opt["requires_confirmation"],
                    f"Write-like action '{opt['action']}' for '{opt['target']}' must have requires_confirmation=True"
                )

    def test_target_and_reason_non_empty(self):
        """T4e: every option has non-empty target and reason."""
        overdue_loops = [
            {"id": "L-005", "party": "Hank", "description": "Send outline", "target": (TODAY - timedelta(days=4)).isoformat(), "closed": False},
        ]
        report = _report_with(overdue=overdue_loops)
        options = _build_options(report)
        for opt in options:
            self.assertTrue(opt.get("target"), f"Option missing non-empty 'target': {opt}")
            self.assertTrue(opt.get("reason"), f"Option missing non-empty 'reason': {opt}")

    def test_crossing_without_loop_gets_open_loop_option(self):
        """T4b: crossing with no matching loop party → open_loop option."""
        # Crossing for "Isolated Person" — no loop references them
        crossings = [_crossing("Isolated Person", tier="inner", overage=40)]
        report = _report_with(crossings=crossings)
        options = _build_options(report)
        open_loops = [o for o in options if o["action"] == "open_loop"]
        self.assertGreater(len(open_loops), 0, "Expected open_loop option for crossing without loop")

    def test_crossing_with_existing_loop_no_duplicate_open_loop(self):
        """T4b: crossing whose name is in an active loop party does not get duplicate open_loop."""
        crossings = [_crossing("Jane Doe")]
        overdue = [
            {"id": "L-006", "party": "Jane Doe", "description": "Follow up", "target": (TODAY - timedelta(days=1)).isoformat(), "closed": False},
        ]
        report = _report_with(overdue=overdue, crossings=crossings)
        options = _build_options(report)
        # Should not have a redundant open_loop for Jane Doe (she already has an overdue loop)
        open_loops_for_jane = [
            o for o in options
            if o["action"] == "open_loop" and "jane" in (o.get("target") or "").lower()
        ]
        self.assertEqual(
            len(open_loops_for_jane), 0,
            "Should not generate open_loop for crossing already covered by an active loop"
        )

    def test_linkedin_outreach_queue_produces_options(self):
        """T4f: LinkedIn outreach queue items produce open_outreach_loop options."""
        linkedin_delta = {
            "available": True,
            "label": "fresh",
            "stale": False,
            "data": {
                "outreach_queue": [
                    {"name": "Sarah Connor", "reason": "Promotion detected", "priority": "high"},
                    {"name": "Kyle Reese", "reason": "Company move", "priority": "medium"},
                ],
                "monitor_queue": [],
                "reactivation_candidates": [],
            },
        }
        report = _report_with()
        sf = cj.build_source_freshness(report)
        cjb = cj.build_all(report, TODAY)["cos_judgment"]
        winh = cj.build_what_is_not_happening(report, TODAY)
        options = cj.build_execution_options(report, TODAY, cjb, winh, linkedin_delta)
        outreach_opts = [o for o in options if o["action"] == "open_outreach_loop"]
        self.assertGreaterEqual(len(outreach_opts), 2, "Expected open_outreach_loop options from LinkedIn queue")
        for opt in outreach_opts:
            self.assertTrue(opt["requires_confirmation"])

    def test_ignore_action_is_valid(self):
        """T4c: 'ignore' is a valid execution action."""
        self.assertIn("ignore", EXECUTION_ACTIONS)

    def test_no_options_for_empty_report(self):
        """T4: empty report produces a list (possibly empty — no false positives)."""
        report = _report_with()
        options = _build_options(report)
        self.assertIsInstance(options, list)

    def test_recommendation_id_unique(self):
        """T4: recommendation_ids should be unique across options."""
        overdue = [
            {"id": f"L-{i:03d}", "party": f"Person {i}", "description": f"Task {i}", "target": (TODAY - timedelta(days=i)).isoformat(), "closed": False}
            for i in range(1, 4)
        ]
        report = _report_with(overdue=overdue)
        options = _build_options(report)
        ids = [o["recommendation_id"] for o in options]
        self.assertEqual(len(ids), len(set(ids)), f"Duplicate recommendation_ids found: {ids}")


if __name__ == "__main__":
    unittest.main()
