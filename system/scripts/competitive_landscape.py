#!/usr/bin/env python3
"""
competitive_landscape.py — RB-2026-09-01.

Todd's corrected ask (the vendor-list CSV/xlsx built earlier the same day
was not what he meant): an analysis artifact, modeled on his own canonical
Restaurant Tech Coverage workbook, that tracks the competition (and Genius
itself) across every real piece of the tech stack -- market share across
the tracked ~1,654 brands, real battle cards with a computed delta between
Genius and each competitor, and a standing view of which competitors still
need real research.

Reuses, never reimplements:
  - ecosystem_intelligence._read_graph()/_index_by_id() -- the graph itself.
  - ecosystem_export.is_current_win(rel) -- the exact "is this relationship
    a real, current, confirmed deployment" filter the Phase 2 migration
    tool and the existing xlsx exporter already use, so market share here
    can't silently disagree with what ecosystem_export.py already
    considers a real win.
  - competitor_intelligence_common.load_registry()/load_competitor() -- the
    9(+) tracked competitors' real, sourced battle-card content
    (positioning_summary, todds_pov, vs_genius.{genius_advantages,
    competitor_advantages}), cross-referenced via each competitor's real
    vendor_entity_id, never a name guess.
  - rb_core.GP_OWN_TERMS -- the Genius/Global Payments family rollup
    (vendor-genius, vendor-global-payments, vendor-worldpay, ...), same
    list used everywhere else this shows up.

Same discipline as everywhere else in this codebase: every number here is
computed from real, persisted relationships/evidence. Nothing is invented.
A category or competitor with no real data says so honestly (0 brands, "no
positioning on file yet") rather than a plausible-sounding placeholder.

CLI:
    python3 competitive_landscape.py market-share <category>
    python3 competitive_landscape.py battle-card <category>
    python3 competitive_landscape.py research-status
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import ecosystem_export as eco_export  # noqa: E402
import tech_stack_workbook_sync as wb_sync  # noqa: E402
import earnings_trend  # noqa: E402

# RB-2026-09-06: battle-card extension, Predictive Market Intelligence.
# Explicit mapping, not a fuzzy-name matcher -- checked directly and found
# ecosystem_intelligence.json's vendor entity names don't cleanly match
# earnings_calls.jsonl's company names (e.g. vendor "NCR" vs. earnings
# company "NCR Voyix"), and there's a real duplicate-entity situation
# (both vendor-ncr and vendor-ncr-voyix exist, both matching "NCR Voyix"
# via name/alias). Only 6 tracked vendors are public companies with real
# earnings history at all -- a small, static, human-reviewed mapping is
# more honest here than fuzzy-matching machinery that could silently
# resolve the NCR ambiguity the wrong way. Not comprehensive by design:
# most restaurant-tech vendors (Qu, Revel, Restaurant365, Nory, ...) are
# privately held and have no public earnings to attach.
_VENDOR_TO_EARNINGS_COMPANY: dict[str, str] = {
    "vendor-global-payments": "Global Payments",
    "vendor-ncr-voyix": "NCR Voyix",
    "vendor-olo": "Olo",
    "vendor-oracle": "Oracle Corp",
    "vendor-par-technology": "PAR Technology",
    "vendor-toast": "Toast",
}

# The full, real tech-stack category taxonomy this analysis covers: every
# real column tech_stack_workbook_sync.py's canonical workbook tracks
# (reused, not re-typed by hand -- a new workbook column picked up there
# automatically becomes a new category here too), plus payments_gateway
# (2026-09-01, Todd's own explicit addition -- not a real workbook column
# today, a distinction he wants tracked going forward regardless).
TECH_STACK_CATEGORIES: list[str] = sorted(set(wb_sync._COLUMN_TO_CATEGORY.values()) | {"payments_gateway"})

# RB-2026-09-01: two categories from Todd's competitive-intelligence design
# request (Platform, Delivery/Aggregation) have no per-brand relationship
# data in the ecosystem graph today -- no workbook column, no real
# vendor-relationship category to compute market share from. Kept
# deliberately SEPARATE from TECH_STACK_CATEGORIES (which stays strictly
# graph-derived, and is what category_market_share/listTechStackCategories/
# getCategoryMarketShare operate over) so market share for these two stays
# honestly absent rather than silently zero-filled. They ARE valid
# category_battle_cards keys -- RM-facing content (positioning, discovery
# questions, wedge) doesn't need brand-count data to be real and useful.
EXTRA_BATTLE_CARD_CATEGORIES: set[str] = {"platform", "delivery_aggregation"}

_GP_OWN_TERMS_LOWER = {t.lower() for t in core.GP_OWN_TERMS}


def _is_genius_family(vendor_name: Optional[str]) -> bool:
    return (vendor_name or "").strip().lower() in _GP_OWN_TERMS_LOWER


def _recent_financial_health_for_vendor(vendor_id: str) -> Optional[dict]:
    """Real, evidence-grounded financial-health/momentum read from the
    vendor's OWN public earnings (RB-2026-09-06, Predictive Market
    Intelligence battle-card extension) -- honestly None for the vendors
    (most of them) with no public earnings history to attach, never a
    placeholder or a generic hedge."""
    company = _VENDOR_TO_EARNINGS_COMPANY.get(vendor_id)
    if not company:
        return None
    trend = earnings_trend.compute_entity_trend(company)
    if trend.get("status") != "ok":
        return None
    return {
        "earnings_company": company,
        "financial_health_signals": trend.get("financial_health_signals") or {},
        "event_frequency_recent_vs_prior": trend.get("event_frequency_recent_vs_prior"),
        "answer": trend.get("answer"),
    }


def _competitor_vendor_index(*, by_id: Optional[dict] = None) -> dict[str, dict]:
    """vendor_entity_id -> real competitor_intelligence profile, for every
    tracked competitor that has resolved one. Loaded once per call --
    only 9(+) real competitors, cheap."""
    sys.path.insert(0, str(SCRIPTS_DIR))
    import competitor_intelligence_common as cic
    index: dict[str, dict] = {}
    reg = cic.load_registry()
    for entry in reg.get("registry", []):
        slug = entry.get("competitor_slug", "")
        comp_path = cic.competitor_dir(slug) / "competitor.json"
        if not comp_path.exists():
            continue
        comp = cic.load_json(comp_path)
        vendor_entity_id = (comp or {}).get("vendor_entity_id")
        if vendor_entity_id:
            index[vendor_entity_id] = comp
            # Product-specific entities can represent the same public
            # competitor in the ecosystem (e.g. vendor-par-punchh vs.
            # vendor-par-technology). Join those deterministically through
            # the shared ticker instead of fuzzy vendor-name matching.
            primary = (by_id or {}).get(vendor_entity_id) or {}
            ticker = (primary.get("ticker") or "").strip().upper()
            if ticker:
                for candidate_id, candidate in (by_id or {}).items():
                    if candidate.get("entity_type") != "vendor":
                        continue
                    if (candidate.get("ticker") or "").strip().upper() == ticker:
                        index.setdefault(candidate_id, comp)
    return index


def vendor_brand_ids(graph: dict, vendor_id: str) -> set[str]:
    """Distinct brand entity IDs with a real, CURRENT relationship to this
    vendor, across ALL tech-stack categories. Factored out of
    vendor_brand_count() (2026-09-30) so a caller needing the real brand
    IDENTITIES -- not just the count -- can union this set across several
    vendor entities (e.g. a product-family rollup across a vendor's
    several product-line entities) without double-counting a brand that
    happens to show up under more than one line."""
    seen: set[str] = set()
    for rel in graph.get("relationships") or []:
        if rel.get("relationship_type") != "uses_vendor_for_category":
            continue
        if rel.get("to_entity_id") != vendor_id:
            continue
        if not eco_export.is_current_win(rel):
            continue
        brand_id = rel.get("from_entity_id")
        if brand_id:
            seen.add(brand_id)
    return seen


def vendor_brand_count(graph: dict, vendor_id: str) -> int:
    """Distinct brands with a real, CURRENT relationship to this vendor,
    across ALL tech-stack categories (unlike category_market_share, which
    is scoped to one category) -- the one live-computable "scale" fact for
    a vendor's Company Profile (2026-09-28), no research pipeline needed."""
    return len(vendor_brand_ids(graph, vendor_id))


