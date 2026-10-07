"""Tests for scripts/hunter_claude_transport.py.

No real `claude` process is ever invoked here: run_claude and run_sweep are
monkeypatched in every test that exercises cmd_run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import hunter_orchestrator as ho  # noqa: E402
import hunter_claude_transport as hct  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cache = tmp_path / ".cache"
    ledger = cache / "hunter_orchestrator"
    jobs = cache / "hunter_pending_jobs"
    inbox = tmp_path / "inbox" / "hunter_packets"
    jobs.mkdir(parents=True)
    cfg_path = tmp_path / "config.json"
    cfg = json.loads(ho.CONFIG_PATH.read_text(encoding="utf-8"))
    cfg["engines"]["chatgpt_deep_research"]["enabled"] = False
    cfg["engines"]["claude_code_headless"]["enabled"] = True
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
    monkeypatch.setattr(ho, "DAILY_PLAN_PATH", ledger / "daily_plan.json")  # else dispatch reads the real, stale plan
    monkeypatch.setattr(ho, "ROOT", tmp_path)  # hct.write_response uses ho.ROOT / "inbox" / ...
    return {"jobs": jobs, "ledger": ledger, "inbox": inbox}


def _priority_assignment(target_key="company:brand-example", playbook="enterprise_account_profile"):
    return {
        "schema": "rb.hunter_priority_assignment.v1", "status": "prepared_local_assignment",
        "assignment_id": "priority-" + target_key.split(":")[-1], "target_keys": [target_key],
        "subjobs": [{
            "target_key": target_key, "rank": 1, "suggested_playbook": playbook,
            "job": {
                "schema": "rb.hunter_job.v1",
                "directive": {
                    "plan": {"payload_schema": "rb.enterprise_account_profile_research.v1"},
                    "packet_requirements": {"known_gap_ids": [f"gap:{target_key}:leadership"], "discovery_domains": ["technology stack"]},
                },
            },
        }],
    }


def _record_plenty_of_capacity():
    ho.cmd_capacity_record(type("A", (), {
        "engine": "claude_code_headless", "daily_used_pct": None, "weekly_used_pct": 10,
        "five_hour_used_pct": 5, "daily_reset_at": None, "weekly_reset_at": None,
        "source": "test", "note": "",
    })())


def _lease_one_job(env, assignment):
    # Writes the lease event directly rather than going through cmd_dispatch, which would
    # also run the automatic transport for claude_code_headless -- these tests call cmd_run
    # themselves and need an isolated, still-leased job to call it on.
    path = env["jobs"] / "a.json"
    path.write_text(json.dumps(assignment), encoding="utf-8")
    jid = ho.job_id_for(assignment)
    ho._transition(jid, "leased", engine="claude_code_headless", attempt=1, job_path=str(path))
    return jid


def test_build_prompt_includes_target_schema_and_gaps():
    subjob = _priority_assignment()["subjobs"][0]
    prompt = hct.build_prompt(subjob)
    assert "company:brand-example" in prompt
    assert "rb.enterprise_account_profile_research.v1" in prompt
    assert "gap:company:brand-example:leadership" in prompt
    assert "HUNTER.md" in prompt


def test_schema_guard_rejects_non_priority_assignment(env):
    jid = _lease_one_job(env, {"schema": "something_else", "target_keys": ["company:brand-example"], "subjobs": []})
    with pytest.raises(SystemExit):
        hct.cmd_run(type("A", (), {"job_id": jid, "dry_run": False})())


def test_dry_run_never_calls_claude_and_leaves_job_leased(env, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("run_claude must not be called on a dry run")
    monkeypatch.setattr(hct, "run_claude", boom)
    jid = _lease_one_job(env, _priority_assignment())
    out = hct.cmd_run(type("A", (), {"job_id": jid, "dry_run": True})())
    assert out["dry_run"] is True
    assert ho.job_states()[jid]["state"] == "leased"


def test_rejects_a_job_not_leased_to_this_engine(env):
    # Nothing leased at all yet.
    with pytest.raises(SystemExit):
        hct.cmd_run(type("A", (), {"job_id": "hj_missing", "dry_run": False})())


def test_assemble_response_single_subjob_is_an_ordinary_packet():
    assignment = _priority_assignment()
    packet = {"schema": "rb.hunter_research_packet.v1", "targets": [{"target_key": "company:brand-example"}]}
    out = hct.assemble_response(assignment, [{"packet": packet, "envelope": {}}])
    assert out is packet


def test_assemble_response_two_subjobs_becomes_a_bundle():
    assignment = {"assignment_id": "priority-a-b", "target_keys": ["company:a", "company:b"]}
    p1 = {"targets": [{"target_key": "company:a"}]}
    p2 = {"targets": [{"target_key": "company:b"}]}
    out = hct.assemble_response(assignment, [{"packet": p1, "envelope": {}}, {"packet": p2, "envelope": {}}])
    assert out["schema"] == "rb.hunter_research_bundle_response.v1"
    assert out["bundle_id"] == "priority-a-b"
    assert out["packets"] == [p1, p2]


def test_successful_run_writes_packet_and_completes_the_job(env, monkeypatch):
    assignment = _priority_assignment()
    jid = _lease_one_job(env, assignment)
    packet = {"schema": "rb.hunter_research_packet.v1", "targets": [{"target_key": "company:brand-example"}]}
    monkeypatch.setattr(hct, "run_claude", lambda prompt: {"packet": packet, "envelope": {"total_cost_usd": 0.1}})
    job_path = str(env["jobs"] / "a.json")

    def fake_sweep():
        return {"schema": "rb.hunter_sweep_result.v1", "processed": [{"job_path": job_path, "receipt": {"ok": True}}]}
    monkeypatch.setattr(hct, "run_sweep", fake_sweep)

    out = hct.cmd_run(type("A", (), {"job_id": jid, "dry_run": False})())
    assert out["ok"] is True
    assert ho.job_states()[jid]["state"] == "completed"
    assert Path(out["packet_path"]).exists()
    assert json.loads(Path(out["packet_path"]).read_text()) == packet


def test_failed_validation_marks_job_retryable_not_lost(env, monkeypatch):
    assignment = _priority_assignment()
    jid = _lease_one_job(env, assignment)
    packet = {"schema": "rb.hunter_research_packet.v1", "targets": [{"target_key": "company:brand-example"}]}
    monkeypatch.setattr(hct, "run_claude", lambda prompt: {"packet": packet, "envelope": {}})
    monkeypatch.setattr(hct, "run_sweep", lambda: {"processed": []})  # no match -> not accepted

    out = hct.cmd_run(type("A", (), {"job_id": jid, "dry_run": False})())
    assert out["ok"] is False
    assert ho.job_states()[jid]["state"] == "retryable"
    assert any(j["job_id"] == jid for j in ho.queued_jobs())


def test_claude_failure_does_not_lose_the_job(env, monkeypatch):
    assignment = _priority_assignment()
    jid = _lease_one_job(env, assignment)

    def boom(prompt):
        raise RuntimeError("claude exited 1: rate limited")
    monkeypatch.setattr(hct, "run_claude", boom)

    out = hct.cmd_run(type("A", (), {"job_id": jid, "dry_run": False})())
    assert out["ok"] is False
    assert "rate limited" in out["errors"][0]
    assert ho.job_states()[jid]["state"] == "retryable"


def test_dispatch_runs_the_transport_automatically_for_this_engine(env, monkeypatch):
    called = {}

    def stub_cmd_run(args):
        called["job_id"] = args.job_id
        return {"ok": True, "stubbed": True}
    monkeypatch.setattr(hct, "cmd_run", stub_cmd_run)
    monkeypatch.setitem(sys.modules, "hunter_claude_transport", hct)

    _record_plenty_of_capacity()
    (env["jobs"] / "a.json").write_text(json.dumps(_priority_assignment()), encoding="utf-8")
    res = ho.cmd_dispatch(type("A", (), {"confirm": True})())
    assert called.get("job_id") == res["results"][0]["job_id"]
    assert res["results"][0]["transport"] == {"ok": True, "stubbed": True}


def test_dispatch_contains_a_transport_exception_to_one_job(env, monkeypatch):
    def boom(args):
        raise RuntimeError("transport blew up")
    monkeypatch.setattr(hct, "cmd_run", boom)
    monkeypatch.setitem(sys.modules, "hunter_claude_transport", hct)

    _record_plenty_of_capacity()
    (env["jobs"] / "a.json").write_text(json.dumps(_priority_assignment()), encoding="utf-8")
    res = ho.cmd_dispatch(type("A", (), {"confirm": True})())
    assert "transport blew up" in res["results"][0]["transport_error"]
    # dispatch itself did not raise, and the lease is still recorded
    assert ho.job_states()[res["results"][0]["job_id"]]["state"] == "leased"


# ---- capacity-blocked handling ----

def test_parse_reset_time_extracts_next_occurrence():
    iso = hct._parse_reset_time("You've hit your session limit · resets 4pm (America/Chicago)")
    assert iso is not None
    parsed = __import__("datetime").datetime.fromisoformat(iso)
    assert parsed.tzinfo is not None


def test_parse_reset_time_returns_none_for_unrecognized_text():
    assert hct._parse_reset_time("some other error entirely") is None


def test_capacity_blocked_releases_without_spending_an_attempt(env, monkeypatch):
    assignment = _priority_assignment()
    jid = _lease_one_job(env, assignment)

    def boom(prompt):
        raise hct.ClaudeCapacityBlocked("You've hit your session limit · resets 4pm (America/Chicago)", None)
    monkeypatch.setattr(hct, "run_claude", boom)

    out = hct.cmd_run(type("A", (), {"job_id": jid, "dry_run": False})())
    assert out["ok"] is False
    assert out["capacity_blocked"] is True
    assert ho.job_states()[jid]["state"] == "queued"
    assert any(j["job_id"] == jid for j in ho.queued_jobs())


def test_capacity_blocked_with_unparseable_reset_still_requeues(env, monkeypatch):
    jid = _lease_one_job(env, _priority_assignment())

    def boom(prompt):
        raise hct.ClaudeCapacityBlocked("rate limit hit, try later", None)
    monkeypatch.setattr(hct, "run_claude", boom)

    out = hct.cmd_run(type("A", (), {"job_id": jid, "dry_run": False})())
    assert out["not_before"]  # falls back to a default delay rather than erroring
    assert ho.job_states()[jid]["state"] == "queued"
