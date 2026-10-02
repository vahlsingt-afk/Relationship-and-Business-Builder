#!/usr/bin/env python3
"""Import structured Top-500 company-profile gap-fill packets.

These packets are datasets (``dataset.records``), not narrative articles.  Each
record is merged into the existing brand's profile and the graph's provenanced
``deep_research_profile.company_profile`` namespace.  Explicit technology
relationships are reconciled by exact brand/vendor identity only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import brand_profile_common as bpc  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import rb_core as core  # noqa: E402

RECEIPT_PATH = core.CACHE_DIR / "top500_profile_gapfill_ingest.json"
DATASET_NAME_MARKERS = {
    "top-500 company profile gap fill",
    "top-600 company profile gap fill",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_gapfill_dataset(data: Any) -> bool:
    dataset = data.get("dataset") if isinstance(data, dict) else None
    name = str((dataset or {}).get("name") or "").lower()
    return bool(
        isinstance(dataset, dict)
        and isinstance(dataset.get("records"), list)
        and any(marker in name for marker in DATASET_NAME_MARKERS)
    )


def classify_bytes(content: bytes) -> bool:
    try:
        return is_gapfill_dataset(json.loads(content.decode("utf-8-sig")))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False


def _confidence(value: Any) -> tuple[str, float]:
    try:
        score = float(value) / 100.0 if float(value) > 1 else float(value)
    except (TypeError, ValueError):
        score = 0.7
    level = "high" if score >= 0.85 else ("medium" if score >= 0.60 else "low")
    return level, max(0.0, min(score, 1.0))


def _resolve_brand(rec: dict, graph: dict, by_id: dict) -> dict | None:
    explicit = rec.get("canonical_brand_id") or rec.get("brand_id")
    entity = by_id.get(explicit) if explicit else None
    if entity and entity.get("entity_type") == "brand":
        return entity
    resolved = ei._resolve_entity_id_any_type(str(rec.get("brand_name") or ""), graph)
    entity = by_id.get(resolved) if resolved else None
    return entity if entity and entity.get("entity_type") == "brand" else None


def _resolve_vendor(name: str, graph: dict, by_id: dict) -> dict | None:
    target = name.strip().casefold()
    canonical = [e for e in graph.get("entities") or [] if e.get("entity_type") == "vendor" and str(e.get("name") or "").casefold() == target]
    if len(canonical) == 1:
        return canonical[0]
    # Research packets often use the vendor's legal suffix while the graph
    # stores its commercial name (for example "Thanx, Inc." vs "Thanx").
    # Resolve only when suffix-stripping leaves one unique vendor match.
    def commercial_key(value: str) -> str:
        value = re.sub(r"\b(incorporated|inc|llc|ltd|limited|corp|corporation|plc)\b\.?", "", value.casefold())
        return re.sub(r"[^a-z0-9]+", "", value)

    legal_key = commercial_key(name)
    legal_matches = [
        e for e in graph.get("entities") or []
        if e.get("entity_type") == "vendor" and commercial_key(str(e.get("name") or "")) == legal_key
    ]
    if len(legal_matches) == 1:
        return legal_matches[0]
    resolved = ei._resolve_entity_id_any_type(name, graph)
    entity = by_id.get(resolved) if resolved else None
    if entity and entity.get("entity_type") == "vendor":
        return entity
    # Compound labels such as "PAR Technology (Punchh)" name the product in
    # parentheses; prefer that exact vendor alias over guessing from tokens.
    parenthetical = re.findall(r"\(([^)]+)\)", name)
    if len(parenthetical) == 1:
        inner = parenthetical[0].strip().casefold()
        matches = [
            e for e in graph.get("entities") or [] if e.get("entity_type") == "vendor"
            and inner in {str(e.get("name") or "").casefold(), *(str(a).casefold() for a in e.get("aliases") or [])}
        ]
        if len(matches) == 1:
            return matches[0]
    return None


def _profile_field(leaf: dict, packet_id: str) -> dict:
    level, _ = _confidence(leaf.get("confidence"))
    status = str(leaf.get("status") or "reported").lower().replace(" ", "_")
    if status not in {"confirmed", "reported", "not_yet_researched"}:
        status = "reported"
    return bpc.field(
        leaf.get("value"), status=status, evidence_ids=[packet_id], confidence=level,
        as_of=leaf.get("as_of"), last_reviewed_by="system:top500_profile_gapfill_ingest",
    )


def _can_replace(existing: dict | None, incoming: dict) -> bool:
    if not existing or existing.get("status") == "not_yet_researched":
        return True
    if bpc.is_human_reviewed(existing):
        return False
    rank = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
    return rank.get(incoming.get("confidence"), 0) >= rank.get(existing.get("confidence"), 0)


def _relationship_id(brand_id: str, vendor_id: str, category: str) -> str:
    digest = hashlib.sha256(f"{brand_id}|{vendor_id}|{category}|gapfill".encode()).hexdigest()[:12]
    return f"rel-gapfill-{digest}"


def ingest(path: Path, *, dry_run: bool = False) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not is_gapfill_dataset(data):
        raise ValueError(f"not a Top-500 profile gap-fill dataset: {path}")
    dataset = data["dataset"]
    records = dataset["records"]
    packet_id = f"top500-profile-gapfill::{dataset.get('version') or path.stem}"
    graph = ei._read_graph()
    by_id = ei._index_by_id(graph.get("entities") or [])
    counts = {
        "records_seen": len(records), "brands_resolved": 0, "brands_unresolved": 0,
        "profile_fields_added": 0, "profile_fields_updated": 0,
        "profile_fields_unchanged": 0, "profile_fields_preserved_human": 0,
        "graph_fields_added": 0, "graph_fields_updated": 0, "graph_fields_unchanged": 0,
        "relationships_added": 0, "relationships_updated": 0,
        "relationships_unresolved_vendor": 0,
    }
    unresolved_brands: list[str] = []
    unresolved_vendors: list[dict] = []
    dirty_profiles: dict[str, dict] = {}

    field_map = {
        "parent_ownership": ("identity", "parent_ownership"),
        "headquarters": ("identity", "hq_city_state"),
        "founded": ("identity", "founded_year"),
        "market_identity": ("identity", "market_identity"),
        "ownership_type": ("identity", "ownership_type"),
        "operating_structure": ("identity", "operating_structure"),
        "synopsis": (None, "synopsis"),
        "conflicts_notes": ("identity", "conflicts_notes"),
    }

    for rec in records:
        entity = _resolve_brand(rec, graph, by_id)
        if not entity:
            counts["brands_unresolved"] += 1
            unresolved_brands.append(str(rec.get("brand_name") or rec.get("brand_id") or "unknown"))
            continue
        counts["brands_resolved"] += 1
        brand_id = entity["id"]
        profile = dirty_profiles.get(brand_id) or bpc.get_profile(brand_id, graph=graph)
        company_profile = rec.get("company_profile") or {}
        graph_profile = entity.setdefault("attributes", {}).setdefault("deep_research_profile", {}).setdefault("company_profile", {})

        for source_key, (group, target_key) in field_map.items():
            leaf = company_profile.get(source_key)
            if leaf is None:
                continue
            if not isinstance(leaf, dict):
                leaf = {"value": leaf}
            if leaf.get("value") in (None, ""):
                continue
            incoming = _profile_field(leaf, packet_id)
            container = profile if group is None else profile.setdefault(group, {})
            existing = container.get(target_key)
            if existing and existing.get("value") == incoming.get("value"):
                counts["profile_fields_unchanged"] += 1
            elif _can_replace(existing, incoming):
                container[target_key] = incoming
                counts["profile_fields_added" if not existing or existing.get("status") == "not_yet_researched" else "profile_fields_updated"] += 1
            else:
                counts["profile_fields_preserved_human"] += 1

            graph_record = {
                "value": leaf.get("value"), "confidence": leaf.get("confidence"),
                "status": leaf.get("status") or "reported", "sources": leaf.get("sources") or company_profile.get("sources") or [],
                "packet_id": packet_id, "recorded_at": _now(),
            }
            graph_existing = graph_profile.get(source_key)
            if graph_existing and graph_existing.get("value") == graph_record["value"]:
                counts["graph_fields_unchanged"] += 1
            else:
                graph_profile[source_key] = graph_record
                counts["graph_fields_added" if graph_existing is None else "graph_fields_updated"] += 1

        dirty_profiles[brand_id] = profile

        for tech in company_profile.get("existing_technology_relationships") or []:
            if not isinstance(tech, dict) or not tech.get("vendor") or not tech.get("category"):
                continue
            vendor = _resolve_vendor(str(tech["vendor"]), graph, by_id)
            if not vendor:
                counts["relationships_unresolved_vendor"] += 1
                unresolved_vendors.append({"brand": entity.get("name"), "vendor": tech.get("vendor")})
                continue
            category = str(tech["category"]).strip().lower().replace(" ", "_")
            rel_id = _relationship_id(brand_id, vendor["id"], category)
            rel = {
                "id": rel_id, "from_entity_id": brand_id, "to_entity_id": vendor["id"],
                "relationship_type": "uses_vendor_for_category", "category": category,
                "status": "active", "evidence_posture": "provisional",
                "confidence": ei._confidence("medium", "Structured Top-500 profile research packet."),
                "sources": [packet_id],
                "source_assertions": [{
                    "source_id": packet_id, "url": None, "title": dataset.get("name"),
                    "published_at": dataset.get("version", "")[:10] or None,
                    "discovered_at": _now()[:10],
                    "paraphrase": f"{entity.get('name')} lists {vendor.get('name')} for {category} in the structured research packet.",
                    "posture": "current",
                }],
                "created_at": _now(), "updated_at": _now(),
            }
            existing_rel = next((r for r in graph.get("relationships") or [] if r.get("id") == rel_id), None)
            existed = existing_rel is not None
            # Clean fields from an interrupted pre-validation attempt before
            # the corrected record is upserted.
            if existing_rel is not None:
                existing_rel.pop("notes", None)
            if not dry_run:
                ei.resolve_and_upsert_relationship(graph, rel, by_id=by_id)
            counts["relationships_updated" if existed else "relationships_added"] += 1

    if not dry_run:
        for brand_id, profile in dirty_profiles.items():
            bpc.save_profile(brand_id, profile)
        ei._write_graph(graph)

    result = {
        "ok": True, "dataset_type": "top500_profile_gapfill", "dataset_version": dataset.get("version"),
        "packet_id": packet_id, "source_file": str(path), "dry_run": dry_run,
        "counts": counts, "unresolved_brands": unresolved_brands,
        "unresolved_vendors": unresolved_vendors, "completed_at": _now(),
    }
    if not dry_run:
        RECEIPT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def sweep(root: Path, *, dry_run: bool = False) -> dict:
    files = []
    totals: dict[str, int] = {}
    for path in sorted(root.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not is_gapfill_dataset(data):
            continue
        result = ingest(path, dry_run=dry_run)
        files.append({"path": str(path), "counts": result["counts"]})
        for key, value in result["counts"].items():
            totals[key] = totals.get(key, 0) + int(value)
    return {"ok": True, "root": str(root), "files_matched": len(files), "files": files, "totals": totals, "dry_run": dry_run}


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file")
    group.add_argument("--sweep")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = ingest(Path(args.file), dry_run=args.dry_run) if args.file else sweep(Path(args.sweep), dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
