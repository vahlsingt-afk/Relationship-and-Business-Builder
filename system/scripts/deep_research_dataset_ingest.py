#!/usr/bin/env python3
"""
deep_research_dataset_ingest.py — RB-2026-09-27.

Ingests the "RBB Restaurant Account Intelligence Base" deep-research
dataset (a 250-brand external research deliverable: identity, scale/
performance, leadership, public business contacts, strategy, technology)
into RB's real intelligence stores, review-first throughout.

Three destinations, chosen by content shape, not a blanket allowlist:

  - An `identity` field whose name signals an ownership change (e.g.
    "ownership") -> ownership_promotion.propose_ownership_finding() --
    existing, already-tested M&A review queue. Reused, not reimplemented.
  - A `technology` field -> tech_stack_relationship_promotion.
    propose_research_finding(), but ONLY when a vendor RB already tracks
    (an existing vendor entity name/alias) is named verbatim in the
    evidence text. Most technology entries are prose describing a
    proprietary/in-house system with no vendor to extract; this module
    never guesses one from free text -- discovering a genuinely new
    vendor from unstructured prose is exactly the kind of unsupported
    inference RB's review-first discipline exists to prevent.
  - Everything else (scale_performance, leadership, public_business_
    contacts, strategy, and any other identity field) -> a new,
    lightweight review-first attribute queue built here: a net-new field
    (nothing on file yet) is applied immediately; a field that already
    holds a DIFFERENT value routes to review; a field matching what's
    already on file is a silent no-op. Leadership is deliberately
    included here, confidence/source/as_of preserved -- Todd, 2026-09-27:
    a decision-maker listing with no confidence attached isn't useful for
    identifying who actually has buying authority.

Brand identity resolution is exact name/alias match only
(ecosystem_intelligence._resolve_entity_id_any_type) restricted to
entity_type == "brand" -- these are all real, already-tracked restaurant
chains; a brand that doesn't resolve (or resolves to something that isn't
a brand entity) needs a human to look at it, never a guessed new entity.

CLI:
    python3 deep_research_dataset_ingest.py ingest --file PATH [--dry-run]
    python3 deep_research_dataset_ingest.py pending
    python3 deep_research_dataset_ingest.py confirm <id>
    python3 deep_research_dataset_ingest.py reject <id>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import ownership_promotion as op  # noqa: E402
import tech_stack_relationship_promotion as tsrp  # noqa: E402

STORE_PATH = core.SYSTEM_DIR / ".cache" / "deep_research_dataset_attribute_proposals.json"

# Namespaced under one key on the brand entity's `attributes` dict so this
# dataset's contribution stays clearly provenanced and never collides with
# unrelated attribute keys (rank, segment, technomic_history, ...).
_ATTRIBUTE_NAMESPACE = "deep_research_profile"


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
    return f"attr-{brand_id}-{field_group}-{field_name}"


def _is_ownership_field(field_group: str, field_name: str) -> bool:
    return field_group == "identity" and "ownership" in field_name.lower()


# ownership_promotion's own _extract_acquirer_name() anchors its patterns
# on the TARGET brand's name appearing right next to the acquirer in text
# (e.g. "X acquired Del Taco") -- validated against real headline prose,
# not this dataset's shape. This dataset's own ownership values instead
# read "Acquired by X from Y; ..." with no repeated brand name, and Y (the
# seller) directly follows X with no punctuation boundary, over-capturing
# ownership_promotion's generic pattern into "X from Y" as one implausible
# name (confirmed against the dataset's real Del Taco/Yadav record). This
# is a small, targeted extractor for that specific real phrasing instead --
# "from" itself is a valid stop boundary, since it introduces the seller,
# not part of the acquirer's name.
_ACQUIRED_BY_RE = re.compile(r"(?i:acquired\s+by)\s+([A-Z][\w&.,' -]{1,60}?)(?=\s+from\b|[;.,\n]|$)")


def _extract_owner_name(value_text: str) -> Optional[str]:
    match = _ACQUIRED_BY_RE.search(value_text or "")
    if not match:
        return None
    name = match.group(1).strip().strip("'\"")
    return name or None


def _find_known_vendor(value_text: str, graph: dict) -> Optional[str]:
    """Return the single existing vendor entity's canonical name if exactly
    one already-known vendor is named verbatim (word-boundary, via tech_
    stack_relationship_promotion's own tested matcher) in value_text, else
    None. Never guesses a vendor RB doesn't already track."""
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


def _leaf_source_url(leaf: dict) -> Optional[str]:
    sources = leaf.get("sources") or []
    if not sources:
        return None
    first = sources[0]
    if isinstance(first, str):
        return first
    if isinstance(first, dict):
        return first.get("url")
    return None


def _leaf_source_title(leaf: dict) -> Optional[str]:
    sources = leaf.get("sources") or []
    if not sources:
        return None
    first = sources[0]
    return first.get("title") if isinstance(first, dict) else None


def _walk_profile(profile: dict):
    """Yield (field_group, field_name, leaf_dict) for every leaf assertion
    in a record's profile -- a leaf is any dict carrying a 'value' key."""
    for field_group, fields in (profile or {}).items():
        if not isinstance(fields, dict):
            continue
        for field_name, leaf in fields.items():
            if isinstance(leaf, dict) and "value" in leaf:
                yield field_group, field_name, leaf


def _apply_or_queue_attribute(
    graph: dict, store: dict, *, brand_id: str, brand_name: str,
    field_group: str, field_name: str, leaf: dict, packet_id: str,
) -> str:
    """Returns one of: applied | queued | unchanged."""
    by_id = ei._index_by_id(graph.get("entities") or [])
    entity = by_id[brand_id]
    namespace = entity.setdefault("attributes", {}).setdefault(_ATTRIBUTE_NAMESPACE, {})
    group = namespace.setdefault(field_group, {})
    existing = group.get(field_name)
    new_value = leaf.get("value")

    if existing is not None and existing.get("value") == new_value:
        return "unchanged"

    record = {
        "value": new_value,
        "confidence": leaf.get("confidence"),
        "status": leaf.get("status"),
        "as_of": leaf.get("as_of"),
        "scope": leaf.get("scope"),
        "sources": leaf.get("sources"),
        "packet_id": packet_id,
        "recorded_at": _now_iso(),
    }

    if existing is None:
        group[field_name] = record
        return "applied"

    cid = _candidate_id(brand_id, field_group, field_name)
    store["pending"][cid] = {
        "candidate_id": cid,
        "brand_id": brand_id,
        "brand_name": brand_name,
        "field_group": field_group,
        "field_name": field_name,
        "existing": existing,
        "proposed": record,
        "status": "proposed_pending_confirmation",
        "created_at": _now_iso(),
    }
    return "queued"


def ingest(dataset_path: Path, *, dry_run: bool = False) -> dict:
    data = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    dataset = data.get("dataset") or {}
    records = dataset.get("records") or []
    packet_id = f"deep-research-dataset::{dataset.get('version') or Path(dataset_path).stem}"

    graph = ei._read_graph()
    store = _load_store()

    counts = {
        "brands_total": len(records),
        "brands_resolved": 0,
        "brands_unresolved": 0,
        "ownership_proposed": 0,
        "technology_proposed": 0,
        "technology_vendor_unidentified": 0,
        "attributes_applied": 0,
        "attributes_queued": 0,
        "attributes_unchanged": 0,
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

        for field_group, field_name, leaf in _walk_profile(rec.get("profile") or {}):
            value = leaf.get("value")
            if not value:
                continue
            source_url = _leaf_source_url(leaf)
            source_title = _leaf_source_title(leaf)

            if _is_ownership_field(field_group, field_name):
                if dry_run:
                    counts["ownership_proposed"] += 1
                    continue
                # Pass owner_name explicitly from this dataset's own
                # phrasing (see _extract_owner_name) rather than relying on
                # propose_ownership_finding's built-in fallback extractor,
                # which is anchored on a different real-world phrasing
                # pattern and over-captures this dataset's "Acquired by X
                # from Y" shape. A miss still proposes a real, reviewable
                # candidate with an unknown owner name -- never blocked on
                # extraction succeeding.
                result = op.propose_ownership_finding(
                    brand_id, evidence_text=str(value), owner_name=_extract_owner_name(str(value)),
                    source_url=source_url, source_title=source_title, evidence_date=leaf.get("as_of"),
                )
                if result.get("proposed"):
                    counts["ownership_proposed"] += 1
                elif result.get("error"):
                    errors.append({"brand": brand_name, "field": f"{field_group}.{field_name}", "error": result["error"]})
                continue

            if field_group == "technology":
                vendor_name = _find_known_vendor(str(value), graph)
                if not vendor_name:
                    counts["technology_vendor_unidentified"] += 1
                    continue
                if dry_run:
                    counts["technology_proposed"] += 1
                    continue
                result = tsrp.propose_research_finding(
                    brand_id, vendor_name, str(value), source_url=source_url,
                    source_title=source_title, evidence_date=leaf.get("as_of"),
                )
                if result.get("proposed"):
                    counts["technology_proposed"] += 1
                elif result.get("error"):
                    errors.append({"brand": brand_name, "field": f"{field_group}.{field_name}", "error": result["error"]})
                continue

            # Generic attribute route.
            if dry_run:
                existing = (
                    entity.get("attributes", {})
                    .get(_ATTRIBUTE_NAMESPACE, {})
                    .get(field_group, {})
                    .get(field_name)
                )
                if existing is not None and existing.get("value") == value:
                    counts["attributes_unchanged"] += 1
                elif existing is None:
                    counts["attributes_applied"] += 1
                else:
                    counts["attributes_queued"] += 1
                continue

            outcome = _apply_or_queue_attribute(
                graph, store, brand_id=brand_id, brand_name=entity.get("name", brand_name),
                field_group=field_group, field_name=field_name, leaf=leaf, packet_id=packet_id,
            )
            counts[f"attributes_{outcome}"] += 1

    if not dry_run:
        if counts["attributes_applied"]:
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
