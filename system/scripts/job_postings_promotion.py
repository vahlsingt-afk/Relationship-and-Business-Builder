#!/usr/bin/env python3
"""
job_postings_promotion.py — RB-2026-09-18, next-sprint Workstream 3.

Job postings as a leading-indicator source: a customers_prospects account
posting a "Director of Restaurant Technology" or "POS Program Manager"
role is a real pre-signal of an impending tech-stack evaluation, before any
press release or executive move confirms it. vulnerability_taxonomy.py's
tech_hiring category already classifies exactly this kind of role title and
is already wired into entity_alerts.py/competitive_vulnerability.py's
scoring -- but that pipeline only ever sees Google News RSS results, which
never surface a job board posting itself (job boards aren't news). This
module is the missing acquisition step, not a new taxonomy: it finds real
postings and creates review-first candidates; it does not touch
entity_alerts.py's live vulnerability-scoring pipeline at all (that pipeline
recomputes vulnerability_items fresh, unattended, with no confirm gate --
gating THIS module's candidates behind record_proposal() and then also
feeding the live pipeline would mean the same signal reaches Todd through
two different trust postures for no real benefit).

Acquisition: no paid job-board API (LinkedIn Jobs/Indeed require one).
Uses DuckDuckGo's no-API-key HTML search restricted to known ATS domains
(greenhouse/lever/ashby/workday/icims -- the same domain set
job_intelligence.py already recognizes for a different purpose, personal
job-search message classification), the identical discovery pattern
earnings_monitor.py already proved for Motley Fool transcripts
(_search_fool_transcript_url) -- unofficial, best-effort, degrades to
"nothing found" on any failure rather than raising.

Structurally mirrors priority_account_publisher_scan.py (fetch + classify +
review-first candidate store, all in one file -- there is no separate
persisted "raw signal" layer to mine the way executive_move_promotion.py
mines ecosystem_intelligence.json's signals array, so the two-file
ingest/promotion split used elsewhere doesn't apply here). Same discipline
as every other promotion script in this codebase: never invents a fact.
Confirming appends a lightweight, fact-free reference-evidence entry to the
account's own evidence.jsonl -- same shape as priority_account_publisher_
scan.py's own confirm -- never a structured tech-stack claim (a job posting
asserts nothing about what a brand currently runs, only that it may be
evaluating something).

Scope, deliberately narrow for v1: customers_prospects accounts only (real
prospects/customers Todd actively tracks, where this is a genuine sales-
timing signal). Competitor/vendor tech-hiring intelligence is a DIFFERENT
narrative (a competitor's own roadmap, not a sales trigger) and is out of
scope here -- a natural v2 extension, not built to avoid conflating two
different "why this matters" stories in one candidate stream.

Confidence-Based Auto-Recording Phase 6/7 (2026-09-25): wired into
getJobPostingCandidates/confirmProposal (kind="job_posting"), same as its
sibling promotion modules. Confirming here never claims a fact -- only
links a source and bumps last_evidence_date (extracted_claims is always
[]), the same purely-additive shape as priority_account_publisher_scan.py
-- so scan() now auto-confirms every material, non-duplicate candidate
immediately, tagged "system:job_postings_promotion". The candidate store
is an audit trail of every posting ever linked this way, not a review
queue.

CLI:
    python3 job_postings_promotion.py scan            # scan + write candidates, print summary
    python3 job_postings_promotion.py pending          # list pending candidates
    python3 job_postings_promotion.py confirm <id>     # write the evidence reference
    python3 job_postings_promotion.py reject <id>
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
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
import ecosystem_intelligence as ei  # noqa: E402
import entity_alerts as ea  # noqa: E402
import vulnerability_taxonomy as vt  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402

STORE_PATH = core.CACHE_DIR / "job_postings_candidates.json"

# Same ATS domains job_intelligence.py already recognizes (JOB_SIGNAL_PATTERNS),
# reused here for the opposite purpose: acquisition, not message classification.
JOB_BOARD_SITES = (
    "site:boards.greenhouse.io OR site:jobs.lever.co OR site:jobs.ashbyhq.com "
    "OR site:myworkdayjobs.com OR site:icims.com"
)

_BROWSER_LIKE_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_FETCH_TIMEOUT = 15  # seconds

# Todd's own employer -- a job posting at Genius/Worldpay is not competitive
# or customer intelligence. Same exclusion tech_stack_relationship_promotion.py
# and priority_account_publisher_scan.py already apply for the same reason.
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


def _url_key(url: str, title: str) -> str:
    basis = (url or "").strip().lower() or (title or "").strip().lower()
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def _candidate_id(entity_id: str, url: str, title: str) -> str:
    return f"{entity_id}::{_url_key(url, title)}"


# ---------------------------------------------------------------------------
# Target universe -- customers_prospects accounts only (see module docstring)
# ---------------------------------------------------------------------------

def _target_accounts(graph: dict) -> tuple[list[dict], list[str]]:
    by_id = ei._index_by_id(graph.get("entities") or [])
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
            continue
        entity_id = ei._resolve_entity_id_any_type(display_name, graph)
        if not entity_id:
            unresolved.append(display_name)
            continue
        entity = by_id.get(entity_id, {})
        resolved[entity_id] = {
            "entity_id": entity_id,
            "name": entity.get("name") or display_name,
            "aliases": entity.get("aliases") or [],
            "slug": slug,
        }
    return list(resolved.values()), unresolved


# ---------------------------------------------------------------------------
# Fetch — DuckDuckGo HTML search, no API key, restricted to ATS domains
# ---------------------------------------------------------------------------

def _fetch_ddg_html(url: str) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_LIKE_USER_AGENT})
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, UnicodeDecodeError):
        return None


_RESULT_RE = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)


def _search_job_postings(company: str, fetcher=None) -> Optional[list[dict]]:
    """Best-effort discovery of real ATS-hosted job postings for `company`
    via DuckDuckGo's no-API-key HTML search -- same discovery pattern
    earnings_monitor._search_fool_transcript_url() already proved (unofficial,
    could break/rate-limit without notice, so every step degrades to None on
    failure rather than raising). Returns None (not []) on a genuine fetch
    failure so callers can tell "fetch failed" from "fetch succeeded, zero
    results" -- same distinction _fetch_publisher_articles() makes."""
    fetcher = fetcher or _fetch_ddg_html
    query = f'"{company}" ({JOB_BOARD_SITES})'
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    body = fetcher(url)
    if body is None:
        return None

    out = []
    for href, title_html in _RESULT_RE.findall(body):
        match = re.search(r"uddg=([^&]+)", href)
        real_url = urllib.parse.unquote(match.group(1)) if match else href
        title = html.unescape(re.sub(r"<[^>]+>", "", title_html)).strip()
        if not title or not real_url:
            continue
        if not ea._mentions_entity(company, title, ""):
            continue
        out.append({"title": title, "url": real_url, "source_name": "DuckDuckGo Job Board Search"})
    return out[:10]


