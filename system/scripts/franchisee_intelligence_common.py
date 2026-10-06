"""
franchisee_intelligence_common.py — Franchisee Finder, Phase 1 (2026-10-02).

Storage primitives for the new canonical `franchisee_finder` data domain,
per system/design/FRANCHISEE_FINDER_SPEC.md. Deliberately mirrors
competitor_intelligence_common.py's shape (same JSON/JSONL primitives,
same <domain>_dir()/create_<x>_shell() pattern, same locked-registry
discipline hardened after RB-DEFECT-071) rather than importing from it --
same reasoning competitor_intelligence_common.py itself gives for not
sharing with account_research_common.py: these are separately-governed
systems, and duplication keeps each free to evolve its own schema.

Field-shape decision (Phase 1, deliberately simple): every Hunter-sourced
assertion -- legal_entities, brand_relationships, leadership,
operating_geography, growth_notes, technology_relationships -- is a list
of extended_field()-shaped leaves with a single human-readable STRING
value (e.g. "Taco Bell -- 84 units -- FDD Item 20, as of 2026-Q3"), the
same convention competitor_intelligence.py already uses for `products`/
`key_customers`. This matches the uniform "field + string value" shape
every Hunter finding already carries (see hunter_research_packet.schema.
json) and ships a real, working Phase 1 fast. It sacrifices exact
machine-queryable unit-count arithmetic across brands -- Phase 2 (per the
spec's own phasing: "Entity Resolution and People") is where that
structured upgrade belongs, once real research volume shows it's worth
the added complexity. `total_identified_units` is the one exception: a
direct numeric scalar, because "how many stores does this operator run"
is a named Phase 1 goal on its own, not something that should require
parsing prose.

Confidence model: assertion-level, per spec section 6 -- every leaf
carries its own confidence, never a single franchisee-record-level score.
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


def franchisee_dir(slug: str, *, create: bool = False) -> Path:
    slug_safety.assert_safe_slug(slug, label="franchisee_slug")
    d = ROOT / "organizations" / slug
    if not d.is_dir():
        if create:
            d.mkdir(parents=True, exist_ok=True)
            return d
        raise FileNotFoundError(f"No franchisee intelligence folder for slug '{slug}' at {d}")
    return d


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def save_json_atomic(path: Path, data) -> None:
    """Write via temp-file-plus-replace -- never exposes a partially
    written file to a concurrent reader. Used for the registry
    specifically, same discipline as competitor_intelligence_common.py's
    save_json_atomic() after RB-DEFECT-071 (a real corrupted-registry
    incident from unlocked concurrent writes); per-organization
    organization.json writes have no shared-state race and stay on
    save_json()."""
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


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-") or "org"


# Evidence categories for the append-only evidence.jsonl log (free-text
# sourced notes), parallel to competitor_intelligence_common's
# VALID_EVIDENCE_CATEGORIES. "discovery" is unique to this domain: the
# assertion that this organization franchises a given brand AT ALL, which
# itself carries its own confidence separate from any field about it --
# per spec section 4, franchisee lists are hard to get, so the discovery
# claim itself is real intelligence, not a given.
VALID_EVIDENCE_CATEGORIES = {
    "headquarters", "registered_address", "ownership", "legal_entity",
    "brand_relationship", "unit_count", "leadership", "sales_growth",
    "operating_geography", "technology", "discovery", "other",
}

# List-shaped fields, each a list of extended_field()-shaped leaves with a
# single string value (see module docstring for why).
EXTENDED_PROFILE_FIELDS = (
    "legal_entities", "brand_relationships", "leadership",
    "operating_geography", "growth_notes", "technology_relationships",
)

# Scalar (single synthesized value) fields, parallel to competitor_
# intelligence_common.py's EXTENDED_SCALAR_FIELDS. "registered_address" is
# kept separate from "headquarters" per spec section 11 (operating HQ and
# the legal/registered address are not the same fact and must not be
# collapsed into one).
EXTENDED_SCALAR_FIELDS = (
    "headquarters", "registered_address", "ownership", "sales_estimate",
    "total_identified_units",
)

# hierarchy_level, informational only -- never auto-inferred from unit
# count alone; set from real evidence (an org with 1 unit in 3 brands is
# still "multi_brand_franchisee_group", not "single_unit").
VALID_HIERARCHY_LEVELS = {
    "unknown", "single_unit", "multi_unit_single_brand",
    "multi_brand_franchisee_group",
}


def extended_field(
    value,
    *,
    status: str = "confirmed",
    evidence_ids: list | None = None,
    confidence: str = "medium",
    as_of: str | None = None,
    last_reviewed_by: str = "system:franchisee_intelligence_common",
    source_url: str | None = None,
    finding_type: str | None = None,
    source_owner: str | None = None,
    source_type: str | None = None,
    published_at: str | None = None,
    deployment_scope: str | None = None,
    is_vendor_claim: bool | None = None,
    is_inference: bool | None = None,
    limitations_or_conflicts: str | None = None,
) -> dict:
    leaf = {
        "value": value, "status": status, "evidence_ids": evidence_ids or [],
        "confidence": confidence, "as_of": as_of, "scope": "franchisee_organization",
        "last_reviewed_by": last_reviewed_by,
    }
    if source_url:
        leaf["source_url"] = source_url
    if finding_type:
        leaf["finding_type"] = finding_type
    if source_owner:
        leaf["source_owner"] = source_owner
    if source_type:
        leaf["source_type"] = source_type
    if published_at:
        leaf["published_at"] = published_at
    if deployment_scope:
        leaf["deployment_scope"] = deployment_scope
    if is_vendor_claim is not None:
        leaf["is_vendor_claim"] = is_vendor_claim
    if is_inference is not None:
        leaf["is_inference"] = is_inference
    if limitations_or_conflicts:
        leaf["limitations_or_conflicts"] = limitations_or_conflicts
    return leaf


def _empty_franchisee_json(slug: str, display_name: str) -> dict:
    scalar_default = {
        "value": None, "status": "not_yet_researched", "evidence_ids": [],
        "confidence": "unknown", "as_of": None, "scope": "franchisee_organization", "last_reviewed_by": None,
    }
    return {
        "franchisee_id": f"fran-{slug}",
        "franchisee_slug": slug,
        "display_name": display_name,
        "aliases": [],
        "hierarchy_level": "unknown",
        "legal_entities": [],
        "brand_relationships": [],
        "leadership": [],
        "operating_geography": [],
        "growth_notes": [],
        "technology_relationships": [],  # Phase 3 schema accommodation (spec section 14); not populated in Phase 1
        "headquarters": dict(scalar_default),
        "registered_address": dict(scalar_default),
        "ownership": dict(scalar_default),
        "sales_estimate": dict(scalar_default),
        "total_identified_units": dict(scalar_default),
        # How this org was first identified -- per spec section 4/11, the
        # discovery itself is uncertain intelligence (franchisee lists are
        # hard to get), not a given once an org shell exists.
        "source_discovery": {
            "method": None, "discovered_from_brand": None,
            "confidence": "unknown", "as_of": None, "source_url": None,
        },
        "last_evidence_date": None,
        "last_researched": None,
        "next_scheduled_review": None,
        "confidence_tier": "low",  # low -> monthly refresh; high -> quarterly (spec section 8)
        "template_version": "franchisee-intelligence-v1",
        "updated_at": now_iso(),
    }


def create_franchisee_shell(slug: str, display_name: str) -> Path:
    """Cheap, ungated shell creation -- same philosophy as competitor_
    intelligence's create_competitor_shell(). Callers (Hunter's discovery
    importer, a human) create a shell when real evidence first names an
    organization; this function never guesses whether one should exist."""
    d = franchisee_dir(slug, create=True)
    save_json(d / "organization.json", _empty_franchisee_json(slug, display_name))
    (d / "evidence.jsonl").touch()
    return d


def load_franchisee(slug: str) -> dict:
    d = franchisee_dir(slug)
    return {
        "organization": load_json(d / "organization.json"),
        "evidence": load_jsonl(d / "evidence.jsonl"),
    }


def get_extended_profile(organization: dict) -> dict:
    """Read-time backward-compat default-filling, same pattern as
    competitor_intelligence_common.get_extended_profile() -- never
    KeyErrors on a record missing a field a later schema revision added."""
    result = dict(organization)
    for f in EXTENDED_PROFILE_FIELDS:
        if f not in result:
            result[f] = []
    for f in EXTENDED_SCALAR_FIELDS:
        if f not in result:
            result[f] = {
                "value": None, "status": "not_yet_researched", "evidence_ids": [],
                "confidence": "unknown", "as_of": None, "scope": "franchisee_organization", "last_reviewed_by": None,
            }
    return result


def registry_path() -> Path:
    return ROOT / "_portfolio" / "franchisee_registry.json"


def _registry_lock_path() -> Path:
    p = ROOT / "_portfolio" / ".registry.lock"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@contextlib.contextmanager
def _registry_lock():
    """Cross-process exclusive lock around the registry's read-modify-
    write critical section -- RB-DEFECT-071 happened to competitor_
    registry.json from exactly the unlocked version of this; this domain
    gets the hardened pattern from day one instead of waiting for its own
    incident."""
    lock_path = _registry_lock_path()
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _rebuild_registry_from_disk() -> dict:
    known: dict[str, dict] = {}
    p = registry_path()
    if p.exists():
        try:
            raw = p.read_text(encoding="utf-8")
            recovered, _ = json.JSONDecoder().raw_decode(raw)
            for e in recovered.get("registry", []):
                slug = e.get("franchisee_slug")
                if slug:
                    known[slug] = e
        except Exception:
            pass

    orgs_dir = ROOT / "organizations"
    if orgs_dir.is_dir():
        for d in sorted(orgs_dir.iterdir()):
            if not d.is_dir() or d.name in known:
                continue
            org_path = d / "organization.json"
            if not org_path.exists():
                continue
            try:
                data = json.loads(org_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            ts = data.get("updated_at") or now_iso()
            known[d.name] = {
                "franchisee_slug": d.name,
                "display_name": data.get("display_name") or d.name,
                "first_tracked_at": ts,
                "last_updated_at": ts,
            }
    return {"registry": sorted(known.values(), key=lambda e: e.get("first_tracked_at", ""))}


def load_registry() -> dict:
    p = registry_path()
    if not p.exists():
        return {"registry": []}
    try:
        return load_json(p)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        _LOG.warning("franchisee_registry.json unreadable (%s) -- self-healing from disk", exc)
        rebuilt = _rebuild_registry_from_disk()
        try:
            save_json_atomic(p, rebuilt)
        except OSError:
            pass
        return rebuilt


def save_registry(reg: dict) -> None:
    save_json_atomic(registry_path(), reg)


def register_franchisee(slug: str, display_name: str) -> None:
    with _registry_lock():
        reg = load_registry()
        entries = reg.setdefault("registry", [])
        existing = next((e for e in entries if e.get("franchisee_slug") == slug), None)
        if existing is None:
            entries.append({
                "franchisee_slug": slug,
                "display_name": display_name,
                "first_tracked_at": now_iso(),
                "last_updated_at": now_iso(),
            })
        else:
            existing["last_updated_at"] = now_iso()
        save_registry(reg)
