#!/usr/bin/env python3
"""
intelligence_assessment.py — RB pre-brief intelligence assessment phase.

Runs AFTER the gather phase (passive_email_intelligence, passive_ri_ingest)
and BEFORE daily_brief.py generates the brief.  Produces a structured
assessment cache at system/.cache/intelligence_assessment.json that
daily_brief.py reads via _load_intelligence_assessment().

Six phases — each best-effort; one failure never stops the rest:

  Phase 1 — Web Intelligence Ingestion
    Runs web_scanner.scan_all_sources(), writes new items to IntelligenceDB.
    The scanner's 6-hour TTL cache means daily_brief.py's fallback _run_web_scan()
    is nearly instant when called minutes later.

  Phase 2 — Gatherer Daily Ecosystem Change Detection
    Normalizes fresh public signals into a rolling-24-hour change packet and
    proposes Hunter follow-up without promoting headlines into canonical facts.

  Phase 3 — Email Intelligence Classification
    Reads passive_email_intelligence.json (written by gather phase).
    Promotes top_headlines to structured IntelligenceDB items tagged with
    entity, signal_type, and source so they participate in convergence detection.

  Phase 4 — Convergence Analysis
    Queries IntelligenceDB for entities with items across 2+ distinct source
    names over 30 days (multi_source) and entity pairs that co-appear in 2+
    items (entity_pairs).  These are the sustained patterns, not one-off items.

  Phase 5 — Mutation Proposals
    Cross-references convergences with active_threads.yaml and yesterday's
    assessment to generate mutation proposals.
    Types: watchlist_add, thread_intelligence, loop_create.
    thread_intelligence/loop_create always require CoS confirmation.
    watchlist_add auto-applies directly to ecosystem_intelligence.json's
    watch_list (RB 2026-08-27, Todd's direction) when confidence is high
    and the entity resolves cleanly to exactly one existing graph entity —
    conservative defaults (tier_2, interrupt_eligible=False), never a
    genuinely new entity. Lower-confidence or ambiguous cases still require
    confirmation, same as before.

  Phase 6 — Trust Stats
    Aggregates all phase outputs into a standardized block that daily_brief.py
    surfaces as "DAILY INTELLIGENCE ASSESSMENT" — the audit trail of what RB
    did autonomously before the brief was generated.

Output:  system/.cache/intelligence_assessment.json
Contract: rb_intelligence_assessment_v1

Usage:
    python3 system/scripts/intelligence_assessment.py          # run all phases
    python3 system/scripts/intelligence_assessment.py --cache  # skip if fresh
    python3 system/scripts/intelligence_assessment.py --smoke  # smoke test
    python3 system/scripts/intelligence_assessment.py --json   # print JSON summary
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import rb_core as core  # noqa: E402
import mutation_policy  # noqa: E402

SYSTEM_DIR = core.SYSTEM_DIR
CACHE_DIR = SYSTEM_DIR / ".cache"
CACHE_PATH = CACHE_DIR / "intelligence_assessment.json"
ACTIVE_THREADS_PATH = SYSTEM_DIR / "active_threads.yaml"
PASSIVE_EMAIL_CACHE = CACHE_DIR / "passive_email_intelligence.json"
PRIOR_ASSESSMENT_PATH = CACHE_PATH  # same file — read yesterday's before writing today's

CONTRACT = "rb_intelligence_assessment_v1"

# Minimum strategic_relevance_score to promote a headline to IntelligenceDB.
# passive_email_intelligence.top_headlines are already pre-filtered to the best
# headlines (typically 5-15 per day), so we accept all of them.
MIN_EMAIL_RELEVANCE = 0.0

# Category → signal_type mapping for email headline classification.
_CATEGORY_TO_SIGNAL: dict[str, str] = {
    "acquisition": "acquisition",
    "merger": "acquisition",
    "saas_consolidation": "acquisition",
    "funding": "funding",
    "investment": "funding",
    "ipo": "funding",
    "product_launch": "product-launch",
    "platform_convergence": "partnership",
    "partnership": "partnership",
    "integration": "partnership",
    "executive_change": "exec-change",
    "leadership": "exec-change",
    "expansion": "expansion",
    "growth": "expansion",
    "closure": "closure",
    "regulatory": "regulatory",
    "financial": "financial",
    "earnings": "financial",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _load_json(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_active_thread_entities() -> dict[str, list[str]]:
    """Return {thread_id: [company_name, ...]} from active_threads.yaml."""
    try:
        import yaml
        data = yaml.safe_load(ACTIVE_THREADS_PATH.read_text(encoding="utf-8"))
        threads = (data or {}).get("threads") or []
        result: dict[str, list[str]] = {}
        for t in threads:
            tid = t.get("id") or ""
            companies = t.get("companies") or []
            if tid and companies:
                result[tid] = [c.lower() for c in companies if c]
        return result
    except Exception:
        return {}


def _load_watchlist_entities() -> set[str]:
    """Return lowercase entity names currently on the watchlist.

    RB-2026-08-24: this read from SYSTEM_DIR/"watchlist.json", which has
    never existed on disk -- always returned an empty set, so every
    multi-source-converging entity looked "not yet tracked" regardless of
    whether it actually was. The real, live watchlist is
    ecosystem_intelligence.json's watch_list array (see
    ecosystem_intelligence.py::watch_list_cmd / the getWatchList API).
    """
    try:
        data = json.loads(core.ECOSYSTEM_INTELLIGENCE_PATH.read_text(encoding="utf-8"))
        entity_names = {e.get("id"): e.get("name") for e in (data.get("entities") or [])}
        result: set[str] = set()
        for entry in (data.get("watch_list") or []):
            name = entity_names.get(entry.get("entity_id")) or entry.get("entity_id") or ""
            if name:
                result.add(name.lower())
        return result
    except Exception:
        return set()


def _category_to_signal(category: str, themes: list[str]) -> str:
    """Map email headline category/themes to a signal_type string."""
    cat_lower = (category or "").lower()
    for key, sig in _CATEGORY_TO_SIGNAL.items():
        if key in cat_lower:
            return sig
    for theme in (themes or []):
        tl = theme.lower()
        for key, sig in _CATEGORY_TO_SIGNAL.items():
            if key in tl:
                return sig
    return "general"


# ---------------------------------------------------------------------------
# Phase 1 — Web Intelligence Ingestion
# ---------------------------------------------------------------------------

def phase1_web_intelligence() -> dict:
    """Run web_scanner and return structured results.

    The scanner writes new items to IntelligenceDB internally.
    Its 6-hour TTL cache means daily_brief.py's _run_web_scan() fallback
    returns cached results instantly when called minutes later.
    """
    try:
        import web_scanner as ws
        result = ws.scan_all_sources()
        return {
            "status": "ok",
            "items_fetched": result.items_fetched,
            "items_from_cache": result.items_from_cache,
            "world_national": result.world_national,
            "industry_primary": result.restaurant_industry,
            "industry_technology": result.restaurant_technology,
            "source_health": result.source_health,
            "errors": result.errors,
            "metadata": result.metadata,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "error": str(exc),
            "items_fetched": 0,
            "items_from_cache": 0,
            "world_national": [],
            "industry_primary": [],
            "industry_technology": [],
            "source_health": [],
            "errors": [str(exc)],
            "metadata": {},
        }


# ---------------------------------------------------------------------------
# Phase 2 — Email Intelligence Classification
# ---------------------------------------------------------------------------

def phase2_email_classification() -> dict:
    """Promote email top_headlines to structured IntelligenceDB items.

    Reads passive_email_intelligence.json, promotes each top_headline
    above MIN_EMAIL_RELEVANCE to the IntelligenceDB so it participates
    in convergence detection.  Uses a content-hash tag to avoid writing
    the same headline twice on repeated runs.
    """
    result: dict[str, Any] = {
        "status": "ok",
        "items_classified": 0,
        "items_skipped_duplicate": 0,
        "items_skipped_no_entity": 0,
        "signal_types_found": [],
        "entities_detected": [],
    }

    pei = _load_json(PASSIVE_EMAIL_CACHE)
    if not pei or not isinstance(pei, dict):
        result["status"] = "skipped_no_source"
        return result

    headlines: list[dict] = pei.get("top_headlines") or []
    if not headlines:
        result["status"] = "skipped_empty"
        return result

    # Open DB with retry — SQLite WAL may be briefly locked if the 4 AM scan and
    # another process overlap.  Three attempts with 1-second backoff is sufficient
    # for the typical transient "disk I/O error" (WAL checkpoint conflict).
    import time as _time
    db = None
    last_open_exc: Exception | None = None
    for _attempt in range(3):
        try:
            from intelligence_db import IntelligenceDB
            db = IntelligenceDB()
            db.open()
            last_open_exc = None
            break
        except Exception as exc:  # noqa: BLE001
            last_open_exc = exc
            if _attempt < 2:
                _time.sleep(1)
    if db is None or last_open_exc is not None:
        result["status"] = "error"
        result["error"] = str(last_open_exc)
        return result

    signal_types_seen: set[str] = set()
    entities_seen: set[str] = set()

    try:
        for headline in headlines:
            score = headline.get("strategic_relevance_score") or 0.0
            if score < MIN_EMAIL_RELEVANCE:
                continue

            title = (headline.get("headline") or "").strip()
            source_name = (headline.get("source") or "email_harvest").strip()
            category = headline.get("category") or ""
            themes: list[str] = headline.get("themes") or []
            confidence_raw = headline.get("confidence") or "medium"
            confidence = confidence_raw if confidence_raw in ("high", "medium", "low") else "medium"

            # Entities from the headline
            ents_raw: list[str] = []
            ents_block = headline.get("entities") or {}
            if isinstance(ents_block, dict):
                ents_raw = ents_block.get("companies") or []
            elif isinstance(ents_block, list):
                ents_raw = ents_block
            ents_raw = [e.strip() for e in ents_raw if e and e.strip()]

            if not title:
                result["items_skipped_no_entity"] += 1
                continue

            # Content hash for idempotent writes
            content_hash = _sha256(f"{title}|{source_name}")

            # Skip if already written (keyword tag = content_hash)
            existing = db.search(tag_type="keyword", tag_value=content_hash, days=2)
            if existing:
                result["items_skipped_duplicate"] += 1
                continue

            signal_type = _category_to_signal(category, themes)
            signal_types_seen.add(signal_type)
            entities_seen.update(e.lower() for e in ents_raw)

            tags = [{"type": "keyword", "value": content_hash}]
            for ent in ents_raw:
                tags.append({"type": "entity", "value": ent})
            tags.append({"type": "signal_type", "value": signal_type})
            if category:
                tags.append({"type": "keyword", "value": category})
            for theme in themes[:3]:
                tags.append({"type": "keyword", "value": theme})

            why = headline.get("why_it_matters") or ""
            content_body = f"Category: {category}. {why}".strip()

            db.add_item(
                title=title,
                content=content_body,
                source_name=source_name,
                source_type="email_harvest",
                confidence=confidence,
                tags=tags,
            )
            result["items_classified"] += 1

    except Exception as exc:  # noqa: BLE001
        result["status"] = "partial_error"
        result["error"] = str(exc)
    finally:
        try:
            db.close()
        except Exception:
            pass

    result["signal_types_found"] = sorted(signal_types_seen)
    result["entities_detected"] = sorted(entities_seen)
    return result


# ---------------------------------------------------------------------------
# Phase 3 — Convergence Analysis
# ---------------------------------------------------------------------------

def phase3_convergence_analysis() -> dict:
    """Query IntelligenceDB for sustained multi-source patterns."""
    result: dict[str, Any] = {
        "status": "ok",
        "multi_source": [],
        "entity_pairs": [],
        "multi_source_count": 0,
        "entity_pair_count": 0,
    }
    try:
        from intelligence_db import IntelligenceDB
        with IntelligenceDB() as db:
            # Multi-source: entities appearing across 2+ distinct source
            # names AND across 2+ distinct calendar days.
            #
            # RB-2026-08-25: confirmed live -- the top-ranked "sustained
            # pattern" entities on a given day (Burger King, Firehouse
            # Subs, Tim Hortons: 300+ items across 5-6 sources each) all
            # had a gathered_date span of exactly ONE calendar day. Source
            # diversity alone doesn't distinguish a real multi-day pattern
            # from many feeds simultaneously covering the same same-day
            # story -- this section's own name ("Sustained patterns") and
            # its rendered claim ("a sustained pattern, not a one-off
            # headline") require actual persistence over time, which only
            # distinct_days (added to find_convergences/cross_entity_
            # convergence for exactly this) can verify.
            all_convs = db.find_convergences(min_entity_hits=2, days=30)
            multi_source = [
                c for c in all_convs
                if len(c.get("sources") or []) >= 2 and c.get("distinct_days", 0) >= 2
            ][:10]

            # Entity pairs co-appearing in 2+ items across 2+ distinct days.
            pairs = [
                p for p in db.cross_entity_convergence(days=30, min_shared_items=2)
                if p.get("distinct_days", 0) >= 2
            ][:10]

            result["multi_source"] = multi_source
            result["entity_pairs"] = pairs
            result["multi_source_count"] = len(multi_source)
            result["entity_pair_count"] = len(pairs)
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["error"] = str(exc)

    return result


# ---------------------------------------------------------------------------
# Phase 4 — Mutation Proposals
# ---------------------------------------------------------------------------

def phase4_mutation_proposals(
    convergences: dict,
    prior_assessment: dict | None,
) -> dict:
    """Generate review-first mutation proposals from convergences.

    Cross-references multi_source convergences with:
    - active_threads.yaml → thread_intelligence proposals
    - watchlist.json     → watchlist_add proposals
    - yesterday's assessment → recurring pattern escalation
    """
    result: dict[str, Any] = {
        "status": "ok",
        "proposals": [],
        "proposals_count": 0,
    }

    multi_source: list[dict] = convergences.get("multi_source") or []
    if not multi_source:
        return result

    try:
        thread_entities = _load_active_thread_entities()   # {thread_id: [entity_lower, ...]}
        watchlist_entities = _load_watchlist_entities()    # {entity_lower, ...}

        # RB 2026-08-27: Todd, on watchlist_add proposals sitting unconfirmed
        # indefinitely -- "we might want to automatically add to the
        # watchlist and then remove if needed - there is no cockpit anymore
        # [to reliably confirm through]." High-confidence proposals now
        # auto-apply directly into ecosystem_intelligence.json's watch_list
        # (conservative defaults: priority=tier_2, interrupt_eligible=False,
        # so nothing escalates to urgent-interrupt status without a human
        # choosing that). Only auto-applies when the entity resolves
        # cleanly to exactly one existing graph entity via the same
        # resolver ecosystem_intelligence.py's own mutation pipeline uses
        # (_resolve_brand_entity_id) -- an ambiguous or genuinely-new-entity
        # case falls back to the original review-first proposal rather than
        # guessing at entity creation.
        import ecosystem_intelligence as _ei
        # RB 2026-08-27: confirmed live -- _read_graph's default parameter
        # (path: Path = core.ECOSYSTEM_INTELLIGENCE_PATH) is bound ONCE at
        # ecosystem_intelligence.py's first import in a process, not
        # re-evaluated per call. Calling _read_graph() with no argument
        # silently ignores any later reassignment of
        # core.ECOSYSTEM_INTELLIGENCE_PATH (e.g. test isolation) and always
        # reads whatever path was live at that first import -- which, in a
        # real run, wrote real auto-applied entries into the actual
        # production file during what should have been an isolated test.
        # Pass the path explicitly so this always reads/writes the real,
        # currently-configured path rather than a stale cached default.
        _graph = _ei._read_graph(core.ECOSYSTEM_INTELLIGENCE_PATH)
        _graph_dirty = False
        _existing_watch_entity_ids = {
            e.get("entity_id") for e in (_graph.get("watch_list") or [])
        }

        # Entities that appeared in yesterday's assessment
        prior_entities: set[str] = set()
        if prior_assessment and isinstance(prior_assessment, dict):
            for c in (prior_assessment.get("phase_3_convergences") or {}).get("multi_source") or []:
                prior_entities.add((c.get("entity") or "").lower())

        proposals: list[dict] = []
        seen_proposals: set[str] = set()  # (type, entity) dedup
        # RB-DEFECT-2026-09-18 (KFC watchlist reconciliation gap): every
        # watchlist_add auto-apply decision made below gets a mutation_policy
        # receipt once we know whether _ei._write_graph() actually succeeded
        # (see the _graph_dirty block after this loop) -- recording it here,
        # before the write is attempted, would risk claiming a mutation
        # happened when the write later failed and got rolled back.
        _watchlist_add_decisions: list[mutation_policy.MutationDecision] = []

        for conv in multi_source:
            entity = conv.get("entity") or ""
            if not entity:
                continue
            entity_lower = entity.lower()
            # RB-2026-09-03: confirmed live -- Global Payments (Todd's own
            # employer) was proposed as "[NEW COMPETITOR DETECTED] Global
            # Payments" with "Confirm to open an intelligence file for
            # Global Payments... and start tracking it." Global Payments/
            # Genius/Worldpay having no prior graph entity makes them look
            # exactly like a genuine new-entity discovery to this loop, but
            # they're the opposite of a competitor. Same exclusion list
            # already used for tech-stack intelligence (core.GP_OWN_TERMS,
            # see tech_stack_relationship_promotion.py's _OWN_COMPANY_TERMS
            # and render_intelligence_brief.py's _GP_OWN_TERMS) -- reused
            # here rather than duplicated again, since intelligence_
            # assessment.py already imports core.
            if any(term in entity_lower for term in core.GP_OWN_TERMS):
                continue
            item_count = conv.get("item_count") or 0
            sources = conv.get("sources") or []
            signal_types = conv.get("signal_types") or []
            is_recurring = entity_lower in prior_entities

            # ── thread_intelligence: entity matches an active thread ──
            for tid, t_entities in thread_entities.items():
                if entity_lower in t_entities:
                    key = f"thread_intelligence|{entity_lower}|{tid}"
                    if key not in seen_proposals:
                        seen_proposals.add(key)
                        proposals.append({
                            "type": "thread_intelligence",
                            "entity": entity,
                            "thread_id": tid,
                            "rationale": (
                                f"{entity} has {item_count} intelligence items "
                                f"across {len(sources)} sources in 30 days; "
                                f"matches active thread {tid}."
                            ),
                            "signal_types": signal_types,
                            "item_count": item_count,
                            "sources": sources,
                            "confidence": "high" if item_count >= 4 else "medium",
                            "is_recurring": is_recurring,
                            "requires_confirmation": True,
                        })

            # ── watchlist_add: sustained activity, not yet tracked ──
            if entity_lower not in watchlist_entities and item_count >= 3:
                key = f"watchlist_add|{entity_lower}"
                if key not in seen_proposals:
                    seen_proposals.add(key)
                    confidence = "high" if (item_count >= 5 or is_recurring) else "medium"
                    rationale = (
                        f"{entity} has {item_count} items across {len(sources)} "
                        f"sources in 30 days but is not on your watchlist."
                    )
                    requires_confirmation = True
                    auto_applied = False
                    proposal_type = "watchlist_add"
                    suggested_entity_id = None
                    if confidence == "high":
                        entity_id, is_new_entity = _ei._resolve_brand_entity_id(entity, _graph)
                        if is_new_entity and entity_id:
                            # Reversible autonomy: a high-confidence sustained
                            # company is added first and can be removed later.
                            # Create only a provisional brand shell; do not
                            # infer vendor status, tech stack, or posture.
                            _graph.setdefault("entities", []).append({
                                "id": entity_id,
                                "name": entity,
                                "entity_type": "brand",
                                "subtype": "restaurant_brand",
                                "status": "watch",
                                "domains": ["restaurants"],
                                "aliases": [],
                                "attributes": {
                                    "provisional": True,
                                    "discovered_by": "intelligence_assessment",
                                },
                                "sources": ["intelligence_assessment"],
                                "confidence": {
                                    "level": "high",
                                    "score": None,
                                    "rationale": rationale,
                                    "review_after": None,
                                },
                                "created_at": date.today().isoformat(),
                                "updated_at": date.today().isoformat(),
                            })
                            _graph.setdefault("watch_list", []).append({
                                "entity_id": entity_id,
                                "priority": "tier_2",
                                "added_at": date.today().isoformat(),
                                "added_by": "cos_suggested",
                                "reason": rationale,
                                "last_signal_at": None,
                                "interrupt_eligible": False,
                            })
                            _existing_watch_entity_ids.add(entity_id)
                            _graph_dirty = True
                            requires_confirmation = False
                            auto_applied = True
                            _watchlist_add_decisions.append(mutation_policy.decide(
                                source="intelligence_assessment:watchlist_add",
                                new_value=entity_id,
                                existing_value=None,
                                is_set_member=True,
                                confidence=1.0 if is_recurring else 0.9,
                                field_name="watch_list",
                                entity_id=entity_id,
                            ))
                        elif (
                            entity_id
                            and entity_id not in _existing_watch_entity_ids
                        ):
                            _graph.setdefault("watch_list", []).append({
                                "entity_id": entity_id,
                                "priority": "tier_2",
                                "added_at": date.today().isoformat(),
                                "added_by": "cos_suggested",
                                "reason": rationale,
                                "last_signal_at": None,
                                "interrupt_eligible": False,
                            })
                            _existing_watch_entity_ids.add(entity_id)
                            _graph_dirty = True
                            requires_confirmation = False
                            auto_applied = True
                            _watchlist_add_decisions.append(mutation_policy.decide(
                                source="intelligence_assessment:watchlist_add",
                                new_value=entity_id,
                                existing_value=None,
                                is_set_member=True,
                                confidence=1.0 if is_recurring else 0.9,
                                field_name="watch_list",
                                entity_id=entity_id,
                            ))
                    proposals.append({
                        "type": proposal_type,
                        "entity": entity,
                        "rationale": rationale,
                        "signal_types": signal_types,
                        "item_count": item_count,
                        "sources": sources,
                        "confidence": confidence,
                        "is_recurring": is_recurring,
                        "requires_confirmation": requires_confirmation,
                        "auto_applied": auto_applied,
                        "suggested_entity_id": suggested_entity_id,
                    })

        # Persist auto-applied watchlist_add entries, if any. Isolated from
        # the outer except -- a write/validation failure here must correct
        # the specific proposals it affected (fall back to review-first,
        # matching today's broader lesson: never claim something persisted
        # when it didn't), not discard every proposal phase 4 computed.
        if _graph_dirty:
            try:
                _ei._write_graph(_graph)
            except Exception:  # noqa: BLE001
                for p in proposals:
                    if p.get("type") == "watchlist_add" and p.get("auto_applied"):
                        p["auto_applied"] = False
                        p["requires_confirmation"] = True
                for decision in _watchlist_add_decisions:
                    mutation_policy.record_receipt(decision, artifact=None, applied=False)
            else:
                for decision in _watchlist_add_decisions:
                    mutation_policy.record_receipt(
                        decision,
                        artifact="ecosystem_intelligence.json:watch_list",
                        applied=True,
                    )

        # Sort: high confidence first, then by item_count descending
        proposals.sort(
            key=lambda p: (0 if p["confidence"] == "high" else 1, -p["item_count"])
        )
        result["proposals"] = proposals[:10]
        result["proposals_count"] = len(result["proposals"])

    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["error"] = str(exc)

    return result


# ---------------------------------------------------------------------------
# Phase 5 — Trust Stats
# ---------------------------------------------------------------------------

def phase5_trust_stats(
    p1: dict, p2: dict, p3: dict, p4: dict,
    started_at: str,
) -> dict:
    """Aggregate all phase outputs into the trust stats block."""
    from datetime import datetime as _dt

    source_health: list[dict] = p1.get("source_health") or []
    sources_assessed = len(source_health)
    sources_rejected = sum(
        1 for s in source_health
        if (s.get("status") or "") in ("error", "failed", "empty")
    )
    sources_accepted = sources_assessed - sources_rejected

    items_fetched = p1.get("items_fetched") or 0
    items_from_cache = p1.get("items_from_cache") or 0
    items_written = items_fetched - items_from_cache

    email_classified = p2.get("items_classified") or 0
    convergences = (p3.get("multi_source_count") or 0) + (p3.get("entity_pair_count") or 0)
    proposals = p4.get("proposals_count") or 0

    # Confidence: lowest across accepted sources
    errors: list[str] = []
    for phase_result in (p1, p2, p3, p4):
        if phase_result.get("status") == "error":
            errors.append(phase_result.get("error") or "unknown error")
        errs = phase_result.get("errors") or []
        if isinstance(errs, list):
            errors.extend(str(e) for e in errs if e)

    # Confidence: degrade if many sources failed or phases errored
    if sources_rejected >= (sources_assessed / 2) if sources_assessed else False:
        overall_confidence = "low"
    elif errors:
        overall_confidence = "medium"
    else:
        overall_confidence = "high"

    # Intelligence gaps
    gaps: list[str] = []
    if not source_health:
        gaps.append("Web scanner did not run — no RSS source health data.")
    if sources_rejected > 0:
        failed = [
            s.get("source") or s.get("name") or "unknown"
            for s in source_health
            if (s.get("status") or "") in ("error", "failed", "empty")
        ]
        gaps.append(f"Sources failed: {', '.join(failed[:5])}")
    if email_classified == 0 and p2.get("status") not in ("skipped_no_source", "skipped_empty"):
        gaps.append("No email headlines promoted to IntelligenceDB.")
    if convergences == 0:
        gaps.append("No sustained patterns detected in 30-day window — DB may be under-populated.")

    # Duration
    try:
        now = _dt.now(timezone.utc)
        start = _dt.fromisoformat(started_at)
        duration = round((now - start).total_seconds(), 1)
    except Exception:
        duration = 0.0

    return {
        # Unified trust stats contract (shared with on-demand ingest path)
        "source": "scheduled",
        "items_classified": email_classified,          # alias for unified contract
        "convergences_detected": convergences,
        "multi_source_entities": p3.get("multi_source_count") or 0,
        "entity_pairs_detected": p3.get("entity_pair_count") or 0,
        "mutation_proposals": proposals,
        "entity_context_hits": 0,                     # n/a for scheduled path
        "confidence": overall_confidence,
        "intelligence_gaps": gaps,
        # Scheduled-path extended fields
        "sources_assessed": sources_assessed,
        "sources_accepted": sources_accepted,
        "sources_rejected": sources_rejected,
        "items_fetched": items_fetched,
        "items_from_cache": items_from_cache,
        "items_written_to_db": items_written,
        "email_items_classified": email_classified,
        "phase_errors": errors,
        "duration_seconds": duration,
    }


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_assessment(*, today: date | None = None) -> dict:
    """Run all five phases and write intelligence_assessment.json.

    Returns the full assessment dict.
    """
    assessment_date = (today or date.today()).isoformat()
    started_at = _now_iso()

    # Load prior day's assessment for recurring-pattern detection in Phase 4
    prior: dict | None = None
    prior_raw = _load_json(PRIOR_ASSESSMENT_PATH)
    if isinstance(prior_raw, dict) and prior_raw.get("assessment_date") != assessment_date:
        prior = prior_raw  # yesterday's — safe to use

    # Run phases — each is best-effort
    errors: list[str] = []

    try:
        p1 = phase1_web_intelligence()
    except Exception as exc:  # noqa: BLE001
        p1 = {"status": "error", "error": str(exc), "items_fetched": 0,
              "items_from_cache": 0, "world_national": [], "industry_primary": [],
              "industry_technology": [], "source_health": [], "errors": [], "metadata": {}}
        errors.append(f"phase1: {exc}")

    try:
        import gatherer
        p_gatherer = gatherer.build_packet(p1)
        gatherer.write_packet(p_gatherer)
    except Exception as exc:  # noqa: BLE001
        p_gatherer = {"contract": "rb.gatherer_daily_change_packet.v1", "status": "error", "error": str(exc),
                      "changes": [], "hunter_escalations": []}
        errors.append(f"gatherer: {exc}")

    try:
        p2 = phase2_email_classification()
    except Exception as exc:  # noqa: BLE001
        p2 = {"status": "error", "error": str(exc), "items_classified": 0,
              "signal_types_found": [], "entities_detected": []}
        errors.append(f"phase2: {exc}")

    try:
        p3 = phase3_convergence_analysis()
    except Exception as exc:  # noqa: BLE001
        p3 = {"status": "error", "error": str(exc), "multi_source": [],
              "entity_pairs": [], "multi_source_count": 0, "entity_pair_count": 0}
        errors.append(f"phase3: {exc}")

    try:
        p4 = phase4_mutation_proposals(p3, prior)
    except Exception as exc:  # noqa: BLE001
        p4 = {"status": "error", "error": str(exc), "proposals": [], "proposals_count": 0}
        errors.append(f"phase4: {exc}")

    try:
        trust_stats = phase5_trust_stats(p1, p2, p3, p4, started_at)
    except Exception as exc:  # noqa: BLE001
        trust_stats = {"confidence": "low", "error": str(exc)}
        errors.append(f"phase5: {exc}")

    result: dict = {
        "assessment_date": assessment_date,
        "generated_at": _now_iso(),
        "contract": CONTRACT,
        "trust_stats": trust_stats,
        "phase_1_web": p1,
        "phase_2_gatherer": p_gatherer,
        "phase_2_email": p2,
        "phase_3_convergences": p3,
        "phase_4_proposals": p4,
        "run_errors": errors,
    }

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def is_fresh(today: date | None = None) -> bool:
    """Return True if today's assessment cache already exists."""
    if not CACHE_PATH.exists():
        return False
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return data.get("assessment_date") == (today or date.today()).isoformat()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--date", help="ISO date to assess. Defaults to today.")
    p.add_argument("--cache", action="store_true", help="Skip if today's assessment already exists.")
    p.add_argument("--smoke", action="store_true", help="Verify phases load; no writes.")
    p.add_argument("--json", action="store_true", dest="emit_json", help="Print JSON summary.")
    args = p.parse_args()

    today = date.fromisoformat(args.date) if args.date else date.today()

    if args.smoke:
        # Just verify imports work
        try:
            import web_scanner  # noqa: F401
            from intelligence_db import IntelligenceDB  # noqa: F401
            print("intelligence_assessment: smoke OK")
            return 0
        except ImportError as exc:
            print(f"intelligence_assessment: smoke FAIL — {exc}")
            return 1

    if args.cache and is_fresh(today):
        if args.emit_json:
            print(json.dumps({"skipped": True, "reason": "cache_fresh", "date": today.isoformat()}))
        else:
            print(f"intelligence_assessment: skipped (cache fresh for {today})")
        return 0

    result = run_assessment(today=today)
    ok = not result.get("run_errors")

    if args.emit_json:
        ts = result.get("trust_stats") or {}
        print(json.dumps({
            "ok": ok,
            "assessment_date": result["assessment_date"],
            "trust_stats": ts,
            "proposals_count": (result.get("phase_4_proposals") or {}).get("proposals_count", 0),
            "run_errors": result.get("run_errors"),
        }, indent=2))
    else:
        ts = result.get("trust_stats") or {}
        print(
            f"intelligence_assessment: {'OK' if ok else 'WARN'} | "
            f"sources {ts.get('sources_accepted', '?')}/{ts.get('sources_assessed', '?')} | "
            f"items {ts.get('items_fetched', '?')} fetched "
            f"({ts.get('items_written_to_db', '?')} new) | "
            f"email {ts.get('email_items_classified', '?')} classified | "
            f"convergences {ts.get('convergences_detected', '?')} | "
            f"proposals {ts.get('mutation_proposals', '?')}"
        )
        if result.get("run_errors"):
            for e in result["run_errors"]:
                print(f"  WARN: {e}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
