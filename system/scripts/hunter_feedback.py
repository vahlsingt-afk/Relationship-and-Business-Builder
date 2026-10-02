#!/usr/bin/env python3
"""Attribute Hunter review outcomes to sources and research methods."""
from __future__ import annotations

from collections import defaultdict


def attribute(packet: dict, decisions: list[dict]) -> dict:
    findings = {row.get("finding_id"): row for row in packet.get("findings") or []}
    sources = {row.get("source_id"): row for row in packet.get("source_ledger") or []}
    stats = defaultdict(lambda: {"accepted": 0, "rejected": 0, "corrected": 0})
    for decision in decisions:
        finding = findings.get(decision.get("finding_id"))
        outcome = decision.get("outcome")
        if not finding or outcome not in {"accepted", "rejected", "corrected"}:
            continue
        for source_id in finding.get("source_ids") or []:
            source = sources.get(source_id) or {}
            key = source.get("publisher") or source_id
            stats[key][outcome] += 1
    ranked = []
    for source, counts in stats.items():
        total = sum(counts.values())
        ranked.append({"source": source, **counts, "acceptance_rate": round(counts["accepted"] / total, 3)})
    ranked.sort(key=lambda row: (-row["acceptance_rate"], -row["accepted"], row["source"]))
    return {"schema": "rb.hunter_feedback_attribution.v1", "packet_id": packet.get("packet_id"), "sources": ranked}
