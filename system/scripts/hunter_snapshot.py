#!/usr/bin/env python3
"""Capture and compare Hunter's supplied pre-research state."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def from_directive(directive: dict) -> dict:
    targets = {}
    for target in (directive.get("gap_manifest") or {}).get("targets") or []:
        targets[target["target_key"]] = {
            "display_name": target.get("display_name"),
            "current_state": target.get("current_state") or {},
            "gap_ids": [gap.get("gap_id") for gap in target.get("gaps") or []],
        }
    captured_at = directive.get("prepared_at") or datetime.now(timezone.utc).isoformat()
    return {
        "schema": "rb.hunter_before_snapshot.v1",
        "captured_at": captured_at,
        "prior_state_as_of": (directive.get("packet_requirements") or {}).get("prior_state_as_of"),
        "targets": targets,
        "state_hash": _digest(targets),
    }


def compare(snapshot: dict, packet: dict) -> dict:
    before = snapshot.get("targets") or {}
    errors = []
    reviewed = []
    for index, event in enumerate(packet.get("change_events") or []):
        target = before.get(event.get("target_key"))
        if target is None:
            errors.append({"code": "change_target_missing_from_snapshot", "path": f"change_events/{index}"})
            continue
        if event.get("prior_state") in (None, {}, "unknown"):
            errors.append({"code": "change_prior_state_empty", "path": f"change_events/{index}"})
        if event.get("prior_state") == event.get("new_state"):
            errors.append({"code": "change_has_no_delta", "path": f"change_events/{index}"})
        reviewed.append(event.get("change_event_id"))
    return {
        "schema": "rb.hunter_change_comparison.v1",
        "snapshot_hash": snapshot.get("state_hash"),
        "reviewed_change_event_ids": reviewed,
        "valid": not errors,
        "errors": errors,
    }
