#!/usr/bin/env python3
"""
query_engine.py — Unified query surface across all four RB intelligence modules.

Accepts a natural-language question + optional entity name, parses intent
deterministically (no LLM), dispatches to whichever modules are relevant, and
returns a synthesized answer with per-module results and source attribution.

Modules:
  micro       — Micro graph (getMicroGraphSummary query types)
  macro       — Macro behavioral signals, artifacts, entity risk profiles
  relationship — Relationship interactions, Who Matters Now leaderboard
  intelligence — Intelligence DB entity summaries and signal convergences

Usage (CLI):
    python3 query_engine.py --entity "PAR Technology" --question "exit positioning?"
    python3 query_engine.py --entity "McDonald's" --question "how many operators?" --json
    python3 query_engine.py --question "who matters most right now?" --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import rb_core as core  # noqa: E402
import campaign_engine  # noqa: E402

# ---------------------------------------------------------------------------
# Intent signal tables — deterministic keyword scoring (no LLM)
# ---------------------------------------------------------------------------

_MICRO_SIGNALS = {
    "operator", "operators", "franchisee", "franchisees", "stores", "store count",
    "locations", "how many", "operator count", "largest", "top operators",
    "state", "states", "coop", "co-op", "cooperative", "graph", "network map",
    "relationship coverage", "who covers", "who runs", "nsn", "workbook",
    "micro graph", "ecosystem graph", "topology",
}

_MACRO_SIGNALS = {
    "consumer", "behavior", "behaviour", "affordability", "trade down", "value",
    "sentiment", "hesitation", "friction", "stress", "market signal", "macro",
    "brand risk", "platform", "positioning", "behavioral", "tech implication",
    "artifact", "parking lot", "migration", "operational pain", "industry signal",
    "category", "spending", "foot traffic", "market trend", "competitor",
    "multi-brand", "multi brand", "franchisee group", "franchisee groups",
    "operator group", "unit count", "unit counts", "brand portfolio",
}

_RELATIONSHIP_SIGNALS = {
    "who matters", "relationship", "contact", "touch", "last touch", "interaction",
    "reach out", "outreach", "broker", "introduce", "introduction", "warm",
    "signal", "trust", "dormant", "cooling", "reconnect", "owed", "follow up",
    "follow-up", "connection", "network", "tier", "inner", "lki", "lmi",
}

_INTELLIGENCE_SIGNALS = {
    "what do we know", "know about", "intelligence", "gathered", "recent",
    "summary", "everything", "signal", "acquisition", "exit", "positioning",
    "growth", "distress", "consolidation", "pattern", "convergence", "hypothesis",
    "thesis", "strategic", "m&a", "pe", "private equity", "activist",
    "leadership change", "earnings", "guidance", "filing", "insight",
}

_ECOSYSTEM_SIGNALS = {
    "tech stack", "technology stack", "pos", "point of sale", "payments",
    "loyalty", "crm", "back office", "inventory", "labor", "digital menu",
    "menu board", "dmb", "kds", "kitchen", "drive thru", "drive-thru",
    "vendor", "incumbent", "deployment", "rollout", "renewal", "case study",
    "press release", "restaurant technology", "technology vendor",
}

# Artifact signals: questions about open opportunities, dossiers, or account context
_ARTIFACT_SIGNALS = {
    "opportunity", "dossier", "account", "status", "pipeline", "interview",
    "recruiter", "application", "candidate", "role", "position", "offer",
    "proposal", "engagement", "consulting", "current status", "what is the status",
    "foods connected", "global payments", "par technology", "genius", "xenial",
}

# Campaign signals: conference/invitation/registration questions dispatch to
# campaign_engine.py (roster, coverage gaps, account-first recommendations)
# rather than the artifact/account-dossier module.
_CAMPAIGN_SIGNALS = {
    "campaign", "conference", "invite", "invites", "invited", "invitation",
    "invitations", "registration", "registrations", "registered", "attendee",
    "attendees", "roster", "coverage gap", "coverage gaps", "worldpay",
    "tier 0", "account coverage", "who should i invite", "genius conference",
    "genius user conference", "who has registered", "already invited",
    "already registered", "account first", "conference target",
}
_CAMPAIGN_ENTITY_TERMS = {"genius", "genius conference", "genius user conference"}

# Entity terms that strongly suggest micro graph dispatch
_MICRO_ENTITY_TERMS = {"mcdonald", "mcdonalds", "mcd", "nsn", "national supplier network"}

# Entity terms that strongly suggest artifact dispatch (registered account dossiers)
_ARTIFACT_ENTITY_TERMS = {
    "global payments", "genius", "xenial", "foods connected", "foodsconnected",
    "par technology", "par tech", "toast pos",
}

# ---------------------------------------------------------------------------
# Intent parsing
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> set[str]:
    """Lowercase unigrams and bigrams from text."""
    words = re.findall(r"[a-z0-9'&-]+", text.lower())
    unigrams = set(words)
    bigrams = {f"{words[i]} {words[i+1]}" for i in range(len(words) - 1)}
    return unigrams | bigrams


def parse_intent(entity: str | None, question: str) -> dict[str, float]:
    """Score each module 0.0–1.0 based on question + entity signals.

    Returns dict: {module: score} for micro, macro, relationship, intelligence.
    Any module scoring > 0 will be dispatched. Multiple modules may be active.
    """
    tokens = _tokenize((entity or "") + " " + question)

    def _score(signal_set: set[str]) -> float:
        hits = tokens & signal_set
        return min(1.0, len(hits) / max(1, min(3, len(signal_set) // 6)))

    scores: dict[str, float] = {
        "micro": _score(_MICRO_SIGNALS),
        "macro": _score(_MACRO_SIGNALS),
        "relationship": _score(_RELATIONSHIP_SIGNALS),
        "intelligence": _score(_INTELLIGENCE_SIGNALS),
        "ecosystem": _score(_ECOSYSTEM_SIGNALS),
        "artifact": _score(_ARTIFACT_SIGNALS),
        "campaign": _score(_CAMPAIGN_SIGNALS),
    }

    # Entity-level boosts
    if entity:
        entity_lower = entity.lower()
        if any(t in entity_lower for t in _MICRO_ENTITY_TERMS):
            scores["micro"] = max(scores["micro"], 0.6)
        # Artifact entity boost: registered dossier entities always route to artifact module
        if any(t in entity_lower for t in _ARTIFACT_ENTITY_TERMS):
            scores["artifact"] = max(scores["artifact"], 0.7)
        if any(t in entity_lower for t in _CAMPAIGN_ENTITY_TERMS):
            scores["campaign"] = max(scores["campaign"], 0.7)

    # Fallback: if nothing scores, activate intelligence (broadest module)
    if all(v == 0.0 for v in scores.values()):
        scores["intelligence"] = 0.4

    return scores


def _active_modules(scores: dict[str, float], threshold: float = 0.1) -> list[str]:
    """Return modules above threshold, sorted by score descending."""
    active = [(mod, s) for mod, s in scores.items() if s >= threshold]
    active.sort(key=lambda x: x[1], reverse=True)
    return [mod for mod, _ in active]


# ---------------------------------------------------------------------------
# Module dispatchers
# ---------------------------------------------------------------------------

def _dispatch_micro(entity: str | None, question: str) -> dict[str, Any]:
    """Query micro graph for the named entity."""
    try:
        import micro_graph_query as mgq  # noqa: F401
        graphs_dir = core.SYSTEM_DIR / "graphs"
        registry_path = graphs_dir / "index.json"
        if not registry_path.exists():
            return {"module": "micro", "status": "unavailable", "note": "no graph registry found"}

        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        graphs = registry.get("graphs", [])

        # Find usable graph matching entity (active or partial)
        entity_lower = (entity or "").lower()
        matched = None
        for g in graphs:
            if g.get("status") not in {"active", "partial"}:
                continue
            aliases = [
                g.get("entity", ""),
                g.get("name", ""),
                g.get("graph_id", ""),
                g.get("graph_slug", ""),
            ] + g.get("activation_terms", [])
            if any(entity_lower in a.lower() for a in aliases if a):
                matched = g
                break

        if not matched and graphs:
            # Fallback to first usable graph
            usable = [g for g in graphs if g.get("status") in {"active", "partial"}]
            if usable:
                matched = usable[0]

        if not matched:
            return {
                "module": "micro",
                "status": "no_active_graph",
                "note": f"No micro graph available for entity '{entity}'. Available: "
                        + ", ".join(g.get("name", g.get("graph_id", "")) for g in graphs),
            }

        graph_slug = matched.get("graph_slug") or matched.get("graph_id", "").replace("micro_ecosystem:", "")
        index_path = graphs_dir / "micro" / graph_slug / "index.json"
        if not index_path.exists():
            return {"module": "micro", "status": "index_missing", "graph_slug": graph_slug}

        index = json.loads(index_path.read_text(encoding="utf-8"))

        # Determine query_type from question tokens — most specific first
        q_lower = question.lower()
        if any(t in q_lower for t in ("coop", "co-op", "cooperative")):
            query_type = "coop_lookup"
        elif any(t in q_lower for t in ("state", "states")) and "operator" not in q_lower:
            query_type = "state_lookup"
        elif any(t in q_lower for t in ("largest", "top operator", "biggest")):
            query_type = "largest_operators"
        elif any(t in q_lower for t in ("relationship", "coverage", "contact")):
            query_type = "relationship_coverage"
        elif any(t in q_lower for t in ("how many", "operator count", "operator_count", "how many operators")):
            query_type = "operator_count"
        else:
            query_type = "summary"

        counts = index.get("counts", {})
        tiers = counts.get("operator_tiers", {})

        # Flatten counts for template rendering
        flat_counts = {**counts, **tiers}
        flat_counts.setdefault("stores_with_operator_entity", counts.get("stores_with_operator_entity", 0))

        # Try precomputed_answers first (Option C — computed at graph build
        # time by micro_graph_builder.py / micro_graph_query.build_precomputed_
        # answers), then fall back to live template rendering.
        precomputed = index.get("precomputed_answers", {})
        templates = index.get("retrieval_answer_templates", {})

        # Map query_type to template key
        _template_key_map = {
            "operator_count": "operator_distribution",
            "summary": "operator_distribution",
        }
        template_key = _template_key_map.get(query_type, query_type)

        # RB-DEFECT-2026-07-27: build_precomputed_answers() stores its four
        # covered query types under "coop_distribution" and "state_
        # distribution", but this dispatcher's own query_type detection above
        # produces "coop_lookup" / "state_lookup" -- two different naming
        # schemes for the same thing. precomputed.get(query_type) silently
        # missed every time for these two types, so a real, ready-to-cite
        # co-op or state breakdown sitting in index.json was never used --
        # every "which co-ops have the most stores?" / "how many stores per
        # state?" question fell all the way back to the generic
        # operator_distribution template instead, defeating Option C for
        # exactly the two query types it was built to answer fastest.
        _precomputed_key_map = {
            "coop_lookup": "coop_distribution",
            "state_lookup": "state_distribution",
        }
        precomputed_key = _precomputed_key_map.get(query_type, query_type)

        def _extract_answer(val) -> str:
            if isinstance(val, dict):
                return val.get("answer", "")
            return val or ""

        precomp_val = precomputed.get(precomputed_key) or precomputed.get("summary")
        if precomp_val:
            answer_text = _extract_answer(precomp_val)
        else:
            raw_template = templates.get(template_key) or templates.get("operator_distribution", "")
            try:
                answer_text = raw_template.format(**flat_counts) if raw_template else ""
            except (KeyError, ValueError):
                answer_text = raw_template

        # Supplement with structured counts for non-text query types
        structured: dict[str, Any] = {}
        if query_type == "largest_operators":
            structured["top_operators"] = index.get("top_operator_entities", [])[:10]
            if not answer_text:
                tops = structured["top_operators"][:3]
                answer_text = f"Largest operators: {', '.join(str(t) for t in tops)}" if tops else "No operator list available."
        elif query_type == "state_lookup":
            structured["state_distribution"] = index.get("state_distribution", {})
            if not answer_text:
                answer_text = f"Stores across {counts.get('states_with_stores', '?')} states."
        elif query_type == "coop_lookup":
            structured["coop_distribution"] = index.get("coop_distribution", {})
            if not answer_text:
                answer_text = f"{counts.get('coops_with_stores', '?')} co-ops with stores mapped."

        return {
            "module": "micro",
            "status": "ok",
            "graph_id": matched.get("graph_id"),
            "entity": matched.get("name", matched.get("entity")),
            "query_type": query_type,
            "answer": answer_text,
            "node_count": counts.get("nodes"),
            "edge_count": counts.get("edges"),
            "sources": [matched.get("source_workbook", "")],
            **structured,
        }

    except Exception as exc:  # noqa: BLE001
        return {"module": "micro", "status": "error", "error": str(exc)}


def _dispatch_macro(entity: str | None, question: str) -> dict[str, Any]:
    """Query macro behavioral intelligence for the entity or general signals."""
    try:
        import macro_intelligence as mi
        import franchisee_hierarchy as fh

        signals = mi.query_behavioral_signals()
        artifacts = mi.query_behavioral_artifacts()
        entities = mi.query_entity_risks()
        hierarchy_query = entity or question
        franchisee_groups = fh.query(hierarchy_query)
        # Broad hierarchy requests should return the largest multi-brand groups.
        if not entity and any(term in question.lower() for term in (
            "multi-brand", "multi brand", "franchisee group", "operator group", "largest"
        )):
            franchisee_groups = fh.query(multi_brand_only=True)

        # Filter to entity if provided
        if entity:
            entity_lower = entity.lower()
            signals = [s for s in signals if entity_lower in json.dumps(s).lower()]
            artifacts = [a for a in artifacts if entity_lower in json.dumps(a).lower()]
            entities = [e for e in entities if entity_lower in json.dumps(e).lower()]

        answer_parts: list[str] = []
        if signals:
            sig_types = list({s.get("signal_type", "unknown") for s in signals})
            answer_parts.append(f"{len(signals)} behavioral signal(s): {', '.join(sig_types[:5])}")
        if artifacts:
            art_names = [a.get("artifact_name", "") for a in artifacts[:3]]
            answer_parts.append(f"{len(artifacts)} behavioral artifact(s): {', '.join(art_names)}")
        if entities:
            risk_labels = [e.get("risk_label", "") for e in entities[:3]]
            answer_parts.append(f"{len(entities)} entity risk profile(s): {', '.join(risk_labels)}")
        if franchisee_groups:
            group_names = [g.get("name", "") for g in franchisee_groups[:5]]
            answer_parts.append(
                f"{len(franchisee_groups)} franchisee group(s): {', '.join(group_names)}"
            )

        answer = "; ".join(answer_parts) if answer_parts else "No macro intelligence found for this query."

        return {
            "module": "macro",
            "status": "ok",
            "signal_count": len(signals),
            "artifact_count": len(artifacts),
            "entity_count": len(entities),
            "signals": signals[:5],
            "artifacts": artifacts[:3],
            "entity_risks": entities[:3],
            "franchisee_group_count": len(franchisee_groups),
            "franchisee_groups": franchisee_groups[:25],
            "sources": ["system/franchisee_hierarchy.json"] if franchisee_groups else [],
            "answer": answer,
        }

    except Exception as exc:  # noqa: BLE001
        return {"module": "macro", "status": "error", "error": str(exc)}


def _dispatch_relationship(entity: str | None, question: str) -> dict[str, Any]:
    """Query relationship intelligence — interactions and WMN leaderboard."""
    try:
        import relationship_intake as ri

        q_lower = question.lower()
        wmn_requested = any(t in q_lower for t in ("who matters", "matters now", "wmn", "leaderboard", "top contact"))

        interactions = ri.query_interactions(
            contact_id=None,
            signal_type=None,
            claim_status=None,
        )

        # Filter to entity if provided and not a WMN question
        if entity and not wmn_requested:
            entity_lower = entity.lower()
            interactions = [i for i in interactions if entity_lower in json.dumps(i).lower()]

        wmn_items: list[dict] = []
        if wmn_requested or not interactions:
            wmn_items = ri.query_who_matters_now(top_n=5)

        answer_parts: list[str] = []
        if interactions:
            sig_types = list({i.get("signal_type", "unknown") for i in interactions})
            names = list({i.get("entity_name", "") for i in interactions if i.get("entity_name")})
            answer_parts.append(
                f"{len(interactions)} relationship interaction(s) on record "
                f"({', '.join(sig_types[:3])})"
                + (f" — contacts: {', '.join(names[:3])}" if names else "")
            )
        if wmn_items:
            top_names = [w.get("name", w.get("contact_id", "")) for w in wmn_items[:3]]
            answer_parts.append(f"Top contacts right now: {', '.join(top_names)}")

        answer = "; ".join(answer_parts) if answer_parts else "No relationship intelligence found for this query."

        return {
            "module": "relationship",
            "status": "ok",
            "interaction_count": len(interactions),
            "interactions": interactions[:5],
            "who_matters_now": wmn_items,
            "answer": answer,
        }

    except Exception as exc:  # noqa: BLE001
        return {"module": "relationship", "status": "error", "error": str(exc)}


def _dispatch_intelligence(entity: str | None, question: str) -> dict[str, Any]:
    """Query Intelligence DB — entity summary and signal convergences."""
    try:
        import intelligence_db as idb

        db = idb.IntelligenceDB()
        db.open()
        try:
            if entity:
                summary = db.entity_summary(entity, days=90)
                answer = (
                    f"{summary['item_count']} intelligence item(s) about {entity} in the last 90 days"
                    + (f" — signal types: {', '.join(summary['signal_types'][:4])}" if summary.get("signal_types") else "")
                    + (f" — sources: {', '.join(summary['sources'][:3])}" if summary.get("sources") else "")
                ) if summary["item_count"] > 0 else f"No gathered intelligence found for '{entity}' in the last 90 days."
                return {
                    "module": "intelligence",
                    "status": "ok",
                    "entity": entity,
                    "summary": summary,
                    "answer": answer,
                }
            else:
                # General convergence search — top converging entities.
                # RB-DEFECT-2026-09-18: this call site used to be the one
                # place find_convergences() wasn't also filtered for
                # distinct_days (daily_brief.py and intelligence_assessment.py
                # already did) -- min_distinct_days now defaults to 2 inside
                # find_convergences() itself, and this explicit arg documents
                # that this path relies on it too, same as the other two.
                convergences = db.find_convergences(min_entity_hits=2, days=30, min_distinct_days=2)
                answer = (
                    f"{len(convergences)} entity convergence(s) in the last 30 days"
                ) if convergences else "No entity convergences found in the last 30 days."
                return {
                    "module": "intelligence",
                    "status": "ok",
                    "convergences": convergences[:5],
                    "answer": answer,
                }
        finally:
            db.close()

    except Exception as exc:  # noqa: BLE001
        return {"module": "intelligence", "status": "error", "error": str(exc)}


def _dispatch_ecosystem(entity: str | None, question: str) -> dict[str, Any]:
    """Query canonical restaurant/vendor relationships plus the research queue.

    Canonical ecosystem relationships and unvalidated archive candidates are
    returned in separate fields. Candidate URLs must never be presented as an
    installed technology relationship until validated and ingested.
    """
    graph_path = core.SYSTEM_DIR / "ecosystem_intelligence.json"
    research_path = core.SYSTEM_DIR / "research" / "restaurant_tech_research_2026-07-21.json"
    try:
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        research = json.loads(research_path.read_text(encoding="utf-8")) if research_path.exists() else {}
        entities = graph.get("entities") or []
        by_id = {e.get("id"): e for e in entities}
        needle = (entity or "").strip().lower()
        matched = None
        if needle:
            for item in entities:
                names = [item.get("name", ""), *(item.get("aliases") or [])]
                if any(needle == str(n).lower() or needle in str(n).lower() for n in names):
                    matched = item
                    break

        relationships = []
        if matched:
            for rel in graph.get("relationships") or []:
                if rel.get("from_entity_id") != matched.get("id"):
                    continue
                vendor = by_id.get(rel.get("to_entity_id"), {})
                relationships.append({
                    "vendor": vendor.get("name"),
                    "category": rel.get("category"),
                    "product": rel.get("product"),
                    "status": rel.get("status"),
                    "evidence_posture": rel.get("evidence_posture"),
                    "confidence": rel.get("confidence"),
                    "interpretation_scope": rel.get("interpretation_scope"),
                    "deployment": rel.get("deployment"),
                    "sources": rel.get("sources") or [],
                })

        candidates = []
        if needle:
            candidates = [c for c in (research.get("candidate_evidence") or [])
                          if needle == str(c.get("Brand", "")).lower()
                          or needle in str(c.get("Brand", "")).lower()]
        candidates = candidates[:50]

        if matched:
            answer = f"{matched.get('name')}: {len(relationships)} canonical vendor relationship(s)"
            if candidates:
                answer += f" and {len(candidates)} unvalidated archive candidate page(s)."
            else:
                answer += "."
        elif entity:
            answer = f"No canonical ecosystem entity matched '{entity}'."
            if candidates:
                answer += f" {len(candidates)} unvalidated research candidate(s) matched."
        else:
            summary = research.get("summary") or {}
            answer = (
                f"Restaurant technology research covers {summary.get('brand_universe', 0)} brands; "
                f"{summary.get('candidate_pages', 0)} candidate pages across "
                f"{summary.get('matched_brands', 0)} brands await validation."
            )
        return {
            "module": "ecosystem", "status": "ok", "entity": matched,
            "canonical_relationships": relationships,
            "unvalidated_candidates": candidates,
            "research_summary": research.get("summary") or {},
            "answer": answer,
            "sources": [str(graph_path), str(research_path)] if research_path.exists() else [str(graph_path)],
        }
    except Exception as exc:  # noqa: BLE001
        return {"module": "ecosystem", "status": "error", "error": str(exc)}


def _dispatch_signals(entity: str | None, question: str) -> dict[str, Any]:
    """Query signal synthesis for pattern classification (exit/growth/distress)."""
    if not entity:
        return {"module": "signals", "status": "skipped", "note": "entity required for signal synthesis"}
    try:
        import signal_synthesis as ss
        import competitor_intelligence_common as cic

        # RB-2026-09-16: an ad-hoc question naming a tracked competitor by
        # an alternate name (e.g. "NCR" for "NCR Voyix") used to miss every
        # signal recorded under its canonical name or other aliases --
        # entity_convergence_scan.py's daily scan got this fix first
        # (2026-09-16) but this human-facing query path didn't. Fails
        # closed to [] for anything that isn't a tracked competitor
        # (Blue Sheet customers, generic text), so behavior is unchanged
        # for the common case.
        aliases = cic.resolve_aliases(entity) or None
        result = ss.synthesize_entity_signals(entity, aliases=aliases)
        pattern = result.get("dominant_pattern", "unknown")
        hypothesis = result.get("synthesis_hypothesis", "")
        signal_count = result.get("signal_count", 0)

        answer = f"Signal pattern: {pattern} ({signal_count} signal(s))"
        if hypothesis:
            answer += f" — {hypothesis}"

        return {
            "module": "signals",
            "status": "ok",
            "entity": entity,
            "dominant_pattern": pattern,
            "signal_count": signal_count,
            "hypothesis": hypothesis,
            "opportunity_or_risk": result.get("opportunity_or_risk", ""),
            "answer": answer,
        }
    except Exception as exc:  # noqa: BLE001
        return {"module": "signals", "status": "error", "error": str(exc)}


def _dispatch_earnings_trend(entity: str | None, question: str) -> dict[str, Any]:
    """Query the earnings-history trend log (RB-2026-09-05, Predictive
    Market Intelligence Phase 1) -- dimension-mix shift and event-frequency
    change over a company's recorded earnings history."""
    if not entity:
        return {"module": "earnings_trend", "status": "skipped", "note": "entity required for earnings trend"}
    try:
        import earnings_trend as et

        result = et.compute_entity_trend(entity)
        return {"module": "earnings_trend", **result}
    except Exception as exc:  # noqa: BLE001
        return {"module": "earnings_trend", "status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

def _dispatch_artifact(entity: str | None, question: str) -> dict[str, Any]:
    """Query the RB artifact registry for account dossiers and seeded intelligence.

    Covers account_dossier, competitive_intelligence, and other non-micro-graph
    artifacts registered in system/artifacts/registry.json.  This fills the gap
    where intelligence_db has no data but a seeded dossier exists — e.g. Global
    Payments, Foods Connected, PAR Technology.
    """
    if not entity:
        return {"module": "artifact", "status": "skipped", "note": "entity required for artifact lookup"}

    registry_path = core.SYSTEM_DIR / "artifacts" / "registry.json"
    if not registry_path.exists():
        return {"module": "artifact", "status": "unavailable", "note": "artifact registry not found"}

    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"module": "artifact", "status": "error", "error": str(exc)}

    entity_lower = entity.lower().strip()
    matched: dict | None = None
    for art in registry.get("artifacts") or []:
        # Check entity name and aliases
        if entity_lower in (art.get("entity") or "").lower():
            matched = art
            break
        aliases = [a.lower() for a in (art.get("entity_aliases") or [])]
        if any(entity_lower in a or a in entity_lower for a in aliases):
            matched = art
            break
        # Also check artifact name
        if entity_lower in (art.get("name") or "").lower():
            matched = art
            break

    if not matched:
        return {
            "module": "artifact",
            "status": "not_found",
            "entity": entity,
            "answer": f"No registered artifact found for '{entity}'. Run POST /artifacts to register.",
        }

    art_id = matched.get("artifact_id") or ""
    art_status = matched.get("status") or "unknown"
    art_type = matched.get("artifact_type") or "artifact"
    art_name = matched.get("name") or entity

    # Build base answer from registry metadata
    freshness = matched.get("freshness_date") or "unknown"
    notes = matched.get("notes") or []

    answer_parts = [f"[{art_status.upper()}] {art_name} ({art_type}) — last updated {freshness}."]

    # Load dossier data if available
    dossier_data: dict = {}
    data_path_rel = matched.get("data_path")
    if data_path_rel:
        data_path = core.PROJECT_DIR / data_path_rel
        if data_path.exists():
            try:
                dossier_data = json.loads(data_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass

    if dossier_data:
        summary = dossier_data.get("summary") or ""
        if summary:
            answer_parts.append(summary[:200])

        contacts = dossier_data.get("key_contacts") or []
        if contacts:
            names = [c.get("name") or "" for c in contacts[:3] if c.get("name")]
            answer_parts.append(f"Key contacts: {', '.join(names)}.")

        intel = dossier_data.get("intelligence") or {}
        if intel:
            for k, v in list(intel.items())[:3]:
                answer_parts.append(f"{k.replace('_',' ').title()}: {str(v)[:80]}")

        opp = dossier_data.get("opportunity") or {}
        if opp:
            answer_parts.append(
                f"Opportunity: {opp.get('role','')} — {opp.get('status','')}."
            )

        next_actions = dossier_data.get("next_actions") or []
        if next_actions:
            answer_parts.append(f"Next actions: {next_actions[0]}")

    elif notes:
        # Fall back to registry notes
        answer_parts.extend([n[:120] for n in notes[:3] if n])

    return {
        "module": "artifact",
        "status": "ok",
        "entity": entity,
        "artifact_id": art_id,
        "artifact_type": art_type,
        "artifact_status": art_status,
        "freshness_date": freshness,
        "answer": " ".join(answer_parts),
        "dossier_summary": dossier_data.get("summary") if dossier_data else None,
        "sources": [f"system/artifacts/registry.json", *(
            [matched.get("data_path")] if matched.get("data_path") else []
        )],
    }


def _resolve_campaign_id(entity: str | None, question: str) -> tuple[str | None, list[dict]]:
    """Match entity/question text against known campaigns by id or name.
    Falls back to the single active campaign when exactly one exists and
    nothing else disambiguates it."""
    campaigns = campaign_engine.list_campaigns()
    if not campaigns:
        return None, []
    needle = ((entity or "") + " " + question).lower()
    for c in campaigns:
        cid = (c.get("id") or "").lower().replace("-", " ")
        cname = (c.get("name") or "").lower()
        if cid and cid in needle:
            return c["id"], campaigns
        if cname and any(word in needle for word in cname.split() if len(word) > 3):
            return c["id"], campaigns
    if len(campaigns) == 1:
        return campaigns[0]["id"], campaigns
    return None, campaigns


def _dispatch_campaign(entity: str | None, question: str) -> dict[str, Any]:
    """Query campaign_engine.py's already-built roster/coverage-gap/account-
    first artifacts — conference invitation, registration, and account
    coverage questions. Reads precomputed campaign state; never re-runs
    eligibility/scoring itself (that's --build-roster's job)."""
    campaign_id, known = _resolve_campaign_id(entity, question)
    if not campaign_id:
        return {
            "module": "campaign", "status": "ambiguous" if known else "not_found",
            "answer": (
                f"Multiple campaigns exist ({[c['id'] for c in known]}); say which one."
                if known else "No campaigns exist yet. Run campaign_engine.py --build-roster to create one."
            ),
        }

    roster = campaign_engine._load_roster(campaign_id)
    if not roster:
        return {"module": "campaign", "status": "not_found", "campaign_id": campaign_id,
                "answer": f"Campaign {campaign_id!r} has no roster yet — run --build-roster first."}

    config = campaign_engine.load_campaign_config(campaign_id)
    q = question.lower()
    d = campaign_engine._campaign_dir(campaign_id)
    sources = [campaign_engine._display_path(d / "roster.json")]

    # Route to the specific slice the question is actually asking for.
    if any(t in q for t in ("coverage gap", "coverage gaps", "where do we have gaps", "weak")):
        gaps_path = d / "coverage_gaps.json"
        gaps = json.loads(gaps_path.read_text(encoding="utf-8")) if gaps_path.exists() else []
        sources.append(campaign_engine._display_path(gaps_path))
        top = gaps[:10]
        answer = (
            f"{len(gaps)} coverage gaps identified. Top: " +
            "; ".join(f"{g['company']} ({', '.join(g['gap_types'])}, introducer: {g.get('recommended_introducer') or 'none'})" for g in top[:5])
        ) if gaps else "No coverage gaps computed yet."
        return {"module": "campaign", "status": "ok", "campaign_id": campaign_id, "gap_count": len(gaps),
                "top_gaps": top, "answer": answer, "sources": sources}

    if any(t in q for t in ("restaurant operator", "restaurant operators")):
        rows = campaign_engine.query_restaurant_operator_prospects(roster, config, limit=20)
        answer = f"{len(rows)} restaurant-operator prospects not yet invited (showing up to 20). Top: " + \
                 "; ".join(f"{r['name']} ({r['company']})" for r in rows[:5]) if rows else "None found."
        return {"module": "campaign", "status": "ok", "campaign_id": campaign_id,
                "restaurant_operators": rows, "answer": answer, "sources": sources}

    if any(t in q for t in ("registered", "who's registered", "who has registered")) and "not" not in q:
        registered = [p for p in roster["prospects"] if p["status"] == "registered"]
        answer = f"{len(registered)} registered so far." if registered else "No one has registered yet."
        return {"module": "campaign", "status": "ok", "campaign_id": campaign_id,
                "registered": registered, "answer": answer, "sources": sources}

    if any(t in q for t in ("invited", "invite", "invitation")) and "not" in q or "follow up" in q:
        queue_b = campaign_engine.build_queue_b(roster, config)
        answer = f"{len(queue_b)} invited but not yet registered. Top: " + \
                 "; ".join(f"{r['name']} ({r['company']})" for r in queue_b[:5]) if queue_b else "No one is in this state."
        return {"module": "campaign", "status": "ok", "campaign_id": campaign_id,
                "invited_not_registered": queue_b, "answer": answer, "sources": sources}

    # Broad trigger set — added 2026-07-14 after a live GPT round-trip showed
    # "give me a Top 100" fell through to the dashboard-summary default below
    # (which only ever exposes a 5-6 row preview), because none of the
    # original narrower phrases ("priority list", "top priorities", ...)
    # matched. A 'top N' number in the question sets the limit directly, so
    # 'Top 100' actually returns up to 100 full rows, not a fixed 30.
    top_n_match = re.search(r"top\s+(\d+)", q)
    if top_n_match or any(t in q for t in (
        "priority list", "priority invite list", "ranked list", "rank everyone", "top priorities",
        "full list", "full roster", "ranked roster", "detailed list", "complete list",
        "all prospects", "top invites", "top candidates", "invite list",
    )):
        limit = min(int(top_n_match.group(1)), 500) if top_n_match else 100
        priority_rows = campaign_engine.build_priority_invite_list(roster, config, limit=limit)
        priority_path = d / "priority_invite_list.json"
        if priority_path.exists():
            sources.append(campaign_engine._display_path(priority_path))
        answer = (
            f"Top {len(priority_rows)} priority invites (relationship-first ranking — 40% relationship / "
            "25% executive buying authority / 20% strategic brand value / 10% recent engagement / 5% "
            "existing invite status). Full ranked list is in the priority_invite_list field — render all "
            "of it as a table, don't just summarize the first few. Preview: " + "; ".join(
                f"{r['priority']}. {r['contact']} ({r['brand']}) — {r['invite_status']}" for r in priority_rows[:5]
            )
        ) if priority_rows else "No prospects on the priority list yet."
        return {"module": "campaign", "status": "ok", "campaign_id": campaign_id,
                "priority_invite_list": priority_rows, "answer": answer, "sources": sources}

    if any(t in q for t in ("who should i invite", "invite next", "net new", "not yet invited")):
        queue_a = campaign_engine.build_queue_a(roster, config, limit=20)
        answer = f"Top invitation targets: " + "; ".join(f"{r['name']} ({r['company']}, score {r['score']})" for r in queue_a[:5])
        return {"module": "campaign", "status": "ok", "campaign_id": campaign_id,
                "queue_a_top": queue_a, "answer": answer, "sources": sources}

    if entity:
        rollup_path = d / "company_rollup.json"
        if rollup_path.exists():
            rollup = json.loads(rollup_path.read_text(encoding="utf-8"))
            entity_lower = entity.lower()
            match = next((c for c in rollup["companies"] if entity_lower in c["company"].lower()), None)
            if match:
                answer = (
                    f"{match['company']}: {match['contact_count']} known contact(s), best contact "
                    f"{match['best_contact_name']} (score {match['best_contact_score']}), status: {match['status_summary']}."
                )
                sources.append(campaign_engine._display_path(rollup_path))
                return {"module": "campaign", "status": "ok", "campaign_id": campaign_id,
                        "company": match, "answer": answer, "sources": sources}

    # Default: overall campaign status summary. Always also attaches the
    # full priority_invite_list, not just the dashboard's 10-brand preview —
    # added 2026-07-14 after a GPT round-trip showed a question that missed
    # every specific trigger phrase above fell through to here and got only
    # the small dashboard preview, which the GPT correctly refused to pad
    # out to "Top 100" by inventing entries. Rather than keep chasing an
    # ever-growing list of magic phrases, the fallback itself now always
    # carries the complete real data.
    dashboard_path = d / "executive_dashboard.json"
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8")) if dashboard_path.exists() else {}
    sources.append(campaign_engine._display_path(dashboard_path))
    priority_rows = campaign_engine.build_priority_invite_list(roster, config, limit=100)
    priority_path = d / "priority_invite_list.json"
    if priority_path.exists():
        sources.append(campaign_engine._display_path(priority_path))
    answer = (
        f"{dashboard.get('eligible_enterprise_prospects', len(roster['prospects']))} eligible prospects across "
        f"{dashboard.get('company_count', '?')} companies. "
        f"{dashboard.get('invited_not_registered', 0)} invited, not registered; "
        f"{dashboard.get('registered', 0)} registered; "
        f"{dashboard.get('worldpay_customer_conversion_opportunities', 0)} Tier 0 conversion opportunities. "
        f"The full ranked list of all {len(priority_rows)} prospects is in the priority_invite_list field — "
        "render the complete table from it, not just a preview."
    )
    return {"module": "campaign", "status": "ok", "campaign_id": campaign_id,
            "dashboard": dashboard, "priority_invite_list": priority_rows, "answer": answer, "sources": sources}


_MODULE_DISPATCH = {
    "micro": _dispatch_micro,
    "macro": _dispatch_macro,
    "relationship": _dispatch_relationship,
    "intelligence": _dispatch_intelligence,
    "ecosystem": _dispatch_ecosystem,
    "artifact": _dispatch_artifact,
    "campaign": _dispatch_campaign,
}

# Signal synthesis activates when intelligence or macro score high + entity present
_SIGNAL_TRIGGERS = {"exit", "positioning", "pattern", "growth", "distress", "consolidation",
                    "m&a", "acquisition", "activist", "pe ", "private equity"}

# RB-2026-09-05: earnings-trend activates on its own trigger set (not just
# _SIGNAL_TRIGGERS) since "trend"/"quarter over quarter"/"earnings" questions
# about a company don't always carry an exit/growth/distress keyword, but do
# still want the dimension-mix/frequency read over the earnings-history log.
_EARNINGS_TREND_TRIGGERS = {"trend", "earnings", "quarter over quarter", "quarterly",
                            "momentum", "frequency", "recurring"}


def query(
    entity: str | None,
    question: str,
    *,
    modules: list[str] | None = None,
    threshold: float = 0.1,
) -> dict[str, Any]:
    """Entry point. Parse intent, dispatch, synthesize.

    Parameters
    ----------
    entity    : Named entity (company or person). Optional.
    question  : Natural language question.
    modules   : If set, override intent parsing and dispatch only these modules.
    threshold : Intent score threshold for module activation (default 0.1).

    Returns
    -------
    {
        contract      : "rb_query_v1",
        entity        : str | None,
        question      : str,
        intent_scores : {module: float},
        modules_used  : [str],
        results       : {module: {...}},
        answer        : str,
        sources       : [str],
        generated_at  : ISO str,
    }
    """
    from datetime import datetime

    intent_scores = parse_intent(entity, question)

    if modules:
        active = [m for m in modules if m in _MODULE_DISPATCH]
    else:
        active = _active_modules(intent_scores, threshold=threshold)

    # Activate signal synthesis when entity present + question has pattern signals
    q_lower = question.lower()
    if entity and any(t in q_lower for t in _SIGNAL_TRIGGERS):
        if "signals" not in active:
            active.append("signals")

    # Activate earnings-trend when entity present + question asks about trend/
    # quarterly pattern/momentum -- also piggybacks on the signals trigger set
    # (a "growth"/"pattern" question about a company plausibly wants both reads).
    if entity and any(t in q_lower for t in _EARNINGS_TREND_TRIGGERS | _SIGNAL_TRIGGERS):
        if "earnings_trend" not in active:
            active.append("earnings_trend")

    results: dict[str, Any] = {}
    for mod in active:
        fn = _MODULE_DISPATCH.get(mod)
        if fn:
            results[mod] = fn(entity, question)
        elif mod == "signals":
            results["signals"] = _dispatch_signals(entity, question)
        elif mod == "earnings_trend":
            results["earnings_trend"] = _dispatch_earnings_trend(entity, question)

    # Build synthesized answer
    answer_lines: list[str] = []
    sources: list[str] = []

    for mod in active + (["signals"] if "signals" in results and "signals" not in active else []):
        r = results.get(mod, {})
        if r.get("status") == "ok" and r.get("answer"):
            answer_lines.append(f"[{mod.upper()}] {r['answer']}")
        for s in r.get("sources", []):
            if s and s not in sources:
                sources.append(s)

    if not answer_lines:
        answer = "No results found across queried modules."
    elif len(answer_lines) == 1:
        answer = answer_lines[0]
    else:
        answer = "\n".join(answer_lines)

    return {
        "contract": "rb_query_v1",
        "entity": entity,
        "question": question,
        "intent_scores": intent_scores,
        "modules_used": list(results.keys()),
        "results": results,
        "answer": answer,
        "sources": sources,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="Unified RB query engine.")
    p.add_argument("--entity", default=None, help="Entity name (company or person).")
    p.add_argument("--question", required=True, help="Natural language question.")
    p.add_argument("--modules", default=None, help="Comma-separated module override (micro,macro,relationship,intelligence,signals).")
    p.add_argument("--threshold", type=float, default=0.1, help="Intent score threshold (default 0.1).")
    p.add_argument("--json", action="store_true", help="Output full JSON result.")
    args = p.parse_args()

    module_list = [m.strip() for m in args.modules.split(",")] if args.modules else None

    result = query(
        entity=args.entity,
        question=args.question,
        modules=module_list,
        threshold=args.threshold,
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    print(f"Query: {result['question']}")
    if result["entity"]:
        print(f"Entity: {result['entity']}")
    print(f"Modules: {', '.join(result['modules_used'])}")
    print()
    print(result["answer"])
    if result["sources"]:
        print(f"\nSources: {', '.join(result['sources'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
