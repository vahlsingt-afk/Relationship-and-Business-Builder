#!/usr/bin/env python3
"""Hunter multi-engine orchestrator: leases, capacity reserves, and telemetry.

Phases 0-2 only. This module decides WHICH admitted engine may take a queued
Hunter job and WHEN. It never edits an assignment, never validates packets,
and never writes canonical RBB state. Validation stays in hunter_cycle.py
(finalize / sweep), unchanged.

Config:   system/research/hunter_orchestrator_config.json
Ledgers:  system/.cache/hunter_orchestrator/*.jsonl  (append-only)
State:    system/.cache/hunter_orchestrator/reserve_state.json
Signal:   system/.cache/hunter_capacity_watch_signal.json  (Capacity Watch pacing)

Commands:
  status                     Show config, reserves, capacity, signal, job states
  plan                       Dry run: which engine each queued job would take now
  dispatch [--confirm]       Lease queued jobs to eligible engines (dry run unless --confirm)
  release JOB_ID --reason R  Release a lease (failure, timeout, or operator release)
  complete JOB_ID --validation-json FILE [--telemetry-json FILE]
                             Record a validated outcome and telemetry
  capacity-record ENGINE ... Record a usage snapshot for a usage-model engine
  runout ENGINE --note N     Record that Todd ran out of bandwidth in the period
  reserve-review             CoS pass: adjust reserves from recorded evidence
  signal MODE --note N       Set Capacity Watch pacing mode (pause|slow|normal|accelerate)
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import sys
from contextlib import contextmanager
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent
CONFIG_PATH = ROOT / "research" / "hunter_orchestrator_config.json"
CACHE_DIR = ROOT / ".cache"
LEDGER_DIR = CACHE_DIR / "hunter_orchestrator"
LEASES_PATH = LEDGER_DIR / "leases.jsonl"
TELEMETRY_PATH = LEDGER_DIR / "telemetry.jsonl"
CAPACITY_PATH = LEDGER_DIR / "capacity.jsonl"
RUNOUTS_PATH = LEDGER_DIR / "runouts.jsonl"
ADJUSTMENTS_PATH = LEDGER_DIR / "reserve_adjustments.jsonl"
RESERVE_STATE_PATH = LEDGER_DIR / "reserve_state.json"
SIGNAL_PATH = CACHE_DIR / "hunter_capacity_watch_signal.json"
PENDING_JOBS_DIR = CACHE_DIR / "hunter_pending_jobs"
QUEUE_PATH = CACHE_DIR / "hunter_priority_queue.json"
DAILY_PLAN_PATH = LEDGER_DIR / "daily_plan.json"

# Job lifecycle. Each value lists the states it may move to.
TRANSITIONS = {
    "queued": {"leased"},
    "leased": {"running", "queued", "capacity_blocked", "completed", "validation_failed", "retryable", "terminal_failed"},
    "running": {"queued", "capacity_blocked", "completed", "validation_failed", "retryable", "terminal_failed"},
    "capacity_blocked": {"queued"},
    "validation_failed": {"retryable", "terminal_failed", "queued"},
    "retryable": {"leased", "queued", "terminal_failed"},
    "completed": set(),
    "terminal_failed": {"queued"},  # only via explicit operator reset
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def load_config() -> dict:
    cfg = _load_json(CONFIG_PATH, None)
    if not cfg or cfg.get("schema") != "rb.hunter_orchestrator_config.v1":
        raise SystemExit(f"invalid or missing orchestrator config: {CONFIG_PATH}")
    return cfg


# ---------- append-only ledgers (locked) ----------

def _append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
            fh.flush()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # A torn final write is ignored and reported, never silently repaired.
            rows.append({"event": "unparseable_line_ignored", "raw": line[:200]})
    return rows


# ---------- job identity and state ----------

def job_id_for(job: dict) -> str:
    """Deterministic job ID, identical for any engine that receives the same assignment."""
    targets = job.get("target_keys") or ([job["target_key"]] if job.get("target_key") else [])
    basis = {
        "assignment_id": job.get("assignment_id") or "",
        "target_keys": sorted(targets),
        "playbook": job.get("playbook") or job.get("playbook_id") or "",
        "schema_version": job.get("bundle_schema") or job.get("payload_schema") or job.get("schema") or "",
    }
    digest = hashlib.sha256(json.dumps(basis, sort_keys=True).encode("utf-8")).hexdigest()
    return "hj_" + digest[:20]


def job_states() -> dict[str, dict]:
    """Latest known record per job ID, derived from the lease ledger."""
    latest: dict[str, dict] = {}
    for row in _read_jsonl(LEASES_PATH):
        jid = row.get("job_id")
        if jid:
            latest[jid] = row
    return latest


def _transition(job_id: str, to_state: str, **fields) -> dict:
    current = job_states().get(job_id, {}).get("state", "queued")
    if to_state not in TRANSITIONS.get(current, set()):
        raise ValueError(f"illegal transition for {job_id}: {current} -> {to_state}")
    event = {"job_id": job_id, "state": to_state, "from_state": current, "at": _iso(_now()), **fields}
    _append(LEASES_PATH, event)
    return event


def _active_lease(job_id: str) -> dict | None:
    row = job_states().get(job_id)
    if row and row.get("state") == "leased":
        return row
    return None


def _lease_expired(row: dict, ttl_minutes: int) -> bool:
    try:
        started = datetime.fromisoformat(row["at"])
    except (KeyError, ValueError):
        return True
    return _now() - started > timedelta(minutes=ttl_minutes)


# ---------- capacity, reset windows, reserves ----------

def latest_capacity(engine: str) -> dict | None:
    rows = [r for r in _read_jsonl(CAPACITY_PATH) if r.get("engine") == engine]
    return rows[-1] if rows else None


def _hours_until(reset_at: str | None) -> float | None:
    if not reset_at:
        return None
    try:
        reset = datetime.fromisoformat(reset_at)
    except ValueError:
        return None
    if reset.tzinfo is None:
        reset = reset.replace(tzinfo=timezone.utc)
    return (reset - _now()).total_seconds() / 3600


def _in_after_hours(cfg: dict, now_local: datetime) -> bool:
    start = dtime.fromisoformat(cfg["burn_down"]["after_hours_start"])
    end = dtime.fromisoformat(cfg["burn_down"]["after_hours_end"])
    t = now_local.time()
    return t >= start or t < end  # window wraps midnight


def reserve_state() -> dict:
    """Current daily and weekly reserve percentages, adjusted only by reserve-review."""
    cfg = load_config()
    base = {"daily_pct": cfg["reserve"]["starting_daily_pct"], "weekly_pct": cfg["reserve"]["starting_weekly_pct"]}
    stored = _load_json(RESERVE_STATE_PATH, {})
    return {**base, **{k: stored[k] for k in ("daily_pct", "weekly_pct") if k in stored}}


def effective_reserve(engine: str, now: datetime | None = None) -> dict:
    """Reserve floors that apply right now for one engine.

    Returns the configured daily and weekly reserve, replaced by the emergency
    reserve when a reset falls inside the final-window threshold.
    """
    cfg = load_config()
    if not cfg["engines"][engine].get("reserve_applies"):
        return {"daily_pct": 0, "weekly_pct": 0, "burn_down": False}
    state = reserve_state()
    snap = latest_capacity(engine) or {}
    final_h = cfg["burn_down"]["final_window_hours"]
    emergency = cfg["burn_down"]["emergency_reserve_pct"]
    daily_h = _hours_until(snap.get("daily_reset_at"))
    weekly_h = _hours_until(snap.get("weekly_reset_at"))
    daily = emergency if (daily_h is not None and daily_h <= final_h) else state["daily_pct"]
    weekly = emergency if (weekly_h is not None and weekly_h <= final_h) else state["weekly_pct"]
    return {"daily_pct": daily, "weekly_pct": weekly, "burn_down": daily != state["daily_pct"] or weekly != state["weekly_pct"]}


def signal() -> dict:
    cfg = load_config()["capacity_watch"]
    raw = _load_json(SIGNAL_PATH, {})
    mode = raw.get("mode", cfg["default_mode"])
    if mode not in cfg["modes"]:
        mode = cfg["default_mode"]
    try:
        set_at = datetime.fromisoformat(raw["set_at"])
        if (_now() - set_at) > timedelta(hours=cfg["stale_after_hours"]):
            mode = cfg["stale_behavior"]
    except (KeyError, ValueError):
        mode = cfg["stale_behavior"]
    return {"mode": mode, "raw": raw}


def engine_eligibility(engine: str) -> tuple[bool, str]:
    """Whether an engine may take a new lease right now, with the reason."""
    cfg = load_config()
    ecfg = cfg["engines"].get(engine)
    if not ecfg:
        return False, "unknown engine"
    if not ecfg.get("admitted"):
        return False, "engine not admitted"
    if not ecfg.get("enabled"):
        return False, f"engine disabled: {ecfg.get('enable_when', 'not enabled in config')}"
    if not ecfg.get("reserve_applies"):
        return True, "local execution ledger; remaining allowance unknown"
    snap = latest_capacity(engine)
    if not snap:
        return False, "no capacity snapshot recorded"
    reserve = effective_reserve(engine)
    if (snap.get("daily_used_pct") or 0) >= 100 - reserve["daily_pct"]:
        return False, f"daily usage at reserve floor ({reserve['daily_pct']}% reserved)"
    if (snap.get("weekly_used_pct") or 0) >= 100 - reserve["weekly_pct"]:
        return False, f"weekly usage at reserve floor ({reserve['weekly_pct']}% reserved)"
    if (snap.get("five_hour_used_pct") or 0) >= 100 - reserve["daily_pct"]:
        return False, "short-window allowance at reserve floor"
    return True, "within reserve"


def leases_started_within(hours: float) -> int:
    cutoff = _now() - timedelta(hours=hours)
    n = 0
    for row in _read_jsonl(LEASES_PATH):
        if row.get("state") == "leased":
            try:
                if datetime.fromisoformat(row["at"]) >= cutoff:
                    n += 1
            except (KeyError, ValueError):
                continue
    return n


def burn_down_active(now: datetime | None = None) -> bool:
    """True when any engine's reset is inside the final window (expiring capacity)."""
    cfg = load_config()
    for name, ecfg in cfg["engines"].items():
        if ecfg.get("reserve_applies") and effective_reserve(name)["burn_down"]:
            return True
    return False


