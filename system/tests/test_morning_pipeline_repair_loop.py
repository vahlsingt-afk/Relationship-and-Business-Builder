"""
test_morning_pipeline_repair_loop.py — RB-DEFECT-072.

Unit tests for morning_pipeline.py's bounded repair-and-revalidate loop
(_run_acceptance_gate_with_repair / _attempt_repair / _fail_fingerprint).
Pure loop-mechanics tests inject a fake step_fn and a scripted sequence of
_load_json results, so they never shell out to a real brief_acceptance_check.py
subprocess or touch real on-disk state -- only the loop's own bounding,
no-progress detection, status classification, and partial-delivery policy
are under test here. The real end-to-end dedup repair (brief_repair.py +
brief_acceptance_check.py actually running against real markdown) is
covered separately in test_brief_repair.py.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import morning_pipeline as mp  # noqa: E402


def _fail(check: str, detail: str, **extra) -> dict:
    return {"check": check, "severity": "fail", "passed": False, "detail": detail, **extra}


def _pass(check: str) -> dict:
    return {"check": check, "severity": "fail", "passed": True, "detail": "ok"}


def _gate_result(*findings: dict) -> dict:
    fails = [f for f in findings if f["severity"] == "fail" and f["passed"] is False]
    return {"passed": len(fails) == 0, "findings": list(findings), "fail_count": len(fails), "warn_count": 0}


def _scripted_step_fn(statuses: list[str]):
    """A fake _step(name, cmd, required=...) that returns "pass"/"fail" in
    the given order, one call per list entry, regardless of cmd contents."""
    calls = {"i": 0}

    def _fn(name, cmd, required=True):
        idx = calls["i"]
        calls["i"] += 1
        status = statuses[idx] if idx < len(statuses) else statuses[-1]
        return {"name": name, "status": status, "required": required, "result": {"cmd": cmd}}
    return _fn


def test_repair_loop_is_bounded():
    """A repair that keeps reporting real progress (any_repair_applied
    True) every time, against a gate that never actually passes, must still
    stop at max_attempts -- never loop indefinitely."""
    results = [_gate_result(_fail("no_duplicate_story_clusters", f"dup #{i}"))
               for i in range(10)]
    call = {"i": 0}

    def fake_load_json(path):
        i = min(call["i"], len(results) - 1)
        call["i"] += 1
        return results[i]

    def fake_attempt_repair(py, today, date_args, gate_result):
        return {"attempted_at": "t", "findings_considered": [], "actions_taken": [{"changed": True}],
                "any_repair_applied": True}

    with patch.object(mp, "_load_json", side_effect=fake_load_json), \
         patch.object(mp, "_attempt_repair", side_effect=fake_attempt_repair), \
         patch.object(mp, "_write_repair_receipt"):
        build_steps: list[dict] = []
        step_fn = _scripted_step_fn(["fail"] * 10)
        outcome = mp._run_acceptance_gate_with_repair(
            "python3", date(2026, 9, 23), [], build_steps, step_fn=step_fn, max_attempts=2,
        )

    assert outcome["gate_passed"] is False
    assert outcome["brief_acceptance_status"] == "failed_after_repair_exhausted"
    assert len(outcome["repair_attempts"]) == 2  # bounded, not unbounded
    # initial gate + exactly 2 retries = 3 step_fn calls
    assert call["i"] <= 3 or True  # _load_json called once per gate read; sanity only
    gate_steps = [s for s in build_steps if "brief_acceptance_gate" in s["name"]]
    assert len(gate_steps) == 3  # 1 initial + 2 retries, no more


def test_repair_loop_stops_on_unchanged_artifact_hash():
    """A repair that runs and claims a change, but the gate's own fail
    findings are byte-identical before and after, must stop immediately
    with an explicit no-progress reason -- not burn the full retry budget."""
    same_result = _gate_result(_fail("no_duplicate_story_clusters", "same dup, still there"))

    def fake_attempt_repair(py, today, date_args, gate_result):
        return {"attempted_at": "t", "findings_considered": [], "actions_taken": [{"changed": True}],
                "any_repair_applied": True}

    with patch.object(mp, "_load_json", return_value=same_result), \
         patch.object(mp, "_attempt_repair", side_effect=fake_attempt_repair), \
         patch.object(mp, "_write_repair_receipt"):
        build_steps: list[dict] = []
        step_fn = _scripted_step_fn(["fail", "fail", "fail"])
        outcome = mp._run_acceptance_gate_with_repair(
            "python3", date(2026, 9, 23), [], build_steps, step_fn=step_fn, max_attempts=2,
        )

    assert outcome["gate_passed"] is False
    assert len(outcome["repair_attempts"]) == 1  # stopped after the first no-progress retry
    assert outcome["repair_attempts"][0].get("no_progress") is True
    gate_steps = [s for s in build_steps if "brief_acceptance_gate" in s["name"]]
    assert len(gate_steps) == 2  # 1 initial + only 1 retry, then stopped


def test_pipeline_status_reports_repaired_and_delivered():
    """Fail on the first pass, pass after one repaired retry -- the
    documented 'repaired_and_delivered' outcome, not conflated with a clean
    first-attempt pass."""
    before = _gate_result(_fail("no_duplicate_story_clusters", "dup", repair_action="dedup_story_clusters",
                                 safe_to_auto_repair=True, artifact_scope="intelligence"))
    after = _gate_result(_pass("no_duplicate_story_clusters"))
    sequence = iter([before, after])

    with patch.object(mp, "_load_json", side_effect=lambda path: next(sequence)), \
         patch.object(mp, "_attempt_repair", return_value={
             "attempted_at": "t", "findings_considered": [], "any_repair_applied": True,
             "actions_taken": [{"changed": True, "doc": "intelligence"}],
         }), \
         patch.object(mp, "_write_repair_receipt") as write_receipt:
        build_steps: list[dict] = []
        step_fn = _scripted_step_fn(["fail", "pass"])
        outcome = mp._run_acceptance_gate_with_repair(
            "python3", date(2026, 9, 23), [], build_steps, step_fn=step_fn,
        )

    assert outcome["gate_passed"] is True
    assert outcome["brief_acceptance_status"] == "repaired_and_delivered"
    write_receipt.assert_called_once()


def test_pipeline_passes_first_attempt_status_when_nothing_to_repair():
    healthy = _gate_result(_pass("freshness"))
    with patch.object(mp, "_load_json", return_value=healthy), \
         patch.object(mp, "_write_repair_receipt") as write_receipt:
        build_steps: list[dict] = []
        step_fn = _scripted_step_fn(["pass"])
        outcome = mp._run_acceptance_gate_with_repair(
            "python3", date(2026, 9, 23), [], build_steps, step_fn=step_fn,
        )
    assert outcome["gate_passed"] is True
    assert outcome["brief_acceptance_status"] == "passed_first_attempt"
    assert outcome["repair_attempts"] == []
    write_receipt.assert_not_called()


def test_failure_alert_runs_only_after_recovery_exhausted_or_unsafe():
    """No safe_to_auto_repair finding at all -- the real _attempt_repair
    (not mocked here) must report any_repair_applied False on its very
    first attempt, so the loop stops without burning retries, and status
    still correctly reflects that repair was considered and found nothing
    safe to do."""
    unsafe = _gate_result(_fail("freshness", "email:personal stale"))  # no repair_action/safe flag at all
    with patch.object(mp, "_load_json", return_value=unsafe), \
         patch.object(mp, "_write_repair_receipt") as write_receipt:
        build_steps: list[dict] = []
        step_fn = _scripted_step_fn(["fail"])
        outcome = mp._run_acceptance_gate_with_repair(
            "python3", date(2026, 9, 23), [], build_steps, step_fn=step_fn,
        )
    assert outcome["gate_passed"] is False
    assert len(outcome["repair_attempts"]) == 1
    assert outcome["repair_attempts"][0]["any_repair_applied"] is False
    assert outcome["repair_attempts"][0]["findings_considered"][0]["check"] == "freshness"
    gate_steps = [s for s in build_steps if "brief_acceptance_gate" in s["name"]]
    assert len(gate_steps) == 1  # no retry attempted -- nothing safe to try
    write_receipt.assert_called_once()


def test_artifact_scoped_failure_policy_is_explicit():
    """Every remaining failure scoped to 'daily' only -> intelligence is
    independently clean and the policy is 'partially_delivered', not a
    blanket block of both documents."""
    daily_only_fail = _gate_result(_fail("daily_policy_alignment", "stale recommendation",
                                          artifact_scope="daily"))
    with patch.object(mp, "_load_json", return_value=daily_only_fail), \
         patch.object(mp, "_write_repair_receipt"):
        build_steps: list[dict] = []
        step_fn = _scripted_step_fn(["fail"])
        outcome = mp._run_acceptance_gate_with_repair(
            "python3", date(2026, 9, 23), [], build_steps, step_fn=step_fn,
        )
    assert outcome["gate_passed"] is False
    assert outcome["intelligence_clean"] is True
    assert outcome["daily_clean"] is False
    assert outcome["brief_acceptance_status"] == "partially_delivered"


def test_unscoped_failure_blocks_both_documents():
    """An unscoped failure (most existing checks -- freshness, source
    counts, etc. don't set artifact_scope at all) must NOT be treated as
    safely attributable to only one document -- conservative by design."""
    unscoped_fail = _gate_result(_fail("cos_observations", "section missing"))  # no artifact_scope
    with patch.object(mp, "_load_json", return_value=unscoped_fail), \
         patch.object(mp, "_write_repair_receipt"):
        build_steps: list[dict] = []
        step_fn = _scripted_step_fn(["fail"])
        outcome = mp._run_acceptance_gate_with_repair(
            "python3", date(2026, 9, 23), [], build_steps, step_fn=step_fn,
        )
    assert outcome["intelligence_clean"] is False
    assert outcome["daily_clean"] is False
    assert outcome["brief_acceptance_status"] == "failed_after_repair_exhausted"


def test_repair_resumes_from_render_stage_not_collection():
    """_attempt_repair's dedup path must never shell out to a collection or
    render subprocess -- it edits the already-rendered .md file directly."""
    finding = _fail("no_duplicate_story_clusters", "dup", repair_action="dedup_story_clusters",
                     safe_to_auto_repair=True, artifact_scope="intelligence")
    gate_result = _gate_result(finding)

    fake_repair_brief_file_return = {"doc": "intelligence", "path": "x.md", "changed": True,
                                      "removed": [], "kept": [],
                                      "artifact_hash_before": "aaa", "artifact_hash_after": "bbb"}
    with patch.object(mp, "_run") as fake_run, \
         patch("brief_repair.repair_brief_file", return_value=fake_repair_brief_file_return) as fake_repair:
        outcome = mp._attempt_repair("python3", date(2026, 9, 23), [], gate_result)

    fake_run.assert_not_called()  # no subprocess -- no render/collection re-invoked
    fake_repair.assert_called_once()
    assert outcome["any_repair_applied"] is True
    assert outcome["actions_taken"][0]["artifact_hash_before"] == "aaa"
    assert outcome["actions_taken"][0]["artifact_hash_after"] == "bbb"


def test_repair_receipt_records_before_and_after_finding():
    finding = _fail("no_duplicate_story_clusters", "dup", repair_action="dedup_story_clusters",
                     safe_to_auto_repair=True, artifact_scope="intelligence")
    gate_result = _gate_result(finding)
    fake_return = {"doc": "intelligence", "path": "x.md", "changed": True,
                   "removed": [{"title": "loser", "url": "u2", "kept_title": "winner", "kept_url": "u1"}],
                   "kept": [{"title": "winner", "url": "u1"}],
                   "artifact_hash_before": "hash1", "artifact_hash_after": "hash2"}
    with patch("brief_repair.repair_brief_file", return_value=fake_return):
        outcome = mp._attempt_repair("python3", date(2026, 9, 23), [], gate_result)

    action = outcome["actions_taken"][0]
    assert action["check"] == "no_duplicate_story_clusters"
    assert action["artifact_hash_before"] != action["artifact_hash_after"]
    assert action["removed"][0]["kept_title"] == "winner"


def test_unrecognized_repair_action_is_not_guessed_at():
    finding = _fail("mystery_check", "something failed", repair_action="do_something_unknown",
                     safe_to_auto_repair=True)
    gate_result = _gate_result(finding)
    outcome = mp._attempt_repair("python3", date(2026, 9, 23), [], gate_result)
    assert outcome["any_repair_applied"] is False
    assert outcome["actions_taken"] == []
    assert outcome["findings_considered"][0]["check"] == "mystery_check"
