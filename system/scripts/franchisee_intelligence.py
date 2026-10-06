"""
franchisee_intelligence.py — Franchisee Finder, Phase 1 (2026-10-02).

Writers for the franchisee_finder domain, mirroring competitor_
intelligence.py's add_extended_profile_finding()/add_competitive_note()
discipline: every call is one real, sourced observation, never fabricated,
never silently promoted to higher confidence than the evidence supports.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import franchisee_intelligence_common as fic  # noqa: E402


def slugify(display_name: str) -> str:
    return fic._slug(display_name)


def create_franchisee(display_name: str, *, slug: str | None = None) -> dict:
    slug = slug or slugify(display_name)
    fic.create_franchisee_shell(slug, display_name)
    fic.register_franchisee(slug, display_name)
    return {"ok": True, "franchisee_slug": slug}


def add_franchisee_evidence(slug: str, note: str, *, category: str = "other",
                             source: str | None = None, confidence: str | int | None = None) -> dict:
    if category not in fic.VALID_EVIDENCE_CATEGORIES:
        raise ValueError(f"category must be one of {sorted(fic.VALID_EVIDENCE_CATEGORIES)}, got {category!r}")
    if not (note or "").strip():
        raise ValueError("note must not be empty")
    entry = {
        "category": category, "summary": note.strip(), "source": source,
        "confidence": confidence, "recorded_at": fic.now_iso(),
    }
    fic.append_jsonl(fic.franchisee_dir(slug) / "evidence.jsonl", entry)
    org = fic.load_json(fic.franchisee_dir(slug) / "organization.json")
    org["last_evidence_date"] = fic.today()
    org["updated_at"] = fic.now_iso()
    fic.save_json(fic.franchisee_dir(slug) / "organization.json", org)
    return {"ok": True, "franchisee_slug": slug}


def add_extended_profile_finding(
    slug: str, field: str, value: str, *, confidence: str = "medium",
    source_url: str | None = None, as_of: str | None = None,
    finding_type: str | None = None, source_owner: str | None = None,
    source_type: str | None = None, published_at: str | None = None,
    is_vendor_claim: bool | None = None, is_inference: bool | None = None,
    limitations_or_conflicts: str | None = None,
) -> dict:
    """Writer for EXTENDED_PROFILE_FIELDS (list fields, each entry
    independently dated/sourced) and EXTENDED_SCALAR_FIELDS (single
    synthesized-prose or numeric-string field, e.g. headquarters,
    total_identified_units)."""
    if field in fic.EXTENDED_SCALAR_FIELDS:
        pass
    elif field not in fic.EXTENDED_PROFILE_FIELDS:
        raise ValueError(f"field must be one of {fic.EXTENDED_PROFILE_FIELDS + fic.EXTENDED_SCALAR_FIELDS}, got {field!r}")
    if not str(value or "").strip():
        raise ValueError("value must not be empty")

    org = fic.load_json(fic.franchisee_dir(slug) / "organization.json")
    leaf = fic.extended_field(
        str(value).strip(), confidence=confidence, as_of=as_of, source_url=source_url,
        finding_type=finding_type, source_owner=source_owner, source_type=source_type,
        published_at=published_at, is_vendor_claim=is_vendor_claim, is_inference=is_inference,
        limitations_or_conflicts=limitations_or_conflicts,
    )

    if field in fic.EXTENDED_SCALAR_FIELDS:
        org[field] = leaf
    else:
        existing = org.setdefault(field, [])
        if any(e.get("value") == leaf["value"] for e in existing):
            return {"ok": True, "franchisee_slug": slug, "field": field, "deduped": True}
        existing.append(leaf)

    org["last_evidence_date"] = fic.today()
    org["last_researched"] = fic.today()
    org["updated_at"] = fic.now_iso()
    fic.save_json(fic.franchisee_dir(slug) / "organization.json", org)
    fic.register_franchisee(slug, org.get("display_name", slug))
    return {"ok": True, "franchisee_slug": slug, "field": field, "deduped": False}


def set_hierarchy_level(slug: str, level: str) -> dict:
    if level not in fic.VALID_HIERARCHY_LEVELS:
        raise ValueError(f"level must be one of {sorted(fic.VALID_HIERARCHY_LEVELS)}, got {level!r}")
    org = fic.load_json(fic.franchisee_dir(slug) / "organization.json")
    org["hierarchy_level"] = level
    org["updated_at"] = fic.now_iso()
    fic.save_json(fic.franchisee_dir(slug) / "organization.json", org)
    return {"ok": True, "franchisee_slug": slug, "hierarchy_level": level}


def set_confidence_tier(slug: str, tier: str) -> dict:
    """low -> monthly refresh cadence, high -> quarterly (spec section 8).
    Never auto-computed from a single assertion's confidence; a caller
    (the Hunter importer, after processing a full packet) decides this
    from the organization's overall evidence state."""
    if tier not in {"low", "high"}:
        raise ValueError(f"tier must be 'low' or 'high', got {tier!r}")
    org = fic.load_json(fic.franchisee_dir(slug) / "organization.json")
    org["confidence_tier"] = tier
    org["updated_at"] = fic.now_iso()
    fic.save_json(fic.franchisee_dir(slug) / "organization.json", org)
    return {"ok": True, "franchisee_slug": slug, "confidence_tier": tier}


_NAME_FILLER_WORDS = {
    "group", "restaurant", "restaurants", "holdings", "companies", "company",
    "co", "llc", "inc", "incorporated", "corp", "corporation", "enterprises",
    "management", "the", "of", "and", "&",
}


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _significant_tokens(name: str) -> set[str]:
    return {t for t in _norm_name(name).split() if t and t not in _NAME_FILLER_WORDS}


def find_similar_franchisees(candidate_name: str) -> list[dict]:
    """Real-but-cheap name-similarity check over the registry: exact
    normalized match, substring containment, or every significant
    (non-filler) token of the shorter name present in the longer one --
    e.g. 'ABC Group' vs 'ABC Restaurant Group' share the significant token
    {'abc'} and nothing else disqualifying, so they flag. Deliberately
    conservative on recall over precision: used only to flag a possible
    duplicate for human/importer review, never to auto-merge (spec
    section 3: 'must not automatically assume similarly named legal
    entities represent the same franchisee organization') -- a false-
    positive flag costs one review decision; a missed real duplicate
    costs a silently split organization record."""
    norm_candidate = _norm_name(candidate_name)
    if not norm_candidate:
        return []
    candidate_tokens = _significant_tokens(candidate_name)
    matches = []
    for entry in fic.load_registry().get("registry", []):
        existing_name = entry.get("display_name", "")
        norm_existing = _norm_name(existing_name)
        if not norm_existing:
            continue
        if norm_candidate == norm_existing or norm_candidate in norm_existing or norm_existing in norm_candidate:
            matches.append(entry)
            continue
        existing_tokens = _significant_tokens(existing_name)
        shorter, longer = sorted([candidate_tokens, existing_tokens], key=len)
        if shorter and shorter <= longer:
            matches.append(entry)
    return matches
