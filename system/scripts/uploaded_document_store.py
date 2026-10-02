#!/usr/bin/env python3
"""
uploaded_document_store.py — persists full extracted text (and, where
mechanically derivable, section/page structure) for a document uploaded
through uploadAndIngestFile, keyed by a stable document_id, so it can be
retrieved in a later call instead of only living in memory for the one
request that ingested it.

RB-2026-08-31 (Todd's defect report, Pollo Campero RFP): uploadAndIngestFile's
"intelligence" pipeline extracted a DOCX/PDF's full text mechanically
(_extract_text_from_docx / _extract_text_from_pdf in server.py), used it
in-memory for triage/mutation_engine/account_reference_detector, then
discarded it -- only a 400-char excerpt ever survived, baked into a Blue
Sheet's auto-linked reference evidence. There was no way to ask "what does
this document actually say" after the one call that ingested it, so the GPT
could not do the requested section-by-section RFP review. This module is the
durable side of that fix: save_document() persists the real extraction once;
load_document() returns it by id. Content stays entirely mechanical --
whatever the extractor actually produced -- never re-summarized or
re-interpreted here.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402

STORE_DIR = core.SYSTEM_DIR / "uploaded_documents"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def document_path(document_id: str) -> Path:
    return STORE_DIR / f"{document_id}.json"


def save_document(
    document_id: str, *, filename: str, content_type: str, extracted_text: str,
    account_links: Optional[list[str]] = None, extraction_status: str = "ok",
    sections: Optional[list[dict]] = None, warnings: Optional[list[str]] = None,
) -> dict:
    """Persist one uploaded document's full extraction. Idempotent per
    document_id (the caller derives it from a content hash) -- re-ingesting
    identical bytes overwrites with the same content, not a duplicate."""
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "document_id": document_id,
        "filename": filename,
        "content_type": content_type,
        "extraction_status": extraction_status,
        "extracted_text": extracted_text or "",
        "char_count": len(extracted_text or ""),
        "sections": sections or [],
        "account_links": account_links or [],
        "warnings": warnings or [],
        "created_at": _now_iso(),
    }
    document_path(document_id).write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    return record


def load_document(document_id: str) -> Optional[dict]:
    path = document_path(document_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
