#!/usr/bin/env python3
"""team_market_intelligence.py — Latest News + Earnings Center for the Team Portal.

Exposes existing market-signal and earnings data to the team, allowlisted
the same way every other Team Portal surface is (team_tech_stack.py's
module docstring): explicit field allowlist, never a blocklist. Confirmed
necessary by direct inspection of the raw source data here, not just by
precedent -- system/inbox/market_signals.json and market_signals_earnings.
jsonl items carry real Todd-only fields alongside the shareable ones
(`why_this_matters_to_todd`, `recommended_action`, `timing_priority`,
`affected_relationships_or_threads` -- the last of which names Todd's own
internal account-research threads by title). None of those fields appear
in the allowlist below.

Earnings data (system/earnings_calendar.yaml, system/earnings_history/
earnings_calls.jsonl, earnings_trend.compute_entity_trend) is public-market
/ public-filing information with no Todd-private layer at all -- exposed
near-verbatim, the same "these are facts either way" treatment
team_portal_api.py already gives the ecosystem-profile/company-profile
routes. The one exception is earnings_calendar.yaml's own `notes` /
`added_by` fields, which can carry Todd's free-text triage notes -- those
are dropped.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import yaml  # noqa: E402
import earnings_monitor  # noqa: E402
import earnings_trend  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import team_artifact_footer as footer  # noqa: E402

MARKET_SIGNALS_PATH = ROOT / "system" / "inbox" / "market_signals.json"
MARKET_SIGNALS_EARNINGS_PATH = ROOT / "system" / "inbox" / "market_signals_earnings.jsonl"
# 2026-10-02: market_source_feeds.py's real, already-scheduled trade-press
# RSS feeder (7 curated sources) writes here -- real feedback found this
# file existed and was already being refreshed, but nothing in this module
# ever read it, so its content never reached Latest News or Top 5 Trends.
# See system/ROADMAP.md, "Team Portal Latest News / Top 5 Trends" for the
# full root-cause writeup.
MARKET_SIGNALS_FEED_PATH = ROOT / "system" / "inbox" / "market_signals_feed.jsonl"
EARNINGS_CALENDAR_PATH = ROOT / "system" / "earnings_calendar.yaml"

# market_source_feeds.py's own category classifier predates this module's
# CATEGORY_LABELS taxonomy and uses a few different names for the same real
# concepts -- normalized here, at the one place this feed is read, rather
# than changing market_source_feeds.py's own output (daily_brief.py's
# condensed-industry-context consumer, via market_signals.py, reads that
# output independently and is untouched by this map).
_FEED_CATEGORY_NORMALIZE = {
    "unit_growth": "operator_expansion",
    "m_and_a": "ma_pe_activity",
}


class NotFoundError(Exception):
    """A company id/ticker the caller passed isn't on the earnings watchlist."""


# Bare stock-price-movement signals (a ticker moved X%, elevated volume,
# near a 52-week high/low) -- real market activity, but the least
# informative thing to show a teammate scanning for actual restaurant-tech
# developments. Shared by Latest News (hide_noise filter below) and
# restaurant_tech_trends.py's evidence ranking/note, so both surfaces agree
# on exactly what counts as noise.
NOISE_SIGNAL_TYPES = {"price_move", "52w_low", "52w_high", "volume_spike"}

# Real 2026-09-30 finding: earnings_monitor.py's own feed sometimes has no
# classified signal_type beyond "material_event"/"earnings_release" and a
# bare SEC filing-type title ("8-K - Current report", "10-Q - Quarterly
# report") -- every field on that item (title, summary, "why it matters")
# repeats the same boilerplate, zero real content. Confirmed live: with
# NOISE_SIGNAL_TYPES hidden, these bare filings were the only thing left
# standing in as Top 5 Trends "evidence" for a category -- just as
# uninformative as a bare price move, so hide_noise treats them the same.
_BARE_FILING_TITLE_PREFIXES = ("8-K", "10-Q", "10-K")


