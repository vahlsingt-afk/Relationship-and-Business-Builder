#!/usr/bin/env python3
"""
process_pending_captures.py — automatically triage every pending capture.

RB-DEFECT-2026-07-09: capture_ingest.py's --scan step only queues new
transcripts as pending; the actual intelligence-extraction step (transcript
-> intelligence_triage -> persisted intelligence -> mark_processed) only ran
via the Custom GPT calling POST /captures/{id}/submit or
POST /captures/process_all -- both requiring a live chat command. Real
captures sat pending across multiple brief cycles because nothing ever
called the processing step automatically ("Captures — 2 Pending Processing"
recurring day after day instead of being processed).

This script calls the exact same logic as POST /captures/process_all
(server.process_all_pending_captures() -- both delegate to one function, no
duplicated logic) directly, without going through HTTP, so morning_pipeline.py
can run it as a plain subprocess step right after capture_ingest_scan /
capture adapters queue new captures.

The Custom GPT's "RB, process my captures" chat command still works exactly
as before -- this just means captures are no longer stuck waiting for that
command to ever be said.

Usage:
    python3 system/scripts/process_pending_captures.py
    python3 system/scripts/process_pending_captures.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent
sys.path.insert(0, str(SYSTEM_DIR / "api"))
sys.path.insert(0, str(SCRIPTS_DIR))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON summary.")
    args = parser.parse_args()

    import server  # noqa: E402 — path set up above

    summary = server.process_all_pending_captures()

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(
            f"Captures: {summary['total']} total, {summary['processed']} processed, "
            f"{summary['skipped']} skipped, {summary['errors']} errors."
        )
        for r in summary["results"]:
            if r["status"] == "processed":
                print(f"  processed  {r['file_id']} — {r.get('title_hint', '')} "
                      f"(streams={r.get('triage_stream_count', 0)}, persisted={r.get('persisted_count', 0)})")
            elif r["status"] == "skipped":
                print(f"  skipped    {r['file_id']} — {r.get('reason', '')}")
            else:
                print(f"  error      {r['file_id']} — {r.get('error', '')}")

    return 0 if summary["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
