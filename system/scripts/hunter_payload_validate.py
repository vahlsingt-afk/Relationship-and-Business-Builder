#!/usr/bin/env python3
"""Validate playbook-specific Hunter payload envelopes."""
from __future__ import annotations

import json
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parent.parent
REGISTRY_PATH = SYSTEM_DIR / "research" / "hunter_payload_registry.json"


def validate(payload_schema: str, payload: object) -> list[dict]:
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    spec = (registry.get("payloads") or {}).get(payload_schema)
    if not spec:
        return [{"code": "unknown_payload_schema", "path": "payload_schema", "message": payload_schema}]
    if not isinstance(payload, dict):
        return [{"code": "payload_not_object", "path": "payload", "message": "payload must be an object"}]
    collection = spec["collection"]
    rows = payload.get(collection)
    # Empty legacy envelopes remain intake-compatible while older research
    # producers migrate; populated payloads must use the registered shape.
    if rows is None and payload == {"findings": []}:
        return []
    if not isinstance(rows, list):
        return [{"code": "payload_collection_missing", "path": f"payload/{collection}", "message": "required array is missing"}]
    errors = []
    required = spec.get("required_item_fields") or []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append({"code": "payload_item_not_object", "path": f"payload/{collection}/{index}", "message": "item must be an object"})
            continue
        missing = [field for field in required if field not in row]
        if missing:
            errors.append({"code": "payload_item_fields_missing", "path": f"payload/{collection}/{index}", "message": f"missing fields: {missing}"})
    return errors
