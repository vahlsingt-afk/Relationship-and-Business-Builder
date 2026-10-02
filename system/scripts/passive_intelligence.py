#!/usr/bin/env python3
"""
passive_intelligence.py — confidence-weighted assessment for passive inputs.

Passive inputs are uploaded posts, screenshots, vendor claims, copied articles,
or social commentary. This module turns them into intelligence evaluations before
any ecosystem graph mutation can happen.

The implementation is deterministic by design: it does not invent external
corroboration. It scores the source, extracts factual claims, checks local RB
ecosystem evidence for matching/contradicting records, and returns explicit
mutation eligibility. External search can add sources later, but uncertainty is
preserved until corroboration exists.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402


CLAIM_STATUS_ORDER = [
    "verified",
    "likely_true",
    "plausible_but_unverified",
    "disputed",
    "likely_false",
    "insufficient_evidence",
]

GRAPH_MUTATION_ELIGIBILITY = [
    "canonical_fact",
    "corroborated_intelligence",
    "weak_signal",
    "emerging_narrative",
    "vendor_positioning",
    "not_eligible",
]

SOURCE_TYPE_QUALITY: dict[str, dict[str, Any]] = {
    "sec_filing": {"authority": "primary", "quality": "primary", "commercial_incentive": "low", "base": 0.92},
    "earnings_call": {"authority": "primary", "quality": "primary", "commercial_incentive": "medium", "base": 0.88},
    "primary_operator_statement": {"authority": "primary", "quality": "primary", "commercial_incentive": "medium", "base": 0.84},
    "engineering_blog": {"authority": "primary", "quality": "strong", "commercial_incentive": "medium", "base": 0.78},
    "credible_reporting": {"authority": "secondary", "quality": "strong", "commercial_incentive": "low", "base": 0.74},
    "conference_statement": {"authority": "primary_or_operator", "quality": "medium", "commercial_incentive": "medium", "base": 0.66},
    "linkedin_operator_post": {"authority": "operator_anecdote", "quality": "medium", "commercial_incentive": "medium", "base": 0.54},
    "linkedin_vendor_post": {"authority": "vendor_claim", "quality": "medium", "commercial_incentive": "high", "base": 0.42},
    "vendor_claim": {"authority": "vendor_claim", "quality": "weak", "commercial_incentive": "high", "base": 0.36},
    "opinion": {"authority": "commentary", "quality": "weak", "commercial_incentive": "unknown", "base": 0.28},
    "unknown": {"authority": "unknown", "quality": "unknown", "commercial_incentive": "unknown", "base": 0.22},
}

NARRATIVE_KEYWORDS: dict[str, list[str]] = {
    "vendor_claim": ["our customers", "customer momentum", "book a demo", "platform", "roi", "case study"],
    "opinion": ["i think", "my take", "hot take", "believe", "should", "will be"],
    "rumor": ["heard", "rumor", "apparently", "sources say", "word is"],
    "strategic_weak_signal": ["budget", "compute cost", "defect", "cancel", "exhausted", "overrun", "adoption"],
    "anecdotal_operator_feedback": ["we tried", "our team", "operators", "engineers report", "in practice"],
    "verified_reporting": ["reported", "according to", "filing", "earnings call", "transcript"],
    "thought_leadership_narrative": ["future of", "trend", "thesis", "narrative", "what this means"],
    "marketing_adjacent_positioning": ["unlock", "transform", "revolutionize", "category leader", "trusted by"],
}

FACTUAL_CLAIM_PATTERNS = (
    r"\b(cancelled|canceled|exhausted|exceeded|acknowledged|reported|found|increased|decreased|grew|declined|launched|acquired|replaced|migrated|uses|selected|signed|terminated)\b",
    r"\b\d+(?:\.\d+)?\s?(?:x|%|percent|months?|years?|million|billion|m|b)\b",
    r"\b(costs?|budgets?|licenses?|defects?|prs?|compute|labor|contracts?|rollout|deployment)\b",
)

CONTRADICTION_TERMS = {
    "denied",
    "disputed",
    "refuted",
    "false",
    "not true",
    "no evidence",
    "contradicted",
}

PRIMARY_CORROBORATION_SOURCE_TYPES = [
    "sec_filing",
    "earnings_call",
    "primary_operator_statement",
    "engineering_blog",
    "conference_statement",
]

SECONDARY_CORROBORATION_SOURCE_TYPES = [
    "credible_reporting",
    "public_interview",
    "existing_ecosystem_reference",
    "prior_rb_signal",
]


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "unknown"


def _load_graph() -> dict:
    path = core.ECOSYSTEM_INTELLIGENCE_PATH
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return []
    pieces = re.split(r"(?<=[.!?])\s+|(?:\n|^)\s*[-•*]\s+", normalized)
    return [p.strip(" -•*\t") for p in pieces if p.strip(" -•*\t")]


def classify_source(metadata: dict | None) -> dict:
    """Classify a passive source without upgrading its factual authority."""
    metadata = metadata or {}
    raw_type = str(metadata.get("source_type") or "").lower()
    platform = str(metadata.get("platform") or "").lower()
    title = str(metadata.get("title") or metadata.get("source_name") or "").lower()
    url = str(metadata.get("url") or "").lower()
    blob = f"{raw_type} {platform} {title} {url}"

    if "sec" in blob or "10-k" in blob or "10-q" in blob:
        source_type = "sec_filing"
    elif "earnings" in blob or "transcript" in blob:
        source_type = "earnings_call"
    elif "engineering" in blob or "tech blog" in blob:
        source_type = "engineering_blog"
    elif "operator" in blob or "customer" in blob:
        source_type = "primary_operator_statement"
    elif "linkedin" in blob and ("vendor" in blob or "sales" in blob):
        source_type = "linkedin_vendor_post"
    elif "linkedin" in blob:
        source_type = "linkedin_operator_post"
    elif "vendor" in blob or "case study" in blob or "customer story" in blob:
        source_type = "vendor_claim"
    elif "report" in blob or "article" in blob or "press" in blob:
        source_type = "credible_reporting"
    elif "opinion" in blob or "commentary" in blob:
        source_type = "opinion"
    else:
        source_type = raw_type if raw_type in SOURCE_TYPE_QUALITY else "unknown"

    model = SOURCE_TYPE_QUALITY[source_type]
    return {
        "source_type": source_type,
        "source_authority": model["authority"],
        "source_quality": model["quality"],
        "commercial_incentive_exposure": model["commercial_incentive"],
        "platform_context": platform or metadata.get("platform") or "unknown",
        "historical_reliability": metadata.get("historical_reliability") or "unknown",
        "relationship_proximity": metadata.get("relationship_proximity") or "unknown",
        "base_confidence": model["base"],
    }


def classify_narrative(text: str, source: dict | None = None) -> str:
    haystack = f"{text or ''} {(source or {}).get('source_type') or ''}".lower()
    for label, keywords in NARRATIVE_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            return label
    return "strategic_weak_signal"


def extract_claims(text: str) -> list[dict]:
    """Extract factual-looking claims while leaving narrative framing behind."""
    claims: list[dict] = []
    for sentence in _sentences(text):
        if not any(re.search(pattern, sentence, re.IGNORECASE) for pattern in FACTUAL_CLAIM_PATTERNS):
            continue
        claim_id = f"claim-{len(claims) + 1:02d}-{_slug(sentence)[:48]}"
        claims.append({
            "claim_id": claim_id,
            "claim_text": sentence[:500],
            "claim_kind": "factual_assertion",
            "extracted_entities": _extract_entities(sentence),
        })
    if not claims and text.strip():
        claims.append({
            "claim_id": "claim-01-overall-narrative",
            "claim_text": text.strip()[:500],
            "claim_kind": "narrative_or_opinion",
            "extracted_entities": _extract_entities(text),
        })
    return claims


def _extract_entities(text: str) -> list[str]:
    candidates = re.findall(r"\b[A-Z][A-Za-z0-9&'.-]*(?:\s+[A-Z][A-Za-z0-9&'.-]*){0,3}", text or "")
    blocked = {"The", "This", "That", "When", "If", "AI", "PRs"}
    out = []
    for candidate in candidates:
        value = candidate.strip()
        if value in blocked or len(value) < 3:
            continue
        if value not in out:
            out.append(value)
    return out[:8]


def _tokens(value: str) -> set[str]:
    words = {
        w
        for w in re.findall(r"[a-z0-9]{3,}", (value or "").lower())
        if w not in {"the", "and", "for", "with", "that", "this", "from", "have", "has", "are", "was"}
    }
    return words


def corroborate_claim(claim: dict, graph: dict | None = None) -> dict:
    """Check existing RB graph evidence for overlap with a claim."""
    graph = graph if graph is not None else _load_graph()
    claim_tokens = _tokens(claim.get("claim_text", ""))
    matches: list[dict] = []
    contradictions: list[dict] = []

    records: list[tuple[str, dict, str]] = []
    for source in graph.get("sources", []) if graph else []:
        records.append(("source", source, " ".join(str(source.get(k) or "") for k in ("title", "notes", "url"))))
    for signal in graph.get("signals", []) if graph else []:
        records.append(("signal", signal, " ".join(str(signal.get(k) or "") for k in ("summary", "interpretation", "signal_type"))))
    for rel in graph.get("relationships", []) if graph else []:
        records.append(("relationship", rel, " ".join(str(rel.get(k) or "") for k in ("relationship_type", "category", "product", "strategic_note", "evidence_posture"))))

    for record_type, record, haystack in records:
        overlap = claim_tokens & _tokens(haystack)
        if len(overlap) < 3:
            continue
        item = {
            "record_type": record_type,
            "record_id": record.get("id"),
            "overlap_terms": sorted(overlap)[:8],
        }
        if any(term in haystack.lower() for term in CONTRADICTION_TERMS):
            contradictions.append(item)
        else:
            matches.append(item)

    return {
        "corroboration_count": len(matches),
        "corroboration_sources": [m["record_id"] for m in matches if m.get("record_id")][:5],
        "contradiction_count": len(contradictions),
        "contradiction_sources": [m["record_id"] for m in contradictions if m.get("record_id")][:5],
        "local_matches": matches[:5],
    }


def score_claim(claim: dict, source_assessment: dict, corroboration: dict, narrative_classification: str) -> dict:
    base = float(source_assessment.get("base_confidence") or 0.22)
    score = base
    score += min(0.28, 0.14 * int(corroboration.get("corroboration_count") or 0))
    score -= min(0.35, 0.18 * int(corroboration.get("contradiction_count") or 0))
    # Commercial-incentive penalty: vendor claims and marketing language cannot
    # benefit from their own source authority. Rumours carry equivalent uncertainty.
    if narrative_classification in {"vendor_claim", "marketing_adjacent_positioning", "rumor"}:
        score -= 0.08
    if claim.get("claim_kind") == "narrative_or_opinion":
        score -= 0.12
    score = max(0.0, min(1.0, round(score, 2)))

    if corroboration.get("contradiction_count"):
        status = "disputed" if score >= 0.35 else "likely_false"
    elif score >= 0.82 and corroboration.get("corroboration_count", 0) >= 1:
        status = "verified"
    elif score >= 0.68 and corroboration.get("corroboration_count", 0) >= 1:
        status = "likely_true"
    elif score >= 0.35:
        status = "plausible_but_unverified"
    else:
        status = "insufficient_evidence"

    if status == "verified":
        eligibility = "canonical_fact"
    elif status == "likely_true":
        eligibility = "corroborated_intelligence"
    elif status == "plausible_but_unverified":
        if narrative_classification in {"vendor_claim", "marketing_adjacent_positioning"}:
            eligibility = "vendor_positioning"
        elif narrative_classification == "rumor":
            eligibility = "emerging_narrative"
        else:
            eligibility = "weak_signal"
    elif status == "disputed":
        eligibility = "emerging_narrative"
    else:
        eligibility = "not_eligible"

    return {
        "confidence_score": score,
        "claim_status": status,
        "graph_mutation_eligibility": eligibility,
    }


def strategic_relevance_score(claim: dict, text: str) -> float:
    relevant_terms = {
        "budget", "cost", "compute", "labor", "defect", "deployment", "vendor",
        "operator", "restaurant", "pos", "payments", "license", "cancel", "adoption",
        "microsoft", "uber", "nvidia", "ai", "coding",
    }
    score = 0.2 + min(0.5, len(_tokens(claim.get("claim_text", "")) & relevant_terms) * 0.08)
    if any(entity for entity in claim.get("extracted_entities", []) if entity.lower() in (text or "").lower()):
        score += 0.1
    return round(min(1.0, score), 2)


def build_corroboration_search_plan(claim: dict, source_assessment: dict, scoring: dict) -> dict:
    """Describe the source work needed before RB can upgrade a passive claim.

    This is intentionally a plan, not invented corroboration. It gives the
    retrieval layer and GPT caller concrete source classes and search queries
    while preserving uncertainty until actual sources are added.
    """
    claim_text = claim.get("claim_text", "")
    entities = claim.get("extracted_entities") or []
    query_subject = " ".join(entities[:3]) or claim_text
    claim_status = scoring.get("claim_status")
    eligibility = scoring.get("graph_mutation_eligibility")
    source_type = source_assessment.get("source_type")

    required_sources = 1 if source_type in {"sec_filing", "earnings_call", "primary_operator_statement"} else 2
    if claim_status in {"verified", "likely_true"}:
        required_sources = 0
    elif eligibility == "vendor_positioning":
        required_sources = max(required_sources, 2)

    preferred_source_types = PRIMARY_CORROBORATION_SOURCE_TYPES + SECONDARY_CORROBORATION_SOURCE_TYPES
    if "defect" in claim_text.lower() or "prs" in claim_text.lower() or "code" in claim_text.lower():
        preferred_source_types = [
            "engineering_blog",
            "credible_reporting",
            "public_interview",
            "prior_rb_signal",
            "existing_ecosystem_reference",
        ]
    elif any(term in claim_text.lower() for term in ["budget", "cost", "license", "compute"]):
        preferred_source_types = [
            "earnings_call",
            "conference_statement",
            "credible_reporting",
            "public_interview",
            "existing_ecosystem_reference",
            "prior_rb_signal",
        ]

    search_queries = []
    if query_subject:
        search_queries.extend([
            f'"{query_subject}" "{claim_text[:80]}"',
            f'"{query_subject}" earnings call transcript AI compute cost budget',
            f'"{query_subject}" engineering blog AI code defects',
            f'"{query_subject}" interview conference statement AI coding budget',
        ])

    return {
        "status": "satisfied" if required_sources == 0 else "needed",
        "minimum_new_corroborating_sources": required_sources,
        "preferred_source_types": preferred_source_types[:6],
        "search_queries": search_queries[:4],
        "promotion_rule": (
            "Do not promote to canonical fact until a primary source or at least two independent credible sources support the claim."
            if required_sources else
            "Existing source/corroboration is sufficient for current claim status; preserve source links."
        ),
        "disqualifiers": [
            "same-author reposts",
            "vendor case-study reuse without operator confirmation",
            "opinion threads without named evidence",
            "articles that cite only the passive source under evaluation",
        ],
    }


def evaluate_passive_intelligence(
    content: str,
    source_metadata: dict | None = None,
    graph: dict | None = None,
    today: date | None = None,
) -> dict:
    """Return a canonical passive-intelligence evaluation."""
    today = today or date.today()
    graph = graph if graph is not None else _load_graph()
    source = classify_source(source_metadata)
    narrative = classify_narrative(content, source)
    claims = []

    for claim in extract_claims(content):
        corroboration = corroborate_claim(claim, graph)
        scoring = score_claim(claim, source, corroboration, narrative)
        claims.append({
            **claim,
            **source,
            **corroboration,
            **scoring,
            "corroboration_search_plan": build_corroboration_search_plan(claim, source, scoring),
            "verification_timestamp": str(today),
            "narrative_classification": narrative,
            "strategic_relevance_score": strategic_relevance_score(claim, content),
            "uncertainty_preserved": scoring["graph_mutation_eligibility"] not in {"canonical_fact", "corroborated_intelligence"},
        })

    max_score = max((c["confidence_score"] for c in claims), default=0.0)
    verified_count = sum(1 for c in claims if c["claim_status"] in {"verified", "likely_true"})
    weak_count = sum(1 for c in claims if c["graph_mutation_eligibility"] in {"weak_signal", "emerging_narrative", "vendor_positioning"})
    return {
        "contract": "rb_passive_intelligence_evaluation_v1",
        "evaluated_at": _now(),
        "verification_timestamp": str(today),
        "source_assessment": source,
        "narrative_classification": narrative,
        "claim_count": len(claims),
        "claims": claims,
        "summary": {
            "highest_confidence_score": max_score,
            "verified_or_likely_true_claims": verified_count,
            "weak_or_emerging_claims": weak_count,
            "canonical_graph_mutation_allowed": verified_count > 0,
            "requires_external_corroboration": any(c["claim_status"] in {"plausible_but_unverified", "insufficient_evidence"} for c in claims),
            "corroboration_search_required": any((c.get("corroboration_search_plan") or {}).get("status") == "needed" for c in claims),
            "corroboration_search_queue": [
                {
                    "claim_id": c.get("claim_id"),
                    "claim_text": c.get("claim_text"),
                    "minimum_new_corroborating_sources": (c.get("corroboration_search_plan") or {}).get("minimum_new_corroborating_sources", 0),
                    "preferred_source_types": (c.get("corroboration_search_plan") or {}).get("preferred_source_types", []),
                    "search_queries": (c.get("corroboration_search_plan") or {}).get("search_queries", []),
                }
                for c in claims
                if (c.get("corroboration_search_plan") or {}).get("status") == "needed"
            ],
        },
    }


def signal_metadata_from_evaluation(evaluation: dict) -> dict:
    """Compress an evaluation into graph signal metadata."""
    claims = evaluation.get("claims") or []
    if not claims:
        return {}
    primary = max(
        claims,
        key=lambda c: (c.get("strategic_relevance_score") or 0, c.get("confidence_score") or 0),
    )
    return {
        "source_type": primary.get("source_type"),
        "source_quality": primary.get("source_quality"),
        "confidence_score": primary.get("confidence_score"),
        "corroboration_count": primary.get("corroboration_count", 0),
        "corroboration_sources": primary.get("corroboration_sources", []),
        "claim_status": primary.get("claim_status"),
        "verification_timestamp": primary.get("verification_timestamp"),
        "narrative_classification": primary.get("narrative_classification"),
        "graph_mutation_eligibility": primary.get("graph_mutation_eligibility"),
        "strategic_relevance_score": primary.get("strategic_relevance_score"),
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate passive intelligence content.")
    parser.add_argument("content", help="Text content to evaluate.")
    parser.add_argument("--source-type", default=None)
    parser.add_argument("--platform", default=None)
    parser.add_argument("--title", default=None)
    args = parser.parse_args()

    print(json.dumps(evaluate_passive_intelligence(
        args.content,
        {"source_type": args.source_type, "platform": args.platform, "title": args.title},
    ), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
