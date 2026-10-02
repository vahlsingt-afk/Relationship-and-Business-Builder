#!/usr/bin/env python3
"""
tech_stack_relationship_promotion.py — RB-2026-08-31.

Todd's real tech-stack coverage (system/ecosystem_intelligence.json's
uses_vendor_for_category relationships) covers only ~278 of ~1,654 tracked
brands (~17%) -- thin enough that a competitive-landscape/battle-card
artifact built directly on it would mostly say "no data," not "here is a
real gap." Investigation (2026-08-31) found real, already-captured signal
that was never promoted into that coverage:

  - ecosystem_intelligence.json's own `signals` array (2,371 entries) --
    written by technomic_watchlist_scan.py and other pipelines, some of
    which (signal_type "vendor_claimed_customer_relationship", and now
    "vendor_relationship_formed" per the new ecosystem_brief.py keyword
    category this module depends on) name a real brand+vendor relationship
    but were only ever surfaced as brief-line intelligence, never checked
    against or written into the relationship graph.
  - system/account_intelligence/*.md -- Todd's own real notes/JPR
    transcripts, some of which mention a brand and a known vendor together
    but were never structured into a relationship either.

Same discipline as everywhere else in this codebase: this module never
invents a relationship. It proposes candidates for review -- persisted in
a pending-proposal store (same shape as identity_match_review.py) -- and
only ecosystem_intelligence.py's own proven relationship-write path
(vendor_relationship() + resolve_and_upsert_relationship() +
_write_graph(), the same functions the Master Account Plan and workbook
ingestion pipelines already use) ever touches the real graph, and only
after an explicit confirm via record_proposal().

CLI:
    python3 tech_stack_relationship_promotion.py scan            # scan + write candidates, print summary
    python3 tech_stack_relationship_promotion.py pending          # list pending candidates
    python3 tech_stack_relationship_promotion.py confirm <id>     # write the relationship into the graph
    python3 tech_stack_relationship_promotion.py reject <id>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import ecosystem_brief as eb  # noqa: E402

STORE_PATH = core.SYSTEM_DIR / ".cache" / "tech_stack_relationship_proposals.json"
ACCOUNT_INTELLIGENCE_DIR = core.SYSTEM_DIR / "account_intelligence"

# Signal types worth mining for a real brand->vendor adoption claim. The
# graph's existing (rare, LinkedIn-pipeline-only) "vendor_claimed_customer_
# relationship" type, plus the new keyword-driven "vendor_relationship_
# formed" class ecosystem_brief.py now assigns to press-release signals.
_VENDOR_SIGNAL_TYPES = {"vendor_claimed_customer_relationship", "vendor_relationship_formed"}

_MIN_NAME_LEN = 4  # same short-alias noise guard as account_reference_detector._name_in_text


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_store() -> dict:
    if not STORE_PATH.exists():
        return {"candidates": {}}
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"candidates": {}}
    data.setdefault("candidates", {})
    return data


def _save_store(store: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    store["_generated_at"] = _now_iso()
    STORE_PATH.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")


def _candidate_id(brand_id: str, vendor_id: str, category: Optional[str]) -> str:
    return f"{brand_id}::{vendor_id}::{category or 'unknown'}"


# RB-2026-08-31: confirmed live while building this -- inferring category
# from the VENDOR's own attributes.primary_category (a single, best-known
# category per vendor, e.g. Qu's is "bi_analytics") is unsafe: a vendor
# like Qu genuinely holds several real categories across different brands
# (pos, kds_kitchen_ops, menu_management, unified_commerce, ...), so
# defaulting every signal to the vendor's ONE primary category produced
# false "new" candidates for Blaze Pizza/Dave's Hot Chicken/GoTo Foods/
# Playa Bowls under category "bi_analytics" when all four already had a
# real, correct "pos" relationship with Qu on file -- the dedup check
# (which compares category too) never caught it because bi_analytics
# genuinely wasn't already on file, even though the underlying fact was.
# Only trust a category the signal/doc TEXT itself actually names.
_CATEGORY_TEXT_HINTS: dict[str, list[str]] = {
    "pos": ["point of sale", " pos ", " pos,", " pos.", "pos system", "pos software"],
    "payments": ["payment processing", "payments platform", "card processing", "payment terminal"],
    "loyalty": ["loyalty program", "loyalty platform", "rewards program", "guest loyalty"],
    "online_ordering": ["online ordering", "digital ordering", "mobile ordering", "app ordering"],
    "kds_kitchen_ops": ["kitchen display", "kds", "kitchen operations"],
    "drive_thru_ai": ["drive-thru ai", "drive thru ai", "voice ai drive"],
    "kiosks": ["self-order kiosk", "self order kiosk", "kiosks"],
    "digital_menu_boards": ["digital menu board", "menu board"],
    "labor_workforce": ["labor scheduling", "workforce management", "employee scheduling"],
    "back_office_operations": ["back office", "back-office"],
    "inventory": ["inventory management", "inventory platform"],
    "franchise_management": ["franchise management"],
    "bi_analytics": ["business intelligence", "analytics platform", "reporting dashboard"],
    # RB-2026-09-01: the 7 categories competitive_landscape.py needs that
    # tech_stack_workbook_sync.py's real "Canonical Tech Stack" columns
    # already track but no category-detection hint existed for yet.
    "pos_hardware": ["pos hardware", "pos terminal hardware", "payment terminal hardware"],
    "payments_gateway": ["payment gateway", "payments gateway", "gateway integration"],
    "drive_thru_timers": ["drive-thru timer", "drive thru timer", "speed of service timer", "sos timer"],
    "ai_solution_1": ["ai ordering assistant", "conversational ai ordering"],
    # No "ai_solution_2" entry: Todd's own workbook numbering (1 vs. 2, e.g.
    # a second/backup AI vendor) has no real-world keyword signal that
    # distinguishes it from ai_solution_1 in free text -- signal/doc mining
    # can never confidently tell them apart, so it deliberately never
    # detects ai_solution_2 rather than guessing. A human still can via
    # researchBrandTechStack's explicit `category` parameter.
    "broadband_network": ["broadband provider", "network connectivity provider", "store network provider"],
    "in_restaurant_media": ["in-restaurant media", "in-store media network", "digital signage network"],
}


def _infer_category_from_text(text: str) -> Optional[str]:
    """Return a category ONLY when the text itself names one -- never a
    guess from vendor metadata (see the comment above)."""
    tl = f" {(text or '').lower()} "
    for category, hints in _CATEGORY_TEXT_HINTS.items():
        if any(h in tl for h in hints):
            return category
    return None


def _existing_relationship(graph: dict, brand_id: str, vendor_id: str, category: Optional[str]) -> Optional[dict]:
    """A real, already-covered (brand, vendor, category) triple -- never
    propose a candidate for something the graph already has, active or
    historical (a historical entry means the fact was already reviewed
    once; re-proposing it as if new would be misleading)."""
    for rel in graph.get("relationships") or []:
        if rel.get("relationship_type") != "uses_vendor_for_category":
            continue
        if rel.get("from_entity_id") != brand_id or rel.get("to_entity_id") != vendor_id:
            continue
        if category is not None and rel.get("category") != category:
            continue
        return rel
    return None


def _conflict_preview(
    graph: dict, brand_id: str, vendor_id: str, category: Optional[str], relationship_status: Optional[str],
) -> dict:
    """Read-only preview of ecosystem_intelligence.check_relationship_conflict()
    against this candidate's (brand, vendor, category) -- lets a human
    reviewing pending_candidates() see whether confirming this would collide
    with an existing rival vendor's claim on the same category, before
    anything is written. RB-2026-09-08: previously the pipeline only ever
    checked for the exact same (brand, vendor, category) triple already on
    file (_existing_relationship, still used for that dedup) -- a candidate
    proposing a DIFFERENT vendor for a category some other vendor already
    holds sailed through with zero visibility into the rivalry until
    record_proposal() actually wrote it (which is still safe -- it already
    runs the real check via resolve_and_upsert_relationship -- just too late
    for the review step to be an informed one).

    vendor_role is deliberately left unset here (unlike a real graph write):
    check_relationship_conflict() only skips conflict-checking for a known
    non-exclusive role (approved_hardware_vendor etc.), and a preview with no
    role information should err toward flagging a possible conflict for
    human review, not toward silently assuming a non-exclusive role it has
    no evidence for."""
    if not category:
        return {"conflict": False}
    preview_rel = {
        "from_entity_id": brand_id, "to_entity_id": vendor_id,
        "category": category, "status": relationship_status or "active",
    }
    verdict = ei.check_relationship_conflict(graph, preview_rel)
    if not verdict.get("conflict"):
        return {"conflict": False}
    existing = verdict.get("existing") or {}
    by_id = ei._index_by_id(graph.get("entities") or [])
    rival_vendor = by_id.get(existing.get("to_entity_id"), {})
    return {
        "conflict": True,
        "resolution": verdict.get("resolution"),  # "auto_superseded" | "requires_confirmation"
        "rival_vendor_id": existing.get("to_entity_id"),
        "rival_vendor_name": rival_vendor.get("name"),
        "rival_status": existing.get("status"),
    }


def _add_candidate(
    store: dict, *, graph: dict, brand_id: str, brand_name: str, vendor_id: str, vendor_name: str,
    category: Optional[str], category_confidence: str,
    source_type: str, source_title: str, source_url: Optional[str], evidence_excerpt: str,
    origin: str, origin_ref: str, relationship_status: Optional[str] = None,
    evidence_date: Optional[str] = None,
) -> bool:
    """Insert or refresh one pending candidate. Returns True if this is a
    genuinely new candidate (for the run summary's new_this_run count).
    vendor_id/brand_id need not already exist as graph entities -- external
    research (brand_tech_stack_research.py) can propose a genuinely new
    vendor; ecosystem_intelligence.vendor_relationship() creates it at
    confirm time the same way workbook ingestion already does.

    evidence_date (RB-2026-09-08): the real-world date the underlying
    evidence was published/discovered -- e.g. a signal's own event_at, an
    account_intelligence doc's filename date prefix, or a date the caller of
    propose_research_finding() supplies. Previously dropped entirely; only
    detected_at (when RBB itself created the candidate) was kept, which
    conflates "found this today" with "this happened today" -- the same
    captured_at/event_at confusion already found and fixed elsewhere this
    session (the Del Taco/Postmates review item). Threaded through to the
    graph's source.published_at at confirm time (see record_proposal())."""
    cid = _candidate_id(brand_id, vendor_id, category)
    existing = store["candidates"].get(cid)
    if existing:
        if existing["status"] != "proposed_pending_confirmation":
            return False  # already resolved (confirmed/rejected) -- never re-propose
        # Same candidate, another corroborating source -- stack the evidence
        # rather than silently dropping it or duplicating the candidate.
        if origin_ref not in existing.get("supporting_refs", []):
            existing.setdefault("supporting_refs", []).append(origin_ref)
        return False
    store["candidates"][cid] = {
        "candidate_id": cid,
        "status": "proposed_pending_confirmation",
        "brand_id": brand_id,
        "brand_name": brand_name,
        "vendor_id": vendor_id,
        "vendor_name": vendor_name,
        "category": category,
        "category_confidence": category_confidence,  # "stated_in_signal" | "stated_in_text" | "unknown"
        "source_type": source_type,
        "source_title": source_title,
        "source_url": source_url,
        "evidence_excerpt": evidence_excerpt[:600],
        "origin": origin,
        "origin_ref": origin_ref,
        "relationship_status": relationship_status,  # None means "active" at confirm time
        "evidence_date": evidence_date,
        "conflict_preview": _conflict_preview(graph, brand_id, vendor_id, category, relationship_status),
        "supporting_refs": [],
        "detected_at": _now_iso(),
        "resolved_at": None,
        "resolution_note": None,
    }
    return True


def scan_existing_signals(*, dry_run: bool = False) -> dict:
    """Scan ecosystem_intelligence.json's own signals array for vendor-
    relationship-shaped entries and propose candidates for any (brand,
    vendor, category) triple the graph doesn't already have covered.

    2026-09-25 (Confidence-Based Auto-Recording): a genuinely new candidate
    with a real (non-"unknown") category is auto-applied immediately after
    this scan (see the record_proposal() batch pass below), via the same
    confidence-aware engine ecosystem_intelligence.check_relationship_
    conflict() now uses -- no more sitting in proposed_pending_confirmation
    waiting on a human click. A candidate with no inferrable category still
    can't auto-apply (record_proposal() has always refused to guess one)
    and stays pending, same as before."""
    graph = ei._read_graph()
    by_id = ei._index_by_id(graph.get("entities") or [])
    store = _load_store()

    scanned = 0
    new_count = 0
    to_auto_apply: list[str] = []
    for sig in graph.get("signals") or []:
        if sig.get("signal_type") not in _VENDOR_SIGNAL_TYPES:
            continue
        scanned += 1
        entity_ids = sig.get("entities") or []
        vendor_ids = [e for e in entity_ids if e.startswith("vendor-")]
        brand_ids = [e for e in entity_ids if e.startswith("brand-")]
        if not vendor_ids or not brand_ids:
            continue

        source_id = (sig.get("sources") or [None])[0]
        source = next((s for s in graph.get("sources") or [] if s.get("id") == source_id), None)
        source_type = (source or {}).get("source_type") or "credible_trade_reporting"
        category = _infer_category_from_text(sig.get("summary") or "")
        category_confidence = "stated_in_signal" if category else "unknown"

        for vendor_id in vendor_ids:
            vendor = by_id.get(vendor_id)
            if not vendor or vendor.get("entity_type") != "vendor":
                continue
            for brand_id in brand_ids:
                brand = by_id.get(brand_id)
                if not brand or brand.get("entity_type") != "brand":
                    continue
                if _existing_relationship(graph, brand_id, vendor_id, category):
                    continue
                added = _add_candidate(
                    store, graph=graph, brand_id=brand_id, brand_name=brand.get("name"),
                    vendor_id=vendor_id, vendor_name=vendor.get("name"), category=category,
                    category_confidence=category_confidence, source_type=source_type,
                    source_title=(source or {}).get("title") or sig.get("summary") or "",
                    source_url=(source or {}).get("url"), evidence_excerpt=sig.get("summary") or "",
                    origin="signal_scan", origin_ref=sig["id"],
                    evidence_date=sig.get("event_at"),
                )
                new_count += added
                if added and category:
                    to_auto_apply.append(_candidate_id(brand_id, vendor_id, category))

    if not dry_run:
        _save_store(store)

    auto_applied = 0
    if not dry_run:
        for cid in to_auto_apply:
            result = record_proposal(cid, confirmed=True, confirmed_by="system:tech_stack_relationship_promotion")
            if result.get("confirmed"):
                auto_applied += 1
        store = _load_store()

    return {"signals_scanned": scanned, "new_candidates": new_count, "auto_applied": auto_applied,
            "total_pending": sum(1 for c in store["candidates"].values()
                                  if c["status"] == "proposed_pending_confirmation")}


def _name_in_text(name: str, text_lower: str) -> bool:
    """Word-boundary match -- confirmed live while building this: a plain
    substring check matched the real brand "Ready" inside the common word
    "already" on every line that used it. Same failure class as the
    intelligence_mutation_engine olo/ncr/qu word-boundary defect fixed
    earlier this session -- \\b boundaries, not bare `in`."""
    n = (name or "").strip().lower()
    if len(n) < _MIN_NAME_LEN:
        return False
    return re.search(r"\b" + re.escape(n) + r"\b", text_lower) is not None


# RB-2026-08-31: confirmed live while building this -- matching at
# PARAGRAPH granularity against a real account_intelligence doc pulled in
# an entire markdown table as one block (no blank lines between rows), so
# every brand named in ANY row got cross-paired with every vendor named in
# ANY OTHER row ("Domino's x Oracle", "Papa Johns x Crunchtime" -- neither
# pairing was ever actually stated in the source). Matching at LINE
# granularity instead means only names that share the same table row/
# bullet/sentence can pair -- eliminates that whole class of false
# cross-pairing. Trade-off, accepted deliberately: a bullet list that
# states the vendor per-line without repeating the subject brand's name on
# every line (common in a single-brand doc, e.g. "- Oracle Simphony side
# uses Verifone P400.") won't match here -- precision over recall, same
# principle as the rest of this codebase's evidence-sourcing discipline.
_LINE_SPLIT_RE = re.compile(r"\r?\n")

# Todd's own employer's brands -- a customer-win fact about Genius/Worldpay
# itself is not competitive tech-stack intelligence and has its own real
# channels (Blue Sheets, Master Account Plans). RB-2026-09-01: centralized
# to rb_core.GP_OWN_TERMS (was duplicated here and in
# render_intelligence_brief.py; a third real need in competitive_landscape
# .py made the duplication worth ending).
_OWN_COMPANY_TERMS = set(core.GP_OWN_TERMS)

# Aspirational/prospective language -- "in process of migrating," "open to
# discussing," "considering," "evaluating" describe a SALES CONVERSATION or
# a not-yet-completed change, not a real, current vendor relationship. A
# line carrying one of these must never produce a candidate even if an
# adoption keyword also appears elsewhere on it (confirmed live: "In
# process of migrating to Oracle Simphony, but open to discussing Genius"
# otherwise produced a false "already uses Genius" candidate).
_PROSPECTIVE_MARKERS = [
    "in process of", "open to discussing", "considering", "evaluating",
    "candidate for", "potential", "exploring", "in talks", "proposed",
    "may consider", "could consider",
]

_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-|$)")
_VALID_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _date_prefix_from_filename(stem: str) -> Optional[str]:
    """account_intelligence/*.md's real naming convention is
    YYYY-MM-DD-<slug>.md (confirmed against real files) -- a genuine
    evidence_date, not guessed, when the filename actually starts with one.
    Returns None rather than a wrong date for any file that doesn't."""
    m = _DATE_PREFIX_RE.match(stem)
    return m.group(1) if m else None


