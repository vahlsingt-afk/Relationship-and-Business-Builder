#!/usr/bin/env python3
"""
import_genius_competitive_research.py — RB-2026-09-26.

Loads a deep-research JSON export (schema "rb.competitive_research.v1"/
"v2" -- see the instruction set that produced it) into RB's real, existing
stores:

  - Phase 1 entities (Genius's own product lines): Positioning/Strengths
    claims at VERY HIGH/HIGH confidence, not flagged is_inference, AND on
    a real GENIUS_PRODUCT_LINES-mapped entity, become genius_capabilities.py
    capability points -- tagged added_by="system:genius_competitive_
    research_<date>", never "Todd Vahlsing", so a reader can always tell
    this was imported research, not something Todd personally typed.
    Everything else about a phase-1 entity (financial/market signals,
    named customers, weaknesses, C-suite, news, rumors -- plus ALL claims
    for the parent-company entity "Global Payments (parent)" and the
    "adjacent" entity "Kitchen Management", neither of which maps to a
    single product line) goes into genius_capabilities.py's companion
    evidence log (add_evidence(), scope = the product line, or "parent"/
    "adjacent") instead -- found live, during the first real import run,
    that routing ONLY capability-shaped claims and silently dropping the
    rest lost 92 real, sourced claims with nowhere to go.

  - Phase 2 entities (competitors) become competitor_intelligence
    evidence.jsonl records via add_competitive_note() -- every real claim,
    every confidence level, "fuzzy is okay" (Todd's own stated principle
    this session). Strengths/Weaknesses claims at VERY HIGH/HIGH/MEDIUM
    confidence, not flagged is_inference, ALSO get promoted into the
    structured vs_genius gap-analysis field via add_gap_point(), linked
    back to the evidence record that grounds them (evidence_id passed
    through, per add_gap_point()'s own "auditable back to real evidence"
    discipline). A competitor not yet tracked in RB (recommended_add=true,
    e.g. Acrelec/Delphi/Fourth) is resolved-or-created via
    ensure_competitor() using its real name, the same mechanism
    Team Portal already uses -- not the slug-guess fallback
    ensure_competitor_by_slug() uses elsewhere, since a real name is
    available here and gives a materially better shell (real aliases,
    real primary_category from a matching vendor entity when one exists).
    An entity flagged flag_acquired=true is skipped entirely ONLY when it
    has zero real claims (Compeat, round 1: every claim was a pure cross-
    reference to Restaurant365, its acquirer). When it carries real claims
    (Revel Systems/CardFree/Yumpingo/Delaget/Jolt/MeazureUp, round 2: real
    acquisition facts, dates, prices, competitive implications), it's
    processed normally and gets its own competitor record -- flag_acquired
    means "don't treat this as a live independent rival forever," not
    "discard its real history."

  - NOT_FOUND and CROSS_REFERENCE claims are never imported -- neither is
    a real fact; both are informational only in the source document.

Deliberately does NOT attempt to extract structured executive-move or
ownership candidates from C-Suite/leadership prose (that's exactly the
kind of general-purpose free-text extraction this codebase avoids --
executive_move_promotion.py's own regex extraction is narrowly anchored
to a known target name + a fixed set of appointment-verb phrases, not a
general parser). C-Suite & Leadership claims land as plain evidence
(category "other"), same as every other non-exact-match dimension --
a human can promote a specific one manually later if warranted.

Idempotent: a local import log (system/.cache/genius_competitive_research_
import_log.json) tracks which (entity, claim) pairs have already been
written, keyed by a stable hash of entity name + claim text + dimension --
so re-running against an updated/expanded export (the same file re-sent
after a correction, e.g. the RTI-footprint addition) only writes what's
genuinely new, never re-appends already-imported evidence.

Confidence mapping (claim.confidence -> RB's schema-level vocabulary,
same low/medium/high/critical bands ecosystem_intelligence.json and
confidence_calibration.py already use):
  VERY HIGH -> critical   HIGH -> high   MEDIUM -> medium   LOW -> low

CLI:
    python3 import_genius_competitive_research.py <path-to.json> --dry-run
    python3 import_genius_competitive_research.py <path-to.json> --confirm
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import competitor_intelligence as compintel  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402
import genius_capabilities as gc  # noqa: E402

IMPORT_LOG_PATH = core.CACHE_DIR / "genius_competitive_research_import_log.json"
SOURCE_TAG = "system:genius_competitive_research_2026-09"

_SKIP_CONFIDENCE = {"NOT_FOUND", "CROSS_REFERENCE"}

_CONFIDENCE_TO_LEVEL = {
    "VERY HIGH": "critical",
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
}

# dimension (as it appears in the JSON) -> competitor_intelligence evidence category.
# Only dimensions with a real, exact-meaning match get their own category;
# everything else lands honestly in "other" rather than being force-fit.
_DIMENSION_TO_EVIDENCE_CATEGORY = {
    "Positioning": "positioning",
    "Strengths": "strength",
    "Weaknesses / Vulnerabilities": "weakness",
    "Named Customers": "reference_customer",
    "Pricing signals": "pricing",
}
_DEFAULT_EVIDENCE_CATEGORY = "other"

# Phase-1 Genius entity name (as it appears in the JSON) -> scope.
# GENIUS_PRODUCT_LINES-valued entries are eligible for BOTH the capability
# library and the evidence log; "parent" (Global Payments corporate-level
# facts) and "adjacent" (a Genius-adjacent line, e.g. Kitchen Management)
# are evidence-log-only -- genius_capabilities.add_capability() itself
# still only accepts a real GENIUS_PRODUCT_LINES value, by design (a
# capability library scoped to "what Genius offers" shouldn't carry
# parent-company financial metrics). Hand-reviewed, not pattern-matched --
# names are free text in the source and a wrong guess here would mis-seed
# either store.
_GENIUS_ENTITY_TO_SCOPE = {
    "Genius N Software / Genius for Enterprise POS (formerly Xenial Cloud)": "pos",
    "Genius Back Office (formerly RTI)": "back_office",
    "Worldpay": "payments",
    "Genius Loyalty (formerly Como)": "loyalty_engagement",
    "Genius Drive Thru Director": "kitchen_drive_thru",
    "Genius Digital Menu Boards": "digital_menu_boards",
    "Global Payments (parent)": "parent",
    "Kitchen Management (adjacent Genius line, for context)": "adjacent",
}

# genius_own_evidence's VALID_EVIDENCE_CATEGORIES is a subset of
# competitor_intelligence's (no customer_win/customer_loss -- see
# genius_capabilities.py) -- reuse the competitor mapping for the
# dimensions both share and fall back to "other" for the rest.
_DIMENSION_TO_GENIUS_EVIDENCE_CATEGORY = {
    "Positioning": "positioning",
    "Strengths": "strength",
    "Weaknesses / Vulnerabilities": "weakness",
    "Named Customers": "reference_customer",
    "Pricing signals": "pricing",
}

_CAPABILITY_DIMENSIONS = {"Positioning", "Strengths"}
_CAPABILITY_MIN_CONFIDENCE = {"VERY HIGH", "HIGH"}
_GAP_POINT_DIMENSIONS = {"Strengths": "competitor", "Weaknesses / Vulnerabilities": "genius"}
_GAP_POINT_MIN_CONFIDENCE = {"VERY HIGH", "HIGH", "MEDIUM"}


def _claim_key(entity_name: str, dimension: str, claim_text: str) -> str:
    basis = f"{entity_name}\x1f{dimension}\x1f{claim_text}".encode("utf-8")
    return hashlib.sha1(basis).hexdigest()[:20]


def _load_import_log() -> dict:
    if not IMPORT_LOG_PATH.exists():
        return {"imported_claim_keys": []}
    try:
        data = json.loads(IMPORT_LOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"imported_claim_keys": []}
    data.setdefault("imported_claim_keys", [])
    return data


def _save_import_log(data: dict) -> None:
    IMPORT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    IMPORT_LOG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _claim_source(claim: dict) -> str:
    urls = claim.get("urls") or []
    if urls:
        return urls[0]
    if claim.get("source_ref"):
        ref = claim["source_ref"]
        return f"{ref.get('name', 'unknown')}, {ref.get('date', '')}".strip(", ")
    return claim.get("source_text") or SOURCE_TAG


def _import_genius_capability(entity_name: str, product_line: str, claim: dict, *, dry_run: bool) -> Optional[dict]:
    point = claim["claim"].strip()
    source = _claim_source(claim)
    why = f"Source: {source}" if source and source != SOURCE_TAG else None
    if dry_run:
        return {"action": "add_genius_capability", "category": product_line, "point": point[:100]}
    return gc.add_capability(product_line, point, why_it_matters=why, added_by=SOURCE_TAG)


def _import_genius_evidence(scope: str, claim: dict, *, dry_run: bool) -> Optional[dict]:
    dimension = claim["dimension"]
    category = _DIMENSION_TO_GENIUS_EVIDENCE_CATEGORY.get(dimension, "other")
    level = _CONFIDENCE_TO_LEVEL.get(claim["confidence"], "low")
    note = claim["claim"].strip()
    source = _claim_source(claim)
    if dry_run:
        return {"action": "add_genius_evidence", "scope": scope, "category": category,
                "confidence": level, "note": note[:100]}
    return gc.add_evidence(scope, note, category=category, source=source, confidence=level)


def _import_competitor_evidence(slug: str, claim: dict, *, dry_run: bool) -> Optional[dict]:
    dimension = claim["dimension"]
    category = _DIMENSION_TO_EVIDENCE_CATEGORY.get(dimension, _DEFAULT_EVIDENCE_CATEGORY)
    level = _CONFIDENCE_TO_LEVEL.get(claim["confidence"], "low")
    note = claim["claim"].strip()
    source = _claim_source(claim)
    if dry_run:
        return {"action": "add_competitive_note", "slug": slug, "category": category,
                "confidence": level, "note": note[:100]}
    return compintel.add_competitive_note(slug, note, category=category, source=source, confidence=level)


def _import_gap_point(slug: str, side: str, claim: dict, evidence_id: Optional[str], *, dry_run: bool) -> Optional[dict]:
    point = claim["claim"].strip()
    if dry_run:
        return {"action": "add_gap_point", "slug": slug, "side": side, "point": point[:100]}
    return compintel.add_gap_point(slug, side, point, evidence_id=evidence_id)


def import_research(payload: dict, *, dry_run: bool = True) -> dict:
    log = _load_import_log()
    seen: set[str] = set(log["imported_claim_keys"])
    new_keys: list[str] = []

    summary = {
        "entities_processed": 0,
        "entities_skipped": [],
        "genius_capabilities_added": 0,
        "genius_evidence_added": 0,
        "competitor_evidence_added": 0,
        "gap_points_added": 0,
        "claims_skipped_not_found_or_cross_ref": 0,
        "claims_skipped_already_imported": 0,
        "genius_own_claims_with_no_destination": 0,
        "actions_preview": [],
    }

    for entity in payload.get("entities", []):
        entity_name = entity.get("entity") or ""
        phase = entity.get("phase")
        claims = entity.get("claims") or []

        if entity.get("flag_acquired"):
            real_claims = sum(1 for c in claims if c["confidence"] not in _SKIP_CONFIDENCE)
            if real_claims == 0:
                # e.g. Compeat (round 1): flag_acquired AND every claim is a
                # pure cross-reference to the acquirer's own entity -- there
                # is genuinely nothing here to preserve as a standalone
                # record. Confirmed real, not assumed: only skip when the
                # claims themselves carry no content.
                summary["entities_skipped"].append({
                    "entity": entity_name,
                    "reason": "flag_acquired with no real claims (pure cross-reference to the acquirer)",
                })
                continue
            # RB-2026-09-26, round 2: found live that several flag_acquired
            # entities (Revel Systems, CardFree, Yumpingo, Delaget, Jolt,
            # MeazureUp) carry real, sourced acquisition facts, positioning,
            # and competitive implications -- NOT pure cross-references.
            # flag_acquired means "don't treat this as a live, independent
            # rival forever," not "discard its real history." Falls through
            # to the normal phase2_competitor path below; the acquisition
            # fact itself lands as ordinary Snapshot-category evidence, so
            # a reader sees the full picture (what it was, who absorbed it,
            # what that means competitively) on its own competitor record.

        if phase == "phase1_genius":
            scope = _GENIUS_ENTITY_TO_SCOPE.get(entity_name)
            if not scope:
                real_claims = sum(1 for c in claims if c["confidence"] not in _SKIP_CONFIDENCE)
                summary["entities_skipped"].append({
                    "entity": entity_name, "reason": "no scope mapping in _GENIUS_ENTITY_TO_SCOPE",
                    "real_claims_with_no_destination": real_claims,
                })
                summary["genius_own_claims_with_no_destination"] += real_claims
                continue
            summary["entities_processed"] += 1
            is_capability_eligible_scope = scope in cic.GENIUS_PRODUCT_LINES
            for claim in claims:
                if claim["confidence"] in _SKIP_CONFIDENCE:
                    summary["claims_skipped_not_found_or_cross_ref"] += 1
                    continue
                key = _claim_key(entity_name, claim["dimension"], claim["claim"])
                if key in seen:
                    summary["claims_skipped_already_imported"] += 1
                    continue

                is_capability = (
                    is_capability_eligible_scope
                    and claim["dimension"] in _CAPABILITY_DIMENSIONS
                    and claim["confidence"] in _CAPABILITY_MIN_CONFIDENCE
                    and not claim.get("is_inference")
                )
                if is_capability:
                    result = _import_genius_capability(entity_name, scope, claim, dry_run=dry_run)
                    if result is not None:
                        summary["genius_capabilities_added"] += 1
                        if dry_run:
                            summary["actions_preview"].append(result)
                else:
                    result = _import_genius_evidence(scope, claim, dry_run=dry_run)
                    if result is not None:
                        summary["genius_evidence_added"] += 1
                        if dry_run:
                            summary["actions_preview"].append(result)
                new_keys.append(key)

        elif phase == "phase2_competitor":
            summary["entities_processed"] += 1
            slug: Optional[str] = None
            if not dry_run:
                slug, _already = compintel.ensure_competitor(entity_name)
            for claim in claims:
                if claim["confidence"] in _SKIP_CONFIDENCE:
                    summary["claims_skipped_not_found_or_cross_ref"] += 1
                    continue
                key = _claim_key(entity_name, claim["dimension"], claim["claim"])
                if key in seen:
                    summary["claims_skipped_already_imported"] += 1
                    continue
                evidence_result = _import_competitor_evidence(slug or entity_name, claim, dry_run=dry_run)
                if evidence_result is not None:
                    summary["competitor_evidence_added"] += 1
                    if dry_run:
                        summary["actions_preview"].append(evidence_result)
                new_keys.append(key)

                gap_side = _GAP_POINT_DIMENSIONS.get(claim["dimension"])
                if (gap_side and claim["confidence"] in _GAP_POINT_MIN_CONFIDENCE
                        and not claim.get("is_inference")):
                    evidence_id = evidence_result.get("evidence_id") if evidence_result else None
                    gap_result = _import_gap_point(slug or entity_name, gap_side, claim, evidence_id, dry_run=dry_run)
                    if gap_result is not None:
                        summary["gap_points_added"] += 1
                        if dry_run:
                            summary["actions_preview"].append(gap_result)
        else:
            summary["entities_skipped"].append({"entity": entity_name, "reason": f"unknown phase {phase!r}"})

    if not dry_run and new_keys:
        log["imported_claim_keys"] = sorted(seen | set(new_keys))
        _save_import_log(log)

    return summary


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("json_path")
    p.add_argument("--confirm", action="store_true", help="Actually write (default: dry run only).")
    args = p.parse_args()

    payload = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
    result = import_research(payload, dry_run=not args.confirm)
    preview = result.pop("actions_preview", [])
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not args.confirm:
        print(f"\n[dry run -- {len(preview)} action(s) previewed, nothing written. Re-run with --confirm to apply.]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
