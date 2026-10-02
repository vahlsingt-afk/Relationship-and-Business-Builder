#!/usr/bin/env python3
"""
cos_synthesis.py — Chief of Staff Synthesis Engine (Sprints A, D, E, F, I, M)

Produces strategic narrative synthesis from already-computed brief sections.
This is NOT entity-frequency counting.  Each pattern answers a business question:

  Pattern 1 — Opportunity Unlock
    "What single action would unblock the most active opportunities?"

  Pattern 2 — Relationship-Opportunity Hot Zone
    "Who should I call today because a fresh signal + open deal just converged?"

  Pattern 3 — Decision Momentum
    "Which pending decision now has new evidence supporting or contradicting it?"

  Pattern 4 — Source Gap Impact
    "Which active opportunities are intelligence-dark due to declared source gaps?"

  Pattern 5 — Relationship Activation  (Sprint D)
    "Who in Todd's network should be contacted because of today's intelligence?"
    Uses contact_index.py to map company mentions → named contacts with context.

  Pattern 6 — Cross-Market Theme Convergence  (Sprint F)
    "What macro shift is being independently validated by 3+ companies today?"
    When 3+ distinct companies signal the same theme (labor, AI, delivery, pricing,
    etc.), that convergence is independent validation — not coincidence.

  Sprint E — Per-Item ORI Block
    attach_ori_blocks(items, sections) enriches each item with a structured
    Opportunity / Risk / Relationship block in extras["ori_block"].
    Only attached when at least one relationship is resolvable from the contact index.

  Sprint M — Relationship Intelligence Mutation Engine
    detect_relationship_mutations(sections, ci) scans observed relationship
    behavior for advocacy, sponsor, peer-trust, and risk signals.  When evidence
    exceeds a confidence threshold it generates proposed relationship graph
    mutations — without waiting for the user to ask.
    MEDIUM confidence → ask_todd (binary Y/N confirmation).
    HIGH confidence   → act_today (auto-apply recommended).

Called by _compute_connect_the_dots() in daily_brief.py after all section-level
computation is complete.  Returns a list of canonical_item dicts.

All outputs use the same canonical schema as daily_brief._canonical_item().
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

# Sprint D — Contact Intelligence Index.  Best-effort import.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from contact_index import ContactIndex as _ContactIndex
    _HAS_CONTACT_INDEX = True
except Exception:  # noqa: BLE001
    _HAS_CONTACT_INDEX = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _canonical_synth(
    *,
    title: str,
    summary: str,
    why_it_matters: str,
    recommended_action: str,
    disposition: str = "act_today",
    confidence: str = "high",
    source_refs: list[str] | None = None,
    extras: dict | None = None,
) -> dict:
    """Minimal canonical item compatible with daily_brief._canonical_item schema.

    Includes all required fields: autonomous_discovery_value, novelty_block,
    intelligence_lifecycle — so downstream tests that validate canonical items pass.
    """
    today = date.today().isoformat()
    # Synthesis items are always system-detected and new this cycle.
    adv = "high"  # cross-domain synthesis is inherently high autonomous discovery value
    return {
        "title": title,
        "summary": summary,
        "why_it_matters": why_it_matters,
        "recommended_action": recommended_action,
        "disposition": disposition,
        "grounding": "system_detected",
        "freshness": "fresh",
        "confidence": confidence,
        "source_refs": source_refs or [],
        "extras": extras or {},
        "intelligence_lifecycle": {
            "state": "NEW",
            "first_seen": today,
            "last_seen": today,
        },
        # Required by AD3 tests — all canonical items must have novelty + adv.
        "autonomous_discovery_value": adv,
        "novelty": {
            "is_new": True,
            "reason": "cross_domain_synthesis",
            "autonomous_discovery_value": adv,
            "source_discovered": True,
        },
    }


def _item_text(item: dict) -> str:
    """Concatenate title + summary for keyword matching."""
    return f"{item.get('title', '')} {item.get('summary', '')}".lower()


def _extras(item: dict) -> dict:
    return item.get("extras") or {}


# ---------------------------------------------------------------------------
# Pattern 1 — Opportunity Unlock
# ---------------------------------------------------------------------------
# Find 2+ ACTIVE or WAITING opportunities that share the same blocking keyword.
# Blocking keywords: "awaiting", "no response", "pending", "silent", "needs",
#                   "waiting", "follow up", "follow-up", "referral", "reference"
# When 2+ match the same cluster, the unlock message is strategic.

_BLOCK_KEYWORDS: list[tuple[str, str]] = [
    # (regex, human label)
    (r"\b(await|waiting|no response|silent|pending)\b", "awaiting a response"),
    (r"\b(follow.?up|follow up)\b", "needing a follow-up"),
    (r"\b(reference|referral)\b", "needing a reference or referral"),
    (r"\b(intro|introduction)\b", "needing an introduction"),
    (r"\b(proposal|deck|materials)\b", "awaiting materials or a proposal"),
]


def _opportunity_unlock(sections: dict) -> list[dict]:
    """Pattern 1: common blocking condition across 2+ opportunities."""
    opp_items = sections.get("opportunity_board") or []
    active_opps = [
        it for it in opp_items
        if _extras(it).get("state") in ("ACTIVE", "WAITING")
    ]
    if len(active_opps) < 2:
        return []

    results: list[dict] = []
    for pattern_re, label in _BLOCK_KEYWORDS:
        matched = [
            it for it in active_opps
            if re.search(pattern_re, _item_text(it), re.IGNORECASE)
        ]
        if len(matched) < 2:
            continue

        companies = [_extras(it).get("companies") or it.get("title", "?") for it in matched]
        companies_str = ", ".join(c for c in companies[:4] if c)
        n = len(matched)

        results.append(_canonical_synth(
            title=f"[UNLOCK] {n} opportunities are blocked by the same condition: {label}",
            summary=(
                f"{n} active opportunities ({companies_str}) share the same blocking condition: "
                f"{label}. Resolving this one constraint would advance all {n} simultaneously."
            ),
            why_it_matters=(
                f"When multiple opportunities share a blocking condition, addressing that condition "
                f"has {n}x leverage. This is your highest ROI action in the opportunity pipeline today."
            ),
            recommended_action=(
                f"Identify the single action that unblocks {label} — "
                f"then apply it to all {n} opportunities ({companies_str[:80]}) before end of day."
            ),
            disposition="act_today",
            confidence="high",
            source_refs=["opportunity_board"],
            extras={
                "convergence_type": "opportunity_unlock",
                "block_pattern": label,
                "companies": companies_str,
                "opportunity_count": n,
            },
        ))

    # Deduplicate: keep only the highest-count pattern per blocking type
    seen: set[str] = set()
    deduped: list[dict] = []
    for item in sorted(results, key=lambda x: -(x["extras"].get("opportunity_count", 0))):
        key = item["extras"].get("block_pattern", "")
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped[:2]  # cap at 2 unlock items per cycle


# ---------------------------------------------------------------------------
# Pattern 2 — Relationship-Opportunity Hot Zone
# ---------------------------------------------------------------------------
# Find a contact who appears in BOTH:
#   (a) last_24h_relationship_signals or email_intelligence_harvest (fresh signal)
#   (b) opportunity_board extras.companies or active_threads contact names
# When found: "Call [name] today — fresh signal + open deal."

def _relationship_opportunity_hotzone(sections: dict) -> list[dict]:
    """Pattern 2: contacts with a fresh signal AND an active open opportunity."""
    fresh_signal_items = list(
        (sections.get("last_24h_relationship_signals") or [])
        + (sections.get("email_intelligence_harvest") or [])
    )
    opp_items = [
        it for it in (sections.get("opportunity_board") or [])
        if _extras(it).get("state") in ("ACTIVE", "WAITING")
    ]

    if not fresh_signal_items or not opp_items:
        return []

    # Build a set of lowercase contact names / companies from opportunity board.
    # Split both on separators (,/&) and on spaces so "Acme Corp" → tokens "acme" + "corp".
    # Only keep tokens > 4 chars to avoid false positives on common words.
    opp_entity_words: dict[str, str] = {}  # normalized_token → display_name (company)
    _STOP_WORDS = {"corp", "inc", "llc", "ltd", "the", "and", "group", "foods",
                   "tech", "brand", "brands", "company", "companies", "restaurant"}
    for opp in opp_items:
        companies_raw = _extras(opp).get("companies") or ""
        for part in re.split(r"[,/&]", companies_raw):
            display = part.strip()
            # Index both the whole company name and individual significant tokens
            whole = display.lower()
            if len(whole) > 4:
                opp_entity_words[whole] = display
            for token in re.split(r"\s+", whole):
                if len(token) >= 4 and token not in _STOP_WORDS:
                    opp_entity_words[token] = display

    results: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()

    for sig_item in fresh_signal_items:
        sig_text = _item_text(sig_item)
        sig_extras = _extras(sig_item)
        contact_name = (
            sig_extras.get("contact_name")
            or sig_extras.get("party")
            or sig_item.get("title", "")
        )
        contact_lower = contact_name.lower()

        # Check if any opp entity word appears in the signal text or contact name
        for opp_word, opp_display in opp_entity_words.items():
            if opp_word in sig_text or opp_word in contact_lower:
                pair = (contact_lower[:40], opp_word)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)

                # Find the matching opp item for its title / next step
                matching_opp = next(
                    (o for o in opp_items if opp_word in _item_text(o).lower()),
                    None,
                )
                opp_title = (matching_opp or {}).get("title", opp_display)
                opp_state = _extras(matching_opp or {}).get("state", "ACTIVE")

                results.append(_canonical_synth(
                    title=f"[HOT ZONE] {contact_name} — fresh signal + {opp_state} deal converged",
                    summary=(
                        f"{contact_name} generated a fresh signal today AND has an active opportunity "
                        f"in your pipeline ({opp_title}). "
                        f"This is your highest-leverage outreach window — the signal gives you a natural reason to connect."
                    ),
                    why_it_matters=(
                        "Outreach is most effective when it feels timely rather than transactional. "
                        "A fresh signal from a contact with an open deal is a rare convergence — use it today."
                    ),
                    recommended_action=(
                        f"Contact {contact_name} today. Reference the fresh signal as context. "
                        f"Advance the {opp_title} opportunity in the same conversation if natural."
                    ),
                    disposition="act_today",
                    confidence="high",
                    source_refs=["last_24h_relationship_signals", "opportunity_board"],
                    extras={
                        "convergence_type": "relationship_opportunity_hotzone",
                        "contact": contact_name,
                        "opportunity": opp_title,
                        "opp_state": opp_state,
                    },
                ))
                break  # one hit per signal item

    return results[:3]  # cap at 3 hotzone items


# ---------------------------------------------------------------------------
# Pattern 3 — Decision Momentum
# ---------------------------------------------------------------------------
# Find decision_queue items where the same contact/company also appears in
# new_intelligence_today, email_intelligence_harvest, or last_24h_relationship_signals.
# When found: "Decision about X now has new evidence."

def _decision_momentum(sections: dict) -> list[dict]:
    """Pattern 3: pending decisions that now have new supporting/contradicting evidence."""
    pending_decisions = sections.get("decision_queue") or []
    new_intelligence = list(
        (sections.get("new_intelligence_today") or [])
        + (sections.get("email_intelligence_harvest") or [])
        + (sections.get("last_24h_relationship_signals") or [])
    )

    if not pending_decisions or not new_intelligence:
        return []

    results: list[dict] = []
    seen_decisions: set[str] = set()

    for decision in pending_decisions[:10]:  # scan first 10 pending decisions
        dec_title = decision.get("title") or ""
        if not dec_title or dec_title in seen_decisions:
            continue

        # Extract keywords from decision title (words >4 chars)
        dec_words = {w.lower() for w in re.findall(r'\b\w{4,}\b', dec_title)}
        if not dec_words:
            continue

        # Find new intelligence items that share keywords with this decision
        supporting: list[dict] = []
        for intel_item in new_intelligence:
            intel_text = _item_text(intel_item)
            overlap = sum(1 for w in dec_words if w in intel_text)
            if overlap >= 2:  # at least 2 keyword overlaps = likely related
                supporting.append(intel_item)

        if not supporting:
            continue

        seen_decisions.add(dec_title)
        intel_titles = "; ".join(
            (it.get("title") or "")[:60] for it in supporting[:2]
        )

        results.append(_canonical_synth(
            title=f"[DECISION SIGNAL] New evidence for pending decision: {dec_title[:80]}",
            summary=(
                f"A pending decision — '{dec_title[:100]}' — now has new intelligence that may "
                f"inform it: {intel_titles}. "
                f"Review the new evidence before the decision window closes."
            ),
            why_it_matters=(
                "Pending decisions become more expensive the longer they sit. "
                "New evidence that arrives while a decision is open is your signal to act now — "
                "the information cost of waiting has decreased."
            ),
            recommended_action=(
                # RB-DEFECT-2026-07-10d: intel_titles is already 2 titles each
                # truncated to 60 chars ("; "-joined, up to ~122 chars) -- a
                # second [:80] slice here cut it again mid-word with no
                # ellipsis ("...cargo ships taking US-backed Hormuz; Microsoft
                # is doing"). Use it as already sized instead of re-truncating.
                f"Review the new evidence ({intel_titles}), then make the call on: {dec_title[:80]}. "
                "Do not defer again without a specific new reason."
            ),
            disposition="act_today",
            confidence="medium",
            source_refs=["decision_queue", "new_intelligence_today"],
            extras={
                "convergence_type": "decision_momentum",
                "decision": dec_title,
                "supporting_signals": [it.get("title") for it in supporting[:3]],
            },
        ))

    return results[:2]  # cap at 2 momentum items


# ---------------------------------------------------------------------------
# Pattern 4 — Source Gap Impact
# ---------------------------------------------------------------------------
# Use source_gap_declarations to identify which active opportunities are
# intelligence-dark.  If LinkedIn is down and 3 opps live there, say so.

def _source_gap_impact(sections: dict) -> list[dict]:
    """Pattern 4: which active opportunities are affected by declared source gaps."""
    gap_declarations = sections.get("source_gap_declarations") or []
    if not gap_declarations:
        return []

    active_opps = [
        it for it in (sections.get("opportunity_board") or [])
        if _extras(it).get("state") in ("ACTIVE", "WAITING")
    ]
    if not active_opps:
        return []

    results: list[dict] = []

    # Map gap types to the domains they affect
    gap_domain_map = {
        "LINKEDIN GAP": "relationship intelligence, contact research, and professional network signals",
        "EMAIL GAP": "email-based communication signals and relationship touchpoint data",
        "CALENDAR GAP": "calendar-based scheduling signals and meeting intelligence",
        "MARKET GAP": "market signals, industry news, and competitive intelligence",
        "WEB GAP": "web-scraped industry content and public market signals",
    }

    for gap_item in gap_declarations:
        gap_title = gap_item.get("title") or ""
        # Identify gap type from title
        gap_type = next(
            (k for k in gap_domain_map if k in gap_title.upper()),
            None,
        )
        if not gap_type:
            continue

        gap_domain = gap_domain_map[gap_type]
        n_opps = len(active_opps)
        companies = ", ".join(
            _extras(o).get("companies") or o.get("title", "?")
            for o in active_opps[:4]
        )

        results.append(_canonical_synth(
            title=f"[GAP IMPACT] {gap_type}: {n_opps} active opportunities are intelligence-dark",
            summary=(
                f"{gap_type} is declared. This means {n_opps} active opportunities ({companies}) "
                f"are missing {gap_domain}. "
                f"Any intelligence claims about these opportunities should be treated as [STALE] or [MEMORY]."
            ),
            why_it_matters=(
                f"When {gap_type.lower()} is unavailable, you cannot verify contact status, "
                f"company movement, or relationship health for your active deals. "
                f"You may be acting on weeks-old data without knowing it."
            ),
            recommended_action=(
                f"Acknowledge that {n_opps} opportunities ({companies[:80]}) currently lack "
                f"{gap_domain}. Prioritize restoring this source or use direct outreach to fill the gap."
            ),
            disposition="monitor",
            confidence="high",
            source_refs=["source_gap_declarations", "opportunity_board"],
            extras={
                "convergence_type": "source_gap_impact",
                "gap_type": gap_type,
                "affected_opportunities": [_extras(o).get("companies") for o in active_opps[:5]],
                "opportunity_count": n_opps,
            },
        ))

    return results[:2]  # cap at 2 gap impact items


# ---------------------------------------------------------------------------
# Pattern 5 — Relationship Activation  (Sprint D)
# ---------------------------------------------------------------------------
# For today's significant intelligence items, cross-reference the contact index
# to find named contacts at mentioned companies.  Produce outreach suggestions
# with full context: why this contact, what to discuss, open loop status.

def _relationship_activation(sections: dict) -> list[dict]:
    """Pattern 5: named contact outreach suggestions from today's intelligence.

    Requires contact_index.py (Sprint D).  Silent no-op if index is unavailable.
    """
    if not _HAS_CONTACT_INDEX:
        return []

    try:
        ci = _ContactIndex.load()
    except Exception:  # noqa: BLE001
        return []

    # Gather significant intelligence items to cross-reference
    intelligence_items = list(
        (sections.get("restaurant_industry_headlines") or [])[:20]
        + (sections.get("condensed_industry_context") or [])[:10]
        + (sections.get("new_intelligence_today") or [])[:10]
        + (sections.get("watchlist_intelligence") or [])[:5]
    )

    # Collect company names mentioned in today's intelligence
    company_mentions: dict[str, list[dict]] = {}  # company_lower → [intel_items]
    for item in intelligence_items:
        entities = (_extras(item).get("entities") or [])
        if isinstance(entities, str):
            entities = [entities]
        # Also pull company references from item text
        text = _item_text(item)
        for ent in entities:
            if ent and len(ent) > 3:
                company_mentions.setdefault(ent.lower(), [])
                company_mentions[ent.lower()].append(item)

    if not company_mentions:
        return []

    results: list[dict] = []
    seen_contact_ids: set[str] = set()

    for company_lower, intel_items in company_mentions.items():
        contacts = ci.by_company(company_lower)
        for contact in contacts[:2]:  # max 2 contacts per company mention
            if contact["id"] in seen_contact_ids:
                continue
            if contact.get("loop_count", 0) == 0:
                continue  # only surface contacts we have an actual relationship with
            seen_contact_ids.add(contact["id"])

            contact_name = contact["name"]
            company = contact["company"] or company_lower.title()
            open_loops = contact.get("open_loop_count", 0)
            loop_context = contact.get("loop_context") or ""
            intel_title = intel_items[0].get("title", "")[:80]

            # Determine outreach reason
            if open_loops > 0:
                urgency = "act_today"
                open_note = f" You have {open_loops} open loop{'s' if open_loops > 1 else ''} with them: {loop_context[:80]}"
            else:
                urgency = "monitor"
                open_note = ""

            results.append(_canonical_synth(
                title=f"[RELATIONSHIP ACTIVATION] {contact_name} ({company}) — today's intelligence creates an outreach window",
                summary=(
                    f"Today's intelligence mentions {company}: '{intel_title}'. "
                    f"{contact_name} at {company} is in your active network.{open_note}"
                ),
                why_it_matters=(
                    f"Intelligence about {company} gives you a specific, timely, non-transactional "
                    f"reason to reach out to {contact_name}. "
                    "Outreach timed to relevant industry news has significantly higher response rates."
                ),
                recommended_action=(
                    f"Contact {contact_name} at {company}. "
                    f"Reference today's signal as context: '{intel_title[:60]}'. "
                    + (f"Also advance open loop: {loop_context[:80]}." if open_loops > 0 else
                       "Keep the relationship warm — this is intelligence they likely care about.")
                ),
                disposition=urgency,
                confidence="high" if open_loops > 0 else "medium",
                source_refs=["contact_index", "restaurant_industry_headlines"],
                extras={
                    "convergence_type": "relationship_activation",
                    "contact": contact_name,
                    "contact_id": contact["id"],
                    "company": company,
                    "intel_trigger": intel_title,
                    "open_loops": open_loops,
                    "loop_context": loop_context[:100],
                    "importance": contact.get("importance", "medium"),
                },
            ))

    # Sort: act_today first, then by contact importance
    _importance_rank = {"high": 0, "medium": 1, "low": 2}
    results.sort(key=lambda x: (
        0 if x["disposition"] == "act_today" else 1,
        _importance_rank.get(_extras(x).get("importance", "medium"), 1),
    ))
    return results[:4]  # cap at 4 activation items per cycle


# ---------------------------------------------------------------------------
# Pattern 6 — Cross-Market Theme Convergence  (Sprint F)
# ---------------------------------------------------------------------------
# When 3+ distinct companies independently signal the same strategic theme today,
# that is independent validation of a macro shift — not coincidence.
#
# Algorithm:
#   1. Collect items from restaurant_industry_headlines + watchlist_intelligence
#      + condensed_industry_context.
#   2. For each item, extract the primary company (extras.entities[0] or title).
#   3. Map all keywords in the item text to canonical themes via _THEME_MAP.
#   4. For each theme, collect the distinct set of companies that mention it.
#   5. When ≥3 distinct companies mention the same theme → emit a CONVERGENCE item.
#   6. Cap at 2 convergence items per cycle (highest company count wins).

# Keyword → canonical theme mapping.
# Multiple keywords can map to the same theme so variant phrasing collapses correctly.
_THEME_MAP: dict[str, str] = {
    # Labor & workforce
    "labor": "labor cost pressure",
    "wage": "labor cost pressure",
    "wages": "labor cost pressure",
    "staffing": "labor cost pressure",
    "hiring": "labor cost pressure",
    "layoff": "labor cost pressure",
    "headcount": "labor cost pressure",
    "workforce": "labor cost pressure",
    "turnover": "labor cost pressure",
    "minimum wage": "labor cost pressure",
    # Delivery & off-premise
    "delivery": "off-premise growth",
    "off-premise": "off-premise growth",
    "catering": "off-premise growth",
    "drive-thru": "off-premise growth",
    "drive thru": "off-premise growth",
    "pickup": "off-premise growth",
    "online order": "off-premise growth",
    "digital order": "off-premise growth",
    # AI & automation
    "artificial intelligence": "AI and automation",
    " ai ": "AI and automation",
    "automation": "AI and automation",
    "robot": "AI and automation",
    "automated": "AI and automation",
    "machine learning": "AI and automation",
    "kiosk": "AI and automation",
    # Pricing & inflation
    "pricing": "menu pricing pressure",
    "price increase": "menu pricing pressure",
    "menu price": "menu pricing pressure",
    "inflation": "menu pricing pressure",
    "cost pressure": "menu pricing pressure",
    "commodity": "menu pricing pressure",
    "food cost": "menu pricing pressure",
    # Technology & digital
    "point of sale": "restaurant technology investment",
    "pos ": "restaurant technology investment",
    "loyalty": "restaurant technology investment",
    "mobile app": "restaurant technology investment",
    "digital platform": "restaurant technology investment",
    "cloud": "restaurant technology investment",
    "saas": "restaurant technology investment",
    "payments": "restaurant technology investment",
    "fintech": "restaurant technology investment",
    # Expansion & growth
    "expansion": "unit growth momentum",
    "new location": "unit growth momentum",
    "new unit": "unit growth momentum",
    "franchise": "unit growth momentum",
    "franchisee": "unit growth momentum",
    "opening": "unit growth momentum",
    "development": "unit growth momentum",
    # Consumer & traffic
    "traffic": "consumer traffic pressure",
    "same-store": "consumer traffic pressure",
    "comparable": "consumer traffic pressure",
    "guest count": "consumer traffic pressure",
    "foot traffic": "consumer traffic pressure",
    "consumer spending": "consumer traffic pressure",
    "value": "consumer traffic pressure",
    # Private equity & M&A
    "acquisition": "M&A and capital activity",
    "merger": "M&A and capital activity",
    "private equity": "M&A and capital activity",
    "investment": "M&A and capital activity",
    "funding": "M&A and capital activity",
    "ipo": "M&A and capital activity",
    "raises": "M&A and capital activity",
    "capital": "M&A and capital activity",
    # Sustainability
    "sustainability": "sustainability and ESG pressure",
    "carbon": "sustainability and ESG pressure",
    "esg": "sustainability and ESG pressure",
    "renewable": "sustainability and ESG pressure",
    "plant-based": "sustainability and ESG pressure",
}


def _extract_company_from_item(item: dict) -> str | None:
    """Extract the primary company name from an item's entities or title."""
    entities = (_extras(item).get("entities") or [])
    if isinstance(entities, str):
        entities = [entities]
    entities = [e for e in entities if e and len(e) > 2]
    if entities:
        return entities[0]
    # Fall back: first capitalized word sequence from title
    title = item.get("title") or ""
    m = re.search(r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)\b", title)
    return m.group(1) if m else None


