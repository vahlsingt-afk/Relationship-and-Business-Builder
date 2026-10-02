#!/usr/bin/env python3
"""
priority_account_publisher_scan.py — RB-DEFECT-069, 2026-09-10.

Closes a real gap found live: RestaurantNews.com is an already-monitored
industry source (feeds the general ~10-item restaurant-news section of the
daily brief), but "monitored" only ever meant "feeds that general cap" --
it never meant "every article about one of Todd's actual priority accounts
is guaranteed to surface." A real, material Del Taco article (Sept 2, 2026
catering-platform launch) never made that general cap and was invisible to
RBB until Todd manually supplied the URL 8 days later.

Investigated before building: RestaurantNews.com's own RSS feed
(https://www.restaurantnews.com/feed/) is real but only exposes ~14 items
covering roughly one day -- a high-volume publisher whose feed a priority
account's article can scroll off before any daily scan ever sees it.
Confirmed live instead: Google News RSS restricted to
`site:restaurantnews.com`, queried with the account's own name, reliably
surfaces the exact missed article with its correct real publish date. This
is the SAME proven pattern entity_alerts.py's _fetch_press_releases()
already uses for press-wire sites -- just pointed at trade-publisher
domains, queried per PRIORITY ACCOUNT instead of per watchlist entity.

Never invents a match, never auto-writes a structured fact: confirming a
candidate appends a lightweight, fact-free reference-evidence entry (a
link to a real source, not an extracted claim -- extracted_claims is
always []) to the account's own evidence.jsonl, same shape as
account_reference_detector.link_references()'s Blue Sheet branch.

Confidence-Based Auto-Recording Phase 5 (2026-09-25): because confirming
never claims a fact -- only links a source and bumps last_evidence_date --
there is no existing value here to protect, the same reasoning that made
watchlist_promotion.py's two scans safe to auto-apply. scan() now confirms
every material, non-duplicate candidate immediately via record_proposal(),
tagged "system:priority_account_publisher_scan". The candidate store is an
audit trail of every source ever linked this way, not a review queue.

CLI:
    python3 priority_account_publisher_scan.py scan            # scan + write candidates, print summary
    python3 priority_account_publisher_scan.py pending          # list pending candidates
    python3 priority_account_publisher_scan.py confirm <id>     # write the evidence reference
    python3 priority_account_publisher_scan.py reject <id>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import web_scanner as ws  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import ecosystem_brief as eb  # noqa: E402
import entity_alerts as ea  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402

MASTER_ACCOUNT_PLANS_ENGINE_DIR = core.PROJECT_DIR / "master_account_plans" / "_engine"
sys.path.insert(0, str(MASTER_ACCOUNT_PLANS_ENGINE_DIR))
try:
    import mp_impact_review  # noqa: E402
    import mp_common  # noqa: E402
except Exception:  # noqa: BLE001 -- optional, same posture as ecosystem_intelligence's own optional imports
    mp_impact_review = None
    mp_common = None

STORE_PATH = core.CACHE_DIR / "priority_account_publisher_candidates.json"

# Same domains web_scanner.py/fetch_google.py already treat as real,
# tracked restaurant-industry trade publishers, plus restaurantnews.com
# (confirmed live 2026-09-10 to have a working RSS feed AND site search --
# see module docstring for why a per-account Google News search is used
# instead of scanning that feed directly).
INDUSTRY_PUBLISHER_SITES = (
    "site:restaurantnews.com OR site:restaurantdive.com OR site:nrn.com OR "
    "site:qsrmagazine.com OR site:restauranttechnologynews.com OR "
    "site:franchisetimes.com OR site:restaurantbusinessonline.com"
)

# RB-2026-09-10: confirmed live -- customers_prospects_registry.json has a
# real "worldpay" account entry (Todd's own employer, tracked there for a
# different reason), which resolved as a genuine priority account and got
# scanned for trade-press coverage about itself. Same exclusion
# tech_stack_relationship_promotion.py already applies for the same reason
# (core.GP_OWN_TERMS's own docstring: "a customer-win fact about one of
# these is not competitive intelligence, it's Todd's own account").
_OWN_COMPANY_TERMS = {t.lower() for t in core.GP_OWN_TERMS}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_store() -> dict:
    if not STORE_PATH.exists():
        return {"candidates": {}}
    try:
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"candidates": {}}


def _save_store(store: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(store, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _url_or_title_key(url: str, title: str) -> str:
    """Stable dedup key: prefer the real URL; fall back to a normalized
    title only when no URL is available (defensive -- every real fetch
    path here always has one)."""
    basis = (url or "").strip().lower() or (title or "").strip().lower()
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def _candidate_id(entity_id: str, url: str, title: str) -> str:
    return f"{entity_id}::{_url_or_title_key(url, title)}"


# ---------------------------------------------------------------------------
# Priority-account universe
# ---------------------------------------------------------------------------

def _priority_accounts(graph: dict) -> tuple[list[dict], list[str]]:
    """The real, small priority-account universe: every customers_prospects
    account (any status -- pre_engagement shells included, not just
    active/current Blue Sheets) UNION every row in every vendor's Master
    Account Plan ranked_portfolio.json. Each resolved to its real
    ecosystem_intelligence.json brand entity (so its already-canonical
    name+aliases can be reused rather than re-tracked here) via
    ei._resolve_entity_id_any_type() -- never guessed; a name that doesn't
    resolve is reported in `unresolved`, not silently dropped or force-
    matched.

    Returns (accounts, unresolved_names). Each account dict:
    {entity_id, name, aliases, evidence_target}, where evidence_target is
    {"kind": "customers_prospects", "slug": ...} when a real
    customers_prospects account exists, else
    {"kind": "master_account_plan", "vendor_slug": ..., "account_name": ...}
    for a Master-Account-Plan-only account with no customers_prospects
    record yet.
    """
    by_id = ei._index_by_id(graph.get("entities") or [])
    known_entity_ids = set(by_id.keys())
    resolved: dict[str, dict] = {}
    unresolved: list[str] = []

    for entry in cpc.load_registry().get("registry", []):
        account_id = entry.get("account_id", "")
        slug = account_id.replace("acct-", "", 1)
        if not slug:
            continue
        try:
            account_json = cpc.load_account(slug).get("account") or {}
        except FileNotFoundError:
            account_json = {}
        display_name = account_json.get("display_name") or slug.replace("-", " ").title()
        if display_name.strip().lower() in _OWN_COMPANY_TERMS:
            continue  # Todd's own employer, not a priority customer account
        entity_id = ei._resolve_entity_id_any_type(display_name, graph)
        if not entity_id:
            unresolved.append(display_name)
            continue
        entity = by_id.get(entity_id, {})
        resolved[entity_id] = {
            "entity_id": entity_id,
            "name": entity.get("name") or display_name,
            "aliases": entity.get("aliases") or [],
            "evidence_target": {"kind": "customers_prospects", "slug": slug},
        }

    if mp_common is not None and mp_impact_review is not None:
        for mp_entry in mp_common.load_registry().get("registry", []):
            vendor_slug = mp_entry.get("vendor_slug")
            if not vendor_slug:
                continue
            portfolio_path = mp_common.vendor_dir(vendor_slug) / "ranked_portfolio.json"
            if not portfolio_path.exists():
                continue
            for row in mp_common.load_json(portfolio_path) or []:
                account_name = row.get("account_name", "")
                if account_name.strip().lower() in _OWN_COMPANY_TERMS:
                    continue  # Todd's own employer, not a priority customer account
                entity_slug = mp_impact_review._entity_slug_for_row(row, known_entity_ids)
                if not entity_slug:
                    unresolved.append(account_name)
                    continue
                entity_id = f"brand-{entity_slug}"
                if entity_id in resolved:
                    continue  # already covered via a real customers_prospects account
                entity = by_id.get(entity_id, {})
                resolved[entity_id] = {
                    "entity_id": entity_id,
                    "name": entity.get("name") or row.get("account_name", ""),
                    "aliases": entity.get("aliases") or [],
                    "evidence_target": {
                        "kind": "master_account_plan",
                        "vendor_slug": vendor_slug,
                        "account_name": row.get("account_name", ""),
                    },
                }

    return list(resolved.values()), unresolved


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _fetch_publisher_articles(name: str, fetcher=None) -> Optional[list[dict]]:
    """Fetch and parse Google News RSS results for one account name,
    restricted to INDUSTRY_PUBLISHER_SITES. Same shape as
    entity_alerts._fetch_entity()/_fetch_press_releases(). Returns None
    (not []) on a genuine fetch failure, so callers can tell "fetch failed"
    from "fetch succeeded, zero results" -- the two are reported
    separately in scan()'s coverage summary."""
    fetcher = fetcher or ea._fetch_with_ua
    query = f'"{name}" ({INDUSTRY_PUBLISHER_SITES})'
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": query, "hl": "en-US", "gl": "US", "ceid": "US:en",
    })
    raw = fetcher(url, 10)
    if raw is None:
        return None
    try:
        items = ws.parse_rss_items(raw, "Industry Publisher Search")
    except Exception:  # noqa: BLE001 -- malformed feed body is a fetch failure too, not a crash
        return None

    out = []
    for it in items[:10]:
        title = it.get("title", "")
        description = it.get("description", "")
        if not ea._mentions_entity(name, title, description):
            continue
        out.append({
            "title": title,
            "url": it.get("url", ""),
            "pub_date": it.get("pub_date", ""),
            "source_name": "Industry Publisher Search",
        })
    return out


