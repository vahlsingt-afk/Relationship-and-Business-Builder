#!/usr/bin/env python3
"""
conflict_pattern_monitor.py — RB defect 2026-10-08, self-healing / learning
layer #2 (sibling to render_reachability_check.py).

ecosystem_intelligence.py's check_relationship_conflict() already makes a
real, mechanical, audited decision every time a new vendor-for-category
claim rivals an existing one -- auto_superseded (confidently better
evidence wins) or recorded_alongside (too close to call, both kept,
neither promoted) -- and logs every decision to system/inbox/ecosystem/
conflict_queue.jsonl. 90 real decisions on record as of 2026-10-08, back
to 2026-08-03. Nothing has ever read this log back.

Confirmed live, the reason this matters: the same Blaze Pizza POS rivalry
(Qu vs. Oracle) was independently re-confirmed as unresolved on 62
separate pipeline runs across 13 days -- recorded_alongside is the right
call each individual time (don't guess, don't block), but nothing ever
escalates "this has now been re-confirmed unresolved N times over M days"
into something a human should actually look at and decide. That gap --
not the per-event decision logic, which already works well -- is what
this module closes.

This is the "CoS learns about conflicts, why they occur, and how they are
resolved" layer Todd asked for: aggregates the full decision history into
durable per-rivalry stats and flags brand+category rivalries that have
gone unresolved long enough to need a human decision, rather than a
one-time point-in-time scan. Detection only, same discipline as every
other check built this sprint -- this never picks a winner, and it never
auto-closes a rivalry; a human (or a future, separately-reviewed
extension of the mechanical tier) decides that.

CLI:
    python3 conflict_pattern_monitor.py            # text report
    python3 conflict_pattern_monitor.py --json       # machine-readable
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402

CONFLICT_LOG_PATH = core.CONFLICT_QUEUE_PATH

# A rivalry re-confirmed unresolved fewer than this many times, or spanning
# fewer days than this, isn't yet a real pattern -- a one-off recorded_
# alongside on day one is normal, expected system behavior (the Papa
# John's/Worldpay case this logic exists to record correctly), not
# something needing escalation.
MIN_RECURRENCE_COUNT = 3
MIN_SPAN_DAYS = 3


def load_conflict_log(path: Path | None = None) -> list[dict]:
    path = path or CONFLICT_LOG_PATH
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Both naive ("2026-08-03T13:30:03", the format older entries in
    # conflict_queue.jsonl actually use) and tz-aware timestamps appear in
    # the real log -- normalize to UTC so min()/max()/subtraction across a
    # mixed-format log never raises instead of silently comparing wrong.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _rivalry_key(record: dict) -> tuple | None:
    """Groups by (brand, category, the two vendors involved) regardless of
    which one is "incoming" vs "existing" in a given record -- the same
    underlying A-vs-B rivalry can appear with either vendor as the
    newcomer across different runs."""
    brand_id = record.get("brand_id")
    category = record.get("category")
    incoming_vendor = (record.get("incoming") or {}).get("vendor_id")
    existing_vendor = (record.get("existing") or {}).get("vendor_id")
    if not (brand_id and category and incoming_vendor and existing_vendor):
        return None
    return (brand_id, category, frozenset({incoming_vendor, existing_vendor}))


def summarize(records: list[dict]) -> dict:
    """Resolution-type breakdown -- the coarse "how are conflicts actually
    being resolved" view."""
    by_resolution: dict[str, int] = defaultdict(int)
    for r in records:
        by_resolution[r.get("resolution") or "unknown"] += 1
    return {"total": len(records), "by_resolution": dict(by_resolution)}


def recurring_rivalries(
    records: list[dict], *, min_count: int = MIN_RECURRENCE_COUNT, min_span_days: int = MIN_SPAN_DAYS,
) -> list[dict]:
    """Brand+category rivalries re-confirmed unresolved (resolution ==
    recorded_alongside) repeatedly over real elapsed time -- the pattern a
    single point-in-time scan of the log can't see, only accumulated
    history can. Sorted by occurrence count, most-recurring first."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        if r.get("resolution") != "recorded_alongside":
            continue
        key = _rivalry_key(r)
        if key:
            groups[key].append(r)

    findings = []
    for key, group_records in groups.items():
        dates = sorted(d for d in (_parse_dt(r.get("detected_at")) for r in group_records) if d)
        if not dates:
            continue
        span_days = (dates[-1] - dates[0]).days
        if len(group_records) < min_count or span_days < min_span_days:
            continue
        brand_id, category, vendor_ids = key
        latest = max(group_records, key=lambda r: r.get("detected_at") or "")
        brand_name = latest.get("brand_name") or brand_id
        vendor_names = sorted({
            (latest.get("incoming") or {}).get("vendor_name") or "",
            (latest.get("existing") or {}).get("vendor_name") or "",
        } - {""})
        findings.append({
            "brand_id": brand_id, "brand_name": brand_name, "category": category,
            "vendors": vendor_names, "occurrences": len(group_records),
            "first_detected_at": dates[0].isoformat(), "last_detected_at": dates[-1].isoformat(),
            "span_days": span_days, "latest_note": latest.get("note"),
        })
    findings.sort(key=lambda f: -f["occurrences"])
    return findings


def stuck_legacy_requires_confirmation(records: list[dict]) -> list[dict]:
    """The pre-2026-09-25 resolution type (see _write_conflict_record's own
    docstring): "requires_confirmation" entries were a blocking queue
    nothing in the codebase ever read back -- confirmed live, 20+ entries,
    oldest 53+ days, permanently stuck. check_relationship_conflict() no
    longer produces this resolution, but the historical entries are still
    sitting in the log, so this stays a standing (not re-growing) finding."""
    return [r for r in records if r.get("resolution") == "requires_confirmation"]


def collect_findings() -> list[str]:
    records = load_conflict_log()
    findings: list[str] = []

    rivalries = recurring_rivalries(records)
    for riv in rivalries:
        vendors_str = " vs. ".join(riv["vendors"]) if riv["vendors"] else "unknown vendors"
        findings.append(
            f"{riv['brand_name']} / {riv['category']}: {vendors_str} re-confirmed unresolved "
            f"{riv['occurrences']}x over {riv['span_days']}d (first {riv['first_detected_at'][:10]}, "
            f"last {riv['last_detected_at'][:10]}) -- needs a human decision, not another auto-record."
        )

    stuck = stuck_legacy_requires_confirmation(records)
    if stuck:
        oldest = min((d for d in (_parse_dt(r.get("detected_at")) for r in stuck) if d), default=None)
        age = f", oldest from {oldest.date().isoformat()}" if oldest else ""
        findings.append(
            f"{len(stuck)} legacy 'requires_confirmation' conflict record(s) in conflict_queue.jsonl{age} -- "
            "a resolution type retired 2026-09-25 that nothing ever reads back; these will never resolve "
            "on their own."
        )

    return findings


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    records = load_conflict_log()
    report = {
        "summary": summarize(records),
        "recurring_rivalries": recurring_rivalries(records),
        "stuck_legacy_requires_confirmation_count": len(stuck_legacy_requires_confirmation(records)),
        "findings": collect_findings(),
    }
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print("conflict_pattern_monitor:")
        print(f"  {report['summary']['total']} total decision(s) on record: {report['summary']['by_resolution']}")
        if report["findings"]:
            for f in report["findings"]:
                print(f"  - {f}")
        else:
            print("  CLEAN -- no recurring unresolved rivalries, no stuck legacy entries.")
    return 0 if not report["findings"] else 1


if __name__ == "__main__":
    sys.exit(main())
