#!/usr/bin/env python3
"""Public source feeder for restaurant-tech / operator market signals.

Fetches RSS or publicly-available feeds from a curated list of restaurant-tech
trade publications, legal/business news sources, and operator press feeds.

Design principles:
- Fixture-first: each source has a fixture fallback if the live feed is
  unavailable or returns an error.
- No broad crawling, no authenticated scraping.
- Output shape maps directly to market_signals.normalize_item().
- Source health is always reported — missing/unavailable feeds become
  source-health gaps, not silent synthesis.

Usage:
    python3 market_source_feeds.py --fetch --save-health
    python3 market_source_feeds.py --fixture          # use built-in fixtures only
    python3 market_source_feeds.py --fetch --source restaurant_dive
    python3 market_source_feeds.py --list-sources
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import rb_core as core  # noqa: E402
    INBOX_DIR = core.INBOX_DIR
    SYSTEM_DIR = core.SYSTEM_DIR
except Exception:
    # Standalone / test mode
    _HERE = Path(__file__).resolve().parent.parent
    INBOX_DIR = _HERE / "inbox"
    SYSTEM_DIR = _HERE

try:
    from ecosystem_intelligence import AI_APPLICATION_VALUES  # noqa: E402
except Exception:
    # Keep this module usable standalone even if ecosystem_intelligence.py's
    # own imports are unavailable — mirrors the rb_core fallback above.
    AI_APPLICATION_VALUES = {
        "voice_ai", "computer_vision", "ai_personalization_next_best_action", "forecasting",
        "labor_optimization", "food_waste_optimization", "robotics_autonomous_systems",
        "predictive_maintenance", "guest_sentiment_conversational_analytics",
        "order_accuracy_kitchen_throughput_ops_intelligence", "other",
    }


FEED_HEALTH_PATH = SYSTEM_DIR / ".cache" / "market_source_feeds_health.json"
FEEDER_OUTPUT_PATH = INBOX_DIR / "market_signals_feed.jsonl"

_FETCH_TIMEOUT = 12  # seconds


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

SOURCES: list[dict[str, Any]] = [
    {
        "slug": "restaurant_dive",
        "name": "Restaurant Dive",
        "source_type": "vertical_trade",
        "side": "operator_demand",
        "rss_url": "https://www.restaurantdive.com/feeds/news/",
        "category_hints": ["restaurant_ai", "pos", "labor", "drive_thru", "payments"],
        "watchlist_priority": True,
    },
    {
        "slug": "restaurant_business",
        "name": "Restaurant Business",
        "source_type": "vertical_trade",
        "side": "operator_demand",
        "rss_url": "https://www.restaurantbusinessonline.com/rss.xml",
        "category_hints": ["restaurant_ai", "franchise", "labor", "drive_thru"],
        "watchlist_priority": True,
    },
    {
        "slug": "nations_restaurant_news",
        "name": "Nation's Restaurant News",
        "source_type": "vertical_trade",
        "side": "operator_demand",
        "rss_url": "https://www.nrn.com/rss/all",
        "category_hints": ["restaurant_ai", "pos", "payments", "franchise"],
        "watchlist_priority": True,
    },
    {
        "slug": "qsr_magazine",
        "name": "QSR Magazine",
        "source_type": "vertical_trade",
        "side": "operator_demand",
        "rss_url": "https://www.qsrmagazine.com/rss",
        "category_hints": ["drive_thru", "restaurant_ai", "labor", "pos"],
        "watchlist_priority": True,
    },
    {
        "slug": "hospitality_technology",
        "name": "Hospitality Technology",
        "source_type": "enterprise_restaurant_technology",
        "side": "vendor_supply",
        "rss_url": "https://hospitalitytech.com/rss.xml",
        "category_hints": ["pos", "restaurant_ai", "payments", "inventory"],
        "watchlist_priority": True,
    },
    {
        "slug": "pymnts_restaurant",
        "name": "PYMNTS (restaurant/food)",
        "source_type": "mainstream",
        "side": "vendor_supply",
        "rss_url": "https://www.pymnts.com/feed/",
        "category_hints": ["payments", "restaurant_ai", "pos"],
        "keyword_filter": ["restaurant", "food service", "QSR", "fast casual", "franchise"],
        "watchlist_priority": False,
    },
    {
        "slug": "fast_casual",
        "name": "Fast Casual",
        "source_type": "vertical_trade",
        "side": "operator_demand",
        "rss_url": "https://www.fastcasual.com/rss/",
        "category_hints": ["restaurant_ai", "drive_thru", "pos", "labor"],
        "watchlist_priority": False,
    },
]


# ---------------------------------------------------------------------------
# Signal classification helpers
# ---------------------------------------------------------------------------

_SIGNAL_TYPE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("lawsuit",         re.compile(r"\b(lawsuit|litigation|sue|sued|suing|complaint|injunction|court|legal action)\b", re.I)),
    ("location_closure", re.compile(r"\b(clos(?:e|es|ed|ing)|shutter(?:s|ed|ing)?|cease operations|bankrupt(?:cy)?|underperforming locations?)\b.{0,60}\b(location|restaurant|unit|store|stores|market|restaurants)\b|\b(location|restaurant|unit|store|stores|market|restaurants)\b.{0,60}\b(clos(?:e|es|ed|ing)|shutter(?:s|ed|ing)?|cease operations|bankrupt(?:cy)?)\b", re.I)),
    ("location_opening", re.compile(r"\b(open(?:s|ed|ing)?|expand(?:s|ed|ing)?|new unit|new location|new restaurant|growth market|development agreement)\b.{0,60}\b(location|restaurant|unit|store|stores|market|restaurants)\b|\b(location|restaurant|unit|store|stores|market|restaurants)\b.{0,60}\b(open(?:s|ed|ing)?|expand(?:s|ed|ing)?|development agreement)\b", re.I)),
    ("merger_acquisition", re.compile(r"\b(merger|merge|acqui(?:re|res|red|sition)|purchase(?:s|d)?|buy(?:s|ing|out)?|takeover|combination|consolidat(?:e|es|ed|ion))\b", re.I)),
    ("divestiture_selloff", re.compile(r"\b(sell(?:s|ing)? off|selloff|sold|divest(?:s|ed|iture)?|spin(?:s|ning)? off|carve-?out|asset sale|portfolio exit)\b", re.I)),
    ("investment_announcement", re.compile(r"\b(invest(?:s|ed|ing|ment)?|fund(?:s|ed|ing)?|capital raise|raises? \$|growth equity|private equity|strategic investment|financing round)\b", re.I)),
    ("c_suite_change", re.compile(r"\b(ceo|cfo|coo|cto|cio|chief executive|chief financial|chief operating|chief technology|chief information|president)\b.{0,50}\b(appoint(?:s|ed)?|name(?:s|d)?|hire(?:s|d)?|join(?:s|ed)?|depart(?:s|ed)?|step(?:s|ped)? down|resign(?:s|ed)?|retire(?:s|d)?)\b|\b(appoint(?:s|ed)?|name(?:s|d)?|hire(?:s|d)?|depart(?:s|ed)?|resign(?:s|ed)?)\b.{0,50}\b(ceo|cfo|coo|cto|cio|president)\b", re.I)),
    ("provider_win", re.compile(r"\b(select(?:s|ed)?|choose(?:s|chose|n)?|deploy(?:s|ed)?|roll(?:s|ed)? out|standardi[sz](?:e|es|ed)? on|partners? with|customer win|wins? (?:deal|contract|account))\b.{0,80}\b(pos|payments?|platform|solution|vendor|provider|technology|software|ai|kiosk|loyalty|ordering)\b", re.I)),
    # RB Unified Restaurant-Tech Graph (2026-07-31): renewal/expansion and
    # churn/loss detection, checked ahead of the broader implementation_failure/
    # pilot_rollback/partnership catch-alls so explicit lifecycle language wins.
    ("contract_renewal_expansion", re.compile(
        r"\b(renews?|renewed|renewal of|extends? (?:its |their )?(?:agreement|contract|partnership)|"
        r"expands? (?:its |their )?(?:rollout|partnership|agreement|deployment)|"
        r"multi.year renewal|extended (?:its |their )?agreement)\b", re.I)),
    ("vendor_churn_loss", re.compile(
        r"\b(replac(?:e|es|ed|ing) [\w\s]{1,30}? with|switch(?:es|ed|ing)? (?:to|from|away from)|"
        r"drops? (?:its |their )?(?:vendor|provider|partner|platform|system)|"
        r"discontinues? (?:its |their )?use of|ends? (?:its |their )?relationship with|"
        r"moves? away from|transitions? away from|terminates? (?:its |their )?agreement with)\b", re.I)),
    ("implementation_failure", re.compile(r"\b(rollback|failed|failure|dumped|scrapped|abandoned|pulled|discontinued|error|unreliable)\b", re.I)),
    ("pilot_rollback",  re.compile(r"\b(pilot|test|trial|proof of concept|POC).{0,40}\b(end|cancel|stop|rollback|fail)\b", re.I)),
    ("product_update",  re.compile(r"\b(update(?:s|d)?|upgrade(?:s|d)?|enhance(?:s|d)?|adds?|integrat(?:e|es|ed|ion)|new version|feature release)\b.{0,60}\b(product|platform|feature|solution|tool|module|capability|api|integration)\b", re.I)),
    ("product_launch",  re.compile(r"\b(launch|debut|introduce|release|announce|unveil|new)\b.{0,40}\b(ai|product|platform|feature|solution|tool)\b", re.I)),
    ("operator_priority", re.compile(r"\b(priority|invest|budget|roadmap|strategic|initiative|accelerate)\b", re.I)),
    ("partnership",     re.compile(r"\b(partner|partnership|integrat|collaborat|deal|agreement|acqui)\b", re.I)),
    ("earnings_signal", re.compile(r"\b(earnings|same-store sales|same store sales|comps|comparable sales|revenue|margin|traffic|check size|unit growth|Q[1-4]|quarter|forecast|guidance|outlook|ebitda|profit|loss)\b", re.I)),
]

_CATEGORY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("restaurant_ai",   re.compile(r"\b(ai|artificial intelligence|machine learning|computer vision|automation|voice ai|upsell ai|forecasting)\b", re.I)),
    ("pos",             re.compile(r"\b(pos|point.of.sale|payment terminal|kiosk|handheld)\b", re.I)),
    ("payments",        re.compile(r"\b(payment|pay.at.table|acquiring|processor|stored value|fraud|checkout)\b", re.I)),
    ("labor",           re.compile(r"\b(labor|scheduling|workforce|staffing|employee|tip|wage|retention)\b", re.I)),
    ("drive_thru",      re.compile(r"\b(drive.thru|drive.through|lane|throughput|speed of service|headset|timer|order accuracy)\b", re.I)),
    ("inventory",       re.compile(r"\b(inventory|food cost|procurement|waste|shrink|counting)\b", re.I)),
    ("loyalty",         re.compile(r"\b(loyalty|crm|guest data|personali|engagement|membership|offer)\b", re.I)),
    ("unit_growth",      re.compile(r"\b(opening|new location|unit growth|development agreement|expansion|clos(?:e|es|ed|ing|ure|ures)?|same-store sales|same store sales|traffic|comps)\b", re.I)),
    ("m_and_a",          re.compile(r"\b(merger|merge|acquire|acquires|acquired|acquisition|divestiture|selloff|private equity|strategic investment|consolidation)\b", re.I)),
    ("leadership",       re.compile(r"\b(ceo|cfo|coo|cto|cio|chief executive|president|leadership|board)\b", re.I)),
    ("online_ordering", re.compile(r"\b(online order|delivery|first.party|marketplace|catering)\b", re.I)),
    ("franchise",       re.compile(r"\b(franchise|franchisee|franchisor|multi.unit)\b", re.I)),
]

_STRATEGIC_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("high", re.compile(
        r"\b(lawsuit|litigation|rollback|implementation.fail|ai.fail|franchisee.trust|"
        r"pilot.cancel|vendor.replace|pos.replace|security.breach|data.breach)\b", re.I
    )),
    ("medium", re.compile(
        r"\b(partner|launch|invest|roadmap|acqui|ai|automation|integration|platform)\b", re.I
    )),
]

_COMPANY_PATTERNS = re.compile(
    r"\b(Toast|Square|PAX|NCR|Aloha|Oracle|Micros|Revel|Lightspeed|Shift4|Heartland|"
    r"Global Payments|Genius|Worldpay|Fiserv|Paytronix|Punchh|Olo|Qu|HungerRush|"
    r"Dragontail|Presto|SoundHound|Valyou|Xenial|Tillster|Intouch|Mantis|"
    r"McDonald's|Yum Brands|Pizza Hut|Taco Bell|KFC|Starbucks|Domino's|"
    r"Chick-fil-A|Chipotle|Shake Shack|Wingstop|Jack in the Box|Sonic|"
    r"Darden|Olive Garden|Applebee's|IHOP|Dine Brands|Restaurant Brands|"
    r"Burger King|Popeyes|Tim Hortons|Subway|Panera|Five Guys|"
    r"Smooth Commerce|Chaac Pizza)\b",
    re.I,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _classify_signal_type(text: str) -> str:
    for signal_type, pattern in _SIGNAL_TYPE_PATTERNS:
        if pattern.search(text):
            return signal_type
    return "market_update"


def _classify_category(text: str, hints: list[str]) -> str:
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(text):
            return category
    return hints[0] if hints else "restaurant_tech"


# RB Unified Restaurant-Tech Graph (2026-07-31): tag the AI application a
# signal describes, using the same closed enum as the ecosystem_intelligence.py
# graph schema's relationship.ai_application field (AI_APPLICATION_VALUES),
# so tagged signals can be reconciled against graph relationships directly.
_AI_APPLICATION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("voice_ai", re.compile(r"\b(voice ai|voice ordering|voice assistant|conversational ai ordering)\b", re.I)),
    ("computer_vision", re.compile(r"\b(computer vision|camera.based|visual (?:ai|recognition)|image recognition)\b", re.I)),
    ("ai_personalization_next_best_action", re.compile(r"\b(personali[sz]ation|next.best.action|ai.driven upsell|recommendation engine)\b", re.I)),
    ("forecasting", re.compile(r"\b(demand forecast|sales forecast|ai forecasting|predictive demand)\b", re.I)),
    ("labor_optimization", re.compile(r"\b(labor optimization|workforce optimization|ai scheduling|staffing optimization)\b", re.I)),
    ("food_waste_optimization", re.compile(r"\b(food waste|waste optimization|waste reduction ai)\b", re.I)),
    ("robotics_autonomous_systems", re.compile(r"\b(robot(?:ic|ics)?|autonomous (?:system|robot|delivery)|automated kitchen)\b", re.I)),
    ("predictive_maintenance", re.compile(r"\b(predictive maintenance|equipment health monitoring)\b", re.I)),
    ("guest_sentiment_conversational_analytics", re.compile(r"\b(guest sentiment|sentiment analysis|conversational analytics|review analytics)\b", re.I)),
    ("order_accuracy_kitchen_throughput_ops_intelligence", re.compile(r"\b(order accuracy|kitchen throughput|ops intelligence|drive.thru ai|speed of service ai)\b", re.I)),
]


def _classify_ai_application(text: str) -> str | None:
    """Return the AI_APPLICATION_VALUES enum value the text describes, or
    None if the text doesn't describe an AI application at all (not every
    signal is AI-related, so 'no match' is distinct from 'other')."""
    for value, pattern in _AI_APPLICATION_PATTERNS:
        if pattern.search(text):
            return value
    return None


# RB Unified Restaurant-Tech Graph (2026-07-31): distinguish "signed but not
# yet live" language from "currently deployed" language, using the same
# deployment_status vocabulary Phase 1 added to the graph schema
# (relationship.deployment_status) -- a trade-press signal claiming a
# contract was signed is weaker evidence than one confirming an active
# rollout, and the two must not be conflated when a signal later feeds a
# graph mutation.
_DEPLOYMENT_STAGE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("contracted_deployment_pending", re.compile(
        r"\b(sign(?:s|ed)? (?:a |an )?(?:agreement|contract|deal)|plans? to (?:roll out|deploy|launch)|"
        r"will (?:begin|start) (?:rolling out|deploying)|upcoming (?:rollout|deployment)|"
        r"expected to (?:roll out|deploy|launch))\b", re.I)),
    ("active_rollout", re.compile(
        r"\b(now live (?:at|in|across)|currently deployed|has (?:fully )?rolled out|"
        r"is (?:now )?live (?:at|in|across)|already deployed|up and running (?:at|in|across))\b", re.I)),
]


def _classify_deployment_stage_hint(text: str) -> str | None:
    """Return a WORKBOOK_DEPLOYMENT_STATUS_VALUES member describing whether
    the signal text claims a pending/contracted deployment or a currently-
    live one, or None if the text doesn't make either claim."""
    for value, pattern in _DEPLOYMENT_STAGE_PATTERNS:
        if pattern.search(text):
            return value
    return None


