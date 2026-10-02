#!/usr/bin/env python3
"""Production watchdog for the daily intelligence cycle."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import rb_core as core

CACHE_PATH = core.CACHE_DIR / "intelligence_cycle_health.json"
GOOD_SOURCE_STATUSES = {"refreshed", "ok", "fresh"}
# Global Payments does not permit the desired integration. These feeds remain
# visible as accepted manual-only limitations, but must not create a fresh
# operational alarm every morning.
ACCEPTED_MANUAL_LIMITATIONS = {"calendar:global-payments", "email:global-payments"}


def assess(*, execution_report: dict, source_health: dict, trace_audit: dict,
           downstream: dict, today: date) -> dict:
    alerts = []

    def add(code: str, severity: str, message: str, **evidence) -> None:
        alerts.append({"code": code, "severity": severity, "message": message, "evidence": evidence})

    refresh = execution_report.get("refresh_status")
    if not execution_report:
        add("missing_execution_receipt", "critical",
            "The daily cycle has no execution receipt; collection cannot be verified.")
    elif refresh == "Failed":
        add("collection_failed", "critical", "The daily collection pipeline failed.")
    elif refresh == "Partial":
        add("collection_partial", "warning", "The daily collection pipeline completed partially.")

    audit_status = trace_audit.get("status")
    if audit_status == "failed":
        add("trace_audit_failed", "critical", "The intelligence decision-chain audit failed.")
    elif audit_status == "degraded":
        add("incomplete_decision_chain", "critical",
            "At least one material intelligence item lacks a complete evidence-to-action trace.",
            incomplete_traces=int(trace_audit.get("incomplete_traces") or 0))

    safe_failures = int(downstream.get("safe_failed") or 0)
    if safe_failures:
        add("downstream_execution_failed", "critical",
            "A safe downstream artifact update failed.", failures=safe_failures)

    daily = (execution_report.get("intelligence_cycle_statistics") or {}).get("daily_monitoring") or {}
    sources_scanned = int(daily.get("sources_scanned") or 0)
    if execution_report and refresh in {"Success", "Partial"} and sources_scanned == 0:
        add("zero_public_sources_scanned", "warning",
            "The cycle completed but reported zero public intelligence sources scanned.")

    configured_sources = source_health.get("sources") or {}
    web_scanner_healthy = (
        isinstance(configured_sources.get("web_scanner"), dict)
        and configured_sources["web_scanner"].get("status") in GOOD_SOURCE_STATUSES)
    blind_spots = []
    accepted_limitations = []
    for name, row in configured_sources.items():
        if not isinstance(row, dict) or row.get("status") in GOOD_SOURCE_STATUSES:
            continue
        # market_signals is a legacy manually curated inbox. A healthy live
        # web_scanner supersedes its freshness for daily public collection.
        if name == "market_signals" and web_scanner_healthy:
            accepted_limitations.append({"source": name, "status": row.get("status"),
                                         "mode": "superseded_by_live_web_scanner"})
            continue
        if name in ACCEPTED_MANUAL_LIMITATIONS:
            accepted_limitations.append({"source": name, "status": row.get("status"),
                                         "mode": "manual_export_if_available"})
            continue
        tier = int(row.get("tier") or 99)
        if tier <= 3:
            blind_spots.append({"source": name, "tier": tier, "status": row.get("status"),
                                "days_unhealthy": int(row.get("days_unhealthy") or 0),
                                "reason": row.get("reason")})
    blind_spots.sort(key=lambda row: (row["tier"], -row["days_unhealthy"], row["source"]))
    if blind_spots:
        add("source_coverage_blind_spots", "warning",
            "Priority intelligence sources are stale, unavailable, or failed.",
            sources=[row["source"] for row in blind_spots])

    severity_order = {"critical": 2, "warning": 1}
    max_severity = max((severity_order[a["severity"]] for a in alerts), default=0)
    status = "failed" if max_severity == 2 else ("degraded" if max_severity == 1 else "healthy")
    report = {
        "date": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "alerts": alerts,
        "critical_alerts": sum(a["severity"] == "critical" for a in alerts),
        "warnings": sum(a["severity"] == "warning" for a in alerts),
        "priority_source_blind_spots": blind_spots,
        "accepted_manual_limitations": accepted_limitations,
    }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
