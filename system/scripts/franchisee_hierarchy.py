#!/usr/bin/env python3
"""Searchable multi-brand franchisee hierarchy for macro intelligence."""
from __future__ import annotations

import json
import re
from pathlib import Path


STORE_PATH = Path(__file__).resolve().parent.parent / "franchisee_hierarchy.json"


def load(path: Path | None = None) -> dict:
    target = path or STORE_PATH
    if not target.exists():
        return {"contract": "rb_franchisee_hierarchy_v1", "operators": []}
    return json.loads(target.read_text(encoding="utf-8"))


def query(
    text: str | None = None,
    *,
    min_total_units: int | None = None,
    multi_brand_only: bool = False,
    path: Path | None = None,
) -> list[dict]:
    """Search operator names, locations, brands, and hierarchy-level terms."""
    records = list(load(path).get("operators", []))
    if min_total_units is not None:
        records = [r for r in records if int(r.get("total_units", 0)) >= min_total_units]
    if multi_brand_only:
        records = [r for r in records if int(r.get("brand_count", 0)) > 1]
    if text:
        terms = re.findall(r"[a-z0-9]+", text.lower())
        records = [
            r for r in records
            if all(term in json.dumps(r, ensure_ascii=False).lower() for term in terms)
        ]
    return sorted(records, key=lambda r: (r.get("rank", 999), -r.get("total_units", 0)))