def mode_throughput_ok() -> tuple[bool, str]:
    cfg = load_config()
    mode = signal()["mode"]
    if not cfg["capacity_watch"]["modes"][mode].get("dispatch", True):
        return False, f"Capacity Watch mode '{mode}' pauses dispatch"
    cap = cfg["capacity_watch"]["modes"][mode].get("max_leases_per_hour")
    # Expiring capacity after hours is the one case where burn-down may run at accelerate pace.
    if burn_down_active() and _in_after_hours(cfg, datetime.now(ZoneInfo(cfg["burn_down"]["timezone"]))):
        cap = max(cap or 0, cfg["capacity_watch"]["modes"]["accelerate"]["max_leases_per_hour"])
    if cap is not None and leases_started_within(1) >= cap:
        return False, f"Capacity Watch mode '{mode}' limit of {cap} leases/hour reached"
    return True, mode


def expire_stale_leases() -> list[dict]:
    """Return leases older than the TTL to queued. Attempts already count toward max_attempts."""
    ttl = load_config()["lease"]["ttl_minutes"]
    expired = []
    for jid, row in job_states().items():
        if row.get("state") == "leased" and _lease_expired(row, ttl):
            attempts = _attempt_count(jid)
            target = "terminal_failed" if attempts >= load_config()["lease"]["max_attempts"] else "queued"
            expired.append(_transition(jid, target, reason="lease_expired", attempts=attempts))
    return expired


