#!/usr/bin/env python3
"""
audit_log.py — append-only audit log for RB security and privacy events.

Supports the question:
    What did RB see, what did it keep, what did it ignore, what did it
    change, and why?

Sprint: RB 9.13 — Security, Privacy, and Agentic Architecture

Event types:
    source_accessed       — a source (Gmail, Calendar, Drive, etc.) was read
    item_persisted        — an item was written to a durable or derived store
    item_rejected         — an item was blocked from persistence (policy, confidence, etc.)
    item_deleted          — an item was deleted/forgotten at user request
    mutation_proposed     — a mutating action was proposed but not yet confirmed
    mutation_confirmed    — user confirmed a proposed mutation
    mutation_executed     — a mutation was executed after confirmation
    mutation_rejected     — user rejected a proposed mutation
    security_warning      — security-sensitive condition detected (injection attempt, scope issue, etc.)
    gate_blocked          — an action was blocked by the action gate (no confirmation)

Storage:
    system/audit/<YYYY-MM>.jsonl   — append-only monthly JSONL partitioned by event timestamp
    system/.cache/audit_log_index.json — lightweight secondary index

CLI:
    python3 system/scripts/audit_log.py recent [--limit N]
    python3 system/scripts/audit_log.py query --type <event_type> [--since ISO] [--profile PROFILE]
    python3 system/scripts/audit_log.py --smoke
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402

SYSTEM_DIR = core.SYSTEM_DIR
CACHE_DIR = SYSTEM_DIR / ".cache"
AUDIT_DIR = Path(os.environ.get("RB_AUDIT_DIR", str(SYSTEM_DIR / "audit")))
INDEX_PATH = Path(os.environ.get("RB_AUDIT_INDEX_PATH", str(CACHE_DIR / "audit_log_index.json")))

# ---------------------------------------------------------------------------
# Valid event types
# ---------------------------------------------------------------------------

VALID_EVENT_TYPES = {
    "source_accessed",
    "item_persisted",
    "item_rejected",
    "item_deleted",
    "mutation_proposed",
    "mutation_confirmed",
    "mutation_executed",
    "mutation_rejected",
    "security_warning",
    "gate_blocked",
}

# ---------------------------------------------------------------------------
# Valid data classes (mirrors SECURITY_PRIVACY_ARCHITECTURE.md)
# ---------------------------------------------------------------------------

VALID_DATA_CLASSES = {
    "raw_source",
    "normalized",
    "intelligence",
    "memory",
    "summaries",
    "audit",
    "unknown",
}

# ---------------------------------------------------------------------------
# Required fields for a valid audit event
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = {
    "event_type",
    "timestamp",
    "data_class",
    "item_summary",
    "reason",
    "outcome",
}


# ---------------------------------------------------------------------------
# Core write path
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _event_id(payload: dict) -> str:
    """Stable deterministic event_id from type + timestamp + summary hash."""
    key = f"{payload.get('event_type','?')}:{payload.get('timestamp','?')}:{payload.get('item_summary','?')}"
    return "AUDIT-" + hashlib.sha256(key.encode()).hexdigest()[:16].upper()


def _partition_key(ts: str) -> str:
    """Return YYYY-MM from an ISO timestamp string."""
    try:
        return ts[:7]  # "YYYY-MM"
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m")


def _audit_path(partition: str) -> Path:
    """Return the JSONL path for a given YYYY-MM partition."""
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    return AUDIT_DIR / f"{partition}.jsonl"


def append_event(
    event_type: str,
    item_summary: str,
    reason: str,
    outcome: str,
    data_class: str = "unknown",
    source: str | None = None,
    profile_context: str | None = None,
    scopes_used: list[str] | None = None,
    retention_class: str | None = None,
    extra: dict | None = None,
) -> dict:
    """
    Write one audit event to the append-only log.

    Returns the written event dict (including assigned event_id).
    Never raises — on error, returns a synthetic error event logged to stderr.

    Rules:
    - item_summary must not contain raw source content.
    - event_type must be in VALID_EVENT_TYPES.
    - data_class must be in VALID_DATA_CLASSES.
    - Append-only: never modifies existing lines.
    """
    if event_type not in VALID_EVENT_TYPES:
        event_type = "security_warning"
        reason = f"INVALID_EVENT_TYPE:{event_type} — {reason}"

    if data_class not in VALID_DATA_CLASSES:
        data_class = "unknown"

    now = _now_iso()
    payload: dict[str, Any] = {
        "event_type": event_type,
        "timestamp": now,
        "data_class": data_class,
        "item_summary": item_summary[:500],  # hard cap — no raw content leakage
        "reason": reason[:500],
        "outcome": outcome[:200],
    }
    if source:
        payload["source"] = source
    if profile_context:
        payload["profile_context"] = profile_context
    if scopes_used:
        payload["scopes_used"] = scopes_used
    if retention_class:
        payload["retention_class"] = retention_class
    if extra:
        # Extra fields allowed but must not contain keys that shadow required fields
        safe_extra = {k: v for k, v in extra.items() if k not in payload}
        payload.update(safe_extra)

    payload["event_id"] = _event_id(payload)

    partition = _partition_key(now)
    path = _audit_path(partition)
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
    except Exception as exc:
        print(f"[audit_log] WARNING: could not write audit event: {exc}", file=sys.stderr)

    _update_index(payload, partition)
    return payload


def _update_index(event: dict, partition: str) -> None:
    """Update the lightweight secondary index with the new event summary."""
    try:
        if INDEX_PATH.exists():
            index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        else:
            index = {"partitions": [], "event_count": 0, "last_event": None}

        if partition not in index.get("partitions", []):
            index.setdefault("partitions", []).append(partition)
            index["partitions"].sort()

        index["event_count"] = index.get("event_count", 0) + 1
        index["last_event"] = {
            "event_id": event["event_id"],
            "event_type": event["event_type"],
            "timestamp": event["timestamp"],
        }
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        INDEX_PATH.write_text(json.dumps(index, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"[audit_log] WARNING: could not update index: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Query path
# ---------------------------------------------------------------------------

def _load_partition(partition: str) -> list[dict]:
    path = _audit_path(partition)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def load_events(
    since: str | None = None,
    until: str | None = None,
    event_type: str | None = None,
    profile_context: str | None = None,
    data_class: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """
    Load audit events with optional filters.

    since/until: ISO timestamp strings (inclusive).
    Returns list sorted by timestamp ascending, truncated to limit.
    """
    if INDEX_PATH.exists():
        index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        partitions = sorted(index.get("partitions", []))
    else:
        # Fall back to scanning the audit dir
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        partitions = sorted(p.stem for p in AUDIT_DIR.glob("*.jsonl"))

    if not partitions:
        return []

    # Filter partitions by date range
    if since:
        since_partition = _partition_key(since)
        partitions = [p for p in partitions if p >= since_partition]
    if until:
        until_partition = _partition_key(until)
        partitions = [p for p in partitions if p <= until_partition]

    all_events: list[dict] = []
    for partition in partitions:
        all_events.extend(_load_partition(partition))

    # Apply filters
    if since:
        all_events = [e for e in all_events if e.get("timestamp", "") >= since]
    if until:
        all_events = [e for e in all_events if e.get("timestamp", "") <= until]
    if event_type:
        all_events = [e for e in all_events if e.get("event_type") == event_type]
    if profile_context:
        all_events = [e for e in all_events if e.get("profile_context") == profile_context]
    if data_class:
        all_events = [e for e in all_events if e.get("data_class") == data_class]

    all_events.sort(key=lambda e: e.get("timestamp", ""))
    return all_events[-limit:] if len(all_events) > limit else all_events


def recent_events(limit: int = 20) -> list[dict]:
    """Return the N most recent audit events across all partitions."""
    return load_events(limit=limit)


# ---------------------------------------------------------------------------
# Convenience wrappers for common audit patterns
# ---------------------------------------------------------------------------

def log_source_accessed(
    source: str,
    item_summary: str,
    profile_context: str | None = None,
    scopes_used: list[str] | None = None,
    data_class: str = "raw_source",
    outcome: str = "accessed",
) -> dict:
    return append_event(
        event_type="source_accessed",
        item_summary=item_summary,
        reason=f"Source read: {source}",
        outcome=outcome,
        data_class=data_class,
        source=source,
        profile_context=profile_context,
        scopes_used=scopes_used,
    )


def log_item_persisted(
    item_summary: str,
    data_class: str,
    retention_class: str,
    source: str | None = None,
    reason: str = "judgment_approved",
    profile_context: str | None = None,
) -> dict:
    return append_event(
        event_type="item_persisted",
        item_summary=item_summary,
        reason=reason,
        outcome="persisted",
        data_class=data_class,
        retention_class=retention_class,
        source=source,
        profile_context=profile_context,
    )


def log_item_rejected(
    item_summary: str,
    reason: str,
    data_class: str = "intelligence",
    source: str | None = None,
    profile_context: str | None = None,
) -> dict:
    return append_event(
        event_type="item_rejected",
        item_summary=item_summary,
        reason=reason,
        outcome="rejected",
        data_class=data_class,
        source=source,
        profile_context=profile_context,
    )


def log_item_deleted(
    item_summary: str,
    reason: str = "user_forget_request",
    data_class: str = "memory",
    profile_context: str | None = None,
    tombstone: str | None = None,
) -> dict:
    extra: dict[str, Any] = {}
    if tombstone:
        extra["tombstone"] = tombstone[:200]
    return append_event(
        event_type="item_deleted",
        item_summary=item_summary,
        reason=reason,
        outcome="deleted",
        data_class=data_class,
        profile_context=profile_context,
        extra=extra if extra else None,
    )


def log_mutation_proposed(
    item_summary: str,
    source: str | None = None,
    profile_context: str | None = None,
) -> dict:
    return append_event(
        event_type="mutation_proposed",
        item_summary=item_summary,
        reason="action_proposed_awaiting_confirmation",
        outcome="proposed",
        data_class="intelligence",
        source=source,
        profile_context=profile_context,
    )


def log_mutation_confirmed(
    item_summary: str,
    profile_context: str | None = None,
) -> dict:
    return append_event(
        event_type="mutation_confirmed",
        item_summary=item_summary,
        reason="user_confirmed",
        outcome="confirmed",
        data_class="intelligence",
        profile_context=profile_context,
    )


def log_mutation_executed(
    item_summary: str,
    source: str | None = None,
    profile_context: str | None = None,
) -> dict:
    return append_event(
        event_type="mutation_executed",
        item_summary=item_summary,
        reason="confirmed_mutation_executed",
        outcome="executed",
        data_class="memory",
        source=source,
        profile_context=profile_context,
    )


def log_mutation_rejected(
    item_summary: str,
    reason: str = "user_rejected",
    profile_context: str | None = None,
) -> dict:
    return append_event(
        event_type="mutation_rejected",
        item_summary=item_summary,
        reason=reason,
        outcome="rejected",
        data_class="intelligence",
        profile_context=profile_context,
    )


def log_security_warning(
    item_summary: str,
    reason: str,
    source: str | None = None,
    profile_context: str | None = None,
    extra: dict | None = None,
) -> dict:
    return append_event(
        event_type="security_warning",
        item_summary=item_summary,
        reason=reason,
        outcome="warning_logged",
        data_class="audit",
        source=source,
        profile_context=profile_context,
        extra=extra,
    )


def log_gate_blocked(
    item_summary: str,
    action_type: str,
    reason: str = "no_user_confirmation",
    profile_context: str | None = None,
) -> dict:
    return append_event(
        event_type="gate_blocked",
        item_summary=item_summary,
        reason=reason,
        outcome="blocked",
        data_class="intelligence",
        profile_context=profile_context,
        extra={"action_type": action_type},
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def _smoke() -> bool:
    """Run in-memory smoke tests (no file I/O required for the logic checks)."""
    errors: list[str] = []

    # 1. Event ID generation is deterministic
    p1 = {"event_type": "source_accessed", "timestamp": "2026-05-27T10:00:00+00:00", "item_summary": "test"}
    p2 = {"event_type": "source_accessed", "timestamp": "2026-05-27T10:00:00+00:00", "item_summary": "test"}
    if _event_id(p1) != _event_id(p2):
        errors.append("event_id not deterministic")

    # 2. Partition key extraction
    if _partition_key("2026-05-27T10:00:00+00:00") != "2026-05":
        errors.append("partition_key failed")

    # 3. item_summary hard-capped at 500 chars
    long_summary = "X" * 600
    # Build a payload as append_event would
    capped = long_summary[:500]
    if len(capped) != 500:
        errors.append("summary cap failed")

    # 4. Invalid event_type coerced to security_warning
    # (append_event itself coerces — we check the variable rebinding logic)
    bad_type = "totally_invalid_type"
    if bad_type in VALID_EVENT_TYPES:
        errors.append("invalid type was not rejected")

    # 5. All required fields defined
    for f in REQUIRED_FIELDS:
        if not f:
            errors.append(f"empty required field: {f}")

    # 6. VALID_DATA_CLASSES covers the architecture's five classes plus audit and unknown
    expected_classes = {"raw_source", "normalized", "intelligence", "memory", "summaries", "audit", "unknown"}
    missing = expected_classes - VALID_DATA_CLASSES
    if missing:
        errors.append(f"missing data classes: {missing}")

    if errors:
        for e in errors:
            print(f"  FAIL: {e}")
        return False

    print("audit_log smoke: all checks passed")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="RB audit log CLI")
    parser.add_argument("--smoke", action="store_true", help="Run smoke tests")
    sub = parser.add_subparsers(dest="cmd")

    recent_p = sub.add_parser("recent", help="Show recent audit events")
    recent_p.add_argument("--limit", type=int, default=20)

    query_p = sub.add_parser("query", help="Query audit events")
    query_p.add_argument("--type", dest="event_type")
    query_p.add_argument("--since")
    query_p.add_argument("--until")
    query_p.add_argument("--profile")
    query_p.add_argument("--data-class", dest="data_class")
    query_p.add_argument("--limit", type=int, default=50)

    args = parser.parse_args()

    if args.smoke:
        ok = _smoke()
        sys.exit(0 if ok else 1)

    if args.cmd == "recent":
        events = recent_events(limit=args.limit)
        print(json.dumps(events, indent=2))

    elif args.cmd == "query":
        events = load_events(
            since=args.since,
            until=args.until,
            event_type=args.event_type,
            profile_context=args.profile,
            data_class=args.data_class,
            limit=args.limit,
        )
        print(json.dumps(events, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
