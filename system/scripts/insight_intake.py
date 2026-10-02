#!/usr/bin/env python3
"""
insight_intake.py — Conversational insight extraction and mutation-proof path.

Detects durable strategic insights inside free-form text (conversations,
LinkedIn analysis, market reports, Daily Brief review, social signals).
Classifies each insight, emits review-first mutation proposals, and persists
confirmed insights with full traceability.

The signal path:
  signal → classification → confidence → proposed mutation →
  persistence proof → retrieval → future use

Invariants:
  - All mutations require_confirmation=True. No auto-mutations.
  - Every output insight has a non-None persistence_status.
  - Insights are written to conversation_insights.json and retrievable by tag/type.
  - No auto-send, no auto-close, no relationship-tier promotion without confirmation.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parent.parent
# Uses conversation_insights.json to avoid schema collision with strategic_memory.py
# which owns strategic_memory.json (schema: rb_persistent_strategic_memory_v1).
STRATEGIC_MEMORY_PATH = Path(os.environ.get(
    "RB_CONVERSATION_INSIGHTS_PATH", str(SYSTEM_DIR / "conversation_insights.json")
))

INSIGHT_TYPES = frozenset({
    "industry_trend",
    "vendor_positioning",
    "user_positioning",
    "thought_leadership_theme",
    "market_signal",
    "relationship_implication",
    "action_opportunity",
})

PERSISTENCE_STATUSES = frozenset({
    "RB recorded",
    "RB updated",
    "RB proposed",
    "RB blocked",
    "RB skipped",
    "RB did not persist",
    "pending confirmation",
})

_SOURCE_TYPES = frozenset({
    "conversation",
    "linkedin_analysis",
    "market_analysis",
    "daily_brief_review",
    "social_signal",
    "uploaded_artifact",
})

# Keyword sets per insight type (substring-matched, case-insensitive).
# A sentence matches a type if at least one keyword appears in it.
_TYPE_KEYWORDS: dict[str, set[str]] = {
    "industry_trend": {
        "retention",
        "economics",
        "frequency",
        "customer",
        "restaurant",
        "operator",
        "hospitality",
        "adoption",
        "commodit",
        "consolidat",
        "structural shift",
        "trend",
        "market is",
        "industry is",
        "behavior",
    },
    "market_signal": {
        "report",
        "data",
        "survey",
        "percent",
        "%",
        "ratio",
        "research",
        "study",
        "evidence",
        "metric",
        "statistic",
        "times more",
        "times as",
    },
    "vendor_positioning": {
        "position",
        "reframe",
        "competitive",
        "differentiat",
        "infrastructure",
        "solution",
        "platform",
        "vendor",
        "category",
        "moat",
        "not just",
        "rather than",
        "instead of",
    },
    "user_positioning": {
        "your angle",
        "your thesis",
        "your narrative",
        "your take",
        "position yourself",
        "opportunity for you",
        "todd can",
        "you can position",
    },
    "thought_leadership_theme": {
        "thesis",
        "perspective",
        "contrarian",
        "insight",
        "argument",
        "case for",
        "why it matters",
        "the real",
        "reframe",
        "narrative",
        "thought leadership",
        "original take",
    },
    "relationship_implication": {
        "implication",
        "relevant to",
        "worth sharing",
        "should tell",
        "mention to",
        "for your contact",
        "aligns with",
        "supports your",
    },
    "action_opportunity": {
        "opportunity",
        "timing",
        "opening for",
        "right time",
        "window for",
        "approach",
        "pitch",
        "propose",
        "now is",
    },
}

# Domain-specific retrieval tags and their trigger keywords (substring match).
_DOMAIN_TAGS: dict[str, list[str]] = {
    "restaurant_tech": ["restaurant", "hospitality", "pos ", "toast", "operator"],
    "retention_economics": ["retention", "churn", "frequency", "loyalty"],
    "linkedin_positioning": ["linkedin", "thought leadership", "content strategy"],
    "vendor_intelligence": ["vendor", "platform", "infrastructure", "solution"],
}


# --------------------------------------------------------------------------- #
# Internal helpers                                                             #
# --------------------------------------------------------------------------- #

def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"insight-{ts}-{uuid.uuid4().hex[:6]}"


def _split_sentences(text: str) -> list[str]:
    raw = [s.strip() for s in text.replace("\n", " ").split(".")]
    return [s for s in raw if len(s) > 10]


def _classify_text(text: str) -> list[dict]:
    """Classify text into insight types using keyword substring matching.

    Returns list of dicts with insight_type, confidence, matched_keywords,
    and representative_sentences, sorted highest-confidence first.
    """
    sentences = _split_sentences(text)
    text_lower = text.lower()

    type_hits: dict[str, set[str]] = {t: set() for t in INSIGHT_TYPES}
    type_sentences: dict[str, list[str]] = {t: [] for t in INSIGHT_TYPES}

    for insight_type, keywords in _TYPE_KEYWORDS.items():
        for sentence in sentences:
            sl = sentence.lower()
            hits = [kw for kw in keywords if kw in sl]
            if hits:
                type_hits[insight_type].update(hits)
                if sentence not in type_sentences[insight_type]:
                    type_sentences[insight_type].append(sentence)

    # Also scan full text for multi-word keywords that might span sentence splits
    for insight_type, keywords in _TYPE_KEYWORDS.items():
        multi_word = [kw for kw in keywords if " " in kw]
        for kw in multi_word:
            if kw in text_lower:
                type_hits[insight_type].add(kw)

    conf_rank = {"high": 0, "medium": 1, "low": 2}
    results = []
    for insight_type, hits in type_hits.items():
        if not hits:
            continue
        unique = list(hits)[:5]
        confidence = "high" if len(unique) >= 3 else "medium" if len(unique) >= 2 else "low"
        results.append({
            "insight_type": insight_type,
            "confidence": confidence,
            "matched_keywords": unique,
            "representative_sentences": type_sentences[insight_type][:2],
        })

    results.sort(key=lambda x: conf_rank[x["confidence"]])
    return results


def _extract_claim(representative_sentences: list[str], text: str) -> str:
    if representative_sentences:
        ranked = sorted(representative_sentences, key=lambda s: min(len(s), 200), reverse=True)
        return ranked[0][:300]
    return text[:200]


def _build_mutation_proposals(insight_type: str, claim: str, source_type: str, confidence: str) -> list[dict]:
    """Build review-first mutation proposals. All require confirmation."""
    proposals = []

    if insight_type in ("industry_trend", "market_signal"):
        proposals.append({
            "mutation_type": "industry_graph",
            "target": "system/graphs/industry_intelligence.json",
            "operation": "add",
            "value": {
                "claim": claim[:200],
                "source_type": source_type,
                "confidence": confidence,
            },
            "requires_confirmation": True,
            "persistence_endpoint": "POST /insights/confirm",
        })

    if insight_type in ("vendor_positioning", "user_positioning"):
        proposals.append({
            "mutation_type": "user_positioning",
            "target": "system/00_TODD_PROFILE.md",
            "operation": "add",
            "value": {
                "positioning_claim": claim[:200],
                "source_type": source_type,
            },
            "requires_confirmation": True,
            "persistence_endpoint": "POST /insights/confirm",
        })

    if insight_type == "thought_leadership_theme":
        proposals.append({
            "mutation_type": "strategic_memory",
            "target": "system/strategic_memory.json",
            "operation": "add",
            "value": {
                "theme": claim[:200],
                "reuse_tags": ["linkedin_post", "thought_leadership", "positioning"],
            },
            "requires_confirmation": True,
            "persistence_endpoint": "POST /insights/confirm",
        })

    if insight_type == "relationship_implication":
        proposals.append({
            "mutation_type": "contact_ri",
            "target": "baseline_index.json",
            "operation": "flag_for_review",
            "value": {
                "implication": claim[:200],
                "source_type": source_type,
            },
            "requires_confirmation": True,
            "persistence_endpoint": "POST /insights/confirm",
        })

    if insight_type == "action_opportunity":
        proposals.append({
            "mutation_type": "strategic_memory",
            "target": "system/strategic_memory.json",
            "operation": "add",
            "value": {
                "opportunity": claim[:200],
                "reuse_tags": ["action_opportunity", "follow_up"],
            },
            "requires_confirmation": True,
            "persistence_endpoint": "POST /insights/confirm",
        })

    return proposals


def _build_future_use_tags(insight_type: str, text_lower: str) -> list[str]:
    tags = ["strategic_memory"]
    _type_tags = {
        "industry_trend": ["daily_brief", "industry_graph", "market_intelligence"],
        "market_signal": ["daily_brief", "market_intelligence"],
        "vendor_positioning": ["thought_leadership", "positioning", "daily_brief"],
        "user_positioning": ["positioning", "thought_leadership"],
        "thought_leadership_theme": ["thought_leadership", "linkedin_post", "positioning"],
        "relationship_implication": ["relationship_management", "contact_intelligence"],
        "action_opportunity": ["daily_brief", "execution_planning"],
    }
    tags.extend(_type_tags.get(insight_type, []))
    for domain_tag, keywords in _DOMAIN_TAGS.items():
        if any(kw in text_lower for kw in keywords):
            tags.append(domain_tag)
    return list(dict.fromkeys(tags))


# --------------------------------------------------------------------------- #
# Persistence                                                                  #
# --------------------------------------------------------------------------- #

def _load_strategic_memory(store_path: Path | None = None) -> dict:
    path = store_path or STRATEGIC_MEMORY_PATH
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"_schema_version": "1.0", "insights": []}


def _save_strategic_memory(memory: dict, store_path: Path | None = None) -> None:
    path = store_path or STRATEGIC_MEMORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(memory, indent=2, default=str))


def _write_pending_insights(insights: list[dict], store_path: Path | None = None) -> None:
    memory = _load_strategic_memory(store_path=store_path)
    existing_ids = {i.get("id") for i in memory.get("insights", [])}
    for insight in insights:
        if insight["id"] not in existing_ids:
            memory["insights"].append(insight)
    memory["_last_updated"] = _timestamp()
    _save_strategic_memory(memory, store_path=store_path)


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #

def process_text(
    text: str,
    source_type: str = "conversation",
    context: dict | None = None,
    store_path: Path | None = None,
) -> dict:
    """Extract durable strategic insights from free-form text.

    Returns:
      insights             — classified insight records, each with persistence_status
      mutation_proposals   — flat list of all review-first mutation proposals
      retrieval_tags       — all unique tags for retrieval
      persistence_status   — summary status for the batch
    """
    if not text or not text.strip():
        return {
            "insights": [],
            "mutation_proposals": [],
            "retrieval_tags": [],
            "persistence_status": "RB did not persist",
            "note": "Empty input. No insights extracted.",
        }

    if source_type not in _SOURCE_TYPES:
        source_type = "conversation"

    text_lower = text.lower()
    classifications = _classify_text(text)
    now = _timestamp()
    insights = []
    all_mutations: list[dict] = []
    all_tags: list[str] = []

    for cls in classifications:
        insight_id = _make_id()
        claim = _extract_claim(cls["representative_sentences"], text)
        mutations = _build_mutation_proposals(
            cls["insight_type"], claim, source_type, cls["confidence"]
        )
        tags = _build_future_use_tags(cls["insight_type"], text_lower)

        insight = {
            "id": insight_id,
            "source_type": source_type,
            "source_text_snippet": text[:300],
            "created_at": now,
            "insight_type": cls["insight_type"],
            "claim": claim,
            "confidence": cls["confidence"],
            "matched_keywords": cls["matched_keywords"],
            "claim_status": "proposed",
            "future_use_tags": tags,
            "proposed_mutations": mutations,
            "persistence_status": "pending confirmation",
            "confirmed_at": None,
        }
        insights.append(insight)
        all_mutations.extend(mutations)
        all_tags.extend(tags)

    if not insights:
        return {
            "insights": [],
            "mutation_proposals": [],
            "retrieval_tags": [],
            "persistence_status": "RB did not persist",
            "note": "No durable strategic insights detected in input text.",
        }

    _write_pending_insights(insights, store_path=store_path)

    return {
        "insights": insights,
        "mutation_proposals": all_mutations,
        "retrieval_tags": list(dict.fromkeys(all_tags)),
        "persistence_status": "pending confirmation",
        "insight_count": len(insights),
        "mutation_proposal_count": len(all_mutations),
    }


def record_insight(
    insight_id: str,
    confirmed: bool = True,
    store_path: Path | None = None,
) -> dict:
    """Confirm or reject a pending insight.

    Returns the updated insight record, or an error dict if not found.
    """
    memory = _load_strategic_memory(store_path=store_path)
    for idx, entry in enumerate(memory.get("insights", [])):
        if entry.get("id") == insight_id:
            if confirmed:
                memory["insights"][idx]["claim_status"] = "confirmed"
                memory["insights"][idx]["persistence_status"] = "RB recorded"
                memory["insights"][idx]["confirmed_at"] = _timestamp()
            else:
                memory["insights"][idx]["claim_status"] = "rejected"
                memory["insights"][idx]["persistence_status"] = "RB skipped"
            memory["_last_updated"] = _timestamp()
            _save_strategic_memory(memory, store_path=store_path)
            return memory["insights"][idx]
    return {"error": f"Insight {insight_id} not found."}


def query_insights(
    tags: list[str] | None = None,
    insight_type: str | None = None,
    claim_status: str | None = None,
    store_path: Path | None = None,
) -> list[dict]:
    """Retrieve insights from strategic memory by tag, type, or claim status."""
    memory = _load_strategic_memory(store_path=store_path)
    results = list(memory.get("insights", []))

    if insight_type:
        results = [i for i in results if i.get("insight_type") == insight_type]
    if claim_status:
        results = [i for i in results if i.get("claim_status") == claim_status]
    if tags:
        results = [
            i for i in results
            if any(t in (i.get("future_use_tags") or []) for t in tags)
        ]
    return results


def query_narrative_convergence(tags: list[str], store_path: Path | None = None) -> dict:
    """Return convergence count and confidence for a set of tags.

    Used by DEFECT-009: counts how many confirmed insights share the given
    tags, indicating narrative/thesis alignment accumulation over time.
    """
    confirmed = query_insights(tags=tags, claim_status="confirmed", store_path=store_path)
    pending = query_insights(tags=tags, claim_status="proposed", store_path=store_path)
    total = len(confirmed) + len(pending)
    confidence = "high" if len(confirmed) >= 3 else "medium" if total >= 2 else "low"
    return {
        "tag_query": tags,
        "confirmed_count": len(confirmed),
        "pending_count": len(pending),
        "total_count": total,
        "convergence_confidence": confidence,
    }
