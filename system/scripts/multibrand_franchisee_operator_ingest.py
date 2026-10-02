#!/usr/bin/env python3
"""
multibrand_franchisee_operator_ingest.py — RB-2026-09-27.

Ingests the "RBB Multi-Brand Franchisee Operators" dataset (47 qualified
multi-brand restaurant franchisee operators -- e.g. Flynn Group, Sun
Holdings -- each operating 100+ locations across 2+ restaurant brands)
into RB's real intelligence graph.

Unlike deep_research_dataset_ingest.py (which only enriches EXISTING brand
entities), this dataset introduces genuinely new entities and a new
relationship type (operates) -- the dataset's own top-level
relationship_model key spells the latter out directly. Kept as a separate
script rather than folded into the sibling ingest: the source shape,
entity-creation semantics, and merge rules are materially different, and
forcing one script to handle both would obscure more than it would share.

Todd, 2026-09-27: a multi-brand franchisee operator is a type of brand for
RB's purposes, not a separate category -- these entities use
entity_type "brand" like every restaurant concept already in the graph,
distinguished only by subtype ("multi_brand_franchisee_operator" instead
of the existing "restaurant_brand") so they stay queryable/countable
alongside brands rather than forking into a parallel, unqueried category.

What gets ingested, and what deliberately doesn't:
  - A new brand entity (subtype multi_brand_franchisee_operator) per
    qualified operator (net-new only -- if an entity with this name/alias
    already exists, of ANY type, it's reused if it's already this
    subtype, or skipped and flagged if it's a different brand subtype or
    entity type entirely; never silently creates a duplicate or
    misattributes data onto an unrelated entity). An "operator_name" like
    "Yadav Enterprises / JIB Management" is split on " / " into one
    canonical name plus aliases. Kept on a distinct "operator-" id prefix
    (not "brand-") purely to rule out any id collision with an ordinary
    restaurant brand -- the "treated as a brand" semantics live in entity_
    type/subtype, not the id shape.
  - operator -> operates -> brand relationships for every brand name that
    resolves to an EXISTING brand entity (exact name/alias match only,
    same discipline as the sibling ingest -- never creates a new brand
    entity here). A brand name that doesn't resolve is reported, not
    guessed at.
  - profile.leadership.people -> a per-person roster merge under
    attributes.deep_research_profile.current_leadership (NOT "leadership"
    -- that key already means a dict of per-role leaves on ordinary brand
    entities in deep_research_dataset_ingest.py; reusing it here for a
    list caused a real crash, fixed 2026-09-27): a new person (by name) is
    appended; an existing person's DIFFERENT title routes to review; a
    matching person is a no-op.
  - profile.evidence_ledger -> appended net-new (deduped by (finding,
    source_url)) under attributes.deep_research_profile.evidence_ledger --
    free-text research findings, not structured claims, so no attempt is
    made to parse or route individual entries into other subsystems.
  - profile.technology_stack / procurement_authority / business_profile /
    ownership_and_ma's own "status"/"dimensions" fields are the SOURCE
    dataset's own research-progress tracking (e.g. "research_needed"),
    not evidence about the operator -- never ingested as if they were
    findings. Only real evidence (leadership.people, evidence_ledger) is
    ingested.

CLI:
    python3 multibrand_franchisee_operator_ingest.py ingest --file PATH [--dry-run]
    python3 multibrand_franchisee_operator_ingest.py pending
    python3 multibrand_franchisee_operator_ingest.py confirm <id>
    python3 multibrand_franchisee_operator_ingest.py reject <id>
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
import ecosystem_intelligence as ei  # noqa: E402

STORE_PATH = core.SYSTEM_DIR / ".cache" / "multibrand_franchisee_operator_proposals.json"

# Same namespace as deep_research_dataset_ingest.py -- both are additive
# research-derived profile data on top of the canonical entity record.
_ATTRIBUTE_NAMESPACE = "deep_research_profile"

# Todd, 2026-09-27: this category is a type of brand for RB's purposes --
# entity_type "brand" (queryable/countable alongside every other brand),
# distinguished from an ordinary restaurant concept (subtype
# "restaurant_brand", the only other value in use as of this writing) by
# this subtype instead of a separate entity_type.
_OPERATOR_SUBTYPE = "multi_brand_franchisee_operator"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_store() -> dict:
    if STORE_PATH.exists():
        try:
            data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("pending"), dict):
                return data
        except Exception:
            pass
    return {"pending": {}}


def _save_store(store: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")


def _confidence_from_score(raw_score: Optional[float], rationale: str) -> dict:
    """Map the dataset's own 0-100 confidence score onto the graph schema's
    confidence object (level enum low/medium/high/critical + a 0-1 score).
    Confirmed live: ecosystem_intelligence.json's schema requires `level`
    whenever `confidence` is present at all -- an empty {} (what this
    script originally wrote) fails validation, and _write_graph() writes
    the file to disk BEFORE running that validation, so a schema mismatch
    here doesn't just get rejected cleanly, it lands invalid data on disk
    first. Never skip populating this."""
    if raw_score is None:
        # "level" enum has no "unknown" value (low/medium/high/critical
        # only) -- default to the conservative end rather than guess.
        return {"level": "low", "rationale": rationale}
    score = max(0.0, min(1.0, raw_score / 100))
    if raw_score >= 90:
        level = "high"
    elif raw_score >= 70:
        level = "medium"
    else:
        level = "low"
    return {"level": level, "score": score, "rationale": rationale}


def _split_operator_name(operator_name: str) -> tuple[str, list[str]]:
    parts = [p.strip() for p in operator_name.split(" / ") if p.strip()]
    if not parts:
        return operator_name.strip(), []
    return parts[0], parts[1:]


def _resolve_or_create_operator(
    canonical_name: str, aliases: list[str], graph: dict, *,
    source_confidence: Optional[float] = None, source_type: Optional[str] = None, dry_run: bool = False,
) -> tuple[Optional[str], str]:
    """Returns (operator_id_or_None, outcome): 'created' | 'existing' |
    'name_collision_skipped'. Checks the canonical name AND every alias
    against ALL existing entities (any type) -- a match against something
    that ISN'T already a brand/multi_brand_franchisee_operator (a real
    restaurant brand, a vendor, ...) is a genuine name collision and must
    never be silently merged into it."""
    existing_id = ei._resolve_entity_id_any_type(canonical_name, graph)
    if not existing_id:
        for alias in aliases:
            existing_id = ei._resolve_entity_id_any_type(alias, graph)
            if existing_id:
                break
    if existing_id:
        by_id = ei._index_by_id(graph.get("entities") or [])
        existing = by_id[existing_id]
        if existing.get("entity_type") == "brand" and existing.get("subtype") == _OPERATOR_SUBTYPE:
            return existing_id, "existing"
        return None, "name_collision_skipped"

    # Deliberately keeps the "operator-" id prefix rather than "brand-"
    # (the convention for ordinary restaurant brands) -- entity_type/
    # subtype already carry the "treated as a brand" semantics Todd asked
    # for; a distinct id prefix costs nothing and rules out any future id
    # collision with a real restaurant brand entirely, rather than relying
    # on slugs never coinciding.
    operator_id = f"operator-{ei._slug(canonical_name)}"
    if not dry_run:
        graph.setdefault("entities", []).append({
            "id": operator_id, "name": canonical_name, "entity_type": "brand", "subtype": _OPERATOR_SUBTYPE,
            "status": "active", "aliases": aliases, "attributes": {}, "sources": [],
            "confidence": _confidence_from_score(
                source_confidence, rationale=f"Source: {source_type}" if source_type else "External research dataset",
            ),
            "domains": ["restaurants"],
        })
    return operator_id, "created"


def _ensure_operates_relationship(
    graph: dict, operator_id: str, brand_id: str, source_url: Optional[str], *,
    source_confidence: Optional[float] = None, source_type: Optional[str] = None, dry_run: bool = False,
) -> str:
    """Returns 'applied' or 'unchanged'. A simple, direct upsert -- this is
    a stable structural fact (this operator runs this brand), not a
    deployment claim needing the heavier uses_vendor_for_category
    confidence/staleness machinery, so it's never routed through that."""
    for rel in graph.get("relationships") or []:
        if (
            rel.get("from_entity_id") == operator_id
            and rel.get("to_entity_id") == brand_id
            and rel.get("relationship_type") == "operates"
        ):
            return "unchanged"
    if not dry_run:
        timestamp = _now_iso()
        graph.setdefault("relationships", []).append({
            "id": f"rel-{operator_id}-operates-{brand_id}",
            "from_entity_id": operator_id, "to_entity_id": brand_id,
            "relationship_type": "operates", "status": "active", "domains": ["restaurants"],
            "sources": [source_url] if source_url else [],
            "confidence": _confidence_from_score(
                source_confidence, rationale=f"Source: {source_type}" if source_type else "External research dataset",
            ),
            "created_at": timestamp, "updated_at": timestamp,
        })
    return "applied"


