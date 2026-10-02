"""
add_evidence.py — RB-2026-08-28.

The structured, reviewed counterpart to account_reference_detector.py's
automatic reference-linking: a clean, reusable one-call action for adding a
REAL evidence record (a specific commercial term, a specific claim, a
specific participant) to a Blue Sheet's evidence.jsonl.

Same discipline as competitor_intelligence.add_competitive_note(): every
field here must be something the user actually said or something already
present in a real source the model was given -- never invented, never
inferred, never a model-generated summary presented as fact. This is what
turns "I could not find any queryable path for this transcript's real
commercial terms" into a one-call action instead of a manual JSON edit
(which is how ev-pollo-campero-0017 was recovered the first time this gap
was found, 2026-08-28).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as bs_common  # noqa: E402


def add_evidence(
    slug: str,
    *,
    excerpt: str,
    source_type: str,
    extracted_claims: list[str] | None = None,
    event_date: str | None = None,
    participants: list[str] | None = None,
    opportunity_ids: list[str] | None = None,
    source_author: str | None = None,
    confidence: str = "medium",
    scope: str | None = None,
    evidence_class: str = "user_reported",
    limitations: str = "",
    durable_source_id: str | None = None,
) -> dict:
    if not (excerpt or "").strip():
        raise ValueError("excerpt must not be empty")
    if not (source_type or "").strip():
        raise ValueError("source_type must not be empty")

    acct_dir = bs_common.account_dir(slug)  # raises FileNotFoundError if slug unknown
    evidence_path = acct_dir / "evidence.jsonl"
    existing = bs_common.load_jsonl(evidence_path) if evidence_path.exists() else []
    evidence_id = bs_common.next_evidence_id(slug, existing)
    today = bs_common.today()

    record = {
        "evidence_id": evidence_id,
        "account_id": f"acct-{slug}",
        "opportunity_ids": opportunity_ids or [],
        "source_type": source_type,
        "durable_source_id": durable_source_id,
        "source_author": source_author,
        "participants": participants or [],
        "event_date": event_date or today,
        "ingestion_date": today,
        "excerpt": excerpt.strip(),
        "extracted_claims": extracted_claims or [],
        "evidence_class": evidence_class,
        "confidence": confidence,
        "scope": scope or f"account:acct-{slug}",
        "limitations": limitations,
        "contradiction_links": [],
        "processing_version": "add_evidence-v1",
    }
    bs_common.append_jsonl(evidence_path, record)

    reg = bs_common.load_registry()
    for entry in reg.get("registry", []):
        if entry.get("account_id") == f"acct-{slug}":
            entry["last_evidence_date"] = record["event_date"]
            break
    bs_common.save_json(bs_common.registry_path(), reg)

    # RB-2026-09-02: real bug found live -- this function used to only ever
    # touch evidence.jsonl, never the account's actual .xlsx workbook, so
    # every "[BLUE SHEET EVIDENCE SAVED]" confirmation left the downloadable
    # file silently stale (or, for one account, registered against a path
    # that never existed at all -- see blue_sheet_registry.json fix,
    # RB-2026-09-02). Import deferred and wrapped so a render.py/openpyxl
    # failure in some environment can never take add_evidence.py's own
    # importability down with it (same isolation precedent as server.py's
    # separate try/except blocks for these two modules).
    workbook_regenerated = False
    workbook_regeneration_error = None
    try:
        import render as bs_render  # noqa: E402
        bs_render.render(slug)
        workbook_regenerated = True
    except Exception as exc:  # noqa: BLE001
        workbook_regeneration_error = f"{type(exc).__name__}: {exc}"

    result = {
        "ok": True,
        "account_slug": slug,
        "evidence_id": evidence_id,
        "workbook_regenerated": workbook_regenerated,
    }
    if workbook_regeneration_error:
        result["workbook_regeneration_error"] = workbook_regeneration_error
    return result
