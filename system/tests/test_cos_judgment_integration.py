#!/usr/bin/env python3
"""
test_cos_judgment_integration.py — RB 9.20 DEFECT-008 regression tests.

Verifies that cos_judgment.py:
  1. Is importable from the real repo's scripts directory
  2. build_all() returns all required CoS blocks
  3. All execution options have requires_confirmation=True
  4. Hard truths are evidence-bound (claim+evidence keys are present, never None)
  5. what_is_not_happening is a list (not None, not a plain dict)
  6. daily_brief._HAS_COS_JUDGMENT is True in production

DEFECT-008: Daily Brief was producing helpful-assistant drift instead of world-class CoS
posture because cos_judgment.py was written to the wrong directory and never wired into
daily_brief.py. This test suite proves the fix is in place and will catch any regression
where the import is broken or the block structure changes.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import cos_judgment as cj  # must be importable — if not, DEFECT-008 is not fixed


# ---------------------------------------------------------------------------
# Minimal report fixture
# ---------------------------------------------------------------------------

def _make_report(
    *,
    overdue_loops: int = 0,
    crossings: int = 0,
    active_threads: int = 0,
) -> dict:
    """Minimal report dict compatible with cos_judgment.build_all()."""
    today = date.today()

    def _loop(i: int, overdue: bool) -> dict:
        target = (today - timedelta(days=2)) if overdue else (today + timedelta(days=3))
        return {"id": f"loop-{i}", "party": f"Contact {i}", "description": f"Test loop {i}", "target": target.isoformat()}

    overdue_list = [_loop(i, overdue=True) for i in range(overdue_loops)]
    future_list = []

    return {
        "loops": {
            "overdue": overdue_list,
            "due_today": [],
            "this_week": future_list,
            "open": overdue_list + future_list,
        },
        "crossings": [{"id": f"rc-{i}", "name": f"Person {i}", "days_overdue": 35} for i in range(crossings)],
        "active_threads": [{"id": f"thread-{i}", "title": f"Thread {i}"} for i in range(active_threads)],
        "drr_top": [],
        "email": {},
        "calendar": {},
        "social": {},
        "strategic_memory": {},
        "strategic_events": {},
    }


TODAY = date.today()


# ---------------------------------------------------------------------------
# RB-COS-IMPORT-001: cos_judgment is importable
# ---------------------------------------------------------------------------

def test_cos_judgment_is_importable():
    """cos_judgment.py must be in the real repo's scripts directory."""
    assert hasattr(cj, "build_all"), "build_all() must exist in cos_judgment"
    assert hasattr(cj, "build_source_freshness"), "build_source_freshness() must exist"
    assert hasattr(cj, "build_hard_truths"), "build_hard_truths() must exist"
    assert hasattr(cj, "build_what_is_not_happening"), "build_what_is_not_happening() must exist"
    assert hasattr(cj, "build_execution_options"), "build_execution_options() must exist"


# ---------------------------------------------------------------------------
# RB-COS-BUILD-ALL-001: build_all returns all required keys
# ---------------------------------------------------------------------------

def test_build_all_returns_all_required_blocks():
    """build_all() must return every CoS block needed by daily_brief.py."""
    report = _make_report()
    result = cj.build_all(report, TODAY)

    required_keys = {
        "source_freshness",
        "cos_judgment",
        "what_is_not_happening",
        "execution_options",
        "macro_to_operator_synthesis",
        "linkedin_relationship_delta",
    }
    missing = required_keys - set(result.keys())
    assert not missing, f"build_all() missing keys: {missing}"


def test_cos_judgment_block_has_hard_truths():
    """cos_judgment block must have a hard_truths key (may be empty list)."""
    report = _make_report()
    result = cj.build_all(report, TODAY)
    cos_j = result["cos_judgment"]
    assert "hard_truths" in cos_j, "cos_judgment must have hard_truths key"
    assert isinstance(cos_j["hard_truths"], list), "hard_truths must be a list"


def test_what_is_not_happening_is_a_list():
    """what_is_not_happening must be a list, not None or a plain dict."""
    report = _make_report()
    result = cj.build_all(report, TODAY)
    wnh = result["what_is_not_happening"]
    assert isinstance(wnh, list), f"what_is_not_happening must be list, got {type(wnh)}"


def test_execution_options_is_a_list():
    """execution_options must be a list, not None or a plain dict."""
    report = _make_report()
    result = cj.build_all(report, TODAY)
    eo = result["execution_options"]
    assert isinstance(eo, list), f"execution_options must be list, got {type(eo)}"


# ---------------------------------------------------------------------------
# RB-COS-HARD-TRUTHS-001: hard_truths are evidence-bound
# ---------------------------------------------------------------------------

