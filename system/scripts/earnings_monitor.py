#!/usr/bin/env python3
"""earnings_monitor.py — Public company earnings & IR feed monitor (RB 9.28/9.29/Sprint G).

A world-class CoS monitors earnings calls and investor relations filings for
every public company that matters to the principal's industry. This script
fetches two primary-source feeds per company:

  1. SEC EDGAR 8-K filings — material events including earnings releases,
     "strategic alternatives" announcements, CEO departures, and activist
     investor settlements. These are primary-source, filed under oath.

  2. Investor Relations press release RSS — company-published earnings
     summaries, guidance updates, and product announcements. Higher frequency
     than EDGAR but lower authority.

Output is written to system/inbox/market_signals_earnings.jsonl in the same
schema as market_signals_feed.jsonl, so market_signals.py can merge it into
the daily brief pipeline automatically.

Signal taxonomy:
  earnings_release    — quarterly/annual financial results
  guidance_update     — forward guidance revision (raise, cut, or withdraw)
  strategic_review    — "exploring strategic alternatives" or similar
  material_event      — other 8-K material events (M&A, board change, lawsuit)
  leadership_change   — CEO/CFO/board appointment or departure
  activist_investor   — Schedule 13D/13G, activist settlement, proxy fight
  provider_win              — named customer win / new deployment announcement
  contract_renewal_expansion — renewal, extension, or rollout expansion of an
                               existing customer relationship
  vendor_churn_loss          — a customer replaces, drops, or switches away
                               from a vendor (RB Unified Restaurant-Tech Graph,
                               2026-07-31 — same vocabulary as
                               market_source_feeds.py's classifier, so
                               intelligence_mutation_engine.py can map either
                               source's signal_type to the same
                               reconciliation-outcome vocabulary)

Watch list mutation (RB 9.29):
  add_company()           — add a company to earnings_calendar.yaml
  remove_company()        — remove a company by name or ticker
  scan_for_watch_candidates() — scan signals for unlisted companies above threshold
  auto_add_from_signals() — auto-add companies that cross the threshold

Usage:
    python3 earnings_monitor.py                  # human-readable
    python3 earnings_monitor.py --fetch          # live EDGAR + IR fetches
    python3 earnings_monitor.py --fixture        # fixture-only (no network)
    python3 earnings_monitor.py --json           # JSON output
    python3 earnings_monitor.py --save-health    # write source health
    python3 earnings_monitor.py --list-companies # show configured companies
    python3 earnings_monitor.py --watch-only     # watch_priority=true only
    python3 earnings_monitor.py --add "Company"  # add to watch list
    python3 earnings_monitor.py --remove "Ticker/Name"  # remove from watch list
    python3 earnings_monitor.py --auto-scan      # auto-add companies above threshold
"""
from __future__ import annotations

import argparse
import calendar
import functools
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from entity_identity import find_entity, identity_terms, load_entities, search_queries
except ImportError:  # package import in tests
    from .entity_identity import find_entity, identity_terms, load_entities, search_queries

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import rb_core as core
    INBOX_DIR = core.INBOX_DIR
    SYSTEM_DIR = core.SYSTEM_DIR
    CACHE_DIR = core.CACHE_DIR
except Exception:
    _HERE = Path(__file__).resolve().parent.parent
    INBOX_DIR = _HERE / "inbox"
    SYSTEM_DIR = _HERE
    CACHE_DIR = _HERE / ".cache"

try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

try:
    import ecosystem_intelligence as _ei  # noqa: E402  # RB-2026-09-08, 3-store unification Phase 3 — entity_id resolution
except Exception:  # noqa: BLE001 — same optional-import posture as rb_core above
    _ei = None

EARNINGS_CALENDAR_PATH = SYSTEM_DIR / "earnings_calendar.yaml"
# RB_EARNINGS_OUTPUT_PATH: test-session isolation (see system/tests/conftest.py,
# same pattern as RB_REQUEST_LOG_PATH/RB_AUDIT_DIR). Without it, any test that
# exercises build_earnings_intelligence()/build_canonical_brief() without
# individually patching OUTPUT_PATH reads real production earnings signals
# and (via record_earnings_history) makes real network fetches and writes
# real history records -- discovered 2026-08-10 when the RB-DEFECT-066
# backfill made market_signals_earnings.jsonl non-empty of earnings_release
# rows for the first time, exposing every previously-harmless unpatched call.
OUTPUT_PATH = Path(os.environ.get(
    "RB_EARNINGS_OUTPUT_PATH", str(INBOX_DIR / "market_signals_earnings.jsonl")))
HEALTH_PATH = CACHE_DIR / "earnings_monitor_health.json"
SNAPSHOTS_DIR = SYSTEM_DIR / "_snapshots"

_FETCH_TIMEOUT = 15  # seconds

# SEC EDGAR 8-K Atom feed template. CIK is zero-padded to 10 digits by EDGAR.
_EDGAR_8K_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcompany&CIK={cik}&type=8-K"
    "&dateb=&owner=include&count=10&output=atom"
)

# RB Unified Restaurant-Tech Graph (2026-08-01), Phase 4 follow-up: 10-Q/10-K
# filing-index feeds. Same EDGAR browse-edgar endpoint and Atom shape as the
# 8-K feed above -- this is index/metadata-level coverage (filing title, date,
# accession number), the same depth the existing 8-K fetcher already
# operates at, not full-document text extraction (that would require
# fetching and parsing the filing body itself, a separate and much larger
# scope -- still deferred). Lower-frequency than 8-Ks (quarterly/annual, not
# event-driven), so only fetched at "full" tier, never "light".
_EDGAR_10K_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcompany&CIK={cik}&type=10-K"
    "&dateb=&owner=include&count=5&output=atom"
)
_EDGAR_10Q_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcompany&CIK={cik}&type=10-Q"
    "&dateb=&owner=include&count=5&output=atom"
)

# SEC EDGAR also publishes an Atom feed for all recent filings:
_EDGAR_RECENT_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&dateb=&owner=include&count=40&output=atom"

# Lookback: ignore filings older than this many days
_DEFAULT_LOOKBACK_DAYS = 90

# Tiered daily fetch (Unified Restaurant-Tech Graph request, 2026-07-31): as
# the tracked universe grows past a dozen names, fetching every company with
# the same full EDGAR+IR-RSS, 90-day-lookback treatment every single day
# doesn't scale. "Light" tier does a cheap EDGAR-8K-only check with a short
# lookback for the whole universe; "full" tier (unchanged full fetch) is
# reserved for watch_priority=true companies and anyone within
# _NEAR_EARNINGS_DAYS of their next estimated report date.
_LIGHT_LOOKBACK_DAYS = 14
_NEAR_EARNINGS_DAYS = 21


# ---------------------------------------------------------------------------
# Signal classification
# ---------------------------------------------------------------------------

_SIGNAL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("strategic_review",   re.compile(
        r"\b(strategic\s+alternatives?|strategic\s+review|sale\s+process|"
        r"go\s+private|take\s+private|explore.*options?|explore.*transaction|"
        r"review.*options?|merger\s+agreement|definitive\s+agreement|"
        r"acquisition\s+by|acquired\s+by|letter\s+of\s+intent)\b", re.I)),
    ("activist_investor",  re.compile(
        r"\b(activist\s+investor|schedule\s+13[dg]|proxy\s+fight|"
        r"proxy\s+contest|shareholder\s+demand|board\s+seat|"
        r"special\s+committee|standstill\s+agreement|settlement\s+with|"
        r"nominate.*director|director\s+nomination)\b", re.I)),
    ("leadership_change",  re.compile(
        r"\b(appoints?|names?|hires?|elects?|designates?)\b.{0,80}"
        r"\b(chief\s+executive|ceo|chief\s+financial|cfo|chief\s+operating|"
        r"coo|chief\s+technology|cto|president|chairman|director)\b|"
        r"\b(steps?\s+down|resigns?|retires?|departs?)\b.{0,80}"
        r"\b(chief\s+executive|ceo|president|chairman)\b", re.I)),
    ("guidance_update",    re.compile(
        r"\b(revises?\s+guidance|updates?\s+guidance|raises?\s+guidance|"
        r"lowers?\s+guidance|withdraws?\s+guidance|guidance\s+range|"
        r"fiscal\s+year\s+(?:20\d\d\s+)?outlook|full.year\s+outlook|"
        r"second.quarter\s+guidance|preliminary\s+results)\b", re.I)),
    ("earnings_release",   re.compile(
        r"\b(reports?\s+(?:first|second|third|fourth|q[1-4]|fiscal|full.year|"
        r"annual|fourth.quarter|third.quarter|second.quarter|first.quarter)"
        r"[\s\w]*(?:results?|earnings?|revenue|financial|quarter)|"
        r"(?:first|second|third|fourth|q[1-4])\s+quarter\s+(?:20\d\d\s+)?"
        r"(?:results?|earnings?|financial)|"
        r"fiscal\s+(?:20\d\d\s+)?(?:fourth|full.year|annual)\s+"
        r"(?:results?|earnings?|financial))\b", re.I)),
    # RB Unified Restaurant-Tech Graph (2026-07-31): customer-win/renewal/churn
    # detection — same signal_type names as market_source_feeds.py's trade-press
    # classifier, so intelligence_mutation_engine.py maps either source's rows
    # onto the same reconciliation-outcome vocabulary.
    ("vendor_churn_loss",  re.compile(
        r"\b(replac(?:e|es|ed|ing)\s+[\w\s]{1,30}?\s+with|switch(?:es|ed|ing)?\s+(?:to|from|away\s+from)|"
        r"drops?\s+(?:its\s+)?(?:vendor|provider|partner|platform|system)|"
        r"discontinues?\s+(?:its\s+)?use\s+of|ends?\s+(?:its\s+)?relationship\s+with|"
        r"moves?\s+away\s+from|transitions?\s+away\s+from|terminates?\s+(?:its\s+)?agreement\s+with)\b",
        re.I)),
    ("contract_renewal_expansion", re.compile(
        r"\b(renews?|renewed|renewal\s+of|extends?\s+(?:its\s+)?(?:agreement|contract|partnership)|"
        r"expands?\s+(?:its\s+)?(?:rollout|partnership|agreement|deployment)|"
        r"multi.year\s+renewal|extended\s+(?:its\s+)?agreement)\b", re.I)),
    ("provider_win",       re.compile(
        r"\b(selects?|selected|chooses?|chose|names?\s+\w+\s+as\s+(?:its\s+)?(?:exclusive\s+)?"
        r"(?:provider|vendor|partner)|announces?\s+new\s+customer|signs?\s+(?:new\s+)?agreement\s+with|"
        r"named\s+(?:exclusive\s+)?(?:provider|vendor|partner))\b", re.I)),
]

# Fallback for any 8-K that doesn't match a specific pattern
_DEFAULT_SIGNAL = "material_event"

_SIGNAL_PATTERNS_BY_NAME: dict[str, re.Pattern] = dict(_SIGNAL_PATTERNS)


def _classify_signal(title: str, summary: str = "") -> str:
    """Return the most specific signal type for a title+summary pair."""
    text = f"{title} {summary}"
    for sig_type, pattern in _SIGNAL_PATTERNS:
        if pattern.search(text):
            return sig_type
    return _DEFAULT_SIGNAL


# RB-DEFECT-066: EDGAR's browse-edgar getcompany Atom feed only ever gives a
# generic title ("8-K - Current report") -- the title/summary regex path
# above can never classify an earnings 8-K from that feed alone. Item 2.02
# ("Results of Operations and Financial Condition") is the standard EDGAR
# item code for an earnings release, visible on the filing's own index
# page, one further fetch away from the entry's link.
#
# RB-DEFECT-2026-09-30: this used to also treat a bare EX-99.1 exhibit as
# sufficient on its own ("almost always the attached press release").
# Confirmed live false positive: McDonald's filed an Item 7.01 (Regulation
# FD Disclosure) 8-K on 2026-09-23 with an EX-99.1 investor-conference
# exhibit and NO Item 2.02 -- not an earnings release at all, but the
# EX-99.1-alone check classified it as one, which incorrectly set
# last_reported_date and made the Team Portal Earnings Center show
# McDonald's as both "recently reported" (from this false date) and
# "upcoming" (the real report_months estimate, unaware of the false date).
# Item 2.02 is the actual canonical earnings-release item code and is
# required on its own now; EX-99.1 alone is common on plenty of non-
# earnings 8-Ks (investor presentations, unrelated press releases) and is
# no longer sufficient by itself.
_EARNINGS_ITEM_RE = re.compile(
    r"item\s*2\.02|results\s+of\s+operations\s+and\s+financial\s+condition", re.I)


def _classify_8k_via_index(url: str) -> bool:
    """Best-effort: fetch an 8-K filing's index page and look for Item 2.02,
    the canonical earnings-release item code. Returns False (never raises)
    on any fetch failure -- classification then falls back to
    material_event, same as before this existed.
    """
    if not url:
        return False
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "RelationshipBuilder/1.0 rb-monitor@private.local",
                "Accept": "text/html",
            },
        )
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, UnicodeDecodeError):
        return False
    return bool(_EARNINGS_ITEM_RE.search(html))


# ---------------------------------------------------------------------------
# Pain point derivations
# ---------------------------------------------------------------------------

_PAIN_TEMPLATES: dict[str, str] = {
    "strategic_review": (
        "Company exploring strategic alternatives — potential sale, merger, "
        "or go-private. Active M&A process may compress vendor decision timelines."
    ),
    "activist_investor": (
        "Activist investor involvement. Board or management change pressure "
        "creates leadership uncertainty and potential strategy pivot."
    ),
    "leadership_change": (
        "C-suite or board leadership change. New leadership sets new vendor "
        "priorities; existing relationships need requalification."
    ),
    "guidance_update": (
        "Forward guidance revised. Revenue or margin changes affect tech "
        "spend capacity and vendor evaluation cycles."
    ),
    "earnings_release": (
        "Quarterly earnings release. Revenue, margin, and traffic metrics "
        "signal financial health and tech investment capacity."
    ),
    "material_event": (
        "Material event filed with SEC. Review for vendor impact, "
        "leadership changes, or strategic shifts."
    ),
    "provider_win": (
        "Named customer win or new deployment announced. Confirms a live "
        "vendor-customer relationship — candidate for a new graph relationship."
    ),
    "contract_renewal_expansion": (
        "Existing customer relationship renewed, extended, or expanded. "
        "Reinforces an existing graph relationship rather than creating a new one."
    ),
    "vendor_churn_loss": (
        "Customer replaced, dropped, or switched away from a vendor. "
        "The prior vendor relationship should be reviewed for supersession."
    ),
}


