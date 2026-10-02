#!/usr/bin/env python3
"""opportunity_sensing.py — RB 9.12 opportunity sensing layer.

Detects pain that the selected user or their product/service can credibly solve.
Sources: market news, job postings, LinkedIn posts, earnings signals, press releases,
company blogs, and manual notes.

Design principles:
- Profile-driven: all fit scoring reads a user profile, not hardcoded assumptions.
- No person-specific schema fields (use why_this_matters_to_user, not _to_todd).
- Job postings are first-class intelligence — they reveal pain, priority, budget,
  team buildout, and leadership gaps beyond mere employment listings.
- Multi-profile safe: same signal can produce different recommendations for
  different profiles.
- Relationship path discipline: never invent a path; unknown → no_known_path.
- Source freshness discipline: stale sources downgrade posture.
- Proof stats on every signal: sourcing is never implicit.

Opportunity signal shape:
    {
      "opportunity_signal_id": "...",
      "detected_at": "ISO datetime",
      "source": {
        "source_type": "market_news|job_posting|...",
        "source_name": "...",
        "url": "...",
        "freshness": "fresh|stale|manual_context|unknown"
      },
      "pain": {
        "summary": "...",
        "pain_type": "growth|cost_pressure|...",
        "evidence": [...]
      },
      "subject": {
        "company": "...",
        "people": [],
        "industry": "...",
        "customer_or_employer": "customer|employer|partner|vendor|unknown"
      },
      "fit": {
        "user_capability_match": "high|medium|low|none",
        "product_service_match": "high|medium|low|none",
        "matched_profile_capabilities": [],
        "why_this_matters_to_user": "..."
      },
      "opportunity_type": "sales|job|consulting|partnership|intro|content|research|no_action",
      "relationship_path": {
        "status": "warm_path|possible_path|no_known_path|unknown",
        "contacts": [],
        "broker_candidates": []
      },
      "recommended_posture": "act_today|nurture|monitor|research_first|ignore",
      "confidence": "high|medium|low",
      "proof_stats": {
        "sources_reviewed": 0,
        "matching_profile_facts": 0,
        "matching_relationship_paths": 0,
        "deduped_signals": 0
      }
    }

CLI:
    python3 opportunity_sensing.py --smoke       # regression tests
    python3 opportunity_sensing.py --json        # run against live signals
    python3 opportunity_sensing.py --profile todd_vahlsing --json
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

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

try:
    import rb_core as core  # noqa: E402
    _SYSTEM_DIR = core.SYSTEM_DIR
    _INBOX_DIR = core.INBOX_DIR
except Exception:
    _SYSTEM_DIR = _HERE.parent
    _INBOX_DIR = _SYSTEM_DIR / "inbox"

try:
    import user_profile as _up  # noqa: E402
    _HAS_PROFILE_LOADER = True
except ImportError:
    _HAS_PROFILE_LOADER = False


# ---------------------------------------------------------------------------
# Pain type taxonomy
# ---------------------------------------------------------------------------

PAIN_TYPES = frozenset({
    "growth",
    "cost_pressure",
    "implementation_failure",
    "leadership_gap",
    "customer_churn",
    "operational_complexity",
    "digital_transformation",
    "compliance",
    "market_expansion",
    "turnaround",
    "hiring_need",
    "vendor_gap",
})

OPPORTUNITY_TYPES = frozenset({
    "sales",
    "job",
    "consulting",
    "partnership",
    "intro",
    "content",
    "research",
    "no_action",
})

POSTURES = frozenset({
    "act_today",
    "nurture",
    "monitor",
    "research_first",
    "ignore",
})

FRESHNESS_VALUES = frozenset({"fresh", "stale", "manual_context", "unknown"})
MATCH_LEVELS = frozenset({"high", "medium", "low", "none"})
CONFIDENCE_LEVELS = frozenset({"high", "medium", "low"})
PATH_STATUSES = frozenset({"warm_path", "possible_path", "no_known_path", "unknown"})


# ---------------------------------------------------------------------------
# Pain extraction — keyword patterns
# ---------------------------------------------------------------------------

_PAIN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("implementation_failure", re.compile(
        r"\b(rollback|failed|failure|abandoned|scrap|discontinu|pulled|unreliable|"
        r"accuracy.issue|implementation.gap|pilot.cancel|rollout.fail|"
        r"franchisee.trust|operator.trust)\b", re.I,
    )),
    ("turnaround", re.compile(
        r"\b(clos(?:e|es|ed|ing|ure)|shut|bankrupt|restructur|distress|"
        r"margin.pressure|underperform|store.closure|unit.closure)\b", re.I,
    )),
    ("cost_pressure", re.compile(
        r"\b(cost.cut|cost.pressure|margin.compress|labor.cost|food.cost|"
        r"wage.pressure|tariff|interest.rate|same.store.sales.declin)\b", re.I,
    )),
    ("digital_transformation", re.compile(
        r"\b(digital.transform|moderniz|pos.replac|technology.overhaul|"
        r"fragmented.system|legacy.system|cloud.migrat|platform.consolidat)\b", re.I,
    )),
    ("operational_complexity", re.compile(
        r"\b(operational.complex|fragmented|siloed|manual.process|"
        r"workflow.burden|integration.strain|above.store|visibility.gap)\b", re.I,
    )),
    ("growth", re.compile(
        r"\b(expansion|new.market|unit.growth|new.location|development.agreement|"
        r"international.expansion|franchise.growth|customer.acquisition)\b", re.I,
    )),
    ("market_expansion", re.compile(
        r"\b(enter.new.market|international.launch|new.segment|enterprise.entry|"
        r"national.rollout|chain.expansion|new.vertical)\b", re.I,
    )),
    ("vendor_gap", re.compile(
        r"\b(seeking.vendor|rfp|vendor.search|no.current.solution|"
        r"gap.in.technology|unmet.need|looking.for.partner|"
        r"no.one.does|can.t.find)\b", re.I,
    )),
    ("hiring_need", re.compile(
        r"\b(hiring|open.position|job.posting|seeking.candidate|"
        r"now.hiring|apply.today|vp.of|director.of|head.of|"
        r"we.are.looking.for|join.our.team)\b", re.I,
    )),
    ("leadership_gap", re.compile(
        r"\b(interim|stepping.down|departed|resign|left.the.company|"
        r"leadership.transition|ceo.change|cfo.change|coo.change|cto.change|"
        r"new.ceo|new.cro|new.cso|new.vp)\b", re.I,
    )),
    ("customer_churn", re.compile(
        r"\b(churn|lost.customer|customer.attrition|switching.vendor|"
        r"vendor.replace|dropped|contract.not.renewed|terminated.contract)\b", re.I,
    )),
    ("compliance", re.compile(
        r"\b(compliance|regulation|regulatory|audit|penalty|fine|mandate|"
        r"food.safety|pci|data.breach)\b", re.I,
    )),
]


def extract_pain_type(text: str) -> str:
    """Return the dominant pain type from signal text. Returns 'vendor_gap' as fallback."""
    for pain_type, pattern in _PAIN_PATTERNS:
        if pattern.search(text):
            return pain_type
    return "vendor_gap"


def extract_pain_evidence(text: str, *, max_items: int = 5) -> list[str]:
    """Extract short evidence snippets that support the pain claim."""
    evidence = []
    for pain_type, pattern in _PAIN_PATTERNS:
        for m in pattern.finditer(text):
            start = max(0, m.start() - 40)
            end = min(len(text), m.end() + 60)
            snippet = text[start:end].replace("\n", " ").strip()
            tag = f"[{pain_type}] …{snippet}…"
            if tag not in evidence:
                evidence.append(tag)
            if len(evidence) >= max_items:
                return evidence
    return evidence


# ---------------------------------------------------------------------------
# Job posting intelligence — extract intent beyond "job listing"
# ---------------------------------------------------------------------------

_JOB_TITLE_PATTERNS = {
    "digital_transformation": re.compile(
        r"\b(digital.transform|moderniz|technology.leader|vp.technology|"
        r"cto|chief.technology|head.of.it|director.of.technology)\b", re.I,
    ),
    "growth": re.compile(
        r"\b(enterprise.account|vp.sales|cro|chief.revenue|head.of.sales|"
        r"national.accounts?|enterprise.gtm|business.development)\b", re.I,
    ),
    "operational_complexity": re.compile(
        r"\b(vp.operations|director.of.operations|coo|head.of.ops|"
        r"operations.lead|regional.director|franchise.support)\b", re.I,
    ),
    "leadership_gap": re.compile(
        r"\b(interim|fractional|chief.of.staff|advisor|consultant|"
        r"principal|managing.director)\b", re.I,
    ),
    "vendor_gap": re.compile(
        r"\b(pos|payments|loyalty|inventory|back.office|above.store|"
        r"analytics|data|ai|machine.learning|drive.thru)\b", re.I,
    ),
}

_JOB_SIGNALS_BUDGET = re.compile(
    r"\b(strategic.priority|budget|investment|program|initiative|"
    r"transformation.program|modernization.program)\b", re.I,
)

_JOB_SIGNALS_PAIN = re.compile(
    r"\b(fragmented|legacy|siloed|manual|inconsistent|unreliable|"
    r"disparate|lack.of.visibility|no.single.source)\b", re.I,
)


def analyze_job_posting(text: str) -> dict[str, Any]:
    """Extract intelligence from a job posting beyond the listing itself.

    Returns a dict with:
      - inferred_pain_types: list of pain types the posting implies
      - inferred_company_priorities: list of priorities
      - intelligence_signals: plain-language interpretation
      - job_signal_quality: "rich" | "moderate" | "thin"
    """
    pain_types = []
    priorities = []
    intelligence = []

    for pain_type, pattern in _JOB_TITLE_PATTERNS.items():
        if pattern.search(text):
            if pain_type not in pain_types:
                pain_types.append(pain_type)

    for pain_type, pattern in _PAIN_PATTERNS:
        if pattern.search(text) and pain_type not in pain_types:
            pain_types.append(pain_type)

    if _JOB_SIGNALS_BUDGET.search(text):
        priorities.append("budget_or_transformation_priority_signaled")
        intelligence.append("Job description language signals active budget or strategic program.")

    if _JOB_SIGNALS_PAIN.search(text):
        intelligence.append("Job description language reveals underlying tech/process pain (fragmented, legacy, siloed, manual, etc.).")

    if "hiring_need" in pain_types:
        intelligence.append("Active hiring signals headcount approval and executive attention.")

    if "digital_transformation" in pain_types:
        intelligence.append("Digital transformation hire suggests POS/platform/above-store technology investment cycle.")

    if "growth" in pain_types:
        intelligence.append("Enterprise/GTM hire signals vendor pursuing customer growth or operator expanding footprint.")

    if "leadership_gap" in pain_types:
        intelligence.append("Interim/fractional/advisor title may indicate leadership gap — consulting opportunity.")

    quality = "rich" if len(intelligence) >= 3 else "moderate" if len(intelligence) >= 1 else "thin"

    return {
        "inferred_pain_types": pain_types or ["hiring_need"],
        "inferred_company_priorities": priorities,
        "intelligence_signals": intelligence,
        "job_signal_quality": quality,
    }


# ---------------------------------------------------------------------------
# Profile fit scoring
# ---------------------------------------------------------------------------

def _text_overlap(profile_list: list[str], signal_text: str) -> list[str]:
    """Return profile items whose keywords appear in signal_text."""
    matches = []
    for item in profile_list:
        # Convert snake_case to keywords and check text
        keywords = item.lower().replace("_", " ").split()
        core_kw = [kw for kw in keywords if len(kw) > 3]
        if any(kw in signal_text.lower() for kw in core_kw):
            matches.append(item)
    return matches


def score_fit(
    signal_text: str,
    pain_types: list[str],
    *,
    profile: dict[str, Any],
    source_type: str = "market_news",
) -> dict[str, Any]:
    """Score profile fit against a signal.

    Args:
        signal_text: Combined title + summary text of the signal.
        pain_types: Pain type(s) extracted from the signal.
        profile: Canonical profile dict from user_profile.load().
        source_type: Source type (affects scoring when it's a job_posting).

    Returns:
        {
          "user_capability_match": "high|medium|low|none",
          "product_service_match": "high|medium|low|none",
          "matched_profile_capabilities": [...],
          "why_this_matters_to_user": "...",
          "matching_profile_facts": int,
        }
    """
    capabilities = profile.get("capabilities") or []
    ps_capabilities = profile.get("product_service_capabilities") or []
    credible_pain_types = set(profile.get("credible_pain_types") or [])
    no_go_categories = set(profile.get("no_go_categories") or [])
    target_industries = profile.get("target_industries") or []
    adjacent_industries = profile.get("adjacent_industries") or []

    # No-go check
    for ng in no_go_categories:
        ng_keywords = ng.lower().replace("_", " ").split()
        if any(kw in signal_text.lower() for kw in ng_keywords if len(kw) > 3):
            return {
                "user_capability_match": "none",
                "product_service_match": "none",
                "matched_profile_capabilities": [],
                "why_this_matters_to_user": "Signal falls in a no-go category for this profile.",
                "matching_profile_facts": 0,
            }

    # Pain type fit
    pain_overlap = credible_pain_types & set(pain_types)

    # Capability keyword matches
    cap_matches = _text_overlap(capabilities, signal_text)
    ps_matches = _text_overlap(ps_capabilities, signal_text)

    # Industry fit
    industry_matches = (
        _text_overlap(target_industries, signal_text) +
        _text_overlap(adjacent_industries, signal_text)
    )

    matching_facts = len(cap_matches) + len(ps_matches) + len(pain_overlap) + len(industry_matches)

    # Determine match levels
    if pain_overlap and (cap_matches or ps_matches) and industry_matches:
        user_match = "high"
    elif (pain_overlap or cap_matches) and (industry_matches or ps_matches):
        user_match = "medium"
    elif pain_overlap or cap_matches or industry_matches:
        user_match = "low"
    else:
        user_match = "none"

    if ps_matches and pain_overlap:
        ps_match = "high"
    elif ps_matches or (pain_overlap and industry_matches):
        ps_match = "medium"
    elif ps_matches:
        ps_match = "low"
    else:
        ps_match = "none"

    # Build explanation
    why_parts = []
    if cap_matches:
        why_parts.append(f"User capabilities match: {', '.join(cap_matches[:3])}")
    if ps_matches:
        why_parts.append(f"Product/service match: {', '.join(ps_matches[:3])}")
    if pain_overlap:
        why_parts.append(f"Credible pain overlap: {', '.join(pain_overlap)}")
    if industry_matches:
        why_parts.append(f"Industry fit: {', '.join(industry_matches[:2])}")
    if not why_parts:
        why_parts.append("No clear profile match detected for this signal.")

    return {
        "user_capability_match": user_match,
        "product_service_match": ps_match,
        "matched_profile_capabilities": list(dict.fromkeys(cap_matches + ps_matches)),
        "why_this_matters_to_user": " | ".join(why_parts),
        "matching_profile_facts": matching_facts,
    }


# ---------------------------------------------------------------------------
# Opportunity type classification
# ---------------------------------------------------------------------------

def classify_opportunity_type(
    *,
    pain_types: list[str],
    source_type: str,
    fit: dict[str, Any],
    profile: dict[str, Any],
    signal_text: str = "",
) -> str:
    """Return the most appropriate opportunity type for this signal + profile combination.

    Rules:
    - If fit is "none", return "no_action".
    - Job postings can be "job" or "sales" depending on profile and signal.
    - Market pain → "sales" or "consulting" depending on profile's preferred types and career context.
    - Multi-type possible: return the highest-priority one.
    """
    user_match = fit.get("user_capability_match", "none")
    ps_match = fit.get("product_service_match", "none")
    preferred = set(profile.get("preferred_opportunity_types") or [])
    career_context = profile.get("career_context") or ""

    if user_match == "none" and ps_match == "none":
        return "no_action"

    # Job posting logic
    if source_type == "job_posting":
        # Could be employment opportunity or sales signal into the company
        job_analysis = analyze_job_posting(signal_text)
        # If the user is in job search mode or has strong employment-fit
        job_search_active = profile.get("career_context") in (
            "job_search", "employed", "founder_advisor",
        )
        if "job" in preferred and user_match in ("high", "medium") and job_search_active:
            # Also check if their product could solve the pain the job reveals
            if ps_match in ("high", "medium") and "sales" in preferred:
                # Both are possible — prefer the stronger match
                if ps_match == "high":
                    return "sales"
                return "job"
            return "job"
        if ps_match in ("high", "medium") and "sales" in preferred:
            return "sales"
        if user_match in ("high", "medium"):
            return "consulting"
        return "no_action"

    # Non-job-posting signals
    credible_pain_types = set(profile.get("credible_pain_types") or [])
    pain_overlap = credible_pain_types & set(pain_types)

    if ps_match in ("high", "medium") and "sales" in preferred:
        return "sales"
    if user_match in ("high", "medium"):
        # Capability-keyword matches alone are insufficient when no pain type
        # aligns with what the profile can credibly address and no product/service
        # match exists — treat as no_action to prevent false-positive consulting
        # classifications driven by incidental keyword overlap (e.g. "chain" in
        # "supply_chain_logistics" matching "QSR chain").
        if not pain_overlap and ps_match == "none":
            return "no_action"
        if "consulting" in preferred and career_context in (
            "consulting", "founder", "founder_advisor", "advisor",
        ):
            return "consulting"
        if "job" in preferred:
            return "job"
        return "research"
    if user_match == "low" or ps_match == "low":
        if "content" in preferred and "growth" in pain_types:
            return "content"
        return "research"
    return "no_action"


# ---------------------------------------------------------------------------
# Posture assignment
# ---------------------------------------------------------------------------

def assign_posture(
    *,
    opportunity_type: str,
    fit: dict[str, Any],
    freshness: str,
    relationship_path_status: str,
    confidence: str,
) -> str:
    """Assign a recommended action posture.

    Rules:
    - no_action → ignore
    - stale freshness → research_first or monitor (never act_today)
    - warm_path + high fit → act_today
    - high fit but no_known_path → research_first
    - medium fit → nurture or monitor
    - low fit → monitor or ignore
    """
    if opportunity_type == "no_action":
        return "ignore"

    user_match = fit.get("user_capability_match", "none")
    ps_match = fit.get("product_service_match", "none")
    best_match = "high" if "high" in (user_match, ps_match) else "medium" if "medium" in (user_match, ps_match) else "low"

    if freshness == "stale":
        return "research_first" if best_match in ("high", "medium") else "monitor"

    if best_match == "none":
        return "ignore"

    if best_match == "high":
        if relationship_path_status == "warm_path" and confidence == "high":
            return "act_today"
        if relationship_path_status in ("warm_path", "possible_path"):
            return "nurture"
        return "research_first"

    if best_match == "medium":
        if relationship_path_status == "warm_path":
            return "nurture"
        return "monitor"

    # low
    return "monitor"


# ---------------------------------------------------------------------------
# Relationship path (read from baseline — no invention)
# ---------------------------------------------------------------------------

def _load_baseline_for_path() -> list[dict[str, Any]]:
    try:
        return core.load_baseline()
    except Exception:
        return []


def assess_relationship_path(
    company: str,
    people: list[str],
    *,
    baseline: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assess relationship path WITHOUT inventing contacts.

    Returns:
        {
          "status": "warm_path|possible_path|no_known_path|unknown",
          "contacts": [...],
          "broker_candidates": [],
        }
    """
    if not company and not people:
        return {"status": "unknown", "contacts": [], "broker_candidates": []}

    if baseline is None:
        baseline = _load_baseline_for_path()

    if not baseline:
        return {"status": "unknown", "contacts": [], "broker_candidates": []}

    company_lower = company.lower() if company else ""
    people_lower = [p.lower() for p in (people or [])]

    matched_contacts = []
    broker_candidates = []

    for entry in baseline:
        entry_company = str(entry.get("current_company") or "").lower()
        entry_name = str(entry.get("name") or "").lower()
        entry_id = entry.get("id") or entry_name
        signal_class = entry.get("signal_class") or ""
        rc_tier = entry.get("rc_tier") or ""

        name_match = any(p in entry_name or entry_name in p for p in people_lower if p)
        company_match = (company_lower and company_lower in entry_company) or (
            company_lower and entry_company in company_lower and len(company_lower) > 4
        )

        if name_match or company_match:
            rec = {
                "contact_id": entry_id,
                "name": entry.get("name"),
                "company": entry.get("current_company"),
                "signal_class": signal_class,
                "rc_tier": rc_tier,
            }
            if signal_class == "RC":
                matched_contacts.append(rec)
            elif signal_class in ("LKI", "LMI"):
                broker_candidates.append(rec)

    if matched_contacts:
        # Check warmth: RC inner = warm, others = possible
        warm = [c for c in matched_contacts if c.get("rc_tier") == "inner"]
        status = "warm_path" if warm else "possible_path"
        return {"status": status, "contacts": matched_contacts, "broker_candidates": broker_candidates}

    if broker_candidates:
        return {"status": "possible_path", "contacts": [], "broker_candidates": broker_candidates}

    return {"status": "no_known_path", "contacts": [], "broker_candidates": []}


# ---------------------------------------------------------------------------
# Freshness assessment
# ---------------------------------------------------------------------------

def assess_freshness(published_at: str | None, fetched_at: str | None,
                     source_type: str = "market_news") -> str:
    """Return freshness label for a signal."""
    if source_type == "manual_note":
        return "manual_context"

    # Use published_at as primary freshness signal
    anchor_str = fetched_at or published_at
    if not anchor_str:
        return "unknown"

    try:
        anchor = datetime.fromisoformat(anchor_str.replace("Z", "+00:00"))
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - anchor.astimezone(timezone.utc)).total_seconds() / 3600
        # Market news: stale after 48h; job postings: 72h
        threshold = 72 if source_type == "job_posting" else 48
        return "stale" if age_hours > threshold else "fresh"
    except (ValueError, TypeError):
        return "unknown"


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

