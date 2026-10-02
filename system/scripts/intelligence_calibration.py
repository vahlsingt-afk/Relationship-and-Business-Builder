#!/usr/bin/env python3
"""Correlate Todd's intelligence_action_queue dispositions with real
downstream outcomes -- the second half of Codex handoff item #5 (the first
half, durable disposition recording, already shipped in
intelligence_action_queue.py's resolve()/STATE_PATH, 2026-09-15).

Recording "Todd accepted this" is not the same as knowing it was a GOOD
call. This module is deliberately narrow about what it claims to measure:

- Disposition counts (accepted/rejected/deferred) by item_type -- always
  computable, no external ground truth needed.
- Outcome correlation for buying_window_hypothesis items ONLY: whether an
  accepted hypothesis's entity now shows outcome="active_pursuit" in
  sales_opportunity_radar_state.json -- a real, independently-tracked signal
  (that state is itself driven by the Blue Sheet registry's real
  active/current accounts, not inferred here). Every other item_type
  (competitor_review, first_party_page_change, baseline_research_gap,
  downstream_ramification) has no equivalent real ground-truth source yet.
  Reported as "no_outcome_source" rather than a fabricated correlation --
  never claim a signal was validated by an outcome this module cannot
  actually observe.

This will read as mostly empty/uninformative for weeks after this ships --
that is correct, not a bug. There is nothing to calibrate against until
real dispositions accumulate. Never infer a false-positive/false-negative
verdict from a single data point; percentages here are only meaningful once
total_resolutions is large enough to matter, and this module does not
pretend otherwise.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

import intelligence_action_queue as iaq
import rb_core as core
import sales_opportunity_radar as radar

CACHE_PATH = core.CACHE_DIR / "intelligence_calibration.json"

# Item types with a real, independently-tracked ground-truth outcome to
# correlate against. Keep this list explicit and reviewed by hand, same
# discipline as intelligence_action_queue.py's own threshold gates -- adding
# a type here is a claim that a real outcome source exists for it, not a
# guess.
OUTCOME_CORRELATABLE_TYPES = frozenset({"buying_window_hypothesis"})
DISPOSITIONS = ("accepted", "rejected", "deferred")


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _radar_outcome(entity_id: str | None, entity_name: str | None, radar_entities: dict) -> str | None:
    """Same entity_id-or-name fallback key sales_opportunity_radar.py's own
    _calibrate() uses -- an unresolved-entity hypothesis (entity_id=None,
    e.g. the live Yum Brands case) is keyed there by entity NAME, not None.
    Looking up only by entity_id would silently miss every such row."""
    key = entity_id or entity_name
    if not key:
        return None
    return (radar_entities.get(key) or {}).get("outcome")


def build(today: date | None = None) -> dict:
    today = today or date.today()
    resolutions = list((_load(iaq.STATE_PATH).get("resolutions") or {}).values())
    radar_entities = _load(radar.STATE_PATH).get("entities") or {}

    by_item_type: dict[str, dict[str, int]] = {}
    outcome_rows: list[dict] = []
    for entry in resolutions:
        item_type = entry.get("item_type") or "unknown"
        disposition = entry.get("disposition") or "unknown"
        bucket = by_item_type.setdefault(item_type, {d: 0 for d in DISPOSITIONS})
        if disposition in bucket:
            bucket[disposition] += 1

        if item_type not in OUTCOME_CORRELATABLE_TYPES:
            continue
        real_outcome = _radar_outcome(entry.get("entity_id"), entry.get("entity"), radar_entities)
        outcome_rows.append({
            "queue_id": entry.get("queue_id"), "entity": entry.get("entity"),
            "entity_id": entry.get("entity_id"), "disposition": disposition,
            "resolved_at": entry.get("resolved_at"), "real_outcome": real_outcome,
        })

    accepted_outcome_rows = [row for row in outcome_rows if row["disposition"] == "accepted"]
    active_pursuit_confirmed = sum(1 for row in accepted_outcome_rows if row["real_outcome"] == "active_pursuit")

    report = {
        "contract": "rb_intelligence_calibration_v1",
        "date": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_resolutions": len(resolutions),
        "disposition_counts_by_item_type": by_item_type,
        "outcome_correlation": {
            "scope": sorted(OUTCOME_CORRELATABLE_TYPES),
            "accepted_with_known_outcome": len(accepted_outcome_rows),
            "accepted_confirmed_active_pursuit": active_pursuit_confirmed,
            "rows": outcome_rows,
        },
        "policy": (
            "Disposition counts are always real; outcome correlation only covers "
            "buying_window_hypothesis against sales_opportunity_radar_state.json's "
            "real active_pursuit signal. Every other item_type has no outcome source "
            "yet and is never claimed to be validated. Small total_resolutions counts "
            "are not statistically meaningful -- do not draw conclusions from a "
            "handful of dispositions."
        ),
    }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    args = parser.parse_args()
    report = build(date.fromisoformat(args.date) if args.date else None)
    print(json.dumps(
        {key: value for key, value in report.items() if key != "outcome_correlation"}
        | {"outcome_correlation": {k: v for k, v in report["outcome_correlation"].items() if k != "rows"}},
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