def _build_pain_point(sig_type: str, title: str) -> str:
    base = _PAIN_TEMPLATES.get(sig_type, _PAIN_TEMPLATES["material_event"])
    return f"{title[:120]}. {base}"


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------

# RB-2026-09-08: earnings_calendar.yaml's company names come from SEC/EDGAR
# filings -- real legal-entity names ("Starbucks Corp", "Domino's Pizza Inc",
# "BJ's Restaurants, Inc.") -- while ecosystem_intelligence.json's brand/
# vendor entities use the plainer operating name ("Starbucks", "Domino's",
# "BJ's Restaurants"). Confirmed live against the real 41-distinct-company
# corpus: exact match alone resolved only 11/41; stripping this small,
# conservative, purely-legal-boilerplate suffix list (never touching
# category/product words -- "Domino's Pizza Inc" still correctly does NOT
# resolve, since "Pizza" isn't boilerplate) raised that to 28/41 with zero
# false positives observed. Applied only here, as a fallback after an exact
# match fails -- NOT folded into the shared _resolve_entity_id_any_type(),
# which stays exact-match-only for its other two real callers
# (entity_alerts_cache.json, technomic_watchlist_promoted.json), where this
# legal-suffix pattern doesn't apply and hasn't been needed.
_CORPORATE_SUFFIX_RE = re.compile(
    r",?\s+(Inc\.?|Incorporated|Corp\.?|Corporation|Co\.?|Company|"
    r"Holdings?,?\s*Inc\.?|Holdings?|Group|LLC|L\.L\.C\.?|Ltd\.?|Limited)\s*$",
    re.IGNORECASE,
)


def _strip_corporate_suffix(name: str) -> str:
    prev = None
    while prev != name:
        prev = name
        name = _CORPORATE_SUFFIX_RE.sub("", name).strip()
    return name


@functools.lru_cache(maxsize=1)
def _graph_for_entity_resolution() -> dict | None:
    """Reads ecosystem_intelligence.json once per process (this script runs
    as a one-shot CLI invocation, not a long-lived server, so process-
    lifetime caching is correct here, not a staleness risk) rather than
    once per row. lru_cache (not a bare module global) so tests can reset
    it explicitly via .cache_clear() -- a bare global would let one test's
    real-data read silently leak into a later test's isolated fixture,
    depending on file execution order within the same pytest process; a
    real risk class this session has hit before against live data."""
    if _ei is None:
        return None
    try:
        return _ei._read_graph()
    except Exception:  # noqa: BLE001 — resolution is best-effort, never blocks a real row
        return None


def _resolve_company_entity_id(name: str) -> str | None:
    """RB-2026-09-08, 3-store unification Phase 3: canonicalize
    market_signals_earnings.jsonl's raw "company" string against a real
    entity_id. Tries an exact match first; if that fails, retries once
    with common corporate-legal suffixes stripped (see
    _CORPORATE_SUFFIX_RE above). Never guesses beyond that -- an
    unresolvable or genuinely ambiguous name (e.g. the real "NCR Voyix"
    case) returns None."""
    graph = _graph_for_entity_resolution()
    if graph is None:
        return None
    eid = _ei._resolve_entity_id_any_type(name, graph)
    if eid:
        return eid
    stripped = _strip_corporate_suffix(name)
    if stripped != name:
        return _ei._resolve_entity_id_any_type(stripped, graph)
    return None


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _build_row(
    company: dict,
    title: str,
    url: str,
    published_at: str,
    source_name: str,
    source_type: str,
    sig_type: str,
    summary: str = "",
) -> dict:
    name = company["name"]
    relevance = company.get("strategic_relevance", "medium")
    timing = (
        "today" if sig_type in (
            "strategic_review", "activist_investor", "leadership_change", "vendor_churn_loss",
        ) else "this_week"
    )
    return {
        "title": title,
        "url": url,
        "source_name": source_name,
        "source_type": source_type,
        "source_quality": "strong",   # primary-source IR / EDGAR = strong
        "published_at": published_at[:10] if published_at else "",
        "company": name,
        "entity_id": _resolve_company_entity_id(name),
        "entity_search_terms": identity_terms(company),
        "entity_search_queries": search_queries(company, "restaurant technology"),
        "side": company.get("side", "vendor_supply"),
        "category": company.get("category", "pos"),
        "signal_type": sig_type,
        "pain_point_or_priority": _build_pain_point(sig_type, title),
        "strategic_relevance": relevance,
        "affected_relationships_or_threads": [],
        "macro_force": "none",
        "restaurant_operator_impact": "",
        "restaurant_tech_vendor_implication": (
            f"{name} {sig_type.replace('_', ' ')} — monitor for vendor relationship impact."
        ),
        "second_order_impact": summary[:300] if summary else "",
        "relationship_opportunity": "",
        "why_this_matters_to_todd": (
            f"{name} is a tracked entity in RB. {sig_type.replace('_', ' ').title()} "
            f"signals affect network positioning and deal timing."
        ),
        "timing_priority": timing,
        "recommended_action": (
            "act_today" if sig_type in ("strategic_review", "activist_investor", "vendor_churn_loss")
            else "monitor"
        ),
        "confidence": "high",
        "_source_hash": _url_hash(url),
        "_ingested_at": datetime.now(timezone.utc).isoformat(),
        "_earnings_monitor": True,
    }


# ---------------------------------------------------------------------------
# Feed parsers
# ---------------------------------------------------------------------------

def _fetch_xml(url: str, *, retries: int = 1, backoff_seconds: float = 1.5) -> ET.Element | None:
    """Fetch and parse an RSS/Atom XML feed. Returns root element or None.

    SEC EDGAR requires a descriptive User-Agent per their access policy:
    https://www.sec.gov/os/accessing-edgar-data
    Format: "ToolName/Version contact@email.com"

    RB-DEFECT-2026-08-19: this had zero retry logic, and the ~34-company
    scan fires up to ~100 EDGAR/IR requests back to back with no pacing.
    Confirmed live: a single scan run logged 33/33 EDGAR requests as
    "Could not fetch" (ir_rss_unresolved/edgar-failure reader-facing text
    named 4+ companies as a "coverage gap"), while re-issuing the exact same
    requests seconds apart individually succeeded -- a transient timeout/
    connection hiccup under burst load, not a real per-company gap. Retry a
    plain timeout or connection error once before giving up; a genuine
    malformed-response (ET.ParseError) or non-network OSError is not
    retried since a second attempt won't fix bad content.
    """
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    # SEC EDGAR policy: must identify your tool and provide contact
                    "User-Agent": "RelationshipBuilder/1.0 rb-monitor@private.local",
                    "Accept": "application/atom+xml,application/rss+xml,application/xml,text/xml",
                    "Accept-Encoding": "identity",
                },
            )
            with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
                raw = resp.read()
            return ET.fromstring(raw)
        except (urllib.error.URLError, OSError):
            if attempt < retries:
                time.sleep(backoff_seconds * (attempt + 1))
                continue
            return None
        except ET.ParseError:
            return None
    return None


def _edgar_ns(tag: str) -> str:
    """Strip any namespace prefix from an EDGAR Atom tag."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _text(el: ET.Element | None, tag: str, ns: str = "") -> str:
    if el is None:
        return ""
    child = el.find(f"{ns}{tag}") if ns else el.find(tag)
    if child is None:
        # Try stripping namespace
        for c in el:
            if _edgar_ns(c.tag) == tag:
                return (c.text or "").strip()
        return ""
    return (child.text or "").strip()


def _parse_edgar_feed(
    root: ET.Element, company: dict, lookback_days: int, *, source_type: str = "sec_edgar_8k",
) -> list[dict]:
    """Parse EDGAR Atom feed and return market_signals-compatible rows.

    source_type distinguishes which EDGAR filing-index feed this came from
    (8-K vs 10-K vs 10-Q) -- the Atom shape and parsing logic are identical
    across all three, only the URL's `type=` query param differs.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = []

    # EDGAR Atom: entries are <entry> elements under root
    # Namespaced: {http://www.w3.org/2005/Atom}entry
    entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    if not entries:
        # Try without namespace
        entries = root.findall(".//entry")

    for entry in entries:
        title = (_text(entry, "title", "{http://www.w3.org/2005/Atom}") or
                 _text(entry, "title")).strip()
        # EDGAR link: <link href="..."/> — NOTE: use `is not None` not `or`
        # because ET.Element with no children evaluates to False (self-closing tags).
        link_el = entry.find("{http://www.w3.org/2005/Atom}link")
        if link_el is None:
            link_el = entry.find("link")
        url = ""
        if link_el is not None:
            url = link_el.get("href") or link_el.text or ""
        url = url.strip()

        updated = (_text(entry, "updated", "{http://www.w3.org/2005/Atom}") or
                   _text(entry, "updated") or
                   _text(entry, "published", "{http://www.w3.org/2005/Atom}") or
                   _text(entry, "pubDate")).strip()

        summary = (_text(entry, "summary", "{http://www.w3.org/2005/Atom}") or
                   _text(entry, "description")).strip()

        # Recency filter
        if updated:
            try:
                pub_dt = datetime.fromisoformat(
                    updated.replace("Z", "+00:00").replace(" ", "T")
                )
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
            except ValueError:
                pass  # keep if we can't parse the date

        if not title or not url:
            continue

        sig_type = _classify_signal(title, summary)
        # RB-DEFECT-066: browse-edgar's Atom title is a generic "8-K -
        # Current report" for every 8-K, so an earnings 8-K falls through to
        # material_event above. One further fetch of the filing's own index
        # page (Item 2.02 / EX-99.1 check) resolves the ambiguity. Only
        # worth the extra request for 8-Ks that are still unclassified --
        # 10-K/10-Q feeds and anything the title/summary regex already
        # matched skip this.
        if sig_type == _DEFAULT_SIGNAL and source_type == "sec_edgar_8k":
            if _classify_8k_via_index(url):
                sig_type = "earnings_release"
        rows.append(_build_row(
            company=company,
            title=title,
            url=url,
            published_at=updated[:10] if updated else "",
            source_name=f"SEC EDGAR ({company['ticker']})",
            source_type=source_type,
            sig_type=sig_type,
            summary=summary,
        ))
    return rows


