"""RB-DEFECT-067 — weekly-plan render invalidation.

Todd confirmed a weekly-plan draft mid-day (`weekly_plan_generator.py
--confirm`); `weekly_plan.json` updated correctly, but the already-rendered
`system/briefs/YYYY-MM-DD-daily-brief.md` (existence-only cache gate --
`out_md.exists() and not force`) kept serving the pre-confirmation content,
including a stale "awaiting confirmation" warning, until someone manually
passed `--force`. Fixed with `rb_core.weekly_plan_fingerprint()` (a content
hash of weekly_plan.json + weekly_plan_draft.json's week_of/status/
confirmed_at) stamped into each render's companion .json, and
`rb_core.is_weekly_plan_render_current()` to check a cached render against
the CURRENT live fingerprint before trusting it.

These tests cover the two extracted rb_core.py functions directly rather
than exercising a full render_daily_brief.render()/render_intelligence_
brief.render() call, which would require mocking the entire canonical_brief
cache and every sub-renderer just to test one boolean decision -- that's
exactly why the check was factored out on its own.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import rb_core as core


def _write_plan(path: Path, week_of: str, status: str, confirmed_at: str = "") -> None:
    path.write_text(json.dumps({
        "week_of": week_of, "status": status, "confirmed_at": confirmed_at,
    }), encoding="utf-8")


def test_fingerprint_changes_when_plan_status_changes(tmp_path):
    plan_path = tmp_path / "weekly_plan.json"
    draft_path = tmp_path / "weekly_plan_draft.json"
    _write_plan(plan_path, "2026-08-10", "active")
    _write_plan(draft_path, "2026-08-17", "draft_pending_confirmation", "")

    with patch.object(core, "SYSTEM_DIR", tmp_path):
        before = core.weekly_plan_fingerprint()

        # Simulate a real --confirm: the live plan adopts the new week,
        # the draft is marked confirmed (RB-DEFECT-067's other fix).
        _write_plan(plan_path, "2026-08-17", "active")
        _write_plan(draft_path, "2026-08-17", "confirmed", "2026-08-19T10:00:00Z")
        after = core.weekly_plan_fingerprint()

    assert before != after, "confirming a draft must change the fingerprint"


def test_fingerprint_stable_across_unchanged_rewrite(tmp_path):
    """A file re-saved with byte-identical meaningful content (a tool
    touching the file without an actual state change) must NOT force a
    needless re-render -- the fingerprint is built from content
    (week_of/status/confirmed_at), not mtime, specifically to guard against
    this."""
    plan_path = tmp_path / "weekly_plan.json"
    draft_path = tmp_path / "weekly_plan_draft.json"
    _write_plan(plan_path, "2026-08-17", "active")
    _write_plan(draft_path, "2026-08-17", "confirmed", "2026-08-19T10:00:00Z")

    with patch.object(core, "SYSTEM_DIR", tmp_path):
        fp1 = core.weekly_plan_fingerprint()
        # Re-save identical content (e.g. a formatting-only rewrite)
        _write_plan(plan_path, "2026-08-17", "active")
        fp2 = core.weekly_plan_fingerprint()

    assert fp1 == fp2


def test_fingerprint_handles_missing_files(tmp_path):
    """Neither file existing yet (fresh install) must not raise."""
    with patch.object(core, "SYSTEM_DIR", tmp_path):
        fp = core.weekly_plan_fingerprint()
    assert isinstance(fp, str) and fp


def test_is_render_current_false_when_fingerprint_mismatches(tmp_path):
    plan_path = tmp_path / "weekly_plan.json"
    draft_path = tmp_path / "weekly_plan_draft.json"
    meta_path = tmp_path / "2026-08-19-daily-brief.json"

    _write_plan(plan_path, "2026-08-10", "active")
    _write_plan(draft_path, "2026-08-17", "draft_pending_confirmation")

    with patch.object(core, "SYSTEM_DIR", tmp_path):
        stale_fp = core.weekly_plan_fingerprint()
        meta_path.write_text(json.dumps({"weekly_plan_fingerprint": stale_fp}), encoding="utf-8")

        # This is the exact live-observed sequence: a mid-day confirm
        # changes the plan state out from under an already-rendered brief.
        _write_plan(plan_path, "2026-08-17", "active")
        _write_plan(draft_path, "2026-08-17", "confirmed", "2026-08-19T10:00:00Z")

        assert core.is_weekly_plan_render_current(meta_path) is False, (
            "a render taken before the confirm must be treated as stale "
            "once the weekly plan actually changes"
        )


def test_is_render_current_true_when_fingerprint_matches(tmp_path):
    plan_path = tmp_path / "weekly_plan.json"
    draft_path = tmp_path / "weekly_plan_draft.json"
    meta_path = tmp_path / "2026-08-19-daily-brief.json"

    _write_plan(plan_path, "2026-08-17", "active")
    _write_plan(draft_path, "2026-08-17", "confirmed", "2026-08-19T10:00:00Z")

    with patch.object(core, "SYSTEM_DIR", tmp_path):
        current_fp = core.weekly_plan_fingerprint()
        meta_path.write_text(json.dumps({"weekly_plan_fingerprint": current_fp}), encoding="utf-8")
        assert core.is_weekly_plan_render_current(meta_path) is True


def test_is_render_current_false_for_pre_fingerprint_render(tmp_path):
    """A render from before this field existed (no weekly_plan_fingerprint
    key at all) must be treated as stale, not silently trusted forever."""
    meta_path = tmp_path / "2026-08-19-daily-brief.json"
    meta_path.write_text(json.dumps({"date": "2026-08-19", "trust_score": 63}), encoding="utf-8")
    with patch.object(core, "SYSTEM_DIR", tmp_path):
        assert core.is_weekly_plan_render_current(meta_path) is False


def test_is_render_current_false_when_meta_file_missing(tmp_path):
    meta_path = tmp_path / "does-not-exist.json"
    with patch.object(core, "SYSTEM_DIR", tmp_path):
        assert core.is_weekly_plan_render_current(meta_path) is False
