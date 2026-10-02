from __future__ import annotations

import json
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import capture_ingest  # noqa: E402
import rb_core as core  # noqa: E402


def _source() -> dict:
    settings = json.loads(core.SETTINGS_PATH.read_text(encoding="utf-8"))
    return next(
        row for row in settings["capture_sources"]["sources"]
        if row["id"] == "chatgpt_intelligence_drop"
    )


def test_markdown_is_read_as_pretranscribed_text(tmp_path):
    packet = tmp_path / "packet.md"
    packet.write_text("# Evidence\n\nBrand: Example", encoding="utf-8")

    text, method = capture_ingest._extract_text(packet, "pre_transcribed")

    assert text == "# Evidence\n\nBrand: Example"
    assert method == "pre_transcribed"


def test_chatgpt_drop_has_explicit_deep_research_type():
    source = _source()
    assert source["enabled"] is True
    assert source["capture_type"] == "deep_research"
    assert "**/*.md" in source["patterns"]


def test_morning_pipeline_scans_before_processing():
    pipeline = (SCRIPTS / "morning_pipeline.py").read_text(encoding="utf-8")
    assert pipeline.index('_step("capture_ingest_scan"') < pipeline.index('_step("capture_process_all"')


def test_morning_pipeline_imports_platform_research_after_sidecar_sweep_before_review_scan():
    """RB-DEFECT-073: the canonical-import step must run after the sidecar
    coverage sweep (same sidecar intake surface) and before the competitor
    review scan reads that day's state, matching the existing capture ->
    review-scan ordering discipline this file already asserts above."""
    pipeline = (SCRIPTS / "morning_pipeline.py").read_text(encoding="utf-8")
    sweep_idx = pipeline.index('_step("deep_research_sidecar_sweep"')
    import_idx = pipeline.index('_step("competitor_platform_research_import"')
    review_idx = pipeline.index('_step("competitor_intelligence_review_scan"')
    assert sweep_idx < import_idx < review_idx
