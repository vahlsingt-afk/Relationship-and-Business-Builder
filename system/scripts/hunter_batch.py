#!/usr/bin/env python3
"""Prepare CoS-ranked Hunter bundles and produce evidence-based batch reports.

This utility owns local batch preparation and reporting only. ChatGPT Deep
Research remains the research engine; returned packets are still finalized by
hunter_cycle.sweep() in dry-run mode.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))
import hunter_cycle as hc  # noqa: E402

REPORT_DIR = SYSTEM_DIR / "reports" / "hunter_batches"
MAX_WORK_UNITS = 25


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _safe_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "batch"


def _matching_brand_pairs(rows: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    brands = {}
    discoveries = {}
    for row in rows:
        key = row.get("target_key") or ""
        if key.startswith("company:"):
            brands[key.split(":", 1)[1]] = row
        elif key.startswith("franchise-discovery:"):
            discoveries[key.split(":", 1)[1]] = row
    return brands, discoveries


def _select_work_units(queue: dict, count: int) -> tuple[list[list[dict]], set[str]]:
    rows = queue.get("queue") or []
    pending = hc._pending_target_keys()
    completed = hc._queue_completed_targets(queue)
    brands, discoveries = _matching_brand_pairs(rows)
    consumed: set[str] = set()
    selected: list[list[dict]] = []
    for row in rows:
        key = row.get("target_key")
        if not key or key in consumed or key in pending or key in completed:
            continue
        companion = None
        if key.startswith("company:"):
            companion = discoveries.get(key.split(":", 1)[1])
        elif key.startswith("franchise-discovery:"):
            companion = brands.get(key.split(":", 1)[1])
        group = [row]
        if companion and companion.get("target_key") not in pending | completed:
            group.append(companion)
        group.sort(key=lambda item: item.get("rank", 10**9))
        selected.append(group)
        consumed.update(item["target_key"] for item in group if item.get("target_key"))
        if len(selected) >= count:
            break
    return selected, completed


def prepare_batch(*, work_units: int, batch_id: str | None = None,
                  deep_research_available: bool = False,
                  transport_available: bool = False) -> dict:
    if not deep_research_available or not transport_available:
        raise ValueError("Batch not queued: confirm both an available ChatGPT Deep Research session and supported Drive/local response capture.")
    if not 1 <= work_units <= MAX_WORK_UNITS:
        raise ValueError(f"work_units must be between 1 and {MAX_WORK_UNITS}")
    if hc._pending_job_files():
        raise ValueError("A Hunter job or batch is already pending; finish or explicitly resolve it before preparing another batch.")

    queue = hc._read_queue()
    generated = queue.get("generated_at")
    try:
        generated_dt = datetime.fromisoformat(str(generated).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("CoS priority queue is missing a valid generated_at") from error
    if generated_dt.tzinfo is None:
        generated_dt = generated_dt.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - generated_dt > timedelta(hours=48):
        raise ValueError("CoS priority queue is older than 48 hours; refresh it through the RBB pipeline before preparing a batch.")

    selected, completed = _select_work_units(queue, work_units)
    if len(selected) != work_units:
        raise ValueError(f"Only {len(selected)} eligible work units remain in the CoS queue; requested {work_units}.")
    batch_id = batch_id or f"priority-{work_units}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    batch_id = _safe_slug(batch_id)
    assignment_id = batch_id
    subjobs = []
    target_keys = []
    ranks = []
    display_names = []
    for unit_number, group in enumerate(selected, start=1):
        display_names.append(group[0].get("display_name"))
        for row in group:
            target_key = row["target_key"]
            playbook = row["suggested_playbook"]
            job = hc.prepare(playbook, universe="all", target_keys=[target_key], limit=1,
                             deep_research_available=True)
            resolved = ((job.get("directive") or {}).get("packet_requirements") or {}).get("target_keys") or []
            if resolved != [target_key]:
                raise ValueError(f"Hunter preparation resolved {target_key!r} as {resolved!r}; refusing to queue the batch.")
            subjobs.append({"target_key": target_key, "rank": row.get("rank"),
                            "suggested_playbook": playbook, "work_unit": unit_number, "job": job})
            target_keys.append(target_key)
            ranks.append(row.get("rank"))

    assignment = {
        "schema": "rb.hunter_priority_assignment.v1",
        "status": "prepared_local_assignment",
        "queue_generated_at": generated,
        "queue_ranks": ranks,
        "work_unit_count": len(selected),
        "subjob_count": len(subjobs),
        "target_keys": target_keys,
        "display_names": display_names,
        "bundle_schema": hc.BUNDLE_SCHEMA,
        "assignment_id": assignment_id,
        "drive_assignment_file": f"hunter-assignment-{batch_id}.json",
        "drive_response_file": f"hunter-response-{batch_id}.json",
        "subjobs": subjobs,
        "pending_job_count": len(hc._pending_job_files()),
        "transport_required": True,
        "research_authorized": True,
        "submission_note": "One ChatGPT Deep Research request must complete every ranked work unit and return one ordinary Hunter packet per subjob inside one rb.hunter_research_bundle_response.v1 response. Paired brand/franchise-discovery rows are one work unit but separate subjobs. Return filename must start with hunter- and contain response, batch, bundle, or packet. No partial bundle in the watched inbox; no canonical writes.",
    }
    pending_path = hc.PENDING_JOBS_DIR / f"bundle-{_safe_slug(assignment_id)}.json"
    if pending_path.exists():
        raise ValueError(f"Batch assignment already exists: {pending_path}")
    hc.PENDING_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    pending_path.write_text(json.dumps(assignment, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assignment["queued_path"] = str(pending_path)
    assignment["completed_targets_skipped"] = sorted(completed)
    return assignment


def _find_batch(batch_id: str | None) -> tuple[dict, Path, bool] | None:
    candidates = []
    for folder, is_pending in ((hc.PENDING_JOBS_DIR, True), (hc.PROCESSED_JOBS_DIR, False)):
        if not folder.is_dir():
            continue
        for path in folder.glob("bundle-*.json"):
            job = _read_json(path)
            if job and job.get("schema") == "rb.hunter_priority_assignment.v1":
                candidates.append((job, path, is_pending))
    if batch_id:
        slug = _safe_slug(batch_id)
        for row in candidates:
            if row[0].get("assignment_id") == batch_id or _safe_slug(row[0].get("assignment_id") or "") == slug:
                return row
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda row: row[1].stat().st_mtime, reverse=True)
    return candidates[0]


def _response_for(batch_id: str) -> tuple[dict | None, dict | None, str | None]:
    processed_paths = sorted(hc.PROCESSED_PACKETS_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True) if hc.PROCESSED_PACKETS_DIR.is_dir() else []
    for path in processed_paths:
        if not path.is_file() or path.name.endswith(".receipt.json"):
            continue
        try:
            packet = hc.load_packet_artifact(path)
        except (OSError, ValueError):
            continue
        if packet.get("schema") != hc.BUNDLE_SCHEMA or packet.get("assignment_id") != batch_id:
            continue
        receipt = _read_json(path.with_name(path.name + ".receipt.json"))
        return packet, receipt, path.name
    inbox_paths = sorted(hc.PACKETS_INBOX_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True) if hc.PACKETS_INBOX_DIR.is_dir() else []
    for path in inbox_paths:
        if not path.is_file():
            continue
        try:
            packet = hc.load_packet_artifact(path)
        except (OSError, ValueError):
            continue
        if packet.get("schema") == hc.BUNDLE_SCHEMA and packet.get("assignment_id") == batch_id:
            return packet, None, path.name
    return None, None, None


def build_report(batch_id: str | None = None, *, assignment_file: str | None = None) -> dict:
    found = _find_batch(batch_id)
    if not found:
        raise ValueError(f"No Hunter batch assignment found for {batch_id or 'latest'}")
    assignment, job_path, is_pending = found
    batch_id = assignment.get("assignment_id") or job_path.stem.removeprefix("bundle-")
    packet, receipt, packet_name = _response_for(batch_id)
    expected = {row.get("target_key"): row for row in assignment.get("subjobs") or []}
    child_packets = {}
    if packet:
        for child in packet.get("packets") or []:
            targets = child.get("targets") or []
            keys = [t.get("target_key") if isinstance(t, dict) else t for t in targets]
            if len(keys) == 1:
                child_packets[keys[0]] = child
    child_receipts = {row.get("target_key"): row.get("receipt") or {}
                      for row in (receipt or {}).get("child_receipts") or []}
    subjob_rows = []
    for target_key, subjob in expected.items():
        child = child_packets.get(target_key)
        child_receipt = child_receipts.get(target_key)
        if child_receipt and child_receipt.get("ok"):
            status = "validated_dry_run" if not child_receipt.get("confirmed") else "confirmed"
        elif child:
            status = "validation_failed"
        elif packet:
            status = "packet_missing_from_returned_bundle"
        elif is_pending:
            status = "awaiting_deep_research_response"
        else:
            status = "not_returned"
        dispatch = (child_receipt or {}).get("dispatch") or {}
        imported = dispatch.get("payload_import") or {}
        validation = (child_receipt or {}).get("validation") or {}
        subjob_rows.append({
            "target_key": target_key,
            "display_name": (subjob.get("job", {}).get("directive", {}).get("gap_manifest") or {}).get("display_name") or target_key,
            "rank": subjob.get("rank"),
            "work_unit": subjob.get("work_unit"),
            "playbook": subjob.get("suggested_playbook"),
            "status": status,
            "packet_id": (child or {}).get("packet_id"),
            "findings": len((child or {}).get("findings") or []),
            "sources": len((child or {}).get("source_ledger") or []),
            "gap_outcomes": len((child or {}).get("gap_outcomes") or []),
            "unanswered_questions": len((child or {}).get("unanswered_questions") or []),
            "validation_errors": len(validation.get("errors") or []),
            "payload_records_malformed": imported.get("records_malformed", 0),
            "payload_records_rejected": imported.get("records_rejected", 0),
            "canonical_writes": dispatch.get("canonical_applied", 0),
        })
    # A returned packet may already be sitting in the inbox before sweep has
    # written a receipt. Count it as returned and awaiting local validation.
    if packet and not receipt:
        for row in subjob_rows:
            if row["status"] == "awaiting_deep_research_response":
                row["status"] = "returned_awaiting_sweep"
    by_unit: dict[int, list[dict]] = defaultdict(list)
    for row in subjob_rows:
        by_unit[int(row.get("work_unit") or 0)].append(row)
    work_rows = []
    for unit_number, rows in sorted(by_unit.items()):
        statuses = {row["status"] for row in rows}
        if statuses == {"validated_dry_run"}:
            status = "validated_dry_run"
        elif "validation_failed" in statuses or "packet_missing_from_returned_bundle" in statuses:
            status = "needs_repair"
        elif statuses == {"awaiting_deep_research_response"}:
            status = "awaiting_deep_research_response"
        elif "awaiting_deep_research_response" in statuses or "returned_awaiting_sweep" in statuses:
            status = "incomplete_return"
        elif statuses == {"confirmed"}:
            status = "confirmed"
        else:
            status = "incomplete"
        work_rows.append({"work_unit": unit_number, "queue_ranks": [r["rank"] for r in rows],
                          "targets": [r["target_key"] for r in rows], "status": status})

    status_counts = Counter(row["status"] for row in subjob_rows)
    summary = {
        "work_units": len(work_rows),
        "subjobs": len(subjob_rows),
        "packets_returned": len(child_packets),
        "validated_dry_run": status_counts.get("validated_dry_run", 0),
        "validation_failed": status_counts.get("validation_failed", 0),
        "awaiting_response": status_counts.get("awaiting_deep_research_response", 0),
        "returned_awaiting_sweep": status_counts.get("returned_awaiting_sweep", 0),
        "missing_from_bundle": status_counts.get("packet_missing_from_returned_bundle", 0),
        "findings": sum(row["findings"] for row in subjob_rows),
        "sources": sum(row["sources"] for row in subjob_rows),
        "gap_outcomes": sum(row["gap_outcomes"] for row in subjob_rows),
        "validation_errors": sum(row["validation_errors"] for row in subjob_rows),
        "payload_records_rejected": sum(row["payload_records_rejected"] for row in subjob_rows),
        "canonical_writes": sum(row["canonical_writes"] for row in subjob_rows),
    }
    if packet is None:
        overall_status = "awaiting_deep_research_response" if is_pending else "no_returned_bundle"
    elif receipt and receipt.get("ok"):
        overall_status = "validated_dry_run" if not receipt.get("confirmed") else "confirmed"
    elif receipt:
        overall_status = "bundle_received_needs_repair"
    else:
        overall_status = "returned_awaiting_sweep"
    report = {
        "schema": "rb.hunter_batch_report.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "batch_id": batch_id,
        "status": overall_status,
        "queue_generated_at": assignment.get("queue_generated_at"),
        "queue_ranks": assignment.get("queue_ranks") or [],
        "assignment_path": str(job_path),
        "assignment_file": assignment_file or assignment.get("drive_assignment_file") or f"hunter-assignment-{batch_id}.json",
        "response_file": assignment.get("drive_response_file") or f"hunter-response-{batch_id}.json",
        "response_artifact": packet_name,
        "receipt_ok": (receipt or {}).get("ok"),
        "confirmed": (receipt or {}).get("confirmed", False),
        "summary": summary,
        "work_units": work_rows,
        "subjobs": subjob_rows,
    }
    return report


def _next_action(report: dict) -> str:
    if report["status"] != "awaiting_deep_research_response":
        return ""
    folder_url = "https://drive.google.com/drive/folders/1OhUiu9w6db2gGUMET4Hqe2K8xh6FNS0T"
    return ("$ deep-research\n"
            f"Use Google Drive to read {report['assignment_file']} from the private RBB Hunter Cycle Inbox ({folder_url}). "
            "In the Relationship and Business Builder project, follow system/research/HUNTER.md and each subjob's exact directive and suggested playbook. "
            f"Complete all {report['summary']['work_units']} work units ({report['summary']['subjobs']} child packets), then return one "
            "rb.hunter_research_bundle_response.v1 JSON object with the exact assignment_id and all target_keys. "
            f"Save it as {report['response_file']} in the same Drive folder. Do not upload a partial bundle; do not write canonical records.")


def render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [f"# Hunter batch report: {report['batch_id']}", "",
             f"**Status:** {report['status']}", "",
             f"Queue snapshot: `{report.get('queue_generated_at')}`; ranks {min(report['queue_ranks']) if report['queue_ranks'] else 'n/a'}–{max(report['queue_ranks']) if report['queue_ranks'] else 'n/a'}.", "",
             f"Work units: **{summary['work_units']}** · Child packets: **{summary['subjobs']}** · Returned: **{summary['packets_returned']}** · Valid dry-run: **{summary['validated_dry_run']}** · Failed validation: **{summary['validation_failed']}** · Returned, awaiting sweep: **{summary['returned_awaiting_sweep']}** · Awaiting response: **{summary['awaiting_response']}**.", "",
             f"Findings: {summary['findings']} · Sources: {summary['sources']} · Gap outcomes: {summary['gap_outcomes']} · Validation errors: {summary['validation_errors']} · Payload records rejected: {summary['payload_records_rejected']} · Canonical writes: {summary['canonical_writes']}.", "",
             "## Work units", "", "| Unit | Queue rank(s) | Target(s) | Status |", "|---:|---:|---|---|"]
    for row in report["work_units"]:
        ranks = ", ".join(str(value) for value in row["queue_ranks"])
        targets = ", ".join(row["targets"])
        lines.append(f"| {row['work_unit']} | {ranks} | {targets} | {row['status']} |")
    if report["subjobs"] and summary["packets_returned"]:
        lines.extend(["", "## Packet quality", "", "| Rank | Target | Playbook | Status | Findings | Sources | Gap outcomes | Validation errors | Payload rejected |", "|---:|---|---|---|---:|---:|---:|---:|---:|"])
        for row in report["subjobs"]:
            lines.append(f"| {row['rank']} | {row['target_key']} | {row['playbook']} | {row['status']} | {row['findings']} | {row['sources']} | {row['gap_outcomes']} | {row['validation_errors']} | {row['payload_records_rejected']} |")
    action = _next_action(report)
    if action:
        lines.extend(["", "## Next action", "", "Use the following prompt in the RBB ChatGPT project:", "", "```text", action, "```"])
    lines.append("")
    return "\n".join(lines)


def write_report(report: dict) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / f"{_safe_slug(report['batch_id'])}.json"
    md_path = REPORT_DIR / f"{_safe_slug(report['batch_id'])}.md"
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    markdown = render_markdown(report)
    json_path.write_text(serialized, encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    (REPORT_DIR / "latest.json").write_text(serialized, encoding="utf-8")
    (REPORT_DIR / "latest.md").write_text(markdown, encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare CoS-ranked Hunter research batches and report their intake quality")
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="Prepare one ranked batch bundle")
    prep.add_argument("--work-units", type=int, default=25)
    prep.add_argument("--batch-id")
    prep.add_argument("--deep-research-available", action="store_true", help="Confirm ChatGPT Deep Research is available for this run")
    prep.add_argument("--transport-available", action="store_true", help="Confirm Drive assignment/response plus local sync are available")
    rep = sub.add_parser("report", help="Write a JSON and Markdown report for one batch")
    rep.add_argument("--batch-id")
    rep.add_argument("--assignment-file")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare_batch(work_units=args.work_units, batch_id=args.batch_id,
                                   deep_research_available=args.deep_research_available,
                                   transport_available=args.transport_available)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        report = build_report(args.batch_id, assignment_file=args.assignment_file)
        json_path, md_path = write_report(report)
        print(json.dumps({"report": report, "json_path": str(json_path), "markdown_path": str(md_path)}, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({"error": str(error)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
