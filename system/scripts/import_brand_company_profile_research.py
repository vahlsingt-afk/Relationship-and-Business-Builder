#!/usr/bin/env python3
"""
import_brand_company_profile_research.py — 2026-10-03.

RB live incident: Charleys' Hunter packet (enterprise_account_profile,
payload_schema rb.brand_company_profile.v1) finalized clean -- schema-
valid, change-comparison clean -- but every one of its 10 mutation_
proposals came back "handler_recognized: false". hunter_change_dispatch.py
has only ever had one real brand-profile write handler (_apply_brand_
recent_signal, field_path "brand_profile.recent_signals"); everything else
this playbook's own required_modules promise (identity, footprint,
leadership, franchise_disclosure, financial_operating_health) had nowhere
real to land, so it sat in system/.cache/hunter_mutation_proposals.jsonl
forever.

This module is the real importer -- same review-first discipline as
import_competitor_platform_research.py and import_franchisee_research.py,
but reading payload.records (the registered rb.brand_company_profile.v1
collection) rather than the generic, thinner mutation_proposals array,
since records carry the actual values Hunter found and mutation_proposals
in the wild has shown up with every new_value stripped to null.

Per-record_type routing:
  - company_identity    -> brand_profile_common identity.{legal_entity,
                            hq_city_state, parent_ownership}
  - footprint_snapshot   -> brand_profile_common footprint.{total_units,
                            franchised_units, company_owned_units} -- SKIPPED
                            when the existing field is already human-
                            reviewed (brand_profile_common.is_human_reviewed),
                            same precedence _refresh_footprint() already
                            gives a human correction over Technomic.
  - leadership_snapshot  -> brand_profile_common.add_leadership_person(),
                            confirmed vs. reported_unverified by finding_type.
  - franchise_disclosure -> brand_profile_common franchise_disclosure (one
                            scalar field, whole record as its value).
  - financial_operating_snapshot -> brand_profile_common
                            financial_operating_health (same pattern).
  - technology_relationship / technology_observation -> NOT a brand_profile
    field at all. Routed as a pending candidate into tech_stack_
    relationship_promotion.py's own real review store (_add_candidate) --
    the established, already-reviewed surface for "does this brand use
    this vendor" decisions, using the job's own resolved brand identity
    (never re-derived from Hunter's free-text brand name, which could
    diverge from the canonical entity and create a duplicate brand).

Never auto-creates the underlying ecosystem brand entity -- a target
whose brand_id doesn't resolve is an identity problem (routed to
mutation_policy review), not license to invent one.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import brand_profile_common as bpc  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import mutation_policy  # noqa: E402
import tech_stack_relationship_promotion as tsrp  # noqa: E402

SOURCE_TAG = "system:brand_company_profile_research_2026-10"

_RECORD_TYPES = {
    "company_identity", "footprint_snapshot", "leadership_snapshot",
    "franchise_disclosure", "financial_operating_snapshot",
    "technology_relationship", "technology_observation",
}
_VERIFIED_FINDING_TYPES = {"independently_verified"}


class _IdentityUnresolved(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _validate_record(record: dict) -> Optional[str]:
    if not isinstance(record, dict):
        return "record is not an object"
    if not record.get("target_key"):
        return "missing required key: target_key"
    record_type = record.get("record_type")
    if record_type not in _RECORD_TYPES:
        return f"record_type must be one of {sorted(_RECORD_TYPES)}, got {record_type!r}"
    return None


def _resolve_brand(target_key: str) -> tuple[str, dict]:
    if not (isinstance(target_key, str) and target_key.startswith("company:")):
        raise _IdentityUnresolved(f"target_key must start with 'company:', got {target_key!r}")
    brand_id = target_key.split(":", 1)[1]
    try:
        entity = bpc.resolve_brand_entity(brand_id)
    except bpc.NotFoundError:
        raise _IdentityUnresolved(f"brand_id {brand_id!r} is not a tracked brand entity; profile findings never auto-create one")
    return brand_id, entity


def _source_lookup(packet: dict) -> dict[str, dict]:
    return {row.get("source_id"): row for row in packet.get("source_ledger") or [] if isinstance(row, dict)}


def _aggregate_profile_records(records: list, packet: dict) -> tuple[list[dict], int]:
    """Expand the broad brand-profile payload row into the importer's
    deliberately narrow, reviewable record types.

    The registered brand payload has historically returned one nested
    profile object per target.  Do not guess a type for that whole object:
    split only fields that have an established importer route, and attach
    provenance from the matching Hunter finding module (or explicit nested
    source_ids).  Unmapped strategy, news, and ambiguous footprint data stay
    in the packet for review rather than being forced into a canonical field.
    """
    findings = [row for row in packet.get("findings") or [] if isinstance(row, dict)]
    typed: list[dict] = []
    expanded = 0

    def evidence(modules: set[str], explicit_sources: list | None = None) -> tuple[list[str], list[str]]:
        wanted = {str(value) for value in (explicit_sources or []) if value}
        selected = [f for f in findings if f.get("module") in modules]
        if not wanted:
            for finding in selected:
                wanted.update(str(value) for value in finding.get("source_ids") or [] if value)
        finding_ids = [str(f.get("finding_id")) for f in selected
                       if f.get("finding_id") and (not wanted or wanted.intersection(map(str, f.get("source_ids") or [])))]
        return sorted(wanted), finding_ids

    def as_of(modules: set[str]) -> str | None:
        values = [f.get("as_of") for f in findings
                  if f.get("module") in modules and isinstance(f.get("as_of"), str)
                  and len(f["as_of"]) == 10 and f["as_of"][4] == "-" and f["as_of"][7] == "-"]
        return max(values) if values else None

    for record in records:
        if not isinstance(record, dict) or record.get("record_type"):
            if isinstance(record, dict):
                typed.append(record)
            continue
        if not record.get("target_key") or not any(
            key in record for key in ("headquarters_context", "leadership", "financial_operating_health",
                                      "technology_stack", "franchise_disclosure", "footprint")
        ):
            typed.append(record)
            continue

        expanded += 1
        target_key = record["target_key"]
        headquarters = record.get("headquarters_context") or {}

        # Identity fields are routed only with identity-module evidence.
        identity_sources, identity_findings = evidence({"identity"})
        identity = {
            "record_type": "company_identity", "target_key": target_key,
            "legal_entity": record.get("legal_entity"),
            "headquarters": headquarters.get("brand_canada_us_leadership_base"),
            "ownership_type": record.get("ownership_type"),
            "owner": record.get("parent_company"),
            "source_ids": identity_sources, "finding_ids": identity_findings,
        }
        identity = {key: value for key, value in identity.items() if value not in (None, "", [], {})}
        if len(identity) > 2 and identity_sources and identity_findings:
            typed.append(identity)

        leadership_rows = record.get("leadership") or []
        people = []
        leadership_sources: set[str] = set()
        for person in leadership_rows:
            if not isinstance(person, dict) or not (person.get("name") and person.get("title")):
                continue
            people.append({"name": person["name"], "title": person["title"]})
            leadership_sources.update(str(value) for value in person.get("source_ids") or [] if value)
        if people:
            source_ids, finding_ids = evidence({"leadership"}, sorted(leadership_sources))
            if source_ids and finding_ids:
                typed.append({"record_type": "leadership_snapshot", "target_key": target_key,
                              "as_of": as_of({"leadership"}), "people": people,
                              "source_ids": source_ids, "finding_ids": finding_ids})

        financial = record.get("financial_operating_health")
        if isinstance(financial, dict) and financial:
            source_ids, finding_ids = evidence({"financial_operating_health"})
            if source_ids and finding_ids:
                typed.append({"record_type": "financial_operating_snapshot", "target_key": target_key,
                              "as_of": as_of({"financial_operating_health"}),
                              "source_ids": source_ids, "finding_ids": finding_ids,
                              **financial})

        disclosure = record.get("franchise_disclosure")
        if isinstance(disclosure, dict) and disclosure:
            source_ids, finding_ids = evidence({"franchise_governance"})
            disclosure_value = dict(disclosure)
            disclosure_value["as_of"] = as_of({"franchise_governance"}) or disclosure_value.get("as_of")
            if source_ids and finding_ids:
                typed.append({"record_type": "franchise_disclosure", "target_key": target_key,
                              "as_of": as_of({"franchise_governance"}),
                              "source_ids": source_ids, "finding_ids": finding_ids,
                              **disclosure_value})

        for technology in record.get("technology_stack") or []:
            if not isinstance(technology, dict) or not (technology.get("vendor") and technology.get("category")):
                continue
            source_ids, finding_ids = evidence({"technology_stack"}, technology.get("source_ids") or [])
            if source_ids and finding_ids:
                typed.append({"record_type": "technology_relationship", "target_key": target_key,
                              "source_ids": source_ids, "finding_ids": finding_ids,
                              **technology})

    return typed, expanded


def _primary_source(record: dict, sources: dict[str, dict]) -> dict:
    for source_id in record.get("source_ids") or []:
        row = sources.get(source_id)
        if row:
            return row
    return {}


def _decide_and_apply_scalar(brand_id: str, profile: dict, field_path: str, value, *,
                              confidence, as_of, source_url, finding_type, dry_run: bool,
                              get_existing, set_value) -> dict:
    existing = get_existing(profile)
    existing_value = existing.get("value")
    existing_date = existing.get("as_of")
    decision = mutation_policy.decide(
        source=SOURCE_TAG, new_value=value, existing_value=existing_value,
        is_replacement=True, new_date=as_of, existing_date=existing_date,
        observed_at=as_of, confidence=confidence, field_name=field_path, entity_id=brand_id,
    )
    applied = False
    if not dry_run and decision.auto_apply:
        set_value(profile, bpc.field(value, confidence=confidence, as_of=as_of, source_url=source_url,
                                      last_reviewed_by=SOURCE_TAG))
        bpc.save_profile(brand_id, profile)
        applied = True
    if not dry_run:
        mutation_policy.record_receipt(decision, artifact=f"brand_profile/{brand_id}.json" if applied else None, applied=applied)
    return {"status": decision.status, "applied": applied, "field_path": field_path}


def _apply_company_identity(brand_id: str, record: dict, source: dict, *, dry_run: bool) -> list[dict]:
    results = []
    confidence = "high"
    source_url = source.get("url")
    profile = bpc.get_profile(brand_id, persist=False)
    if record.get("legal_entity"):
        results.append(_decide_and_apply_scalar(
            brand_id, profile, "identity.legal_entity", record["legal_entity"],
            confidence=confidence, as_of=bpc.today(), source_url=source_url, finding_type=None, dry_run=dry_run,
            get_existing=lambda p: p["identity"]["legal_entity"],
            set_value=lambda p, leaf: p["identity"].__setitem__("legal_entity", leaf),
        ))
    if record.get("headquarters"):
        results.append(_decide_and_apply_scalar(
            brand_id, profile, "identity.hq_city_state", record["headquarters"],
            confidence=confidence, as_of=bpc.today(), source_url=source_url, finding_type=None, dry_run=dry_run,
            get_existing=lambda p: p["identity"]["hq_city_state"],
            set_value=lambda p, leaf: p["identity"].__setitem__("hq_city_state", leaf),
        ))
    ownership_bits = [str(v) for v in (record.get("ownership_type"), record.get("owner")) if v]
    if ownership_bits:
        results.append(_decide_and_apply_scalar(
            brand_id, profile, "identity.parent_ownership", "; ".join(ownership_bits),
            confidence=confidence, as_of=bpc.today(), source_url=source_url, finding_type=None, dry_run=dry_run,
            get_existing=lambda p: p["identity"]["parent_ownership"],
            set_value=lambda p, leaf: p["identity"].__setitem__("parent_ownership", leaf),
        ))
    return results


_FOOTPRINT_FIELD_MAP = {
    "us_and_territories_total": "total_units",
    "us_and_territories_franchised": "franchised_units",
    "us_and_territories_company_owned": "company_owned_units",
}


def _apply_footprint_snapshot(brand_id: str, record: dict, source: dict, *, dry_run: bool) -> list[dict]:
    results = []
    profile = bpc.get_profile(brand_id, persist=False)
    as_of = record.get("as_of") or bpc.today()
    for record_key, field_name in _FOOTPRINT_FIELD_MAP.items():
        value = record.get(record_key)
        if value is None:
            continue
        existing = profile["footprint"].get(field_name) or {}
        if bpc.is_human_reviewed(existing):
            results.append({"status": "skipped_human_reviewed", "applied": False, "field_path": f"footprint.{field_name}"})
            continue
        results.append(_decide_and_apply_scalar(
            brand_id, profile, f"footprint.{field_name}", value,
            confidence="high", as_of=as_of, source_url=source.get("url"), finding_type=None, dry_run=dry_run,
            get_existing=lambda p, fn=field_name: p["footprint"][fn],
            set_value=lambda p, leaf, fn=field_name: p["footprint"].__setitem__(fn, leaf),
        ))
    return results


def _record_finding_type(record: dict, findings_by_id: dict[str, dict]) -> str:
    """The strongest finding_type among the record's own finding_ids --
    cross-referenced against the packet's real findings array, never
    assumed from an unrelated finding elsewhere in the packet."""
    types = {findings_by_id[fid]["finding_type"] for fid in record.get("finding_ids") or []
             if fid in findings_by_id and findings_by_id[fid].get("finding_type")}
    if "independently_verified" in types:
        return "independently_verified"
    return next(iter(types), "marketplace_reported")


def _apply_leadership_snapshot(brand_id: str, record: dict, source: dict, *, finding_type: str, dry_run: bool) -> list[dict]:
    results = []
    profile = bpc.get_profile(brand_id, persist=False)
    verified = finding_type in _VERIFIED_FINDING_TYPES
    as_of = record.get("as_of") or bpc.today()
    for person in record.get("people") or []:
        name, title = person.get("name"), person.get("title")
        if not (name and title):
            continue
        decision = mutation_policy.decide(
            source=SOURCE_TAG, new_value=f"{name} -- {title}", is_set_member=True,
            observed_at=as_of, confidence="high" if verified else "medium",
            field_name="leadership", entity_id=brand_id,
        )
        applied = False
        if not dry_run and decision.auto_apply:
            added = bpc.add_leadership_person(profile, name=name, title=title, verified=verified,
                                               confidence="high" if verified else "medium", as_of=as_of,
                                               source_url=source.get("url"), last_reviewed_by=SOURCE_TAG)
            if added:
                bpc.save_profile(brand_id, profile)
            applied = added
        if not dry_run:
            mutation_policy.record_receipt(decision, artifact=f"brand_profile/{brand_id}.json" if applied else None, applied=applied)
        results.append({"status": decision.status, "applied": applied, "field_path": "leadership", "name": name})
    return results


def _apply_whole_record_scalar(brand_id: str, record: dict, source: dict, *, field_path: str,
                                get_existing, set_value, dry_run: bool) -> list[dict]:
    profile = bpc.get_profile(brand_id, persist=False)
    value = {k: v for k, v in record.items() if k not in {"record_type", "target_key", "source_ids", "finding_ids"}}
    as_of = record.get("as_of") or record.get("issuance_date") or bpc.today()
    return [_decide_and_apply_scalar(
        brand_id, profile, field_path, value, confidence="high", as_of=as_of,
        source_url=source.get("url"), finding_type=None, dry_run=dry_run,
        get_existing=get_existing, set_value=set_value,
    )]


def _apply_technology_record(brand_id: str, brand_entity: dict, record: dict, source: dict, *, dry_run: bool) -> dict:
    vendor = record.get("vendor")
    category = record.get("category")
    if not (vendor and category):
        return {"status": "skipped_incomplete", "applied": False}
    graph = ei._read_graph()
    vendor_id = f"vendor-{ei._slug(vendor)}"
    store = tsrp._load_store()
    is_new = tsrp._add_candidate(
        store, graph=graph, brand_id=brand_id, brand_name=brand_entity.get("name") or brand_id,
        vendor_id=vendor_id, vendor_name=vendor, category=ei._norm_category(category),
        category_confidence="stated_in_text",
        source_type="deep_research_packet", source_title=source.get("title") or f"Hunter research -- {brand_entity.get('name')}",
        source_url=source.get("url"), evidence_excerpt=str(record.get("scope") or record.get("product") or "")[:600],
        origin="hunter_brand_company_profile", origin_ref=f"{record.get('record_type')}:{vendor}:{category}",
        evidence_date=record.get("observed_at"),
    )
    if not dry_run:
        tsrp._save_store(store)
    return {"status": "candidate_added" if is_new else "candidate_already_pending", "applied": is_new and not dry_run}


def import_records(sidecar: dict, *, dry_run: bool = True) -> dict:
    research_data = sidecar
    if sidecar.get("schema") == "rb.hunter_research_packet.v1" and isinstance(sidecar.get("payload"), dict):
        research_data = sidecar["payload"]
    raw_records = research_data.get("records") or []
    records, expanded = _aggregate_profile_records(raw_records, sidecar)
    sources = _source_lookup(sidecar)
    findings_by_id = {row.get("finding_id"): row for row in sidecar.get("findings") or [] if isinstance(row, dict)}

    summary: dict[str, Any] = {
        "records_received": len(raw_records), "records_expanded": expanded,
        "typed_records_received": len(records), "records_malformed": 0, "applied": 0,
        "queued_for_review": 0, "identity_unresolved": 0, "malformed_errors": [],
        "by_record_type": {},
    }
    for idx, record in enumerate(records):
        error = _validate_record(record)
        if error:
            summary["records_malformed"] += 1
            summary["malformed_errors"].append({"index": idx, "error": error})
            continue
        try:
            brand_id, entity = _resolve_brand(record["target_key"])
        except _IdentityUnresolved as exc:
            decision = mutation_policy.decide(
                source=SOURCE_TAG, new_value=record.get("record_type"), identity_ambiguous=True,
                identity_reason=str(exc), field_name=record.get("record_type"), entity_id=record.get("target_key"),
            )
            if not dry_run:
                mutation_policy.record_receipt(decision, artifact=None, applied=False)
            summary["identity_unresolved"] += 1
            continue

        source = _primary_source(record, sources)
        record_type = record["record_type"]
        bucket = summary["by_record_type"].setdefault(record_type, {"applied": 0, "queued": 0})

        if record_type == "company_identity":
            outcomes = _apply_company_identity(brand_id, record, source, dry_run=dry_run)
        elif record_type == "footprint_snapshot":
            outcomes = _apply_footprint_snapshot(brand_id, record, source, dry_run=dry_run)
        elif record_type == "leadership_snapshot":
            finding_type = _record_finding_type(record, findings_by_id)
            outcomes = _apply_leadership_snapshot(brand_id, record, source, finding_type=finding_type, dry_run=dry_run)
        elif record_type == "franchise_disclosure":
            outcomes = _apply_whole_record_scalar(
                brand_id, record, source, field_path="franchise_disclosure",
                get_existing=lambda p: p["franchise_disclosure"],
                set_value=lambda p, leaf: p.__setitem__("franchise_disclosure", leaf),
                dry_run=dry_run,
            )
        elif record_type == "financial_operating_snapshot":
            outcomes = _apply_whole_record_scalar(
                brand_id, record, source, field_path="financial_operating_health",
                get_existing=lambda p: p["financial_operating_health"],
                set_value=lambda p, leaf: p.__setitem__("financial_operating_health", leaf),
                dry_run=dry_run,
            )
        else:  # technology_relationship / technology_observation
            outcomes = [_apply_technology_record(brand_id, entity, record, source, dry_run=dry_run)]

        for outcome in outcomes:
            if outcome.get("applied"):
                summary["applied"] += 1
                bucket["applied"] += 1
            else:
                summary["queued_for_review"] += 1
                bucket["queued"] += 1

    summary["records_rejected"] = summary["records_malformed"] + summary["identity_unresolved"]
    return summary
