#!/usr/bin/env python3
"""Build an evidence-linked CoS synthesis context from Hunter handoffs."""
from __future__ import annotations

from collections import defaultdict


def synthesize(records: list[dict]) -> dict:
    grouped = defaultdict(list)
    for record in records:
        handoff = record.get("handoff") or record
        for target in handoff.get("connected_target_keys") or ["industry"]:
            grouped[target].append({
                "handoff_id": handoff.get("handoff_id"),
                "headline": handoff.get("headline"),
                "connection": handoff.get("connection"),
                "why_it_matters": handoff.get("why_it_matters"),
                "finding_ids": handoff.get("finding_ids") or [],
                "change_event_ids": handoff.get("change_event_ids") or [],
                "confidence_pct": handoff.get("confidence_pct"),
                "is_inference": handoff.get("is_inference"),
            })
    return {
        "schema": "rb.hunter_cos_synthesis_context.v1",
        "instruction": "Connect evidence without upgrading inference to fact; cite finding and change IDs.",
        "target_threads": dict(sorted(grouped.items())),
        "handoff_count": sum(len(rows) for rows in grouped.values()),
    }