def compute_confidence(
    *,
    fit: dict[str, Any],
    freshness: str,
    relationship_path: dict[str, Any],
    pain_evidence: list[str],
    source_quality: str = "medium",
) -> str:
    """Compute overall signal confidence."""
    score = 0

    user_match = fit.get("user_capability_match", "none")
    ps_match = fit.get("product_service_match", "none")

    if user_match == "high" or ps_match == "high":
        score += 3
    elif user_match == "medium" or ps_match == "medium":
        score += 2
    elif user_match == "low" or ps_match == "low":
        score += 1

    if freshness == "fresh":
        score += 2
    elif freshness == "manual_context":
        score += 1
    # stale adds 0

    path_status = relationship_path.get("status", "unknown")
    if path_status == "warm_path":
        score += 3
    elif path_status == "possible_path":
        score += 2
    elif path_status == "no_known_path":
        score += 1

    if len(pain_evidence) >= 3:
        score += 2
    elif pain_evidence:
        score += 1

    if source_quality == "strong":
        score += 1

    if score >= 8:
        return "high"
    if score >= 5:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Signal ID generation
# ---------------------------------------------------------------------------

def _signal_id(source_type: str, company: str, pain_type: str, url: str = "") -> str:
    text = f"{source_type}:{company}:{pain_type}:{url}"
    return "opp_" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:14]


