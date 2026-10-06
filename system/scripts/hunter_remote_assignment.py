#!/usr/bin/env python3
"""Remote-assignment glue for a ChatGPT-native Task calling the RBB API.

ChatGPT's own scheduled Tasks run on OpenAI's servers, not on this Mac, and
cannot read local files -- that's the whole reason the paused "Hunter 90+ Gap
Cycle" Task failed ("the local RBB control plane files ... are not mounted
here"). This module is what lets a Task reach the real, live Hunter queue
instead: two functions, called by two new API routes (`getHunterAssignment`,
`submitHunterPacket`), that reuse the exact same orchestrator internals the
local dispatch path already uses -- `engine_eligibility`, leasing, `sweep`,
`complete`. Nothing here is a second implementation of Hunter's governance.

Unlike the local launchd-dispatched engines, a remote assignment is leased and
immediately marked `running` in the same call: the HTTP request itself proves
the engine is actively working right now, so there is no idle-hold window to
protect against the way there is between a local lease and a human starting
ChatGPT Work by hand (see hunter_orchestrator.py's lease/start split).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import hunter_cycle
import hunter_orchestrator as ho


def _write_validation(job_id: str, accepted: bool, errors: list[str]) -> Path:
    ho.LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    path = ho.LEDGER_DIR / f"remote_{job_id}.validation.json"
    path.write_text(json.dumps({"accepted": accepted, "errors": errors}) + "\n", encoding="utf-8")
    return path


def remote_assign(engine: str) -> dict:
    """Lease and immediately start the next eligible queued job for `engine`.

    Returns {"status": "assigned", "job_id": ..., "assignment": <the full
    pending-job file content, same shape hunter_cycle.py prepare-priority
    writes>} or {"status": "blocked", "reason": ...}. Never fabricates an
    assignment: a blocked response means no canonical state changed.
    """
    cfg = ho.load_config()
    if engine not in cfg["engines"]:
        return {"status": "blocked", "reason": f"unknown engine: {engine}"}
    ok, why = ho.engine_eligibility(engine)
    if not ok:
        return {"status": "blocked", "reason": why}
    jobs = ho.queued_jobs()
    if not jobs:
        return {"status": "blocked", "reason": "queue is empty"}
    job = jobs[0]
    if not ho._not_before_passed(job["job_id"]):
        return {"status": "blocked", "reason": "top-ranked job is capacity_blocked until its not_before time"}
    ho._transition(
        job["job_id"], "leased", engine=engine, attempt=ho._attempt_count(job["job_id"]) + 1,
        capacity_at_dispatch=ho.latest_capacity(engine), reserve_at_dispatch=ho.effective_reserve(engine),
        job_path=job["path"],
    )
    ho.cmd_start(argparse.Namespace(job_id=job["job_id"], note=f"remote assignment to {engine}"))
    assignment = ho._load_json(Path(job["path"]), {})
    return {"status": "assigned", "job_id": job["job_id"], "assignment": assignment}


def remote_submit(job_id: str, engine: str, response: dict) -> dict:
    """Validate and finalize a packet (or bundle) returned by a remote engine.

    Runs the exact same hunter_cycle.sweep() every other engine's output goes
    through -- no mutation is applied here beyond what sweep's dry-run
    (confirm=False) already does for every engine; canonical writes stay a
    separate, reviewed step. Returns {"ok": bool, "receipt": ...}.
    """
    lease = ho.job_states().get(job_id)
    if not lease or lease.get("engine") != engine or lease.get("state") not in ("leased", "running"):
        return {"ok": False, "job_id": job_id,
                "errors": [f"job {job_id} is not an active {engine} assignment (state={lease and lease.get('state')})"]}
    job_path = Path(lease["job_path"])
    inbox = ho.ROOT / "inbox" / "hunter_packets"
    inbox.mkdir(parents=True, exist_ok=True)
    packet_path = inbox / f"{engine}-remote-{job_id}-{int(time.time())}.json"
    packet_path.write_text(json.dumps(response, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    sweep_result = hunter_cycle.sweep()
    receipt = None
    for entry in sweep_result.get("processed") or []:
        if entry.get("job_path") == str(job_path):
            receipt = entry.get("receipt")
            break
    accepted = bool(receipt and receipt.get("ok"))
    val_path = _write_validation(job_id, accepted, [] if accepted else [json.dumps(receipt or sweep_result)[:1000]])
    ho.cmd_complete(argparse.Namespace(job_id=job_id, validation_json=str(val_path), telemetry_json=None))
    return {"ok": accepted, "job_id": job_id, "packet_path": str(packet_path), "receipt": receipt}
