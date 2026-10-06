"""Tests for scripts/hunter_orchestrator.py (phases 0-2).

All ledgers, the signal file, and the pending-jobs directory are redirected to
pytest's tmp_path so no real runtime state is touched.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from zoneinfo import ZoneInfo

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import hunter_orchestrator as ho  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cache = tmp_path / ".cache"
    ledger = cache / "hunter_orchestrator"
    jobs = cache / "hunter_pending_jobs"
    jobs.mkdir(parents=True)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text((ho.CONFIG_PATH).read_text(encoding="utf-8"), encoding="utf-8")
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
    return {"jobs": jobs, "ledger": ledger}


def _write_job(env, name, assignment_id, targets):
    job = {"assignment_id": assignment_id, "target_keys": targets, "playbook": "franchise-discovery",
           "bundle_schema": "rb.hunter_research_bundle_response.v1"}
    (env["jobs"] / f"{name}.json").write_text(json.dumps(job), encoding="utf-8")
    return job


def _snapshot(engine, daily=0.0, weekly=0.0, five=0.0, daily_reset_h=48, weekly_reset_h=100):
    now = datetime.now(timezone.utc)
    return ho.cmd_capacity_record(type("A", (), {
        "engine": engine, "daily_used_pct": daily, "weekly_used_pct": weekly, "five_hour_used_pct": five,
        "daily_reset_at": (now + timedelta(hours=daily_reset_h)).isoformat(),
        "weekly_reset_at": (now + timedelta(hours=weekly_reset_h)).isoformat(),
        "source": "test", "note": "",
    })())


def _args(**kw):
    return type("A", (), kw)()


# ---- identity ----

def test_job_id_is_deterministic_and_engine_independent():
    job = {"assignment_id": "priority-x", "target_keys": ["company:b", "company:a"], "playbook": "p"}
    same = dict(reversed(list(job.items())))
    assert ho.job_id_for(job) == ho.job_id_for(same)
    assert ho.job_id_for(job) != ho.job_id_for({**job, "assignment_id": "priority-y"})


# ---- config and reserves ----

def test_starting_reserves_are_sixty_percent(env):
    assert ho.reserve_state() == {"daily_pct": 60, "weekly_pct": 60}


def test_deep_research_has_no_reserve_and_is_eligible(env):
    ok, why = ho.engine_eligibility("chatgpt_deep_research")
    assert ok and "unknown" in why
    assert ho.effective_reserve("chatgpt_deep_research")["daily_pct"] == 0


def test_work_is_admitted_but_disabled_until_enabled(env):
    ok, why = ho.engine_eligibility("chatgpt_work")
    assert not ok and "disabled" in why


def test_enabled_work_needs_a_snapshot(env, monkeypatch):
    cfg = json.loads(ho.CONFIG_PATH.read_text())
    cfg["engines"]["chatgpt_work"]["enabled"] = True
    ho.CONFIG_PATH.write_text(json.dumps(cfg))
    ok, why = ho.engine_eligibility("chatgpt_work")
    assert not ok and "no capacity snapshot" in why


def test_work_respects_reserve_floor(env, monkeypatch):
    cfg = json.loads(ho.CONFIG_PATH.read_text())
    cfg["engines"]["chatgpt_work"]["enabled"] = True
    ho.CONFIG_PATH.write_text(json.dumps(cfg))
    _snapshot("chatgpt_work", daily=30, weekly=30, five=10)
    assert ho.engine_eligibility("chatgpt_work")[0] is True  # 30% used, 60% reserved -> 40% available
    # A reset inside 24h drops the reserve to emergency, so the same usage is eligible there.
    _snapshot("chatgpt_work", daily=41, weekly=10, five=10, daily_reset_h=5)
    assert ho.engine_eligibility("chatgpt_work")[0] is True
    _snapshot("chatgpt_work", daily=41, weekly=10, five=10)
    ok, why = ho.engine_eligibility("chatgpt_work")
    assert not ok and "daily" in why


def test_final_window_drops_reserve_to_emergency(env):
    _snapshot("chatgpt_work", daily=10, weekly=10, five=0, daily_reset_h=5, weekly_reset_h=200)
    eff = ho.effective_reserve("chatgpt_work")
    assert eff["daily_pct"] == 10          # emergency: reset within 24h
    assert eff["weekly_pct"] == 60         # weekly still far away
    assert eff["burn_down"] is True


# ---- reserve review (CoS) ----

def test_runout_raises_reserve_and_is_recorded(env):
    _snapshot("chatgpt_work", daily=50, weekly=50, five=0, daily_reset_h=20, weekly_reset_h=100)
    ho.cmd_runout(_args(engine="chatgpt_work", note="ran out Wed afternoon"))
    out = ho.cmd_reserve_review(None)
    assert ho.reserve_state()["daily_pct"] == 65
    assert any(a["from"] == 60 and a["to"] == 65 for a in out["adjustments"])
    assert ho._read_jsonl(ho.ADJUSTMENTS_PATH)


def test_unused_capacity_lowers_reserve_within_bounds(env):
    _snapshot("chatgpt_work", daily=5, weekly=5, five=0)
    ho.cmd_reserve_review(None)
    assert ho.reserve_state() == {"daily_pct": 55, "weekly_pct": 55}


def test_reserve_never_leaves_bounds(env):
    ho.RESERVE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ho.RESERVE_STATE_PATH.write_text(json.dumps({"daily_pct": 20, "weekly_pct": 85}))
    _snapshot("chatgpt_work", daily=0, weekly=0, five=0)
    ho.cmd_reserve_review(None)
    # weekly 85 lowers to 80 (in bounds); daily at floor 20 stays at 20
    assert ho.reserve_state() == {"daily_pct": 20, "weekly_pct": 80}


# ---- Capacity Watch ----

def test_pause_blocks_dispatch(env, env_jobs=None):
    ho.cmd_signal(_args(mode="pause", note="test"))
    ok, why = ho.mode_throughput_ok()
    assert not ok and "pauses" in why


def test_stale_signal_falls_back_to_normal(env):
    stale = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    ho.SIGNAL_PATH.write_text(json.dumps({"mode": "pause", "set_at": stale}))
    assert ho.signal()["mode"] == "normal"


# ---- leasing, release, completion ----

def test_dispatch_leases_highest_ranked_job_first(env):
    _write_job(env, "b", "priority-b", ["company:b"])
    _write_job(env, "a", "priority-a", ["company:a"])
    ho.QUEUE_PATH.write_text(json.dumps({"items": [{"target_key": "company:a"}, {"target_key": "company:b"}]}))
    res = ho.cmd_dispatch(_args(confirm=True))
    assert res["results"][0]["engine"] == "chatgpt_deep_research"
    job_id = res["results"][0]["job_id"]
    assert ho.job_states()[job_id]["state"] == "leased"
    assert ho.job_states()[job_id]["job_path"].endswith("a.json")


def test_dry_run_writes_nothing(env):
    _write_job(env, "a", "priority-a", ["company:a"])
    res = ho.cmd_dispatch(_args(confirm=False))
    assert res["dry_run"] is True and res["results"][0]["action"] == "would_lease"
    assert not ho.LEASES_PATH.exists()


def test_leased_job_is_not_leased_twice(env):
    _write_job(env, "a", "priority-a", ["company:a"])
    ho.cmd_dispatch(_args(confirm=True))
    res = ho.cmd_dispatch(_args(confirm=True))
    assert res["results"] == []


def test_capacity_blocked_returns_to_queue_after_not_before(env):
    _write_job(env, "a", "priority-a", ["company:a"])
    res = ho.cmd_dispatch(_args(confirm=True))
    jid = res["results"][0]["job_id"]
    ho.cmd_release(_args(job_id=jid, reason="capacity_blocked", not_before=None))
    assert ho.job_states()[jid]["state"] == "queued"
    assert ho.job_states()[jid]["not_before"]
    # not yet eligible because not_before is in the future
    assert ho.cmd_dispatch(_args(confirm=True))["results"] == []


def test_failure_release_counts_attempts_and_terminates(env):
    cfg = json.loads(ho.CONFIG_PATH.read_text())
    cfg["lease"]["max_attempts"] = 1
    ho.CONFIG_PATH.write_text(json.dumps(cfg))
    _write_job(env, "a", "priority-a", ["company:a"])
    jid = ho.cmd_dispatch(_args(confirm=True))["results"][0]["job_id"]
    ho.cmd_release(_args(job_id=jid, reason="timeout", not_before=None))
    assert ho.job_states()[jid]["state"] == "terminal_failed"


def test_completion_with_valid_packet_and_telemetry(env, tmp_path):
    _write_job(env, "a", "priority-a", ["company:a"])
    jid = ho.cmd_dispatch(_args(confirm=True))["results"][0]["job_id"]
    v = tmp_path / "v.json"
    v.write_text(json.dumps({"accepted": True, "errors": []}))
    t = tmp_path / "t.json"
    t.write_text(json.dumps({"engine": "chatgpt_deep_research", "findings_generated": 4}))
    ho.cmd_complete(_args(job_id=jid, validation_json=str(v), telemetry_json=str(t)))
    assert ho.job_states()[jid]["state"] == "completed"
    assert ho._read_jsonl(ho.TELEMETRY_PATH)[0]["findings_generated"] == 4


def test_failed_validation_becomes_retryable_not_lost(env, tmp_path):
    _write_job(env, "a", "priority-a", ["company:a"])
    jid = ho.cmd_dispatch(_args(confirm=True))["results"][0]["job_id"]
    v = tmp_path / "v.json"
    v.write_text(json.dumps({"accepted": False, "errors": ["schema"]}))
    ho.cmd_complete(_args(job_id=jid, validation_json=str(v), telemetry_json=None))
    assert ho.job_states()[jid]["state"] == "retryable"
    assert any(j["job_id"] == jid for j in ho.queued_jobs())


def test_illegal_transition_is_rejected(env):
    with pytest.raises(ValueError):
        ho._transition("hj_none", "completed")


def test_expired_lease_returns_to_queue(env):
    _write_job(env, "a", "priority-a", ["company:a"])
    jid = ho.cmd_dispatch(_args(confirm=True))["results"][0]["job_id"]
    rows = ho._read_jsonl(ho.LEASES_PATH)
    rows[-1]["at"] = (datetime.now(timezone.utc) - timedelta(hours=9)).isoformat()
    ho.LEASES_PATH.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    ho.expire_stale_leases()
    assert ho.job_states()[jid]["state"] == "queued"


def test_plan_is_read_only(env):
    _write_job(env, "a", "priority-a", ["company:a"])
    out = ho.cmd_plan(_args())
    assert out["plan"][0]["engine"] == "chatgpt_deep_research"
    assert not ho.LEASES_PATH.exists()


# ---- daily plan (reserve review in the morning cycle) ----

def _enable_work(env):
    cfg = json.loads(ho.CONFIG_PATH.read_text())
    cfg["engines"]["chatgpt_work"]["enabled"] = True
    ho.CONFIG_PATH.write_text(json.dumps(cfg))


def _put_capacity(engine, daily, weekly, daily_reset, weekly_reset, five=0):
    ho.CAPACITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    rec = {"engine": engine, "recorded_at": "2026-10-06T19:00:00+00:00", "daily_used_pct": daily,
           "weekly_used_pct": weekly, "five_hour_used_pct": five, "daily_reset_at": daily_reset,
           "weekly_reset_at": weekly_reset, "source": "test", "note": ""}
    with ho.CAPACITY_PATH.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")


def test_excess_before_next_reset_is_scheduled_after_hours(env):
    from datetime import datetime as dt
    _enable_work(env)
    # 14:00 Central on 2026-10-06 = 19:00 UTC; daily reset midnight Central = 05:00 UTC on 10-07
    now = dt(2026, 10, 6, 19, 0, tzinfo=timezone.utc)
    _put_capacity("chatgpt_work", daily=10, weekly=10,
                  daily_reset="2026-10-07T05:00:00+00:00", weekly_reset="2026-10-12T05:00:00+00:00")
    for i in range(6):
        _write_job(env, f"j{i}", f"priority-{i}", [f"company:{i}"])
    plan = ho.build_daily_plan(now=now)
    eng = plan["engines"]["chatgpt_work"]
    # reset is 10h away, so the final-window emergency reserve (10%) applies: 100 - 10 - 10 = 80
    assert eng["daily_usable_pct"] == 80
    # burn-down sends the expiring excess after hours, not into the day
    assert eng["planned_day_slots"] == 0
    # the review lowers the weekly reserve 60 -> 55 (90% unused), so weekly usable is 35 -> 7 slots
    assert eng["planned_after_hours_slots"] == 7
    after = [p for p in plan["planned"] if p["window"] == "after_hours"]
    from datetime import datetime as dt
    times = [dt.fromisoformat(p["run_after"]) for p in after]
    assert after and all(t >= dt(2026, 10, 6, 18, 0, tzinfo=ZoneInfo("America/Chicago")) for t in times)
    assert all(t < dt(2026, 10, 7, 0, 0, tzinfo=ZoneInfo("America/Chicago")) for t in times)
    assert ho.DAILY_PLAN_PATH.exists()


def test_reset_before_after_hours_paces_capacity_into_the_day(env):
    from datetime import datetime as dt
    _enable_work(env)
    now = dt(2026, 10, 6, 13, 0, tzinfo=timezone.utc)  # 08:00 Central
    # daily reset 15:00 Central (20:00 UTC) is before the 18:00 after-hours start
    _put_capacity("chatgpt_work", daily=0, weekly=0,
                  daily_reset="2026-10-06T20:00:00+00:00", weekly_reset="2026-10-12T05:00:00+00:00")
    plan = ho.build_daily_plan(now=now)
    eng = plan["engines"]["chatgpt_work"]
    # no after-hours window before this reset, so the usable capacity is paced across the day, not expired
    assert eng["planned_after_hours_slots"] == 0
    # review lowers weekly reserve 60 -> 55, so weekly usable 45 -> 9 slots
    assert eng["planned_day_slots"] == 9
    assert eng["unplanned_expiring_slots"] == 0


def test_planned_slot_is_locked_until_run_after(env):
    from datetime import datetime as dt
    _enable_work(env)
    now = dt(2026, 10, 6, 19, 0, tzinfo=timezone.utc)
    _put_capacity("chatgpt_work", daily=10, weekly=10,
                  daily_reset="2026-10-07T05:00:00+00:00", weekly_reset="2026-10-12T05:00:00+00:00")
    _write_job(env, "a", "priority-a", ["company:a"])
    plan = ho.build_daily_plan(now=now)
    jid = ho.job_id_for(json.loads((env["jobs"] / "a.json").read_text()))
    slot = plan["planned"][0]
    assert ho.plan_slot_for(jid, now=now) is None
    assert ho.plan_slot_for(jid, now=dt.fromisoformat(slot["run_after"]) + timedelta(seconds=1)) is not None


def test_no_snapshot_means_no_slots(env):
    from datetime import datetime as dt
    _enable_work(env)
    plan = ho.build_daily_plan(now=dt(2026, 10, 6, 19, 0, tzinfo=timezone.utc))
    assert plan["engines"]["chatgpt_work"] == {"status": "no_snapshot"}
    assert plan["planned"] == []


def test_deep_research_gets_planned_slots_without_a_reserve(env):
    from datetime import datetime as dt
    # Work/Claude disabled: Deep Research is the only engine. It has no reserve,
    # so it must still get planned slots, or dispatch would never lease its jobs.
    _write_job(env, "a", "priority-a", ["company:a"])
    now = dt(2026, 10, 6, 17, 0, tzinfo=timezone.utc)
    plan = ho.build_daily_plan(now=now)
    assert plan["unassigned_queued_jobs"] == 0
    assert plan["planned"][0]["engine"] == "chatgpt_deep_research"
    assert plan["planned"][0]["window"] == "day"
    jid = ho.job_id_for(json.loads((env["jobs"] / "a.json").read_text()))
    assert ho.plan_slot_for(jid, now=dt.fromisoformat(plan["planned"][0]["run_after"]) + timedelta(seconds=1))