# ---------- queued jobs ----------

def queued_jobs() -> list[dict]:
    """Pending job files in CoS queue order (rank from the priority queue when present)."""
    rank: dict[str, int] = {}
    queue = _load_json(QUEUE_PATH, {})
    for i, item in enumerate(queue.get("queue") or []):
        key = item.get("target_key") if isinstance(item, dict) else None
        if key:
            rank.setdefault(key, i)
    jobs = []
    if PENDING_JOBS_DIR.is_dir():
        for path in PENDING_JOBS_DIR.glob("*.json"):
            job = _load_json(path, None)
            if not isinstance(job, dict):
                continue
            jid = job_id_for(job)
            state = job_states().get(jid, {}).get("state", "queued")
            if state not in ("queued", "retryable", "capacity_blocked"):
                continue
            targets = job.get("target_keys") or [job.get("target_key", "")]
            best = min((rank.get(t, 10**9) for t in targets), default=10**9)
            jobs.append({"job_id": jid, "path": str(path), "state": state, "rank": best, "job": job})
    jobs.sort(key=lambda j: (j["rank"], j["path"]))
    return jobs


def _not_before_passed(jid: str) -> bool:
    row = job_states().get(jid, {})
    nb = row.get("not_before")
    if not nb:
        return True
    return _now() >= datetime.fromisoformat(nb)