# ---------------------------------------------------------------------------
# Materiality + dedup
# ---------------------------------------------------------------------------

def _known_vendor_names(graph: dict) -> list[str]:
    names: list[str] = []
    for e in graph.get("entities") or []:
        if e.get("entity_type") != "vendor":
            continue
        names.append(e.get("name", ""))
        names.extend(e.get("aliases") or [])
    return [n for n in names if n]


def _material_signal_class(title: str, known_vendor_names: list[str]) -> Optional[str]:
    """Reuses ecosystem_brief.py's existing signal taxonomy (the same
    materiality gate every other RB pipeline uses) rather than inventing a
    parallel one for this scanner alone. Trade-press mentions default to
    "medium" confidence, matching vendor_relationship()'s own
    SOURCE_QUALITY_MODEL default for this source_type. Returns the signal
    class if material, else None.

    RB-2026-09-10, confirmed live against real production search results:
    "vendor_relationship_formed"'s generic "rolls out"/"rolling out"/
    "deploys" keywords -- reliable inside ecosystem_intelligence.json's own
    already-curated signals array -- produce real false positives against
    raw open trade-press search, which covers every kind of restaurant news:
    "Slim Chickens Rolling Out Bacon Ranch Chicken Sandwich" and "Golden
    Corral rolls out brunch systemwide" both classified as
    vendor_relationship_formed despite naming zero vendors -- menu-item and
    program launches, not technology adoption. Every OTHER material class
    (leadership_change, funding_event, ...) produced zero false positives in
    the same real test -- their trigger phrases ("names X CEO", "raised",
    "acquisition") aren't ambiguous outside a vendor context the way "rolls
    out" is. Fix, scoped to this one class only: a vendor_relationship_formed
    match must also actually name a real, known vendor entity (or alias) --
    the same "never guess, cross-reference the real graph" discipline this
    session applies everywhere else."""
    sig_class = eb._classify_signal(title, "")
    if not eb._is_material_signal("medium", sig_class):
        return None
    if sig_class == "vendor_relationship_formed":
        if not any(ea._mentions_entity(name, title, "") for name in known_vendor_names):
            return None
    return sig_class


