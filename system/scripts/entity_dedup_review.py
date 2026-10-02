#!/usr/bin/env python3
"""
entity_dedup_review.py — RB-2026-09-27.

Closes a real gap flagged twice and fixed neither time: two entities in
ecosystem_intelligence.json representing the same real-world brand or
vendor. Confirmed live: `brand-zaxby-s` (name "Zaxby's", alias "Zaxbys")
and `brand-zaxbys` (name "Zaxbys", no aliases) are two separate entities
for the same chain -- surfaced only as a side effect of
deep_account_intelligence_v56_ingest.py's ei._resolve_entity_id_any_type()
correctly returning None (genuine ambiguity, two matches) rather than
guessing. The older `vendor-ncr` / `vendor-ncr-voyix` case (flagged
2026-09-06 in the battle-card-extension work, never actually cleaned up)
is the same class of problem -- vendor-ncr's own aliases already list
"NCR Voyix" verbatim, which is vendor-ncr-voyix's entire `name`.

This is NOT entity_convergence_scan.py (that's cross-signal PATTERN
detection -- exit positioning, distress, consolidation -- on a single
already-resolved entity; it has nothing to do with whether two entity
records secretly refer to the same thing). This is dedicated identity
dedup: same entity_type (+ subtype, when both sides have one set and it
differs -- e.g. an ordinary restaurant brand sharing a name with a
multi_brand_franchisee_operator entity is a deliberate, DIFFERENT real-
world thing, not a duplicate; see multibrand_franchisee_operator_ingest.
py's own name_collision_skipped path, which exists for exactly that
reason and never even creates such an entity), whose name/alias sets
collide after stripping ALL punctuation and whitespace -- "Zaxby's" and
"Zaxbys" both collapse to "zaxbys". This is deliberately more aggressive
than ecosystem_intelligence._norm_key()'s token-underscore normalization
("zaxby_s" vs "zaxbys" -- does NOT collide), because a false positive here
costs a human one `reject`, while a false negative leaves a real duplicate
sitting in the graph indefinitely.

Same review-first discipline as every other mutation-capable module in
this codebase (see ownership_promotion.py, multibrand_franchisee_operator_
ingest.py): scanning only ever proposes candidates into a pending store;
nothing is ever auto-merged, and confirming a merge always requires an
explicit, human-supplied `canonical_id` naming which of the two entities
survives -- this module never guesses which side is "the real one."

Merge discipline on confirm (see merge_entities()): net-new fields copy
onto the canonical entity from the losing one; a field that's POPULATED
and DIFFERENT on both sides is never silently overwritten -- it's recorded
under the canonical entity's attributes.dedup_merge_conflicts for Todd's
follow-up instead (same "never silently overwrite a populated field"
discipline as _merge_leadership() in multibrand_franchisee_operator_
ingest.py). Every entity_id reference graph-wide (relationships, other
entities' owner_entity_id, signals.entities[], assessments.entity_id,
user_relevance.entity_id, strategic_recommendations.entities[]) is
repointed from the losing id to the canonical id before the losing entity
is deleted; a relationship that pointed at BOTH sides of the merge (rare,
but possible) becomes a self-loop after repointing and is dropped rather
than kept as a meaningless from==to edge.

Known, accepted limitation: ecosystem_intelligence._write_graph() writes
to disk BEFORE running its own schema validator, with no in-process
rollback on failure (same architectural gap documented in test_multibrand_
franchisee_operator_ingest.py and the "Ingest Top-500 deep account
intelligence dataset" incident, 2026-09-27). That validator subprocess
used to always check the real production ecosystem_intelligence.json by a
hardcoded path regardless of an isolated caller's own tmp graph -- fixed
in ecosystem_intelligence.py the same day this module was built, so
_write_graph() now validates whatever path it actually wrote. This
module's own test suite still runs system/schemas/validate.py a second
time, directly, against its isolated output (same belt-and-suspenders
pattern as test_multibrand_franchisee_operator_ingest.py) -- cheap, and it
stops depending on that upstream fix staying in place. In production, a
write that somehow lands schema-invalid is recovered from
system/_snapshots/ (ei._write_graph() snapshots the prior good file before
every write) -- there is still no in-process rollback.

CLI:
    python3 entity_dedup_review.py scan [--dry-run]
    python3 entity_dedup_review.py pending
    python3 entity_dedup_review.py confirm <candidate_id> --canonical-id <id> [--dry-run]
    python3 entity_dedup_review.py reject <candidate_id>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402

STORE_PATH = core.SYSTEM_DIR / ".cache" / "entity_dedup_candidates.json"

# Fields this module itself writes onto a merged entity's attributes --
# never treated as ordinary loser-side attribute data to copy/compare when
# merging a THIRD entity into an already-merged one later.
_MERGE_BOOKKEEPING_KEYS = {"dedup_merge_conflicts", "dedup_merge_history"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_store() -> dict:
    if not STORE_PATH.exists():
        return {"candidates": {}}
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("candidates"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"candidates": {}}


def _save_store(store: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(store, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _collision_key(text: str) -> str:
    """Strip ALL non-alphanumeric characters (not just collapse to '_'
    like ecosystem_intelligence._norm_key()) -- "Zaxby's" and "Zaxbys"
    must land on the identical key for this scan to find them."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _entity_collision_keys(entity: dict) -> set[str]:
    keys = set()
    name_key = _collision_key(entity.get("name") or "")
    if name_key:
        keys.add(name_key)
    for alias in entity.get("aliases") or []:
        alias_key = _collision_key(alias)
        if alias_key:
            keys.add(alias_key)
    return keys


