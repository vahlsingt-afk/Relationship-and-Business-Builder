"""RB-DEFECT-067 — cross-artifact weekly-plan consistency check.

Covers verify_weekly_plan_consistency.py's core check functions: the
draft-state regression guard (the exact confirm_draft() bug this defect
started from) and the rendered-brief fingerprint check (proves a stale
.md would be caught, not just that the mechanism exists in isolation --
see test_weekly_plan_render_invalidation.py for the fingerprint unit tests
this builds on).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import rb_core as core
import verify_weekly_plan_consistency as verify


def _write(path: Path, **fields) -> None:
    path.write_text(json.dumps(fields), encoding="utf-8")


def test_check_weekly_plan_source_flags_stuck_draft_for_active_week(tmp_path):
    """The RB-DEFECT-067 regression this whole defect started from: a draft
    for the SAME week the active plan already covers, still marked
    draft_pending_confirmation -- proof confirm_draft() forgot to update
    the draft file after promotion."""
    _write(tmp_path / "weekly_plan.json", week_of="2026-08-17", status="active")
    _write(tmp_path / "weekly_plan_draft.json", week_of="2026-08-17",
           status="draft_pending_confirmation", generated_at="2026-08-17T10:02:48Z")

    with patch.object(core, "SYSTEM_DIR", tmp_path), patch.object(verify.core, "SYSTEM_DIR", tmp_path):
        source_week, results = verify.check_weekly_plan_source(__import__("datetime").date(2026, 8, 19))

    assert source_week == "2026-08-17"
    draft_check = next(r for r in results if r.name == "draft_state_consistency")
    assert draft_check.status == "mismatch"
    assert "RB-DEFECT-067 regression" in draft_check.detail


def test_check_weekly_plan_source_ok_when_draft_confirmed(tmp_path):
    _write(tmp_path / "weekly_plan.json", week_of="2026-08-17", status="active")
    _write(tmp_path / "weekly_plan_draft.json", week_of="2026-08-17",
           status="confirmed", confirmed_at="2026-08-19T14:30:00Z")

    with patch.object(core, "SYSTEM_DIR", tmp_path), patch.object(verify.core, "SYSTEM_DIR", tmp_path):
        _, results = verify.check_weekly_plan_source(__import__("datetime").date(2026, 8, 19))

    draft_check = next(r for r in results if r.name == "draft_state_consistency")
    assert draft_check.status == "ok"


def test_check_weekly_plan_source_ok_when_draft_is_for_a_different_week(tmp_path):
    """A pending draft for NEXT week while this week's plan is active is
    normal (nothing to confirm yet) -- must not false-positive."""
    _write(tmp_path / "weekly_plan.json", week_of="2026-08-17", status="active")
    _write(tmp_path / "weekly_plan_draft.json", week_of="2026-08-24",
           status="draft_pending_confirmation", generated_at="2026-08-24T10:00:00Z")

    with patch.object(core, "SYSTEM_DIR", tmp_path), patch.object(verify.core, "SYSTEM_DIR", tmp_path):
        _, results = verify.check_weekly_plan_source(__import__("datetime").date(2026, 8, 24))

    draft_check = next(r for r in results if r.name == "draft_state_consistency")
    assert draft_check.status == "ok"


def test_pending_current_week_draft_is_not_compared_to_old_active_plan(tmp_path):
    """The brief may legitimately display this week's pending draft while
    last week's confirmed plan remains the active scoring plan."""
    _write(tmp_path / "weekly_plan.json", week_of="2026-09-07", status="active")
    _write(tmp_path / "weekly_plan_draft.json", week_of="2026-09-14",
           status="draft_pending_confirmation")
    payload = {"canonical_brief": {"sections": {"weekly_plan_focus": [{
        "extras": {"week_of": "2026-09-14"},
        "source_refs": ["weekly_plan_draft.json"],
    }]}}}
    cache_path = tmp_path / "daily_brief.json"
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
    with patch.object(core, "SYSTEM_DIR", tmp_path), patch.object(verify.core, "SYSTEM_DIR", tmp_path), \
         patch.object(verify, "DAILY_BRIEF_CACHE", cache_path):
        result = verify.check_daily_brief_cache("2026-09-07")
    assert result.status == "ok"


def test_pending_draft_display_must_match_draft_file(tmp_path):
    _write(tmp_path / "weekly_plan_draft.json", week_of="2026-09-14",
           status="draft_pending_confirmation")
    payload = {"canonical_brief": {"sections": {"weekly_plan_focus": [{
        "extras": {"week_of": "2026-09-21"},
        "source_refs": ["weekly_plan_draft.json"],
    }]}}}
    with patch.object(core, "SYSTEM_DIR", tmp_path), patch.object(verify.core, "SYSTEM_DIR", tmp_path):
        result = verify._check_weekly_focus("test", payload, "2026-09-07")
    assert result.status == "mismatch"


