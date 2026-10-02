#!/usr/bin/env python3
"""
relationship_health_aggregate.py — persist metadata-derived relationship
health into baseline_index.json (Metadata-First Connector, RB Phase 1).

This script does not compute anything new. It reuses RB's existing
metadata-only computations and writes their per-contact result onto each
baseline entry under a `relationship_health` key, so downstream consumers
(daily brief, cards) can read it without recomputing:

    - rb_core.drr_score()            — recency/tier/circles/completeness/evidence.
    - rb_core.email_overlay()        — sent_followups response_overdue /
                                        awaiting_response classification
                                        (business_days_since_sent, expected_response_by).
    - rb_core.interaction_overlay()  — messages/calls touch counts (metadata only).

Nothing written here is a `durable_memory`-class item under
privacy_guard.py's five-layer pipeline — this is a structured-field update
on an existing baseline record, the same category as the `last_touch`/
`rc_state` mutations already applied elsewhere in this codebase (see
mutations.py, refresh_sources.py). privacy_guard.guard_durable_memory()'s
required-fields gate therefore does not apply. The write is still snapshotted
first via mutations.snapshot(), matching the safe-write discipline used
throughout mutations.py.

The persisted block is fully metadata-derived: no email/message body,
subject line, or snippet content is ever copied into it — only counts,
scores, and timestamps.

CLI:
    python3 system/scripts/relationship_health_aggregate.py --apply
    python3 system/scripts/relationship_health_aggregate.py --json   # dry run, no write
    python3 system/scripts/relationship_health_aggregate.py --smoke
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import mutations  # noqa: E402

_OVERDUE_AND_AWAITING = {"response_overdue", "awaiting_response"}


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _to_iso(raw_value: str | None) -> str | None:
    """Normalize an inbox timestamp (ISO or RFC 2822) to ISO 8601 for
    persistence, so relationship_health always carries a consistently
    sortable, parseable timestamp shape regardless of source format."""
    dt = core._parse_msg_dt(raw_value)
    return dt.isoformat() if dt else raw_value


def _later_iso(current: str | None, candidate_raw: str | None) -> str | None:
    """Return whichever of two timestamps is later, comparing by parsed
    datetime (not string order — RFC 2822 strings like 'Wed, 10 Jun 2026'
    don't sort chronologically as strings)."""
    candidate_iso = _to_iso(candidate_raw)
    if current is None:
        return candidate_iso
    current_dt = core._parse_msg_dt(current)
    candidate_dt = core._parse_msg_dt(candidate_raw)
    if candidate_dt is None:
        return current
    if current_dt is None or candidate_dt > current_dt:
        return candidate_iso
    return current


def _within_days(raw_value: str | None, cutoff_date: date) -> bool:
    """Best-effort recency check. Inbox timestamps arrive in either ISO 8601
    or RFC 2822 (raw Gmail thread-list header) shape — reuse rb_core's
    tolerant parser (core._parse_msg_dt) rather than assuming ISO, matching
    the same fix applied to relationship_signals.py's sent-followup dating."""
    dt = core._parse_msg_dt(raw_value)
    if dt is None:
        return False
    return dt.date() >= cutoff_date


def compute_relationship_health(
    baseline: list[dict],
    today: date | None = None,
    threads: list[dict] | None = None,
    email_overlay_data: dict | None = None,
    interaction_overlay_data: dict | None = None,
) -> dict[str, dict]:
    """Return {contact_id: relationship_health_block} for every baseline entry.

    Overlay data can be injected for tests; defaults to live rb_core loaders.
    """
    today = today or date.today()
    if threads is None:
        threads = [t for t in core.load_active_threads() if t.get("status") == "open"]
    if email_overlay_data is None:
        email_overlay_data = core.email_overlay(baseline=baseline, threads=threads)
    if interaction_overlay_data is None:
        interaction_overlay_data = core.interaction_overlay(baseline=baseline, today=today, recent_days=90)

    cutoff_90d = today - timedelta(days=90)

    # ── Commitment intelligence: outstanding follow-ups per contact ────────
    followups: dict[str, dict] = {}
    email_touches_90d: dict[str, int] = {}
    for sf in email_overlay_data.get("sent_followups") or []:
        status = sf.get("response_status")
        sent_at = sf.get("sent_at")
        for contact in sf.get("matched_contacts") or []:
            cid = contact.get("id")
            if not cid:
                continue
            if _within_days(sent_at, cutoff_90d):
                email_touches_90d[cid] = email_touches_90d.get(cid, 0) + 1
            if status not in _OVERDUE_AND_AWAITING:
                continue
            slot = followups.setdefault(cid, {
                "outstanding_follow_ups_count": 0,
                "last_response_overdue_at": None,
                "last_awaiting_response_at": None,
            })
            slot["outstanding_follow_ups_count"] += 1
            if status == "response_overdue":
                slot["last_response_overdue_at"] = _later_iso(slot["last_response_overdue_at"], sent_at)
            else:
                slot["last_awaiting_response_at"] = _later_iso(slot["last_awaiting_response_at"], sent_at)

    for row in email_overlay_data.get("from_baseline") or []:
        cid = (row.get("match") or {}).get("id")
        if cid and _within_days(row.get("last_message_at"), cutoff_90d):
            email_touches_90d[cid] = email_touches_90d.get(cid, 0) + 1

    # ── Communication frequency: messages + calls (metadata-only) ─────────
    interaction_touches: dict[str, int] = {}
    for slot in interaction_overlay_data.get("matched_contacts") or []:
        cid = slot.get("id")
        if not cid:
            continue
        interaction_touches[cid] = (
            slot.get("messages_in", 0) + slot.get("messages_out", 0)
            + slot.get("calls_in", 0) + slot.get("calls_out", 0) + slot.get("calls_missed", 0)
        )

    # ── Assemble per-contact block ─────────────────────────────────────────
    result: dict[str, dict] = {}
    computed_at = datetime.now(timezone.utc).isoformat()
    for entry in baseline:
        cid = entry.get("id")
        if not cid:
            continue
        drr = core.drr_score(entry, today, threads=threads)
        touches = email_touches_90d.get(cid, 0) + interaction_touches.get(cid, 0)
        commitment = followups.get(cid, {
            "outstanding_follow_ups_count": 0,
            "last_response_overdue_at": None,
            "last_awaiting_response_at": None,
        })
        result[cid] = {
            "computed_at": computed_at,
            "drr_score": drr["score"],
            "drr_components": drr["components"],
            "communication_frequency_90d": round(touches / 3.0, 1),
            "outstanding_follow_ups_count": commitment["outstanding_follow_ups_count"],
            "last_response_overdue_at": commitment["last_response_overdue_at"],
            "last_awaiting_response_at": commitment["last_awaiting_response_at"],
            "source": "relationship_health_aggregate.py",
        }
    return result


def apply_to_baseline(baseline_path: Path = core.BASELINE_PATH) -> dict:
    """Compute relationship_health for every entry and write it back to baseline_index.json."""
    baseline = json.loads(baseline_path.read_text())
    health_by_id = compute_relationship_health(baseline)
    for entry in baseline:
        cid = entry.get("id")
        if cid in health_by_id:
            entry["relationship_health"] = health_by_id[cid]

    mutations.snapshot(baseline_path, f"pre-relationship-health-aggregate-{_now_tag()}")
    baseline_path.write_text(json.dumps(baseline, indent=2) + "\n")
    return {"entries_updated": len(health_by_id), "path": str(baseline_path)}


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def _smoke() -> bool:
    errors: list[str] = []
    today = date(2026, 7, 6)

    baseline = [
        {
            "id": "jane-doe",
            "name": "Jane Doe",
            "current_company": "Acme Inc",
            "signal_class": "RC",
            "rc_state": "ACTIVE",
            "rc_tier": "inner",
            "last_touch": "2026-07-01",
            "circles": ["circle-a"],
            "email": "jane@acme.com",
            "phone": None,
            "sources": ["manual"],
        },
        {
            "id": "john-silent",
            "name": "John Silent",
            "current_company": "Beta Co",
            "signal_class": "LKI",
            "rc_state": None,
            "rc_tier": None,
            "last_touch": "2025-01-01",
            "circles": [],
            "email": "john@beta.com",
            "phone": None,
            "sources": [],
        },
    ]

    fake_email_overlay = {
        "sent_followups": [
            {
                "response_status": "response_overdue",
                "sent_at": "2026-06-25T00:00:00+00:00",
                "matched_contacts": [{"id": "jane-doe", "name": "Jane Doe"}],
            },
        ],
        "from_baseline": [
            {"match": {"id": "jane-doe"}, "last_message_at": "2026-07-01T00:00:00+00:00"},
        ],
    }
    fake_interaction_overlay = {
        "matched_contacts": [
            {"id": "jane-doe", "messages_in": 2, "messages_out": 1, "calls_in": 0, "calls_out": 0, "calls_missed": 0},
        ],
    }

    result = compute_relationship_health(
        baseline, today=today, threads=[],
        email_overlay_data=fake_email_overlay,
        interaction_overlay_data=fake_interaction_overlay,
    )

    # ── 1. Overdue follow-up detected for jane-doe ─────────────────────────
    if result["jane-doe"]["outstanding_follow_ups_count"] < 1:
        errors.append("compute_relationship_health: jane-doe should have >=1 outstanding follow-up")
    if result["jane-doe"]["last_response_overdue_at"] != "2026-06-25T00:00:00+00:00":
        errors.append("compute_relationship_health: last_response_overdue_at not set correctly")

    # ── 2. drr_score matches calling rb_core.drr_score() directly ──────────
    direct_drr = core.drr_score(baseline[0], today, threads=[])
    if result["jane-doe"]["drr_score"] != direct_drr["score"]:
        errors.append("compute_relationship_health: drr_score should match rb_core.drr_score() directly")

    # ── 3. Silent contact has no outstanding follow-ups, no email touches ──
    if result["john-silent"]["outstanding_follow_ups_count"] != 0:
        errors.append("compute_relationship_health: john-silent should have 0 outstanding follow-ups")

    # ── 4. Idempotency: recomputing with identical inputs yields identical
    #      scores/counts (only computed_at is expected to change) ──────────
    result2 = compute_relationship_health(
        baseline, today=today, threads=[],
        email_overlay_data=fake_email_overlay,
        interaction_overlay_data=fake_interaction_overlay,
    )
    for cid in ("jane-doe", "john-silent"):
        a, b = dict(result[cid]), dict(result2[cid])
        a.pop("computed_at", None)
        b.pop("computed_at", None)
        if a != b:
            errors.append(f"compute_relationship_health: non-idempotent output for {cid}")

    # ── 5. No raw content ever appears in the block ────────────────────────
    import privacy_guard as pg
    for cid, block in result.items():
        for v in block.values():
            if isinstance(v, str) and len(v) > pg.MAX_EXCERPT_LEN:
                errors.append(f"compute_relationship_health: {cid} block contains a value exceeding MAX_EXCERPT_LEN")

    if errors:
        for e in errors:
            print(f"  FAIL: {e}")
        return False

    print("relationship_health_aggregate smoke: all checks passed")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Persist metadata-derived relationship health to baseline_index.json")
    parser.add_argument("--smoke", action="store_true", help="Run smoke tests")
    parser.add_argument("--apply", action="store_true", help="Compute and write relationship_health onto baseline_index.json")
    parser.add_argument("--json", dest="json_out", action="store_true", help="Dry run: print computed blocks as JSON, no write")
    args = parser.parse_args()

    if args.smoke:
        return 0 if _smoke() else 1

    if args.apply:
        result = apply_to_baseline()
        print(json.dumps(result))
        return 0

    if args.json_out:
        baseline = core.load_baseline()
        health = compute_relationship_health(baseline)
        print(json.dumps(health, indent=2))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
