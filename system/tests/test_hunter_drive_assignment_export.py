"""Tests for scripts/hunter_drive_assignment_export.py.

All orchestrator file paths and the Drive-synced folder are redirected to
tmp_path, same pattern as the other orchestrator test fixtures.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import hunter_orchestrator as ho  # noqa: E402
import hunter_drive_assignment_export as hdae  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cache = tmp_path / ".cache"
    ledger = cache / "hunter_orchestrator"
    jobs = cache / "hunter_pending_jobs"
    drive = tmp_path / "drive_inbox"
    jobs.mkdir(parents=True)
    cfg_path = tmp_path / "config.json"
    cfg = json.loads(ho.CONFIG_PATH.read_text(encoding="utf-8"))
    cfg["engines"]["chatgpt_deep_research"]["enabled"] = True
    cfg["engines"]["chatgpt_work"]["enabled"] = True
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(ho, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(ho, "CACHE_DIR", cache)
    monkeypatch.setattr(ho, "LEDGER_DIR", ledger)
    monkeypatch.setattr(ho, "LEASES_PATH", ledger / "leases.jsonl")
    monkeypatch.setattr(ho, "TELEMETRY_PATH", ledger / "telemetry.jsonl")
    monkeypatch.setattr(ho, "CAPACITY_PATH", ledger / "capacity.jsonl")
    monkeypatch.setattr(ho, "RUNOUTS_PATH", ledger / "runouts.jsonl")
    monkeypatch.setattr(ho, "ADJUSTMENTS_PATH", ledger / "reserve_adjustments.jsonl")
    monkeypatch.setattr(ho, "RESERVE_STATE_PATH", ledger / "reserve_state.json")
    monkeypatch.setattr(ho, "SIGNAL_PATH", cache / "hunter_capacity_watch_signal.json")
    monkeypatch.setattr(ho, "PENDING_JOBS_DIR", jobs)
    monkeypatch.setattr(ho, "QUEUE_PATH", cache / "hunter_priority_queue.json")
    monkeypatch.setattr(ho, "DAILY_PLAN_PATH", ledger / "daily_plan.json")
    monkeypatch.setattr(hdae, "DRIVE_INBOX", drive)
    return {"jobs": jobs, "drive": drive}


def _assignment(target_key="company:brand-example"):
    return {
        "schema": "rb.hunter_priority_assignment.v1", "assignment_id": "priority-example",
        "target_keys": [target_key],
        "subjobs": [{"target_key": target_key, "rank": 1, "suggested_playbook": "enterprise_account_profile",
                     "job": {"schema": "rb.hunter_job.v1", "directive": {
                         "plan": {"payload_schema": "rb.enterprise_account_profile_research.v1"},
                         "packet_requirements": {"known_gap_ids": ["gap:1"], "discovery_domains": ["domain:1"]}}}}],
    }


def _write_job(env, name, assignment):
    (env["jobs"] / f"{name}.json").write_text(json.dumps(assignment), encoding="utf-8")


def test_export_writes_flat_assignment_for_eligible_engine(env):
    _write_job(env, "a", _assignment())
    out = hdae.export_for_engine("chatgpt_deep_research", confirm=True)
    assert out["exported"] is True
    path = env["drive"] / hdae.ENGINE_FILENAMES["chatgpt_deep_research"]
    assert path.exists()
    written = json.loads(path.read_text())
    assert written["schema"] == "rb.hunter_drive_assignment.v1"
    assert written["subjobs"][0]["target_key"] == "company:brand-example"
    assert written["subjobs"][0]["known_gap_ids"] == ["gap:1"]


def test_export_leases_the_job_so_it_is_not_double_assigned(env):
    _write_job(env, "a", _assignment())
    hdae.export_for_engine("chatgpt_deep_research", confirm=True)
    out = hdae.export_for_engine("chatgpt_work", confirm=True)
    assert out["exported"] is False  # nothing left in the queue for Work


def test_dry_run_writes_nothing(env):
    _write_job(env, "a", _assignment())
    out = hdae.export_for_engine("chatgpt_deep_research", confirm=False)
    assert out["exported"] is False  # confirm=False never writes, even with a real eligible job
    assert not (env["drive"] / hdae.ENGINE_FILENAMES["chatgpt_deep_research"]).exists()
    assert ho.job_states() == {}  # and nothing was actually leased


def test_no_eligible_job_clears_a_stale_file(env):
    env["drive"].mkdir(parents=True)
    stale = env["drive"] / hdae.ENGINE_FILENAMES["chatgpt_deep_research"]
    stale.write_text("{}", encoding="utf-8")
    out = hdae.export_for_engine("chatgpt_deep_research", confirm=True)
    assert out["exported"] is False
    assert not stale.exists()


def test_disabled_engine_is_never_exported_to(env, monkeypatch):
    cfg = json.loads(ho.CONFIG_PATH.read_text())
    cfg["engines"]["chatgpt_work"]["enabled"] = False
    ho.CONFIG_PATH.write_text(json.dumps(cfg))
    _write_job(env, "a", _assignment())
    out = hdae.export_for_engine("chatgpt_work", confirm=True)
    assert out["exported"] is False
    assert not (env["drive"] / hdae.ENGINE_FILENAMES["chatgpt_work"]).exists()
