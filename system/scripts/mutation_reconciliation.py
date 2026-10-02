#!/usr/bin/env python3
"""
mutation_reconciliation.py — daily trust-stat sweep for the recording engine.

Founding principle this exists to police: input gets assessed, mutates the
right artifact, and only THEN may the CoS speak. That depends on the model
actually calling a tool on a given turn — something no amount of instruction
wording can force (see 2026-08-25's live touchContact test: an explicit,
matching trigger phrase, a bold anti-fabrication rule already in place, and
the model still narrated a fabricated receipt with a wrong name, zero API
calls made). This script is the independent check: not "did the chat reply
sound right," but "does system/audit/*.jsonl actually show it happened."

Two distinct failure modes, deliberately not conflated:
  SILENT   — a monitored, instrumented write operation has zero
             mutation_executed audit events in the lookback window despite
             real HTTP traffic to it (or, for near-zero-traffic ops, despite
             being live and callable). Nothing was ever claimed with enough
             detail to safely redo — flag only, never fabricate a repair.
  STALE    — has fired before, but not recently (cadence-based, softer than
             SILENT).
  NOT_YET_INSTRUMENTED — the endpoint doesn't call audit_log.py at all yet,
             so this script has no signal to reconcile. Reported honestly
             as a gap in the reconciliation engine itself, not a finding
             about whether the operation works.

No auto-repair in this version, on purpose: every failure mode observed so
far (2026-08-25 investigation) is "the model never called anything" — there
is no recorded claim to safely re-execute. Auto-repair only becomes safe once
a call is logged as mutation_executed but the target artifact demonstrably
doesn't reflect it (a different failure mode, not yet observed). Add it then,
against a real case, not speculatively now.

CLI:
    python3 mutation_reconciliation.py [--days N] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import audit_log as al  # noqa: E402

import os  # noqa: E402

SYSTEM_DIR = core.SYSTEM_DIR
# Same env var server.py's _RequestLogger honors — lets tests point both at
# an isolated temp file together.
REQUEST_LOG_PATH = Path(os.environ.get("RB_REQUEST_LOG_PATH", str(SYSTEM_DIR / "api" / "request.log")))
REPORT_PATH = SYSTEM_DIR / ".cache" / "mutation_reconciliation.json"

# ---------------------------------------------------------------------------
# Monitored operations — HTTP method/path-prefix pairs for request.log
# correlation, and whether the handler currently calls audit_log.py.
# Update this list whenever a write-tagged GPT operation is added, removed,
# or newly wired to audit_log — see system/api/openapi_gpt.yaml's "write"-
# tagged ops and system/scripts/validate_openapi_gpt.py's GPT_OPERATIONS.
# ---------------------------------------------------------------------------

MONITORED_OPS: dict[str, dict] = {
    "touchContact": {
        "method": "POST", "prefix": "/touch", "instrumented": True,
        "instrumented_since": "2026-08-25", "cadence_days": 14,
    },
    "closeLoop": {
        "method": "POST", "prefix": "/loops/close", "instrumented": True,
        "instrumented_since": "2026-08-25", "cadence_days": 14,
    },
    "confirmProposal": {
        "method": "POST", "prefix": "/confirm", "instrumented": True,
        "instrumented_since": "2026-08-25", "cadence_days": 14,
    },
    "ingestExecutiveDeclaration": {
        "method": "POST", "prefix": "/ingest/executive_declaration",
        "instrumented": True, "instrumented_since": "2026-08-25", "cadence_days": 7,
    },
    "submitCapture": {
        "method": "POST", "prefix": "/captures/", "instrumented": True,
        "instrumented_since": "2026-08-25", "cadence_days": None,
    },
    "closeThread": {
        "method": "POST", "prefix": "/threads/close", "instrumented": True,
        "instrumented_since": "2026-08-25", "cadence_days": None,
    },
    "processOpportunityUpdate": {
        "method": "POST", "prefix": "/opportunity/update", "instrumented": True,
        "instrumented_since": "2026-08-25", "cadence_days": None,
    },
    "processRelationshipIntake": {
        "method": "POST", "prefix": "/relationship/intake", "instrumented": True,
        "instrumented_since": "2026-08-25", "cadence_days": None,
    },
    "processMacroSignal": {
        "method": "POST", "prefix": "/macro/signal", "instrumented": True,
        "instrumented_since": "2026-08-25", "cadence_days": None,
    },
    "uploadAndIngestFile": {
        "method": "POST", "prefix": "/ingest/upload", "instrumented": True,
        "instrumented_since": "2026-08-25", "cadence_days": None,
    },
    "refreshSources": {
        "method": "POST", "prefix": "/sources/refresh", "instrumented": True,
        "instrumented_since": "2026-08-25", "cadence_days": None,
    },
    "manualRelationshipIntake": {
        "method": "POST", "prefix": "/manual_relationship_intake", "instrumented": False, "cadence_days": None,
    },
}

_OP_PREFIX_RE = re.compile(r"^(?P<op>[A-Za-z][A-Za-z0-9]*):\s")
_REQUEST_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ "
    r"(?P<method>GET|POST|PUT|DELETE|PATCH) (?P<path>\S+?)\?\S* "
    r"auth=(?P<auth>YES|NO) status=(?P<status>\d+) (?P<ms>\d+)ms"
)


def _cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _instrumented_since_dt(spec: dict) -> Optional[datetime]:
    raw = spec.get("instrumented_since")
    if not raw:
        return None
    return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _count_request_log_calls(days: int) -> dict[str, dict]:
    """Raw HTTP call counts per monitored op from request.log, window-bounded.

    Tracks two figures per op: `calls` (all traffic in the lookback window —
    context) and `calls_since_instrumented` (traffic only from the date
    audit_log wiring actually landed — the figure the silent/healthy verdict
    is based on, so a backlog of pre-instrumentation calls never reads as a
    false "silent" alarm)."""
    counts: dict[str, dict] = {
        op: {"calls": 0, "calls_since_instrumented": 0, "last_call_at": None, "statuses": {}}
        for op in MONITORED_OPS
    }
    if not REQUEST_LOG_PATH.exists():
        return counts
    cutoff = _cutoff(days)
    since_dt = {op: _instrumented_since_dt(spec) for op, spec in MONITORED_OPS.items()}
    with REQUEST_LOG_PATH.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = _REQUEST_LINE_RE.match(line)
            if not m:
                continue
            try:
                ts = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if ts < cutoff:
                continue
            method, path = m.group("method"), m.group("path")
            for op, spec in MONITORED_OPS.items():
                if method == spec["method"] and path.startswith(spec["prefix"]):
                    counts[op]["calls"] += 1
                    status = m.group("status")
                    counts[op]["statuses"][status] = counts[op]["statuses"].get(status, 0) + 1
                    if counts[op]["last_call_at"] is None or ts.isoformat() > counts[op]["last_call_at"]:
                        counts[op]["last_call_at"] = ts.isoformat()
                    if since_dt[op] is not None and ts >= since_dt[op]:
                        counts[op]["calls_since_instrumented"] += 1
                    break
    return counts


def _scan_audit_mutations(days: int) -> dict[str, dict]:
    """mutation_executed/mutation_proposed/mutation_rejected events per
    monitored op from the audit log, parsed by the leading "opName: "
    convention every wired handler now follows (see server.py's
    al.log_mutation_* call sites).

    `proposed` is tracked separately from `executed` and deliberately NOT
    treated as a lesser outcome: several endpoints (processMacroSignal
    always, processOpportunityUpdate/processRelationshipIntake often) are
    review-first by design — a correctly-working call produces
    mutation_proposed, not mutation_executed, because the actual write
    happens later via confirmProposal. Judging their health on `executed`
    alone would flag a perfectly healthy proposal-only endpoint as SILENT."""
    events: dict[str, dict] = {
        op: {"executed": 0, "proposed": 0, "rejected": 0, "last_executed_at": None, "last_proposed_at": None}
        for op in MONITORED_OPS
    }
    cutoff = _cutoff(days)
    partitions = sorted(al.AUDIT_DIR.glob("*.jsonl")) if al.AUDIT_DIR.exists() else []
    for path in partitions:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_type = rec.get("event_type")
            if event_type not in ("mutation_executed", "mutation_proposed", "mutation_rejected"):
                continue
            ts_raw = rec.get("timestamp")
            try:
                ts = datetime.fromisoformat(ts_raw)
            except (TypeError, ValueError):
                continue
            if ts < cutoff:
                continue
            m = _OP_PREFIX_RE.match(rec.get("item_summary", ""))
            if not m or m.group("op") not in MONITORED_OPS:
                continue
            op = m.group("op")
            if event_type == "mutation_executed":
                events[op]["executed"] += 1
                if events[op]["last_executed_at"] is None or ts.isoformat() > events[op]["last_executed_at"]:
                    events[op]["last_executed_at"] = ts.isoformat()
            elif event_type == "mutation_proposed":
                events[op]["proposed"] += 1
                if events[op]["last_proposed_at"] is None or ts.isoformat() > events[op]["last_proposed_at"]:
                    events[op]["last_proposed_at"] = ts.isoformat()
            else:
                events[op]["rejected"] += 1
    return events


def _days_since(iso_ts: Optional[str]) -> Optional[float]:
    if not iso_ts:
        return None
    try:
        ts = datetime.fromisoformat(iso_ts)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0


def build_report(days: int = 30) -> dict:
    http = _count_request_log_calls(days)
    audit = _scan_audit_mutations(days)
    ops_report = {}
    for op, spec in MONITORED_OPS.items():
        h, a = http[op], audit[op]
        confirmed_activity = a["executed"] + a["proposed"]
        last_activity_at = max(
            (t for t in (a["last_executed_at"], a["last_proposed_at"]) if t), default=None
        )
        days_since = _days_since(last_activity_at)
        calls_relevant = h["calls_since_instrumented"] if spec["instrumented"] else h["calls"]
        if not spec["instrumented"]:
            status = "not_yet_instrumented"
        elif confirmed_activity == 0 and calls_relevant == 0:
            status = "no_activity_observed"
        elif confirmed_activity == 0 and calls_relevant > 0:
            status = "silent"  # calls are landing at the endpoint, no confirmed activity ever comes out
        elif spec["cadence_days"] and days_since is not None and days_since > spec["cadence_days"]:
            status = "stale"
        else:
            status = "healthy"
        ops_report[op] = {
            "instrumented": spec["instrumented"],
            "instrumented_since": spec.get("instrumented_since"),
            "http_calls_window": h["calls"],
            "http_calls_since_instrumented": h["calls_since_instrumented"] if spec["instrumented"] else None,
            "http_status_breakdown": h["statuses"],
            "audit_mutations_executed": a["executed"],
            "audit_mutations_proposed": a["proposed"],
            "audit_mutations_rejected": a["rejected"],
            "last_confirmed_activity_at": last_activity_at,
            "days_since_last_confirmed_activity": round(days_since, 1) if days_since is not None else None,
            "status": status,
        }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window_days": days,
        "ops": ops_report,
        "summary": {
            "healthy": sum(1 for v in ops_report.values() if v["status"] == "healthy"),
            "stale": sum(1 for v in ops_report.values() if v["status"] == "stale"),
            "silent": sum(1 for v in ops_report.values() if v["status"] == "silent"),
            "not_yet_instrumented": sum(1 for v in ops_report.values() if v["status"] == "not_yet_instrumented"),
            "no_activity_observed": sum(1 for v in ops_report.values() if v["status"] == "no_activity_observed"),
        },
    }
    return report


def _write_report(report: dict) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def _log_findings(report: dict) -> None:
    """Emit one audit event per SILENT op so this sweep's own findings are
    themselves independently verifiable, not just printed to a terminal."""
    for op, data in report["ops"].items():
        if data["status"] == "silent":
            al.log_item_rejected(
                item_summary=f"mutation_reconciliation: {op} SILENT — "
                             f"{data['http_calls_since_instrumented']} HTTP calls since instrumentation "
                             f"({data['instrumented_since']}), 0 confirmed mutations",
                reason="write_operation_receiving_traffic_with_zero_confirmed_mutations",
                data_class="audit",
            )


def _print_summary(report: dict) -> None:
    print(f"mutation_reconciliation — {report['window_days']}d window, generated {report['generated_at']}")
    print(f"  healthy={report['summary']['healthy']} stale={report['summary']['stale']} "
          f"silent={report['summary']['silent']} not_yet_instrumented={report['summary']['not_yet_instrumented']} "
          f"no_activity_observed={report['summary']['no_activity_observed']}")
    for op, data in sorted(report["ops"].items(), key=lambda kv: kv[1]["status"]):
        relevant = data["http_calls_since_instrumented"] if data["instrumented"] else data["http_calls_window"]
        print(f"  [{data['status']:>22}] {op:<28} http(relevant)={relevant if relevant is not None else '—':<4} "
              f"http(all)={data['http_calls_window']:<4} confirmed={data['audit_mutations_executed']:<4} "
              f"last={data['last_confirmed_activity_at'] or '—'}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    report = build_report(days=args.days)
    _write_report(report)
    _log_findings(report)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
