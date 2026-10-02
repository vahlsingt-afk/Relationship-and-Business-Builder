#!/usr/bin/env python3
"""Audit evidence traceability across the daily intelligence decision chain."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import rb_core as core

CACHE_PATH = core.CACHE_DIR / "intelligence_cycle_audit.json"


def build(*, ramifications: dict, downstream: dict, today: date) -> dict:
    """Prove every ramification has evidence, baseline context, and disposition."""
    downstream_items = downstream.get("items") or []
    by_source: dict[str, list[dict]] = {}
    for item in downstream_items:
        by_source.setdefault(str(item.get("source_id") or ""), []).append(item)

    traces = []
    for row in ramifications.get("ramifications") or []:
        ramification_id = str(row.get("ramification_id") or "")
        queued = by_source.get(ramification_id, [])
        queued_pairs = {(item.get("target_artifact"), item.get("proposed_action")) for item in queued}
        missing = []
        if not row.get("signal_id"):
            missing.append("signal_id")
        if not row.get("source_refs"):
            missing.append("source_refs")
        if row.get("baseline_status") not in {"established", "insufficient"}:
            missing.append("baseline_status")
        if not row.get("review_lens") or not row.get("recommended_follow_up"):
            missing.append("assessment")
        for impact in row.get("affected_artifacts") or []:
            pair = (impact.get("artifact"), impact.get("action"))
            if pair[0] != "daily_intelligence_report" and pair not in queued_pairs:
                missing.append(f"downstream:{pair[0]}")
        traces.append({
            "ramification_id": ramification_id,
            "signal_id": row.get("signal_id"),
            "entity_name": row.get("entity_name"),
            "source_refs": row.get("source_refs") or [],
            "baseline_status": row.get("baseline_status"),
            "downstream_impact_ids": [item.get("impact_id") for item in queued],
            "status": "complete" if not missing else "incomplete",
            "missing_stages": missing,
        })

    incomplete = sum(trace["status"] == "incomplete" for trace in traces)
    status = "no_material_events" if not traces else ("degraded" if incomplete else "complete")
    report = {
        "date": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "material_events": int(ramifications.get("material_events_assessed") or 0),
        "ramifications_traced": len(traces),
        "complete_traces": len(traces) - incomplete,
        "incomplete_traces": incomplete,
        "traces": traces,
    }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
