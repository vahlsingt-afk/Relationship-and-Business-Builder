"""
test_morning_pipeline_refresh_persisted_briefs_wiring.py

RB-2026-09-25: Team Portal's owner-only-generated documents (Canonical
Background Brief, Competitive Brief, Battle Card) never call an LLM and a
teammate's view is always read-only -- so nothing regenerates them unless
Todd personally clicks the button. refresh_persisted_briefs.py closes that
gap; this confirms it's actually wired into the daily scan (not just built
and never called), and that it runs after the steps most likely to feed it
new confirmed facts (the promotion scans, the deep-research sidecar sweep,
and post_capture_cascade_resync), matching the exact ordering-assertion
convention test_morning_pipeline_post_capture_resync_ordering.py already
established.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIPELINE_SRC = (ROOT / "system" / "scripts" / "morning_pipeline.py").read_text(encoding="utf-8")


def test_refresh_persisted_briefs_step_present() -> None:
    assert '_step("refresh_persisted_briefs"' in PIPELINE_SRC
    assert "refresh_persisted_briefs.py" in PIPELINE_SRC


def test_refresh_persisted_briefs_runs_after_its_real_input_sources() -> None:
    refresh_idx = PIPELINE_SRC.index('_step("refresh_persisted_briefs"')
    for step_name in (
        "ownership_promotion_scan",
        "executive_move_promotion_scan",
        "deep_research_sidecar_sweep",
        "competitor_intelligence_review_scan",
        "post_capture_cascade_resync",
    ):
        step_idx = PIPELINE_SRC.index(f'_step("{step_name}"')
        assert step_idx < refresh_idx, f"{step_name} must run before refresh_persisted_briefs"


def test_refresh_persisted_briefs_is_not_required(monkeypatch=None) -> None:
    """A bad subject inside the refresh (e.g. one competitor record that
    fails to render) must never fail the whole daily pipeline -- same
    required=False discipline every other scan step in this list uses."""
    idx = PIPELINE_SRC.index('_step("refresh_persisted_briefs"')
    following = PIPELINE_SRC[idx:idx + 300]
    assert "required=False" in following