# ---------- commands ----------

def cmd_status(_args) -> dict:
    cfg = load_config()
    out = {
        "signal": signal()["mode"],
        "reserve_state": reserve_state(),
        "engines": {},
        "queued_jobs": len(queued_jobs()),
    }
    for name in cfg["engines"]:
        ok, why = engine_eligibility(name)
        snap = latest_capacity(name)
        out["engines"][name] = {
            "eligible": ok,
            "reason": why,
            "effective_reserve": effective_reserve(name),
            "latest_capacity": snap,
        }
    return out


def cmd_plan(_args) -> dict:
    ok_mode, mode_reason = mode_throughput_ok()
    cfg = load_config()
    plan = []
    for j in queued_jobs():
        choice = None
        if ok_mode:
            for name in sorted(cfg["engines"], key=lambda n: cfg["engines"][n]["preference"]):
                if name not in cfg["routing"]["task_fit"]["default"]:
                    continue
                if engine_eligibility(name)[0]:
                    choice = name
                    break
        plan.append({"job_id": j["job_id"], "rank": j["rank"], "engine": choice, "state": j["state"]})
        if choice is None:
            break  # nothing downstream can run; keep rank order intact
    return {"mode": mode_reason, "plan": plan}


def cmd_dispatch(args) -> dict:
    cfg = load_config()
    if args.confirm:
        expire_stale_leases()
    ok_mode, mode_reason = mode_throughput_ok()
    results = []
    if not ok_mode:
        return {"mode": mode_reason, "leased": [], "dry_run": not args.confirm}
    for j in queued_jobs():
        if not _not_before_passed(j["job_id"]):
            continue
        # Today's plan decides WHEN: a job leases only once its planned slot is due.
        # Jobs with no plan entry (or a future slot) wait without blocking the queue.
        slot = plan_slot_for(j["job_id"]) if DAILY_PLAN_PATH.exists() else None
        if DAILY_PLAN_PATH.exists() and slot is None:
            results.append({"job_id": j["job_id"], "action": "waiting_for_planned_slot"})
            continue
        engine = None
        preferred = [slot["engine"]] if slot else []
        order = preferred + [n for n in sorted(cfg["engines"], key=lambda n: cfg["engines"][n]["preference"]) if n not in preferred]
        for name in order:
            if name in cfg["routing"]["task_fit"]["default"] and engine_eligibility(name)[0]:
                engine = name
                break
        if engine is None:
            break  # higher-ranked job waits; lower-ranked work must not jump ahead
        if not args.confirm:
            results.append({"job_id": j["job_id"], "engine": engine, "action": "would_lease"})
            continue
        ok, why = mode_throughput_ok()
        if not ok:
            results.append({"job_id": j["job_id"], "action": "stopped", "reason": why})
            break
        event = _transition(
            j["job_id"], "leased", engine=engine, attempt=_attempt_count(j["job_id"]) + 1,
            capacity_at_dispatch=latest_capacity(engine), reserve_at_dispatch=effective_reserve(engine),
            job_path=j["path"],
        )
        results.append({"job_id": j["job_id"], "engine": engine, "action": "leased", "at": event["at"]})
    return {"mode": mode_reason, "dry_run": not args.confirm, "results": results}


def _attempt_count(jid: str) -> int:
    return sum(1 for r in _read_jsonl(LEASES_PATH) if r.get("job_id") == jid and r.get("state") == "leased")