def _classify_strategic_relevance(text: str) -> str:
    for level, pattern in _STRATEGIC_PATTERNS:
        if pattern.search(text):
            return level
    return "low"


def _extract_company(text: str) -> str:
    matches = _COMPANY_PATTERNS.findall(text)
    seen: dict[str, None] = {}
    for m in matches:
        seen[m] = None
    return " / ".join(seen) if seen else ""


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def _parse_date(raw: str) -> str:
    """Normalize an RSS date string to YYYY-MM-DD, or return today."""
    if not raw:
        return datetime.now(timezone.utc).date().isoformat()
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(raw.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if m:
        return m.group(1)
    return datetime.now(timezone.utc).date().isoformat()


# ---------------------------------------------------------------------------
# RSS fetching
# ---------------------------------------------------------------------------

def _fetch_rss(url: str, timeout: int = _FETCH_TIMEOUT) -> ET.Element | None:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "RB-market-source-feeds/1.0 (+https://github.com/rb)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        return ET.fromstring(raw)
    except (urllib.error.URLError, urllib.error.HTTPError, ET.ParseError, OSError):
        return None


def _parse_rss_items(root: ET.Element, source: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    channel = root.find("channel")
    if channel is None:
        entries = root.findall("atom:entry", ns) or root.findall("entry")
        for entry in entries[:20]:
            title_el = entry.find("atom:title", ns) or entry.find("title")
            link_el = entry.find("atom:link", ns) or entry.find("link")
            pub_el = entry.find("atom:updated", ns) or entry.find("atom:published", ns) or entry.find("published")
            summary_el = entry.find("atom:summary", ns) or entry.find("summary") or entry.find("content")
            title = _strip_html(title_el.text if title_el is not None else "")
            url = (link_el.get("href") if link_el is not None else None) or ""
            pub_date = _parse_date(pub_el.text if pub_el is not None else "")
            summary = _strip_html(summary_el.text if summary_el is not None else "")
            items.append(_build_row(title, url, pub_date, summary, source))
    else:
        for item in channel.findall("item")[:20]:
            title = _strip_html((item.findtext("title") or ""))
            url = (item.findtext("link") or "").strip()
            pub_date = _parse_date(item.findtext("pubDate") or "")
            description = _strip_html(item.findtext("description") or "")
            items.append(_build_row(title, url, pub_date, description, source))
    return [r for r in items if r]


def _build_row(title: str, url: str, published_at: str, summary: str,
               source: dict[str, Any]) -> dict[str, Any] | None:
    if not title.strip():
        return None
    keyword_filter: list[str] = source.get("keyword_filter", [])
    if keyword_filter:
        combined = (title + " " + summary).lower()
        if not any(kw.lower() in combined for kw in keyword_filter):
            return None
    combined_text = title + " " + summary
    signal_type = _classify_signal_type(combined_text)
    category = _classify_category(combined_text, source.get("category_hints", []))
    strategic_relevance = _classify_strategic_relevance(combined_text)
    company = _extract_company(combined_text)
    ai_application = _classify_ai_application(combined_text)
    deployment_stage_hint = _classify_deployment_stage_hint(combined_text)
    if strategic_relevance == "high" and source.get("watchlist_priority"):
        confidence = "high"
    elif strategic_relevance in ("high", "medium"):
        confidence = "medium"
    else:
        confidence = "low"
    pain_point = summary[:300].strip() if summary else title
    return {
        "title": title,
        "url": url,
        "source_name": source["name"],
        "source_type": source["source_type"],
        "source_slug": source["slug"],
        "published_at": published_at,
        "fetched_at": _now(),
        "company": company,
        "side": source["side"],
        "category": category,
        "signal_type": signal_type,
        "pain_point_or_priority": pain_point,
        "strategic_relevance": strategic_relevance,
        "confidence": confidence,
        "ai_application": ai_application,
        "deployment_stage_hint": deployment_stage_hint,
        "feeder": "market_source_feeds",
    }


# ---------------------------------------------------------------------------
# Built-in fixtures
# ---------------------------------------------------------------------------

FIXTURE_ROWS: list[dict[str, Any]] = [
    {
        "title": "Pizza Hut / Yum Brands franchisee sues over Dragontail AI rollout failures",
        "url": "https://www.restaurantbusinessonline.com/technology/pizza-hut-yum-dragontail-ai-lawsuit",
        "source_name": "Restaurant Business",
        "source_type": "vertical_trade",
        "source_slug": "restaurant_business",
        "published_at": "2026-05-20",
        "fetched_at": _now(),
        "company": "Pizza Hut / Yum Brands / Dragontail / Chaac Pizza Northeast",
        "side": "operator_demand",
        "category": "restaurant_ai",
        "signal_type": "lawsuit",
        "pain_point_or_priority": (
            "Franchisee litigation over AI rollout reliability, operator workflow fit, "
            "and implementation governance. Dragontail AI order-management system failed "
            "to meet operational accuracy standards during high-volume Friday night service."
        ),
        "strategic_relevance": "high",
        "confidence": "high",
        "feeder": "market_source_feeds",
        "fixture": True,
    },
    {
        "title": "Starbucks dumps AI-powered inventory counting tool due to persistent errors",
        "url": "https://www.pymnts.com/restaurant-tech/starbucks-ai-inventory-rollback",
        "source_name": "PYMNTS",
        "source_type": "mainstream",
        "source_slug": "pymnts_restaurant",
        "published_at": "2026-05-22",
        "fetched_at": _now(),
        "company": "Starbucks",
        "side": "operator_demand",
        "category": "inventory",
        "signal_type": "pilot_rollback",
        "pain_point_or_priority": (
            "AI inventory counting tool discontinued after persistent accuracy errors. "
            "Operational trust breakdown when AI cannot meet store-floor reliability bar."
        ),
        "strategic_relevance": "high",
        "confidence": "medium",
        "feeder": "market_source_feeds",
        "fixture": True,
    },
    {
        "title": "Toast launches AI-powered labor forecasting for multi-unit operators",
        "url": "https://www.restaurantdive.com/news/toast-ai-labor-forecasting-multi-unit",
        "source_name": "Restaurant Dive",
        "source_type": "vertical_trade",
        "source_slug": "restaurant_dive",
        "published_at": "2026-05-18",
        "fetched_at": _now(),
        "company": "Toast",
        "side": "vendor_supply",
        "category": "labor",
        "signal_type": "product_launch",
        "pain_point_or_priority": (
            "AI labor forecasting reduces scheduling errors and overtime costs for operators "
            "managing 10+ locations. Positions Toast deeper in above-store analytics."
        ),
        "strategic_relevance": "medium",
        "confidence": "medium",
        "feeder": "market_source_feeds",
        "fixture": True,
    },
    {
        "title": "McDonald's accelerates AI drive-thru order accuracy program across 10,000 locations",
        "url": "https://www.qsrmagazine.com/technology/mcdonalds-ai-drive-thru-accuracy",
        "source_name": "QSR Magazine",
        "source_type": "vertical_trade",
        "source_slug": "qsr_magazine",
        "published_at": "2026-05-15",
        "fetched_at": _now(),
        "company": "McDonald's",
        "side": "operator_demand",
        "category": "drive_thru",
        "signal_type": "operator_priority",
        "pain_point_or_priority": (
            "Order accuracy improvement at scale. Franchisee buy-in required for "
            "consistent rollout. Speed-of-service and accuracy remain the primary "
            "drive-thru operational metrics."
        ),
        "strategic_relevance": "medium",
        "confidence": "medium",
        "feeder": "market_source_feeds",
        "fixture": True,
    },
    {
        "title": "Global Payments Genius integrates AI-powered upsell for QSR operators",
        "url": "https://hospitalitytech.com/global-payments-genius-ai-upsell-qsr",
        "source_name": "Hospitality Technology",
        "source_type": "enterprise_restaurant_technology",
        "source_slug": "hospitality_technology",
        "published_at": "2026-05-12",
        "fetched_at": _now(),
        "company": "Global Payments / Genius",
        "side": "vendor_supply",
        "category": "restaurant_ai",
        "signal_type": "product_launch",
        "pain_point_or_priority": (
            "Payment-platform-native AI upsell reduces integration complexity for operators "
            "already on Genius POS. Positions Global Payments as restaurant AI stack layer."
        ),
        "strategic_relevance": "high",
        "confidence": "medium",
        "feeder": "market_source_feeds",
        "fixture": True,
    },
]


# ---------------------------------------------------------------------------
# Source health
# ---------------------------------------------------------------------------

class SourceHealth:
    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []

    def record(self, slug: str, name: str, status: str, row_count: int = 0,
               error: str | None = None, fixture_used: bool = False) -> None:
        self.results.append({
            "slug": slug, "name": name, "status": status,
            "row_count": row_count, "fixture_used": fixture_used,
            "error": error, "checked_at": _now(),
        })

    def to_dict(self) -> dict[str, Any]:
        ok = [r for r in self.results if r["status"] == "ok"]
        failed = [r for r in self.results if r["status"] not in ("ok", "fixture_only")]
        return {"generated_at": _now(), "sources_ok": len(ok),
                "sources_failed": len(failed), "sources": self.results}

    def save(self, path: Path = FEED_HEALTH_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    def print_summary(self) -> None:
        for r in self.results:
            icon = "✓" if r["status"] == "ok" else ("⚑" if r["status"] == "fixture_only" else "✗")
            note = (" [fixture fallback]" if r["fixture_used"] else "") + (f" — {r['error']}" if r["error"] else "")
            print(f"  {icon} {r['name']}: {r['status']} ({r['row_count']} rows){note}")


# ---------------------------------------------------------------------------
# Fetch logic
# ---------------------------------------------------------------------------

def fetch_source(source: dict[str, Any], health: SourceHealth) -> list[dict[str, Any]]:
    rss_url = source.get("rss_url")
    if not rss_url:
        health.record(source["slug"], source["name"], "skipped_no_rss", 0)
        return []
    root = _fetch_rss(rss_url)
    if root is None:
        health.record(source["slug"], source["name"], "failed", 0,
                      error=f"Could not fetch or parse {rss_url}")
        return []
    rows = _parse_rss_items(root, source)
    if not rows:
        health.record(source["slug"], source["name"], "filtered_empty", 0,
                      error="RSS parsed but no rows passed filter")
        return []
    health.record(source["slug"], source["name"], "ok", len(rows))
    return rows


def fetch_all_sources(health: SourceHealth) -> list[dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []
    for source in SOURCES:
        all_rows.extend(fetch_source(source, health))
    return all_rows


def get_fixture_rows() -> list[dict[str, Any]]:
    return [dict(r) for r in FIXTURE_ROWS]


def run(*, use_fixtures: bool = False, fetch_live: bool = True,
        source_filter: str | None = None, save_health: bool = False,
        save_output: bool = False, output_path: Path = FEEDER_OUTPUT_PATH,
        health_path: Path = FEED_HEALTH_PATH) -> dict[str, Any]:
    health = SourceHealth()
    rows: list[dict[str, Any]] = []
    if use_fixtures:
        fixture_rows = get_fixture_rows()
        for r in fixture_rows:
            health.record(r.get("source_slug", "unknown"), r.get("source_name", "unknown"),
                          "fixture_only", 1, fixture_used=True)
        rows.extend(fixture_rows)
    elif fetch_live:
        sources = SOURCES
        if source_filter:
            sources = [s for s in sources if s["slug"] == source_filter]
            if not sources:
                return {"error": f"Unknown source slug: {source_filter}", "rows": [], "health": health.to_dict()}
        rows = fetch_all_sources(health)
    health_dict = health.to_dict()
    if save_health:
        health.save(health_path)
    if save_output and rows:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return {"rows": rows, "row_count": len(rows), "health": health_dict}


def refresh_market_sources(save_health: bool = True) -> dict[str, Any]:
    """Called by refresh_sources.py --market --save-health."""
    result = run(fetch_live=True, save_health=save_health, save_output=True)
    rows = result.get("rows", [])
    health = result.get("health", {})
    failed = [s for s in health.get("sources", []) if s["status"] == "failed"]
    if not rows and failed:
        return {"status": "failed", "row_count": 0, "health": health,
                "error": f"{len(failed)} source(s) failed: {[s['slug'] for s in failed]}"}
    if not rows:
        return {"status": "skipped_no_raw_input", "row_count": 0, "health": health}
    return {"status": "refreshed", "row_count": len(rows), "health": health,
            "output_path": str(FEEDER_OUTPUT_PATH)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="RB market source feeder")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--fetch", action="store_true")
    group.add_argument("--fixture", action="store_true")
    group.add_argument("--list-sources", action="store_true")
    p.add_argument("--source", dest="source_filter", metavar="SLUG")
    p.add_argument("--save-health", action="store_true")
    p.add_argument("--save-output", action="store_true")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()
    if args.list_sources:
        for s in SOURCES:
            print(f"  {s['slug']:30s} {s['name']}")
        return 0
    if args.fixture:
        result = run(use_fixtures=True, fetch_live=False,
                     save_health=args.save_health, save_output=args.save_output)
    else:
        result = run(fetch_live=True, source_filter=args.source_filter,
                     save_health=args.save_health, save_output=args.save_output)
    health = result["health"]
    rows = result["rows"]
    print(f"\n=== market_source_feeds run: {result['row_count']} rows ===")
    health_obj = SourceHealth()
    health_obj.results = health.get("sources", [])
    health_obj.print_summary()
    if not args.quiet:
        for r in rows[:5]:
            print(f"  [{r['strategic_relevance']:6s}] {r['title'][:80]}")
        if len(rows) > 5:
            print(f"  ... and {len(rows) - 5} more")
    if result.get("error"):
        print(f"\nERROR: {result['error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
