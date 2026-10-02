#!/usr/bin/env python3
"""
micro_graph_mcdonalds.py — extract a McDonald's operational topology workbook
into a separate RB micro ecosystem graph.

This intentionally does not write baseline_index.json or
ecosystem_intelligence.json. The output is a scoped micro graph under
system/graphs/micro/mcdonalds_us_ops/.

Usage:
    python3 system/scripts/micro_graph_mcdonalds.py \
      "/path/to/NSN Lookup 2026-05 MAY.xlsx" --write
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import audit_log  # noqa: E402
import rb_core as core  # noqa: E402

GRAPH_ID = "micro_ecosystem:mcdonalds_us_ops"
GRAPH_DIR = core.SYSTEM_DIR / "graphs" / "micro" / "mcdonalds_us_ops"
GRAPH_PATH = GRAPH_DIR / "graph.json"
SOURCES_PATH = GRAPH_DIR / "sources.json"
ACTIVATION_PATH = GRAPH_DIR / "activation.json"
README_PATH = GRAPH_DIR / "README.md"


def _today() -> str:
    return date.today().isoformat()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slug(value: Any, fallback: str = "unknown") -> str:
    text = "" if value is None else str(value).strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return slug or fallback


def _clean(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, str):
        text = value.strip()
        return text if text else None
    return value


def _norm_email(value: Any) -> str | None:
    text = _clean(value)
    if not text:
        return None
    text = str(text).strip().lower()
    return text if "@" in text else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sheet_ref(source_id: str, sheet: str, row: int | None = None, col: str | None = None) -> str:
    ref = f"{source_id}:{sheet}"
    if row is not None:
        ref += f"!{row}"
    if col:
        ref += f":{col}"
    return ref


def _headers(ws) -> dict[str, int]:
    row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
    out: dict[str, int] = {}
    for i, value in enumerate(row):
        if value is None:
            continue
        out[str(value).strip()] = i
    return out


def _value(row: tuple, headers: dict[str, int], name: str) -> Any:
    idx = headers.get(name)
    if idx is None or idx >= len(row):
        return None
    return _clean(row[idx])


_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class GraphBuilder:
    def __init__(self, source_id: str):
        self.source_id = source_id
        self.nodes: dict[str, dict] = {}
        self.edges: dict[str, dict] = {}
        self.source_counts: Counter[str] = Counter()

    def node(
        self,
        node_id: str,
        node_type: str,
        name: str,
        *,
        subtype: str | None = None,
        attributes: dict | None = None,
        sources: list[str] | None = None,
        confidence_level: str = "high",
        confidence_score: float = 0.9,
    ) -> None:
        existing = self.nodes.get(node_id)
        if existing is None:
            self.nodes[node_id] = {
                "id": node_id,
                "type": node_type,
                "subtype": subtype,
                "name": name,
                "attributes": {k: v for k, v in (attributes or {}).items() if v is not None},
                "sources": [],
                "confidence": {
                    "level": confidence_level,
                    "score": confidence_score,
                    "rationale": "Extracted from structured workbook source table.",
                },
            }
        else:
            # 2026-10-01: narrow defensive guard -- see the identical
            # comment in micro_graph_builder.py's node() for the real,
            # confirmed bug (ecosystem_intelligence.py's _upsert_entity())
            # this guards against reintroducing if this builder ever
            # starts loading a prior persisted graph.json into a new run.
            # A higher-confidence existing attribute is never overwritten
            # by a lower-confidence incoming one.
            incoming_rank = _CONFIDENCE_RANK.get(confidence_level, 0)
            existing_rank = _CONFIDENCE_RANK.get((existing.get("confidence") or {}).get("level"), 0)
            if incoming_rank >= existing_rank:
                existing["attributes"].update({k: v for k, v in (attributes or {}).items() if v is not None})
            else:
                for k, v in (attributes or {}).items():
                    if v is not None and existing["attributes"].get(k) is None:
                        existing["attributes"][k] = v
            if subtype and not existing.get("subtype"):
                existing["subtype"] = subtype
        for src in sources or []:
            if src not in self.nodes[node_id]["sources"]:
                self.nodes[node_id]["sources"].append(src)

    def edge(
        self,
        edge_type: str,
        from_id: str,
        to_id: str,
        *,
        attributes: dict | None = None,
        sources: list[str] | None = None,
        confidence_level: str = "high",
        confidence_score: float = 0.9,
    ) -> None:
        if from_id not in self.nodes or to_id not in self.nodes:
            return
        edge_id = f"edge:{edge_type}:{from_id}:{to_id}"
        existing = self.edges.get(edge_id)
        if existing is None:
            self.edges[edge_id] = {
                "id": edge_id,
                "type": edge_type,
                "from": from_id,
                "to": to_id,
                "attributes": {k: v for k, v in (attributes or {}).items() if v is not None},
                "sources": [],
                "confidence": {
                    "level": confidence_level,
                    "score": confidence_score,
                    "rationale": "Extracted from structured workbook source table.",
                },
            }
        else:
            existing["attributes"].update({k: v for k, v in (attributes or {}).items() if v is not None})
        for src in sources or []:
            if src not in self.edges[edge_id]["sources"]:
                self.edges[edge_id]["sources"].append(src)


def _person_id(role: str, name: Any = None, email: Any = None, scope: Any = None) -> str | None:
    email_norm = _norm_email(email)
    if email_norm:
        return f"person:{role}:{_slug(email_norm)}"
    if name:
        scope_slug = _slug(scope, "global") if scope else "global"
        return f"person:{role}:{scope_slug}:{_slug(name)}"
    return None


def _add_person_role(
    g: GraphBuilder,
    *,
    role: str,
    name: Any,
    email: Any = None,
    scope: Any = None,
    source: str,
) -> str | None:
    pid = _person_id(role, name, email, scope)
    if not pid:
        return None
    email_norm = _norm_email(email)
    g.node(
        pid,
        "person",
        str(name or email_norm),
        subtype=role,
        attributes={
            "role": role,
            "email": email_norm,
            "scope": scope,
            "privacy_note": "Phone numbers intentionally omitted from the micro graph.",
        },
        sources=[source],
        confidence_level="medium" if not email_norm else "high",
        confidence_score=0.75 if not email_norm else 0.9,
    )
    return pid


def build_graph(workbook_path: Path) -> tuple[dict, dict, dict, str]:
    source_hash = _sha256(workbook_path)
    source_id = f"src-workbook-{_slug(workbook_path.stem)}-{source_hash[:10]}"
    source_file = {
        "id": source_id,
        "source_type": "xlsx_micro_topology_workbook",
        "title": workbook_path.name,
        "path": str(workbook_path),
        "sha256": source_hash,
        "captured_at": _now(),
        "retention_class": "derived_intelligence",
        "notes": "Raw upload treated as source material; graph projection is the durable query artifact.",
    }

    wb = load_workbook(workbook_path, read_only=True, data_only=True)
    formula_wb = load_workbook(workbook_path, read_only=True, data_only=False)
    g = GraphBuilder(source_id)

    g.node(
        "ecosystem:mcdonalds-us-ops",
        "ecosystem",
        "McDonald's US Operations",
        subtype="micro_ecosystem",
        attributes={"graph_id": GRAPH_ID, "domain": "restaurants"},
        sources=[source_id],
    )
    g.node(
        "brand:mcdonalds",
        "brand",
        "McDonald's",
        subtype="restaurant_brand",
        attributes={"domain": "restaurants"},
        sources=[source_id],
    )
    g.edge("contains_brand", "ecosystem:mcdonalds-us-ops", "brand:mcdonalds", sources=[source_id])

    source_tables: list[dict] = []
    for ws in wb.worksheets:
        formula_ws = formula_wb[ws.title]
        formula_cells = 0
        literal_cells = 0
        for row in formula_ws.iter_rows():
            for cell in row:
                if cell.value is None:
                    continue
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    formula_cells += 1
                else:
                    literal_cells += 1
        source_tables.append({
            "sheet": ws.title,
            "max_row": ws.max_row,
            "max_column": ws.max_column,
            "formula_cells": formula_cells,
            "literal_cells": literal_cells,
        })

    # Markets: coop -> market -> field office and role/person mappings.
    if "Markets" in wb.sheetnames:
        ws = wb["Markets"]
        h = _headers(ws)
        for excel_row, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            coop = _value(row, h, "RFM CoOp Name")
            market = _value(row, h, "Market")
            field_office = _value(row, h, "Field Office")
            if not coop and not market and not field_office:
                continue
            src = _sheet_ref(source_id, "Markets", excel_row)
            if field_office:
                fo_id = f"field_office:{_slug(field_office)}"
                g.node(fo_id, "field_office", str(field_office), sources=[src])
                g.edge("belongs_to_ecosystem", fo_id, "ecosystem:mcdonalds-us-ops", sources=[src])
            else:
                fo_id = None
            if market:
                market_id = f"market:{_slug(market)}"
                g.node(market_id, "market", str(market), sources=[src])
                g.edge("belongs_to_ecosystem", market_id, "ecosystem:mcdonalds-us-ops", sources=[src])
                if fo_id:
                    g.edge("covered_by_field_office", market_id, fo_id, sources=[src])
            else:
                market_id = None
            if coop:
                coop_id = f"coop:{_slug(coop)}"
                g.node(coop_id, "coop", str(coop), sources=[src])
                g.edge("belongs_to_ecosystem", coop_id, "ecosystem:mcdonalds-us-ops", sources=[src])
                if market_id:
                    g.edge("maps_to_market", coop_id, market_id, sources=[src])
                if fo_id:
                    g.edge("covered_by_field_office", coop_id, fo_id, sources=[src])
            for role, name_col, email_col in [
                ("otm", "OTM - Name", "OTM - Email"),
                ("stim", "STIM - Name", "STIM - Email"),
            ]:
                person_id = _add_person_role(
                    g,
                    role=role,
                    name=_value(row, h, name_col),
                    email=_value(row, h, email_col),
                    scope=field_office or market or coop,
                    source=src,
                )
                if person_id:
                    if fo_id:
                        g.edge("role_covers_field_office", person_id, fo_id, sources=[src])
                    if market_id:
                        g.edge("role_covers_market", person_id, market_id, sources=[src])
                    if coop:
                        g.edge("role_covers_coop", person_id, f"coop:{_slug(coop)}", sources=[src])

    # FO OTM-STIM: field office role/person mappings.
    if "FO OTM-STIM" in wb.sheetnames:
        ws = wb["FO OTM-STIM"]
        h = _headers(ws)
        for excel_row, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            field_office = _value(row, h, "Field Office")
            if not field_office:
                continue
            src = _sheet_ref(source_id, "FO OTM-STIM", excel_row)
            fo_id = f"field_office:{_slug(field_office)}"
            g.node(fo_id, "field_office", str(field_office), attributes={"store_count_formula_value": _value(row, h, "Count")}, sources=[src])
            g.edge("belongs_to_ecosystem", fo_id, "ecosystem:mcdonalds-us-ops", sources=[src])
            for role, name_col, email_col in [
                ("otm", "OTM - Name", "OTM - Email"),
                ("stim", "STIM - Name", "STIM - Email"),
            ]:
                person_id = _add_person_role(
                    g,
                    role=role,
                    name=_value(row, h, name_col),
                    email=_value(row, h, email_col),
                    scope=field_office,
                    source=src,
                )
                if person_id:
                    g.edge("role_covers_field_office", person_id, fo_id, sources=[src])

    # RFM and COOP2: store -> coop mappings.
    store_to_coops: dict[str, set[str]] = {}
    for sheet, nsn_col, coop_col in [
        ("RFM", "NSN", "COOP as of 04/14/26"),
        ("COOP2", "STORE_CODE", "Coop"),
    ]:
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        h = _headers(ws)
        for excel_row, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            nsn = _value(row, h, nsn_col)
            coop = _value(row, h, coop_col)
            if nsn is None or not coop:
                continue
            store_id = f"store:{_slug(nsn)}"
            coop_id = f"coop:{_slug(coop)}"
            src = _sheet_ref(source_id, sheet, excel_row)
            g.node(coop_id, "coop", str(coop), sources=[src])
            store_to_coops.setdefault(store_id, set()).add(coop_id)

    # Entity: entity -> FBP mappings.
    if "Entity" in wb.sheetnames:
        ws = wb["Entity"]
        h = _headers(ws)
        for excel_row, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            entity = _value(row, h, "Entity")
            fbp = _value(row, h, "FBP")
            email = _value(row, h, "EMAIL")
            if not entity:
                continue
            src = _sheet_ref(source_id, "Entity", excel_row)
            entity_id = f"entity:{_slug(entity)}"
            g.node(entity_id, "entity", str(entity), subtype="operator_entity", sources=[src])
            g.edge("belongs_to_ecosystem", entity_id, "ecosystem:mcdonalds-us-ops", sources=[src])
            fbp_id = _add_person_role(g, role="fbp", name=fbp, email=email, scope=entity, source=src)
            if fbp_id:
                g.edge("fbp_for_entity", fbp_id, entity_id, sources=[src])

    # StoreTech: store/site base table plus operational links.
    if "StoreTech" in wb.sheetnames:
        ws = wb["StoreTech"]
        h = _headers(ws)
        for excel_row, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            nsn = _value(row, h, str(next(iter(h.keys())))) if False else _clean(row[0] if row else None)
            if nsn is None:
                continue
            src = _sheet_ref(source_id, "StoreTech", excel_row)
            store_id = f"store:{_slug(nsn)}"
            field_office = _value(row, h, "Field Office")
            market = _value(row, h, "Market")
            entity = _value(row, h, "Entity Name")
            fbp_name = _value(row, h, "FBP Name")
            fbp_email = _value(row, h, "FBP E-Mail")
            operator_name = _value(row, h, "Operator Name")
            operator_email = _value(row, h, "Operator Email")
            otm_name = _value(row, h, "OTM - Name")
            otm_email = _value(row, h, "OTM - Email")
            otp_name = _value(row, h, "OTP Name")
            otp_email = _value(row, h, "OTP Email")
            store_attrs = {
                "nsn": nsn,
                "bli": _value(row, h, "BLI #"),
                "state_site": _value(row, h, "State Site #"),
                "mcopco": _value(row, h, "McOpCo #"),
                "status": _value(row, h, "Status Code"),
                "zone": _value(row, h, "Zone"),
                "region": _value(row, h, "Region"),
                "restaurant_name": _value(row, h, "Restaurant Name"),
                "address_line_1": _value(row, h, "Address Line #1"),
                "city": _value(row, h, "City"),
                "state": _value(row, h, "State"),
                "zip": _value(row, h, "Zip"),
                "county": _value(row, h, "County"),
                "latitude": _value(row, h, "Latitude"),
                "longitude": _value(row, h, "Longitude"),
                "timezone": _value(row, h, "Timezone"),
                "effective_date": _value(row, h, "Effective Date"),
                "store_type": _value(row, h, "Store Type"),
                "building_type": _value(row, h, "Building Type"),
            }
            g.node(store_id, "store", f"Store {nsn}", subtype="restaurant_store", attributes=store_attrs, sources=[src])
            g.edge("belongs_to_brand", store_id, "brand:mcdonalds", sources=[src])
            if field_office:
                fo_id = f"field_office:{_slug(field_office)}"
                g.node(fo_id, "field_office", str(field_office), sources=[src])
                g.edge("covered_by_field_office", store_id, fo_id, sources=[src])
            if market:
                market_id = f"market:{_slug(market)}"
                g.node(market_id, "market", str(market), sources=[src])
                g.edge("belongs_to_market", store_id, market_id, sources=[src])
            for coop_id in store_to_coops.get(store_id, set()):
                g.edge("belongs_to_coop", store_id, coop_id, sources=[src])
            if entity:
                entity_id = f"entity:{_slug(entity)}"
                g.node(entity_id, "entity", str(entity), subtype="operator_entity", attributes={"entity_id": _value(row, h, "Entity ID")}, sources=[src])
                g.edge("operated_by_entity", store_id, entity_id, sources=[src])
            for role, name, email, edge_type in [
                ("fbp", fbp_name, fbp_email, "fbp_for_store"),
                ("operator", operator_name, operator_email, "operator_for_store"),
                ("otm", otm_name, otm_email, "otm_for_store"),
                ("otp", otp_name, otp_email, "otp_for_store"),
            ]:
                person_id = _add_person_role(g, role=role, name=name, email=email, scope=field_office or market or nsn, source=src)
                if person_id:
                    g.edge(edge_type, person_id, store_id, sources=[src])

    nodes = sorted(g.nodes.values(), key=lambda x: x["id"])
    edges = sorted(g.edges.values(), key=lambda x: x["id"])
    node_counts = Counter(n["type"] for n in nodes)
    edge_counts = Counter(e["type"] for e in edges)

    graph = {
        "version": 1,
        "contract": "rb_micro_ecosystem_graph_v1",
        "graph_id": GRAPH_ID,
        "name": "McDonald's US Operations Micro Ecosystem",
        "description": "Micro-topology graph extracted from the NSN Lookup workbook. Kept separate from personal RI and macro industry intelligence.",
        "domain": "restaurants",
        "created_at": _now(),
        "last_updated": _today(),
        "source_workbook": source_id,
        "activation_policy": {
            "default": "dormant",
            "activate_when": [
                "query mentions McDonald's operational topology, NSN, store/site, field office, market, coop, OTM, STIM, RFM, FBP, OTP",
                "active thread or meeting prep references McDonald's deployment, store rollout, field support, or operational accountability",
            ],
        },
        "privacy_policy": {
            "raw_phone_numbers_included": False,
            "personal_relationship_promotion": "never automatic; requires RI evidence or user confirmation",
            "graph_scope": "micro_ecosystem",
        },
        "quality": {
            "confidence_model": "High for literal structured table mappings; medium for role/person mappings without email; formula-derived lookup tab is source context, not canonical topology.",
            "known_limits": [
                "Workbook values are treated as operational source evidence, not externally corroborated truth.",
                "NSN-Lookup formulas are not used as the primary source of topology when source tables are available.",
                "Phone numbers are intentionally omitted from person nodes.",
            ],
        },
        "counts": {
            "nodes": len(nodes),
            "edges": len(edges),
            "node_types": dict(sorted(node_counts.items())),
            "edge_types": dict(sorted(edge_counts.items())),
        },
        "nodes": nodes,
        "edges": edges,
    }
    sources = {
        "version": 1,
        "graph_id": GRAPH_ID,
        "generated_at": _now(),
        "workbook": source_file,
        "sheets": source_tables,
    }
    activation = {
        "version": 1,
        "graph_id": GRAPH_ID,
        "default_state": "dormant",
        "primary_terms": [
            "mcdonalds",
            "mcdonald's",
            "nsn",
            "storetech",
            "store tech",
            "field office",
            "coop",
            "co-op",
            "rfm",
            "otm",
            "stim",
            "fbp",
            "otp",
            "site id",
            "store code",
        ],
        "query_intents": [
            "store lookup",
            "field support routing",
            "market mapping",
            "operational accountability",
            "deployment topology",
            "role coverage",
            "finance business partner mapping",
        ],
        "load_policy": "Load graph.json only for matching query intent or active-thread evidence. Otherwise keep dormant.",
    }
    readme = f"""# McDonald's US Operations Micro Graph

