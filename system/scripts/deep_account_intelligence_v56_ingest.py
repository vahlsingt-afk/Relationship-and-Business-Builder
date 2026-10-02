#!/usr/bin/env python3
"""
deep_account_intelligence_v56_ingest.py — RB-2026-09-27.

Ingests the "RBB Top 500 Restaurant Intelligence Expansion" deep-research
dataset (500 brand records: scale, canonical_technology, franchise
disclosure/recruitment evidence, current leadership roster, deep-pass
public evidence) into RB's real intelligence graph. Sibling to (not a
replacement for) deep_research_dataset_ingest.py -- this dataset's
per-record shape is materially different (canonical_technology leaves
carry no sources/as_of/scope; leadership is a roster list, not per-role
leaves; several fields are dataset-level research-progress tracking, not
evidence) so it gets its own script rather than forcing one shared shape.

Todd, 2026-09-27: no field here is ever silently overwritten. A field
that already holds a value on the brand entity keeps that value on any
conflict -- the incoming value is queued to this script's own pending
review store instead, exactly like deep_research_dataset_ingest.py and
multibrand_franchisee_operator_ingest.py already do. Only a genuinely
net-new field (nothing on file yet) auto-applies; a matching value is a
silent no-op; a *different* value always routes to review, never
overwrite. Evidence ledgers (deep_pass_public_evidence, deep_account_
profile.fresh_evidence, franchise_recruitment_evidence) are additive by
nature -- there's no "conflict" to review since new entries are appended
net-new (deduped by finding+source_url) and existing entries are never
touched -- so a ledger has nothing to route to review.

What gets ingested, and what deliberately doesn't:
  - scale (units/franchisee_owned_units/company_owned_units/
    ownership_confidence) -> one atomic field under deep_research_profile.
    scale_snapshot -- review-gated like any other scalar fact.
  - canonical_technology (per-category leaf: value/confidence/status) ->
    always recorded under deep_research_profile.canonical_technology
    (review-gated per category), AND, when an existing known vendor
    entity's name is named verbatim in the value text, ALSO proposed to
    tech_stack_relationship_promotion (existing, already-tested review
    queue) -- these are two different destinations (an entity attribute
    vs. a relationship candidate), so writing to both is additive, not a
    conflicting double-write.
  - franchise_disclosure -> one atomic field (the whole findings dict
    plus its own confidence/source) under deep_research_profile.
    franchise_disclosure -- review-gated as a unit, since its sub-fields
    (fee amounts, POS name, etc.) are all sourced from the same FDD
    filing and don't make sense to review piecemeal.
  - current_leadership -> a per-person roster merge under
    deep_research_profile.current_leadership, same discipline as
    multibrand_franchisee_operator_ingest.py's leadership merge: net-new
    person appended, matching person is a no-op, a person with a
    DIFFERENT title/status routes to review.
  - deep_pass_public_evidence, deep_account_profile.fresh_evidence,
    franchise_recruitment_evidence -> three separate append-only ledgers
    (deduped by (finding, source_url)), kept distinct rather than merged
    into one list so each retains its own source-structure provenance.
  - research_quality, leadership_research_status, canonical_metadata,
    current_leadership_roles_to_identify, deep_account_profile.
    dimensions/next_research_gaps/recommended_source_order,
    technology_procurement_classification, technology_vendor_aliases,
    actionable_sales_stack_matrix -- all dataset-internal research-
    progress tracking (what's been reviewed, what's still a gap), not
    evidence about the brand. Never ingested, same exclusion precedent as
    deep_research_dataset_ingest.py's research_quality-style fields.
  - The dataset's auxiliary top-level structures (ma_portfolio_graph_v35,
    franchisee_network_graph_v36, franchisee_operator_intelligence_v37,
    multi_unit_operator_universe_v49/51, payments_provider_reference_v41,
    embedded_payments_platform_reference_v44, technology_normalization,
    genius_normalization_v31, evidence_model_v48) are dataset-level
    reference/glossary/cross-brand-graph material, not per-record
    findings -- deliberately out of scope for this script. Flagged
    separately as a follow-up (see session notes), not attempted here.

Brand identity resolution is exact name/alias match only
(ecosystem_intelligence._resolve_entity_id_any_type) restricted to
entity_type == "brand" -- same discipline as every sibling ingest.

CLI:
    python3 deep_account_intelligence_v56_ingest.py ingest --file PATH [--dry-run]
    python3 deep_account_intelligence_v56_ingest.py pending
    python3 deep_account_intelligence_v56_ingest.py confirm <id>
    python3 deep_account_intelligence_v56_ingest.py reject <id>
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
import tech_stack_relationship_promotion as tsrp  # noqa: E402

STORE_PATH = core.SYSTEM_DIR / ".cache" / "deep_account_intelligence_v56_proposals.json"

# Same namespace as deep_research_dataset_ingest.py / multibrand_
# franchisee_operator_ingest.py -- all research-derived profile data on
# top of the canonical entity record lives under this one key.
_ATTRIBUTE_NAMESPACE = "deep_research_profile"

_EVIDENCE_LEDGER_FIELDS = {
    "deep_pass_public_evidence": "deep_pass_public_evidence",
    "franchise_recruitment_evidence": "franchise_recruitment_evidence",
}


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


def _candidate_id(brand_id: str, field_group: str, field_name: str) -> str:
    return f"acct56-{brand_id}-{field_group}-{field_name}"


def _find_known_vendor(value_text: str, graph: dict) -> Optional[str]:
    """Same discipline as deep_research_dataset_ingest._find_known_vendor:
    return the single existing vendor entity's canonical name if exactly
    one already-known vendor is named verbatim in value_text, else None.
    Never guesses a vendor RB doesn't already track."""
    text_lower = (value_text or "").lower()
    matches: set[str] = set()
    for entity in graph.get("entities") or []:
        if entity.get("entity_type") != "vendor":
            continue
        names = [entity.get("name") or ""] + list(entity.get("aliases") or [])
        if any(tsrp._name_in_text(name, text_lower) for name in names if name):
            matches.add(entity.get("name"))
    if len(matches) == 1:
        return next(iter(matches))
    return None


