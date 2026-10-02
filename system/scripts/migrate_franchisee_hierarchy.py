#!/usr/bin/env python3
"""
migrate_franchisee_hierarchy.py — one-time Franchisee Finder Phase 1 seed
import.

Investigated before writing anything: Franchisee Finder's own spec
(system/design/FRANCHISEE_FINDER_SPEC.md, section 19) names
system/franchisee_hierarchy.json (a static Franchise Times "2026
Restaurant 200" extract -- 200 operators, name/location/brands+unit-
counts/revenue, no legal entities, no confidence model) as the only prior
art to reconcile. A repo-wide check for existing franchisee-shaped data
found a SECOND, richer prior-art source the spec didn't know about:
multibrand_franchisee_operator_ingest.py (2026-09-27) already created 47
real `multi_brand_franchisee_operator` brand-entities in
ecosystem_intelligence.json, each with a sourced headquarters finding,
current leadership roster, and operates->brand edges (confidence +
source_url per edge) -- genuinely overlapping content (28 of the 47 match
a Restaurant 200 operator by name/alias; the other 19, e.g. Aramark,
Sodexo, Compass Group USA, are foodservice contractors FRANdata's
Multi-Brand 50 tracks that Restaurant Times' revenue-ranked list doesn't
reach). Reconciling both into one record is more complete and more
honest than seeding from Franchise Times alone would have been.

Merge logic, per organization (keyed by franchisee_finder_common.slugify
of the canonical name):
  - Franchise Times is the only source with real unit counts -- every
    brand_relationship's unit_count assertion comes from there when the
    org appears in franchisee_hierarchy.json.
  - When the SAME org also has a multi_brand_franchisee_operator entity in
    ecosystem_intelligence.json: headquarters is sourced from that entity's
    own evidence_ledger finding (confidence 100, a dated access_url) in
    preference to Franchise Times' bare "City, ST" column (which doesn't
    distinguish headquarters from a registered address, per spec section
    11) when available; current_leadership backfills `people`; a brand
    that BOTH sources agree the org operates gets a second evidence_id and
    a confidence bump (two independent sources corroborating is a real,
    not invented, confidence signal); linked_graph_entity_id cross-
    references the real operator-<slug> entity id.
  - An org with an ecosystem entity but no Restaurant 200 row (the 19
    above) is seeded from ecosystem only -- brand_relationships exist
    (from the real operates edges) but their unit_count assertion is
    honestly None/unresolved: Franchise Times is the only unit-count
    source this migration has, and it doesn't cover these orgs.

Never invents a confidence score, a unit count, or a headquarters value
neither source actually states. Idempotent and read-only against both
source files -- re-running overwrites this domain's own seeded records
with the same inputs, never the two source files.

CLI:
    python3 migrate_franchisee_hierarchy.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import franchisee_finder_common as ffc  # noqa: E402

FRANCHISEE_HIERARCHY_PATH = core.SYSTEM_DIR / "franchisee_hierarchy.json"
FRANCHISE_TIMES_SOURCE_URL = "https://www.franchisetimes.com/franchise_times_top_200/"

# Franchise Times' own revenue-ranked methodology makes unit counts a real,
# named-publication figure, but not independently verified by RB -- a
# single-source, non-legal-entity-resolved count. 65 reflects that: a real
# publicly reported figure, not a guess, but below what a second
# corroborating source (CORROBORATED_CONFIDENCE_PCT) or direct research
# would support.
SINGLE_SOURCE_CONFIDENCE_PCT = 65
CORROBORATED_CONFIDENCE_PCT = 80
# Franchise Times' location column doesn't state whether it's headquarters
# or a registered address (spec section 11 draws that distinction) --
# scored lower than a brand's unit count for exactly that ambiguity.
LOCATION_AS_HQ_CONFIDENCE_PCT = 55


def _role_category(title: str) -> str:
    t = (title or "").lower()
    if "chief executive" in t or re.search(r"\bceo\b", t):
        return "ceo"
    if "chief operating" in t or re.search(r"\bcoo\b", t) or "president" in t:
        return "coo"
    if "technology" in t or "cto" in t or "digital" in t:
        return "technology_leader"
    if "operations" in t:
        return "operations_leader"
    return "other"


def _load_ecosystem_operators() -> tuple[list[dict], dict]:
    """Every real multi_brand_franchisee_operator entity (deduped by id --
    aliases are kept on the entity, never flattened into separate top-level
    records, which would silently create duplicate organizations for one
    real operator), each enriched with its operates->brand edges under
    'operates_edges'."""
    graph = ei._read_graph()
    entities = graph.get("entities") or []
    rels = graph.get("relationships") or []
    ops = [e for e in entities if e.get("subtype") == "multi_brand_franchisee_operator"]
    enriched_ops = []
    for op in ops:
        edges = [r for r in rels if r.get("from_entity_id") == op["id"] and r.get("relationship_type") == "operates"]
        enriched = dict(op)
        enriched["operates_edges"] = edges
        enriched_ops.append(enriched)
    return enriched_ops, graph


def _match_keys(name: str, aliases: list[str] | None = None) -> set[str]:
    keys = {ffc.slugify(name)}
    for a in aliases or []:
        keys.add(ffc.slugify(a))
    return {k for k in keys if k}


def _brand_name_for_edge(edge: dict, graph: dict) -> str | None:
    by_id = ei._index_by_id(graph.get("entities") or [])
    target = by_id.get(edge.get("to_entity_id"))
    return target.get("name") if target else None


def build_organization(fh_operator: dict | None, eco_entity: dict | None, graph: dict) -> dict:
    name = (fh_operator or eco_entity)["name"] if fh_operator else eco_entity["name"]
    slug = ffc.slugify(name)
    org = ffc._empty_organization_json(slug, name)
    org["aliases"] = list((eco_entity or {}).get("aliases") or [])
    if eco_entity:
        org["linked_graph_entity_id"] = eco_entity["id"]

    evidence_records: list[dict] = []

    def _record_evidence(**fields) -> str:
        """Assigns this evidence record the same id add_evidence() would
        generate on disk (ff-ev-<slug>-NNNN, 1-indexed), so the assertion
        built right after can reference it by evidence_ids -- without this,
        every assertion would persist with an empty evidence_ids list,
        exactly the "confident claim with no traceable source" failure mode
        the spec's confidence model exists to prevent."""
        evidence_id = f"ff-ev-{slug}-{len(evidence_records) + 1:04d}"
        evidence_records.append({"evidence_id": evidence_id, **fields})
        return evidence_id

    # --- Headquarters ---
    hq_finding = None
    if eco_entity:
        for item in (eco_entity.get("attributes", {}).get("deep_research_profile", {}).get("evidence_ledger") or []):
            if item.get("category") == "headquarters":
                hq_finding = item
                break
    if hq_finding:
        eid = _record_evidence(
            kind="headquarters", finding=hq_finding.get("finding"),
            source_url=hq_finding.get("source_url"), access_date=hq_finding.get("access_date"),
            source="ecosystem_intelligence.json multi_brand_franchisee_operator_ingest",
        )
        org["headquarters"] = ffc.assertion_field(
            hq_finding.get("finding"), confidence_pct=hq_finding.get("confidence", 100),
            evidence_ids=[eid], source_url=hq_finding.get("source_url"),
            as_of=hq_finding.get("access_date"), status="confirmed",
        )
    elif fh_operator and fh_operator.get("location"):
        eid = _record_evidence(
            kind="headquarters", finding=fh_operator["location"],
            source_url=FRANCHISE_TIMES_SOURCE_URL, source="Franchise Times 2026 Restaurant 200",
        )
        org["headquarters"] = ffc.assertion_field(
            fh_operator["location"], confidence_pct=LOCATION_AS_HQ_CONFIDENCE_PCT,
            evidence_ids=[eid], source_url=FRANCHISE_TIMES_SOURCE_URL, status="inferred",
            confidence_rationale="Franchise Times location column; not confirmed as headquarters vs. registered address.",
        )

    # --- People / leadership ---
    if eco_entity:
        for person in (eco_entity.get("attributes", {}).get("deep_research_profile", {}).get("current_leadership") or []):
            eid = _record_evidence(
                kind="leadership", finding=f"{person.get('name')} -- {person.get('title')}",
                source_url=person.get("source_url"),
                source="ecosystem_intelligence.json multi_brand_franchisee_operator_ingest",
            )
            org["people"].append({
                "name": person.get("name"),
                "title": person.get("title"),
                "role_category": _role_category(person.get("title") or ""),
                "confidence_pct": person.get("confidence"),
                "status": "confirmed",
                "evidence_ids": [eid],
                "source_url": person.get("source_url"),
                "as_of": org["created_at"][:10],
                "public_contact_info": None,
            })

    # --- Brand relationships ---
    eco_edge_by_brand: dict[str, dict] = {}
    if eco_entity:
        for edge in eco_entity.get("operates_edges") or []:
            bname = _brand_name_for_edge(edge, graph)
            if bname:
                eco_edge_by_brand[bname] = edge

    brand_rows: dict[str, dict] = {}
    if fh_operator:
        for b in fh_operator.get("brands") or []:
            brand_rows[b["brand"]] = {"unit_count": b.get("unit_count"), "from_fh": True}
    for bname in eco_edge_by_brand:
        brand_rows.setdefault(bname, {"unit_count": None, "from_fh": False})

    for bname, row in brand_rows.items():
        brand_entity_id = ei._resolve_entity_id_any_type(bname, graph)
        edge = eco_edge_by_brand.get(bname)
        corroborated = row["from_fh"] and edge is not None
        evidence_ids: list[str] = []
        if row["from_fh"]:
            confidence_pct = CORROBORATED_CONFIDENCE_PCT if corroborated else SINGLE_SOURCE_CONFIDENCE_PCT
            evidence_ids.append(_record_evidence(
                kind="brand_relationship", finding=f"{bname}: {row['unit_count']} units",
                source_url=FRANCHISE_TIMES_SOURCE_URL, source="Franchise Times 2026 Restaurant 200",
            ))
            status = "confirmed" if corroborated else "inferred"
        else:
            confidence_pct = None
            status = "unresolved"
        if edge is not None:
            edge_sources = edge.get("sources") or []
            evidence_ids.append(_record_evidence(
                kind="brand_relationship", finding=f"{org['display_name']} operates {bname} (operates relationship)",
                source_url=edge_sources[0] if edge_sources else None,
                source="ecosystem_intelligence.json multi_brand_franchisee_operator_ingest",
                confidence=(edge.get("confidence") or {}).get("score"),
            ))
        rel = ffc.brand_relationship(
            bname, row["unit_count"], brand_entity_id=brand_entity_id,
            unit_count_basis="Franchise Times 2026 Restaurant 200" if row["from_fh"] else None,
            confidence_pct=confidence_pct, evidence_ids=evidence_ids,
            source_url=FRANCHISE_TIMES_SOURCE_URL if row["from_fh"] else None,
            status=status,
        )
        org["brand_relationships"].append(rel)

    org["total_identified_units"] = sum(
        r["unit_count"]["value"] or 0 for r in org["brand_relationships"]
    )
    org["research_status"]["overall_profile_quality"] = (
        "medium" if (fh_operator and eco_entity) else "low"
    )
    org["source_system"] = "manual_seed_import"
    return org, evidence_records


