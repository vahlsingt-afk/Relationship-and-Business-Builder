#!/usr/bin/env python3
"""
post_capture_cascade.py — the downstream cascade a manual GP Outlook or
LinkedIn capture must trigger.

Built for the 2026-09-18 intelligence-cycle repair (system/CLAUDE_HANDOFF_
INTELLIGENCE_CYCLE_REPAIR_2026-09-18.md, "Required implementation outcomes
#2"). Root cause this closes: outlook_gui_capture.py (the GP Outlook manual-
capture script) wrote raw rows into calendar.global-payments.json/email.
global-payments.json/email_sent.global-payments.json and returned -- no
subprocess call, no import of any downstream module, nothing. A capture
landing outside the scheduled 4-5 AM LaunchAgent window was invisible to
every rebuild path until the *next* day's scheduled run, and even that
scheduled run computed meeting-prep/loops (inside refresh_all.py, called
from morning_pipeline.py's `refresh_intelligence_caches` step) *before* that
day's own GP/LinkedIn ingestion and interaction_event_ledger rebuild ran
(see morning_pipeline.py's scan_steps ordering) -- so even the automated
path was computing on stale data.

What this module does NOT need to rebuild: daily_brief.build_report() and
meeting_prep._load_report() already recompute live from raw source files on
every call (confirmed by reading both) -- so most of "the brief reflects
this capture" is already true the moment raw files change. What genuinely
needs an explicit rebuild after a capture:

  1. source_health.json               (refresh_sources.py --health-only)
  2. baseline_index.json last_touch    (refresh_sources.apply_cross_source_last_touch(),
                                         now includes GP source files -- see
                                         system/tests/test_refresh_sources_cross_source_last_touch_gp.py)
  3. interaction_event_ledger.json /
     interaction_current_state.json   (interaction_event_ledger.py)
  4. meeting_prep cache               (meeting_prep.py --for-today)
  5. loop/obligation evaluation       (loop_autopilot.py --phase morning)
  6. system/.cache/daily_brief.json   (daily_brief.py --cache) -- this is
                                         what render_daily_brief.py /
                                         render_intelligence_brief.py read,
                                         so rebuilding it is what makes the
                                         *rendered* brief reflect the capture,
                                         even though build_report() itself
                                         was already live.

Each step is itself an idempotent recompute (not an append), so running
this cascade twice with no new data is safe and produces the same state --
that is what "idempotent" means here, not a dedup ledger of its own.

Every step gets a receipt (name, command, returncode, ok); the whole run
gets one too, appended to CASCADE_RECEIPTS_PATH, so "the cascade ran and
what it did" is durable and auditable the same way every other mutation in
this repair is (see mutation_policy.py). A source-file write alone is never
reported as cascade success -- `ok` is only true when every step's
subprocess/call actually returned success.

CLI:
    python3 post_capture_cascade.py --trigger gp_outlook_manual_capture
    python3 post_capture_cascade.py --trigger linkedin_manual_capture --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402

SYSTEM_DIR = core.SYSTEM_DIR
CACHE_DIR = SYSTEM_DIR / ".cache"
CASCADE_RECEIPTS_PATH = CACHE_DIR / "post_capture_cascade_receipts.jsonl"
CASCADE_LATEST_PATH = CACHE_DIR / "post_capture_cascade_latest.json"
PUBLISHED_DIR = SYSTEM_DIR / "published" / "daily"


def _run(cmd: list[str]) -> dict:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return {
            "returncode": proc.returncode,
            "stdout_tail": (proc.stdout or "")[-2000:],
            "stderr_tail": (proc.stderr or "")[-2000:],
        }
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout_tail": "", "stderr_tail": "timeout"}
    except Exception as exc:  # noqa: BLE001
        return {"returncode": -1, "stdout_tail": "", "stderr_tail": str(exc)}


def _step(name: str, cmd: list[str] | None = None, *, call=None) -> dict:
    """Run one cascade stage. Either a subprocess `cmd` or an in-process
    `call` (a zero-arg callable returning a result dict) -- cross_source_last_
    touch has no CLI flag of its own, so it runs in-process rather than via a
    throwaway subprocess wrapper script."""
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if call is not None:
        try:
            result = call()
            ok = bool(result) and result.get("status") != "failed" and result.get("ok") is not False
        except Exception as exc:  # noqa: BLE001
            result = {"error": str(exc)}
            ok = False
        return {"name": name, "call": True, "started_at": started, "status": "pass" if ok else "fail", "result": result}

    result = _run(cmd)
    ok = result["returncode"] == 0
    return {
        "name": name,
        "command": cmd,
        "started_at": started,
        "status": "pass" if ok else "fail",
        "result": result,
    }


def _brief_already_published(today: date) -> bool:
    return (PUBLISHED_DIR / today.isoformat() / "brief.json").exists()


def run_cascade(*, today: date | None = None, trigger: str, py: str | None = None) -> dict:
    """Run the full post-capture cascade and return a receipt.

    `trigger` names the event that caused this cascade to run (e.g.
    "gp_outlook_manual_capture", "linkedin_manual_capture") -- it is
    recorded on the receipt, not used to branch behavior; every trigger
    gets the same full cascade, because every trigger can affect
    interaction state, meeting prep, loops, and the brief alike.
    """
    today = today or date.today()
    py = py or sys.executable or "python3"
    date_args = ["--date", today.isoformat()]

    steps: list[dict] = []

    steps.append(_step("source_health_recompute", [
        py, str(SCRIPTS_DIR / "refresh_sources.py"), "--health-only",
    ]))

    def _cross_source_last_touch_call() -> dict:
        import refresh_sources as rs
        return rs.apply_cross_source_last_touch()

    steps.append(_step("cross_source_last_touch", call=_cross_source_last_touch_call))

    steps.append(_step("interaction_event_ledger", [
        py, str(SCRIPTS_DIR / "interaction_event_ledger.py"), "--json",
    ]))

    steps.append(_step("meeting_prep", [
        py, str(SCRIPTS_DIR / "meeting_prep.py"), "--for-today", *date_args,
    ]))

    steps.append(_step("loop_autopilot_morning", [
        py, str(SCRIPTS_DIR / "loop_autopilot.py"), "--phase", "morning", "--json",
    ]))

    steps.append(_step("daily_brief_cache_rebuild", [
        py, str(SCRIPTS_DIR / "daily_brief.py"), "--cache", "--dry-run", *date_args,
    ]))

    ok = all(s["status"] == "pass" for s in steps)
    already_published = _brief_already_published(today)

    receipt = {
        "trigger": trigger,
        "today": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "steps": steps,
        "steps_passed": sum(1 for s in steps if s["status"] == "pass"),
        "steps_failed": sum(1 for s in steps if s["status"] != "pass"),
        "ok": ok,
        "post_brief_amendment_needed": ok and already_published,
        "recommended_actions": (
            [
                "Today's brief was already published before this cascade ran. "
                "system/.cache/daily_brief.json now reflects the new capture, "
                "but the published brief/email does not until re-rendered and "
                "re-sent: python3 system/scripts/render_daily_brief.py --date "
                f"{today.isoformat()} (and render_intelligence_brief.py), then "
                "resend via morning_pipeline.py's send_daily_brief_email / "
                "send_intelligence_brief_email steps, or an explicit amendment note."
            ]
            if (ok and already_published) else []
        ),
    }

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with CASCADE_RECEIPTS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt, default=str) + "\n")
    CASCADE_LATEST_PATH.write_text(json.dumps(receipt, indent=2, default=str) + "\n", encoding="utf-8")

    return receipt


def load_receipts(*, since: str | None = None) -> list[dict]:
    if not CASCADE_RECEIPTS_PATH.exists():
        return []
    out: list[dict] = []
    for line in CASCADE_RECEIPTS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since and str(row.get("generated_at") or "") < since:
            continue
        out.append(row)
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--trigger", default="manual", help="What caused this cascade (e.g. gp_outlook_manual_capture).")
    p.add_argument("--date", help="ISO date to treat as today (default: system date).")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    today = date.fromisoformat(args.date) if args.date else date.today()
    receipt = run_cascade(today=today, trigger=args.trigger)

    if args.json:
        print(json.dumps(receipt, indent=2, default=str))
    else:
        status = "OK" if receipt["ok"] else "FAILED"
        print(f"post_capture_cascade [{status}] trigger={receipt['trigger']} "
              f"{receipt['steps_passed']}/{len(receipt['steps'])} steps passed")
        for s in receipt["steps"]:
            print(f"  [{s['status']}] {s['name']}")
        if receipt["post_brief_amendment_needed"]:
            print("  NOTE: today's brief was already published -- re-render/re-send needed.")
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
