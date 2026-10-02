#!/usr/bin/env python3
"""
technomic_watchlist_scan.py — tiered press-release scan of the full
Technomic Top 1500 restaurant-brand universe (RB-DEFECT-2026-08-19).

Todd: "I think our watchlist needs to be bigger - potential customers as
well as competitors ... why wouldn't we scan for press releases from the
top 100 daily and then scan the rest of the list on a weekly basis?" and,
on what to do with each finding: "is this a meaningful signal to the user
about the business - if yes - report it - if no - record it in the
company profile."

The 1500-brand universe (ranked by Technomic system sales) is already
imported into system/ecosystem_intelligence.json as entity_type="brand"
records with attributes.rank -- this script does not re-import it, it just
scans it on a schedule the daily-mandatory-watchlist scan (entity_alerts.py,
~155 entities/day) can't afford to run for 1500 names every day.

Tiering:
    Tier 1 (rank <= TIER1_SIZE, minus anything already covered by
            entity_alerts.MANDATORY_ALL): scanned every run. These are the
            highest-revenue brands in the industry -- a genuine wire press
            release naming one of them is inherently worth surfacing, so
            everything found here is "reported."
    Tier 2 (everything else, ranked TIER1_SIZE+1 .. max rank): rotated
            through in ~weekly batches (batch size = ceil(remaining / 7)),
            state tracked in system/.cache/technomic_scan_rotation.json.
            Findings are classified via ecosystem_brief._classify_signal();
            only the material classes (leadership_change, rfp_cycle_signal,
            extreme_pain, vendor_displacement, funding_event) are
            "reported" -- everything else is recorded only.

Report path: material findings are merged into entity_alerts.py's own
entity_alerts_cache.json (the exact cache daily_brief.py's
_compute_watchlist_intelligence already reads for "New Activity" press
releases), plus a small manifest at
system/.cache/technomic_watchlist_promoted.json naming which entities to
add to today's dynamic watchlist roster. No new rendering path needed --
Section F already knows how to show these once the entity name is in the
roster with a fresh press_release_items hit.

Record path: EVERY finding (material or not, tier 1 or tier 2) gets a
signal written to system/ecosystem_intelligence.json via
ecosystem_brief._write_signal_to_graph -- the company profile. This is the
persistent trace for the "if no, record it" half of Todd's rule; a
non-material Tier 2 finding never reaches the brief but is never silently
dropped either.

Usage:
    python3 technomic_watchlist_scan.py --cache            # run today's batch, write results
    python3 technomic_watchlist_scan.py --dry-run           # print without writing
    python3 technomic_watchlist_scan.py --cache --limit 10  # cap batch size (testing)
    python3 technomic_watchlist_scan.py --cache --tier1-only
    python3 technomic_watchlist_scan.py --cache --tier2-only
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import entity_alerts as ea  # noqa: E402
import ecosystem_brief as eb  # noqa: E402

TIER1_SIZE = 100
ROTATION_PATH = core.CACHE_DIR / "technomic_scan_rotation.json"
PROMOTED_PATH = core.CACHE_DIR / "technomic_watchlist_promoted.json"
# RB-2026-09-05, watchlist auto-expansion: PROMOTED_PATH is overwritten each
# scan day with no memory of prior days -- a brand could be "reported" every
# day for months and there was no way to tell "keeps coming up" from "showed
# up once." This is the persistent record watchlist_promotion.py's
# repeated-appearance promotion bar reads.
HISTORY_PATH = core.CACHE_DIR / "technomic_watchlist_history.jsonl"

# Between-request pacing for the batch fetch loop (on top of _fetch_with_ua's
# own retry) -- a burst of 100-200 sequential Google News RSS requests with
# no pacing at all risks the same soft rate-limit class of failure the
# earnings_monitor.py EDGAR fix addressed the same night.
REQUEST_PACING_SECONDS = 0.4

MATERIAL_SIGNAL_CLASSES = {
    "leadership_change", "rfp_cycle_signal", "extreme_pain",
    "vendor_displacement", "funding_event",
}


def _load_graph() -> dict:
    if not core.ECOSYSTEM_INTELLIGENCE_PATH.exists():
        return {}
    try:
        return json.loads(core.ECOSYSTEM_INTELLIGENCE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_graph(graph: dict) -> None:
    core.ECOSYSTEM_INTELLIGENCE_PATH.write_text(
        json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _ranked_brand_universe(graph: dict) -> list[dict]:
    """All brand entities with a Technomic rank, sorted ascending by rank."""
    brands = [
        e for e in graph.get("entities", [])
        if e.get("entity_type") == "brand" and (e.get("attributes") or {}).get("rank") is not None
    ]
    brands.sort(key=lambda e: e["attributes"]["rank"])
    return brands


# RB-DEFECT-2026-08-19: confirmed live -- an exact-string exclusion check
# let "Chipotle Mexican Grill" (the Technomic graph's legal name) through
# as a "new" tier-1 entity even though "Chipotle" is already in
# MANDATORY_ALL and scanned daily -- same company, two names, would have
# fragmented into two separate Section F lines instead of being excluded.
# Normalize away common corporate/descriptor suffixes and compare token
# sets both directions (mandatory-list's short form is a subset of the
# Technomic graph's fuller legal name, or vice versa).
_NAME_STOPWORDS = {
    "inc", "incorporated", "corp", "corporation", "llc", "co", "company",
    "holdings", "group", "international", "restaurant", "restaurants",
    "grill", "bar", "cafe", "kitchen", "eatery", "the",
}


def _normalize_name_tokens(name: str) -> frozenset[str]:
    import re as _re
    # Drop apostrophes entirely (not replace-with-space) before the general
    # punctuation strip -- confirmed live: "Zaxby's" (mandatory list) vs.
    # "Zaxbys" (Technomic graph's own de-apostrophized name) tokenized to
    # {"zaxby","s"} vs {"zaxbys"}, neither a subset of the other, so the
    # duplicate wasn't caught. Stripping the apostrophe first makes both
    # "zaxbys".
    bare = name.lower().replace("'", "").replace("’", "")
    bare = _re.sub(r"[^a-z0-9\s]", " ", bare)
    tokens = {t for t in bare.split() if t and t not in _NAME_STOPWORDS}
    return frozenset(tokens)


def _already_mandatory(name: str, mandatory_lower: set[str],
                        mandatory_token_sets: list[frozenset[str]]) -> bool:
    if name.strip().lower() in mandatory_lower:
        return True
    name_tokens = _normalize_name_tokens(name)
    if not name_tokens:
        return False
    for mand_tokens in mandatory_token_sets:
        if not mand_tokens:
            continue
        if name_tokens <= mand_tokens or mand_tokens <= name_tokens:
            return True
    return False


def _load_rotation() -> dict:
    if ROTATION_PATH.exists():
        try:
            return json.loads(ROTATION_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"_rotation_index": 0, "_last_run_date": None}


def _save_rotation(state: dict) -> None:
    ROTATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    ROTATION_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def compute_batch(today: date, *, tier1_only: bool = False, tier2_only: bool = False,
                   limit: int | None = None) -> dict:
    """Return {'tier1': [entity...], 'tier2': [entity...]} for today's scan,
    advancing (but not persisting) the tier-2 rotation window."""
    graph = _load_graph()
    universe = _ranked_brand_universe(graph)
    mandatory_lower = {n.strip().lower() for n in ea.MANDATORY_ALL}
    mandatory_token_sets = [_normalize_name_tokens(n) for n in ea.MANDATORY_ALL]
    universe = [
        e for e in universe
        if not _already_mandatory(e["name"], mandatory_lower, mandatory_token_sets)
    ]

    tier1 = [] if tier2_only else universe[:TIER1_SIZE]
    tier2_pool = universe[TIER1_SIZE:]

    tier2: list[dict] = []
    if not tier1_only and tier2_pool:
        rotation = _load_rotation()
        batch_size = max(1, math.ceil(len(tier2_pool) / 7))
        idx = int(rotation.get("_rotation_index") or 0) % len(tier2_pool)
        for offset in range(batch_size):
            tier2.append(tier2_pool[(idx + offset) % len(tier2_pool)])
        rotation["_rotation_index"] = (idx + batch_size) % len(tier2_pool)
        rotation["_last_run_date"] = today.isoformat()
        _save_rotation(rotation)

    if limit:
        tier1 = tier1[:limit]
        tier2 = tier2[:limit]

    return {"tier1": tier1, "tier2": tier2, "graph": graph}


def _raw_row_for_item(entity_name: str, item: dict) -> dict:
    return {
        "company": entity_name,
        "title": item.get("title", ""),
        "summary": item.get("title", ""),
        "published_at": (item.get("pub_date") or "")[:10] or None,
        "confidence": "medium",
        "source_type": "public_company_primary" if item.get("press_release") else "vertical_trade",
        "source_name": item.get("source") or "Press Wire",
        "url": item.get("url", ""),
    }


def run_scan(today: date | None = None, *, tier1_only: bool = False, tier2_only: bool = False,
             limit: int | None = None, dry_run: bool = False, quiet: bool = False) -> dict:
    today = today or date.today()
    batch = compute_batch(today, tier1_only=tier1_only, tier2_only=tier2_only, limit=limit)
    graph = batch["graph"]
    entity_idx = eb._entity_index(graph)

    ea_cache = ea._load_cache()
    ea_entities_cache: dict = ea_cache.setdefault("entities", {})
    now_iso = datetime.now(timezone.utc).isoformat()

    promoted: list[dict] = []
    recorded = 0
    reported = 0
    scanned = 0

    for tier_name, roster in (("tier1", batch["tier1"]), ("tier2", batch["tier2"])):
        for entity in roster:
            name = entity["name"]
            scanned += 1
            items = ea._fetch_press_releases(name)
            time.sleep(REQUEST_PACING_SECONDS)
            if not items:
                continue

            raw_rows = [_raw_row_for_item(name, it) for it in items]
            matches = eb._match_signals_to_entities(raw_rows, entity_idx, today, graph=graph)

            entity_is_material = False
            for match in matches:
                sig_class = match["signal_class"]
                is_material = (
                    match["confidence"] in {"high", "medium"}
                    and sig_class in MATERIAL_SIGNAL_CLASSES
                )
                record_eligible = (
                    (match.get("intelligence_metadata") or {}).get("graph_mutation_eligibility") != "not_eligible"
                )
                if record_eligible and not dry_run:
                    eb._write_signal_to_graph(graph, match, today)
                    recorded += 1
                    if is_material:
                        eb._write_activation_assessment(
                            graph, match, f"sig-{match['event_at']}-{match['entity_id']}-{sig_class}", today)

                if tier_name == "tier1" or is_material:
                    entity_is_material = True

            if entity_is_material:
                reported += 1
                if not dry_run:
                    prior = (ea_entities_cache.setdefault(name, {})).get("press_release_items") or []
                    seen_urls = {it.get("url", "") for it in prior}
                    merged = list(prior)
                    for it in items:
                        u = it.get("url", "")
                        if u and u in seen_urls:
                            continue
                        seen_urls.add(u)
                        merged.append(it)
                    merged = ea._prune_stale_items(merged, today, ttl_days=7)
                    ea_entities_cache[name]["press_release_items"] = merged[:15]
                    ea_entities_cache[name]["press_release_last_checked"] = now_iso
                promoted.append({
                    # RB-2026-09-08, 3-store unification Phase 3: `entity`
                    # here is already the real graph entity dict (from
                    # _ranked_brand_universe(), a filter over graph["entities"]),
                    # so its own "id" IS the canonical entity_id already --
                    # no name-based resolution needed, unlike
                    # entity_alerts_cache.json/market_signals_earnings.jsonl,
                    # which only ever had a raw string to work from.
                    "name": name, "entity_id": entity["id"], "tier": tier_name,
                    "rank": (entity.get("attributes") or {}).get("rank"),
                })

            if not quiet:
                print(f"  [{tier_name}] {name}: {len(items)} press release(s) found"
                      + (" -> REPORTED" if entity_is_material else " -> recorded only"))

    if not dry_run:
        ea_cache["_generated_at"] = now_iso
        ea_cache["_technomic_scan_date"] = today.isoformat()
        ea._save_cache(ea_cache)

        graph["last_updated"] = str(today)
        _save_graph(graph)

        # RB-DEFECT-2026-08-19: confirmed live -- a tier1-only run followed
        # by a separate tier2 run on the same day overwrote the tier1 run's
        # entire promoted-entity manifest instead of adding to it (tier1 and
        # tier2 are meant to run as separate invocations -- daily vs.
        # weekly-rotating -- so same-day double-writes are the normal case,
        # not an edge case). Merge with whatever's already on disk for today
        # instead of clobbering it.
        PROMOTED_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing_promoted: list[dict] = []
        if PROMOTED_PATH.exists():
            try:
                prior = json.loads(PROMOTED_PATH.read_text(encoding="utf-8"))
                if prior.get("_scan_date") == today.isoformat():
                    existing_promoted = prior.get("entities") or []
            except (OSError, json.JSONDecodeError):
                pass
        seen_names = {p.get("name") for p in existing_promoted}
        newly_added = [p for p in promoted if p["name"] not in seen_names]
        merged_promoted = existing_promoted + newly_added
        PROMOTED_PATH.write_text(json.dumps({
            "_generated_at": now_iso,
            "_scan_date": today.isoformat(),
            "entities": merged_promoted,
        }, indent=2), encoding="utf-8")

        # Append-only, one line per entity newly reported today -- never
        # rewritten, so this is safe to append even on a second same-day
        # tier1/tier2 run (existing_promoted's own name-dedup above already
        # keeps a same-day double-run from double-counting here too).
        if newly_added:
            with open(HISTORY_PATH, "a", encoding="utf-8") as f:
                for p in newly_added:
                    f.write(json.dumps({
                        "date": today.isoformat(), "name": p["name"], "tier": p["tier"],
                    }) + "\n")

    return {
        "scanned": scanned,
        "recorded": recorded,
        "reported": reported,
        "promoted_entities": [p["name"] for p in promoted],
        "tier1_count": len(batch["tier1"]),
        "tier2_count": len(batch["tier2"]),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cache", action="store_true", help="Write results (graph, alert cache, promotion manifest).")
    p.add_argument("--dry-run", action="store_true", help="Scan and print, write nothing.")
    p.add_argument("--limit", type=int, default=None, help="Cap entities scanned per tier (testing).")
    p.add_argument("--tier1-only", action="store_true")
    p.add_argument("--tier2-only", action="store_true")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    result = run_scan(
        tier1_only=args.tier1_only, tier2_only=args.tier2_only,
        limit=args.limit, dry_run=not args.cache or args.dry_run, quiet=args.quiet,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