def _themes_in_text(text: str) -> set[str]:
    """Return the set of canonical themes mentioned in text."""
    text_lower = f" {text.lower()} "
    found: set[str] = set()
    for keyword, theme in _THEME_MAP.items():
        if keyword in text_lower:
            found.add(theme)
    return found


def _cross_company_theme(sections: dict) -> list[dict]:
    """Pattern 6: 3+ distinct companies independently signal the same theme today."""
    # Gather candidate items from high-signal sections
    candidate_items = list(
        (sections.get("restaurant_industry_headlines") or [])[:30]
        + (sections.get("watchlist_intelligence") or [])[:15]
        + (sections.get("condensed_industry_context") or [])[:15]
        + (sections.get("new_intelligence_today") or [])[:10]
    )
    if len(candidate_items) < 3:
        return []

    # theme → {company_lower: [item, ...]}
    theme_companies: dict[str, dict[str, list[dict]]] = {}

    for item in candidate_items:
        company = _extract_company_from_item(item)
        if not company or len(company) < 2:
            continue
        company_lower = company.lower()
        text = _item_text(item)
        for theme in _themes_in_text(text):
            theme_companies.setdefault(theme, {}).setdefault(company_lower, []).append(item)

    # Score themes: distinct company count
    scored: list[tuple[int, str, dict[str, list[dict]]]] = []
    for theme, company_map in theme_companies.items():
        n_companies = len(company_map)
        if n_companies >= 3:
            scored.append((n_companies, theme, company_map))

    # Sort by company count descending
    scored.sort(key=lambda x: -x[0])

    results: list[dict] = []
    seen_themes: set[str] = set()

    for n_companies, theme, company_map in scored:
        if theme in seen_themes:
            continue
        seen_themes.add(theme)

        companies = sorted(company_map.keys())
        companies_display = ", ".join(c.title() for c in companies[:5])
        if n_companies > 5:
            companies_display += f" (+{n_companies - 5} more)"

        # Collect representative headlines
        sample_titles: list[str] = []
        for co_items in list(company_map.values())[:3]:
            if co_items:
                sample_titles.append((co_items[0].get("title") or "")[:70])
        evidence_str = " | ".join(t for t in sample_titles if t)

        results.append(_canonical_synth(
            title=(
                f"[CONVERGENCE] Independent validation of '{theme}': "
                f"{n_companies} companies signal the same shift today"
            ),
            summary=(
                f"{n_companies} independent companies ({companies_display}) all signal "
                f"'{theme}' in today's intelligence. "
                f"Sample signals: {evidence_str}. "
                f"When this many operators independently report the same theme, it is a "
                f"confirmed macro shift — not noise."
            ),
            why_it_matters=(
                f"Cross-company convergence on '{theme}' means this is a structural trend, "
                f"not a single-company story. "
                f"Any restaurant technology or relationship strategy that ignores '{theme}' "
                f"is now operating with an incomplete market map."
            ),
            recommended_action=(
                f"Treat '{theme}' as a confirmed market theme. "
                f"Review open opportunities and active relationships for '{theme}' exposure. "
                f"Consider whether this theme belongs in your strategic narrative for the next "
                f"board or client conversation."
            ),
            disposition="monitor",
            confidence="high",
            source_refs=["restaurant_industry_headlines", "watchlist_intelligence"],
            extras={
                "convergence_type": "cross_company_theme",
                "theme": theme,
                "companies": companies[:10],
                "company_count": n_companies,
                "sample_headlines": sample_titles[:3],
            },
        ))

    return results[:2]  # cap at 2 convergence items per cycle