def cmd_release(args) -> dict:
    cfg = load_config()
    jid = args.job_id
    row = job_states().get(jid, {})
    cur = row.get("state")
    if cur not in ("leased", "running"):
        raise SystemExit(f"job {jid} is not leased or running (state={cur})")
    if args.reason == "capacity_blocked":
        not_before = args.not_before or _iso(_now() + timedelta(hours=1))
        event = _transition(jid, "capacity_blocked", reason=args.reason, not_before=not_before)
        _transition(jid, "queued", reason="returned after capacity block", not_before=not_before)
        return event
    attempts = _attempt_count(jid)
    max_attempts = cfg["lease"]["max_attempts"]
    if attempts >= max_attempts:
        return _transition(jid, "terminal_failed", reason=args.reason, attempts=attempts)
    return _transition(jid, "queued", reason=args.reason, attempts=attempts)


def cmd_complete(args) -> dict:
    jid = args.job_id
    validation = _load_json(Path(args.validation_json), None)
    if validation is None:
        raise SystemExit("validation JSON missing or unreadable")
    ok = bool(validation.get("accepted"))
    state = "completed" if ok else "validation_failed"
    event = _transition(jid, state, validation_errors=validation.get("errors", []))
    if not ok:
        _transition(jid, "retryable", reason="validation failed")
    if args.telemetry_json:
        tele = _load_json(Path(args.telemetry_json), {})
        tele.update({"job_id": jid, "recorded_at": _iso(_now()), "status": state})
        _append(TELEMETRY_PATH, tele)
    return event


def cmd_capacity_record(args) -> dict:
    cfg = load_config()
    if args.engine not in cfg["engines"]:
        raise SystemExit(f"unknown engine: {args.engine}")
    record = {
        "engine": args.engine,
        "recorded_at": _iso(_now()),
        "daily_used_pct": args.daily_used_pct,
        "weekly_used_pct": args.weekly_used_pct,
        "five_hour_used_pct": args.five_hour_used_pct,
        "daily_reset_at": args.daily_reset_at,
        "weekly_reset_at": args.weekly_reset_at,
        "source": args.source,
        "note": args.note,
    }
    _append(CAPACITY_PATH, record)
    return record


def cmd_runout(args) -> dict:
    record = {"engine": args.engine, "at": _iso(_now()), "note": args.note}
    _append(RUNOUTS_PATH, record)
    return record


def cmd_reserve_review(_args) -> dict:
    """CoS pass. Raise a reserve after a run-out; lower it after an unused period.

    Evidence: run-outs since the last adjustment and the latest snapshot's
    usage at reset. Every change is recorded in reserve_adjustments.jsonl.
    """
    cfg = load_config()
    rules = cfg["reserve"]
    state = reserve_state()
    adjustments = []
    runouts = _read_jsonl(RUNOUTS_PATH)
    for engine in ("chatgpt_work", "claude_co_work"):
        snap = latest_capacity(engine)
        if not snap:
            continue
        # Only run-outs newer than the last adjustment for this engine count as evidence.
        last_adj = max((a["at"] for a in _read_jsonl(ADJUSTMENTS_PATH) if a.get("engine") == engine), default="")
        recent_runouts = [r for r in runouts if r.get("engine") == engine and r.get("at", "") > last_adj]
        for period in ("daily", "weekly"):
            key = f"{period}_pct"
            cur = state[key]
            used = snap.get(f"{period}_used_pct")
            new, reason = cur, None
            if recent_runouts:
                new = min(cur + rules["adjust_step_pct"], rules["max_pct"])
                reason = f"run-out reported ({len(recent_runouts)} note(s))"
            elif used is not None and (100 - used) >= rules["adjust_rule"]["lower_when_unused_pct"]:
                new = max(cur - rules["adjust_step_pct"], rules["min_pct"])
                reason = f"{100 - used:.0f}% unused at last snapshot, no run-out"
            if new != cur:
                state[key] = new
                adjustments.append({"engine": engine, "period": period, "from": cur, "to": new, "reason": reason, "at": _iso(_now())})
                _append(ADJUSTMENTS_PATH, adjustments[-1])
    if adjustments:
        RESERVE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESERVE_STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return {"reserve_state": state, "adjustments": adjustments}


