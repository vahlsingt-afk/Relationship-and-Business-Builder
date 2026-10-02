"""
franchisee_finder_common.py — storage primitives for Franchisee Finder.

Franchisee Finder is its own canonical RBB data domain (see
system/design/FRANCHISEE_FINDER_SPEC.md): an evidence-backed ownership-
resolution system for restaurant franchisee organizations -- not a simple
directory. It supersedes system/franchisee_hierarchy.json (a static,
one-time Franchise Times extract with no legal entities, no confidence
model, no evidence/provenance) as a continuously-refreshed domain; that
file is a seed/import source only (see migrate_franchisee_hierarchy.py),
never the schema this builds on.

Mirrors competitor_intelligence_common.py's shape deliberately (same
JSON/JSONL primitives, same org_dir()/registry-with-lock pattern) rather
than sharing code with it -- same reasoning that module gives for not
sharing with blue_sheets/_engine/common.py or account_research_common.py:
these are separately-governed systems, and duplication keeps each one
free to evolve its own schema.

Confidence model (spec section 6): confidence lives at the
ASSERTION/relationship level, not the franchisee-record level -- an
organization's headquarters, a specific brand's unit count, and an
ownership claim can each carry a different confidence. Every assertion
goes through assertion_field() below, the single shape every reader can
rely on. Phase 1 is read-only + seed-import only (see
system/design/FRANCHISEE_FINDER_SPEC.md section 16) -- no write/submission
endpoints exist yet; those are Phase 2's review workflow.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import datetime
import logging
import os
import re
import tempfile
from pathlib import Path

import slug_safety

_LOG = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent / "franchisee_finder"  # .../system/franchisee_finder

# Confidence thresholds are configurable, not hard-coded (spec section 8) --
# this is the one real default; callers that need a different threshold
# pass their own rather than this module growing a settings-read dependency.
DEFAULT_HIGH_CONFIDENCE_PCT = 85

VALID_OWNERSHIP_STRUCTURES = {
    "privately_held", "public", "pe_backed", "franchisee_of_franchisee", "unknown",
}

VALID_ASSERTION_STATUSES = {"confirmed", "inferred", "unresolved", "contradicted"}

VALID_PERSON_ROLE_CATEGORIES = {
    "ceo", "coo", "owner", "technology_leader", "operations_leader", "other",
}

# Schema-accommodation-only categories for future technology-buying
# intelligence (spec section 14) -- not populated in Phase 1, just reserved
# so the schema doesn't need a redesign when Phase 3 fills this in.
TECHNOLOGY_CATEGORIES = {
    "pos", "payments", "kds", "digital_menu_boards", "back_office",
    "inventory", "workforce", "loyalty", "digital_ordering", "hardware",
}


def org_dir(slug: str, *, create: bool = False) -> Path:
    # Defense-in-depth, same choke-point convention as competitor_dir()/
    # account_dir()/vendor_dir() -- every real caller derives slug via
    # _slugify() before reaching here, but this is the one place every
    # org-path lookup goes through.
    slug_safety.assert_safe_slug(slug, label="org_slug")
    d = ROOT / "organizations" / slug
    if not d.is_dir():
        if create:
            d.mkdir(parents=True, exist_ok=True)
            return d
        raise FileNotFoundError(f"No Franchisee Finder record for slug '{slug}' at {d}")
    return d


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_jsonl(path: Path):
    if not path.exists():
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def append_jsonl(path: Path, record) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False))
        f.write("\n")


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.date.today().isoformat()


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def assertion_field(
    value,
    *,
    confidence_pct: int | None,
    evidence_ids: list | None = None,
    source_url: str | None = None,
    as_of: str | None = None,
    status: str = "unresolved",
    confidence_rationale: str | None = None,
    known_conflicts: str | None = None,
    last_verified: str | None = None,
) -> dict:
    """One assertion, shaped so confidence/evidence/provenance travel with
    the specific claim rather than the whole record (spec section 6) --
    e.g. "headquarters -> Dallas, TX (95%)" and "operates 84 Taco Bells
    (91%)" on the same org each carry their own confidence. Never forces a
    confidence value when the research genuinely doesn't support one --
    confidence_pct=None is valid and means "not yet assessed", distinct
    from a low-but-real score."""
    if status not in VALID_ASSERTION_STATUSES:
        raise ValueError(f"status must be one of {sorted(VALID_ASSERTION_STATUSES)}, got {status!r}")
    if confidence_pct is not None and not (0 <= confidence_pct <= 100):
        raise ValueError(f"confidence_pct must be 0-100 or None, got {confidence_pct!r}")
    field = {
        "value": value,
        "confidence_pct": confidence_pct,
        "status": status,
        "evidence_ids": evidence_ids or [],
        "as_of": as_of or today(),
        "last_verified": last_verified or as_of or today(),
    }
    if source_url:
        field["source_url"] = source_url
    if confidence_rationale:
        field["confidence_rationale"] = confidence_rationale
    if known_conflicts:
        field["known_conflicts"] = known_conflicts
    return field


def brand_relationship(
    brand_name: str,
    unit_count: int | None,
    *,
    brand_entity_id: str | None = None,
    unit_count_basis: str | None = None,
    confidence_pct: int | None = None,
    evidence_ids: list | None = None,
    source_url: str | None = None,
    as_of: str | None = None,
    status: str = "unresolved",
) -> dict:
    """A franchisee's relationship to one brand (spec sections 3, 9) --
    unit_count is itself an assertion (confidence + evidence), and `history`
    carries point-in-time prior values so a later re-research never
    silently overwrites an earlier figure (spec section 9: "Never simply
    overwrite meaningful historical intelligence")."""
    as_of = as_of or today()
    unit_count_assertion = assertion_field(
        unit_count, confidence_pct=confidence_pct, evidence_ids=evidence_ids,
        source_url=source_url, as_of=as_of, status=status,
    )
    return {
        "brand_name": brand_name,
        "brand_entity_id": brand_entity_id,  # link to ecosystem_intelligence.json's brand-<slug>, if resolved
        "unit_count": unit_count_assertion,
        "unit_count_basis": unit_count_basis,
        "history": [
            {"date": as_of, "unit_count": unit_count, "unit_count_basis": unit_count_basis, "note": None},
        ],
    }


def record_unit_count_change(relationship: dict, new_unit_count: int, *, date: str | None = None,
                              note: str | None = None, confidence_pct: int | None = None,
                              evidence_ids: list | None = None, source_url: str | None = None) -> dict:
    """Append a new unit-count observation to a brand_relationship's history
    and promote it to the current `unit_count` assertion -- never overwrites
    `history`, only appends (spec section 9)."""
    date = date or today()
    relationship = dict(relationship)
    history = list(relationship.get("history") or [])
    history.append({"date": date, "unit_count": new_unit_count, "unit_count_basis": relationship.get("unit_count_basis"), "note": note})
    relationship["history"] = history
    relationship["unit_count"] = assertion_field(
        new_unit_count,
        confidence_pct=confidence_pct if confidence_pct is not None else relationship.get("unit_count", {}).get("confidence_pct"),
        evidence_ids=evidence_ids or relationship.get("unit_count", {}).get("evidence_ids"),
        source_url=source_url or relationship.get("unit_count", {}).get("source_url"),
        as_of=date,
        status=relationship.get("unit_count", {}).get("status", "unresolved"),
    )
    return relationship


def _empty_organization_json(slug: str, display_name: str) -> dict:
    return {
        "org_id": f"ff-{slug}",
        "org_slug": slug,
        "display_name": display_name,
        "aliases": [],
        # Links to a real multi_brand_franchisee_operator entity in
        # ecosystem_intelligence.json, when one exists for this organization
        # (see migrate_franchisee_hierarchy.py) -- that graph already carries
        # real operates->brand edges and leadership for ~47 operators from a
        # separate 2026-09-27 ingest; this field cross-references it rather
        # than duplicating its identity. None when no such entity exists.
        "linked_graph_entity_id": None,
        "headquarters": None,          # assertion_field()-shaped {city/state string, confidence...}
        "registered_address": None,    # kept separate from headquarters per spec section 11 -- they can differ
        "ownership": {
            "structure": None,         # assertion_field()-shaped, value from VALID_OWNERSHIP_STRUCTURES
            "owners": [],              # list of {name, relationship_type, ...assertion_field() keys}
        },
        "legal_entities": [],          # list of {name, relationship_type, ...assertion_field() keys}
        "brand_relationships": [],     # list of brand_relationship()-shaped dicts
        "geographic_footprint": [],    # list of assertion_field()-shaped entries, value = state/region string
        "people": [],                  # list of {name, title, role_category, ...assertion_field() keys, public_contact_info}
        "technology": {},              # schema accommodation only (spec section 14) -- empty in Phase 1
        "confidence_thresholds": {"high_confidence_pct": DEFAULT_HIGH_CONFIDENCE_PCT},
        "research_status": {
            "last_researched": None,
            "next_scheduled_review": None,
            "overall_profile_quality": "unresearched",
        },
        "source_system": "manual_seed_import",  # or "chatgpt_deep_research" / "user_submission" once Phase 2 exists
        "total_identified_units": 0,
        "template_version": "franchisee-finder-v1",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


def create_organization_shell(slug: str, display_name: str) -> Path:
    """Creates a brand-new, empty Franchisee Finder record. Cheap and
    ungated, same philosophy as Competitor Intelligence's
    create_competitor_shell() -- tracking a franchisee organization is a
    research act, not a commitment the way createBlueSheetAccount is."""
    d = org_dir(slug, create=True)
    save_json(d / "organization.json", _empty_organization_json(slug, display_name))
    (d / "evidence.jsonl").touch()
    return d


def load_organization(slug: str) -> dict:
    d = org_dir(slug)
    return {
        "organization": load_json(d / "organization.json"),
        "evidence": load_jsonl(d / "evidence.jsonl"),
    }


def save_organization(slug: str, organization: dict) -> None:
    d = org_dir(slug, create=True)
    organization = dict(organization)
    organization["updated_at"] = now_iso()
    save_json(d / "organization.json", organization)


def add_evidence(slug: str, evidence: dict) -> dict:
    """Append one evidence record to an organization's append-only ledger.
    Returns the record with a generated evidence_id if one wasn't supplied,
    so callers can reference it from assertion_field(evidence_ids=[...])."""
    d = org_dir(slug, create=True)
    existing = load_jsonl(d / "evidence.jsonl")
    evidence = dict(evidence)
    evidence.setdefault("evidence_id", f"ff-ev-{slug}-{len(existing) + 1:04d}")
    evidence.setdefault("recorded_at", now_iso())
    append_jsonl(d / "evidence.jsonl", evidence)
    return evidence


def registry_path() -> Path:
    return ROOT / "_portfolio" / "franchisee_registry.json"


def save_json_atomic(path: Path, data) -> None:
    """Write via temp-file-plus-replace -- never exposes a partially written
    file to a concurrent reader. Same discipline as competitor_intelligence_
    common.py's save_json_atomic(), for the one shared-state file (the
    registry) multiple callers can race on."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _registry_lock_path() -> Path:
    p = ROOT / "_portfolio" / ".registry.lock"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@contextlib.contextmanager
def _registry_lock():
    lock_path = _registry_lock_path()
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def load_registry() -> dict:
    p = registry_path()
    if not p.exists():
        return {"registry": []}
    return load_json(p)


def save_registry(reg: dict) -> None:
    save_json_atomic(registry_path(), reg)


def register_organization(slug: str, display_name: str) -> None:
    with _registry_lock():
        reg = load_registry()
        entries = reg.setdefault("registry", [])
        existing = next((e for e in entries if e.get("org_slug") == slug), None)
        if existing is None:
            entries.append({
                "org_slug": slug,
                "display_name": display_name,
                "first_tracked_at": now_iso(),
                "last_updated_at": now_iso(),
            })
        else:
            existing["display_name"] = display_name
            existing["last_updated_at"] = now_iso()
        save_registry(reg)