# ---------------------------------------------------------------------------
# Sprint E — Per-Item ORI Block
# ---------------------------------------------------------------------------
# For each significant intelligence item, attach a structured
# Opportunity / Risk / Relationship block so GPT renders insight, not just facts.
#
# The block is placed in extras["ori_block"] and only attached when:
#   (a) the item has entity references, AND
#   (b) at least one named contact in the contact index maps to those entities.
#
# Structure:
#   ori_block = {
#     "opportunity":         str — what opening this item creates,
#     "risk":                str — what threat this item signals,
#     "relationships":       list[{name, company, open_loops, loop_context, action}],
#     "confidence":          str — high|medium|low,
#     "recommended_action":  str — single top action,
#   }

_OPP_SIGNAL_WORDS = frozenset({
    "raises", "funding", "partnership", "expansion", "launch", "hires", "acquires",
    "acqui", "merger", "growth", "wins", "contract", "award", "deal", "upgrade",
    "invest", "capital", "scale", "opening", "new market",
})
_RISK_SIGNAL_WORDS = frozenset({
    "layoff", "cuts", "bankruptcy", "lawsuit", "regulator", "fine", "breach",
    "outage", "recall", "scandal", "decline", "miss", "fraud", "pivot",
    "restructure", "loss", "debt", "downturn", "headcount", "shrink",
})


