"""
web_scanner.py — RB 9.38 / Sprint E-2, Sprint E-2b (configurable sources)
External intelligence web scanner.

Sources are loaded from system/industry_sources.yaml — not hardcoded here.
This makes RB industry-agnostic: a real estate user configures real estate
feeds; a healthcare user configures healthcare feeds.  The restaurant sources
in industry_sources.yaml are the default for restaurant-tech users.

Classification buckets
──────────────────────
  world_national      — world/national news (always included)
  industry_primary    — user's primary industry (maps to restaurant_industry section)
  industry_technology — technology within the user's industry (maps to restaurant_technology section)
  mixed               — classify per article using keyword heuristics

Cache
─────
  .cache/web_scanner_cache.json — 6-hour TTL per source.
  On cache hit: returns cached items, skips network fetch and DB write.
  On cache miss: fetches, classifies, writes to IntelligenceDB, updates cache.

Key exports
───────────
  ScanResult           dataclass: world_national, restaurant_industry,
                        restaurant_technology, source_health, stats, metadata
  scan_all_sources()   run all sources → ScanResult
  load_sources_config() read system/industry_sources.yaml → config dict
  SOURCES              loaded source list (from YAML or builtin fallback)
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from entity_identity import attribution_terms, identity_terms, load_entities
except ImportError:  # package import in tests
    from .entity_identity import attribution_terms, identity_terms, load_entities

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SYSTEM_DIR = Path(__file__).resolve().parent.parent
_CACHE_DIR = _SYSTEM_DIR / ".cache"
_CACHE_FILE = _CACHE_DIR / "web_scanner_cache.json"
_ACTIVE_THREADS_PATH = _SYSTEM_DIR / "active_threads.yaml"
INDUSTRY_SOURCES_PATH: Path = _SYSTEM_DIR / "industry_sources.yaml"

CACHE_TTL_HOURS: int = 6

# ---------------------------------------------------------------------------
# Builtin fallback sources
#
# Used ONLY when industry_sources.yaml is missing or empty.
# In normal operation the scanner reads from that file — these constants
# are never surfaced to non-restaurant-tech users.
# ---------------------------------------------------------------------------

_BUILTIN_SOURCES: list[dict] = [
    {
        "name": "Restaurant Dive",
        "url": "https://www.restaurantdive.com/feeds/news/",
        "default_domain": "mixed",
        "confidence": "medium",
        "description": "Restaurant operations and technology news",
    },
    {
        "name": "Nation's Restaurant News",
        "url": "https://www.nrn.com/rss.xml",
        "default_domain": "industry_primary",
        "confidence": "medium",
        "description": "Restaurant industry trade publication",
    },
    {
        "name": "Restaurant Technology News",
        "url": "https://restauranttechnologynews.com/feed/",
        "default_domain": "industry_technology",
        "confidence": "medium",
        "description": "Restaurant technology focused coverage",
    },
    {
        # RB-DEFECT-2026-07-17: /rss.xml 403s (WAF-blocked regardless of
        # User-Agent) -- this cache entry sat frozen since 2026-06-03 with
        # nothing surfacing the failure. /feed (WordPress default) works and
        # returns same-day articles.
        "name": "QSR Magazine",
        "url": "https://www.qsrmagazine.com/feed",
        "default_domain": "industry_primary",
        "confidence": "medium",
        "description": "Quick service restaurant industry",
    },
    {
        # RB-DEFECT-2026-07-17: /rss 404s. The site's own <link rel="alternate">
        # advertises this search-based feed instead; it works but is often
        # empty (no #topstory-tagged article at scan time) -- better than a
        # permanent 404 since it refreshes cleanly instead of freezing.
        "name": "Franchise Times",
        "url": "https://www.franchisetimes.com/search/?f=rss&t=article&l=50&s=start_time&sd=desc&k%5B%5D=%23topstory",
        "default_domain": "industry_primary",
        "confidence": "medium",
        "description": "Franchise industry news",
    },
]

_BUILTIN_METADATA: dict = {
    "industry_name": "Restaurant Technology",
    "primary_label": "Restaurant Industry",
    "technology_label": "Restaurant Technology",
    "world_label": "World & National",
}

# ---------------------------------------------------------------------------
# Source config loader
# ---------------------------------------------------------------------------

def load_sources_config(path: Path | None = None) -> dict:
    """
    Load industry sources configuration from YAML.

    Reads system/industry_sources.yaml and returns:
    {
        "sources":  list[dict],  — source configs (name, url, default_domain, ...)
        "metadata": dict,        — section labels (primary_label, technology_label, ...)
    }

    Falls back to _BUILTIN_SOURCES + _BUILTIN_METADATA when:
      - The file doesn't exist (fresh install, not yet onboarded)
      - The file exists but has no sources defined
      - The file is malformed

    Users configure their industry by editing system/industry_sources.yaml —
    no code changes needed when switching industries.
    """
    config_path = path or INDUSTRY_SOURCES_PATH
    if config_path.exists():
        try:
            import yaml as _yaml  # type: ignore
            raw = _yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                sources = raw.get("sources") or []
                # Filter out template placeholder entries (empty name or url)
                sources = [
                    s for s in sources
                    if isinstance(s, dict)
                    and s.get("name", "").strip()
                    and s.get("url", "").strip()
                ]
                if sources:
                    return {
                        "sources": sources,
                        "metadata": raw.get("metadata") or _BUILTIN_METADATA,
                    }
        except Exception:  # noqa: BLE001
            pass  # fall through to builtin

    # Fallback: builtin restaurant-tech defaults
    return {
        "sources": _BUILTIN_SOURCES,
        "metadata": _BUILTIN_METADATA,
    }


# Module-level: load at import time. Tests can monkeypatch SOURCES or pass
# sources_config directly to scan_all_sources() to avoid YAML dependency.
_loaded_config: dict = load_sources_config()
SOURCES: list[dict] = _loaded_config["sources"]
SOURCE_METADATA: dict = _loaded_config["metadata"]


# ---------------------------------------------------------------------------
# Domain routing helpers
# ---------------------------------------------------------------------------

# Map from YAML default_domain values to ScanResult bucket names.
# Also supports legacy values that were used before the YAML config (backward compat).
_DOMAIN_TO_BUCKET: dict[str, str] = {
    "world_national":      "world_national",
    "industry_primary":    "restaurant_industry",   # maps to existing brief section
    "industry_technology": "restaurant_technology",  # maps to existing brief section
    "restaurant_industry":    "restaurant_industry",   # legacy compat
    "restaurant_technology":  "restaurant_technology",  # legacy compat
    "mixed":               "world_national",  # resolved per-article; this is the fallback
}

# ---------------------------------------------------------------------------
# Classification keywords
# ---------------------------------------------------------------------------

# Tech classification takes priority over industry classification.
# Match against lowercased title + description text.
_TECH_KEYWORDS: frozenset[str] = frozenset({
    # "pos" intentionally omitted — it is a substring of "positive", "composable", etc.
    # Use explicit multi-word forms instead.
    "point of sale", "point-of-sale", "pos system", "pos platform",
    "pos terminal", "pos solution", "kiosk", "self-order",
    "payment", "payments", "digital ordering", "online ordering", "mobile ordering",
    "mobile app", "software", "technology", "tech platform", "platform",
    "api", "integration", "ai ", "artificial intelligence", "automation",
    "cloud", "saas", "subscription", "data analytics", "analytics",
    "restaurant technology", "restaurant tech", "fintech", "payment processor",
    "toast ", " olo ", " par ", "par technology", "xenial", "ncr voyix",
    "ncr ", "oracle micros", "micros ", "lightspeed", "revel systems",
    "global payments", "worldpay", "heartland payment", "shift4", "fiserv",
    "doordash", "uber eats", "grubhub", "instacart", "deliveroo",
    "coates group", "foods connected", "matrix software", "genius restaurant",
    "digital kitchen", "kitchen display", "kds", "drive-thru technology",
    "loyalty platform", "loyalty program tech", "crm platform", "cdp",
    "menu management", "back-of-house", "labor management software",
    "inventory software", "franchise tech", "franchisee portal",
    "restaurant erp", "restaurant crm", "restaurant ai",
})

# Industry classification (used when tech keywords not matched)
_INDUSTRY_KEYWORDS: frozenset[str] = frozenset({
    "restaurant", "restaurants", "chain", "chains", "franchise", "franchisee",
    "franchisees", "operator", "operators", "quick service", "qsr",
    "fast casual", "fast food", "casual dining", "fine dining", "full service",
    "mcdonald", "starbucks", "chick-fil-a", "taco bell", "burger king",
    "wendy's", "wendy", "subway", "chipotle", "domino", "pizza hut",
    "yum brands", "yum!", "darden", "inspire brands", "jack in the box",
    "panera", "sonic ", "arby's", "popeyes", "wingstop", "shake shack",
    "dutch bros", "sweetgreen", "cava ", "el pollo", "raising cane",
    "restaurant brands", "rbi ", "menu item", "menu price", "menu",
    "same-store sales", "comparable sales", "traffic", "guest count",
    "drive-thru", "delivery", "catering", "loyalty rewards", "reward program",
    "food cost", "labor cost", "unit economics", "unit count",
    "new location", "new restaurant", "store opening", "remodel",
    "food safety", "food recall", "health inspection",
    "consumer spending", "dining out", "eat out", "foodservice",
    "food and beverage", "f&b", "hospitality",
})

# ---------------------------------------------------------------------------
# Signal type detection
# ---------------------------------------------------------------------------

# _SIGNAL_PATTERNS defined below after _CORE_ENTITY_WATCHLIST (new comprehensive format)

# ---------------------------------------------------------------------------
# Core entity watchlist
# ---------------------------------------------------------------------------

_CORE_ENTITY_WATCHLIST: list[str] = [
    # ── POS / Platform ────────────────────────────────────────────────────────
    "PAR Technology", "PAR",
    "Toast",
    "NCR Voyix", "NCR",
    "Oracle MICROS", "Oracle Hospitality", "MICROS",
    "Square", "Block",
    "SpotOn",
    "Qu",
    "Revel Systems",
    "Samsung Koomi", "Koomi",
    "GK Software", "GK",
    "Lightspeed",
    "Xenial",
    "HungerRush",
    "Flipdish",
    "Tillster",
    "TASK Group", "TASK",
    "Bopple",
    "Clover",
    # ── Loyalty / CRM ─────────────────────────────────────────────────────────
    "Punchh",
    "Paytronix",
    "Thanx",
    "SessionM",
    "PAR Loyalty",
    "Blackbird",
    "Incentivio",
    "Spendgo",
    # ── Payments ─────────────────────────────────────────────────────────────
    "Global Payments",
    "Shift4",
    "Fiserv",
    "Adyen",
    "Worldpay",
    "FreedomPay",
    "Stripe",
    "Heartland Payment",
    "PayPal",
    "Paysafe",
    # ── Back Office / Finance ─────────────────────────────────────────────────
    "Restaurant365", "R365",
    "Crunchtime",
    "QSR Automations",
    "MarginEdge",
    "Compeat",
    "Data Central",
    "Above the Line", "Above Store",
    "Agilysys",
    "Intouch Insight",
    # ── Inventory / Food Cost ─────────────────────────────────────────────────
    "MarketMan",
    "xtraCHEF",
    "ClearCOGS",
    "Tenzo",
    "NomadGo",
    "Barmetrix",
    # ── Digital Menu Boards / Drive-Thru Display ──────────────────────────────
    # STRATACACHE: missed June 2026 debt-driven sale to foreign ownership.
    # Now on permanent watch. This category should have been monitored all along.
    "STRATACACHE",
    "Coates Group",
    "Acrelec",
    "Mood Media",
    "Raydiant",
    "Spectrio",
    "Samsung VXT",
    "Nanonation",
    "Korbyt",
    "Scala",
    # ── Online Ordering / Delivery Platform ───────────────────────────────────
    "Olo",
    "Lunchbox",
    "Checkmate",
    "Deliverect",
    "ChowNow",
    "Owner.com",
    "DoorDash",
    "Uber Eats",
    "Grubhub",
    "Deliveroo",
    # ── Voice AI ─────────────────────────────────────────────────────────────
    "SoundHound",
    "Presto",
    "ConverseNow",
    "Kea",
    "Valyant",
    "Hi Auto",
    # ── Visual AI ────────────────────────────────────────────────────────────
    "Berry AI",
    "Agot",
    "Roboflow",
    "Everseen",
    "Veesion",
    # ── Labor / Workforce ────────────────────────────────────────────────────
    "Harri",
    "Legion",
    "Fourth",
    "HotSchedules",
    "Schoox",
    "Opus Training", "Opus",
    "WorkJam",
    "Crunchtime",
    # ── Robotics ─────────────────────────────────────────────────────────────
    "Miso Robotics", "Miso",
    "Richtech Robotics", "Richtech",
    "Hyphen",
    "Picnic",
    "Bear Robotics",
    "Serve Robotics",
    # ── Restaurant Brands / Operators ─────────────────────────────────────────
    "McDonald's", "McDonalds",
    "Starbucks",
    "Yum Brands", "Yum! Brands",
    "Restaurant Brands International", "RBI",
    "Chipotle",
    "Darden", "Olive Garden",
    "Inspire Brands",
    "Chick-fil-A",
    "Taco Bell",
    "Burger King",
    "Wendy's",
    "Subway",
    "Domino's",
    "Pizza Hut",
    "Panera",
    "Wingstop",
    "Shake Shack",
    "Sweetgreen",
    "CAVA",
    "Dutch Bros",
    "Raising Cane's",
    "Jack in the Box",
    # ── RB-specific active watchlist ──────────────────────────────────────────
    "Genius",
    "Matrix Software Solutions",
    "Foods Connected",
    "Franchisee Bridge",
]

# ---------------------------------------------------------------------------
# Signal type classification
# ---------------------------------------------------------------------------
# Maps signal_type → human label and priority weight (higher = more important)
# Used to sort and badge signals in the Restaurant Technology Radar section.
SIGNAL_TYPES: dict[str, dict] = {
    "acquisition":          {"label": "ACQUISITION",      "weight": 100, "emoji": "🏢"},
    "funding_round":        {"label": "FUNDING",          "weight": 90,  "emoji": "💰"},
    "pe_activity":          {"label": "PE/BUYOUT",        "weight": 88,  "emoji": "📊"},
    "bankruptcy":           {"label": "BANKRUPTCY",       "weight": 85,  "emoji": "⚠️"},
    "executive_hire":       {"label": "EXEC HIRE",        "weight": 75,  "emoji": "👤"},
    "executive_departure":  {"label": "EXEC DEPARTURE",   "weight": 72,  "emoji": "👤"},
    "customer_win":         {"label": "CUSTOMER WIN",     "weight": 70,  "emoji": "✅"},
    "customer_loss":        {"label": "CUSTOMER LOSS",    "weight": 68,  "emoji": "❌"},
    "deployment":           {"label": "DEPLOYMENT",       "weight": 60,  "emoji": "🚀"},
    "product_launch":       {"label": "PRODUCT LAUNCH",   "weight": 55,  "emoji": "🆕"},
    "earnings_surprise":    {"label": "EARNINGS",         "weight": 50,  "emoji": "📈"},
    "partnership":          {"label": "PARTNERSHIP",      "weight": 45,  "emoji": "🤝"},
    "restructuring":        {"label": "RESTRUCTURING",    "weight": 80,  "emoji": "🔄"},
    "general":              {"label": "NEWS",             "weight": 10,  "emoji": "📰"},
}

# Pattern lists for signal classification — matched against headline + summary text
_SIGNAL_PATTERNS: list[tuple[str, list[str]]] = [
    ("acquisition", [
        r"\bacquir\w*\b", r"\bmerger\b", r"\bbuys\b", r"\bbought\b",
        r"\btakeover\b", r"\bpurchase[sd]?\b", r"\bbuy out\b", r"\bbuyout\b",
        r"\bm&a\b", r"\bprivate equity.*acqui", r"\bsold to\b",
    ]),
    ("funding_round", [
        # Require "raises" + $ to co-occur with company/startup context, not analyst/political context
        r"\braises?\b.{0,60}\$[\d].{0,40}(?:million|billion|seed|round|series|fund)\b",
        r"\bseries [a-e]\b", r"\bfunding round\b",
        r"\bsecures? (?:invest|fund|capital)\w*", r"\b\$[\d].{0,40}(?:million|billion).{0,40}(?:rais|fund|invest)\b",
        r"\bseed (?:round|funding)\b", r"\bventure capital\b", r"\bvc fund\b",
        r"\bipo\b", r"\bpublic offering\b", r"\bstock market debut\b",
    ]),
    ("pe_activity", [
        r"\bprivate equity\b", r"\bpe firm\b", r"\bbuyout firm\b",
        r"\brecapitali[sz]\w*\b", r"\bleveraged buyout\b", r"\blbo\b",
        r"\bcarve[- ]out\b", r"\bportfolio company\b",
    ]),
    ("bankruptcy", [
        r"\bfiles? (?:for )?(?:chapter (?:11|7)|ch\.? (?:11|7)|bankruptcy)\b(?! in \d{4})",
        r"\bfiled (?:for )?(?:chapter (?:11|7)|ch\.? (?:11|7)|bankruptcy)\b(?! in \d{4})",
        r"\bchapter (?:11|7) (?:bankruptcy|filing|liquidation|protection)\b",
        r"\bch\.? (?:11|7) (?:bankruptcy|filing|liquidation|protection)\b",
        r"\binsolvenc\w*\b", r"\breceivership\b",
        r"\bliquidat\w*\b.*(?:assets|operations|chain|restaurant)",
        r"\bclos(?:es?|ing) (?:all|most of its|stores|locations|operations)\b",
    ]),
    ("executive_hire", [
        r"\bappoints?\b", r"\bnames?\b.*(?:ceo|cro|cto|cfo|vp|svp|evp|president|chief)",
        r"\bhires?\b.*(?:ceo|cro|cto|cfo|vp|president|chief)",
        r"\bjoins as\b", r"\bnew (?:ceo|cro|cto|cfo|vp|president|chief)\b",
        r"\bwelcomes?\b.*(?:executive|leader|chief)\b",
    ]),
    ("executive_departure", [
        r"\bsteps down\b", r"\bresigns?\b", r"\bdeparts?\b.*(?:ceo|cro|vp|chief)",
        r"\bleaves?\b.*(?:ceo|role|position|company)", r"\bfired\b",
        r"\bterminated\b", r"\bexit\w*\b.*(?:ceo|executive|chief)",
    ]),
    ("customer_win", [
        r"\bwins?\b.*(?:contract|deal|account|client)",
        r"\bselects?\b.*(?:as|for) (?:its|their|the)\b",
        r"\bdeploy\w*\b.*(?:partner|chain|brand|operator)",
        r"\blaunches?\b.*(?:with|for|at) [A-Z]",
        r"\bpowering\b", r"\bpartners with\b.*(?:chain|brand|restaurant)",
        r"\bsigns?\b.*(?:agreement|deal|contract).*(?:chain|brand|operator|restaurant)",
    ]),
    ("customer_loss", [
        r"\bdrops?\b.*(?:vendor|platform|provider|system)",
        r"\breplac\w*\b.*(?:with|by) [A-Z]",
        r"\bswitch\w*\b.*(?:from|away from|provider)",
        r"\benters?\b.*(?:with|agreement).*(?:compet)",
    ]),
    ("deployment", [
        r"\brollout\b", r"\bdeployment\b", r"\bimplementation\b",
        r"\bnationwide\b", r"\bglobal rollout\b", r"\bexpands? to\b",
        r"\binstall\w*\b.*(?:locations|stores|restaurants|units)",
    ]),
    ("product_launch", [
        r"\blaunch\w*\b.*(?:product|platform|feature|solution|ai|tool)",
        r"\bintroduc\w*\b.*(?:new|product|platform|ai|feature)",
        r"\breleas\w*\b.*(?:new (?:product|version|update|platform)|software|app\b)",
        r"\bunveil\w*\b.*(?:product|platform|feature|solution|ai|tool)",
        r"\bannounce\w*\b.*(?:new product|platform|solution)",
    ]),
    ("earnings_surprise", [
        r"\bearnings\b.*(?:beat|miss|exceed|below|above)",
        r"\brevenue\b.*(?:beat|miss|exceed|surpass|below guidance)",
        r"\bguidance\b.*(?:cut|lower|raise|below|above)",
        r"\bquarterly results\b", r"\bq[1-4] (?:earnings|results)\b",
    ]),
    ("restructuring", [
        r"\brestructur\w*\b", r"\blayoffs?\b", r"\bjob cuts?\b",
        r"\bworkforce reduction\b", r"\bdownsiz\w*\b", r"\breorganiz\w*\b",
        r"\bspin[- ]off\b", r"\bdivest\w*\b",
    ]),
    ("partnership", [
        r"\bpartnership\b", r"\bstrategic alliance\b", r"\bjoint venture\b",
        r"\bcollaborate\w*\b", r"\bintegrat\w*\b.*partner",
    ]),
]

# Pre-compile all patterns for performance
_COMPILED_SIGNAL_PATTERNS: list[tuple[str, list[re.Pattern]]] = [
    (sig_type, [re.compile(p, re.IGNORECASE) for p in pats])
    for sig_type, pats in _SIGNAL_PATTERNS
]


def classify_signal_type(title: str, summary: str = "") -> str:
    """Classify a headline into a signal type using pattern matching.

    Returns the signal_type key from SIGNAL_TYPES.
    Checks in priority order (highest weight first) so acquisition beats partnership.
    """
    text = f"{title} {summary}".lower()
    # Check in priority order
    for sig_type, compiled_pats in _COMPILED_SIGNAL_PATTERNS:
        for pat in compiled_pats:
            if pat.search(text):
                return sig_type
    return "general"


def signal_badge(signal_type: str) -> str:
    """Return a short badge string like '[💰 FUNDING]' for a signal type."""
    info = SIGNAL_TYPES.get(signal_type, SIGNAL_TYPES["general"])
    return f"[{info['emoji']} {info['label']}]"


def signal_weight(signal_type: str) -> int:
    """Return the importance weight for sorting (higher = more important)."""
    return SIGNAL_TYPES.get(signal_type, SIGNAL_TYPES["general"])["weight"]


def _graph_entity_identities() -> list[tuple[str, list[str]]]:
    """Return canonical entity names paired with all outbound identity terms."""
    return [
        (entity.get("name") or "", attribution_terms(entity))
        for entity in load_entities()
        if entity.get("name")
    ]

# ---------------------------------------------------------------------------
# XML namespaces for RSS/Atom parsing
# ---------------------------------------------------------------------------

_NS_ATOM = "http://www.w3.org/2005/Atom"
_NS_DC = "http://purl.org/dc/elements/1.1/"
_NS_CONTENT = "http://purl.org/rss/1.0/modules/content/"
_NS_MEDIA = "http://search.yahoo.com/mrss/"

# ---------------------------------------------------------------------------
# ScanResult
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    """
    Output of scan_all_sources().

    Lists contain _canonical_item-compatible dicts ready to extend
    brief sections directly.

    metadata carries section labels from industry_sources.yaml so the CoS
    (and rendering rules) can use the user's configured labels rather than
    hardcoded restaurant-industry names.
    """
    world_national: list[dict] = field(default_factory=list)
    restaurant_industry: list[dict] = field(default_factory=list)
    restaurant_technology: list[dict] = field(default_factory=list)
    source_health: list[dict] = field(default_factory=list)
    items_fetched: int = 0
    items_from_cache: int = 0
    errors: list[str] = field(default_factory=list)
    # Labels from industry_sources.yaml metadata — empty dict if not configured
    metadata: dict = field(default_factory=dict)

    def all_items(self) -> list[dict]:
        return self.world_national + self.restaurant_industry + self.restaurant_technology

    def total(self) -> int:
        return len(self.world_national) + len(self.restaurant_industry) + len(self.restaurant_technology)

    def to_brief_sections(self) -> dict[str, list[dict]]:
        return {
            "world_national_headlines": self.world_national,
            "restaurant_industry_headlines": self.restaurant_industry,
            "restaurant_technology_headlines": self.restaurant_technology,
        }


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
# RB-DEFECT-2026-08-14: confirmed live -- a QSR Magazine article used
# &#8203; (zero-width space, U+200B) as an inline word-break hint. Python's
# \s doesn't match U+200B (or its ZWNJ/ZWJ/BOM cousins), so after
# html.unescape() decodes it to a real character it survives whitespace
# collapsing untouched and renders as no space at all -- "Bun Slut" (the
# actual restaurant chain name) became "Bunslut" in the brief. These
# characters are invisible and only ever used as a soft space/line-break
# point in practice, so replacing them with a literal space is always the
# safe reading, never a corruption of intentional (visible) content.
_ZERO_WIDTH_RE = re.compile("[​‌‍﻿]")

# RB-QUALITY-2026-09-04: confirmed live in the Watchlist section -- a
# Nation's Restaurant News RSS <description> was actually a multi-story
# digest, not a single-article summary: "7 Brew is paying $143M for 73
# closed Salad and Go sites SEPTEMBER 02, 2026 TOP STORIES Pizza…". The
# original feed almost certainly had these as separate lines; collapsing
# all whitespace (below) erased that structure, leaving page chrome and a
# second headline fused onto the first with no separator. Cut the
# description at the first recognized chrome/digest marker rather than
# guessing at a generic all-caps heuristic (real content legitimately
# contains acronyms and dates; these specific phrases don't belong in a
# single-article summary regardless of casing).
_DESC_CHROME_MARKER_RE = re.compile(
    r"\b(top stories|read more|sign up|subscribe|breaking news|"
    r"related articles?|more from|latest news|trending now)\b",
    re.IGNORECASE,
)


def _strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    if not text:
        return ""
    text = html.unescape(text)
    text = _ZERO_WIDTH_RE.sub(" ", text)
    text = _HTML_TAG_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _cut_at_chrome_marker(cleaned: str) -> str:
    """Cut an already-HTML-stripped description off at the first
    digest/chrome marker -- see _DESC_CHROME_MARKER_RE for why."""
    match = _DESC_CHROME_MARKER_RE.search(cleaned)
    if match and match.start() > 0:
        cleaned = cleaned[: match.start()].rstrip(" -–—|.,")
    return cleaned


def _clean_description(text: str) -> str:
    """_strip_html, plus cutting off at the first digest/chrome marker.
    Only applied to description/summary fields, not titles (titles are
    short enough, and structurally a single headline, that this class of
    collision hasn't been observed there)."""
    return _cut_at_chrome_marker(_strip_html(text))


def _truncate_desc(text: str, limit: int = 600) -> str:
    """Word-boundary-safe truncation for description fields -- a bare
    text[:limit] can (and did) cut mid-word with no indicator that
    anything was cut off."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_space = cut.rfind(" ")
    if last_space > 0:
        cut = cut[:last_space]
    return cut.rstrip() + "…"


# ---------------------------------------------------------------------------
# RSS / Atom parsing
# ---------------------------------------------------------------------------

def _text(el: ET.Element | None, *tags: str, default: str = "") -> str:
    """Extract text from element, trying each tag name in order."""
    if el is None:
        return default
    for tag in tags:
        child = el.find(tag)
        if child is not None and child.text:
            return _strip_html(child.text.strip())
    return default


def _attr(el: ET.Element | None, tag: str, attrib: str, default: str = "") -> str:
    """Get attribute from a child element."""
    if el is None:
        return default
    child = el.find(tag)
    if child is not None:
        return child.get(attrib, default)
    return default


def parse_rss_items(xml_bytes: bytes, source_name: str) -> list[dict]:
    """
    Parse RSS 2.0 or Atom feed bytes into a list of item dicts.

    Each dict has: title, url, description, pub_date (ISO date string or "").

    Handles both RSS 2.0 (<item>) and Atom (<entry>) formats.
    Returns [] on malformed XML.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    items: list[dict] = []

    # Detect format: RSS 2.0 has <channel>/<item>, Atom has <entry>
    ns_atom = f"{{{_NS_ATOM}}}"

    # Atom
    if root.tag == f"{ns_atom}feed" or any(
        child.tag == f"{ns_atom}entry" for child in root
    ):
        for entry in root.iter(f"{ns_atom}entry"):
            title_el = entry.find(f"{ns_atom}title")
            title = _strip_html(title_el.text or "") if title_el is not None else ""

            # Link: prefer rel="alternate" or first <link>
            url = ""
            for link_el in entry.findall(f"{ns_atom}link"):
                rel = link_el.get("rel", "alternate")
                href = link_el.get("href", "")
                if href and rel in ("alternate", ""):
                    url = href
                    break
            if not url:
                url = _attr(entry, f"{ns_atom}link", "href")

            # Description: content > summary
            desc_el = entry.find(f"{ns_atom}content") or entry.find(f"{ns_atom}summary")
            desc = _clean_description(desc_el.text or "") if desc_el is not None else ""

            # Date: updated or published
            date_str = ""
            for date_tag in (f"{ns_atom}published", f"{ns_atom}updated"):
                el = entry.find(date_tag)
                if el is not None and el.text:
                    date_str = _parse_date_safe(el.text.strip())
                    if date_str:
                        break

            if title:
                items.append({
                    "title": title[:300],
                    "url": url,
                    "description": _truncate_desc(desc),
                    "pub_date": date_str,
                    "source_name": source_name,
                })
        return items

    # RSS 2.0 — look for <channel><item>
    channel = root.find("channel")
    if channel is None:
        channel = root  # some feeds omit <channel>

    for item_el in channel.findall("item"):
        title = _text(item_el, "title")
        url = _text(item_el, "link", "guid")
        if not url:
            guid_el = item_el.find("guid")
            if guid_el is not None and (guid_el.get("isPermaLink", "true") == "true"):
                url = guid_el.text or ""

        desc = _cut_at_chrome_marker(_text(
            item_el,
            f"{{{_NS_CONTENT}}}encoded",
            "description",
        ))
        pub_date_raw = _text(item_el, "pubDate", f"{{{_NS_DC}}}date")
        pub_date = _parse_date_safe(pub_date_raw)

        if title:
            items.append({
                "title": title[:300],
                "url": url,
                "description": _truncate_desc(desc),
                "pub_date": pub_date,
                "source_name": source_name,
            })

    return items


def _parse_date_safe(raw: str) -> str:
    """
    Parse a feed date string to ISO date YYYY-MM-DD. Returns "" on failure.
    Handles RFC 2822 (RSS), ISO 8601 (Atom), and common variants.
    """
    if not raw:
        return ""
    raw = raw.strip()

    # ISO 8601: 2026-06-01T10:00:00Z or 2026-06-01
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if m:
        return m.group(1)

    # RFC 2822: Mon, 01 Jun 2026 10:00:00 +0000
    # Try a few common patterns
    rfc_patterns = [
        r"\w+,\s+(\d{1,2})\s+(\w+)\s+(\d{4})",
        r"(\d{1,2})\s+(\w+)\s+(\d{4})",
    ]
    month_map = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "may": "05", "jun": "06", "jul": "07", "aug": "08",
        "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    }
    for pat in rfc_patterns:
        m = re.search(pat, raw, re.IGNORECASE)
        if m:
            groups = m.groups()
            if len(groups) == 3:
                day, month_str, year = groups
                month = month_map.get(month_str[:3].lower(), "")
                if month:
                    try:
                        return f"{year}-{month}-{int(day):02d}"
                    except ValueError:
                        pass
    return ""


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_headline(
    title: str,
    description: str,
    default_domain: str = "mixed",
) -> str:
    """
    Classify a headline into one of three output domains.

    Returns: "world_national" | "restaurant_industry" | "restaurant_technology"

    Accepts both YAML domain values (industry_primary, industry_technology)
    and legacy values (restaurant_industry, restaurant_technology).

    Priority:
      1. Non-mixed default_domain → bypass keywords and use the source's prior
         (a configured technology source always routes to technology)
      2. "mixed" sources: tech keywords → technology; industry keywords → industry
      3. Fallback → world_national
    """
    # Normalise YAML domain values to internal bucket names
    _norm = _DOMAIN_TO_BUCKET.get(default_domain, default_domain)

    # Non-mixed source: respect the configured domain entirely — skip keyword heuristics.
    # This is the key change for industry-agnostic use: a PropTech source stays PropTech
    # without needing restaurant-specific keywords.
    if _norm == "restaurant_technology":
        return "restaurant_technology"
    if _norm == "restaurant_industry":
        return "restaurant_industry"
    if _norm == "world_national" and default_domain != "mixed":
        return "world_national"

    combined = (title + " " + description).lower()

    # "mixed" sources: use keyword heuristics to classify per article
    # Check tech keywords first (higher specificity)
    for kw in _TECH_KEYWORDS:
        if kw in combined:
            return "restaurant_technology"

    # Check industry keywords
    for kw in _INDUSTRY_KEYWORDS:
        if kw in combined:
            return "restaurant_industry"

    # Apply source default for restaurant sources
    if default_domain in ("restaurant_industry", "industry_primary"):
        return "restaurant_industry"

    return "world_national"


def detect_entities(
    title: str,
    description: str,
    extra_watchlist: list[str] | None = None,
) -> list[str]:
    """
    Detect known entities mentioned in a headline.

    Checks the ecosystem graph's name + aliases + ticker identity set, then
    legacy and thread watchlists. Returns canonical entity names.
    """
    combined = title + " " + description
    combined_lower = combined.casefold()
    found: list[str] = []
    seen_lower: set[str] = set()
    identities = _graph_entity_identities()
    identities.extend((name, [name]) for name in _CORE_ENTITY_WATCHLIST)
    identities.extend((name, [name]) for name in (extra_watchlist or []))
    for canonical_name, terms in identities:
        canonical_key = canonical_name.casefold()
        if canonical_key in seen_lower:
            continue

        matched = False
        for term in terms:
            normalized = str(term or "").strip()
            if len(normalized) < 2:
                continue
            # Avoid compiling regex for every watched entity on every headline.
            # The active-thread watchlist can be large; the substring prefilter
            # preserves matching behavior while keeping daily scans bounded.
            if normalized.casefold() not in combined_lower:
                continue
            if re.search(
                rf"(?<!\w){re.escape(normalized)}(?!\w)",
                combined,
                re.IGNORECASE,
            ):
                matched = True
                break

        if matched:
            found.append(canonical_name)
            seen_lower.add(canonical_key)
    return found


def detect_signal_type(title: str, description: str) -> str:
    """Infer signal type from headline text using the comprehensive signal engine.

    Returns one of the SIGNAL_TYPES keys: acquisition | funding_round | pe_activity |
    bankruptcy | executive_hire | executive_departure | customer_win | customer_loss |
    deployment | product_launch | earnings_surprise | restructuring | partnership | general

    Uses _COMPILED_SIGNAL_PATTERNS (pre-compiled regex, checked in priority order).
    """
    return classify_signal_type(title, description)


# ---------------------------------------------------------------------------
# Implication + macro-category classification  (021E / 021F)
# ---------------------------------------------------------------------------

# 021E: structured implication category per signal_type
_IMPLICATION_BY_SIGNAL_TYPE: dict[str, str] = {
    "acquisition":         "opportunity",       # M&A = vendor-landscape / competitive-position shift
    "funding_round":       "buying-behavior",   # new capital = buyer appetite / timeline shift
    "pe_activity":         "opportunity",       # PE activity = ownership shift, new mandate
    "bankruptcy":          "opportunity",       # exit = competitive displacement opportunity
    "executive_hire":      "relationship",      # new leader = re-evaluate relationship map
    "executive_departure": "relationship",      # departure = instability / re-engagement window
    "customer_win":        "opportunity",       # competitor win = understand why they won
    "customer_loss":       "opportunity",       # competitor loss = displacement opportunity
    "deployment":          "opportunity",       # major rollout = market validation signal
    "product_launch":      "opportunity",       # new product = competitive threat or partner angle
    "earnings_surprise":   "operator-pressure", # earnings signal = check watch-list positions
    "restructuring":       "opportunity",       # restructuring = leadership gap, consulting need
    "partnership":         "opportunity",       # partnership = ecosystem positioning shift
    "general":             "theme",
}

# keyword fallback when signal_type doesn't map cleanly
_IMPLICATION_KEYWORD_MAP: list[tuple[str, list[str]]] = [
    ("pain",             ["cost", "pressure", "challenge", "struggle", "decline", "loss", "cut", "layoff", "bankruptcy"]),
    ("buying-behavior",  ["purchase", "deploy", "install", "rollout", "adopt", "invest", "upgrade", "contract"]),
    ("operator-pressure", ["margin", "labor", "wage", "traffic", "comparable", "same-store", "headcount", "squeeze"]),
    ("opportunity",      ["launch", "acquisition", "expand", "partner", "growth", "hire", "open", "deal"]),
    ("relationship",     ["appoint", "ceo", "cto", "president", "new leader", "resign", "depart", "named", "joins"]),
]


def detect_implication(signal_type: str, title: str, description: str) -> str:
    """021E: return implication category for a signal.

    Categories: pain | buying-behavior | operator-pressure | opportunity |
                relationship | theme
    """
    if signal_type in _IMPLICATION_BY_SIGNAL_TYPE:
        return _IMPLICATION_BY_SIGNAL_TYPE[signal_type]
    text = (title + " " + description).lower()
    for category, keywords in _IMPLICATION_KEYWORD_MAP:
        if any(kw in text for kw in keywords):
            return category
    return "theme"


# 021F: macro-economic/market-force categories
_MACRO_CATEGORY_PATTERNS: list[tuple[str, list[str]]] = [
    ("labor",            ["labor", "wage", "wages", "staffing", "worker", "workforce", "turnover",
                           "hiring", "job", "employment", "minimum wage", "overtime"]),
    ("consumer-spending", ["consumer spend", "consumer confidence", "discretionary", "wallet",
                            "household spend", "retail sales", "personal spend"]),
    ("traffic",          ["foot traffic", "same-store", "comp sales", "comparable", "visit frequency",
                           "dining traffic", "restaurant traffic"]),
    ("inflation",        ["inflation", "cpi", "price pressure", "food cost", "commodity", "tariff",
                           "freight cost", "supply chain cost", "pricing pressure"]),
    ("rates",            ["interest rate", "federal reserve", "fed rate", "rate hike", "rate cut",
                           "monetary policy", "bond yield", "prime rate", "borrowing cost"]),
    ("earnings",         ["earnings", "quarterly results", "q1 ", "q2 ", "q3 ", "q4 ",
                           "annual revenue", "guidance", "ebitda", "net income", "operating income"]),
    ("AI-investment",    ["artificial intelligence", " ai ", " ai-", "machine learning", "automation",
                           "generative ai", "llm", "large language model", "tech investment"]),
]


def detect_macro_category(title: str, description: str) -> str | None:
    """021F: return the macro-economic category for an article, or None if not macro.

    Only the first matched category is returned (highest-priority match wins).
    """
    text = (title + " " + description).lower()
    for category, keywords in _MACRO_CATEGORY_PATTERNS:
        if any(kw in text for kw in keywords):
            return category
    return None


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

def _load_cache(cache_path: Path | None = None) -> dict:
    """Load the web scanner cache from disk. Returns {} if missing or corrupt."""
    path = cache_path or _CACHE_FILE
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_cache(cache: dict, cache_path: Path | None = None) -> None:
    """Persist the scanner cache to disk."""
    path = cache_path or _CACHE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def is_cache_fresh(cache: dict, source_name: str, ttl_hours: int = CACHE_TTL_HOURS) -> bool:
    """
    Return True if the cached entry for source_name was fetched within ttl_hours.
    """
    entry = cache.get(source_name)
    if not isinstance(entry, dict):
        return False
    last_fetch_str = entry.get("last_fetch", "")
    if not last_fetch_str:
        return False
    try:
        last_fetch = datetime.fromisoformat(last_fetch_str.replace("Z", "+00:00"))
        if last_fetch.tzinfo is None:
            last_fetch = last_fetch.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
        return last_fetch >= cutoff
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# HTTP fetch
# ---------------------------------------------------------------------------

def _default_fetcher(
    url: str, timeout: int = 8, *, retries: int = 2, retry_delay: float = 2.0
) -> bytes | None:
    """
    Fetch a URL and return raw bytes. Returns None on any error.

    User-Agent set to avoid 403s from feeds that block default Python UA.

    RB-DEFECT-2026-07-30: confirmed live on two separate mornings
    (2026-07-29, 2026-07-30) that every single feed failed with the same
    DNS-resolution error (`[Errno 8] nodename nor servname provided`) when
    the pipeline ran right around a scheduled wake, then succeeded minutes
    later on a manual re-run with zero code change — the network interface
    simply wasn't reconnected yet. `_scan_source` also never updates a
    feed's cache timestamp on failure, so a bad moment produced a
    day-long gap in every headline section rather than one missed cycle.
    Retry a couple of times with a short delay before giving up; skip
    retrying `HTTPError` (a real server response like 403/404 won't change
    on retry).
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; RBScanner/1.0; "
                "+https://github.com/relationship-builder)"
            ),
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
    )
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError:
            return None
        except (urllib.error.URLError, OSError, TimeoutError):
            if attempt < retries:
                time.sleep(retry_delay)
                continue
            return None
    return None


# ---------------------------------------------------------------------------
# Active threads watchlist loader
# ---------------------------------------------------------------------------

def _load_thread_entities() -> list[str]:
    """
    Load entity names from active_threads.yaml for enhanced detection.
    Returns [] if file missing or unreadable.
    """
    if not _ACTIVE_THREADS_PATH.exists():
        return []
    try:
        # Avoid importing yaml — parse manually for the common structure
        text = _ACTIVE_THREADS_PATH.read_text(encoding="utf-8")
        # Extract values after 'company:' lines
        entities: list[str] = []
        for line in text.splitlines():
            m = re.match(r"^\s*(?:company|entity_name|entity):\s*['\"]?(.+?)['\"]?\s*$", line)
            if m:
                val = m.group(1).strip().strip("'\"")
                if val and len(val) > 2 and val.lower() not in ("null", "none", ""):
                    entities.append(val)
        return list(dict.fromkeys(entities))  # deduplicate preserving order
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# Per-headline → DB item + brief item conversion
# ---------------------------------------------------------------------------

def _build_brief_item(
    raw_item: dict,
    domain: str,
    entities: list[str],
    signal_type: str,
    source_config: dict,
) -> dict:
    """
    Convert a parsed RSS item into a brief-section-compatible dict.

    Output matches the shape expected by daily_brief section renderers:
    {
        title, summary, why_it_matters, recommended_action,
        disposition, grounding, confidence, extras: {domain, entities, signal_type, ...}
    }
    """
    conf = source_config.get("confidence", "medium")
    source_name = raw_item.get("source_name") or source_config.get("name", "")
    title = raw_item.get("title", "")
    desc = raw_item.get("description", "")
    url = raw_item.get("url", "")
    pub_date = raw_item.get("pub_date", "")

    # why_it_matters = the actual article description/summary.
    # Previously used a junk template string ("product launch signal via BBC
    # Business") that gave the GPT nothing useful to render. The article
    # description IS the implication — let the CoS layer interpret it from
    # real content rather than from a pre-baked label.
    why = (desc[:300] if desc else title)

    # For high-signal event types add a one-line CoS context note after the
    # description so the GPT has an explicit "why this matters to your world"
    # hook without being told to invent one.
    signal_context = {
        "acquisition":         "M&A event — assess vendor landscape / competitive position.",
        "funding_round":       "New capital — buyer appetite and timeline may shift.",
        "pe_activity":         "PE/ownership event — new mandate, leadership change likely.",
        "bankruptcy":          "Operator/vendor exit — assess impact on active opportunities.",
        "executive_hire":      "Leadership hire — re-evaluate relationship map and active threads.",
        "executive_departure": "Leadership departure — instability signal; re-engagement window.",
        "customer_win":        "Competitor win — understand why they won; assess your positioning.",
        "customer_loss":       "Customer loss — displacement opportunity for the displaced vendor.",
        "restructuring":       "Restructuring — leadership gap and consulting opportunity.",
        "earnings_surprise":   "Public earnings signal — check against watch-list positions.",
    }
    if signal_type in signal_context:
        why = f"{why} ↳ {signal_context[signal_type]}"

    # High-importance signals surface as act_today
    _high_importance = {"acquisition", "funding_round", "pe_activity", "bankruptcy",
                        "executive_hire", "executive_departure", "customer_win", "restructuring"}
    disposition = "act_today" if (signal_type in _high_importance and entities) else "monitor"

    # For world/national headlines, restrict badges to M&A, funding, and deployment only.
    # Industry-specific signals (bankruptcy, customer_win, product_launch, etc.) produce
    # false positives on general news (e.g. "ordered to release" → PRODUCT LAUNCH).
    _effective_signal = signal_type
    if domain == "world_national" and signal_type not in {
        "acquisition", "funding_round", "pe_activity", "deployment", "general"
    }:
        _effective_signal = "general"

    _badge = signal_badge(_effective_signal)
    _weight = signal_weight(_effective_signal)

    # Badge the title when it's a non-generic signal type so the GPT and user
    # can immediately see "this is an acquisition" without reading the body.
    badged_title = f"{_badge} {title}" if _effective_signal != "general" else title

    return {
        "title": badged_title,
        "summary": desc[:400] if desc else title,
        "why_it_matters": why,
        "recommended_action": (
            f"Review {SIGNAL_TYPES.get(signal_type, {}).get('label', signal_type)} signal for {entities[0]}"
            if entities else f"Review {source_name} headline"
        ),
        "disposition": disposition,
        "grounding": "system_detected",
        "confidence": conf,
        "freshness": "fresh",
        "source_refs": [source_name],
        "action_options": [],
        "extras": {
            "domain": domain,
            "entities": entities,
            "signal_type": signal_type,
            "signal_badge": _badge,
            "signal_weight": _weight,
            # 021E: structured implication category for CoS rendering
            "implication": detect_implication(signal_type, title, desc),
            # 021F: macro-economic/market-force category (None = not a macro signal)
            "macro_category": detect_macro_category(title, desc),
            "source_url": url,
            "pub_date": pub_date,
            "source_name": source_name,
        },
    }


def _item_dedup_key(title: str, source_name: str) -> str:
    """Stable dedup key for a headline."""
    raw = f"{source_name}::{title.strip().lower()}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Per-source scan
# ---------------------------------------------------------------------------

def _scan_source(
    source_config: dict,
    cache: dict,
    thread_entities: list[str],
    fetcher: Callable[[str, int], bytes | None],
    force_refresh: bool = False,
    db: Any = None,
) -> tuple[list[dict], dict]:
    """
    Scan a single source. Returns (brief_items, health_record).

    Reads from cache when fresh; fetches + writes cache + writes DB when stale.
    """
    name = source_config["name"]
    url = source_config["url"]
    default_domain = source_config.get("default_domain", "mixed")

    health: dict = {
        "source": name,
        "url": url,
        "status": "unknown",
        "items_returned": 0,
        "from_cache": False,
        "error": None,
    }

    # Cache hit?
    if not force_refresh and is_cache_fresh(cache, name):
        cached_items = (cache.get(name) or {}).get("items", [])
        health["status"] = "cache_hit"
        health["from_cache"] = True
        health["items_returned"] = len(cached_items)
        return cached_items, health

    # Fetch
    raw_bytes = fetcher(url, 8)
    if raw_bytes is None:
        health["status"] = "fetch_error"
        health["error"] = f"Failed to fetch {url}"
        return [], health

    # Parse
    raw_items = parse_rss_items(raw_bytes, name)
    if not raw_items:
        health["status"] = "parse_error"
        health["error"] = f"No items parsed from {url}"
        _update_cache(cache, name, [])
        return [], health

    # Cap to 20 most recent items per source
    raw_items = raw_items[:20]

    # Classify, detect, convert
    brief_items: list[dict] = []
    db_tags_batch: list[tuple[str, list[dict]]] = []

    for raw in raw_items:
        title = raw.get("title", "")
        desc = raw.get("description", "")
        if not title:
            continue

        domain = classify_headline(title, desc, default_domain)
        entities = detect_entities(title, desc, extra_watchlist=thread_entities)
        signal_type = detect_signal_type(title, desc)

        brief_item = _build_brief_item(raw, domain, entities, signal_type, source_config)
        brief_items.append(brief_item)

        # Write to IntelligenceDB (best-effort)
        if db is not None:
            try:
                tags = [{"type": "domain", "value": domain.replace("_", "-")}]
                for ent in entities:
                    tags.append({"type": "entity", "value": ent})
                if signal_type != "general":
                    tags.append({"type": "signal_type", "value": signal_type})
                tags.append({"type": "source_entity", "value": name})

                domain_to_industry = {
                    "restaurant_technology": "restaurant-tech",
                    "restaurant_industry": "restaurant-ops",
                    "world_national": "general",
                }
                tags.append({
                    "type": "industry",
                    "value": domain_to_industry.get(domain, "general"),
                })

                db.add_item(
                    title=title,
                    content=_truncate_desc(desc),
                    source_name=name,
                    source_url=raw.get("url"),
                    source_type="web_scan",
                    gathered_date=raw.get("pub_date") or date.today().isoformat(),
                    confidence=source_config.get("confidence", "medium"),
                    lifecycle_state="new",
                    tags=tags,
                )
            except Exception:  # noqa: BLE001
                pass  # never let DB failure block brief

    # Update cache
    _update_cache(cache, name, brief_items)

    health["status"] = "ok"
    health["items_returned"] = len(brief_items)
    return brief_items, health


def _update_cache(cache: dict, source_name: str, items: list[dict]) -> None:
    """Update the in-memory cache dict for a source (does not write to disk)."""
    cache[source_name] = {
        "last_fetch": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def scan_all_sources(
    db_path: Path | None = None,
    fetcher: Callable[[str, int], bytes | None] | None = None,
    force_refresh: bool = False,
    sources_config: dict | None = None,
    cache_path: Path | None = None,
) -> ScanResult:
    """
    Scan all configured sources and return a ScanResult.

    Parameters
    ----------
    db_path        Path to IntelligenceDB file; if None uses DEFAULT_DB_PATH.
                   Pass a temp path in tests to avoid touching production DB.
    fetcher        Callable(url, timeout) → bytes | None. If None, uses urllib.
                   Inject a mock fetcher in tests to avoid network calls.
    force_refresh  Bypass cache TTL and always re-fetch. Default False.
    sources_config Optional config dict overriding industry_sources.yaml.
                   Schema: {"sources": [...], "metadata": {...}}.
                   Used in tests and onboarding validation to avoid file I/O.
                   If None, loads from INDUSTRY_SOURCES_PATH (or builtin fallback).
    cache_path     Path to the scanner's fetch cache; if None uses _CACHE_FILE
                   (system/.cache/web_scanner_cache.json). Pass a temp path in
                   tests — db_path/sources_config were already isolated per-test,
                   but this one wasn't, so a mock fetcher's fixture RSS content
                   (cached by source *name*) got written straight into the real
                   production cache on every test run, silently overwriting
                   genuinely fetched headlines for any source name the test
                   happened to reuse (e.g. "BBC Business", "Restaurant Dive").

    Returns ScanResult with classified headline items and source health records.
    Each item is a brief-section-compatible dict.
    """
    if fetcher is None:
        fetcher = _default_fetcher

    # Resolve sources and metadata
    cfg = sources_config or load_sources_config()
    active_sources = cfg.get("sources") or SOURCES
    meta = cfg.get("metadata") or SOURCE_METADATA

    # Load IntelligenceDB for write path
    db = None
    try:
        from intelligence_db import IntelligenceDB
        _db_path = db_path or (_SYSTEM_DIR / ".cache" / "intelligence.db")
        db = IntelligenceDB(_db_path)
        db.open()
    except Exception:  # noqa: BLE001
        db = None  # scanner always runs, DB write is best-effort

    # Load extra entities from active_threads
    thread_entities = _load_thread_entities()

    # Load cache
    cache = _load_cache(cache_path)
    # Cache is subordinate to the active source registry. Removed feeds and
    # test fixtures must never survive as phantom production sources.
    active_names = {
        str(source.get("name") or "").strip()
        for source in active_sources if str(source.get("name") or "").strip()
    }
    cache = {name: entry for name, entry in cache.items() if name in active_names}

    result = ScanResult(metadata=meta)

    try:
        for source_config in active_sources:
            try:
                brief_items, health = _scan_source(
                    source_config=source_config,
                    cache=cache,
                    thread_entities=thread_entities,
                    fetcher=fetcher,
                    force_refresh=force_refresh,
                    db=db,
                )

                if health["from_cache"]:
                    result.items_from_cache += health["items_returned"]
                else:
                    result.items_fetched += health["items_returned"]

                result.source_health.append(health)

                # Route items into the right bucket using the domain map.
                # Handles both YAML domain values (industry_primary, industry_technology)
                # and legacy values (restaurant_industry, restaurant_technology).
                for item in brief_items:
                    raw_domain = (item.get("extras") or {}).get("domain", "world_national")
                    bucket = _DOMAIN_TO_BUCKET.get(raw_domain, "world_national")
                    if bucket == "restaurant_technology":
                        result.restaurant_technology.append(item)
                    elif bucket == "restaurant_industry":
                        result.restaurant_industry.append(item)
                    else:
                        result.world_national.append(item)

                if health.get("error"):
                    result.errors.append(health["error"])

            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"{source_config.get('name', '?')}: {exc}")
                result.source_health.append({
                    "source": source_config.get("name", "unknown"),
                    "status": "exception",
                    "error": str(exc),
                    "items_returned": 0,
                    "from_cache": False,
                })

        # Persist cache after all sources processed
        try:
            _save_cache(cache, cache_path)
        except Exception:  # noqa: BLE001
            pass

    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass

    return result
