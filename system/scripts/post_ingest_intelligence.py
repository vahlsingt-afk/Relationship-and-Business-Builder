#!/usr/bin/env python3
"""
post_ingest_intelligence.py — Stage 6 intelligence generation from freshly
ingested structured data (RB-DEFECT-064 Phase 4).

Given the baseline entries a structured-dataset ingest run touched (matched +
enhanced) and created, surfaces two genuine signals using RB's existing
relationship-intelligence primitives — no new graph store, no new scoring
model, nothing invented for this module:

  - Dormant relationships resurfaced: an *existing* contact this import
    touched has gone quiet (no `last_touch` on file, or it's older than
    DORMANT_DAYS). An old CRM export is often exactly what makes someone
    like this visible again.
  - Warm introduction candidates: a *newly created* contact's company
    already has a baseline insider who could broker a path in, per
    `rb_core.find_intro_paths` — the same broker-scoring `intro_engine.py`
    already uses for on-demand lookups, applied here automatically instead
    of waiting for the operator to ask about that company by name.

Deliberately does not attempt Person->Industry/Event/Opportunity/Meeting
edges (RB-DEFECT-064's original Stage 5 ask). A CRM "export contacts" CSV
carries none of that data — fabricating it would violate the same
never-report-a-number-that-wasn't-actually-computed discipline
mutation_report.py already enforces for mutation counts.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402

DORMANT_DAYS = 365
MAX_INTRO_LOOKUPS = 50


def dormant_relationships_resurfaced(
    touched_ids: list[str],
    baseline_by_id: dict[str, dict],
    *,
    today: date | None = None,
    dormant_days: int = DORMANT_DAYS,
) -> list[dict[str, Any]]:
    """Existing contacts this import touched that have gone quiet.

    A contact qualifies if `last_touch` is null, or older than `dormant_days`.
    Order follows `touched_ids` (caller controls it, e.g. import row order).
    """
    today = today or date.today()
    out: list[dict[str, Any]] = []
    for entry_id in touched_ids:
        entry = baseline_by_id.get(entry_id)
        if entry is None:
            continue
        last_touch = entry.get("last_touch")
        days_since: int | None = None
        if last_touch:
            try:
                days_since = (today - date.fromisoformat(last_touch)).days
            except ValueError:
                days_since = None
        if last_touch is None or (days_since is not None and days_since > dormant_days):
            out.append({
                "id": entry_id,
                "name": entry.get("name"),
                "last_touch": last_touch,
                "days_since_last_touch": days_since,
            })
    return out


def warm_intro_candidates(
    new_entries: list[dict],
    baseline: list[dict],
    *,
    today: date | None = None,
    threads: list[dict] | None = None,
    limit: int = MAX_INTRO_LOOKUPS,
) -> list[dict[str, Any]]:
    """Newly created contacts whose company already has a viable broker on file.

    One `find_intro_paths` lookup per distinct new-entry company (capped at
    `limit` distinct companies, to keep a large import bounded — the rest are
    simply not evaluated, not silently skipped-and-hidden: callers can see
    how many new companies existed vs. how many were checked).

    Only counts a candidate whose `has_proximity` is true (same company,
    shared Circle, or active-thread overlap with that company) — the target
    company genuinely has an insider. `find_intro_paths` also returns a
    highest-DRR "ask around" fallback when no insider exists at all; that's
    a legitimate on-demand suggestion for `intro_engine.py`'s interactive use,
    but reporting it here as a "warm introduction candidate" would overstate
    a connection that doesn't actually exist — not the never-fabricate-a-number
    discipline this module exists to uphold.
    """
    today = today or date.today()
    company_broker_cache: dict[str, dict | None] = {}
    out: list[dict[str, Any]] = []

    for entry in new_entries:
        company = (entry.get("current_company") or "").strip()
        if not company:
            continue
        if company not in company_broker_cache:
            if len(company_broker_cache) >= limit:
                continue
            paths = core.find_intro_paths(
                company, limit=1, baseline=baseline, threads=threads, today=today,
            )
            candidates = paths.get("candidate_brokers") or []
            top = candidates[0] if candidates else None
            company_broker_cache[company] = top if (top and top.get("has_proximity")) else None
        broker = company_broker_cache.get(company)
        if broker:
            out.append({
                "new_contact": entry.get("name"),
                "company": company,
                "broker_id": broker.get("id"),
                "broker_name": broker.get("name"),
                "reason": broker.get("reason"),
            })
    return out


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def _smoke() -> bool:
    errors: list[str] = []
    today = date(2026, 7, 7)

    baseline_by_id = {
        "dormant-contact": {"id": "dormant-contact", "name": "Dormant Contact", "last_touch": "2024-01-01"},
        "fresh-contact": {"id": "fresh-contact", "name": "Fresh Contact", "last_touch": "2026-07-01"},
        "never-touched": {"id": "never-touched", "name": "Never Touched", "last_touch": None},
    }
    dormant = dormant_relationships_resurfaced(
        ["dormant-contact", "fresh-contact", "never-touched"], baseline_by_id, today=today,
    )
    dormant_ids = {d["id"] for d in dormant}
    if dormant_ids != {"dormant-contact", "never-touched"}:
        errors.append(f"expected dormant-contact + never-touched flagged, got {dormant_ids}")

    baseline = [
        {"id": "insider-1", "name": "Insider One", "current_company": "Acme Corp",
         "signal_class": "RC", "rc_tier": "inner", "circles": [], "tags": [], "notes": "", "last_touch": "2026-06-01"},
        {"id": "new-hire", "name": "New Hire", "current_company": "Acme Corp",
         "signal_class": "VC", "circles": [], "tags": [], "notes": "", "last_touch": None},
    ]
    new_entries = [{"id": "new-hire", "name": "New Hire", "current_company": "Acme Corp"}]
    candidates = warm_intro_candidates(new_entries, baseline, today=today, threads=[])
    if not candidates or candidates[0]["broker_name"] != "Insider One":
        errors.append(f"expected Insider One as broker for Acme Corp, got {candidates}")

    no_company_entries = [{"id": "x", "name": "No Company", "current_company": None}]
    if warm_intro_candidates(no_company_entries, baseline, today=today, threads=[]):
        errors.append("entries with no company should never produce a candidate")

    if errors:
        for e in errors:
            print(f"  FAIL: {e}")
        return False
    print("post_ingest_intelligence smoke: all checks passed")
    return True


if __name__ == "__main__":
    sys.exit(0 if _smoke() else 1)