def test_check_rendered_daily_brief_catches_stale_render(tmp_path):
    """The exact live failure: weekly_plan.json changes after a brief was
    already rendered -- the rendered .md's stamped fingerprint no longer
    matches, so it must be flagged rather than trusted."""
    import datetime as _dt

    briefs_dir = tmp_path / "briefs"
    briefs_dir.mkdir()
    target = _dt.date(2026, 8, 19)

    _write(tmp_path / "weekly_plan.json", week_of="2026-08-10", status="active")
    _write(tmp_path / "weekly_plan_draft.json", week_of="2026-08-17",
           status="draft_pending_confirmation", generated_at="2026-08-17T10:00:00Z")

    with patch.object(core, "SYSTEM_DIR", tmp_path), patch.object(verify.core, "SYSTEM_DIR", tmp_path), \
         patch.object(verify, "BRIEFS_DIR", briefs_dir):
        stale_fp = core.weekly_plan_fingerprint()
        _write(briefs_dir / f"{target.isoformat()}-daily-brief.json",
               date=target.isoformat(), weekly_plan_fingerprint=stale_fp)

        # Confirm happens after the render above.
        _write(tmp_path / "weekly_plan.json", week_of="2026-08-17", status="active")
        _write(tmp_path / "weekly_plan_draft.json", week_of="2026-08-17",
               status="confirmed", confirmed_at="2026-08-19T14:30:00Z")

        result = verify.check_rendered_daily_brief(target)

    assert result.status == "mismatch"
    assert "stale" in result.detail.lower()


def test_check_rendered_daily_brief_ok_when_fingerprint_current(tmp_path):
    import datetime as _dt

    briefs_dir = tmp_path / "briefs"
    briefs_dir.mkdir()
    target = _dt.date(2026, 8, 19)

    _write(tmp_path / "weekly_plan.json", week_of="2026-08-17", status="active")
    _write(tmp_path / "weekly_plan_draft.json", week_of="2026-08-17",
           status="confirmed", confirmed_at="2026-08-19T14:30:00Z")

    with patch.object(core, "SYSTEM_DIR", tmp_path), patch.object(verify.core, "SYSTEM_DIR", tmp_path), \
         patch.object(verify, "BRIEFS_DIR", briefs_dir):
        current_fp = core.weekly_plan_fingerprint()
        _write(briefs_dir / f"{target.isoformat()}-daily-brief.json",
               date=target.isoformat(), weekly_plan_fingerprint=current_fp)
        result = verify.check_rendered_daily_brief(target)

    assert result.status == "ok"


def test_run_checks_end_to_end_fails_on_stale_state(tmp_path):
    """Full run_checks() sweep, mirroring the live-observed scenario:
    confirm the draft but never re-render -- overall result must be FAIL."""
    import datetime as _dt

    briefs_dir = tmp_path / "briefs"
    briefs_dir.mkdir()
    published_dir = tmp_path / "published"
    published_dir.mkdir()
    target = _dt.date(2026, 8, 19)

    _write(tmp_path / "weekly_plan.json", week_of="2026-08-10", status="active")
    _write(tmp_path / "weekly_plan_draft.json", week_of="2026-08-17",
           status="draft_pending_confirmation", generated_at="2026-08-17T10:00:00Z")

    with patch.object(core, "SYSTEM_DIR", tmp_path), patch.object(verify.core, "SYSTEM_DIR", tmp_path), \
         patch.object(verify, "BRIEFS_DIR", briefs_dir), \
         patch.object(verify, "PUBLISHED_DIR", published_dir), \
         patch.object(verify, "DAILY_BRIEF_CACHE", tmp_path / "daily_brief.json"):
        stale_fp = core.weekly_plan_fingerprint()
        _write(briefs_dir / f"{target.isoformat()}-daily-brief.json",
               date=target.isoformat(), weekly_plan_fingerprint=stale_fp)

        _write(tmp_path / "weekly_plan.json", week_of="2026-08-17", status="active")
        # RB-DEFECT-067 still-present scenario: draft never got cleared either.
        results = verify.run_checks(target, api_key=None)

    mismatches = [r for r in results if r.status == "mismatch"]
    assert mismatches, "at least the stuck-draft and stale-render checks must fail here"
    names = {r.name for r in mismatches}
    assert "draft_state_consistency" in names
    assert "rendered_daily_brief" in names
