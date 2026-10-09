#!/usr/bin/env python3
"""Hourly, between-mornings recovery for Hunter packets whose ChatGPT-side
Drive save failed.

RB-DEFECT-2026-10-09: confirmed live twice in one day -- a ChatGPT research
session completes real work, then Step 4 (the code-interpreter-file ->
Drive-upload two-step save) fails with a stale/expired container session.
Before this script, recovering from that meant Todd manually downloading
the packet ChatGPT still offers as a chat attachment and either dropping it
into the Drive-synced folder by hand or sending it here for someone to
place and sweep -- and even then, the existing automated recovery path
(hunter_download_watcher.py, which does exactly this: picks a genuine
rb.hunter_research_packet.v1 envelope out of ~/Downloads and copies it into
the Drive inbox) only ran once a day, inside the 4am pre-brief-scan. A
packet that failed its save at, say, 9am sat untouched for up to 19 hours
even if Todd did nothing more than leave the download sitting in
~/Downloads.

This runs the exact same chain the morning scan runs -- hunter_download_watcher
-> hunter_drive_inbox_sync -> hunter_office_manager --confirm ->
hunter_cycle.sweep --confirm -- just hourly, so a recovered packet gets
picked up, validated, and (if clean) actually recorded within the hour
instead of waiting for the next morning and a separate manual confirm.

RB-2026-10-09 (part 2): sweep now runs --confirm here, not dry-run.
Confirming never bypasses review -- hunter_change_dispatch still routes
every mutation_proposal to the real review queue
(hunter_mutation_proposals.jsonl) regardless of confirm; nothing becomes a
canonical "fact" without Todd acting on it there. What --confirm actually
changes is two things, both wanted: (1) a packet that validates cleanly
gets its findings/CoS-handoffs actually written instead of sitting as a
preview someone has to separately re-run with --confirm by hand; (2) a
packet that FAILS validation gets correctly left in PENDING_JOBS_DIR for
the next corrected resubmission (hunter_orchestrator.sync_from_sweep only
preserves a retryable job's file when confirm=True) -- on a dry run it
still gets archived regardless of outcome, which is exactly what forced
manual job-file restoration after every failed validation before this
change.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import hunter_download_watcher  # noqa: E402
import hunter_drive_inbox_sync  # noqa: E402
import hunter_office_manager  # noqa: E402
import hunter_cycle  # noqa: E402


def run() -> dict:
    results = {}
    try:
        results["download_watcher"] = hunter_download_watcher.sweep_downloads()
    except Exception as error:  # noqa: BLE001 -- one step's failure must not block the rest
        results["download_watcher"] = {"error": str(error)}
    try:
        results["drive_inbox_sync"] = hunter_drive_inbox_sync.run()
    except Exception as error:  # noqa: BLE001
        results["drive_inbox_sync"] = {"error": str(error)}
    try:
        results["office_manager"] = hunter_office_manager.run(confirm=True)
    except Exception as error:  # noqa: BLE001
        results["office_manager"] = {"error": str(error)}
    try:
        results["sweep"] = hunter_cycle.sweep(confirm=True)
    except Exception as error:  # noqa: BLE001
        results["sweep"] = {"error": str(error)}
    return {"schema": "rb.hunter_recovery_sweep.v1", **results}


def main() -> int:
    print(json.dumps(run(), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
