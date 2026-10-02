#!/usr/bin/env python3
"""Deterministic, explainable ranking for RB's most important learnings."""
from __future__ import annotations

import re
from datetime import date

MATERIAL_SIGNAL_WEIGHTS = {
    "acquisition": 20, "merger": 20, "earnings": 18, "financial": 17,
    "exec-change": 17, "leadership_change": 17, "closure": 17,
    "funding": 16, "vendor_relationship_formed": 16, "partnership": 14,
    "expansion": 12, "product-launch": 10, "regulatory": 12,
}
STOPWORDS = {
    "after", "before", "being", "business", "company", "current", "daily",
    "from", "global", "industry", "intelligence", "market", "national",
    "news", "restaurant", "restaurants", "signal", "source", "technology",
    "their", "this", "today", "watchlist", "week", "with", "world", "year",
    "plan", "your",
}


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower())
            if len(token) >= 4 and token not in STOPWORDS and not token.isdigit()}


def _item_text(item: dict) -> str:
    extras = item.get("extras") or {}
    return " ".join(str(value or "") for value in (
        item.get("title"), item.get("summary"), item.get("why_it_matters"),
        extras.get("entity_name"), " ".join(extras.get("entities") or []),
    ))


def _objective_tokens(sections: dict) -> set[str]:
    tokens: set[str] = set()
    for key in ("weekly_plan_focus", "active_opportunity_pipeline"):
        for item in sections.get(key) or []:
            tokens |= _tokens(_item_text(item))
    return tokens


def score_item(item: dict, *, objective_tokens: set[str], today: date) -> dict:
    extras = item.get("extras") or {}
    text_tokens = _tokens(_item_text(item))
    overlap = objective_tokens & text_tokens
    relevance = min(25, len(overlap) * 5)
    domain = str(extras.get("domain") or "").lower()
    if domain in {"restaurant_technology", "restaurant-tech", "technology"}:
        relevance = max(relevance, 12)
    elif domain in {"restaurant_industry", "restaurant"}:
        relevance = max(relevance, 8)
    if extras.get("watchlist_status") in {"Escalation", "New Activity"}:
        relevance = max(relevance, 18)
    if extras.get("account_slug") or extras.get("competitor_slug"):
        relevance = max(relevance, 20)

    signal_type = str(extras.get("signal_type") or item.get("signal_type") or "general")
    magnitude = MATERIAL_SIGNAL_WEIGHTS.get(signal_type, 6)
    title_lower = str(item.get("title") or "").lower()
    for key, weight in MATERIAL_SIGNAL_WEIGHTS.items():
        if key.replace("-", " ") in title_lower:
            magnitude = max(magnitude, weight)

    urgency = 3
    disposition = item.get("disposition")
    if disposition == "act_today":
        urgency = 15
    elif disposition in {"prepare", "review"}:
        urgency = 10
    pub_date = str(extras.get("pub_date") or extras.get("published_date") or "")[:10]
    if pub_date:
        try:
            age = (today - date.fromisoformat(pub_date)).days
            if age <= 1:
                urgency = max(urgency, 12)
            elif age <= 7:
                urgency = max(urgency, 8)
        except ValueError:
            pass

    lifecycle = str(extras.get("lifecycle_state") or item.get("lifecycle_state") or "NEW")
    novelty = 15 if lifecycle == "NEW" else (10 if lifecycle == "REACTIVATED" else 4)

    confidence = str(item.get("confidence") or extras.get("confidence") or "unknown").lower()
    confidence_score = {"high": 10, "medium": 7, "low": 3}.get(confidence, 2)
    source_count = len(set(str(ref) for ref in item.get("source_refs") or [] if ref))
    evidence = min(15, confidence_score + min(5, max(0, source_count - 1) * 2))

    entities = extras.get("entities") or []
    reach = min(10, max(2, len(set(entities)) * 2))
    if extras.get("convergence_count"):
        reach = min(10, reach + 3)

    breakdown = {"todd_relevance": relevance, "commercial_magnitude": magnitude,
                 "urgency": urgency, "novelty": novelty,
                 "evidence_strength": evidence, "ecosystem_reach": reach}
    return {"score": sum(breakdown.values()), "score_breakdown": breakdown,
            "matched_objective_terms": sorted(overlap)[:8], "signal_type": signal_type}


def rank_insights(sections: dict, *, today: date, limit: int = 5) -> list[dict]:
    objective_tokens = _objective_tokens(sections)
    candidates = []
    seen = set()
    for item in ((sections.get("what_todd_doesnt_know_yet") or [])
                 + (sections.get("new_intelligence_today") or [])
                 + (sections.get("reactivated_intelligence") or [])):
        title = str(item.get("title") or "").strip()
        if not title or title.startswith("No Materially New"):
            continue
        fingerprint = re.sub(r"\W+", " ", title.lower()).strip()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        candidates.append({"item": item, **score_item(item, objective_tokens=objective_tokens, today=today)})
    candidates.sort(key=lambda row: (-row["score"], str(row["item"].get("title") or "").lower()))
    return candidates[:limit]