def _merge_leadership(
    graph: dict, store: dict, *, operator_id: str, operator_name: str,
    people: list[dict], packet_id: str, dry_run: bool = False,
) -> dict:
    counts = {"applied": 0, "queued": 0, "unchanged": 0}
    by_id = ei._index_by_id(graph.get("entities") or [])
    entity = by_id[operator_id]
    # RB-2026-09-27: stored under "current_leadership", NOT "leadership" --
    # deep_research_dataset_ingest.py already uses deep_research_profile.
    # leadership for a DIFFERENT shape entirely (a dict of per-role leaves,
    # e.g. leadership.ceo, on ordinary brand entities). Reusing that key
    # here for a list-of-people roster caused a real crash: any brand-
    # entity brief renderer that assumes leadership is a dict would raise
    # AttributeError on an operator entity's list. "current_leadership"
    # matches the key deep_account_intelligence_v56_ingest.py's own
    # roster-shaped leadership already uses, for exactly this reason.
    existing_roster = entity.get("attributes", {}).get(_ATTRIBUTE_NAMESPACE, {}).get("current_leadership", [])
    roster_by_name = {p["name"]: p for p in existing_roster if isinstance(p, dict) and p.get("name")}

    roster = None
    if not dry_run:
        namespace = entity.setdefault("attributes", {}).setdefault(_ATTRIBUTE_NAMESPACE, {})
        roster = namespace.setdefault("current_leadership", [])

    for person in people:
        name = person.get("name")
        if not name:
            continue
        new_record = {
            "name": name, "title": person.get("title"), "confidence": person.get("confidence"),
            "source_url": person.get("source_url"), "packet_id": packet_id, "recorded_at": _now_iso(),
        }
        existing = roster_by_name.get(name)
        if existing is None:
            if not dry_run:
                roster.append(new_record)
            roster_by_name[name] = new_record
            counts["applied"] += 1
        elif existing.get("title") == new_record["title"]:
            counts["unchanged"] += 1
        else:
            if not dry_run:
                cid = f"leader-{operator_id}-{ei._slug(name)}"
                store["pending"][cid] = {
                    "candidate_id": cid, "operator_id": operator_id, "operator_name": operator_name,
                    "person_name": name, "existing": existing, "proposed": new_record,
                    "status": "proposed_pending_confirmation", "created_at": _now_iso(),
                }
            counts["queued"] += 1
    return counts