# ---------------------------------------------------------------------------
# Core: score_signal — the main entry point for a single raw signal
# ---------------------------------------------------------------------------

def score_signal(
    raw: dict[str, Any],
    *,
    profile: dict[str, Any],
    baseline: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score a single raw market/job/news signal against a user profile.

    Args:
        raw: A market signal row (from market_source_feeds or market_signals)
             or a job posting dict. Minimum expected keys: title, url, source_type,
             company, pain_point_or_priority (or description).
        profile: Canonical profile dict from user_profile.load().
        baseline: Optional pre-loaded baseline for relationship path lookup.

    Returns:
        Full opportunity signal dict (see module docstring for shape).
    """
    title = str(raw.get("title") or "")
    summary = str(
        raw.get("pain_point_or_priority") or
        raw.get("description") or
        raw.get("summary") or
        raw.get("text") or ""
    )
    signal_text = (title + " " + summary).strip()
    url = str(raw.get("url") or "")
    source_type = str(raw.get("source_type") or "market_news")
    source_name = str(raw.get("source_name") or source_type)
    company = str(raw.get("company") or "")
    published_at = str(raw.get("published_at") or "")
    fetched_at = str(raw.get("fetched_at") or "")
    source_quality = str(raw.get("source_quality") or raw.get("confidence") or "medium")
    detected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Freshness
    freshness = assess_freshness(published_at, fetched_at, source_type)

    # Pain extraction
    if source_type == "job_posting":
        job_intel = analyze_job_posting(signal_text)
        pain_types = job_intel["inferred_pain_types"]
        pain_summary = "; ".join(job_intel["intelligence_signals"]) or title
    else:
        pain_types = [extract_pain_type(signal_text)]
        pain_summary = summary[:300] or title

    pain_evidence = extract_pain_evidence(signal_text)

    # Subject
    industry = str(raw.get("category") or raw.get("industry") or "")
    people = list(raw.get("people") or [])
    # customer_or_employer: job posting → employer, else customer or unknown
    if source_type == "job_posting":
        customer_or_employer = "employer"
    elif raw.get("side") == "operator_demand":
        customer_or_employer = "customer"
    elif raw.get("side") == "vendor_supply":
        customer_or_employer = "vendor"
    else:
        customer_or_employer = "unknown"

    # Profile fit
    fit = score_fit(signal_text, pain_types, profile=profile, source_type=source_type)

    # Relationship path
    if baseline is None:
        baseline = _load_baseline_for_path()
    rel_path = assess_relationship_path(company, people, baseline=baseline)

    # Opportunity type
    opp_type = classify_opportunity_type(
        pain_types=pain_types,
        source_type=source_type,
        fit=fit,
        profile=profile,
        signal_text=signal_text,
    )

    # Confidence
    confidence = compute_confidence(
        fit=fit,
        freshness=freshness,
        relationship_path=rel_path,
        pain_evidence=pain_evidence,
        source_quality=source_quality,
    )

    # Posture
    posture = assign_posture(
        opportunity_type=opp_type,
        fit=fit,
        freshness=freshness,
        relationship_path_status=rel_path["status"],
        confidence=confidence,
    )

    # Proof stats
    proof_stats = {
        "sources_reviewed": 1,
        "matching_profile_facts": fit.get("matching_profile_facts", 0),
        "matching_relationship_paths": len(rel_path.get("contacts") or []) + len(rel_path.get("broker_candidates") or []),
        "deduped_signals": 0,
    }

    return {
        "opportunity_signal_id": _signal_id(source_type, company, pain_types[0], url),
        "detected_at": detected_at,
        "source": {
            "source_type": source_type,
            "source_name": source_name,
            "url": url,
            "freshness": freshness,
        },
        "pain": {
            "summary": pain_summary,
            "pain_type": pain_types[0] if pain_types else "vendor_gap",
            "evidence": pain_evidence,
        },
        "subject": {
            "company": company,
            "people": people,
            "industry": industry,
            "customer_or_employer": customer_or_employer,
        },
        "fit": {
            "user_capability_match": fit["user_capability_match"],
            "product_service_match": fit["product_service_match"],
            "matched_profile_capabilities": fit["matched_profile_capabilities"],
            "why_this_matters_to_user": fit["why_this_matters_to_user"],
        },
        "opportunity_type": opp_type,
        "relationship_path": rel_path,
        "recommended_posture": posture,
        "confidence": confidence,
        "proof_stats": proof_stats,
    }


# ---------------------------------------------------------------------------
# Batch scoring + section builder for daily brief
# ---------------------------------------------------------------------------

def score_signals(
    raw_signals: list[dict[str, Any]],
    *,
    profile: dict[str, Any],
    baseline: list[dict[str, Any]] | None = None,
    max_surface: int = 5,
) -> dict[str, Any]:
    """Score a batch of signals and return a brief-ready report.

    Returns:
        {
          "profile_id": "...",
          "scored": [...],         # all scored signals
          "surface": [...],        # top signals to show in the brief
          "ignored_count": int,    # no_action + ignore posture count
          "proof_stats": {...},
        }
    """
    if baseline is None:
        baseline = _load_baseline_for_path()

    scored = []
    for raw in raw_signals:
        try:
            sig = score_signal(raw, profile=profile, baseline=baseline)
            scored.append(sig)
        except Exception:  # noqa: BLE001
            continue

    # Sort: act_today first, then nurture, then research_first, then monitor, then ignore
    posture_rank = {"act_today": 0, "nurture": 1, "research_first": 2, "monitor": 3, "ignore": 4}
    confidence_rank = {"high": 0, "medium": 1, "low": 2}
    scored.sort(key=lambda s: (
        posture_rank.get(s["recommended_posture"], 5),
        confidence_rank.get(s["confidence"], 3),
    ))

    # Surface only actionable signals
    surface = [
        s for s in scored
        if s["recommended_posture"] not in ("ignore",) and s["opportunity_type"] != "no_action"
    ][:max_surface]

    ignored_count = sum(
        1 for s in scored
        if s["recommended_posture"] == "ignore" or s["opportunity_type"] == "no_action"
    )

    aggregate_proof = {
        "sources_reviewed": len(scored),
        "matching_profile_facts": sum(s["proof_stats"]["matching_profile_facts"] for s in scored),
        "matching_relationship_paths": sum(s["proof_stats"]["matching_relationship_paths"] for s in scored),
        "deduped_signals": 0,
    }

    return {
        "profile_id": profile.get("profile_id", "unknown"),
        "scored": scored,
        "surface": surface,
        "ignored_count": ignored_count,
        "proof_stats": aggregate_proof,
    }


def build_section(
    *,
    profile: dict[str, Any] | None = None,
    market_signals_report: dict[str, Any] | None = None,
    max_surface: int = 5,
) -> dict[str, Any]:
    """Build the opportunity sensing section for the daily brief.

    Loads the user profile and available signals, scores them, and returns
    a brief-ready report. Never raises — returns an error-flagged dict on failure.
    """
    try:
        if profile is None and _HAS_PROFILE_LOADER:
            profile = _up.load()
        if profile is None:
            return {
                "error": "No profile available — run profile_bootstrap.py first",
                "surface": [],
                "ignored_count": 0,
                "proof_stats": {"sources_reviewed": 0},
            }

        # Gather signals
        raw_signals: list[dict[str, Any]] = []
        if market_signals_report:
            raw_signals.extend(
                market_signals_report.get("top") or
                market_signals_report.get("all_ranked") or []
            )

        # Also load the feeder output if available
        feeder_path = _INBOX_DIR / "market_signals_feed.jsonl"
        if feeder_path.exists():
            for line in feeder_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        raw_signals.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

        if not raw_signals:
            return {
                "profile_id": profile.get("profile_id"),
                "surface": [],
                "ignored_count": 0,
                "proof_stats": {"sources_reviewed": 0},
                "note": "No signals available — market_signals.json or feeder output not yet populated.",
            }

        return score_signals(raw_signals, profile=profile, max_surface=max_surface)

    except Exception as exc:  # noqa: BLE001
        return {
            "error": str(exc),
            "surface": [],
            "ignored_count": 0,
            "proof_stats": {"sources_reviewed": 0},
        }


# ---------------------------------------------------------------------------
# Daily brief text renderer
# ---------------------------------------------------------------------------

def render_brief_section(report: dict[str, Any]) -> str:
    """Render the Opportunity Sensing section for inclusion in the daily brief."""
    if report.get("error"):
        return f"Opportunity Sensing: ERROR — {report['error']}"

    lines = ["Opportunity Sensing"]
    lines.append(f"(profile={report.get('profile_id')}, "
                 f"sources_reviewed={report['proof_stats']['sources_reviewed']}, "
                 f"ignored={report.get('ignored_count', 0)})")
    lines.append("")

    surface = report.get("surface") or []
    if not surface:
        note = report.get("note") or "No credible opportunities detected in current signals."
        lines.append(f"  {note}")
        return "\n".join(lines)

    for sig in surface:
        posture = sig["recommended_posture"].upper()
        company = sig["subject"]["company"] or "Unknown Company"
        opp_type = sig["opportunity_type"]
        confidence = sig["confidence"]
        pain_summary = sig["pain"]["summary"][:200]
        why = sig["fit"]["why_this_matters_to_user"]
        path_status = sig["relationship_path"]["status"]
        contacts = sig["relationship_path"].get("contacts") or []
        contact_names = ", ".join(c.get("name") or c.get("contact_id") for c in contacts[:3])

        lines.extend([
            f"[{posture}] {company} — {opp_type}",
            f"  Pain: {pain_summary}",
            f"  Why the user can help: {why}",
            f"  Relationship path: {path_status}" +
            (f" ({contact_names})" if contact_names else ""),
            f"  Confidence: {confidence}",
            f"  Grounding: {sig['proof_stats']['matching_profile_facts']} profile fact(s), "
            f"{sig['proof_stats']['matching_relationship_paths']} path(s)",
            "",
        ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------

# Canonical test profiles used across tests (no Todd-specific keys)
_PROFILE_A = {
    "profile_id": "profile_a_job_seeker",
    "source": "profiles_dir",
    "facts": {},
    "capabilities": [
        "enterprise_restaurant_tech_sales",
        "gtm_strategy_and_execution",
        "mcdonalds_global_account_management",
    ],
    "product_service_capabilities": [],
    "target_customers": [],
    "target_employers": ["restaurant_tech_vendors_vp_enterprise_or_above"],
    "target_industries": ["restaurant_technology", "quick_service_restaurants"],
    "adjacent_industries": ["payments_and_fintech"],
    "preferred_opportunity_types": ["job", "consulting", "content"],
    "no_go_categories": ["free_strategy_or_unpaid_thinking"],
    "credible_pain_types": ["gtm_execution_failure", "leadership_gap", "integration_failure"],
    "constraints": {},
    "voice_constraints": [],
    "intro_philosophy": "balanced",
    "intro_boundaries": [],
    "career_context": "job_search",
    "confidence": "high",
}

_PROFILE_B = {
    "profile_id": "profile_b_saas_seller",
    "source": "profiles_dir",
    "facts": {},
    "capabilities": ["enterprise_saas_sales", "drive_thru_technology"],
    "product_service_capabilities": ["drive_thru_ai_solution", "restaurant_voice_ai"],
    "target_customers": ["quick_service_restaurant_chains_200_plus_units"],
    "target_employers": [],
    "target_industries": ["restaurant_technology", "drive_thru"],
    "adjacent_industries": [],
    "preferred_opportunity_types": ["sales", "partnership"],
    "no_go_categories": ["commission_only_arrangements"],
    "credible_pain_types": ["vendor_gap", "digital_transformation", "operational_complexity"],
    "constraints": {},
    "voice_constraints": [],
    "intro_philosophy": "aggressive",
    "intro_boundaries": [],
    "career_context": "employed",
    "confidence": "high",
}

_PROFILE_C = {
    "profile_id": "profile_c_consultant",
    "source": "profiles_dir",
    "facts": {},
    "capabilities": ["restaurant_technology_advisory", "gtm_consulting"],
    "product_service_capabilities": ["advisory_fractional_sales_leadership",
                                     "gtm_execution_consulting_for_restaurant_tech"],
    "target_customers": ["restaurant_tech_vendors_series_b_to_public"],
    "target_employers": [],
    "target_industries": ["restaurant_technology"],
    "adjacent_industries": [],
    "preferred_opportunity_types": ["consulting", "content", "partnership"],
    "no_go_categories": [],
    "credible_pain_types": ["gtm_execution_failure", "market_expansion", "operational_complexity"],
    "constraints": {},
    "voice_constraints": [],
    "intro_philosophy": "balanced",
    "intro_boundaries": [],
    "career_context": "consulting",
    "confidence": "high",
}

_PROFILE_WEAK_FIT = {
    "profile_id": "profile_d_weak_fit",
    "source": "profiles_dir",
    "facts": {},
    "capabilities": ["supply_chain_logistics", "warehouse_management"],
    "product_service_capabilities": ["logistics_optimization_software"],
    "target_customers": ["retail_grocery_chains"],
    "target_employers": ["logistics_companies"],
    "target_industries": ["supply_chain", "logistics", "grocery"],
    "adjacent_industries": ["cold_storage"],
    "preferred_opportunity_types": ["sales", "consulting"],
    "no_go_categories": [],
    "credible_pain_types": ["operational_complexity", "cost_pressure"],
    "constraints": {},
    "voice_constraints": [],
    "intro_philosophy": "balanced",
    "intro_boundaries": [],
    "career_context": "consulting",
    "confidence": "high",
}

# Test signals
_SIGNAL_MARKET_PAIN_OPERATOR = {
    "title": "Restaurant operator announces store closures, margin pressure, and technology modernization",
    "url": "https://www.restaurantdive.com/news/operator-closures-modernization",
    "source_name": "Restaurant Dive",
    "source_type": "market_news",
    "company": "Sunshine Grill",
    "side": "operator_demand",
    "category": "restaurant_technology",
    "signal_type": "turnaround",
    "pain_point_or_priority": (
        "Cost pressure from rising food costs and labor. Store closures of 12 underperforming "
        "locations. Technology modernization program initiated to reduce operational complexity."
    ),
    "strategic_relevance": "high",
    "published_at": "2026-05-27",
    "fetched_at": "2026-05-27T08:00:00+00:00",
    "confidence": "high",
}

_SIGNAL_JOB_EMPLOYMENT = {
    "title": "Restaurant-tech vendor posts VP Enterprise Accounts role for national chain expansion",
    "url": "https://jobs.example.com/vp-enterprise-accounts",
    "source_name": "LinkedIn Jobs",
    "source_type": "job_posting",
    "company": "DriveAI",
    "side": "vendor_supply",
    "category": "restaurant_technology",
    "signal_type": "hiring_need",
    "pain_point_or_priority": (
        "VP Enterprise Accounts needed to lead national chain expansion program. "
        "Candidate should have experience managing enterprise restaurant technology accounts "
        "and franchise adoption. GTM execution background required."
    ),
    "strategic_relevance": "high",
    "published_at": "2026-05-27",
    "fetched_at": "2026-05-27T08:00:00+00:00",
    "confidence": "high",
}

_SIGNAL_JOB_DIGITAL_TRANSFORM = {
    "title": "Target company posts for Director of Digital Transformation",
    "url": "https://jobs.example.com/director-digital-transformation",
    "source_name": "LinkedIn Jobs",
    "source_type": "job_posting",
    "company": "MegaBurger",
    "side": "operator_demand",
    "category": "restaurant_technology",
    "signal_type": "hiring_need",
    "pain_point_or_priority": (
        "Director of Digital Transformation needed. Responsibilities include modernizing "
        "fragmented POS systems, drive-thru voice AI, and loyalty data integration. "
        "Current systems are legacy and siloed."
    ),
    "strategic_relevance": "high",
    "published_at": "2026-05-27",
    "fetched_at": "2026-05-27T08:00:00+00:00",
    "confidence": "high",
}

_SIGNAL_DRIVE_THRU_AI = {
    "title": "Restaurant operator invests in AI drive-thru modernization",
    "url": "https://www.qsrmagazine.com/technology/ai-drive-thru-modernization",
    "source_name": "QSR Magazine",
    "source_type": "market_news",
    "company": "FastBurger",
    "side": "operator_demand",
    "category": "drive_thru",
    "signal_type": "operator_priority",
    "pain_point_or_priority": (
        "National QSR chain invests $50M in AI drive-thru modernization. "
        "Target: reduce order inaccuracy and speed of service. "
        "Current drive-thru systems legacy and inconsistent."
    ),
    "strategic_relevance": "high",
    "published_at": "2026-05-27",
    "fetched_at": "2026-05-27T08:00:00+00:00",
    "confidence": "medium",
}

_SIGNAL_STALE = {
    "title": "QSR operator modernization initiative",
    "url": "https://www.restaurantdive.com/old-article",
    "source_name": "Restaurant Dive",
    "source_type": "market_news",
    "company": "OldBrand",
    "side": "operator_demand",
    "category": "restaurant_technology",
    "pain_point_or_priority": "Technology modernization initiative announced.",
    "strategic_relevance": "high",
    "published_at": "2026-01-01",
    "fetched_at": "2026-01-01T08:00:00+00:00",
    "confidence": "medium",
}


def _smoke() -> int:
    failures: list[str] = []

    def ck(cond: bool, msg: str) -> None:
        mark = "OK" if cond else "FAIL"
        print(f"  {mark}   {msg}")
        if not cond:
            failures.append(msg)

    # 1. Market intelligence pain → sales opportunity
    sig_sales = score_signal(_SIGNAL_MARKET_PAIN_OPERATOR, profile=_PROFILE_B, baseline=[])
    ck(sig_sales["opportunity_type"] == "sales",
       f"Market pain → sales opportunity for Profile B (got {sig_sales['opportunity_type']})")
    ck(sig_sales["pain"]["pain_type"] in PAIN_TYPES,
       f"Pain type is valid (got {sig_sales['pain']['pain_type']})")

    # 2. Market intelligence pain → consulting opportunity
    sig_consulting = score_signal(_SIGNAL_MARKET_PAIN_OPERATOR, profile=_PROFILE_C, baseline=[])
    ck(sig_consulting["opportunity_type"] in ("consulting", "sales", "research"),
       f"Market pain → consulting/sales for Profile C (got {sig_consulting['opportunity_type']})")
    ck(sig_consulting["fit"]["user_capability_match"] != "none",
       f"Profile C has some capability match for operator pain (got {sig_consulting['fit']['user_capability_match']})")

    # 3. Job posting → employment opportunity
    sig_job = score_signal(_SIGNAL_JOB_EMPLOYMENT, profile=_PROFILE_A, baseline=[])
    ck(sig_job["opportunity_type"] == "job",
       f"Job posting → employment for Profile A job_seeker (got {sig_job['opportunity_type']})")
    ck(sig_job["source"]["source_type"] == "job_posting",
       "Source type preserved as job_posting")

    # 4. Job posting → sales opportunity
    sig_job_sales = score_signal(_SIGNAL_JOB_DIGITAL_TRANSFORM, profile=_PROFILE_B, baseline=[])
    ck(sig_job_sales["opportunity_type"] in ("sales", "consulting"),
       f"Job posting (digital transform) → sales/consulting for SaaS seller (got {sig_job_sales['opportunity_type']})")

    # 5. Same signal scored differently for two profiles
    sig_a = score_signal(_SIGNAL_DRIVE_THRU_AI, profile=_PROFILE_A, baseline=[])
    sig_b = score_signal(_SIGNAL_DRIVE_THRU_AI, profile=_PROFILE_B, baseline=[])
    ck(sig_a["opportunity_type"] != sig_b["opportunity_type"] or
       sig_a["fit"]["user_capability_match"] != sig_b["fit"]["user_capability_match"],
       f"Same signal produces different result for Profile A ({sig_a['opportunity_type']}) "
       f"vs Profile B ({sig_b['opportunity_type']})")

    # 6. Weak-fit signal → no_action or ignore
    sig_weak = score_signal(_SIGNAL_DRIVE_THRU_AI, profile=_PROFILE_WEAK_FIT, baseline=[])
    ck(sig_weak["opportunity_type"] == "no_action" or
       sig_weak["recommended_posture"] in ("ignore", "monitor"),
       f"Weak-fit profile → no_action or ignore (type={sig_weak['opportunity_type']}, "
       f"posture={sig_weak['recommended_posture']})")

    # 7. Source freshness downgrade → research_first or monitor
    sig_stale = score_signal(_SIGNAL_STALE, profile=_PROFILE_B, baseline=[])
    ck(sig_stale["source"]["freshness"] == "stale",
       f"Stale signal detected as stale (got {sig_stale['source']['freshness']})")
    ck(sig_stale["recommended_posture"] in ("research_first", "monitor", "ignore"),
       f"Stale signal → research_first/monitor/ignore (got {sig_stale['recommended_posture']})")

    # 8. Relationship path confidence discipline — never invent a path
    # With empty baseline: must be no_known_path or unknown
    sig_path = score_signal(_SIGNAL_DRIVE_THRU_AI, profile=_PROFILE_B, baseline=[])
    ck(sig_path["relationship_path"]["status"] in PATH_STATUSES,
       f"Relationship path status is a valid enum value (got {sig_path['relationship_path']['status']})")
    ck(sig_path["relationship_path"]["status"] in ("no_known_path", "unknown"),
       f"Empty baseline → no_known_path or unknown (got {sig_path['relationship_path']['status']})")

    # 9. No person-specific schema fields in output
    all_keys: set[str] = set()
    for sig in [sig_sales, sig_consulting, sig_job, sig_job_sales, sig_a, sig_b, sig_weak, sig_stale]:
        all_keys.update(_collect_all_keys(sig))
    person_specific = [k for k in all_keys if re.search(r"_to_todd$|_for_todd$|todd_specific", k)]
    ck(not person_specific,
       f"No person-specific schema fields in output (found: {person_specific})")

    # 10. proof_stats present and non-negative
    for sig in [sig_sales, sig_job, sig_stale]:
        ps = sig["proof_stats"]
        ck(ps["sources_reviewed"] >= 0 and ps["matching_profile_facts"] >= 0,
           f"proof_stats are non-negative for signal {sig['opportunity_signal_id'][:20]}")

    # 11. Opportunity signal ID is stable for same input
    id1 = _signal_id("job_posting", "DriveAI", "hiring_need", "https://jobs.example.com/vp")
    id2 = _signal_id("job_posting", "DriveAI", "hiring_need", "https://jobs.example.com/vp")
    ck(id1 == id2, "Opportunity signal ID is deterministic for same input")

    # 12. Job posting intelligence extraction works
    job_intel = analyze_job_posting(_SIGNAL_JOB_DIGITAL_TRANSFORM["pain_point_or_priority"])
    ck("digital_transformation" in job_intel["inferred_pain_types"] or
       "vendor_gap" in job_intel["inferred_pain_types"],
       f"Job posting intelligence extracts digital_transformation or vendor_gap pain "
       f"(got {job_intel['inferred_pain_types']})")
    ck(job_intel["job_signal_quality"] in ("rich", "moderate", "thin"),
       "Job signal quality is valid")

    print(f"--- opportunity_sensing smoke complete: {len(failures)} failure(s) ---")
    return 1 if failures else 0


def _collect_all_keys(d: Any, _seen: set | None = None) -> set[str]:
    """Recursively collect all dict keys from a nested structure."""
    if _seen is None:
        _seen = set()
    if isinstance(d, dict):
        for k, v in d.items():
            _seen.add(k)
            _collect_all_keys(v, _seen)
    elif isinstance(d, (list, tuple)):
        for item in d:
            _collect_all_keys(item, _seen)
    return _seen


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="RB opportunity sensing")
    p.add_argument("--smoke", action="store_true", help="Run in-memory regression tests")
    p.add_argument("--json", action="store_true", help="Emit JSON output")
    p.add_argument("--profile", dest="profile_id", help="Profile ID to score against")
    p.add_argument("--top", type=int, default=5, help="Number of opportunities to surface")
    args = p.parse_args(argv)

    if args.smoke:
        return _smoke()

    if not _HAS_PROFILE_LOADER:
        print("ERROR: user_profile.py not found — cannot load profile", file=sys.stderr)
        return 1

    profile = _up.load(args.profile_id)
    try:
        import market_signals as _ms  # noqa: E402
        ms_report = _ms.build_report(top_n=20)
    except Exception:  # noqa: BLE001
        ms_report = None

    report = build_section(profile=profile, market_signals_report=ms_report, max_surface=args.top)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render_brief_section(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
