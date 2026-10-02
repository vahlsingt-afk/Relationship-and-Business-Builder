#!/usr/bin/env python3
"""Perform deterministic citation integrity checks without paid or live access."""
from __future__ import annotations

from urllib.parse import urlparse


def verify(packet: dict) -> dict:
    sources = {row.get("source_id"): row for row in packet.get("source_ledger") or []}
    errors, warnings = [], []
    for source_id, source in sources.items():
        parsed = urlparse(source.get("url") or "")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append({"code": "citation_url_invalid", "source_id": source_id})
        if source.get("productive") and source.get("access_status") not in {"accessible", "partial"}:
            errors.append({"code": "productive_source_inaccessible", "source_id": source_id})
        if source.get("productive") and not source.get("published_at"):
            warnings.append({"code": "citation_date_missing", "source_id": source_id})
        if source.get("productive") and not source.get("canonical_url"):
            warnings.append({"code": "canonical_url_missing", "source_id": source_id})
    for finding in packet.get("findings") or []:
        for source_id in finding.get("source_ids") or []:
            if source_id not in sources:
                errors.append({"code": "citation_reference_missing", "finding_id": finding.get("finding_id"), "source_id": source_id})
    return {
        "schema": "rb.hunter_citation_verification.v1",
        "mode": "offline_integrity",
        "live_content_checked": False,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
    }
