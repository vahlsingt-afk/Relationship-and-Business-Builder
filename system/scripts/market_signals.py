#!/usr/bin/env python3
"""
market_signals.py — first-pass market / operator signal intake.

Reads operator-curated rows from `system/inbox/market_signals.json`,
classifies their source quality per `restaurant_tech_watchlist.md`,
dedupes, enriches each item into a strategic-intelligence chain, ranks,
and emits a top-N payload that the daily brief can splice into the CoS
briefing.

This is a minimal MVP per the handoff: input is currently a hand-curated
JSON file. No web crawling, no LLM. The intent is to make the brief's
`condensed_industry_context` slot source-backed rather than synthetic.

Input shape (per row, also documented in the handoff):

    {
      "title": "...",
      "url": "...",
      "source_name": "Restaurant Business",
      "source_type": "vertical_trade|linkedin_social|public_company_primary
                     |company_blog|event_signal|mainstream",
      "source_quality": "strong|medium|weak_for_hospitality",   # optional
      "published_at": "2026-05-20",
      "company": "...",
      "side": "vendor_supply|operator_demand",
      "category": "restaurant_ai|pos|payments|loyalty|drive_thru|labor|...",
      "signal_type": "product_launch|operator_priority|earnings_signal|...",
      "pain_point_or_priority": "...",
      "strategic_relevance": "high|medium|low|none",
      "affected_relationships_or_threads": ["T-...", "person-id"],
      "macro_force": "oil_prices|labor_cost|interest_rates|...",
      "restaurant_operator_impact": "...",
      "restaurant_tech_vendor_implication": "...",
      "second_order_impact": "...",
      "relationship_opportunity": "...",
      "why_this_matters_to_todd": "...",
      "timing_priority": "today|this_week|monitor",
      "recommended_action": "act_today|monitor|ask_todd|ignore",
      "confidence": "high|medium|low"
    }

CLI:

    python3 system/scripts/market_signals.py            # human-readable
    python3 system/scripts/market_signals.py --json
    python3 system/scripts/market_signals.py --cache    # writes .cache/market_signals.json
    python3 system/scripts/market_signals.py --top 5    # take top-N (default 7)
    python3 system/scripts/market_signals.py --smoke    # in-memory regression
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
from market_signals_ri import enrich_with_ri  # noqa: E402


# ----------------------------------------------------------------------
# Source-quality and ranking tables
# ----------------------------------------------------------------------

# Per system/restaurant_tech_watchlist.md "Source Priority":
#   - LinkedIn/social posts from leaders, operators, buyers, analysts ⇒ strong
#   - Vertical trade and hospitality sources                          ⇒ strong
#   - Public-company primary data                                     ⇒ strong
#   - Event signals (NRA exhibitor / sponsor / speaker)               ⇒ strong
#   - Company blog (primary but lower-signal)                         ⇒ medium
#   - Mainstream business news (only as corroboration / macro)        ⇒ weak_for_hospitality
#   - Government / macro primary data                                 ⇒ medium
SOURCE_TYPE_TO_QUALITY = {
    "linkedin_social":        "strong",
    "vertical_trade":         "strong",
    "public_company_primary": "strong",
    "event_signal":           "strong",
    "company_blog":           "medium",
    "government_data":         "medium",
    "commodity_market":        "medium",
    "macro_primary":           "medium",
    "mainstream":             "weak_for_hospitality",
}

# Sort keys. Higher numbers rank earlier in the brief.
RELEVANCE_RANK = {"high": 3, "medium": 2, "low": 1, "none": 0}
QUALITY_RANK = {"strong": 2, "medium": 1, "weak_for_hospitality": 0}
TIMING_RANK = {"today": 3, "this_week": 2, "monitor": 1, "none": 0}

# Allowed disposition values per the canonical CoS doctrine.
CANONICAL_DISPOSITIONS = {"act_today", "monitor", "ask_todd", "ignore"}


INBOX_PATH = core.INBOX_DIR / "market_signals.json"

# RB 9.28 — supplemental feed files merged into build_report()
_FEED_JSONL_PATHS = [
    core.INBOX_DIR / "market_signals_feed.jsonl",      # market_source_feeds.py (trade press RSS)
    core.INBOX_DIR / "market_signals_earnings.jsonl",  # earnings_monitor.py (IR + EDGAR)
]


# ----------------------------------------------------------------------
# Loaders / normalizers
# ----------------------------------------------------------------------

def load_raw() -> dict:
    """Read the operator-curated market_signals.json. Returns {} when the
    file is missing or malformed — the caller treats missing data as a
    stale/under-instrumented state, not a hard failure."""
    if not INBOX_PATH.exists():
        return {}
    try:
        return json.loads(INBOX_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def load_feed_items() -> list[dict]:
    """Read supplemental JSONL feed files (trade press RSS + earnings monitor).

    Returns a list of raw item dicts in the same schema as market_signals.json
    items[]. These are merged into build_report() so all signal sources flow
    through the same normalizer → ranker → deduplication pipeline.

    RB 9.28: feeds market_signals_feed.jsonl and market_signals_earnings.jsonl.
    Missing files are silently skipped (not all feeds run on every refresh cycle).
    """
    rows: list[dict] = []
    for path in _FEED_JSONL_PATHS:
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            continue
    return rows


def _coerce_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _coerce_score(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        score = float(v)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(100.0, score))


def _coerce_str_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    return [s] if s else []


def _norm_url(u: str | None) -> str | None:
    """Lightweight URL canonicalization for dedup. Drops trailing slash,
    fragment, and the most common tracking query keys. Case-folds host."""
    if not u:
        return None
    s = u.strip()
    if not s:
        return None
    # Strip fragment.
    if "#" in s:
        s = s.split("#", 1)[0]
    # Strip a few tracking params from the query string.
    if "?" in s:
        base, qs = s.split("?", 1)
        keep = []
        for pair in qs.split("&"):
            if not pair:
                continue
            k = pair.split("=", 1)[0].lower()
            if k.startswith("utm_") or k in {"ref", "ref_src", "ref_url", "mc_cid", "mc_eid", "fbclid", "gclid"}:
                continue
            keep.append(pair)
        s = base + (("?" + "&".join(keep)) if keep else "")
    # Lowercase scheme + host; preserve path case (some sites are case-sensitive).
    if "://" in s:
        scheme, rest = s.split("://", 1)
        scheme = scheme.lower()
        if "/" in rest:
            host, path = rest.split("/", 1)
            s = f"{scheme}://{host.lower()}/{path}"
        else:
            s = f"{scheme}://{rest.lower()}"
    return s.rstrip("/")


def normalize_item(raw: dict) -> dict:
    """Fill in derivable fields, coerce types, set safe defaults.

    Does NOT throw on missing fields; instead, returns the item with
    blanks so the ranker and renderer can still place it (with low
    confidence). The caller can decide to drop incomplete items.
    """
    title = _coerce_str(raw.get("title")) or ""
    url = _coerce_str(raw.get("url"))
    source_type = _coerce_str(raw.get("source_type")) or "unknown"
    source_quality = _coerce_str(raw.get("source_quality"))
    if not source_quality:
        source_quality = SOURCE_TYPE_TO_QUALITY.get(source_type, "medium")

    published_at = _coerce_str(raw.get("published_at"))
    company = _coerce_str(raw.get("company")) or ""
    category = _coerce_str(raw.get("category")) or "unspecified"
    side = _coerce_str(raw.get("side")) or "unspecified"
    signal_type = _coerce_str(raw.get("signal_type")) or "unspecified"
    pain = _coerce_str(raw.get("pain_point_or_priority")) or ""
    relevance = _coerce_str(raw.get("strategic_relevance")) or "low"
    affected = raw.get("affected_relationships_or_threads") or []
    if not isinstance(affected, list):
        affected = [str(affected)]
    action = _coerce_str(raw.get("recommended_action")) or "monitor"
    if action not in CANONICAL_DISPOSITIONS:
        action = "monitor"
    confidence = _coerce_str(raw.get("confidence")) or "medium"

    macro_force = _coerce_str(raw.get("macro_force"))
    timing = _coerce_str(raw.get("timing_priority"))
    if not timing:
        timing = "today" if action == "act_today" else "this_week" if relevance == "high" else "monitor"
    if timing not in TIMING_RANK:
        timing = "monitor"

    return {
        "title": title,
        "url": url,
        "url_canonical": _norm_url(url),
        "source_name": _coerce_str(raw.get("source_name")) or source_type,
        "source_type": source_type,
        "source_quality": source_quality,
        "published_at": published_at,
        "company": company,
        "category": category,
        "side": side,
        "signal_type": signal_type,
        "macro_force": macro_force,
        "pain_point_or_priority": pain,
        "strategic_relevance": relevance,
        "affected_relationships_or_threads": affected,
        "recommended_action": action,
        "confidence": confidence,
        "timing_priority": timing,
        "restaurant_operator_impact": _coerce_str(raw.get("restaurant_operator_impact")),
        "restaurant_tech_vendor_implication": _coerce_str(raw.get("restaurant_tech_vendor_implication")),
        "second_order_impact": _coerce_str(raw.get("second_order_impact")),
        "relationship_opportunity": _coerce_str(raw.get("relationship_opportunity")),
        "why_this_matters_to_todd": _coerce_str(raw.get("why_this_matters_to_todd")),
        "strategic_operator_entity_type": _coerce_str(raw.get("strategic_operator_entity_type")),
        "strategic_operator_movement": _coerce_str(raw.get("strategic_operator_movement")),
        "influence_score": _coerce_score(raw.get("influence_score")),
        "relationship_value_score": _coerce_score(raw.get("relationship_value_score")),
        "operational_pressure_score": _coerce_score(raw.get("operational_pressure_score")),
        "ecosystem_impact_score": _coerce_score(raw.get("ecosystem_impact_score")),
        "future_opportunity_score": _coerce_score(raw.get("future_opportunity_score")),
        "relationship_proximity": _coerce_str(raw.get("relationship_proximity")),
        "known_network_overlap": _coerce_str_list(raw.get("known_network_overlap")),
        "mutual_connections": _coerce_str_list(raw.get("mutual_connections")),
        "existing_vendor_relationships": _coerce_str_list(raw.get("existing_vendor_relationships")),
        "buying_window_probability": _coerce_str(raw.get("buying_window_probability")),
        "follow_up_priority": _coerce_str(raw.get("follow_up_priority")),
        "watchlist_bucket": _coerce_str(raw.get("watchlist_bucket")),
        # Note this item's grounding for downstream renderers — all
        # market-signals are operator-curated until a fetcher exists.
        "grounding": "manual_user_provided",
    }


# ----------------------------------------------------------------------
# Strategic-intelligence enrichment
# ----------------------------------------------------------------------

MACRO_KEYWORDS = {
    "tariff": "tariffs",
    "iran": "middle_east_conflict_risk",
    "middle east": "middle_east_conflict_risk",
    "oil": "oil_and_fuel_prices",
    "fuel": "oil_and_fuel_prices",
    "interest rate": "interest_rates",
    "consumer spending": "consumer_spending",
    "labor": "labor_cost_pressure",
    "wage": "labor_cost_pressure",
    "immigration": "labor_availability",
    "produce": "commodity_inflation",
    "beef": "commodity_inflation",
    "poultry": "commodity_inflation",
    "dairy": "commodity_inflation",
    "supply chain": "supply_chain_disruption",
    "real estate": "commercial_real_estate_pressure",
    "regulation": "regulatory_shift",
}

OPERATOR_IMPACT_BY_CATEGORY = {
    "drive_thru": "Operators will scrutinize speed, accuracy, labor substitution, and customer-experience tradeoffs before scaling spend.",
    "restaurant_ai": "Operators will separate AI pilots that reduce labor, waste, or accuracy pain from generic AI theater.",
    "labor": "Labor pressure raises urgency for scheduling, forecasting, training, and automation tools with measurable ROI.",
    "back_office": "Margin pressure makes food cost, inventory, procurement, and above-store controls more urgent.",
    "payments": "Payment economics, fraud, and platform bundling can shift vendor preference and switching timelines.",
    "loyalty": "Traffic and check-pressure make guest data, retention, and offer precision more important.",
    "pos": "Platform-transition signals can open replacement, integration, and consolidation sales cycles.",
    "supply_chain": "Supply disruption raises demand for forecasting, inventory visibility, and supplier flexibility.",
}

VENDOR_IMPLICATION_BY_CATEGORY = {
    "drive_thru": "Drive-thru, voice, camera, menu-board, timer, and order-accuracy vendors gain or lose urgency based on proof of throughput and accuracy.",
    "restaurant_ai": "AI vendors with operator-specific workflows and hard ROI gain credibility; broad horizontal AI claims face skepticism.",
    "labor": "Labor, scheduling, training, and automation vendors get a stronger urgency story if savings are measurable.",
    "back_office": "Inventory, food-cost, procurement, and analytics vendors benefit when margin compression becomes visible.",
    "payments": "Integrated payment platforms may benefit from consolidation pressure, while standalone tools face ROI scrutiny.",
    "loyalty": "CRM, loyalty, personalization, and CDP vendors benefit when brands need traffic without blanket discounting.",
    "pos": "POS and middleware vendors should watch for displacement windows, integration strain, and consolidation mandates.",
    "supply_chain": "Forecasting, procurement, and inventory vendors become more relevant when input volatility hits operators.",
}

MACRO_OPERATOR_IMPACT = {
    "tariffs": "Input costs and equipment prices rise, compressing franchisee/operator margins.",
    "middle_east_conflict_risk": "Energy and freight volatility can pressure food distribution and consumer confidence.",
    "oil_and_fuel_prices": "Distribution, delivery, and guest travel costs rise, putting pressure on store economics.",
    "interest_rates": "Higher cost of capital slows remodels, new-unit growth, franchisee financing, and long-payback projects.",
    "consumer_spending": "Traffic and check management become more fragile; operators protect value perception.",
    "labor_cost_pressure": "Store-level margin pressure increases demand for productivity and automation.",
    "labor_availability": "Staffing scarcity increases interest in scheduling, automation, training, and retention tools.",
    "commodity_inflation": "Food-cost volatility makes inventory, forecasting, menu engineering, and waste reduction more urgent.",
    "supply_chain_disruption": "Operators need visibility, substitution planning, and tighter inventory discipline.",
    "commercial_real_estate_pressure": "Unit economics and expansion timing become more cautious.",
    "regulatory_shift": "Compliance complexity increases operational overhead and reporting needs.",
}

MACRO_VENDOR_IMPLICATION = {
    "tariffs": "Vendors tied to hardware or imported equipment may face cost exposure; ROI-oriented efficiency tools gain urgency.",
    "middle_east_conflict_risk": "Vendors that reduce fuel, labor, inventory waste, or planning uncertainty get a stronger message.",
    "oil_and_fuel_prices": "Delivery, routing, inventory, and labor-efficiency vendors can tie value to margin protection.",
    "interest_rates": "Long-implementation platforms may stall; modular, fast-payback tools have an advantage.",
    "consumer_spending": "Loyalty, pricing, personalization, and throughput vendors can position around traffic defense.",
    "labor_cost_pressure": "Labor automation and productivity vendors gain urgency if claims are measurable.",
    "labor_availability": "Scheduling, training, hiring, and automation vendors gain relevance.",
    "commodity_inflation": "Inventory, procurement, forecasting, and food-cost vendors gain urgency.",
    "supply_chain_disruption": "Supply-chain visibility and forecasting vendors gain urgency.",
    "commercial_real_estate_pressure": "Growth-dependent vendor pipelines may slow; same-store productivity tools benefit.",
    "regulatory_shift": "Compliance, labor, reporting, and back-office vendors gain relevance.",
}


def _infer_macro_force(it: dict) -> str | None:
    if it.get("macro_force"):
        return it["macro_force"]
    text = " ".join([
        it.get("title") or "",
        it.get("category") or "",
        it.get("signal_type") or "",
        it.get("pain_point_or_priority") or "",
    ]).lower()
    for needle, force in MACRO_KEYWORDS.items():
        if needle in text:
            return force
    return None


def _relationship_matches(company: str, *, baseline: list[dict] | None,
                          threads: list[dict] | None) -> list[str]:
    if not company or company == "(general)":
        return []
    low = company.lower()
    matches: list[str] = []
    for t in threads or []:
        hay = " ".join([
            str(t.get("id") or ""),
            str(t.get("title") or ""),
            str(t.get("context") or ""),
            " ".join(str(c) for c in (t.get("companies") or [])),
        ]).lower()
        if low in hay:
            tid = t.get("id")
            if tid:
                matches.append(f"active_thread:{tid}")
    for e in baseline or []:
        company_name = str(e.get("company") or e.get("current_company") or "").lower()
        if low and low in company_name:
            eid = e.get("id") or e.get("name")
            if eid:
                matches.append(f"contact:{eid}")
        if len(matches) >= 6:
            break
    return matches


def _build_market_ri_assessment(
    out: dict,
    combined: list[str],
    *,
    source_freshness: str = "fresh",
) -> dict:
    """Build a P-036-compliant ri_assessment block for a market signal item.

    Status rules:
      unavailable — source is stale or missing (confidence capped at 0.55)
      proposed    — signal maps to known contacts/threads in Todd's network
      blocked     — company/person mentioned but no baseline match found
      irrelevant  — no relationship consequence detected
    """
    company = out.get("company") or ""
    strategic_relevance = out.get("strategic_relevance") or "none"
    confidence_label = out.get("confidence") or "medium"
    confidence_map = {"high": 0.85, "medium": 0.65, "low": 0.40}
    raw_confidence = confidence_map.get(confidence_label, 0.65)

    if source_freshness in {"stale", "unavailable"}:
        # Stale sources cannot produce confident RI claims (P-036 rule)
        return {
            "status": "unavailable",
            "source": "market_signals",
            "source_freshness": source_freshness,
            "confidence": min(raw_confidence, 0.55),
            "evidence": [],
            "mapped_contact_ids": [],
            "mapped_thread_ids": [],
            "proposed_mutation": None,
            "display_recommendation": "suppress_unless_asked",
            "reason": (
                f"market_signals source_freshness={source_freshness}; "
                "cannot produce confident RI claim from stale or unavailable data."
            ),
        }

    contact_ids = [r.split(":", 1)[1] for r in combined if r.startswith("contact:")]
    thread_ids = [r.split(":", 1)[1] for r in combined if r.startswith("active_thread:")]
    evidence = []

    if combined:
        # Network hit — at least one contact or thread maps to this signal
        if contact_ids:
            evidence.append(f"contact match(es): {', '.join(contact_ids[:3])}")
        if thread_ids:
            evidence.append(f"active thread match(es): {', '.join(thread_ids[:3])}")
        evidence.append(f"strategic_relevance={strategic_relevance}")
        # Proposed mutation: flag for relationship-timed outreach / monitoring.
        # No automated touchContact here — market signals surface intelligence,
        # not confirmed interactions. Operator confirms if a touch is warranted.
        proposed_mutation = {
            "command": None,
            "payload": {
                "note": (
                    f"Market signal '{out.get('title', '')}' maps to "
                    f"{len(contact_ids)} contact(s) and {len(thread_ids)} thread(s). "
                    "Consider timely outreach or thread update."
                ),
                "contact_ids": contact_ids,
                "thread_ids": thread_ids,
            },
        }
        return {
            "status": "proposed",
            "source": "market_signals",
            "source_freshness": source_freshness,
            "confidence": round(raw_confidence, 2),
            "evidence": evidence,
            "mapped_contact_ids": contact_ids,
            "mapped_thread_ids": thread_ids,
            "proposed_mutation": proposed_mutation,
            "display_recommendation": (
                "show" if strategic_relevance in {"high", "medium"} else "suppress_unless_asked"
            ),
            "reason": (
                f"Signal maps to {len(combined)} network reference(s); "
                f"relationship action may be warranted (strategic_relevance={strategic_relevance})."
            ),
        }

    if company and strategic_relevance in {"high", "medium"}:
        # Company/signal mentioned but no baseline match — blocked, not irrelevant
        return {
            "status": "blocked",
            "source": "market_signals",
            "source_freshness": source_freshness,
            "confidence": round(raw_confidence * 0.6, 2),
            "evidence": [f"company='{company}' not matched in baseline or active_threads"],
            "mapped_contact_ids": [],
            "mapped_thread_ids": [],
            "proposed_mutation": None,
            "display_recommendation": "suppress_unless_asked",
            "reason": (
                f"entity_match=none; '{company}' is not in Todd's baseline or active threads. "
                "Cannot propose RI action without a mapped contact."
            ),
        }

    return {
        "status": "irrelevant",
        "source": "market_signals",
        "source_freshness": source_freshness,
        "confidence": 0.0,
        "evidence": [],
        "mapped_contact_ids": [],
        "mapped_thread_ids": [],
        "proposed_mutation": None,
        "display_recommendation": "suppress",
        "reason": "No relationship consequence detected for this market signal.",
    }


def enrich_item(it: dict, *, baseline: list[dict] | None = None,
                threads: list[dict] | None = None,
                source_freshness: str = "fresh") -> dict:
    """Add the CoS intelligence chain fields. Existing operator-curated
    fields win; deterministic heuristics fill gaps so the brief can
    answer what changed, why it matters, who it maps to, and what to do."""
    out = dict(it)
    category = out.get("category") or "unspecified"
    macro_force = _infer_macro_force(out)
    if macro_force:
        out["macro_force"] = macro_force
        out["signal_layer"] = "macro_economic_geopolitical"
    elif out.get("side") == "operator_demand":
        out["signal_layer"] = "restaurant_industry_signal"
    else:
        out["signal_layer"] = "restaurant_tech_market"

    if not out.get("restaurant_operator_impact"):
        out["restaurant_operator_impact"] = (
            MACRO_OPERATOR_IMPACT.get(macro_force)
            or OPERATOR_IMPACT_BY_CATEGORY.get(category)
            or "Operators may adjust budget, rollout timing, vendor scrutiny, or operating priorities if this signal persists."
        )
    if not out.get("restaurant_tech_vendor_implication"):
        out["restaurant_tech_vendor_implication"] = (
            MACRO_VENDOR_IMPLICATION.get(macro_force)
            or VENDOR_IMPLICATION_BY_CATEGORY.get(category)
            or "Restaurant-tech vendors should translate the signal into pipeline timing, displacement risk, ROI pressure, or positioning changes."
        )
    if not out.get("second_order_impact"):
        out["second_order_impact"] = (
            f"{out['restaurant_operator_impact']} Therefore: {out['restaurant_tech_vendor_implication']}"
        )

    existing_affected = [
        str(a) for a in (out.get("affected_relationships_or_threads") or [])
        if str(a).strip()
    ]
    inferred = _relationship_matches(out.get("company") or "", baseline=baseline, threads=threads)
    combined = []
    for ref in existing_affected + inferred:
        if ref not in combined:
            combined.append(ref)
    out["affected_relationships_or_threads"] = combined

    if not out.get("relationship_opportunity"):
        if combined:
            out["relationship_opportunity"] = (
                "Review mapped contacts/threads for timely outreach, prep, or monitoring: "
                + ", ".join(combined[:4])
            )
        else:
            out["relationship_opportunity"] = (
                "No direct network mapping yet; monitor or research a relationship path before acting."
            )

    if not out.get("why_this_matters_to_todd"):
        company = out.get("company") or "this signal"
        out["why_this_matters_to_todd"] = (
            f"{company} may change operator urgency, vendor positioning, or relationship timing "
            f"inside Todd's restaurant-tech and McDonald's-adjacent network."
        )

    operator_scores = [
        out.get("influence_score"),
        out.get("relationship_value_score"),
        out.get("operational_pressure_score"),
        out.get("ecosystem_impact_score"),
        out.get("future_opportunity_score"),
    ]
    numeric_operator_scores = [float(s) for s in operator_scores if s is not None]
    if out.get("strategic_operator_entity_type") or out.get("strategic_operator_movement"):
        out["signal_layer"] = "strategic_operator_movement"
        if not out.get("watchlist_bucket"):
            out["watchlist_bucket"] = "Strategic Operators"
        if not out.get("relationship_proximity"):
            out["relationship_proximity"] = "unknown_needs_mapping"
        if not out.get("follow_up_priority"):
            avg = sum(numeric_operator_scores) / len(numeric_operator_scores) if numeric_operator_scores else 0
            out["follow_up_priority"] = "high" if avg >= 75 else "medium" if avg >= 45 else "monitor"
        if not out.get("buying_window_probability"):
            pressure = out.get("operational_pressure_score") or 0
            opportunity = out.get("future_opportunity_score") or 0
            out["buying_window_probability"] = (
                "high" if max(pressure, opportunity) >= 75
                else "medium" if max(pressure, opportunity) >= 45
                else "unknown"
            )

    if not out.get("timing_priority") or out["timing_priority"] not in TIMING_RANK:
        if out.get("recommended_action") == "act_today":
            out["timing_priority"] = "today"
        elif out.get("strategic_relevance") == "high" or combined:
            out["timing_priority"] = "this_week"
        else:
            out["timing_priority"] = "monitor"

    operator_score_boost = (
        int((sum(numeric_operator_scores) / len(numeric_operator_scores)) / 5)
        if numeric_operator_scores else 0
    )
    out["priority_score"] = (
        RELEVANCE_RANK.get(out.get("strategic_relevance"), 0) * 100
        + QUALITY_RANK.get(out.get("source_quality"), 0) * 20
        + TIMING_RANK.get(out.get("timing_priority"), 0) * 10
        + (25 if combined else 0)
        + (15 if macro_force else 0)
        + (20 if out.get("signal_layer") == "strategic_operator_movement" else 0)
        + operator_score_boost
    )

    # P-036: every signal must carry an ri_assessment block.
    out["ri_assessment"] = _build_market_ri_assessment(
        out, combined, source_freshness=source_freshness
    )
    return out


# ----------------------------------------------------------------------
# Dedup + rank
# ----------------------------------------------------------------------

def dedupe(items: Iterable[dict]) -> list[dict]:
    """Prefer canonical-URL match; fall back to (company, title, date)."""
    seen_url: set[str] = set()
    seen_triple: set[tuple[str, str, str]] = set()
    out: list[dict] = []
    for it in items:
        key_url = it.get("url_canonical")
        if key_url and key_url in seen_url:
            continue
        triple = (
            (it.get("company") or "").lower().strip(),
            (it.get("title") or "").lower().strip(),
            (it.get("published_at") or "").strip(),
        )
        if all(triple) and triple in seen_triple:
            continue
        if key_url:
            seen_url.add(key_url)
        if all(triple):
            seen_triple.add(triple)
        out.append(it)
    return out


def _date_sort_key(it: dict) -> str:
    """Sort key for freshness (descending). Use published_at, which is a
    YYYY-MM-DD or ISO timestamp; ISO strings sort correctly. Missing dates
    sort last."""
    p = (it.get("published_at") or "")
    return p


def rank(items: Iterable[dict]) -> list[dict]:
    """Sort by strategic_relevance, then source_quality, then freshness.
    Weak-for-hospitality items are kept (per the handoff) but always rank
    behind strong/medium items at the same relevance level.

    Implemented as a two-pass stable sort so the date dimension can sort
    descending (newer first) while the other dimensions sort ascending.
    Items with no published_at sort last within their relevance/quality
    bucket.
    """
    # Pass 1: secondary key — newest published_at first. Empty dates sort
    # last by giving them an empty sentinel.
    by_date = sorted(
        items,
        key=lambda it: it.get("published_at") or "",
        reverse=True,
    )
    # Pass 2: primary keys — relevance, then quality (both descending).
    return sorted(
        by_date,
        key=lambda it: (
            -int(it.get("priority_score") or 0),
            -RELEVANCE_RANK.get(it.get("strategic_relevance"), 0),
            -QUALITY_RANK.get(it.get("source_quality"), 0),
        ),
    )


_DOMINANT_SOURCE_TYPE = "sec_edgar_8k"
_DOMINANT_RESERVE_SLOTS = 3


def _select_top(ranked: list[dict], top_n: int) -> list[dict]:
    """Select the top `top_n` items, reserving room for non-SEC items.

    RB 9.71: `rank()`'s priority_score favors SEC 8-K filings
    (source_quality + recency push them to ~400-410) over curated
    vertical-trade items (~395-400) even when the curated items are fresher
    and tied to active threads. A plain `ranked[:top_n]` slice with top_n=7
    (the morning_headlines cut) was filled entirely with 8-K filings,
    burying every curated `market_signals.json` item.

    This reserves up to `_DOMINANT_RESERVE_SLOTS` of `top_n` for non-8-K
    items (in rank order) when such items exist, backfilling with 8-K items
    if there aren't enough non-8-K items to fill the reservation. Pure
    ordering tweak — does not change `priority_score` or `all_ranked`, so
    other consumers of the full ranked list are unaffected.
    """
    top_n = max(0, int(top_n))
    if top_n == 0:
        return []
    max_dominant = max(0, top_n - _DOMINANT_RESERVE_SLOTS)
    selected: list[dict] = []
    overflow: list[dict] = []
    dominant_count = 0
    for it in ranked:
        if len(selected) >= top_n:
            break
        if it.get("source_type") == _DOMINANT_SOURCE_TYPE:
            if dominant_count < max_dominant:
                selected.append(it)
                dominant_count += 1
            else:
                overflow.append(it)
        else:
            selected.append(it)
    for it in overflow:
        if len(selected) >= top_n:
            break
        selected.append(it)
    return selected


# ----------------------------------------------------------------------
# Top-level pipeline
# ----------------------------------------------------------------------

def _is_overlay_stale(fetched_at: str | None, *, max_age_hours: int = 36,
                      now: datetime | None = None) -> bool:
    """Mark market signals stale when the inbox file is missing, has no
    fetched_at, or is older than max_age_hours. 36h default — daily
    refresh expected, with a half-day buffer for weekends."""
    if not fetched_at:
        return True
    try:
        dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    anchor = now or datetime.now(timezone.utc)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    age_hours = (anchor.astimezone(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0
    return age_hours > max_age_hours


def build_report(*, top_n: int = 7, raw: dict | None = None,
                 now: datetime | None = None,
                 feed_items: list[dict] | None = None) -> dict:
    """Run the full pipeline. `raw` overrides the inbox read (for tests).
    `now` overrides the staleness anchor (for tests).
    `feed_items` overrides the JSONL feed read (for tests — RB 9.28).

    RB 9.28: supplemental items from market_signals_feed.jsonl (trade press)
    and market_signals_earnings.jsonl (IR + EDGAR) are merged with the
    hand-curated market_signals.json before normalization and ranking.
    """
    if raw is None:
        raw = load_raw()
    fetched_at = raw.get("fetched_at")
    items_raw = list(raw.get("items") or [])

    # Merge supplemental feed items (RB 9.28)
    if feed_items is None:
        feed_items = load_feed_items()
    items_raw.extend(feed_items)

    baseline = core.load_baseline()
    threads = core.load_active_threads()
    freshness_label = (
        "unavailable" if not items_raw
        else "stale" if _is_overlay_stale(fetched_at, now=now)
        else "fresh"
    )
    items = [
        enrich_item(normalize_item(it), baseline=baseline, threads=threads,
                    source_freshness=freshness_label)
        for it in items_raw
    ]
    deduped = dedupe(items)
    ranked = rank(deduped)
    ranked = enrich_with_ri(ranked)
    top = _select_top(ranked, top_n)

    stale = _is_overlay_stale(fetched_at, now=now)
    has_data = bool(items_raw)

    return {
        "fetched_at": fetched_at,
        "stale": stale,
        "has_data": has_data,
        "input_count": len(items),
        "deduped_count": len(deduped),
        "top_count": len(top),
        "top_n_requested": int(top_n),
        "top": top,
        "all_ranked": ranked,
        # Surface a clear stale-data note so downstream consumers (Step 6
        # daily brief integration) can render "market signals: missing or
        # stale" without inventing content.
        "stale_reason": _stale_reason(fetched_at, has_data, now=now),
    }


def _stale_reason(fetched_at: str | None, has_data: bool,
                  now: datetime | None = None) -> str | None:
    if not has_data and not fetched_at:
        return (
            f"No market signals file at {INBOX_PATH.relative_to(core.PROJECT_DIR)}. "
            "Brief should report market intelligence as missing, not synthesize."
        )
    if not fetched_at:
        return (
            f"market_signals.json exists but has no fetched_at; treat as stale."
        )
    if _is_overlay_stale(fetched_at, now=now):
        return (
            f"market_signals.json fetched_at={fetched_at} is older than the "
            f"36h freshness threshold."
        )
    return None


# ----------------------------------------------------------------------
# Renderer
# ----------------------------------------------------------------------

def render_text(report: dict) -> str:
    lines: list[str] = []
    if report.get("stale_reason"):
        lines.append(f"market_signals: STALE — {report['stale_reason']}")
    fa = report.get("fetched_at") or "unknown"
    lines.append(
        f"market_signals (fetched {fa}, "
        f"input={report['input_count']}, deduped={report['deduped_count']}, "
        f"top={report['top_count']} of {report['top_n_requested']} requested)"
    )
    if not report.get("top"):
        lines.append("  (no items)")
        return "\n".join(lines)
    for it in report["top"]:
        bits = [
            f"[{it.get('strategic_relevance','?')}/{it.get('source_quality','?')}/{it.get('confidence','?')}]",
            f"({it.get('recommended_action','?')})",
            f"{it.get('company','')}",
            f"— {it.get('signal_type','?')}: {it.get('title','')[:80]}",
        ]
        lines.append("  " + " ".join(bits))
        if it.get("pain_point_or_priority"):
            lines.append(f"      {it['pain_point_or_priority']}")
        lines.append(f"      why Todd: {it.get('why_this_matters_to_todd')}")
        lines.append(f"      operator impact: {it.get('restaurant_operator_impact')}")
        lines.append(f"      vendor implication: {it.get('restaurant_tech_vendor_implication')}")
        lines.append(f"      timing: {it.get('timing_priority')} · priority_score={it.get('priority_score')}")
        meta = [
            it.get("source_name") or it.get("source_type"),
            it.get("published_at") or "",
        ]
        lines.append(f"      source: {' / '.join(s for s in meta if s)}")
        if it.get("url"):
            lines.append(f"      url: {it['url']}")
        affected = it.get("affected_relationships_or_threads") or []
        if affected:
            lines.append(f"      affects: {', '.join(affected)}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Smoke test
# ----------------------------------------------------------------------

_SMOKE_RAW = {
    "fetched_at": "2026-05-20T07:00:00-05:00",
    "items": [
        {
            "title": "Olo Q1 earnings: AI-driven order accuracy named as 2026 strategic priority",
            "url": "https://investors.olo.com/press/2026/q1-earnings",
            "source_name": "Olo Investor Relations",
            "source_type": "public_company_primary",
            "published_at": "2026-05-19",
            "company": "Olo",
            "side": "vendor_supply",
            "category": "restaurant_ai",
            "signal_type": "earnings_signal",
            "pain_point_or_priority": "Order accuracy at scale; mentions drive-thru voice pilots.",
            "strategic_relevance": "high",
            "affected_relationships_or_threads": ["T-2026-05-genius-global-payments"],
            "recommended_action": "monitor",
            "confidence": "high",
        },
        {
            "title": "McDonald's USA exec: drive-thru voice rollout pauses pending accuracy improvements",
            "url": "https://www.restaurantbusinessonline.com/some-article",
            "source_name": "Restaurant Business",
            "source_type": "vertical_trade",
            "published_at": "2026-05-20",
            "company": "McDonald's",
            "side": "operator_demand",
            "category": "drive_thru",
            "signal_type": "operator_priority",
            "pain_point_or_priority": "Voice-AI accuracy / labor swap economics.",
            "strategic_relevance": "high",
            "strategic_operator_entity_type": "large_regional_operator",
            "strategic_operator_movement": "technology_standardization_signal",
            "influence_score": 80,
            "relationship_value_score": 60,
            "operational_pressure_score": 85,
            "ecosystem_impact_score": 90,
            "future_opportunity_score": 70,
            "relationship_proximity": "active McDonald's-adjacent thread",
            "known_network_overlap": ["T-2026-05-genius-global-payments"],
            "buying_window_probability": "medium",
            "watchlist_bucket": "Strategic Operators",
            "affected_relationships_or_threads": [],
            "recommended_action": "monitor",
            "confidence": "medium",
        },
        {
            "title": "Mainstream WSJ piece on AI in restaurants",
            "url": "https://www.wsj.com/some-article",
            "source_name": "WSJ",
            "source_type": "mainstream",
            "published_at": "2026-05-19",
            "company": "(general)",
            "side": "vendor_supply",
            "category": "restaurant_ai",
            "signal_type": "industry_overview",
            "pain_point_or_priority": "Macro framing only.",
            "strategic_relevance": "low",
            "recommended_action": "ignore",
            "confidence": "low",
        },
        {
            "title": "Oil prices rise after Middle East shipping disruption",
            "url": "https://www.eia.gov/todayinenergy/detail.php?id=fixture",
            "source_name": "EIA",
            "source_type": "macro_primary",
            "published_at": "2026-05-20",
            "company": "(macro)",
            "side": "macro",
            "category": "oil_prices",
            "signal_type": "macro_pressure",
            "pain_point_or_priority": "Fuel and distribution cost pressure.",
            "strategic_relevance": "medium",
            "recommended_action": "monitor",
            "confidence": "medium",
        },
        # Duplicate of #1 by URL.
        {
            "title": "Olo Q1 earnings (republished)",
            "url": "https://investors.olo.com/press/2026/q1-earnings?utm_source=rss",
            "source_name": "Olo (rss)",
            "source_type": "public_company_primary",
            "published_at": "2026-05-19",
            "company": "Olo",
            "side": "vendor_supply",
            "category": "restaurant_ai",
            "signal_type": "earnings_signal",
            "strategic_relevance": "high",
            "recommended_action": "monitor",
            "confidence": "high",
        },
    ],
}


def _smoke() -> int:
    failures: list[str] = []

    def ck(cond: bool, msg: str) -> None:
        mark = "OK" if cond else "FAIL"
        print(f"  {mark}   {msg}")
        if not cond:
            failures.append(msg)

    # Pin "now" so the staleness check is deterministic against the
    # fixture's fetched_at.
    now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)

    report = build_report(top_n=7, raw=_SMOKE_RAW, now=now)

    ck(report["has_data"], "report.has_data == True with fixture items")
    ck(report["input_count"] == 5, f"input_count=5 (got {report['input_count']})")
    ck(report["deduped_count"] == 4, f"deduped removes the utm duplicate (got {report['deduped_count']})")
    ck(report["top_count"] == 4, f"top_count=4 (got {report['top_count']})")

    top = report["top"]
    # Both high-relevance items should rank above the low-relevance WSJ
    # piece. Among the two highs, strong source_quality ties go to the
    # newer published_at: McDonald's 2026-05-20 vs Olo 2026-05-19.
    ck(top[0]["company"] == "McDonald's",
       f"first ranked is McDonald's (got {top[0]['company']})")
    ck(top[1]["company"] == "Olo",
       f"second ranked is Olo (got {top[1]['company']})")
    ck(top[2]["company"] == "(macro)",
       f"macro item ranks ahead of low-relevance mainstream (got {top[2]['company']})")
    ck(top[3]["company"] == "(general)",
       f"low-relevance mainstream falls to last (got {top[3]['company']})")

    # source_quality inferred from source_type.
    mainstream = next((i for i in top if i["source_type"] == "mainstream"), None)
    ck(mainstream is not None
       and mainstream["source_quality"] == "weak_for_hospitality",
       "mainstream source_type auto-classified as weak_for_hospitality")

    # All dispositions are canonical.
    ck(all(i["recommended_action"] in CANONICAL_DISPOSITIONS for i in top),
       "every top item carries a canonical disposition")

    ck(all(i.get("second_order_impact") for i in top),
       "every top item carries second_order_impact")
    ck(all(i.get("restaurant_operator_impact") for i in top),
       "every top item carries restaurant_operator_impact")
    strategic = [i for i in top if i.get("signal_layer") == "strategic_operator_movement"]
    ck(strategic and strategic[0].get("watchlist_bucket") == "Strategic Operators",
       "strategic operator movement keeps watchlist bucket")
    ck(strategic and strategic[0].get("operational_pressure_score") == 85.0,
       "strategic operator movement carries operational pressure score")
    ck(all(i.get("restaurant_tech_vendor_implication") for i in top),
       "every top item carries restaurant_tech_vendor_implication")
    ck(all(i.get("why_this_matters_to_todd") for i in top),
       "every top item carries why_this_matters_to_todd")
    ck(all(i.get("timing_priority") in TIMING_RANK for i in top),
       "every top item carries canonical timing_priority")
    macro = next((i for i in top if i.get("company") == "(macro)"), None)
    ck(macro is not None and macro.get("macro_force") == "middle_east_conflict_risk",
       f"macro force inferred from Middle East fixture (got {(macro or {}).get('macro_force')})")

    # Empty input → stale reason set.
    empty = build_report(top_n=7, raw={}, now=now)
    ck(not empty["has_data"], "empty input → has_data False")
    ck(empty["stale"] is True, "empty input → stale True")
    ck("No market signals file" in (empty["stale_reason"] or ""),
       f"empty input → stale_reason names missing file "
       f"(got {empty['stale_reason']!r})")

    # Missing fetched_at on a non-empty file → still stale.
    no_ts = {"items": [_SMOKE_RAW["items"][0]]}
    nrep = build_report(top_n=7, raw=no_ts, now=now)
    ck(nrep["stale"] is True, "missing fetched_at → stale True")

    print(f"--- market_signals smoke complete: {len(failures)} failure(s) ---")
    return 1 if failures else 0


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Market / operator signal intake (Step 5 sprint MVP).",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON.")
    p.add_argument("--cache", action="store_true",
                   help="Write the report to system/.cache/market_signals.json")
    p.add_argument("--top", type=int, default=7,
                   help="Number of top items to surface (default 7).")
    p.add_argument("--smoke", action="store_true",
                   help="Run in-memory regression with bundled fixture.")
    args = p.parse_args(argv)

    if args.smoke:
        return _smoke()

    report = build_report(top_n=args.top)
    if args.cache:
        core.write_cache("market_signals", report, source="market_signals.py")
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render_text(report))
    # Exit 0 even when data is missing — that's a normal operational
    # state ("under-instrumented") that the brief should report, not a
    # hard failure.
    return 0


if __name__ == "__main__":
    sys.exit(main())
