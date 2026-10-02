#!/usr/bin/env python3
"""Review-first discovery of new restaurant and restaurant-tech publishers.

Rotates through the canonical watchlist plus the Top-1500 ecosystem and uses
entity-specific Google News RSS results to observe publisher domains. Repeated
relevant publishers become candidates; this script never edits
industry_sources.yaml automatically.
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import rb_core as core
import watchlist_registry as wr
import web_scanner as ws
from entity_identity import load_entities

CACHE_PATH = core.CACHE_DIR / "public_source_discovery.json"
ROTATION_PATH = core.CACHE_DIR / "public_source_discovery_rotation.json"
BATCH_SIZE = 40
MAX_CANDIDATES = 50
_STRATEGIC_RE = re.compile(
    r"\b(technology|tech|software|platform|payments?|point.of.sale|\bpos\b|AI|digital|"
    r"loyalty|drive.thru|kiosk|earnings|revenue|sales|traffic|margin|consumer|labor|"
    r"CEO|CIO|CTO|executive|acqui|merger|IPO|bankrupt|clos(?:e|es|ing|ure)|expan|"
    r"franchi|strategy|strategic|investment|funding|partnership|appoint|restructur|"
    r"RFP|RFI|procurement|moderni[sz]|migration|outage|downtime|layoff|job cuts?|"
    r"customer loss|switch(?:es|ed|ing)?|replace(?:s|d|ment)?|hiring)\b",
    re.IGNORECASE,
)


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").removeprefix("www.").lower()
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 else host


def _configured_domains() -> set[str]:
    out = set()
    for source in ws.load_sources_config().get("sources") or []:
        host = _domain(source.get("url") or "")
        if host:
            out.add(host)
    return out


def entity_pool() -> list[str]:
    priority = wr.mandatory_all()
    ecosystem = [e.get("name") for e in load_entities()
                 if e.get("entity_type") == "brand" and e.get("name")
                 and float((e.get("attributes") or {}).get("rank") or 999999) <= 1500]
    seen, out = set(), []
    for name in priority + ecosystem:
        key = name.casefold()
        if key not in seen:
            seen.add(key); out.append(name)
    return out


def _batch(pool: list[str], size: int, today: date) -> tuple[list[str], int]:
    if not pool:
        return [], 0
    state = {}
    try:
        state = json.loads(ROTATION_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    start = int(state.get("next_index") or 0) % len(pool)
    chosen = [pool[(start + i) % len(pool)] for i in range(min(size, len(pool)))]
    return chosen, (start + len(chosen)) % len(pool)


def parse_publishers(raw: bytes, entity: str) -> list[dict]:
    root = ET.fromstring(raw)
    out = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        source = item.find("source")
        source_url = source.get("url", "") if source is not None else ""
        publisher = (source.text or "").strip() if source is not None else ""
        host = _domain(source_url)
        if host and entity.casefold() in title.casefold() and _STRATEGIC_RE.search(title):
            out.append({"entity": entity, "publisher": publisher or host,
                        "domain": host, "title": title})
    return out


def fetch_entity(entity: str) -> list[dict]:
    query = f'"{entity}" (restaurant OR POS OR payments OR technology OR franchise)'
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 RB-SourceDiscovery/1.0"})
    with urllib.request.urlopen(req, timeout=12) as response:
        return parse_publishers(response.read(), entity)


def run(*, today: date | None = None, batch_size: int = BATCH_SIZE,
        fetcher=fetch_entity) -> dict:
    today = today or date.today()
    pool = entity_pool()
    entities, next_index = _batch(pool, batch_size, today)
    observations, errors = [], []
    for entity in entities:
        try:
            observations.extend(fetcher(entity))
        except Exception as exc:  # one entity must never stop the rotation
            errors.append({"entity": entity, "error": str(exc)[:240]})
    configured = _configured_domains()
    prior = {}
    try:
        prior = json.loads(CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    # Candidates written by the pre-corroboration prototype have no
    # observation_days and must not be grandfathered into review status.
    prior_by_domain = {c.get("domain"): c for c in prior.get("candidates") or []
                       if c.get("domain") and c.get("observation_days")}
    by_domain = defaultdict(list)
    for obs in observations:
        if obs["domain"] not in configured and obs["domain"] != "news.google.com":
            by_domain[obs["domain"]].append(obs)
    candidates = []
    for domain, rows in by_domain.items():
        distinct_entities = len({r["entity"] for r in rows})
        mentions = len(rows)
        old = prior_by_domain.get(domain) or {}
        days = sorted(set((old.get("observation_days") or []) + [today.isoformat()]))
        score = min(100, 20 + distinct_entities * 15 + min(mentions, 8) * 4 + min(len(days), 3) * 10)
        if distinct_entities >= 2 or mentions >= 3:
            candidates.append({
                "domain": domain, "publisher": Counter(r["publisher"] for r in rows).most_common(1)[0][0],
                "score": score, "distinct_entities": distinct_entities,
                "mentions": mentions,
                "first_seen": old.get("first_seen") or today.isoformat(),
                "last_seen": today.isoformat(), "observation_days": days,
                "status": "pending_review" if len(days) >= 2 else "observing",
                "example_headlines": [r["title"] for r in rows[:3]],
            })
    # Preserve previously observed domains that were not rediscovered in this
    # rotation; corroboration is across days, not only within one batch.
    present = {c["domain"] for c in candidates}
    candidates.extend(c for d, c in prior_by_domain.items() if d not in present)
    candidates.sort(key=lambda r: (r.get("status") != "pending_review", -r["score"], -r["distinct_entities"], r["domain"]))
    candidates = candidates[:MAX_CANDIDATES]
    result = {
        "date": today.isoformat(), "generated_at": datetime.now(timezone.utc).isoformat(),
        "pool_size": len(pool), "entities_checked": entities,
        "observations": len(observations), "configured_domains_excluded": len(configured),
        "candidates": candidates, "errors": errors,
        "policy": "proposal_only; industry_sources.yaml is never auto-mutated",
    }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(result, indent=2) + "\n")
    ROTATION_PATH.write_text(json.dumps({"last_run": today.isoformat(), "next_index": next_index}, indent=2) + "\n")
    return result


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date")
    p.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = p.parse_args()
    result = run(today=date.fromisoformat(args.date) if args.date else None, batch_size=args.batch_size)
    print(json.dumps(result, indent=2))
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
