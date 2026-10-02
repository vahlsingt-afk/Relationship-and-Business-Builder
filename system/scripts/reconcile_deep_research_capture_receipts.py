#!/usr/bin/env python3
"""
reconcile_deep_research_capture_receipts.py — RB defect 2026-09-30.

Investigation (see system/CLAUDE_HANDOFF_RB_DEEP_RESEARCH_MUTATION_RECEIPT_
DEFECT_2026-09-30.md) confirmed that a deep-research capture's receipt
(written by server.py's submitCapture / process_all_pending_captures when
generic intelligence_triage classifiers run) reports "exec_mutations: 0" for
a class of mutation it never measures at all -- the structured findings in
the capture's JSON sidecar are applied separately, later in the same
pipeline cycle, by import_competitor_platform_research.py. The two receipts
were never reconciled, so a real canonical mutation could be (and, for six
packets in the 2026-09-30 morning cycle, was) reported as zero.

This script closes that gap: for every capture_type == "deep_research"
capture already marked processed, it locates the capture's JSON sidecar
(same basename, .json instead of .md -- see capture_ingest.
deep_research_sidecar_info()), reads that sidecar's packet_id, looks up
import_competitor_platform_research.get_import_receipt(packet_id), and -- if
found -- merges the real structured_import_* counts and
canonical_targets_changed into the capture's stored processing_result via
capture_ingest.reconcile_processing_result().

Idempotent: re-running is a no-op for a capture whose
processing_result.structured_import_reconciled is already true. A capture
whose sidecar has no findings, or whose packet_id has no import receipt yet
(the importer hasn't swept it), is left untouched -- there is nothing yet to
reconcile, not a failure.

Runs in morning_pipeline.py immediately after competitor_platform_research_
import (deep_research_sidecar_sweep -> competitor_platform_research_import
-> this step), so today's reconciliation reflects today's import run.

CLI:
    python3 reconcile_deep_research_capture_receipts.py [--since-date today]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import capture_ingest  # noqa: E402
import import_competitor_platform_research as importer  # noqa: E402

RECEIPT_LOG_PATH = SCRIPTS_DIR.parent / ".cache" / "capture_receipt_reconciliation_latest.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def reconcile(since_date: str | None = None) -> dict:
    captures = capture_ingest.list_processed(limit=200, since_date=since_date)
    reconciled: list[dict] = []
    skipped: list[dict] = []

    for cap in captures:
        if cap.get("capture_type") != "deep_research":
            continue
        file_id = cap["file_id"]
        result = cap.get("processing_result") or {}
        if result.get("structured_import_reconciled") is True:
            skipped.append({"file_id": file_id, "reason": "already_reconciled"})
            continue

        sidecar_info = capture_ingest.deep_research_sidecar_info(cap.get("source_file"))
        if not sidecar_info["has_findings"]:
            skipped.append({"file_id": file_id, "reason": "no_structured_sidecar_findings"})
            continue

        packet_id = sidecar_info["packet_id"]
        receipt = importer.get_import_receipt(packet_id) if packet_id else None
        if receipt is None:
            skipped.append({"file_id": file_id, "packet_id": packet_id,
                             "reason": "no_import_receipt_yet"})
            continue

        applied = int(receipt.get("applied") or 0)
        deduped = int(receipt.get("deduped") or 0)
        queued = int(receipt.get("queued_for_review") or 0)
        rejected = int(receipt.get("findings_rejected") or 0)
        targets = receipt.get("canonical_targets_changed") or []

        if applied > 0:
            statement = (
                f"{applied} structured finding(s) applied to canonical record(s) "
                f"for: {', '.join(targets)}."
            )
        elif queued > 0:
            statement = f"No canonical mutation applied yet; {queued} finding(s) queued for review."
        elif rejected > 0:
            statement = f"No canonical mutation applied; {rejected} finding(s) rejected (malformed or unresolved identity)."
        else:
            statement = "No canonical mutation occurred (all findings already processed or deduplicated)."

        updates = {
            "structured_import_reconciled": True,
            "structured_import_packet_id": packet_id,
            "structured_import_applied": applied,
            "structured_import_deduped": deduped,
            "structured_import_queued": queued,
            "structured_import_rejected": rejected,
            "canonical_targets_changed": targets,
            "canonical_mutation_statement": statement,
        }
        ok = capture_ingest.reconcile_processing_result(file_id, updates)
        if ok:
            reconciled.append({"file_id": file_id, "packet_id": packet_id, **updates})
        else:
            skipped.append({"file_id": file_id, "packet_id": packet_id, "reason": "write_failed"})

    report = {
        "generated_at": _now_iso(),
        "reconciled_count": len(reconciled),
        "skipped_count": len(skipped),
        "reconciled": reconciled,
        "skipped": skipped,
    }
    RECEIPT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT_LOG_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--since-date", default=None, help="'today', 'yesterday', or YYYY-MM-DD. Default: no date filter.")
    args = p.parse_args()
    report = reconcile(since_date=args.since_date)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