Generated: {graph['created_at']}

This directory is a micro ecosystem graph projection from `{workbook_path.name}`.
It is intentionally separate from `baseline_index.json` and `ecosystem_intelligence.json`.

## Counts

- Nodes: {len(nodes)}
- Edges: {len(edges)}
- Source workbook SHA-256: `{source_hash}`

## Files

- `graph.json` — normalized topology nodes and edges
- `sources.json` — workbook/sheet metadata and provenance
- `activation.json` — terms and intents that should activate this graph

## Guardrails

- Raw workbook is source material, not the durable intelligence artifact.
- Phone numbers are intentionally omitted from person nodes.
- Operational people are not promoted into Todd's personal relationship graph automatically.
- Use this graph for McDonald's operational topology questions, not broad restaurant industry analysis.
"""
    return graph, sources, activation, readme


def write_outputs(graph: dict, sources: dict, activation: dict, readme: str, *, source_path: Path) -> None:
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    GRAPH_PATH.write_text(json.dumps(graph, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    SOURCES_PATH.write_text(json.dumps(sources, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ACTIVATION_PATH.write_text(json.dumps(activation, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    README_PATH.write_text(readme, encoding="utf-8")
    audit_log.append_event(
        "source_accessed",
        item_summary=f"McDonald's micro topology workbook: {source_path.name}",
        reason="Extract source workbook into micro ecosystem graph.",
        outcome="accessed",
        data_class="raw_source",
        source=str(source_path),
        retention_class="source_cache",
    )
    audit_log.append_event(
        "item_persisted",
        item_summary=f"McDonald's micro ecosystem graph ({graph['counts']['nodes']} nodes, {graph['counts']['edges']} edges)",
        reason="Persist normalized topology graph projection separate from personal RI and macro ecosystem graph.",
        outcome="persisted",
        data_class="intelligence",
        source=str(GRAPH_PATH.relative_to(core.PROJECT_DIR)),
        retention_class="derived_intelligence",
        extra={"graph_id": GRAPH_ID},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract McDonald's NSN workbook into RB micro graph artifacts.")
    parser.add_argument("workbook", help="Path to NSN Lookup workbook")
    parser.add_argument("--write", action="store_true", help="Write system/graphs/micro/mcdonalds_us_ops artifacts")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args(argv)

    workbook = Path(args.workbook).expanduser()
    if not workbook.exists():
        print(f"ERROR: workbook not found: {workbook}", file=sys.stderr)
        return 2

    graph, sources, activation, readme = build_graph(workbook)
    if args.write:
        write_outputs(graph, sources, activation, readme, source_path=workbook)
    summary = {
        "ok": True,
        "written": bool(args.write),
        "graph_id": graph["graph_id"],
        "graph_path": str(GRAPH_PATH.relative_to(core.PROJECT_DIR)),
        "source_workbook": sources["workbook"]["title"],
        "source_sha256": sources["workbook"]["sha256"],
        "counts": graph["counts"],
        "sheets": sources["sheets"],
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"OK {graph['graph_id']}: {graph['counts']['nodes']} nodes, {graph['counts']['edges']} edges")
        if args.write:
            print(f"Wrote {GRAPH_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
