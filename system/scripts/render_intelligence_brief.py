#!/usr/bin/env python3
"""render_intelligence_brief.py — pre-render the RB Intelligence Brief (Part 1).

Runs at 5am after refresh_sources.py and daily_brief.py have completed.
Reads from the daily_brief cache, renders all 12 canonical sections into
markdown, applies deduplication (skips URLs seen in the last 7 days unless
the item is a named corporate event), and writes:

  system/briefs/YYYY-MM-DD-intelligence-brief.md   — final markdown
  system/briefs/YYYY-MM-DD-intelligence-brief.json — structured metadata

Also updates system/.cache/rendered_headlines.json with today's URLs.

Usage:
    python3 render_intelligence_brief.py [--date YYYY-MM-DD] [--dry-run] [--force]
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_assist
import rb_core as core
import entity_convergence_scan
import render_daily_brief
import intelligence_insight_ranking

BRIEFS_DIR = core.SYSTEM_DIR / "briefs"
RENDERED_HEADLINES_PATH = core.SYSTEM_DIR / ".cache" / "rendered_headlines.json"
STORY_LEDGER_PATH = core.SYSTEM_DIR / ".cache" / "story_ledger.json"
# RB-2026-09-08: separate persisted cross-day dedup state for the team
# edition, mirroring RENDERED_HEADLINES_PATH/STORY_LEDGER_PATH above -- see
# render_team_edition's docstring note on why this was missing entirely.
# Kept separate from the personal brief's files rather than shared: the two
# editions run on different cadences (daily vs. Tuesday/Friday) and a story
# already shown to Todd in the personal brief may still be genuinely new to
# a GP/Genius reader who never sees that edition.
TEAM_RENDERED_HEADLINES_PATH = core.SYSTEM_DIR / ".cache" / "team_rendered_headlines.json"
TEAM_STORY_LEDGER_PATH = core.SYSTEM_DIR / ".cache" / "team_story_ledger.json"
TEAM_SYNTH_CACHE_PATH = core.SYSTEM_DIR / ".cache" / "team_brief_synthesis.json"
# RB-2026-08-25: separate cache/key-space from TEAM_SYNTH_CACHE_PATH above --
# see _synthesize_personal_why_batch's docstring for why A-D of the personal
# edition needed its own synthesis pass instead of reusing the team one.
PERSONAL_WHY_SYNTH_CACHE_PATH = core.SYSTEM_DIR / ".cache" / "personal_brief_why_synthesis.json"
DAILY_BRIEF_CACHE = core.SYSTEM_DIR / ".cache" / "daily_brief.json"
DEEP_RESEARCH_COVERAGE_PATH = core.SYSTEM_DIR / "research" / "deep_research_coverage.json"
ADAPTIVE_RESEARCH_RECEIPTS_PATH = core.SYSTEM_DIR / "research" / "adaptive_research_receipts.jsonl"
# Lifecycle entries (career phase, opportunity mutations) are meant to be
# one-shot forever, but _load_prior_brief_state only scans the last 7 days
# of rendered briefs — a fact shown once and never repeated within that
# window (e.g. offer-accepted on 2026-06-21) silently ages out and gets
# re-rendered as if new once the 7-day lookback no longer covers it. This
# is a separate, permanent store with no expiry.
RENDERED_LIFECYCLE_PATH = core.SYSTEM_DIR / ".cache" / "rendered_lifecycle_events.json"

# Named corporate event signal badges that bypass the dedup gate
CORPORATE_EVENT_BADGES = {"ACQUISITION", "EARNINGS", "FUNDING", "EXEC DEPARTURE", "EXEC HIRE", "M&A", "IPO"}
DEDUP_WINDOW_DAYS = 7
# World/national headlines are a daily front page, not a weekly digest — BBC/
# NPR/Axios's small daily pool was getting exhausted by the 7-day window,
# leaving nothing but the (separately filtered) high-volume Yahoo analyst-note
# feed to backfill the section for most of the week.
WORLD_NATIONAL_DEDUP_WINDOW_DAYS = 3
# RB-DEFECT-2026-07-20: these two constants used to be defined independently
# here AND in daily_brief.py -- two copies of the same "7-day base window,
# 21-day corporate ceiling" rule, free to drift out of sync with each other.
# Both now source from the one shared definition in rb_core.py.
_CORPORATE_EVENT_MAX_AGE_DAYS = core.CORPORATE_EVENT_MAX_AGE_DAYS
FRESHNESS_GATE_DAYS = core.HEADLINE_FRESHNESS_DAYS
# RB-2026-09-25: Todd's explicit call -- kill the corporate grace entirely.
# This used to let a corporate-badged item (ACQUISITION/FUNDING/EXEC HIRE/
# BANKRUPTCY/etc.) re-render for multiple days in low-volume sections
# (restaurant_tech, restaurant_industry) rather than let the section go
# thin on a slow news day (RB-DEFECT-2026-07-10). In practice this meant
# the exact same headline and "why it matters" text repeating verbatim for
# up to 4 days -- e.g. a real Digital Transactions acquisition brief ran
# unchanged 2026-09-20 through 09-22, and a funding item ran unchanged
# 2026-09-23/09-24 -- read as stale/repetitive rather than "still active."
# Todd's call: a section that's genuinely thin because there's no fresh
# news should render thin (or empty), not recycle old content to look
# fuller. _render_headline_section already has a real, honest empty/thin
# fallback ("*0 material headlines this cycle.*" / "*N items this
# cycle.*") -- nothing new needed there, this constant was the only thing
# standing in its way. Now uniform across every section, matching the
# 1-day treatment world/national already used (see _is_duplicate's and
# StoryLedger.should_render's docstrings for the exact "shown on day 0,
# suppressed from day 1" mechanics this still relies on).
CORPORATE_DEDUP_GRACE_DAYS = 1
MIN_HEADLINES_PER_SECTION = 5

# ---------------------------------------------------------------------------
# A/B section noise filters — titles matching these patterns are discarded
# regardless of source. These are retail investor / lifestyle content that
# should never surface in a CoS-to-CEO intelligence brief.
# ---------------------------------------------------------------------------
_WORLD_NOISE_TITLE_PATTERNS = [
    # Analyst note columns
    " here is why", "price target boosted", "price target raised", "price target cut",
    "price target trimmed", "price target lowered", "price target lifted",
    "upgraded at ", "downgraded at ", "initiated at ", "reiterates buy",
    "reiterates hold", "reiterates sell", "raises price target", "cuts price target",
    "trims price target", "boosts price target",
    # Dividend / stock screener content
    "best nasdaq stocks", "best s&p stocks", "best dow stocks",
    "stocks to buy for dividends", "dividend stocks", "stocks to watch",
    "analyst forecast", "analyst rating",
    # Lifestyle / cooking / personal finance noise
    "leftovers", "cookbook", "food scraps", "fridge", "recipe",
    "personal finance", "how to save money", "budgeting tips",
    "mindset shift", "mental health", "self-care",
    # Consumer/lifestyle stories that slip through BBC Business
    "rogue builder", "got the tennis bug", "play sport without paying",
    "wimbledon", "irmaa hits retirees", "retirees two years after",
    "property sale tax", "how to play", "sport on a budget",
    "home improvement scam", "builder spent", "lanzarote",
    # Travel/airline in wrong section (belt-and-suspenders; also caught in D filter)
    "hour flight", "non-stop flight", "airline", "airport",
    # Clickbait analyst-warning columns (not a discrete event)
    "never seen before", "huge warning for investors",
]

# Sell-side research notes shaped "<Firm> <Verb> <Company> ..." — e.g. "Piper
# Sandler Initiates Visa Inc. (V) With Overweight Rating", "UBS Reaffirms Buy
# on CBRE Group (CBRE)", "Goldman Sachs and Bernstein Assess Fiserv Inc.
# (FISV) Following CEO Transition". These pass the positive relevance gate
# because they're keyword-rich in exactly the domains it targets (payments,
# fintech, banking, AI) — matched by firm name at/near the start of the
# title plus a rating-note verb, rather than an exhaustive phrase list,
# since firms and phrasing vary endlessly.
_JUNK_URL_PATTERNS = ["mail.google.com", "click1.", "click2.", "/click/", "/Forward.do", "utm_source=", "unsubscribe"]

_NEWSLETTER_JUNK_URL_PATTERNS = ["mail.google.com", "/Forward.do", "unsubscribe",
                                 "update subscriptions", "suggest a story", "share this newsletter",
                                 "resources.industrydive.com", "tradepub.com", "/webinar",
                                 "utm_medium=Event"]
_NEWSLETTER_JUNK_TITLE_PATTERNS = ["share this newsletter", "update subscriptions",
                                   "suggest a story", "subscribe"]
# RB-2026-08-25: confirmed live -- "::The latest in pizza news & marketing::"
# rendered as a D+ item, with a generic "Why it matters" ("Staying updated
# on the latest pizza marketing trends is crucial...") -- exactly the
# "generic, template" language the editorial standard forbids. This isn't a
# real article headline, it's a newsletter section masthead/wrapper: a real
# headline never opens and closes with the same run of decorative
# punctuation. Reject titles wrapped in matching leading/trailing
# non-alphanumeric runs of 2+ characters (::...::, ***...***, ---...---).
_DECORATIVE_MASTHEAD_TITLE_RE = re.compile(r"^([^\w\s]{2,})\s*.+?\s*\1\s*$")


def _newsletter_usable_article_count(item: dict) -> int:
    """Cheap pre-pass score for picking the best of several eligible editions
    of the same publication -- counts non-junk article links, ignoring the
    relevance/rendered-elsewhere filtering the main render loop applies
    later (good enough for ranking, not meant to be the final list)."""
    extras = item.get("extras") or {}
    articles = extras.get("articles") or []
    count = sum(
        1 for a in articles
        if a.get("url") and a.get("title")
        and not any(s in (a.get("url") or "").lower() for s in _NEWSLETTER_JUNK_URL_PATTERNS)
        and not any(s in (a.get("title") or "").lower() for s in _NEWSLETTER_JUNK_TITLE_PATTERNS)
    )
    if count == 0 and (extras.get("body_summary") or "").strip():
        return 0  # has some content (a summary) but zero articles -- still ranks below any real article count
    return count
# Word-boundary matched (not bare substring) — "ai" and "pos" as naive
# substrings matched ANY word containing those letters ("entertainment",
# "available" both contain "ai"; "purpose", "exposed" both contain "pos"),
# letting generic BBC world-news stories (an ITV/Sky media deal, a Wegovy
# pill story) through Section D as if they were restaurant technology.
_TECH_KEYWORDS = [r"technology", r"tech", r"ai", r"software", r"platform", r"payments?", r"pos",
                  r"digital", r"automation", r"kiosk", r"ordering", r"data", r"analytics",
                  r"robotics", r"cloud", r"saas", r"integration", r"worldpay", r"toast", r"par",
                  r"olo", r"miso", r"grubhub", r"doordash", r"uber eats"]
_TECH_KEYWORD_RE = re.compile(r"\b(" + "|".join(_TECH_KEYWORDS) + r")\b", re.IGNORECASE)


def _select_restaurant_tech_items(sections: dict) -> list[dict]:
    """Section D candidate pool: restaurant_technology_headlines plus the
    broader what_todd_doesnt_know_yet catch-all.

    Items already sourced from restaurant_technology_headlines are trusted at
    face value — that section membership (populated by daily_brief.py's
    dedicated restaurant-tech industry feed) IS the domain classification, so
    re-running a narrow keyword whitelist against it dropped legitimate items
    whose headline just didn't happen to contain a listed keyword (e.g.
    "GoTab Acquires Fishbowl", "Dishio Surpasses 350 Restaurant Customers" —
    both restaurant-tech vendor news with no literal "tech"/"pos"/"software"
    in the title), collapsing a 10-item section down to 1-2 survivors.
    The keyword/domain gate still applies to the what_todd_doesnt_know_yet
    catch-all, which mixes every domain and genuinely needs filtering to keep
    generic world news out of Section D."""
    tech_items = []
    for t_item in sections.get("restaurant_technology_headlines", []):
        extras = t_item.get("extras") or {}
        url = (extras.get("source_url") or "").strip()
        if any(p in url for p in _JUNK_URL_PATTERNS):
            continue
        # RB-DEFECT-2026-07-08: some Section D sources (e.g. Restaurant Dive)
        # publish general industry/financial news alongside genuine tech
        # coverage, all tagged domain=restaurant_technology upstream simply
        # because that's the feed bucket they came from -- not because the
        # content is about technology. "Jersey Mike's rapid growth in 4
        # charts" (systemwide sales CAGR) rendered in Section D purely
        # because trusting section membership (the fix above) stopped
        # keyword-checking it. The upstream classifier already tags real
        # events (acquisition/funding/customer_win/deployment, evidenced by a
        # signal_badge) with a specific signal_type -- only the catch-all
        # "general" bucket (signal_type == "general", the lowest-confidence
        # tier) needs a content check, since that's the only tier where pure
        # business/financial filler and genuine tech news are mixed together.
        if (extras.get("signal_type") or "").lower() == "general":
            title_lower = (t_item.get("title") or "").lower()
            if not _TECH_KEYWORD_RE.search(title_lower):
                continue
        tech_items.append(t_item)
    for t_item in sections.get("what_todd_doesnt_know_yet", []):
        extras = t_item.get("extras") or {}
        url = (extras.get("source_url") or "").strip()
        title_lower = (t_item.get("title") or "").lower()
        if any(p in url for p in _JUNK_URL_PATTERNS):
            continue
        # RB-DEFECT-2026-07-24: a watchlist-status item (title shape
        # "{Entity} — {Relevant Activity|New Activity|Escalation}", tagged
        # with extras.watchlist_status) already has its own home in Section
        # F -- but "Serve Robotics — Relevant Activity" still passed the
        # tech-keyword check below purely because the watched ENTITY'S OWN
        # NAME contains a listed keyword ("robotics"), duplicating a
        # zero-content earnings-call-date press release into Section D as
        # if it were genuine restaurant-tech news. Watchlist-sourced items
        # are excluded from this catch-all outright; the keyword/domain
        # checks below are for genuine scraped news headlines only.
        if extras.get("watchlist_status"):
            continue
        if url and _TECH_KEYWORD_RE.search(title_lower):
            tech_items.append(t_item)
        elif url and extras.get("domain") == "restaurant_technology":
            tech_items.append(t_item)
    return tech_items


_ANALYST_NOTE_PATTERN = re.compile(
    r"^(piper sandler|bofa|td cowen|benchmark|ubs|citizens|cantor fitzgerald|"
    r"goldman sachs|bernstein|morgan stanley|jpmorgan|jp morgan|wells fargo|"
    r"raymond james|evercore|wedbush|barclays|citigroup|deutsche bank|"
    r"rbc capital|keybanc|stifel|truist|oppenheimer|baird|needham|"
    r"jefferies|mizuho|scotiabank|hsbc)\b"
    # RB-DEFECT-2026-08-14: "Needham raises Agilysys stock price target on
    # Marriott rollout" slipped through Section F's Watchlist rollup (which
    # never runs this pattern at all -- see below) because "raises...price
    # target" isn't a verb this list recognized even when it IS checked --
    # only the upgrade/downgrade/maintain family was covered, not the far
    # more common "raises/cuts/lowers/reiterates ... price target" phrasing.
    r".{0,80}\b(initiates|highlights|reaffirms|assess|assesses|maintains|"
    r"upgrades|downgrades|raises|cuts|lowers|reiterates)\b",
)

# RB-DEFECT-2026-08-14: retail-investor "should I buy this stock" SEO content
# (simplywall.st, Motley Fool, Zacks) -- "Is NCR Voyix (VYX) Undervalued Or Is
# Its SaaS Shift Already Priced In?" -- reached Section F's Watchlist rollup
# as if it were a tracked-entity news event. Distinct from the "How {Company}
# ({TICKER}) Is ... Strategy" shape below: this is the "Is {Company} ({TICKER})
# ... ?" valuation-question shape.
_INVESTOR_OPINION_PATTERN = re.compile(
    r"^is\s+.{2,60}\([a-z]{1,6}\)\s+.{3,100}\?", re.IGNORECASE,
)
_INVESTOR_OPINION_DOMAINS = ("simplywall.st",)

# RB-DEFECT-2026-07-12: syndicated single-ticker "investor explainer" content
# ("How Norfolk Southern (NSC) Is Navigating Regulatory Review to Advance Its
# Transformational Rail Merger Strategy") is a Yahoo Finance/Motley-Fool-style
# SEO template, not a hard-news report of an actual event that day -- it
# passed the world/national relevance gate (and even earned an [ACQUISITION]
# badge, since "merger" appears in the title) purely because the template is
# keyword-rich, then rendered as if a US domestic railroad's regulatory
# filing were "World News." The "How {Company} ({TICKER}) Is ... {Noun}
# Strategy" shape is specific enough to this content-farm template that a
# genuine news headline essentially never matches it.
_TICKER_EXPLAINER_PATTERN = re.compile(
    r"^how\s+.{2,60}\([a-z]{1,6}\)\s+is\s+.{5,100}\bstrategy\b", re.IGNORECASE,
)

# RB-DEFECT-2026-07-12: Yahoo Finance publishes this exact templated title
# daily regardless of any actual news ("Mortgage and refinance interest
# rates today, {date}: Rates moving {up/lower} today") -- an evergreen rate
# tracker, not a news event, and the canonical spec explicitly calls out
# "evergreen observations... = skip, not news." It slipped through the
# positive relevance gate because "interest rate" is a listed macro keyword
# and the title happens to contain "interest rates."
_MORTGAGE_RATE_TRACKER_PATTERN = re.compile(
    r"mortgage and refinance interest rates today", re.IGNORECASE,
)

# RB-DEFECT-2026-08-17: confirmed live -- four of seven items in a single
# National Headlines section were generic financial-media analysis/investor-
# personality content with no specific event, exactly the "thematic
# headline... analyst summary, not a news event" pattern the canonical spec
# prohibits, but none of the existing patterns caught this shape:
#   "Analysis-Big investors hunt for tomorrow's AI winners as capex angst fades"
#   "Fed Chair Kevin Warsh's Job Just Got Much Easier. Here's What's Likely
#    Next for the Stock Market As a Result."
#   "Billionaire Bill Ackman doubles down on these stocks in Q2"
#   "'Spending is leading to earnings': Wall Street strategists see payoff..."
# Four distinct templates, each specific enough that real hard news
# essentially never matches it: a Reuters/AP wire "Analysis-" prefix, a
# speculative "here's what's likely next" listicle, "billionaire/investor
# {verb}s on these stocks" personality content, and a quote-lead ('...':
# {source} says/sees/thinks) template.
_ANALYSIS_PREFIX_PATTERN = re.compile(r"^analysis[\-:]", re.IGNORECASE)
_SPECULATIVE_LISTICLE_PATTERN = re.compile(
    r"here'?s what'?s (likely )?next|here'?s what (it|that|this) means", re.IGNORECASE,
)
_INVESTOR_PERSONALITY_PATTERN = re.compile(
    r"^(billionaire|hedge fund (manager|billionaire))\b.{0,60}\b"
    r"(doubles? down|bets? on|loads? up|dumps?|sells? off|buys?)\b", re.IGNORECASE,
)
_QUOTE_LEAD_ANALYSIS_PATTERN = re.compile(
    r"^['‘’\"“”][^'\"‘’“”]{5,80}['‘’\"“”]\s*:\s*"
    r".{0,60}\b(strategists?|analysts?|economists?)\b", re.IGNORECASE,
)

# Positive relevance gate for world/national headlines.
# An item must match at least one keyword to pass — prevents consumer/lifestyle
# stories from BBC Business and Yahoo Finance from leaking into executive briefings.
_WORLD_RELEVANCE_KEYWORDS = [
    # Macro / financial
    "economy", "gdp", "inflation", "interest rate", "federal reserve", "fed ",
    "recession", "trade war", "tariff", "sanctions", "currency", "dollar",
    "treasury", "bond yield", "debt ceiling", "fiscal",
    # Oil / energy prices — a whole category (politics, wars, oil prices,
    # consumer spending) was missing entirely; "Diesel sees biggest monthly
    # fall in 26 years" failed the old gate since nothing matched "fuel".
    "oil price", "fuel price", "gas price", "diesel", "opec", "energy price",
    "crude oil", "gasoline",
    # Labor market — jobs/unemployment data is macro news, not business trivia.
    "jobs report", "labor market", "job market", "unemployment", "layoffs",
    "hiring slowdown",
    # Consumer spending
    "consumer spending", "retail sales", "consumer confidence",
    # Geopolitics / policy
    "war", "conflict", "ceasefire", "iran", "china", "russia", "ukraine",
    "nato", "israel", "taiwan", "north korea", "middle east", "strait",
    "trade deal", "g7", "g20", "summit", "un ", "united nations",
    "regulation", "antitrust", "legislation", "congress", "senate",
    "executive order", "department of justice", "doj", "supreme court",
    "white house", "prime minister", "netanyahu", "putin", "zelensky", "trump",
    # Technology / AI / enterprise
    "artificial intelligence", " ai ", "chip", "semiconductor", "cloud",
    "cybersecurity", "data breach", "quantum", "tech giant", "big tech",
    "investment plan", "infrastructure",
    # Payments / fintech / commerce
    "payment", "fintech", "banking", "financial services", "visa", "mastercard",
    "acquisition", "merger", "ipo", "funding", "billion", "venture",
    # Restaurant / hospitality industry
    "restaurant", "hospitality", "food service", "qsr", "quick service",
]

# Travel/aviation terms that disqualify an item from D: Restaurant Technology
_TRAVEL_NOISE_TERMS = [
    "flight", "airline", "airport", "aviation", "aircraft", "passengers",
    "seats", "boarding", "london to sydney", "non-stop", "layover",
]

# World/National scope classification (foreign-subject override + US keyword
# detection) now lives in rb_core.classify_world_national_scope, shared with
# daily_brief.py's pre-render cap so both agree on which items are which scope.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _load_prior_brief_state(target_date: date) -> dict:
    """Load the last 7 days of rendered briefs to power cross-day freshness checks.

    Returns a dict with:
      - what_changed_keys: set of entry prefixes shown in any of the last 7 briefs
      - lifecycle_keys: set of lifecycle_transition entry prefixes (suppress once shown)
      - strategic_signal_fingerprints: set of (title, evidence_count) tuples from last 7 days
      - strategic_signal_counts: dict of title -> max evidence count seen in last 7 days
      - newsletter_editions: set of "Publication | Date" strings shown in last 7 days
      - relationship_delta_keys: set of "name|event_timestamp" shown in last 7 days
      - communication_queue_keys: set of queue snapshot strings shown in last 7 days
      - k_section_urls: set of URLs rendered in K section in last 7 days
    """
    import re as _re2
    state: dict = {
        "what_changed_keys": set(),
        "lifecycle_keys": set(),
        "strategic_signal_fingerprints": set(),
        "strategic_signal_counts": {},   # title -> int (max evidence count seen)
        "strategic_signal_days_seen": {},  # title -> set of dates seen (rendered or footnote)
        "strategic_signal_last_rendered": {},  # title -> most recent date it was actively rendered (had new/changed evidence)
        "newsletter_editions": set(),
        "relationship_delta_keys": set(),
        "communication_queue_keys": set(),
        "k_section_urls": set(),
    }

    for days_back in range(1, 8):
        check_date = target_date - timedelta(days=days_back)
        prior_path = BRIEFS_DIR / f"{check_date.isoformat()}-intelligence-brief.md"
        if not prior_path.exists():
            continue
        try:
            text = prior_path.read_text(encoding="utf-8")
        except Exception:
            continue

        # Newsletter editions — suppress re-runs of the same edition
        for m in _re2.finditer(r"^\*\*(.+?)\*\*\s*\|\s*(.+)$", text, _re2.MULTILINE):
            pub = m.group(1).strip()
            dt = m.group(2).strip()
            if pub and dt:
                state["newsletter_editions"].add(f"{pub}|{dt}")

        # What Changed Today — accumulate all entries across last 7 days
        wc_match = _re2.search(r"## What Changed Today\n(.*?)(?=\n##|\Z)", text, _re2.DOTALL)
        if wc_match:
            for line in wc_match.group(1).splitlines():
                line = line.strip().lstrip("- ")
                if not line:
                    continue
                state["what_changed_keys"].add(line[:120])
                # Lifecycle entries (career phase, opportunity mutations) are one-shot —
                # once shown in any prior brief, suppress forever until the text actually changes
                if "lifecycle_transition" in line or "opportunity mutation" in line.lower():
                    state["lifecycle_keys"].add(line[:120])

        # Strategic signals — track title + evidence count across all 7 days
        si_match = _re2.search(r"## I: Strategic Signals\n(.*?)(?=\n##|\Z)", text, _re2.DOTALL)
        if si_match:
            si_text = si_match.group(1)
            for m2 in _re2.finditer(r"\*\*(.+?)\*\*\nacross (\d+) evidence", si_text):
                sig_title = m2.group(1).strip()
                sig_count = m2.group(2)
                state["strategic_signal_fingerprints"].add((sig_title, sig_count))
                state["strategic_signal_days_seen"].setdefault(sig_title, set()).add(check_date)
                prior_last = state["strategic_signal_last_rendered"].get(sig_title)
                if prior_last is None or check_date > prior_last:
                    state["strategic_signal_last_rendered"][sig_title] = check_date
                try:
                    prev_max = state["strategic_signal_counts"].get(sig_title, 0)
                    state["strategic_signal_counts"][sig_title] = max(prev_max, int(sig_count))
                except ValueError:
                    pass
            # Also count footnote-only appearances ("Ongoing (no change...): Title A; Title B")
            for fn_match in _re2.finditer(r"Ongoing[^:]*:\s*(.+)", si_text):
                for sig_title in fn_match.group(1).split(";"):
                    sig_title = sig_title.strip().rstrip(".")
                    if sig_title:
                        state["strategic_signal_days_seen"].setdefault(sig_title, set()).add(check_date)

        # Relationship Deltas — extract name + last-event timestamp pairs
        h_match = _re2.search(r"## H: Relationship Deltas\n(.*?)(?=\n##|\Z)", text, _re2.DOTALL)
        if h_match:
            for line in h_match.group(1).splitlines():
                # Extract contact name from "- **Name** [STATUS] — ..."
                nm = _re2.match(r"-\s+\*\*(.+?)\*\*", line)
                if nm:
                    contact = nm.group(1).strip()
                    # Extract timestamp if present
                    ts = _re2.search(r"Last event (\S+)", line)
                    ts_val = ts.group(1) if ts else ""
                    state["relationship_delta_keys"].add(f"{contact}|{ts_val}")

        # Communication queue — exact snapshot strings
        cq = _re2.search(r"\*\*Communication queue:\*\* (.+)", text)
        if cq:
            state["communication_queue_keys"].add(cq.group(1).strip())

        # K section URLs — extract linked URLs to catch cross-day Fiserv-style repeats
        k_match = _re2.search(r"## K: GP/Genius.*?\n(.*?)(?=\n##|\Z)", text, _re2.DOTALL)
        if k_match:
            for url_m in _re2.finditer(r"\(https?://[^\)]+\)", k_match.group(1)):
                state["k_section_urls"].add(url_m.group(0)[1:-1])

    return state


def _today_str(d: date) -> str:
    return d.isoformat()


def _parse_pub_date(raw: str | None) -> date | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(raw[:len(fmt) + 5].strip(), fmt).date()
        except ValueError:
            continue
    return None


def _is_corporate_event(title: str, badge: str | None) -> bool:
    if badge:
        for b in CORPORATE_EVENT_BADGES:
            if b in badge.upper():
                return True
    return False


def _is_fresh(pub_date_str: str | None, today: date, is_corporate: bool) -> bool:
    return core.is_fresh_pub_date(_parse_pub_date(pub_date_str), today, is_corporate)


def _is_duplicate(url: str | None, rendered: dict, today: date, is_corporate: bool,
                   window_days: int = DEDUP_WINDOW_DAYS,
                   corporate_grace_days: int = 1) -> bool:
    """`corporate_grace_days` — how many days a corporate-badged item (ACQUISITION,
    FUNDING, CUSTOMER WIN, etc.) may re-render before being treated as a repeat.

    RB-DEFECT-2026-07-10 (superseded 2026-09-25, kept for history): low-volume
    sections (restaurant_tech, restaurant_industry) used to get a longer
    (4-day) grace than world/national's 1-day, because a 1-day cutoff there
    meant every badged item vanished the day after it first appeared and a
    slow news day rendered "0 material headlines." That traded emptiness for
    repetition — the same headline and "why it matters" text repeating
    verbatim for up to 4 days, confirmed live as a real recurring problem
    (see CORPORATE_DEDUP_GRACE_DAYS's own comment). Todd's call, 2026-09-25:
    kill the grace — every section now gets the same 1-day treatment
    world/national always had. A section that's genuinely thin some days
    renders thin (the existing "*N item(s) this cycle.*" / "*0 material
    headlines*" fallback already handles that honestly); it no longer gets
    padded with recycled content to avoid looking empty."""
    if not url:
        return False
    entry = rendered.get(url)
    if not entry:
        return False
    if is_corporate:
        # RB-DEFECT-2026-07-13: this used to compare `today` against
        # last_rendered, which _mark_rendered bumps forward to today every
        # single time the item successfully re-renders -- so as long as an
        # item kept getting picked up by the upstream feed (or was manually
        # re-rendered), its effective age never advanced past ~1 day, and it
        # never hit the grace threshold. Confirmed live: a Cotton Patch Cafe
        # acquisition story first shown 2026-07-09 was still rendering
        # verbatim on 2026-07-13 (render_count: 20) -- a genuinely 4-day-old
        # story that should have expired days earlier. The grace period is
        # meant to measure "how long since this story FIRST appeared," not
        # "how long since it was last shown" (which resets every render) --
        # anchor to first_rendered instead.
        #
        # RB-DEFECT-2026-07-22: the non-corporate same-day escape hatch
        # below (`age_days == 0` against last_rendered, added to support
        # --force same-day re-renders) used to run BEFORE this branch --
        # so on any day the brief rendered more than once (a resend, a
        # retry, a manual force-regenerate), last_rendered was already
        # "today" the second time an item was evaluated, and the escape
        # returned "not a duplicate" without ever reaching the
        # first_rendered check below. Confirmed live: "How P. Terry's
        # Burger Stand..." (first_rendered 2026-07-18) was still rendering
        # on 2026-07-22, a full day past its 4-day corporate grace, with
        # last_rendered already bumped to today from an earlier same-day
        # render. The corporate branch must always measure from
        # first_rendered regardless of same-day re-renders -- it doesn't
        # need the escape hatch anyway (a genuinely new same-day story has
        # first_rendered == today, age 0, which already passes).
        first = _parse_pub_date(entry.get("first_rendered")) or _parse_pub_date(entry.get("last_rendered"))
        if first is None:
            return False
        return (today - first).days >= corporate_grace_days
    last = _parse_pub_date(entry.get("last_rendered"))
    if last is None:
        return False
    age_days = (today - last).days
    # Same-day re-renders always pass (supports --force); suppress only previous days
    if age_days == 0:
        return False
    return age_days < window_days


def _corporate_story_already_covered(title: str, rendered: dict) -> str | None:
    """Return the earliest first_rendered date string among existing
    registry entries sharing an anchor noun with this corporate-badged
    title, or None if there's no match.

    RB-DEFECT-2026-07-17: some trade-press CMSes republish the same story
    under a new URL path (NRN moved "Wonder acquires Mighty Quinn's BBQ"
    from /emerging-chains/ to /regional-chains/ on day 5) -- keyed purely
    by URL, the dedup registry treated the republished link as a brand-new
    story and restarted its 4-day grace countdown, so the identical
    acquisition headline rendered five days straight instead of the
    intended four. Anchor nouns (e.g. "Mighty", "Quinn") persist across a
    republish even when the URL doesn't."""
    nouns = _story_anchor_nouns(title)
    if not nouns:
        return None
    earliest = None
    for entry in rendered.values():
        if not entry.get("is_corporate"):
            continue
        entry_nouns = frozenset(entry.get("anchor_nouns") or [])
        if not (nouns & entry_nouns):
            continue
        first = entry.get("first_rendered")
        if first and (earliest is None or first < earliest):
            earliest = first
    return earliest


def _mark_rendered(url: str | None, title: str, section: str, today: date, rendered: dict,
                    is_corporate: bool = False, first_rendered_override: str | None = None) -> None:
    if not url:
        return
    today_str = _today_str(today)
    if url in rendered:
        rendered[url]["last_rendered"] = today_str
        rendered[url]["render_count"] = rendered[url].get("render_count", 1) + 1
    else:
        rendered[url] = {
            # RB-DEFECT-2026-07-17: when this exact URL is new but
            # _corporate_story_already_covered() found the same underlying
            # story already registered under a different (republished) URL,
            # inherit that story's original first_rendered instead of
            # starting a fresh countdown -- otherwise a CMS re-categorizing
            # a story's URL path gives it another full grace period for free.
            "first_rendered": first_rendered_override or today_str,
            "last_rendered": today_str,
            "title": title[:120],
            "section": section,
            "render_count": 1,
        }
    if is_corporate:
        nouns = _story_anchor_nouns(title)
        if nouns:
            rendered[url]["is_corporate"] = True
            rendered[url]["anchor_nouns"] = sorted(nouns)


_WORLD_NATIONAL_ALLOWED_BADGES = {"[🏢 ACQUISITION]", "[💰 FUNDING]", "[🚀 DEPLOYMENT]"}

# Minimum title keywords required for each badge to be shown.
# If none match the bare title, the badge is stripped as a misfire.
_BADGE_TITLE_KEYWORDS: dict[str, list[str]] = {
    "[🏢 ACQUISITION]": [
        "acqui", "merger", "merge with", "buys ", "buyout", "to buy",
        "purchase of", "deal to buy", "takeover", "acquires", "to acquire",
    ],
    "[⚠️ BANKRUPTCY]": [
        "bankrupt", "chapter 7", "chapter 11", "ch. 7", "ch. 11",
        "liquidat", "insolvenc", "receivership", "closes all", "closing all",
    ],
    "[✅ CUSTOMER WIN]": [
        "wins ", "win ", "selects ", "selected ", "deploys", "deployment",
        "partners with", "signed ", "signs ", "contract", "awarded",
    ],
    "[🆕 PRODUCT LAUNCH]": [
        "launch", "introduces", "unveils", "announces new", "new platform",
        "new product", "new solution", "new feature",
    ],
    "[💸 CUSTOMER LOSS]": [
        "drops ", "drops\n", "replac", "switch", "away from", "terminated",
    ],
    # RB-DEFECT-2026-07-20: "Wonder is valued at more than $9B after latest
    # fundraise" -- a standard funding-round headline phrasing -- had its
    # badge stripped because neither "valued at" nor "valuation" were in
    # this list. That made it fall through to a non-corporate render path
    # entirely, invisible to the story-identity same-run/cross-day dedup
    # its badge-intact siblings ("Wonder raises $650M...") went through --
    # the same real-world event rendered twice in one brief instead of once.
    "[💰 FUNDING]": [
        "raises ", "series ", "seed round", "funding round", "secures funding",
        "secures investment", "venture capital", "ipo", "public offering",
        "stock market debut", "vc fund", "valued at", "valuation", "fundraise",
    ],
}

# Titles containing these phrases disqualify a FUNDING badge regardless of keywords
_FUNDING_BADGE_BLOCKLIST = [
    "price target", "analyst", "rating", "upgrade", "downgrade", "fundraiser",
    "fundraising", "campaign donation", "political", "pac ", "rnc ", "dnc ",
    "rally", "rallies", "rallied", "republican", "democrat", "senator", "congressman",
    "vance", "trump", "harris", "biden", "governor", "campaign",
]

# RB-DEFECT-2026-08-14: "Blue Chip Partners LLC Acquires 1,314 Shares of
# Microsoft Corporation $MSFT" earned an [ACQUISITION] badge because "acquires"
# is a required keyword for that badge -- but this is a routine 13F-style
# institutional shareholding disclosure (a trivial position), not M&A. Real
# M&A headlines say "Acquires {Company}"; these always say "shares of/in" or
# "stake/position in" the target, and the target is a plain corporation name,
# not "the company" being bought out. Regex (not substring) so it only catches
# the actual "N shares of" shape, not an unrelated coincidental phrase.
_ACQUISITION_BADGE_BLOCKLIST_RE = re.compile(
    r"\bshares? (of|in)\b|\bstake in\b|\bposition in\b|\b[\d,]+\s+shares\b", re.IGNORECASE,
)


def _badge_is_justified(badge: str, bare_title: str, summary: str = "") -> bool:
    """Return False if the badge is likely a misclassification based on title or summary."""
    required = _BADGE_TITLE_KEYWORDS.get(badge)
    if required is None:
        return True  # no rule → keep badge as-is
    tl = bare_title.lower()
    combined = (bare_title + " " + summary).lower()
    if badge == "[💰 FUNDING]" and any(b in combined for b in _FUNDING_BADGE_BLOCKLIST):
        return False
    if badge == "[🏢 ACQUISITION]" and _ACQUISITION_BADGE_BLOCKLIST_RE.search(combined):
        return False
    return any(k in tl for k in required)


_REDUNDANCY_STOPWORDS = {
    "this", "that", "with", "from", "into", "onto", "have", "will", "could",
    "would", "should", "their", "there", "which", "about", "after", "these",
    "those", "been", "being", "were", "when", "than", "also", "more", "most",
    "some", "such", "each", "just", "over", "under", "while", "story",
}


def _is_redundant_with_summary(candidate: str, summary: str) -> bool:
    """True if `candidate` (the synthesized "Why it matters" sentence)
    mostly just restates `summary` (the outlet's own factual line already
    rendered directly above it) in different words -- see the "every word
    earns its place" check at _fmt_headline's call site. A cheap
    significant-word-overlap heuristic, not semantic similarity: good
    enough to catch the common case (the synthesis leans on the same nouns
    the summary already gave it) without needing another API call."""
    if not summary:
        return False
    cand_words = {w for w in re.findall(r"[a-z]{4,}", candidate.lower())
                  if w not in _REDUNDANCY_STOPWORDS}
    if len(cand_words) < 4:
        return False  # too short to judge reliably either way
    summary_words = {w for w in re.findall(r"[a-z]{4,}", summary.lower())
                      if w not in _REDUNDANCY_STOPWORDS}
    overlap = cand_words & summary_words
    return len(overlap) / len(cand_words) >= 0.6


def _fmt_headline(item: dict, today: date, section: str, rendered: dict) -> str | None:
    """Format a single headline item. Returns None if it should be skipped."""
    import re as _re
    extras = item.get("extras") or {}
    url = extras.get("source_url", "").strip()
    title = (item.get("title") or "").strip()
    # RB-2026-08-25: _render_headline_section's synthesis pre-pass keys its
    # cache off this original, unmutated title -- `title` itself gets
    # reassigned to a badge-stripped version further down for some sections/
    # badges (see the badge-justification block below), which would silently
    # break the why-line lookup for exactly those items if used as the key.
    _original_title = title
    source = extras.get("source_name", "").strip()
    pub_date = extras.get("pub_date", "").strip()
    why = (item.get("why_it_matters") or item.get("summary") or "").strip()
    # RB-DEFECT-2026-08-16: confirmed live -- "Why Starbucks Was Right to
    # Pull the Plug on Its AI Inventory System" rendered its why_it_matters
    # as raw chat-app DOM markup ("]:pointer-events-auto ... scroll-mb-[calc(
    # var(--scroll-root-safe-area-inset-bottom,0px)...))]" dir="auto"
    # data-turn-id="request-..." data-testid="conversation-turn-48"") instead
    # of any real article content -- data-turn-id/conversation-turn/
    # threadScrollVars are a chat UI's own internal markup, not anything
    # restauranttechnologynews.com would ever serve. This item had been
    # sitting in the intelligence DB since 2026-08-09 (REACTIVATED lifecycle
    # state), so whatever ingested it captured the wrong page entirely --
    # this is a render-layer backstop against that class of corruption
    # regardless of where upstream it happened, not a fix for the ingestion
    # bug itself. Never trust CSS/DOM-shaped text as prose.
    if why and _re.search(r'data-(turn-id|testid)=|pointer-events-(auto|none)|scroll-m[bt]-\[|dir="auto"', why):
        why = ""
    badge = extras.get("signal_badge", "")
    is_corp = _is_corporate_event(title, badge)

    # Analyst-note noise: "<Firm> Initiates/Highlights/Reaffirms/Assesses <Company> ..."
    # Applies to every headline section, not just world/national — these items
    # can also reach Section D (Restaurant Technology) via the
    # what_todd_doesnt_know_yet fallback pool and its loose _TECH_KEYWORDS
    # match (e.g. "integration", "digital" alone are enough to pass), which
    # let "Cantor Fitzgerald Highlights Remitly Global (RELY)'s ... Digital
    # Remittances" — a Yahoo Finance stock note with zero restaurant
    # relevance — through as if it were restaurant tech news.
    if _ANALYST_NOTE_PATTERN.search(title.lower()):
        return None
    _bare_title_for_noise_check = _re.sub(r"^\[[^\]]+\]\s*", "", title)
    if _TICKER_EXPLAINER_PATTERN.search(_bare_title_for_noise_check):
        return None
    if _MORTGAGE_RATE_TRACKER_PATTERN.search(_bare_title_for_noise_check):
        return None
    if (_ANALYSIS_PREFIX_PATTERN.search(_bare_title_for_noise_check)
            or _SPECULATIVE_LISTICLE_PATTERN.search(_bare_title_for_noise_check)
            or _INVESTOR_PERSONALITY_PATTERN.search(_bare_title_for_noise_check)
            or _QUOTE_LEAD_ANALYSIS_PATTERN.search(_bare_title_for_noise_check)):
        return None

    # World/national: noise filter then positive relevance gate
    if section in ("world", "national"):
        tl = title.lower()
        if any(p in tl for p in _WORLD_NOISE_TITLE_PATTERNS):
            return None
        # Hard drop for political/lifestyle fluff that passes the US keyword check
        # but has no relevance to macro economy, policy, or business
        _NATIONAL_FILLER_PATTERNS = [
            "golf course", "d.c. golf", "east potomac", "wes moore", "run-up to 2028",
            "2028 campaign", "union shadows", "run down", "golf links",
        ]
        if any(p in tl for p in _NATIONAL_FILLER_PATTERNS):
            return None
        # Positive gate — item must be relevant to macro/geopolitics/fintech/enterprise.
        # Strip any leading [BADGE] label first — badge text (e.g. "[ACQUISITION]")
        # contains relevance keywords itself and would otherwise satisfy its own gate.
        # Require the match in the TITLE, not just an incidental mention in the why/summary
        # blurb (e.g. a fraud-case profile that happens to call the defendant "a China critic"
        # shouldn't pass just because "china" appears once in passing).
        bare_tl = _re.sub(r"^\[[^\]]+\]\s*", "", tl)
        # RB-DEFECT-2026-07-11: an item carrying one of the allowed world/
        # national badges (ACQUISITION/FUNDING/DEPLOYMENT) was already
        # vetted as a genuine corporate event by daily_brief.py's upstream
        # classifier -- re-running this section's own broad keyword
        # allowlist against it is redundant and lossy, since that list
        # doesn't cover every synonym the badge-specific check further down
        # (_badge_is_justified) already does. Observed live: "[ACQUISITION]
        # EasyJet agrees to surprise takeover bid" was correctly badged but
        # dropped here because "takeover" isn't in _WORLD_RELEVANCE_KEYWORDS,
        # even though it's in _BADGE_TITLE_KEYWORDS["[ACQUISITION]"] and
        # would have passed _badge_is_justified a few lines later. Trust the
        # badge here; _badge_is_justified still verifies it isn't a misfire.
        # The bypass itself must still be earned -- a badge that isn't
        # justified by the title (a misclassification, e.g. an ACQUISITION
        # label on an anniversary puff piece) shouldn't get a free pass on
        # relevance just for wearing a bracket label.
        title_badge_match = _re.match(r"^(\[[^\]]+\])\s+", title)
        has_allowed_badge = bool(
            title_badge_match
            and title_badge_match.group(1) in _WORLD_NATIONAL_ALLOWED_BADGES
            and _badge_is_justified(title_badge_match.group(1), title[title_badge_match.end():], summary=why)
        )
        if not has_allowed_badge and not any(k in bare_tl for k in _WORLD_RELEVANCE_KEYWORDS):
            return None

    # Restaurant tech section — discard travel/airline misfires and vendor press releases
    if section == "restaurant_tech":
        tl = title.lower()
        if any(p in tl for p in _TRAVEL_NOISE_TERMS):
            return None
        # Vendor advertorial detection: "[Company] [Verb] Restaurant(s) ..."
        # Restaurant Technology News publishes these daily for any vendor who submits them.
        # Pattern: short company name + action verb + "restaurant(s)" anywhere in title.
        import re as _re2
        _RTN_PR_PATTERN = _re2.compile(
            r"^.{2,45}\b(advances|gives|strengthens|helps|powers|enables|delivers|transforms|simplifies|positions|equips|expands|brings|offers|provides)\b.{0,60}\brestaurants?\b",
            _re2.IGNORECASE,
        )
        # RB-DEFECT-2026-07-10: this pattern matches on generic verb+"restaurant(s)"
        # phrasing alone and was dropping genuine customer-win/deployment/acquisition
        # events that the upstream classifier had already vetted with a real
        # signal_badge (e.g. "[✅ CUSTOMER WIN] Taco Bell Expands Drive-Thru Voice AI
        # to Nearly 900 Restaurants" — "Expands ... Restaurants" is exactly the
        # advertorial shape, but this is real, newsworthy customer-adoption news,
        # not vendor puffery). A signal_badge means daily_brief.py's classifier
        # already made this call with more context than a title-only regex has —
        # only apply the vendor-PR heuristic to unbadged/general-tier items, same
        # exemption principle _select_restaurant_tech_items already applies above.
        if (
            source.lower() in ("restaurant technology news", "restauranttechnologynews.com")
            and not badge
            and _RTN_PR_PATTERN.search(title)
        ):
            # Keep items about GP/Genius/Worldpay (your employer) but label them
            is_gp = any(k in tl for k in ("global payments", "genius", "worldpay"))
            if not is_gp:
                return None  # drop vendor PR for competitors

    # Strip badges that don't match the title content
    badge_match = _re.match(r"^(\[[^\]]+\])\s+", title)
    if badge_match:
        detected_badge = badge_match.group(1)
        bare_title = title[badge_match.end():]
        if section in ("world", "national"):
            # World/national: only allow M&A, funding, deployment badges
            if detected_badge not in _WORLD_NATIONAL_ALLOWED_BADGES:
                title = bare_title
                badge = ""
                is_corp = False
            elif not _badge_is_justified(detected_badge, bare_title, summary=why):
                # Secondary verification: strip FUNDING misfires (analyst upgrades, political)
                title = bare_title
                badge = ""
                is_corp = False
                why = _re.sub(r"\s*↳[^.]*\.", "", why).strip()
        else:
            # Restaurant sections: verify the badge is justified by the title keywords
            if not _badge_is_justified(detected_badge, bare_title, summary=why):
                title = bare_title
                badge = ""
                is_corp = False
                # Also strip the ↳ implication line injected by the badge template
                why = _re.sub(r"\s*↳[^.]*\.", "", why).strip()

    # Validate any embedded "↳ Label — implication" annotation even when the title
    # had no bracket badge to strip (the annotation can be baked into why_it_matters
    # independent of the title). If the label's claim isn't supported by the title
    # content, strip the annotation rather than show a nonsensical dot-connection.
    _DOT_LABEL_KEYWORDS = {
        "restructuring": ["layoff", "restructur", "downsiz", "cut jobs", "workforce reduction", "job cut"],
        "m&a event": ["acqui", "merger", "buys ", "buyout", "takeover"],
        "competitor win": ["wins ", "win ", "selects ", "partners with", "signed ", "contract", "awarded"],
        "bankruptcy": ["bankrupt", "chapter 7", "chapter 11", "liquidat", "insolvenc"],
    }
    # Analysis/opinion pieces that debate or refute a claim aren't event reports —
    # a dot-connection template shouldn't fire just because the debated term appears.
    _ANALYSIS_PIECE_CUES = [
        "convenient excuse", "won't lead to", "doesn't lead to", "isn't due to",
        "myth", "misconception", "skeptic", "questions whether", "why he believes",
        "why she believes", "debunk", "doubts that", "not actually",
    ]
    dot_match = _re.search(r"↳\s*([^—]+?)\s*—", why)
    if dot_match:
        label = dot_match.group(1).strip().lower()
        required = _DOT_LABEL_KEYWORDS.get(label)
        combined_check = (title + " " + why).lower()
        is_analysis_piece = any(c in combined_check for c in _ANALYSIS_PIECE_CUES)
        if is_analysis_piece:
            why = _re.sub(r"\s*↳[^.]*\.", "", why).strip()
        elif required is not None:
            tl_check = title.lower()
            if not any(k in tl_check for k in required):
                why = _re.sub(r"\s*↳[^.]*\.", "", why).strip()

    if not url:
        return None  # no URL = skip (hallucination prevention)
    if not _is_fresh(pub_date, today, is_corp):
        return None  # stale
    # World/national is a front-page framing (today's newspaper), not a
    # "don't repeat within a business week" framing — a shorter window here
    # lets the section refill with real BBC/NPR/Axios content sooner, rather
    # than sitting deduped-empty for most of the week while only the
    # high-volume Yahoo analyst-note pool (filtered separately above) would
    # otherwise be left to backfill it.
    dedup_window = WORLD_NATIONAL_DEDUP_WINDOW_DAYS if section in ("world", "national") else DEDUP_WINDOW_DAYS
    # RB-2026-09-25: no longer section-conditional -- CORPORATE_DEDUP_GRACE_DAYS
    # is now 1 everywhere (Todd killed the extended low-volume-section grace;
    # see its own comment for why). Kept as a named local rather than inlining
    # the constant directly, since it still threads through both the
    # story-identity path (active_display_days=) and the legacy _is_duplicate
    # fallback (corporate_grace_days=) below.
    corporate_grace = CORPORATE_DEDUP_GRACE_DAYS

    # RB-DEFECT-2026-07-20 Phase 2: corporate items are deduped by canonical
    # story identity (entity+event+disambiguator, rb_core.StoryLedger), not
    # by URL. Every earlier fix here (exact-URL, anchor-noun, leading-subject,
    # republished-URL inheritance) closed exactly one disguise the same
    # real-world event could wear -- confirmed live, Wonder's $650M Series D
    # still fragmented into 4 separate registry entries across 4 outlets even
    # after all of them. resolve_story_identity() returns None for the
    # minority of titles it can't confidently identify (e.g. a roundup
    # headline with no clean leading subject); those fall back to the
    # original URL+anchor-noun path unchanged, below.
    story_key = core.resolve_story_identity(title, extras) if is_corp else None

    if story_key is not None and story_key[1] == "exec_change":
        # RB-2026-08-25: lets Section F (watchlist) recognize when it's about
        # to re-announce a leadership change already told in A-D this run --
        # see _rendered_exec_hire_entities_this_run's definition below.
        _rendered_exec_hire_entities_this_run.add(story_key[0])

    if story_key is not None:
        found = _story_ledger.lookup(story_key)
        if found is None:
            # RB-DEFECT-2026-07-20 (Phase 1.3): a brand-new story shouldn't
            # "first appear" already days old just because corporate items
            # get a long freshness ceiling elsewhere -- see the matching
            # comment in the pre-Phase-2 fallback branch below.
            pub = _parse_pub_date(pub_date)
            if pub and (today - pub).days > corporate_grace:
                return None
        if not _story_ledger.should_render(story_key, today, active_display_days=corporate_grace, url=url):
            return None  # this story has already been told
        existing_story_id = found[0] if found else None
        if existing_story_id and existing_story_id in _rendered_story_ids_this_run:
            return None  # already shown in an earlier section this run, under a different URL
        if url in _rendered_this_run:
            return None
        story_id = _story_ledger.record(story_key, url, title, today)
        _rendered_story_ids_this_run.add(story_id)
        _mark_rendered(url, title, section, today, rendered)
        _rendered_this_run.add(url)
    else:
        # RB-DEFECT-2026-07-20 (Phase 1.3): a corporate item's up-to-21-day
        # freshness ceiling (_is_fresh above) is meant to keep an already-
        # tracked story visible through a slow news day, not to let a URL RB
        # has never seen before "first appear" days late as if it were
        # breaking news. Confirmed live: QSR Magazine's feed was dead
        # 2026-06-03 through 2026-07-17; once reactivated, its first scan
        # surfaced several exec-hire items already 4 days old, which
        # rendered as fresh "new activity" simply because nothing had ever
        # registered their URLs before. A first-time URL gets the tighter
        # per-section grace window instead of the full corporate ceiling;
        # an already-tracked URL re-rendering inside its existing grace
        # period is unaffected.
        if is_corp and url not in rendered:
            pub = _parse_pub_date(pub_date)
            if pub and (today - pub).days > corporate_grace:
                return None  # too old to be a first-time appearance
        if _is_duplicate(url, rendered, today, is_corp, window_days=dedup_window,
                          corporate_grace_days=corporate_grace):
            return None  # already rendered within the window
        if url in _rendered_this_run:
            return None  # already shown in an earlier section this run

        # RB-DEFECT-2026-07-20 Phase 3 (optional, best-effort): story_key is
        # None here because resolve_story_identity() couldn't confidently
        # key this title (e.g. a roundup headline with no clean leading
        # subject) -- before falling back to the pre-Phase-2 anchor-noun
        # logic, check whether an LLM confirms this is actually the same
        # event as an already-tracked story. find_tiebreak_candidates()
        # pre-filters to stories sharing a significant word, so this only
        # calls the model for genuine candidates, not every active story.
        # llm_assist returns None on any failure/unavailability (no API
        # key, no package, any error) -- that's a plain no-op here, and
        # everything falls through to the original logic unchanged.
        tiebreak_matched = False
        if is_corp:
            for candidate_id, candidate_story in core.find_tiebreak_candidates(title, _story_ledger, today):
                if candidate_id in _rendered_story_ids_this_run:
                    return None  # already shown this run -- no need to spend an API call
                if llm_assist.same_story_tiebreak(title, candidate_story.get("title_sample") or "") is not True:
                    continue
                first_seen = _parse_pub_date(candidate_story.get("first_seen"))
                if first_seen and (today - first_seen).days > corporate_grace:
                    return None  # LLM-confirmed same event, already past its display window
                _story_ledger.merge_into(candidate_id, url, today)
                _rendered_story_ids_this_run.add(candidate_id)
                _mark_rendered(url, title, section, today, rendered)
                _rendered_this_run.add(url)
                tiebreak_matched = True
                break  # only spend one confirmed match's worth of API calls per item

        if not tiebreak_matched:
            # RB-DEFECT-2026-07-17: this URL may be new to the registry while the
            # same underlying story has been running under a different (republished)
            # URL for days — see _corporate_story_already_covered. Inherit that
            # story's original first_rendered so a URL change doesn't buy it a fresh
            # grace period, and suppress outright if the original is already past grace.
            covered_since = _corporate_story_already_covered(title, rendered) if is_corp else None
            if covered_since:
                covered_date = _parse_pub_date(covered_since)
                if covered_date and url not in rendered and (today - covered_date).days >= corporate_grace:
                    return None  # same story already past grace under a different URL

            _mark_rendered(url, title, section, today, rendered, is_corporate=is_corp,
                           first_rendered_override=covered_since)
            _rendered_this_run.add(url)
            if is_corp:
                subject = _corporate_subject_signature(title)
                if subject:
                    _rendered_subjects_this_run.add(subject)

    # INTELLIGENCE_BRIEF_CANONICAL.md is unconditional: Part 1 never carries
    # a recommendation ("Any recommendation... belongs in Daily Brief"). The
    # ↳ annotation upstream (web_scanner.py signal_context) is written as an
    # implication/action hook for Part 2's Technology Radar -- e.g. "M&A
    # event — assess vendor landscape / competitive position" -- and the
    # justification checks above only catch misclassified *labels*, not the
    # fact that even a correctly-labeled annotation is still an action
    # directive. Strip it here regardless of justification; Part 2 reads the
    # same why_it_matters field upstream of this truncation and keeps it.
    why = _re.sub(r"\s*↳[^.]*\.\s*$", "", why).strip()

    lines = [f"[{title}]({url})"]
    meta = " | ".join(filter(None, [source, pub_date]))
    if meta:
        lines.append(meta)
    # Suppress why/summary when it is just a repeat of the title (adds no information)
    bare_for_compare = _re.sub(r"^\[[^\]]+\]\s*", "", title).strip().lower()
    if why and why.strip().lower() not in (title.lower(), bare_for_compare):
        why_trunc = (why[:197] + "…") if len(why) > 200 else why
        lines.append(f"*{why_trunc}*")
    # RB-2026-08-25 (Gap #10): the italic line above is the outlet's own
    # factual summary -- this is the separate CoS business-climate judgment
    # Todd's editorial standard calls for, synthesized by
    # _synthesize_personal_why_batch and looked up here by title. A-D only
    # (world/national/restaurant/restaurant_tech); E/F/K have their own
    # existing why-text paths and must not get a second, redundant line.
    #
    # "Every word on the page has to earn its place" (Todd, 2026-08-25): a
    # synthesized line that just restates the summary line directly above it
    # in different words isn't additive, it's bloat -- check for that before
    # rendering it, the same way the title-repeat check above already does
    # for the summary line itself.
    if section in ("world", "national", "restaurant", "restaurant_tech"):
        synth_why = _personal_why_synth.get(_team_synth_key(_original_title), "")
        if (synth_why
                and not synth_why.strip().lower().startswith(_PERSONAL_WHY_NO_IMPLICATION_PREFIXES)
                and not _is_redundant_with_summary(synth_why, why)
                and not _why_already_shown_this_section(section, synth_why)):
            lines.append(f"**Why it matters:** {synth_why}")
            _shown_why_by_section_this_run.setdefault(section, []).append(synth_why)
    lines.append(f"**[Read more →]({url})**")
    return "\n".join(lines)


# Module-level set to prevent the same URL from appearing in two sections in the same run
_rendered_this_run: set[str] = set()

# RB-2026-09-10: "What RB Found Without You Telling It" (renders first) and
# Section F's watchlist escalations pull from two different feeds
# (email_intelligence_harvest vs watchlist_intelligence) that can both pick
# up the same real-world story -- confirmed live: the same Payments Dive
# "Latitude...stablecoins" story rendered in full in both sections. F's
# escalation items copy their triggering evidence's text verbatim into
# extras.evidence_headline but frequently carry no source_url at all (as
# here), so the existing _rendered_this_run (URL-keyed) dedup can't catch
# this pair -- `if url and url in _rendered_this_run` never even evaluates
# when url is empty. Keyed by normalized title text instead, same
# bracket-strip + lowercase + 60-char-prefix normalization
# _wrb_content_fingerprint uses in daily_brief.py for the equivalent
# problem one layer up (duplicated rather than imported, same reasoning as
# _truncate_clean's docstring in render_daily_brief.py).
_rendered_wrb_fingerprints_this_run: set[str] = set()


def _wrb_fingerprint(text: str) -> str:
    text = re.sub(r"^\[[^\]]+\]\s*", "", (text or "").strip())
    return text.lower()[:60]

# RB-QUALITY-2026-09-04: confirmed live -- when one underlying event (e.g.
# the Iran war driving oil prices) produces 7 separate World Headlines that
# day, _PERSONAL_HEADLINE_WHY_ROLE_SUMMARY's per-story synthesis correctly
# names the same real mechanism for each -- the prompt isn't being generic,
# the news genuinely is one story wearing 7 headlines. Reading the same
# sentence 7 times in a row still reads as boilerplate regardless of why
# it happened. Rather than building real topic/entity clustering (a bigger,
# riskier change), suppress the repeated sentence itself, per section, per
# run: keep every headline/link/outlet-summary (no real news gets dropped),
# just don't re-print a "Why it matters" line that's substantially the same
# claim as one already shown in this section. Reuses the same word-overlap
# heuristic as _is_redundant_with_summary, just against prior why-lines
# instead of against the current item's own summary.
_shown_why_by_section_this_run: dict[str, list[str]] = {}


def _consequence_tail(sentence: str, n_words: int = 9) -> set[str]:
    """The templated repetition observed live concentrates in the sentence's
    ENDING (the consequence clause -- "...costs for the restaurant
    industry" / "...cost structure for restaurant operations"), not the
    whole sentence -- each one names a different triggering entity/country
    up front (Russia, China, the Strait of Hormuz...), which is exactly
    what dilutes a whole-sentence word-overlap check below any reasonable
    threshold even when the actual claim is functionally identical. Compare
    just the tail instead."""
    words = [w for w in re.findall(r"[a-z]{3,}", sentence.lower())
             if w not in _REDUNDANCY_STOPWORDS]
    return set(words[-n_words:])


# RB-QUALITY-2026-09-04: even the tail-overlap check above misses real
# duplicates when two sentences land on the same underlying claim using
# genuinely different specific words for it -- "energy prices" vs. "food
# sourcing" vs. "fuel and food prices" are three different lexical choices
# for one macro-mechanism (input-cost inflation from an oil/energy shock),
# confirmed live: this was the exact case where pure word overlap failed
# to catch a known duplicate. A small, hand-curated set of the mechanism
# categories actually observed repeating -- not a generic NLP classifier --
# catches same-category claims regardless of the specific noun chosen.
# Deliberately narrow: expand only when a new category is confirmed
# repeating live, not preemptively.
_MACRO_MECHANISM_CLUSTERS = {
    "energy_supply_chain_costs": {
        "oil", "energy", "fuel", "diesel", "gas", "supply", "chain",
        "chains", "sourcing", "logistics", "shipping", "transportation",
        "prices", "price", "costs", "cost",
    },
}


def _macro_mechanism_categories(sentence: str) -> set[str]:
    words = set(re.findall(r"[a-z]{3,}", sentence.lower()))
    return {
        name for name, cluster_words in _MACRO_MECHANISM_CLUSTERS.items()
        if len(words & cluster_words) >= 2
    }


def _why_already_shown_this_section(section: str, candidate: str) -> bool:
    cand_tail = _consequence_tail(candidate)
    cand_categories = _macro_mechanism_categories(candidate)
    if len(cand_tail) < 3 and not cand_categories:
        return False  # too short a tail to judge reliably
    for prior in _shown_why_by_section_this_run.get(section, []):
        if cand_categories and cand_categories & _macro_mechanism_categories(prior):
            return True
        prior_tail = _consequence_tail(prior)
        if not prior_tail:
            continue
        overlap = cand_tail & prior_tail
        if len(overlap) / len(cand_tail) >= 0.55:
            return True
    return False

# RB-DEFECT-2026-07-17: _rendered_this_run only catches a byte-identical URL
# repeat. Wonder's $650M Series D / $9B valuation was reported by three
# different outlets (Restaurant Business Online, Fast Casual, Restaurant
# Dive) -- C rendered two of them, then E's separate earnings scan added the
# third under yet another URL, since as far as E could tell it was a fresh
# story. _story_anchor_nouns can't catch this either: it deliberately drops
# the sentence-leading word ("Wonder" opens every one of these titles), so
# the one proper noun that ties them together is never counted. This
# narrower, corporate-event-only signature keeps the leading capitalized
# subject specifically so E can tell "already covered in C/D" from "genuinely
# new company" before adding a raw feed_key candidate.
#
# RB-DEFECT-2026-07-20 Phase 2: superseded by rb_core.StoryLedger + a real
# entity+event identity for anything resolve_story_identity() can confidently
# resolve -- _rendered_subjects_this_run's leading-word heuristic is kept
# only as the same-run fallback for the minority of titles that don't
# resolve to a clean story identity (see _fmt_headline/_render_earnings).
_rendered_subjects_this_run: set[str] = set()

# RB-DEFECT-2026-07-20 Phase 2: same-run companion to _rendered_this_run, but
# keyed by story_id (rb_core.StoryLedger) instead of URL -- a story already
# rendered once this run (in C or D) must not render again in E just because
# a third outlet's URL is new to this run. _rendered_this_run alone can't
# catch that since the URL genuinely differs.
_rendered_story_ids_this_run: set[str] = set()

# RB-2026-08-25: Section F (watchlist) pulls from a separate press-release
# scan pipeline than A-D's corporate headlines, so the same real-world exec
# hire can reach both sections under two different URLs with no shared story
# identity to catch it (resolve_story_identity needs a badge-tagged headline
# title; F's watchlist items don't carry one). Confirmed live: the Dave &
# Buster's COO hire rendered in Section C from an NRN headline, then again in
# Section F's "New activity" from a GlobeNewswire press-release URL for what
# is almost certainly the same announcement. Tracks entity_key (not the full
# story identity, since F has no comparable title to resolve one from) so F
# can at least recognize "this entity's leadership news was already told
# above" and point back instead of repeating it.
_rendered_exec_hire_entities_this_run: set[str] = set()

# Persisted across runs (system/.cache/story_ledger.json) -- populated in
# render()/render_team_edition() before any section renders, replacing the
# corporate branch of rendered_headlines.json's per-URL registry.
_story_ledger: "core.StoryLedger" = core.StoryLedger()


def _corporate_subject_signature(title: str) -> str | None:
    """Leading capitalized subject of a corporate-event headline (e.g. "Wonder",
    "Domino's Pizza", "McDonald's"), used only to catch the same real-world
    event covered by multiple outlets under different URLs/wording -- not a
    general same-story test (see _story_anchor_nouns for that)."""
    bare = re.sub(r"^\[[^\]]+\]\s*", "", title).strip()
    m = re.match(r"^([A-Z][\w'&.]*(?:\s+[A-Z][\w'&.]*){0,3})", bare)
    if not m:
        return None
    return m.group(1).strip().lower()


def _story_anchor_nouns(title: str) -> frozenset:
    """Extract proper nouns from a title (capitalized words ≥4 chars, not the first word).

    Used for story-cluster dedup: two headlines sharing any anchor noun are
    considered the same story and limited to 2 items combined.
    """
    import re as _re
    _GENERIC = {
        "This", "Here", "What", "Why", "How", "When", "Where", "Who",
        "The", "These", "Those", "With", "From", "Behind",
    }
    words = _re.findall(r"[A-Z][a-z]{3,}", title)
    if not words:
        return frozenset()
    # Only skip the first *match* if it's actually the sentence-start word —
    # i.e. the title (after leading punctuation/quotes) begins with it. When
    # the true first word doesn't match the regex (too short, e.g. "The" in
    # "'The spectacle Iran wants...'"), the first match found is a real
    # anchor noun ("Iran") and dropping it broke cross-article same-story
    # matching for exactly that case — two different outlets' headlines
    # about the same Iran story shared no noun because "Iran" was silently
    # skipped from one of them.
    stripped = title.lstrip(" '\"“‘[({")
    if stripped.startswith(words[0]):
        words = words[1:]
    return frozenset(w for w in words if w not in _GENERIC)


_TOPIC_STOPWORDS = {
    "the", "and", "for", "with", "from", "after", "near", "says", "said",
    "calls", "call", "could", "would", "what", "why", "how", "this", "that",
    "into", "over", "amid", "watch", "news", "new", "report", "reports", "current",
}


def _story_topic_words(title: str) -> frozenset[str]:
    """Meaning-bearing headline words used for same-event clustering."""
    words: set[str] = set()
    for raw in re.findall(r"[a-z0-9]+", title.lower()):
        word = {"russian": "russia", "ukrainian": "ukraine"}.get(raw, raw)
        for suffix in ("ing", "ed", "s"):
            if len(word) > len(suffix) + 3 and word.endswith(suffix):
                word = word[:-len(suffix)]
                break
        if len(word) >= 3 and word not in _TOPIC_STOPWORDS:
            words.add(word)
    return frozenset(words)


def _render_headline_section(items: list, section_label: str, section_key: str,
                              today: date, rendered: dict, scope_filter=None,
                              cluster_state: dict | None = None,
                              max_per_cluster: int = 1) -> str:
    lines = [f"## {section_label}\n"]
    rendered_items = []

    # Topic cluster dedup: limit same-story to 2 items using word-overlap matching.
    # Each rendered item's sig_words is stored; a new item is "same story" if it
    # shares ≥2 significant words with any existing cluster that already has 2 items.
    #
    # cluster_state lets callers share this registry across multiple sections
    # (World + National both draw from the same world_national pool) — without
    # it, the same real-world event covered by two different articles (one
    # classified world, one classified national due to an incidental US
    # mention) rendered in BOTH sections since each section's dedup only ever
    # saw its own half of the pool.
    if cluster_state is None:
        cluster_state = {"sig_words": [], "counts": []}
    cluster_sig_words: list[frozenset] = cluster_state["sig_words"]
    cluster_render_counts: list[int] = cluster_state["counts"]

    def _find_cluster(nouns: frozenset) -> int | None:
        """Return the matching real-world-event cluster, if any."""
        for i, cn in enumerate(cluster_sig_words):
            shared = nouns & cn
            overlap = len(shared) / min(len(nouns), len(cn))
            if (len(shared) >= 3 and overlap >= 0.30) or (len(shared) >= 2 and overlap >= 0.50):
                return i
        return None

    # scope filter (world vs US for world_national_headlines) — shared with
    # daily_brief.py's pre-render cap via rb_core.classify_world_national_scope
    # so the two stay in agreement about which items are which scope.
    if scope_filter:
        scoped_items = []
        for item in items:
            item_scope = core.classify_world_national_scope(item)
            if scope_filter == "us" and item_scope != "us":
                continue
            if scope_filter == "world" and item_scope != "world":
                continue
            scoped_items.append(item)
    else:
        scoped_items = list(items)

    # RB-2026-08-25 (Gap #10): synthesize the CoS "why it matters" line for
    # this section's candidates before formatting -- batched once per
    # section call (well under brief_synthesis.MAX_ITEMS_PER_CALL) rather
    # than per item, same pattern _render_team_newsletters uses for D+.
    _synthesize_personal_why_batch([
        {"title": (it.get("title") or "").strip(),
         "evidence": f"{(it.get('title') or '').strip()} {(it.get('summary') or '').strip()}".strip()}
        for it in scoped_items if (it.get("title") or "").strip()
    ])

    for item in scoped_items:
        extras = item.get("extras") or {}
        title = (item.get("title") or "").strip()
        nouns = _story_topic_words(title)
        # One real-world event gets one article. Corroborating outlets belong
        # in provenance, not as repeated headlines in an executive brief.
        if nouns:
            cluster_idx = _find_cluster(nouns)
            if cluster_idx is not None:
                # Merge nouns into cluster so related entities get caught going forward
                cluster_sig_words[cluster_idx] = cluster_sig_words[cluster_idx] | nouns
                if cluster_render_counts[cluster_idx] >= max_per_cluster:
                    continue
                cluster_render_counts[cluster_idx] += 1
            else:
                cluster_sig_words.append(nouns)
                cluster_render_counts.append(1)

        fmt = _fmt_headline(item, today, section_key, rendered)
        if fmt:
            rendered_items.append(fmt)

    if not rendered_items:
        # No caller actually supplies a fallback for a zero-item section (confirmed: A/B/C/D
        # all route through this function and `_append_section` just skips a "" return with
        # no substitute) — the header and content both vanished silently, which contradicts
        # every other quiet-cycle section in the brief (F, I) rendering an explicit zero-state
        # line instead of disappearing. Render the header + a low-volume explanation instead.
        lines.append("*0 material headlines this cycle.*")
        return "\n\n".join(lines)
    if len(rendered_items) < MIN_HEADLINES_PER_SECTION:
        lines.extend(rendered_items)
        lines.append(f"\n*{len(rendered_items)} item{'s' if len(rendered_items) != 1 else ''} this cycle.*")
    else:
        lines.extend(rendered_items)

    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

_TODAY_DATE_PATTERNS = None


def _summary_is_recent(summary: str, today: date) -> bool:
    """Return True if the summary references a date within the last 7 days."""
    import re
    today_str = today.isoformat()[:7]  # YYYY-MM
    # Match ISO dates in the summary
    for m in re.finditer(r"(\d{4}-\d{2}-\d{2})", summary):
        try:
            d = date.fromisoformat(m.group(1))
            if (today - d).days <= 7:
                return True
        except ValueError:
            pass
    return False


def _render_autonomous_discovery(sections: dict) -> str:
    """What RB Found Without You Telling It — autonomous-discovery digest.

    RB-DEFECT-2026-08-25: what_rb_found_without_you_telling_it has carried
    real, distinct content since RB-DEFECT-013 (RB 9.24B) — daily_brief.py
    scans several upstream sections for items with
    novelty.autonomous_discovery_value in (high, medium) AND
    novelty.source_discovered, dedupes by title, and populates this section
    (31 real items in the live cache the day this was found). The pre-render
    migration to this script never implemented it: the field was computed
    and shipped in the API payload, then silently absent from the actual
    displayed brief ever since. Restored per the original embedded
    rendering-rule intent — render immediately after the coverage line,
    before any narrative analysis, industry commentary, or active-thread
    reminders. Items are already high/medium-filtered upstream; this only
    ranks (act_today first, then confidence) and caps at 7 so a heavy day
    doesn't crowd out the sections that follow.
    """
    raw_items = sections.get("what_rb_found_without_you_telling_it") or []
    # This section is for synthesis, not a second newsletter list. Require
    # either explicit convergence/pattern framing or corroboration across
    # multiple source references. Single-story discoveries remain in their
    # normal news section.
    items = [
        item for item in raw_items
        if (item.get("title") or "").lower().startswith("multiple-source convergence:")
        or (item.get("extras") or {}).get("convergence_type")
    ]
    if not items:
        return (
            "## What RB Concluded Today\n\n"
            "*No autonomous discoveries above monitor-only threshold this cycle.*"
        )

    def _rank(item: dict) -> tuple:
        disposition_rank = 0 if item.get("disposition") == "act_today" else 1
        confidence_rank = {"high": 0, "medium": 1, "low": 2}.get(item.get("confidence"), 1)
        return (disposition_rank, confidence_rank)

    ranked = sorted(items, key=_rank)[:7]
    lines = ["## What RB Concluded Today\n"]
    for item in ranked:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        summary = (item.get("summary") or "").strip()
        why = (item.get("why_it_matters") or "").strip()
        action = (item.get("recommended_action") or "").strip()
        reader_title = re.sub(r"^Multiple-source convergence:\s*", "", title, flags=re.IGNORECASE)
        detail = why or summary
        detail = _truncate_at_word_boundary(detail, 280)
        prefix = "[ACTION TODAY] " if item.get("disposition") == "act_today" else ""
        source_url = ""
        for ref in item.get("source_refs") or []:
            candidate = ref.get("url") if isinstance(ref, dict) else str(ref).split(":", 1)[-1]
            if str(candidate).startswith("http"):
                source_url = str(candidate)
                break
        shown_title = f"[{reader_title}]({source_url})" if source_url else reader_title
        line = f"- {prefix}**{shown_title}**"
        if detail:
            line += f" — {detail}"
        if action:
            line += f" **Next:** {_truncate_at_word_boundary(action, 180)}"
        lines.append(line)
        _rendered_wrb_fingerprints_this_run.add(_wrb_fingerprint(title))

    remaining = len(items) - len(ranked)
    if remaining > 0:
        lines.append(f"\n*{remaining} additional lower-priority discovery item(s) not shown.*")

    return "\n".join(lines)


def _truncate_at_word_boundary(text: str, limit: int) -> str:
    """RB-QUALITY-2026-09-04: bare `text[:150]` cut mid-word with no
    ellipsis, e.g. "...just created a m —" — confirmed live in What Changed
    Today's email-activity and relationship-signal bullets, both of which
    then run straight into a following " — sender name" suffix, reading as
    a single garbled fragment. Cut at the last whole word within the
    budget instead, and only append "…" when actually truncated.
    """
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_space = cut.rfind(" ")
    if last_space > 0:
        cut = cut[:last_space]
    return cut.rstrip() + "…"


def _render_what_changed(
    sections: dict,
    today: date,
    prior_state: dict | None = None,
    persistent_lifecycle: dict | None = None,
) -> str:
    """What Changed Today — strict delta report. Only genuinely new items."""
    lines = ["## What Changed Today\n"]
    items = []
    prior_keys: set = (prior_state or {}).get("what_changed_keys", set())
    lifecycle_keys: set = (prior_state or {}).get("lifecycle_keys", set())
    comm_queue_keys: set = (prior_state or {}).get("communication_queue_keys", set())
    # persistent_lifecycle has no 7-day expiry — see RENDERED_LIFECYCLE_PATH.
    if persistent_lifecycle is None:
        persistent_lifecycle = {}

    def _is_unchanged(entry: str) -> bool:
        """Return True if this entry appeared verbatim in any of the last 7 briefs."""
        return entry[:120] in prior_keys

    def _is_lifecycle_shown(entry: str) -> bool:
        """Return True if this lifecycle/opportunity entry was ever shown before —
        checks the 7-day rolling scan AND the permanent lifecycle store."""
        key = entry[:120]
        return key in lifecycle_keys or key in persistent_lifecycle

    # Known senders that are expected daily and carry no new business intelligence
    _MUTED_EMAIL_SENDERS = [
        # Devotional / religious
        "rick warren", "daily hope", "joel osteen", "joyce meyer", "charles stanley",
        "devotional", "bible verse",
        # Retail / commercial
        "kohl's", "kohls", "ihop", "old navy", "gap ", "banana republic",
        "macy's", "macys", "target ", "walmart", "amazon deals", "bed bath",
        # Job alerts — muted now that Todd has accepted a role at Global Payments
        "linkedin job al", "linkedin job", "job alert", "jobs alert",
        "new jobs update", "new europe jobs", "jobs update -",
        "indeed", "ziprecruiter", "you may be a fit", "new jobs for you",
        "jobs you might like", "confidential careers",
        # Promotional / marketing noise
        "turbo", "turbotax", "know where you stand, free",
        "ai courses:", "build real-world ai skills",
        "top 3 cour", "top courses",
        # RB-DEFECT-2026-07-20 (Phase 1.4): sports-score digests and webinar/
        # masterclass marketing carry no CoS-relevant signal, but weren't
        # covered by any existing muted pattern -- "12th straight Red Sox win
        # might be wildest yet — MLB Morning Lineup" and "Complimentary
        # August LinkedIn Masterclass-update — Success Champions" both
        # rendered as if they were notable email activity.
        "morning lineup", "masterclass", "webinar invit", "complimentary session",
    ]

    def _triage_email_summary(summary: str) -> str:
        """Re-order email preview subjects: business first, muted senders last/removed."""
        import re as _re2
        # Summary is a semicolon-separated list of "Subject — Sender" snippets
        parts = [p.strip() for p in summary.split(";") if p.strip()]
        muted, rest = [], []
        for p in parts:
            pl = p.lower()
            if any(m in pl for m in _MUTED_EMAIL_SENDERS):
                muted.append(p)
            else:
                rest.append(p)
        # RB-DEFECT-2026-07-20 Phase 3 (optional, best-effort): the keyword
        # blocklist above only catches known senders/phrasing -- layer an
        # LLM relevance check on top for the ambiguous middle ground it
        # can't reach (a subject line from a sender we've never muted that's
        # still pure noise). None (unavailable/failed) is a no-op; the item
        # is kept exactly as the keyword-only check would have left it.
        still_rest = []
        for p in rest:
            if llm_assist.is_low_signal(p) is True:
                muted.append(p)
            else:
                still_rest.append(p)
        rest = still_rest
        # Keep muted senders only if there's nothing else to show.
        # If ALL subjects are muted (no business signal in the preview), suppress
        # the subject list entirely — just return the thread-count portion.
        if rest:
            ordered = rest
        elif muted:
            # Everything in the preview window is promotional/job alerts — no intel value
            return ""
        else:
            ordered = []
        result = "; ".join(ordered[:4])
        return (result[:197] + "…") if len(result) > 200 else result

    # Personal intelligence deltas — suppress if unchanged from yesterday
    for item in sections.get("personal_intelligence_delta", []):
        title = (item.get("title") or "").strip()
        summary = (item.get("summary") or "").strip()
        why = (item.get("why_it_matters") or "").strip()
        action = (item.get("recommended_action") or "").strip()
        extras = item.get("extras") or {}
        delta_source = extras.get("delta_source", "")
        if not title and not summary:
            continue
        # RB-DEFECT-2026-07-10: genuinely personal correspondence (not a
        # business/colleague relationship per _correspondence_label in
        # daily_brief.py) has no place in the Intelligence Brief — this
        # document is a "newspaper," it does not surface the content of a
        # personal 1:1 email. Business/colleague-labeled correspondence and
        # introduction emails are still genuinely business-relevant and
        # stay visible.
        if delta_source == "personal_correspondence" and extras.get("correspondence_label") == "Personal correspondence":
            continue
        label = f"**{title}**" if title else ""
        # Apply email triage to the email activity line
        if delta_source == "email" or "email activity" in title.lower():
            detail = _triage_email_summary(summary)
            # If triage returned empty (all subjects were promotional noise), suppress detail
            if not detail:
                detail = "no notable subjects in preview window"
        else:
            detail = (summary[:197] + "…") if len(summary) > 200 else summary
        src = f"*(source: {delta_source})*" if delta_source else ""
        entry = " — ".join(filter(None, [label, detail]))
        if src:
            entry += f" {src}"
        # Lifecycle transitions (career phase, opportunity mutations) are one-shot —
        # suppress once they've appeared in any prior brief
        if delta_source == "lifecycle_transition":
            if _is_lifecycle_shown(entry):
                continue
            persistent_lifecycle[entry[:120]] = {"first_rendered": today.isoformat()}
        if not _is_unchanged(entry):
            items.append(f"- {entry}")

    # What changed since yesterday (non-relationship items)
    for item in sections.get("what_changed_since_yesterday", []):
        title = (item.get("title") or "").strip()
        summary = (item.get("summary") or "").strip()
        extras = item.get("extras") or {}
        delta_source = extras.get("delta_source", "")
        if not title and not summary:
            continue
        label = f"**{title}**" if title else ""
        detail = (summary[:197] + "…") if len(summary) > 200 else summary
        src = f"*(source: {delta_source})*" if delta_source else ""
        entry = " — ".join(filter(None, [label, detail]))
        if src:
            entry += f" {src}"
        if not _is_unchanged(entry):
            items.append(f"- {entry}")

    # Communication intelligence — single summary line only (not all relationship signals)
    for item in sections.get("communication_intelligence", []):
        title = (item.get("title") or "").strip()
        summary = (item.get("summary") or "").strip()
        extras = item.get("extras") or {}
        emails_waiting = extras.get("emails_awaiting_response")
        followups_overdue = extras.get("followups_overdue")
        loops_overdue = extras.get("loops_overdue")
        meetings_needing_prep = extras.get("meetings_needing_prep")

        if any(v is not None for v in [emails_waiting, followups_overdue, loops_overdue, meetings_needing_prep]):
            parts = []
            if emails_waiting:
                parts.append(f"{emails_waiting} emails awaiting response")
            if followups_overdue:
                parts.append(f"{followups_overdue} follow-ups overdue")
            if loops_overdue:
                parts.append(f"{loops_overdue} loops overdue")
            if meetings_needing_prep:
                parts.append(f"{meetings_needing_prep} meetings needing prep")
            if parts:
                queue_str = " · ".join(parts)
                # Equal totals do not prove that no work happened: loops may
                # have closed while different ones became due. Report only
                # the verified current state and make correction easy.
                try:
                    settings = core.load_settings()
                    cockpit_url = (((settings.get("daily_briefing") or {}).get("delivery") or {})
                                   .get("chatgpt_brief_url") or "").strip()
                except Exception:
                    cockpit_url = ""
                action = (f" [Review or update in RB]({cockpit_url})"
                          if cockpit_url else " Tell RB which item changed.")
                items.append(f"- **Communication queue:** {queue_str}.{action}")
        elif title:
            items.append(f"- **{title}**" + (f" — {_truncate_at_word_boundary(summary, 150)}" if summary else ""))
        break  # one communication_intelligence summary line is enough

    # Relationship signals — only today's calendar events and actual new activity
    # Suppress items where "last contact" is more than 7 days old
    for item in sections.get("last_24h_relationship_signals", []):
        title = (item.get("title") or "").strip()
        summary = (item.get("summary") or "").strip()
        if not title or not summary:
            continue
        # Calendar events for today are always included
        today_iso = today.isoformat()
        if today_iso in summary:
            items.append(f"- **{title}** — {_truncate_at_word_boundary(summary, 150)}")
        # Suppress "last contact" items older than 7 days
        elif "last contact" in summary.lower():
            if _summary_is_recent(summary, today):
                items.append(f"- **{title}** — {_truncate_at_word_boundary(summary, 150)}")
            # else suppress — not actually new

    if items:
        lines.extend(items)
    else:
        lines.append("*No material changes since yesterday.*")

    return "\n".join(lines)


# Newsletters prioritized for CoS curation (order = preference)
_PRIORITY_NEWSLETTERS = [
    "restaurant technology news", "restaurant dive", "qsr", "nation's restaurant news",
    "nrn", "modern restaurant management", "fast casual", "payments dive",
    # Michael "schatzy" Schatzberg's "Hospitality Headline" — a genuine
    # hospitality-industry newsletter Todd deliberately kept in the pipeline
    # (see the fetch-domain broadening for linkedin.com). Its "articles" are
    # mostly CTA button labels once junk-filtered, and its lead paragraph is
    # often a personal-interest teaser hook (sports, holidays) before the
    # actual on-topic content — neither reliably contains a hospitality
    # keyword, so without an explicit allowlist entry the relevance gate
    # dropped this specific, wanted publication entirely.
    "schatzberg",
]

# Keywords that flag an article as relevant for CoS earmarking
_NEWSLETTER_RELEVANCE_KEYWORDS = [
    "ai", "technology", "tech", "platform", "payments", "pos", "ordering", "digital",
    "automation", "data", "enterprise", "software", "integration", "labor", "kiosk",
    "delivery", "loyalty", "analytics", "cloud", "saas", "restaurant", "operator",
    "worldpay", "toast", "par", "olo", "square", "clover", "shift4", "hospitality",
    "qsr", "franchise",
]
_NEWSLETTER_RELEVANCE_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _NEWSLETTER_RELEVANCE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

MAX_NEWSLETTERS = 3
MAX_ARTICLES_PER_NEWSLETTER = 5
# INTELLIGENCE_BRIEF_CANONICAL.md: "Min 3 articles per newsletter shown."
MIN_ARTICLES_PER_NEWSLETTER = 3

# RB-DEFECT-2026-07-20 (Phase 1.4): LinkedIn's automated "invitations to
# connect" digest ("Here's a summary of your latest invitations to connect.
# Temidayo Oke... Robert Delmont Client Success Leader | QSR & Restaurant
# Technology...") rendered in D+ as if it were a real industry newsletter --
# it has zero article links, but the connection-request profile bios it
# lists are often packed with restaurant-tech buzzwords (QSR, Restaurant
# Technology, Digital Ordering...), which satisfied the relevance-keyword
# gate below even though the email itself carries no industry news. Reject
# these chrome patterns before the relevance check ever runs.
_LOW_SIGNAL_NEWSLETTER_PATTERNS = [
    "invitations to connect", "mutual connections", "accept view profile",
]


def _is_low_signal_newsletter(item: dict, body_summary: str) -> bool:
    title = (item.get("title") or "").lower()
    combined = f"{title} {body_summary.lower()}"
    if any(p in combined for p in _LOW_SIGNAL_NEWSLETTER_PATTERNS):
        return True
    # RB-DEFECT-2026-07-20 Phase 3 (optional, best-effort): the pattern list
    # above only catches known chrome phrasing -- layer an LLM relevance
    # check on top for the ambiguous middle ground it can't reach. None
    # (unavailable/failed) is a no-op; D+ is capped at MAX_NEWSLETTERS
    # candidates a day, so this stays low-volume.
    return llm_assist.is_low_signal(item.get("title") or "", body_summary) is True


def _newsletter_sort_key(item: dict) -> int:
    extras = item.get("extras") or {}
    source = (extras.get("source_name") or item.get("title") or "").lower()
    for i, name in enumerate(_PRIORITY_NEWSLETTERS):
        if name in source:
            return i
    return 99


_NEWSLETTER_OFF_TOPIC = [
    "supreme court", "immigration", "trump", "congress", "senate", "lawsuit",
    "election", "legal protection", "haitian", "syrian", "refugee",
    "price target", "stock volatility", "dividend", "nasdaq", "analyst",
    "recipe", "leftovers", "cooking", "mindset", "self-care",
]


def _article_relevance_score(title: str) -> int:
    tl = title.lower()
    if any(k in tl for k in _NEWSLETTER_OFF_TOPIC):
        return -1  # force to bottom; filtered out below
    # Word-boundary matched — a bare substring check on "tech" scored
    # "Mapping the Path from Technical Excellence to Strategic Leadership"
    # (a recruiter newsletter, not restaurant/tech content) as relevant,
    # since "technical" contains "tech". Same bug class as Section D.
    return len(_NEWSLETTER_RELEVANCE_RE.findall(tl))


def _render_newsletter_inbox(sections: dict, prior_state: dict | None = None) -> str:
    items = sections.get("newsletter_intelligence", [])
    if not items:
        return ""

    seen_editions: set = (prior_state or {}).get("newsletter_editions", set())

    # Sort by priority, then by most-recent edition first — a 3-day fetch
    # lookback in _compute_newsletter_intelligence means a delayed/missed
    # edition can still be "eligible" days after publication. Sorting by
    # date too (not just source priority) ensures that when two editions of
    # the same publication both qualify, the newest one wins the source's
    # single slot below rather than an arbitrary insertion-order pick.
    def _sort_key(item: dict) -> tuple:
        priority = _newsletter_sort_key(item)
        pub_date = (item.get("extras") or {}).get("pub_date", "")
        parsed = _parse_pub_date(pub_date)
        # Newest first within the same priority tier — sort ascending on the
        # negative ordinal so unparseable dates (None) sort last.
        return (priority, -(parsed.toordinal() if parsed else -1))

    # RB-DEFECT-2026-07-08: when a publication has multiple eligible editions
    # in the lookback window, the newest-first sort let a content-free edition
    # (e.g. a "Premium Report"/sponsored-survey teaser with 0 real articles,
    # just a body_summary) claim the publication's one-slot-per-run limit
    # ahead of an older-but-still-eligible edition that actually had real
    # articles -- "QSR Magazine" showed a linkless survey teaser while a
    # 13-article edition from two days earlier sat unused. Pick the edition
    # with the most usable articles per publication before sorting/rendering;
    # ties keep the newest.
    best_by_source: dict[str, dict] = {}
    for item in items:
        source = (item.get("extras") or {}).get("source_name", item.get("title", "Newsletter"))
        current = best_by_source.get(source)
        if current is None:
            best_by_source[source] = item
            continue
        if _newsletter_usable_article_count(item) > _newsletter_usable_article_count(current):
            best_by_source[source] = item
        elif _newsletter_usable_article_count(item) == _newsletter_usable_article_count(current):
            cur_date = _parse_pub_date((current.get("extras") or {}).get("pub_date", ""))
            new_date = _parse_pub_date((item.get("extras") or {}).get("pub_date", ""))
            if new_date and (not cur_date or new_date > cur_date):
                best_by_source[source] = item

    sorted_items = sorted(best_by_source.values(), key=_sort_key)
    lines = ["## D+: Newsletter Inbox\n"]
    rendered_newsletters = 0
    # One edition per publication per brief — otherwise a publication with
    # two eligible editions (e.g. a missed edition surfacing late alongside
    # today's) can consume multiple of the limited MAX_NEWSLETTERS slots,
    # crowding out a different publication entirely. Observed live:
    # "Payments Dive" appeared twice (Jul 02 and Jul 04 editions) in one
    # brief, using 2 of 3 available slots.
    seen_sources_this_run: set = set()

    for item in sorted_items:
        if rendered_newsletters >= MAX_NEWSLETTERS:
            break
        extras = item.get("extras") or {}
        source = extras.get("source_name", item.get("title", "Newsletter"))
        pub_date = extras.get("pub_date", "")
        articles = extras.get("articles") or []

        if source in seen_sources_this_run:
            continue

        # Skip editions already shown in a prior brief (same publication + same date)
        edition_key = f"{source}|{pub_date}"
        if edition_key in seen_editions:
            continue

        # Filter to valid articles with URLs and score by relevance
        valid = [a for a in articles if a.get("url") and a.get("title")]
        # RB-DEFECT-2026-07-08: a newsletter article whose URL was already
        # rendered standalone in an earlier section (e.g. Section D covering
        # "Taco Bell revs up drive-thru AI deployment" from Restaurant Dive,
        # then the Restaurant Dive newsletter D+ entry linking the same
        # article again) repeated the same story twice in one brief.
        # Headline sections already register every rendered URL into
        # _rendered_this_run; D+ never checked it.
        valid = [a for a in valid if (a.get("url") or "") not in _rendered_this_run]
        # RB-DEFECT-2026-07-10i: the exact-URL check above misses a
        # newsletter's own click-tracking link for an article whose direct
        # publisher URL was already rendered in an earlier section (e.g.
        # Section D linking restaurantdive.com/news/x directly, then
        # Restaurant Dive's own newsletter listing the same article again
        # via a link.restaurantdive.com tracking redirect that decodes to
        # the identical destination) -- different URL strings, same story.
        if _rendered_this_run:
            _rendered_resolved = {_resolve_wrapped_url(u) for u in _rendered_this_run}
            valid = [a for a in valid if _resolve_wrapped_url(a.get("url") or "") not in _rendered_resolved]
        # Skip tracking/unsubscribe/non-editorial URLs. Every publisher routes
        # articles through a click-tracking redirect domain (link.paymentsdive.com,
        # link.restaurantdive.com, click1.inform.wtwhmedia.com, ...) so the redirect
        # domain itself is not a reliable junk signal — filter on URL path/query
        # patterns that are specific to non-article links instead.
        valid = [a for a in valid
                 if not any(s in (a.get("url") or "").lower() for s in _NEWSLETTER_JUNK_URL_PATTERNS)
                 and not any(s in (a.get("title") or "").lower() for s in _NEWSLETTER_JUNK_TITLE_PATTERNS)]

        body_summary = (extras.get("body_summary") or "").strip()
        # An essay-format edition (e.g. a LinkedIn personal write-up) can have
        # zero real article links after masthead/CTA/author-profile links are
        # filtered out, but still be worth showing via body_summary — don't
        # drop the whole edition just because its link list is empty.
        if not valid and not body_summary:
            continue
        if _is_low_signal_newsletter(item, body_summary):
            continue

        # Publication-level relevance gate: D+ is restaurant/hospitality/
        # payments-tech, not a general newsletter inbox. A known industry
        # source is trusted outright; anything else needs at least one
        # article that actually matches an industry keyword. Without this,
        # a fetch-domain fix that legitimately let a personal-interest
        # newsletter (a Christian devotional/news roundup) through the
        # pipeline also put it in front of a CoS reading about restaurant
        # technology, since "not off-topic" (score >= 0) was being treated
        # as good enough — it isn't; it only means "not political."
        source_is_known_industry = any(p in source.lower() for p in _PRIORITY_NEWSLETTERS)
        has_relevant_article = any(_article_relevance_score(a.get("title", "")) > 0 for a in valid)
        # Also check the extracted body summary — filtering the newsletter's
        # own masthead/author-profile self-links (see _extract_body_articles)
        # can remove the only article-list entry that happened to carry an
        # industry keyword (e.g. "Hospitality Headline"), even though the
        # newsletter's actual lead paragraph is genuinely on-topic.
        has_relevant_summary = _article_relevance_score(body_summary) > 0
        if not source_is_known_industry and not has_relevant_article and not has_relevant_summary:
            continue

        # Sort by relevance score, drop off-topic articles (score < 0)
        valid.sort(key=lambda a: _article_relevance_score(a.get("title", "")), reverse=True)
        valid = [a for a in valid if _article_relevance_score(a.get("title", "")) >= 0]
        curated = valid[:MAX_ARTICLES_PER_NEWSLETTER]

        # RB-DEFECT-2026-08-14: confirmed live -- Nation's Restaurant News
        # rendered with a single article link after junk/relevance filtering
        # left only one survivor from its original list, well under the
        # canonical "min 3 articles per newsletter" floor. That's a
        # different shape than the zero-articles-plus-body_summary "essay
        # edition" case above (a LinkedIn author's personal post, which
        # never had a real article list to begin with) -- here `valid`
        # started non-empty, the roundup just didn't clear the bar. Skip the
        # article list rather than show a roundup-format newsletter with
        # one lonely link.
        # RB-2026-08-28: originally `continue`'d past the WHOLE edition here
        # -- but an edition can have both a real body_summary AND 1-2
        # sub-floor articles (e.g. a personal LinkedIn essay that also links
        # one or two things), and dropping the entire edition discarded a
        # genuinely worth-reading summary along with the article list it had
        # nothing to do with. Only drop the article list in that case, same
        # as the zero-articles essay case just above -- only drop the whole
        # edition when there's no summary to fall back on either.
        if valid and len(curated) < MIN_ARTICLES_PER_NEWSLETTER:
            if not body_summary:
                continue
            curated = []

        header = f"**{source}**"
        if pub_date:
            header += f" | {pub_date}"
        lines.append(header)
        # The article list is mostly CTA button labels for sources like
        # LinkedIn newsletters ("READ MORE", "LISTEN NOW") with no content of
        # their own — body_summary (the author's actual lead paragraph,
        # extracted separately) is what makes this section worth reading
        # rather than a bare list of link titles.
        if body_summary:
            lines.append(f"*{body_summary}*")
        for art in curated:
            lines.append(f"- [{art['title']}]({art['url']})")
            _rendered_this_run.add(art["url"])
        lines.append("")
        rendered_newsletters += 1
        seen_sources_this_run.add(source)

    if rendered_newsletters == 0:
        return ""

    return "\n".join(lines)


def _render_identity_match_candidates(sections: dict, today: date | None = None) -> str:
    """H+: Identity Confirmations — inbound email senders whose name matches an
    email-less baseline contact. Never auto-merged; surfaced so the operator
    can confirm or reject via the Custom GPT (POST /identity/confirm)."""
    items = sections.get("identity_match_candidates", [])
    if not items:
        return ""

    today = today or date.today()
    lines = [f"## H+: Identity Confirmations Needed ({len(items)})\n"]
    for item in items:
        extras = item.get("extras") or {}
        title = (item.get("title") or "").strip()
        summary = (item.get("summary") or "").strip()
        # RB-DEFECT-2026-08-19: this candidate had no age signal -- a match
        # first surfaced 2026-08-07 read identically to one seen for the
        # first time today, so it never looked overdue even after sitting
        # unresolved for 12 days. Todd: "this needs to be cleared off."
        # Surface pending age so a stale item reads as stale.
        age_note = ""
        first_seen = extras.get("first_seen")
        if first_seen:
            try:
                days_pending = (today - date.fromisoformat(str(first_seen)[:10])).days
                if days_pending >= 3:
                    age_note = f" **Pending {days_pending} days — resolve or reject below.**"
            except ValueError:
                pass
        lines.append(f"- **{title}** — {summary}{age_note}")
        sender = extras.get("sender_name") or "this sender"
        email = extras.get("sender_email") or "this email address"
        baseline = extras.get("baseline_name") or "the existing contact"
        lines.append(
            f"  **Decision needed:** Is {sender} <{email}> the same person as {baseline}? "
            f"Reply **\"Yes, save {email}\"**, **\"No, different person\"**, or **\"Not sure\"**."
        )

    return "\n".join(lines)


_EARNINGS_CORPORATE_BADGES = ("ACQUISITION", "EARNINGS", "FUNDING", "EXEC HIRE",
                               "EXEC DEPARTURE", "M&A", "IPO",
                               # RB-DEFECT-2026-08-19: web_scanner.py's SIGNAL_TYPES
                               # already classifies "customer_win"/"partnership"
                               # press releases (a competitor landing or renewing
                               # a major restaurant account) -- they just never
                               # routed into Section E, so they'd surface (if at
                               # all) buried in C/D as an ordinary headline. Todd:
                               # "Competitor wins should surface in section E."
                               "CUSTOMER WIN", "PARTNERSHIP")

_NO_EARNINGS_EVENTS_TEXT = "## E: Earnings & Corporate\n\n*No earnings reports or material corporate events this cycle.*"


def _earnings_no_events_text(sections: dict) -> str:
    """RB-DEFECT-066: PAR's Q2 earnings call was omitted from a brief that
    simultaneously (and falsely) claimed zero earnings events -- the earnings
    scan hadn't actually finished before the brief's cutoff that morning.
    Before asserting "no events," check the scan_health item daily_brief.py
    attaches to earnings_intelligence; if the scan didn't complete for today
    (or never ran), say so instead of implying a clean, completed check.
    """
    scan_health = next(
        (i for i in (sections.get("earnings_intelligence") or [])
         if isinstance(i, dict) and (i.get("extras") or {}).get("earnings_type") == "scan_health"),
        None,
    )
    if scan_health is not None:
        extras = scan_health.get("extras") or {}
        if not extras.get("scan_complete"):
            return "## E: Earnings & Corporate\n\n*Earnings scan incomplete; no clean conclusion available.*"
        unresolved = extras.get("unresolved_ir_failures") or []
        if unresolved:
            # RB-DEFECT-2026-08-19: Todd's standing instruction is one short
            # line here, not a company-by-company hedge -- but the underlying
            # signal (some tickers' IR pages are consistently unreachable,
            # not just quiet this cycle) still has to reach someone who can
            # fix it. Keep the reader line to a single short clause and let
            # unresolved_ir_failures keep flowing to scan_health/audit for
            # that follow-up instead of re-explaining it in the brief daily.
            names = ", ".join(unresolved[:4]) + ("…" if len(unresolved) > 4 else "")
            return (
                "## E: Earnings & Corporate\n\n"
                f"*Research recovery is still incomplete for {names}. RB is checking alternate "
                "SEC, company-newsroom, filing, and reputable secondary sources; this brief must "
                "not be treated as publication-ready until the recovery gate clears.*"
            )
    return _NO_EARNINGS_EVENTS_TEXT


_EARNINGS_METRIC_RE = re.compile(
    r"(?:(?:GAAP|Adjusted|Organic|Non-GAAP)\s+){0,2}"
    r"(?:revenue|revenues|EPS|ARR|Annual Recurring Revenue)"
    r"[^.;•]{0,15}?(?:increased|decreased|grew|declined|rose|fell)"
    r"[^.;•]{0,60}?\d+(?:\.\d+)?%"
    r"(?:[^.;•]{0,40}?to\s+\$[\d,.]+\s*(?:million|billion|M|B))?",
    re.IGNORECASE,
)


def _extract_earnings_highlights(text: str, max_items: int = 3) -> list[str]:
    """RB-DEFECT-2026-08-11: Todd rejected the templated "why this matters"
    line as generic filler -- "if all you are going to give me is a generic
    statement then don't bother." Pull the handful of "metric verb N%[...to
    $X]" clauses earnings-release boilerplate reliably contains (e.g.
    "Quarterly revenues increased 19% year-over-year to $133.4 million")
    instead of fabricating a summary. Returns [] when the source text has no
    real figures (e.g. an EDGAR filing-index stub with no release body, like
    Olo's 8-K here) -- callers must treat an empty result as "nothing to
    say" and add no note at all, not synthesize a fallback."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _EARNINGS_METRIC_RE.finditer(text or ""):
        clause = re.sub(r"\s+", " ", m.group(0)).strip(" .;•")
        key = clause.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(clause)
        if len(out) >= max_items:
            break
    return out


# Per-category (not per-company, not one-size-fits-all) reason a tracked
# entity's movement matters to a GP/Genius reader -- drawn from the same
# watchlist `category` classification restaurant_tech_watchlist.md already
# assigns each tracked company, so it varies by what the company actually
# does rather than repeating one sentence for every earnings item.
_CATEGORY_GP_RELEVANCE = {
    "restaurant_tech_pos": "a direct POS competitor to GP/Genius's restaurant point-of-sale business",
    "restaurant_tech_payments": "a direct payments-processing competitor to GP/Genius",
    "restaurant_tech_loyalty": "a loyalty/CRM competitor, adjacent to GP/Genius's guest-engagement stack",
    "restaurant_tech_digital": "a digital-ordering platform that typically integrates with (or competes "
                                "for budget against) POS/payments providers like GP/Genius",
    "restaurant_tech_ai": "an AI vendor in the restaurant stack — a read on where operator tech budget is shifting",
    "restaurant_tech_backoffice": "a back-office/ops vendor — signals where restaurant tech budget is "
                                   "going outside POS/payments",
    "restaurant_tech_infrastructure": "infrastructure restaurant-tech vendors build on — an indirect demand signal",
    "restaurant_tech_other": "a restaurant-tech vendor in GP/Genius's competitive set",
    "restaurant_brand": "an operator in GP/Genius's actual customer base — a direct demand-side read "
                         "on tech/payments budget appetite",
}


def _gp_earnings_note(text: str, entity_name: str, category: str) -> str:
    """A real CoS summary (extracted financial highlights) plus a reason
    specific to what this entity actually does, not a copy-pasted sentence.
    Returns "" -- add no line at all -- when there's nothing substantive to
    extract, per explicit instruction not to fill the space with filler."""
    highlights = _extract_earnings_highlights(text)
    if not highlights:
        return ""
    name = entity_name.strip() or "This company"
    relevance = _CATEGORY_GP_RELEVANCE.get(category)
    summary = "; ".join(highlights)
    summary = summary[:1].upper() + summary[1:]
    if relevance:
        return f"CoS summary: {summary}. Why this matters for GP: {name} is {relevance}."
    return f"CoS summary: {summary}."


def _render_earnings(sections: dict, today: date, rendered: dict | None = None,
                      gp_context: bool = False) -> str:
    wl_items = sections.get("watchlist_intelligence", [])
    earnings_items = [i for i in wl_items
                      if (i.get("extras") or {}).get("watchlist_status") not in ("No Change", None)
                      and any(k in (i.get("title") or "").upper()
                              for k in ("EARNINGS", "FUNDING", "EXEC", "ACQUISITION", "M&A", "IPO"))]

    # RB-DEFECT-2026-07-10: this section only ever scanned watchlist_intelligence,
    # so a funding/exec-departure event that reached the brief via a different
    # feed (restaurant_industry_headlines' "[💰 FUNDING] Jersey Mike's IPO...",
    # or a "Fiserv president exits" story surfaced only through the GP field
    # intelligence pool) never counted here — Section E said "no events" in
    # the same document where C and K had already reported them. Both feeds
    # tag genuine corporate events with the same extras.signal_badge the
    # Section D classifier uses; scan them too, deduping by URL against what
    # watchlist_intelligence already contributed.
    seen_urls = {
        (i.get("extras") or {}).get("source_url") for i in earnings_items
        if (i.get("extras") or {}).get("source_url")
    }
    # RB-DEFECT-2026-07-17: exact-URL dedup alone let the same real-world
    # event through a second time under a different outlet's URL (Wonder's
    # $650M Series D reported by Restaurant Business Online + Fast Casual in
    # C, then Restaurant Dive's writeup of the identical event added here in
    # E) -- track the leading-subject signature of everything already
    # rendered in C/D this run (and already queued for E) so a same-company,
    # same-event story doesn't get a third airing just because a third outlet
    # covered it under a fresh link.
    seen_subjects = set(_rendered_subjects_this_run)
    for i in earnings_items:
        subj = _corporate_subject_signature(i.get("title") or "")
        if subj:
            seen_subjects.add(subj)
    for feed_key in ("restaurant_industry_headlines", "restaurant_technology_headlines"):
        for i in sections.get(feed_key, []):
            extras = i.get("extras") or {}
            badge = (extras.get("signal_badge") or "").upper()
            if not any(b in badge for b in _EARNINGS_CORPORATE_BADGES):
                continue
            title = i.get("title") or ""
            # RB-DEFECT-2026-07-27: this scan trusted extras.signal_badge at
            # face value, without the same _badge_is_justified() check
            # _fmt_headline applies for C/D -- "Wingstop Expands National
            # Wing Day into Five-Day Celebration" and "How the Jersey Mike's
            # IPO will affect the M&A market" are both upstream-tagged
            # signal_type=acquisition / [🏢 ACQUISITION] despite neither
            # describing an actual acquisition (a wing-day promo and an
            # M&A-market analysis podcast, respectively). C correctly
            # stripped the unjustified badge before display, but this loop
            # read the raw, unstripped badge and counted both as genuine
            # M&A events -- repeating stale marketing/analysis content in
            # Section E days after C had already moved past them.
            badge_match = re.match(r"^(\[[^\]]+\])\s+", title)
            if badge_match:
                bare_title = title[badge_match.end():]
                why_check = (i.get("why_it_matters") or i.get("summary") or "")
                if not _badge_is_justified(badge_match.group(1), bare_title, summary=why_check):
                    continue
            url = extras.get("source_url")
            if url and url in seen_urls:
                continue
            subj = _corporate_subject_signature(title)
            if subj and subj in seen_subjects:
                continue
            if url:
                seen_urls.add(url)
            if subj:
                seen_subjects.add(subj)
            earnings_items.append(i)

    # RB-DEFECT-2026-07-10i: seen_urls above only catches a duplicate the
    # earnings scan itself introduced (watchlist item vs. the two feed_key
    # loops) -- it never checked whether C: Restaurant Industry or
    # D: Restaurant Technology had *already rendered* this exact URL a few
    # lines earlier in the same brief. Every badge-tagged item picked up by
    # C/D's own headline selection (a common case -- badges like
    # [ACQUISITION]/[FUNDING] are exactly what makes a headline "material")
    # got shown a second time here verbatim: same title, same link, same
    # source. A/B/C/D and D+ all register into _rendered_this_run via
    # _fmt_headline; E never checked or contributed to that registry.

    if not earnings_items:
        # Canonical contract requires the section present with a one-sentence
        # "none this cycle" line rather than silently vanishing — an absent
        # section reads as "did RB even check?", not "confirmed nothing happened."
        return _earnings_no_events_text(sections)

    lines = ["## E: Earnings & Corporate\n"]
    rendered_count = 0
    for item in earnings_items:
        extras = item.get("extras") or {}
        title = (item.get("title") or "").strip()
        src_refs = item.get("source_refs") or []
        if not isinstance(src_refs, list):
            src_refs = []
        why = (item.get("why_it_matters") or item.get("summary") or "").strip()

        # Resolve a real URL (must start with http)
        url = ""
        for candidate in [extras.get("evidence_headline"), extras.get("source_url")]:
            if candidate and str(candidate).startswith("http"):
                url = str(candidate)
                break
        if not url:
            for r in src_refs:
                if isinstance(r, dict):
                    u = r.get("url") or ""
                    if u.startswith("http"):
                        url = u
                        break

        # Skip items that have no real URL and only a generic placeholder summary
        _GENERIC_PLACEHOLDERS = [
            "mentioned in today's web intelligence scan",
            "review morning_headlines for detail",
        ]
        if not url and any(p in why.lower() for p in _GENERIC_PLACEHOLDERS):
            continue

        # Already shown verbatim in an earlier section (A-D, D+) this run --
        # see RB-DEFECT-2026-07-10i above.
        if url and url in _rendered_this_run:
            continue

        # RB-DEFECT-2026-07-20 Phase 2: same story-identity dedup as C/D (see
        # the matching block in _fmt_headline) -- E has always re-implemented
        # its own version of this check independently, which is exactly how
        # it drifted out of sync with C/D's logic in the first place.
        story_key = core.resolve_story_identity(title, extras)
        skip = False
        if story_key is not None:
            found = _story_ledger.lookup(story_key)
            if found is None:
                pub = _parse_pub_date(extras.get("pub_date"))
                if pub and (today - pub).days > CORPORATE_DEDUP_GRACE_DAYS:
                    skip = True
            if not skip and not _story_ledger.should_render(
                    story_key, today, active_display_days=CORPORATE_DEDUP_GRACE_DAYS, url=url):
                skip = True
            existing_story_id = found[0] if found else None
            if not skip and existing_story_id and existing_story_id in _rendered_story_ids_this_run:
                skip = True
            if not skip and url and url in _rendered_this_run:
                skip = True
            if not skip:
                story_id = _story_ledger.record(story_key, url, title, today)
                _rendered_story_ids_this_run.add(story_id)
                if url:
                    _mark_rendered(url, title, "earnings", today, rendered or {})
        else:
            # RB-DEFECT-2026-07-14: the same-run check above only ever caught a
            # duplicate WITHIN today's brief -- it never checked whether this
            # item had already aged out of C/D on a PRIOR day (via the fix in
            # RB-DEFECT-2026-07-13). Since C/D suppress a stale corporate item
            # via _is_duplicate before E ever runs, that item is no longer in
            # _rendered_this_run when E scans -- so E kept re-including it every
            # day regardless of age. Confirmed live: "Pinkbox owner acquires
            # Hot Dog on a Stick" (first shown 2026-07-09) was still rendering
            # in E on 2026-07-14, five days past its own 4-day corporate grace
            # period, having already correctly disappeared from C days earlier.
            if url and rendered is not None and _is_duplicate(
                    url, rendered, today, is_corporate=True,
                    corporate_grace_days=CORPORATE_DEDUP_GRACE_DAYS):
                skip = True

            # RB-DEFECT-2026-07-20 Phase 3 (optional, best-effort): same
            # LLM tie-break as C/D's fallback branch (see _fmt_headline) --
            # only fires for the small minority of titles resolve_story_
            # identity() couldn't confidently key, and only for candidates
            # sharing a significant word. None (unavailable/failed) is a
            # pure no-op, falling through to the pre-Phase-2 logic below.
            tiebreak_matched = False
            if not skip:
                for candidate_id, candidate_story in core.find_tiebreak_candidates(title, _story_ledger, today):
                    if candidate_id in _rendered_story_ids_this_run:
                        skip = True
                        break
                    if llm_assist.same_story_tiebreak(title, candidate_story.get("title_sample") or "") is not True:
                        continue
                    first_seen = _parse_pub_date(candidate_story.get("first_seen"))
                    if first_seen and (today - first_seen).days > CORPORATE_DEDUP_GRACE_DAYS:
                        skip = True
                        break
                    _story_ledger.merge_into(candidate_id, url, today)
                    _rendered_story_ids_this_run.add(candidate_id)
                    if url:
                        _mark_rendered(url, title, "earnings", today, rendered or {})
                    tiebreak_matched = True
                    break

            # RB-DEFECT-2026-07-20 (Phase 1.3): same first-appearance gap as C/D
            # -- a URL E has never seen before shouldn't "first appear" already
            # days old just because corporate items get a long freshness ceiling
            # elsewhere. See the matching comment in _fmt_headline.
            if not skip and not tiebreak_matched and url and rendered is not None and url not in rendered:
                pub = _parse_pub_date(extras.get("pub_date"))
                if pub and (today - pub).days > CORPORATE_DEDUP_GRACE_DAYS:
                    skip = True

            # RB-DEFECT-2026-07-17: same republished-URL problem as C/D (see
            # _corporate_story_already_covered) -- a story already past its
            # grace window under one URL shouldn't get a fresh countdown here
            # just because this URL is new to the registry.
            covered_since = None
            if not skip and not tiebreak_matched:
                covered_since = _corporate_story_already_covered(title, rendered) if rendered is not None else None
                if covered_since:
                    covered_date = _parse_pub_date(covered_since)
                    if covered_date and url not in (rendered or {}) and (today - covered_date).days >= CORPORATE_DEDUP_GRACE_DAYS:
                        skip = True

            if not skip and not tiebreak_matched and url and rendered is not None:
                _mark_rendered(url, title, "earnings", today, rendered, is_corporate=True,
                               first_rendered_override=covered_since)

        if skip:
            continue
        # A filing shell with no retrievable financial or operating facts is
        # source-health telemetry, not a reader-facing story.  Do not send
        # "call getCompanyEarningsHistory" or exhibit-fetch failures to
        # either edition's reader.
        # RB-DEFECT-2026-08-19: this skip used to be gated on gp_context
        # (team edition only) -- the 2026-08-18 CoS editorial standard bans
        # this leak unconditionally, and the personal edition's earnings
        # item loop had no equivalent guard, so a personal-edition item
        # whose entire why-text was internal fetch-failure jargon would
        # render that jargon verbatim instead of being caught here.
        # RB-DEFECT-2026-08-19: _gp_earnings_note (the function that pulls
        # real extracted highlights -- "Quarterly revenues increased 19%..."
        # -- out of the raw press-release/filing text) used to compute and
        # render only for the team edition. Todd, personal edition: "This
        # area should surface the highest intel we have - Earnings Reports
        # and Calls and details on what they mean." The extraction itself
        # has no team-only content in it; only the display gate was wrong.
        note = _gp_earnings_note(
            why, extras.get("entity_name") or "", extras.get("category") or "")
        internal_only = any(p in why.lower() for p in (
            "no press-release text was retrievable", "exhibit fetch failed",
            "call getcompanyearningshistory", "confirmed earnings release",
        ))
        if internal_only and not note:
            continue
        if url:
            lines.append(f"[{title}]({url})")
            _rendered_this_run.add(url)
        else:
            # RB-QUALITY-2026-09-04: confirmed live -- "Chipotle — Relevant
            # Activity" rendered with no link at all and no indication why,
            # reading as a broken/incomplete entry rather than what it
            # actually is: the underlying watchlist signal (from
            # relationship_signals/intelligence_store.json) genuinely never
            # captured a source URL, only a bare headline citation ("[Fast
            # Casual] CAVA and Chipotle Take Different Paths to Traffic
            # Growth"). The real fix is upstream (that capture pipeline
            # should preserve a source link) -- not attempted here. Until
            # then, say so explicitly rather than silently presenting
            # unverifiable content as if it were a normal sourced story.
            lines.append(f"**{title}** *(no source link available)*")
        if why:
            why_trunc = _truncate_on_word(why, 200)
            lines.append(f"*{why_trunc}*")
        # 2026-08-10 feature request: earnings items get a durable
        # cross-quarter history record (system/earnings_history/
        # earnings_calls.jsonl) -- surface the pointer here rather than
        # relying on the 200-char why_it_matters truncation above, which can
        # crowd it out on a long executive-summary excerpt.
        #
        # RB-DEFECT-2026-08-18: this used to spell out the raw operation
        # name ("call getCompanyEarningsHistory for entity_name=...") --
        # already suppressed for the team edition (gp_context) as an
        # internal-instruction leak, but the personal edition kept it. The
        # 2026-08-18 CoS editorial standard prohibits internal API/script
        # syntax in reader text unconditionally, not just for team output --
        # the GPT already has this operation in its own action list and
        # doesn't need it repeated in brief prose. Keep the genuinely useful
        # part (how much history exists) in plain language.
        history_api = extras.get("history_api")
        if history_api:
            history_count = extras.get("history_count") or 0
            lines.append(
                f"*Full earnings history: {history_count} quarter"
                f"{'s' if history_count != 1 else ''} on record.*"
            )
        exhibit_url = extras.get("exhibit_url")
        if exhibit_url and exhibit_url != url:
            lines.append(f"*Primary source (press release): {exhibit_url}*")
        if note:
            lines.append(f"*{note}*")
        lines.append("")
        rendered_count += 1

    if rendered_count == 0:
        return _earnings_no_events_text(sections)
    return "\n".join(lines)


def _truncate_on_word(text: str, limit: int) -> str:
    """Truncate text to `limit` chars without cutting mid-word."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_space = cut.rfind(" ")
    if last_space > limit * 0.6:  # don't over-trim if the last space is too early
        cut = cut[:last_space]
    return cut.rstrip(" .,;:") + "…"


def _watchlist_entity_name(item: dict) -> str:
    extras = item.get("extras") or {}
    name = extras.get("entity_name") or (item.get("title") or "").split(" — ")[0].strip()
    return name


_WATCHLIST_GENERIC_PLACEHOLDERS = (
    "mentioned in today's web intelligence scan",
    "review morning_headlines for detail",
)


# RB-DEFECT-2026-08-17: confirmed live -- "Microsoft" surfaced as watchlist
# "New Activity" on the strength of "IREN Delivers Horizon 1 to Microsoft and
# Achieves NVIDIA Exemplar..." (an AI-datacenter capacity story with zero
# restaurant/payments angle), and "Amazon Web Services" via "AppFolio Names
# Amazon Web Services its Preferred Cloud Provider" (AppFolio is property-
# management software, unrelated to restaurants). Both entity names
# genuinely appear in the text -- this isn't a misattribution bug like the
# Cracker Barrel/Yum Brands case _watchlist_evidence_names_own_entity
# guards against -- these are mega-cap platforms mentioned constantly in
# unrelated contexts, so a bare name match is not enough evidence of
# relevance the way it is for a narrowly-scoped entity like "Toast" or
# "Qu." Require restaurant/hospitality/payments context alongside the name
# for this small set of maximally-generic entities specifically.
_GENERIC_MEGACAP_ENTITIES = frozenset({
    "microsoft", "amazon", "amazon web services", "aws", "google", "alphabet",
    "apple", "meta", "meta platforms", "nvidia",
})
# RB-2026-08-25: confirmed live -- "Revel" (the restaurant POS company)
# surfaced as watchlist "New Activity" on the strength of "Riverside
# Resources Executes Option Agreement for the Revel," a mining-industry
# press release about an unrelated property/claim that happens to share the
# name. Unlike the Cracker Barrel/Yum Brands mistag (_watchlist_evidence_
# names_own_entity), the word "Revel" genuinely appears in the text -- it's
# just referring to something else entirely, a different failure mode a
# literal-name-presence check can't catch. Same fix as the megacap set
# above: for entity names ambiguous enough to collide with an unrelated
# common word/place/claim name, require restaurant/payments context
# alongside the name rather than trusting a bare match.
_AMBIGUOUS_NAME_ENTITIES = frozenset({"revel"})
_RESTAURANT_PAYMENTS_CONTEXT_RE = re.compile(
    r"restaurant|hospitality|pos\b|point.of.sale|payment|merchant|drive.thru|"
    r"quick.service|qsr\b|fast.casual|menu|kitchen|foodservice|dining",
    re.IGNORECASE,
)


def _clean_watchlist_why(why: str, entity_name: str = "") -> str | None:
    """RB-DEFECT-2026-08-14: Section F's watchlist rollup builds its lines
    straight from why_it_matters/summary with no badge-justification or
    analyst-note/investor-opinion filtering -- the checks _fmt_headline
    applies to every other headline section (A-D). Confirmed live: "Blue
    Chip Partners LLC Acquires 1,314 Shares of Microsoft Corporation" got an
    [ACQUISITION] badge (a routine 13F stake disclosure, not M&A), and "Is
    NCR Voyix (VYX) Undervalued..." / "Needham raises Agilysys stock price
    target" (retail-investor opinion / analyst notes, not tracked-entity
    news) both rendered as if they were watchlist-relevant events.

    Returns None to drop the item as noise, or the (possibly badge-stripped)
    string to keep it -- same contract as _fmt_headline's title handling,
    just operating on the single why_it_matters string this section uses in
    place of a separate title field.
    """
    if not why:
        return why
    bare = why
    badge_match = re.match(r"^(\[[^\]]+\])\s+", why)
    if badge_match:
        bare = why[badge_match.end():]
    bare_l = bare.lower()
    if (_ANALYST_NOTE_PATTERN.search(bare_l)
            or _TICKER_EXPLAINER_PATTERN.search(bare)
            or _INVESTOR_OPINION_PATTERN.search(bare)
            or any(d in bare_l for d in _INVESTOR_OPINION_DOMAINS)):
        return None
    _needs_context = entity_name.strip().lower() in _GENERIC_MEGACAP_ENTITIES | _AMBIGUOUS_NAME_ENTITIES
    if _needs_context and not _RESTAURANT_PAYMENTS_CONTEXT_RE.search(bare):
        return None
    if badge_match and not _badge_is_justified(badge_match.group(1), bare):
        return bare  # strip the misclassified badge, keep the rest
    return why


# RB-2026-08-25: keyword cues that a watchlist item's own why-text is a
# leadership/personnel announcement -- used to decide whether an entity
# already covered as an [EXEC HIRE] in A-D this run (see
# _rendered_exec_hire_entities_this_run) is safe to suppress here as the
# same announcement, vs. genuinely different watchlist news about that
# entity that happens to also be in the watchlist scan today.
_LEADERSHIP_ANNOUNCEMENT_CUES = (
    "names ", "appoints", "hires", "promotes", "co-ceo", "co-ceos",
    "chief executive", "chief marketing", "chief operating", "chief financial",
    " ceo", " coo", " cmo", " cfo", "president", "executive leadership",
)


def _is_likely_same_exec_hire(item_name: str, why_text: str) -> bool:
    entity_key = core._normalize_apostrophe(item_name.strip().lower())
    if entity_key not in _rendered_exec_hire_entities_this_run:
        return False
    return any(cue in why_text.lower() for cue in _LEADERSHIP_ANNOUNCEMENT_CUES)


def _watchlist_evidence_names_own_entity(item: dict) -> bool:
    """RB-DEFECT-2026-08-11: the upstream entity classifier tagged the same
    Cracker Barrel CEO-departure story to three different watchlist entities
    -- Yum Brands, Bloomin' Brands, and (correctly) Cracker Barrel. Neither
    Yum Brands nor Bloomin' Brands is mentioned anywhere in that story; only
    "Cracker Barrel" is. Surfacing "Relevant Activity" broadly (team edition
    only) makes this kind of mis-tag visible and misleading in a way it
    wasn't when the bucket was suppressed entirely -- a GP/Genius reader
    told "Yum Brands moved" when the real subject is an unrelated company is
    a real account-intelligence accuracy risk, not just noise. Require the
    tagged entity's own name to actually appear (word-bounded) in its own
    evidence text before trusting the tag.

    Deliberately checks why_it_matters/summary only, NOT title: a watchlist
    item's title is auto-generated as f"{entity_name} — {status}" (see
    _watchlist_entity_name), so it trivially contains its own entity_name by
    construction regardless of what the evidence actually says -- checking
    title would make this a no-op that always returns True."""
    extras = item.get("extras") or {}
    name = (extras.get("entity_name") or "").strip()
    if not name:
        return True  # nothing to cross-check against; don't over-reject
    haystack = item.get("why_it_matters") or item.get("summary") or ""
    return bool(re.search(r"\b" + re.escape(name) + r"\b", haystack, re.IGNORECASE))


def _render_watchlist_material_activity(wl_items: list[dict],
                                         include_relevant_activity: bool = False) -> list[str]:
    """RB-DEFECT-2026-07-08: Section F rendered only the "No Change" count —
    escalated/new/relevant-activity entities existed (and were counted in the
    What Changed Today delta line) but were never actually listed in the
    section body itself, so "what's escalated, what's new" had to be
    reverse-engineered from a summary count with no names attached.

    include_relevant_activity (team edition only, RB-DEFECT-2026-08-11): Todd's
    personal brief deliberately dropped "Relevant Activity" entities per his
    own RB-DEFECT-2026-07-27 request below -- he can query the watchlist
    on-demand and didn't want the scan roster cluttering his reading. A
    GP/Genius sales reader has no equivalent "ask RB" fallback and does want
    to know when a tracked competitor/customer had real movement -- so the
    team edition surfaces "Relevant Activity" entities too, but only the ones
    with genuine evidence (a real source URL and a non-generic why_it_matters,
    not the bare "mentioned in today's scan" placeholder produced for every
    entity that was merely checked)."""
    escalated = [
        i for i in wl_items
        if (i.get("extras") or {}).get("watchlist_status") == "Escalation"
        and not (i.get("extras") or {}).get("loop_id")
        and "overdue loop" not in (i.get("why_it_matters") or i.get("summary") or "").lower()
    ]
    new_activity = [i for i in wl_items if (i.get("extras") or {}).get("watchlist_status") == "New Activity"]
    relevant_with_evidence: list[dict] = []
    if include_relevant_activity:
        for i in wl_items:
            if (i.get("extras") or {}).get("watchlist_status") != "Relevant Activity":
                continue
            why = (i.get("why_it_matters") or i.get("summary") or "").strip()
            url = ((i.get("extras") or {}).get("source_url") or "").strip()
            if not why or not url or any(p in why.lower() for p in _WATCHLIST_GENERIC_PLACEHOLDERS):
                continue
            if not _watchlist_evidence_names_own_entity(i):
                continue
            relevant_with_evidence.append(i)

    lines: list[str] = []
    # RB-DEFECT-2026-08-17: 17 escalated watchlist entities used to each get
    # a full duplicated block, but most of them trace back to the same
    # handful of overdue loops (McDonald's/PAR Technology/PAR Loyalty/Global
    # Payments/Harri/STRATACACHE are all L-2026-07-23-003). Group by the
    # underlying loop so a reader sees "N entities, one overdue loop" once,
    # not the identical reason repeated N times. Earnings/market-signal
    # escalations (Olo, Adyen) have no loop id to share and stay standalone.
    for group in core.group_watchlist_escalations(escalated):
        entities = group["entities"]
        name = entities[0]
        why = _clean_watchlist_why(group["why"], entity_name=name) or ""  # strip a misclassified badge; an
        # escalated entity stays escalated even if its why-text turns out to
        # be pure noise -- unlike New/Relevant Activity below, dropping the
        # whole line here isn't an option (see loop above), so it just
        # renders without a why-text line instead.
        # RB-DEFECT-2026-08-19: confirmed live -- Olo's escalated line
        # rendered "Confirmed earnings release; no press-release text was
        # retrievable (exhibit fetch failed or unavailable)..." verbatim.
        # _render_earnings already strips this exact internal fetch-failure
        # phrasing (see internal_only check above in this file), but that
        # check never ran for F -- this is the same class of leak the
        # editorial standard bans ("no verbose internals leaking into
        # reader-facing copy"), just via a different code path.
        if any(p in why.lower() for p in (
                "no press-release text was retrievable", "exhibit fetch failed",
                "call getcompanyearningshistory")):
            why = "Earnings release confirmed; filing text unavailable."
        item = group["items"][0]
        extras = item.get("extras") or {}
        url = (extras.get("source_url") or "").strip()
        if group["loop_id"] and len(entities) > 1:
            lines.append(f"**[ESCALATED] {len(entities)} entities — same overdue loop**")
            lines.append(f"  {', '.join(entities)}")
        elif not group["loop_id"] and len(entities) > 1:
            # RB-2026-09-03: url-grouped (core.group_watchlist_escalations)
            # -- multiple entities named in the same source article. Same
            # "say it once" treatment as the loop_id case above, not one
            # full duplicated block per entity. Same bold-wraps-link style
            # as the single-entity `elif url:` case below.
            label = f"{len(entities)} entities — same source article"
            lines.append(f"**[ESCALATED] [{label}]({url})**" if url else f"**[ESCALATED] {label}**")
            lines.append(f"  {', '.join(entities)}")
        elif url:
            lines.append(f"**[ESCALATED] [{name}]({url})**")
        else:
            lines.append(f"**[ESCALATED] {name}**")
        if url and url in _rendered_this_run:
            # RB-DEFECT-2026-08-12: an escalated item that's also a badge-
            # tagged earnings/corporate event (PAR/Olo/Fiserv) was rendered
            # in full in E: Earnings & Corporate, then rendered AGAIN in full
            # here -- the identical multi-hundred-word press-release blurb,
            # twice in the same document. E always runs before F, so
            # anything E already showed is in _rendered_this_run by the time
            # F renders.
            lines.append("  Earnings/corporate event — see E: Earnings & Corporate above for detail.")
        elif why and _wrb_fingerprint(why) in _rendered_wrb_fingerprints_this_run:
            # RB-2026-09-10: same "already shown above, don't restate" logic
            # as the url-keyed case above, for escalations that carry no
            # source_url (common -- e.g. this Stripe item) so the url-keyed
            # check can't catch them. See _rendered_wrb_fingerprints_this_run.
            lines.append("  Already surfaced above in What RB Found Without You Telling It.")
        elif why:
            if group["status_changed"]:
                # Fresh escalation (status differs from yesterday's brief) —
                # this is genuinely new information, show it in full.
                lines.append(f"  {why}")
            else:
                # RB-DEFECT-2026-08-12: daily_brief.py already computes
                # status_changed/prior_status by diffing against yesterday's
                # published brief -- but this renderer never consulted it,
                # so an entity that had already been "Escalation" for days
                # (e.g. PAR/Olo/Fiserv, stuck in Escalation with no
                # mechanism to ever downgrade after their one-time earnings
                # event) got the exact same full paragraph re-rendered every
                # single day. Truncate on a repeat -- loop-reference lines
                # ("Overdue loop L-... — party") are already short enough
                # that this truncation is a no-op for them; only the long
                # earnings-blurb repeats actually shrink.
                #
                # RB-DEFECT-2026-08-19: dropped the "(Unchanged since prior
                # brief)" preamble Todd flagged as pure status-tracking
                # noise -- "user doesn't care if the status changed - tell
                # me what the signal is and what it means for me." The
                # signal itself (why_short) is the whole line now.
                why_short = _truncate_on_word(why, 120)
                lines.append(f"  {why_short}")
        # INTELLIGENCE_BRIEF_CANONICAL.md: Part 1 never carries a
        # recommendation ("Any recommendation... belongs in Daily Brief").
        # recommended_action ("→ Follow up immediately") used to render here
        # unconditionally for every escalated entity -- that's the Daily
        # Brief's job (Section 3 Technology Radar / CoS Recommendations).
    if escalated:
        lines.append("")

    if new_activity:
        # RB-DEFECT-2026-08-14: was capped at [:15] with a "...and N more"
        # teaser for the remainder -- Todd asked to see every item, not a
        # hidden count. Items dropped by _clean_watchlist_why (analyst
        # notes/investor-opinion noise) are noise, not "more"; the header
        # count reflects what's actually about to render, below.
        rendered_new: list[tuple[dict, str, str]] = []
        for item in new_activity:
            item_name = _watchlist_entity_name(item)
            why = (item.get("why_it_matters") or item.get("summary") or "").strip()
            why = _clean_watchlist_why(why, entity_name=item_name)
            if why is None:
                continue
            if _is_likely_same_exec_hire(item_name, why):
                # RB-2026-08-25: this entity's leadership change was already
                # told in Section C/D above (different pipeline, different
                # URL, same real-world event) -- don't re-announce it here.
                continue
            why_short = (why[:140] + "…") if len(why) > 140 else why
            rendered_new.append((item, why_short, item_name))
        lines.append(f"**New activity ({len(rendered_new)}):**")
        for item, why_short, name in rendered_new:
            # RB-DEFECT-2026-07-10e: press-release/alert evidence had a real
            # source URL captured in daily_brief.py but never rendered as a
            # link, unlike every other headline section (A-E) — the entity
            # name became the clickable anchor when a URL is present, same
            # pattern as [title](url) elsewhere in this brief.
            url = ((item.get("extras") or {}).get("source_url") or "").strip()
            entity_label = f"[{name}]({url})" if url else name
            lines.append(f"- {entity_label} — {why_short}" if why_short else f"- {entity_label}")
        lines.append("")

    # RB-DEFECT-2026-07-27: "Relevant Activity" (background monitoring with
    # no material development) used to be enumerated by name here -- Todd
    # explicitly asked to stop for HIS OWN personal brief: "I don't need to
    # know which companies you scanned — I can ask who is on the watchlist
    # if I want to know." That's still true for the default (personal) path
    # below. See include_relevant_activity above for why the team edition
    # diverges from this.
    if relevant_with_evidence:
        # RB-DEFECT-2026-08-14: same "...and N more" teaser removed here as
        # New Activity above -- show every item, and filter noise (analyst
        # notes/investor-opinion pieces, misclassified badges) rather than
        # arbitrarily hiding items past a fixed count.
        rendered_relevant: list[tuple[dict, str, str]] = []
        for item in relevant_with_evidence:
            item_name = _watchlist_entity_name(item)
            why = (item.get("why_it_matters") or item.get("summary") or "").strip()
            why = _clean_watchlist_why(why, entity_name=item_name)
            if why is None:
                continue
            if _is_likely_same_exec_hire(item_name, why):
                continue
            why_short = (why[:140] + "…") if len(why) > 140 else why
            rendered_relevant.append((item, why_short, item_name))
        lines.append(f"**Also moving ({len(rendered_relevant)}):**")
        for item, why_short, name in rendered_relevant:
            url = ((item.get("extras") or {}).get("source_url") or "").strip()
            entity_label = f"[{name}]({url})" if url else name
            lines.append(f"- {entity_label} — {why_short}" if why_short else f"- {entity_label}")
        lines.append("")

    return lines


def _render_watchlist_rollup(sections: dict, today: date | None = None,
                              include_relevant_activity: bool = False) -> str:
    wl_items = sections.get("watchlist_intelligence", [])
    material_lines = _render_watchlist_material_activity(
        wl_items, include_relevant_activity=include_relevant_activity)

    # Surface price watch signals written today from market_signals_earnings.jsonl
    # Consolidate price-move + volume-spike for the same ticker into one entry.
    price_lines: list[str] = []
    try:
        signals_path = core.SYSTEM_DIR / "inbox" / "market_signals_earnings.jsonl"
        today_str = (today or date.today()).isoformat()
        if signals_path.exists():
            # Group all today's price-watch signals by ticker
            from collections import defaultdict as _dd
            by_ticker: dict = _dd(list)
            for raw in signals_path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                sig = json.loads(raw)
                if not sig.get("_price_watch"):
                    continue
                if str(sig.get("published_at") or "")[:10] != today_str:
                    continue
                ticker = (sig.get("ticker") or sig.get("company") or "UNKNOWN").upper()
                by_ticker[ticker].append(sig)

            # Broad-market context: if ≥4 tickers are all moving the same direction,
            # it's likely a sector/market rally rather than individual events.
            up_movers = [t for t, sigs in by_ticker.items()
                         if any("PRICE MOVE" in (s.get("title") or "") and
                                ("↑" in (s.get("title") or "") or float((s.get("pct_change") or 0)) > 0)
                                for s in sigs)]
            broad_market_note = ""
            if len(up_movers) >= 4:
                broad_market_note = (
                    f"*Note: {len(up_movers)} watchlist names moving higher simultaneously — "
                    f"likely reflects broad market or sector rally, not individual events.*"
                )

            for ticker, sigs in by_ticker.items():
                # Determine the canonical URL (prefer non-Yahoo fallback if available)
                url = next((s.get("url") for s in sigs if s.get("url")), "")

                # Collect signal types for this ticker
                price_sig = next((s for s in sigs if "PRICE MOVE" in (s.get("title") or "")), None)
                vol_sig = next((s for s in sigs if "VOLUME SPIKE" in (s.get("title") or "")), None)
                hi_lo_sig = next((s for s in sigs if any(x in (s.get("title") or "") for x in ("52W HIGH", "52W LOW"))), None)

                # Build consolidated label
                parts_label = []
                if price_sig:
                    # Extract move direction/magnitude from title e.g. "↑6.2%"
                    import re as _re3
                    m = _re3.search(r"([↑↓][0-9.]+%)", price_sig.get("title") or "")
                    pct = m.group(1) if m else ""
                    signal_type = "📈" if "↑" in (price_sig.get("title") or "") else "📉"
                    parts_label.append(f"{signal_type} {pct}")
                if vol_sig:
                    m2 = _re3.search(r"([0-9.]+x) normal volume", vol_sig.get("detail") or vol_sig.get("pain_point_or_priority") or "")
                    vol_str = f"{m2.group(1)} vol" if m2 else "vol spike"
                    parts_label.append(vol_str)
                if hi_lo_sig:
                    is_hi = "52W HIGH" in (hi_lo_sig.get("title") or "")
                    parts_label.append("🔺 near 52W high" if is_hi else "🔻 near 52W low")

                # Get company name and price detail from price_sig or first sig
                ref_sig = price_sig or hi_lo_sig or sigs[0]
                company = ref_sig.get("company") or ticker
                detail = (ref_sig.get("pain_point_or_priority") or "").strip()
                # Shorten detail to first sentence
                import re as _re4
                first_sent = _re4.split(r"(?<=[.!?])\s", detail)[0] if detail else ""
                if len(first_sent) > 150:
                    first_sent = first_sent[:147] + "…"

                label_str = " · ".join(parts_label) if parts_label else "signal"
                consolidated_title = f"{company} ({ticker}) — {label_str}"
                if url:
                    price_lines.append(f"[{consolidated_title}]({url})")
                else:
                    price_lines.append(f"**{consolidated_title}**")
                if first_sent and "no confirmed news" not in first_sent.lower():
                    price_lines.append(f"*{first_sent}*")
                price_lines.append("")

            if broad_market_note:
                price_lines.insert(0, broad_market_note)
                price_lines.insert(1, "")
    except Exception:
        pass

    parts = ["## F: Watchlist\n"]
    parts.extend(material_lines)
    parts.extend(price_lines)
    # RB-DEFECT-2026-07-27: the "N other entities unchanged since last scan"
    # count (and the "Relevant activity" name list above) were both removed
    # per explicit request -- Todd doesn't need the scanned/unchanged
    # roster in the brief, only escalations and genuinely new activity.
    # Still distinguish "quiet day" (data existed, nothing material) from a
    # genuine data gap (no watchlist_intelligence at all) rather than
    # collapsing both into the same message.
    if not material_lines and not price_lines:
        parts.append("*No escalations or new watchlist activity today.*" if wl_items else "*No watchlist data.*")
    return "\n".join(parts)


_CLOSED_OPPORTUNITY_STATES = {"CLOSED", "CLOSED_SILENT", "REJECTED"}
# A closure is only a delta worth reporting for a day or two after it happens —
# beyond that it's the same historical fact repeating, which is exactly what
# Part 1's "delta report, not a telemetry dump" rule prohibits. Patrick Nelson
# (closed 52 days of evidence-age ago) and Coates Group (49d) would otherwise
# re-render as if they were today's news every single day forever.
_RECENT_CLOSURE_MAX_AGE_DAYS = 2


def _render_opportunities(sections: dict) -> str:
    """G: Opportunities — current commercial opportunities only."""
    items = sections.get("opportunity_board", []) + sections.get("w2_intelligence", [])

    active: list[tuple[dict, dict]] = []
    recent_closures: list[tuple[dict, dict]] = []
    for item in items:
        extras = item.get("extras") or {}
        state = (extras.get("opportunity_state") or extras.get("state") or "").upper()
        if state in ("ACTIVE", "WAITING"):
            active.append((item, extras))
        elif state in _CLOSED_OPPORTUNITY_STATES:
            age = extras.get("evidence_age_days")
            title_text = (item.get("title") or "").lower()
            career_item = any(term in title_text for term in (
                "role conversation", "job", "recruiter", "director", "commercial director"
            ))
            if not career_item and age is not None and age <= _RECENT_CLOSURE_MAX_AGE_DAYS:
                recent_closures.append((item, extras))
        # else: no recognized state (e.g. the "no active opportunities" w2 stub) — skip

    strategic_names = ("Pollo Campero", "McDonald's", "IKEA")
    strategic: list[dict] = []
    for item in sections.get("loops_and_obligations", []) or []:
        haystack = f"{item.get('title') or ''} {item.get('summary') or ''}"
        if any(name.lower() in haystack.lower() for name in strategic_names):
            strategic.append(item)
    # Future/parked commercial loops do not appear in the overdue-only
    # canonical section. Pull them from the ledger so a live account such as
    # IKEA is not displaced by an old career record.
    try:
        ledger_loops = core.parse_loop_ledger() if "loops_and_obligations" in sections else []
        for loop in ledger_loops:
            if loop.closed:
                continue
            matched = next((name for name in strategic_names
                            if name.lower() in f"{loop.party} {loop.description}".lower()), None)
            if not matched:
                continue
            if any(matched.lower() in f"{i.get('title') or ''} {i.get('summary') or ''}".lower()
                   for i in strategic):
                continue
            strategic.append({
                "title": loop.party,
                "summary": loop.description,
                "extras": {"target_date": loop.target.isoformat(), "account_name": matched},
            })
    except Exception:
        pass

    if not active and not recent_closures and not strategic:
        return "## G: Opportunities\n\n*No verified active commercial opportunities.*"

    lines = ["## G: Opportunities\n"]

    account_order = {name.lower(): idx for idx, name in enumerate(strategic_names)}
    def _account_rank(item: dict) -> int:
        text = f"{item.get('title') or ''} {item.get('summary') or ''}".lower()
        return min((rank for name, rank in account_order.items() if name in text), default=99)

    shown_accounts: set[str] = set()
    for item in sorted(strategic, key=_account_rank):
        text = f"{item.get('title') or ''} {item.get('summary') or ''}".lower()
        account = next((name for name in strategic_names if name.lower() in text), "")
        if account.lower() in shown_accounts:
            continue
        shown_accounts.add(account.lower())
        title = re.sub(r"^(?:Overdue:\s*)?L-[^—]+—\s*", "", (item.get("title") or "").strip())
        if account and account.lower() not in title.lower():
            title = f"{account} — {title}"
        summary = re.sub(r"\*+", "", (item.get("summary") or "").strip())
        next_sentence = re.split(r"(?<=[.!?])\s+", summary)[0]
        lines.append(f"- **{title}** — {_truncate_on_word(next_sentence, 150)}")
        if len(shown_accounts) >= 3:
            break

    def _clean_title(item: dict) -> str:
        return re.sub(r"^\[[^\]]+\]\s*", "", (item.get("title") or "").strip())

    for item, extras in active:
        state = (extras.get("opportunity_state") or extras.get("state") or "UNKNOWN").upper()
        age = extras.get("evidence_age_days")
        if age is None:
            label = state
        elif age > 14:
            label = "STALE⚠"
        elif age <= 1:
            label = "NEW"
        else:
            label = "UNCHANGED"
        age_str = f" ({age}d since last evidence)" if age is not None else ""
        lines.append(f"- **{_clean_title(item)}** — {label}{age_str}")

    for item, extras in recent_closures:
        state = (extras.get("opportunity_state") or extras.get("state") or "").upper()
        lines.append(f"- **{_clean_title(item)}** — {state} (closed within last {_RECENT_CLOSURE_MAX_AGE_DAYS}d)")

    return "\n".join(lines)


def _render_relationship_deltas(sections: dict, today: date,
                                prior_state: dict | None = None) -> str:
    """H: Relationship Deltas — contacts with activity in the last 24 hours only.

    A 24h hard cutoff prevents stale interactions from repeating across days.
    Contacts already shown in the last 7 briefs with the same timestamp are suppressed.
    """
    import re as _re
    items = sections.get("last_24h_relationship_signals", [])
    prior_delta_keys: set = (prior_state or {}).get("relationship_delta_keys", set())

    lines = ["## H: Relationship Deltas\n"]
    seen: set = set()
    rendered: list = []
    yesterday = today - timedelta(days=1)

    for item in items:
        name = (item.get("title") or "").strip()
        summary = (item.get("summary") or "").strip()
        if not name or name in seen or not summary:
            continue
        if "no" in name.lower() and "signal" in name.lower():
            continue
        extras = item.get("extras") or {}
        lifecycle = item.get("intelligence_lifecycle") or {}
        seen.add(name)

        # Extract last-event timestamp from summary
        ts_match = _re.search(r"Last event (\S+)", summary)
        ts_val = (extras.get("signal_timestamp") or (ts_match.group(1) if ts_match else ""))

        # 24h hard cutoff: event must be today or yesterday
        event_is_fresh = False
        if ts_val and len(ts_val) >= 10:
            try:
                event_date = date.fromisoformat(ts_val[:10])
                event_is_fresh = event_date >= yesterday
            except ValueError:
                pass

        # Also allow items explicitly referencing today's date or calendar events
        if not event_is_fresh:
            if today.isoformat() in summary:
                event_is_fresh = True

        learned_today = str(lifecycle.get("first_seen") or "")[:10] == today.isoformat()
        if not event_is_fresh and not learned_today:
            continue  # neither a fresh interaction nor newly learned this cycle

        # Suppress if we already showed this exact name+timestamp in a prior brief
        dedup_key = f"{name}|{ts_val}"
        if dedup_key in prior_delta_keys:
            continue

        freshness = (item.get("freshness") or "").upper()
        if learned_today:
            status = "LEARNED TODAY"
        elif today.isoformat() in summary or "NEW" in freshness or "RECENT" in freshness:
            status = "NEW"
        else:
            status = "ACTIVE"

        # Reformat raw telemetry ("0/0 msg in/out, 0/1/1 calls in/out/missed") into a
        # human sentence rather than leaking the internal counter format.
        tele_match = _re.search(
            r"(\d+)/(\d+)\s*msg in/out,\s*(\d+)/(\d+)/(\d+)\s*calls in/out/missed",
            summary,
        )
        if tele_match:
            msg_in, msg_out, call_in, call_out, call_missed = (int(x) for x in tele_match.groups())
            parts = []
            if msg_in or msg_out:
                parts.append(f"{msg_in} message{'s' if msg_in != 1 else ''} in / {msg_out} out")
            if call_in or call_out:
                parts.append(f"{call_in} call{'s' if call_in != 1 else ''} in / {call_out} out")
            if call_missed:
                parts.append(f"{call_missed} missed call{'s' if call_missed != 1 else ''}")
            activity_str = ", ".join(parts) if parts else "no message or call activity"
            time_str = ts_val[11:16] if len(ts_val) >= 16 else ""
            human_summary = f"{activity_str}" + (f" — last contact at {time_str} UTC" if time_str else "")
        else:
            human_summary = (summary[:297] + "…") if len(summary) > 300 else summary

        rendered.append(f"- **{name}** [{status}] — {human_summary}")

    if rendered:
        lines.extend(rendered)
    else:
        lines.append("*No new relationship activity in the last 24 hours.*")

    return "\n".join(lines)


def _get_watchlist_rally(today: date) -> dict | None:
    """Return a synthetic signal dict if ≥4 watchlist names moved up today."""
    try:
        pw_path = core.SYSTEM_DIR / "inbox" / "market_signals_earnings.jsonl"
        today_str = today.isoformat()
        up_movers: list[str] = []
        seen: set[str] = set()
        if pw_path.exists():
            for raw in pw_path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                sig = json.loads(raw)
                if str(sig.get("published_at") or "")[:10] != today_str:
                    continue
                if "PRICE MOVE" not in (sig.get("title") or ""):
                    continue
                if "↑" not in (sig.get("title") or ""):
                    continue
                company = (sig.get("company") or "").strip()
                if company and company not in seen:
                    seen.add(company)
                    up_movers.append(company)
        if len(up_movers) < 4:
            return None
        names = ", ".join(up_movers[:5])
        if len(up_movers) > 5:
            names += f" +{len(up_movers) - 5} more"
        return {
            "title": f"Sector-wide equity rally — {len(up_movers)} watchlist names up simultaneously",
            "summary": (
                f"Broad institutional buying across restaurant tech and payments: {names}. "
                f"Simultaneous movement across {len(up_movers)} names points to macro/sector rotation, "
                f"not company-specific news. Watch for deal announcements or macro catalyst confirmation."
            ),
            "_synthetic": True,
        }
    except Exception:
        return None


def _render_strategic_signals(sections: dict, prior_state: dict | None = None,
                               today: date | None = None) -> str:
    items = list(sections.get("strategic_industry_signals", []))

    # Inject sector rally as a synthetic signal if warranted and not already in the list
    if today is not None:
        rally = _get_watchlist_rally(today)
        if rally:
            rally_keyword = "sector-wide equity rally"
            already_present = any(
                rally_keyword in (i.get("title") or "").lower()
                for i in items
            )
            if not already_present:
                items.insert(0, rally)

    if not items:
        return "## I: Strategic Signals\n\n*No converging signals identified this cycle.*"

    prior_fingerprints: set = (prior_state or {}).get("strategic_signal_fingerprints", set())
    prior_counts: dict = (prior_state or {}).get("strategic_signal_counts", {})
    last_rendered: dict = (prior_state or {}).get("strategic_signal_last_rendered", {})
    STALE_RETIREMENT_DAYS = 5  # drop a signal from the footnote after this many consecutive days with no new evidence

    lines = ["## I: Strategic Signals\n"]
    suppressed: list[str] = []  # track signals suppressed as unchanged
    retired: list[str] = []     # track signals dropped entirely as stale
    rendered_count = 0
    for item in items[:5]:
        import re as _re
        title = (item.get("title") or "").strip()
        summary = (item.get("summary") or "").strip()
        why = (item.get("why_it_matters") or "").strip()
        action = (item.get("recommended_action") or "").strip()
        extras = item.get("extras") or {}
        src_refs = item.get("source_refs") or []
        if not isinstance(src_refs, list):
            src_refs = []

        # Extract evidence count and channel count from summary
        evidence_count = ""
        channel_count = ""
        ev_match = _re.search(r"across (\d+) evidence item\(s\) and (\d+) source channel\(s\)", summary)
        if ev_match:
            evidence_count = ev_match.group(1)
            channel_count = ev_match.group(2)

        # Synthetic signals (e.g. sector rally) always render — no fingerprint dedup
        is_synthetic = item.get("_synthetic", False)

        # A stale event can still render as "new" here if this exact
        # (title, evidence_count) fingerprint simply never made a prior
        # brief's top-5 cut — fingerprint novelty only tells us "did WE
        # report this," not "is this actually new information." Check the
        # event's own evidence age (computed at the source in daily_brief.py
        # from strategic_events.json's last_seen_at, since that field is
        # frozen at ingestion and never re-evaluated against today) first,
        # independent of fingerprint/rendering history.
        if not is_synthetic and extras.get("is_actually_stale"):
            days_stale = extras.get("days_since_evidence") or STALE_RETIREMENT_DAYS
            if days_stale >= STALE_RETIREMENT_DAYS + 7:  # well past ongoing — drop it
                retired.append(title)
            else:
                suppressed.append(title)
            continue

        # Suppress if this exact title+count combo appeared in any of the last 7 briefs
        fingerprint = (title, evidence_count)
        if not is_synthetic and fingerprint in prior_fingerprints:
            # Retire entirely (don't even footnote) once it's gone N+ CONSECUTIVE days with
            # no new evidence — repeating "no new evidence" forever isn't useful, it's noise.
            # Measured from the last day this exact signal was actively rendered (i.e. had
            # new/changed evidence), not from how many of the last 7 days it merely appeared —
            # a signal footnoted as "ongoing" on 5 of the last 7 days but rendered fresh
            # yesterday is 1 day stale, not 5+.
            last_rendered_date = last_rendered.get(title)
            stale_days = (today - last_rendered_date).days if (today and last_rendered_date) else STALE_RETIREMENT_DAYS
            if stale_days >= STALE_RETIREMENT_DAYS:
                retired.append(title)
            else:
                suppressed.append(title)
            continue

        # Compute delta vs. prior max seen (e.g., "36 items (+6 since last shown)")
        delta_str = ""
        if evidence_count:
            try:
                curr = int(evidence_count)
                prev_max = prior_counts.get(title, 0)
                if prev_max > 0 and curr > prev_max:
                    delta_str = f" ↑+{curr - prev_max} since last reported"
                elif prev_max > 0:
                    delta_str = f" (ongoing)"
            except ValueError:
                pass

        # Strip internal pipeline labels before rendering
        clean = summary
        for prefix in ("validated_signal ", "validated_signal\n", "multiple-source convergence: "):
            if clean.lower().startswith(prefix):
                clean = clean[len(prefix):]
        clean = clean.strip()
        _FEED_LABELS = {
            "company_press": "company press",
            "enterprise_restaurant_technology_news": "restaurant tech news",
            "restaurant_trade_news": "restaurant trade news",
            "linkedin_social": "LinkedIn",
            "legal_business_news": "business news",
            "general_web": "web",
        }
        for raw, readable in _FEED_LABELS.items():
            clean = clean.replace(raw, readable)
        clean_trunc = (clean[:297] + "…") if len(clean) > 300 else clean

        evidence_parts = []
        for r in src_refs[:3]:
            if isinstance(r, dict):
                url_val = r.get("url") or r.get("source_url") or ""
                src = r.get("name") or r.get("publication") or ""
                dt = r.get("date") or ""
                if url_val and url_val.startswith("http"):
                    evidence_parts.append(url_val)
                elif src:
                    evidence_parts.append(f"{src} {dt}".strip())
            elif isinstance(r, str) and r:
                if not r.startswith("strategic_events."):
                    evidence_parts.append(r)

        # Append delta annotation to the evidence count line in clean_trunc
        if delta_str and evidence_count:
            clean_trunc = clean_trunc.replace(
                f"across {evidence_count} evidence",
                f"across {evidence_count} evidence{delta_str}"
            )

        reader_title = _re.sub(r"^Multiple-source convergence:\s*", "", title, flags=_re.IGNORECASE)
        lines.append(f"**{reader_title}**")
        if why:
            lines.append(f"{why}")
        elif clean_trunc:
            lines.append(clean_trunc)
        if evidence_count or channel_count:
            delta_note = delta_str.strip() if delta_str else ""
            evidence_note = f"Supported by {evidence_count or 'multiple'} evidence items across {channel_count or 'multiple'} source channels"
            lines.append(f"*Confidence basis: {evidence_note}{('; ' + delta_note) if delta_note else ''}.*")
        if action:
            lines.append(f"**What RB will do:** {action}.")
        if evidence_parts:
            src_display = evidence_parts[0]
            if ":" in src_display and not src_display.startswith("http"):
                colon_idx = src_display.index(":")
                candidate = src_display[colon_idx + 1:].strip()
                if candidate.startswith("http"):
                    src_display = candidate
            lines.append(f"*Source: {src_display}*")
        lines.append("")
        rendered_count += 1

    if rendered_count == 0:
        note = ""
        if suppressed:
            note = f" Ongoing signals with no new evidence: {'; '.join(suppressed[:3])}."
        if retired:
            note += f" Retired (stale {STALE_RETIREMENT_DAYS}+ days, no longer tracked): {'; '.join(retired[:3])}."
        return f"## I: Strategic Signals\n\n*No new converging signals since yesterday.{note}*"

    # Show suppressed-but-ongoing signals as a footnote so they're not invisible
    if suppressed:
        lines.append(f"*Ongoing (no change in 7 days): {'; '.join(suppressed[:5])}*")
        lines.append("")
    if retired:
        lines.append(f"*Retired from tracking (stale {STALE_RETIREMENT_DAYS}+ days, no new evidence): {'; '.join(retired[:5])}*")
        lines.append("")

    return "\n".join(lines)


# RB-DEFECT-2026-07-10: this section rendered intelligence_triage.py's raw
# classification internals verbatim -- "ri event: Person-level RI signal
# detected: 5 trigger(s): joined, meeting with, promoted. Possible named
# subjects: Hey Dolor, Thank, Day, Compliance, Yep, Yep." "Hey Dolor" /
# "Thank" / "Yep, Yep" are transcript filler words the triage engine's name
# heuristic mistook for people's names, and "ri event"/"micro graph
# enrichment" are internal type labels, not reader-facing prose. The triage
# engine's own extracted_summary strings were never meant to be shown to a
# reader as-is -- they're diagnostic output for whoever built the classifier.
# Clean display labels + strip the unreliable name clause rather than
# attempting to fix name-extraction quality at this render layer.
_CAPTURE_INTELLIGENCE_TYPE_LABELS = {
    "ri_event": "Relationship signal",
    "career_pipeline_update": "Career/opportunity update",
    "micro_graph_enrichment": "Company data enrichment",
    "micro_graph_build": "New company profile candidate",
    "strategic_memory": "Strategic insight",
    "macro_signal": "Market signal",
    "executive_declaration": "Personal declaration",
}

_CAPTURE_NAMED_SUBJECTS_CLAUSE_RE = re.compile(
    r"\s*Possible named subjects:[^.]*\.", re.IGNORECASE,
)


def _clean_capture_summary(summary: str) -> str:
    """Strip the unreliable 'Possible named subjects: ...' clause (filler
    transcript words misidentified as person names) from a triage-engine
    extracted_summary string, keeping the trigger-detection sentence, which
    is at least an accurate description of what pattern fired."""
    return _CAPTURE_NAMED_SUBJECTS_CLAUSE_RE.sub("", summary).strip()


_PATTERN_LABELS = {
    "exit_positioning": "Exit-positioning",
    "distress": "Distress",
    "consolidation": "Consolidation",
    "competitive_shift": "Competitive shift",
    "growth_mode": "Growth mode",
}


def _render_entity_convergence(scan_result: dict | None) -> str:
    """I+: Entity Signal Convergence -- RB-2026-08-28, strategic-assessment
    priority item #5 ("seeing what the CEO doesn't see"). Runs
    entity_convergence_scan.py's already-computed result (a scheduled
    morning_pipeline.py step, not computed here) across the bounded set of
    entities Todd is actually working (active Blue Sheet accounts, tracked
    competitors, watchlist tier_1) through signal_synthesis.py's existing,
    already-tested cross-store pattern engine -- mechanical scoring of real,
    already-persisted signals against a fixed taxonomy, no free-text
    generation, no new fabrication surface.

    Same repeat-noise discipline already proven twice elsewhere in this
    codebase (watchlist escalation decay, loop-ledger suppression): a new
    or changed classification gets full detail; a persisting one gets a
    one-line mention, not the same paragraph re-rendered every morning."""
    if not scan_result:
        return "## I+: Entity Signal Convergence\n\n*Scan not available this cycle.*"

    new_findings = scan_result.get("new_findings") or []
    persisting = scan_result.get("persisting_findings") or []
    scanned = scan_result.get("entities_scanned", 0)

    if not new_findings and not persisting:
        # A normal zero is operational telemetry, not executive intelligence.
        # Repeated-zero health detection belongs in the watchdog, where the
        # system can distinguish a quiet day from a broken detector.
        return ""

    lines = ["## I+: Entity Signal Convergence\n"]
    for f in new_findings:
        label = _PATTERN_LABELS.get(f["dominant_pattern"], f["dominant_pattern"])
        lines.append(f"**{f['entity']} — {label}** ({f['pattern_confidence']} confidence, {f['signal_count']} signals)")
        if f.get("synthesis_hypothesis"):
            lines.append(f"  {f['synthesis_hypothesis']}")
        if f.get("opportunity_or_risk"):
            lines.append(f"  *{f['opportunity_or_risk']}*")
        lines.append("")

    if persisting:
        names = ", ".join(
            f"{f['entity']} ({_PATTERN_LABELS.get(f['dominant_pattern'], f['dominant_pattern'])}, since {f['first_seen']})"
            for f in persisting
        )
        lines.append(f"*Still current: {names}.*")

    return "\n".join(lines).rstrip()


def _render_early_signals(payload: dict | None) -> str:
    """Render only review-worthy anticipatory signals; quiet days stay quiet."""
    signals = [
        item for item in (payload or {}).get("signals", [])
        if item.get("confidence") in {"high", "medium"}
    ][:6]
    if not signals:
        return ""
    lines = ["## Early Signals — Before the Headlines\n"]
    for item in signals:
        entity = item.get("entity") or "Unresolved entity"
        kinds = ", ".join(item.get("signal_types") or [])
        horizon = item.get("expected_horizon") or "timing unknown"
        lines.append(f"**{entity}** — {item.get('confidence', 'unknown')} confidence; {kinds}; {horizon}")
        evidence = item.get("evidence_chain") or []
        if evidence:
            first = evidence[0]
            label = first.get("title") or first.get("url") or "source"
            url = first.get("url")
            lines.append(f"  Evidence: [{label}]({url})" if url else f"  Evidence: {label}")
        if item.get("what_confirms"):
            lines.append(f"  Confirm with: {item['what_confirms']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _render_intelligence_assessment_summary(sections: dict) -> str:
    """Overnight Intelligence Scan -- trust stats, sustained multi-source
    patterns, and review-first mutation proposals from intelligence_
    assessment.py's Phase 3/4.

    RB-2026-08-24: daily_brief.py has computed this section
    (sections["intelligence_assessment_summary"]) since Sprint F, but no
    renderer ever read it -- real, dated findings ("Burger King has 307
    items across 6 sources but isn't on your watchlist") were generated
    every day and never once reached the actual brief. This closes that gap.
    """
    items = sections.get("intelligence_assessment_summary", [])
    if not items:
        return ""

    header_item = next((i for i in items if "trust_stats" in (i.get("extras") or {})), None)
    pattern_items = [i for i in items
                      if (i.get("extras") or {}).get("convergence_type") == "sustained_multi_source"]
    proposal_items = [i for i in items if "proposal_type" in (i.get("extras") or {})]

    # No real assessment ran (e.g. the "not yet run" fallback item) -- an
    # internal ops message, not reader-facing intelligence. Stay silent
    # rather than clutter the brief with something the reader can't act on.
    if header_item is None:
        return ""

    if not pattern_items and not proposal_items:
        return ""

    lines = ["## Overnight Intelligence Scan\n"]

    if pattern_items:
        lines.append("**Companies RB is now monitoring more closely:**")
        for item in pattern_items:
            extras = item.get("extras") or {}
            entity = extras.get("entity") or (item.get("title") or "").replace("[SUSTAINED] ", "").split(" — ", 1)[0]
            signal_types = [str(x).replace("_", " ") for x in (extras.get("signal_types") or [])]
            basis = ", ".join(signal_types[:3]) or "multi-source activity"
            lines.append(
                f"- **{entity}** — repeated activity across independent sources, led by {basis}. "
                "RB will report again only when the pattern changes or creates a concrete opportunity or risk."
            )
        lines.append("")

    if proposal_items:
        lines.append("**Watchlist changes:**")
        for item in proposal_items:
            title = (item.get("title") or "").replace("[PROPOSED] ", "")
            action = item.get("recommended_action", "")
            if "added to watchlist" in title.lower() or "auto-added" in action.lower():
                lines.append(f"- **{title}** — remove it later if it proves irrelevant.")
            else:
                lines.append(f"- **{title}** — {action}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _capture_intelligence_reported_path() -> Path:
    # RB-2026-09-21: originally derived from PROCESSED_DIR.parent instead of
    # capture_ingest.CAPTURES_DIR directly, on the assumption every test here
    # sets PROCESSED_DIR to a "processed" subdirectory of an isolated tmp
    # root -- wrong: several existing tests in this area set PROCESSED_DIR to
    # the tmp directory itself, so .parent resolved to the *shared* OS temp
    # root, not an isolated location, and different tests' captures started
    # suppressing each other through it. Back to the explicit dependency:
    # every test that calls this function must isolate CAPTURES_DIR
    # alongside PROCESSED_DIR (see test_capture_intelligence_freshness.py and
    # the other capture-intelligence test files for the pattern).
    import capture_ingest
    return capture_ingest.CAPTURES_DIR / ".capture_intelligence_reported.json"


def _load_capture_intelligence_reported() -> set[str]:
    path = _capture_intelligence_reported_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ids = data.get("file_ids")
        if isinstance(ids, list):
            return set(ids)
    except (OSError, ValueError):
        pass
    return set()


def _record_capture_intelligence_reported(file_ids: set[str]) -> None:
    if not file_ids:
        return
    path = _capture_intelligence_reported_path()
    existing = _load_capture_intelligence_reported()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"file_ids": sorted(existing | file_ids)}, indent=2) + "\n", encoding="utf-8")


def _render_capture_intelligence() -> str:
    """Capture Intelligence summary for the Intelligence Brief.

    Reports on captures processed since they last appeared in this section,
    and surfaces cross-session patterns. Pending captures are shown in the
    Daily Brief instead (to drive action), not here.

    Before the freshness filter, this re-described the same static handful of
    processed files (by mtime, no recency check) every single day once
    nothing new had been captured in days — e.g. "Processed: 3 meeting · 87
    words captured" repeating verbatim for a week off 3 files from a single
    July 1 test session, giving the false impression of fresh daily activity.

    RB-2026-09-21: the fix above used a fixed 36h processed_at lookback --
    wider than the ~24h daily brief cadence, so a capture processed in
    roughly the last third of a day fell inside *two* consecutive days'
    windows and got reported twice. Confirmed live: several 2026-09-16 JPR
    recordings appeared in both the 2026-09-17 AND 2026-09-18 Intelligence
    Briefs. Same class of bug capture_ingest.py's own _find_new_files()
    already hit once (a recency window is not a substitute for tracking
    what was actually already shown, RB-2026-08-28) -- fixed the same way
    here: a persisted "already reported" file_id set, not a time window.
    """
    try:
        import capture_ingest
        processed_dir = capture_ingest.PROCESSED_DIR
        if not processed_dir.exists():
            return ""
        recent = sorted(processed_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:20]
        if not recent:
            return ""
        all_items = []
        for p in recent:
            try:
                all_items.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
    except Exception:
        return ""

    if not all_items:
        return ""

    reported_ids = _load_capture_intelligence_reported()

    def _not_yet_reported(item: dict) -> bool:
        return item.get("file_id") not in reported_ids

    items = [item for item in all_items if _not_yet_reported(item)]
    if not items:
        return "## Capture Intelligence\n\n*No captures processed since the last brief.*"

    # Count by type and source
    by_type: dict[str, int] = {}
    by_source: dict[str, int] = {}
    total_words = 0
    for item in items:
        ct = item.get("capture_type", "unknown")
        sl = item.get("source_label", "unknown")
        by_type[ct] = by_type.get(ct, 0) + 1
        by_source[sl] = by_source.get(sl, 0) + 1
        total_words += item.get("word_count", 0)

    # RB 2026-08-26: reviewed live -- "2 Recent" labeled two captures whose
    # actual recording dates were 2026-07-22 and 2026-08-24 (weeks/days old;
    # visible in each item's own title_hint). "Recent" here only ever meant
    # recently *processed* -- processed_at is the sole timestamp anywhere in
    # this pipeline (capture_ingest.py never records a real session/meeting
    # date) -- so a recording synced or reprocessed today reads as "Recent"
    # regardless of when it was actually captured. Label what's actually
    # true instead of implying content freshness the data can't back up.
    lines = [f"## Capture Intelligence — {len(items)} Recently Processed\n"]
    type_summary = " · ".join(f"{v} {k}" for k, v in sorted(by_type.items(), key=lambda x: -x[1]))
    lines.append(f"**Processed:** {type_summary} · {total_words:,} words captured\n")
    source_summary = " · ".join(f"{v} {k}" for k, v in sorted(by_source.items(), key=lambda x: (-x[1], x[0])))
    if source_summary:
        lines.append(f"**Inputs:** {source_summary}\n")

    # Zero-stream captures (empty test recordings, silent meetings) add noise
    # without conveying signal — only itemize captures that yielded something.
    # RB-DEFECT-2026-07-08: triage_stream_count was permanently 0 (server.py
    # read a "stream_count" key intelligence_triage never returned), so this
    # filter silently treated every capture as signal-free even when
    # persisted_count showed real intelligence had been extracted and
    # written to IntelligenceDB. Checking persisted_count too means captures
    # processed before the server.py fix (which already have a correct
    # persisted_count) start rendering correctly without needing reprocessing.
    def _has_signal(item: dict) -> bool:
        result = item.get("processing_result") or {}
        return (
            result.get("triage_stream_count", 0) > 0
            or result.get("persisted_count", 0) > 0
            or (result.get("structured_import_applied") or 0) > 0
        )

    signal_items = [item for item in items if _has_signal(item)]
    for item in signal_items[:5]:
        hint = item.get("title_hint", "")
        ctype = item.get("capture_type", "")
        proc_at = (item.get("processed_at") or "")[:10]
        result = item.get("processing_result") or {}
        stream_count = result.get("triage_stream_count", 0)
        persisted_items = result.get("persisted_items") or []
        lines.append(f"- **{hint}** [{ctype}] {proc_at}")

        # Lead with what processing changed in RB, not transcript inventory.
        # Do not overclaim an account/artifact update when the receipt only
        # proves IntelligenceDB persistence.
        persisted_count = int(result.get("persisted_count") or len(persisted_items))
        llm_summary = result.get("llm_summary") or {}
        affected = list(dict.fromkeys(llm_summary.get("companies_mentioned") or []))[:5]
        impact_parts = []
        if persisted_count:
            impact_parts.append(f"persisted {persisted_count} intelligence record(s)")
        if affected:
            impact_parts.append(f"connected the capture to {', '.join(affected)}")
        if result.get("artifact_updates"):
            impact_parts.append(f"updated {len(result.get('artifact_updates') or [])} governed artifact(s)")
        elif persisted_count:
            impact_parts.append("no governed account artifact update was recorded")
        if impact_parts:
            lines.append(f"  - **System impact:** {'; '.join(impact_parts)}.")

        # RB defect 2026-09-30: for a deep-research capture, "persisted N
        # intelligence record(s)" above (generic triage) is not the real
        # mutation outcome -- the structured sidecar importer
        # (import_competitor_platform_research.py) is. Show its
        # reconciled, unambiguous statement explicitly instead of letting
        # exec_mutations=0/persisted_count=0 read as "nothing happened."
        canonical_statement = result.get("canonical_mutation_statement")
        if canonical_statement:
            lines.append(f"  - **Canonical mutation:** {canonical_statement}")

        # RB-DEFECT-2026-07-15: intelligence_triage's classifiers are
        # keyword-trigger detection, not comprehension -- "9 trigger(s):
        # hired, joined, left, network, reached out, reply" tells Todd
        # nothing about who was actually discussed or what came of it.
        # When a real LLM read the transcript (transcript_summarizer.py),
        # show that instead: it's a genuine summary, not a trigger tally.
        llm_summary = result.get("llm_summary")
        if llm_summary:
            people = llm_summary.get("people_mentioned") or []
            companies = llm_summary.get("companies_mentioned") or []
            topics = llm_summary.get("topics") or []
            decisions = llm_summary.get("decisions") or []
            actions = llm_summary.get("action_items") or []
            # INTELLIGENCE_BRIEF_CANONICAL.md: "Why Todd cares"/CoS-style
            # editorial synthesis belongs in the Daily Brief, not here
            # ("This conversation is crucial for understanding..." is RB's
            # own interpretive judgment, not a transcript fact).
            # People/Companies/Topics/Decisions/Action items are what was
            # actually said -- why_it_matters is RB's opinion about it, and
            # correctly stays unrendered in THIS brief. RB-2026-08-28:
            # verified live that "belongs in the Daily Brief" was aspirational,
            # not actual -- render_daily_brief.py has no llm_summary/
            # why_it_matters reference at all; its own _render_captures()
            # only covers PENDING (not-yet-processed) captures, a different
            # concept. This field is computed by transcript_summarizer.py
            # and then genuinely surfaces nowhere -- a real, separately-
            # scoped gap (needs a real design decision on where it goes in
            # the Daily Brief), not something to bolt on here.
            if people:
                lines.append(f"  - **People:** {', '.join(people)}")
            if companies:
                lines.append(f"  - **Companies:** {', '.join(companies)}")
            if topics:
                lines.append(f"  - **Topics:** {', '.join(topics)}")
            if decisions:
                lines.append(f"  - **Decisions:** {'; '.join(decisions)}")
            if actions:
                lines.append(f"  - **Action items:** {'; '.join(actions)}")
        elif persisted_items:
            # Real "why it mattered" detail — what was extracted, not what was said.
            for pi in persisted_items[:5]:
                raw_type = pi.get("intelligence_type") or ""
                itype = _CAPTURE_INTELLIGENCE_TYPE_LABELS.get(raw_type, raw_type.replace("_", " "))
                summary = _clean_capture_summary((pi.get("extracted_summary") or "").strip())
                if summary:
                    lines.append(f"  - **{itype}:** {summary}")
                else:
                    lines.append(f"  - **{itype}**")
        else:
            # Older capture, processed before persisted_items was tracked —
            # fall back to the bare count rather than nothing.
            count = stream_count or result.get("persisted_count", 0)
            lines.append(f"  - {count} intelligence stream{'s' if count != 1 else ''} extracted (detail not available for this capture)")

    # "No intelligence streams" is true per intelligence_triage's narrow
    # definition (it only recognizes specific structured types: executive
    # declarations, relationship signals, strategic signals, etc.) but a real
    # transcript with a direct personal request — "close some loops... send a
    # reminder", "update the to-do list" — doesn't match any of those types
    # and was silently dropped as noise, so the brief said nothing happened
    # even though Todd said something. Show what was actually captured
    # instead of just declaring the absence of a formal intelligence stream.
    no_signal_with_transcript = [
        item for item in items
        if item not in signal_items and (item.get("transcript") or "").strip()
    ]
    if not signal_items and no_signal_with_transcript:
        lines.append("*No structured intelligence streams extracted — here's what was actually said:*")
        for item in no_signal_with_transcript[:5]:
            hint = item.get("title_hint", "")
            ctype = item.get("capture_type", "")
            transcript = item["transcript"].strip()
            snippet = transcript[:180] + ("…" if len(transcript) > 180 else "")
            lines.append(f"- **{hint}** [{ctype}] — \"{snippet}\"")
    elif not signal_items:
        lines.append("*No intelligence streams extracted from recent captures.*")

    # Every item that contributed to this render (the summary counts above,
    # whether or not it was itemized in the top-5 detail) must never be
    # counted or itemized again — this is what actually fixes the
    # double-reporting bug, not just narrowing the lookback window.
    _record_capture_intelligence_reported({item["file_id"] for item in items if item.get("file_id")})

    return "\n".join(lines)


def _load_collection_scan_events(target_date: date, days_back: int = 2) -> dict[str, dict]:
    """Return {date_iso: latest collection_scan_completed event} for target_date
    and the prior `days_back` days. A day can have multiple scan_only/brief_only
    runs — load_events() returns them timestamp-ascending, so the last write
    per date key is that day's most complete (latest) scan."""
    try:
        import audit_log
    except ImportError:
        return {}
    since = (target_date - timedelta(days=days_back)).isoformat() + "T00:00:00"
    until = target_date.isoformat() + "T23:59:59"
    try:
        events = audit_log.load_events(
            event_type="collection_scan_completed", since=since, until=until, limit=200
        )
    except Exception:
        return {}
    by_date: dict[str, dict] = {}
    for e in events:
        d = e.get("date") or (e.get("timestamp") or "")[:10]
        if d:
            by_date[d] = e
    return by_date


def _scan_source_count(scan: dict | None, source_key: str) -> int | None:
    if not scan:
        return None
    for s in scan.get("source_detail") or []:
        if s.get("source_key") == source_key:
            return s.get("records_processed")
    return None


def _scan_source_count_sum(scan: dict | None, source_keys: list[str]) -> int | None:
    if not scan:
        return None
    total = 0
    found = False
    for k in source_keys:
        v = _scan_source_count(scan, k)
        if v is not None:
            total += v
            found = True
    return total if found else None


def _proof_delta_str(today_val: int | None, yesterday_val: int | None) -> str:
    if today_val is None or yesterday_val is None:
        return "—"
    d = today_val - yesterday_val
    return f"{'+' if d >= 0 else ''}{d}"


def _proof_table_row(label: str, today_val, yesterday_val) -> str:
    t_str = str(today_val) if today_val is not None else "—"
    y_str = str(yesterday_val) if yesterday_val is not None else "—"
    return f"{label:<28} {t_str:<8} {y_str:<12} {_proof_delta_str(today_val, yesterday_val)}"


def _proof_news_scan_row(sections: dict, label: str, section_key: str) -> str:
    items = sections.get(section_key) or []
    sources = sorted({
        (i.get("extras") or {}).get("source_name")
        for i in items if (i.get("extras") or {}).get("source_name")
    })
    src_str = ", ".join(sources[:5]) if sources else "none"
    if len(sources) > 5:
        src_str += f" +{len(sources) - 5} more"
    return f"  {label}: {len(items)} articles · {src_str}"


def _render_proof_dashboard(sections: dict, proof: dict | None, target_date: date | None = None) -> str:
    """J: Proof Dashboard — trust anchor, not a lead. Canonical format:
    personal-data Today/Yesterday/Delta table, then a news-scan row, then
    per-source health. Replaces the old "What RB Did Overnight" prose
    (3 flat bullets, no yesterday comparison, no per-source breakdown) —
    counts prove work was done; a plain sentence doesn't let the reader
    verify anything against yesterday.
    """
    today = target_date or date.today()
    yesterday = today - timedelta(days=1)
    scan_events = _load_collection_scan_events(today)
    today_scan = scan_events.get(today.isoformat())
    yesterday_scan = scan_events.get(yesterday.isoformat())

    # Fallback path for counts when no audit scan record exists (e.g. first
    # run, or audit log unavailable) — same source the old renderer used.
    ics = sections.get("intelligence_collection_summary", [])
    extras = (ics[0].get("extras") or {}) if ics else {}
    pi = extras.get("personal_intel_proof") or {}

    def _get_fallback(key: str):
        if proof and proof.get(key) is not None:
            return proof[key]
        if pi.get(key) is not None:
            return pi[key]
        return None

    emails_today = (_scan_source_count_sum(today_scan, ["email:personal", "email:bridgepoint"])
                    or _get_fallback("email_threads_total"))
    emails_yday = _scan_source_count_sum(yesterday_scan, ["email:personal", "email:bridgepoint"])
    sms_today = _scan_source_count(today_scan, "messages") or _get_fallback("sms_events_total")
    sms_yday = _scan_source_count(yesterday_scan, "messages")
    calls_today = _scan_source_count(today_scan, "calls") or _get_fallback("calls_events_total")
    calls_yday = _scan_source_count(yesterday_scan, "calls")
    cal_today = (_scan_source_count_sum(today_scan, ["calendar:personal", "calendar:bridgepoint"])
                 or _get_fallback("calendar_events_total"))
    cal_yday = _scan_source_count_sum(yesterday_scan, ["calendar:personal", "calendar:bridgepoint"])
    mutations_today = (today_scan or {}).get("mutations_generated") or extras.get("mutations_generated")
    mutations_yday = (yesterday_scan or {}).get("mutations_generated")

    contacts_today = None
    try:
        contacts_today = len(json.loads(core.BASELINE_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass

    watchlist_count = len(sections.get("watchlist_intelligence") or [])

    lines = [
        "## J: Proof Dashboard\n",
        "```",
        "Personal Data                 Today    Yesterday    Delta",
        _proof_table_row("Emails (threads)", emails_today, emails_yday),
        _proof_table_row("SMS (conversations)", sms_today, sms_yday),
        _proof_table_row("Calls", calls_today, calls_yday),
        _proof_table_row("Calendar events", cal_today, cal_yday),
        _proof_table_row("Contacts", contacts_today, None),
        _proof_table_row("Mutations declared", mutations_today, mutations_yday),
        f"{'Watchlist entities scanned':<28} {watchlist_count:<8} {'—':<12} —",
        "```",
        "",
        "**News Scan**",
        _proof_news_scan_row(sections, "World", "world_national_headlines"),
        _proof_news_scan_row(sections, "Restaurant", "restaurant_industry_headlines"),
        _proof_news_scan_row(sections, "Tech", "restaurant_technology_headlines"),
        "",
        "**Source Health**",
    ]

    live_source_health = _load_json(core.CACHE_DIR / "source_health.json", {})
    live_health_sources = live_source_health.get("sources") or {}
    live_health_today = str(live_source_health.get("generated_at") or "")[:10] == today.isoformat()
    if live_health_today and live_health_sources:
        healthy_statuses = {"refreshed", "ok", "fresh"}
        healthy = [row for row in live_health_sources.values()
                   if isinstance(row, dict) and row.get("status") in healthy_statuses]
        unhealthy = [(name, row) for name, row in live_health_sources.items()
                     if isinstance(row, dict) and row.get("status") not in healthy_statuses]
        lines.append(f"  ✓ Healthy: {len(healthy)} sources")
        for name, row in unhealthy:
            lines.append(f"  ⚠ {name} — {str(row.get('status') or 'unknown').replace('_', ' ').title()}")
        attempted = len(healthy) + len(unhealthy)
        if attempted:
            lines.append(f"  Freshness: {round(len(healthy) / attempted * 100)}%")
    elif today_scan:
        detail = today_scan.get("source_detail") or []
        healthy = [s for s in detail if s.get("status") == "Healthy"]
        unhealthy = [s for s in detail if s.get("status") != "Healthy"]
        lines.append(f"  ✓ Healthy: {len(healthy)} sources")
        for s in unhealthy:
            lines.append(f"  ⚠ {s.get('source_key')} — {s.get('status')} (trust {s.get('trust_score')}%)")
        # INTELLIGENCE_BRIEF_CANONICAL.md's Proof Dashboard format shows
        # "Freshness: N% | Trust: N%" -- only Trust was ever rendered.
        # Freshness (are sources current, distinct from Trust's broader
        # reliability weighting) is derivable from the same audit event:
        # sources_attempted minus whatever's stale or unavailable this cycle.
        attempted = today_scan.get("sources_attempted")
        stale_count = len(today_scan.get("stale_sources") or [])
        unavailable_count = today_scan.get("sources_unavailable") or 0
        freshness_pct = None
        if attempted:
            fresh = max(attempted - stale_count - unavailable_count, 0)
            freshness_pct = round(fresh / attempted * 100)
        trust = today_scan.get("trust_score")
        if freshness_pct is not None and trust is not None:
            lines.append(f"  Freshness: {freshness_pct}% | Trust: {trust}%")
        elif trust is not None:
            lines.append(f"  Trust: {trust}%")
    else:
        # RB-DEFECT-2026-07-08: the morning pipeline writes the
        # collection_scan_completed audit event and generates this brief in
        # close sequence — confirmed live, the brief file was written 5
        # seconds BEFORE its own collection scan's completion event landed
        # in the audit log. That race made Source Health falsely claim it
        # "cannot verify source freshness" even on a run that successfully
        # scanned 190+ items and populated every headline section with
        # today-dated articles. Fall back to the headline sections
        # themselves (populated in this same render from the same scan) as
        # direct evidence a scan ran, rather than only trusting a
        # separately-logged event that may not have landed yet.
        today_str = today.isoformat()
        scanned_today = any(
            (hl.get("extras") or {}).get("pub_date") == today_str
            for section_key in ("world_national_headlines", "restaurant_industry_headlines",
                                 "restaurant_technology_headlines")
            for hl in (sections.get(section_key) or [])
        )
        if scanned_today:
            lines.append(
                "  ✓ Scan evidence: fresh today-dated articles present in this brief "
                "(collection_scan_completed audit record not yet written when this brief "
                "was generated — timing, not a scan failure)."
            )
        else:
            lines.append("  ⚠ No collection scan record found for today — cannot verify source freshness.")

    return "\n".join(lines)


def _render_intelligence_cycle_report(sections: dict, target_date: date | None = None) -> str:
    """Reader-facing intelligence health and learnings appendix.

    Internal pipeline counters are intentionally omitted unless they indicate
    a material exception. Normal zeros belong in observability, not a CoS
    report. The reader gets coverage, consequence, and required action.
    """
    collection = sections.get("intelligence_collection_summary") or []
    extras = (collection[0].get("extras") or {}) if collection else {}
    stats = extras.get("intelligence_cycle_statistics") or {}
    daily = stats.get("daily_monitoring") or {}
    routine = stats.get("routine_research") or {}

    sources = int(daily.get("sources_scanned") or 0)
    fetched = int(daily.get("items_fetched") or 0)
    cached = int(daily.get("items_from_cache") or 0)
    evaluated = fetched + cached
    status = str(daily.get("watchdog_status") or "unknown")
    critical = int(daily.get("watchdog_critical_alerts") or 0)
    warnings = int(daily.get("watchdog_warnings") or 0)
    failures = int(daily.get("downstream_failures") or 0)
    lines = ["## Intelligence Health & Learning", ""]
    coverage = f"{sources} source channels"
    if evaluated:
        coverage += f" and {evaluated:,} gathered or reused items"
    lines.append(f"**Coverage:** RB evaluated {coverage}.")
    if status not in {"healthy", "ok"} or critical or warnings or failures:
        lines.append(
            f"**Health:** {status.capitalize()}"
            + (f" — {critical} critical alert(s), {warnings} warning(s)" if critical or warnings else "")
            + (f", {failures} downstream failure(s)" if failures else "")
            + ". See Source Health below for the specific exception and recovery status."
        )
    else:
        lines.append("**Health:** Healthy; no material collection or downstream exception affected this brief.")

    gaps = int(daily.get("newsworthy_baseline_gaps") or 0)
    urgent = int(daily.get("urgent_research_requests") or 0)
    if gaps or urgent:
        lines.append(
            f"**Research triggered by today's news:** {gaps} baseline gap(s); "
            f"{urgent} urgent research request(s) queued."
        )

    entity_names = [str(name) for name in (routine.get("entities") or []) if name]
    researched = int(routine.get("accounts_or_entities_researched") or 0)
    evidence = int(routine.get("evidence_items_added") or 0)
    if researched or evidence:
        lines.append(
            f"**Baseline construction:** Researched {researched} entit{'y' if researched == 1 else 'ies'}"
            + (f" ({', '.join(entity_names[:5])})" if entity_names else "")
            + f" and added {evidence} evidence item(s)."
        )
    blind_spots = [str(name) for name in (daily.get("priority_source_blind_spots") or []) if name]
    # Health can legitimately improve after the canonical brief payload was
    # assembled (for example, a LinkedIn export ingested minutes later).
    # Reconcile cached warning names against the current health receipt so
    # resolved or non-priority internal artifacts do not remain false alarms.
    live_health = _load_json(core.CACHE_DIR / "source_health.json", {})
    live_sources = live_health.get("sources") or {}
    blind_spots = [
        name for name in blind_spots
        if not isinstance(live_sources.get(name), dict)
        or (
            live_sources[name].get("status") not in {"refreshed", "ok", "fresh"}
            and int(live_sources[name].get("tier") or 99) <= 3
        )
    ]
    if blind_spots:
        lines.append("**Sources requiring recovery:** " + ", ".join(blind_spots[:8]) + ".")

    ranked = intelligence_insight_ranking.rank_insights(
        sections, today=target_date or date.today(), limit=5)

    lines.extend(["", "**Most important things RB learned**"])
    if not ranked:
        lines.append("No evidence-backed net-new learning was produced in this cycle.")
    else:
        for rank, result in enumerate(ranked, start=1):
            item = result["item"]
            title = str(item.get("title") or "").strip()
            reader_title = re.sub(r"^\[[^\]]+\]\s*", "", title).strip()
            summary = str(item.get("summary") or "").strip()
            item_extras = item.get("extras") or {}
            url = str(item_extras.get("source_url") or "").strip()
            refs = [str(ref) for ref in (item.get("source_refs") or []) if ref]
            detail = f" — {summary}" if summary and summary != title else ""
            source_text = f" *(Sources: {', '.join(refs[:3])})*" if refs else ""
            linked_title = f"[{reader_title}]({url})" if url else reader_title
            why = str(item.get("why_it_matters") or "").strip()
            if summary and why.startswith(summary):
                why = why[len(summary):].lstrip(" .—↳")
            if not why:
                why = str(item.get("recommended_action") or "").strip()
            lines.append(f"{rank}. **{linked_title}**{detail}{source_text}")
            if why:
                lines.append(f"   **Why it matters:** {why}")
    return "\n".join(lines)


def _render_deep_research_baseline_progress(target_date: date) -> str:
    """Render completion-first deep-research coverage and prior-day receipts.

    `deep_research_coverage.json` is the authoritative prioritized ledger: a
    target counts only after a completed packet has been recorded.  The report
    deliberately separates competitors from restaurant brands because the
    dispatcher exhausts competitor gaps before moving to brand coverage.
    """
    state = _load_json(DEEP_RESEARCH_COVERAGE_PATH, {})
    targets = state.get("targets") or {}
    if not isinstance(targets, dict):
        targets = {}
    active = [row for row in targets.values() if isinstance(row, dict) and row.get("active", True)]

    def _covered(row: dict) -> bool:
        return int(row.get("coverage_count") or 0) > 0

    companies = [row for row in active if row.get("kind") == "company"]
    competitors = [row for row in active if row.get("kind") == "competitor"]
    covered_companies = [row for row in companies if _covered(row)]
    covered_competitors = [row for row in competitors if _covered(row)]
    total = len(companies) + len(competitors)
    covered_total = len(covered_companies) + len(covered_competitors)
    overall_pct = (100.0 * covered_total / total) if total else 0.0
    company_pct = (100.0 * len(covered_companies) / len(companies)) if companies else 0.0
    competitor_pct = (100.0 * len(covered_competitors) / len(competitors)) if competitors else 0.0
    phase = "maintenance" if total and covered_total == total else "initial coverage"

    prior_day = target_date - timedelta(days=1)
    researched = []
    for row in active:
        stamp = str(row.get("last_covered_at") or "")
        if stamp[:10] == prior_day.isoformat() and row.get("name"):
            researched.append(str(row["name"]))
    researched = sorted(dict.fromkeys(researched), key=str.casefold)

    failures: list[str] = []
    if ADAPTIVE_RESEARCH_RECEIPTS_PATH.exists():
        for raw in ADAPTIVE_RESEARCH_RECEIPTS_PATH.read_text(encoding="utf-8").splitlines():
            try:
                receipt = json.loads(raw)
            except (TypeError, ValueError):
                continue
            stamp = str(
                receipt.get("ended_at") or receipt.get("completed_at")
                or receipt.get("started_at") or receipt.get("checked_at") or ""
            )
            if stamp[:10] != prior_day.isoformat():
                continue
            status = str(receipt.get("status") or "").lower()
            if not any(token in status for token in ("fail", "error", "block")):
                continue
            label = receipt.get("batch_id") or receipt.get("entity_name") or "research batch"
            reason = receipt.get("failure") or receipt.get("error") or receipt.get("blocker") or status
            failures.append(f"{label}: {str(reason).strip()[:240]}")

    lines = [
        "## Deep Research Baseline",
        "",
        f"**Ecosystem completion:** {covered_total:,} of {total:,} targets ({overall_pct:.1f}%) — {phase}.",
        f"**Priority progress:** competitors {len(covered_competitors):,}/{len(competitors):,} "
        f"({competitor_pct:.1f}%); restaurant brands {len(covered_companies):,}/{len(companies):,} "
        f"({company_pct:.1f}%).",
        "*Only targets with a completed, recorded evidence packet count toward coverage.*",
        "",
        f"**Researched {prior_day.strftime('%B %-d')}:** "
        + (", ".join(researched) if researched else "None recorded."),
        "**Research failures:** " + ("None recorded." if not failures else ""),
    ]
    if failures:
        lines.extend(f"- {failure}" for failure in failures)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# GP/Genius Field Intelligence
# ---------------------------------------------------------------------------

# Companies and products directly relevant to Todd's role at Global Payments:
# cross-selling Genius products into enterprise Worldpay customers.
# RB-2026-09-01: moved to rb_core.GP_OWN_TERMS (now a third module needs
# the identical list) -- kept as a local alias so every existing use site
# in this file didn't need touching.
_GP_OWN_TERMS = core.GP_OWN_TERMS

# Enterprise POS/payments competitors whose moves signal customer opportunity or threat
# RB 2026-08-26: bare "revel" false-positived on an unrelated mining-company
# press release ("Riverside Resources... the Revel" — a mining claim name).
# Revel Systems (the POS competitor) is reliably referred to by its full
# brand name in coverage, so match on that instead of the generic word.
_GP_COMPETITIVE_TERMS = [
    "toast", "par technology", "par tech", "brink pos", "shift4", "lightspeed",
    "ncr voyix", "ncr", "aloha", "oracle hospitality", "micros", "agilysys",
    "revel systems", "revel pos", "square", "clover", "stripe", "adyen",
    "fiserv", "heartland payment",
]

# Enterprise restaurant signals that indicate a customer may be in market
_GP_MARKET_TERMS = [
    "enterprise restaurant", "enterprise pos", "payment platform", "payment processing",
    "pos replacement", "pos migration", "tech stack", "unified commerce",
    "payment rails", "acquiring", "interchange", "payment modernization",
    "qsr technology", "quick service technology", "full service technology",
]

_GP_ALL_TERMS = _GP_OWN_TERMS + _GP_COMPETITIVE_TERMS + _GP_MARKET_TERMS


# "Square" (Block's payments product) is also a common place-name word — strip
# known "___ Square" place names before matching so they don't false-positive
# the competitive-term scan (same failure mode as "toast"/"ncr" substring hits,
# just at the whole-word level instead of inside another word).
_GP_PLACE_NAME_FALSE_POSITIVES = [
    "logan square", "union square", "times square", "madison square",
    "washington square", "town square", "public square", "market square",
    "red square", "trafalgar square", "tiananmen square", "herald square",
]

# Food uses of competitor brand names are not competitive intelligence.
# Confirmed live: "Why IHOP brought back Stuffed French Toast" scored as a
# Toast competitive hit and entered GP/Genius Field Intelligence.
_GP_FOOD_FALSE_POSITIVES = [
    "french toast", "avocado toast", "cinnamon toast", "toast points",
    "toast recipe", "toasted bread",
]


def _strip_gp_false_positive_phrases(text: str) -> str:
    for phrase in _GP_PLACE_NAME_FALSE_POSITIVES + _GP_FOOD_FALSE_POSITIVES:
        text = text.replace(phrase, "")
    return text


def _term_in_text(term: str, text: str) -> bool:
    """Word-boundary match — plain `in` lets short competitor names like "ncr",
    "square", or "toast" match inside unrelated words ("Increase", "Logan
    Square", "Toaster"), producing false-positive GP/Genius relevance hits."""
    import re as _re3
    return _re3.search(r"\b" + _re3.escape(term) + r"\b", text) is not None


def _gp_score(text: str) -> int:
    """Return relevance score for GP/Genius mission. Higher = more relevant."""
    tl = _strip_gp_false_positive_phrases(text.lower())
    score = 0
    for t in _GP_OWN_TERMS:
        if _term_in_text(t, tl):
            score += 3  # direct company/product mentions weight highest
    for t in _GP_COMPETITIVE_TERMS:
        if _term_in_text(t, tl):
            score += 2
    for t in _GP_MARKET_TERMS:
        if _term_in_text(t, tl):
            score += 1
    return score


def _render_gp_intel(sections: dict, today: date,
                     f_rendered_urls: set[str] | None = None,
                     prior_state: dict | None = None) -> str:
    """K: GP/Genius — Field Intelligence.

    Facts only: signals about Global Payments, Genius, Worldpay, and the
    enterprise POS/payments competitive landscape. Daily Brief renders the
    'so what' interpretation of these same signals.

    f_rendered_urls: URLs already surfaced in F: Watchlist — skip to avoid cross-section duplication.
    prior_state: 7-day prior brief state — skip URLs already shown in K in the last 7 days.
    """
    import re as _re
    # RB-DEFECT-2026-08-14: confirmed live -- "Toast aims to drive AI into
    # dining" rendered twice in K on 2026-08-13, once found via the headline-
    # pool scan (direct restaurantdive.com URL) and once via the newsletter-
    # article scan (a link.restaurantdive.com/click/... tracking redirect for
    # the same story). The RB-DEFECT-2026-08-12 fix below only stops K from
    # re-showing a URL already rendered in a DIFFERENT section (D+/F) -- it
    # never deduped K's own two internal loops against each other, and the
    # final "seen" dedup keyed on line[:80], which starts with the [title]
    # markdown but still includes the differing URL right after it, so two
    # renderings of the identical story with different URLs never matched.
    # Carry the bare title alongside score+line so the final dedup can key
    # on title text, not on a URL-containing line prefix.
    signals: list[tuple[int, str, str]] = []  # (score, formatted_line, title_for_dedup)
    _f_seen = f_rendered_urls or set()
    _k_prior_urls: set = (prior_state or {}).get("k_section_urls", set())
    # RB-DEFECT-2026-08-12: D+ Newsletter Inbox's own link for an article is
    # usually a wrapped tracking redirect (link.restaurantdive.com/click/...),
    # while K's headline-pool scan finds the same story via its direct
    # publisher URL -- different strings, same story ("Toast aims to drive
    # AI into dining" rendered in both D+ and K on 2026-08-12). D+ already
    # resolves wrapped links before comparing against _rendered_this_run;
    # K's three dedup checks below never did. Resolve once, reuse everywhere.
    _rendered_resolved: set = {_resolve_wrapped_url(u) for u in _rendered_this_run}

    _GP_JUNK_URLS = ["mail.google.com", "unsubscribe"]

    # Scan all headline pools for GP-relevant items
    # Score on TITLE only — body text produces too many false positives
    pools = [
        sections.get("world_national_headlines", []),
        sections.get("restaurant_industry_headlines", []),
        sections.get("restaurant_technology_headlines", []),
        sections.get("what_todd_doesnt_know_yet", []),
    ]
    for pool in pools:
        for item in pool:
            title = (item.get("title") or "").strip()
            why = (item.get("why_it_matters") or item.get("summary") or "").strip()
            extras = item.get("extras") or {}
            url = (extras.get("source_url") or "").strip()
            source = extras.get("source_name", "").strip()
            pub_date = extras.get("pub_date", "").strip()
            # Score only on title + source to avoid body-text false positives
            score = _gp_score(title + " " + source)
            if score < 2 or not url:
                continue
            if any(j in url for j in _GP_JUNK_URLS):
                continue
            # Skip URLs already shown in K in the last 7 days
            if url in _k_prior_urls:
                continue
            # Skip RTN vendor press releases from competitors (same filter as section D)
            _rtn_source = source.lower() in ("restaurant technology news", "restauranttechnologynews.com")
            _rtn_pr = _re.search(
                r"^.{2,45}\b(advances|gives|strengthens|helps|powers|enables|delivers|transforms|simplifies|positions|equips|expands|brings|offers|provides)\b.{0,60}\brestaurants?\b",
                title, _re.IGNORECASE)
            if _rtn_source and _rtn_pr:
                is_gp = any(k in title.lower() for k in ("global payments", "genius", "worldpay"))
                if not is_gp:
                    continue  # competitor vendor PR — skip
            # Skip URLs already rendered in D or F
            if url in _rendered_this_run or _resolve_wrapped_url(url) in _rendered_resolved:
                continue
            # Strip badge prefix for display
            bare = _re.sub(r"^\[[^\]]+\]\s*", "", title).strip()
            meta = " | ".join(filter(None, [source, pub_date]))
            line = f"[{bare}]({url})"
            if meta:
                line += f"\n{meta}"
            if why and why.lower() not in title.lower():
                why_trunc = (why[:197] + "…") if len(why) > 200 else why
                line += f"\n*{why_trunc}*"
            signals.append((score, line, bare))

    # Scan newsletter articles for GP-relevant items
    for nl_item in sections.get("newsletter_intelligence", []):
        extras = nl_item.get("extras") or {}
        source = extras.get("source_name", "")
        articles = extras.get("articles") or []
        for art in articles:
            a_title = (art.get("title") or "").strip()
            a_url = (art.get("url") or "").strip()
            if not a_title or not a_url:
                continue
            if any(j in a_url for j in _GP_JUNK_URLS):
                continue
            if (a_url in _k_prior_urls or a_url in _rendered_this_run
                    or _resolve_wrapped_url(a_url) in _rendered_resolved):
                continue  # shown in K in last 7 days, or already rendered this run
            # RB-2026-08-30: URL-only dedup above only ever caught K
            # re-showing a story an *earlier* section had already rendered
            # (and therefore marked) under the exact same or a decodable
            # URL. It never caught the same real-world event covered by a
            # completely different outlet/URL -- e.g. Section B covers a
            # PayPal/Stripe/Advent deal collapse via Yahoo Finance's own
            # article, and this newsletter pool carries a Payments Dive
            # forward of Bloomberg's write-up of the identical event. Reuse
            # the same outlet-independent story-identity check D+'s
            # newsletter path uses (see the matching comment there).
            story_key = core.resolve_story_identity(a_title)
            is_dup_story = False
            if story_key is not None:
                found = _story_ledger.lookup(story_key)
                if found is not None and found[0] in _rendered_story_ids_this_run:
                    is_dup_story = True
            if not is_dup_story:
                for candidate_id, candidate_story in core.find_tiebreak_candidates(
                        a_title, _story_ledger, today):
                    if candidate_id not in _rendered_story_ids_this_run:
                        continue
                    if llm_assist.same_story_tiebreak(
                            a_title, candidate_story.get("title_sample") or "") is True:
                        is_dup_story = True
                        break
            if is_dup_story:
                continue
            score = _gp_score(a_title + " " + source)
            if score < 2:
                continue
            line = f"[{a_title}]({a_url})"
            if source:
                line += f"\n{source}"
            signals.append((score, line, a_title))

    # Pull price watch signals for competitive watchlist companies.
    # Skip URLs already rendered in F: Watchlist to prevent cross-section duplication.
    try:
        pw_path = core.SYSTEM_DIR / "inbox" / "market_signals_earnings.jsonl"
        today_str = today.isoformat()
        if pw_path.exists():
            for raw in pw_path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                sig = json.loads(raw)
                if str(sig.get("published_at") or "")[:10] != today_str:
                    continue
                url = sig.get("url") or ""
                # Skip if this URL (or its ticker's Yahoo URL) was already in F
                if url and (url in _f_seen or url in _rendered_this_run
                            or _resolve_wrapped_url(url) in _rendered_resolved):
                    continue
                company = (sig.get("company") or "").lower()
                ticker = (sig.get("ticker") or "").lower()
                score = _gp_score(company + " " + ticker)
                if score < 2:
                    continue
                title = (sig.get("title") or "").strip()
                detail = (sig.get("pain_point_or_priority") or "").strip()
                line = f"[{title}]({url})" if url else f"**{title}**"
                if detail:
                    detail_trunc = (detail[:197] + "…") if len(detail) > 200 else detail
                    line += f"\n*{detail_trunc}*"
                signals.append((score, line, title))
    except Exception:
        pass

    # Deduplicate and sort by score. Key on normalized title text, not the
    # rendered line -- see the RB-DEFECT-2026-08-14 note above signals'
    # declaration for why line[:80] missed the same story under two URLs.
    seen: set[str] = set()
    unique: list[tuple[int, str]] = []
    for score, line, dedup_title in sorted(signals, key=lambda x: -x[0]):
        key = _re.sub(r"[^a-z0-9]+", " ", dedup_title.lower()).strip()
        if key and key not in seen:
            seen.add(key)
            unique.append((score, line))

    if not unique:
        return ""

    lines = ["## K: GP/Genius — Field Intelligence\n"]
    for _, line in unique[:8]:
        lines.append(line)
        lines.append("")
    return "\n".join(lines)

_MD_LINK_RE = re.compile(r'\[([^\]]+)\]\((https?://[^\s)]+)\)')

# RB-DEFECT-2026-07-10g: some newsletter "partner content"/gated-whitepaper
# links (observed live: Restaurant Dive's tradepub.com syndication links)
# embed the subscriber's own PII directly in a base64-encoded path segment
# of the tracking URL -- e.g. decoding one revealed
# "...&email=vahlsingt@gmail.com&first=Todd&last=Vahlsing&company=..."
# in plain sight, no click required. Fine for the personal brief (it's
# Todd's own subscription), but this must never appear in a document
# meant for the wider team's eyes.
_TEAM_EDITION_PII_NEEDLES = ["vahlsingt@gmail.com", "vahlsing", "bridgepointops"]


def _decode_wrapped_url_segments(url: str) -> list[str]:
    """Newsletter click-tracking redirects commonly carry the real
    destination URL as a base64-encoded path segment (e.g. TradePub/
    Restaurant Dive links). Return every segment that decodes to something
    URL-shaped, lowercased, for either PII scanning or same-article dedup."""
    decoded_segments = []
    for seg in re.split(r"[/?&=]", url):
        if len(seg) >= 24 and re.fullmatch(r"[A-Za-z0-9_\-]+", seg):
            padded = seg + "=" * (-len(seg) % 4)
            try:
                decoded = base64.urlsafe_b64decode(padded).decode("utf-8", errors="ignore")
            except Exception:
                continue
            if decoded:
                decoded_segments.append(decoded)
    return decoded_segments


def _url_leaks_pii(url: str) -> bool:
    haystacks = [url.lower()] + [d.lower() for d in _decode_wrapped_url_segments(url)]
    combined = " ".join(haystacks)
    return any(needle in combined for needle in _TEAM_EDITION_PII_NEEDLES)


def _resolve_wrapped_url(url: str) -> str:
    """Normalize a URL for cross-section same-article dedup: if it's a
    click-tracking redirect wrapping a real destination URL (base64 path
    segment), return that destination instead -- so a newsletter's own
    tracking link for an article isn't treated as a different story just
    because Section D linked the publisher's direct URL for the same
    article. Falls back to the (query-stripped) input URL unchanged."""
    for decoded in _decode_wrapped_url_segments(url):
        if decoded.startswith("http"):
            return decoded.split("?")[0].rstrip("/").lower()
    return url.split("?")[0].rstrip("/").lower()


def _clean_url_for_display(url: str) -> str:
    """RB-QUALITY-2026-09-04: the team edition's D+/F/I sections were
    embedding raw newsletter click-tracking URLs verbatim -- e.g. a ~500-
    char informamail05.com redirect carrying utm_rid=CPG06000082977594 and
    similar opaque subscriber/tracking tokens. _url_leaks_pii only catches
    LITERAL PII substrings (Todd's actual name/email) and correctly missed
    these -- an opaque tracking token isn't PII by that check, but it can
    still tie a click back to Todd's own newsletter subscription once this
    goes to a wider audience, which is exactly the risk this document's own
    PII defense-in-depth exists to prevent. _resolve_wrapped_url already
    decodes these same redirects and strips tracking query strings, but
    only for internal dedup comparison -- it lowercases and strips the
    trailing slash, which is fine for a comparison key but risks breaking
    a real, clickable link. Same decode logic, display-safe: preserve case
    and path exactly, only drop the tracking query string."""
    for decoded in _decode_wrapped_url_segments(url):
        if decoded.startswith("http"):
            return decoded.split("?")[0]
    return url.split("?")[0]


def _strip_pii_links(markdown: str) -> str:
    """Defense-in-depth pass over the assembled team-edition markdown: any
    markdown link whose URL (raw or base64-decoded) contains Todd's own PII
    loses its hyperlink (the visible label is kept) rather than being sent
    out with a link that leaks his email/name/employer."""
    def _repl(m: "re.Match[str]") -> str:
        text, url = m.group(1), m.group(2)
        return text if _url_leaks_pii(url) else m.group(0)
    return _MD_LINK_RE.sub(_repl, markdown)


# This is Todd's own industry-news reading, not a work-branded document --
# he may or may not choose to forward it to his team. "Genius"/"Global
# Payments" (his employer's product/company names, and his own competitive-
# intelligence tracking of them) don't belong in something scoped as a
# general industry brief.
_BANNED_TERMS_RE = re.compile(r"\b(global\s+payments|genius)\b", re.IGNORECASE)


def _mentions_banned_terms(*texts: str) -> bool:
    return any(_BANNED_TERMS_RE.search(t or "") for t in texts)


def _filter_banned_terms(items: list[dict]) -> list[dict]:
    return [
        i for i in items
        if not _mentions_banned_terms(
            i.get("title") or "", i.get("summary") or "", i.get("why_it_matters") or "")
    ]


def _filter_newsletter_banned_terms(items: list[dict]) -> list[dict]:
    out = []
    for item in items:
        item = dict(item)
        extras = dict(item.get("extras") or {})
        articles = extras.get("articles") or []
        extras["articles"] = [a for a in articles if not _mentions_banned_terms(a.get("title") or "")]
        if _mentions_banned_terms(extras.get("body_summary") or ""):
            extras["body_summary"] = ""
        item["extras"] = extras
        out.append(item)
    return out


def _last_team_brief_date(before: date) -> "date | None":
    """Most recent existing team-intelligence-brief.md strictly before `before`.

    Filesystem-derived rather than a separate state file: each edition is
    already written to BRIEFS_DIR as f"{date}-team-intelligence-brief.md",
    so that naming convention IS the publish ledger.

    RB-DEFECT-2026-08-11: before this pipeline was Tue/Fri-only, it rendered
    a team-intelligence-brief.md every single day, so BRIEFS_DIR is full of
    leftover files dated on ordinary weekdays from that era. The very first
    Tuesday run under the new cadence picked up the prior day's (Monday)
    leftover file as "last published," collapsing window_start to today and
    silently emptying the Restaurant Industry/Technology sections. Only a
    file whose OWN date falls on a publish weekday (Tuesday/Friday) can be a
    genuine prior edition -- anything else is old-regime noise.
    """
    candidates: list[date] = []
    for p in BRIEFS_DIR.glob("*-team-intelligence-brief.md"):
        try:
            d = date.fromisoformat(p.name[:10])
        except ValueError:
            continue
        if d < before and d.weekday() in (1, 4):  # Tuesday, Friday
            candidates.append(d)
    return max(candidates) if candidates else None


_GP_CATEGORY_PSEUDO_ENTITIES = {
    "ai (restaurant & horizontal)", "back office", "digital ordering & guest experience",
    "enterprise operators", "industry analysts & operators", "investors & board-level",
    "loyalty & crm", "pos", "payments", "restaurant ceos & founders",
    "restaurant chains & groups", "restaurant technology (other)", "restaurant technology ceos",
    "technology analysts & influencers",
}

# RB-DEFECT-2026-08-11 (reader-relevance pass): the team edition's readers
# are Global Payments/Genius sales professionals, not Todd -- a story only
# belongs in A/B if it (a) names a company on the restaurant-tech watchlist
# (a tracked competitor, customer, or prospect), or (b) is substantively
# about a payments/POS/loyalty/restaurant-tech topic even without a named
# tracked company. Keyword stems drawn from restaurant_tech_watchlist.md's
# own category list (POS, Payments, Loyalty/CRM, digital ordering, drive-thru
# tech, kitchen ops tech, restaurant AI, enterprise integration).
_GP_RELEVANT_THEME_RE = re.compile(
    r"\b("
    r"pos|point.of.sale|payments?|pay.at.table|checkouts?|acquiring|processors?|"
    r"stored.value|fraud|kiosks?|loyalty|CRM|guest.data|drive.?thru|"
    r"kitchen.display|KDS|online.order(ing|s)?|digital.order(ing|s)?|"
    r"first.party.order(ing|s)?|delivery.(platform|enablement|orchestration)s?|"
    r"menu.boards?|voice.AI|AI|artificial.intelligence|labor.(management|scheduling|"
    r"forecasting|optimization)|inventory.(management|software)|procurement|"
    r"food.costs?|marketing.automation|CDP|reservations?|waitlists?|"
    r"table.management|computer.vision|self.(order(ing|s)?|service)|"
    r"franchise.technolog(y|ies)|multi.unit.(system|technology)s?|"
    r"integration.layers?|middleware|API"
    r")\b",
    re.IGNORECASE,
)

# Team-edition classification must be narrower than the broad relevance gate.
# A story may be relevant to a restaurant-tech seller without being a
# restaurant-technology story.  This keeps promotions/traffic/operator news in
# A while moving actual products, platforms, data, AI and payments into B.
_TEAM_TECH_CLASS_RE = re.compile(
    r"\b(ai|artificial intelligence|operating layer|platform|software|pos|"
    r"point.of.sale|payments?|loyalty|rewards?|guest data|data (stack|platform|"
    r"foundation)|digital ordering|online ordering|kiosk|drive.?thru tech|kds|"
    r"kitchen display|automation|computer vision|integration|api)\b",
    re.IGNORECASE,
)

_TEAM_LEADERSHIP_RE = re.compile(
    r"\b(ceo|chief executive|president|chief .* officer|resigns?|steps? down|"
    r"appointed|appoints?|hires?|departure|exit)\b", re.IGNORECASE
)


def _team_item_is_technology(item: dict) -> bool:
    text = f"{item.get('title') or ''} {item.get('summary') or ''} {item.get('why_it_matters') or ''}"
    return bool(_TEAM_TECH_CLASS_RE.search(text))


def _team_why_it_matters_fallback(title: str, summary: str = "") -> str:
    """Deterministic bucket fallback -- only used when LLM synthesis is
    unavailable (no API key, call failed) or hasn't covered this item yet.
    Keep this concrete and short; it never exposes source-health, API,
    scoring or renderer language."""
    text = f"{title} {summary}".lower()
    if _TEAM_LEADERSHIP_RE.search(text):
        return "Leadership change can reset strategy, budgets and vendor relationships; watch for a new decision-maker and reopened priorities."
    if re.search(r"\b(funding|raises? \$|capital|acqui(?:res|sition)|sold|sale)\b", text):
        return "New ownership or capital can accelerate product investment and create a fresh vendor-selection window."
    if re.search(r"\b(loyalty|rewards?|guest data|first.party|frequency|visits?)\b", text):
        return "This is a practical signal on how operators are using first-party data and loyalty to increase frequency, not just discount transactions."
    if re.search(r"\b(western union|digital shift|embedded payments?|acquiring|processor|fraud|credit|debit|cash)\b", text):
        return "The shift changes customer expectations and competitive economics in payments; it is useful context for product and sales conversations."
    if re.search(r"\b(ai|artificial intelligence|data stack|data foundation|operating layer|automation)\b", text):
        return "It shows where restaurant technology budgets are moving and what operators will expect vendors to integrate, prove and support."
    if re.search(r"\b(pos|point.of.sale|platform|software|kiosk|digital ordering|online ordering|kds|integration|api)\b", text):
        return "It may change the restaurant technology stack, integration requirements or competitive position around POS and payments."
    if re.search(r"\b(traffic|same.store sales|promotion|bogo|value|margin|cost|marketing)\b", text):
        return "It is a read on operator traffic and margin pressure—and on the commercial tactics brands are using to respond."
    return "Read this only if the company or issue is relevant to an active customer, prospect or competitive decision."


# RB-DEFECT-2026-08-21: the team edition's "why it matters" line used to be
# a pure keyword-bucket lookup (see _team_why_it_matters_fallback above) --
# confirmed live, four unrelated leadership stories (Jack in the Box, Pizza
# Hut, Qu, Mike's Red Tacos) rendered the identical generic sentence in one
# issue, and the "Read this only if..." non-answer fallback shipped in A/B
# even though D+ already knew to drop it. Route every story through the same
# hardened LLM synthesis used for Todd's personal Dot Connections
# (brief_synthesis.py) first, grounded in that story's actual headline/
# summary text, and only fall back to the bucket sentence if synthesis is
# unavailable or hasn't covered this item.
_TEAM_READER_ROLE_SUMMARY = (
    "The reader is a Global Payments/Genius restaurant-technology sales "
    "professional receiving a curated, twice-weekly industry newsletter "
    "from a colleague. They are not Todd himself -- they may not know his "
    "specific accounts or pipeline. Explain, in one concrete sentence "
    "grounded only in the evidence given, why this specific story (not "
    "stories like it in general) is worth a restaurant-tech/payments "
    "seller's attention: what changed, and what it plausibly means for "
    "competitive positioning, a customer/prospect conversation, or the "
    "vendor landscape. Never write a sentence generic enough to apply to "
    "any other leadership change, funding round, or loyalty story -- name "
    "the specific company, product or dynamic from the evidence."
)

_team_synth_why: dict[str, str] = {}  # normalized-title -> synthesized sentence, reset per render


def _team_synth_key(title: str) -> str:
    return re.sub(r"\W+", " ", (title or "").lower()).strip()


def _team_synthesize_batch(items: list[dict]) -> None:
    """Best-effort batched LLM synthesis for a set of team-edition stories.

    items: [{"title": <headline>, "evidence": <headline + summary text>}, ...]
    Populates the persistent cache (TEAM_SYNTH_CACHE_PATH) and the per-run
    _team_synth_why dict. Never raises -- on any failure the caller silently
    keeps using _team_why_it_matters_fallback.
    """
    global _team_synth_why
    cache = _load_json(TEAM_SYNTH_CACHE_PATH, {})

    keyed: dict[str, dict] = {}
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        key = _team_synth_key(title)
        if key not in keyed:
            keyed[key] = {"key": key, "title": title, "evidence": it.get("evidence") or title}

    for key, entry in keyed.items():
        if key in cache:
            _team_synth_why[key] = cache[key]

    to_synthesize = [v for k, v in keyed.items() if k not in cache]
    if not to_synthesize:
        return

    try:
        import brief_synthesis as _bs
        result = _bs.synthesize_signals(to_synthesize, _TEAM_READER_ROLE_SUMMARY) or {}
    except Exception:
        return

    changed = False
    for key, fields in result.items():
        why = (fields.get("why") or "").strip()
        if not why or _mentions_banned_terms(why):
            continue
        # RB-2026-08-25: confirmed live -- a bare why[:280] cut a sentence
        # mid-word ("...crucial for sales professionals to understand these
        # innovat") straight into the reader-facing brief. Truncate on a
        # word boundary like every other length-capped field in this file.
        why = _truncate_on_word(why, 280)
        cache[key] = why
        _team_synth_why[key] = why
        changed = True
    if changed:
        _save_json(TEAM_SYNTH_CACHE_PATH, cache)


# RB-2026-08-25 (Gap #10 -- editorial standard, principles 1/3/4): Todd's
# 2026-08-24 guiding principles for the Intelligence Brief require a CoS
# "why it matters" sentence for World/National/Restaurant Industry/
# Restaurant Technology headlines ("The CoS commentary in the newspaper
# should be limited to why a piece of news is important"). Confirmed live in
# the 2026-08-25 brief: every A-D item rendered with only the outlet's own
# RSS/press blurb (why_it_matters was never populated upstream for these
# sections, so _fmt_headline's `why_it_matters or summary` fallback always
# picked the raw summary) -- zero business-climate synthesis anywhere on the
# "front page." This reuses the exact _team_synthesize_batch machinery
# (cache/key/best-effort contract) with its own cache file and role summary:
# the personal reader is Todd himself tracking business-climate relevance
# (war/inflation/prices/labor/policy/tech), not a GP/Genius seller being
# pitched competitive positioning -- a shared cache/prompt would produce the
# wrong angle for one edition or the other.
_PERSONAL_HEADLINE_WHY_ROLE_SUMMARY = (
    "The reader is a Chief of Staff-style executive at a restaurant-"
    "technology/payments company, reading a morning newspaper of world, "
    "national, restaurant-industry, and restaurant-technology headlines. "
    "They care about a story only in the context of how it might affect "
    "the restaurant industry's business climate: war, inflation, oil/food "
    "prices, protests, labor law, immigration, politics, and technology all "
    "count if they plausibly move the industry's operations, cost "
    "structure, profitability, or competitive dynamics. In one concise "
    "sentence, grounded only in the evidence given, explain why THIS "
    "specific story matters to that business climate -- name the actual "
    "mechanism (a cost, a policy change, a competitive shift, a labor or "
    "supply effect), never a generic 'this could affect the industry' "
    "hedge. If a story genuinely has no plausible business-climate "
    "implication, say so plainly ('No clear business-climate implication.') "
    "rather than inventing one."
)

_personal_why_synth: dict[str, str] = {}  # normalized-title -> synthesized sentence, reset per render
_PERSONAL_WHY_NO_IMPLICATION_PREFIXES = ("no clear business-climate implication", "no clear implication")


def _synthesize_personal_why_batch(items: list[dict]) -> None:
    """Best-effort batched LLM synthesis for A-D headline items (personal
    Intelligence Brief only -- the team edition already has its own
    equivalent via _team_synthesize_batch/_TEAM_READER_ROLE_SUMMARY).

    items: [{"title": <headline>, "evidence": <headline + summary text>}, ...]
    Populates the persistent cache (PERSONAL_WHY_SYNTH_CACHE_PATH) and the
    per-run _personal_why_synth dict. Never raises -- on any failure the
    caller (_fmt_headline) just renders without a "Why it matters" line,
    same as today's behavior."""
    global _personal_why_synth
    cache = _load_json(PERSONAL_WHY_SYNTH_CACHE_PATH, {})

    keyed: dict[str, dict] = {}
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        key = _team_synth_key(title)
        if key not in keyed:
            keyed[key] = {"key": key, "title": title, "evidence": it.get("evidence") or title}

    for key, entry in keyed.items():
        if key in cache:
            _personal_why_synth[key] = cache[key]

    to_synthesize = [v for k, v in keyed.items() if k not in cache]
    if not to_synthesize:
        return

    try:
        import brief_synthesis as _bs
        result = _bs.synthesize_signals(to_synthesize, _PERSONAL_HEADLINE_WHY_ROLE_SUMMARY) or {}
    except Exception:
        return

    changed = False
    for key, fields in result.items():
        why = (fields.get("why") or "").strip()
        if not why:
            continue
        why = _truncate_on_word(why, 280)
        cache[key] = why
        _personal_why_synth[key] = why
        changed = True
    if changed:
        _save_json(PERSONAL_WHY_SYNTH_CACHE_PATH, cache)


def _team_why_it_matters(title: str, summary: str = "") -> str:
    """Reader-facing relevance line for the executive team edition. Prefers
    real per-story LLM synthesis (see _team_synthesize_batch); falls back to
    the deterministic bucket sentence when synthesis hasn't covered this
    story."""
    key = _team_synth_key(title)
    synthesized = _team_synth_why.get(key)
    if synthesized:
        return synthesized
    return _team_why_it_matters_fallback(title, summary)


def _render_team_headline_section(items: list[dict], title: str, limit: int = 5) -> str:
    """Render ranked, linked executive stories without feed boilerplate."""
    rows: list[str] = []
    seen: set[str] = set()
    seen_entities: set[str] = set()
    for item in items:
        headline = re.sub(r"^\[[^\]]+\]\s*", "", (item.get("title") or "").strip())
        extras = item.get("extras") or {}
        url = (extras.get("source_url") or "").strip()
        if not headline or not url:
            continue
        key = re.sub(r"\W+", " ", headline.lower()).strip()
        if key in seen:
            continue
        # Same-company coverage from two trade feeds is usually one story,
        # not two executive takeaways (e.g. Palona launch + funding writeup).
        entity_key = " ".join(key.split()[:2])
        if entity_key and entity_key in seen_entities:
            continue
        seen.add(key)
        if entity_key:
            seen_entities.add(entity_key)
        why = _team_why_it_matters(headline, item.get('summary') or item.get('why_it_matters') or '')
        # RB-DEFECT-2026-08-21: D+ already drops any story that only earns
        # the "Read this only if..." non-answer -- A/B shipped it unfiltered.
        # A story the system can't say anything specific about shouldn't run.
        if why.startswith("Read this only if"):
            continue
        source = (extras.get("source_name") or "").strip()
        pub_date = (extras.get("pub_date") or "").strip()
        meta = " · ".join(x for x in (source, pub_date) if x)
        rows.append(f"### [{headline}]({_clean_url_for_display(url)})" + (f"\n*{meta}*" if meta else ""))
        rows.append(f"**Why it matters:** {why}")
        _rendered_this_run.add(url)
        if len(rows) // 2 >= limit:
            break
    if not rows:
        return ""
    return f"## {title}\n\n" + "\n\n".join(rows)


def _team_newsletter_score(title: str) -> int:
    t = title.lower()
    score = 0
    if _TEAM_LEADERSHIP_RE.search(t): score += 5
    if re.search(r"\b(western union|dutch bros|wingstop|pizza hut|cracker barrel|olo|toast|fiserv|stripe)\b", t): score += 4
    if "western union" in t: score += 4
    if _TEAM_TECH_CLASS_RE.search(t): score += 3
    if re.search(r"\b(traffic|sales|margin|cost|marketing|strategy|growth|digital shift)\b", t): score += 2
    if re.search(r"\b(sponsored|webinar|guide|report|download|register|doorDash customers)\b", t, re.I): score -= 4
    return score


def _render_team_newsletters(newsletter_items: list[dict], name_re: "re.Pattern | None",
                             *, filter_team_terms: bool = True, max_items: int = 7,
                             today: date | None = None, rendered: dict | None = None) -> str:
    """Curated newsletter stories for an executive reader.

    No raw email preview, no forced three-link floor, and no orphaned lead
    headline.  Each surviving story has a link and a reason to read it.

    RB-2026-08-29: confirmed live -- "Agentic Commerce: How to Power
    AI-Driven Payment Experiences" (a static industrydive.com resource-
    library link, not dated news) rendered here 3 days running
    (2026-08-27/28/29), and "Wingstop's chief brand officer is departing"
    rendered 2 days running with the literal identical tracking URL. Every
    other headline-producing section (A-E) checks/marks the persistent
    RENDERED_HEADLINES_PATH registry so a story doesn't repeat across days;
    this one only ever compared candidates against the current run's own
    _rendered_this_run set, which resets every render and so caught nothing
    from a prior day. `today`/`rendered` are optional (None preserves the
    old no-cross-day-dedup behavior) because the twice-weekly team-brief
    call site intentionally does NOT share the daily personal-brief's
    registry -- they're different documents/audiences/cadences, and wiring
    them into the same registry would let one suppress content in the
    other. Only the personal Intelligence Brief call site passes these.
    """
    candidates: list[tuple[int, str, str, str, str, str]] = []
    seen_titles: set[str] = set()
    editions = (_filter_newsletter_banned_terms(newsletter_items)
                if filter_team_terms else newsletter_items)
    # RB-DEFECT-2026-08-19: confirmed live -- "Pizza Hut's Global CEO
    # resigns" rendered in both D (Restaurant Technology, direct
    # restaurantdive.com URL) and D+ (Curated Trade Reads, a
    # link.restaurantdive.com/click/... tracking redirect for the identical
    # story). D+'s own seen_titles/entity_seen dedup only ever compared
    # candidates against each other -- it never checked _rendered_this_run,
    # the cross-section registry every other lettered section (A-E, K)
    # already respects. Resolve each tracking link the same way K does
    # (_resolve_wrapped_url decodes the base64 destination segment, no
    # network call) before comparing.
    _rendered_resolved = {_resolve_wrapped_url(u) for u in _rendered_this_run}
    for item in sorted(editions, key=lambda x: _newsletter_sort_key(x)):
        extras = item.get("extras") or {}
        source = (extras.get("source_name") or item.get("title") or "Newsletter").strip()
        # RB-2026-08-29: "CStore Decisions" (and "CStore Decisions' Morning
        # Fill-Up") already lands on the whitelisted inform.wtwhmedia.com
        # domain -- confirmed live, 9 editions in the bridgepoint inbox over
        # the prior 2 weeks -- but never passed this filter, so it was
        # fetched and then silently dropped every time. Todd confirmed he
        # wants c-store trade content (payments/POS adjacent to his
        # Genius/Worldpay scope) included alongside restaurant/QSR.
        #
        # "ai report" (not bare "ai" -- that's a substring of unrelated
        # words like "retail" and "email", which would over-match) admits
        # The AI Report, which Todd subscribed to for AI-in-restaurants
        # coverage. Unlike the other sources here, this one isn't
        # restaurant-scoped by construction -- individual headlines still
        # have to clear the normal relevance score gate below, which counts
        # a bare "ai" mention as signal regardless of source. That gate was
        # tuned assuming every candidate already came from a restaurant/
        # payments-scoped source; it may let through AI Report headlines
        # with no real restaurant angle. Revisit if that turns out noisy.
        if not any(p in source.lower() for p in ("restaurant", "qsr", "payments", "nrn", "pmq", "cstore", "ai report", "rundown ai")):
            continue
        for idx, article in enumerate(extras.get("articles") or []):
            headline = (article.get("title") or "").strip()
            url = (article.get("url") or "").strip()
            if not headline or not url or _url_leaks_pii(url):
                continue
            resolved_url = _resolve_wrapped_url(url)
            if url in _rendered_this_run or resolved_url in _rendered_resolved:
                continue
            if any(s in headline.lower() for s in _NEWSLETTER_JUNK_TITLE_PATTERNS):
                continue
            if _DECORATIVE_MASTHEAD_TITLE_RE.match(headline):
                continue
            # RB-2026-08-29: confirmed live -- "resources.industrydive.com"
            # is already in _NEWSLETTER_JUNK_URL_PATTERNS specifically to
            # reject static resource-library/sponsored links (evergreen,
            # not dated news), but this checked only the raw `url`, which is
            # always a click-tracking redirect (link.restaurantdive.com/
            # click/.../<base64>/...) whose literal string never contains
            # that substring -- only the base64-decoded destination does.
            # The pattern silently never fired for any wrapped resource-
            # library link. "Agentic Commerce: How to Power AI-Driven
            # Payment Experiences" and "How One Restaurant Group Discovered
            # AI That Actually Works" are both resources.industrydive.com
            # links that slipped through this way and re-rendered as if
            # they were fresh headlines on 3 and 2 consecutive days
            # respectively. Check both forms.
            if any(s in url.lower() or s in resolved_url.lower() for s in _NEWSLETTER_JUNK_URL_PATTERNS):
                continue
            norm = re.sub(r"\W+", " ", headline.lower()).strip()
            title_key = f"title:{norm}"
            # Cross-day dedup: keyed on the resolved (unwrapped) destination,
            # not the raw click-tracking URL -- the same newsletter link gets
            # a fresh tracking wrapper (different campaign/redirect domain)
            # on every send, so raw-URL comparison alone misses genuine
            # repeats. See the docstring note above for the live case found.
            # Some platforms (e.g. wtwhmedia's click1.inform.wtwhmedia.com
            # links) use an opaque tracking slug with no embedded/decodable
            # destination at all -- _resolve_wrapped_url falls back to
            # returning those unchanged, so a genuinely repeated story
            # (confirmed live: "Scooter's Coffee Names Anna Faktorovich
            # Chief Marketing Officer", 2026-08-27 and 2026-08-28, two
            # different opaque tracking URLs for the identical headline)
            # would still slip past URL-only dedup. Also check the
            # normalized-title key as a second, independent identity.
            if rendered is not None and today is not None and (
                _is_duplicate(resolved_url, rendered, today, is_corporate=False)
                or _is_duplicate(title_key, rendered, today, is_corporate=False)):
                continue
            # RB-2026-08-30: confirmed live -- "PayPal Deal Talks End as
            # Advent, Stripe Group Abandons Acquisition Effort" (a Payments
            # Dive newsletter forward, landing here in D+) rendered in the
            # same report as "[ACQUISITION] PayPal (PYPL) Hit After Stripe,
            # Advent Drop $53B Planned Takeover" (Yahoo Finance, Section B)
            # -- two different outlets covering the identical event under
            # unrelated URLs and unrelated leading phrasing, so neither the
            # URL/title dedup just above nor an exact resolve_story_identity()
            # match (tried first, below) could catch it -- the two titles'
            # leading subjects don't line up word-for-word even though a
            # human reads them as the same story. Sections A-E fall back to
            # an LLM tiebreak (core.find_tiebreak_candidates +
            # llm_assist.same_story_tiebreak) for exactly this shape of
            # near-miss; this newsletter path never consulted either
            # mechanism. Reuse both, narrowed to candidates already shown
            # earlier in this same run (an API call here is only worth
            # spending to avoid an in-report repeat, not to chase every
            # active cross-day story).
            skip_as_duplicate = False
            if today is not None:
                story_key = core.resolve_story_identity(headline)
                if story_key is not None:
                    found = _story_ledger.lookup(story_key)
                    if found is not None and found[0] in _rendered_story_ids_this_run:
                        skip_as_duplicate = True
                if not skip_as_duplicate:
                    for candidate_id, candidate_story in core.find_tiebreak_candidates(
                            headline, _story_ledger, today):
                        if candidate_id not in _rendered_story_ids_this_run:
                            continue
                        if llm_assist.same_story_tiebreak(
                                headline, candidate_story.get("title_sample") or "") is True:
                            skip_as_duplicate = True
                            break
            if skip_as_duplicate:
                continue
            score = _team_newsletter_score(headline)
            if name_re and name_re.search(headline): score += 2
            if score < 2:
                continue
            if norm in seen_titles:
                continue
            why = _team_why_it_matters(headline)
            if why.startswith("Read this only if"):
                continue
            date_label = (extras.get("pub_date") or "").strip()
            candidates.append((score, source, date_label, headline, url, norm))

    # Rank across the whole inbox, not inside every publication.  This is an
    # executive brief, not a newsletter directory.  Limit each source to two
    # stories and suppress alternate headlines about the same named company.
    candidates.sort(key=lambda x: x[0], reverse=True)
    chosen: list[tuple[int, str, str, str, str, str]] = []
    source_counts: dict[str, int] = {}
    entity_seen: set[str] = set()
    known_entities = ("pizza hut", "cracker barrel", "dutch bros", "wingstop",
                      "western union", "fiserv", "stripe", "paypal", "olive garden")
    for row in candidates:
        _, source, _, headline, _, norm = row
        if norm in seen_titles or source_counts.get(source, 0) >= 2:
            continue
        # RB 2026-08-26: reviewed live -- "Scooter's Coffee Names Anna
        # Faktorovich CMO" and "Blaze Pizza Appoints Tracy Stockard CMO"
        # rendered in both Section C (exec-hire) and here (D+), because this
        # loop's own cross-section dedup only checked a tiny hand-maintained
        # `known_entities` tuple that didn't include either company. Reuse
        # the same registry/cue check Section F already uses instead of
        # trying to keep a second entity list in sync.
        _headline_lower = headline.lower()
        if any(
            ent in _headline_lower and any(cue in _headline_lower for cue in _LEADERSHIP_ANNOUNCEMENT_CUES)
            for ent in _rendered_exec_hire_entities_this_run
        ):
            continue
        entity = next((e for e in known_entities if e in headline.lower()), "")
        if entity and entity in entity_seen:
            continue
        seen_titles.add(norm)
        if entity:
            entity_seen.add(entity)
        source_counts[source] = source_counts.get(source, 0) + 1
        chosen.append(row)
        if len(chosen) >= max_items:
            break

    if not chosen:
        return ""
    # Synthesize real per-story "why it matters" for the final, already-
    # narrowed selection (bounded at max_items) before rendering -- the
    # coarse candidate filter above only needed the deterministic bucket to
    # weed out zero-signal stories; the ones that actually ship deserve the
    # real thing.
    _team_synthesize_batch([{"title": headline, "evidence": headline} for _, _, _, headline, _, _ in chosen])
    blocks = []
    for _, source, date_label, headline, url, norm in chosen:
        # RB-QUALITY-2026-09-04: display the resolved/cleaned destination,
        # not the raw click-tracking redirect -- dedup tracking below still
        # uses the original `url` unchanged, only the rendered link differs.
        blocks.append(f"### [{headline}]({_clean_url_for_display(url)})")
        blocks.append(f"*{source}" + (f" · {date_label}" if date_label else "") + "*")
        blocks.append(f"**Why it matters:** {_team_why_it_matters(headline)}")
        # Register so a section rendered after D+ (E, F, K) doesn't repeat
        # this story under its own direct-publisher URL.
        _rendered_this_run.add(url)
        # Register in the persistent cross-day registry so this story
        # doesn't repeat in tomorrow's brief either. Both the resolved URL
        # (the raw tracking wrapper churns per send) and the normalized
        # title (for platforms whose tracking links have no decodable
        # destination at all) are registered -- see the two comments above.
        if rendered is not None and today is not None:
            _mark_rendered(_resolve_wrapped_url(url), headline, "newsletter", today, rendered)
            _mark_rendered(f"title:{norm}", headline, "newsletter", today, rendered)
    return "## D+: Curated Trade Reads\n\n" + "\n\n".join(blocks)


def _render_team_watchlist(items: list[dict], today: date, rendered: dict, limit: int = 5) -> str:
    # RB-DEFECT-2026-08-21: this section used to render raw upstream
    # why_it_matters/summary text verbatim -- for tracked entities that's
    # frequently just the source press release's own title (sometimes with
    # PR-wire correction-notice formatting like "/C O R R E C T I O N -- .../"),
    # which tells a reader nothing beyond "something was published." Every
    # other lettered section synthesizes a real relevance sentence; F should
    # too, grounded in that raw text as evidence.
    candidates: list[dict] = []
    for item in items:
        extras = item.get("extras") or {}
        if extras.get("watchlist_status") not in ("New Activity", "Relevant Activity"):
            continue
        if not _watchlist_evidence_names_own_entity(item):
            continue
        name = _watchlist_entity_name(item)
        url = (extras.get("source_url") or "").strip()
        evidence = (item.get("why_it_matters") or item.get("summary") or "").strip()
        if not name or not url or not evidence:
            continue
        # RB-2026-09-08: a "Relevant Activity" watchlist status can persist
        # for the same entity across multiple editions with no new evidence
        # -- confirmed live, P.F. Chang's/Jamba/Topgolf rendered byte-for-
        # byte identical in two consecutive team editions 4 days apart.
        # Cross-day dedup against the same persisted registry E:Earnings
        # already uses (this section previously only tracked same-run
        # dedup via _rendered_this_run, which resets every render).
        if _is_duplicate(url, rendered, today, is_corporate=False):
            continue
        candidates.append({"name": name, "url": url, "evidence": evidence})
        if len(candidates) >= limit:
            break
    if not candidates:
        return ""
    _team_synthesize_batch([
        {"title": c["evidence"], "evidence": f"{c['name']}: {c['evidence']}"} for c in candidates
    ])
    rows = []
    for c in candidates:
        why = _team_why_it_matters(c["evidence"])
        if why.startswith("Read this only if"):
            why = _truncate_on_word(c["evidence"], 180)
        rows.append(f"- **[{c['name']}]({_clean_url_for_display(c['url'])})** — {why}")
        # So Section I (which also scans watchlist items) doesn't repeat a
        # story F already surfaced this run, and so a future edition's
        # cross-day dedup check above sees it too.
        _rendered_this_run.add(c["url"])
        _mark_rendered(c["url"], c["name"], "team_watchlist", today, rendered)
    return "## F: Watchlist\n\n" + "\n".join(rows)


def _watchlist_entity_names(sections: dict) -> set[str]:
    """Real company/person names on the restaurant-tech watchlist (excludes
    the category-rollup pseudo-entities like "POS" or "Back Office", which
    are section headers in watchlist_intelligence, not entities a headline
    could plausibly be "about")."""
    names: set[str] = set()
    for item in sections.get("watchlist_intelligence", []) or []:
        name = (item.get("extras") or {}).get("entity_name") or ""
        name = name.strip()
        if name and name.lower() not in _GP_CATEGORY_PSEUDO_ENTITIES:
            names.add(name)
    return names


def _watchlist_name_re(watchlist_names: set[str]) -> "re.Pattern | None":
    """Word-boundary regex matching any tracked watchlist name.

    RB-DEFECT-2026-08-11: a naive `name in haystack` substring check let the
    2-letter watchlist entity "Qu" false-positive-match inside "Quality"
    (as in "Florida-based Quality Fresca..."), which is how an irrelevant
    Moe's Southwest Grill bankruptcy story slipped past the relevance
    filter. Names are sorted longest-first so "Restaurant Brands
    International" can't get shadowed by a shorter alternative earlier in
    the pattern."""
    if not watchlist_names:
        return None
    escaped = sorted((re.escape(n) for n in watchlist_names), key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(escaped) + r")\b", re.IGNORECASE)


_SPONSORED_URL_MARKERS = ("/spons/", "/sponsored/", "/partner-content/", "/branded-content/")


def _is_sponsored(item: dict) -> bool:
    """RB-DEFECT-2026-08-11: Restaurant Dive's "/spons/" URL path marks
    native advertising -- "Can your tech tell you this? Game-changing ways
    restaurant operators are using AI" reads like an article but is paid
    placement. A GP/Genius reader forwarding this brief shouldn't have an ad
    presented to them as if it were reporting, even when it's on-topic."""
    url = ((item.get("extras") or {}).get("source_url") or "").lower()
    return any(m in url for m in _SPONSORED_URL_MARKERS)


_GARBLED_TEXT_RE = re.compile(
    r"pointer-events|scroll-m[bt]-|threadScrollVars|calc\(var\(--|var\(--[a-z-]+[,)]",
    re.IGNORECASE,
)


def _looks_garbled(text: str) -> bool:
    """RB-DEFECT-2026-08-11: a broken page fetch leaked raw CSS/JS class
    names into a story's summary field ("]:pointer-events-auto
    R6Vx5W_threadScrollVars scroll-mb-[calc(...)]..."). Sending that to a
    reader who'll forward it to colleagues looks broken and unprofessional
    -- worse than showing no summary at all."""
    return bool(_GARBLED_TEXT_RE.search(text or ""))


def _is_gp_relevant(item: dict, watchlist_names: set[str], name_re: "re.Pattern | None") -> bool:
    """Does this item earn its place in a GP/Genius sales reader's inbox?
    Keep it if it names a tracked competitor/customer/prospect, or is
    substantively about a payments/POS/loyalty/restaurant-tech topic."""
    if _is_sponsored(item):
        return False
    extras = item.get("extras") or {}
    title = item.get("title") or ""
    summary = item.get("summary") or item.get("why_it_matters") or ""
    entities = set(extras.get("entities") or [])
    if entities & watchlist_names:
        return True
    haystack = f"{title} {summary}"
    if name_re and name_re.search(haystack):
        return True
    return bool(_GP_RELEVANT_THEME_RE.search(haystack))


def _filter_gp_relevant(items: list, watchlist_names: set[str],
                         name_re: "re.Pattern | None") -> list:
    return [i for i in items if _is_gp_relevant(i, watchlist_names, name_re)]


def _clean_garbled_summaries(items: list) -> list:
    """Blank out a garbled why_it_matters/summary (see _looks_garbled)
    rather than dropping an otherwise-legitimate, on-topic story over a
    broken field. _fmt_headline already renders no summary line at all when
    why is empty, so this degrades gracefully to a bare headline+link."""
    out = []
    for item in items:
        why = item.get("why_it_matters") or item.get("summary") or ""
        if _looks_garbled(why):
            item = dict(item)
            item["why_it_matters"] = ""
            item["summary"] = ""
        out.append(item)
    return out


def _filter_newsletter_for_gp(newsletter_items: list, name_re: "re.Pattern | None") -> list:
    """Team-edition-only per-article relevance filter + cross-source title
    dedup for D+. The shared _render_newsletter_inbox only gates at the
    publication level (a trusted industry source's articles all pass
    through unfiltered); a GP/Genius reader needs each individual article to
    earn its place too. Also dedups near-identical coverage across sibling
    newsletters from the same publisher network (e.g. "QSR AM Jolt" and
    "QSR Magazine" both running a same-day item) -- they're different
    source_name values, so the shared per-source cap never compares them."""
    seen_titles: set[str] = set()
    out: list = []
    for item in newsletter_items:
        item = dict(item)
        extras = dict(item.get("extras") or {})
        articles = extras.get("articles") or []
        kept = []
        for a in articles:
            title = (a.get("title") or "").strip()
            if not title:
                continue
            norm = re.sub(r"\W+", " ", title.lower()).strip()
            if norm in seen_titles:
                continue
            if not ((name_re and name_re.search(title))
                    or _GP_RELEVANT_THEME_RE.search(title)):
                continue
            seen_titles.add(norm)
            kept.append(a)
        # RB-DEFECT-2026-08-11: a block with zero surviving articles used to
        # still render via its body_summary fallback -- but body_summary is
        # usually raw email-subject/ad-copy text ("BROUGHT TO YOU BY --
        # Walmart Business..."), not substantive content, so an all-filtered
        # block left an orphaned, content-free header in the brief. Drop the
        # whole block once nothing GP-relevant survives.
        if not kept:
            continue
        extras["articles"] = kept
        item["extras"] = extras
        out.append(item)
    return out


def _since_window(items: list, window_start: "date | None") -> list:
    """Keep items published on/after window_start (accumulation since the
    last published edition). Items with no parseable pub_date, or when
    window_start is None (first-ever edition -- nothing to accumulate
    from yet), pass through unfiltered."""
    if window_start is None:
        return items
    out = []
    for item in items:
        extras = item.get("extras") or {}
        raw = extras.get("pub_date") or extras.get("published_date")
        pub = _parse_pub_date(raw)
        if pub is None or pub >= window_start:
            out.append(item)
    return out


def _render_team_business_summary(industry_items: list, tech_items: list, wl_items: list,
                                   coverage_str: str, today: date, rendered: dict) -> str:
    """RB-DEFECT-2026-08-11: Section I used to be the shared cross-source
    "convergence" engine (_render_strategic_signals) -- reader-opaque stats
    ("98 evidence item(s) across 4 source channel(s)") with no answer to
    "what should I actually know right now." Team edition replaces it with a
    plain-language roll-up built from what's already been gathered for A/B/F:
    which tracked accounts moved, and what ownership/financial-distress
    events hit the sector, in the window since the last edition."""
    escalated = [i for i in wl_items if (i.get("extras") or {}).get("watchlist_status") == "Escalation"]
    new_activity = [i for i in wl_items if (i.get("extras") or {}).get("watchlist_status") == "New Activity"]
    relevant_with_evidence = []
    for i in wl_items:
        if (i.get("extras") or {}).get("watchlist_status") != "Relevant Activity":
            continue
        why = (i.get("why_it_matters") or i.get("summary") or "").strip()
        url = ((i.get("extras") or {}).get("source_url") or "").strip()
        if (why and url and not any(p in why.lower() for p in _WATCHLIST_GENERIC_PLACEHOLDERS)
                and _watchlist_evidence_names_own_entity(i)):
            relevant_with_evidence.append(i)

    moved_names: list[str] = []
    for i in escalated + new_activity + relevant_with_evidence:
        name = _watchlist_entity_name(i)
        if name and name not in moved_names:
            moved_names.append(name)

    _EVENT_TYPES = {"bankruptcy", "acquisition", "funding_round"}
    events = [i for i in industry_items + tech_items
              if (i.get("extras") or {}).get("signal_type") in _EVENT_TYPES]

    header = f"## I: What Matters for GP\n\n*{coverage_str}*\n"
    bullets: list[str] = []
    # RB-DEFECT-2026-08-21: this section used to restate whichever
    # industry/tech items happened to also be the top A/B entries, verbatim
    # -- confirmed live, three of Section I's bullets were word-for-word
    # duplicates of A/B headlines already shown above with the same
    # why-it-matters line. A synthesis section that repeats what the reader
    # just read isn't synthesis. Skip anything _rendered_this_run already
    # covered (A, B, D+, E and F all register their URLs there before this
    # runs) so I only surfaces what didn't otherwise make the cut.
    summary_entities: set[str] = set()
    for item in (tech_items + industry_items):
        title = re.sub(r"^\[[^\]]+\]\s*", "", (item.get("title") or "").strip())
        url = ((item.get("extras") or {}).get("source_url") or "").strip()
        if not title or not url or url in _rendered_this_run:
            continue
        entity_key = re.sub(r"[^a-z0-9]+", " ", title.lower()).split()[:2]
        entity_key = " ".join(entity_key)
        if entity_key in summary_entities:
            continue
        why = _team_why_it_matters(title, item.get('summary') or '')
        # RB-DEFECT-2026-08-21: an item A/B already dropped for earning only
        # the "Read this only if..." non-answer must not resurface here --
        # I is a second chance to add real synthesis, not a dumping ground
        # for what A/B already rejected as content-free.
        if why.startswith("Read this only if"):
            continue
        summary_entities.add(entity_key)
        bullets.append(f"- **[{title}]({_clean_url_for_display(url)})** — {why}")
        if len(bullets) >= 3:
            break
    for item in events[:1]:
        title = re.sub(r"^\[[^\]]+\]\s*", "", (item.get("title") or "").strip())
        if title and not any(title in b for b in bullets):
            bullets.append(f"- **{title}** — Ownership or capital movement can reopen strategy, "
                           "budget and vendor decisions; identify the new decision path before outreach.")
    # Add only a concrete watchlist development.  Counts and name lists force
    # the reader to do the synthesis themselves and fail the executive test.
    scored_watchlist = []
    for item in wl_items:
        extras = item.get("extras") or {}
        url = (extras.get("source_url") or "").strip()
        evidence = (item.get("why_it_matters") or item.get("summary") or "").strip()
        name = _watchlist_entity_name(item)
        if not url or not evidence or url in _rendered_this_run or not _watchlist_evidence_names_own_entity(item):
            continue
        # RB-2026-09-08: cross-day dedup was missing here entirely --
        # confirmed live, the MarginEdge $80M Series D funding story ran in
        # this exact section on two consecutive team editions (Friday and
        # the following Tuesday) as if new both times. url in
        # _rendered_this_run above only catches a same-run repeat (e.g.
        # something F already picked); it has no memory of prior editions.
        if _is_duplicate(url, rendered, today, is_corporate=False):
            continue
        if not (_GP_RELEVANT_THEME_RE.search(f"{name} {evidence}")
                or re.search(r"\b(restaurant|qsr|hospitality|franchise|payments?)\b",
                             f"{name} {evidence}", re.I)):
            continue
        score = _team_newsletter_score(f"{name} {evidence}")
        if re.search(r"\b(partnership|rollout|deployment|acquisition|funding|executive|appoints|resigns)\b",
                     evidence, re.I):
            score += 4
        scored_watchlist.append((score, name, url, evidence))
    if scored_watchlist:
        _, name, url, evidence = max(scored_watchlist, key=lambda x: x[0])
        # RB-QUALITY-2026-09-04: confirmed live -- this always fell straight
        # to _team_why_it_matters_fallback's generic bucket text (e.g.
        # MarginEdge got the verbatim "New ownership or capital can
        # accelerate product investment..." template), never real synthesis.
        # Root cause: the upfront _team_synthesize_batch() call earlier in
        # render_team_edition() only covers industry_items/tech_items, keyed
        # by their full headline title -- watchlist items were never in that
        # batch at all, and even if a same-named story happened to be, the
        # cache key here is the bare entity NAME ("MarginEdge"), not a
        # headline, so it could never have matched anyway. Synthesize this
        # one item on demand, keyed the same way it's looked up.
        _team_synthesize_batch([{"title": name, "evidence": f"{name}. {evidence}"}])
        wl_why = _team_why_it_matters(name, evidence)
        if not wl_why.startswith("Read this only if"):
            bullets.append(f"- **[{name}]({_clean_url_for_display(url)})** — {_truncate_on_word(evidence, 160)} {wl_why}")
            _mark_rendered(url, name, "team_business_summary", today, rendered)
    if not bullets:
        return header + "*Everything that cleared the executive relevance bar this cycle is already covered above.*"
    return header + "\n".join(bullets[:4])


def render_team_edition(target_date: date, dry_run: bool = False, force: bool = False) -> str:
    """Team-facing edition: restaurant/payments-tech industry news only.

    The reader is a Global Payments/Genius sales professional, not Todd --
    RB-DEFECT-2026-08-11 rebuilt every section around that: a story earns
    its place only if it adds to the reader's knowledge of a payments/POS/
    loyalty/restaurant-tech topic, names a tracked competitor/customer/
    prospect (see _watchlist_entity_names), or represents a business
    opportunity (_is_gp_relevant / _filter_gp_relevant). Publishes Tuesdays
    and Fridays only (see morning_pipeline.py, which only appends this
    pipeline step on those weekdays); the restaurant industry (A) and
    restaurant technology (B) headline sections accumulate every fresh,
    GP-relevant item published since the last edition (via _since_window +
    _last_team_brief_date) rather than a fixed daily window, so a Tuesday
    edition covers everything since Friday and vice versa. World and
    National headlines are dropped entirely -- out of scope for a
    restaurant-industry digest. Same-story dedup is capped to 1 item per
    cluster here (vs. 2 for the personal brief) -- a reader skimming for
    what matters doesn't need the same bankruptcy filing covered by two
    trade outlets back to back. D+ Newsletter Inbox gets the same
    per-article relevance filter (_filter_newsletter_for_gp) plus
    cross-source title dedup, since "ran in an industry newsletter" isn't
    the same bar as "relevant to GP." E: Earnings & Corporate stays broad
    (competitors and customers/prospects are exactly what this section is
    for) but each item gets a templated "why this matters for GP" line
    (_gp_earnings_note). F: Watchlist surfaces "Relevant Activity" entities
    with genuine evidence, not just Escalation/New Activity -- Todd's
    personal brief deliberately suppresses "Relevant Activity" (his own
    RB-DEFECT-2026-07-27 request, since he can query the watchlist
    on-demand), but a GP/Genius reader has no such fallback and does want
    to know when a tracked account moved. Section I replaces the shared
    cross-source "convergence" stats dump with a plain "what mattered for
    GP since last brief" roll-up (_render_team_business_summary) built from
    what A/B/F already found. Every remaining section (and the newsletter/
    earnings/watchlist feeds) is also filtered to exclude any item
    mentioning "Global Payments" or "Genius" by name, and drops K: GP/
    Genius Field Intelligence entirely (it exists solely to track Todd's
    own employer and its product, not the industry).
    """
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    out_md = BRIEFS_DIR / f"{target_date.isoformat()}-team-intelligence-brief.md"

    if out_md.exists() and not force and not dry_run:
        return out_md.read_text(encoding="utf-8")

    cache = _load_json(DAILY_BRIEF_CACHE, {})
    data = cache.get("data") or cache
    sections = (data.get("canonical_brief") or {}).get("sections") or {}

    if not sections:
        return f"# Restaurant & Payments Industry Brief — {target_date.isoformat()}\n\n*No data available for this date.*\n"

    global _rendered_this_run, _rendered_subjects_this_run, _rendered_story_ids_this_run, _story_ledger, _team_synth_why, _rendered_exec_hire_entities_this_run
    prior_rendered_this_run = _rendered_this_run
    prior_rendered_subjects = _rendered_subjects_this_run
    prior_rendered_story_ids = _rendered_story_ids_this_run
    prior_story_ledger = _story_ledger
    _rendered_this_run = set()
    _rendered_subjects_this_run = set()
    _rendered_story_ids_this_run = set()
    _rendered_exec_hire_entities_this_run = set()
    _team_synth_why = {}

    # RB-2026-09-08: this used to start both of these empty every run and
    # never save them back -- confirmed live, three straight sections (E,
    # F, and I's watchlist pick) repeated the prior edition's stories
    # verbatim word-for-word (Yum Brands 8-K, P.F. Chang's/Jamba/Topgolf,
    # MarginEdge funding) because nothing remembered they'd already been
    # shown. Same persisted-state pattern as the personal brief's render(),
    # just in the team edition's own separate files (see
    # TEAM_RENDERED_HEADLINES_PATH/TEAM_STORY_LEDGER_PATH above).
    rendered_headlines = _load_json(TEAM_RENDERED_HEADLINES_PATH, {})
    _story_ledger = core.StoryLedger(_load_json(TEAM_STORY_LEDGER_PATH, {}))

    weekday = target_date.strftime("%A")
    rendered_at = cache.get("_generated_at") or data.get("rendered_at") or ""
    freshness_str = f"data as of {rendered_at[:16].replace('T', ' ')}" if rendered_at else "data freshness unknown"

    # RB-DEFECT: publishes Tue/Fri only now (see morning_pipeline.py step
    # gating) -- accumulate every restaurant industry/tech headline since the
    # last published edition instead of a fixed daily window.
    last_published = _last_team_brief_date(target_date)
    window_start = last_published + timedelta(days=1) if last_published else None
    coverage_str = (
        f"covering {window_start.strftime('%b %-d')}–{target_date.strftime('%b %-d, %Y')}"
        if window_start else f"covering through {target_date.strftime('%b %-d, %Y')}"
    )

    watchlist_names = _watchlist_entity_names(sections)
    watchlist_name_re = _watchlist_name_re(watchlist_names)

    industry_items = _clean_garbled_summaries(_filter_gp_relevant(_since_window(
        _filter_banned_terms(sections.get("restaurant_industry_headlines", [])), window_start),
        watchlist_names, watchlist_name_re))
    tech_items = _clean_garbled_summaries(_filter_gp_relevant(_since_window(
        _filter_banned_terms(_select_restaurant_tech_items(sections)), window_start),
        watchlist_names, watchlist_name_re))

    # Correct upstream category drift for the team edition.  Palona's AI
    # operating layer, for example, is technology even when the source feed
    # placed it in Restaurant Industry.
    moved_to_tech = [i for i in industry_items if _team_item_is_technology(i)]
    industry_items = [i for i in industry_items if not _team_item_is_technology(i)]
    tech_by_key: dict[str, dict] = {}
    for item in moved_to_tech + tech_items:
        key = ((item.get("extras") or {}).get("source_url")
               or re.sub(r"\W+", " ", (item.get("title") or "").lower()).strip())
        if key and key not in tech_by_key:
            tech_by_key[key] = item
    tech_items = list(tech_by_key.values())

    def _append_section(parts: list, rendered: str) -> None:
        if rendered:
            parts.append(rendered)
            parts.append("---")

    # Synthesize A/B's real per-story "why it matters" up front -- one batch
    # call covers both sections plus the top-story pick below, instead of
    # each section re-deriving its own boilerplate independently.
    _team_synthesize_batch([
        {
            "title": re.sub(r"^\[[^\]]+\]\s*", "", (i.get("title") or "").strip()),
            "evidence": (i.get("title") or "") + ". " + (i.get("summary") or i.get("why_it_matters") or ""),
        }
        for i in industry_items + tech_items
        if (i.get("title") or "").strip()
    ])

    # RB-DEFECT-2026-08-21: five roughly-equal-weight sections gave a
    # skimming reader no signal about what to read first. Surface the single
    # highest-scoring story (reusing the same ranking already used to curate
    # D+) as a one-line pointer right under the header.
    top_line = ""
    _scored = sorted(
        (
            (_team_newsletter_score(re.sub(r"^\[[^\]]+\]\s*", "", (i.get("title") or "").strip())), i)
            for i in industry_items + tech_items
            if (i.get("extras") or {}).get("source_url")
        ),
        key=lambda x: x[0], reverse=True,
    )
    if _scored and _scored[0][0] > 0:
        _top_item = _scored[0][1]
        _top_title = re.sub(r"^\[[^\]]+\]\s*", "", (_top_item.get("title") or "").strip())
        _top_url = (_top_item.get("extras") or {}).get("source_url") or ""
        _top_why = _team_why_it_matters(_top_title, _top_item.get("summary") or _top_item.get("why_it_matters") or "")
        if not _top_why.startswith("Read this only if"):
            top_line = f"**Today's must-read:** [{_top_title}]({_clean_url_for_display(_top_url)}) — {_top_why}"

    md_parts = [
        f"# Restaurant & Payments Industry Brief — {weekday}, {target_date.strftime('%B %-d, %Y')}",
        f"*{freshness_str} · {coverage_str} · restaurant, payments, and industry news*",
    ]
    if top_line:
        md_parts.append(top_line)
    md_parts.append("---")

    # max_per_cluster=1 (vs. the personal brief's default of 2): a reader
    # skimming for what matters doesn't need the same story from two outlets.
    _restaurant_cluster_state: dict = {"sig_words": [], "counts": []}
    _append_section(md_parts, _render_team_headline_section(
        industry_items, "A: Restaurant Industry"))

    _append_section(md_parts, _render_team_headline_section(
        tech_items, "B: Restaurant Technology"))

    newsletter = _render_team_newsletters(
        sections.get("newsletter_intelligence", []), watchlist_name_re)
    if newsletter:
        md_parts.append(newsletter)
        md_parts.append("---")

    earnings_sections = dict(sections)
    earnings_sections["watchlist_intelligence"] = _filter_banned_terms(
        sections.get("watchlist_intelligence", []))
    headline_entity_keys = {
        " ".join(re.sub(r"^\[[^\]]+\]\s*", "", (i.get("title") or "").lower()).split()[:2])
        for i in industry_items + tech_items
    }
    def _not_already_covered_entity(item: dict) -> bool:
        bare = re.sub(r"^\[[^\]]+\]\s*", "", (item.get("title") or "").lower())
        return " ".join(bare.split()[:2]) not in headline_entity_keys
    earnings_sections["restaurant_industry_headlines"] = [
        i for i in industry_items if _not_already_covered_entity(i)]
    earnings_sections["restaurant_technology_headlines"] = [
        i for i in tech_items if _not_already_covered_entity(i)]
    earnings = _render_earnings(earnings_sections, target_date, rendered=rendered_headlines,
                                 gp_context=True)
    if earnings and not earnings.lstrip().startswith("## E: Earnings & Corporate\n\n*No "):
        md_parts.append(earnings)
        md_parts.append("---")

    public_sections = dict(sections)
    public_wl_items = [
        i for i in sections.get("watchlist_intelligence", [])
        if (i.get("extras") or {}).get("watchlist_status") != "Escalation"
        and (i.get("extras") or {}).get("entity_name", "").strip().lower() not in ("genius", "global payments")
        and not _mentions_banned_terms(i.get("why_it_matters") or "", i.get("summary") or "")
        and not any(p in ((i.get("why_it_matters") or i.get("summary") or "").lower())
                    for p in ("no press-release text was retrievable", "exhibit fetch failed",
                              "call getcompanyearningshistory"))
    ]
    public_sections["watchlist_intelligence"] = public_wl_items
    team_watchlist = _render_team_watchlist(public_wl_items, target_date, rendered_headlines)
    if team_watchlist:
        md_parts.append(team_watchlist)
        md_parts.append("---")

    md_parts.append(_render_team_business_summary(
        industry_items, tech_items, public_wl_items, coverage_str, target_date, rendered_headlines))

    markdown = _strip_pii_links("\n\n".join(md_parts))

    if not dry_run:
        out_md.write_text(markdown, encoding="utf-8")
        _save_json(TEAM_RENDERED_HEADLINES_PATH, rendered_headlines)
        _save_json(TEAM_STORY_LEDGER_PATH, _story_ledger.to_dict())

    # Team rendering is an isolated product pass.  Do not leak its temporary
    # dedup registry into personal-brief rendering or subsequent tests/calls.
    _rendered_this_run = prior_rendered_this_run
    _rendered_subjects_this_run = prior_rendered_subjects
    _rendered_story_ids_this_run = prior_rendered_story_ids
    _story_ledger = prior_story_ledger

    return markdown


def _render_top_story(sections: dict, target_date: date) -> str:
    """Top Story — the single highest-materiality item of the day, pulled
    above every other section instead of waiting in whichever section it
    would normally land in.

    RB-2026-09-03, Todd's direct request: a PAR Technology competitor
    product launch sat passively in D: Restaurant Technology with generic
    boilerplate why-it-matters, while the SAME story already gets real,
    territory-specific sales commentary in the Daily Brief's GP/Genius:
    Dot Connections section -- Todd: "that's the one article that means
    something." The fix is not new synthesis; it's surfacing the existing
    top-scored GP/Genius Dot Connections pick (reused as-is, sorted
    highest-score-first there) at the very top of this document too,
    instead of only in the Daily Brief.

    Reuses render_daily_brief._render_gp_dot_connections() verbatim --
    same scoring, same persisted dedup registry (GP_DOT_RENDERED_PATH) --
    and extracts just its top bullet rather than re-implementing any of
    that selection logic here. Returns "" when there's nothing to show
    (no dot-connection-worthy item today), matching every other section's
    empty-state convention.
    """
    gp_dot_rendered = _load_json(render_daily_brief.GP_DOT_RENDERED_PATH, {})
    try:
        gp_dots_markdown = render_daily_brief._render_gp_dot_connections(
            sections, target_date, rendered=gp_dot_rendered,
        )
    except Exception:
        return ""
    _save_json(render_daily_brief.GP_DOT_RENDERED_PATH, gp_dot_rendered)

    if not gp_dots_markdown:
        return ""
    bullet_lines = [
        ln for ln in gp_dots_markdown.splitlines()
        if ln.startswith("- ")
        and render_daily_brief.GP_DOT_GENERIC_FALLBACK not in ln
    ]
    if not bullet_lines:
        return ""
    top_bullet = bullet_lines[0][2:]  # strip the leading "- "

    return (
        "## Top Story\n\n"
        f"{top_bullet}\n\n"
        "*Highest-materiality item today, by GP/Genius territory relevance "
        "— see GP/Genius: Dot Connections in today's Daily Brief for full context.*"
    )


def render(target_date: date, dry_run: bool = False, force: bool = False,
           proof_dashboard: dict | None = None) -> str:
    """Render the Intelligence Brief for target_date. Returns the markdown string."""
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)

    out_md = BRIEFS_DIR / f"{target_date.isoformat()}-intelligence-brief.md"
    out_json = BRIEFS_DIR / f"{target_date.isoformat()}-intelligence-brief.json"

    # RB-DEFECT-067: same fingerprint-gated cache invalidation as
    # render_daily_brief.py -- Part 1 doesn't currently render weekly-plan
    # content, but shares the same existence-only cache gate pattern that
    # caused Part 2 to serve a stale render after a mid-day plan
    # confirmation. Applied here defensively so the two renderers can't
    # silently diverge in invalidation behavior if Part 1 ever grows a
    # weekly-plan-derived section.
    current_plan_fp = core.weekly_plan_fingerprint()
    current_health_fp = core.source_health_fingerprint()
    if out_md.exists() and not force and not dry_run:
        if core.is_weekly_plan_render_current(out_json) and core.is_source_health_render_current(out_json):
            return out_md.read_text(encoding="utf-8")

    # Load canonical data
    cache = _load_json(DAILY_BRIEF_CACHE, {})
    data = cache.get("data") or cache
    sections = (data.get("canonical_brief") or {}).get("sections") or {}

    if not sections:
        return f"# RB Intelligence Brief — {target_date.isoformat()}\n\n*No data available for this date.*\n"

    # Reset session dedup so cross-section same-run duplicates are caught fresh
    global _rendered_this_run, _rendered_subjects_this_run, _rendered_story_ids_this_run, _story_ledger, _rendered_exec_hire_entities_this_run, _personal_why_synth, _shown_why_by_section_this_run, _rendered_wrb_fingerprints_this_run
    _rendered_this_run = set()
    _rendered_subjects_this_run = set()
    _rendered_story_ids_this_run = set()
    _rendered_exec_hire_entities_this_run = set()
    _personal_why_synth = {}
    _shown_why_by_section_this_run = {}
    _rendered_wrb_fingerprints_this_run = set()

    # Load dedup index and prior brief state for cross-day freshness
    rendered_headlines = _load_json(RENDERED_HEADLINES_PATH, {})
    _story_ledger = core.StoryLedger(_load_json(STORY_LEDGER_PATH, {}))
    prior_state = _load_prior_brief_state(target_date)

    weekday = target_date.strftime("%A")
    cb = data.get("canonical_brief") or {}

    # Data coverage summary — more useful than an opaque trust score percentage
    # RB-DEFECT-2026-08-19: "calendar_intelligence" is checked here but no
    # code in daily_brief.py ever populates sections["calendar_intelligence"]
    # (confirmed: the key appears nowhere in daily_brief.py, only in the two
    # renderers' coverage lists) -- it reported "Missing: calendar
    # intelligence" every single day regardless of whether calendar data
    # actually flowed, including days with real, populated day-ahead/
    # personal-intelligence-delta calendar events. day_ahead is the key that
    # actually carries calendar data end to end; drop the dead key instead of
    # tracking a stream that structurally cannot ever report present.
    _COVERAGE_KEYS = [
        "world_national_headlines", "restaurant_industry_headlines",
        "restaurant_technology_headlines", "strategic_industry_signals",
        "watchlist_intelligence", "communication_intelligence", "day_ahead",
    ]
    populated_keys = [k for k in _COVERAGE_KEYS if sections.get(k)]
    populated = len(populated_keys)
    missing_keys = [k for k in _COVERAGE_KEYS if k not in populated_keys]
    rendered_at = cache.get("_generated_at") or data.get("rendered_at") or ""
    freshness_str = f"data as of {rendered_at[:16].replace('T', ' ')}" if rendered_at else "data freshness unknown"
    labels = {
        "world_national_headlines": "world/national news",
        "restaurant_industry_headlines": "restaurant industry",
        "restaurant_technology_headlines": "restaurant technology",
        "strategic_industry_signals": "strategic industry signals",
        "watchlist_intelligence": "watchlist",
        "communication_intelligence": "communications",
        "day_ahead": "day-ahead prep / calendar",
    }
    # RB-QUALITY-2026-09-04: "all N streams active" reported only whether
    # each of the 7 BRIEF SECTIONS had content today -- it said nothing
    # about the underlying raw source health shown further down in
    # "Source Health," which can (and regularly does) show several sources
    # at 0-25% trust the same day this line claims full coverage. Reading
    # both together in the same document, "all streams active" read as an
    # overclaim next to sources marked Unavailable/Stale right below it.
    # Pull the same trust score Proof Dashboard already computes so this
    # top-line claim can't contradict the appendix under it.
    _trust_score = None
    try:
        _scan_events_for_coverage = _load_collection_scan_events(target_date)
        _today_scan_for_coverage = _scan_events_for_coverage.get(target_date.isoformat())
        if _today_scan_for_coverage:
            _trust_score = _today_scan_for_coverage.get("trust_score")
    except Exception:
        pass
    _live_health = _load_json(core.CACHE_DIR / "source_health.json", {})
    _live_sources = _live_health.get("sources") or {}
    _live_health_current = str(_live_health.get("generated_at") or "")[:10] == target_date.isoformat()
    _accepted_live_limitations = {"calendar:global-payments", "email:global-payments"}
    if isinstance(_live_sources.get("web_scanner"), dict) and _live_sources["web_scanner"].get("status") in {"refreshed", "ok", "fresh"}:
        _accepted_live_limitations.add("market_signals")
    _live_unhealthy = [
        name for name, row in _live_sources.items()
        if isinstance(row, dict)
        and name not in _accepted_live_limitations
        and row.get("status") not in {"refreshed", "ok", "fresh"}
        and int(row.get("tier") or 99) <= 3
    ] if _live_health_current else []

    if missing_keys:
        missing = ", ".join(labels[k] for k in missing_keys)
        coverage_line = (
            f"*Coverage: {populated} of {len(_COVERAGE_KEYS)}. Missing: {missing} — "
            f"no verified output was produced; review the source in RB. {freshness_str}.*"
        )
    elif _live_health_current and _live_unhealthy:
        coverage_line = (
            f"*Coverage: all {len(_COVERAGE_KEYS)} brief sections produced content; "
            f"source exceptions remain for {', '.join(_live_unhealthy)}. "
            f"See Source Health below. {freshness_str}.*"
        )
    elif _live_health_current:
        coverage_line = f"*Coverage: all {len(_COVERAGE_KEYS)} brief sections produced content; monitored sources are current. {freshness_str}.*"
    elif _trust_score is not None and _trust_score < 70:
        coverage_line = (
            f"*Coverage: all {len(_COVERAGE_KEYS)} brief sections produced content, "
            f"but underlying source trust is only {_trust_score}% today — see Source "
            f"Health below before treating this as complete. {freshness_str}.*"
        )
    else:
        coverage_line = f"*Coverage: all {len(_COVERAGE_KEYS)} brief sections produced content · {freshness_str}.*"

    # World/National split — all items go through both filters
    wn_all = sections.get("world_national_headlines", [])

    def _append_section(parts: list, rendered: str) -> None:
        """Append a section + divider only if the section has content."""
        if rendered:
            parts.append(rendered)
            parts.append("---")

    persistent_lifecycle = _load_json(RENDERED_LIFECYCLE_PATH, {})
    md_parts = [
        f"# RB Intelligence Brief — {weekday}, {target_date.strftime('%B %-d, %Y')}",
        coverage_line,
        "---",
    ]
    top_story = _render_top_story(sections, target_date)
    if top_story:
        md_parts.append(top_story)
        md_parts.append("---")
    md_parts.extend([
        _render_autonomous_discovery(sections),
        "---",
    ])

    # Shared across A and B so the same real-world event (covered by two
    # different articles that land in different scope buckets) can't render
    # in both sections — see _story_anchor_nouns/cluster_state.
    _world_national_cluster_state: dict = {"sig_words": [], "counts": []}
    _append_section(md_parts, _render_headline_section(
        wn_all, "A: World Headlines", "world", target_date, rendered_headlines,
        scope_filter="world", cluster_state=_world_national_cluster_state))
    _append_section(md_parts, _render_headline_section(
        wn_all, "B: National Headlines", "national", target_date, rendered_headlines,
        scope_filter="us", cluster_state=_world_national_cluster_state))
    # RB-DEFECT-2026-07-10i: C and D each got their own fresh cluster_state,
    # so the same underlying story covered by different outlets (e.g. a
    # restaurant-industry trade write-up of a drive-thru AI rollout landing
    # in C, and a restaurant-tech write-up of the identical rollout landing
    # in D) rendered in both sections -- the anchor-noun "same story" dedup
    # this uses only ever saw its own section's half of the pool, exactly
    # the bug the world/national cluster_state was built to prevent, just
    # not extended to the restaurant beats.
    # RB 2026-08-26: reviewed live -- two Krispy Kreme Pumpkin Spice stories
    # (same announcement, two trade outlets) both rendered in C back to
    # back, because the default max_per_cluster=2 exists to allow "primary
    # angle + one distinct follow-up," but this cluster match doesn't check
    # whether the second item actually adds a new angle vs. just being
    # another outlet's writeup of the identical announcement. Tightened to
    # 1 for the restaurant beats, matching the reasoning already written
    # above for why two outlets on the same story shouldn't both render.
    _restaurant_cluster_state: dict = {"sig_words": [], "counts": []}
    _append_section(md_parts, _render_headline_section(
        sections.get("restaurant_industry_headlines", []),
        "C: Restaurant Industry", "restaurant", target_date, rendered_headlines,
        cluster_state=_restaurant_cluster_state, max_per_cluster=1))

    # Section D: Restaurant Tech
    tech_items = _select_restaurant_tech_items(sections)
    _append_section(md_parts, _render_headline_section(
        tech_items, "D: Restaurant Technology", "restaurant_tech", target_date, rendered_headlines,
        cluster_state=_restaurant_cluster_state, max_per_cluster=1))

    # D+: Newsletter Inbox
    newsletter = _render_team_newsletters(
        sections.get("newsletter_intelligence", []), None,
        filter_team_terms=False, max_items=5,
        today=target_date, rendered=rendered_headlines)
    if newsletter:
        md_parts.append(newsletter)
        md_parts.append("---")

    # E: Earnings & Corporate
    earnings = _render_earnings(sections, target_date, rendered=rendered_headlines)
    if earnings:
        md_parts.append(earnings)
        md_parts.append("---")

    # Collect URLs used in F: Watchlist so K can skip exact duplicates
    _f_urls: set[str] = set()
    try:
        pw_path = core.SYSTEM_DIR / "inbox" / "market_signals_earnings.jsonl"
        today_str = target_date.isoformat()
        if pw_path.exists():
            for _raw in pw_path.read_text(encoding="utf-8").splitlines():
                _raw = _raw.strip()
                if not _raw:
                    continue
                _sig = json.loads(_raw)
                if not _sig.get("_price_watch"):
                    continue
                if str(_sig.get("published_at") or "")[:10] != today_str:
                    continue
                _u = _sig.get("url") or ""
                if _u:
                    _f_urls.add(_u)
    except Exception:
        pass

    md_parts.extend([
        _render_watchlist_rollup(sections, today=target_date),
        "---",
        # RB 2026-08-26: Section G (Opportunities) removed — it duplicated
        # Daily Brief's "## Active Opportunities" (render_daily_brief.py),
        # which already renders the identical opportunity_board/w2_intelligence
        # source data. This was leftover from an incomplete migration (see
        # the "moved from Intel Brief" comment on the Daily Brief side) —
        # the Intelligence Brief's copy was never actually removed until now.
        _render_relationship_deltas(sections, target_date, prior_state=prior_state),
        "---",
    ])

    identity_matches = _render_identity_match_candidates(sections, today=target_date)
    if identity_matches:
        md_parts.append(identity_matches)
        md_parts.append("---")

    md_parts.extend([
        _render_strategic_signals(sections, prior_state=prior_state, today=target_date),
        "---",
    ])

    # I+: Entity Signal Convergence -- reads entity_convergence_scan.py's
    # last persisted result (a scheduled morning_pipeline.py step writes
    # it); rendering never re-runs the scan itself.
    convergence_scan = entity_convergence_scan.load_last_result()
    _append_section(md_parts, _render_entity_convergence(convergence_scan))

    try:
        early_signals = json.loads((core.CACHE_DIR / "early_intelligence_signals.json").read_text())
    except (OSError, ValueError):
        early_signals = None
    _append_section(md_parts, _render_early_signals(early_signals))

    assessment_summary = _render_intelligence_assessment_summary(sections)
    if assessment_summary:
        md_parts.append(assessment_summary)
        md_parts.append("---")

    gp_intel = _render_gp_intel(sections, target_date, f_rendered_urls=_f_urls,
                                 prior_state=prior_state)
    if gp_intel:
        md_parts.append(gp_intel)
        md_parts.append("---")

    # Capture Intelligence — processed sessions
    cap_intel = _render_capture_intelligence()
    if cap_intel:
        md_parts.append(cap_intel)
        md_parts.append("---")

    # Operational receipts belong after the substantive intelligence, where
    # they prove the system worked without displacing executive judgment.
    md_parts.append(_render_what_changed(
        sections, target_date, prior_state=prior_state,
        persistent_lifecycle=persistent_lifecycle,
    ))
    md_parts.append("---")
    md_parts.append(_render_intelligence_cycle_report(sections, target_date))
    md_parts.append("---")
    md_parts.append(_render_deep_research_baseline_progress(target_date))
    md_parts.append("---")

    md_parts.extend([
        _render_proof_dashboard(sections, proof_dashboard, target_date=target_date),
        "",
        # INTELLIGENCE_BRIEF_CANONICAL.md: Part 1 must close by offering
        # Part 2 -- "Then offer Part 2: 'Intelligence picture complete.
        # Want your Daily Brief?'" This used to stop at the first sentence,
        # dropping the transition into the Daily Brief entirely.
        "*Intelligence picture complete. Want your Daily Brief?*",
    ])

    markdown = "\n\n".join(md_parts)

    if not dry_run:
        out_md.write_text(markdown, encoding="utf-8")
        _save_json(out_json, {
            "date": target_date.isoformat(),
            "rendered_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            "sections_rendered": 12,
            "trust_score": cb.get("trust_score") or 0,
            "weekly_plan_fingerprint": current_plan_fp,
            "source_health_fingerprint": current_health_fp,
        })
        _save_json(RENDERED_HEADLINES_PATH, rendered_headlines)
        _save_json(STORY_LEDGER_PATH, _story_ledger.to_dict())
        _save_json(RENDERED_LIFECYCLE_PATH, persistent_lifecycle)

    return markdown


def main() -> int:
    p = argparse.ArgumentParser(description="Pre-render the RB Intelligence Brief.")
    p.add_argument("--date", default=None, help="Date to render (YYYY-MM-DD). Default: today.")
    p.add_argument("--dry-run", action="store_true", help="Print to stdout, don't save.")
    p.add_argument("--force", action="store_true", help="Re-render even if file exists.")
    p.add_argument("--team", action="store_true",
                   help="Render the team-facing edition (industry news only, no personal content).")
    args = p.parse_args()

    target = date.fromisoformat(args.date) if args.date else date.today()

    if args.team:
        markdown = render_team_edition(target, dry_run=args.dry_run, force=args.force)
        if args.dry_run:
            print(markdown)
        else:
            out = BRIEFS_DIR / f"{target.isoformat()}-team-intelligence-brief.md"
            print(f"✓ Team Intelligence Brief rendered: {out}")
        return 0

    markdown = render(target, dry_run=args.dry_run, force=args.force)

    if args.dry_run:
        print(markdown)
    else:
        out = BRIEFS_DIR / f"{target.isoformat()}-intelligence-brief.md"
        print(f"✓ Intelligence Brief rendered: {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
