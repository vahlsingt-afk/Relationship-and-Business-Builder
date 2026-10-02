#!/usr/bin/env python3
"""
completeness_contract.py — RB-DEFECT-016-F: Completeness-before-interpretation contract.

Every major section must answer "What must be known to answer this question?"
before emitting any classification.

Provides:
  SectionCompleteness     — dataclass representing a section's evidence gate
  assess_momentum_coverage — check whether relationship momentum can be classified
  build_information_debt_queue — scan sections for blocked/INCOMPLETE assessments

Architecture rule (DEFECT-016-F):
  1. Can RB answer this question with current data?
     If NO → attempt automatic retrieval.
  2. Can RB automatically obtain missing data?
     If YES → initiate retrieval, re-evaluate.
     If NO  → mark INCOMPLETE, generate remediation action.
  Only after passing these gates may interpretation/classification occur.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Completeness states
# ---------------------------------------------------------------------------

COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"
INCOMPLETE = "INCOMPLETE"

# Coverage thresholds for momentum classification
# sms: must be available_with_snippets or available_fresh
SMS_SUFFICIENT_STATES = {"available_with_snippets", "available_fresh"}
# linkedin: stale but not unavailable is PARTIAL, not INCOMPLETE
LINKEDIN_BLOCKING_STATES = {"source_unavailable", "refresh_failed"}


@dataclass
class SectionCompleteness:
    """Evidence-gate result for one section's classification attempt."""
    section: str
    status: str                          # COMPLETE | PARTIAL | INCOMPLETE
    required_sources: list[str]
    missing_sources: list[str]
    recovery_action: str
    confidence: str                      # high | medium | low
    classifications_withheld: list[str] = field(default_factory=list)
    notes: str = ""

    @property
    def can_classify(self) -> bool:
        return self.status == COMPLETE

    def to_brief_item(self) -> dict:
        """Return a _canonical_item-compatible dict for embedding in sections."""
        withheld_str = (
            f" Classifications withheld: {', '.join(self.classifications_withheld)}."
            if self.classifications_withheld else ""
        )
        return {
            "title": f"[{self.status}] {self.section} — completeness gate",
            "summary": (
                f"Required sources: {', '.join(self.required_sources)}. "
                f"Missing: {', '.join(self.missing_sources) if self.missing_sources else 'none'}."
                + withheld_str
            ),
            "why_it_matters": (
                "RB cannot emit a classification when required sources are missing. "
                f"Recovery: {self.recovery_action}"
            ),
            "recommended_action": self.recovery_action,
            "disposition": "act_today" if self.status == INCOMPLETE else "monitor",
            "grounding": "system_detected",
            "freshness": "fresh",
            "source_refs": ["completeness_contract"],
            "confidence": self.confidence,
            "extras": {
                "completeness_status": self.status,
                "required_sources": self.required_sources,
                "missing_sources": self.missing_sources,
                "classifications_withheld": self.classifications_withheld,
                "recovery_action": self.recovery_action,
            },
        }


# ---------------------------------------------------------------------------
# Source coverage helpers
# ---------------------------------------------------------------------------

def _sms_coverage_from_report(report: dict) -> str:
    """Return sms coverage state from direct_comms_health items in report.

    Returns one of: available_fresh | available_with_snippets |
                    available_metadata_only | available_stale | unavailable
    """
    dch_items = report.get("direct_comms_health") or []
    for item in dch_items:
        extras = item.get("extras") or {}
        state = extras.get("direct_comms_state") or ""
        channel = extras.get("channel") or item.get("title") or ""
        if "message" in channel.lower() or "sms" in channel.lower():
            return state
    # If no direct_comms items exist, try checking direct from module
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import direct_comms_health as _dch
        result = _dch.check_messages_readiness()
        return result.get("state", "unavailable")
    except Exception:  # noqa: BLE001
        return "unavailable"