def _pair_id(id_a: str, id_b: str) -> str:
    return "::".join(sorted([id_a, id_b]))


def find_collision_candidates(graph: dict) -> list[dict]:
    """Pure detection -- reads `graph`, writes nothing, mutates nothing.
    Returns one entry per unordered pair of DISTINCT entities sharing
    entity_type whose name/alias collision keys overlap, excluding pairs
    where both sides have a subtype set and it differs (a deliberate,
    different real-world thing -- see module docstring)."""
    by_type: dict[str, list[dict]] = {}
    for e in graph.get("entities") or []:
        if not e.get("id") or not e.get("name"):
            continue
        by_type.setdefault(e.get("entity_type") or "unknown", []).append(e)

    pairs: dict[str, dict] = {}
    for entity_type, group in by_type.items():
        key_index: dict[str, dict[str, dict]] = {}
        for e in group:
            for key in _entity_collision_keys(e):
                key_index.setdefault(key, {})[e["id"]] = e
        for key, matched in key_index.items():
            ids = sorted(matched.keys())
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b = matched[ids[i]], matched[ids[j]]
                    sub_a, sub_b = a.get("subtype"), b.get("subtype")
                    if sub_a and sub_b and sub_a != sub_b:
                        continue
                    pid = _pair_id(a["id"], b["id"])
                    entry = pairs.setdefault(pid, {
                        "candidate_id": pid,
                        "entity_type": entity_type,
                        "entity_ids": [a["id"], b["id"]],
                        "entity_names": [a.get("name"), b.get("name")],
                        "shared_keys": [],
                    })
                    if key not in entry["shared_keys"]:
                        entry["shared_keys"].append(key)
    return sorted(pairs.values(), key=lambda p: p["candidate_id"])


def scan(*, dry_run: bool = False) -> dict:
    """Run detection against the real graph and queue genuinely new pairs
    for review. A pair already in the store (pending, confirmed, or
    rejected) is never re-proposed -- same discipline as ownership_
    promotion._add_candidate(). dry_run=True runs detection and reports
    the full scope WITHOUT writing anything to the candidate store --
    this is the safe, read-only mode for finding out how many collision
    pairs exist before any review or merge happens."""
    graph = ei._read_graph()
    found = find_collision_candidates(graph)
    store = _load_store()

    new_ids: list[str] = []
    for cand in found:
        cid = cand["candidate_id"]
        if cid in store["candidates"]:
            continue
        store["candidates"][cid] = {
            **cand,
            "status": "proposed_pending_confirmation",
            "detected_at": _now_iso(),
            "resolved_at": None,
        }
        new_ids.append(cid)

    if not dry_run:
        _save_store(store)

    return {
        "entities_scanned": len(graph.get("entities") or []),
        "collision_pairs_found_this_scan": len(found),
        "new_candidates": len(new_ids),
        "new_candidate_ids": new_ids,
        "total_pending": sum(
            1 for c in store["candidates"].values() if c["status"] == "proposed_pending_confirmation"
        ),
        "dry_run": dry_run,
    }


