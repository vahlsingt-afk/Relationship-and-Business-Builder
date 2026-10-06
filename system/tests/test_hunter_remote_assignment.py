"""Tests for scripts/hunter_remote_assignment.py.

hunter_cycle.sweep() is monkeypatched in every test that calls remote_submit,
so no real file-system sweep of the real inbox runs.
"""
from __future__ import annotations

import json
import sys
from datetime import timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import hunter_orchestrator as ho  # noqa: E402
import hunter_cycle  # noqa: E402
import hunter_remote_assignment as hra  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cache = tmp_path / ".cache"
    ledger = cache / "hunter_orchestrator"
    jobs = cache / "hunter_pending_jobs"
    jobs.mkdir(parents=True)
    cfg_path = tmp_path / "config.json"
    cfg = json.loads(ho.CONFIG_PATH.read_text(encoding="utf-8"))
    cfg["engines"]["chatgpt_deep_research"]["enabled"] = True
    cfg["engines"]["chatgpt_work"]["enabled"] = False
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
    monkeypatch.setattr(ho, "ROOT", tmp_path)
    return {"jobs": jobs}


def _assignment(target_key="company:brand-example"):
    return {
        "schema": "rb.hunter_priority_assignment.v1", "assignment_id": "priority-example",
        "target_keys": [target_key],
        "subjobs": [{"target_key": target_key, "rank": 1, "suggested_playbook": "enterprise_account_profile",
                     "job": {"schema": "rb.hunter_job.v1", "directive": {
                         "plan": {"payload_schema": "rb.enterprise_account_profile_research.v1"},
                         "packet_requirements": {"known_gap_ids": [], "discovery_domains": []}}}}],
    }


def _queue_one(env, assignment=None):
    (env["jobs"] / "a.json").write_text(json.dumps(assignment or _assignment()), encoding="utf-8")


def test_assign_returns_blocked_when_engine_unknown(env):
    out = hra.remote_assign("not_a_real_engine")
    assert out["status"] == "blocked"
    assert "unknown engine" in out["reason"]


def test_assign_returns_blocked_when_engine_ineligible(env):
    out = hra.remote_assign("chatgpt_work")  # disabled in this fixture
    assert out["status"] == "blocked"


def test_assign_returns_blocked_when_queue_empty(env):
    out = hra.remote_assign("chatgpt_deep_research")
    assert out["status"] == "blocked"
    assert "queue is empty" in out["reason"]


def test_assign_leases_and_starts_the_job_immediately(env):
    _queue_one(env)
    out = hra.remote_assign("chatgpt_deep_research")
    assert out["status"] == "assigned"
    jid = out["job_id"]
    assert ho.job_states()[jid]["state"] == "running"  # not just leased: the call itself is the start
    assert out["assignment"]["assignment_id"] == "priority-example"


def test_assign_does_not_release_an_already_leased_job(env):
    _queue_one(env)
    first = hra.remote_assign("chatgpt_deep_research")
    second = hra.remote_assign("chatgpt_deep_research")
    assert second["status"] == "blocked"
    assert second["reason"] == "queue is empty"
    assert first["job_id"] not in [j["job_id"] for j in ho.queued_jobs()]


def test_submit_rejects_unknown_job(env):
    out = hra.remote_submit("hj_missing", "chatgpt_deep_research", {"schema": "x"})
    assert out["ok"] is False
    assert "not an active" in out["errors"][0]


def test_submit_rejects_wrong_engine(env):
    _queue_one(env)
    jid = hra.remote_assign("chatgpt_deep_research")["job_id"]
    out = hra.remote_submit(jid, "chatgpt_work", {"schema": "x"})
    assert out["ok"] is False


def test_submit_accepted_completes_the_job(env, monkeypatch):
    _queue_one(env)
    jid = hra.remote_assign("chatgpt_deep_research")["job_id"]
    job_path = str(env["jobs"] / "a.json")
    monkeypatch.setattr(hunter_cycle, "sweep",
                         lambda: {"processed": [{"job_path": job_path, "receipt": {"ok": True}}]})
    packet = {"schema": "rb.hunter_research_packet.v1", "targets": [{"target_key": "company:brand-example"}]}
    out = hra.remote_submit(jid, "chatgpt_deep_research", packet)
    assert out["ok"] is True
    assert ho.job_states()[jid]["state"] == "completed"
    assert json.loads(Path(out["packet_path"]).read_text()) == packet


def test_submit_rejected_leaves_job_retryable_not_lost(env, monkeypatch):
    _queue_one(env)
    jid = hra.remote_assign("chatgpt_deep_research")["job_id"]
    monkeypatch.setattr(hunter_cycle, "sweep", lambda: {"processed": []})
    out = hra.remote_submit(jid, "chatgpt_deep_research", {"schema": "rb.hunter_research_packet.v1"})
    assert out["ok"] is False
    assert ho.job_states()[jid]["state"] == "retryable"
    assert any(j["job_id"] == jid for j in ho.queued_jobs())
