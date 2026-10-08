#!/usr/bin/env python3
"""Exports the next leased assignment for each non-automatic ChatGPT engine
(chatgpt_deep_research, chatgpt_work) into the Drive-Desktop-synced local
folder, as one flat, easy-to-parse JSON file per engine. A scheduled ChatGPT
Task reads that file via its own Google Drive skill -- no API call either
direction, closing the loop the custom Action approach couldn't: verified
2026-10-08, the Drive connector can read an existing file from this same
folder and (via a code-interpreter-generated file artifact) upload a real
plain-JSON file back into it.

Each engine gets its own job via `hunter_orchestrator.py dispatch --engine X`
(see that module for the scoping this relies on), so Deep Research and Work
never get assigned the same target: whichever runs first leases it, and
queued_jobs() excludes anything already leased.

Writing the per-engine file is the only new thing this does. Everything
downstream is unchanged: the Task's resulting packet lands in the same
RBB Hunter Cycle Inbox folder as any human-run cycle, and the existing
hunter_drive_inbox_sync.py + hunter_cycle.py sweep -- already run every
morning -- validate and finalize it exactly as they do today.

Usage:
  python3 hunter_drive_assignment_export.py [--confirm]

Without --confirm this previews what would be leased and exported, without
leasing anything or touching the Drive-synced folder.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import hunter_orchestrator as ho  # noqa: E402

DRIVE_INBOX = Path(os.environ.get("RB_HUNTER_DRIVE_INBOX", str(Path.home() / "My Drive" / "RBB Hunter Cycle Inbox"))).expanduser()

# Matches hunter_drive_inbox_sync.py's own "hunter-assignment-*.json" convention
# (its importer already ignores files with this prefix as outgoing, not returned).
ENGINE_FILENAMES = {
    "chatgpt_deep_research": "hunter-assignment-chatgpt-deep-research-current.json",
    "chatgpt_work": "hunter-assignment-chatgpt-work-current.json",
}


def _ensure_queue_not_empty() -> None:
    """Self-sufficient like the local launchd flow: if nothing is queued, prepare
    one the same way a human would -- the real CLI entrypoint, not a reimplementation
    of its internals."""
    if ho.queued_jobs():
        return
    subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "hunter_cycle.py"), "prepare-priority", "--queue", "--transport-available"],
        cwd=str(ho.ROOT), capture_output=True, text=True, timeout=60,
    )


def _extract_subjob(subjob: dict) -> dict:
    # Same field paths hunter_claude_transport.py's build_prompt already uses --
    # hunter_cycle.py's prepare() wraps the directive under job["directive"].
    directive = subjob["job"]["directive"]
    return {
        "target_key": subjob["target_key"],
        "suggested_playbook": subjob.get("suggested_playbook") or directive["plan"]["payload_schema"],
        "payload_schema": directive["plan"]["payload_schema"],
        "known_gap_ids": directive["packet_requirements"]["known_gap_ids"],
        "discovery_domains": directive["packet_requirements"]["discovery_domains"],
    }


def export_for_engine(engine: str, *, confirm: bool) -> dict:
    filename = ENGINE_FILENAMES[engine]
    path = DRIVE_INBOX / filename
    result = ho.cmd_dispatch(argparse.Namespace(confirm=confirm, engine=engine))
    leased = [r for r in result.get("results", []) if r.get("engine") == engine and r.get("action") in ("leased", "would_lease")]
    if not leased:
        if confirm and path.exists():
            path.unlink()  # no eligible job right now -- don't leave a stale assignment for the Task to re-research
        return {"engine": engine, "exported": False, "reason": result.get("mode"), "dispatch_results": result.get("results")}

    job_id = leased[0]["job_id"]
    if confirm:
        job_path_str = ho.job_states().get(job_id, {}).get("job_path")
    else:
        # A dry run records no lease event to read job_path back from; look it up
        # from the still-queued job instead, for preview purposes only.
        match = next((j for j in ho.queued_jobs() if j["job_id"] == job_id), None)
        job_path_str = match["path"] if match else None
    if not job_path_str:
        return {"engine": engine, "exported": False, "reason": "job_path_unavailable", "job_id": job_id}

    assignment = json.loads(Path(job_path_str).read_text(encoding="utf-8"))
    flat = {
        "schema": "rb.hunter_drive_assignment.v1",
        "engine": engine,
        "job_id": job_id,
        "assignment_id": assignment.get("assignment_id"),
        "subjobs": [_extract_subjob(s) for s in assignment["subjobs"]],
    }
    if confirm:
        DRIVE_INBOX.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(flat, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"engine": engine, "exported": confirm, "job_id": job_id, "path": str(path), "assignment": flat}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--confirm", action="store_true", help="Actually lease jobs and write the Drive-synced files; omit for a dry-run preview")
    args = p.parse_args(argv)
    if args.confirm:
        _ensure_queue_not_empty()
    results = [export_for_engine(name, confirm=args.confirm) for name in ENGINE_FILENAMES]
    print(json.dumps({"dry_run": not args.confirm, "drive_inbox": str(DRIVE_INBOX), "results": results}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
