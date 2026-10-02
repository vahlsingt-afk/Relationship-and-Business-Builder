#!/usr/bin/env python3
"""entity_alerts.py — Google-News-search "alerts" for the mandatory watchlist (RB-9.65-D).

A world-class CoS doesn't just read a fixed list of trade pubs — they have
Google Alerts firing on every entity that matters, watching for acquisitions,
funding rounds, executive moves, layoffs, and partnerships. This script is
that layer for RB.

For each entity on the mandatory watchlist (the same list daily_brief.py's
_compute_watchlist_intelligence() guarantees coverage for), it builds a
targeted Google News RSS search query — entity name + a curated set of
trigger keywords (acquires, raises, appoints, names, departs, layoffs,
partners, launches...) — and caches matching headlines.

Because Google News rate-limits aggressive querying, entities are scanned in
a daily-rotating batch (default 8/day) so the full ~23-entity watchlist gets
refreshed roughly every 3 days. Each entity's cache entry records
`last_checked` so daily_brief.py / _compute_watchlist_intelligence can tell
"checked recently, nothing found" from "not yet checked this cycle".

Output cache: system/.cache/entity_alerts_cache.json
    {
      "_generated_at": "...",
      "_rotation_index": 0,
      "entities": {
        "PAR Technology": {
          "last_checked": "...",
          "items": [{"title", "url", "pub_date", "signal_type", "source"}]
        },
        ...
      }
    }

Usage:
    python3 entity_alerts.py --cache       # rotate today's batch, fetch, save cache
    python3 entity_alerts.py --json        # print cache as JSON
    python3 entity_alerts.py --cache --all # fetch ALL entities (slow; manual use)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core  # noqa: E402
import web_scanner as ws  # noqa: E402
import vulnerability_taxonomy as vt  # noqa: E402
import watchlist_registry as wr  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402  # RB-2026-09-08, 3-store unification Phase 3 — entity_id resolution

CACHE_PATH = core.CACHE_DIR / "entity_alerts_cache.json"

# RB-2026-09-05: this used to be two independent hardcoded literal lists
# (here and in daily_brief.py) with nothing enforcing they stayed
# identical -- confirmed still byte-identical after 8+ months, but that
# was luck, not a guarantee, especially now that watchlist_promotion.py
# needs a single real place to write a newly-confirmed brand/vendor into.
# Read once at import time, same as the literals it replaces -- this
# module runs as a fresh subprocess per scan (see technomic_watchlist_
# scan.py's `import entity_alerts as ea`), so a promotion confirmed
# between runs is picked up by the next one automatically.
MANDATORY_RESTAURANT_BRANDS = wr.mandatory_restaurant_brands()
MANDATORY_RESTAURANT_TECH = wr.mandatory_restaurant_tech()
MANDATORY_ALL = MANDATORY_RESTAURANT_BRANDS + MANDATORY_RESTAURANT_TECH

#: Entities scanned per rotation batch (general-purpose alerts).
#: 20/day covers the full 155-entity universe roughly weekly while press
#: releases continue to use a separate all-entity scan.
ROTATION_BATCH_SIZE = 20

#: Official press release wire services — queries filtered to these domains
#: so only authoritative company announcements are captured, not news coverage.
PRESS_RELEASE_SITES = (
    "site:prnewswire.com OR site:businesswire.com OR site:globenewswire.com"
)

# RB-DEFECT-2026-07-10b: two watchlist entities ("Fourth", "Square") are also
# common English words, so requiring the entity name in the headline (the
# RB-DEFECT-2026-07-10 fix) isn't enough on its own -- "Sandisk to Report
# Fiscal Fourth Quarter and Fiscal Year 2026 Results" matches "Fourth" via
# the ordinal, not the POS company Fourth. These patterns catch the specific
# idioms that dominate false positives for each name; a real Fourth/Square
# press release won't happen to also contain them.
_AMBIGUOUS_ENTITY_FALSE_POSITIVE_RE: dict[str, re.Pattern] = {
    "fourth": re.compile(
        r"\b(fiscal\s+)?fourth\s+quarter\b|\bq4\b", re.IGNORECASE,
    ),
    # RB-DEFECT-2026-07-16: "Longacre Square Partners" (an unrelated PE/real-
    # estate firm) slipped through as a "Square" match — the place-name list
    # only covered generic idioms, not other proper nouns that happen to
    # contain "Square", and firm-name suffixes like "Partners"/"Capital" are
    # a strong tell that "Square" here is part of a longer name, not the
    # payments company.
    "square": re.compile(
        r"\b(town|public|market|central|main|union|times|longacre)\s+square\b"
        r"|\bsquare[\s-]+(feet|foot|footage|mile|miles|partners|capital|ventures|hospitality|group)\b",
        re.IGNORECASE,
    ),
    # RB-DEFECT-2026-07-17: "Legion" (the restaurant workforce-management
    # company) matched "Lenovo Launches Legion R9000P Featuring World's
    # First Inkjet-print..." -- a Lenovo gaming-laptop product line that
    # happens to share the same brand name. Lenovo's "Legion" line is the
    # dominant false-positive source for this entity.
    # RB-DEFECT-2026-07-22: "Royal Canadian Legion" (a veterans' charity) is
    # the same false-positive class -- another well-known org that happens
    # to share the bare word "Legion". Same whack-a-mole shape as the Lenovo
    # case; each new false positive needs its own idiom added here.
    # RB-DEFECT-2026-07-23: the "royal canadian legion" idiom only covers
    # headlines naming the org directly -- "2028-29 Legion Nationals host
    # city revealed: Nanaimo is next" slipped through because it's the
    # Royal Canadian Legion's sponsored youth sports championship (a
    # recurring press-release source given the org sponsors several
    # "Legion [Sport] Nationals/Championships" programs), named without
    # "Royal Canadian" in front of it.
    "legion": re.compile(
        r"\blenovo\s+(legion|launches\s+legion)\b|\blegion\s+(r\d|y\d|5i|7i|pro|slim|tower|gaming)\b"
        r"|\b(royal\s+canadian|american)\s+legion\b|\blegion\s+(hall|branch|post)\b"
        r"|\blegion\s+(nationals|championships?)\b",
        re.IGNORECASE,
    ),
}

#: Trigger keywords — M&A, funding, leadership, layoffs, partnerships.
#: Kept in sync with web_scanner.classify_signal_type's signal taxonomy.
ALERT_KEYWORDS = (
    "acquires OR acquired OR acquisition OR merger OR raises OR funding "
    "OR appoints OR names OR promotes OR hires OR departs OR resigns "
    "OR steps down OR layoffs OR restructuring OR partners OR partnership "
    "OR launches"
)

#: Items older than this are dropped from the cache on each refresh.
ITEM_TTL_DAYS = 14

#: Vulnerability items (exec change, tech hiring, strategic change, RFP/RFI)
#: stay relevant across the 6-18 month opportunity horizon, so they're kept
#: much longer than routine news items.
VULNERABILITY_ITEM_TTL_DAYS = 540

#: Tech-relevant executive title keywords for the exec-change/hiring query.
#: Restricted to MANDATORY_RESTAURANT_BRANDS (operator accounts) — these are
#: the entities RB tracks for technology-replacement vulnerability.
VULNERABILITY_EXEC_KEYWORDS = (
    'CIO OR CTO OR "Chief Information Officer" OR "Chief Technology Officer" '
    'OR "Chief Digital Officer" OR "VP Technology" OR "VP Digital" '
    'OR "Director of Restaurant Technology" OR "VP Digital Transformation" '
    'OR "POS Program Manager" OR "Technology Architect" '
    'OR "Enterprise Applications Manager"'
)

#: Strategic-change + RFP/RFI keywords for the second vulnerability query.
VULNERABILITY_STRATEGIC_KEYWORDS = (
    'acquires OR acquisition OR "private equity" OR divest OR '
    '"new ownership" OR "digital transformation" OR "technology modernization" '
    'OR "request for proposal" OR RFP OR RFI OR "vendor evaluation" '
    'OR procurement OR "platform migration" OR "contract renewal" '
    'OR "replacing vendor" OR "selected a new" OR "implementation failure"'
)


def _build_query(entity: str) -> str:
    return f'"{entity}" ({ALERT_KEYWORDS})'


def _build_vulnerability_queries(entity: str) -> list[str]:
    """Return the vulnerability-signal queries for an entity.

    Two queries (Google News query length limits): one for tech-executive
    change / tech-hiring signals, one for strategic-change / RFP-RFI signals.
    """
    return [
        f'"{entity}" ({VULNERABILITY_EXEC_KEYWORDS})',
        f'"{entity}" ({VULNERABILITY_STRATEGIC_KEYWORDS})',
    ]


# RB 2026-08-26: reviewed live -- Section F (Watchlist) was surfacing any
# official-wire press release naming a watchlist entity in its headline,
# with zero topical filter beyond that: a "National Potato Month" promo, a
# bowl-game sponsorship release, and a bare "News from Panera Bread" title
# with no real content all qualified. web_scanner.classify_signal_type is
# the real, comprehensive signal taxonomy already used elsewhere -- reused
# below -- but its own patterns don't cover every real phrasing ("selects
# ... to power", "Unveils ... Release" both genuinely slipped past it in
# testing). Rather than loosen that shared classifier's patterns (used
# elsewhere for badging) just to widen this one filter, OR it with a
# broader keyword net scoped to this use only.
_PRESS_RELEASE_RELEVANCE_FALLBACK_RE = re.compile(
    r"\b(acqui(re|red|res|sition)|merger|merges?|raises?|funding|invest(s|ment|ed)?"
    r"|appoints?|names?|promotes?|hires?|departs?|resigns?|steps?\s+down"
    r"|layoffs?|restructur(e|ing)|partners?|partnership|launches?|expands?"
    r"|unveils?|introduc\w*|releas\w*|selects?|powering|powers?|deploys?"
    r"|integrat\w*|IPO|earnings|revenue|acquisition|divest(s|iture)?"
    r"|CEO|CFO|CTO|CIO|technology|platform|point.of.sale|\bpos\b|payment(s)?)\b",
    re.IGNORECASE,
)


def _fetch_press_releases(entity: str) -> list[dict]:
    """Fetch press releases for one entity from official wire services.

    Queries Google News restricted to prnewswire.com, businesswire.com, and
    globenewswire.com — authoritative sources only, not news coverage of releases.
    Returns items tagged with signal_type="press_release" and [PR] badge.
    """
    query = f'"{entity}" ({PRESS_RELEASE_SITES})'
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": query, "hl": "en-US", "gl": "US", "ceid": "US:en",
    })
    raw = _fetch_with_ua(url, 10)
    if raw is None:
        return []
    out = []
    entity_lower = entity.lower()
    for it in ws.parse_rss_items(raw, "Press Wire")[:10]:
        title = it.get("title", "")
        # RB-DEFECT-2026-07-10: Google News's exact-phrase search matches the
        # entity name ANYWHERE in a press release's indexed content, including
        # boilerplate "About [Company]" sections mentioning an unrelated
        # cloud/infra partner in passing (e.g. a biotech acquisition release
        # that happens to note it's "built on Amazon Web Services" got
        # attributed to Amazon Web Services in the Watchlist, with zero real
        # AWS relevance). Require the entity name to actually appear in the
        # headline -- a release genuinely ABOUT this entity will name it
        # there; one that just mentions it in passing won't.
        title_lower = title.lower()
        if entity_lower not in title_lower:
            continue
        # RB-DEFECT-2026-07-10b: a small number of watchlist entities are
        # also common English words ("Fourth", "Square") -- the headline-
        # match fix above stops incidental full-text mentions but not a
        # coincidental word match ("Sandisk to Report Fiscal Fourth Quarter
        # and Fiscal Year 2026 Results" matched entity "Fourth" via the
        # ordinal, not the POS company). Reject the specific idiom patterns
        # that dominate false positives for these two known-ambiguous names.
        if _AMBIGUOUS_ENTITY_FALSE_POSITIVE_RE.get(entity_lower, None) and \
                _AMBIGUOUS_ENTITY_FALSE_POSITIVE_RE[entity_lower].search(title_lower):
            continue
        if (
            ws.classify_signal_type(title) == "general"
            and not _PRESS_RELEASE_RELEVANCE_FALLBACK_RE.search(title_lower)
        ):
            continue
        src_url = it.get("url", "")
        # Infer wire service from URL
        if "prnewswire" in src_url:
            wire = "PR Newswire"
        elif "businesswire" in src_url:
            wire = "Business Wire"
        elif "globenewswire" in src_url:
            wire = "Globe Newswire"
        else:
            wire = "Press Wire"
        out.append({
            "title": title,
            "url": src_url,
            "pub_date": it.get("pub_date", ""),
            "signal_type": "press_release",
            "signal_badge": "[PRESS RELEASE]",
            "source": wire,
            "entity": entity,
            "press_release": True,
        })
    return out


def refresh_press_releases(today: date | None = None) -> dict:
    """Scan ALL watchlist entities for press releases — runs every pipeline cycle.

    Unlike the rotating general-purpose alert scan, press releases require same-day
    detection and are always scanned for the full entity list. Results are stored
    in entity_cache[entity]["press_release_items"] (TTL = 7 days).
    """
    today = today or date.today()
    cache = _load_cache()
    entities_cache: dict = cache.setdefault("entities", {})
    now_iso = datetime.now(timezone.utc).isoformat()
    pr_ttl = 7  # press releases stay relevant for one week

    hits = 0
    for entity in MANDATORY_ALL:
        items = _fetch_press_releases(entity)
        prior = (entities_cache.setdefault(entity, {})).get("press_release_items") or []
        seen_urls: set[str] = set()
        merged = []
        for it in items + prior:
            u = it.get("url", "")
            if u and u in seen_urls:
                continue
            seen_urls.add(u)
            merged.append(it)
        merged = _prune_stale_items(merged, today, ttl_days=pr_ttl)
        entities_cache[entity]["press_release_items"] = merged[:15]
        entities_cache[entity]["press_release_last_checked"] = now_iso
        hits += len(items)

    cache["_generated_at"] = now_iso
    cache["_pr_scan_date"] = today.isoformat()
    _save_cache(cache)
    return {"entities_scanned": len(MANDATORY_ALL), "press_releases_found": hits}


def _fetch_entity(entity: str, fetcher=None) -> list[dict]:
    """Fetch and parse Google News RSS results for one entity. Returns brief items."""
    fetcher = fetcher or ws._default_fetcher
    query = _build_query(entity)
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": query, "hl": "en-US", "gl": "US", "ceid": "US:en",
    })
    req_headers_fetcher = lambda u, t: _fetch_with_ua(u, t)
    raw = req_headers_fetcher(url, 10)
    if raw is None:
        return []
    raw_items = ws.parse_rss_items(raw, "Google News Search")[:10]
    out = []
    for it in raw_items:
        title = it.get("title", "")
        sig_type = ws.classify_signal_type(title, it.get("description", ""))
        out.append({
            "title": title,
            "url": it.get("url", ""),
            "pub_date": it.get("pub_date", ""),
            "signal_type": sig_type,
            "signal_badge": ws.signal_badge(sig_type),
            "source": "google_news_search",
            "entity": entity,
        })
    return out


def _mentions_entity(entity: str, title: str, description: str) -> bool:
    """True if `entity`'s own name genuinely appears in the combined
    title+description text.

    RB-2026-09-06: found live while building the competitive_vulnerability
    "blast radius" broadening -- Google News RSS search does NOT reliably
    honor the quoted-phrase filter already in our own query (`f'"{entity}"
    (...)'`), confirmed against real cached data: Subway, Five Guys, Arby's,
    and Blaze Pizza all had vulnerability_items whose headlines never
    mentioned the brand at all (e.g. "Hawaiian retailer ABC Stores hires...
    CIO" stored under Subway). `vt.classify_vulnerability` correctly
    classifies the CATEGORY of a real vulnerability headline, but was never
    asked to also confirm the headline is actually ABOUT the entity it's
    being attributed to -- a real gap, not a hypothetical one. Normalizes
    punctuation for a looser but still real match (apostrophe variants,
    "&" vs "and", etc.) rather than a brittle exact-string check."""
    normalized_entity = re.sub(r"[^a-z0-9 ]", "", entity.lower()).strip()
    if not normalized_entity:
        return False
    normalized_text = re.sub(r"[^a-z0-9 ]", "", f"{title} {description}".lower())
    return normalized_entity in normalized_text


def _fetch_vulnerability_alerts(entity: str, fetcher=None) -> list[dict]:
    """Fetch and classify vulnerability-signal items for one entity.

    Runs the exec-change/tech-hiring and strategic-change/RFP queries,
    classifies each item via vulnerability_taxonomy, and drops items that
    don't match any vulnerability category (Google News queries are
    keyword-OR, so plenty of noise comes back unclassified) OR that don't
    actually mention the entity at all (RB-2026-09-06 -- see
    _mentions_entity; Google News RSS doesn't reliably honor our own
    quoted-phrase query filter, so a post-fetch relevance check is real,
    not redundant).
    """
    out: list[dict] = []
    for query in _build_vulnerability_queries(entity):
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
            "q": query, "hl": "en-US", "gl": "US", "ceid": "US:en",
        })
        raw = _fetch_with_ua(url, 10)
        if raw is None:
            continue
        for it in ws.parse_rss_items(raw, "Google News Search")[:10]:
            title = it.get("title", "")
            description = it.get("description", "")
            if not _mentions_entity(entity, title, description):
                continue
            category = vt.classify_vulnerability(title, description)
            if category is None:
                continue
            out.append({
                "title": title,
                "url": it.get("url", ""),
                "pub_date": it.get("pub_date", ""),
                "category": category,
                "category_label": vt.category_label(category),
                "potential_categories": vt.extract_potential_categories(f"{title} {description}"),
                "source": "google_news_search",
                "entity": entity,
            })
    return out


def _fetch_with_ua(url: str, timeout: int = 10, *, retries: int = 1,
                    backoff_seconds: float = 1.5) -> bytes | None:
    """Google News RSS requires a browser-like User-Agent (default urllib UA is blocked).

    RB-DEFECT-2026-08-19: no retry here either (same class of bug fixed in
    earnings_monitor.py's _fetch_xml the same night) -- a batch scan of
    dozens to hundreds of entities has no tolerance for a single transient
    timeout dropping that entity's check for the whole cycle. Retry a
    connection/timeout error once before giving up.
    """
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; RB/1.0)"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            if attempt < retries:
                time.sleep(backoff_seconds * (attempt + 1))
                continue
            return None
    return None


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"_generated_at": None, "_rotation_index": 0, "entities": {}}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")


def _prune_stale_items(items: list[dict], today: date, ttl_days: int = ITEM_TTL_DAYS) -> list[dict]:
    cutoff = today - timedelta(days=ttl_days)
    out = []
    for it in items:
        pub = it.get("pub_date") or ""
        try:
            d = date.fromisoformat(pub[:10])
            if d < cutoff:
                continue
        except ValueError:
            pass  # keep undated items rather than discard silently
        out.append(it)
    return out


def refresh(scan_all: bool = False, today: date | None = None) -> dict:
    """Rotate through MANDATORY_ALL, fetch alerts for today's batch, merge into cache."""
    today = today or date.today()
    cache = _load_cache()
    entities_cache: dict = cache.setdefault("entities", {})
    # RB-2026-09-08, 3-store unification Phase 3: canonicalize this cache's
    # raw string keys against a real entity_id where one unambiguously
    # resolves. Read once per refresh() call, not per entity. Never guesses
    # -- a genuinely ambiguous name (e.g. the real "NCR Voyix" case, which
    # matches both vendor-ncr's alias and vendor-ncr-voyix's own name)
    # gets entity_id: None, same as an unresolvable one.
    graph = ei._read_graph()

    if scan_all:
        batch = MANDATORY_ALL
        next_index = 0
    else:
        idx = int(cache.get("_rotation_index") or 0) % len(MANDATORY_ALL)
        batch = []
        i = idx
        for _ in range(min(ROTATION_BATCH_SIZE, len(MANDATORY_ALL))):
            batch.append(MANDATORY_ALL[i % len(MANDATORY_ALL)])
            i += 1
        next_index = i % len(MANDATORY_ALL)

    now_iso = datetime.now(timezone.utc).isoformat()
    for entity in batch:
        items = _fetch_entity(entity)
        prior = (entities_cache.get(entity) or {}).get("items") or []
        # Merge new + prior, dedupe by URL, keep most recent ITEM_TTL_DAYS
        seen_urls = set()
        merged = []
        for it in items + prior:
            u = it.get("url", "")
            if u and u in seen_urls:
                continue
            seen_urls.add(u)
            merged.append(it)
        merged = _prune_stale_items(merged, today)
        entry = {"entity_id": ei._resolve_entity_id_any_type(entity, graph), "last_checked": now_iso, "items": merged[:10]}

        # RB-VULN-01: vulnerability signals (exec change, tech hiring,
        # strategic change, RFP/RFI) — operator brands only, kept much
        # longer (VULNERABILITY_ITEM_TTL_DAYS) since they confirm a
        # 6-18 month opportunity horizon.
        if entity in MANDATORY_RESTAURANT_BRANDS:
            vuln_items = _fetch_vulnerability_alerts(entity)
            prior_vuln = (entities_cache.get(entity) or {}).get("vulnerability_items") or []
            seen_vuln_urls = set()
            merged_vuln = []
            for it in vuln_items + prior_vuln:
                u = it.get("url", "")
                if u and u in seen_vuln_urls:
                    continue
                seen_vuln_urls.add(u)
                merged_vuln.append(it)
            merged_vuln = _prune_stale_items(merged_vuln, today, ttl_days=VULNERABILITY_ITEM_TTL_DAYS)
            entry["vulnerability_items"] = merged_vuln[:20]
        elif (entities_cache.get(entity) or {}).get("vulnerability_items"):
            entry["vulnerability_items"] = entities_cache[entity]["vulnerability_items"]

        entities_cache[entity] = entry

    cache["_generated_at"] = now_iso
    cache["_rotation_index"] = next_index
    cache["_batch_scanned"] = batch
    _save_cache(cache)
    return cache


def backfill_entity_ids(*, dry_run: bool = False) -> dict:
    """One-time pass (RB-2026-09-08, 3-store unification Phase 3):
    resolves entity_id for every existing cache entry that doesn't already
    have one, without touching any other field. Safe to re-run (a no-op
    for entries that already resolved). Never guesses -- an entry that
    doesn't resolve unambiguously gets entity_id: None, same as refresh()'s
    own write path."""
    cache = _load_cache()
    entities_cache: dict = cache.get("entities", {})
    graph = ei._read_graph()

    resolved: list[str] = []
    unresolved: list[str] = []
    for name, entry in entities_cache.items():
        if "entity_id" in entry:
            continue  # already stamped (by this backfill or by refresh())
        eid = ei._resolve_entity_id_any_type(name, graph)
        entry["entity_id"] = eid
        (resolved if eid else unresolved).append(name)

    if not dry_run:
        _save_cache(cache)
    return {"resolved": resolved, "unresolved": unresolved, "dry_run": dry_run}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", action="store_true", help="Fetch today's rotation batch and update the cache.")
    parser.add_argument("--all", action="store_true", help="Fetch ALL mandatory entities (slow; manual/backfill use).")
    parser.add_argument("--press-releases", action="store_true",
                        help="Scan ALL entities for press releases (PR Newswire/Business Wire/Globe Newswire). "
                             "Runs every pipeline cycle.")
    parser.add_argument("--json", action="store_true", help="Print the cache as JSON.")
    parser.add_argument("--backfill-entity-ids", action="store_true",
                        help="One-time: resolve entity_id for existing cache entries that don't have one yet.")
    parser.add_argument("--dry-run", action="store_true", help="With --backfill-entity-ids: report what would change, write nothing.")
    args = parser.parse_args()

    if args.backfill_entity_ids:
        result = backfill_entity_ids(dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
        return 0

    if args.press_releases:
        result = refresh_press_releases()
        print(f"entity_alerts --press-releases: scanned {result['entities_scanned']} entities, "
              f"{result['press_releases_found']} press releases found")
        return 0

    if args.cache:
        cache = refresh(scan_all=args.all)
        n_items = sum(len(v.get("items") or []) for v in cache["entities"].values())
        print(f"entity_alerts: scanned {len(cache.get('_batch_scanned') or [])} entities "
              f"(rotation_index now {cache['_rotation_index']}), "
              f"{n_items} cached items across {len(cache['entities'])} entities")
        return 0

    if args.json:
        print(json.dumps(_load_cache(), indent=2))
        return 0

    cache = _load_cache()
    for name, rec in cache.get("entities", {}).items():
        items = rec.get("items") or []
        if items:
            print(f"{name} (checked {rec.get('last_checked')}): {len(items)} item(s)")
            for it in items[:3]:
                print(f"    {it.get('signal_badge', '')} {it.get('title', '')[:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