def _apply_or_queue_scalar(
    graph: dict, store: dict, *, brand_id: str, brand_name: str,
    field_group: str, field_name: str, new_value, confidence, source_url: Optional[str],
    packet_id: str, as_of: Optional[str] = None,
) -> str:
    """Returns one of: applied | queued | unchanged. Generic review-gated
    scalar-field merge shared by scale, canonical_technology and
    franchise_disclosure -- never overwrites an existing value; a
    difference always routes to review instead.

    as_of: RB-2026-09-27, Todd -- "confidence levels, data date...are
    important to these records." Pass the dataset's own real date for
    this fact (e.g. canonical_metadata.last_verified, franchise_
    disclosure.document_year) whenever one exists; never invent one when
    the source genuinely doesn't carry a date."""
    by_id = ei._index_by_id(graph.get("entities") or [])
    entity = by_id[brand_id]
    namespace = entity.setdefault("attributes", {}).setdefault(_ATTRIBUTE_NAMESPACE, {})
    group = namespace.setdefault(field_group, {})
    existing = group.get(field_name)

    if existing is not None and existing.get("value") == new_value:
        return "unchanged"

    record = {
        "value": new_value, "confidence": confidence, "source_url": source_url, "as_of": as_of,
        "packet_id": packet_id, "recorded_at": _now_iso(),
    }

    if existing is None:
        group[field_name] = record
        return "applied"

    cid = _candidate_id(brand_id, field_group, field_name)
    store["pending"][cid] = {
        "candidate_id": cid, "brand_id": brand_id, "brand_name": brand_name,
        "field_group": field_group, "field_name": field_name,
        "existing": existing, "proposed": record,
        "status": "proposed_pending_confirmation", "created_at": _now_iso(),
    }
    return "queued"


def _merge_evidence_ledger(entity: dict, field_name: str, evidence_items: list[dict], packet_id: str) -> int:
    """Append-only: new (finding, source_url) pairs are added, existing
    entries are never touched -- there is nothing to route to review
    here, since nothing already on file is ever changed."""
    namespace = entity.setdefault("attributes", {}).setdefault(_ATTRIBUTE_NAMESPACE, {})
    ledger = namespace.setdefault(field_name, [])
    existing_keys = {(e.get("finding"), e.get("source_url")) for e in ledger if isinstance(e, dict)}

    added = 0
    for item in evidence_items:
        if not isinstance(item, dict):
            continue
        key = (item.get("finding"), item.get("source_url"))
        if key in existing_keys:
            continue
        ledger.append({**item, "packet_id": packet_id, "recorded_at": _now_iso()})
        existing_keys.add(key)
        added += 1
    return added


