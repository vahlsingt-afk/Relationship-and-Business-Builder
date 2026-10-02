#!/usr/bin/env python3
"""Repair the confirmed 2026-09-29 canonical data-quality defects."""
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import ecosystem_intelligence as ei
import rb_core as core

BAD_SOURCE = "RBB_competitor_platform_baseline_2026-09-29.json"


def _norm(value: str) -> str:
    value = (value or "").lower().replace("&", "and")
    value = re.sub(r"\b(the|incorporated|inc|corporation|corp|company|co|llc|l\.l\.c|lp|l\.p|plc)\b", "", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def _owner_confidence(value) -> dict:
    try:
        score = float(value)
        if score > 1:
            score /= 100
    except (TypeError, ValueError):
        score = 1.0
    score = max(0.0, min(1.0, score))
    level = "critical" if score >= .95 else "high" if score >= .8 else "medium" if score >= .55 else "low"
    return {"level": level, "score": score}


def repair(*, dry_run: bool) -> dict:
    graph = ei._read_graph()
    entities = graph.get("entities") or []
    by_id = {e["id"]: e for e in entities}

    bad_relationships = []
    kept = []
    for rel in graph.get("relationships") or []:
        if BAD_SOURCE in json.dumps(rel, ensure_ascii=False):
            bad_relationships.append(rel["id"])
        else:
            kept.append(rel)
    graph["relationships"] = kept

    gp = by_id.get("vendor-global-payments")
    genius = by_id.get("vendor-genius")
    sonic = by_id.get("brand-sonic")
    if sonic and "Sonic Drive-In" not in sonic.setdefault("aliases", []):
        sonic["aliases"].append("Sonic Drive-In")
    if sonic:
        attrs = sonic.setdefault("attributes", {})
        deep = attrs.setdefault("deep_research_profile", {})
        for conflict in attrs.get("dedup_merge_conflicts") or []:
            if conflict.get("field") != "attributes.deep_research_profile" or conflict.get("duplicate_entity_id") != "brand-sonic-drive-in":
                continue
            for key, value in (conflict.get("duplicate_value") or {}).items():
                deep.setdefault(key, value)
        old_profile = core.SYSTEM_DIR / "brand_profiles" / "brand-sonic-drive-in.json"
        new_profile = core.SYSTEM_DIR / "brand_profiles" / "brand-sonic.json"
        if old_profile.exists() and not new_profile.exists() and not dry_run:
            profile = json.loads(old_profile.read_text(encoding="utf-8"))
            profile["brand_id"] = "brand-sonic"
            profile["brand_name"] = "SONIC"
            new_profile.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if gp:
        gp.setdefault("aliases", [])
        # The combined commercial name belongs to the Genius suite. Keeping it
        # on both parent and child makes exact-name resolution ambiguous.
        gp["aliases"] = [
            alias for alias in gp["aliases"]
            if alias not in {"Global Payments/Genius", "Global Payments Genius"}
        ]
    if genius:
        genius.setdefault("aliases", [])
        for alias in ("Genius POS", "Genius Product Suite", "Global Payments/Genius"):
            if alias not in genius["aliases"]:
                genius["aliases"].append(alias)
        genius["owner_name"] = "Global Payments"
        genius["owner_entity_id"] = "vendor-global-payments"
        genius["owner_confidence"] = {"level": "critical", "score": 1.0}
        genius["owner_confirmed_by"] = "canonical-repair-2026-09-29"

    # Repair a legacy product-specific vendor id to the canonical Qu entity.
    for relationship in graph.get("relationships") or []:
        if relationship.get("to_entity_id") == "vendor-qu-pos":
            relationship["to_entity_id"] = "vendor-qu"

    name_index: dict[str, set[str]] = {}
    for entity in entities:
        for name in [entity.get("name")] + list(entity.get("aliases") or []):
            key = _norm(name)
            if key:
                name_index.setdefault(key, set()).add(entity["id"])

    owners_linked = []
    owners_ambiguous = []
    for entity in entities:
        profile = ((entity.get("attributes") or {}).get("deep_research_profile") or {}).get("company_profile") or {}
        leaf = profile.get("parent_ownership") or {}
        owner_name = leaf.get("value") if isinstance(leaf, dict) else None
        if not owner_name or entity.get("owner_entity_id"):
            continue
        matches = name_index.get(_norm(owner_name), set()) - {entity["id"]}
        if len(matches) == 1:
            owner_id = next(iter(matches))
            entity["owner_name"] = owner_name
            entity["owner_entity_id"] = owner_id
            entity["owner_confidence"] = _owner_confidence(leaf.get("confidence"))
            entity["owner_confirmed_by"] = "top500-profile-gapfill-unique-name-resolution"
            owners_linked.append({"entity_id": entity["id"], "owner_entity_id": owner_id})
        elif len(matches) > 1:
            owners_ambiguous.append({"entity_id": entity["id"], "owner_name": owner_name, "matches": sorted(matches)})

    # Recover safely if an earlier interrupted repair wrote scalar confidence
    # before schema validation rejected it.
    for entity in entities:
        confidence = entity.get("owner_confidence")
        if confidence is not None and not isinstance(confidence, dict):
            entity["owner_confidence"] = _owner_confidence(confidence)

    result = {
        "dry_run": dry_run,
        "false_relationships_removed": len(bad_relationships),
        "false_relationship_ids": bad_relationships,
        "genius_rollup_configured": bool(gp and genius),
        "owners_linked": len(owners_linked),
        "owner_links": owners_linked,
        "owners_ambiguous": owners_ambiguous,
    }
    if dry_run:
        return result

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = core.SNAPSHOTS_DIR / f"ecosystem_intelligence.pre-canonical-quality-repair.{stamp}.json"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(core.SYSTEM_DIR / "ecosystem_intelligence.json", backup)
    ei._write_graph(graph)
    result["backup"] = str(backup)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    print(json.dumps(repair(dry_run=not args.confirm), indent=2))
