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


def _bare_single_job(target_key="company:brand-example"):
    # RB-DEFECT-2026-10-09: the real shape hunter_cycle.py's prepare-priority
    # --queue writes for a single target with no bundle companion -- the bare
    # rb.hunter_cycle_job.v1 job object itself, no top-level "subjobs" at all.
    return {
        "schema": "rb.hunter_cycle_job.v1",
        "directive": {
            "plan": {"playbook": "enterprise_account_profile", "payload_schema": "rb.brand_company_profile.v1"},
            "packet_requirements": {
                "target_keys": [target_key],
                "known_gap_ids": ["gap:1"], "discovery_domains": ["domain:1"],
            },
        },
    }


def test_export_handles_the_bare_single_job_shape_with_no_subjobs_wrapper(env):
    _write_job(env, "a", _bare_single_job())
    out = hdae.export_for_engine("chatgpt_deep_research", confirm=True)
    assert out["exported"] is True
    written = json.loads((env["drive"] / hdae.ENGINE_FILENAMES["chatgpt_deep_research"]).read_text())
    assert written["subjobs"][0]["target_key"] == "company:brand-example"
    assert written["subjobs"][0]["suggested_playbook"] == "enterprise_account_profile"
    assert written["subjobs"][0]["known_gap_ids"] == ["gap:1"]


def test_a_malformed_job_releases_its_lease_instead_of_crashing(env):
    # Neither shape: no "subjobs" and no directive.packet_requirements.target_keys.
    _write_job(env, "a", {"schema": "rb.hunter_cycle_job.v1", "directive": {"plan": {}, "packet_requirements": {}}})
    out = hdae.export_for_engine("chatgpt_deep_research", confirm=True)
    assert out["exported"] is False
    assert out["reason"] == "malformed_job_shape"
    assert ho.job_states()[out["job_id"]]["state"] == "queued"  # not left stuck leased


def test_both_engines_can_get_distinct_fresh_jobs_in_the_same_run(env):
    # RB-DEFECT-2026-10-09: before the job_id_for fix, two different single-target
    # bare jobs collided into the same job_id, so leasing one made the other
    # vanish from the queue too -- confirmed live with two real companies.
    ho.cmd_capacity_record(type("A", (), {
        "engine": "chatgpt_work", "daily_used_pct": 0, "weekly_used_pct": 0, "five_hour_used_pct": 0,
        "daily_reset_at": None, "weekly_reset_at": None, "source": "test", "note": "",
    })())
    _write_job(env, "a", _bare_single_job("company:brand-a"))
    _write_job(env, "b", _bare_single_job("company:brand-b"))
    first = hdae.export_for_engine("chatgpt_deep_research", confirm=True)
    second = hdae.export_for_engine("chatgpt_work", confirm=True)
    assert first["exported"] is True
    assert second["exported"] is True
    assert first["job_id"] != second["job_id"]


def test_ensure_queue_not_empty_tops_up_to_one_job_per_engine(env, monkeypatch):
    # RB-DEFECT-2026-10-09: before this fix, topping up stopped at exactly one
    # ready job -- with two engines sharing the queue, whichever export ran
    # first always claimed it, confirmed live: chatgpt_work found "reason:
    # normal" every single run even right after a fresh prepare-priority call.
    calls = {"n": 0}

    def fake_run(*a, **kw):
        calls["n"] += 1
        _write_job(env, f"generated-{calls['n']}", _bare_single_job(f"company:brand-{calls['n']}"))
        return type("R", (), {"returncode": 0})()
    monkeypatch.setattr(hdae.subprocess, "run", fake_run)

    hdae._ensure_queue_not_empty()
    assert len(ho.queued_jobs()) == len(hdae.ENGINE_FILENAMES)
    assert calls["n"] == len(hdae.ENGINE_FILENAMES)


def test_ensure_queue_not_empty_stops_when_a_call_makes_no_progress(env, monkeypatch):
    monkeypatch.setattr(hdae.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0})())
    hdae._ensure_queue_not_empty()  # no job ever gets written -- must not spin or raise
    assert len(ho.queued_jobs()) == 0


def test_main_isolates_one_engines_failure_from_the_other(env, monkeypatch):
    def boom(engine, *, confirm):
        if engine == "chatgpt_deep_research":
            raise RuntimeError("boom")
        return {"engine": engine, "exported": True}
    monkeypatch.setattr(hdae, "export_for_engine", boom)
    monkeypatch.setattr(sys, "argv", ["hunter_drive_assignment_export.py"])
    assert hdae.main([]) == 0  # must not raise even though one engine's call did