def _known_source_urls(graph: dict) -> set[str]:
    return {s.get("url") for s in graph.get("sources") or [] if s.get("url")}


def _already_captured(account: dict, url: str, known_source_urls: set[str]) -> bool:
    """True if this URL is already on file anywhere real -- either as a
    graph source (e.g. the Del Taco article, captured manually via a
    different path on 2026-09-10 before this scanner existed) or already
    linked as evidence on this specific account. Checked before a
    candidate is ever created, so a re-scan never re-proposes something
    already known regardless of which path first captured it."""
    if url in known_source_urls:
        return True
    target = account["evidence_target"]
    if target["kind"] != "customers_prospects":
        return False
    try:
        evidence_path = cpc.account_dir(target["slug"]) / "evidence.jsonl"
    except FileNotFoundError:
        return False
    if not evidence_path.exists():
        return False
    for record in cpc.load_jsonl(evidence_path):
        if record.get("durable_source_id") == url:
            return True
    return False


# ---------------------------------------------------------------------------
# Candidate store
# ---------------------------------------------------------------------------

def _add_candidate(store: dict, *, account: dict, article: dict, signal_class: str) -> bool:
    cid = _candidate_id(account["entity_id"], article["url"], article["title"])
    existing = store["candidates"].get(cid)
    if existing:
        return False  # already proposed (pending, confirmed, or rejected) -- never re-propose
    store["candidates"][cid] = {
        "candidate_id": cid,
        "status": "proposed_pending_confirmation",
        "entity_id": account["entity_id"],
        "account_name": account["name"],
        "evidence_target": account["evidence_target"],
        "signal_class": signal_class,
        "title": article["title"],
        "url": article["url"],
        "pub_date": article["pub_date"],
        "source_name": article["source_name"],
        "detected_at": _now_iso(),
        "resolved_at": None,
    }
    return True