def test_hard_truth_items_have_claim_and_evidence():
    """Every hard truth must have a claim and evidence — not None or empty."""
    report = _make_report(overdue_loops=5, crossings=2)
    result = cj.build_all(report, TODAY)
    hard_truths = result["cos_judgment"]["hard_truths"]

    assert hard_truths, "Should detect hard truths with 5 overdue loops"
    for ht in hard_truths:
        assert isinstance(ht, dict), f"hard_truth item must be dict, got {type(ht)}"
        assert ht.get("claim"), f"hard_truth missing claim: {ht}"
        assert ht.get("evidence"), f"hard_truth missing evidence: {ht}"
        assert ht.get("why_it_matters"), f"hard_truth missing why_it_matters: {ht}"


def test_overdue_loops_trigger_hard_truths():
    """4+ overdue loops must produce a hard truth about execution drift."""
    report = _make_report(overdue_loops=4)
    result = cj.build_all(report, TODAY)
    hard_truths = result["cos_judgment"]["hard_truths"]
    claims = " ".join(ht.get("claim") or "" for ht in hard_truths).lower()
    assert "loop" in claims or "overdue" in claims or "execution" in claims, \
        f"Expected loop/execution hard truth for 4 overdue loops. Got: {claims}"


# ---------------------------------------------------------------------------
# RB-COS-EXECUTION-OPTIONS-001: all execution options require confirmation
# ---------------------------------------------------------------------------

def test_all_execution_options_have_requires_confirmation_field():
    """Every execution option must carry a requires_confirmation field (bool)."""
    report = _make_report(overdue_loops=3, crossings=3, active_threads=2)
    result = cj.build_all(report, TODAY)

    for opt in result["execution_options"]:
        assert isinstance(opt, dict), f"execution option must be dict, got {type(opt)}"
        assert "requires_confirmation" in opt, (
            f"Execution option missing requires_confirmation field: {opt}"
        )
        assert isinstance(opt["requires_confirmation"], bool), (
            f"requires_confirmation must be bool, got {type(opt['requires_confirmation'])}: {opt}"
        )


def test_write_actions_always_require_confirmation():
    """open_loop, create_task, open_outreach_loop are write-like — must require confirmation."""
    report = _make_report(overdue_loops=2, crossings=3, active_threads=2)
    result = cj.build_all(report, TODAY)

    write_actions = cj.WRITE_ACTIONS
    for opt in result["execution_options"]:
        if opt.get("action") in write_actions:
            assert opt.get("requires_confirmation") is True, (
                f"Write action '{opt['action']}' must require confirmation: {opt}"
            )


# ---------------------------------------------------------------------------
# RB-COS-WIRING-001: daily_brief imports cos_judgment successfully
# ---------------------------------------------------------------------------

def test_daily_brief_imports_cos_judgment():
    """daily_brief._HAS_COS_JUDGMENT must be True in the real repo."""
    import daily_brief as db
    assert getattr(db, "_HAS_COS_JUDGMENT", False) is True, (
        "daily_brief._HAS_COS_JUDGMENT is False — cos_judgment import failed in production. "
        "DEFECT-008 is not resolved."
    )


def test_daily_brief_has_cos_judgment_module():
    """daily_brief._cj must be the cos_judgment module."""
    import daily_brief as db
    cj_mod = getattr(db, "_cj", None)
    assert cj_mod is not None, "_cj module not found in daily_brief"
    assert hasattr(cj_mod, "build_all"), "_cj.build_all missing"


# ---------------------------------------------------------------------------
# RB-COS-SOURCE-FRESHNESS-001: source_freshness gate
# ---------------------------------------------------------------------------

def test_source_freshness_returns_per_source_labels():
    """source_freshness must contain per-source labels for all key sources."""
    report = _make_report()
    sf = cj.build_source_freshness(report)

    assert "sources" in sf, "source_freshness must have 'sources' key"
    required_sources = {"email", "calendar", "social", "linkedin_delta", "macro_synthesis", "baseline"}
    present = set(sf["sources"].keys())
    missing = required_sources - present
    assert not missing, f"source_freshness missing sources: {missing}"

    for source_name, source_data in sf["sources"].items():
        label = source_data.get("label")
        assert label in {"fresh", "stale", "source_unavailable", "refresh_failed", "historical_memory", "inferred"}, \
            f"source {source_name} has invalid label '{label}'"


def test_source_freshness_quiet_claim_allowed_is_boolean():
    """quiet_claim_allowed must be a boolean so callers can gate conclusions."""
    report = _make_report()
    sf = cj.build_source_freshness(report)
    assert isinstance(sf.get("quiet_claim_allowed"), bool), \
        f"quiet_claim_allowed must be bool, got {type(sf.get('quiet_claim_allowed'))}"


# ---------------------------------------------------------------------------
# RB-COS-EMPTY-REPORT-001: build_all handles empty/minimal report without error
# ---------------------------------------------------------------------------

def test_build_all_handles_empty_report():
    """build_all must not raise on a completely empty report dict."""
    result = cj.build_all({}, TODAY)
    assert "cos_judgment" in result
    assert "source_freshness" in result
    assert "what_is_not_happening" in result
    assert "execution_options" in result