def _parse_rss_feed(root: ET.Element, company: dict, lookback_days: int) -> list[dict]:
    """Parse standard RSS 2.0 feed and return market_signals-compatible rows."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = []

    # Handle both RSS 2.0 (<channel><item>) and Atom (<entry>)
    items = (root.findall(".//item") or
             root.findall(".//{http://www.w3.org/2005/Atom}entry") or
             root.findall(".//entry"))

    for item in items:
        title = (_text(item, "title") or
                 _text(item, "title", "{http://www.w3.org/2005/Atom}")).strip()

        # URL: <link> or <guid> in RSS; <link href=""> in Atom
        # NOTE: use `is not None` not `or` for ET.Element to avoid self-closing tag gotcha.
        url = (_text(item, "link") or "").strip()
        if not url:
            link_el = item.find("{http://www.w3.org/2005/Atom}link")
            if link_el is not None:
                url = link_el.get("href") or ""
        if not url:
            link_el = item.find("link")
            if link_el is not None:
                url = link_el.get("href") or link_el.text or ""
        if not url:
            url = (_text(item, "guid") or "").strip()
        url = url.strip()

        pub = (_text(item, "pubDate") or
               _text(item, "published", "{http://www.w3.org/2005/Atom}") or
               _text(item, "updated", "{http://www.w3.org/2005/Atom}")).strip()

        description = (_text(item, "description") or
                       _text(item, "summary", "{http://www.w3.org/2005/Atom}")).strip()

        # Recency filter
        if pub:
            try:
                pub_dt = datetime.fromisoformat(
                    pub.replace("Z", "+00:00").replace(" ", "T")
                )
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
            except ValueError:
                # RFC 2822 date format (common in RSS): "Mon, 27 May 2026 12:00:00 GMT"
                import email.utils
                try:
                    pub_dt = datetime(*email.utils.parsedate(pub)[:6],
                                     tzinfo=timezone.utc)
                    if pub_dt < cutoff:
                        continue
                except (TypeError, ValueError):
                    pass  # keep if can't parse

        if not title or not url:
            continue

        sig_type = _classify_signal(title, description)
        rows.append(_build_row(
            company=company,
            title=title,
            url=url,
            published_at=pub[:10] if pub else "",
            source_name=f"{company['name']} IR",
            source_type="public_company_primary",
            sig_type=sig_type,
            summary=description,
        ))
    return rows


# ---------------------------------------------------------------------------
# Health tracker
# ---------------------------------------------------------------------------

class _Health:
    def __init__(self):
        self._records: list[dict] = []

    def record(self, company: str, feed_type: str, status: str,
               row_count: int = 0, error: str = "") -> None:
        self._records.append({
            "company": company,
            "feed_type": feed_type,
            "status": status,
            "row_count": row_count,
            "error": error,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        })

    def to_dict(self) -> dict:
        # RB-9.63: ir_rss failures are supplementary (IR press release pages).
        # EDGAR 8-K is the authoritative source. Only count edgar_8k failures as
        # real failures — ir_rss failures are expected when IR sites block scrapers.
        edgar_records = [r for r in self._records if r["feed_type"] == "edgar_8k"]

        # RB-DEFECT-066: a company whose IR RSS failed AND whose page
        # fallback also failed (or had nowhere to fall back to) is a genuine
        # blind spot -- SEC EDGAR being the "authoritative" source doesn't
        # help when the same filing's generic title also failed
        # classification. Surface these explicitly so callers (daily_brief's
        # "no earnings events" gate) can tell "checked, found nothing" apart
        # from "couldn't check."
        ir_rss_status: dict[str, str] = {}
        fallback_status: dict[str, str] = {}
        for r in self._records:
            if r["feed_type"] == "ir_rss":
                ir_rss_status[r["company"]] = r["status"]
            elif r["feed_type"] == "ir_page_fallback":
                fallback_status[r["company"]] = r["status"]
        unresolved_ir_failures = sorted(
            company for company, status in ir_rss_status.items()
            if status == "failed" and fallback_status.get(company) != "ok"
        )

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sources": self._records,
            "ok_count": sum(1 for r in self._records if r["status"] == "ok"),
            "failed_count": sum(1 for r in edgar_records if r["status"] in ("failed", "empty")),
            "ir_rss_failed_count": sum(1 for r in self._records
                                       if r["feed_type"] == "ir_rss" and r["status"] == "failed"),
            "skipped_count": sum(1 for r in self._records if r["status"].startswith("skipped")),
            "ir_rss_unresolved_companies": unresolved_ir_failures,
        }

    def print_summary(self) -> None:
        icons = {"ok": "✓", "failed": "✗", "empty": "○",
                 "skipped_no_url": "–", "skipped_light_tier": "◦", "fixture": "◆"}
        for r in self._records:
            icon = icons.get(r["status"], "?")
            note = f" ({r['error']})" if r["error"] else f" ({r['row_count']} rows)"
            print(f"  {icon} {r['company']} [{r['feed_type']}]: {r['status']}{note}")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def _load_calendar() -> list[dict]:
    """Load earnings_calendar.yaml. Returns list of company dicts."""
    if not EARNINGS_CALENDAR_PATH.exists():
        return []
    text = EARNINGS_CALENDAR_PATH.read_text(encoding="utf-8")
    if _YAML_AVAILABLE:
        try:
            data = _yaml.safe_load(text)
            companies = data.get("companies") or []
            entities = load_entities()
            for company in companies:
                entity = find_entity(company.get("name") or "", entities)
                if entity:
                    company["aliases"] = list(entity.get("aliases") or [])
                    company["ticker"] = entity.get("ticker")
                company["search_terms"] = identity_terms(company)
                company["search_queries"] = search_queries(
                    company, "earnings OR investor relations"
                )
            return companies
        except Exception:
            pass
    return []


def _dedupe(rows: list[dict]) -> list[dict]:
    """Remove duplicate rows by _source_hash."""
    seen: set[str] = set()
    out: list[dict] = []
    for row in rows:
        h = row.get("_source_hash") or _url_hash(row.get("url") or row.get("title") or "")
        if h not in seen:
            seen.add(h)
            out.append(row)
    return out


def _load_existing_hashes() -> set[str]:
    """Read existing output JSONL to avoid re-emitting known rows."""
    if not OUTPUT_PATH.exists():
        return set()
    hashes: set[str] = set()
    for line in OUTPUT_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            h = row.get("_source_hash", "")
            if h:
                hashes.add(h)
        except json.JSONDecodeError:
            continue
    return hashes


def fetch_company(
    company: dict,
    *,
    live: bool = True,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    health: _Health,
    tier: str = "full",
) -> list[dict]:
    """Fetch EDGAR 8-K + IR RSS for a single company. Returns list of rows.

    tier="light" does EDGAR-8K only (skips the separate IR-RSS request) with
    a shorter lookback -- the cheap daily check for the whole tracked
    universe. tier="full" is the unchanged full fetch, reserved for
    watch_priority companies and anyone near their next report date (see
    _fetch_tier()).
    """
    rows: list[dict] = []
    name = company["name"]
    if tier == "light":
        lookback_days = min(lookback_days, _LIGHT_LOOKBACK_DAYS)

    # ── EDGAR 8-K ────────────────────────────────────────────────────────────
    cik = company.get("edgar_cik")
    if cik:
        edgar_url = _EDGAR_8K_URL.format(cik=cik.lstrip("0") or "0")
        if live:
            root = _fetch_xml(edgar_url)
            if root is not None:
                edgar_rows = _parse_edgar_feed(root, company, lookback_days)
                rows.extend(edgar_rows)
                health.record(name, "edgar_8k", "ok", len(edgar_rows))
            else:
                health.record(name, "edgar_8k", "failed", error=f"Could not fetch {edgar_url}")
        else:
            health.record(name, "edgar_8k", "skipped_no_url")
    else:
        health.record(name, "edgar_8k", "skipped_no_url", error="No edgar_cik configured")

    # ── EDGAR 10-K / 10-Q filing index ────────────────────────────────────────
    # Quarterly/annual, not event-driven like 8-Ks -- only worth checking at
    # full tier, same reasoning as skipping IR-RSS at light tier below.
    if tier == "light":
        health.record(name, "edgar_10k_10q", "skipped_light_tier")
    elif cik:
        for feed_type, url_template, source_type in (
            ("edgar_10k", _EDGAR_10K_URL, "sec_edgar_10k"),
            ("edgar_10q", _EDGAR_10Q_URL, "sec_edgar_10q"),
        ):
            filing_url = url_template.format(cik=cik.lstrip("0") or "0")
            if live:
                root = _fetch_xml(filing_url)
                if root is not None:
                    filing_rows = _parse_edgar_feed(root, company, lookback_days, source_type=source_type)
                    rows.extend(filing_rows)
                    health.record(name, feed_type, "ok", len(filing_rows))
                else:
                    health.record(name, feed_type, "failed", error=f"Could not fetch {filing_url}")
            else:
                health.record(name, feed_type, "skipped_no_url")
    else:
        health.record(name, "edgar_10k_10q", "skipped_no_url", error="No edgar_cik configured")

    # ── IR press release RSS ──────────────────────────────────────────────────
    # Light tier skips this entirely -- EDGAR 8-K is the authoritative source
    # (see module docstring) and a second network request per company isn't
    # worth it for the daily-cheap-check pass across the whole universe.
    if tier == "light":
        health.record(name, "ir_rss", "skipped_light_tier")
    else:
        ir_url = company.get("ir_rss_url")
        ir_failed = False
        if ir_url:
            if live:
                root = _fetch_xml(ir_url)
                if root is not None:
                    ir_rows = _parse_rss_feed(root, company, lookback_days)
                    rows.extend(ir_rows)
                    health.record(name, "ir_rss", "ok", len(ir_rows))
                else:
                    health.record(name, "ir_rss", "failed", error=f"Could not fetch {ir_url}")
                    ir_failed = True
            else:
                health.record(name, "ir_rss", "skipped_no_url")
        else:
            health.record(name, "ir_rss", "skipped_no_url", error="No ir_rss_url configured")

        # RB-DEFECT-066: PAR's IR RSS endpoint failed with no fallback, and
        # SEC classification alone wasn't reliable enough to catch the
        # earnings call on its own -- fall back to the plain IR page when the
        # feed request itself fails. This is a detection net, not a
        # press-release scraper: it only confirms an earnings release exists
        # on the page, via the same regex _classify_signal already uses.
        if ir_failed and live:
            ir_page_url = company.get("ir_page_url")
            if ir_page_url:
                fallback_rows = _fetch_ir_page_fallback(company, ir_page_url, lookback_days)
                if fallback_rows:
                    rows.extend(fallback_rows)
                    health.record(name, "ir_page_fallback", "ok", len(fallback_rows))
                else:
                    health.record(
                        name, "ir_page_fallback", "failed",
                        error=f"No earnings-release language found at {ir_page_url}",
                    )
            else:
                health.record(
                    name, "ir_page_fallback", "skipped_no_url",
                    error="No ir_page_url configured for fallback",
                )

    return rows


def _fetch_ir_page_fallback(company: dict, url: str, lookback_days: int) -> list[dict]:
    """Best-effort fallback when a company's IR RSS feed fails to fetch
    (RB-DEFECT-066): fetch the plain IR page HTML and look for the same
    earnings-release language _classify_signal already matches against
    titles. Emits at most one signal row -- this confirms "an earnings
    release exists on this page" rather than parsing/paginating multiple
    releases, which is out of scope (see defect's non-goals).
    """
    del lookback_days  # not used: the page fallback has no publish-date field to filter on
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "RelationshipBuilder/1.0 rb-monitor@private.local",
                "Accept": "text/html",
            },
        )
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, UnicodeDecodeError):
        return []

    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()

    match = _SIGNAL_PATTERNS_BY_NAME["earnings_release"].search(text)
    if not match:
        return []
    snippet = text[max(0, match.start() - 60): match.end() + 120].strip()
    return [_build_row(
        company=company,
        title=f"{company['name']} investor relations page — earnings release detected",
        url=url,
        published_at=date.today().isoformat(),
        source_name=f"{company['name']} IR (page fallback)",
        source_type="public_company_primary_fallback",
        sig_type="earnings_release",
        summary=snippet,
    )]


def run(
    *,
    live: bool = True,
    watch_only: bool = False,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    save_output: bool = True,
    save_health: bool = False,
    tiered: bool = True,
) -> dict:
    """Full pipeline. Returns summary dict.

    tiered=True (the default for a full run) fetches watch_priority=true and
    near-earnings companies at "full" depth (EDGAR + IR RSS, full lookback)
    and everyone else at "light" depth (EDGAR-8K only, short lookback) --
    see _fetch_tier(). watch_only already restricts to the always-full set,
    so tiering is a no-op in that mode. Pass tiered=False to force every
    company to full depth regardless of tier (e.g. a one-off deep refresh).
    """
    companies = _load_calendar()
    if watch_only:
        companies = [c for c in companies if c.get("watch_priority")]

    today = date.today()
    health = _Health()
    all_rows: list[dict] = []
    existing_hashes = _load_existing_hashes() if save_output else set()
    tier_counts = {"full": 0, "light": 0}

    for company in companies:
        tier = _fetch_tier(company, today) if (tiered and not watch_only) else "full"
        tier_counts[tier] += 1
        rows = fetch_company(company, live=live, lookback_days=lookback_days, health=health, tier=tier)
        all_rows.extend(rows)

    deduped = _dedupe(all_rows)
    new_rows = [r for r in deduped if r.get("_source_hash") not in existing_hashes]

    if save_output and new_rows:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with OUTPUT_PATH.open("a", encoding="utf-8") as fh:
            for row in new_rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    reconciled_dates: list[str] = []
    if save_output:
        try:
            reconciled_dates = _reconcile_confirmed_dates(companies)
        except Exception:  # noqa: BLE001
            reconciled_dates = []

    health_dict = health.to_dict()
    if save_health:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        HEALTH_PATH.write_text(json.dumps(health_dict, indent=2), encoding="utf-8")

    return {
        "companies_checked": len(companies),
        "tier_counts": tier_counts,
        "reconciled_report_dates": reconciled_dates,
        "total_rows": len(all_rows),
        "deduped_rows": len(deduped),
        "new_rows": len(new_rows),
        "output_path": str(OUTPUT_PATH),
        "health": health_dict,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Staleness check
# ---------------------------------------------------------------------------

def is_stale(max_age_hours: int = 25) -> bool:
    """True if output JSONL is older than max_age_hours or does not exist."""
    if not OUTPUT_PATH.exists():
        return True
    try:
        mtime = datetime.fromtimestamp(OUTPUT_PATH.stat().st_mtime, tz=timezone.utc)
        return (datetime.now(timezone.utc) - mtime).total_seconds() > max_age_hours * 3600
    except OSError:
        return True


def scan_health_summary(today: date | None = None) -> dict:
    """RB-DEFECT-066: report whether today's earnings scan actually
    completed, for daily_brief.py's "no earnings events" gate.

    A daily brief that rendered before the earnings monitor finished (the
    scan_only/brief_only race that missed PAR's Aug 6 call) must not assert
    "no earnings events" with the same confidence as a brief built from a
    completed scan -- this distinguishes "checked, found nothing" from
    "didn't finish checking."
    """
    if today is None:
        today = date.today()
    if not HEALTH_PATH.exists():
        return {
            "scan_complete": False,
            "generated_at": None,
            "reason": "no_health_cache",
            "unresolved_ir_failures": [],
        }
    try:
        health = json.loads(HEALTH_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "scan_complete": False,
            "generated_at": None,
            "reason": "unreadable_health_cache",
            "unresolved_ir_failures": [],
        }
    generated_at = health.get("generated_at") or ""
    scan_complete = generated_at[:10] == today.isoformat()
    return {
        "scan_complete": scan_complete,
        "generated_at": generated_at,
        "reason": "ok" if scan_complete else "stale_health_cache",
        "unresolved_ir_failures": health.get("ir_rss_unresolved_companies") or [],
        "edgar_failed_count": health.get("failed_count", 0),
    }


def get_recent_signals(
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    companies: list[str] | None = None,
    signal_types: list[str] | None = None,
) -> list[dict]:
    """Read the output JSONL and return rows matching filters."""
    if not OUTPUT_PATH.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows: list[dict] = []
    for line in OUTPUT_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if companies and row.get("company") not in companies:
            continue
        if signal_types and row.get("signal_type") not in signal_types:
            continue
        pub = row.get("published_at") or row.get("_ingested_at") or ""
        if pub:
            try:
                pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
            except ValueError:
                pass
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Confidence-Based Auto-Recording, Phase 4 (2026-09-25): bridge today's
# captured signals into brand_profile_common.add_signal() so a fact RB
# already gathered (an earnings release, a leadership change, a strategic
# review) actually reaches the mechanical brand brief instead of sitting
# only in this JSONL. Operator-side rows only (side == "operator_demand") --
# vendor-side (side == "vendor_supply") rows belong to the competitor
# intelligence store instead (evidence.jsonl via competitor_intelligence.py),
# a structurally different write path not yet wired to this bridge.
# add_signal()'s own (type, value) dedupe makes re-running this against
# rows already bridged a no-op, so a 1-day lookback each morning is safe.
_SIGNAL_TYPE_TO_BPC_SIGNAL_TYPE: dict[str, str] = {
    "earnings_release": "financial_health",
    "guidance_update": "financial_health",
    "leadership_change": "leadership_change",
    "strategic_review": "ownership_change",
    "activist_investor": "challenge_or_headwind",
    "vendor_churn_loss": "competitive_positioning",
    "provider_win": "competitive_positioning",
    "contract_renewal_expansion": "expansion_or_contraction",
    "material_event": "other",
}


def bridge_signals_to_brand_profiles(*, lookback_days: int = 1, dry_run: bool = False) -> dict:
    """Reads recently-captured operator-side rows from this module's own
    output log and appends each as a brand_profile_common signal. An
    earnings release / 8-K / guidance update is a primary-source,
    company-originated fact -- calibrated at confidence_calibration.py's
    top band. Never overwrites anything already on a profile; each call is
    purely additive, matching every other consumer of add_signal()."""
    import brand_profile_common as _bpc
    import confidence_calibration as _cc

    rows = [
        r for r in get_recent_signals(lookback_days=lookback_days)
        if (r.get("side") or "vendor_supply") == "operator_demand"
    ]
    graph = _ei._read_graph() if _ei is not None else None
    # Cache profiles per entity_id across the loop (not just re-fetch each
    # row) so two rows for the same brand within one call see each other's
    # additions -- otherwise a same-batch (type, value) duplicate would be
    # double-counted as "bridged" in dry_run mode, since dry_run never
    # writes to disk between rows for the real dedupe check to see.
    profiles: dict[str, dict] = {}
    not_brand_ids: set[str] = set()
    bridged = skipped_no_entity = skipped_not_brand = skipped_no_title = 0
    for row in rows:
        entity_id = row.get("entity_id") or _resolve_company_entity_id(row.get("company", ""))
        if not entity_id:
            skipped_no_entity += 1
            continue
        title = (row.get("title") or "").strip()
        if not title:
            skipped_no_title += 1
            continue
        if entity_id in not_brand_ids:
            skipped_not_brand += 1
            continue
        if entity_id not in profiles:
            try:
                profiles[entity_id] = _bpc.get_profile(entity_id, graph=graph)
            except _bpc.NotFoundError:
                not_brand_ids.add(entity_id)
                skipped_not_brand += 1
                continue
        profile = profiles[entity_id]
        sig_type = row.get("signal_type") or _DEFAULT_SIGNAL
        bpc_type = _SIGNAL_TYPE_TO_BPC_SIGNAL_TYPE.get(sig_type, "other")
        level, _score = _cc.score_for_source(row.get("source_type") or "earnings_release")
        before = len(profile.get("recent_signals") or [])
        _bpc.add_signal(
            profile,
            value=title,
            signal_type=bpc_type,
            status="reported",
            confidence=level,
            as_of=row.get("published_at") or None,
            last_reviewed_by="system:earnings_monitor_bridge",
            source_url=row.get("url") or None,
        )
        if len(profile.get("recent_signals") or []) > before:
            bridged += 1
    if not dry_run:
        for entity_id, profile in profiles.items():
            _bpc.save_profile(entity_id, profile)
    return {
        "ok": True,
        "rows_scanned": len(rows),
        "bridged": bridged,
        "skipped_no_entity": skipped_no_entity,
        "skipped_not_brand": skipped_not_brand,
        "skipped_no_title": skipped_no_title,
        "dry_run": dry_run,
    }


# ---------------------------------------------------------------------------
# Watch list mutation (RB 9.29)
# ---------------------------------------------------------------------------

def _snapshot_calendar() -> Path:
    """Write a timestamped backup of earnings_calendar.yaml before mutation.

    Returns the path written. Safe to call even if the calendar does not
    exist yet (writes an empty file so the snapshot path is always valid).
    """
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snap_path = SNAPSHOTS_DIR / f"earnings_calendar.pre-{ts}.yaml"
    if EARNINGS_CALENDAR_PATH.exists():
        snap_path.write_text(
            EARNINGS_CALENDAR_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
    else:
        snap_path.write_text("# snapshot: calendar did not exist\n", encoding="utf-8")
    return snap_path


_CALENDAR_HEADER = """\
# earnings_calendar.yaml — Public company registry for RB earnings monitoring.
#
# Managed by earnings_monitor.py. Do not hand-edit the companies list while
# the pipeline is running. Snapshots are written to system/_snapshots/ before
# each mutation.
#
# Fields: name, ticker, edgar_cik, ir_rss_url, ir_page_url, side, category,
#         report_months, strategic_relevance, watch_priority, notes,
#         added_by, added_at (auto-set on dynamic entries).
#         last_reported_date (auto-set by _reconcile_confirmed_dates once a
#         real earnings_release signal lands -- supersedes the report_months
#         estimate for that cycle; see RB-DEFECT-066).
#         ir_page_url is the plain investor-relations page, used as a
#         fallback source only when ir_rss_url fails to fetch.
"""

_META_KEYS = frozenset({"_added_by", "_added_at", "_watch_added_reason"})


def _clean_entry(co: dict) -> dict:
    """Strip internal metadata keys and promote to top-level readable fields."""
    out = {k: v for k, v in co.items() if k not in _META_KEYS}
    if "_added_by" in co:
        out.setdefault("added_by", co["_added_by"])
    if "_added_at" in co:
        out.setdefault("added_at", co["_added_at"])
    return out


def _rewrite_calendar(companies: list[dict]) -> None:
    """Rewrite earnings_calendar.yaml from a list of company dicts.

    Preserves a human-readable header comment. Strips internal _ metadata
    keys from output (they are only used in memory / return values).
    """
    if not _YAML_AVAILABLE:
        raise RuntimeError("PyYAML is required to rewrite earnings_calendar.yaml")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    doc = {
        "version": 1,
        "updated_at": now,
        "companies": [_clean_entry(co) for co in companies],
    }
    text = _CALENDAR_HEADER + "\n" + _yaml.dump(
        doc, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    EARNINGS_CALENDAR_PATH.write_text(text, encoding="utf-8")


def _reconcile_confirmed_dates(
    companies: list[dict], lookback_days: int = 14,
) -> list[str]:
    """RB-DEFECT-066: once a real earnings_release signal exists for a
    company, record the confirmed report date on its calendar entry
    (last_reported_date) so estimated dates stop colliding with a cycle that
    has already happened -- PAR's calendar kept estimating "~Aug 8" after it
    had already reported on Aug 6.

    Reads today's freshly-written output JSONL (this runs after fetch), not
    a separate network call. Safe to call every cycle: no-ops when nothing
    changed. Returns the list of company names whose entry was updated.
    """
    if not _YAML_AVAILABLE:
        return []

    signals = get_recent_signals(lookback_days=lookback_days, signal_types=["earnings_release"])
    latest_by_company: dict[str, str] = {}
    for row in signals:
        name = row.get("company") or ""
        pub = row.get("published_at") or ""
        if not name or not pub:
            continue
        if pub > latest_by_company.get(name, ""):
            latest_by_company[name] = pub

    if not latest_by_company:
        return []

    updated: list[str] = []
    for co in companies:
        confirmed = latest_by_company.get(co.get("name") or "")
        if confirmed and co.get("last_reported_date") != confirmed:
            co["last_reported_date"] = confirmed
            updated.append(co["name"])

    if updated:
        _snapshot_calendar()
        _rewrite_calendar(companies)
    return updated


def reclassify_stale_material_events(
    *, companies: list[str] | None = None, dry_run: bool = False,
) -> dict:
    """RB-DEFECT-066 backfill: rows ingested into market_signals_earnings.jsonl
    *before* _classify_8k_via_index existed are stuck with their original
    classification forever -- the pipeline is append-only and dedupes by URL
    hash, so a filing already on disk is never re-fetched or reclassified,
    no matter how much smarter later runs get. Investigating the PAR miss
    found the same generic-title 8-K bug had already silently swallowed
    Starbucks' 2026-07-29 and Domino's' 2026-07-20 earnings releases (and
    Fiserv's 2026-08-06 one, ingested just hours before this classifier
    shipped) -- confirmed live against SEC EDGAR (Item 2.02 + EX-99.1
    present on all three index pages).

    Re-runs the index-page check against every material_event-tagged 8-K
    row (optionally scoped to `companies`, a list of calendar entity
    names) and rewrites in place any that turn out to be earnings releases.
    Writes a timestamped backup of the whole JSONL to system/_snapshots/
    before any write. Also re-runs confirmed-date reconciliation afterward
    so the calendar reflects the corrected history immediately rather than
    waiting for tomorrow's cycle.

    dry_run=True checks and reports without writing anything.
    """
    if not OUTPUT_PATH.exists():
        return {"ok": True, "checked": 0, "reclassified": [], "dry_run": dry_run}

    raw_lines = OUTPUT_PATH.read_text(encoding="utf-8").splitlines()
    entries: list[tuple[str, dict | None]] = []
    for raw in raw_lines:
        stripped = raw.strip()
        if not stripped:
            entries.append((raw, None))
            continue
        try:
            entries.append((raw, json.loads(stripped)))
        except json.JSONDecodeError:
            entries.append((raw, None))

    checked = 0
    reclassified: list[dict] = []
    for i, (raw, row) in enumerate(entries):
        if row is None:
            continue
        if row.get("signal_type") != "material_event":
            continue
        if row.get("source_type") != "sec_edgar_8k":
            continue
        company_name = row.get("company") or ""
        if companies is not None and company_name not in companies:
            continue
        url = row.get("url") or ""
        if not url:
            continue
        checked += 1

        if _classify_8k_via_index(url):
            title = row.get("title") or ""
            row["signal_type"] = "earnings_release"
            row["pain_point_or_priority"] = _build_pain_point("earnings_release", title)
            row["restaurant_tech_vendor_implication"] = (
                f"{company_name} earnings release — monitor for vendor relationship impact."
            )
            row["why_this_matters_to_todd"] = (
                f"{company_name} is a tracked entity in RB. Earnings Release "
                f"signals affect network positioning and deal timing."
            )
            row["recommended_action"] = "monitor"
            row["_reclassified_by"] = "reclassify_stale_material_events"
            row["_reclassified_at"] = datetime.now(timezone.utc).isoformat()
            entries[i] = (json.dumps(row, ensure_ascii=False), row)
            reclassified.append({
                "company": company_name,
                "url": url,
                "published_at": row.get("published_at"),
                "title": title,
            })

    if reclassified and not dry_run:
        SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = SNAPSHOTS_DIR / f"market_signals_earnings.pre-reclassify-{ts}.jsonl"
        backup_path.write_text(
            "\n".join(raw_lines) + ("\n" if raw_lines else ""), encoding="utf-8"
        )

        new_lines = [raw for raw, _ in entries]
        OUTPUT_PATH.write_text(
            "\n".join(new_lines) + ("\n" if new_lines else ""), encoding="utf-8"
        )

        try:
            calendar_companies = _load_calendar()
            _reconcile_confirmed_dates(calendar_companies)
        except Exception:  # noqa: BLE001
            pass

    return {
        "ok": True,
        "checked": checked,
        "reclassified": reclassified,
        "reclassified_count": len(reclassified),
        "dry_run": dry_run,
    }


def backfill_entity_ids_in_market_signals(*, dry_run: bool = False) -> dict:
    """RB-2026-09-08, 3-store unification Phase 3 one-time backfill.
    market_signals_earnings.jsonl is append-only and this pipeline dedupes
    by URL hash, so rows written before entity_id resolution existed are
    stuck without it forever unless backfilled. Resolves each of the
    file's distinct "company" names ONCE (not per row -- 717 real rows but
    only 41 distinct companies as of this pass) and stamps every matching
    row, preserving every other field exactly. Same snapshot-before-write
    discipline as reclassify_stale_material_events() above. Safe to re-run
    (a no-op for rows that already have entity_id). Never guesses -- an
    unresolvable or ambiguous name gets entity_id: None, same as
    _build_row()'s own write path."""
    if not OUTPUT_PATH.exists():
        return {"ok": True, "checked": 0, "updated": [], "unresolved": [], "dry_run": dry_run}

    raw_lines = OUTPUT_PATH.read_text(encoding="utf-8").splitlines()
    entries: list[tuple[str, dict | None]] = []
    for raw in raw_lines:
        stripped = raw.strip()
        if not stripped:
            entries.append((raw, None))
            continue
        try:
            entries.append((raw, json.loads(stripped)))
        except json.JSONDecodeError:
            entries.append((raw, None))

    name_to_entity_id: dict[str, str | None] = {}
    checked = 0
    updated_companies: set[str] = set()
    unresolved_companies: set[str] = set()
    for i, (raw, row) in enumerate(entries):
        if row is None or "entity_id" in row:
            continue
        checked += 1
        name = row.get("company") or ""
        if name not in name_to_entity_id:
            name_to_entity_id[name] = _resolve_company_entity_id(name)
        eid = name_to_entity_id[name]
        row["entity_id"] = eid
        entries[i] = (json.dumps(row, ensure_ascii=False), row)
        (updated_companies if eid else unresolved_companies).add(name)

    if checked and not dry_run:
        SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = SNAPSHOTS_DIR / f"market_signals_earnings.pre-entity-id-backfill-{ts}.jsonl"
        backup_path.write_text("\n".join(raw_lines) + ("\n" if raw_lines else ""), encoding="utf-8")

        new_lines = [raw for raw, _ in entries]
        OUTPUT_PATH.write_text("\n".join(new_lines) + ("\n" if new_lines else ""), encoding="utf-8")

    return {
        "ok": True, "checked": checked,
        "updated": sorted(updated_companies), "unresolved": sorted(unresolved_companies),
        "dry_run": dry_run,
    }


