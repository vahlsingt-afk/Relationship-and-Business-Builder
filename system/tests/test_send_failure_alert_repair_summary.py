"""
test_send_failure_alert_repair_summary.py — RB-DEFECT-072.

Acceptance criterion: "Failure alerts state what RB tried, what changed,
why recovery stopped, and where the completed-but-undelivered artifacts can
be accessed." send_failure_alert.py reads morning_pipeline.py's own
brief_repair_receipt.json (never re-derives or guesses at what happened).
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import send_failure_alert as sfa  # noqa: E402


def test_body_includes_repair_attempts_when_receipt_present(tmp_path):
    receipt = {
        "date": "2026-09-23",
        "final_passed": False,
        "attempts": [{
            "actions_taken": [{
                "check": "no_duplicate_story_clusters", "doc": "intelligence", "changed": True,
                "removed": [{"title": "loser", "url": "u2"}],
            }],
        }],
    }
    receipt_path = tmp_path / "brief_repair_receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = {"passed": False, "findings": [
        {"check": "no_duplicate_story_clusters", "severity": "fail", "passed": False, "detail": "dup"},
    ]}
    with patch.object(sfa, "REPAIR_RECEIPT_PATH", receipt_path):
        body = sfa._build_body(date(2026, 9, 23), result)

    assert "RB attempted repair before giving up" in body
    assert "removed 1 duplicate headline" in body
    assert "Recovery stopped" in body


def test_body_omits_repair_section_when_no_receipt(tmp_path):
    missing_path = tmp_path / "does_not_exist.json"
    result = {"passed": False, "findings": [
        {"check": "freshness", "severity": "fail", "passed": False, "detail": "stale"},
    ]}
    with patch.object(sfa, "REPAIR_RECEIPT_PATH", missing_path):
        body = sfa._build_body(date(2026, 9, 23), result)
    assert "RB attempted repair" not in body


def test_body_ignores_stale_receipt_from_a_different_day(tmp_path):
    receipt = {"date": "2026-09-20", "final_passed": False, "attempts": []}
    receipt_path = tmp_path / "brief_repair_receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    result = {"passed": False, "findings": []}
    with patch.object(sfa, "REPAIR_RECEIPT_PATH", receipt_path):
        body = sfa._build_body(date(2026, 9, 23), result)
    assert "RB attempted repair" not in body


def test_body_points_to_completed_artifact_locations(tmp_path):
    briefs_dir = tmp_path / "briefs"
    briefs_dir.mkdir()
    (briefs_dir / "2026-09-23-intelligence-brief.md").write_text("content", encoding="utf-8")
    result = {"passed": False, "findings": []}
    with patch.object(sfa, "BRIEFS_DIR", briefs_dir), \
         patch.object(sfa, "REPAIR_RECEIPT_PATH", tmp_path / "none.json"):
        body = sfa._build_body(date(2026, 9, 23), result)
    assert "Intelligence brief" in body
    assert "exists" in body
    assert "Daily brief" in body
    assert "not generated this run" in body