def _entity_evidence(graph: dict, entity_id: str) -> Optional[dict]:
    by_id = ei._index_by_id(graph.get("entities") or [])
    entity = by_id.get(entity_id)
    if entity is None:
        return None
    relationships = [
        {
            "id": r.get("id"), "from_entity_id": r.get("from_entity_id"), "to_entity_id": r.get("to_entity_id"),
            "relationship_type": r.get("relationship_type"), "category": r.get("category"), "status": r.get("status"),
        }
        for r in graph.get("relationships") or []
        if r.get("from_entity_id") == entity_id or r.get("to_entity_id") == entity_id
    ]
    return {"entity": entity, "relationship_count": len(relationships), "relationships": relationships}


def pending_candidates() -> list[dict]:
    """Every pending candidate, enriched with LIVE evidence pulled fresh
    from the current graph (not a stale snapshot from scan time) -- both
    entities' full records plus every relationship pointing at either one,
    so a reviewer has everything needed to pick the canonical side and
    judge what would be merged in. `still_valid` is False when either
    entity was already deleted by an unrelated merge since this candidate
    was queued."""
    store = _load_store()
    graph = ei._read_graph()
    out = []
    for cand in store["candidates"].values():
        if cand.get("status") != "proposed_pending_confirmation":
            continue
        id_a, id_b = cand["entity_ids"]
        evidence_a = _entity_evidence(graph, id_a)
        evidence_b = _entity_evidence(graph, id_b)
        out.append({
            **cand,
            "still_valid": evidence_a is not None and evidence_b is not None,
            "evidence": {id_a: evidence_a, id_b: evidence_b},
        })
    return out


def _replace_and_dedupe(items: list, old: str, new: str) -> list:
    out = []
    for item in items:
        item = new if item == old else item
        if item not in out:
            out.append(item)
    return out


def _merge_attributes(canonical_attrs: dict, loser_attrs: dict, *, loser_id: str, now: str) -> list[dict]:
    conflicts = []
    for key, value in (loser_attrs or {}).items():
        if key in _MERGE_BOOKKEEPING_KEYS:
            continue
        if key not in canonical_attrs or canonical_attrs.get(key) in (None, "", [], {}):
            canonical_attrs[key] = value
            continue
        if canonical_attrs[key] == value:
            continue
        conflicts.append({
            "field": f"attributes.{key}", "canonical_value": canonical_attrs[key],
            "duplicate_value": value, "duplicate_entity_id": loser_id, "recorded_at": now,
        })
    return conflicts