def add_company(
    name: str,
    *,
    ticker: str | None = None,
    edgar_cik: str | None = None,
    ir_rss_url: str | None = None,
    side: str = "vendor_supply",
    category: str = "pos",
    strategic_relevance: str = "medium",
    watch_priority: bool = True,
    reason: str = "",
    added_by: str = "user",
) -> dict:
    """Add a company to earnings_calendar.yaml.

    If the company already exists (by name or ticker), upgrades it to
    watch_priority=true when watch_priority=True is requested and the
    existing entry is not already watching. Snapshots before any write.

    Returns a result dict with keys:
      ok (bool), action (str), company (str), snapshot (str|None)
    """
    if not _YAML_AVAILABLE:
        return {"ok": False, "error": "PyYAML not available; cannot mutate calendar"}

    companies = _load_calendar()
    name_lower = name.lower().strip()

    # Check for existing entry
    for co in companies:
        existing_name = co["name"].lower()
        existing_ticker = str(co.get("ticker") or "").lower()
        target_ticker = (ticker or "").lower()
        if existing_name == name_lower or (
            target_ticker and existing_ticker == target_ticker
        ):
            if not co.get("watch_priority") and watch_priority:
                snap = _snapshot_calendar()
                co["watch_priority"] = True
                co["_added_by"] = added_by
                co["_watch_added_reason"] = reason
                _rewrite_calendar(companies)
                return {
                    "ok": True,
                    "action": "upgraded_to_watch",
                    "company": co["name"],
                    "snapshot": str(snap),
                    "added_by": added_by,
                }
            return {
                "ok": True,
                "action": "already_tracked",
                "company": co["name"],
                "watch_priority": bool(co.get("watch_priority")),
                "snapshot": None,
            }

    # New entry
    snap = _snapshot_calendar()
    now_iso = datetime.now(timezone.utc).isoformat()
    new_entry: dict = {
        "name": name,
        "ticker": ticker or name[:4].upper(),
        "edgar_cik": edgar_cik,
        "ir_rss_url": ir_rss_url,
        "side": side,
        "category": category,
        "report_months": [],
        "strategic_relevance": strategic_relevance,
        "watch_priority": watch_priority,
        "notes": reason or f"Added by {added_by}.",
        "_added_by": added_by,
        "_added_at": now_iso,
    }
    companies.append(new_entry)
    _rewrite_calendar(companies)
    return {
        "ok": True,
        "action": "added",
        "company": name,
        "snapshot": str(snap),
        "added_by": added_by,
        "entry": _clean_entry(new_entry),
    }