def _merge_current_leadership(
    graph: dict, store: dict, *, brand_id: str, brand_name: str,
    people: list[dict], packet_id: str,
) -> dict:
    counts = {"applied": 0, "queued": 0, "unchanged": 0}
    by_id = ei._index_by_id(graph.get("entities") or [])
    entity = by_id[brand_id]
    namespace = entity.setdefault("attributes", {}).setdefault(_ATTRIBUTE_NAMESPACE, {})
    roster = namespace.setdefault("current_leadership", [])
    roster_by_name = {p["name"]: p for p in roster if isinstance(p, dict) and p.get("name")}

    for person in people:
        name = person.get("name")
        if not name:
            continue
        new_record = {
            "name": name, "title": person.get("title"), "function": person.get("function"),
            "status": person.get("status"), "confidence": person.get("confidence"),
            "verification_date": person.get("verification_date"), "source_url": person.get("source_url"),
            "linkedin_url": person.get("linkedin_url"), "packet_id": packet_id, "recorded_at": _now_iso(),
        }
        existing = roster_by_name.get(name)
        if existing is None:
            roster.append(new_record)
            roster_by_name[name] = new_record
            counts["applied"] += 1
        elif existing.get("title") == new_record["title"] and existing.get("status") == new_record["status"]:
            counts["unchanged"] += 1
        else:
            cid = f"acct56-leader-{brand_id}-{ei._slug(name)}"
            store["pending"][cid] = {
                "candidate_id": cid, "brand_id": brand_id, "brand_name": brand_name,
                "person_name": name, "existing": existing, "proposed": new_record,
                "status": "proposed_pending_confirmation", "created_at": _now_iso(),
            }
            counts["queued"] += 1
    return counts


