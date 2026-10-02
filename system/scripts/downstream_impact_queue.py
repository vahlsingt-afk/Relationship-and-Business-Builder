#!/usr/bin/env python3
"""Accountable queue from intelligence ramifications to downstream artifacts."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import account_background_brief as abb
import rb_core as core

QUEUE_PATH = core.CACHE_DIR / "downstream_impact_queue.json"
MUTATION_LEDGER_PATH = core.CACHE_DIR / "intelligence_mutation_ledger.jsonl"
EXECUTION_LEDGER_PATH = core.CACHE_DIR / "downstream_execution_ledger.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(raw)
            if isinstance(row, dict):
                rows.append(row)
        except ValueError:
            continue
    return rows


def _save(store: dict) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")


def _append_execution(row: dict) -> None:
    EXECUTION_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EXECUTION_LEDGER_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def _id(*parts: str) -> str:
    return "impact-" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _slug_from_target(target: str | None) -> str | None:
    match = re.search(r"(?:account_research|customers_prospects)/accounts/([^/]+)/evidence\.jsonl$", target or "")
    return match.group(1) if match else None


def enqueue(ramifications: dict, *, today: date) -> dict:
    store = _load_json(QUEUE_PATH, {"items": []})
    items = store.setdefault("items", [])
    known = {item.get("impact_id") for item in items}
    added_review = added_safe = 0

    for ramification in ramifications.get("ramifications") or []:
        for impact in ramification.get("affected_artifacts") or []:
            artifact = impact.get("artifact") or "unknown"
            if artifact == "daily_intelligence_report":
                continue  # already handled by rendering, not downstream work
            impact_id = _id(str(ramification.get("ramification_id")), artifact, str(impact.get("action")))
            if impact_id in known:
                continue
            items.append({
                "impact_id": impact_id, "source_type": "ramification",
                "source_id": ramification.get("ramification_id"),
                "entity_name": ramification.get("entity_name"),
                "target_artifact": artifact, "proposed_action": impact.get("action"),
                "evidence_summary": ramification.get("evidence_summary"),
                "source_refs": ramification.get("source_refs") or [],
                "review_required": True, "status": "pending_review",
                "queued_at": _now(), "resolved_at": None, "execution_receipt": None,
            })
            known.add(impact_id)
            added_review += 1

    # Approved canonical evidence can safely regenerate its own Background
    # Brief: the renderer reads persisted evidence and never invents fields.
    for mutation in _jsonl(MUTATION_LEDGER_PATH):
        if mutation.get("status") != "canonical_evidence_appended":
            continue
        slug = _slug_from_target(mutation.get("canonical_target"))
        if not slug:
            continue
        impact_id = _id(str(mutation.get("mutation_id")), "account_background_brief", "regenerate")
        if impact_id in known:
            continue
        items.append({
            "impact_id": impact_id, "source_type": "approved_mutation",
            "source_id": mutation.get("mutation_id"), "entity_name": mutation.get("entity_name"),
            "target_artifact": "account_background_brief", "proposed_action": "regenerate_from_canonical_evidence",
            "slug": slug, "review_required": False, "status": "queued",
            "queued_at": _now(), "resolved_at": None, "retry_count": 0,
            "execution_receipt": None,
        })
        known.add(impact_id)
        added_safe += 1
    store["updated_at"] = _now()
    _save(store)
    return {"review_items_added": added_review, "safe_items_added": added_safe, "total_items": len(items)}


def execute_safe() -> dict:
    store = _load_json(QUEUE_PATH, {"items": []})
    applied = failed = 0
    for item in store.get("items", []):
        if item.get("review_required") or item.get("status") not in {"queued", "failed"}:
            continue
        if int(item.get("retry_count") or 0) >= 3:
            continue
        item["status"] = "running"
        item["started_at"] = _now()
        try:
            if item.get("proposed_action") == "regenerate_from_canonical_evidence" and item.get("slug"):
                result = abb.generate_brief(
                    item["slug"], generated_for="",
                    purpose="Automated downstream refresh after approved routine-research evidence.")
                receipt = {"brief_version": result.get("version") or result.get("brief_id"),
                           "artifact": result.get("path") or result.get("brief_path")}
            else:
                raise ValueError("no safe executor registered for this action")
            item.update({"status": "applied", "resolved_at": _now(), "execution_receipt": receipt})
            applied += 1
        except Exception as exc:  # noqa: BLE001
            item.update({"status": "failed", "last_error": str(exc)[:500],
                         "retry_count": int(item.get("retry_count") or 0) + 1})
            failed += 1
        _append_execution({"impact_id": item.get("impact_id"), "timestamp": _now(),
                           "status": item.get("status"), "target_artifact": item.get("target_artifact"),
                           "execution_receipt": item.get("execution_receipt"),
                           "error": item.get("last_error")})
    store["updated_at"] = _now()
    _save(store)
    return {"safe_applied": applied, "safe_failed": failed}


def run(ramifications: dict, *, today: date) -> dict:
    queued = enqueue(ramifications, today=today)
    executed = execute_safe()
    store = _load_json(QUEUE_PATH, {"items": []})
    statuses: dict[str, int] = {}
    for item in store.get("items", []):
        status = item.get("status") or "unknown"
        statuses[status] = statuses.get(status, 0) + 1
    return {"date": today.isoformat(), **queued, **executed, "status_counts": statuses,
            "items": store.get("items", [])}


def list_items(*, status: str | None = "pending_review") -> list[dict]:
    return [item for item in _load_json(QUEUE_PATH, {"items": []}).get("items", [])
            if not status or item.get("status") == status]


def resolve(impact_id: str, *, decision: str, resolution: str) -> dict:
    if decision not in {"approve_manual_action", "reject"} or not resolution.strip():
        raise ValueError("decision and resolution are required")
    store = _load_json(QUEUE_PATH, {"items": []})
    item = next((row for row in store.get("items", [])
                 if row.get("impact_id") == impact_id and row.get("status") == "pending_review"), None)
    if not item:
        raise KeyError(impact_id)
    item.update({"status": "approved_manual_action" if decision == "approve_manual_action" else "rejected",
                 "decision": decision, "resolution": resolution, "resolved_at": _now()})
    _save(store)
    _append_execution({"impact_id": impact_id, "timestamp": _now(), "status": item["status"],
                       "target_artifact": item.get("target_artifact"), "resolution": resolution})
    return {"ok": True, "impact_id": impact_id, "status": item["status"],
            "target_artifact": item.get("target_artifact"),
            "note": "Approval records Todd's decision; the judgment-heavy artifact edit remains a separate explicit operation."}