def _ori_opportunity(item: dict, relationships: list[dict]) -> str:
    """Infer the opportunity this item creates."""
    text = _item_text(item)
    entities = (_extras(item).get("entities") or [])
    company = (entities[0] if entities else "") or ""

    # Check for explicit opportunity signals
    for word in _OPP_SIGNAL_WORDS:
        if word in text:
            if relationships:
                names = ", ".join(r["name"] for r in relationships[:2])
                return (
                    f"{company} activity ({word}) creates a timely, specific opening to engage "
                    f"{names} — use today's news as the conversation anchor."
                )
            return (
                f"{company} activity ({word}) may open new commercial or relationship conversations. "
                f"Monitor for direct outreach opportunities."
            )
    # Default
    if relationships:
        names = ", ".join(r["name"] for r in relationships[:2])
        return (
            f"Today's intelligence about {company} gives you a non-transactional reason "
            f"to connect with {names}."
        )
    return f"Monitor {company} for follow-on signals that create direct engagement opportunities."


def _ori_risk(item: dict) -> str:
    """Infer the risk this item signals."""
    text = _item_text(item)
    entities = (_extras(item).get("entities") or [])
    company = (entities[0] if entities else "") or ""

    for word in _RISK_SIGNAL_WORDS:
        if word in text:
            return (
                f"{company} shows a distress signal ({word}). Contacts at this company may be "
                f"affected — validate relationship status before advancing any open loops."
            )
    return (
        f"No direct risk signal detected for {company} in this item. "
        f"Continue monitoring for adverse developments."
    )


