"""HTTP-level tests for the Hunter remote-engine endpoints:
  GET  /hunter/assignment  (getHunterAssignment)
  POST /hunter/submit      (submitHunterPacket)

Uses FastAPI TestClient against the real server module, same pattern as
test_api_rb9_validation.py. Hunter's own file paths are monkeypatched the
same way test_hunter_remote_assignment.py does it, so no real queue or ledger
file is touched; hunter_cycle.sweep() is monkeypatched for submit tests.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:
    pytest.skip("fastapi not installed", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SERVER_PATH = ROOT / "system" / "api" / "server.py"


def _load_server_module():
    name = "rb_api_server_for_hunter_remote_tests"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SERVER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_server = _load_server_module()
_client = TestClient(_server.app, raise_server_exceptions=True, headers={"x-api-key": "test-key"})

import hunter_orchestrator as ho  # noqa: E402
import hunter_cycle  # noqa: E402


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


def _assignment():
    return {
        "schema": "rb.hunter_priority_assignment.v1", "assignment_id": "priority-example",
        "target_keys": ["company:brand-example"],
        "subjobs": [{"target_key": "company:brand-example", "rank": 1, "suggested_playbook": "enterprise_account_profile",
                     "job": {"schema": "rb.hunter_job.v1", "directive": {
                         "plan": {"payload_schema": "rb.enterprise_account_profile_research.v1"},
                         "packet_requirements": {"known_gap_ids": [], "discovery_domains": []}}}}],
    }


def test_assignment_requires_api_key(env):
    resp = _client.get("/hunter/assignment", params={"engine": "chatgpt_deep_research"}, headers={"x-api-key": "wrong"})
    assert resp.status_code == 401


def test_assignment_rejects_unlisted_engine(env):
    resp = _client.get("/hunter/assignment", params={"engine": "claude_co_work"})
    assert resp.status_code == 422  # not one of the Literal's two allowed values


def test_assignment_blocked_when_queue_empty(env):
    resp = _client.get("/hunter/assignment", params={"engine": "chatgpt_deep_research"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "blocked"


def test_assignment_blocked_when_engine_disabled(env):
    resp = _client.get("/hunter/assignment", params={"engine": "chatgpt_work"})  # disabled in this fixture
    assert resp.json()["status"] == "blocked"


def test_assignment_returns_leased_running_job(env):
    (env["jobs"] / "a.json").write_text(json.dumps(_assignment()), encoding="utf-8")
    resp = _client.get("/hunter/assignment", params={"engine": "chatgpt_deep_research"})
    data = resp.json()
    assert data["status"] == "assigned"
    assert ho.job_states()[data["job_id"]]["state"] == "running"


def test_submit_accepted_marks_job_completed(env, monkeypatch):
    (env["jobs"] / "a.json").write_text(json.dumps(_assignment()), encoding="utf-8")
    jid = _client.get("/hunter/assignment", params={"engine": "chatgpt_deep_research"}).json()["job_id"]
    job_path = str(env["jobs"] / "a.json")
    monkeypatch.setattr(hunter_cycle, "sweep", lambda: {"processed": [{"job_path": job_path, "receipt": {"ok": True}}]})
    packet = {"schema": "rb.hunter_research_packet.v1", "targets": [{"target_key": "company:brand-example"}]}
    resp = _client.post("/hunter/submit", json={"job_id": jid, "engine": "chatgpt_deep_research", "packet": packet})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert ho.job_states()[jid]["state"] == "completed"


def test_submit_wrong_job_id_returns_ok_false_not_500(env):
    resp = _client.post("/hunter/submit", json={"job_id": "hj_missing", "engine": "chatgpt_deep_research", "packet": {"schema": "x"}})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_submit_requires_api_key(env):
    resp = _client.post("/hunter/submit", json={"job_id": "hj_x", "engine": "chatgpt_deep_research", "packet": {}},
                         headers={"x-api-key": "wrong"})
    assert resp.status_code == 401


def test_openapi_hunter_yaml_is_served():
    resp = _client.get("/openapi-hunter.yaml")
    assert resp.status_code == 200
    assert "getHunterAssignment" in resp.text
    assert "submitHunterPacket" in resp.text