def _merge_evidence_ledger(entity: dict, evidence_ledger: list[dict], packet_id: str, *, dry_run: bool = False) -> int:
    existing_ledger = entity.get("attributes", {}).get(_ATTRIBUTE_NAMESPACE, {}).get("evidence_ledger", [])
    existing_keys = {(e.get("finding"), e.get("source_url")) for e in existing_ledger if isinstance(e, dict)}

    ledger = None
    if not dry_run:
        namespace = entity.setdefault("attributes", {}).setdefault(_ATTRIBUTE_NAMESPACE, {})
        ledger = namespace.setdefault("evidence_ledger", [])

    added = 0
    for item in evidence_ledger:
        key = (item.get("finding"), item.get("source_url"))
        if key in existing_keys:
            continue
        if not dry_run:
            ledger.append({**item, "packet_id": packet_id, "recorded_at": _now_iso()})
        existing_keys.add(key)
        added += 1
    return added


def ingest(dataset_path: Path, *, dry_run: bool = False) -> dict:
    data = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    records = data.get("records") or []
    packet_id = f"multibrand-franchisee-operators::{data.get('version') or Path(dataset_path).stem}"

    graph = ei._read_graph()
    store = _load_store()

    counts = {
        "operators_total": len(records),
        "operators_created": 0,
        "operators_existing": 0,
        "operators_name_collision_skipped": 0,
        "operates_relationships_applied": 0,
        "operates_relationships_unchanged": 0,
        "brands_unresolved": 0,
        "leadership_applied": 0,
        "leadership_queued": 0,
        "leadership_unchanged": 0,
        "evidence_ledger_entries_added": 0,
    }
    unresolved_brands: list[str] = []
    name_collisions: list[str] = []

    for rec in records:
        operator_name_raw = rec.get("operator_name")
        if not operator_name_raw:
            continue
        canonical_name, aliases = _split_operator_name(operator_name_raw)
        source_confidence = rec.get("confidence")
        source_type = rec.get("source_type")

        operator_id, outcome = _resolve_or_create_operator(
            canonical_name, aliases, graph,
            source_confidence=source_confidence, source_type=source_type, dry_run=dry_run,
        )
        if outcome == "name_collision_skipped":
            counts["operators_name_collision_skipped"] += 1
            name_collisions.append(operator_name_raw)
            continue
        counts[f"operators_{outcome}"] += 1
        is_new_dry_run = dry_run and outcome == "created"

        by_id = ei._index_by_id(graph.get("entities") or [])
        entity = by_id.get(operator_id)  # None only when is_new_dry_run

        source_url = rec.get("source_url")
        for brand_name in rec.get("brands") or []:
            brand_id = ei._resolve_entity_id_any_type(brand_name, graph)
            brand_entity = by_id.get(brand_id) if brand_id else None
            # subtype exclusion matters now that operators are ALSO
            # entity_type "brand" -- a name in this operator's own
            # brands[] list must resolve to a real restaurant concept, never
            # to another multi-brand-franchisee-operator entity.
            if (
                not brand_entity
                or brand_entity.get("entity_type") != "brand"
                or brand_entity.get("subtype") == _OPERATOR_SUBTYPE
            ):
                counts["brands_unresolved"] += 1
                unresolved_brands.append(brand_name)
                continue
            if is_new_dry_run:
                counts["operates_relationships_applied"] += 1
            else:
                rel_outcome = _ensure_operates_relationship(
                    graph, operator_id, brand_id, source_url,
                    source_confidence=source_confidence, source_type=source_type, dry_run=dry_run,
                )
                counts[f"operates_relationships_{rel_outcome}"] += 1

        profile = rec.get("profile") or {}
        people = (profile.get("leadership") or {}).get("people") or []
        if people:
            if is_new_dry_run:
                counts["leadership_applied"] += len([p for p in people if p.get("name")])
            else:
                leadership_counts = _merge_leadership(
                    graph, store, operator_id=operator_id, operator_name=entity.get("name", canonical_name),
                    people=people, packet_id=packet_id, dry_run=dry_run,
                )
                for key, value in leadership_counts.items():
                    counts[f"leadership_{key}"] += value

        evidence_ledger = profile.get("evidence_ledger") or []
        if evidence_ledger:
            if is_new_dry_run:
                counts["evidence_ledger_entries_added"] += len(evidence_ledger)
            else:
                counts["evidence_ledger_entries_added"] += _merge_evidence_ledger(
                    entity, evidence_ledger, packet_id, dry_run=dry_run,
                )

    if not dry_run:
        ei._write_graph(graph)
        _save_store(store)

    return {
        "counts": counts, "unresolved_brands": unresolved_brands,
        "name_collisions": name_collisions, "dry_run": dry_run,
    }