def pending_candidates() -> list[dict]:
    store = _load_store()
    return [c for c in store["candidates"].values() if c["status"] == "proposed_pending_confirmation"]


def record_proposal(candidate_id: str, *, confirmed: bool, confirmed_by: str = "human") -> dict:
    """Confirm (append a fact-free reference-evidence entry, same shape as
    account_reference_detector.link_references()'s Blue Sheet branch) or
    reject one pending candidate."""
    store = _load_store()
    cand = store["candidates"].get(candidate_id)
    if not cand:
        return {"error": f"unknown candidate id: {candidate_id}"}
    if cand["status"] != "proposed_pending_confirmation":
        return {"error": f"candidate {candidate_id} already resolved: {cand['status']}"}

    if not confirmed:
        cand["status"] = "rejected"
        cand["resolved_at"] = _now_iso()
        _save_store(store)
        return {"rejected": True, "candidate_id": candidate_id}

    target = cand["evidence_target"]
    if target["kind"] == "customers_prospects":
        slug = target["slug"]
        evidence_path = cpc.account_dir(slug) / "evidence.jsonl"
        existing_evidence = cpc.load_jsonl(evidence_path) if evidence_path.exists() else []
        record = {
            "evidence_id": cpc.next_evidence_id(slug, existing_evidence),
            "account_id": f"acct-{slug}",
            "opportunity_ids": [],
            "source_type": "priority_account_publisher_match",
            "durable_source_id": cand["url"],
            "source_author": cand.get("source_name"),
            "participants": [],
            "event_date": cand.get("pub_date") or None,
            "ingestion_date": cpc.today(),
            "excerpt": cand["title"][:400],
            "extracted_claims": [],
            "evidence_class": "auto_linked_reference",
            "confidence": None,
            "scope": f"account:acct-{slug}",
            "limitations": (
                "Auto-linked by name match against a trade-publisher search only -- "
                "no specific fact extracted or claimed. Review the source and use "
                "addBlueSheetEvidence to record real terms."
            ),
            "contradiction_links": [],
            "processing_version": "priority_account_publisher_scan-v1",
            "_source_title": cand["title"],
        }
        cpc.append_jsonl(evidence_path, record)
        reg = cpc.load_registry()
        for entry in reg.get("registry", []):
            if entry.get("account_id") == f"acct-{slug}":
                entry["last_evidence_date"] = cand.get("pub_date") or cpc.today()
                break
        cpc.save_json(cpc.registry_path(), reg)
    elif target["kind"] == "master_account_plan" and mp_common is not None:
        vendor_slug = target["vendor_slug"]
        evidence_path = mp_common.vendor_dir(vendor_slug) / "evidence.jsonl"
        existing_evidence = mp_common.load_jsonl(evidence_path) if evidence_path.exists() else []
        record = {
            "evidence_id": mp_common.next_evidence_id(vendor_slug, existing_evidence),
            "logged_at": mp_common.now_iso(),
            "account_name": target["account_name"],
            "signal_summary": cand["title"][:400],
            "signal_type": "priority_account_publisher_match",
            "event_at": cand.get("pub_date"),
        }
        mp_common.append_jsonl(evidence_path, record)
    else:
        return {"error": f"candidate {candidate_id} has no writable evidence target"}

    cand["status"] = "confirmed"
    cand["resolved_at"] = _now_iso()
    cand["confirmed_by"] = confirmed_by
    _save_store(store)
    return {"confirmed": True, "candidate_id": candidate_id}