def scan_account_intelligence_docs(*, dry_run: bool = False) -> dict:
    """Scan Todd's own account_intelligence notes for a brand and a known
    vendor named on the same LINE, with adoption-shaped (not prospective)
    language on that line -- same mechanical, no-NLP, evidence-attached
    discipline as account_reference_detector.py's Blue Sheet/competitor
    matching."""
    graph = ei._read_graph()
    brands = [e for e in graph.get("entities") or [] if e.get("entity_type") == "brand"]
    vendors = [
        e for e in graph.get("entities") or []
        if e.get("entity_type") == "vendor" and (e.get("name") or "").strip().lower() not in _OWN_COMPANY_TERMS
    ]
    store = _load_store()

    adoption_terms = list(eb.SIGNAL_CLASS_KEYWORDS["vendor_relationship_formed"]) + [
        "uses", "runs on", "is on", "is a customer of", "customer of",
    ]

    scanned = 0
    new_count = 0
    to_auto_apply: list[str] = []
    if not ACCOUNT_INTELLIGENCE_DIR.exists():
        return {"docs_scanned": 0, "new_candidates": 0, "auto_applied": 0, "total_pending": 0}

    # RB-2026-08-31: confirmed live -- checking all ~1,654 brand names on
    # every line via a fresh word-boundary regex each time was slow enough
    # to matter (a 22-doc scan took minutes). Order checks cheapest-first:
    # the ~20-term adoption-language substring check and the ~85-vendor
    # word-boundary check both fail on the overwhelming majority of lines,
    # so only lines that already cleared both ever pay the cost of scanning
    # the full brand list.
    for path in sorted(ACCOUNT_INTELLIGENCE_DIR.glob("*.md")):
        scanned += 1
        evidence_date = _date_prefix_from_filename(path.stem)
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_no, line in enumerate(_LINE_SPLIT_RE.split(text)):
            line_lower = line.lower()
            if not any(term in line_lower for term in adoption_terms):
                continue
            if any(marker in line_lower for marker in _PROSPECTIVE_MARKERS):
                continue
            matched_vendors = [v for v in vendors if _name_in_text(v.get("name", ""), line_lower)]
            if not matched_vendors:
                continue
            matched_brands = [b for b in brands if _name_in_text(b.get("name", ""), line_lower)]
            if not matched_brands:
                continue
            category = _infer_category_from_text(line)
            category_confidence = "stated_in_text" if category else "unknown"
            for vendor in matched_vendors:
                for brand in matched_brands:
                    if brand["id"] == vendor["id"]:
                        continue
                    if _existing_relationship(graph, brand["id"], vendor["id"], category):
                        continue
                    added = _add_candidate(
                        store, graph=graph, brand_id=brand["id"], brand_name=brand.get("name"),
                        vendor_id=vendor["id"], vendor_name=vendor.get("name"), category=category,
                        category_confidence=category_confidence, source_type="operator_context",
                        source_title=path.name, source_url=None,
                        evidence_excerpt=line.strip(), origin="account_intelligence_scan",
                        origin_ref=f"{path.name}#L{line_no}",
                        evidence_date=evidence_date,
                    )
                    new_count += added
                    if added and category:
                        to_auto_apply.append(_candidate_id(brand["id"], vendor["id"], category))

    if not dry_run:
        _save_store(store)

    auto_applied = 0
    if not dry_run:
        for cid in to_auto_apply:
            result = record_proposal(cid, confirmed=True, confirmed_by="system:tech_stack_relationship_promotion")
            if result.get("confirmed"):
                auto_applied += 1
        store = _load_store()

    return {"docs_scanned": scanned, "new_candidates": new_count, "auto_applied": auto_applied,
            "total_pending": sum(1 for c in store["candidates"].values()
                                  if c["status"] == "proposed_pending_confirmation")}