def pending_candidates() -> list[dict]:
    store = _load_store()
    return [c for c in store["pending"].values() if c.get("status") == "proposed_pending_confirmation"]


def record_proposal(candidate_id: str, *, confirmed: bool) -> dict:
    store = _load_store()
    candidate = store["pending"].get(candidate_id)
    if not candidate:
        return {"error": f"no pending candidate '{candidate_id}'"}
    if candidate.get("status") != "proposed_pending_confirmation":
        return {"error": f"candidate '{candidate_id}' already {candidate.get('status')}"}

    if confirmed:
        graph = ei._read_graph()
        by_id = ei._index_by_id(graph.get("entities") or [])
        entity = by_id.get(candidate["operator_id"])
        if not entity:
            return {"error": f"operator entity '{candidate['operator_id']}' no longer exists"}
        namespace = entity.setdefault("attributes", {}).setdefault(_ATTRIBUTE_NAMESPACE, {})
        roster = namespace.setdefault("current_leadership", [])
        for i, person in enumerate(roster):
            if isinstance(person, dict) and person.get("name") == candidate["person_name"]:
                roster[i] = candidate["proposed"]
                break
        else:
            roster.append(candidate["proposed"])
        ei._write_graph(graph)
        candidate["status"] = "confirmed"
    else:
        candidate["status"] = "rejected"
    candidate["resolved_at"] = _now_iso()
    _save_store(store)
    return {"candidate_id": candidate_id, "status": candidate["status"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    ingest_parser = sub.add_parser("ingest")
    ingest_parser.add_argument("--file", required=True)
    ingest_parser.add_argument("--dry-run", action="store_true")
    sub.add_parser("pending")
    confirm_parser = sub.add_parser("confirm")
    confirm_parser.add_argument("id")
    reject_parser = sub.add_parser("reject")
    reject_parser.add_argument("id")
    args = parser.parse_args()

    if args.cmd == "ingest":
        result = ingest(Path(args.file), dry_run=args.dry_run)
    elif args.cmd == "pending":
        result = pending_candidates()
    elif args.cmd == "confirm":
        result = record_proposal(args.id, confirmed=True)
    else:
        result = record_proposal(args.id, confirmed=False)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
