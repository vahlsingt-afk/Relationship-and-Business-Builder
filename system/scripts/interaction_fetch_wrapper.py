#!/usr/bin/env python3
"""
interaction_fetch_wrapper.py — single entry point called by the LaunchAgent.

Runs both fetchers (messages + calls) with a sensible default window, then
refreshes the interaction_overlay cache. Logs every step with timestamps so
the launchd log file is human-readable.

Intended to be called by `~/Library/LaunchAgents/com.relationshipbuilder.interaction-fetch.plist`.
Can also be run by hand for testing:

    python3 system/scripts/interaction_fetch_wrapper.py
    python3 system/scripts/interaction_fetch_wrapper.py --days 7
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402


def log(msg: str) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] {msg}", flush=True)


def run(label: str, cmd: list[str]) -> int:
    log(f"-> {label}: {' '.join(cmd)}")
    rc = subprocess.run(cmd, capture_output=True, text=True)
    if rc.stdout:
        for line in rc.stdout.strip().splitlines():
            log(f"   stdout: {line}")
    if rc.stderr:
        for line in rc.stderr.strip().splitlines():
            log(f"   stderr: {line}")
    log(f"   exit: {rc.returncode}")
    return rc.returncode


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30,
                   help="Window for routine refresh. First-run install uses --days 365.")
    args = p.parse_args()

    py = sys.executable or "/usr/bin/python3"

    log("=== interaction_fetch_wrapper start ===")
    log(f"python={py}")
    log(f"project_dir={core.PROJECT_DIR}")

    failures = 0

    rc = run(
        "fetch_apple_messages",
        [py, str(SCRIPTS_DIR / "fetch_apple_messages.py"), "--days", str(args.days)],
    )
    if rc != 0:
        failures += 1

    rc = run(
        "fetch_apple_calls",
        [py, str(SCRIPTS_DIR / "fetch_apple_calls.py"), "--days", str(args.days)],
    )
    if rc != 0:
        failures += 1

    # Refresh the overlay cache so the next daily brief / MCP / HTTP read is cheap
    rc = run(
        "interaction_overlay (cache)",
        [py, str(SCRIPTS_DIR / "interaction_overlay.py"), "--cache", "--json"],
    )
    if rc != 0:
        failures += 1

    log(f"=== interaction_fetch_wrapper end (failures={failures}) ===")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