def _ori_contact_action(item: dict, contact: dict) -> str:
    """Generate a specific action for this contact given this intel item."""
    open_loops = contact.get("open_loop_count", 0)
    loop_ctx = (contact.get("loop_context") or "")[:80]
    intel_title = (item.get("title") or "")[:60]
    name = contact["name"]
    if open_loops > 0:
        return (
            f"Forward intel to {name} or reference it in outreach — "
            f"then advance open loop: {loop_ctx}"
        )
    return (
        f"Share today's signal with {name}: '{intel_title}'. "
        f"Keep the relationship warm — no open loop active."
    )


def _build_ori_block(item: dict, ci: Any | None) -> dict | None:
    """Build the ORI block for a single item.  Returns None if no contacts resolve."""
    entities = (_extras(item).get("entities") or [])
    if isinstance(entities, str):
        entities = [entities]
    entities = [e for e in entities if e and len(e) > 3]

    relationships: list[dict] = []
    if ci and entities:
        seen_ids: set[str] = set()
        for ent in entities[:4]:
            for contact in ci.by_company(ent)[:2]:
                cid = contact.get("id", "")
                if cid in seen_ids:
                    continue
                if contact.get("loop_count", 0) == 0:
                    continue  # only contacts we actually know
                seen_ids.add(cid)
                relationships.append({
                    "name": contact["name"],
                    "company": contact.get("company", ent),
                    "open_loops": contact.get("open_loop_count", 0),
                    "loop_context": (contact.get("loop_context") or "")[:80],
                    "action": _ori_contact_action(item, contact),
                })

    if not relationships:
        return None  # no contact context → skip ORI block

    confidence = item.get("confidence", "medium")
    top_action: str
    if relationships:
        top_contact = sorted(
            relationships,
            key=lambda r: (0 if r["open_loops"] > 0 else 1, -r["open_loops"]),
        )[0]
        top_action = top_contact["action"]
    else:
        top_action = item.get("recommended_action", "Monitor and assess.")

    return {
        "opportunity": _ori_opportunity(item, relationships),
        "risk": _ori_risk(item),
        "relationships": relationships[:3],
        "confidence": confidence,
        "recommended_action": top_action,
    }


def attach_decision_layers(items: list[dict], section_name: str = "") -> list[dict]:
    """Sprint I: attach a decision field to each act_today item.

    The decision field names the explicit binary (or small-set) choice the
    user must make.  It is distinct from recommended_action — it frames the
    decision, not the action.

    Format: "[Option A], OR [Option B with consequence]."

    Only act_today items receive a decision field.  Items that already have
    one are not overwritten (idempotent).  Silent no-op on all errors.
    """
    for item in items:
        if item.get("disposition") != "act_today":
            continue
        if "decision" in item:
            continue  # already set — idempotent
        try:
            item["decision"] = _infer_decision(item, section_name)
        except Exception:  # noqa: BLE001
            pass
    return items


