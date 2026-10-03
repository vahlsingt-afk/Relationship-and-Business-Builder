#!/usr/bin/env python3
"""hunter_competitor_category_targets.py — 2026-10-02.

Hunter's brand-side target selection (hunter_gap_manifest.py --universe
brands) and plain competitor-side selection (--universe competitors,
priority no_pack_yet > cycle_covered) both exist, but neither answers
"research the top 10 competitors in each real tech-stack category" --
the actual ask behind this module. "Top 10" has to mean something real:
here it's competitive_landscape.category_market_share()'s own ranking,
the same brand-count-based market share every battle card and the Team
Portal already use, never an invented or vendor-claimed ranking.

A competitor can rank top-10 in more than one category (e.g. a POS vendor
that also places in payments_gateway); this module de-duplicates to one
target per real tracked competitor, keeping every category it placed in
and its best rank, so a single Hunter cycle doesn't re-research the same
vendor once per category it happens to compete in.

Gap detection is deliberately specific to this module's mission (value
statement, features, customer feedback/testimonials, and market
positioning) rather than reusing hunter_gap_manifest's full brand-style
field set -- those are exactly the four focus areas this cycle exists to
fill, read directly off each competitor's real competitor.json via
competitor_intelligence_common.get_extended_profile() (never guessed).
Each gap reuses hunter_gap_manifest._gap()/_FIELD_QUESTIONS so the
question text for a given field has exactly one place it's maintained.

Usage:
    python3 hunter_competitor_category_targets.py [--top 10] [--limit N] [--output PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import ecosystem_intelligence as ei  # noqa: E402
import competitive_landscape as cl  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402
import hunter_gap_manifest as hgm  # noqa: E402

SCHEMA = "rb.hunter_competitor_category_manifest.v1"
SOURCE = "system/scripts/competitive_landscape.py:category_market_share"

# The four focus fields this cycle exists to fill, read straight off
# competitor_intelligence_common.get_extended_profile()'s real fields --
# "market positioning" maps to positioning_summary (RBB's own synthesis;
# written separately from the review-first evidence pipeline, but still
# the one real field that answers "how is this competitor positioned").
_FOCUS_FIELDS = ("value_statement", "features", "customer_feedback_testimonials", "positioning_summary")


def _is_empty(value) -> bool:
    if isinstance(value, dict):
        return not (value.get("value") or "").strip()
    if isinstance(value, list):
        return len(value) == 0
    return not (value or "").strip() if isinstance(value, str) else not value


def top_competitors_by_category(graph: dict, *, top: int = 10, by_id: dict | None = None) -> dict[str, dict]:
    """Returns {vendor_id: {vendor_name, placements: [{category, rank, brand_count}, ...]}}
    for every non-Genius vendor that ranks in the real top `top` of at
    least one TECH_STACK_CATEGORIES category. EXTRA_BATTLE_CARD_CATEGORIES
    (platform, delivery_aggregation) are excluded -- by design they carry
    no real brand-count relationship data, so there is no real "top 10"
    to compute there (see competitive_landscape.py's own comment on why
    those stay separate)."""
    by_id = by_id or ei._index_by_id(graph.get("entities") or [])
    placements: dict[str, dict] = {}
    for category in cl.TECH_STACK_CATEGORIES:
        share = cl.category_market_share(graph, category, by_id=by_id)
        market_rows = [r for r in share["vendors"] if not r["is_genius_family"]][:top]
        for row in market_rows:
            entry = placements.setdefault(row["vendor_id"], {"vendor_name": row["vendor_name"], "placements": []})
            entry["placements"].append({
                "category": category, "rank": row["rank"], "brand_count": row["brand_count"],
            })
    return placements


def build_manifest(*, top: int = 10, limit: int | None = None, graph: dict | None = None) -> dict:
    graph = ei._read_graph() if graph is None else graph
    by_id = ei._index_by_id(graph.get("entities") or [])
    placements = top_competitors_by_category(graph, top=top, by_id=by_id)
    comp_index = cl._competitor_vendor_index(by_id=by_id)

    targets = []
    for vendor_id, entry in placements.items():
        comp = comp_index.get(vendor_id)
        if comp is None:
            # Ranks in real ecosystem data but has no competitor_intelligence
            # profile yet -- that absence is itself the first gap Hunter
            # should close (the vendor_startup_profile playbook, not this
            # one, creates the shell); never fabricated here.
            continue
        slug = comp.get("competitor_slug")
        if not slug:
            continue
        target_key = f"competitor:{slug}"
        profile = cic.get_extended_profile(comp)
        gaps = [
            hgm._gap(target_key, field, "missing", SOURCE)
            for field in _FOCUS_FIELDS
            if _is_empty(profile.get(field))
        ]
        targets.append({
            "target_key": target_key,
            "display_name": comp.get("display_name") or entry["vendor_name"] or slug,
            "entity_type": "restaurant_technology_company",
            "categories": sorted(entry["placements"], key=lambda p: p["rank"]),
            "category_count": len(entry["placements"]),
            "best_rank": min(p["rank"] for p in entry["placements"]),
            "gaps": gaps,
            "discovery_domains": [
                "value statement / positioning", "product features and capabilities",
                "customer feedback and testimonials", "market positioning versus named competitors",
            ],
        })

    # Most commercially relevant first (placed in the most top-10
    # categories), then most incomplete first -- same two-key discipline
    # as hunter_gap_manifest.build_manifest's own sort.
    targets.sort(key=lambda t: (-t["category_count"], -len(t["gaps"]), t["display_name"]))
    if limit is not None:
        targets = targets[:max(0, limit)]

    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "top_n_per_category": top,
        "categories_scanned": list(cl.TECH_STACK_CATEGORIES),
        "source": SOURCE,
        "target_count": len(targets),
        "gap_count": sum(len(t["gaps"]) for t in targets),
        "targets": targets,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Top-N-per-tech-category competitor targets for Hunter")
    parser.add_argument("--top", type=int, default=10, help="How many top-ranked competitors per category count as 'top' (default 10)")
    parser.add_argument("--limit", type=int, help="Cap the returned target list after ranking")
    parser.add_argument("--output")
    args = parser.parse_args()
    manifest = build_manifest(top=args.top, limit=args.limit)
    rendered = json.dumps(manifest, indent=2, default=str) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