def _linkedin_coverage_from_freshness(source_freshness: dict) -> str:
    """Return 'covered', 'stale', or 'unavailable' for LinkedIn delta."""
    li = (source_freshness.get("sources") or {}).get("linkedin_delta") or {}
    label = li.get("label") or "source_unavailable"
    if label in LINKEDIN_BLOCKING_STATES:
        return "unavailable"
    if li.get("stale"):
        return "stale"
    return "covered"


def _email_coverage_from_freshness(source_freshness: dict) -> str:
    """Return 'covered' or 'stale'/'unavailable'."""
    em = (source_freshness.get("sources") or {}).get("email") or {}
    label = em.get("label") or "source_unavailable"
    if label == "source_unavailable":
        return "unavailable"
    if em.get("stale"):
        return "stale"
    return "covered"


# ---------------------------------------------------------------------------
# Momentum coverage assessment (DEFECT-016-A gate)
# ---------------------------------------------------------------------------

MOMENTUM_REQUIRED_SOURCES = ["baseline", "sms", "email", "linkedin_delta", "whatsapp"]


def assess_momentum_coverage(report: dict, source_freshness: dict) -> SectionCompleteness:
    """Evaluate whether relationship momentum classification is evidence-safe.

    A COMPLETE result means all required sources are available and current.
    A PARTIAL result means secondary sources (LinkedIn) are stale but not blocking.
    An INCOMPLETE result means primary direct-comms sources (SMS) are unresolvable.

    Only COMPLETE or PARTIAL allows classification to proceed.
    INCOMPLETE withholds all tier classifications.
    """
    missing: list[str] = []
    blocking: list[str] = []

    # Baseline: always available if the brief got this far
    baseline_ok = bool((source_freshness.get("sources") or {}).get("baseline"))

    # SMS / Apple Messages
    sms_state = _sms_coverage_from_report(report)
    sms_ok = sms_state in SMS_SUFFICIENT_STATES
    if not sms_ok:
        if sms_state == "unavailable":
            missing.append("sms (unavailable)")
            blocking.append("sms")
        elif sms_state == "available_metadata_only":
            missing.append("sms (metadata-only — no content)")
            blocking.append("sms")
        elif sms_state in ("available_stale", ""):
            missing.append("sms (stale)")
            blocking.append("sms")

    # Email
    email_cov = _email_coverage_from_freshness(source_freshness)
    if email_cov != "covered":
        missing.append(f"email ({email_cov})")
        # Email stale is blocking for momentum confidence

    # LinkedIn delta
    li_cov = _linkedin_coverage_from_freshness(source_freshness)
    if li_cov != "covered":
        missing.append(f"linkedin_delta ({li_cov})")
        # LinkedIn stale is PARTIAL, not blocking (it changes slowly)
        if li_cov == "unavailable":
            blocking.append("linkedin_delta")

    # WhatsApp is export-backed rather than live. Without a processed cache,
    # RB cannot claim that baseline-only silence represents real inactivity.
    whatsapp_cache = Path(__file__).resolve().parent.parent / ".cache" / "whatsapp_chats.json"
    if not whatsapp_cache.exists():
        missing.append("whatsapp (no processed export)")

    # Determine completeness status
    if not missing:
        status = COMPLETE
        confidence = "high"
    elif blocking:
        status = INCOMPLETE
        confidence = "low"
    else:
        # Only secondary sources degraded (email stale, LinkedIn stale — not blocking)
        status = PARTIAL
        confidence = "medium"

    recovery_parts = []
    if "sms" in blocking or any("sms" in m for m in missing):
        recovery_parts.append("Run: python3 system/scripts/fetch_apple_messages.py --days 90 --include-snippets")
    if "linkedin_delta" in blocking:
        recovery_parts.append("Run: python3 system/scripts/linkedin_ingest.py")
    if any("email" in m for m in missing):
        recovery_parts.append("Refresh email overlay (morning_pipeline.py)")
    if any("whatsapp" in m for m in missing):
        recovery_parts.append("Drop a current WhatsApp export in system/inbox/whatsapp_exports/")
    recovery_action = "; ".join(recovery_parts) if recovery_parts else "Refresh all communication sources."

    return SectionCompleteness(
        section="relationship_momentum_status",
        status=status,
        required_sources=MOMENTUM_REQUIRED_SOURCES,
        missing_sources=missing,
        recovery_action=recovery_action,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Information Debt Queue (DEFECT-016-E)
# ---------------------------------------------------------------------------

def build_information_debt_queue(sections: dict, report: dict, source_freshness: dict) -> list[dict]:
    """Scan sections for INCOMPLETE/PARTIAL completeness gates and blocked classifications.

    Returns a list of information-debt items (canonical_item-compatible dicts).
    Each item represents one blocked conclusion with its cause and recovery action.

    Information debt is upstream of relationship debt and loop debt:
      Unresolved information debt → false momentum states → false loop priorities.
    """
    debt_items: list[dict] = []

    # 1. Check relationship momentum coverage
    momentum_completeness = assess_momentum_coverage(report, source_freshness)
    if momentum_completeness.status != COMPLETE:
        # Find contacts that were withheld
        withheld = momentum_completeness.classifications_withheld

        # Scan relationship_momentum_status for any INCOMPLETE markers
        for item in sections.get("relationship_momentum_status") or []:
            extras = item.get("extras") or {}
            if extras.get("classification_withheld"):
                name = extras.get("contact_name") or item.get("title") or "unknown"
                withheld.append(name)

        # Per-source recovery actions — each debt item gets only its own recovery instruction
        _SOURCE_RECOVERY: dict[str, str] = {
            "sms":           "python3 system/scripts/fetch_apple_messages.py --days 90 --include-snippets",
            "email":         "python3 system/scripts/refresh_sources.py --email --save-health",
            "linkedin_delta": "python3 system/scripts/linkedin_ingest.py",
        }

        # One debt item per missing source that's blocking classification
        for missing_src in momentum_completeness.missing_sources:
            is_blocking = momentum_completeness.status == INCOMPLETE
            # Match the missing source key to its per-source recovery instruction
            src_key = next(
                (k for k in _SOURCE_RECOVERY if missing_src.startswith(k)),
                None,
            )
            per_src_recovery = (
                _SOURCE_RECOVERY[src_key] if src_key
                else momentum_completeness.recovery_action
            )
            debt_items.append({
                "title": f"[INFORMATION DEBT] Momentum classification blocked — {missing_src}",
                "summary": (
                    f"Relationship momentum scoring requires {missing_src} but it is unavailable or unreconciled. "
                    + (f"Contacts affected: {', '.join(withheld[:5])}{'...' if len(withheld) > 5 else ''}."
                       if withheld else "")
                ),
                "why_it_matters": (
                    "False COLD/FROZEN classifications result when newer communication evidence exists in SMS or LinkedIn "
                    "but has not been reconciled. Emitting momentum tiers from baseline last_touch alone "
                    "violates the source-trust doctrine."
                ),
                "recommended_action": per_src_recovery,
                "disposition": "act_today" if is_blocking else "monitor",
                "grounding": "system_detected",
                "freshness": "fresh",
                "source_refs": ["completeness_contract", "direct_comms_health", "cos_judgment"],
                "confidence": "high",
                "extras": {
                    "debt_type": "momentum_source_missing",
                    "missing_source": missing_src,
                    "completeness_status": momentum_completeness.status,
                    "blocking": is_blocking,
                    "contacts_affected": withheld[:20],
                    "recovery_action": per_src_recovery,
                    "status": "BLOCKED_PENDING_DATA" if is_blocking else "DEGRADED_PARTIAL_DATA",
                },
            })

    # 2. Scan actionable intelligence sections for stale-source degradation.
    # Only flag sections that directly produce classifications or recommendations.
    # Skip meta/diagnostic/administrative sections to avoid noise.
    ACTIONABLE_INTELLIGENCE_SECTIONS = {
        "opportunity_board",
        "watchlist_intelligence",
        "last_24h_relationship_signals",
        "relationship_operational_signal_review",
        "connect_the_dots",
        "entity_spotlight",
        "emerging_themes",
        "industry_brief",
        "world_macro_macroeconomic_impact",
    }
    EXCLUDE_FROM_STALE_SCAN = {
        "relationship_momentum_status",  # already handled above
        "information_debt_queue",        # don't self-reference
        "decision_layer",
        "five_things_today",
        "trust_metrics",
        "source_audit",
        "suppressed_today",
        "graph_mutation_log",
        "intelligence_cycle_continuation",
        "daily_prep_summary",
        "recommended_actions",
        "recommended_actions_structured",
        "decision_queue",
        "signal_freshness",
        "resource_verification_and_freshness_status",
        "active_knowledge_assets",
    }

    stale_sections: dict[str, list[str]] = {}
    for section_key, items in sections.items():
        if section_key not in ACTIONABLE_INTELLIGENCE_SECTIONS:
            continue
        if section_key in EXCLUDE_FROM_STALE_SCAN:
            continue
        if not isinstance(items, list):
            continue
        stale_titles: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if (item.get("freshness") or "") == "stale_source_limited":
                stale_titles.append(item.get("title") or "")
        if stale_titles:
            stale_sections[section_key] = stale_titles

    _SECTION_SOURCE_MAP: dict[str, str] = {
        "industry_brief":               "python3 system/scripts/refresh_sources.py --market --save-health",
        "last_24h_relationship_signals": "python3 system/scripts/refresh_sources.py --all --save-health --refresh-signals",
        "opportunity_board":            "python3 system/scripts/refresh_sources.py --all --save-health",
        "watchlist_intelligence":       "python3 system/scripts/refresh_sources.py --market --save-health",
        "connect_the_dots":             "python3 system/scripts/refresh_sources.py --all --save-health",
        "entity_spotlight":             "python3 system/scripts/refresh_sources.py --all --save-health",
        "emerging_themes":              "python3 system/scripts/refresh_sources.py --all --save-health",
    }

    for section_key, titles in stale_sections.items():
        recovery = _SECTION_SOURCE_MAP.get(
            section_key,
            f"python3 system/scripts/refresh_sources.py --all --save-health  # feeds {section_key}",
        )
        debt_items.append({
            "title": f"[INFORMATION DEBT] Stale source degrading {section_key}",
            "summary": (
                f"{len(titles)} item(s) in {section_key} are marked stale_source_limited: "
                + "; ".join(titles[:3]) + ("..." if len(titles) > 3 else "")
            ),
            "why_it_matters": (
                f"Stale sources in {section_key} reduce confidence in those classifications. "
                "Refresh the relevant source before acting on these items."
            ),
            "recommended_action": recovery,
            "disposition": "monitor",
            "grounding": "system_detected",
            "freshness": "fresh",
            "source_refs": [section_key],
            "confidence": "medium",
            "extras": {
                "debt_type": "stale_source_degradation",
                "section": section_key,
                "affected_items": titles[:10],
                "status": "DEGRADED_PARTIAL_DATA",
            },
        })

    # Sort: BLOCKED_PENDING_DATA first, then DEGRADED
    debt_items.sort(key=lambda x: (
        0 if (x.get("extras") or {}).get("status") == "BLOCKED_PENDING_DATA" else 1,
        x.get("title") or "",
    ))
    return debt_items


# ---------------------------------------------------------------------------
# Decision synthesis layer (DEFECT-016-D)
# ---------------------------------------------------------------------------

# Category weights for priority scoring
CATEGORY_WEIGHTS: dict[str, float] = {
    "opportunity_board": 1.0,
    "connect_the_dots": 0.95,
    "entity_spotlight": 0.9,
    "decision_queue": 0.88,
    "relationship_momentum_status": 0.75,
    "last_24h_relationship_signals": 0.75,
    "relationship_operational_signal_review": 0.7,
    "loops_and_obligations": 0.5,
    "intelligence_cycle_continuation": 0.4,
    "recommended_actions": 0.35,
}

# Domain bonus: sections representing live open threads get a flat additive boost
# so that an active opportunity always outranks analytical convergence signals.
# An opportunity you're actively engaged with is a higher-leverage decision than
# a cross-time analysis pattern, regardless of confidence label.
_ACTIVE_THREAD_SECTIONS = {"opportunity_board", "decision_queue"}
_ACTIVE_THREAD_BONUS = 25.0

LOOP_SECTION = "loops_and_obligations"
STRATEGIC_SECTIONS = {"opportunity_board", "connect_the_dots", "entity_spotlight", "decision_queue"}


def build_decision_layer(sections: dict) -> list[dict]:
    """Synthesize the top 3 decisions for today from across the brief.

    A decision is: one action where deferral has a measurable cost.
    Decisions are ranked by:
      1. Strategic opportunity weight
      2. Relationship momentum risk (COLD/FROZEN contact + open loop)
      3. Overdue loop with active relationship thread

    Returns at most 3 items. Each includes a justification and opportunity cost.
    """
    _DISP_SCORE = {"act_today": 100, "ask_todd": 80, "monitor": 40, "ignore": 0}
    _FRESHNESS_BOOST = {"fresh": 10, "manual_context": -15, "stale_source_limited": -20}

    candidates: list[tuple[float, dict, str]] = []
    seen_titles: set[str] = set()

    SKIP = {
        "five_things_today", "decision_layer", "information_debt_queue",
        "trust_metrics", "source_audit", "suppressed_today",
        "active_knowledge_assets", "what_changed_since_yesterday",
        "graph_mutation_log",
    }

    for section_key, items in sections.items():
        if section_key in SKIP or not isinstance(items, list):
            continue
        cat_weight = CATEGORY_WEIGHTS.get(section_key, 0.3)
        for item in items:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or ""
            if not title or title in seen_titles:
                continue
            # RB-2026-09-03: RB's own bookkeeping self-check items (e.g.
            # "RI mutation and loop proof") are deliberately act_today
            # whenever loops are overdue, but they are not a real decision
            # for Todd -- exclude explicitly rather than let disposition
            # alone decide eligibility.
            if (item.get("extras") or {}).get("is_meta_accountability_note"):
                continue
            disp = item.get("disposition") or "monitor"
            if disp in ("ignore", "monitor"):
                continue  # decisions must be act_today or ask_todd
            base = _DISP_SCORE.get(disp, 40)
            base += _FRESHNESS_BOOST.get(item.get("freshness") or "", 0)
            if item.get("confidence") == "high":
                base += 10
            if item.get("grounding") == "system_detected":
                base += 20
            score = base * cat_weight
            # Active-thread bonus: opportunity_board and decision_queue represent
            # live open commitments — they outrank analytical convergence signals.
            if section_key in _ACTIVE_THREAD_SECTIONS:
                score += _ACTIVE_THREAD_BONUS
            candidates.append((score, item, section_key))
            seen_titles.add(title)

    if not candidates:
        return []

    # Guarantee at least one strategic item if any exist
    strategic = [(s, it, sk) for s, it, sk in candidates if sk in STRATEGIC_SECTIONS]
    non_strategic = [(s, it, sk) for s, it, sk in candidates if sk not in STRATEGIC_SECTIONS]

    # Build final top-3: prefer strategic first
    ordered: list[tuple[float, dict, str]] = []
    ordered.extend(sorted(strategic, key=lambda x: -x[0]))
    ordered.extend(sorted(non_strategic, key=lambda x: -x[0]))

    # Dedupe by title (strategic may also appear in non-strategic pool)
    final: list[tuple[float, dict, str]] = []
    seen2: set[str] = set()
    for score, item, section_key in ordered:
        t = item.get("title") or ""
        if t not in seen2:
            final.append((score, item, section_key))
            seen2.add(t)
    final = final[:3]

    result: list[dict] = []
    for rank, (score, item, section_key) in enumerate(final, 1):
        # Build opportunity-cost justification
        tier = (item.get("extras") or {}).get("momentum_tier") or ""
        days_quiet = (item.get("extras") or {}).get("days_quiet")
        opp_cost_parts = []
        if tier in ("COLD", "FROZEN") and days_quiet:
            opp_cost_parts.append(
                f"Relationship momentum is {tier} ({days_quiet}d quiet). "
                "Deferral deepens the gap; re-engagement cost rises each day."
            )
        if section_key in STRATEGIC_SECTIONS:
            opp_cost_parts.append("Strategic opportunity windows are time-bounded. Deferral risks competitor positioning.")
        if section_key == LOOP_SECTION:
            opp_cost_parts.append("Overdue loop signals broken follow-through. Counterparty may interpret as disengagement.")
        if not opp_cost_parts:
            opp_cost_parts.append("Deferral defers the outcome. No neutral holding pattern exists.")

        opp_cost = " ".join(opp_cost_parts)

        result.append({
            "title": f"Decision #{rank}: {item.get('title', '')}",
            "summary": item.get("summary") or item.get("why_it_matters") or "",
            "why_it_matters": item.get("why_it_matters") or "",
            "recommended_action": item.get("recommended_action") or "",
            "disposition": "act_today",
            "grounding": item.get("grounding") or "system_detected",
            "freshness": item.get("freshness") or "fresh",
            "source_refs": item.get("source_refs") or [section_key],
            "confidence": item.get("confidence") or "medium",
            "extras": {
                "source_section": section_key,
                "decision_rank": rank,
                "priority_score": round(score, 1),
                "opportunity_cost": opp_cost,
                "justification": (
                    f"Ranked #{rank} of 3 decisions today. "
                    f"Source: {section_key}. Score: {round(score, 1)}."
                ),
            },
        })

    return result


# ---------------------------------------------------------------------------
# Category-aware five_things slot enforcement (DEFECT-016-C)
# ---------------------------------------------------------------------------

# Maximum loop items allowed in top-5 (across ALL loop-like sections)
LOOP_TOP5_CAP = 2
# If strategic/decision items exist, guarantee at least this many slots
STRATEGIC_GUARANTEED_SLOTS = 1

# Sections considered "loop-like" for the purpose of the slot cap.
# Any item from these sections whose title matches a loop marker counts against the loop cap.
LOOP_LIKE_SECTIONS = {
    LOOP_SECTION,
    "relationship_operational_signal_review",
    "top_priorities_today",
    "recommended_actions",
}

# Title keywords that mark an item as loop-content regardless of section
_LOOP_TITLE_MARKERS = ("overdue", "overdue loop", "due today", "closing this week")


def _is_loop_item(title: str, section_key: str) -> bool:
    """Return True if the item is loop-content (from loop section or title indicates loop)."""
    if section_key in LOOP_LIKE_SECTIONS:
        t_lower = (title or "").lower()
        return any(marker in t_lower for marker in _LOOP_TITLE_MARKERS)
    return section_key == LOOP_SECTION


def _loop_key(title: str) -> str:
    """Normalize a loop title to a dedup key.

    'Overdue: L-2026-05-08-020 — Patrick Nelson'
    'Overdue loop L-2026-05-08-020 — Patrick Nelson'
    → 'l-2026-05-08-020'
    """
    import re
    m = re.search(r"(L-\d{4}-\d{2}-\d{2}-\d+)", title or "", re.IGNORECASE)
    return m.group(1).upper() if m else (title or "").lower()[:60]


def enforce_top5_category_balance(
    candidates: list[tuple[int, dict, str]],
    n: int = 5,
) -> list[tuple[int, dict, str]]:
    """Enforce category balance in the top-N brief priorities.

    Rules:
      1. Loop items (from any loop-like section, identified by title pattern) are
         capped at LOOP_TOP5_CAP slots total across all sections.
      2. Loop items for the same underlying loop (same loop ID) are deduplicated:
         only the highest-scoring representation of each loop is kept.
      3. If strategic items exist, guarantee at least STRATEGIC_GUARANTEED_SLOTS.
      4. Within categories, rank by score descending.
      5. Fill remaining slots with highest-score non-loop items.

    Returns a re-ordered list of up to n candidates.
    """
    # First pass: deduplicate loops across sections by loop ID
    seen_loop_keys: dict[str, tuple[int, dict, str]] = {}
    non_loop: list[tuple[int, dict, str]] = []
    for score, item, sk in candidates:
        title = item.get("title") or ""
        if _is_loop_item(title, sk):
            lkey = _loop_key(title)
            if lkey not in seen_loop_keys or score > seen_loop_keys[lkey][0]:
                seen_loop_keys[lkey] = (score, item, sk)
        else:
            non_loop.append((score, item, sk))

    loop_deduped = list(seen_loop_keys.values())

    strategic_items = [(s, it, sk) for s, it, sk in non_loop if sk in STRATEGIC_SECTIONS]
    other_items = [(s, it, sk) for s, it, sk in non_loop if sk not in STRATEGIC_SECTIONS]

    # Sort each pool by score descending
    loop_deduped.sort(key=lambda x: -x[0])
    strategic_items.sort(key=lambda x: -x[0])
    other_items.sort(key=lambda x: -x[0])

    result: list[tuple[int, dict, str]] = []

    # Guarantee strategic slots first
    strategic_slots = min(STRATEGIC_GUARANTEED_SLOTS, len(strategic_items))
    result.extend(strategic_items[:strategic_slots])

    # Fill remaining slots, respecting loop cap
    loop_used = 0
    pool = sorted(
        loop_deduped + strategic_items[strategic_slots:] + other_items,
        key=lambda x: -x[0],
    )
    for item_tuple in pool:
        if len(result) >= n:
            break
        score, item, sk = item_tuple
        if item_tuple in result:
            continue
        title = item.get("title") or ""
        if _is_loop_item(title, sk):
            if loop_used >= LOOP_TOP5_CAP:
                continue
            loop_used += 1
        result.append(item_tuple)

    # Final dedup by title (handles any edge cases)
    seen: set[str] = set()
    deduped: list[tuple[int, dict, str]] = []
    for item_tuple in result:
        title = item_tuple[1].get("title") or ""
        if title not in seen:
            deduped.append(item_tuple)
            seen.add(title)

    return deduped[:n]


# ---------------------------------------------------------------------------
# Relationship domain classification (DEFECT-016-B)
# ---------------------------------------------------------------------------

# Domains that should be excluded from professional momentum scoring
EXCLUDED_FROM_PROFESSIONAL_MOMENTUM = {"family", "personal"}

# Known family relationship indicators for contacts without explicit domain field
_FAMILY_SURNAMES = {"vahlsing"}
_FAMILY_TAGS = {"family", "spouse", "sibling", "parent", "child", "relative"}


def infer_relationship_domain(contact: dict) -> str:
    """Infer the relationship domain of a baseline contact.

    Checks (in order):
      1. Explicit relationship_domain field on the contact.
      2. Tags containing family indicators.
      3. Surname match against known family surnames.
      4. Default: 'professional'

    Returns one of: family | personal | professional
    """
    # Explicit field wins
    domain = (contact.get("relationship_domain") or "").strip().lower()
    if domain in ("family", "personal", "professional", "opportunity", "industry", "vendor", "customer"):
        return domain

    # Tags
    tags = [t.lower() for t in (contact.get("tags") or [])]
    if any(t in _FAMILY_TAGS for t in tags):
        return "family"

    # Surname heuristic
    name = (contact.get("name") or "").lower()
    name_parts = name.split()
    if name_parts:
        last = name_parts[-1]
        if last in _FAMILY_SURNAMES:
            return "family"

    return "professional"


def should_exclude_from_professional_momentum(contact: dict) -> bool:
    """Return True if the contact should be excluded from professional momentum scoring."""
    domain = infer_relationship_domain(contact)
    return domain in EXCLUDED_FROM_PROFESSIONAL_MOMENTUM
