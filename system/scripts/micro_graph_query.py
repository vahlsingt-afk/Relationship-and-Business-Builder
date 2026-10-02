#!/usr/bin/env python3
"""
micro_graph_query.py — compact retrieval/index layer for RB micro graphs.

The full McDonald's micro graph is intentionally large and dormant by default.
This script creates and queries a small index so RB can find the graph and answer
basic topology questions without semantic-search drift or loading graph.json for
every prompt.

Usage:
    python3 system/scripts/micro_graph_query.py index mcdonalds_us_ops
    python3 system/scripts/micro_graph_query.py summary mcdonalds_us_ops
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import audit_log  # noqa: E402
import rb_core as core  # noqa: E402

GRAPHS_DIR = core.SYSTEM_DIR / "graphs"
MICRO_DIR = GRAPHS_DIR / "micro"
REGISTRY_PATH = GRAPHS_DIR / "index.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _graph_dir(graph_slug: str) -> Path:
    return MICRO_DIR / graph_slug


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_index(graph_slug: str) -> dict:
    gdir = _graph_dir(graph_slug)
    graph_path = gdir / "graph.json"
    sources_path = gdir / "sources.json"
    activation_path = gdir / "activation.json"
    if not graph_path.exists():
        raise FileNotFoundError(f"missing graph: {graph_path}")

    graph = _load_json(graph_path)
    sources = _load_json(sources_path) if sources_path.exists() else {}
    activation = _load_json(activation_path) if activation_path.exists() else {}

    nodes_by_id = {n["id"]: n for n in graph.get("nodes", [])}
    edge_type_counts = Counter()
    node_type_counts = Counter(n.get("type") for n in nodes_by_id.values())

    stores_by_entity: defaultdict[str, set[str]] = defaultdict(set)
    stores_by_state: Counter[str] = Counter()
    stores_by_market: defaultdict[str, set[str]] = defaultdict(set)
    stores_by_field_office: defaultdict[str, set[str]] = defaultdict(set)
    stores_by_coop: defaultdict[str, set[str]] = defaultdict(set)
    role_counts: Counter[str] = Counter()

    for node in nodes_by_id.values():
        if node.get("type") == "store":
            state = (node.get("attributes") or {}).get("state")
            if state:
                stores_by_state[str(state)] += 1
        if node.get("type") == "person":
            role = (node.get("attributes") or {}).get("role") or node.get("subtype")
            if role:
                role_counts[str(role)] += 1

    for edge in graph.get("edges", []):
        etype = edge.get("type")
        edge_type_counts[etype] += 1
        from_id = edge.get("from")
        to_id = edge.get("to")
        if etype == "operated_by_entity" and from_id and to_id:
            stores_by_entity[to_id].add(from_id)
        elif etype == "belongs_to_market" and from_id and to_id:
            stores_by_market[to_id].add(from_id)
        elif etype == "covered_by_field_office" and from_id and to_id and str(from_id).startswith("store:"):
            stores_by_field_office[to_id].add(from_id)
        elif etype == "belongs_to_coop" and from_id and to_id:
            stores_by_coop[to_id].add(from_id)

    operator_records = []
    for entity_id, stores in stores_by_entity.items():
        node = nodes_by_id.get(entity_id, {})
        states = Counter()
        markets = Counter()
        for store_id in stores:
            store = nodes_by_id.get(store_id, {})
            state = (store.get("attributes") or {}).get("state")
            if state:
                states[str(state)] += 1
        for market_id, market_stores in stores_by_market.items():
            overlap = stores & market_stores
            if overlap:
                markets[market_id] = len(overlap)
        operator_records.append({
            "entity_id": entity_id,
            "name": node.get("name") or entity_id,
            "store_count": len(stores),
            "state_count": len(states),
            "states": dict(states.most_common()),
            "top_markets": [
                {
                    "market_id": market_id,
                    "name": (nodes_by_id.get(market_id) or {}).get("name") or market_id,
                    "store_count": count,
                }
                for market_id, count in markets.most_common(8)
            ],
        })
    operator_records.sort(key=lambda r: (-r["store_count"], r["name"]))

    tier_counts = {
        "enterprise_25_plus": sum(1 for r in operator_records if r["store_count"] >= 25),
        "mid_tier_5_to_24": sum(1 for r in operator_records if 5 <= r["store_count"] <= 24),
        "single_digit_1_to_4": sum(1 for r in operator_records if 1 <= r["store_count"] <= 4),
        "single_store": sum(1 for r in operator_records if r["store_count"] == 1),
    }

    state_records = [
        {"state": state, "store_count": count}
        for state, count in stores_by_state.most_common()
    ]
    market_records = [
        {
            "market_id": market_id,
            "name": (nodes_by_id.get(market_id) or {}).get("name") or market_id,
            "store_count": len(stores),
        }
        for market_id, stores in sorted(stores_by_market.items(), key=lambda item: (-len(item[1]), item[0]))
    ]
    field_office_records = [
        {
            "field_office_id": fo_id,
            "name": (nodes_by_id.get(fo_id) or {}).get("name") or fo_id,
            "store_count": len(stores),
        }
        for fo_id, stores in sorted(stores_by_field_office.items(), key=lambda item: (-len(item[1]), item[0]))
    ]
    coop_records = [
        {
            "coop_id": coop_id,
            "name": (nodes_by_id.get(coop_id) or {}).get("name") or coop_id,
            "store_count": len(stores),
        }
        for coop_id, stores in sorted(stores_by_coop.items(), key=lambda item: (-len(item[1]), item[0]))
    ]

    index = {
        "version": 1,
        "contract": "rb_micro_graph_index_v1",
        "graph_slug": graph_slug,
        "graph_id": graph.get("graph_id"),
        "name": graph.get("name"),
        "generated_at": _now(),
        "source_workbook": (sources.get("workbook") or {}).get("title"),
        "source_sha256": (sources.get("workbook") or {}).get("sha256"),
        "paths": {
            "graph": str(graph_path.relative_to(core.PROJECT_DIR)),
            "sources": str(sources_path.relative_to(core.PROJECT_DIR)) if sources_path.exists() else None,
            "activation": str(activation_path.relative_to(core.PROJECT_DIR)) if activation_path.exists() else None,
            "index": str((gdir / "index.json").relative_to(core.PROJECT_DIR)),
        },
        "activation": {
            "default_state": activation.get("default_state", "dormant"),
            "primary_terms": activation.get("primary_terms", []),
            "query_intents": activation.get("query_intents", []),
            "load_policy": activation.get("load_policy"),
        },
        "counts": {
            "nodes": len(nodes_by_id),
            "edges": len(graph.get("edges", [])),
            "node_types": dict(sorted(node_type_counts.items())),
            "edge_types": dict(sorted(edge_type_counts.items())),
            "stores_with_operator_entity": sum(len(v) for v in stores_by_entity.values()),
            "operator_entities_with_stores": len(operator_records),
            "states_with_stores": len(stores_by_state),
            "markets_with_stores": len(stores_by_market),
            "field_offices_with_stores": len(stores_by_field_office),
            "coops_with_stores": len(stores_by_coop),
            "role_person_nodes": dict(sorted(role_counts.items())),
            "operator_tiers": tier_counts,
        },
        "top_operator_entities": operator_records[:100],
        "state_distribution": state_records,
        "market_distribution": market_records,
        "field_office_distribution": field_office_records,
        "coop_distribution": coop_records,
        "retrieval_answer_templates": {
            "operator_distribution": (
                "McDonald's U.S. micro graph currently contains "
                "{operator_entities_with_stores} operator/entity nodes with mapped stores, "
                "{stores_with_operator_entity} stores mapped to an operator/entity, "
                "{enterprise_25_plus} enterprise-scale operators/entities with 25+ stores, "
                "{mid_tier_5_to_24} mid-tier operators/entities with 5-24 stores, and "
                "{single_digit_1_to_4} single-digit operators/entities with 1-4 stores."
            )
        },
        "notes": [
            "Operator/entity nodes come from workbook operational entity fields; they are not automatically personal RI contacts.",
            "Counts are workbook-derived and should be cited as micro-graph evidence, not public McDonald's system totals.",
            "Use this index for retrieval and summary. Load graph.json only for path traversal or store-level drilldown.",
        ],
    }
    # Option C: compute and embed precomputed_answers at build time
    index["precomputed_answers"] = build_precomputed_answers(index)
    return index


def build_precomputed_answers(index: dict) -> dict:
    """Compute ready-to-cite answer strings from a graph index dict.

    Called at graph build time so the answers live in index.json and daily_brief.py
    can read them without recomputing.  Covers the four standard query types:
    operator_count, largest_operators, state_distribution, coop_distribution.

    Returns a dict suitable for storage under index["precomputed_answers"].
    """
    counts = index.get("counts") or {}
    tiers = counts.get("operator_tiers") or {}
    src = index.get("source_workbook") or "RB Micro Graph"
    entity_name = index.get("name") or index.get("graph_slug") or "this graph"
    note_suffix = (
        f"Cite as: source=RB Micro Graph ({entity_name}), tier=1, confidence=verified."
    )

    answers: dict = {}

    # ── operator_count ────────────────────────────────────────────────────────
    tmpl = (index.get("retrieval_answer_templates") or {}).get("operator_distribution")
    if tmpl:
        try:
            rendered = tmpl.format(
                operator_entities_with_stores=counts.get("operator_entities_with_stores", 0),
                stores_with_operator_entity=counts.get("stores_with_operator_entity", 0),
                enterprise_25_plus=tiers.get("enterprise_25_plus", 0),
                mid_tier_5_to_24=tiers.get("mid_tier_5_to_24", 0),
                single_digit_1_to_4=tiers.get("single_digit_1_to_4", 0),
            )
            answers["operator_count"] = {
                "answer": rendered,
                "source": "graph_index",
                "tier": 1,
                "query_type": "operator_count",
                "note": f"Computed from graph index at build time. {note_suffix}",
            }
        except (KeyError, ValueError):
            pass

    # ── largest_operators ─────────────────────────────────────────────────────
    top_ops = (index.get("top_operator_entities") or [])[:10]
    if top_ops:
        lines = [
            f"{op.get('name', '?')} — {op.get('store_count', 0):,} stores"
            f" ({op.get('state_count', 0)} states)"
            for op in top_ops
        ]
        total = counts.get("operator_entities_with_stores", 0)
        answers["largest_operators"] = {
            "answer": (
                f"Top {len(top_ops)} operators by store count (source: {src}):\n"
                + "\n".join(f"{i+1}. {l}" for i, l in enumerate(lines))
                + f"\n\nTotal operator entities with mapped stores: {total:,}."
            ),
            "source": "graph_index",
            "tier": 1,
            "query_type": "largest_operators",
            "operator_list": [
                {
                    "name": op.get("name"),
                    "store_count": op.get("store_count"),
                    "state_count": op.get("state_count"),
                }
                for op in top_ops
            ],
            "note": f"Computed from graph index at build time. {note_suffix}",
        }

    # ── state_distribution ────────────────────────────────────────────────────
    state_dist = (index.get("state_distribution") or [])[:10]
    if state_dist:
        total_states = len(index.get("state_distribution") or [])
        lines = [
            f"{s.get('state', '?')}: {s.get('store_count', 0):,} stores"
            for s in state_dist
        ]
        answers["state_distribution"] = {
            "answer": (
                f"Top {len(state_dist)} states by store count"
                f" (source: {src}, {total_states} states total):\n"
                + "\n".join(lines)
            ),
            "source": "graph_index",
            "tier": 1,
            "query_type": "state_lookup",
            "state_list": state_dist,
            "total_states": total_states,
            "note": (
                f"Computed from graph index at build time. {note_suffix} "
                "For a specific state call getMicroGraphSummary with query_type=state_lookup&state=XX."
            ),
        }

    # ── coop_distribution ─────────────────────────────────────────────────────
    coop_dist = (index.get("coop_distribution") or [])[:10]
    if coop_dist:
        total_coops = len(index.get("coop_distribution") or [])
        lines = [
            f"{c.get('name', '?')}: {c.get('store_count', 0):,} stores"
            for c in coop_dist
        ]
        answers["coop_distribution"] = {
            "answer": (
                f"Top {len(coop_dist)} co-ops by store count"
                f" (source: {src}, {total_coops} co-ops total):\n"
                + "\n".join(lines)
            ),
            "source": "graph_index",
            "tier": 1,
            "query_type": "coop_lookup",
            "coop_list": coop_dist,
            "total_coops": total_coops,
            "note": f"Computed from graph index at build time. {note_suffix}",
        }

    return answers


def write_precomputed_answers(graph_slug: str) -> dict:
    """Load index.json for graph_slug, compute precomputed_answers, and write back.

    Use this to upgrade existing index.json files that were built before Option C.
    Returns the updated precomputed_answers dict.
    """
    gdir = _graph_dir(graph_slug)
    index_path = gdir / "index.json"
    if not index_path.exists():
        raise FileNotFoundError(f"index.json not found for graph: {graph_slug}")
    index = _load_json(index_path)
    answers = build_precomputed_answers(index)
    index["precomputed_answers"] = answers
    _write_json(index_path, index)
    return answers


def refresh_registry(graph_slug: str, index: dict) -> dict:
    if REGISTRY_PATH.exists():
        registry = _load_json(REGISTRY_PATH)
    else:
        registry = {"version": 1, "contract": "rb_graph_registry_v1", "graphs": []}
    graphs = [g for g in registry.get("graphs", []) if g.get("graph_slug") != graph_slug]
    graphs.append({
        "graph_slug": graph_slug,
        "graph_id": index.get("graph_id"),
        "name": index.get("name"),
        "graph_type": "micro_ecosystem",
        "status": "partial",
        "index_path": index["paths"]["index"],
        "activation_terms": index.get("activation", {}).get("primary_terms", [])[:20],
        "source_workbook": index.get("source_workbook"),
        "counts": {
            "nodes": index["counts"]["nodes"],
            "edges": index["counts"]["edges"],
            "operator_entities_with_stores": index["counts"]["operator_entities_with_stores"],
            "stores_with_operator_entity": index["counts"]["stores_with_operator_entity"],
        },
        "updated_at": index.get("generated_at"),
    })
    registry["graphs"] = sorted(graphs, key=lambda g: g.get("graph_slug", ""))
    registry["updated_at"] = _now()
    return registry


def cmd_index(args: argparse.Namespace) -> int:
    index = build_index(args.graph_slug)
    gdir = _graph_dir(args.graph_slug)
    index_path = gdir / "index.json"
    _write_json(index_path, index)
    registry = refresh_registry(args.graph_slug, index)
    _write_json(REGISTRY_PATH, registry)
    audit_log.append_event(
        "item_persisted",
        item_summary=f"Micro graph retrieval index: {args.graph_slug}",
        reason="Persist compact retrieval index so semantic search does not need to find the raw graph file.",
        outcome="persisted",
        data_class="intelligence",
        source=str(index_path.relative_to(core.PROJECT_DIR)),
        retention_class="derived_intelligence",
        extra={"graph_id": index.get("graph_id")},
    )
    if args.json:
        print(json.dumps({"ok": True, "index_path": str(index_path), "counts": index["counts"]}, indent=2))
    else:
        print(f"OK indexed {args.graph_slug}: {index['counts']['operator_entities_with_stores']} operator/entity nodes, {index['counts']['stores_with_operator_entity']} mapped stores")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    index_path = _graph_dir(args.graph_slug) / "index.json"
    if not index_path.exists():
        raise FileNotFoundError(f"missing index: {index_path}; run index first")
    index = _load_json(index_path)
    c = index["counts"]
    tiers = c["operator_tiers"]
    answer = index["retrieval_answer_templates"]["operator_distribution"].format(
        operator_entities_with_stores=c["operator_entities_with_stores"],
        stores_with_operator_entity=c["stores_with_operator_entity"],
        enterprise_25_plus=tiers["enterprise_25_plus"],
        mid_tier_5_to_24=tiers["mid_tier_5_to_24"],
        single_digit_1_to_4=tiers["single_digit_1_to_4"],
    )
    payload = {
        "graph_id": index["graph_id"],
        "source_workbook": index.get("source_workbook"),
        "answer": answer,
        "counts": c,
        "top_operator_entities": index.get("top_operator_entities", [])[: args.limit],
        "top_states": index.get("state_distribution", [])[: args.limit],
        "top_field_offices": index.get("field_office_distribution", [])[: args.limit],
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(answer)
        print("\nTop operator/entity nodes:")
        for row in payload["top_operator_entities"]:
            print(f"- {row['name']}: {row['store_count']} stores across {row['state_count']} state(s)")
        print("\nTop states:")
        for row in payload["top_states"]:
            print(f"- {row['state']}: {row['store_count']} stores")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Index/query RB micro graph artifacts.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_index = sub.add_parser("index", help="Build compact retrieval index for a micro graph.")
    p_index.add_argument("graph_slug")
    p_index.add_argument("--json", action="store_true")
    p_index.set_defaults(func=cmd_index)

    p_summary = sub.add_parser("summary", help="Print indexed micro graph summary.")
    p_summary.add_argument("graph_slug")
    p_summary.add_argument("--limit", type=int, default=10)
    p_summary.add_argument("--json", action="store_true")
    p_summary.set_defaults(func=cmd_summary)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
