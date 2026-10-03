from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import hunter  # noqa: E402
import hunter_cycle  # noqa: E402


def _gatherer_packet():
    return {
        "contract": "rb.gatherer_daily_change_packet.v1",
        "packet_id": "gatherer-20261002T120000Z",
        "generated_at": "2026-10-02T12:00:00Z",
        "changes": [
            {"change_id": "gchg-aaa", "title": "Brand A deploys new platform", "source_url": "https://example.com/a",
             "novelty": "new_to_rbb", "materiality": 95, "scores": {"impact": 85}, "corroborating_sources": []},
        ],
        "hunter_escalations": [
            {"change_id": "gchg-aaa", "target_names": ["Brand A"], "recommended_playbook": "customer_deployment_validation",
             "reason": "Material 24-hour change signal requires source verification before canonical use.", "priority": "high"},
        ],
    }


def test_prepare_gatherer_returns_job_with_before_snapshot(monkeypatch):
    monkeypatch.setattr(hunter, "build_context", lambda keys, modules=None: {"keys": keys})
    job = hunter_cycle.prepare_gatherer(
        "customer_deployment_validation", packet=_gatherer_packet(),
        five_hour_used_pct=20, weekly_used_pct=25, hours_to_weekly_reset=72,
    )
    assert job["schema"] == "rb.hunter_cycle_job.v1"
    assert job["directive"]["source"] == "gatherer"
    snapshot = job["before_snapshot"]
    assert snapshot["schema"] == "rb.hunter_before_snapshot.v1"
    assert "gatherer:gchg-aaa" in snapshot["targets"]
    assert snapshot["targets"]["gatherer:gchg-aaa"]["display_name"] == "Brand A"


def test_cli_prepare_gatherer_subcommand_writes_output(tmp_path, monkeypatch):
    monkeypatch.setattr(hunter, "build_context", lambda keys, modules=None: {"keys": keys})
    packet_path = tmp_path / "packet.json"
    import json
    packet_path.write_text(json.dumps(_gatherer_packet()))
    output_path = tmp_path / "job.json"
    monkeypatch.setattr(sys, "argv", [
        "hunter_cycle.py", "prepare-gatherer", "customer_deployment_validation",
        "--packet", str(packet_path), "--output", str(output_path),
    ])
    rc = hunter_cycle.main()
    assert rc == 0
    result = json.loads(output_path.read_text())
    assert result["schema"] == "rb.hunter_cycle_job.v1"
    assert result["directive"]["source"] == "gatherer"
