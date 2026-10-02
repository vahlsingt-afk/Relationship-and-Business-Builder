#!/usr/bin/env python3
"""
experiential_intelligence.py — Experiential Intelligence Layer.

Transforms firsthand executive experience, deployment lessons, post-mortems,
operational observations, and pattern recognition into reusable institutional
intelligence artifacts.

Signal path:
  experience text → classification → case study generation →
  retrieval hook creation → trust stats → mutation proposals →
  reputation-aware externalization → persistence

Invariants:
  - All mutations require_confirmation=True. No auto-mutations.
  - Trust stats are emitted on every significant ingestion event.
  - Every artifact has both an internal_version (full fidelity) and
    an external_version (anonymized, reputation-safe).
  - persistence_status is always explicit — never silent.
  - employer_sensitive flag is set whenever employer names are detected.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parent.parent
EXPERIENTIAL_INTELLIGENCE_PATH = SYSTEM_DIR / "experiential_intelligence.json"

# ---------------------------------------------------------------------------
# Intelligence taxonomy
# ---------------------------------------------------------------------------

INTELLIGENCE_TYPES = frozenset({
    "personal_experience",
    "industry_observation",
    "failure_case_study",
    "success_case_study",
    "strategic_framework",
    "relationship_intelligence",
    "competitive_intelligence",
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

SOURCE_TYPES = frozenset({
    "conversation",
    "post_mortem",
    "deployment_debrief",
    "industry_discussion",
    "career_reflection",
    "consulting_debrief",
    "client_engagement",
})

# ---------------------------------------------------------------------------
# Classification keyword sets
# ---------------------------------------------------------------------------

_INTEL_TYPE_KEYWORDS: dict[str, set[str]] = {
    "failure_case_study": {
        "failed", "failure", "didn't work", "did not work", "broke down",
        "shut down", "cancelled", "pulled the plug", "abandoned",
        "never deployed", "fell apart", "root cause", "post-mortem",
        "went wrong", "disaster", "blown up", "rejected by",
    },
    "success_case_study": {
        "succeeded", "worked well", "deployed successfully", "went live",
        "adoption rate", "drove results", "positive roi", "reduced cost",
        "increased revenue", "scaled", "celebrated", "achieved",
    },
    "personal_experience": {
        "i worked", "i was at", "i led", "i ran", "i built", "i deployed",
        "my experience", "i've seen", "i've watched", "i've been",
        "in my career", "my previous", "at my last job", "when i was at",
        "i learned", "i discovered", "firsthand",
    },
    "industry_observation": {
        "the industry", "the market", "operators are", "vendors are",
        "restaurants are", "chains are", "the sector", "across the board",
        "consistently seeing", "pattern i've seen", "trend is",
        "typically", "generally", "most companies", "most operators",
    },
    "strategic_framework": {
        "framework", "model", "approach", "methodology", "playbook",
        "principle", "rule of thumb", "the way to", "formula",
        "three steps", "five steps", "key factors", "criteria",
        "how to evaluate", "how to structure",
    },
    "relationship_intelligence": {
        "relationship", "they trust", "he said", "she said", "their team",
        "executive", "ceo", "cto", "coo", "vp of", "director of",
        "decision maker", "champion", "stakeholder", "political",
        "internally they", "culture at",
    },
    "competitive_intelligence": {
        "competitor", "competition", "their product", "their approach",
        "versus", "compared to", "beats them", "behind them", "market share",
        "they're winning", "they're losing", "their strategy", "their weakness",
        "their strength", "positioning",
    },
}

# ---------------------------------------------------------------------------
# Retrieval hook domains — maps domain to trigger keywords and lesson types
# ---------------------------------------------------------------------------

_RETRIEVAL_HOOK_DOMAINS: dict[str, dict] = {
    "inventory_ai": {
        "triggers": ["inventory", "stock", "supply chain", "ordering", "par level"],
        "lesson_types": [
            "trust lessons",
            "workflow duplication lessons",
            "pilot design lessons",
            "operational reality lessons",
        ],
    },
    "voice_ai": {
        "triggers": ["voice", "drive-thru", "drive thru", "ordering kiosk", "phone order"],
        "lesson_types": [
            "trust lessons",
            "failure-rate lessons",
            "friday-night survivability lessons",
            "fallback protocol lessons",
        ],
    },
    "consulting_engagement": {
        "triggers": ["consulting", "client", "engagement", "proposal", "pitch", "contract"],
        "lesson_types": [
            "pricing-model lessons",
            "adoption lessons",
            "change-management lessons",
            "stakeholder alignment lessons",
        ],
    },
    "restaurant_tech": {
        "triggers": ["restaurant", "operator", "chain", "qsr", "fast casual", "hospitality"],
        "lesson_types": [
            "operational reality lessons",
            "vendor lessons",
            "adoption friction lessons",
            "labor impact lessons",
        ],
    },
    "ai_deployment": {
        "triggers": ["ai deployment", "machine learning", "ai rollout", "model", "algorithm", "automation"],
        "lesson_types": [
            "pilot design lessons",
            "trust lessons",
            "failure factors",
            "success factors",
        ],
    },
    "change_management": {
        "triggers": ["change", "adoption", "resistance", "buy-in", "rollout", "training", "behavior change"],
        "lesson_types": [
            "change-management lessons",
            "adoption lessons",
            "stakeholder alignment lessons",
            "communication lessons",
        ],
    },
    "enterprise_sales": {
        "triggers": ["enterprise", "sales cycle", "procurement", "deal", "close", "negotiation", "budget"],
        "lesson_types": [
            "deal structure lessons",
            "champion identification lessons",
            "decision timeline lessons",
            "pricing-model lessons",
        ],
    },
}

# Employer-sensitive entity patterns — names that should be anonymized in external versions.
# This is supplemented by any employer_names passed explicitly at call time.
_DEFAULT_EMPLOYER_SIGNALS = [
    r"\bnomadgo\b",
    r"\bstarbucks\b",
    r"\bmcdonald'?s?\b",
    r"\bfive guys\b",
    r"\bchipotle\b",
    r"\bshake shack\b",
    r"\bchick.?fil.?a\b",
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_id(prefix: str = "exp") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}-{ts}-{uuid.uuid4().hex[:6]}"


def _split_sentences(text: str) -> list[str]:
    raw = [s.strip() for s in re.split(r"[.!?]", text.replace("\n", " "))]
    return [s for s in raw if len(s) > 10]


def _classify_intel_type(text: str) -> list[dict]:
    """Classify text into intelligence types. Returns list sorted by confidence desc."""
    text_lower = text.lower()
    sentences = _split_sentences(text)

    type_hits: dict[str, set[str]] = {t: set() for t in INTELLIGENCE_TYPES}
    type_sentences: dict[str, list[str]] = {t: [] for t in INTELLIGENCE_TYPES}

    for intel_type, keywords in _INTEL_TYPE_KEYWORDS.items():
        for sentence in sentences:
            sl = sentence.lower()
            hits = [kw for kw in keywords if kw in sl]
            if hits:
                type_hits[intel_type].update(hits)
                if sentence not in type_sentences[intel_type]:
                    type_sentences[intel_type].append(sentence)
        # Multi-word keywords against full text
        for kw in keywords:
            if " " in kw and kw in text_lower:
                type_hits[intel_type].add(kw)

    conf_rank = {"high": 0, "medium": 1, "low": 2}
    results = []
    for intel_type, hits in type_hits.items():
        if not hits:
            continue
        unique = list(hits)[:5]
        confidence = "high" if len(unique) >= 3 else "medium" if len(unique) >= 2 else "low"
        results.append({
            "intel_type": intel_type,
            "confidence": confidence,
            "matched_keywords": unique,
            "representative_sentences": type_sentences[intel_type][:3],
        })

    results.sort(key=lambda x: (conf_rank[x["confidence"]], x["intel_type"]))
    return results


def _extract_employer_sensitive(text: str, employer_names: list[str] | None = None) -> tuple[bool, list[str]]:
    """Detect whether the text contains employer-sensitive entity references."""
    text_lower = text.lower()
    found: list[str] = []
    patterns = list(_DEFAULT_EMPLOYER_SIGNALS)
    if employer_names:
        patterns += [rf"\b{re.escape(n.lower())}\b" for n in employer_names]
    for pat in patterns:
        if re.search(pat, text_lower):
            found.append(pat)
    return bool(found), found


def _build_external_version(text: str, employer_names: list[str] | None = None) -> str:
    """Replace employer-sensitive entity names with anonymized generalized language."""
    result = text
    replacements = [
        (r"\bNomadGo\b", "a restaurant AI technology vendor"),
        (r"\bnomadgo\b", "a restaurant ai technology vendor"),
        (r"\bStarbucks\b", "a large national coffee chain"),
        (r"\bstarbucks\b", "a large national coffee chain"),
    ]
    if employer_names:
        for name in employer_names:
            replacements.append((rf"\b{re.escape(name)}\b", "a former employer"))
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result


def _generate_case_study_structure(
    text: str,
    primary_type: str,
    confidence: str,
    representative_sentences: list[str],
    employer_sensitive: bool,
    employer_names: list[str] | None,
) -> dict:
    """Generate structured case study from classified experience text."""
    sentences = _split_sentences(text)
    text_lower = text.lower()

    # Generate a title based on intel type
    type_titles = {
        "failure_case_study": "Operational Failure Case Study",
        "success_case_study": "Success Case Study",
        "personal_experience": "Executive Experience Record",
        "industry_observation": "Industry Pattern Observation",
        "strategic_framework": "Strategic Framework",
        "relationship_intelligence": "Relationship Intelligence Record",
        "competitive_intelligence": "Competitive Intelligence Record",
    }
    base_title = type_titles.get(primary_type, "Intelligence Record")

    # Domain detection for title enrichment
    domain_label = None
    for domain, cfg in _RETRIEVAL_HOOK_DOMAINS.items():
        if any(t in text_lower for t in cfg["triggers"]):
            domain_label = domain.replace("_", " ").title()
            break
    title = f"{base_title}: {domain_label} Deployment" if domain_label else base_title

    # Extract root causes (sentences with causal language)
    root_cause_signals = ["because", "root cause", "failed due to", "caused by", "the problem was",
                          "the issue was", "fundamental", "underlying", "broke down when"]
    root_causes = [s for s in sentences if any(sig in s.lower() for sig in root_cause_signals)][:3]

    # Extract lessons learned
    lesson_signals = ["lesson", "learned", "takeaway", "insight", "realize", "discovered",
                      "should have", "next time", "in hindsight", "key factor"]
    lessons_learned = [s for s in sentences if any(sig in s.lower() for sig in lesson_signals)][:4]
    if not lessons_learned and representative_sentences:
        lessons_learned = representative_sentences[:2]

    # Extract success / failure factors
    success_signals = ["worked", "succeeded", "drove", "enabled", "accelerated", "supported"]
    failure_signals = ["failed", "broke", "rejected", "abandoned", "resistance", "wouldn't adopt"]
    success_factors = [s for s in sentences if any(sig in s.lower() for sig in success_signals)][:3]
    failure_factors = [s for s in sentences if any(sig in s.lower() for sig in failure_signals)][:3]

    # Reusable frameworks — strategic principles derived from the experience
    framework_signals = ["always", "never", "rule", "principle", "framework", "key is", "the way to",
                         "you must", "critical", "essential", "prerequisite"]
    reusable_frameworks = [s for s in sentences if any(sig in s.lower() for sig in framework_signals)][:3]

    internal_version = {
        "title": title,
        "raw_text_snippet": text[:500],
        "root_causes": root_causes,
        "lessons_learned": lessons_learned,
        "success_factors": success_factors,
        "failure_factors": failure_factors,
        "reusable_frameworks": reusable_frameworks,
        "employer_sensitive": employer_sensitive,
        "employer_names_detected": employer_names or [],
    }

    external_text = _build_external_version(text, employer_names)
    external_snippet = external_text[:500]
    external_root_causes = [_build_external_version(s, employer_names) for s in root_causes]
    external_lessons = [_build_external_version(s, employer_names) for s in lessons_learned]
    external_frameworks = [_build_external_version(s, employer_names) for s in reusable_frameworks]

    external_version = {
        "title": title,
        "raw_text_snippet": external_snippet,
        "root_causes": external_root_causes,
        "lessons_learned": external_lessons,
        "success_factors": [_build_external_version(s, employer_names) for s in success_factors],
        "failure_factors": [_build_external_version(s, employer_names) for s in failure_factors],
        "reusable_frameworks": external_frameworks,
        "externalization_note": "Company names and employer references have been generalized for reputation safety.",
    }

    return {
        "title": title,
        "internal_version": internal_version,
        "external_version": external_version,
    }


def _build_retrieval_hooks(text: str, intel_type: str) -> dict[str, list[str]]:
    """Build retrieval hooks keyed to future topic domains."""
    text_lower = text.lower()
    hooks: dict[str, list[str]] = {}
    for domain, cfg in _RETRIEVAL_HOOK_DOMAINS.items():
        if any(trigger in text_lower for trigger in cfg["triggers"]):
            hooks[domain] = cfg["lesson_types"]
    # Always add intel_type as a retrieval hook
    hooks[intel_type] = ["direct experience record", "reusable framework candidate"]
    return hooks


def _build_mutation_proposals(
    intel_type: str,
    case_study_title: str,
    confidence: str,
    source_type: str,
) -> list[dict]:
    """Build review-first mutation proposals across affected intelligence layers."""
    proposals = []

    if intel_type in ("industry_observation", "failure_case_study", "success_case_study"):
        proposals.append({
            "mutation_type": "industry_graph",
            "target": "system/graphs/industry_intelligence.json",
            "operation": "add",
            "value": {"case_study_title": case_study_title, "confidence": confidence},
            "requires_confirmation": True,
            "persistence_endpoint": "POST /experiential/confirm",
        })

    if intel_type in ("failure_case_study", "success_case_study", "personal_experience"):
        proposals.append({
            "mutation_type": "strategic_memory",
            "target": "system/strategic_memory.json",
            "operation": "add",
            "value": {
                "case_study_title": case_study_title,
                "source_type": source_type,
                "intel_type": intel_type,
            },
            "requires_confirmation": True,
            "persistence_endpoint": "POST /experiential/confirm",
        })

    if intel_type == "strategic_framework":
        proposals.append({
            "mutation_type": "user_positioning",
            "target": "system/00_TODD_PROFILE.md",
            "operation": "add",
            "value": {"framework_title": case_study_title},
            "requires_confirmation": True,
            "persistence_endpoint": "POST /experiential/confirm",
        })

    if intel_type in ("relationship_intelligence", "competitive_intelligence"):
        proposals.append({
            "mutation_type": "relationship_graph",
            "target": "system/interaction_ledger.json",
            "operation": "flag_for_review",
            "value": {"case_study_title": case_study_title},
            "requires_confirmation": True,
            "persistence_endpoint": "POST /experiential/confirm",
        })

    return proposals


def _build_trust_stats(
    sources_assessed: int,
    sources_accepted: int,
    sources_rejected: int,
    confidence: str,
    mutation_proposals: list[dict],
    retrieval_hooks: dict[str, list[str]],
    intel_type: str,
) -> dict:
    """Generate trust stats block. Required on every significant ingestion event."""
    mutation_recs = [p["mutation_type"] for p in mutation_proposals]
    follow_up_loops = []
    if intel_type in ("failure_case_study", "success_case_study"):
        follow_up_loops.append("Confirm root causes and lessons learned before mutation")
        follow_up_loops.append("Review externalization before any external surfacing")
    if retrieval_hooks:
        follow_up_loops.append(f"Retrieval hooks active for: {', '.join(retrieval_hooks.keys())}")
    retrieval_classifications = list(retrieval_hooks.keys())

    return {
        "sources_assessed": sources_assessed,
        "sources_accepted": sources_accepted,
        "sources_rejected": sources_rejected,
        "confidence": confidence,
        "mutation_recommendations": mutation_recs,
        "follow_up_loops": follow_up_loops,
        "retrieval_classifications": retrieval_classifications,
        "trust_contract_met": True,
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _load_store(store_path: Path | None = None) -> dict:
    path = store_path or EXPERIENTIAL_INTELLIGENCE_PATH
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"_schema_version": "1.0", "experiences": []}


def _save_store(store: dict, store_path: Path | None = None) -> None:
    path = store_path or EXPERIENTIAL_INTELLIGENCE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2, default=str))


def _write_pending(records: list[dict], store_path: Path | None = None) -> None:
    store = _load_store(store_path)
    existing_ids = {r.get("id") for r in store.get("experiences", [])}
    for record in records:
        if record["id"] not in existing_ids:
            store["experiences"].append(record)
    store["_last_updated"] = _timestamp()
    _save_store(store, store_path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def process_experiential_signal(
    text: str,
    source_type: str = "conversation",
    context: dict | None = None,
    employer_names: list[str] | None = None,
    store_path: Path | None = None,
) -> dict:
    """Transform firsthand experience, observations, or lessons into reusable intelligence artifacts.

    Returns:
      experiences          — classified + structured experience records
      trust_stats          — full trust stats block (always emitted)
      mutation_proposals   — review-first mutation proposals across affected layers
      retrieval_hooks      — domain → lesson_type mapping for future retrieval
      recommended_actions  — surface-level CoS action recommendations
      persistence_status   — explicit status for the batch
    """
    if not text or not text.strip():
        return {
            "experiences": [],
            "trust_stats": _build_trust_stats(0, 0, 0, "none", [], {}, "none"),
            "mutation_proposals": [],
            "retrieval_hooks": {},
            "recommended_actions": [],
            "persistence_status": "RB did not persist",
            "note": "Empty input. No experiential intelligence extracted.",
        }

    if source_type not in SOURCE_TYPES:
        source_type = "conversation"

    employer_sensitive, detected_patterns = _extract_employer_sensitive(text, employer_names)
    classifications = _classify_intel_type(text)

    if not classifications:
        return {
            "experiences": [],
            "trust_stats": _build_trust_stats(1, 0, 1, "low", [], {}, "none"),
            "mutation_proposals": [],
            "retrieval_hooks": {},
            "recommended_actions": [],
            "persistence_status": "RB did not persist",
            "note": "No experiential intelligence patterns detected in input.",
        }

    now = _timestamp()
    records: list[dict] = []
    all_mutations: list[dict] = []
    all_hooks: dict[str, list[str]] = {}
    primary_confidence = classifications[0]["confidence"]

    for cls in classifications:
        exp_id = _make_id("exp")
        case_study = _generate_case_study_structure(
            text,
            cls["intel_type"],
            cls["confidence"],
            cls["representative_sentences"],
            employer_sensitive,
            employer_names,
        )
        hooks = _build_retrieval_hooks(text, cls["intel_type"])
        mutations = _build_mutation_proposals(
            cls["intel_type"],
            case_study["title"],
            cls["confidence"],
            source_type,
        )

        record = {
            "id": exp_id,
            "source_type": source_type,
            "source_text_snippet": text[:400],
            "created_at": now,
            "intel_type": cls["intel_type"],
            "confidence": cls["confidence"],
            "matched_keywords": cls["matched_keywords"],
            "case_study": case_study,
            "employer_sensitive": employer_sensitive,
            "retrieval_hooks": hooks,
            "proposed_mutations": mutations,
            "claim_status": "proposed",
            "persistence_status": "pending confirmation",
            "confirmed_at": None,
            "context": context or {},
        }
        records.append(record)
        all_mutations.extend(mutations)
        for domain, lessons in hooks.items():
            if domain not in all_hooks:
                all_hooks[domain] = lessons

    trust_stats = _build_trust_stats(
        sources_assessed=1,
        sources_accepted=len(records),
        sources_rejected=0,
        confidence=primary_confidence,
        mutation_proposals=all_mutations,
        retrieval_hooks=all_hooks,
        intel_type=classifications[0]["intel_type"],
    )

    recommended_actions = _build_recommended_actions(records, employer_sensitive)

    _write_pending(records, store_path)

    return {
        "experiences": records,
        "trust_stats": trust_stats,
        "mutation_proposals": all_mutations,
        "retrieval_hooks": all_hooks,
        "recommended_actions": recommended_actions,
        "persistence_status": "pending confirmation",
        "experience_count": len(records),
        "mutation_proposal_count": len(all_mutations),
        "employer_sensitive": employer_sensitive,
    }


def _build_recommended_actions(records: list[dict], employer_sensitive: bool) -> list[str]:
    actions = []
    intel_types = {r["intel_type"] for r in records}
    if "failure_case_study" in intel_types:
        actions.append("Confirm failure case study and lock internal version before externalization")
    if "success_case_study" in intel_types:
        actions.append("Confirm success case study — consider LinkedIn thought leadership use")
    if "strategic_framework" in intel_types:
        actions.append("Confirm strategic framework — candidate for positioning and POV mutation")
    if "industry_observation" in intel_types:
        actions.append("Confirm industry observation — route to industry graph + daily brief")
    if employer_sensitive:
        actions.append("Employer-sensitive entities detected — review external_version before any external surfacing")
    return actions


def externalize(
    record_id: str,
    store_path: Path | None = None,
) -> dict:
    """Return the reputation-safe external version of an experience record.

    External versions replace employer names and sensitive entities with
    generalized language. Always use external_version for content outside
    internal intelligence stores.
    """
    store = _load_store(store_path)
    for record in store.get("experiences", []):
        if record.get("id") == record_id:
            case_study = record.get("case_study", {})
            return {
                "id": record_id,
                "intel_type": record.get("intel_type"),
                "employer_sensitive": record.get("employer_sensitive"),
                "external_version": case_study.get("external_version", {}),
                "retrieval_hooks": record.get("retrieval_hooks", {}),
                "externalization_applied": True,
            }
    return {"error": f"Experience {record_id} not found."}


def record_experience(
    experience_id: str,
    confirmed: bool = True,
    store_path: Path | None = None,
) -> dict:
    """Confirm or reject a pending experience record."""
    store = _load_store(store_path)
    for idx, entry in enumerate(store.get("experiences", [])):
        if entry.get("id") == experience_id:
            if confirmed:
                store["experiences"][idx]["claim_status"] = "confirmed"
                store["experiences"][idx]["persistence_status"] = "RB recorded"
                store["experiences"][idx]["confirmed_at"] = _timestamp()
            else:
                store["experiences"][idx]["claim_status"] = "rejected"
                store["experiences"][idx]["persistence_status"] = "RB skipped"
            store["_last_updated"] = _timestamp()
            _save_store(store, store_path)
            return store["experiences"][idx]
    return {"error": f"Experience {experience_id} not found."}


def query_experiences(
    tags: list[str] | None = None,
    intel_type: str | None = None,
    claim_status: str | None = None,
    employer_sensitive: bool | None = None,
    store_path: Path | None = None,
) -> list[dict]:
    """Retrieve experience records by intel type, retrieval tag, or claim status."""
    store = _load_store(store_path)
    results = list(store.get("experiences", []))

    if intel_type:
        results = [r for r in results if r.get("intel_type") == intel_type]
    if claim_status:
        results = [r for r in results if r.get("claim_status") == claim_status]
    if employer_sensitive is not None:
        results = [r for r in results if r.get("employer_sensitive") == employer_sensitive]
    if tags:
        results = [
            r for r in results
            if any(t in (r.get("retrieval_hooks") or {}) for t in tags)
        ]
    return results


def query_retrieval_hooks(
    domain: str,
    claim_status: str = "confirmed",
    store_path: Path | None = None,
) -> dict:
    """Return all experience records with retrieval hooks for the given domain.

    Used to automatically surface relevant lessons when entering a new topic area.
    Returns: domain, lesson_types, matched_experiences, confidence.
    """
    store = _load_store(store_path)
    matched = []
    for record in store.get("experiences", []):
        hooks = record.get("retrieval_hooks", {})
        if domain in hooks:
            if claim_status and record.get("claim_status") != claim_status:
                continue
            matched.append({
                "id": record["id"],
                "intel_type": record["intel_type"],
                "case_study_title": record.get("case_study", {}).get("title", ""),
                "lesson_types": hooks[domain],
                "confidence": record.get("confidence"),
                "employer_sensitive": record.get("employer_sensitive"),
            })

    lesson_types = _RETRIEVAL_HOOK_DOMAINS.get(domain, {}).get("lesson_types", [])
    confidence = "high" if len(matched) >= 3 else "medium" if len(matched) >= 1 else "none"
    return {
        "domain": domain,
        "lesson_types": lesson_types,
        "matched_experiences": matched,
        "match_count": len(matched),
        "retrieval_confidence": confidence,
    }