def scan(*, dry_run: bool = False) -> dict:
    """Run both scan sources and return a combined summary."""
    signals_result = scan_existing_signals(dry_run=dry_run)
    docs_result = scan_account_intelligence_docs(dry_run=dry_run)
    return {"signals": signals_result, "account_intelligence": docs_result}


def pending_candidates() -> list[dict]:
    store = _load_store()
    return [c for c in store["candidates"].values() if c["status"] == "proposed_pending_confirmation"]


def record_proposal(candidate_id: str, *, confirmed: bool, confirmed_by: str = "human") -> dict:
    """Confirm (write the real relationship via ecosystem_intelligence.py's
    own proven write path) or reject one pending candidate.

    confirmed_by (2026-09-25, Confidence-Based Auto-Recording): "human" for
    an explicit confirm via chat/CLI (the default, unchanged), or
    "system:<caller>" when scan_existing_signals()/scan_account_
    intelligence_docs() auto-applied a genuinely new, real-category
    candidate immediately rather than leaving it pending. Stored on the
    candidate so the audit trail (pending_candidates()/the store itself)
    always shows which resolutions were a person's decision vs. the
    confidence-aware engine's own."""
    store = _load_store()
    cand = store["candidates"].get(candidate_id)
    if not cand:
        return {"error": f"unknown candidate id: {candidate_id}"}
    if cand["status"] != "proposed_pending_confirmation":
        return {"error": f"candidate {candidate_id} already resolved: {cand['status']}"}

    if not confirmed:
        cand["status"] = "rejected"
        cand["resolved_at"] = _now_iso()
        _save_store(store)
        return {"rejected": True, "candidate_id": candidate_id}

    if not cand.get("category"):
        return {
            "error": (
                f"candidate {candidate_id} has no known category (category_confidence="
                "'unknown') -- set one via addBlueSheetEvidence/direct graph edit first, "
                "or reject this candidate; never guessed here."
            ),
        }

    graph = ei._read_graph()
    row = {
        "brand": cand["brand_name"],
        "vendor": cand["vendor_name"],
        "category": cand["category"],
        "source_type": cand["source_type"],
        "source": cand["source_title"],
        "source_url": cand.get("source_url") or "",
        # 2026-09-25 (Confidence-Based Auto-Recording): no more hardcoded
        # "medium" here -- omitting this key lets ei.vendor_relationship()
        # fall through to SOURCE_QUALITY_MODEL's real per-source_type
        # default (e.g. "high" for primary_operator_statement, "low" for
        # job_posting), which _confidence() (fixed the same day) now turns
        # into a real numeric confidence.score -- exactly what check_
        # relationship_conflict()'s score comparison needs to tell a
        # trade-press guess apart from a primary/official source.
        "evidence_posture": "provisional",
        # RB-2026-09-02: confirmed live -- without this key,
        # ei.vendor_relationship() defaults status to "active" for every
        # confirmed candidate, including ones whose whole evidentiary point
        # is that the relationship ENDED (e.g. a vendor's own 10-Q showing
        # the customer discontinued it). relationship_status is set by
        # propose_research_finding()'s caller; None here still means
        # "active" (the common, correct default for a real current-signal
        # finding), same as before this fix -- this only changes behavior
        # when a caller actually supplies a non-active status.
        "status": cand.get("relationship_status") or "active",
        # RB-2026-09-08: without this key, ei.vendor_relationship()'s
        # _upsert_source() call always wrote published_at=None for every
        # confirmed candidate, regardless of whether the evidence itself
        # was from today or years ago -- see _add_candidate()'s own
        # evidence_date docstring for the full context. None here (no
        # evidence_date on file) still means published_at stays None, same
        # as before this fix.
        "source_date": cand.get("evidence_date"),
    }
    rel = ei.vendor_relationship(row, graph)
    if rel is None:
        return {"error": f"could not build a relationship record for candidate {candidate_id}"}
    result = ei.resolve_and_upsert_relationship(graph, rel)
    ei._write_graph(graph)

    cand["status"] = "confirmed"
    cand["resolved_at"] = _now_iso()
    cand["relationship_id"] = rel.get("id")
    cand["confirmed_by"] = confirmed_by
    _save_store(store)
    return {
        "confirmed": True, "candidate_id": candidate_id, "relationship_id": rel.get("id"),
        "added": result.get("added"), "conflict": result.get("conflict", {}).get("conflict", False),
    }


