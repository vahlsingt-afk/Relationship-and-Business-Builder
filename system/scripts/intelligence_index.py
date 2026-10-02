#!/usr/bin/env python3
"""
intelligence_index.py — unified "what do we have on X, and where" index.

2026-08-27, built after a real, same-day failure: a McDonald's Background
Brief request silently missed system/account_intelligence/'s real 74KB of
Todd's own prior account research (a 433-line Strategic Account Plan, an
executive summary, and two call-intelligence writeups) -- found only by a
brute-force grep, not through any code path. Each intelligence-bearing
subsystem (system/account_intelligence/, system/artifacts/registry.json,
blue_sheets/, system/account_research/, system/ecosystem_intelligence.json)
has its own bespoke discovery
mechanism, and nothing answered "what exists on this brand, across all of
them" in one place.

Design, per Todd's explicit direction (2026-08-27): an INDEX, not a LOG.

  - The INDEX (system/intelligence_index.json) answers "what exists, where
    to find it, right now." One entry per resource, keyed by path. Creating
    a NEW intelligence document/record registers an entry here.
  - A separate append-only UPDATE LOG (system/intelligence_index_updates
    .jsonl) records that an EXISTING resource changed -- an update does NOT
    get a new index entry (its path didn't change), but the event is not
    silently dropped either. Same "current state vs. history" split already
    used elsewhere in this codebase (evidence.jsonl + change_log.jsonl in
    blue_sheets/_engine).

The index is a real persisted file, kept current by explicit
register_document()/log_update() calls at each system's actual creation/
update points -- not a live directory scan on every query (too slow to call
from every discovery step). rebuild_index() remains available as a full-
rescan safety net: system/account_intelligence/ specifically has no single
owning creation function today (files have historically been dropped there
ad hoc, by Claude Code sessions or Todd directly) -- create_account_
intelligence_doc() below is the new sanctioned way to add one AND registers
it automatically, but rebuild_index() exists so an undocumented future
Write-tool file drop still eventually gets discovered rather than silently
missed again, the way this bug happened the first time.

CLI:
    python3 system/scripts/intelligence_index.py rebuild
    python3 system/scripts/intelligence_index.py find "McDonald's"
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import intelligence_triage as itriage  # noqa: E402

INDEX_PATH = core.SYSTEM_DIR / "intelligence_index.json"
UPDATE_LOG_PATH = core.SYSTEM_DIR / "intelligence_index_updates.jsonl"
ACCOUNT_INTELLIGENCE_DIR = core.SYSTEM_DIR / "account_intelligence"
ECOSYSTEM_PATH = core.SYSTEM_DIR / "ecosystem_intelligence.json"

VALID_RESOURCE_TYPES = {
    "account_intelligence_doc", "account_research_record", "account_research_brief",
    "blue_sheet_account", "master_account_plan", "artifact", "uploaded_source_document",
    # RB-2026-09-07: the customer-side sales-artifact suite (see
    # system/scripts/artifact_vault_common.py) -- account_plan is wired in
    # now; green_sheet/win_plan/rfp_response_plan reserved for the same
    # suite's remaining artifact types, added when each is actually built.
    "account_plan", "green_sheet", "win_plan", "rfp_response_plan",
    # RB-2026-09-07: competitive-side, phase 1 -- persists what
    # competitive_landscape.py/competitor_intelligence.py already compute
    # correctly but previously threw away (see battle_card.py,
    # competitive_brief.py).
    "battle_card", "competitive_brief",
    # RB-2026-09-07: competitive-side, phase 2 -- a genuinely new,
    # account-scoped artifact (see vendor_engagement_analysis.py), not a
    # persistence layer over an existing computation like the two above.
    "vendor_engagement_analysis",
    # RB-2026-09-08: relationship-side, phase 1. relationship_card is a
    # governed snapshot of Todd's own hand-authored system/cards/<id>.md
    # (never generated content); inner_circle is a genuinely new, fully
    # computed roll-up over rc_tier == "inner" contacts.
    "relationship_card", "inner_circle",
    # RB-2026-09-08: relationship-side, phase 2. referral_network_overview
    # is a governed snapshot of Todd's own hand-authored intro_brokers.md
    # (never generated); referral_network_analysis is a genuinely new,
    # per-target persisted view over intro_engine.py's already-computed
    # find_intro_paths() output (see referral_network.py).
    "referral_network_overview", "referral_network_analysis",
    # RB-2026-09-08: relationship-side, phase 3 (final). relationship_plan
    # is a genuinely new, caller-supplied judgment document (a forward-
    # looking per-person goal) -- unlike the other 3 relationship-side
    # artifacts, gated by user_authorization_quote and vault-only, never
    # written onto baseline_index.json (see relationship_plan.py).
    "relationship_plan",
    # RB-2026-09-10: canonical Top 1500 restaurant-brand universe. Without
    # these entries, a brand with real ecosystem relationships could appear
    # nonexistent merely because it had no separate brief or account artifact.
    "ecosystem_brand",
    # RB-2026-09-25: competitive-side, phase 3 -- Value Wedge, a genuinely
    # new, competitor-scoped "why Genius wins" document (see
    # value_wedge.py), sibling to battle_card/competitive_brief above.
    "value_wedge",
    # RB-2026-09-28: customer-side, first artifact type scoped to a
    # discrete per-opportunity event rather than a single evolving
    # per-account document (see win_loss_review.py).
    "win_loss_review",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_for_tokens(name: str) -> str:
    """Real bug found 2026-08-27: itriage._tokens() keeps apostrophes
    inline ("mcdonald's" as one token), but real filenames/aliases drop
    them entirely ("mcdonalds") -- without this, "McDonald's" never
    matches its own real files. Same failure class as the earlier
    diacritic bug, different punctuation."""
    return name.replace("'", "").replace("’", "")


def _load_index() -> dict:
    if not INDEX_PATH.exists():
        return {"contract": "rb_intelligence_index_v1", "generated_at": None, "entries": []}
    try:
        return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"contract": "rb_intelligence_index_v1", "generated_at": None, "entries": []}


def _save_index(index: dict) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def register_document(
    entity: str, resource_type: str, title: str, path: str, *,
    source_system: str, created_at: Optional[str] = None, notes: Optional[str] = None,
) -> dict:
    """Upserts one index entry, keyed by path. Call this at the moment a
    NEW intelligence document/record is created. Calling it again on the
    same path (e.g. a caller that isn't sure whether this is the first
    time) safely refreshes metadata rather than duplicating -- but callers
    that are updating existing content, not creating something new, should
    call log_update() instead per the index/log split above."""
    if resource_type not in VALID_RESOURCE_TYPES:
        raise ValueError(f"invalid resource_type: {resource_type}")
    index = _load_index()
    entry = {
        "entity": entity,
        "entity_tokens": sorted(itriage._tokens(_normalize_for_tokens(entity))),
        "resource_type": resource_type,
        "title": title,
        "path": path,
        "source_system": source_system,
        "created_at": created_at or _now_iso(),
        "registered_at": _now_iso(),
        "notes": notes,
    }
    index["entries"] = [e for e in index.get("entries", []) if e.get("path") != path]
    index["entries"].append(entry)
    index["generated_at"] = _now_iso()
    _save_index(index)
    return entry


def log_update(entity: str, path: str, *, resource_type: Optional[str] = None, note: str = "") -> dict:
    """Append-only: records that an EXISTING resource changed. Never
    touches the index itself -- the resource's path/location didn't
    change, so its index entry (registered at creation) is still correct.
    This is the 'updates should be logged, not indexed' half of the
    design."""
    record = {
        "logged_at": _now_iso(), "entity": entity, "path": path,
        "resource_type": resource_type, "event": "updated", "note": note,
    }
    UPDATE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(UPDATE_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def find(name: str) -> list[dict]:
    """Word-boundary-safe match against every index entry's entity_tokens
    -- the single shared matching path every discovery step (Background
    Brief, and anything built after it) should call instead of growing its
    own bespoke scan."""
    index = _load_index()
    name_tokens = set(itriage._tokens(_normalize_for_tokens(name)))
    if not name_tokens:
        return []
    name_lower = _normalize_for_tokens(name).lower()
    scored_matches = []
    for entry in index.get("entries", []):
        entry_tokens = set(entry.get("entity_tokens") or [])
        entity_normalized = _normalize_for_tokens(entry.get("entity", "")).strip().lower()
        alias_normalized = {
            _normalize_for_tokens(str(alias)).strip().lower()
            for alias in (entry.get("aliases") or []) if alias
        }
        exact_name_match = name_lower.strip() == entity_normalized or name_lower.strip() in alias_normalized
        overlap = len(entry_tokens & name_tokens)
        if entry_tokens & name_tokens:
            # Exact canonical/alias matches must appear before partial token
            # matches (e.g. Golden Chick before Golden Corral or other chicken
            # brands), while preserving broad topic-query recall.
            score = (1000 if exact_name_match else 0) + overlap * 10
            score += int(100 * overlap / max(len(name_tokens), 1))
            scored_matches.append((score, entry))
            continue
        # Fallback for entries indexed before entity_tokens existed, or a
        # short-token entity name -- reuse the same word-boundary-safe
        # substring check used elsewhere (itriage._term_in_text).
        entity_lower = _normalize_for_tokens(entry.get("entity", "")).lower()
        if entity_lower and any(
            itriage._term_in_text(t, entity_lower, entry_tokens) for t in name_tokens
        ):
            scored_matches.append((1000 if exact_name_match else 1, entry))
    scored_matches.sort(key=lambda item: (-item[0], item[1].get("entity", "").lower()))
    return [entry for _, entry in scored_matches]


# ---------------------------------------------------------------------------
# Creation helper for system/account_intelligence/ -- the one source with no
# existing owning module/creation function.
# ---------------------------------------------------------------------------

def create_account_intelligence_doc(entity: str, filename_slug: str, content: str, *, doc_type: str = "account_intelligence") -> dict:
    """Writes a new file into system/account_intelligence/ and registers it
    in the index in the same call -- the sanctioned way to add one going
    forward, so a new document can't be created without also becoming
    discoverable. filename_slug should NOT include the date prefix or
    .md extension (e.g. "mcdonalds-payments-update"); today's date is
    prepended automatically, matching the existing real-file convention."""
    date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filename = f"{date_prefix}-{filename_slug}.md"
    path = ACCOUNT_INTELLIGENCE_DIR / filename
    if path.exists():
        raise FileExistsError(f"{path} already exists -- use log_update() for a change to existing intelligence, not a new file with a colliding name")
    ACCOUNT_INTELLIGENCE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    rel_path = str(path.relative_to(core.PROJECT_DIR))
    entry = register_document(
        entity, "account_intelligence_doc", filename, rel_path,
        source_system="account_intelligence", created_at=date_prefix,
        notes=f"doc_type={doc_type}",
    )
    return entry


# ---------------------------------------------------------------------------
# Full-rescan safety net
# ---------------------------------------------------------------------------

def _rescan_account_intelligence() -> list[dict]:
    entries = []
    if not ACCOUNT_INTELLIGENCE_DIR.is_dir():
        return entries
    for path in ACCOUNT_INTELLIGENCE_DIR.glob("*.md"):
        stem = path.stem
        date_prefix = stem[:10]
        date = date_prefix if len(date_prefix) == 10 and date_prefix[4] == "-" else None
        rest = stem[11:] if date else stem
        # Real bug found while building this: guessing a single "entity"
        # from the first filename token fails for a real file like
        # "2026-08-11-jeff-coffland-mcdonalds-intelligence.md", which leads
        # with a person's name, not the brand -- it would never match a
        # "McDonald's" query. Fix: index EVERY meaningful filename token,
        # not a single guessed entity, so find() matches on any of them
        # (brand, person, or topic). "entity" stays a readable label for
        # display only.
        display = rest.replace("-", " ").title() if rest else stem
        entries.append({
            "entity": display,
            "entity_tokens": sorted(itriage._tokens(rest or stem)),
            "resource_type": "account_intelligence_doc",
            "title": path.name,
            "path": str(path.relative_to(core.PROJECT_DIR)),
            "source_system": "account_intelligence",
            "created_at": date,
        })
    return entries


def _rescan_artifacts() -> list[dict]:
    entries = []
    reg_path = core.SYSTEM_DIR / "artifacts" / "registry.json"
    if not reg_path.exists():
        return entries
    try:
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return entries
    for art in reg.get("artifacts") or []:
        entries.append({
            "entity": art.get("entity") or art.get("name") or art.get("artifact_id"),
            "resource_type": "artifact",
            "title": art.get("name") or art.get("artifact_id"),
            "path": f"system/artifacts/registry.json#{art.get('artifact_id')}",
            "source_system": "artifacts",
            "created_at": art.get("freshness_date"),
        })
    return entries


def _rescan_customers_prospects() -> list[dict]:
    """RB-2026-09-06: replaces the two prior separate rescans
    (_rescan_blue_sheets / _rescan_account_research) now that both systems
    share one storage tree (customers_prospects/) and one registry, per the
    3-store unification plan. resource_type/source_system keep the same
    pre-unification vocabulary ("blue_sheet_account" vs.
    "account_research_record") -- branched on the registry's own
    engagement_tier field -- since nothing downstream needed to change,
    only which real path/registry this reads from."""
    entries = []
    reg_path = core.PROJECT_DIR / "customers_prospects" / "_portfolio" / "customers_prospects_registry.json"
    if not reg_path.exists():
        return entries
    try:
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return entries
    for e in reg.get("registry") or []:
        slug = (e.get("account_id") or "").removeprefix("acct-")
        is_active = e.get("engagement_tier") == "active_engagement"
        entries.append({
            "entity": (e.get("aliases") or [slug])[0] if (e.get("aliases") or [slug]) else slug,
            "resource_type": "blue_sheet_account" if is_active else "account_research_record",
            "title": f"{'Blue Sheet' if is_active else 'Account Research'}: {slug}",
            "path": f"customers_prospects/accounts/{slug}",
            "source_system": "blue_sheets" if is_active else "account_research",
            "created_at": e.get("activation_date") or e.get("last_review_date"),
        })
        brief_dir = core.PROJECT_DIR / "customers_prospects" / "accounts" / slug / "briefs" / "current"
        if (brief_dir / "Background_Brief.md").exists():
            entries.append({
                "entity": (e.get("aliases") or [slug])[0] if (e.get("aliases") or [slug]) else slug,
                "resource_type": "account_research_brief",
                "title": f"Background Brief: {slug}",
                "path": str((brief_dir / "Background_Brief.md").relative_to(core.PROJECT_DIR)),
                "source_system": "account_research",
                "created_at": e.get("last_review_date"),
            })
    return entries


def _rescan_top_1500_brands() -> list[dict]:
    """Index every ranked restaurant brand from the canonical ecosystem.

    This is intentionally one lightweight pointer per brand, not a copy of
    the ecosystem graph. Relationship counts and vendor/category names make
    a hit useful enough to route the caller to queryEngine's ecosystem module
    for the full current record.
    """
    if not ECOSYSTEM_PATH.exists():
        return []
    try:
        graph = json.loads(ECOSYSTEM_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []

    relationships_by_brand: dict[str, list[dict]] = {}
    for rel in graph.get("relationships") or []:
        brand_id = (
            rel.get("from_entity_id") or rel.get("from_entity")
            or rel.get("source_entity_id") or rel.get("brand_id")
        )
        if brand_id:
            relationships_by_brand.setdefault(str(brand_id), []).append(rel)

    brand_entities = [
        e for e in (graph.get("entities") or [])
        if e.get("entity_type") == "brand" and e.get("subtype") == "restaurant_brand"
    ]
    current_years = []
    for entity in brand_entities:
        attrs = entity.get("attributes") or {}
        try:
            rank_number = int(float(attrs.get("rank")))
            latest_year = int(float(attrs.get("technomic_latest_year")))
        except (TypeError, ValueError):
            continue
        if 1 <= rank_number <= 1500:
            current_years.append(latest_year)
    if not current_years:
        return []
    current_year = max(current_years)

    entries = []
    for entity in brand_entities:
        attrs = entity.get("attributes") or {}
        rank = attrs.get("rank")
        try:
            rank_number = int(float(rank))
            latest_year = int(float(attrs.get("technomic_latest_year")))
        except (TypeError, ValueError):
            continue
        if not 1 <= rank_number <= 1500 or latest_year != current_year:
            continue

        brand_id = str(entity.get("id") or "")
        name = str(entity.get("name") or brand_id)
        aliases = [str(a) for a in (entity.get("aliases") or []) if a]
        rels = relationships_by_brand.get(brand_id, [])
        vendors = sorted({
            str(r.get("to_entity_id") or r.get("to_entity") or r.get("target_entity_id") or r.get("vendor_id"))
            for r in rels
            if r.get("to_entity_id") or r.get("to_entity") or r.get("target_entity_id") or r.get("vendor_id")
        })
        categories = sorted({str(r.get("category")) for r in rels if r.get("category")})
        token_text = " ".join([name, brand_id, *aliases])
        notes = f"Top 1500 rank={rank_number}; canonical_relationships={len(rels)}"
        if vendors:
            notes += f"; vendors={','.join(vendors)}"
        if categories:
            notes += f"; categories={','.join(categories)}"
        entries.append({
            "entity": name,
            "entity_tokens": sorted(itriage._tokens(_normalize_for_tokens(token_text))),
            "resource_type": "ecosystem_brand",
            "title": f"Top 1500 Brand: {name}",
            "path": f"system/ecosystem_intelligence.json#{brand_id}",
            "source_system": "ecosystem",
            "created_at": entity.get("created_at"),
            "brand_id": brand_id,
            "rank": rank_number,
            "aliases": aliases,
            "canonical_relationship_count": len(rels),
            "notes": notes,
        })
    return entries


def rebuild_index() -> dict:
    """Full-rescan safety net across all known sources. Safe to re-run any
    time -- rewrites the whole index from current on-disk state, so it
    also self-heals if a real creation event was ever missed."""
    raw_entries = (
        _rescan_account_intelligence() + _rescan_artifacts()
        + _rescan_customers_prospects() + _rescan_top_1500_brands()
    )
    entries = []
    for e in raw_entries:
        e = dict(e)
        if "entity_tokens" not in e:
            e["entity_tokens"] = sorted(itriage._tokens(_normalize_for_tokens(e["entity"] or "")))
        e["registered_at"] = _now_iso()
        e.setdefault("notes", None)
        entries.append(e)
    index = {"contract": "rb_intelligence_index_v1", "generated_at": _now_iso(), "entries": entries}
    _save_index(index)
    return index


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("rebuild")
    p_find = sub.add_parser("find")
    p_find.add_argument("name")
    args = parser.parse_args()

    if args.cmd == "rebuild":
        index = rebuild_index()
        print(json.dumps({"entry_count": len(index["entries"])}, indent=2))
    elif args.cmd == "find":
        print(json.dumps(find(args.name), indent=2, default=str))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