# ---------------------------------------------------------------------------
# Materiality + dedup
# ---------------------------------------------------------------------------

def _tech_hiring_signal(title: str) -> bool:
    """Reuses vulnerability_taxonomy.py's own tech_hiring classifier as the
    quality gate -- the same category entity_alerts.py/competitive_
    vulnerability.py already score, so a generic "Shift Manager" or "Server"
    posting (the overwhelming majority of any real career page) never
    reaches the candidate queue."""
    return vt.classify_vulnerability(title, "") == "tech_hiring"


def _known_source_urls(graph: dict) -> set[str]:
    return {s.get("url") for s in graph.get("sources") or [] if s.get("url")}


def _already_captured(account: dict, url: str, known_source_urls: set[str]) -> bool:
    if url in known_source_urls:
        return True
    try:
        evidence_path = cpc.account_dir(account["slug"]) / "evidence.jsonl"
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

def _add_candidate(store: dict, *, account: dict, posting: dict) -> bool:
    cid = _candidate_id(account["entity_id"], posting["url"], posting["title"])
    if cid in store["candidates"]:
        return False  # already proposed (pending, confirmed, or rejected) -- never re-propose
    store["candidates"][cid] = {
        "candidate_id": cid,
        "status": "proposed_pending_confirmation",
        "entity_id": account["entity_id"],
        "account_name": account["name"],
        "account_slug": account["slug"],
        "vulnerability_category": "tech_hiring",
        "role_title": posting["title"],
        "url": posting["url"],
        "source_name": posting["source_name"],
        "detected_at": _now_iso(),
        "resolved_at": None,
    }
    return True


