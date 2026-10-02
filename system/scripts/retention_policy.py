#!/usr/bin/env python3
"""
retention_policy.py — retention class definitions and expiry enforcement.

Defines the seven retention classes from SECURITY_PRIVACY_ARCHITECTURE.md,
expiry windows, deletability rules, and the delete/forget flow contract.

Sprint: RB 9.13 — Security, Privacy, and Agentic Architecture

Retention classes:
    ephemeral_raw         Raw content; purged after 24h unless pinned
    source_cache          Normalized cache for refresh/debug; 72h
    derived_intelligence  Structured facts while relevant; 90 days
    durable_memory        Judgment-approved long-term memory; indefinite
    audit_log             Access/action/persistence metadata; 365 days, append-only
    user_exportable       Profile and relationship memory; indefinite
    forgettable           Per-request deletion required; must support forget flow

CLI:
    python3 system/scripts/retention_policy.py --smoke
    python3 system/scripts/retention_policy.py check <retention_class> <stored_at_iso>
    python3 system/scripts/retention_policy.py classes
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

# ---------------------------------------------------------------------------
# Retention class definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RetentionClass:
    name: str
    label: str
    window_hours: int | None          # None = indefinite
    deletable: bool
    append_only: bool                 # True = no deletes ever (audit_log)
    description: str
    requires_forget_flow: bool = False  # True = must support user-driven delete


# Sentinel for "no expiry"
INDEFINITE = None

RETENTION_CLASSES: dict[str, RetentionClass] = {
    "ephemeral_raw": RetentionClass(
        name="ephemeral_raw",
        label="Ephemeral Raw",
        window_hours=24,
        deletable=True,
        append_only=False,
        description="Raw content deleted or purged after 24h unless user explicitly pins it.",
    ),
    "source_cache": RetentionClass(
        name="source_cache",
        label="Source Cache",
        window_hours=72,
        deletable=True,
        append_only=False,
        description="Normalized cache retained for refresh/debug window.",
    ),
    "derived_intelligence": RetentionClass(
        name="derived_intelligence",
        label="Derived Intelligence",
        window_hours=90 * 24,  # 90 days
        deletable=True,
        append_only=False,
        description="Structured facts retained while relevant.",
    ),
    "durable_memory": RetentionClass(
        name="durable_memory",
        label="Durable Memory",
        window_hours=INDEFINITE,
        deletable=True,
        append_only=False,
        requires_forget_flow=True,
        description="Judgment-approved long-term relationship memory.",
    ),
    "audit_log": RetentionClass(
        name="audit_log",
        label="Audit Log",
        window_hours=365 * 24,  # 365 days minimum
        deletable=False,
        append_only=True,
        description="Access/action/persistence metadata. Append-only; no deletes.",
    ),
    "user_exportable": RetentionClass(
        name="user_exportable",
        label="User Exportable",
        window_hours=INDEFINITE,
        deletable=True,
        append_only=False,
        requires_forget_flow=True,
        description="Profile and relationship memory the user can export.",
    ),
    "forgettable": RetentionClass(
        name="forgettable",
        label="Forgettable",
        window_hours=INDEFINITE,
        deletable=True,
        append_only=False,
        requires_forget_flow=True,
        description="Must support delete/forget on user request.",
    ),
}

ALL_CLASS_NAMES = set(RETENTION_CLASSES.keys())


# ---------------------------------------------------------------------------
# Data class → default retention class mapping
# ---------------------------------------------------------------------------

DATA_CLASS_DEFAULT_RETENTION: dict[str, str] = {
    "raw_source":    "ephemeral_raw",
    "normalized":    "source_cache",
    "intelligence":  "derived_intelligence",
    "memory":        "durable_memory",
    "summaries":     "user_exportable",
    "audit":         "audit_log",
    "unknown":       "ephemeral_raw",   # conservative default
}


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def get_class(name: str) -> RetentionClass:
    """Return the RetentionClass for the given name. Raises KeyError if unknown."""
    if name not in RETENTION_CLASSES:
        raise KeyError(f"Unknown retention class: {name!r}. Valid: {sorted(ALL_CLASS_NAMES)}")
    return RETENTION_CLASSES[name]


def default_retention_for_data_class(data_class: str) -> str:
    """Return the default retention class name for a given data class."""
    return DATA_CLASS_DEFAULT_RETENTION.get(data_class, "ephemeral_raw")


def is_expired(retention_class_name: str, stored_at: str | datetime) -> bool:
    """
    Return True if an item with this retention class stored at `stored_at`
    has passed its expiry window.

    stored_at: ISO timestamp string or datetime.
    Indefinite retention classes never expire (return False).
    """
    rc = get_class(retention_class_name)
    if rc.window_hours is None:
        return False  # indefinite

    if isinstance(stored_at, str):
        try:
            stored_dt = datetime.fromisoformat(stored_at.replace("Z", "+00:00"))
        except ValueError:
            return False  # cannot parse → assume not expired
    else:
        stored_dt = stored_at

    if stored_dt.tzinfo is None:
        stored_dt = stored_dt.replace(tzinfo=timezone.utc)

    expiry = stored_dt + timedelta(hours=rc.window_hours)
    return datetime.now(timezone.utc) >= expiry


def expiry_at(retention_class_name: str, stored_at: str | datetime) -> datetime | None:
    """
    Return the datetime at which this item expires, or None if indefinite.
    """
    rc = get_class(retention_class_name)
    if rc.window_hours is None:
        return None

    if isinstance(stored_at, str):
        try:
            stored_dt = datetime.fromisoformat(stored_at.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        stored_dt = stored_at

    if stored_dt.tzinfo is None:
        stored_dt = stored_dt.replace(tzinfo=timezone.utc)

    return stored_dt + timedelta(hours=rc.window_hours)


def can_delete(retention_class_name: str) -> bool:
    """Return True if items of this retention class are deletable."""
    return get_class(retention_class_name).deletable


def requires_forget_flow(retention_class_name: str) -> bool:
    """Return True if this retention class requires a user-driven forget flow."""
    return get_class(retention_class_name).requires_forget_flow


def is_append_only(retention_class_name: str) -> bool:
    """Return True if this retention class is append-only (no deletes)."""
    return get_class(retention_class_name).append_only


def assign_retention_class(item: dict) -> str:
    """
    Given an item dict with optional 'retention_class' and 'data_class' keys,
    return the appropriate retention class name.

    Priority:
    1. item['retention_class'] if present and valid
    2. default for item['data_class'] if present
    3. 'ephemeral_raw' as conservative fallback
    """
    explicit = item.get("retention_class")
    if explicit and explicit in ALL_CLASS_NAMES:
        return explicit

    data_class = item.get("data_class", "unknown")
    return default_retention_for_data_class(data_class)


# ---------------------------------------------------------------------------
# Delete / Forget flow helpers
# ---------------------------------------------------------------------------

@dataclass
class ForgetRequest:
    """Represents a user-driven delete/forget request."""
    scope: str                        # "contact", "company", "thread", "source", "date_range", "all"
    scope_value: str                  # e.g. contact name or company name
    confirmed: bool = False
    items_to_remove: list[dict] = field(default_factory=list)
    tombstones: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def build_forget_preview(items: list[dict]) -> list[dict]:
    """
    Given candidate items for deletion, return a safe preview list.
    Each preview entry shows item_summary and retention_class — no raw content.
    """
    preview = []
    for item in items:
        preview.append({
            "item_summary": item.get("item_summary", item.get("description", "unknown item")),
            "retention_class": assign_retention_class(item),
            "data_class": item.get("data_class", "unknown"),
            "stored_at": item.get("stored_at", item.get("timestamp", "unknown")),
            "source": item.get("source", "unknown"),
            "deletable": can_delete(assign_retention_class(item)),
        })
    return preview


def validate_forget_request(request: ForgetRequest, items: list[dict]) -> tuple[bool, list[str]]:
    """
    Validate a forget request against candidate items.

    Returns (is_valid, list_of_errors).
    Blocks deletion of append-only items.
    """
    errors: list[str] = []

    if not request.confirmed:
        errors.append("forget request requires explicit confirmation (confirmed=True)")

    append_only_items = [
        i for i in items
        if is_append_only(assign_retention_class(i))
    ]
    if append_only_items:
        errors.append(
            f"{len(append_only_items)} item(s) are append-only (audit_log) and cannot be deleted"
        )

    non_deletable = [
        i for i in items
        if not can_delete(assign_retention_class(i)) and not is_append_only(assign_retention_class(i))
    ]
    if non_deletable:
        errors.append(f"{len(non_deletable)} item(s) are not deletable by policy")

    return len(errors) == 0, errors


def make_tombstone(item: dict, reason: str = "user_forget_request") -> dict:
    """
    Create a non-sensitive tombstone to prevent accidental re-import.
    Contains no raw content — only metadata.
    """
    return {
        "tombstone": True,
        "original_data_class": item.get("data_class", "unknown"),
        "original_retention_class": assign_retention_class(item),
        "deleted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason": reason,
        "source": item.get("source", "unknown"),
        # A slug for matching future re-imports; no raw text
        "dedupe_hint": item.get("dedupe_key", item.get("event_id", "")),
    }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def _smoke() -> bool:
    errors: list[str] = []

    # 1. All seven retention classes are defined
    expected = {
        "ephemeral_raw", "source_cache", "derived_intelligence",
        "durable_memory", "audit_log", "user_exportable", "forgettable",
    }
    missing = expected - ALL_CLASS_NAMES
    if missing:
        errors.append(f"Missing retention classes: {missing}")

    # 2. Window lookups
    if RETENTION_CLASSES["ephemeral_raw"].window_hours != 24:
        errors.append("ephemeral_raw window != 24h")
    if RETENTION_CLASSES["source_cache"].window_hours != 72:
        errors.append("source_cache window != 72h")
    if RETENTION_CLASSES["derived_intelligence"].window_hours != 90 * 24:
        errors.append("derived_intelligence window != 90 days")
    if RETENTION_CLASSES["durable_memory"].window_hours is not None:
        errors.append("durable_memory should be indefinite")
    if RETENTION_CLASSES["audit_log"].window_hours != 365 * 24:
        errors.append("audit_log window != 365 days")

    # 3. Expiry detection — a very old item should be expired
    old_ts = "2020-01-01T00:00:00+00:00"
    if not is_expired("ephemeral_raw", old_ts):
        errors.append("old ephemeral_raw item not detected as expired")
    if not is_expired("source_cache", old_ts):
        errors.append("old source_cache item not detected as expired")
    if is_expired("durable_memory", old_ts):
        errors.append("durable_memory should never expire")

    # 4. Future item should not be expired
    future_ts = "2099-01-01T00:00:00+00:00"
    if is_expired("ephemeral_raw", future_ts):
        errors.append("future ephemeral_raw item incorrectly flagged as expired")

    # 5. Deletability
    if not can_delete("ephemeral_raw"):
        errors.append("ephemeral_raw should be deletable")
    if can_delete("audit_log"):
        errors.append("audit_log should NOT be deletable")

    # 6. Append-only
    if not is_append_only("audit_log"):
        errors.append("audit_log should be append-only")
    if is_append_only("ephemeral_raw"):
        errors.append("ephemeral_raw should NOT be append-only")

    # 7. forget flow requirement
    if not requires_forget_flow("forgettable"):
        errors.append("forgettable must require forget flow")
    if not requires_forget_flow("durable_memory"):
        errors.append("durable_memory must require forget flow")
    if requires_forget_flow("ephemeral_raw"):
        errors.append("ephemeral_raw should not require forget flow")

    # 8. default retention for data classes
    if default_retention_for_data_class("raw_source") != "ephemeral_raw":
        errors.append("raw_source should default to ephemeral_raw")
    if default_retention_for_data_class("memory") != "durable_memory":
        errors.append("memory should default to durable_memory")
    if default_retention_for_data_class("audit") != "audit_log":
        errors.append("audit should default to audit_log")

    # 9. assign_retention_class respects explicit field
    item_explicit = {"retention_class": "forgettable", "data_class": "raw_source"}
    if assign_retention_class(item_explicit) != "forgettable":
        errors.append("assign_retention_class should respect explicit field")

    # 10. assign_retention_class falls back to data_class default
    item_fallback = {"data_class": "intelligence"}
    if assign_retention_class(item_fallback) != "derived_intelligence":
        errors.append("assign_retention_class should fall back to data_class default")

    # 11. build_forget_preview returns safe summaries
    preview_items = [
        {"item_summary": "Olivia Nielsen / PerfectHire — opportunity signal", "data_class": "memory"},
        {"item_summary": "Email snippet from Alice", "data_class": "normalized"},
    ]
    preview = build_forget_preview(preview_items)
    if len(preview) != 2:
        errors.append("build_forget_preview should return 2 items")
    for p in preview:
        if "item_summary" not in p:
            errors.append("preview missing item_summary")

    # 12. validate_forget_request blocks unconfirmed requests
    req = ForgetRequest(scope="contact", scope_value="Olivia Nielsen", confirmed=False)
    valid, errs = validate_forget_request(req, preview_items)
    if valid:
        errors.append("unconfirmed forget request should be invalid")

    # 13. validate_forget_request blocks audit_log deletion
    audit_item = {"data_class": "audit", "item_summary": "access event"}
    req2 = ForgetRequest(scope="all", scope_value="*", confirmed=True)
    valid2, errs2 = validate_forget_request(req2, [audit_item])
    if valid2:
        errors.append("forget request for audit_log item should be blocked")

    # 14. tombstone has no raw content
    tombstone = make_tombstone({"data_class": "memory", "item_summary": "contact fact", "source": "ri_events"})
    if "item_summary" in tombstone:
        errors.append("tombstone should not contain item_summary (potential raw content)")
    if not tombstone.get("tombstone"):
        errors.append("tombstone should have tombstone=True")

    if errors:
        for e in errors:
            print(f"  FAIL: {e}")
        return False

    print("retention_policy smoke: all checks passed")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="RB retention policy CLI")
    parser.add_argument("--smoke", action="store_true")
    sub = parser.add_subparsers(dest="cmd")

    check_p = sub.add_parser("check", help="Check if an item is expired")
    check_p.add_argument("retention_class", help="Retention class name")
    check_p.add_argument("stored_at", help="ISO timestamp when item was stored")

    sub.add_parser("classes", help="List all retention classes")

    args = parser.parse_args()

    if args.smoke:
        ok = _smoke()
        sys.exit(0 if ok else 1)

    if args.cmd == "check":
        try:
            rc = get_class(args.retention_class)
        except KeyError as e:
            print(str(e))
            sys.exit(1)
        expired = is_expired(args.retention_class, args.stored_at)
        exp_at = expiry_at(args.retention_class, args.stored_at)
        result = {
            "retention_class": rc.name,
            "stored_at": args.stored_at,
            "window_hours": rc.window_hours,
            "expired": expired,
            "expiry_at": exp_at.isoformat() if exp_at else None,
            "deletable": rc.deletable,
            "append_only": rc.append_only,
        }
        print(json.dumps(result, indent=2))

    elif args.cmd == "classes":
        classes = []
        for rc in RETENTION_CLASSES.values():
            classes.append({
                "name": rc.name,
                "label": rc.label,
                "window_hours": rc.window_hours,
                "window_label": (
                    "indefinite" if rc.window_hours is None
                    else f"{rc.window_hours}h ({rc.window_hours // 24}d)"
                ),
                "deletable": rc.deletable,
                "append_only": rc.append_only,
                "requires_forget_flow": rc.requires_forget_flow,
                "description": rc.description,
            })
        print(json.dumps(classes, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