def _infer_decision(item: dict, section_name: str = "") -> str:
    """Infer the binary decision for a single act_today item."""
    extras = item.get("extras") or {}
    convergence_type = extras.get("convergence_type", "")
    title = (item.get("title") or "").lower()
    contact = extras.get("contact", "")

    # ── Synthesis patterns (convergence_type present) ────────────────────────
    if convergence_type == "relationship_activation":
        open_loops = extras.get("open_loops", 0)
        company = extras.get("company", "")
        if open_loops > 0:
            return (
                f"Contact {contact} today using today's {company} signal as the opening "
                f"and advance the open loop, "
                f"OR let another day pass and lose the timeliness advantage."
            )
        return (
            f"Reach out to {contact} while today's signal about {company} is current, "
            f"OR defer and accept that the outreach window will close."
        )

    if convergence_type == "relationship_opportunity_hotzone":
        opportunity = extras.get("opportunity", "the open deal")
        return (
            f"Contact {contact} today — the fresh signal gives you a natural opening "
            f"to advance {opportunity}, "
            f"OR document explicitly why you are passing on this convergence window."
        )

    if convergence_type == "opportunity_unlock":
        block = extras.get("block_pattern", "the blocking condition")
        n = extras.get("opportunity_count", 2)
        return (
            f"Resolve '{block}' today across all {n} affected opportunities simultaneously, "
            f"OR identify the specific constraint preventing resolution and set a date."
        )

    if convergence_type == "decision_momentum":
        pending = (extras.get("decision") or item.get("title") or "")[:80]
        return (
            f"Make the call on '{pending}' today — new evidence has arrived and the cost "
            f"of waiting has decreased, "
            f"OR formally defer with a specific new reason and a commit date."
        )

    if convergence_type == "source_gap_impact":
        gap = extras.get("gap_type", "the missing source")
        return (
            f"Restore {gap} now OR use direct outreach to compensate for the blind spot "
            f"before acting on any opportunity intelligence that depends on it."
        )

    if convergence_type == "cross_company_theme":
        theme = extras.get("theme", "this market theme")
        return (
            f"Incorporate '{theme}' into your active positioning and next stakeholder "
            f"conversation, "
            f"OR document why it is not relevant to your current pipeline and close the signal."
        )

    # ── Earnings Intelligence (Sprint G) ────────────────────────────────────
    earnings_type = extras.get("earnings_type", "")
    if earnings_type == "pre_earnings_alert":
        company = extras.get("company", "")
        days = extras.get("days_until_report", "?")
        return (
            f"Prepare your {company} earnings watch list in the next {days} days "
            f"and review all open relationship/opportunity threads at {company}, "
            f"OR acknowledge this earnings cycle is not a priority and skip preparation."
        )

    # ── Section-based inference (no convergence_type) ────────────────────────
    if section_name == "opportunity_board":
        state = extras.get("state", "ACTIVE")
        companies = extras.get("companies") or item.get("title", "this opportunity")
        if state == "WAITING":
            return (
                f"Follow up today to end the waiting state on {companies}, "
                f"OR close the opportunity if it has been silent for too long."
            )
        return (
            f"Take a specific next action on {companies} today to advance it, "
            f"OR move it to WAITING with a defined trigger and timeline."
        )

    if section_name in ("last_24h_relationship_signals", "relationship_operational_signal_review"):
        return (
            "Act on this relationship signal today while it is fresh, "
            "OR mark it as noted and move on — do not let it age without a decision."
        )

    if section_name == "five_things_today":
        return (
            "Complete this today as stated, "
            "OR make an explicit decision to defer — with a specific date and reason — "
            "and accept the consequence."
        )

    # ── Generic act_today fallback ────────────────────────────────────────────
    return (
        "Act on this today, "
        "OR make an explicit decision to defer with a specific date and documented reason."
    )


def attach_ori_blocks(items: list[dict], sections: dict) -> list[dict]:  # noqa: ARG001
    """Sprint E: attach ori_block to each item that has resolvable contact context.

    Modifies items in-place.  Returns the same list for chaining.
    Called from daily_brief.py on restaurant_industry_headlines,
    opportunity_board, last_24h_relationship_signals after each is computed.
    """
    if not _HAS_CONTACT_INDEX:
        return items

    try:
        ci = _ContactIndex.load()
    except Exception:  # noqa: BLE001
        return items

    for item in items:
        # Skip items that already have an ori_block (e.g. synthesis items)
        existing_extras = item.get("extras") or {}
        if "ori_block" in existing_extras:
            continue
        block = _build_ori_block(item, ci)
        if block:
            if "extras" not in item or item["extras"] is None:
                item["extras"] = {}
            item["extras"]["ori_block"] = block

    return items


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sprint M — Relationship Intelligence Mutation Engine
# ---------------------------------------------------------------------------

# Signal pattern definitions — (pattern, points, signal_type)
# Patterns are matched against lowercased combined text of title + summary +
# why_it_matters + recommended_action for each relationship item.
_RI_SIGNAL_PATTERNS: list[tuple[str, int, str]] = [
    # ── Sponsor signals (highest weight — invests reputation capital) ──────
    (r"creat(?:ed|ing)?\s+(?:a\s+)?(?:role|position|opportunity)\s+for", 20, "sponsor"),
    (r"vouching\s+for", 20, "sponsor"),
    (r"(?:backing|sponsoring)\s+(?:you|your)", 20, "sponsor"),
    (r"recommend(?:ed|ing|s)?\s+you\s+to", 20, "sponsor"),
    (r"put(?:ting)?\s+(?:you|your\s+name)\s+in\s+front", 20, "sponsor"),
    (r"influenc(?:ed|ing)?\s+(?:the\s+)?(?:hiring|decision)", 20, "sponsor"),
    (r"brought\s+you\s+(?:in|into)", 18, "sponsor"),
    (r"execut(?:ive\s+)?sponsor", 20, "sponsor"),
    # ── Advocacy signals (medium-high weight — uses network on your behalf) ─
    (r"introduc(?:ed|ing|es|tion)\s+(?:you|to)", 15, "advocacy"),
    (r"referr(?:ed|ing|al)\s+(?:you|for)", 15, "advocacy"),
    (r"mention(?:ed|ing)?\s+(?:you|your\s+name)\s+to", 15, "advocacy"),
    (r"on\s+your\s+behalf", 15, "advocacy"),
    (r"shar(?:ed|ing)\s+(?:this|the\s+opportunity|the\s+role)\s+with\s+you", 15, "advocacy"),
    (r"pass(?:ed|ing)\s+(?:along|this)\s+(?:to\s+you|your\s+way)", 15, "advocacy"),
    (r"put\s+in\s+a\s+word", 15, "advocacy"),
    (r"advocat(?:ed|ing|es)\s+for\s+(?:you|your)", 15, "advocacy"),
    (r"champion(?:ed|ing|s)?\s+(?:you|your)", 15, "advocacy"),
    (r"promot(?:ed|ing|es|ion)\s+(?:you|your\s+work|your\s+candidacy)", 14, "advocacy"),
    # ── Active problem-solving (contextual advocacy — takes action) ──────────
    (r"who\s+did\s+you\s+speak\s+with", 12, "problem_solving"),
    (r"let\s+me\s+(?:see|check|look)\s+(?:what|who|if)", 12, "problem_solving"),
    (r"(?:identify|find)\s+(?:the\s+)?(?:right\s+)?(?:contact|connection|person)", 12, "problem_solving"),
    (r"leverage\s+(?:my\s+|our\s+)?(?:network|relationships|connections)", 12, "problem_solving"),
    (r"reach\s+out\s+(?:to\s+them|for\s+you|on\s+your\s+behalf)", 12, "problem_solving"),
    (r"(?:pull|call)\s+(?:in\s+)?(?:a\s+)?favor", 10, "problem_solving"),
    (r"went\s+to\s+bat\s+for", 14, "problem_solving"),
    # ── Peer / trust signals (relationship depth) ───────────────────────────
    (r"(?:inner\s+circle|trusted\s+peer|close\s+confidant)", 10, "peer_trust"),
    (r"(?:rely|count)\s+on\s+(?:you|each\s+other|them)", 10, "peer_trust"),
    (r"long.?(?:standing|term)\s+(?:trust|relationship|professional|colleague)", 10, "peer_trust"),
    (r"selected\s+(?:you|them)\s+(?:to\s+replace|as\s+(?:successor|replacement))", 14, "peer_trust"),
    (r"confidential(?:ly)?\s+shar(?:ed|ing)", 8, "peer_trust"),
    (r"trusted\s+(?:advisor|colleague|mentor)", 10, "peer_trust"),
    # ── Risk / deterioration signals (negative weight) ──────────────────────
    (r"gone\s+silent|radio\s+silence|ghosted", -12, "risk"),
    (r"no\s+(?:response|reply)\s+(?:in\s+\d+|for\s+\d+|since)", -10, "risk"),
    (r"deteriorat(?:ing|ed)\s+(?:relationship|engagement)", -12, "risk"),
    (r"broke(?:n)?\s+(?:the\s+)?(?:commitment|promise|trust)", -15, "risk"),
    (r"distanc(?:ed|ing)\s+(?:himself|herself|themselves|from)", -10, "risk"),
    (r"reduced\s+engagement|disengaged|dropped\s+contact", -10, "risk"),
]

