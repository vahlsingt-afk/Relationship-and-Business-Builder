#!/usr/bin/env python3
"""Snapshot and diff first-party entity pages for material commercial change.

URLs come from explicit ``system/inbox/entity_page_watchlist.json`` entries and
primary-source assertions already present in the ecosystem graph. A first run
establishes a baseline; later text changes become reviewable signals only.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

import ecosystem_intelligence as ei
import rb_core as core

CONFIG_PATH = core.INBOX_DIR / "entity_page_watchlist.json"
STRATEGIC_CONFIG_PATH = core.INBOX_DIR / "restaurant_tech_public_source_watchlist.json"
HIGH_VALUE_CONFIG_PATH = core.INBOX_DIR / "restaurant_tech_high_value_target_watchlist.json"
EARLIEST_SIGNAL_CONFIG_PATH = core.INBOX_DIR / "restaurant_tech_earliest_signal_watchlist.json"
STATE_PATH = core.CACHE_DIR / "entity_page_monitor_state.json"
RESULT_PATH = core.CACHE_DIR / "entity_page_changes.json"
MAX_PAGES_PER_RUN = 225
MAX_FETCH_WORKERS = 12
ARTICLE_HOSTS = frozenset({
    "businesswire.com", "globenewswire.com", "prnewswire.com", "accessnewswire.com",
})
MONITORABLE_PATH_RE = re.compile(
    r"/(leadership|management|executive|careers?|jobs?|technology|partners?|"
    r"integrations?|procurement|suppliers?|investors?|investor-relations)(?:/|$)", re.I,
)
ARTICLE_PATH_RE = re.compile(r"/(news|newsroom|press|article|blog|story|release|news-releases?)(?:/|$)", re.I)
MATERIAL_RE = re.compile(
    r"\b(appoint|resign|retir|chief information|chief technology|transformation|"
    r"request for proposal|\brfp\b|\brfi\b|procurement|partner|integration|"
    r"migration|moderni[sz]|outage|layoff|restructur|acqui|contract|selected|"
    r"replace|platform|point.of.sale|payments?|loyalty|release notes?|changelog|"
    r"patents?|franchise disclosure|interchange|agenda|speaker|exhibitor|"
    r"software engineer|product manager|data engineer|architect|version)\b", re.I,
)
# Dynamic listing/search pages (e.g. a careers search page's facet counts) churn
# on nothing but item counts. A diff whose entire word-level delta is made of
# these tokens is pagination noise, not a reviewable business change.
NOISE_TOKEN_RE = re.compile(
    r"^\(?\d[\d,]*\)?$|"
    r"^\(?(items?|results?|jobs?|openings?|positions?|roles?|listings?|of|showing|found)\)?$",
    re.I,
)


def _word_diff(old_text: str, new_text: str) -> tuple[str, str, bool]:
    """Return (readable_excerpt, changed_text, is_listing_count_noise)."""
    old_words, new_words = old_text.split(), new_text.split()
    matcher = SequenceMatcher(None, old_words, new_words, autojunk=False)
    removed, added = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("delete", "replace"):
            removed.append(" ".join(old_words[i1:i2]))
        if tag in ("insert", "replace"):
            added.append(" ".join(new_words[j1:j2]))
    changed_words = " ".join(removed + added).split()
    is_noise = bool(changed_words) and all(NOISE_TOKEN_RE.match(word) for word in changed_words)
    parts = []
    if removed:
        parts.append("Removed: " + " / ".join(chunk for chunk in removed if chunk))
    if added:
        parts.append("Added: " + " / ".join(chunk for chunk in added if chunk))
    excerpt = "; ".join(parts)[:2000]
    changed_text = " ".join(removed + added)
    return excerpt, changed_text, is_noise


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _normalize_html(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"<(script|style|svg)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 RB-PageMonitor/1.0"})
    with urllib.request.urlopen(req, timeout=15) as response:
        return _normalize_html(response.read(2_000_000))


def page_registry(graph: dict) -> list[dict]:
    configured = _load(CONFIG_PATH).get("pages") or []
    configured.extend(_load(STRATEGIC_CONFIG_PATH).get("sources") or [])
    configured.extend(_load(HIGH_VALUE_CONFIG_PATH).get("sources") or [])
    configured.extend(_load(EARLIEST_SIGNAL_CONFIG_PATH).get("sources") or [])
    # Resolve alongside the earliest-signal config so tests and alternate
    # deployments can redirect the whole optional watchlist family together.
    anticipatory_path = EARLIEST_SIGNAL_CONFIG_PATH.parent / "restaurant_tech_anticipatory_channel_watchlist.json"
    configured.extend(_load(anticipatory_path).get("sources") or [])
    trust_path = EARLIEST_SIGNAL_CONFIG_PATH.parent / "restaurant_tech_trust_contract_watchlist.json"
    configured.extend(_load(trust_path).get("sources") or [])
    by_id = {row.get("id"): row for row in graph.get("entities") or []}
    pages: dict[str, dict] = {}
    for row in configured:
        if row.get("url"):
            pages[row["url"]] = dict(row)
    for rel in graph.get("relationships") or []:
        entity = by_id.get(rel.get("from_entity_id")) or {}
        for source in rel.get("source_assertions") or []:
            source_type = source.get("source_type") or ""
            url = source.get("url")
            # A vendor announcement cited on a brand relationship belongs to
            # the vendor, not the brand; inheriting it here creates false page
            # ownership (e.g. Burger King -> PAR investor root). Vendor pages
            # can still be added explicitly with the correct vendor entity.
            if not url or source_type != "primary_operator_statement":
                continue
            parsed = urlparse(url)
            host = (parsed.hostname or "").removeprefix("www.").lower()
            path = parsed.path or "/"
            # Article permalinks are evidence, not pages whose change conveys
            # new intelligence. Automatically monitor only durable first-party
            # roots or named organizational pages; explicit config can opt in
            # to anything else.
            if host in ARTICLE_HOSTS or ARTICLE_PATH_RE.search(path):
                continue
            if path not in {"", "/"} and not MONITORABLE_PATH_RE.search(path):
                continue
            pages.setdefault(url, {
                "entity_id": entity.get("id"), "entity": entity.get("name"),
                "url": url, "page_type": "primary_source_assertion", "source_type": source_type,
            })
    return sorted(pages.values(), key=lambda row: (row.get("entity") or "", row.get("url") or ""))


def run(*, today: date | None = None, fetcher=_fetch) -> dict:
    today = today or date.today()
    graph = ei._read_graph()
    pages = page_registry(graph)
    prior = _load(STATE_PATH)
    allowed_urls = {row.get("url") for row in pages}
    state = {url: value for url, value in (prior.get("pages") or {}).items() if url in allowed_urls}
    start = int(prior.get("next_index") or 0) % max(1, len(pages))
    selected = [pages[(start + idx) % len(pages)] for idx in range(min(MAX_PAGES_PER_RUN, len(pages)))] if pages else []
    changes, unchanged, baselined, errors = [], [], [], []
    fetched: dict[str, tuple[str | None, Exception | None]] = {}
    with ThreadPoolExecutor(max_workers=min(MAX_FETCH_WORKERS, max(1, len(selected)))) as pool:
        future_to_url = {pool.submit(fetcher, page["url"]): page["url"] for page in selected}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                fetched[url] = (future.result(), None)
            except Exception as exc:  # noqa: BLE001
                fetched[url] = (None, exc)

    for page in selected:
        url = page["url"]
        try:
            text, fetch_error = fetched[url]
            if fetch_error:
                raise fetch_error
            assert text is not None
            digest = hashlib.sha256(text.encode()).hexdigest()
            old = state.get(url) or {}
            if not old:
                baselined.append(url)
            elif old.get("sha256") == digest:
                unchanged.append({"entity": page.get("entity"), "url": url, "finding": "checked_no_change"})
            else:
                old_text = old.get("text") or ""
                excerpt, changed_text, is_noise = _word_diff(old_text, text)
                if is_noise:
                    unchanged.append({"entity": page.get("entity"), "url": url, "finding": "checked_listing_count_only"})
                else:
                    changes.append({
                        **page, "detected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "material_keywords_detected": bool(MATERIAL_RE.search(changed_text)),
                        "change_excerpt": excerpt, "status": "pending_review",
                    })
            state[url] = {"sha256": digest, "text": text[:100_000], "last_checked": today.isoformat(), **page}
        except Exception as exc:  # noqa: BLE001
            errors.append({"entity": page.get("entity"), "url": url, "error": str(exc)[:300]})
    payload = {
        "contract": "rb_entity_page_changes_v1", "date": today.isoformat(),
        "pages_registered": len(pages), "pages_checked": len(selected),
        "baselined": len(baselined), "unchanged": unchanged, "changes": changes, "errors": errors,
        "source_categories": sorted({row.get("source_category") for row in pages if row.get("source_category")}),
        "policy": "page/API deltas are review candidates, never canonical claims",
    }
    STATE_PATH.write_text(json.dumps({"next_index": (start + len(selected)) % max(1, len(pages)), "pages": state}, indent=2) + "\n", encoding="utf-8")
    RESULT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    args = parser.parse_args()
    result = run(today=date.fromisoformat(args.date) if args.date else None)
    print(json.dumps({key: result[key] for key in ("date", "pages_registered", "pages_checked", "baselined", "changes", "errors")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
