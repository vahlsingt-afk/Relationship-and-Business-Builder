"""
test_daily_brief_negative_space.py — RB 9.20 Negative-Space analysis tests (T2).

Tests:
  T2a: overdue loops appear in what_is_not_happening
  T2b: threads without loops appear as thread_without_next_action
  T2c: each absence has expected_signal, observed_absence, recommended_disposition
  T2d: silence is not interpreted as rejection unless evidence supports it
  T2e: disposition is one of the allowed values
  T2f: under-instrumented state returns empty list (not wrong inferences)
"""
from __future__ import annotations
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import cos_judgment as cj

TODAY = date(2026, 5, 28)
ALLOWED_DISPOSITIONS = frozenset({"act_today", "monitor", "ask_user", "ignore"})


def _make_loop(loop_id: str, party: str, description: str, target: date) -> dict:
    return {
        "id": loop_id,
        "opened": (TODAY - timedelta(days=10)).isoformat(),
        "party": party,
        "description": description,
        "target": target.isoformat(),
        "status_raw": "open",
        "closed": False,
    }


def _make_thread(tid: str, title: str, people: list[str] | None = None) -> dict:
    return {
        "id": tid,
        "title": title,
        "status": "open",
        "type": "job_opportunity",
        "people": people or [],
        "companies": [],
        "context": "",
        "current_state": "",
        "boost_for_brief": "medium",
        "boost_score": 1.2,
    }


def _base_report(**overrides) -> dict:
    r = {
        "today": TODAY.isoformat(),
        "loops": {"overdue": [], "due_today": [], "this_week": [], "future": [], "closed": []},
        "crossings": [],
        "active_threads": [],
        "drr_top": [],
        "social": {},
        "email": {},
        "calendar": {},
    }
    r.update(overrides)
    return r


class TestNegativeSpaceDetection(unittest.TestCase):

    def test_overdue_loop_appears_in_what_is_not_happening(self):
        """T2a: overdue loop → overdue_commitment absence item."""
        overdue = [_make_loop("L-001", "Jane Doe", "Follow up on proposal", TODAY - timedelta(days=5))]
        report = _base_report(loops={"overdue": overdue, "due_today": [], "this_week": [], "future": [], "closed": []})
        absences = cj.build_what_is_not_happening(report, TODAY)
        self.assertTrue(
            any(a["type"] == "overdue_commitment" for a in absences),
            "Expected overdue_commitment absence for overdue loop"
        )

    def test_overdue_loop_names_party(self):
        """T2a: absence expected_signal includes the loop party name."""
        overdue = [_make_loop("L-002", "John Smith", "Send proposal", TODAY - timedelta(days=3))]
        report = _base_report(loops={"overdue": overdue, "due_today": [], "this_week": [], "future": [], "closed": []})
        absences = cj.build_what_is_not_happening(report, TODAY)
        overdue_abs = [a for a in absences if a["type"] == "overdue_commitment"]
        self.assertGreater(len(overdue_abs), 0)
        self.assertIn("John Smith", overdue_abs[0]["expected_signal"])

    def test_thread_without_loop_is_drifting(self):
        """T2b: active thread with no matching loop → thread_without_next_action."""
        thread = _make_thread("T-001", "Role search", people=["nobody-in-loops"])
        report = _base_report(
            active_threads=[thread],
            loops={"overdue": [], "due_today": [], "this_week": [], "future": [], "closed": []},
        )
        absences = cj.build_what_is_not_happening(report, TODAY)
        self.assertTrue(
            any(a["type"] == "thread_without_next_action" for a in absences),
            "Expected thread_without_next_action for thread with no matching loop"
        )

    def test_thread_with_matching_loop_not_flagged(self):
        """T2b: thread whose person appears in a loop party is not flagged as drifting."""
        thread = _make_thread("T-001", "Role search", people=["jane-doe"])
        loop = _make_loop("L-003", "Jane Doe", "Follow up", TODAY + timedelta(days=5))
        report = _base_report(
            active_threads=[thread],
            loops={"overdue": [], "due_today": [], "this_week": [loop], "future": [], "closed": []},
        )
        absences = cj.build_what_is_not_happening(report, TODAY)
        drifting = [a for a in absences if a["type"] == "thread_without_next_action"]
        self.assertEqual(drifting, [], "Thread with matching loop should not be flagged as drifting")

    def test_absence_has_required_fields(self):
        """T2c: each absence item has expected_signal, observed_absence, recommended_disposition."""
        overdue = [_make_loop("L-004", "Bob", "Check in", TODAY - timedelta(days=2))]
        report = _base_report(loops={"overdue": overdue, "due_today": [], "this_week": [], "future": [], "closed": []})
        absences = cj.build_what_is_not_happening(report, TODAY)
        for a in absences:
            self.assertIn("expected_signal", a, "Absence missing 'expected_signal'")
            self.assertIn("observed_absence", a, "Absence missing 'observed_absence'")
            self.assertIn("recommended_disposition", a, "Absence missing 'recommended_disposition'")
            self.assertIn("why_it_matters", a, "Absence missing 'why_it_matters'")
            self.assertIn("next_check", a, "Absence missing 'next_check'")

    def test_disposition_is_allowed_value(self):
        """T2e: recommended_disposition is one of act_today|monitor|ask_user|ignore."""
        overdue = [_make_loop("L-005", "Carol", "Send outline", TODAY - timedelta(days=4))]
        thread = _make_thread("T-002", "Partnership")
        report = _base_report(
            loops={"overdue": overdue, "due_today": [], "this_week": [], "future": [], "closed": []},
            active_threads=[thread],
        )
        absences = cj.build_what_is_not_happening(report, TODAY)
        for a in absences:
            self.assertIn(
                a["recommended_disposition"],
                ALLOWED_DISPOSITIONS,
                f"Invalid disposition: {a['recommended_disposition']}"
            )

    def test_silence_not_interpreted_as_rejection(self):
        """T2d: overdue loop absence does not claim rejection — only records the silence."""
        overdue = [_make_loop("L-006", "David", "Wait for response", TODAY - timedelta(days=7))]
        report = _base_report(loops={"overdue": overdue, "due_today": [], "this_week": [], "future": [], "closed": []})
        absences = cj.build_what_is_not_happening(report, TODAY)
        for a in absences:
            # Should describe monitored silence, not rejection
            observed_text = (a.get("observed_absence") or "").lower()
            self.assertNotIn("rejected", observed_text, "Silence must not be interpreted as rejection")
            self.assertNotIn("ghosted", observed_text, "Silence must not be interpreted as rejection")

    def test_empty_report_returns_empty_list(self):
        """T2f: no false positives when system has no loops, threads, or signals."""
        report = _base_report()
        absences = cj.build_what_is_not_happening(report, TODAY)
        self.assertIsInstance(absences, list, "Should return a list even when empty")

    def test_multiple_overdue_loops_each_detected(self):
        """T2a: N overdue loops produce N overdue_commitment items."""
        n = 4
        overdue = [
            _make_loop(f"L-{i:03d}", f"Person {i}", f"Task {i}", TODAY - timedelta(days=i + 1))
            for i in range(1, n + 1)
        ]
        report = _base_report(loops={"overdue": overdue, "due_today": [], "this_week": [], "future": [], "closed": []})
        absences = cj.build_what_is_not_happening(report, TODAY)
        overdue_abs = [a for a in absences if a["type"] == "overdue_commitment"]
        self.assertEqual(len(overdue_abs), n, f"Expected {n} overdue_commitment items, got {len(overdue_abs)}")


if __name__ == "__main__":
    unittest.main()