def cmd_signal(args) -> dict:
    cfg = load_config()
    if args.mode not in cfg["capacity_watch"]["modes"]:
        raise SystemExit(f"unknown mode: {args.mode}")
    record = {"mode": args.mode, "set_at": _iso(_now()), "note": args.note}
    SIGNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    SIGNAL_PATH.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def _usable_pct(snap: dict, period: str, reserve: dict) -> float:
    """Percent of this period's total Hunter may still spend: 100 - reserve - used."""
    used = snap.get(f"{period}_used_pct") or 0
    return max(0.0, 100 - reserve[f"{period}_pct"] - used)


def _spread(start: datetime, end: datetime, n: int) -> list[datetime]:
    """n evenly spaced run times inside [start, end). Empty when the window is closed."""
    if n <= 0 or end <= start:
        return []
    step = (end - start) / n
    return [start + step * (i + 0.5) for i in range(n)]


def _next_time_of_day(local: datetime, hhmm: str) -> datetime:
    t = dtime.fromisoformat(hhmm)
    cand = local.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    return cand if cand > local else cand + timedelta(days=1)


def build_daily_plan(now: datetime | None = None) -> dict:
    """Plan today's Hunter slots per engine and write them to daily_plan.json.

    Reserve review runs first, so today's floors reflect the latest evidence.
    Per admitted, reserve-governed engine:
      - usable daily = 100 - reserve - used; usable weekly likewise.
      - half of the usable daily share is paced across the day window (now to 18:00 Central);
      - the rest is scheduled into the after-hours window before the daily reset, because
        unused daily capacity expires at reset.
      - weekly usable caps the total.
    Queue rank order decides which job takes which slot. Dispatch honors each
    slot's run_after time and does not lease outside the plan.
    """
    cfg = load_config()
    pcfg = cfg["daily_plan"]
    tz = ZoneInfo(cfg["burn_down"]["timezone"])
    now = (now or _now()).astimezone(timezone.utc)
    local = now.astimezone(tz)
    review = cmd_reserve_review(None)
    queue = queued_jobs()
    after_hours_start = _next_time_of_day(local, cfg["burn_down"]["after_hours_start"])
    if _in_after_hours(cfg, local):
        after_hours_start = local
    slots: list[dict] = []
    engines_report: dict[str, dict] = {}
    for name, ecfg in cfg["engines"].items():
        if not (ecfg.get("reserve_applies") and ecfg.get("enabled")):
            continue
        snap = latest_capacity(name)
        if not snap:
            engines_report[name] = {"status": "no_snapshot"}
            continue
        reserve = effective_reserve(name, now)
        cost = pcfg["job_cost_estimate_pct"].get(name, 5)
        daily_usable = _usable_pct(snap, "daily", reserve)
        weekly_usable = _usable_pct(snap, "weekly", reserve)
        daily_reset = _parse_dt(snap.get("daily_reset_at"))
        weekly_reset = _parse_dt(snap.get("weekly_reset_at"))
        total_slots = int(min(daily_usable, weekly_usable) // cost)
        after_start = max(now, after_hours_start)
        after_end = min(daily_reset, _next_time_of_day(local, cfg["burn_down"]["after_hours_end"])) if daily_reset else None
        after_window_open = bool(after_end and after_end > after_start)
        share = pcfg["day_share_of_usable_pct"] / 100
        if not after_window_open:
            day_slots = total_slots              # no after-hours before reset: pace it across the day
        elif reserve["burn_down"]:
            day_slots = 0                        # expiring capacity goes to after-hours
        else:
            day_slots = min(int((daily_usable * share) // cost), total_slots)
        after_slots = total_slots - day_slots
        day_end = min(after_hours_start, daily_reset) if daily_reset else after_hours_start
        day_times = _spread(now, day_end, day_slots)
        after_times = _spread(after_start, after_end, after_slots) if after_window_open else []
        # Slots that have no window before reset are not planned; the capacity expires.
        unplanned = after_slots - len(after_times)
        engine_times = [(t, "day") for t in day_times] + [(t, "after_hours") for t in after_times]
        engine_times.sort(key=lambda x: x[0])
        engines_report[name] = {
            "daily_usable_pct": round(daily_usable, 1),
            "weekly_usable_pct": round(weekly_usable, 1),
            "reserve": reserve,
            "daily_reset_at": daily_reset and _iso(daily_reset),
            "weekly_reset_at": weekly_reset and _iso(weekly_reset),
            "planned_day_slots": len(day_times),
            "planned_after_hours_slots": len(after_times),
            "unplanned_expiring_slots": unplanned,
            "cost_estimate_pct": cost,
        }
        for when, window in engine_times:
            slots.append({"engine": name, "run_after": _iso(when), "window": window})
    # Engines without a reserve (Deep Research) have no bandwidth to protect, so they get
    # a fixed daily slot count, paced across the day window. Without this the plan would
    # leave their queued jobs unscheduled and dispatch would never lease them.
    day_end_unreserved = after_hours_start
    for name, count in pcfg.get("unreserved_engine_daily_slots", {}).items():
        ecfg = cfg["engines"].get(name, {})
        if not (ecfg.get("admitted") and ecfg.get("enabled")) or ecfg.get("reserve_applies"):
            continue
        times = _spread(now, day_end_unreserved, int(count))
        engines_report[name] = {"planned_day_slots": len(times), "planned_after_hours_slots": 0,
                                "reserve": None, "note": "no reserve; fixed daily slots"}
        for when in times:
            slots.append({"engine": name, "run_after": _iso(when), "window": "day"})
    slots.sort(key=lambda x: x["run_after"])
    assigned = []
    for job, slot in zip(queue, slots):
        assigned.append({"job_id": job["job_id"], "engine": slot["engine"], "run_after": slot["run_after"],
                         "window": slot["window"], "rank": job["rank"]})
    plan = {
        "schema": "rb.hunter_daily_plan.v1",
        "generated_at": _iso(now),
        "local_time": local.isoformat(timespec="minutes"),
        "signal_mode": signal()["mode"],
        "reserve_review": review,
        "engines": engines_report,
        "queued_jobs": len(queue),
        "planned": assigned,
        "unassigned_queued_jobs": len(queue) - len(assigned),
    }
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_PLAN_PATH.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return plan


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def cmd_daily_plan(_args) -> dict:
    return build_daily_plan()


def plan_slot_for(job_id: str, now: datetime | None = None) -> dict | None:
    """The planned slot for a job if today's plan includes it and its run_after has passed."""
    plan = _load_json(DAILY_PLAN_PATH, {})
    now = (now or _now()).astimezone(timezone.utc)
    for item in plan.get("planned") or []:
        if item.get("job_id") == job_id:
            when = _parse_dt(item.get("run_after"))
            if when and now >= when:
                return item
            return None
    return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("plan").set_defaults(fn=cmd_plan)
    d = sub.add_parser("dispatch")
    d.add_argument("--confirm", action="store_true")
    d.set_defaults(fn=cmd_dispatch)
    r = sub.add_parser("release")
    r.add_argument("job_id")
    r.add_argument("--reason", required=True)
    r.add_argument("--not-before")
    r.set_defaults(fn=cmd_release)
    c = sub.add_parser("complete")
    c.add_argument("job_id")
    c.add_argument("--validation-json", required=True)
    c.add_argument("--telemetry-json")
    c.set_defaults(fn=cmd_complete)
    cr = sub.add_parser("capacity-record")
    cr.add_argument("engine")
    cr.add_argument("--daily-used-pct", type=float)
    cr.add_argument("--weekly-used-pct", type=float)
    cr.add_argument("--five-hour-used-pct", type=float)
    cr.add_argument("--daily-reset-at")
    cr.add_argument("--weekly-reset-at")
    cr.add_argument("--source", required=True)
    cr.add_argument("--note", default="")
    cr.set_defaults(fn=cmd_capacity_record)
    ro = sub.add_parser("runout")
    ro.add_argument("engine")
    ro.add_argument("--note", required=True)
    ro.set_defaults(fn=cmd_runout)
    sub.add_parser("reserve-review").set_defaults(fn=cmd_reserve_review)
    sub.add_parser("daily-plan", help="Reserve review plus today's slot plan").set_defaults(fn=cmd_daily_plan)
    sg = sub.add_parser("signal")
    sg.add_argument("mode")
    sg.add_argument("--note", default="")
    sg.set_defaults(fn=cmd_signal)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = args.fn(args)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