# Human-readable labels for the category taxonomy used across market
# signal records AND earnings_calendar.yaml's own per-company `category`
# field -- same taxonomy, single source of truth (restaurant_tech_trends.py
# imports this rather than keeping its own copy) so a label never drifts
# between the Top 5 Trends and Earnings Center surfaces.
CATEGORY_LABELS = {
    "pos": "POS Modernization",
    "restaurant_ai": "Restaurant AI Adoption",
    "payments": "Payments Platform Convergence",
    "online_ordering": "Online Ordering Evolution",
    "operator_expansion": "Operator Expansion & Growth",
    "back_office_automation": "Back-Office Automation",
    "technology_standardization": "Technology Standardization",
    "loyalty_crm": "Loyalty & CRM",
    "ma_pe_activity": "M&A / PE Activity",
    "executive_change": "Leadership Change",
    "macro_operator_pressure": "Operator Margin Pressure",
    "consumer_demand": "Consumer Demand Shifts",
    "drive_thru_ai": "Drive-Thru AI",
    "kiosk": "Kiosk Expansion",
}


def category_label(category: Optional[str]) -> Optional[str]:
    if not category:
        return None
    return CATEGORY_LABELS.get(category, category.replace("_", " ").title())


def is_noise_item(item: dict) -> bool:
    """True if `item` (a raw market_signals record, or an already-
    allowlisted one carrying at least "signal_type" and "headline"/"title")
    is noise under the "hide stock price/volume noise" filter: a bare
    price/volume signal, or a bare SEC filing title with no real content.
    Single source of truth for what "noise" means -- used by both Latest
    News (hide_noise below) and restaurant_tech_trends.py (evidence
    ranking/filtering), so the two surfaces never disagree."""
    if (item.get("signal_type") or "") in NOISE_SIGNAL_TYPES:
        return True
    title = (item.get("title") or item.get("headline") or "").strip()
    return any(title.startswith(p) for p in _BARE_FILING_TITLE_PREFIXES)


# ---------------------------------------------------------------------------
# Latest News
# ---------------------------------------------------------------------------

def _load_market_signals_json() -> list[dict]:
    if not MARKET_SIGNALS_PATH.exists():
        return []
    data = json.loads(MARKET_SIGNALS_PATH.read_text(encoding="utf-8"))
    return data.get("items", [])


