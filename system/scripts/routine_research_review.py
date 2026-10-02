#!/usr/bin/env python3
"""Review and canonically persist approved routine-research evidence."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import account_background_brief as abb
import customers_prospects_common as cpc
import rb_core as core

REVIEW_PATH = core.CACHE_DIR / "routine_research_field_reviews.json"
MUTATION_LEDGER_PATH = core.CACHE_DIR / "intelligence_mutation_ledger.jsonl"
VALID_DECISIONS = {"approve_evidence", "reject"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> dict:
    try:
        data = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"pending_reviews": []}
    except Exception:
        return {"pending_reviews": []}


def _save(data: dict) -> None:
    REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _ledger(row: dict) -> None:
    MUTATION_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MUTATION_LEDGER_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def list_reviews(*, status: str = "pending_review") -> list[dict]:
    return [row for row in _load().get("pending_reviews", []) if not status or row.get("status") == status]


def resolve(evidence_id: str, *, decision: str, resolution: str) -> dict:
    if decision not in VALID_DECISIONS:
        raise ValueError(f"decision must be one of {sorted(VALID_DECISIONS)}")
    if not resolution.strip():
        raise ValueError("resolution is required")
    store = _load()
    item = next((row for row in store.get("pending_reviews", [])
                 if row.get("evidence_id") == evidence_id and row.get("status") == "pending_review"), None)
    if not item:
        raise KeyError(evidence_id)

    canonical_target = None
    mutation_status = "rejected"
    if decision == "approve_evidence":
        slug, exists = abb.resolve_account(item["entity_name"])
        if not exists:
            slug = abb.create_new_account(item["entity_name"])
        evidence_path = cpc.account_dir(slug) / "evidence.jsonl"
        existing = cpc.load_jsonl(evidence_path) if evidence_path.exists() else []
        record = {
            "evidence_id": cpc.next_evidence_id(slug, existing),
            "account_id": f"acct-{slug}",
            "opportunity_ids": [],
            "source_type": "routine_research",
            "durable_source_id": item.get("source_url"),
            "source_author": item.get("source_title"),
            "participants": [],
            "event_date": item.get("source_published_date"),
            "ingestion_date": cpc.today(),
            "excerpt": str(item.get("evidence_text") or "")[:1000],
            "extracted_claims": [item.get("evidence_text")],
            "evidence_class": item.get("dimension"),
            "confidence": item.get("confidence_pct"),
            "scope": f"account:acct-{slug}",
            "limitations": item.get("limitations") or "",
            "contradiction_links": [],
            "processing_version": "routine-research-review-v1",
            "_source_title": item.get("source_title"),
        }
        cpc.append_jsonl(evidence_path, record)
        cpc.flag_needs_refresh(slug, f"Approved routine-research {item.get('dimension')} evidence {evidence_id}.")
        canonical_target = str(evidence_path.relative_to(core.PROJECT_DIR))
        mutation_status = "canonical_evidence_appended"

    item.update({"status": "approved" if decision == "approve_evidence" else "rejected",
                 "decision": decision, "resolution": resolution, "resolved_at": _now(),
                 "canonical_target": canonical_target})
    _save(store)
    ledger_row = {
        "mutation_id": f"mut-{evidence_id}", "timestamp": _now(),
        "source_cycle": "routine_research", "evidence_id": evidence_id,
        "entity_name": item.get("entity_name"), "dimension": item.get("dimension"),
        "decision": decision, "status": mutation_status,
        "canonical_target": canonical_target, "resolution": resolution,
    }
    _ledger(ledger_row)
    return {"ok": True, **ledger_row}