# Classification mapping: dominant signal type + evidence score → proposed label
_CLASSIFICATION_MAP: list[tuple[str, int, str, list]] = [
    # (dominant_signal_type, min_score, proposed_classification, proposed_tags)
    ("sponsor",        35, "Tier 1 Sponsor",  ["sponsor", "tier1", "advocate"]),
    ("sponsor",        20, "Sponsor",          ["sponsor", "advocate"]),
    ("advocacy",       35, "Tier 1 Advocate",  ["advocate", "tier1"]),
    ("advocacy",       20, "Advocate",         ["advocate"]),
    ("problem_solving",30, "Active Advocate",  ["advocate"]),
    ("problem_solving",15, "Trusted Ally",     ["trusted_peer"]),
    ("peer_trust",     20, "Trusted Peer",     ["trusted_peer"]),
    ("risk",          -20, "At Risk — review engagement", ["at_risk"]),
    ("risk",          -10, "Cooling — monitor engagement", ["monitor_risk"]),
]

# Confidence thresholds
_CONF_HIGH   = 35   # act_today — auto-apply recommended
_CONF_MEDIUM = 18   # ask_todd  — user confirmation required
_CONF_LOW    = 8    # monitor   — log but do not surface as mutation


def _score_relationship_signals(text: str) -> dict:
    """Sprint M: score a text blob for relationship behavior signals.

    Returns a dict with keys:
      total_score  — int, sum of all matched signal points (may be negative)
      by_type      — dict[signal_type, int] — points per signal category
      matches      — list[tuple[str, int, str]] — (pattern_text, points, type)
      dominant     — str — signal type with highest absolute score
    """
    lower = text.lower()
    by_type: dict[str, int] = {}
    matches: list = []

    for pattern, points, sig_type in _RI_SIGNAL_PATTERNS:
        if re.search(pattern, lower):
            by_type[sig_type] = by_type.get(sig_type, 0) + points
            matches.append((pattern, points, sig_type))

    total = sum(by_type.values())

    # Dominant: highest absolute score among positive types; "risk" if only risk
    dominant = ""
    if by_type:
        pos_types = {k: v for k, v in by_type.items() if k != "risk" and v > 0}
        if pos_types:
            dominant = max(pos_types, key=lambda k: pos_types[k])
        elif "risk" in by_type:
            dominant = "risk"

    return {
        "total_score": total,
        "by_type": by_type,
        "matches": matches,
        "dominant": dominant,
    }


def _classify_mutation(dominant: str, total_score: int) -> tuple[str, list]:
    """Sprint M: map dominant signal type + score to proposed classification + tags."""
    for sig_type, min_score, classification, tags in _CLASSIFICATION_MAP:
        if dominant == sig_type:
            if total_score >= 0 and min_score >= 0 and total_score >= min_score:
                return classification, tags
            elif total_score < 0 and min_score < 0 and total_score <= min_score:
                return classification, tags
    # Default fallback
    if total_score >= _CONF_HIGH:
        return "High-Value Contact", ["high_value"]
    if total_score >= _CONF_MEDIUM:
        return "Notable Contact", ["notable"]
    return "", []


def _resolve_contacts_in_text(text: str, ci) -> list:
    """Sprint M: find known contacts whose names appear in the text.

    ci may be a ContactIndex instance or None (returns empty list).
    Returns list of contact dicts from the index.
    """
    if ci is None:
        return []
    lower = text.lower()
    contacts = getattr(ci, "_contacts", None) or []
    if not contacts and hasattr(ci, "all_contacts"):
        contacts = ci.all_contacts()
    matches = []
    seen_ids: set = set()
    for contact in contacts:
        name = contact.get("name") or ""
        if len(name) < 3:
            continue
        # Match full name or last name (must be ≥5 chars to avoid false positives)
        if name.lower() in lower:
            cid = contact.get("id") or name
            if cid not in seen_ids:
                matches.append(contact)
                seen_ids.add(cid)
        elif len(name.split()) > 1 and len(name.split()[-1]) >= 5:
            last = name.split()[-1].lower()
            # Only match last name if it appears as a word boundary
            if re.search(r'\b' + re.escape(last) + r'\b', lower):
                cid = contact.get("id") or name
                if cid not in seen_ids:
                    matches.append(contact)
                    seen_ids.add(cid)
    return matches


