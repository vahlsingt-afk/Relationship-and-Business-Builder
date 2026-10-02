#!/usr/bin/env python3
"""Canonical source observability contract for RB intelligence operations."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional


SOURCE_LABELS = {
    "calendar:bridgepoint": "Calendar (Bridgepoint)",
    "calendar:personal": "Calendar (Personal)",
    "calls": "Apple Calls",
    "email:bridgepoint": "Email (Bridgepoint)",
    "email:personal": "Email (Personal)",
    "interaction_overlay": "Interaction Overlay",
    "linkedin_messaging": "LinkedIn Messages",
    "market_signals": "Market Intelligence",
    "messages": "Apple Messages",
    "relationship_signals": "Relationship Signals",
    "social_engagement": "LinkedIn Engagement",
    "social_feed": "LinkedIn Feed",
    "social_own_posts": "LinkedIn Posts",
    "strategic_operators": "Strategic Operators",
    "user_artifacts": "User Artifacts",
}


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, TypeError, ValueError):
        return {}


def _parse_datetime(value: object) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _status_label(status: str) -> str:
    return {
        "refreshed": "Healthy",
        "ok": "Healthy",
        "fresh": "Healthy",
        "stale": "Stale",
        "failed": "Failed",
        "error": "Failed",
        "skipped_no_raw_input": "Unavailable",
        "not_applicable_on_platform": "Not Applicable",
    }.get(status, "Unknown")


def _trust(status: str, item_count: int) -> tuple[int, str]:
    if status in {"refreshed", "ok", "fresh"}:
        return (100 if item_count > 0 else 85, "verified refresh receipt")
    if status == "stale":
        return (25, "verified stale by configured freshness threshold")
    if status in {"failed", "error"}:
        return (0, "verified processing failure")
    if status in {"skipped_no_raw_input", "not_applicable_on_platform"}:
        return (0, "no processable source input")
    return (0, "source state unknown")


def _source_rows(source_health: dict) -> list[dict]:
    generated_at = source_health.get("generated_at")
    rows = []
    for source_key, raw in sorted((source_health.get("sources") or {}).items()):
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status") or "unknown")
        item_count = int(raw.get("item_count") or 0)
        trust_score, trust_basis = _trust(status, item_count)
        failure_reason = None
        if status not in {"refreshed", "ok", "fresh"}:
            failure_reason = raw.get("reason")
            if not failure_reason and status == "skipped_no_raw_input":
                failure_reason = "source input unavailable to RB"
            elif not failure_reason and status == "not_applicable_on_platform":
                failure_reason = "source is not applicable on this host"
            elif not failure_reason:
                failure_reason = "source processing state is not healthy"
        rows.append({
            "source_key": source_key,
            "source": SOURCE_LABELS.get(source_key, source_key),
            "status": _status_label(status),
            "status_code": status,
            "last_refresh": raw.get("last_refreshed_at"),
            "last_attempt": generated_at,
            "records_processed": item_count,
            "new_signals": None,
            "mutations_detected": None,
            "confidence_score": None,
            "trust_score": trust_score,
            "trust_basis": trust_basis,
            "failure_reason": failure_reason,
            "recovery_type": raw.get("recovery_type"),
            "tier": raw.get("tier"),
        })
    return rows


def _linkedin_export_row(linkedin: dict, *, as_of: date) -> Optional[dict]:
    if not linkedin:
        return None
    generated_at = linkedin.get("_generated_at") or linkedin.get("generated_at")
    refreshed_at = _parse_datetime(generated_at)
    if refreshed_at is None and linkedin.get("ingest_date"):
        refreshed_at = _parse_datetime(f"{linkedin['ingest_date']}T00:00:00+00:00")
    age_hours = None
    if refreshed_at is not None:
        as_of_end = datetime.combine(as_of, datetime.max.time(), tzinfo=timezone.utc)
        age_hours = max(0.0, (as_of_end - refreshed_at).total_seconds() / 3600)
    status = "Healthy" if age_hours is not None and age_hours <= 48 else "Stale"
    status_code = "refreshed" if status == "Healthy" else "stale"
    counts = linkedin.get("headline_counts") or {}
    delta = linkedin.get("delta_intelligence") or {}
    trust_stats = delta.get("trust_statistics") or linkedin.get("trust_statistics") or {}
    company_changes = int(counts.get("company_changes") or 0)
    role_changes = int(counts.get("role_changes") or 0)
    disconnections = int(counts.get("disconnections") or 0)
    reconnections = int(counts.get("reconnections") or 0)
    trust_score = int(trust_stats.get("confidence_score") or 0)
    return {
        "source_key": "linkedin_export",
        "source": "LinkedIn Full Export",
        "status": status,
        "status_code": status_code,
        "last_refresh": generated_at or linkedin.get("ingest_date"),
        "last_attempt": generated_at or linkedin.get("ingest_date"),
        "records_processed": int(counts.get("connections_in_export") or 0),
        "new_signals": int(counts.get("new_connections") or 0),
        "mutations_detected": company_changes + role_changes + disconnections + reconnections,
        "confidence_score": trust_score or None,
        "trust_score": trust_score,
        "trust_basis": "LinkedIn ingestion receipt and source coverage",
        "failure_reason": (
            None if status == "Healthy"
            else "latest LinkedIn export ingestion is older than the 48-hour threshold"
        ),
        "source_file": linkedin.get("source_file"),
        "delta": {
            "new_connections": int(counts.get("new_connections") or 0),
            "company_changes": company_changes,
            "role_changes": role_changes,
            "lost_connections": disconnections,
            "reconnections": reconnections,
            "conflicts": int(counts.get("conflicts") or 0),
        },
    }


def append_collection_scan_record(system_dir: Path, record: dict) -> None:
    """Append a collection_scan_completed record to the monthly audit log.

    Written by morning_pipeline after each run so the scan history is
    queryable independent of the brief artifact.  Format matches the existing
    audit JSONL schema used by intelligence_lifecycle.py.
    """
    audit_dir = system_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    month_key = datetime.now(timezone.utc).strftime("%Y-%m")
    log_path = audit_dir / f"{month_key}.jsonl"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def read_collection_scan_log(
    system_dir: Path,
    *,
    days: int = 7,
    event_type: str = "collection_scan_completed",
) -> list[dict]:
    """Return scan records from the audit JSONL log, newest-first.

    Reads the current and prior month files to cover cross-month boundaries.
    Only records with matching ``event_type`` are returned.
    Records older than ``days`` are excluded.
    """
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    audit_dir = system_dir / "audit"
    now = datetime.now(timezone.utc)
    months = {now.strftime("%Y-%m")}
    prior = now.replace(day=1) - timedelta(days=1)
    months.add(prior.strftime("%Y-%m"))

    records: list[dict] = []
    for month_key in sorted(months):
        log_path = audit_dir / f"{month_key}.jsonl"
        if not log_path.exists():
            continue
        try:
            for raw_line in log_path.read_text(encoding="utf-8").splitlines():
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    rec = json.loads(raw_line)
                except (ValueError, TypeError):
                    continue
                if rec.get("event_type") != event_type:
                    continue
                ts = _parse_datetime(rec.get("timestamp"))
                if ts is not None and ts < cutoff:
                    continue
                records.append(rec)
        except OSError:
            continue

    records.sort(key=lambda r: str(r.get("timestamp") or ""), reverse=True)
    return records


def build_intelligence_health_dashboard(
    system_dir: Path,
    *,
    as_of: date,
    execution_report: Optional[dict] = None,
) -> dict:
    """Return exact source health, ingestion proof, and mutation telemetry."""
    cache_dir = system_dir / ".cache"
    source_health = _load_json(cache_dir / "source_health.json")
    linkedin = _load_json(cache_dir / "linkedin_ingest_latest.json")
    assessment = _load_json(cache_dir / "intelligence_assessment.json")
    report = execution_report or {}

    rows = _source_rows(source_health)
    linkedin_row = _linkedin_export_row(linkedin, as_of=as_of)
    if linkedin_row:
        rows.append(linkedin_row)

    assessment_stats = assessment.get("trust_stats") or {}
    if assessment:
        rows.append({
            "source_key": "intelligence_assessment",
            "source": "Intelligence Assessment",
            "status": "Healthy" if not assessment.get("run_errors") else "Partial",
            "status_code": "refreshed" if not assessment.get("run_errors") else "partial",
            "last_refresh": assessment.get("generated_at"),
            "last_attempt": assessment.get("generated_at"),
            "records_processed": int(assessment_stats.get("items_fetched") or 0),
            "new_signals": int(assessment_stats.get("convergences_detected") or 0),
            "mutations_detected": int(assessment_stats.get("mutation_proposals") or 0),
            "confidence_score": assessment_stats.get("confidence_score"),
            "trust_score": report.get("trust_score"),
            "trust_basis": "daily intelligence assessment receipt",
            "failure_reason": "; ".join(map(str, assessment.get("run_errors") or [])) or None,
        })

    return {
        "contract": "rb_intelligence_health_dashboard_v1",
        "as_of": as_of.isoformat(),
        "generated_at": source_health.get("generated_at") or report.get("generated_at"),
        "refresh_status": report.get("refresh_status"),
        "overall_health": source_health.get("overall_health"),
        "trust_score": report.get("trust_score") or source_health.get("trust_score"),
        "summary": {
            "healthy": sum(row.get("status") == "Healthy" for row in rows),
            "stale": sum(row.get("status") == "Stale" for row in rows),
            "failed_or_partial": sum(
                row.get("status") in {"Failed", "Partial"} for row in rows
            ),
            "unavailable": sum(row.get("status") == "Unavailable" for row in rows),
            "total_sources": len(rows),
        },
        "sources": rows,
        "proof_rule": (
            "Use exact source status and failure_reason. Never describe a source as "
            "'may be stale' when this dashboard is present."
        ),
    }