# ---------------------------------------------------------------------------
# Scan entry point
# ---------------------------------------------------------------------------

def scan(*, dry_run: bool = False, fetcher=None) -> dict:
    graph = ei._read_graph()
    accounts, unresolved = _priority_accounts(graph)
    known_source_urls = _known_source_urls(graph)
    known_vendor_names = _known_vendor_names(graph)
    store = _load_store()

    candidates_found = 0
    material_queued = 0
    duplicates_skipped = 0
    fetch_failed_accounts: set[str] = set()
    to_auto_apply: list[str] = []

    for account in accounts:
        # Query the canonical name AND every real alias -- an account is
        # matched by whichever name a publisher actually used (RB-DEFECT-069's
        # own "alias match" acceptance criterion). Cross-variant duplicate
        # finds (the same article surfacing under two different name
        # queries) are naturally deduped below via _add_candidate()'s
        # candidate_id, which keys on entity_id+URL, not on which name query
        # found it.
        name_variants = list(dict.fromkeys([account["name"]] + list(account["aliases"])))
        for variant in name_variants:
            articles = _fetch_publisher_articles(variant, fetcher=fetcher)
            if articles is None:
                fetch_failed_accounts.add(account["name"])
                continue
            for article in articles:
                if _already_captured(account, article["url"], known_source_urls):
                    duplicates_skipped += 1
                    continue
                signal_class = _material_signal_class(article["title"], known_vendor_names)
                if signal_class is None:
                    continue
                candidates_found += 1
                added = _add_candidate(store, account=account, article=article, signal_class=signal_class)
                if added:
                    material_queued += 1
                    to_auto_apply.append(
                        _candidate_id(account["entity_id"], article["url"], article["title"])
                    )
                else:
                    duplicates_skipped += 1

    auto_applied = 0
    if not dry_run:
        _save_store(store)
        for cid in to_auto_apply:
            result = record_proposal(cid, confirmed=True, confirmed_by="system:priority_account_publisher_scan")
            if result.get("confirmed"):
                auto_applied += 1

    return {
        "accounts_checked": len(accounts),
        "publishers_checked": INDUSTRY_PUBLISHER_SITES.count(" OR ") + 1,
        "candidates_found": candidates_found,
        "material_queued": material_queued,
        "auto_applied": auto_applied,
        "duplicates_skipped": duplicates_skipped,
        "fetch_failures": sorted(fetch_failed_accounts),
        "unresolved_accounts": sorted(set(unresolved)),
        "total_pending": sum(1 for c in store["candidates"].values() if c["status"] == "proposed_pending_confirmation"),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    sub.add_parser("pending")
    confirm_p = sub.add_parser("confirm")
    confirm_p.add_argument("candidate_id")
    reject_p = sub.add_parser("reject")
    reject_p.add_argument("candidate_id")
    args = p.parse_args()

    if args.cmd == "scan":
        print(json.dumps(scan(), indent=2, ensure_ascii=False))
    elif args.cmd == "pending":
        print(json.dumps(pending_candidates(), indent=2, ensure_ascii=False))
    elif args.cmd == "confirm":
        print(json.dumps(record_proposal(args.candidate_id, confirmed=True), indent=2, ensure_ascii=False))
    elif args.cmd == "reject":
        print(json.dumps(record_proposal(args.candidate_id, confirmed=False), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