def remove_company(name_or_ticker: str) -> dict:
    """Remove a company from earnings_calendar.yaml by name or ticker.

    Snapshots before any write. Returns error dict (ok=False) if not found.
    """
    if not _YAML_AVAILABLE:
        return {"ok": False, "error": "PyYAML not available; cannot mutate calendar"}

    companies = _load_calendar()
    target = name_or_ticker.lower().strip()
    removed = [
        co for co in companies
        if co["name"].lower() == target or str(co.get("ticker") or "").lower() == target
    ]
    if not removed:
        return {"ok": False, "error": f"Company not found: {name_or_ticker!r}"}

    snap = _snapshot_calendar()
    remaining = [co for co in companies if co not in removed]
    _rewrite_calendar(remaining)
    return {
        "ok": True,
        "action": "removed",
        "removed": [r["name"] for r in removed],
        "snapshot": str(snap),
        "companies_remaining": len(remaining),
    }


# ---------------------------------------------------------------------------
# Auto-scan engine (RB 9.29)
# ---------------------------------------------------------------------------

def scan_for_watch_candidates(
    threshold: int = 3,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
) -> list[dict]:
    """Scan market signal sources for companies that appear >= threshold times
    but are not yet tracked in the earnings calendar.

    Sources scanned (in order):
      1. system/inbox/market_signals_earnings.jsonl  (EDGAR + IR feeds)
      2. system/inbox/market_signals_feed.jsonl      (trade press RSS)
      3. system/.cache/market_signals.json           (derived cache — all_ranked)

    Returns a list of candidate dicts sorted by appearance_count desc:
      {name, appearance_count, signal_types, first_seen, last_seen,
       in_calendar, auto_add_eligible}
    """
    current = _load_calendar()
    watched_names = {
        term.casefold()
        for company in current
        for term in identity_terms(company)
    }

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    # company_name → {count, signal_types, dates}
    tally: dict[str, dict] = {}

    def _tally_row(row: dict) -> None:
        name = (row.get("company") or "").strip()
        if not name or name in {"(general)", "(macro)", ""}:
            return
        # Recency filter
        pub = row.get("published_at") or row.get("_ingested_at") or ""
        if pub:
            try:
                dt = datetime.fromisoformat(pub[:19].replace(" ", "T"))
                dt = dt.replace(tzinfo=timezone.utc)
                if dt < cutoff:
                    return
            except ValueError:
                pass
        if name not in tally:
            tally[name] = {"count": 0, "signal_types": set(), "dates": []}
        tally[name]["count"] += 1
        tally[name]["signal_types"].add(row.get("signal_type") or "unknown")
        if pub:
            tally[name]["dates"].append(pub[:10])

    # 1+2: raw JSONL feeds
    for jsonl_path in [
        INBOX_DIR / "market_signals_earnings.jsonl",
        INBOX_DIR / "market_signals_feed.jsonl",
    ]:
        if not jsonl_path.exists():
            continue
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                _tally_row(json.loads(line))
            except json.JSONDecodeError:
                continue

    # 3: market_signals derived cache
    cache_path = CACHE_DIR / "market_signals.json"
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            inner = cache.get("data") or cache
            for item in (inner.get("all_ranked") or inner.get("top") or []):
                _tally_row(item)
        except (json.JSONDecodeError, AttributeError):
            pass

    # Build candidate list
    candidates: list[dict] = []
    for name, info in sorted(tally.items(), key=lambda x: -x[1]["count"]):
        in_cal = name.casefold() in watched_names
        if info["count"] >= threshold and not in_cal:
            dates = sorted(d for d in info["dates"] if d)
            candidates.append({
                "name": name,
                "appearance_count": info["count"],
                "signal_types": sorted(info["signal_types"]),
                "first_seen": dates[0] if dates else None,
                "last_seen": dates[-1] if dates else None,
                "in_calendar": False,
                "auto_add_eligible": True,
            })
    return candidates


def auto_add_from_signals(
    threshold: int = 3,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
) -> dict:
    """Auto-add companies from market signals that exceed the appearance threshold.

    Companies added this way are tagged added_by='cos_auto' with watch_priority=true.
    Returns a Trust Statement-compatible summary dict.
    """
    candidates = scan_for_watch_candidates(threshold=threshold, lookback_days=lookback_days)
    adds: list[dict] = []
    errors: list[dict] = []

    for cand in candidates:
        result = add_company(
            cand["name"],
            strategic_relevance="medium",
            watch_priority=True,
            reason=(
                f"Auto-added: appeared {cand['appearance_count']} times in market signals "
                f"over the past {lookback_days} days. "
                f"Signal types: {', '.join(cand['signal_types'])}."
            ),
            added_by="cos_auto",
        )
        if result.get("ok") and result.get("action") in ("added", "upgraded_to_watch"):
            adds.append({
                "company": cand["name"],
                "action": result["action"],
                "appearance_count": cand["appearance_count"],
                "signal_types": cand["signal_types"],
            })
        elif not result.get("ok"):
            errors.append({"company": cand["name"], "error": result.get("error")})

    return {
        "ok": True,
        "threshold": threshold,
        "lookback_days": lookback_days,
        "candidates_found": len(candidates),
        "companies_added": len(adds),
        "adds": adds,
        "errors": errors,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trust_statement": (
            f"Watch-list auto-scan complete. {len(candidates)} candidates above threshold "
            f"({threshold}+ appearances in {lookback_days} days). "
            f"{len(adds)} companies added to earnings calendar."
            + (f" {len(errors)} errors." if errors else "")
        ),
    }


# ---------------------------------------------------------------------------
# Sprint G — Earnings Intelligence Engine
# ---------------------------------------------------------------------------
# Adds three capabilities on top of the existing EDGAR/IR feed pipeline:
#
#   1. Pre-earnings countdown alerts (30/7/1-day windows) estimated from report_months.
#   2. Post-earnings signal parsing: categorise recent earnings releases into 5 dimensions.
#   3. build_earnings_intelligence(today) → list[canonical_item] for daily_brief.
#
# The 5 post-earnings signal dimensions:
#   Financial    — revenue, ARR, NRR, gross margin, EPS, guidance
#   Customer     — traffic, comps, guest counts, retention, churn
#   Technology   — tech investment, AI, POS, payments, platform
#   Operational  — labor, food costs, store count, expansion, openings
#   Franchisee   — franchisee sentiment, royalty, comp-store franchise

# Keywords for each post-earnings signal dimension
_EARNINGS_DIMENSIONS: dict[str, list[str]] = {
    "Financial": [
        "revenue", "arr", "nrr", "gross margin", "ebitda", "eps", "earnings per share",
        "guidance", "outlook", "full-year", "full year", "quarterly results",
        "net income", "net loss", "operating income", "cash flow", "balance sheet",
    ],
    "Customer": [
        "same-store", "comparable", "comps", "traffic", "guest count", "retention",
        "churn", "net revenue retention", "locations", "active customers",
        "restaurant count", "unit economics", "average revenue per",
    ],
    "Technology": [
        "artificial intelligence", "machine learning", "ai ", "pos ", "point of sale",
        "payments", "platform", "saas", "cloud", "digital ordering", "loyalty",
        "integration", "api", "product roadmap", "technology investment", "r&d",
    ],
    "Operational": [
        "labor", "wage", "staffing", "food cost", "commodity", "supply chain",
        "new location", "new unit", "expansion", "openings", "closures",
        "cost savings", "efficiency", "restructuring",
    ],
    "Franchisee": [
        "franchise", "franchisee", "royalty", "same-store franchise", "unit growth",
        "franchise agreement", "franchise development", "area developer",
    ],
}

# Pre-earnings alert thresholds (days before estimated report)
_ALERT_WINDOWS = [
    (1,  "TODAY",     "act_today"),
    (7,  "7 DAYS",    "act_today"),
    (30, "30 DAYS",   "monitor"),
    # RB 9.82 — RB-DEFECT-044 "Horizon Watch (30-90 days)": widen the
    # pre-earnings window so reports 31-90 days out surface as
    # not-yet-actionable horizon items rather than being dropped entirely.
    (90, "90 DAYS",   "monitor"),
]


def _estimate_next_report_date(company: dict, today: date) -> date | None:
    """Estimate the next report date from report_months.

    report_months contains the calendar months (1-12) when the company
    typically reports (e.g. [2, 5, 8, 11] for February/May/August/November).
    Returns the 8th of the next applicable month as a conservative estimate.

    RB-DEFECT-066: `last_reported_date` (set by _reconcile_confirmed_dates
    once a real earnings_release signal lands) supersedes the estimate for
    its own cycle. Without this, a company that already reported this month
    (e.g. PAR reporting Aug 6 while report_months still lists August) kept
    generating a "reports ~Aug 8" pre-earnings alert for an event that had
    already happened, because the 8th-of-month estimate for the current
    month is still >= a report date early in that month.
    """
    months = company.get("report_months") or []
    if not months:
        return None
    months = sorted(int(m) for m in months if 1 <= int(m) <= 12)

    last_reported_dt: date | None = None
    last_reported = company.get("last_reported_date")
    if last_reported:
        try:
            last_reported_dt = date.fromisoformat(str(last_reported))
        except ValueError:
            last_reported_dt = None

    def _already_reported_this_cycle(year: int, month: int) -> bool:
        return bool(
            last_reported_dt
            and last_reported_dt.year == year
            and last_reported_dt.month == month
        )

    # Look for the nearest future month (within current year or wrapping to next)
    for year_offset in (0, 1):
        year = today.year + year_offset
        for month in months:
            if _already_reported_this_cycle(year, month):
                continue
            candidate = date(year, month, 8)  # estimate: 8th of the report month
            if candidate >= today:
                return candidate
            if year == today.year and month == today.month:
                # The 8th-of-month estimate has already passed, but the
                # company hasn't reported yet and this month is still a
                # candidate reporting month — widen the estimate to the
                # end of the current month rather than skipping straight
                # to next year.
                last_day = calendar.monthrange(year, month)[1]
                candidate = date(year, month, last_day)
                if candidate >= today:
                    return candidate
    return None


def _days_until_report(company: dict, today: date) -> int | None:
    """Return days until the estimated next report, or None if unknown."""
    next_date = _estimate_next_report_date(company, today)
    if next_date is None:
        return None
    return (next_date - today).days


def _fetch_tier(company: dict, today: date) -> str:
    """"full" (EDGAR + IR RSS, full lookback) for watch_priority=true
    companies and anyone within _NEAR_EARNINGS_DAYS of their next estimated
    report date; "light" (EDGAR-8K only, short lookback) otherwise. Reuses
    _days_until_report()'s report_months estimate rather than a second
    date calculation."""
    if company.get("watch_priority"):
        return "full"
    days = _days_until_report(company, today)
    if days is not None and 0 <= days <= _NEAR_EARNINGS_DAYS:
        return "full"
    return "light"


def _detect_earnings_dimensions(text: str) -> list[str]:
    """Return which of the 5 earnings signal dimensions are present in text."""
    text_lower = text.lower()
    found: list[str] = []
    for dim, keywords in _EARNINGS_DIMENSIONS.items():
        if any(kw in text_lower for kw in keywords):
            found.append(dim)
    return found


# ---------------------------------------------------------------------------
# Earnings history — durable cross-quarter record (2026-08-10 feature request)
#
# Public earnings calls/reports are high-value, recurring intelligence -- the
# daily brief only ever saw "this cycle," with nothing persisted for the CoS
# to look back across quarters and spot a storyline (a dimension that keeps
# recurring, a company that's gone quiet on a topic it used to emphasize).
# This appends one durable record per confirmed earnings event to
# system/earnings_history/earnings_calls.jsonl, independent of the 14-day
# lookback _build_post_earnings_signals uses for "what's new this cycle."
# ---------------------------------------------------------------------------