def ingest(dataset_path: Path, *, dry_run: bool = False) -> dict:
    data = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    dataset = data.get("dataset") or {}
    records = dataset.get("records") or []
    packet_id = f"deep-account-intelligence-v56::{dataset.get('version') or Path(dataset_path).stem}"

    graph = ei._read_graph()
    store = _load_store()

    counts = {
        "brands_total": len(records),
        "brands_resolved": 0,
        "brands_unresolved": 0,
        "scale_applied": 0, "scale_queued": 0, "scale_unchanged": 0,
        "canonical_technology_applied": 0, "canonical_technology_queued": 0, "canonical_technology_unchanged": 0,
        "technology_vendor_proposed": 0,
        "franchise_disclosure_applied": 0, "franchise_disclosure_queued": 0, "franchise_disclosure_unchanged": 0,
        "current_leadership_applied": 0, "current_leadership_queued": 0, "current_leadership_unchanged": 0,
        "deep_pass_public_evidence_added": 0,
        "franchise_recruitment_evidence_added": 0,
        "account_profile_evidence_added": 0,
    }
    unresolved_brands: list[str] = []
    errors: list[dict] = []

    for rec in records:
        brand_name = rec.get("brand_name")
        if not brand_name:
            continue
        brand_id = ei._resolve_entity_id_any_type(brand_name, graph)
        by_id = ei._index_by_id(graph.get("entities") or [])
        entity = by_id.get(brand_id) if brand_id else None
        if not entity or entity.get("entity_type") != "brand":
            counts["brands_unresolved"] += 1
            unresolved_brands.append(brand_name)
            continue
        counts["brands_resolved"] += 1
        resolved_name = entity.get("name", brand_name)

        # RB-2026-09-27: the record's only per-record date -- canonical_
        # technology and scale carry no field-level date of their own in
        # this dataset, but canonical_metadata.last_verified is the real
        # date the research provider verified BOTH of those fields as of.
        # Attaching it beats leaving as_of silently absent; never invented
        # when the dataset itself has nothing (checked live -- every
        # record in this dataset does carry it).
        canonical_as_of = (rec.get("canonical_metadata") or {}).get("last_verified")

        # ---- scale --------------------------------------------------------
        scale = rec.get("scale") or {}
        if scale:
            if dry_run:
                existing = entity.get("attributes", {}).get(_ATTRIBUTE_NAMESPACE, {}).get("scale_snapshot", {}).get("units")
                if existing is not None and existing.get("value") == scale:
                    counts["scale_unchanged"] += 1
                elif existing is None:
                    counts["scale_applied"] += 1
                else:
                    counts["scale_queued"] += 1
            else:
                outcome = _apply_or_queue_scalar(
                    graph, store, brand_id=brand_id, brand_name=resolved_name,
                    field_group="scale_snapshot", field_name="units", new_value=scale,
                    confidence=scale.get("ownership_confidence"), source_url=None,
                    as_of=canonical_as_of, packet_id=packet_id,
                )
                counts[f"scale_{outcome}"] += 1

        # ---- canonical_technology ------------------------------------------
        for category, leaf in (rec.get("canonical_technology") or {}).items():
            if not isinstance(leaf, dict) or "value" not in leaf:
                continue
            value = leaf.get("value")
            if not value:
                continue
            if dry_run:
                existing = entity.get("attributes", {}).get(_ATTRIBUTE_NAMESPACE, {}).get("canonical_technology", {}).get(category)
                if existing is not None and existing.get("value") == value:
                    counts["canonical_technology_unchanged"] += 1
                elif existing is None:
                    counts["canonical_technology_applied"] += 1
                else:
                    counts["canonical_technology_queued"] += 1
            else:
                outcome = _apply_or_queue_scalar(
                    graph, store, brand_id=brand_id, brand_name=resolved_name,
                    field_group="canonical_technology", field_name=category, new_value=value,
                    confidence=leaf.get("confidence"), source_url=None,
                    as_of=canonical_as_of, packet_id=packet_id,
                )
                counts[f"canonical_technology_{outcome}"] += 1

            vendor_name = _find_known_vendor(str(value), graph)
            if vendor_name:
                if dry_run:
                    counts["technology_vendor_proposed"] += 1
                else:
                    result = tsrp.propose_research_finding(
                        brand_id, vendor_name, str(value), evidence_date=canonical_as_of,
                    )
                    if result.get("proposed"):
                        counts["technology_vendor_proposed"] += 1
                    elif result.get("error"):
                        errors.append({"brand": brand_name, "field": f"canonical_technology.{category}", "error": result["error"]})

        # ---- franchise_disclosure -------------------------------------------
        disclosure = rec.get("franchise_disclosure") or {}
        disclosure_findings = disclosure.get("findings")
        if disclosure_findings:
            if dry_run:
                existing = entity.get("attributes", {}).get(_ATTRIBUTE_NAMESPACE, {}).get("franchise_disclosure", {}).get("findings")
                if existing is not None and existing.get("value") == disclosure_findings:
                    counts["franchise_disclosure_unchanged"] += 1
                elif existing is None:
                    counts["franchise_disclosure_applied"] += 1
                else:
                    counts["franchise_disclosure_queued"] += 1
            else:
                # RB-2026-09-27: the value stored here must be just the
                # findings dict (fee amounts, disclosed POS name, etc) --
                # NOT the whole `disclosure` wrapper it lives inside (which
                # duplicates confidence/hyperlink, already captured
                # separately below, and would otherwise render as a raw
                # nested-dict dump in the brief instead of clean facts).
                document_year = disclosure.get("document_year")
                outcome = _apply_or_queue_scalar(
                    graph, store, brand_id=brand_id, brand_name=resolved_name,
                    field_group="franchise_disclosure", field_name="findings", new_value=disclosure_findings,
                    confidence=disclosure.get("confidence"), source_url=disclosure.get("hyperlink"),
                    as_of=str(document_year) if document_year is not None else None, packet_id=packet_id,
                )
                counts[f"franchise_disclosure_{outcome}"] += 1

        # ---- current_leadership ----------------------------------------------
        people = rec.get("current_leadership") or []
        if people:
            if dry_run:
                counts["current_leadership_applied"] += len([p for p in people if p.get("name")])
            else:
                leadership_counts = _merge_current_leadership(
                    graph, store, brand_id=brand_id, brand_name=resolved_name,
                    people=people, packet_id=packet_id,
                )
                for key, value in leadership_counts.items():
                    counts[f"current_leadership_{key}"] += value

        # ---- evidence ledgers -------------------------------------------------
        if not dry_run:
            for src_field, dest_field in _EVIDENCE_LEDGER_FIELDS.items():
                items = rec.get(src_field) or []
                if items:
                    counts[f"{dest_field}_added"] += _merge_evidence_ledger(entity, dest_field, items, packet_id)
            fresh_evidence = (rec.get("deep_account_profile") or {}).get("fresh_evidence") or []
            if fresh_evidence:
                counts["account_profile_evidence_added"] += _merge_evidence_ledger(
                    entity, "account_profile_evidence", fresh_evidence, packet_id,
                )
        else:
            for src_field, dest_field in _EVIDENCE_LEDGER_FIELDS.items():
                counts[f"{dest_field}_added"] += len(rec.get(src_field) or [])
            counts["account_profile_evidence_added"] += len((rec.get("deep_account_profile") or {}).get("fresh_evidence") or [])

    if not dry_run:
        wrote_anything = any(
            counts[k] for k in (
                "scale_applied", "canonical_technology_applied", "franchise_disclosure_applied",
                "current_leadership_applied", "deep_pass_public_evidence_added",
                "franchise_recruitment_evidence_added", "account_profile_evidence_added",
            )
        )
        if wrote_anything:
            ei._write_graph(graph)
        _save_store(store)

    return {"counts": counts, "unresolved_brands": unresolved_brands, "errors": errors, "dry_run": dry_run}


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
        entity = by_id.get(candidate["brand_id"])
        if not entity:
            return {"error": f"brand entity '{candidate['brand_id']}' no longer exists"}
        namespace = entity.setdefault("attributes", {}).setdefault(_ATTRIBUTE_NAMESPACE, {})
        if "person_name" in candidate:
            roster = namespace.setdefault("current_leadership", [])
            for i, person in enumerate(roster):
                if isinstance(person, dict) and person.get("name") == candidate["person_name"]:
                    roster[i] = candidate["proposed"]
                    break
            else:
                roster.append(candidate["proposed"])
        else:
            group = namespace.setdefault(candidate["field_group"], {})
            group[candidate["field_name"]] = candidate["proposed"]
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