def propose_research_finding(
    brand_id: str, vendor_name: str, evidence_text: str, *,
    category: Optional[str] = None, source_url: Optional[str] = None,
    source_title: Optional[str] = None, status: Optional[str] = None,
    evidence_date: Optional[str] = None,
) -> dict:
    """Propose one candidate from research already gathered by the CALLER
    (rbb-chat/Codex, using its own real web search/fetch tools -- this
    module never fetches the web itself, same reason uploadAndIngestFile
    accepts already-extracted text rather than fetching a file's source
    URL server-side). vendor_name need not already be a known vendor
    entity -- discovering a genuinely new one is real value here; it's
    created the same way workbook ingestion already creates one, only at
    confirm time via ecosystem_intelligence.vendor_relationship().

    category, when omitted, is inferred from evidence_text using the same
    stated-in-text-only rule as the other two scan paths -- never guessed
    from the vendor's general market position.

    status (e.g. "historical" for a relationship the evidence shows has
    ENDED, vs. the default "active") is a real, separate finding from
    category -- confirmed live 2026-09-02: omitting it meant record_proposal
    always wrote status="active" regardless of what the evidence actually
    said, including for a finding whose entire point was that a vendor's
    own 10-Q showed the relationship had ended. Must be a value from
    ei.RELATIONSHIP_STATUS_VOCAB; an invalid value is rejected, never
    silently coerced to "active".

    evidence_date (RB-2026-09-08, YYYY-MM-DD): the real-world date the
    underlying evidence was published/discovered -- e.g. a press release's
    dateline, an SEC filing's date, a LinkedIn post's timestamp. Previously
    had no way in at all: the eventual graph source record's published_at
    was always None regardless of how old or recent the real evidence was
    -- the same captured_at/event_at confusion already found and fixed
    elsewhere this session (the Del Taco/Postmates review item looking
    "new" only because it was captured into the graph weeks after the
    actual 2019 event). Optional (omit rather than guess one) but
    validated when given -- an invalid format is rejected, never silently
    dropped or coerced."""
    if status and status not in ei.RELATIONSHIP_STATUS_VOCAB:
        return {"error": f"status {status!r} is not valid -- must be one of {sorted(ei.RELATIONSHIP_STATUS_VOCAB)}"}
    if evidence_date and not _VALID_DATE_RE.match(evidence_date):
        return {"error": f"evidence_date {evidence_date!r} is not a valid YYYY-MM-DD date."}
    graph = ei._read_graph()
    by_id = ei._index_by_id(graph.get("entities") or [])
    brand = by_id.get(brand_id)
    if not brand or brand.get("entity_type") != "brand":
        return {"error": f"no brand entity '{brand_id}' -- resolve a real brand_id first (e.g. via queryIntelligenceIndex)."}
    vendor_name = (vendor_name or "").strip()
    if not vendor_name:
        return {"error": "vendor_name is required."}
    if vendor_name.strip().lower() in _OWN_COMPANY_TERMS:
        return {"error": "Genius/Global Payments/Worldpay is not competitive tech-stack intelligence -- use addBlueSheetEvidence instead."}
    if not (evidence_text or "").strip():
        return {"error": "evidence_text is required -- never propose a finding with no real evidence attached."}

    vendor_id = f"vendor-{ei._slug(vendor_name)}"
    existing_vendor = by_id.get(vendor_id)
    resolved_category = category or _infer_category_from_text(evidence_text)
    category_confidence = "stated_by_caller" if category else ("stated_in_text" if resolved_category else "unknown")

    if _existing_relationship(graph, brand_id, vendor_id, resolved_category):
        return {"error": f"a relationship for {brand.get('name')} / {vendor_name}"
                          f"{' / ' + resolved_category if resolved_category else ''} is already on file -- nothing new to propose."}

    store = _load_store()
    added = _add_candidate(
        store, graph=graph, brand_id=brand_id, brand_name=brand.get("name"),
        vendor_id=vendor_id, vendor_name=(existing_vendor or {}).get("name") or vendor_name,
        category=resolved_category, category_confidence=category_confidence,
        source_type="credible_trade_reporting", source_title=source_title or vendor_name,
        source_url=source_url, evidence_excerpt=evidence_text, origin="external_research",
        relationship_status=status, evidence_date=evidence_date,
        origin_ref=source_url or f"research::{brand_id}::{vendor_id}::{_now_iso()}",
    )
    _save_store(store)
    cid = _candidate_id(brand_id, vendor_id, resolved_category)
    return {"proposed": added, "candidate_id": cid,
            "note": "already pending (evidence stacked)" if not added else None}


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    sub.add_parser("pending")
    p_confirm = sub.add_parser("confirm")
    p_confirm.add_argument("candidate_id")
    p_reject = sub.add_parser("reject")
    p_reject.add_argument("candidate_id")
    args = p.parse_args()

    if args.cmd == "scan":
        print(json.dumps(scan(), indent=2))
    elif args.cmd == "pending":
        print(json.dumps(pending_candidates(), indent=2))
    elif args.cmd == "confirm":
        print(json.dumps(record_proposal(args.candidate_id, confirmed=True), indent=2))
    elif args.cmd == "reject":
        print(json.dumps(record_proposal(args.candidate_id, confirmed=False), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