def category_market_share(graph: dict, category: str, *, by_id: Optional[dict] = None) -> dict:
    """Every vendor with a real, CURRENT (ecosystem_export.is_current_win)
    relationship in `category`, ranked by brand_count, with two honest
    share numbers: share of brands with ANY known vendor in this category
    (the more meaningful number when overall coverage is thin), and share
    of the full tracked-brand universe (so a thin-coverage category never
    silently reads as "0% for everyone" -- it reads as genuinely unknown
    for the rest)."""
    by_id = by_id or ei._index_by_id(graph.get("entities") or [])
    total_brands = sum(1 for e in graph.get("entities") or [] if e.get("entity_type") == "brand")

    vendor_counts: dict[str, int] = {}
    covered_brand_ids: set[str] = set()
    for rel in graph.get("relationships") or []:
        if rel.get("relationship_type") != "uses_vendor_for_category":
            continue
        if rel.get("category") != category:
            continue
        if not eco_export.is_current_win(rel):
            continue
        vendor_id = rel.get("to_entity_id")
        brand_id = rel.get("from_entity_id")
        if not vendor_id or not brand_id:
            continue
        vendor_counts[vendor_id] = vendor_counts.get(vendor_id, 0) + 1
        covered_brand_ids.add(brand_id)

    total_known = len(covered_brand_ids)
    rows = []
    for vendor_id, count in sorted(vendor_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        vendor = by_id.get(vendor_id) or {}
        rows.append({
            "vendor_id": vendor_id,
            "vendor_name": vendor.get("name"),
            "brand_count": count,
            "share_of_known_pct": round(100 * count / total_known, 1) if total_known else None,
            "share_of_universe_pct": round(100 * count / total_brands, 1) if total_brands else None,
            "is_genius_family": _is_genius_family(vendor.get("name")),
        })
    for i, row in enumerate(rows, start=1):
        row["rank"] = i

    return {
        "category": category,
        "total_brands_tracked": total_brands,
        "brands_with_known_vendor": total_known,
        "coverage_pct": round(100 * total_known / total_brands, 1) if total_brands else None,
        "vendors": rows,
    }


def vendor_concentration(graph: dict, vendor_id: str, *, by_id: Optional[dict] = None) -> dict:
    """Ecosystem Lookup Tool, Competitors tab (Phase B): for a given
    vendor, every real CURRENT relationship (any category, not just one --
    unlike category_market_share) with the linked brands grouped by
    segment/subsegment. Same ecosystem_export.is_current_win() filter
    category_market_share() uses above, so "which brands is this vendor
    installed at" can't silently disagree with what's already considered a
    real win elsewhere in this codebase. Raises ValueError for an unknown
    or non-vendor id -- never returns a silently-empty result for a typo'd
    id."""
    by_id = by_id or ei._index_by_id(graph.get("entities") or [])
    vendor = by_id.get(vendor_id)
    if not vendor or vendor.get("entity_type") != "vendor":
        raise ValueError(f"No vendor entity '{vendor_id}'")

    brands: list[dict] = []
    segment_counts: dict[str, int] = {}
    subsegment_counts: dict[str, int] = {}
    for rel in graph.get("relationships") or []:
        if rel.get("relationship_type") != "uses_vendor_for_category":
            continue
        if rel.get("to_entity_id") != vendor_id:
            continue
        if not eco_export.is_current_win(rel):
            continue
        brand_id = rel.get("from_entity_id")
        brand = by_id.get(brand_id)
        if not brand or brand.get("entity_type") != "brand":
            continue
        attrs = brand.get("attributes") or {}
        segment = attrs.get("segment") or "unknown"
        subsegment = attrs.get("subsegment") or "unknown"
        segment_counts[segment] = segment_counts.get(segment, 0) + 1
        subsegment_counts[subsegment] = subsegment_counts.get(subsegment, 0) + 1
        brands.append({
            "brand_id": brand_id,
            "brand_name": brand.get("name"),
            "category": rel.get("category"),
            "segment": segment,
            "subsegment": subsegment,
        })

    brands.sort(key=lambda b: b["brand_name"] or "")
    return {
        "vendor_id": vendor_id,
        "vendor_name": vendor.get("name"),
        "total_brand_relationships": len(brands),
        "brands": brands,
        "by_segment": sorted(
            [{"segment": k, "count": v} for k, v in segment_counts.items()],
            key=lambda r: -r["count"],
        ),
        "by_subsegment": sorted(
            [{"subsegment": k, "count": v} for k, v in subsegment_counts.items()],
            key=lambda r: -r["count"],
        ),
    }


def battle_card_for_category(
    graph: dict, category: str, *, by_id: Optional[dict] = None, max_competitors: int = 5,
    feature_vendor_id: Optional[str] = None,
) -> dict:
    """Market share for `category` (see category_market_share), joined
    against real competitor_intelligence battle-card content for every
    non-Genius vendor in the top `max_competitors`, plus a computed,
    real delta line against the Genius family's own count in this
    category. A competitor with no positioning on file yet says so
    honestly rather than a placeholder.

    `feature_vendor_id` (2026-09-28 fix): when a specific vendor is the
    reason this battle card is being requested (Team Portal's vendor-page
    lookup always has one), that vendor's card is guaranteed to appear and
    is moved to the front of `battle_cards` regardless of category market
    share -- caught live: looking up PAR Technology in "pos" rendered Qu's
    card first (19 brands vs. PAR's 13), because this function previously
    only ever ranked by market share with no notion of which vendor the
    caller actually cares about."""
    by_id = by_id or ei._index_by_id(graph.get("entities") or [])
    share = category_market_share(graph, category, by_id=by_id)
    comp_index = _competitor_vendor_index(by_id=by_id)

    genius_row = next((r for r in share["vendors"] if r["is_genius_family"]), None)
    genius_count = genius_row["brand_count"] if genius_row else 0
    genius_pct = genius_row["share_of_known_pct"] if genius_row else 0.0

    market_rows = [r for r in share["vendors"] if not r["is_genius_family"]]
    selected_rows = list(market_rows[:max_competitors])
    represented_profiles = {
        (comp_index.get(r["vendor_id"]) or {}).get("competitor_id")
        for r in selected_rows
        if comp_index.get(r["vendor_id"])
    }

    # A deliberately maintained category card is stronger evidence of
    # relevance than a top-five brand-count cutoff. Always include such a
    # competitor, even when its current ecosystem count ranks lower.
    for row in market_rows[max_competitors:]:
        comp = comp_index.get(row["vendor_id"])
        profile_id = (comp or {}).get("competitor_id")
        category_card = ((comp or {}).get("category_battle_cards") or {}).get(category)
        if category_card is not None and profile_id not in represented_profiles:
            selected_rows.append(row)
            represented_profiles.add(profile_id)

    # Also preserve a maintained card when there is not yet a current
    # brand-count relationship in this category. Do not fabricate market
    # share; render an explicit unavailable-count line instead.
    unique_profiles = {
        comp.get("competitor_id"): comp for comp in comp_index.values() if comp.get("competitor_id")
    }
    for profile_id, comp in unique_profiles.items():
        category_card = (comp.get("category_battle_cards") or {}).get(category)
        if category_card is None or profile_id in represented_profiles:
            continue
        selected_rows.append({
            "vendor_id": comp.get("vendor_entity_id"),
            "vendor_name": comp.get("display_name") or comp.get("competitor_slug"),
            "brand_count": None,
            "share_of_known_pct": None,
            "is_genius_family": False,
            "market_share_available": False,
        })
        represented_profiles.add(profile_id)

    if feature_vendor_id is not None and not any(r["vendor_id"] == feature_vendor_id for r in selected_rows):
        featured_row = next((r for r in market_rows if r["vendor_id"] == feature_vendor_id), None)
        if featured_row is not None:
            selected_rows.append(featured_row)
        else:
            comp = comp_index.get(feature_vendor_id)
            selected_rows.append({
                "vendor_id": feature_vendor_id,
                "vendor_name": (by_id.get(feature_vendor_id) or {}).get("name") or feature_vendor_id,
                "brand_count": None,
                "share_of_known_pct": None,
                "is_genius_family": False,
                "market_share_available": False,
            })

    if feature_vendor_id is not None:
        selected_rows.sort(key=lambda r: r["vendor_id"] != feature_vendor_id)

    cards = []
    for row in selected_rows:
        comp = comp_index.get(row["vendor_id"])
        # RB-2026-09-01: prefer a category-specific battle card when one has
        # been filled in; when absent, the vendor-level fields above (still
        # populated the same way as before) remain the fallback. The
        # RM-only fields below (status, confidence_pct, discovery
        # questions, ...) have no vendor-level equivalent, so they stay
        # honestly empty rather than falling back to anything -- never
        # fabricated.
        category_card = ((comp or {}).get("category_battle_cards") or {}).get(category)
        market_share_available = row.get("market_share_available", True)
        delta_pct = round((row["share_of_known_pct"] or 0) - (genius_pct or 0), 1)
        cards.append({
            "vendor_id": row["vendor_id"],
            "vendor_name": row["vendor_name"],
            "brand_count": row["brand_count"],
            "share_of_known_pct": row["share_of_known_pct"],
            "has_competitor_profile": comp is not None,
            "positioning_summary": (comp or {}).get("positioning_summary") or None,
            "todds_pov": (comp or {}).get("todds_pov") or None,
            "genius_advantages": ((comp or {}).get("vs_genius") or {}).get("genius_advantages") or [],
            "competitor_advantages": ((comp or {}).get("vs_genius") or {}).get("competitor_advantages") or [],
            "has_category_battle_card": category_card is not None,
            "status": (category_card or {}).get("status"),
            "confidence_pct": (category_card or {}).get("confidence_pct"),
            "rm_plain_english_posture": (category_card or {}).get("rm_plain_english_posture") or None,
            "listen_for": (category_card or {}).get("listen_for") or [],
            "discovery_questions": (category_card or {}).get("discovery_questions") or [],
            "when_to_bring_todd_in": (category_card or {}).get("when_to_bring_todd_in") or None,
            "red_flags": (category_card or {}).get("red_flags") or [],
            "battle_card_evidence_ids": (category_card or {}).get("evidence_ids") or [],
            "battle_card_source_urls": (category_card or {}).get("source_urls") or [],
            "last_validated": (category_card or {}).get("last_validated"),
            "recent_financial_health": _recent_financial_health_for_vendor(row["vendor_id"]),
            "delta_line": (
                f"Genius: {genius_count} brands ({genius_pct or 0}% of known) vs. "
                f"{row['vendor_name']}: {row['brand_count']} brands ({row['share_of_known_pct'] or 0}% of known) "
                f"-- delta {delta_pct:+.1f} pts"
            ) if market_share_available else (
                f"No current brand-count relationship is on file for {row['vendor_name']} in {category}; "
                "category-specific competitive intelligence is retained below."
            ),
        })

    return {
        "category": category,
        "genius_brand_count": genius_count,
        "genius_share_of_known_pct": genius_pct,
        "total_brands_tracked": share["total_brands_tracked"],
        "brands_with_known_vendor": share["brands_with_known_vendor"],
        "battle_cards": cards,
    }


def brand_category_grid(graph: dict, *, by_id: Optional[dict] = None) -> list[dict]:
    """One row per tracked brand, one column per TECH_STACK_CATEGORIES
    entry, cell = the current vendor's name (from is_current_win
    relationships only) or None -- the actual 'fashioned after the
    canonical sheet' matrix Todd asked for."""
    by_id = by_id or ei._index_by_id(graph.get("entities") or [])
    brand_category_vendor: dict[str, dict[str, str]] = {}
    for rel in graph.get("relationships") or []:
        if rel.get("relationship_type") != "uses_vendor_for_category":
            continue
        if not eco_export.is_current_win(rel):
            continue
        category = rel.get("category")
        brand_id = rel.get("from_entity_id")
        vendor_id = rel.get("to_entity_id")
        if not (category and brand_id and vendor_id):
            continue
        vendor_name = (by_id.get(vendor_id) or {}).get("name")
        if not vendor_name:
            continue
        brand_category_vendor.setdefault(brand_id, {})[category] = vendor_name

    rows = []
    for entity in graph.get("entities") or []:
        if entity.get("entity_type") != "brand":
            continue
        cats = brand_category_vendor.get(entity["id"], {})
        row = {"brand_id": entity["id"], "brand_name": entity.get("name")}
        for category in TECH_STACK_CATEGORIES:
            row[category] = cats.get(category)
        rows.append(row)
    rows.sort(key=lambda r: (r["brand_name"] or "").lower())
    return rows


def competitor_research_status(graph: dict, *, by_id: Optional[dict] = None, stale_after_days: int = 30) -> dict:
    """For every tracked competitor: whether the real battle-card content
    is actually filled in, and whether it's stale. Plus real,
    mechanically-detected candidates -- vendor entities already appearing
    in the graph under categories with no competitor profile yet -- never
    auto-created, only surfaced for Todd to confirm via createCompetitor."""
    import competitor_intelligence_common as cic
    from datetime import date, datetime

    by_id = by_id or ei._index_by_id(graph.get("entities") or [])
    reg = cic.load_registry()
    today = date.today()

    statuses = []
    tracked_vendor_ids: set[str] = set()
    for entry in reg.get("registry", []):
        slug = entry.get("competitor_slug", "")
        comp_path = cic.competitor_dir(slug) / "competitor.json"
        comp = cic.load_json(comp_path) if comp_path.exists() else {}
        vendor_entity_id = (comp or {}).get("vendor_entity_id")
        if vendor_entity_id:
            tracked_vendor_ids.add(vendor_entity_id)
        last_evidence_date = (comp or {}).get("last_evidence_date")
        is_stale = True
        if last_evidence_date:
            try:
                is_stale = (today - date.fromisoformat(last_evidence_date)).days > stale_after_days
            except ValueError:
                is_stale = True
        positioning_filled = bool((comp or {}).get("positioning_summary"))
        vs_genius = (comp or {}).get("vs_genius") or {}
        vs_genius_point_count = len(vs_genius.get("genius_advantages") or []) + len(vs_genius.get("competitor_advantages") or [])
        statuses.append({
            "competitor_slug": slug,
            "display_name": entry.get("display_name") or slug,
            "positioning_filled": positioning_filled,
            "competes_on_filled": bool((comp or {}).get("competes_on")),
            "vs_genius_point_count": vs_genius_point_count,
            "last_evidence_date": last_evidence_date,
            "needs_research": (not positioning_filled) or is_stale,
        })

    # Mechanical (never invented) candidates: real vendors already in the
    # graph under the categories Todd specifically named as needing
    # coverage, with no competitor_intelligence profile at all yet.
    _CANDIDATE_CATEGORIES = {"drive_thru_timers", "ai_solution_1", "ai_solution_2", "pos_hardware"}
    candidate_vendor_ids: set[str] = set()
    for rel in graph.get("relationships") or []:
        if rel.get("relationship_type") != "uses_vendor_for_category":
            continue
        if rel.get("category") not in _CANDIDATE_CATEGORIES:
            continue
        vid = rel.get("to_entity_id")
        if vid and vid not in tracked_vendor_ids:
            candidate_vendor_ids.add(vid)
    candidates = []
    for vid in sorted(candidate_vendor_ids):
        vendor = by_id.get(vid) or {}
        if _is_genius_family(vendor.get("name")):
            continue
        candidates.append({
            "vendor_id": vid,
            "vendor_name": vendor.get("name"),
            "primary_category": (vendor.get("attributes") or {}).get("primary_category"),
        })

    return {"competitors": statuses, "new_competitor_candidates": candidates}


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    p_ms = sub.add_parser("market-share")
    p_ms.add_argument("category")
    p_bc = sub.add_parser("battle-card")
    p_bc.add_argument("category")
    sub.add_parser("research-status")
    args = p.parse_args()

    graph = ei._read_graph()
    if args.cmd == "market-share":
        print(json.dumps(category_market_share(graph, args.category), indent=2))
    elif args.cmd == "battle-card":
        print(json.dumps(battle_card_for_category(graph, args.category), indent=2))
    elif args.cmd == "research-status":
        print(json.dumps(competitor_research_status(graph), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
