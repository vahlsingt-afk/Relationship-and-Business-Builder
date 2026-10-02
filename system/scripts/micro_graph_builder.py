#!/usr/bin/env python3
"""
micro_graph_builder.py — Generalized ETL framework for building RB intelligence
artifact micro graphs from arbitrary source data.

Unlike micro_graph_mcdonalds.py (McDonald's NSN-specific xlsx parser), this
builder is source-agnostic and artifact-agnostic:

  - Works from any artifact registered in system/artifacts/registry.json
  - Accepts text paste, CSV/TSV, JSON, or xlsx (summary-level) source formats
  - Extracts entities (companies, people, products, customers) via deterministic
    heuristics + artifact's own trigger terms and scope domains
  - Produces the standard micro graph artifact set under
      system/graphs/micro/<graph_slug>/
        graph.json        — nodes + edges
        sources.json      — source lineage stack
        index.json        — activation terms + query intents
        activation.json   — load policy (dormant by default)
  - Updates system/artifacts/registry.json (stub → building)
  - Updates system/graphs/micro/index.json (global presence index)
  - Review-first: dry-run by default; --write --confirm to persist

For entity-specific structured workbooks (e.g. McDonald's NSN .xlsx),
use the dedicated parser micro_graph_mcdonalds.py which handles sheet
layout, formula resolution, and full topology extraction.

Usage:
    # Dry run from a CSV file (no writes)
    python3 system/scripts/micro_graph_builder.py \\
      --artifact micro_graph:par_technology \\
      --source /path/to/par_customers.csv

    # Persist
    python3 system/scripts/micro_graph_builder.py \\
      --artifact micro_graph:par_technology \\
      --source /path/to/par_customers.csv \\
      --write --confirm

    # Pipe text from stdin
    echo "PAR Technology Q3 earnings..." | \\
    python3 system/scripts/micro_graph_builder.py \\
      --artifact micro_graph:par_technology \\
      --source-type text

    # List all registered artifacts
    python3 system/scripts/micro_graph_builder.py --list

    # JSON output
    python3 system/scripts/micro_graph_builder.py \\
      --artifact micro_graph:toast_pos \\
      --source /path/to/toast_data.json \\
      --json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent.parent
SYSTEM_DIR = ROOT / "system"

ARTIFACTS_REGISTRY_PATH = SYSTEM_DIR / "artifacts" / "registry.json"
MICRO_GRAPH_DIR = SYSTEM_DIR / "graphs" / "micro"
GLOBAL_INDEX_PATH = MICRO_GRAPH_DIR / "index.json"

# ── Text-extraction signal banks ──────────────────────────────────────────────

_ROLE_KEYWORDS: list[str] = [
    "ceo", "cto", "coo", "cfo", "cpo", "vp", "svp", "evp", "avp",
    "president", "director", "manager", "head of", "founder", "co-founder",
    "chief", "officer", "partner", "principal", "lead", "engineer",
    "analyst", "consultant", "executive", "chair",
]

_PRODUCT_SIGNALS: list[str] = [
    "platform", "pos", "system", "suite", "product", "solution",
    "software", "service", "app", "api", "integration", "module",
]

_RELATIONSHIP_VERBS: list[str] = [
    "joined", "hired", "appointed", "promoted", "leads", "manages",
    "works at", "works for", "partners with", "acquired", "integrates with",
    "deployed", "uses", "selected", "contracted",
]

# Words that look like proper nouns but are not entity names
_STOPWORDS: frozenset[str] = frozenset({
    "the", "and", "for", "that", "this", "with", "from", "have", "has",
    "been", "its", "our", "their", "your", "will", "also", "into",
    "over", "more", "some", "such", "than", "then", "them", "they",
    "each", "other", "after", "before", "about", "would", "could",
    "should", "upon", "when", "where", "there", "these", "those",
    "inc", "corp", "llc", "ltd", "co", "plc", "via", "per", "now",
    "new", "key", "top", "all", "any", "one", "two", "three",
    "first", "last", "next", "back", "may", "can", "not", "but",
    "while", "which", "what", "who", "how", "why", "here",
})


# ── Utility helpers ───────────────────────────────────────────────────────────

def _today() -> str:
    return date.today().isoformat()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slug(value: Any, fallback: str = "unknown") -> str:
    text = "" if value is None else str(value).strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return slug or fallback


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip() or None
    return value


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Registry helpers ──────────────────────────────────────────────────────────

def _load_registry(registry_path: Path = ARTIFACTS_REGISTRY_PATH) -> dict:
    if not registry_path.exists():
        raise FileNotFoundError(f"Registry not found: {registry_path}")
    return json.loads(registry_path.read_text())


def _get_artifact(registry: dict, artifact_id: str) -> dict | None:
    for a in registry.get("artifacts", []):
        if a.get("artifact_id") == artifact_id:
            return a
    return None


# ── Graph builder ─────────────────────────────────────────────────────────────

_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}

class GraphBuilder:
    """
    In-memory node/edge accumulator.

    Deduplicates by id; merges attributes on repeated upserts.
    edge() is a no-op if either endpoint is absent (referential integrity).
    """

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        self.nodes: dict[str, dict] = {}
        self.edges: dict[str, dict] = {}

    def node(
        self,
        node_id: str,
        node_type: str,
        name: str,
        *,
        subtype: str | None = None,
        attributes: dict | None = None,
        sources: list[str] | None = None,
        confidence_level: str = "medium",
        confidence_score: float = 0.65,
    ) -> None:
        existing = self.nodes.get(node_id)
        if existing is None:
            self.nodes[node_id] = {
                "id": node_id,
                "type": node_type,
                "subtype": subtype,
                "name": name,
                "attributes": {
                    k: v for k, v in (attributes or {}).items() if v is not None
                },
                "sources": list(sources or []),
                "confidence": {
                    "level": confidence_level,
                    "score": confidence_score,
                    "rationale": "Extracted by micro_graph_builder.py",
                },
            }
        else:
            # 2026-10-01: narrow defensive guard, added after a confirmed
            # live bug in ecosystem_intelligence.py's analogous _upsert_
            # entity() -- an unconditional attributes.update() there let a
            # later, lower-quality automated write silently clobber an
            # existing, higher-confidence fact with no precedence check.
            # This builder doesn't currently load a prior persisted
            # graph.json into a new run (each run rebuilds the artifact
            # from a single source document), so that exact failure mode
            # isn't live here today -- but the same unconditional-update
            # shape was present, and a future change to load-and-merge
            # across runs would silently reintroduce it. Guard: a
            # higher-confidence existing attribute is never overwritten
            # by a lower-confidence incoming one; same-or-better confidence
            # still updates normally, so legitimate same-document
            # refinement (a later, more detailed sheet/row correcting an
            # earlier summary value) is unaffected.
            incoming_rank = _CONFIDENCE_RANK.get(confidence_level, 0)
            existing_rank = _CONFIDENCE_RANK.get((existing.get("confidence") or {}).get("level"), 0)
            if incoming_rank >= existing_rank:
                existing["attributes"].update(
                    {k: v for k, v in (attributes or {}).items() if v is not None}
                )
            else:
                for k, v in (attributes or {}).items():
                    if v is not None and existing["attributes"].get(k) is None:
                        existing["attributes"][k] = v
            if subtype and not existing.get("subtype"):
                existing["subtype"] = subtype
            for src in sources or []:
                if src not in existing["sources"]:
                    existing["sources"].append(src)

    def edge(
        self,
        edge_type: str,
        from_id: str,
        to_id: str,
        *,
        attributes: dict | None = None,
        sources: list[str] | None = None,
        confidence_level: str = "medium",
        confidence_score: float = 0.65,
    ) -> None:
        # Referential integrity: only add edge if both endpoints exist
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
                "attributes": {
                    k: v for k, v in (attributes or {}).items() if v is not None
                },
                "sources": list(sources or []),
                "confidence": {
                    "level": confidence_level,
                    "score": confidence_score,
                    "rationale": "Extracted by micro_graph_builder.py",
                },
            }
        else:
            existing["attributes"].update(
                {k: v for k, v in (attributes or {}).items() if v is not None}
            )
            for src in sources or []:
                if src not in existing["edges"]:  # pragma: no cover
                    pass
            for src in sources or []:
                if src not in existing["sources"]:
                    existing["sources"].append(src)

    def stats(self) -> dict:
        node_types = Counter(n["type"] for n in self.nodes.values())
        edge_types = Counter(e["type"] for e in self.edges.values())
        return {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "node_types": dict(node_types),
            "edge_types": dict(edge_types),
        }

    def to_graph_json(self, artifact: dict, source_record: dict) -> dict:
        graph_slug = artifact.get("graph_slug") or _slug(artifact["entity"])
        return {
            "contract": "rb_micro_graph_v1",
            "graph_id": f"micro_ecosystem:{graph_slug}",
            "entity": artifact["entity"],
            "entity_aliases": artifact.get("entity_aliases", []),
            "generated_at": _now(),
            "generated_by": "micro_graph_builder.py",
            "source_record": source_record,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "nodes": list(self.nodes.values()),
            "edges": list(self.edges.values()),
        }


# ── Text entity extraction ────────────────────────────────────────────────────

def _extract_from_text(text: str, artifact: dict, source_id: str) -> GraphBuilder:
    """
    Deterministic entity extraction from free-form text.

    Always produces an anchor entity node for the artifact.
    Extracts: companies (capitalized noun phrases), people (FirstName LastName),
    products (entity + product-signal phrases), relationship edges (verb patterns).

    Confidence is kept deliberately low (0.40–0.65) since extraction is
    heuristic-only; no LLM is used.
    """
    g = GraphBuilder(source_id)
    entity_name = artifact["entity"]
    entity_slug = _slug(entity_name)
    entity_id = f"company:{entity_slug}"

    # Anchor node — always present
    g.node(
        entity_id, "company", entity_name,
        subtype="anchor_entity",
        sources=[source_id],
        confidence_level="high",
        confidence_score=0.95,
    )

    # ── Company mentions ──────────────────────────────────────────────────────
    # Capitalized 1–3 word phrases that don't look like sentence starters
    company_re = re.compile(r"\b([A-Z][a-zA-Z&]+(?:\s+[A-Z][a-zA-Z&]+){0,2})\b")
    seen_names: set[str] = {entity_name.lower()}

    for match in company_re.finditer(text):
        raw = match.group(1).strip()
        name_lower = raw.lower()
        if name_lower in seen_names:
            continue
        # Skip stopwords, short strings, and all-caps acronyms > 4 chars
        words = name_lower.split()
        if any(w in _STOPWORDS for w in words):
            continue
        if len(raw) < 3:
            continue
        if raw.isupper() and len(raw) > 4:
            continue
        seen_names.add(name_lower)
        co_id = f"company:{_slug(raw)}"
        g.node(
            co_id, "company", raw,
            sources=[source_id],
            confidence_level="low",
            confidence_score=0.40,
        )

    # ── Person mentions ───────────────────────────────────────────────────────
    # Pattern: FirstName LastName (Title Case, 2 words, no stop words)
    person_re = re.compile(r"\b([A-Z][a-z]{1,20}\s+[A-Z][a-z]{1,20})\b")
    seen_people: set[str] = set()

    for match in person_re.finditer(text):
        name = match.group(1).strip()
        name_lower = name.lower()
        if name_lower in seen_people:
            continue
        words = name_lower.split()
        if any(w in _STOPWORDS for w in words):
            continue
        seen_people.add(name_lower)
        person_id = f"person:{_slug(name)}"
        g.node(
            person_id, "person", name,
            sources=[source_id],
            confidence_level="low",
            confidence_score=0.45,
        )

    # ── Product mentions ──────────────────────────────────────────────────────
    # "{EntityName} {Word} {product signal}" patterns
    for signal in _PRODUCT_SIGNALS:
        product_re = re.compile(
            r"\b(" + re.escape(entity_name) + r"\s+\w+(?:\s+\w+)?\s+" + re.escape(signal) + r")\b",
            re.IGNORECASE,
        )
        for match in product_re.finditer(text):
            product_name = match.group(1).strip()
            product_id = f"product:{_slug(product_name)}"
            g.node(
                product_id, "product", product_name,
                sources=[source_id],
                confidence_level="medium",
                confidence_score=0.60,
            )
            g.edge("has_product", entity_id, product_id, sources=[source_id])

    # ── Relationship edges ────────────────────────────────────────────────────
    # "Person joined/works at/leads Company"
    for verb in _RELATIONSHIP_VERBS:
        rel_re = re.compile(
            r"([A-Z][a-z]+\s+[A-Z][a-z]+)\s+" + re.escape(verb) + r"\s+([A-Z][a-zA-Z\s]{2,40})",
            re.IGNORECASE,
        )
        for match in rel_re.finditer(text):
            p_name = match.group(1).strip()
            c_name = match.group(2).strip().rstrip(".,;")
            p_id = f"person:{_slug(p_name)}"
            c_id = f"company:{_slug(c_name)}"
            if p_id in g.nodes and c_id in g.nodes:
                edge_type = _slug(verb).replace("-", "_")
                g.edge(edge_type, p_id, c_id, sources=[source_id])

    return g


# ── JSON entity extraction ────────────────────────────────────────────────────

def _extract_from_json(data: Any, artifact: dict, source_id: str) -> GraphBuilder:
    """
    Extract nodes/edges from a parsed JSON structure.

    Supports:
      - List of dicts: each dict → a node (heuristic field detection)
      - Dict with 'nodes'/'edges': direct graph import
      - Dict with 'customers'/'contacts'/'people'/'companies' etc.: list extraction
      - Flat dict: single entity record
    """
    g = GraphBuilder(source_id)
    entity_name = artifact["entity"]
    entity_id = f"company:{_slug(entity_name)}"

    g.node(
        entity_id, "company", entity_name,
        subtype="anchor_entity",
        sources=[source_id],
        confidence_level="high",
        confidence_score=0.95,
    )

    def _name_from_rec(rec: dict) -> str | None:
        for key in ("name", "company", "organization", "customer", "account",
                    "contact", "full_name", "brand", "title"):
            val = rec.get(key)
            if val and isinstance(val, str) and val.strip():
                return val.strip()
        return None

    def _ingest_record(rec: dict, node_type: str) -> str | None:
        name = _name_from_rec(rec)
        if not name:
            return None
        node_id = f"{node_type}:{_slug(name)}"
        attrs = {
            k: v for k, v in rec.items()
            if k not in {"name", "company", "organization", "customer", "account",
                         "contact", "full_name", "brand", "title"}
            and not isinstance(v, (dict, list))
            and v is not None
        }
        g.node(node_id, node_type, name, attributes=attrs, sources=[source_id])
        return node_id

    if isinstance(data, list):
        if not data:
            return g
        sample = data[0] if isinstance(data[0], dict) else {}
        person_keys = {"first_name", "last_name", "email", "email_address", "role", "title_field"}
        is_person = any(k.lower() in person_keys for k in sample)
        node_type = "person" if is_person else "customer"
        for rec in data:
            if not isinstance(rec, dict):
                continue
            nid = _ingest_record(rec, node_type)
            if nid:
                g.edge(f"has_{node_type}", entity_id, nid, sources=[source_id])

    elif isinstance(data, dict):
        if "nodes" in data and "edges" in data:
            # Direct graph import — trust the structure
            for node in (data.get("nodes") or []):
                if isinstance(node, dict) and "id" in node and "type" in node and "name" in node:
                    g.nodes[node["id"]] = node
            for edge in (data.get("edges") or []):
                if isinstance(edge, dict) and "id" in edge:
                    g.edges[edge["id"]] = edge
        else:
            list_keys_order = [
                ("customers", "customer"),
                ("contacts", "person"),
                ("people", "person"),
                ("companies", "company"),
                ("accounts", "customer"),
                ("partners", "customer"),
            ]
            for list_key, node_type in list_keys_order:
                items = data.get(list_key)
                if isinstance(items, list):
                    for rec in items:
                        if isinstance(rec, dict):
                            nid = _ingest_record(rec, node_type)
                            if nid:
                                g.edge(f"has_{node_type}", entity_id, nid,
                                       sources=[source_id])

    return g


# ── CSV entity extraction ─────────────────────────────────────────────────────

def _extract_from_csv(text: str, artifact: dict, source_id: str) -> GraphBuilder:
    """
    Extract nodes from a CSV / TSV stream.

    Auto-detects delimiter (comma, tab, pipe).
    Maps rows to entity nodes using header heuristics.
    Adds an anchor entity node for the artifact.
    """
    g = GraphBuilder(source_id)
    entity_name = artifact["entity"]
    entity_id = f"company:{_slug(entity_name)}"

    g.node(
        entity_id, "company", entity_name,
        subtype="anchor_entity",
        sources=[source_id],
        confidence_level="high",
        confidence_score=0.95,
    )

    if not text.strip():
        return g

    # Sniff delimiter
    sample = text[:4096]
    tab_count = sample.count("\t")
    pipe_count = sample.count("|")
    comma_count = sample.count(",")
    if tab_count > comma_count and tab_count > pipe_count:
        delimiter = "\t"
    elif pipe_count > comma_count:
        delimiter = "|"
    else:
        delimiter = ","

    try:
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        fieldnames = reader.fieldnames or []
    except Exception:
        return g

    headers_lower = [h.lower().strip() for h in fieldnames]

    # Determine primary name field
    name_candidates = ["name", "company", "organization", "customer", "account",
                       "contact_name", "full_name", "brand", "restaurant", "operator"]
    name_field: str | None = None
    for candidate in name_candidates:
        if candidate in headers_lower:
            orig_idx = headers_lower.index(candidate)
            name_field = fieldnames[orig_idx]
            break

    # Determine node type
    person_signals = {"first_name", "last_name", "email", "email_address", "role", "title"}
    is_person = any(h in person_signals for h in headers_lower)
    node_type = "person" if is_person else "customer"

    for rec in reader:
        name: str | None = None
        if name_field:
            raw = rec.get(name_field, "")
            name = raw.strip() if raw else None
        if not name:
            # Fall back to first non-empty value
            name = next((v.strip() for v in rec.values() if v and v.strip()), None)
        if not name:
            continue
        node_id = f"{node_type}:{_slug(name)}"
        attrs = {}
        for k, v in rec.items():
            k_clean = k.strip().lower() if k else ""
            v_clean = v.strip() if isinstance(v, str) else v
            if k_clean and v_clean and k_clean != (name_field or "").lower():
                attrs[k_clean] = v_clean
        g.node(node_id, node_type, name, attributes=attrs, sources=[source_id])
        g.edge(f"has_{node_type}", entity_id, node_id, sources=[source_id])

    return g


# ── Activation / index builders ───────────────────────────────────────────────

def _build_activation(artifact: dict, graph_id: str) -> dict:
    aliases = artifact.get("entity_aliases", [])
    trigger_terms = artifact.get("triage_trigger_terms", [])
    entity_lower = artifact["entity"].lower()

    primary_terms: list[str] = [entity_lower]
    seen: set[str] = {entity_lower}
    for t in [*trigger_terms, *aliases]:
        tl = t.lower()
        if tl not in seen:
            primary_terms.append(tl)
            seen.add(tl)
    primary_terms = primary_terms[:20]  # cap for activation index

    question_domains = artifact.get("question_domains") or [
        "entity lookup",
        "customer footprint",
        "org structure",
        "deployment topology",
        "competitive positioning",
    ]

    return {
        "default_state": "dormant",
        "graph_id": graph_id,
        "load_policy": (
            "Load graph.json only for matching query intent or "
            "active-thread evidence. Otherwise keep dormant."
        ),
        "primary_terms": primary_terms,
        "query_intents": question_domains,
        "version": 1,
    }


def _build_index(artifact: dict, graph_id: str, stats: dict) -> dict:
    activation = _build_activation(artifact, graph_id)
    graph_slug = artifact.get("graph_slug") or _slug(artifact["entity"])
    return {
        "contract": "rb_micro_graph_index_v1",
        "graph_id": graph_id,
        "graph_slug": graph_slug,
        "entity": artifact["entity"],
        "entity_aliases": artifact.get("entity_aliases", []),
        "scope_domains": artifact.get("scope_domains", []),
        "question_domains": artifact.get("question_domains", []),
        "confidence_summary": {
            "overall": "medium",
            **{k: v for k, v in stats.items() if isinstance(v, int)},
        },
        "freshness_date": _today(),
        "generated_at": _now(),
        "activation": activation,
    }


# ── Registry / global index update ───────────────────────────────────────────

def _update_registry(
    registry: dict,
    artifact_id: str,
    graph_dir: Path,
    graph_slug: str,
    stats: dict,
    source_record: dict,
    registry_path: Path,
    root: Path = ROOT,
) -> None:
    for a in registry.get("artifacts", []):
        if a.get("artifact_id") != artifact_id:
            continue
        old_status = a.get("status", "stub")
        a["status"] = "building"
        a["graph_slug"] = graph_slug
        a["graph_path"] = str(graph_dir.relative_to(root))
        a["index_path"] = str((graph_dir / "index.json").relative_to(root))
        a["node_count"] = stats["node_count"]
        a["edge_count"] = stats["edge_count"]
        a["freshness_date"] = _today()
        a["last_enriched"] = _today()
        a["enrichment_count"] = (a.get("enrichment_count") or 0) + 1
        a.setdefault("source_lineage", []).append(source_record)
        note = (
            f"First graph build via micro_graph_builder.py on {_today()}. "
            f"Promoted from {old_status} → building."
        )
        a.setdefault("notes", []).append(note)
        break

    registry["active_count"] = sum(
        1 for a in registry.get("artifacts", []) if a.get("status") == "active"
    )
    registry["stub_count"] = sum(
        1 for a in registry.get("artifacts", []) if a.get("status") == "stub"
    )
    registry["updated_at"] = _today()
    registry_path.write_text(json.dumps(registry, indent=2))


def _update_global_index(
    artifact: dict,
    graph_id: str,
    graph_slug: str,
    stats: dict,
    global_index_path: Path,
) -> None:
    if global_index_path.exists():
        index = json.loads(global_index_path.read_text())
    else:
        index = {
            "contract": "rb_micro_graph_presence_index_v1",
            "description": "Lightweight index of all registered RB micro ecosystem graphs.",
            "trust_hierarchy": [
                "user_artifact", "rb_micro_graph", "rb_macro_graph",
                "verified_external_source", "general_model_knowledge",
            ],
            "graphs": [],
            "generated_at": _now(),
            "version": 1,
        }

    # Remove stale entry
    index["graphs"] = [g for g in index.get("graphs", []) if g.get("graph_id") != graph_id]

    # Add updated entry
    index["graphs"].append({
        "graph_id": graph_id,
        "graph_slug": graph_slug,
        "graph_type": artifact.get("artifact_type", "micro_graph"),
        "available": True,
        "entity_name": artifact["entity"],
        "entity_aliases": artifact.get("entity_aliases", []),
        "scope_domains": artifact.get("scope_domains", []),
        "question_domains": artifact.get("question_domains", []),
        "implicit_trigger_terms": artifact.get("triage_trigger_terms", []),
        "confidence_summary": {
            "overall": "medium",
            **{k: v for k, v in stats.items() if isinstance(v, int)},
        },
        "freshness_date": _today(),
        "routing_action": "getMicroGraphSummary",
        "routing_params": {"query": artifact["entity"]},
        "notes": f"Built by micro_graph_builder.py on {_today()}.",
    })
    index["generated_at"] = _now()
    global_index_path.write_text(json.dumps(index, indent=2))


# ── Source record ─────────────────────────────────────────────────────────────

def _build_source_record(
    source_path: Path | None,
    source_type: str,
    source_name: str,
    text: str,
    artifact_id: str,
) -> tuple[str, dict]:
    """Returns (source_id, source_record_dict)."""
    if source_path and source_path.exists():
        digest = _sha256_path(source_path)
        s_name = source_path.name
    else:
        digest = _sha256_text(text)
        s_name = source_name or "paste"

    source_id = f"src-{_slug(artifact_id)}-{_today()}-{digest[:12]}"
    record = {
        "source_id": source_id,
        "source_type": source_type,
        "source_name": s_name,
        "source_path": str(source_path) if source_path else None,
        "sha256_prefix": digest[:16],
        "ingested_at": _now(),
        "ingestion_script": "micro_graph_builder.py",
        "artifact_id": artifact_id,
    }
    return source_id, record


# ── Core pipeline ─────────────────────────────────────────────────────────────

def build_graph(
    artifact_id: str,
    text: str,
    source_type: str = "text",
    source_name: str = "paste",
    source_path: Path | None = None,
    registry_path: Path = ARTIFACTS_REGISTRY_PATH,
    graph_base_dir: Path = MICRO_GRAPH_DIR,
    global_index_path: Path = GLOBAL_INDEX_PATH,
    write: bool = False,
    confirm: bool = False,
) -> dict:
    """
    Core ETL pipeline.  Injectable paths for test isolation.

    Returns a result dict:
      - artifact_id, entity, graph_id, graph_slug, graph_dir
      - stats: {node_count, edge_count, node_types, edge_types}
      - write_status: "dry_run" | "needs_confirm" | "written"
      - proposed_source_lineage_entry
      - artifacts_written / artifacts_to_write
      - note
    """
    registry = _load_registry(registry_path)
    artifact = _get_artifact(registry, artifact_id)
    if artifact is None:
        return {
            "status": "not_found",
            "artifact_id": artifact_id,
            "error": f"Artifact '{artifact_id}' not found in registry.",
        }

    entity_slug = artifact.get("graph_slug") or _slug(artifact["entity"])
    graph_slug = entity_slug
    graph_id = f"micro_ecosystem:{graph_slug}"
    graph_dir = graph_base_dir / graph_slug

    source_id, source_record = _build_source_record(
        source_path, source_type, source_name, text, artifact_id
    )

    # Route to appropriate extractor
    if source_type == "json":
        try:
            data = json.loads(text)
            g = _extract_from_json(data, artifact, source_id)
        except json.JSONDecodeError as exc:
            return {"status": "error", "artifact_id": artifact_id, "error": f"JSON parse error: {exc}"}
    elif source_type in ("csv", "tsv"):
        g = _extract_from_csv(text, artifact, source_id)
    else:
        # text / xlsx-summary / unknown → text extractor
        g = _extract_from_text(text, artifact, source_id)

    stats = g.stats()
    graph_json = g.to_graph_json(artifact, source_record)
    activation = _build_activation(artifact, graph_id)
    index = _build_index(artifact, graph_id, stats)

    proposed_lineage = {
        **source_record,
        "node_count": stats["node_count"],
        "edge_count": stats["edge_count"],
    }

    result: dict = {
        "artifact_id": artifact_id,
        "entity": artifact["entity"],
        "graph_id": graph_id,
        "graph_slug": graph_slug,
        "graph_dir": str(graph_dir),
        "stats": stats,
        "proposed_source_lineage_entry": proposed_lineage,
        "artifact_current_status": artifact.get("status"),
        "proposed_artifact_status": "building",
    }

    if not write:
        result["write_status"] = "dry_run"
        result["note"] = "Dry run — no files written. Pass --write --confirm to persist."
        result["artifacts_to_write"] = [
            str(graph_dir / "graph.json"),
            str(graph_dir / "sources.json"),
            str(graph_dir / "index.json"),
            str(graph_dir / "activation.json"),
        ]
        return result

    if not confirm:
        result["write_status"] = "needs_confirm"
        result["note"] = (
            "Pass --confirm (in addition to --write) to write graph artifacts "
            "and update registry."
        )
        return result

    # ── Persist ───────────────────────────────────────────────────────────────
    graph_dir.mkdir(parents=True, exist_ok=True)
    paths_written: list[str] = []

    (graph_dir / "graph.json").write_text(json.dumps(graph_json, indent=2))
    paths_written.append(str(graph_dir / "graph.json"))

    sources_out = graph_dir / "sources.json"
    if sources_out.exists():
        existing: Any = json.loads(sources_out.read_text())
        if not isinstance(existing, list):
            existing = [existing]
    else:
        existing = []
    existing.append(source_record)
    sources_out.write_text(json.dumps(existing, indent=2))
    paths_written.append(str(sources_out))

    # Option C: embed precomputed_answers at build time so brief/query reads are pure reads
    try:
        import micro_graph_query as _mgq  # noqa: PLC0415
        index["precomputed_answers"] = _mgq.build_precomputed_answers(index)
    except Exception:  # noqa: BLE001
        index.setdefault("precomputed_answers", {})
    (graph_dir / "index.json").write_text(json.dumps(index, indent=2))
    paths_written.append(str(graph_dir / "index.json"))

    (graph_dir / "activation.json").write_text(json.dumps(activation, indent=2))
    paths_written.append(str(graph_dir / "activation.json"))

    root = registry_path.parent.parent.parent  # system/artifacts/.. → root
    _update_registry(
        registry, artifact_id, graph_dir, graph_slug, stats,
        proposed_lineage, registry_path, root=root,
    )
    _update_global_index(artifact, graph_id, graph_slug, stats, global_index_path)
    paths_written.append(str(registry_path))
    paths_written.append(str(global_index_path))

    result["write_status"] = "written"
    result["artifacts_written"] = paths_written
    result["note"] = (
        f"Graph written to {graph_dir}. "
        f"Registry updated: {artifact_id} promoted stub → building."
    )
    return result


# ── CLI helpers ───────────────────────────────────────────────────────────────

def _list_artifacts(registry_path: Path = ARTIFACTS_REGISTRY_PATH) -> None:
    registry = _load_registry(registry_path)
    rows = []
    for a in registry.get("artifacts", []):
        rows.append({
            "artifact_id": a.get("artifact_id", ""),
            "entity": a.get("entity", ""),
            "status": a.get("status", ""),
            "enrichments": a.get("enrichment_count", 0),
            "last_enriched": a.get("last_enriched") or "—",
        })
    header = f"{'artifact_id':<42} {'status':<12} {'entity':<32} {'enrichments'}"
    print(header)
    print("─" * len(header))
    for r in rows:
        print(
            f"{r['artifact_id']:<42} {r['status']:<12} {r['entity']:<32} {r['enrichments']}"
        )


def _auto_source_type(path: Path) -> str:
    return {
        ".csv": "csv", ".tsv": "csv",
        ".json": "json",
        ".txt": "text", ".md": "text",
        ".xlsx": "xlsx", ".xls": "xlsx",
    }.get(path.suffix.lower(), "text")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="micro_graph_builder.py",
        description="Generalized ETL builder for RB micro graph artifacts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--artifact", metavar="ARTIFACT_ID",
                   help="Artifact id from registry (e.g. micro_graph:par_technology)")
    p.add_argument("--source", metavar="FILE",
                   help="Source file path (csv, json, txt, xlsx)")
    p.add_argument("--source-type", metavar="TYPE", default=None,
                   help="Override source format: text | csv | json | xlsx")
    p.add_argument("--source-name", metavar="NAME", default=None,
                   help="Human-readable label for this source")
    p.add_argument("--write", action="store_true",
                   help="Enable file writes (requires --confirm)")
    p.add_argument("--confirm", action="store_true",
                   help="Confirm writes (must combine with --write)")
    p.add_argument("--json", action="store_true",
                   help="Print JSON result to stdout")
    p.add_argument("--list", action="store_true",
                   help="List all registered artifacts and exit")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    if args.list:
        _list_artifacts()
        return

    if not args.artifact:
        print(
            "ERROR: --artifact is required. "
            "Use --list to see registered artifacts.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Resolve source ────────────────────────────────────────────────────────
    text = ""
    source_path: Path | None = None
    source_type = args.source_type
    source_name = args.source_name

    if args.source:
        source_path = Path(args.source)
        if not source_path.exists():
            print(f"ERROR: Source file not found: {source_path}", file=sys.stderr)
            sys.exit(1)
        if not source_type:
            source_type = _auto_source_type(source_path)
        if not source_name:
            source_name = source_path.name

        if source_type == "xlsx":
            # Produce a sheet-structure summary (full xlsx → entity-specific parser)
            try:
                from openpyxl import load_workbook as _load_wb  # type: ignore
                wb = _load_wb(source_path, data_only=True, read_only=True)
                lines = [
                    f"Workbook: {source_path.name}",
                    f"Sheets: {', '.join(wb.sheetnames)}",
                ]
                for sheet_name in wb.sheetnames[:3]:
                    ws = wb[sheet_name]
                    lines.append(f"\nSheet '{sheet_name}':")
                    for row in ws.iter_rows(min_row=1, max_row=5, values_only=True):
                        lines.append(
                            "  " + " | ".join(str(c) for c in row if c is not None)
                        )
                text = "\n".join(lines)
                source_type = "text"
            except ImportError:
                print(
                    "WARNING: openpyxl not installed — treating xlsx as text stub.",
                    file=sys.stderr,
                )
                text = f"xlsx source: {source_path.name} (openpyxl not installed)"
                source_type = "text"
        else:
            text = source_path.read_text(encoding="utf-8", errors="replace")

    elif not sys.stdin.isatty():
        text = sys.stdin.read()
        if not source_type:
            source_type = "text"
        if not source_name:
            source_name = "stdin"
    else:
        print(
            "ERROR: Provide --source <file> or pipe text via stdin.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not source_type:
        source_type = "text"

    result = build_graph(
        artifact_id=args.artifact,
        text=text,
        source_type=source_type,
        source_name=source_name or "paste",
        source_path=source_path,
        write=args.write,
        confirm=args.confirm,
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return

    # Human-readable output
    status = result.get("write_status") or result.get("status", "unknown")
    width = 60
    print(f"\n{'─' * width}")
    print(f"  micro_graph_builder — {result.get('entity', args.artifact)}")
    print(f"{'─' * width}")
    print(f"  artifact_id   : {result.get('artifact_id', '—')}")
    print(f"  graph_id      : {result.get('graph_id', '—')}")
    print(f"  write_status  : {status}")
    if "stats" in result:
        s = result["stats"]
        print(f"  nodes         : {s.get('node_count', 0)}")
        print(f"  edges         : {s.get('edge_count', 0)}")
        nt = s.get("node_types", {})
        if nt:
            print(f"  node_types    : {', '.join(f'{k}={v}' for k, v in sorted(nt.items()))}")
    if result.get("note"):
        print(f"  note          : {result['note']}")
    if result.get("artifacts_written"):
        print("  files written :")
        for fp in result["artifacts_written"]:
            print(f"    {fp}")
    if result.get("artifacts_to_write"):
        print("  would write   :")
        for fp in result["artifacts_to_write"]:
            print(f"    {fp}")
    print()


if __name__ == "__main__":
    main()