def _load_market_signals_earnings_jsonl() -> list[dict]:
    if not MARKET_SIGNALS_EARNINGS_PATH.exists():
        return []
    items = []
    with open(MARKET_SIGNALS_EARNINGS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return items


def _load_market_signals_feed_jsonl() -> list[dict]:
    """market_source_feeds.py's real trade-press RSS output -- the
    genuinely qualitative vendor+operator content this module was missing
    (see MARKET_SIGNALS_FEED_PATH's own comment above). Same shape as
    market_signals_earnings.jsonl's rows; category values get normalized
    to this module's own taxonomy so _section_for() and CATEGORY_LABELS
    route/label them correctly."""
    if not MARKET_SIGNALS_FEED_PATH.exists():
        return []
    items = []
    with open(MARKET_SIGNALS_FEED_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            category = item.get("category")
            if category in _FEED_CATEGORY_NORMALIZE:
                item["category"] = _FEED_CATEGORY_NORMALIZE[category]
            items.append(item)
    return items


# Allowlist only -- see module docstring. Never pass a raw signal record
# through; every field a teammate sees is named explicitly here.
def _allowlist_news_item(item: dict) -> dict:
    return {
        "headline": item.get("title"),
        "source": item.get("source_name"),
        "published_date": item.get("published_at"),
        "summary": item.get("pain_point_or_priority") or None,
        "why_it_matters_to_gp": item.get("restaurant_tech_vendor_implication") or None,
        "companies": [c for c in [item.get("company")] if c],
        "category": item.get("category"),
        "side": item.get("side"),  # operator_demand | vendor_supply -- Restaurant vs Technology filter
        "gp_relevance": item.get("strategic_relevance"),
        "source_link": item.get("url"),
        # Classification only (e.g. "price_move", "vendor_expansion") --
        # not Todd-private, and lets the UI's "hide stock price/volume
        # noise" filter work off the same field server and client side.
        "signal_type": item.get("signal_type"),
    }


def _section_for(item: dict) -> str:
    """Best-effort grouping for the spec's named sections. Falls back to
    Top Stories for anything that doesn't match a more specific bucket --
    intentionally coarse for Phase 1; a tuned taxonomy is Phase 2 work
    once the Trend Registry exists to classify against."""
    category = (item.get("category") or "").lower()
    signal_type = (item.get("signal_type") or "")
    side = item.get("side")
    if "earning" in category or "earning" in signal_type.lower():
        return "Earnings"
    if "leadership" in category or "leadership" in signal_type.lower():
        return "Leadership Changes"
    # "ma_pe" matches this module's own "ma_pe_activity" category value --
    # real 2026-10-02 finding: that value, and market_source_feeds.py's
    # "m_and_a" (normalized to it above), never matched any of the other
    # keys here, so this branch was dead code for every real category this
    # taxonomy actually produces. "merger"/"acquisition" also checked
    # against signal_type, matching market_source_feeds.py's own
    # merger_acquisition/divestiture_selloff/investment_announcement
    # classifications.
    if (
        any(k in category for k in ("m&a", "ma_pe", "acquisition", "funding", "investment"))
        or any(k in signal_type.lower() for k in ("merger", "acquisition", "divestiture", "investment_announcement"))
    ):
        return "M&A and Funding"
    # Real 2026-09-29 finding: no record in either underlying feed ever
    # carries source_type == "press_release" -- that literal value never
    # occurs (checked market_signals.json and market_signals_earnings.
    # jsonl directly), so this branch was permanently dead code and
    # "Press Releases" was an always-empty section. The real equivalent
    # is vertical_trade_company_announcement -- a company's own
    # announcement, picked up by trade press (e.g. a vendor-expansion or
    # product-launch item); SEC-filed earnings press releases already
    # have their own home in the Earnings Center (exhibit_url).
    if item.get("source_type") == "vertical_trade_company_announcement":
        return "Press Releases"
    if side == "vendor_supply":
        return "Restaurant Technology"
    if side == "operator_demand":
        return "Restaurant Operators"
    return "Top Stories"


def get_latest_news(
    *, days: int = 7, company: Optional[str] = None, category: Optional[str] = None,
    side: Optional[str] = None, watchlist_only: bool = False, hide_noise: bool = False,
) -> dict:
    raw = _load_market_signals_json() + _load_market_signals_earnings_jsonl() + _load_market_signals_feed_jsonl()
    cutoff = date.today() - timedelta(days=days)

    filtered = []
    for item in raw:
        pub = item.get("published_at")
        if pub:
            try:
                if date.fromisoformat(pub) < cutoff:
                    continue
            except ValueError:
                pass
        if company and item.get("company") != company:
            continue
        if category and item.get("category") != category:
            continue
        if side and item.get("side") != side:
            continue
        if hide_noise and is_noise_item(item):
            continue
        filtered.append(item)

    items = [_allowlist_news_item(i) for i in filtered]
    items.sort(key=lambda i: i.get("published_date") or "", reverse=True)

    sections: dict[str, list[dict]] = {}
    for raw_item, allowed in zip(filtered, items):
        sections.setdefault(_section_for(raw_item), []).append(allowed)

    return {
        "window_days": days,
        "items": items,
        "sections": sections,
        **footer.footer_fields(generated_by="RBB Team Portal", data_as_of=date.today().isoformat()),
    }


def get_news_companies() -> dict:
    """Company list for the Latest News filter dropdown. Real 2026-09-29
    feedback: a free-text company filter meant a teammate had to guess
    the feed's exact spelling, and Global Payments -- Todd's own
    employer's parent company, tracked with strategic_relevance "high" in
    earnings_calendar.yaml -- didn't show up at all if it had zero recent
    news items in the current window. Built from the earnings_calendar
    watchlist (both operator_demand and vendor_supply sides) unioned with
    whatever additional company names actually appear in the signal
    feeds, so every tracked company/vendor is always selectable.

    restaurant_technology is deliberately restricted to vendors the
    watchlist itself marks as real, actively-relevant restaurant-tech
    coverage (watch_priority true, or strategic_relevance high/medium) --
    an entry like "Genius Sports" (side=vendor_supply, but its own notes
    field reads "Monitoring only -- adjacent payments/data analytics",
    strategic_relevance "low", watch_priority false) is tracked only
    because its name collides with Global Payments' own "Genius"
    platform, not because it's an actual restaurant-tech company. Lumping
    it in with real vendors would mislead a teammate scanning the
    dropdown for restaurant-tech competitors, so it lands in `other`
    instead."""
    operators: set[str] = set()
    vendors: set[str] = set()
    adjacent: set[str] = set()
    for co in _load_calendar_entries():
        name = co.get("name")
        if not name:
            continue
        if co.get("side") == "operator_demand":
            operators.add(name)
        elif co.get("side") == "vendor_supply":
            is_real_vendor = bool(co.get("watch_priority")) or co.get("strategic_relevance") in ("high", "medium")
            (vendors if is_real_vendor else adjacent).add(name)
        else:
            adjacent.add(name)

    known = operators | vendors | adjacent
    for item in _load_market_signals_json() + _load_market_signals_earnings_jsonl():
        name = item.get("company")
        if not name or name in known:
            continue
        (vendors if item.get("side") == "vendor_supply" else operators).add(name)
        known.add(name)

    # Real 2026-09-30 finding (Todd): the dropdown was still scoped to
    # earnings_calendar.yaml's curated ~13-vendor watchlist, not the full
    # restaurant-tech vendor universe this system actually tracks (127
    # real vendor entities in ecosystem_intelligence.json, now also
    # including product-line subsidiaries like "PAR Ops"/"NCR Aloha
    # POS"). Union in every top-level vendor entity (entity_type ==
    # "vendor", no owner_entity_id) -- deliberately EXCLUDING product-
    # line subsidiaries here: a raw market signal's `company` field is
    # always the parent company's own watchlist name (see
    # resolve_competitor's same root-owner reasoning), so a subsidiary
    # like "PAR Timer" would sit in this dropdown as a filter option that
    # can never actually match anything, which is worse than not listing
    # it. Brand/operator side intentionally left as the curated watchlist
    # for now -- the ecosystem graph's ~1,763 brand entities are a much
    # bigger UX question (a flat native <select> can handle it, but it's
    # a real design call, not an obvious "union it in too" the way the
    # vendor side was) -- a real follow-up if Todd wants that too.
    graph = ei._read_graph()
    for entity in graph.get("entities") or []:
        if entity.get("entity_type") != "vendor" or entity.get("owner_entity_id"):
            continue
        name = entity.get("name")
        if name and name not in known:
            vendors.add(name)
            known.add(name)

    return {
        "restaurant_operators": sorted(operators),
        "restaurant_technology": sorted(vendors),
        "other": sorted(adjacent),
    }


# ---------------------------------------------------------------------------
# Earnings Center
# ---------------------------------------------------------------------------

def _load_calendar_entries() -> list[dict]:
    if not EARNINGS_CALENDAR_PATH.exists():
        return []
    data = yaml.safe_load(EARNINGS_CALENDAR_PATH.read_text(encoding="utf-8")) or {}
    return data.get("companies", data) if isinstance(data, dict) else data


def _calendar_summary(co: dict, **extra) -> dict:
    return {
        "name": co.get("name"),
        "ticker": co.get("ticker"),
        "side": co.get("side"),
        "category": co.get("category"),
        # Real 2026-09-30 feedback: the raw snake_case category code
        # ("restaurant_ai") read as meaningless in the Earnings Center
        # table. category_label is the same human-readable taxonomy label
        # Top 5 Trends already uses for this value.
        "category_label": category_label(co.get("category")),
        "strategic_relevance": co.get("strategic_relevance"),
        **extra,
    }


def _history_item(row: dict) -> dict:
    """source_url is the SEC EDGAR filing INDEX page (a directory
    listing, not the report itself); exhibit_url, when present, is the
    actual earnings-release document/call-summary exhibit -- real
    2026-09-29 feedback: the team wants a link to the actual report, not
    just the index. report_url below prefers exhibit_url and falls back
    to source_url so the UI always has one usable "read the report" link
    even for rows earnings_monitor.py never resolved an exhibit for
    (e.g. a 10-Q with no separate earnings-release exhibit)."""
    excerpt = row.get("excerpt") or ""
    return {
        "event_date": row.get("event_date"),
        "title": row.get("title"),
        "source_type": row.get("source_type"),
        "source_url": row.get("source_url"),
        "exhibit_url": row.get("exhibit_url"),
        "report_url": row.get("exhibit_url") or row.get("source_url"),
        "signal_dimensions": row.get("signal_dimensions") or [],
        "excerpt": excerpt[:600],
    }


def get_earnings_center() -> dict:
    calendar = _load_calendar_entries()
    today = date.today()
    recently_reported, upcoming = [], []

    for co in calendar:
        last = co.get("last_reported_date")
        if last:
            try:
                if (today - date.fromisoformat(last)).days <= 45:
                    recently_reported.append(_calendar_summary(co, last_reported_date=last))
            except ValueError:
                pass
        next_date = earnings_monitor._estimate_next_report_date(co, today)  # noqa: SLF001 -- same-codebase reuse, matches earnings_trend.py's own precedent
        if next_date:
            days_until = (next_date - today).days
            if 0 <= days_until <= 60:
                upcoming.append(_calendar_summary(
                    co, estimated_next_report_date=next_date.isoformat(), days_until=days_until,
                ))

    recently_reported.sort(key=lambda c: c.get("last_reported_date") or "", reverse=True)
    upcoming.sort(key=lambda c: c.get("days_until", 999))

    return {
        "recently_reported": recently_reported,
        "upcoming": upcoming,
        **footer.footer_fields(generated_by="RBB Team Portal", data_as_of=today.isoformat()),
    }


def _find_calendar_entry(company_id: str) -> Optional[dict]:
    for co in _load_calendar_entries():
        if co.get("name") == company_id or co.get("ticker") == company_id:
            return co
    return None


def get_earnings_company_detail(company_id: str) -> dict:
    co = _find_calendar_entry(company_id)
    if co is None:
        raise NotFoundError(f"'{company_id}' is not on the earnings watchlist")

    history = earnings_monitor.get_company_earnings_history(co["name"], limit=8)
    trend = earnings_trend.compute_entity_trend(co["name"])  # already evidence-based, no editorial content

    return {
        "company": co.get("name"),
        "ticker": co.get("ticker"),
        "side": co.get("side"),
        "category": co.get("category"),
        "history": [_history_item(h) for h in history],
        "trend": trend,
        **footer.footer_fields(generated_by="RBB Team Portal", data_as_of=date.today().isoformat()),
    }


# Real 2026-09-30 finding: generate_account_talking_points() only ever
# used the bare filing TITLE ("8-K - Current report") -- meaningless, as
# flagged live. But earnings_monitor.record_earnings_history() already
# fetches and persists real press-release/MD&A excerpt text into
# earnings_calls.jsonl automatically, every scheduled cycle a new earnings
# event is detected (see that module's "RB 2026-08-10 feature request"
# comment) -- 736 of 811 recorded events on file already carry real
# excerpt text (confirmed live: e.g. Starbucks' Q3 FY26 excerpt opens
# "...Global Q3 Comparable Store Sales Up 7.9%...Q3 Consolidated Net
# Revenues Down 1% to $9.3 billion...Raises Fiscal Year 2026 Guidance").
# The "identify a new report, generate a summary" half of the ask is
# already live in the intelligence cycle; what was missing was actually
# USING that captured text here instead of discarding it for a bare
# title. No LLM call -- same "no invented claim" discipline as Top 5
# Trends' rationale: every point below is lifted verbatim from a real,
# already-fetched excerpt, never synthesized.
_SENTENCE_SPLIT_RE = re.compile(r"(?<!\d)\.\s+(?=[A-Z])")
_HAS_FIGURE_RE = re.compile(r"\d")
# Real 2026-09-30 finding: a company can file more than one real 8-K on
# the exact same event_date (e.g. McDonald's 2026-08-04 earnings release
# AND a same-day leadership-announcement 8-K) -- picking strictly by
# recency alone can surface the wrong one first. These keywords bias
# toward the genuine earnings-content filing when more than one same-
# dated candidate has a usable excerpt.
_EARNINGS_CONTENT_KEYWORDS_RE = re.compile(
    r"\b(quarter|comparable|same-store|revenue|earnings|guidance|"
    r"net income|fiscal year|results of operations|EPS)\b", re.I)


def _excerpt_talking_points(excerpt: Optional[str], max_points: int = 3) -> list[str]:
    """Split a real earnings-release excerpt into candidate sentences and
    prefer the ones carrying an actual figure (revenue, EPS, comp sales,
    guidance, unit count, %) -- the substantive content of a release.
    Falls back to the excerpt's own leading sentence(s) when none qualify
    (e.g. pure boilerplate), so a real point is never silently dropped in
    favor of nothing. Verbatim extraction only -- no rewriting."""
    excerpt = (excerpt or "").strip()
    if not excerpt:
        return []
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(excerpt) if s.strip()]
    if not sentences:
        return [excerpt[:280]]
    numeric = [s for s in sentences if _HAS_FIGURE_RE.search(s)]
    chosen = (numeric or sentences)[:max_points]
    return [s if s.endswith((".", "!", "?")) else s + "." for s in chosen]


def generate_account_talking_points(company_id: str) -> dict:
    """Spec's 'Generate account talking points' action -- built from the
    same evidence-based fields history/trend already expose, never from
    any of Todd's own account_intelligence notes (that's the Background
    Brief's job, behind its own existing allowlist). Leads with the
    frequency-trend read, then real sentences lifted from the most recent
    1-2 events that have a captured excerpt, each tagged with its date and
    a link to the actual filing/exhibit so a rep can verify or read
    further. Falls back to bare titles only for a company with no
    captured excerpt yet (e.g. a private company earnings_monitor.py has
    no EDGAR CIK for)."""
    detail = get_earnings_company_detail(company_id)
    points = []
    if detail["trend"].get("status") == "ok":
        points.append(detail["trend"]["answer"])

    # Score every event with a usable excerpt, most likely genuine
    # earnings content first, most recent as the tiebreaker -- rather than
    # picking strictly by recency, which can surface a same-dated but
    # unrelated 8-K (e.g. a leadership announcement) ahead of the real
    # earnings-release excerpt.
    candidates = []
    for h in detail["history"]:
        sentences = _excerpt_talking_points(h.get("excerpt"))
        if not sentences:
            continue
        looks_like_earnings = bool(_EARNINGS_CONTENT_KEYWORDS_RE.search(h.get("excerpt") or ""))
        candidates.append((looks_like_earnings, h.get("event_date") or "", h, sentences))
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)

    events_used = 0
    for _looks_like_earnings, _date_key, h, sentences in candidates:
        events_used += 1
        label = h.get("event_date") or "undated"
        link = h.get("report_url")
        for s in sentences:
            line = f"{label}: {s}"
            if link:
                line += f" ({link})"
            points.append(line)
        if events_used >= 2:
            break

    if events_used == 0:
        for h in detail["history"][:3]:
            if h.get("title"):
                points.append(f"{h['event_date']}: {h['title']}")

    if not points:
        points.append(f"No recent earnings-history talking points available for {detail['company']} yet.")
    return {
        "company": detail["company"], "ticker": detail["ticker"], "talking_points": points,
        **footer.footer_fields(generated_by="RBB Team Portal", data_as_of=date.today().isoformat()),
    }