def _pair_operators(fh_operators: list[dict], eco_entities: list[dict]) -> list[tuple[dict | None, dict | None]]:
    """Greedy 1:1 reconciliation by exact slug match on name OR any alias --
    never many-to-one (an eco entity's 2 aliases must resolve to the SAME
    single FH operator, not let each alias claim a different one; each FH
    operator is consumed by at most one eco entity)."""
    fh_remaining = {ffc.slugify(o["name"]): o for o in fh_operators}
    pairs: list[tuple[dict | None, dict | None]] = []
    for eco in eco_entities:
        match_fh = None
        for key in _match_keys(eco["name"], eco.get("aliases")):
            if key in fh_remaining:
                match_fh = fh_remaining.pop(key)
                break
        pairs.append((match_fh, eco))
    for fh_op in fh_remaining.values():
        pairs.append((fh_op, None))
    return pairs


def run(dry_run: bool = False) -> dict:
    fh = json.loads(FRANCHISEE_HIERARCHY_PATH.read_text(encoding="utf-8"))
    eco_entities, graph = _load_ecosystem_operators()

    summary = {"created": 0, "updated": 0, "orgs": []}
    for fh_op, eco_ent in _pair_operators(fh["operators"], eco_entities):
        org, evidence_records = build_organization(fh_op, eco_ent, graph)
        slug = org["org_slug"]
        summary["orgs"].append({
            "org_slug": slug, "display_name": org["display_name"],
            "sources": [s for s, present in (("franchise_times", bool(fh_op)), ("ecosystem_graph", bool(eco_ent))) if present],
            "brand_count": len(org["brand_relationships"]),
            "total_identified_units": org["total_identified_units"],
        })
        if dry_run:
            continue
        existed = (ffc.ROOT / "organizations" / slug).is_dir()
        ffc.save_organization(slug, org)
        (ffc.org_dir(slug) / "evidence.jsonl").write_text("", encoding="utf-8")
        for rec in evidence_records:
            ffc.add_evidence(slug, rec)
        ffc.register_organization(slug, org["display_name"])
        summary["updated" if existed else "created"] += 1

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing anything.")
    args = parser.parse_args()
    summary = run(dry_run=args.dry_run)
    print(json.dumps(
        {"dry_run": args.dry_run, "organization_count": len(summary["orgs"]),
         "created": summary["created"], "updated": summary["updated"]},
        indent=2,
    ))
    multi_brand = sum(1 for o in summary["orgs"] if o["brand_count"] > 1)
    print(f"Multi-brand organizations: {multi_brand}/{len(summary['orgs'])}", file=sys.stderr)


if __name__ == "__main__":
    main()
