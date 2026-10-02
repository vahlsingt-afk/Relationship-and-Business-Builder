#!/usr/bin/env python3
"""
account_reference_detector.py — mechanical name/alias detection of which
Blue Sheet accounts and tracked competitors a piece of text references.

RB-2026-08-28. Closes the gap found live the same day: a real internal
pricing call for Pollo Campero (an already-ACTIVE Blue Sheet, RFP pricing
due 2026-09-04) went through uploadAndIngestFile's generic "intelligence"
pipeline and produced nothing but two generic "vendor name mentioned"
watchlist blips -- none of the real commercial terms discussed ever
connected to the Blue Sheet that's the actual queryable home for exactly
that content.

Deliberately does NOT do free-text fact extraction -- that's the same
class of fabrication risk already confirmed twice this session
(intelligence_mutation_engine.py's brand-name/pronoun-resolution bugs: "They
confirmed as Qu customer" instead of "Church's Chicken"; "Core features\\n
Curate" from a mangled markdown fragment). This module only answers "does
this text mention a KNOWN account/competitor by name or alias" -- the same
mechanical, no-invention discipline as
account_background_brief.find_account_intelligence_docs() and
competitor_intelligence.sync_from_ecosystem()'s doc-reference matching.

What it enables: uploadAndIngestFile's intelligence pipeline calls this
after triage/mutation_engine run (unchanged), and for each match:
  1. Appends a lightweight, fact-free REFERENCE evidence entry (this
     document exists, mentions this account/competitor, dated X) -- makes
     the source discoverable, never invents a specific commercial claim.
  2. Surfaces the match in ingest_result so the CoS can proactively tell
     Todd "this looks relevant to the Pollo Campero Blue Sheet -- want me
     to pull the real details in?" instead of staying silent.
Extracting the REAL structured facts (pricing terms, buying influences,
...) stays a separate, reviewed action -- addBlueSheetEvidence /
addCompetitiveNote -- same as the manual process used to recover the real
Pollo Campero pricing content the same day this was built.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402

CUSTOMERS_PROSPECTS_ROOT = core.PROJECT_DIR / "customers_prospects"
COMPETITOR_INTEL_ROOT = core.SYSTEM_DIR / "competitor_intelligence"


@dataclass
class ReferenceMatch:
    artifact_type: str  # "blue_sheet" | "competitor"
    slug: str
    display_name: str
    matched_on: str  # which name/alias actually matched


def _name_in_text(name: str, text_lower: str) -> bool:
    """Real word-boundary match -- cheap, mechanical, no NLP. Requires at
    least 3 chars to avoid noise matches on short aliases.

    RB-2026-09-08: this used to be a bare substring check (`n in
    text_lower`), which the docstring claimed was "whole-word-ish" but
    wasn't -- confirmed live the same class of bug already fixed once in
    intelligence_mutation_engine.py (2026-08-25, olo/ncr/qu substring
    collisions): the short alias "PAR" (a real PAR Technology alias)
    matched inside the word "Part" in an uploaded business calendar's
    "Pollo Campero Pricing Part 2" line, misfiling that calendar as
    evidence on PAR Technology's competitor record. Same fix pattern as
    that prior fix: \\b-anchored regex instead of substring containment."""
    n = (name or "").strip().lower()
    if len(n) < 3:
        return False
    return re.search(r"\b" + re.escape(n) + r"\b", text_lower) is not None


def _load_blue_sheet_candidates() -> list[dict]:
    """RB-2026-09-06: reads the unified customers_prospects_registry.json
    (post-3-store-unification) instead of the old blue_sheet_registry.json.
    The `status` filter is unchanged and deliberately still checked (not
    replaced by engagement_tier) -- it distinguishes accounts with a real,
    active workbook from active_engagement shells with no real content yet
    (e.g. five-guys/del-taco are engagement_tier=active_engagement but
    status=missing_blue_sheet -- no real evidence.jsonl content to treat as
    a genuine Blue Sheet match target)."""
    import json
    reg_path = CUSTOMERS_PROSPECTS_ROOT / "_portfolio" / "customers_prospects_registry.json"
    if not reg_path.exists():
        return []
    try:
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for entry in reg.get("registry", []):
        if entry.get("status") not in ("active", "current"):
            continue  # no real evidence.jsonl to append to otherwise
        account_id = entry.get("account_id", "")
        slug = account_id.replace("acct-", "", 1)
        display_name = slug.replace("-", " ").title()
        account_json = CUSTOMERS_PROSPECTS_ROOT / "accounts" / slug / "account.json"
        if account_json.exists():
            try:
                acct = json.loads(account_json.read_text(encoding="utf-8"))
                display_name = acct.get("display_name") or display_name
            except (OSError, json.JSONDecodeError):
                pass
        names = [display_name] + (entry.get("aliases") or [])
        out.append({"slug": slug, "display_name": display_name, "names": names})
    return out


def _load_competitor_candidates() -> list[dict]:
    import json
    reg_path = COMPETITOR_INTEL_ROOT / "_portfolio" / "competitor_registry.json"
    if not reg_path.exists():
        return []
    try:
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for entry in reg.get("registry", []):
        slug = entry.get("competitor_slug", "")
        display_name = entry.get("display_name") or slug
        comp_json = COMPETITOR_INTEL_ROOT / "competitors" / slug / "competitor.json"
        names = [display_name]
        if comp_json.exists():
            try:
                comp = json.loads(comp_json.read_text(encoding="utf-8"))
                names += comp.get("aliases") or []
            except (OSError, json.JSONDecodeError):
                pass
        out.append({"slug": slug, "display_name": display_name, "names": names})
    return out


def detect_references(text: str) -> list[ReferenceMatch]:
    """Mechanically find which known Blue Sheet accounts and tracked
    competitors this text mentions by name/alias. Never invents a match;
    an unlisted account/competitor simply isn't detected -- same
    opt-out-only philosophy as personal_relationship_guard."""
    text_lower = (text or "").lower()
    if not text_lower.strip():
        return []

    matches: list[ReferenceMatch] = []
    seen: set[tuple[str, str]] = set()

    for cand in _load_blue_sheet_candidates():
        for name in cand["names"]:
            if _name_in_text(name, text_lower):
                key = ("blue_sheet", cand["slug"])
                if key not in seen:
                    seen.add(key)
                    matches.append(ReferenceMatch("blue_sheet", cand["slug"], cand["display_name"], name))
                break

    for cand in _load_competitor_candidates():
        for name in cand["names"]:
            if _name_in_text(name, text_lower):
                key = ("competitor", cand["slug"])
                if key not in seen:
                    seen.add(key)
                    matches.append(ReferenceMatch("competitor", cand["slug"], cand["display_name"], name))
                break

    return matches


def link_references(
    matches: list[ReferenceMatch], *, source_title: str, source_date: str, excerpt: str = "",
    document_id: str | None = None,
) -> dict:
    """Append a lightweight, fact-free reference evidence entry to each
    matched Blue Sheet/competitor -- makes the source discoverable without
    inventing any specific claim. Idempotent per (slug, source_title).

    RB-2026-08-31: document_id, when the caller has one (uploadAndIngestFile
    always does -- it's the same ingestion_id used to key
    uploaded_document_store), becomes durable_source_id here instead of the
    previous hardcoded None -- a real, callable retrieval pointer
    (getUploadedDocument) rather than only a 400-char excerpt with no way
    back to the full source text."""
    import customers_prospects_common as cpc  # noqa: E402
    import competitor_intelligence_common as cic  # noqa: E402

    linked: list[dict] = []
    for m in matches:
        if m.artifact_type == "blue_sheet":
            acct_dir = cpc.account_dir(m.slug)
            evidence_path = acct_dir / "evidence.jsonl"
            existing = cpc.load_jsonl(evidence_path) if evidence_path.exists() else []
            already = any(
                e.get("source_type") == "uploaded_content_reference" and e.get("_source_title") == source_title
                for e in existing
            )
            if already:
                continue
            evidence_id = cpc.next_evidence_id(m.slug, existing)
            record = {
                "evidence_id": evidence_id,
                "account_id": f"acct-{m.slug}",
                "opportunity_ids": [],
                "source_type": "uploaded_content_reference",
                "durable_source_id": document_id,
                "source_author": None,
                "participants": [],
                "event_date": source_date,
                "ingestion_date": source_date,
                "excerpt": excerpt[:400] or f"Uploaded content mentioning {m.display_name} (matched on '{m.matched_on}').",
                "extracted_claims": [],
                "evidence_class": "auto_linked_reference",
                "confidence": None,
                "scope": f"account:acct-{m.slug}",
                "limitations": (
                    "Auto-linked by name/alias match only -- no specific fact extracted or claimed. "
                    "Review the source and use addBlueSheetEvidence to record real terms."
                ),
                "contradiction_links": [],
                "processing_version": "account_reference_detector-v1",
                "_source_title": source_title,
            }
            cpc.append_jsonl(evidence_path, record)

            reg = cpc.load_registry()
            for entry in reg.get("registry", []):
                if entry.get("account_id") == f"acct-{m.slug}":
                    entry["last_evidence_date"] = source_date
                    break
            cpc.save_json(cpc.registry_path(), reg)

            linked.append({"artifact_type": "blue_sheet", "slug": m.slug, "evidence_id": evidence_id})

        elif m.artifact_type == "competitor":
            try:
                data = cic.load_competitor(m.slug)
            except FileNotFoundError:
                continue
            existing = data["evidence"]
            already = any(
                e.get("category") == "other" and e.get("_source_title") == source_title
                for e in existing
            )
            if already:
                continue
            evidence_id = f"ref-{len(existing) + 1:04d}"
            record = {
                "evidence_id": evidence_id,
                "logged_at": cic.now_iso(),
                "category": "other",
                "summary": excerpt[:400] or f"Uploaded content mentioning {m.display_name} (matched on '{m.matched_on}').",
                "source": source_title,
                "confidence": None,
                "document_id": document_id,
                "_source_title": source_title,
            }
            cic.append_jsonl(cic.competitor_dir(m.slug) / "evidence.jsonl", record)

            comp = data["competitor"]
            comp["last_evidence_date"] = source_date
            comp["updated_at"] = cic.now_iso()
            cic.save_json(cic.competitor_dir(m.slug) / "competitor.json", comp)
            cic.register_competitor(m.slug, comp.get("display_name", m.slug))

            linked.append({"artifact_type": "competitor", "slug": m.slug, "evidence_id": evidence_id})

    return {"linked": linked, "match_count": len(matches)}
