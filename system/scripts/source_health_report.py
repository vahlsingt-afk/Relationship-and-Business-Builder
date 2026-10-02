#!/usr/bin/env python3
"""
source_health_report.py — concise stale/missing source inventory.

Reads system/.cache/source_health.json and prints the sources that keep the
daily brief under-instrumented, grouped by automation path:

  connector_capture  Google/Gmail/Calendar captures that need a connected app
  host_local         local macOS sources such as Messages/Calls
  external_export    LinkedIn exports/browser captures/API tokens
  derived_cache      downstream caches rebuilt after primary sources refresh

Use --json for machine-readable output.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parent.parent
HEALTH_PATH = SYSTEM_DIR / ".cache" / "source_health.json"

BAD_STATUSES = {
    "stale",
    "skipped_no_raw_input",
    "failed",
    "unavailable",
    "not_configured",
}


def _automation_class(source: str, row: dict) -> str:
    recovery_type = row.get("recovery_type")
    tier = int(row.get("tier") or 0)
    if recovery_type == "mcp_capture":
        return "connector_capture"
    if recovery_type == "host_setup":
        return "host_local"
    if recovery_type == "derived_cache" or tier == 4:
        return "derived_cache"
    if recovery_type in {"manual_export", "manual_scan"}:
        return "external_export"
    if source in {"messages", "calls"}:
        return "host_local"
    return "other"


def build_report() -> dict:
    if not HEALTH_PATH.exists():
        return {
            "ok": False,
            "reason": f"{HEALTH_PATH.relative_to(SYSTEM_DIR.parent)} missing; run refresh_sources.py --all --save-health",
            "groups": {},
        }
    health = json.loads(HEALTH_PATH.read_text(encoding="utf-8"))
    groups: dict[str, list[dict]] = {}
    zero_streaks: list[dict] = []
    for source, row in sorted((health.get("sources") or {}).items()):
        status = row.get("status")
        # RB-DEFECT-2026-09-18: a source that runs and returns 0 items isn't
        # "bad" by status string -- it's "refreshed" -- so it never showed up
        # here even after weeks of returning nothing. Surface it separately,
        # only once refresh_sources.py's consecutive-zero streak has crossed
        # ZERO_STREAK_ALERT_THRESHOLD; an ordinary single/double zero day
        # stays invisible here on purpose (that's the suppression the handoff
        # asked for -- a normal quiet day is not a health finding).
        if row.get("zero_streak_alert"):
            zero_streaks.append({
                "source": source,
                "tier": row.get("tier"),
                "consecutive_zero_runs": row.get("consecutive_zero_runs"),
                "last_refreshed_at": row.get("last_refreshed_at"),
            })
        if status not in BAD_STATUSES:
            continue
        group = _automation_class(source, row)
        groups.setdefault(group, []).append({
            "source": source,
            "tier": row.get("tier"),
            "status": status,
            "last_refreshed_at": row.get("last_refreshed_at"),
            "reason": row.get("reason"),
            "recovery_type": row.get("recovery_type"),
            "recovery_command": row.get("recovery_command"),
        })
    if zero_streaks:
        groups["zero_streak"] = zero_streaks
    return {
        "ok": True,
        "generated_at": health.get("generated_at"),
        "overall_health": health.get("overall_health"),
        "brief_trustworthiness": health.get("brief_trustworthiness"),
        "groups": groups,
    }


def _print(report: dict) -> None:
    if not report.get("ok"):
        print(report.get("reason"))
        return
    print(
        f"Source Health — {report.get('brief_trustworthiness')} "
        f"(generated {report.get('generated_at')})"
    )
    print()
    labels = {
        "connector_capture": "Connector captures: automate by connecting the right Gmail/Calendar accounts",
        "host_local": "Host-local sources: automate on this Mac with Full Disk Access",
        "external_export": "External/export sources: automate only with API token, export drop, or browser capture",
        "derived_cache": "Derived caches: rebuild after primary captures refresh",
        "zero_streak": "Returning zero items on repeated runs (not just a quiet day)",
        "other": "Other",
    }
    for group in ("connector_capture", "host_local", "external_export", "derived_cache", "zero_streak", "other"):
        rows = report.get("groups", {}).get(group) or []
        if not rows:
            continue
        print(labels.get(group, group))
        for row in rows:
            if group == "zero_streak":
                print(f"  - {row['source']} [tier {row.get('tier')}] "
                      f"0 items for {row.get('consecutive_zero_runs')} consecutive runs")
                continue
            print(
                f"  - {row['source']} [tier {row.get('tier')}] "
                f"{row.get('status')} — {row.get('reason') or 'no reason recorded'}"
            )
            if row.get("recovery_command"):
                print(f"    recovery: {row['recovery_command']}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print(report)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