def merge_entities(graph: dict, canonical_id: str, loser_id: str) -> dict:
    """Merge `loser_id` into `canonical_id` IN PLACE on `graph`. Never
    writes to disk -- the caller does that (or doesn't, for a dry-run
    preview). See module docstring for the merge/conflict/repoint
    discipline. Raises ValueError if either id is missing or they're the
    same id -- callers must validate against the live graph first."""
    by_id = ei._index_by_id(graph.get("entities") or [])
    canonical = by_id.get(canonical_id)
    loser = by_id.get(loser_id)
    if canonical is None or loser is None:
        raise ValueError(f"merge_entities: missing entity (canonical_id={canonical_id!r}, loser_id={loser_id!r})")
    if canonical_id == loser_id:
        raise ValueError("merge_entities: canonical_id and loser_id must differ")

    now = ei._now()
    conflicts: list[dict] = []

    canonical.setdefault("aliases", [])
    for alias in list(loser.get("aliases") or []) + [loser.get("name")]:
        if alias and alias != canonical.get("name") and alias not in canonical["aliases"]:
            canonical["aliases"].append(alias)

    canonical.setdefault("domains", [])
    for domain in loser.get("domains") or []:
        if domain not in canonical["domains"]:
            canonical["domains"].append(domain)

    canonical.setdefault("sources", [])
    for source in loser.get("sources") or []:
        if source not in canonical["sources"]:
            canonical["sources"].append(source)

    canonical.setdefault("attributes", {})
    conflicts += _merge_attributes(canonical["attributes"], loser.get("attributes") or {}, loser_id=loser_id, now=now)

    if not canonical.get("subtype") and loser.get("subtype"):
        canonical["subtype"] = loser["subtype"]

    canonical_notes = (canonical.get("notes") or "").strip()
    loser_notes = (loser.get("notes") or "").strip()
    if not canonical_notes and loser_notes:
        canonical["notes"] = loser["notes"]
    elif canonical_notes and loser_notes and canonical_notes != loser_notes:
        conflicts.append({
            "field": "notes", "canonical_value": canonical.get("notes"), "duplicate_value": loser.get("notes"),
            "duplicate_entity_id": loser_id, "recorded_at": now,
        })

    if not canonical.get("ticker") and loser.get("ticker"):
        canonical["ticker"] = loser["ticker"]
    elif canonical.get("ticker") and loser.get("ticker") and canonical["ticker"] != loser["ticker"]:
        conflicts.append({
            "field": "ticker", "canonical_value": canonical.get("ticker"), "duplicate_value": loser.get("ticker"),
            "duplicate_entity_id": loser_id, "recorded_at": now,
        })

    if not canonical.get("owner_name") and loser.get("owner_name"):
        canonical["owner_name"] = loser.get("owner_name")
        canonical["owner_entity_id"] = loser.get("owner_entity_id")
        canonical["owner_confidence"] = loser.get("owner_confidence")
        canonical["owner_confirmed_by"] = loser.get("owner_confirmed_by")
    elif canonical.get("owner_name") and loser.get("owner_name") and canonical["owner_name"] != loser["owner_name"]:
        conflicts.append({
            "field": "owner_name", "canonical_value": canonical.get("owner_name"),
            "duplicate_value": loser.get("owner_name"), "duplicate_entity_id": loser_id, "recorded_at": now,
        })

    if loser.get("reported_alternates"):
        canonical.setdefault("reported_alternates", [])
        for alt in loser["reported_alternates"]:
            if alt not in canonical["reported_alternates"]:
                canonical["reported_alternates"].append(alt)

    if loser.get("strategic_narratives"):
        canonical.setdefault("strategic_narratives", [])
        for narrative in loser["strategic_narratives"]:
            if narrative not in canonical["strategic_narratives"]:
                canonical["strategic_narratives"].append(narrative)

    canonical_level = (canonical.get("confidence") or {}).get("level")
    loser_level = (loser.get("confidence") or {}).get("level")
    if not canonical_level and loser_level:
        canonical["confidence"] = loser["confidence"]
    elif canonical_level and loser_level and canonical_level != loser_level:
        conflicts.append({
            "field": "confidence.level", "canonical_value": canonical_level, "duplicate_value": loser_level,
            "duplicate_entity_id": loser_id, "recorded_at": now,
        })

    canonical_created = canonical.get("created_at")
    loser_created = loser.get("created_at")
    if loser_created and (not canonical_created or loser_created < canonical_created):
        canonical["created_at"] = loser_created

    if conflicts:
        canonical["attributes"].setdefault("dedup_merge_conflicts", []).extend(conflicts)
    canonical["attributes"].setdefault("dedup_merge_history", []).append({
        "merged_entity_id": loser_id, "merged_entity_name": loser.get("name"), "merged_at": now,
    })
    canonical["updated_at"] = now

    relationships_repointed = 0
    self_loops_dropped = 0
    kept_relationships = []
    for rel in graph.get("relationships") or []:
        touched = False
        if rel.get("from_entity_id") == loser_id:
            rel["from_entity_id"] = canonical_id
            touched = True
        if rel.get("to_entity_id") == loser_id:
            rel["to_entity_id"] = canonical_id
            touched = True
        if touched:
            relationships_repointed += 1
            rel["updated_at"] = now
            if rel.get("from_entity_id") == rel.get("to_entity_id"):
                self_loops_dropped += 1
                continue
        kept_relationships.append(rel)
    graph["relationships"] = kept_relationships

    other_entity_owner_refs_repointed = 0
    for e in graph.get("entities") or []:
        if e.get("id") == canonical_id:
            continue
        if e.get("owner_entity_id") == loser_id:
            e["owner_entity_id"] = canonical_id
            other_entity_owner_refs_repointed += 1

    signals_repointed = 0
    for sig in graph.get("signals") or []:
        ents = sig.get("entities")
        if isinstance(ents, list) and loser_id in ents:
            sig["entities"] = _replace_and_dedupe(ents, loser_id, canonical_id)
            signals_repointed += 1

    assessments_repointed = 0
    for a in graph.get("assessments") or []:
        if a.get("entity_id") == loser_id:
            a["entity_id"] = canonical_id
            assessments_repointed += 1

    user_relevance_repointed = 0
    for ur in graph.get("user_relevance") or []:
        if ur.get("entity_id") == loser_id:
            ur["entity_id"] = canonical_id
            user_relevance_repointed += 1

    strategic_recommendations_repointed = 0
    for rec in graph.get("strategic_recommendations") or []:
        ents = rec.get("entities")
        if isinstance(ents, list) and loser_id in ents:
            rec["entities"] = _replace_and_dedupe(ents, loser_id, canonical_id)
            strategic_recommendations_repointed += 1

    graph["entities"] = [e for e in graph.get("entities") or [] if e.get("id") != loser_id]

    return {
        "canonical_id": canonical_id,
        "loser_id": loser_id,
        "attribute_conflicts": len(conflicts),
        "conflicts": conflicts,
        "relationships_repointed": relationships_repointed,
        "self_loop_relationships_dropped": self_loops_dropped,
        "other_entity_owner_refs_repointed": other_entity_owner_refs_repointed,
        "signals_repointed": signals_repointed,
        "assessments_repointed": assessments_repointed,
        "user_relevance_repointed": user_relevance_repointed,
        "strategic_recommendations_repointed": strategic_recommendations_repointed,
    }


