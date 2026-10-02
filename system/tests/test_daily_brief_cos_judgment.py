"""
test_daily_brief_cos_judgment.py — RB 9.20 CoS Judgment layer tests (T1, T7).

Tests:
  T1a: hard_truths exist when active loops show effort without conversion
  T1b: hard truths are evidence-bound (never empty claim or evidence)
  T1c: no banned encouragement language in rendered brief
  T1d: canonical_response_eval rejects helpful-assistant drift fixture
  T1e: canonical_response_eval accepts a good-brief fixture
  T1f: cos_judgment block has all required fields
  T1g: under-instrumented fallback appears when evidence is insufficient
"""
from __future__ import annotations
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import cos_judgment as cj
from canonical_response_eval import (
    eval_daily_brief_cos_operator_text,
    eval_daily_brief_cos_operator_json,
    BAD_BRIEF_FIXTURE,
    GOOD_BRIEF_FIXTURE,
    BANNED_PHRASES,
)

TODAY = date(2026, 5, 28)


def _make_loop(loop_id: str, party: str, description: str, target: date, closed: bool = False) -> dict:
    return {
        "id": loop_id,
        "opened": (TODAY - timedelta(days=20)).isoformat(),
        "party": party,
        "description": description,
        "target": target.isoformat(),
        "status_raw": "closed" if closed else "open",
        "closed": closed,
    }


def _make_crossing(name: str, tier: str = "inner", overage: int = 35) -> dict:
    return {
        "name": name,
        "id": name.lower().replace(" ", "-"),
        "tier": tier,
        "last_touch": (TODAY - timedelta(days=overage + 30)).isoformat(),
        "days_ago": overage + 30,
        "overage": overage,
        "company": "Acme Corp",
        "circles": [],
    }


def _make_active_thread(tid: str, title: str, people: list[str] | None = None) -> dict:
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


def _make_report_with_overdue_loops(n_overdue: int = 5) -> dict:
    overdue = [
        _make_loop(f"L-001-{i:03d}", f"Contact {i}", "Follow up on proposal", TODAY - timedelta(days=i + 1))
        for i in range(n_overdue)
    ]
    return {
        "today": TODAY.isoformat(),
        "loops": {
            "overdue": overdue,
            "due_today": [],
            "this_week": [],
            "future": [],
            "closed": [],
        },
        "crossings": [_make_crossing("Alice Smith"), _make_crossing("Bob Jones")],
        "active_threads": [_make_active_thread("T-001", "Role search")],
        "drr_top": [],
        "social": {},
        "email": {},
        "calendar": {},
    }


def _make_report_minimal() -> dict:
    """Minimal report with no evidence — tests under-instrumented fallback."""
    return {
        "today": TODAY.isoformat(),
        "loops": {"overdue": [], "due_today": [], "this_week": [], "future": [], "closed": []},
        "crossings": [],
        "active_threads": [],
        "drr_top": [],
        "social": {"fetched_at": None},
        "email": {"fetched_at": None},
        "calendar": {"fetched_at": None},
    }