def pending_candidates() -> list[dict]:
    store = _load_store()
    return [c for c in store["candidates"].values() if c["status"] == "proposed_pending_confirmation"]


def record_proposal(candidate_id: str, *, confirmed: bool, confirmed_by: str = "human") -> dict:
    """Confirm (append a fact-free reference-evidence entry to the account's
    evidence.jsonl, same shape as priority_account_publisher_scan.py's own
    confirm) or reject one pending candidate. Never writes a structured
    tech-stack claim -- a job posting asserts nothing about what a brand
    currently runs, only that it may be evaluating something."""
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

    slug = cand["account_slug"]
    evidence_path = cpc.account_dir(slug) / "evidence.jsonl"
    existing_evidence = cpc.load_jsonl(evidence_path) if evidence_path.exists() else []
    record = {
        "evidence_id": cpc.next_evidence_id(slug, existing_evidence),
        "account_id": f"acct-{slug}",
        "opportunity_ids": [],
        "source_type": "job_postings_promotion_match",
        "durable_source_id": cand["url"],
        "source_author": cand.get("source_name"),
        "participants": [],
        "event_date": None,
        "ingestion_date": cpc.today(),
        "excerpt": cand["role_title"][:400],
        "extracted_claims": [],
        "evidence_class": "auto_linked_reference",
        "confidence": None,
        "scope": f"account:acct-{slug}",
        "limitations": (
            "Auto-linked by a job-board search match on a tech-hiring-shaped role "
            "title only -- no tech-stack fact extracted or claimed. Review the "
            "posting and use addBlueSheetEvidence to record anything specific."
        ),
        "contradiction_links": [],
        "processing_version": "job_postings_promotion-v1",
        "_source_title": cand["role_title"],
    }
    cpc.append_jsonl(evidence_path, record)
    reg = cpc.load_registry()
    for entry in reg.get("registry", []):
        if entry.get("account_id") == f"acct-{slug}":
            entry["last_evidence_date"] = cpc.today()
            break
    cpc.save_json(cpc.registry_path(), reg)

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
    accounts, unresolved = _target_accounts(graph)
    known_source_urls = _known_source_urls(graph)
    store = _load_store()

    candidates_found = 0
    material_queued = 0
    duplicates_skipped = 0
    fetch_failed_accounts: set[str] = set()
    to_auto_apply: list[str] = []

    for account in accounts:
        name_variants = list(dict.fromkeys([account["name"]] + list(account["aliases"])))
        for variant in name_variants:
            postings = _search_job_postings(variant, fetcher=fetcher)
            if postings is None:
                fetch_failed_accounts.add(account["name"])
                continue
            for posting in postings:
                if _already_captured(account, posting["url"], known_source_urls):
                    duplicates_skipped += 1
                    continue
                if not _tech_hiring_signal(posting["title"]):
                    continue
                candidates_found += 1
                added = _add_candidate(store, account=account, posting=posting)
                if added:
                    material_queued += 1
                    to_auto_apply.append(
                        _candidate_id(account["entity_id"], posting["url"], posting["title"])
                    )
                else:
                    duplicates_skipped += 1

    auto_applied = 0
    if not dry_run:
        _save_store(store)
        for cid in to_auto_apply:
            result = record_proposal(cid, confirmed=True, confirmed_by="system:job_postings_promotion")
            if result.get("confirmed"):
                auto_applied += 1

    return {
        "accounts_checked": len(accounts),
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
    p_confirm = sub.add_parser("confirm")
    p_confirm.add_argument("candidate_id")
    p_reject = sub.add_parser("reject")
    p_reject.add_argument("candidate_id")
    args = p.parse_args()

    if args.cmd == "scan":
        print(json.dumps(scan(), indent=2))
    elif args.cmd == "pending":
        print(json.dumps(pending_candidates(), indent=2))
    elif args.cmd == "confirm":
        print(json.dumps(record_proposal(args.candidate_id, confirmed=True), indent=2))
    elif args.cmd == "reject":
        print(json.dumps(record_proposal(args.candidate_id, confirmed=False), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