def record_proposal(
    candidate_id: str, *, confirmed: bool, canonical_id: Optional[str] = None,
    confirmed_by: str = "human", dry_run: bool = False,
) -> dict:
    """Reject a candidate (never touches the graph), or confirm it by
    merging the non-canonical side into `canonical_id` (required -- this
    module never guesses which entity survives). dry_run=True on a
    confirm computes and returns the full merge preview against a freshly
    read graph WITHOUT writing anything to disk and WITHOUT resolving the
    candidate -- it stays pending for a real confirm afterward."""
    store = _load_store()
    cand = store["candidates"].get(candidate_id)
    if not cand:
        return {"error": f"unknown candidate id: {candidate_id}"}
    if cand["status"] != "proposed_pending_confirmation":
        return {"error": f"candidate {candidate_id} already resolved: {cand['status']}"}

    if not confirmed:
        if not dry_run:
            cand["status"] = "rejected"
            cand["resolved_at"] = _now_iso()
            cand["confirmed_by"] = confirmed_by
            _save_store(store)
        return {"rejected": True, "candidate_id": candidate_id, "dry_run": dry_run}

    if canonical_id not in cand["entity_ids"]:
        return {"error": f"canonical_id must be one of {cand['entity_ids']}, got {canonical_id!r}"}
    loser_id = next(i for i in cand["entity_ids"] if i != canonical_id)

    graph = ei._read_graph()
    by_id = ei._index_by_id(graph.get("entities") or [])
    if canonical_id not in by_id or loser_id not in by_id:
        return {"error": "one or both entities no longer exist in the graph -- candidate is stale"}

    summary = merge_entities(graph, canonical_id, loser_id)

    if dry_run:
        return {"confirmed": False, "dry_run": True, "candidate_id": candidate_id, "merge_summary": summary}

    ei._write_graph(graph)

    cand["status"] = "confirmed"
    cand["resolved_at"] = _now_iso()
    cand["confirmed_by"] = confirmed_by
    cand["canonical_id"] = canonical_id
    cand["loser_id"] = loser_id
    cand["merge_summary"] = summary
    _save_store(store)
    return {"confirmed": True, "candidate_id": candidate_id, "merge_summary": summary}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    scan_p = sub.add_parser("scan")
    scan_p.add_argument("--dry-run", action="store_true")

    sub.add_parser("pending")

    confirm_p = sub.add_parser("confirm")
    confirm_p.add_argument("candidate_id")
    confirm_p.add_argument("--canonical-id", required=True)
    confirm_p.add_argument("--dry-run", action="store_true")

    reject_p = sub.add_parser("reject")
    reject_p.add_argument("candidate_id")

    args = p.parse_args()

    if args.cmd == "scan":
        print(json.dumps(scan(dry_run=args.dry_run), indent=2, ensure_ascii=False))
    elif args.cmd == "pending":
        print(json.dumps(pending_candidates(), indent=2, ensure_ascii=False))
    elif args.cmd == "confirm":
        result = record_proposal(
            args.candidate_id, confirmed=True, canonical_id=args.canonical_id, dry_run=args.dry_run,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.cmd == "reject":
        print(json.dumps(record_proposal(args.candidate_id, confirmed=False), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