EARNINGS_HISTORY_PATH = Path(os.environ.get(
    "RB_EARNINGS_HISTORY_PATH", str(SYSTEM_DIR / "earnings_history" / "earnings_calls.jsonl")))

# Matches the EX-99.1 row on an EDGAR filing index page and captures the
# href of its linked document -- the actual press-release exhibit, one hop
# further than the index page _classify_8k_via_index already checks.
_EX99_EXHIBIT_RE = re.compile(r'EX-99\.1.{0,400}?href="([^"]+\.html?)"', re.I | re.S)

# RB-2026-09-06: Predictive Market Intelligence differentiation-read need --
# a company's genuine strategic framing usually lives in a quoted CEO/
# executive statement further into the release than the standard 600-char
# lead excerpt reaches. Confirmed live against 3 real press releases before
# picking this cap: quote position varies a lot (Wingstop 271 chars,
# Papa Johns ~1,900 chars, Starbucks ~3,200 chars) -- a fixed cap has to be
# large enough to reach the latest of these to be worth adding at all.
# Deliberately NOT a blanket length increase for every excerpt: only
# extends past 600 chars when a real quote is actually found in this
# window, and even then stops at the quote's own end (plus a small buffer),
# never at the full 3,500-char cap unless the quote itself runs that long --
# keeps the fair-use footprint proportional to genuine need, not maximized
# by default.
_QUOTE_SEEK_MAX_LEN = 3500
_QUOTED_SPEECH_RE = re.compile(r'["“]([^"”]{20,400})["”]')


def _extract_excerpt_reaching_quote(body: str) -> str:
    """Bounded lead excerpt (default 600 chars), extended -- only as far as
    needed, capped at _QUOTE_SEEK_MAX_LEN -- when a real quoted statement
    starts beyond the default cut, so a genuine management quote isn't
    silently truncated mid-sentence or omitted entirely."""
    default_excerpt = body[:600]
    quote_match = _QUOTED_SPEECH_RE.search(body[:_QUOTE_SEEK_MAX_LEN])
    if quote_match and quote_match.end() > 600:
        return body[:min(quote_match.end() + 50, _QUOTE_SEEK_MAX_LEN)]
    return default_excerpt


def _fetch_earnings_release_excerpt(index_url: str) -> dict:
    """Best-effort: fetch an 8-K's index page, find its EX-99.1 press-release
    exhibit, fetch that document, and extract a short lead excerpt for a
    genuine executive summary. SEC's index page has no prose of its own, and
    IR RSS (the other primary source) isn't configured for most
    watch_priority companies -- this is the only automated path to real
    earnings-release text without ingesting call transcripts (out of scope;
    see RB-DEFECT-066's non-goals).

    Returns {"excerpt": str, "exhibit_url": str|None}. Never raises --
    degrades to an empty excerpt on any fetch failure so a slow or blocked
    SEC request can never break the brief pipeline.
    """
    empty = {"excerpt": "", "exhibit_url": None}
    if not index_url:
        return empty
    try:
        req = urllib.request.Request(
            index_url,
            headers={
                "User-Agent": "RelationshipBuilder/1.0 rb-monitor@private.local",
                "Accept": "text/html",
            },
        )
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            index_html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, UnicodeDecodeError):
        return empty

    match = _EX99_EXHIBIT_RE.search(index_html)
    if not match:
        return empty
    href = match.group(1)
    exhibit_url = href if href.startswith("http") else f"https://www.sec.gov{href}"

    try:
        req = urllib.request.Request(
            exhibit_url,
            headers={
                "User-Agent": "RelationshipBuilder/1.0 rb-monitor@private.local",
                "Accept": "text/html",
            },
        )
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            doc_html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, UnicodeDecodeError):
        return {"excerpt": "", "exhibit_url": exhibit_url}

    import html as _html_mod
    text = re.sub(r"<[^>]+>", " ", doc_html)
    text = _html_mod.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()

    # EDGAR's own document viewer prepends a fixed banner to every exhibit
    # ("EX-99.1 2 <filename> EX-99.1 [Document] Exhibit 99.1") ahead of the
    # filer's actual press-release text -- strip it unconditionally (live-
    # verified against Wendy's/PAR/Fiserv/Global Payments/Papa Johns real
    # Q2 2026 exhibits) before falling back to the "For Immediate Release" /
    # "For Release" dateline markers filers use inconsistently for their
    # own contact-info boilerplate.
    text = re.sub(
        r"^EX-99\.1\s+\d+\s+\S+\s+EX-99\.1(?:\s+EX-99\.1)?(?:\s+Document)?\s+Exhibit\s+99\.1\s+",
        "", text, count=1, flags=re.I,
    )
    marker = re.search(r"for\s+immediate\s+release|for\s+release\s*:", text, re.I)
    body = text[marker.end():].strip() if marker else text

    # RB-DEFECT-2026-08-14: confirmed live on Olo's 8-K -- when the EX-99.1
    # regex above matches a link that resolves back to SEC's own filing-index
    # page rather than the actual press-release exhibit, this function fetches
    # index-page chrome ("Filed: 2026-08-10 AccNo: 0001213900-26-087318 Size:
    # 420 KB Item 2.02...") and returns it as if it were executive-summary
    # prose. That text then renders verbatim in the brief, truncated mid-word
    # ("Item…"), telling the reader nothing. An index page has no "for
    # immediate release" marker to strip, so `body` is still the raw table
    # text at this point -- reject it here rather than let a caller mistake
    # EDGAR chrome for a real excerpt.
    if _is_edgar_index_boilerplate(body):
        return {"excerpt": "", "exhibit_url": exhibit_url}

    return {"excerpt": _extract_excerpt_reaching_quote(body), "exhibit_url": exhibit_url}


# ---------------------------------------------------------------------------
# Free real transcript excerpts -- RB-2026-09-05, "stay free but expand every
# free resource we can find."
#
# MD&A (_fetch_mda_excerpt above) and the press-release exhibit
# (_fetch_earnings_release_excerpt above) are both written PROSE about the
# quarter -- neither is the actual thing Todd originally asked for: "a deep
# dive into earnings calls... read the tea leaves" from what executives
# actually SAID, live, including their real Q&A answers. Real call
# transcripts aren't SEC-filed and no free official transcript API exists,
# but Motley Fool (fool.com) publishes full, unpaywalled, properly speaker-
# attributed earnings-call transcripts for many (not all -- confirmed live:
# real coverage gap for smaller-cap names like PAR Technology) publicly
# traded companies, as free editorial content, no login required (verified
# live against real McDonald's/Apple transcripts before building this).
#
# Discovery problem: this script runs unattended (cron), with no access to
# a search API or Claude's own web-search tooling -- Motley Fool's own
# transcript index page blocks a plain HTTP fetch, and it has no per-
# company RSS feed. DuckDuckGo's HTML endpoint (html.duckduckgo.com/html/)
# needs no API key and reliably surfaces the exact matching fool.com URL
# for "{company} Q{n} {year} earnings call transcript" (verified live
# against multiple real companies) -- unofficial and could break/rate-limit
# without notice, so every step here is best-effort, matching this file's
# existing "never raises, degrades to empty" discipline throughout.
#
# Copyright discipline: only a short, bounded excerpt (matching this file's
# existing ~600-char excerpts) is ever stored -- never the full transcript
# -- consistent with fair-use-scale quoting for internal research/
# intelligence purposes, the same principle every other excerpt function in
# this file already follows.
_FOOL_TRANSCRIPT_BODY_MARKER = "Full Conference Call Transcript"
_BROWSER_LIKE_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _search_fool_transcript_url(company: str, today: date) -> Optional[str]:
    """Best-effort discovery of the real fool.com transcript URL for this
    company's most recent quarter, via DuckDuckGo's no-API-key HTML search.
    Returns None on any failure or if no fool.com result is found -- never
    raises, so a DDG outage/format change can never break the earnings
    pipeline that calls this."""
    quarter = (today.month - 1) // 3 + 1
    query = f"site:fool.com {company} Q{quarter} {today.year} earnings call transcript"
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_LIKE_USER_AGENT})
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, UnicodeDecodeError):
        return None

    for href in re.findall(r'class="result__a"[^>]*href="([^"]+)"', body):
        match = re.search(r"uddg=([^&]+)", href)
        if not match:
            continue
        real_url = urllib.parse.unquote(match.group(1))
        if "fool.com/earnings/call-transcripts/" in real_url:
            return real_url
    return None


def _fetch_fool_transcript_excerpt(company: str, today: date) -> dict:
    """Best-effort: find + fetch the real Motley Fool earnings-call
    transcript for this company's most recent quarter, and extract a short,
    bounded excerpt of the actual transcript body (the CEO's opening
    framing, typically) -- the only free source of what executives actually
    SAID on the call, as opposed to written prose about the quarter.
    Returns {"excerpt": str, "source_url": str|None}. Never raises --
    degrades to an empty excerpt on any failure (DDG has no result, fool.com
    doesn't cover this company, the fetch fails, or the expected transcript
    marker isn't found)."""
    empty: dict = {"excerpt": "", "source_url": None}
    transcript_url = _search_fool_transcript_url(company, today)
    if not transcript_url:
        return empty

    try:
        req = urllib.request.Request(transcript_url, headers={"User-Agent": _BROWSER_LIKE_USER_AGENT})
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            doc_html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, UnicodeDecodeError):
        return {"excerpt": "", "source_url": transcript_url}

    import html as _html_mod
    text = re.sub(r"<script.*?</script>", " ", doc_html, flags=re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html_mod.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()

    marker_idx = text.find(_FOOL_TRANSCRIPT_BODY_MARKER)
    if marker_idx == -1:
        return {"excerpt": "", "source_url": transcript_url}
    body = text[marker_idx + len(_FOOL_TRANSCRIPT_BODY_MARKER):].strip()
    return {"excerpt": body[:600], "source_url": transcript_url}


# Matches the primary-document row on a 10-Q/10-K filing-index page's
# "Document Format Files" table -- the row whose Description cell is
# exactly "10-Q"/"10-K" (as opposed to an EX-31.1/EX-32.1 exhibit row),
# capturing its href. Live-verified against a real EDGAR index page
# (2026-08-29): the href is wrapped in EDGAR's inline-XBRL viewer
# ("/ix?doc=/Archives/edgar/data/.../company-20260531.htm"), stripped below.
_PRIMARY_DOC_ROW_RE = re.compile(
    r'<td[^>]*>\s*(?:10-Q|10-K)\s*</td>\s*<td[^>]*>\s*<a href="([^"]+)"', re.I,
)

# MD&A section headings. A 10-Q's TOC lists "Item 2." near the top of the
# document before the real section appears later -- _find_mda_section below
# takes the LAST match, not the first, to skip past the TOC entry.
_MDA_HEADING_RE = re.compile(
    r"item\s*2\.?\s*management.s\s+discussion\s+and\s+analysis"
    r"|item\s*7\.?\s*management.s\s+discussion\s+and\s+analysis",
    re.I,
)
_NEXT_ITEM_HEADING_RE = re.compile(r"item\s*\d[a-z]?\.\s+[A-Z]", re.I)


def _find_mda_section(text: str, *, max_len: int = 800) -> str:
    """Given a filing's full stripped text, return a bounded excerpt of its
    Management's Discussion and Analysis section, or "" if not found."""
    matches = list(_MDA_HEADING_RE.finditer(text))
    if not matches:
        return ""
    start = matches[-1].end()
    remainder = text[start:start + max_len + 200]
    next_item = _NEXT_ITEM_HEADING_RE.search(remainder, 20)  # skip past this heading's own tail
    cutoff = next_item.start() if next_item else len(remainder)
    return remainder[:min(cutoff, max_len)].strip()


def _fetch_mda_excerpt(index_url: str) -> dict:
    """Best-effort: fetch a 10-Q/10-K's index page, find its primary filing
    document, and extract a bounded excerpt of the Management's Discussion
    and Analysis section -- the free, EDGAR-archived stand-in for a call
    transcript (RB-2026-08-28/29: real call transcripts aren't filed with
    the SEC at all; MD&A is management's own narrative on strategy,
    competitive position, and results, filed under oath, and reaches back
    as far as EDGAR's archive does -- unlike the 8-K excerpt path, which is
    scoped to whatever exhibit that filing happens to include).

    Returns {"excerpt": str, "exhibit_url": str|None}. Never raises --
    degrades to an empty excerpt on any fetch/parse failure, same
    discipline as _fetch_earnings_release_excerpt.
    """
    empty = {"excerpt": "", "exhibit_url": None}
    if not index_url:
        return empty
    try:
        req = urllib.request.Request(
            index_url,
            headers={
                "User-Agent": "RelationshipBuilder/1.0 rb-monitor@private.local",
                "Accept": "text/html",
            },
        )
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            index_html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, UnicodeDecodeError):
        return empty

    match = _PRIMARY_DOC_ROW_RE.search(index_html)
    if not match:
        return empty
    href = match.group(1)
    # Strip EDGAR's inline-XBRL viewer wrapper ("/ix?doc=/Archives/...") to
    # get the directly-fetchable document URL.
    href = re.sub(r"^/ix\?doc=", "", href)
    doc_url = href if href.startswith("http") else f"https://www.sec.gov{href}"

    try:
        req = urllib.request.Request(
            doc_url,
            headers={
                "User-Agent": "RelationshipBuilder/1.0 rb-monitor@private.local",
                "Accept": "text/html",
            },
        )
        # 10-Q/10-K primary documents run 1-2MB of HTML -- longer than the
        # 8-K exhibit path's timeout budget needs.
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT * 2) as resp:
            doc_html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, UnicodeDecodeError):
        return {"excerpt": "", "exhibit_url": doc_url}

    import html as _html_mod
    text = re.sub(r"<[^>]+>", " ", doc_html)
    text = _html_mod.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()

    excerpt = _find_mda_section(text)
    return {"excerpt": excerpt, "exhibit_url": doc_url}