def _build_mutation_item(contact: dict, evidence: dict,
                         proposed_classification: str, proposed_tags: list,
                         source_item_title: str, section_name: str) -> dict:
    """Sprint M: build a canonical mutation proposal item."""
    total = evidence["total_score"]
    dominant = evidence["dominant"]
    by_type = evidence["by_type"]

    confidence_level = (
        "HIGH" if total >= _CONF_HIGH
        else "MEDIUM" if total >= _CONF_MEDIUM
        else "LOW"
    )
    disposition = "act_today" if confidence_level == "HIGH" else "ask_todd"
    auto_apply = confidence_level == "HIGH"

    contact_name = contact.get("name") or "Unknown Contact"
    current_importance = contact.get("importance") or "unknown"
    company = contact.get("company") or ""

    # Evidence narrative
    sig_bullets = []
    for sig_type, pts in sorted(by_type.items(), key=lambda kv: -abs(kv[1])):
        if pts != 0:
            label = sig_type.replace("_", " ").title()
            sig_bullets.append(f"{label} (+{pts}pts)" if pts > 0 else f"{label} ({pts}pts)")
    evidence_summary = "; ".join(sig_bullets) or "behavioral signals observed"

    company_blurb = f" ({company})" if company else ""

    return {
        "title": f"⚡ Proposed Mutation: {contact_name} → {proposed_classification}",
        "summary": (
            f"Observed behavior in '{source_item_title}' signals "
            f"{proposed_classification.lower()} relationship with {contact_name}{company_blurb}. "
            f"Evidence score: {total}. Dominant signal: {dominant.replace('_', ' ')}."
        ),
        "why_it_matters": (
            f"{contact_name}{company_blurb} demonstrated {dominant.replace('_', ' ')} behavior "
            f"that exceeds the threshold for relationship reclassification. "
            f"Current importance: {current_importance}. "
            f"Proposed: {proposed_classification}. "
            f"{'Auto-apply recommended.' if auto_apply else 'Confirmation required.'}"
        ),
        "recommended_action": (
            f"{'Apply mutation automatically: ' if auto_apply else 'Confirm mutation: '}"
            f"Upgrade {contact_name} to {proposed_classification}. "
            f"Tags to add: {', '.join(proposed_tags)}. "
            f"Evidence: {evidence_summary}."
        ),
        "disposition": disposition,
        "grounding": "system_detected",
        "freshness": "fresh",
        "confidence": confidence_level.lower(),
        "source_refs": [section_name],
        "extras": {
            "convergence_type": "relationship_mutation",
            "mutation_type": "classification_upgrade" if total > 0 else "classification_downgrade",
            "contact_name": contact_name,
            "contact_id": contact.get("id") or "",
            "contact_company": company,
            "current_classification": current_importance,
            "proposed_classification": proposed_classification,
            "proposed_tags": proposed_tags,
            "signal_types": list(by_type.keys()),
            "evidence_summary": evidence_summary,
            "evidence_score": total,
            "confidence": confidence_level,
            "auto_apply": auto_apply,
            "source_section": section_name,
            "source_item_title": source_item_title,
        },
    }


# Sections to scan for relationship behavior signals (Sprint M).
_RI_MUTATION_SCAN_SECTIONS = (
    "last_24h_relationship_signals",
    "email_intelligence_harvest",
    "relationship_operational_signal_review",
    "new_intelligence_today",
    "reactivated_intelligence",
)


def detect_relationship_mutations(sections: dict, ci=None) -> list[dict]:
    """Sprint M: scan relationship sections for advocacy/sponsor/trust/risk signals.

    For each item in the relationship intelligence sections:
    1. Score the item text for behavioral signal patterns.
    2. Resolve which known contacts appear in the text.
    3. If score ≥ MEDIUM threshold: generate a proposed mutation item.
    4. HIGH confidence (≥35) → act_today (auto-apply recommended).
       MEDIUM confidence (≥18) → ask_todd (user confirmation required).

    Deduplicates by (contact_id, proposed_classification) so the same
    mutation isn't proposed twice for different items in the same brief.

    Returns a list of mutation proposal items (canonical schema).
    May be empty if no signals are detected above threshold.
    """
    # Load contact index if not provided
    if ci is None and _HAS_CONTACT_INDEX:
        try:
            ci = _ContactIndex.load()
        except Exception:  # noqa: BLE001
            ci = None

    proposals: list[dict] = []
    seen: set = set()  # (contact_id, proposed_classification)

    for sec_name in _RI_MUTATION_SCAN_SECTIONS:
        for item in (sections.get(sec_name) or []):
            if not isinstance(item, dict):
                continue

            # Skip system/empty items
            title = item.get("title") or ""
            if "no.*signal" in title.lower() or not title:
                continue

            # Build combined text from all visible fields
            text_parts = [
                title,
                item.get("summary") or "",
                item.get("why_it_matters") or "",
                item.get("recommended_action") or "",
            ]
            # Include extras contact_name as signal amplifier
            extras = item.get("extras") or {}
            for field in ("contact_name", "contact", "party"):
                v = extras.get(field) or ""
                if v:
                    text_parts.append(v)
            full_text = " ".join(text_parts)

            evidence = _score_relationship_signals(full_text)
            total = evidence["total_score"]

            if total < _CONF_MEDIUM and total > -abs(_CONF_MEDIUM):
                continue  # below threshold in both directions

            # If the item already names its subject explicitly, that's the
            # authoritative contact — scanning the full free text for any
            # index match can false-positive on unrelated words in the
            # summary (e.g. a non-person entity whose name is a substring
            # of the item's title), so we don't blend it with that lookup.
            explicit = (extras.get("contact_name") or extras.get("contact") or "").strip()
            if explicit and len(explicit) > 2:
                contacts = _resolve_contacts_in_text(explicit, ci)
                if not contacts:
                    contacts = [{"name": explicit, "id": explicit.lower().replace(" ", "-"),
                                 "company": extras.get("company") or "", "importance": "unknown"}]
            else:
                # No explicit subject — fall back to scanning the full text
                # for any known contact mentioned.
                contacts = _resolve_contacts_in_text(full_text, ci)

            if not contacts:
                continue  # can't name the contact — no actionable proposal

            proposed_cls, proposed_tags = _classify_mutation(
                evidence["dominant"], total
            )
            if not proposed_cls:
                continue

            for contact in contacts:
                cid = contact.get("id") or contact.get("name") or ""
                dedup_key = (cid, proposed_cls)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                proposal = _build_mutation_item(
                    contact=contact,
                    evidence=evidence,
                    proposed_classification=proposed_cls,
                    proposed_tags=proposed_tags,
                    source_item_title=title,
                    section_name=sec_name,
                )
                proposals.append(proposal)

    # Sort: HIGH confidence first, then by evidence score
    proposals.sort(
        key=lambda p: (
            -(1 if p["disposition"] == "act_today" else 0),
            -(p.get("extras") or {}).get("evidence_score", 0),
        )
    )
    return proposals


def compute_synthesis(sections: dict, report: dict) -> list[dict]:  # noqa: ARG001
    """Run all synthesis patterns and return combined canonical items.

    Called from daily_brief._compute_connect_the_dots() after standard
    cross-domain intersection logic has already run.

    Returns items sorted:
      1. gap_impact       — trust context (what am I missing?)
      2. relationship_activation — named outreach from today's intel (Sprint D)
      3. convergence      — cross-company theme validation (Sprint F)
      4. hotzone          — fresh signal + active deal (act today)
      5. unlock           — common blocker across multiple opportunities
      6. momentum         — pending decision has new evidence
    """
    gap_items = _source_gap_impact(sections)
    activation_items = _relationship_activation(sections)
    convergence_items = _cross_company_theme(sections)
    hotzone_items = _relationship_opportunity_hotzone(sections)
    unlock_items = _opportunity_unlock(sections)
    momentum_items = _decision_momentum(sections)

    return gap_items + activation_items + convergence_items + hotzone_items + unlock_items + momentum_items
