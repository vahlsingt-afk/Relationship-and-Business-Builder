#!/usr/bin/env python3
"""restaurant_tech_trends.py — Top 5 Restaurant Technology Trends (RB-2026-09-29).

Real 2026-09-29 request: lead the Team Portal's Market Intelligence page
with a "Top 5 Restaurant Technology Trends" section, computed by RB itself
and refreshed weekly alongside RB's other weekly closeout work (see
system/FRIDAY_EOW_ROUTINE.md) -- not a brand-new standalone cron, but not
bolted onto friday_eow_routine.py either, since that script's own
docstring is explicit that it "reviews and surfaces, never auto-fixes"
and that generating real analytical/narrative content "deserves its own
session, not something this routine should attempt or fake." This is
that separate script, following the same weekly-generator convention
weekly_review_generator.py already established (propose a draft
computation, persist it, regenerate on the same cadence).

Methodology, confirmed with Todd 2026-09-29 (broad categories, all
evidence -- the alternative considered and rejected was qualitative-
only signals, which would be more specific but the underlying feeds
only carry ~15 genuinely qualitative items today, too thin for 5 real
trends): group system/inbox/market_signals.json + market_signals_
earnings.jsonl items by their existing `category` taxonomy (the same
one team_market_intelligence.py's Latest News already uses -- pos,
restaurant_ai, payments, online_ordering, etc.), across BOTH stock-
market-activity signals (price_watch, volume spikes, 52-week lows) and
qualitative trade-press content. Ranks by recent (last `window_days`)
volume, classifies direction from recent-vs-prior frequency (same
recent-window/prior-window comparison earnings_trend.py already uses
for per-company financial-health trends, generalized here to a
per-category cut). No LLM call, no invented claim -- every fact in the
output (counts, companies, evidence citations) traces directly to real
signal records, reusing team_market_intelligence._allowlist_news_item
for evidence citations so this can never surface a Todd-private field
that allowlist doesn't already vet.

Storage: system/market_intelligence/restaurant_tech_trends.json -- one
current snapshot, regenerated each run (same "most recent generation
persists" convention as today.md/friday_closeout.md), not a version
history.

CLI:
    python3 restaurant_tech_trends.py [--write] [--window-days 30] [--top 5]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import team_market_intelligence as tmi  # noqa: E402

OUTPUT_PATH = ROOT / "system" / "market_intelligence" / "restaurant_tech_trends.json"

# Human-readable category labels now live in team_market_intelligence.py
# (tmi.CATEGORY_LABELS) -- single source of truth shared with the Earnings
# Center's own category column, so a label never drifts between surfaces.

_DEFAULT_WINDOW_DAYS = 30
_DEFAULT_TOP_N = 5

# Bare stock-price-movement signals (a ticker moved X%, elevated volume,
# near a 52-week high/low) count toward category volume/direction --
# Todd's explicit choice to blend all evidence rather than only
# qualitative signals -- but are the least informative thing to show as
# a citation: real trend content (a product launch, a vendor-expansion
# story, a leadership change) should surface first when both exist in a
# category. Bare SEC filing titles ("8-K - Current report") are just as
# uninformative and get the same treatment. Both checks live in
# tmi.is_noise_item -- the single shared definition of "noise" so Latest
# News's hide_noise filter and this module's evidence ranking/filtering
# never disagree.


def _is_substantive_headline(item: dict) -> bool:
    return not tmi.is_noise_item(item)


def _label_for(category: str) -> str:
    return tmi.category_label(category)


def _direction_for(recent_count: int, prior_count: int) -> str:
    if prior_count == 0:
        return "Emerging" if recent_count > 0 else "Insufficient evidence"
    ratio = recent_count / prior_count
    if ratio >= 1.3:
        return "Accelerating"
    if ratio <= 0.7:
        return "Fading"
    return "Established"


def _rationale_for(
    *, direction: str, recent_count: int, prior_count: int,
    qualitative_count: int, window_days: int,
) -> str:
    """A deterministic, numbers-first sentence explaining WHY this category
    was ranked and classified the way it was -- real 2026-09-30 request:
    "it is not a trend just because the CoS says it is a trend -- back up
    the statement with facts." No LLM call, no editorializing: every clause
    here traces to a field already on the trend record, same "no invented
    claim" constraint the rest of this module follows. Deliberately states
    the qualitative_count == 0 case as plainly as the note field already
    does, so a category whose entire volume is price/volume monitoring
    never reads as a confirmed strategic trend."""
    if prior_count == 0:
        volume = (
            f"First appeared in the last {window_days} days: {recent_count} "
            f"signal(s), none in the prior {window_days}-day window."
        )
    else:
        pct = ((recent_count - prior_count) / prior_count) * 100
        sign = "+" if pct >= 0 else ""
        volume = (
            f"{recent_count} signal(s) in the last {window_days} days vs. "
            f"{prior_count} in the prior {window_days} days ({sign}{pct:.0f}%), "
            f"which is why this is classified \"{direction}.\""
        )
    if qualitative_count > 0:
        substance = (
            f" {qualitative_count} of those signal(s) are substantive news or "
            "filings (product launches, funding, leadership, vendor moves --"
            " not bare price moves) -- see the evidence links below."
        )
    else:
        substance = (
            " None of that volume is qualitative news -- every signal behind "
            "this count is public-market price/volume monitoring. Treat the "
            "direction and confidence above as market-activity-only, not a "
            "confirmed strategic trend."
        )
    return volume + substance


def _confidence_for(recent_count: int) -> str:
    if recent_count >= 20:
        return "high"
    if recent_count >= 5:
        return "medium"
    return "low"


def compute_top_trends(*, window_days: int = _DEFAULT_WINDOW_DAYS, top_n: int = _DEFAULT_TOP_N) -> dict:
    all_items = tmi._load_market_signals_json() + tmi._load_market_signals_earnings_jsonl()
    today = date.today()
    recent_cutoff = (today - timedelta(days=window_days)).isoformat()
    prior_cutoff = (today - timedelta(days=2 * window_days)).isoformat()

    recent_by_category: dict[str, list] = {}
    prior_counts: Counter = Counter()
    first_observed: dict[str, str] = {}

    for item in all_items:
        category = item.get("category")
        published = item.get("published_at") or ""
        if not category:
            continue
        if published and (first_observed.get(category) is None or published < first_observed[category]):
            first_observed[category] = published
        if published >= recent_cutoff:
            recent_by_category.setdefault(category, []).append(item)
        elif published >= prior_cutoff:
            prior_counts[category] += 1

    ranked = sorted(recent_by_category.items(), key=lambda kv: len(kv[1]), reverse=True)[:top_n]

    trends = []
    for category, recent_items in ranked:
        recent_count = len(recent_items)
        prior_count = prior_counts.get(category, 0)
        companies = [c for c, _ in Counter(
            i.get("company") for i in recent_items if i.get("company")
        ).most_common(8)]

        # Evidence: substantive content first (not a bare price move),
        # then most strategically-relevant, then most recent -- deduped
        # by headline, allowlisted through the exact same function
        # Latest News uses, so this can never surface a field that
        # allowlist doesn't already vet.
        sorted_items = sorted(
            recent_items,
            key=lambda i: (
                _is_substantive_headline(i),
                i.get("strategic_relevance") == "high",
                i.get("published_at") or "",
            ),
            reverse=True,
        )
        evidence, seen_headlines = [], set()
        for item in sorted_items:
            allowed = tmi._allowlist_news_item(item)
            if allowed["headline"] in seen_headlines:
                continue
            seen_headlines.add(allowed["headline"])
            evidence.append(allowed)
            if len(evidence) >= 5:
                break

        qualitative_count = sum(1 for i in recent_items if _is_substantive_headline(i))
        direction = _direction_for(recent_count, prior_count)
        # Real 2026-09-29 finding: some categories' entire recent volume is
        # routine stock-price/volume monitoring, zero qualitative news --
        # labeling that a clean "Established" technology trend would
        # overclaim what the evidence shows, even though every individual
        # data point is real. `note` kept as a short standalone flag for
        # that specific case (UI shows it as a warning banner); `rationale`
        # below is the full numbers-first justification shown for every
        # trend, not just the zero-qualitative-evidence ones.
        note = None
        if qualitative_count == 0:
            note = (
                "This window's volume is entirely public-market price/volume "
                "monitoring -- no qualitative news or strategic developments "
                "reported for this category in the last "
                f"{window_days} days."
            )
        rationale = _rationale_for(
            direction=direction, recent_count=recent_count, prior_count=prior_count,
            qualitative_count=qualitative_count, window_days=window_days,
        )

        trends.append({
            "trend_id": f"trend-{category}",
            "category": category,
            "name": _label_for(category),
            "sector": "restaurant_technology",
            "direction": direction,
            "confidence": _confidence_for(recent_count),
            "recent_count": recent_count,
            "prior_count": prior_count,
            "qualitative_evidence_count": qualitative_count,
            "note": note,
            "rationale": rationale,
            "window_days": window_days,
            "companies": companies,
            "evidence": evidence,
            "first_observed": first_observed.get(category),
            "last_updated": today.isoformat(),
        })

    return {
        "generated_at": today.isoformat(),
        "window_days": window_days,
        "trends": trends,
    }


def write_trends(result: dict) -> Path:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return OUTPUT_PATH


def get_current_trends() -> dict:
    """What the Team Portal reads -- the persisted snapshot from the most
    recent weekly run, not a live recomputation on every page load.

    Bare price-move/volume-spike/52-week AND bare SEC-filing-title
    evidence citations (tmi.is_noise_item -- same definition Latest News
    uses) are always stripped from each trend's evidence list -- real
    2026-10-02 feedback: showing this as a toggle ("hide stock price/
    volume noise") implied raw price moves were ever legitimate evidence
    for a *named technology trend*, which they aren't; a "Top 5
    Restaurant Technology Trends" citation should never be a bare price
    move regardless of a checkbox's state. recent_count/prior_count/
    direction/confidence are untouched -- those stay computed from all
    evidence, consistent with the "blend all evidence" methodology above;
    only the citations shown to a teammate are filtered."""
    if not OUTPUT_PATH.exists():
        return {"generated_at": None, "window_days": _DEFAULT_WINDOW_DAYS, "trends": []}
    data = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    for trend in data.get("trends", []):
        trend["evidence"] = [
            e for e in trend.get("evidence", []) if not tmi.is_noise_item(e)
        ]
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="persist the result to disk")
    parser.add_argument("--window-days", type=int, default=_DEFAULT_WINDOW_DAYS)
    parser.add_argument("--top", type=int, default=_DEFAULT_TOP_N)
    args = parser.parse_args()

    result = compute_top_trends(window_days=args.window_days, top_n=args.top)
    if args.write:
        path = write_trends(result)
        print(f"wrote {len(result['trends'])} trend(s) to {path}")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