class TestCoSJudgmentHardTruths(unittest.TestCase):

    def test_hard_truths_exist_with_overdue_loops(self):
        """T1a: hard_truths non-empty when overdue loops exist."""
        report = _make_report_with_overdue_loops(n_overdue=5)
        sf = cj.build_source_freshness(report)
        truths = cj.build_hard_truths(report, TODAY, sf)
        self.assertGreater(len(truths), 0, "Expected at least one hard truth with 5 overdue loops")

    def test_hard_truths_evidence_bound(self):
        """T1b: every hard truth has claim and evidence — never empty."""
        report = _make_report_with_overdue_loops(n_overdue=5)
        sf = cj.build_source_freshness(report)
        truths = cj.build_hard_truths(report, TODAY, sf)
        for ht in truths:
            self.assertIn("claim", ht, "Hard truth missing 'claim'")
            self.assertIn("evidence", ht, "Hard truth missing 'evidence'")
            self.assertIn("confidence", ht, "Hard truth missing 'confidence'")
            self.assertIn("recommended_disposition", ht, "Hard truth missing 'recommended_disposition'")
            self.assertTrue(ht["claim"], "Hard truth 'claim' must be non-empty")
            self.assertTrue(ht["evidence"], "Hard truth 'evidence' must be non-empty")

    def test_overdue_backlog_named_in_hard_truth(self):
        """T1a: execution backlog hard truth references the loop count."""
        report = _make_report_with_overdue_loops(n_overdue=5)
        sf = cj.build_source_freshness(report)
        truths = cj.build_hard_truths(report, TODAY, sf)
        backlog_truths = [ht for ht in truths if "past their target" in ht.get("claim", "")]
        self.assertGreater(len(backlog_truths), 0, "Expected backlog hard truth")
        self.assertIn("5", backlog_truths[0]["claim"], "Hard truth should reference the loop count")

    def test_under_instrumented_fallback(self):
        """T1g: when no evidence is available, a 'under-instrumented' item is returned."""
        report = _make_report_minimal()
        sf = cj.build_source_freshness(report)
        truths = cj.build_hard_truths(report, TODAY, sf)
        self.assertGreater(len(truths), 0)
        # Either it has real evidence (email unavailable is real evidence) or the under-instrumented fallback
        claims = " ".join(ht.get("claim", "") for ht in truths)
        self.assertTrue(
            "under-instrumented" in claims.lower() or "cannot prove" in claims.lower() or "unavailable" in claims.lower(),
            "Expected under-instrumented signal or source unavailability claim"
        )

    def test_cos_judgment_block_has_required_fields(self):
        """T1f: cos_judgment has all required top-level keys."""
        report = _make_report_with_overdue_loops()
        result = cj.build_all(report, TODAY)
        cjb = result["cos_judgment"]
        for field in ("hard_truths", "negative_space", "focus_leaks",
                      "unsupported_assumptions", "execution_pressure",
                      "confidence", "source_refs"):
            self.assertIn(field, cjb, f"cos_judgment missing '{field}'")

    def test_source_refs_non_empty(self):
        """T1f: source_refs in cos_judgment is non-empty."""
        report = _make_report_with_overdue_loops()
        result = cj.build_all(report, TODAY)
        self.assertGreater(len(result["cos_judgment"]["source_refs"]), 0)


class TestCoSJudgmentBannedLanguage(unittest.TestCase):

    def test_canonical_eval_rejects_bad_brief(self):
        """T7: canonical evaluator rejects helpful-assistant drift fixture."""
        result = eval_daily_brief_cos_operator_text(BAD_BRIEF_FIXTURE)
        self.assertFalse(result.passed, "Bad brief fixture must fail canonical evaluation")
        failed_rules = [f.rule for f in result.findings]
        self.assertIn("no_banned_phrases", failed_rules,
                      "Expected banned-phrase failures in bad fixture")

    def test_canonical_eval_accepts_good_brief(self):
        """T7: canonical evaluator accepts a well-formed CoS brief."""
        result = eval_daily_brief_cos_operator_text(GOOD_BRIEF_FIXTURE)
        if not result.passed:
            failures = [f.message for f in result.findings if f.severity == "fail"]
            self.fail(f"Good fixture failed:\n" + "\n".join(failures))

    def test_each_banned_phrase_triggers_failure(self):
        """T7: each individually banned phrase causes a failure."""
        specific_phrases = [
            "You have good momentum",
            "Keep leaning into",
            "Here are some helpful suggestions",
            "Stay consistent",
        ]
        for phrase in specific_phrases:
            result = eval_daily_brief_cos_operator_text(phrase)
            self.assertFalse(result.passed, f"Phrase should trigger failure: '{phrase}'")

    def test_good_brief_has_required_sections(self):
        """T7: good brief includes all four required section keywords."""
        result = eval_daily_brief_cos_operator_text(GOOD_BRIEF_FIXTURE)
        missing = [c for c in result.checks_failed if c.startswith("section_missing")]
        self.assertEqual(missing, [], f"Good brief missing sections: {missing}")

    def test_json_eval_rejects_empty_payload(self):
        """T7: JSON evaluator rejects minimal/empty payload."""
        result = eval_daily_brief_cos_operator_json({})
        self.assertFalse(result.passed)

    def test_json_eval_rejects_empty_hard_truths(self):
        """T7: JSON evaluator rejects payload with empty hard_truths list."""
        payload = {
            "cos_judgment": {
                "hard_truths": [],
                "negative_space": [],
                "confidence": "low",
                "source_refs": ["baseline"],
            },
            "what_is_not_happening": [],
            "execution_options": [],
            "source_freshness": {"sources": {"email": {"label": "stale"}}},
        }
        result = eval_daily_brief_cos_operator_json(payload)
        self.assertFalse(result.passed)
        failed_rules = [f.rule for f in result.findings]
        self.assertIn("hard_truths_required", failed_rules)


if __name__ == "__main__":
    unittest.main()
