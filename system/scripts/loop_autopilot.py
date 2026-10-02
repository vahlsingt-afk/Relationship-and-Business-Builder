#!/usr/bin/env python3
"""
loop_autopilot.py - orchestrate the existing loop lifecycle modules.

This file is intentionally thin. It calls smart_loops, passive_verification, and
closeout as subprocesses so their own dry-run/confirm guards remain authoritative.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent
PROJECT_DIR = SYSTEM_DIR.parent
CACHE_DIR = SYSTEM_DIR / ".cache"


def _run_json(cmd: list[str]) -> tuple[int, dict, str]:
    proc = subprocess.run(cmd, cwd=PROJECT_DIR, capture_output=True, text=True)
    if proc.returncode != 0:
        return proc.returncode, {}, proc.stderr.strip() or proc.stdout.strip()
    try:
        return proc.returncode, json.loads(proc.stdout), ""
    except json.JSONDecodeError as exc:
        return 1, {}, f"failed to parse JSON from {' '.join(cmd)}: {exc}"


def _write_cache(phase: str, payload: dict) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"loop_autopilot_{phase}.json"
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _summary_for_smart_loops(data: dict) -> dict:
    counts = data.get("counts") or {}
    fresh = data.get("fresh") or []
    return {
        "total": counts.get("total", 0),
        "fresh": counts.get("fresh", 0),
        "deduped": counts.get("deduped", 0),
        "by_type": counts.get("by_type") or {},
        "top_entities": [p.get("entity") for p in fresh[:5] if p.get("entity")],
    }


def _summary_for_passive(data: dict) -> dict:
    counts = data.get("counts") or {}
    return {
        "auto_closeable": counts.get("auto_closeable", 0),
        "possible_resolution": counts.get("possible_resolution", 0),
        "open_loops_scanned": data.get("open_loops_scanned", 0),
    }


def phase_morning(*, apply: bool = False, confirm: bool = False) -> dict:
    py = sys.executable or "python3"
    cmd = [py, str(SCRIPTS_DIR / "smart_loops.py"), "--json"]
    if apply:
        cmd.append("--apply")
        if confirm:
            cmd.append("--confirm")
    rc, data, err = _run_json(cmd)
    payload = {
        "phase": "morning",
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command": cmd,
        "returncode": rc,
        "ok": rc == 0,
        "confirmed": bool(apply and confirm),
        "summary": _summary_for_smart_loops(data) if data else {},
        "result": data,
        "error": err,
    }
    _write_cache("morning", payload)
    return payload


def phase_midday() -> dict:
    py = sys.executable or "python3"
    cmd = [py, str(SCRIPTS_DIR / "passive_verification.py"), "--json"]
    rc, data, err = _run_json(cmd)
    payload = {
        "phase": "midday",
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command": cmd,
        "returncode": rc,
        "ok": rc == 0,
        "confirmed": False,
        "summary": _summary_for_passive(data) if data else {},
        "suggested_closeout_command": "python3 system/scripts/loop_autopilot.py --phase closeout --confirm",
        "result": data,
        "error": err,
    }
    _write_cache("midday", payload)
    return payload


def phase_closeout(*, confirm: bool = False) -> dict:
    py = sys.executable or "python3"
    steps = []
    if confirm:
        pv_cmd = [py, str(SCRIPTS_DIR / "passive_verification.py"), "--apply", "--confirm", "--json"]
    else:
        pv_cmd = [py, str(SCRIPTS_DIR / "passive_verification.py"), "--json"]
    pv_rc, pv_data, pv_err = _run_json(pv_cmd)
    steps.append({"name": "passive_verification", "command": pv_cmd, "returncode": pv_rc, "error": pv_err})

    if confirm:
        close_cmd = [py, str(SCRIPTS_DIR / "closeout.py"), "--write", "--confirm", "--json"]
    else:
        close_cmd = [py, str(SCRIPTS_DIR / "closeout.py"), "--json"]
    close_rc, close_data, close_err = _run_json(close_cmd)
    steps.append({"name": "closeout", "command": close_cmd, "returncode": close_rc, "error": close_err})

    payload = {
        "phase": "closeout",
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ok": pv_rc == 0 and close_rc == 0,
        "confirmed": confirm,
        "steps": steps,
        "passive_verification": pv_data,
        "closeout": close_data,
        "summary": {
            "auto_closeable": ((pv_data.get("counts") or {}).get("auto_closeable") if pv_data else 0),
            "possible_resolution": ((pv_data.get("counts") or {}).get("possible_resolution") if pv_data else 0),
            "closeout_counts": close_data.get("counts") or {},
        },
    }
    _write_cache("closeout", payload)
    return payload


def _print_payload(payload: dict) -> None:
    phase = payload.get("phase")
    status = "PASS" if payload.get("ok") else "FAIL"
    print(f"[{status}] loop_autopilot {phase} ({'confirmed' if payload.get('confirmed') else 'dry-run'})")
    summary = payload.get("summary") or {}
    if phase == "morning":
        print(
            f"  proposals: total={summary.get('total', 0)} fresh={summary.get('fresh', 0)} "
            f"deduped={summary.get('deduped', 0)}"
        )
        if summary.get("by_type"):
            print("  by_type: " + ", ".join(f"{k}={v}" for k, v in summary["by_type"].items()))
    elif phase == "midday":
        print(
            f"  auto_closeable={summary.get('auto_closeable', 0)} "
            f"possible_resolution={summary.get('possible_resolution', 0)}"
        )
        print(f"  next: {payload.get('suggested_closeout_command')}")
    elif phase == "closeout":
        counts = summary.get("closeout_counts") or {}
        print("  closeout counts: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    if payload.get("error"):
        print(f"  error: {payload['error']}", file=sys.stderr)


def _smoke() -> int:
    failures: list[str] = []

    def ck(cond: bool, msg: str) -> None:
        mark = "OK" if cond else "FAIL"
        print(f"  {mark}   {msg}")
        if not cond:
            failures.append(msg)

    smart = {"counts": {"total": 3, "fresh": 2, "deduped": 1, "by_type": {"follow_up": 1, "meeting_prep": 1}}, "fresh": [{"entity": "Amy Spytko"}, {"entity": "Patrick Nelson"}]}
    passive = {"counts": {"auto_closeable": 2, "possible_resolution": 1}, "open_loops_scanned": 5}
    ck(_summary_for_smart_loops(smart)["fresh"] == 2, "smart_loops fresh count summarized")
    ck(_summary_for_smart_loops(smart)["deduped"] == 1, "smart_loops deduped count summarized")
    ck(_summary_for_smart_loops(smart)["by_type"]["meeting_prep"] == 1, "smart_loops by_type preserved")
    ck(_summary_for_smart_loops(smart)["top_entities"] == ["Amy Spytko", "Patrick Nelson"], "top entities extracted")
    ck(_summary_for_passive(passive)["auto_closeable"] == 2, "passive auto_closeable summarized")
    ck(_summary_for_passive(passive)["possible_resolution"] == 1, "passive possible_resolution summarized")
    ck(_summary_for_passive(passive)["open_loops_scanned"] == 5, "passive open loop count summarized")
    payload = {"phase": "morning", "ok": True, "confirmed": False, "summary": _summary_for_smart_loops(smart)}
    ck(payload["confirmed"] is False, "dry-run payload is not confirmed")
    ck("follow_up" in payload["summary"]["by_type"], "morning summary includes proposal type")
    cache_path = _write_cache("smoke", {"phase": "smoke", "ok": True})
    ck(cache_path.exists(), "cache writer creates loop_autopilot_smoke.json")
    ck(json.loads(cache_path.read_text())["ok"] is True, "cache writer stores valid JSON")
    ck((sys.executable or "python3") is not None, "python executable available for subprocess orchestration")
    ck((SCRIPTS_DIR / "smart_loops.py").exists(), "smart_loops script exists")
    ck((SCRIPTS_DIR / "passive_verification.py").exists(), "passive_verification script exists")
    ck((SCRIPTS_DIR / "closeout.py").exists(), "closeout script exists")
    ck(date.today().isoformat(), "date context available")
    print(f"--- loop_autopilot smoke complete: {len(failures)} failure(s) ---")
    return 1 if failures else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--phase", choices=["morning", "midday", "closeout"], help="Lifecycle phase to run.")
    p.add_argument("--apply", action="store_true", help="Morning only: apply smart loop proposals. Requires --confirm.")
    p.add_argument("--confirm", action="store_true", help="Allow phase actions that write canonical/artifact state.")
    p.add_argument("--json", action="store_true", help="Emit JSON.")
    p.add_argument("--smoke", action="store_true", help="Run in-memory regression.")
    args = p.parse_args()

    if args.smoke:
        return _smoke()
    if not args.phase:
        p.error("--phase is required unless --smoke is used")
    if args.apply and not args.confirm:
        print("ERROR: --apply requires --confirm.", file=sys.stderr)
        return 2
    if args.phase != "morning" and args.apply:
        print("ERROR: --apply is only valid with --phase morning.", file=sys.stderr)
        return 2

    if args.phase == "morning":
        payload = phase_morning(apply=args.apply, confirm=args.confirm)
    elif args.phase == "midday":
        payload = phase_midday()
    else:
        payload = phase_closeout(confirm=args.confirm)

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        _print_payload(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
