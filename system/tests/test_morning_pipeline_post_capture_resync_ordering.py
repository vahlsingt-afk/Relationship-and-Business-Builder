"""
test_morning_pipeline_post_capture_resync_ordering.py

RB-DEFECT-2026-09-18 (intelligence-cycle repair gap #1): refresh_intelligence_
caches (refresh_all.py, called early in morning_pipeline.py's scan_steps)
runs meeting_prep.py and loop_autopilot.py near its own end -- but that
happens BEFORE rebuild_contact_index, identity_match_scan,
linkedin_export_watcher_scan, hubspot/sms/contacts/whatsapp/outlook
ingestion, and interaction_event_ledger have all run later in the SAME
scan_steps list. So even the routine scheduled pipeline computed meeting-
prep/loop state from stale interaction data on every normal run, not only
on a late manual-capture day.

Fix: a post_capture_cascade_resync step, placed last in scan_steps (after
every ingestion step, including competitor_intelligence_review_scan), that
unconditionally re-runs the same cascade (source health, cross-source
last-touch, interaction ledger, meeting prep, loops, brief cache) a manual
GP/LinkedIn capture already triggers -- guaranteeing the build phase always
reads post-ingestion state regardless of refresh_all.py's own internal
ordering, without restructuring that shared, heavily-used entrypoint.

Follows the exact ordering-assertion convention already established in
test_auto_process_pending_captures.py::TestMorningPipelineWiresAutoProcessing.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIPELINE_SRC = (ROOT / "system" / "scripts" / "morning_pipeline.py").read_text(encoding="utf-8")


def test_post_capture_cascade_resync_step_present() -> None:
    assert '_step("post_capture_cascade_resync"' in PIPELINE_SRC
    assert "post_capture_cascade.py" in PIPELINE_SRC


def test_resync_runs_after_every_ingestion_step_in_scan_steps() -> None:
    resync_idx = PIPELINE_SRC.index('_step("post_capture_cascade_resync"')
    for step_name in (
        "refresh_intelligence_caches",
        "rebuild_contact_index",
        "identity_match_scan",
        "linkedin_export_watcher_scan",
        "hubspot_ingest_scan",
        "linkedin_content_mutation",
        "sms_content_mutation",
        "contacts_ingest_scan",
        "whatsapp_ingest_scan",
        "outlook_manual_ingest_scan",
        "outlook_gui_capture_ingest",
        "interaction_event_ledger",
        "capture_process_all",
        "competitor_intelligence_review_scan",
    ):
        step_idx = PIPELINE_SRC.index(f'_step("{step_name}"')
        assert step_idx < resync_idx, f"{step_name} must run before post_capture_cascade_resync"


def test_resync_runs_before_the_build_phase_reads_the_brief_cache() -> None:
    resync_idx = PIPELINE_SRC.index('_step("post_capture_cascade_resync"')
    write_idx = PIPELINE_SRC.index('_step("write_today_and_manifest"')
    assert resync_idx < write_idx