def _is_edgar_index_boilerplate(text: str) -> bool:
    """True if `text` is SEC EDGAR filing-index chrome ("Filed: 2026-08-10
    AccNo: 0001213900-26-087318 Size: 420 KB") rather than real prose --
    shared by _fetch_earnings_release_excerpt (the exhibit-fetch path) and
    the exec_summary fallback below (the raw-feed-description path). Olo's
    8-K hit the SECOND path: its earnings_history excerpt was correctly
    empty, but `summary` (sourced from web_scanner.py's raw EDGAR Atom
    <description>, upstream of this module entirely) still carried this
    same boilerplate shape, so exec_summary = summary[:200] rendered it
    verbatim instead of falling through to the honest "not retrievable"
    message -- checking excerpt alone missed that second path."""
    return bool(
        re.search(r"\bAccNo:\s*\d{10}-\d{2}-\d{6}\b", text)
        or re.match(r"^\s*Filed:\s*\d{4}-\d{2}-\d{2}\s+AccNo:", text)
    )


def _load_earnings_history_rows() -> list[dict]:
    if not EARNINGS_HISTORY_PATH.exists():
        return []
    rows: list[dict] = []
    for line in EARNINGS_HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def get_company_earnings_history(company_name: str, limit: int = 8) -> list[dict]:
    """Return this company's recorded earnings events, most recent first --
    the durable, cross-quarter record for connect-the-dots trend analysis.
    Backs the getCompanyEarningsHistory API operation."""
    rows = [r for r in _load_earnings_history_rows() if r.get("company") == company_name]
    rows.sort(key=lambda r: r.get("event_date") or "", reverse=True)
    return rows[:limit]


def record_earnings_history(row: dict, dimensions: list[str]) -> dict:
    """Persist a confirmed earnings event to system/earnings_history/
    earnings_calls.jsonl. Idempotent by _source_hash: returns the existing
    record (no re-fetch, no duplicate, no repeated network cost on every
    daily cycle) if this event is already on file.
    """
    company = row.get("company") or ""
    source_hash = row.get("_source_hash") or _url_hash(row.get("url") or row.get("title") or "")

    for rec in _load_earnings_history_rows():
        if rec.get("_source_hash") == source_hash:
            return rec

    excerpt_info = {"excerpt": "", "exhibit_url": None}
    if row.get("source_type") == "sec_edgar_8k" and row.get("url"):
        excerpt_info = _fetch_earnings_release_excerpt(row["url"])
    elif row.get("source_type") in ("sec_edgar_10k", "sec_edgar_10q") and row.get("url"):
        excerpt_info = _fetch_mda_excerpt(row["url"])

    # RB-2026-09-05: "stay free but expand every free resource we can find."
    # A real call transcript (what executives actually SAID, live Q&A
    # included) is a genuinely different, higher-value source than either
    # excerpt above (both are written prose ABOUT the quarter, not spoken
    # commentary) -- independent of source_type, since it's discovered by
    # company name + date, not tied to a specific SEC filing. Best-effort,
    # never blocks recording the rest of this event if unavailable.
    try:
        event_date = date.fromisoformat((row.get("published_at") or "")[:10])
    except ValueError:
        event_date = date.today()
    transcript_info = _fetch_fool_transcript_excerpt(company, event_date)

    # RB-DEFECT-2026-08-29: `dimensions` (the caller's param) is computed
    # from title+summary alone, before the excerpts above -- the only
    # sources of real earnings-release prose -- have even been fetched.
    # Confirmed live: a real Starbucks Q3 earnings release with genuine
    # comp-sales/EPS/revenue content in its excerpt was persisted with
    # signal_dimensions: [] because the generic EDGAR Atom title/summary
    # never happened to contain those keywords. Merge in whatever each
    # excerpt itself actually supports -- never removes what the caller
    # already found, only adds real coverage the excerpts reveal.
    for text in (excerpt_info["excerpt"], transcript_info["excerpt"]):
        if text:
            dimensions = sorted(set(dimensions) | set(_detect_earnings_dimensions(text)))

    record = {
        "company": company,
        "event_date": row.get("published_at") or "",
        "title": row.get("title") or "",
        "signal_dimensions": dimensions,
        "source_url": row.get("url") or "",
        "source_type": row.get("source_type") or "",
        "excerpt": excerpt_info["excerpt"],
        "exhibit_url": excerpt_info["exhibit_url"],
        "transcript_excerpt": transcript_info["excerpt"],
        "transcript_source_url": transcript_info["source_url"],
        "_source_hash": source_hash,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }

    EARNINGS_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EARNINGS_HISTORY_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record


# RB-2026-08-29: 24-month free-source backfill for the predictive-
# intelligence design. EDGAR's browse-edgar Atom feed returns at most one
# page per request (no pagination handled here) -- 100 is comfortably more
# than 24 months of quarterly/annual 10-Q/10-K filings ever needs (at most
# ~10), but a very active 8-K filer (frequent board/officer changes, M&A,
# etc., not just earnings) could exceed 100 events in 24 months, in which
# case the oldest ones in the window would be missed. Real per-company
# coverage is reported in backfill_earnings_history's return value, not
# assumed -- see its docstring.
_BACKFILL_FEED_COUNT = 100


def backfill_earnings_history(
    *, lookback_days: int = 730, companies: list[dict] | None = None, live: bool = True,
) -> dict:
    """One-time historical backfill of system/earnings_history/
    earnings_calls.jsonl -- the free-source half of the 2026-08-29
    predictive-market-intelligence design (see the "Predictive market
    intelligence" ROADMAP.md entry): 24+ months of MD&A excerpts (10-Q/10-K)
    and earnings-release excerpts (8-K) for every company already tracked in
    earnings_calendar.yaml, all free via EDGAR's public archive.

    Idempotent -- record_earnings_history already dedupes by _source_hash,
    so this is safe to re-run, resume after a partial run, or run again
    later to pick up anything a first pass missed (see the coverage caveat
    on _BACKFILL_FEED_COUNT above). Every 10-Q/10-K filing found is recorded
    unconditionally (a quarterly/annual report is inherently the disclosure
    this design wants); 8-Ks are recorded only when classified
    "earnings_release", matching the existing daily pipeline's own filter in
    _build_post_earnings_signals, so this doesn't flood the history with
    routine 8-K item types (board changes, etc.) the daily flow never
    treated as earnings events either.

    Returns a summary dict: {"companies_processed", "records_written",
    "records_already_present", "companies_skipped_no_cik", "per_company":
    [{"name", "written", "already_present", "oldest_event_date",
    "newest_event_date"}]} -- the oldest/newest dates per company are the
    honest signal of whether this run actually reached back 24 months for
    that company, not an assumption.
    """
    companies = companies if companies is not None else _load_calendar()
    summary: dict = {
        "companies_processed": 0,
        "records_written": 0,
        "records_already_present": 0,
        "companies_skipped_no_cik": [],
        "per_company": [],
    }

    feed_specs = [
        ("sec_edgar_8k", _EDGAR_8K_URL, "count=10"),
        ("sec_edgar_10k", _EDGAR_10K_URL, "count=5"),
        ("sec_edgar_10q", _EDGAR_10Q_URL, "count=5"),
    ]

    for company in companies:
        cik = company.get("edgar_cik")
        name = company["name"]
        if not cik:
            summary["companies_skipped_no_cik"].append(name)
            continue

        written = 0
        already_present = 0
        event_dates: list[str] = []

        for source_type, url_template, default_count in feed_specs:
            url = url_template.replace(default_count, f"count={_BACKFILL_FEED_COUNT}").format(
                cik=cik.lstrip("0") or "0"
            )
            if not live:
                continue
            root = _fetch_xml(url)
            if root is None:
                continue
            rows = _parse_edgar_feed(root, company, lookback_days, source_type=source_type)
            for row in rows:
                if source_type == "sec_edgar_8k" and row.get("signal_type") != "earnings_release":
                    continue
                pre_existing_hashes = {r.get("_source_hash") for r in _load_earnings_history_rows()}
                is_new = row.get("_source_hash") not in pre_existing_hashes
                text = f"{row.get('title', '')} {row.get('second_order_impact', '')}"
                dimensions = _detect_earnings_dimensions(text)
                record = record_earnings_history(row, dimensions)
                if is_new:
                    written += 1
                else:
                    already_present += 1
                if record.get("event_date"):
                    event_dates.append(record["event_date"])

        summary["companies_processed"] += 1
        summary["records_written"] += written
        summary["records_already_present"] += already_present
        summary["per_company"].append({
            "name": name,
            "written": written,
            "already_present": already_present,
            "oldest_event_date": min(event_dates) if event_dates else None,
            "newest_event_date": max(event_dates) if event_dates else None,
        })

    return summary


def _build_earnings_trend_note(company_name: str, current_dimensions: list[str]) -> str:
    """One-sentence storyline note answering: is a signal dimension
    recurring across quarters, or is this a first-time/isolated mention?
    Call after record_earnings_history() so history includes the current
    event as the most recent entry."""
    # RB-DEFECT-2026-08-14: this note used to always render something, even
    # for a company's first tracked quarter -- "First earnings event on
    # record for this company — no trend baseline yet" is a statement about
    # RB's own data coverage, not about the company, and it used to lead the
    # 200-char-truncated summary field, crowding out the one thing that IS
    # substantive (the actual excerpt). Returning "" lets callers drop it
    # entirely rather than spend the lead position on a sentence that tells
    # the reader nothing about what happened.
    history = get_company_earnings_history(company_name, limit=6)
    if not history:
        return ""
    prior = history[1:]  # exclude the event just recorded
    if not prior:
        return ""

    from collections import Counter
    counts = Counter(dim for r in prior for dim in (r.get("signal_dimensions") or []))
    recurring = [dim for dim in current_dimensions if counts.get(dim, 0) >= 2]
    total_quarters = len(prior) + 1
    if recurring:
        dim = recurring[0]
        return (
            f"{dim} commentary has now appeared in {counts[dim] + 1} of the last "
            f"{total_quarters} quarters on record — a sustained theme, not a one-off."
        )
    if current_dimensions:
        label = current_dimensions[0] if len(current_dimensions) == 1 else ", ".join(current_dimensions)
        verb = "is a new theme" if len(current_dimensions) == 1 else "are new themes"
        return f"{total_quarters} quarters on record; {label} {verb} this quarter."
    return f"{total_quarters} quarters on record for this company."


def _canonical_earnings_item(
    *,
    title: str,
    summary: str,
    why_it_matters: str,
    recommended_action: str,
    disposition: str = "monitor",
    confidence: str = "high",
    source_refs: list[str] | None = None,
    extras: dict | None = None,
) -> dict:
    """Build a canonical-schema item for the earnings_intelligence section."""
    today_iso = date.today().isoformat()
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
            "first_seen": today_iso,
            "last_seen": today_iso,
        },
        "autonomous_discovery_value": "high",
        "novelty": {
            "is_new": True,
            "reason": "earnings_intelligence",
            "autonomous_discovery_value": "high",
            "source_discovered": True,
        },
    }


def _build_pre_earnings_alerts(
    companies: list[dict], today: date, *, exclude: frozenset[str] = frozenset(),
) -> list[dict]:
    """Generate pre-earnings countdown alerts for watched companies.

    `exclude` (RB-DEFECT-066) names companies with a fresh post-earnings
    signal already in this cycle's output -- their pre-earnings countdown is
    now stale ("reports this week" for an event that already happened) and
    is dropped in favor of the post-earnings review item instead.
    """
    items: list[dict] = []
    watch_companies = [c for c in companies if c.get("watch_priority") and c.get("name") not in exclude]

    for company in watch_companies:
        days = _days_until_report(company, today)
        if days is None:
            continue

        # Find which alert window applies
        window_label = None
        window_disposition = "monitor"
        for threshold, label, disposition in _ALERT_WINDOWS:
            if 0 <= days <= threshold:
                window_label = label
                window_disposition = disposition
                break

        if window_label is None:
            continue  # outside all alert windows

        name = company["name"]
        ticker = company.get("ticker") or "private"
        relevance = company.get("strategic_relevance", "medium")
        notes = (company.get("notes") or "").strip()[:200]
        side = company.get("side", "vendor_supply")
        next_date = _estimate_next_report_date(company, today)
        date_str = next_date.strftime("%B %d") if next_date else "soon"

        # Build signal dimension expectations based on company category/side
        category = company.get("category", "pos")
        if side == "vendor_supply":
            watch_for = "ARR growth, NRR, customer count, technology roadmap, margin"
        else:
            watch_for = "same-store sales, traffic, tech investment signals, guidance"

        # RB-DEFECT-2026-08-17: confirmed live -- McDonald's and Fiserv both
        # showed "reports October 08" in the title, no qualifier, because
        # date_str is a generic "8th of the report month" estimate (see
        # _estimate_next_report_date), not a confirmed date -- two unrelated
        # companies landing on the identical placeholder date is what
        # exposed it. The summary already correctly says "~{date_str}"; the
        # title dropped the tilde and presented the guess as fact. Also
        # dropped the trailing "— {window_label}" ("90 DAYS") from the
        # title -- that's an alert-tier bucket ceiling (this report is
        # *somewhere within* the next 90 days), not the actual day-count,
        # and sitting right next to a specific date it read as if it were
        # one, contradicting the body's real "(52 days away)" figure.
        # window_label/days are still preserved in extras for anything that
        # sorts or groups by alert tier -- only the confusing title text is
        # changed.
        items.append(_canonical_earnings_item(
            title=f"[PRE-EARNINGS] {name} ({ticker}) reports ~{date_str}",
            summary=(
                f"{name} ({ticker}) is estimated to report Q earnings on ~{date_str} "
                f"({days} days away). "
                f"Strategic relevance: {relevance}. "
                + (f"Context: {notes}" if notes else "")
            ),
            why_it_matters=(
                f"{name} is a {'vendor/technology company' if side == 'vendor_supply' else 'restaurant operator'} "
                f"in your tracked universe. "
                f"Earnings calls surface the most candid executive language about "
                f"{'technology spend, platform direction, and competitive positioning' if side == 'vendor_supply' else 'operator tech investment, traffic trends, and vendor evaluation decisions'}. "
                f"These signals directly affect your strategy and pipeline."
            ),
            recommended_action=(
                f"Before {name} reports: review your open opportunities and relationships at {name}. "
                f"Watch for: {watch_for}. "
                f"Post-earnings: flag any signals about {category.replace('_', ' ')} technology or vendor decisions."
            ),
            disposition=window_disposition,
            confidence="medium",  # date is estimated, not confirmed
            source_refs=["earnings_calendar"],
            extras={
                "earnings_type": "pre_earnings_alert",
                "company": name,
                "ticker": ticker,
                "days_until_report": days,
                "estimated_report_date": next_date.isoformat() if next_date else None,
                "alert_window": window_label,
                "strategic_relevance": relevance,
                "side": side,
                "watch_dimensions": watch_for,
            },
        ))

    # Sort: soonest first, then by relevance
    _rel_rank = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda x: (
        x["extras"].get("days_until_report", 999),
        _rel_rank.get(x["extras"].get("strategic_relevance", "medium"), 1),
    ))
    return items[:5]  # cap at 5 pre-earnings alerts per cycle


