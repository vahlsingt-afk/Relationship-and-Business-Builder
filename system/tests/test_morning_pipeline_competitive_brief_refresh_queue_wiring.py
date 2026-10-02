"""
test_morning_pipeline_competitive_brief_refresh_queue_wiring.py

RB-2026-09-30: Todd's "Option B" for a Competitive Brief refresh request
sooner than the weekly Friday EOW synthesis pass -- the request only ever
gets recorded (competitive_brief_refresh_queue.request_refresh); this
confirms the processing step is actually wired into the daily scan (not
just built and never called), that it's best-effort (required=False, same
discipline every other scan step in this list uses), and that it runs
after refresh_persisted_briefs so an emailed copy reflects today's
freshest deterministic brief content too, matching the same ordering-
assertion convention test_morning_pipeline_refresh_persisted_briefs_
wiring.py already established.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIPELINE_SRC = (ROOT / "system" / "scripts" / "morning_pipeline.py").read_text(encoding="utf-8")


def test_competitive_brief_refresh_queue_step_present() -> None:
    assert '_step("competitive_brief_refresh_queue"' in PIPELINE_SRC
    assert "competitive_brief_refresh_queue.py" in PIPELINE_SRC
    assert '"process"' in PIPELINE_SRC


def test_runs_after_refresh_persisted_briefs() -> None:
    refresh_idx = PIPELINE_SRC.index('_step("refresh_persisted_briefs"')
    queue_idx = PIPELINE_SRC.index('_step("competitive_brief_refresh_queue"')
    assert refresh_idx < queue_idx


def test_is_not_required() -> None:
    idx = PIPELINE_SRC.index('_step("competitive_brief_refresh_queue"')
    following = PIPELINE_SRC[idx:idx + 300]
    assert "required=False" in following