def _build_post_earnings_signals(lookback_days: int = 14) -> list[dict]:
    """Parse recent earnings releases from JSONL and categorise by dimension.

    Scans market_signals_earnings.jsonl for signal_type == 'earnings_release'
    in the last lookback_days.  For each, detects which of the 5 signal
    dimensions (Financial/Customer/Technology/Operational/Franchisee) are present.
    """
    items: list[dict] = []

    if not OUTPUT_PATH.exists():
        return items

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    seen_hashes: set[str] = set()

    for line in OUTPUT_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue

        if row.get("signal_type") != "earnings_release":
            continue

        # Recency filter
        pub = row.get("published_at") or row.get("_ingested_at") or ""
        if pub:
            try:
                pub_dt = datetime.fromisoformat(pub[:19].replace(" ", "T"))
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
            except ValueError:
                pass

        h = row.get("_source_hash") or ""
        if h in seen_hashes:
            continue
        seen_hashes.add(h)

        company_name = row.get("company", "Unknown")
        title = row.get("title", "")
        summary = row.get("second_order_impact") or row.get("pain_point_or_priority") or ""
        pub_date = row.get("published_at") or ""

        text = f"{title} {summary}"
        dimensions = _detect_earnings_dimensions(text)

        # RB 2026-08-10 feature request: persist a durable record and pull a
        # real executive-summary excerpt from the press-release exhibit
        # (SEC's index page has no prose of its own). Idempotent by
        # _source_hash, so this only fetches over the network the first
        # cycle a given event is seen -- every later cycle within the
        # lookback window is a cheap on-disk read.
        history_record = record_earnings_history(row, dimensions)
        excerpt = history_record.get("excerpt") or ""
        exhibit_url = history_record.get("exhibit_url")
        trend_note = _build_earnings_trend_note(company_name, dimensions)
        history_count = len(get_company_earnings_history(company_name, limit=100))

        # RB-DEFECT-2026-08-14: `summary` (row.get("second_order_impact") or
        # row.get("pain_point_or_priority")) is sourced from web_scanner.py's
        # raw EDGAR Atom <description> upstream of this module -- for Olo's
        # 8-K it was still-tagged filing-index chrome ("<b>Filed:</b>
        # 2026-08-10 <b>AccNo:</b> ... <b>Size:</b> 420 KB<br>Item…"), which
        # rendered with literal HTML tags visible and told the reader
        # nothing. excerpt was correctly empty (the exhibit-fetch guard
        # above caught that path), but this fallback branch had no
        # equivalent check.
        fallback_summary = re.sub(r"<[^>]+>", " ", summary)
        fallback_summary = re.sub(r"\s+", " ", fallback_summary).strip()
        if _is_edgar_index_boilerplate(fallback_summary):
            fallback_summary = ""

        exec_summary = excerpt[:400] if excerpt else fallback_summary[:200]
        if not exec_summary:
            exec_summary = (
                f"Confirmed earnings release; no press-release text was retrievable "
                f"(exhibit fetch failed or unavailable) — see source link for the filing."
            )

        # Section E (render_intelligence_brief._render_earnings) truncates the
        # summary field to 200 chars -- when there IS a trend note (a real,
        # recurring cross-quarter theme), it's the highest-value content and
        # should lead. When there isn't (trend_note == ""), the actual
        # excerpt/exec_summary is the only substantive content, so it must
        # lead instead of being crowded out by a blank prefix.
        summary_lead = f"{trend_note} " if trend_note else ""

        items.append(_canonical_earnings_item(
            title=f"[POST-EARNINGS] {company_name}: {title[:80]}",
            summary=(
                f"{summary_lead}"
                f"{company_name} earnings release ({pub_date}): {exec_summary}"
            ),
            why_it_matters=(
                f"Earnings releases contain the most authoritative executive-level signal "
                f"about {company_name}'s direction. "
                + (f"Detected signals: {', '.join(dimensions)}. " if dimensions else "")
                + (f"{trend_note} " if trend_note else "")
                + f"Review for vendor decision language, technology investment signals, and guidance revisions."
            ),
            # RB-DEFECT-2026-08-18: confirmed live -- Daily Brief's Technology
            # Radar reads recommended_action directly into its "Implication:"
            # line, so the raw API call syntax here ("call
            # getCompanyEarningsHistory for entity_name=...") rendered
            # verbatim in reader-facing text -- exactly what the 2026-08-18
            # CoS editorial standard prohibits. The same data is already
            # structured separately in extras (history_api/history_count)
            # for callers that want to build their own "N quarters on
            # record" line or make the actual API call -- recommended_action
            # only needs to be human-readable guidance.
            recommended_action=(
                f"Review {company_name} earnings release for: "
                + (", ".join(dimensions) if dimensions else "general financial health")
                + ". Flag any technology vendor mentions or investment guidance changes."
            ),
            disposition="monitor",
            confidence="high",
            source_refs=["earnings_monitor", "sec_edgar_8k"],
            extras={
                "earnings_type": "post_earnings_signal",
                "company": company_name,
                "published_at": pub_date,
                "signal_dimensions": dimensions,
                "source_url": row.get("url", ""),
                "exhibit_url": exhibit_url,
                "strategic_relevance": row.get("strategic_relevance", "medium"),
                "trend_note": trend_note,
                "history_count": history_count,
                "history_api": "getCompanyEarningsHistory",
            },
        ))

    # Sort: most recent first
    items.sort(key=lambda x: x["extras"].get("published_at", ""), reverse=True)
    return items[:5]  # cap at 5 post-earnings signals


def build_earnings_intelligence(today: date | None = None) -> list[dict]:
    """Sprint G: build canonical earnings intelligence items for the daily brief.

    Returns a combined list of:
      - Pre-earnings countdown alerts (for watched companies within 30 days)
      - Post-earnings signal analysis (for recent earnings releases)

    Ordered: pre-earnings alerts first (soonest), then post-earnings signals.
    Safe to call even if the calendar or JSONL is missing.
    """
    if today is None:
        today = date.today()

    try:
        companies = _load_calendar()
    except Exception:  # noqa: BLE001
        companies = []

    try:
        post_signals = _build_post_earnings_signals()
    except Exception:  # noqa: BLE001
        post_signals = []

    # RB-DEFECT-066: compute post-earnings signals first so their companies
    # can be excluded from the pre-earnings countdown -- otherwise a company
    # that already reported this cycle keeps showing a "reports this week"
    # reminder alongside (or instead of, if classification/IR-RSS failed)
    # the actual post-earnings item.
    already_reported = frozenset(
        (s.get("extras") or {}).get("company") for s in post_signals
        if (s.get("extras") or {}).get("company")
    )

    try:
        pre_alerts = _build_pre_earnings_alerts(companies, today, exclude=already_reported)
    except Exception:  # noqa: BLE001
        pre_alerts = []

    return pre_alerts + post_signals


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fetch", action="store_true",
                   help="Fetch live EDGAR + IR feeds (default: fixture mode)")
    p.add_argument("--cache", action="store_true",
                   help="Alias for --fetch --save-health; idempotent daily cache rebuild "
                        "for use by refresh_all.py and morning_pipeline.py")
    p.add_argument("--fixture", action="store_true",
                   help="Fixture-only mode (no network calls)")
    p.add_argument("--json", action="store_true", help="Output raw JSON")
    p.add_argument("--save-health", action="store_true",
                   help=f"Write source health to {HEALTH_PATH}")
    p.add_argument("--watch-only", action="store_true",
                   help="Only monitor watch_priority=true companies")
    p.add_argument("--days", type=int, default=_DEFAULT_LOOKBACK_DAYS,
                   help=f"Lookback window in days (default: {_DEFAULT_LOOKBACK_DAYS})")
    p.add_argument("--no-save", action="store_true",
                   help="Do not write to output JSONL (dry run)")
    p.add_argument("--list-companies", action="store_true",
                   help="List configured companies and exit")
    p.add_argument("--add", metavar="NAME",
                   help="Add a company to the earnings calendar watch list (RB 9.29)")
    p.add_argument("--ticker", metavar="TICKER",
                   help="Ticker symbol for --add (optional)")
    p.add_argument("--cik", metavar="CIK",
                   help="SEC EDGAR CIK for --add (optional)")
    p.add_argument("--ir-url", metavar="URL",
                   help="IR press release RSS URL for --add (optional)")
    p.add_argument("--side", default="vendor_supply",
                   choices=["vendor_supply", "operator_demand"],
                   help="Company side for --add (default: vendor_supply)")
    p.add_argument("--relevance", default="medium",
                   choices=["high", "medium", "low"],
                   help="Strategic relevance for --add (default: medium)")
    p.add_argument("--reason", default="",
                   help="Reason string for --add or --remove")
    p.add_argument("--remove", metavar="NAME_OR_TICKER",
                   help="Remove a company from the calendar by name or ticker (RB 9.29)")
    p.add_argument("--auto-scan", action="store_true",
                   help="Auto-add companies from market signals above the threshold (RB 9.29)")
    p.add_argument("--threshold", type=int, default=3,
                   help="Appearance threshold for --auto-scan (default: 3)")
    p.add_argument("--reclassify-backfill", action="store_true",
                   help="RB-DEFECT-066: re-run 8-K index-page classification against "
                        "already-ingested material_event rows and rewrite any that are "
                        "actually earnings releases. Combine with --watch-only to scope "
                        "to watch_priority companies and --no-save for a dry run.")
    p.add_argument("--backfill-entity-ids", action="store_true",
                   help="RB-2026-09-08, 3-store unification Phase 3: one-time backfill -- "
                        "resolve entity_id for existing market_signals_earnings.jsonl rows "
                        "that don't have one yet. Combine with --no-save for a dry run.")
    p.add_argument("--bridge-to-brand-profiles", action="store_true",
                   help="Confidence-Based Auto-Recording Phase 4 (2026-09-25): bridge "
                        "recently-captured operator-side rows into brand_profile_common "
                        "recent_signals. Combine with --no-save for a dry run.")
    args = p.parse_args(argv)

    # ── Mutation commands (RB 9.29) ──────────────────────────────────────────
    if args.add:
        result = add_company(
            args.add,
            ticker=args.ticker,
            edgar_cik=args.cik,
            ir_rss_url=args.ir_url,
            side=args.side,
            strategic_relevance=args.relevance,
            reason=args.reason,
            added_by="user",
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 1

    if args.remove:
        result = remove_company(args.remove)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 1

    if args.reclassify_backfill:
        scope = None
        if args.watch_only:
            scope = [c["name"] for c in _load_calendar() if c.get("watch_priority")]
        result = reclassify_stale_material_events(companies=scope, dry_run=args.no_save)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 1

    if args.backfill_entity_ids:
        result = backfill_entity_ids_in_market_signals(dry_run=args.no_save)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 1

    if args.bridge_to_brand_profiles:
        result = bridge_signals_to_brand_profiles(lookback_days=args.days, dry_run=args.no_save)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 1

    if args.auto_scan:
        result = auto_add_from_signals(threshold=args.threshold, lookback_days=args.days)
        # RB 9.29 — write result to cache so daily_brief can surface it (watch_scan_result section).
        _scan_cache = CACHE_DIR / "watch_scan_result.json"
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _scan_cache.write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 1

    if args.list_companies:
        companies = _load_calendar()
        print(f"\nEarnings calendar — {len(companies)} companies:\n")
        for c in companies:
            priority = "★" if c.get("watch_priority") else " "
            ticker = str(c.get("ticker") or "—")
            print(f"  {priority} {c['name']:35s} {ticker:6s} "
                  f"({c.get('side','?')}) [{c.get('strategic_relevance','?')}]")
        return 0

    # RB-9.63: --cache is an alias for --fetch --save-health (idempotent daily rebuild).
    if args.cache:
        args.fetch = True
        args.save_health = True

    live = args.fetch and not args.fixture
    result = run(
        live=live,
        watch_only=args.watch_only,
        lookback_days=args.days,
        save_output=not args.no_save,
        save_health=args.save_health,
    )

    if getattr(args, "json"):
        print(json.dumps(result, indent=2))
        return 0

    print(f"\nEarnings monitor — {'live' if live else 'fixture'} mode")
    print(f"Companies checked: {result['companies_checked']}")
    print(f"Rows found: {result['total_rows']} → deduped: {result['deduped_rows']} → new: {result['new_rows']}")
    print(f"Output: {result['output_path']}")
    print()
    print("Source health:")
    _Health().print_summary()   # blank — print from result
    for r in result["health"]["sources"]:
        icons = {"ok": "✓", "failed": "✗", "empty": "○"}
        icon = icons.get(r["status"], "–")
        note = f" ({r['error']})" if r["error"] else f" ({r['row_count']} rows)"
        print(f"  {icon} {r['company']:30s} [{r['feed_type']:10s}]: {r['status']}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
