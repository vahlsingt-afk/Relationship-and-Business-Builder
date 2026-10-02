#!/usr/bin/env python3
"""RI assessment producer for market signal rows.

Implements P-036 §RI Assessment Contract for market/news/company signals.
Called by market_signals.py after enrichment to produce a structured
ri_assessment block for each signal row that mentions people, companies,
opportunities, operators, or active threads.

Integration:
    from market_signals_ri import assess_market_signal_ri

    enriched_row = ...  # output of market_signals normalize + enrich
    enriched_row["ri_assessment"] = assess_market_signal_ri(enriched_row)

Rules (P-036 + P-037):
- Map companies in market rows to: active threads, baseline contacts,
  circles/watchlists, strategic operators.
- Status: proposed / blocked / irrelevant / unavailable.
- Do NOT create touch/contact mutations from market news alone.
- Manual/user-provided evidence amplifies confidence but stays user_uploaded_artifact.
- Source freshness gates confidence at 0.55 for stale sources.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import rb_core as core  # noqa: E402
    SYSTEM_DIR = core.SYSTEM_DIR
    BASELINE_INDEX_PATH = core.SYSTEM_DIR / "baseline_index.json"
    ACTIVE_THREADS_PATH = core.SYSTEM_DIR / "active_threads.yaml"
    WATCHLIST_PATH = core.SYSTEM_DIR / "restaurant_tech_watchlist.md"
except Exception:
    _HERE = Path(__file__).resolve().parent.parent
    SYSTEM_DIR = _HERE
    BASELINE_INDEX_PATH = _HERE / "baseline_index.json"
    ACTIVE_THREADS_PATH = _HERE / "active_threads.yaml"
    WATCHLIST_PATH = _HERE / "restaurant_tech_watchlist.md"


# ---------------------------------------------------------------------------
# Known strategic company → thread/watchlist mappings
# These are the compile-time mappings RB can use without loading full state.
# When baseline_index.json / active_threads.yaml are available, they augment.
# ---------------------------------------------------------------------------

# Map canonical company names to active thread IDs or descriptors
_COMPANY_TO_THREAD: dict[str, list[str]] = {
    "Global Payments": ["thread:genius-global-payments-restaurant-ai"],
    "Genius": ["thread:genius-global-payments-restaurant-ai"],
    "Toast": ["thread:restaurant-tech-pos-vendor-landscape"],
    "Dragontail": ["thread:pizza-hut-yum-dragontail-lawsuit"],
    "Yum Brands": ["thread:pizza-hut-yum-dragontail-lawsuit"],
    "Pizza Hut": ["thread:pizza-hut-yum-dragontail-lawsuit"],
    "Chaac Pizza Northeast": ["thread:pizza-hut-yum-dragontail-lawsuit"],
}

# Company → watchlist categories (operator-demand side = target accounts)
_WATCHLIST_OPERATORS: set[str] = {
    "McDonald's", "Starbucks", "Yum Brands", "Pizza Hut", "Taco Bell", "KFC",
    "Burger King", "Domino's", "Chick-fil-A", "Chipotle", "Shake Shack",
    "Wingstop", "Jack in the Box", "Sonic", "Panera", "Subway",
    "Darden", "Olive Garden", "Applebee's", "IHOP", "Dine Brands",
    "Chaac Pizza Northeast",
}

_WATCHLIST_VENDORS: set[str] = {
    "Toast", "Square", "NCR", "Oracle", "Revel", "Lightspeed", "Shift4",
    "Heartland", "Global Payments", "Genius", "Worldpay", "Fiserv",
    "Paytronix", "Punchh", "Olo", "Qu", "HungerRush", "Dragontail",
    "Presto", "SoundHound", "Xenial", "Tillster", "Smooth Commerce",
}

# Source freshness threshold in hours (matches P-036 / P-024)
_FRESHNESS_THRESHOLD_H = 48


# ---------------------------------------------------------------------------
# State loaders (graceful degradation)
# ---------------------------------------------------------------------------

def _load_baseline_index() -> dict[str, Any]:
    try:
        return json.loads(BASELINE_INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_active_threads() -> list[dict[str, Any]]:
    """Load active threads from YAML or JSON. Graceful on missing file."""
    if not ACTIVE_THREADS_PATH.exists():
        # Try JSON fallback
        json_path = ACTIVE_THREADS_PATH.with_suffix(".json")
        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                return data if isinstance(data, list) else data.get("threads", [])
            except Exception:
                return []
        return []
    # Minimal YAML parser for thread objects
    raw = ACTIVE_THREADS_PATH.read_text(encoding="utf-8")
    threads = []
    current: dict[str, Any] = {}
    for line in raw.splitlines():
        if line.startswith("- id:"):
            if current:
                threads.append(current)
            current = {"id": line.split(":", 1)[1].strip()}
        elif ":" in line and current:
            key, _, val = line.partition(":")
            current[key.strip()] = val.strip()
    if current:
        threads.append(current)
    return threads


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def _extract_companies_from_row(row: dict[str, Any]) -> list[str]:
    """Return normalized company names mentioned in the signal row."""
    companies: list[str] = []
    company_field = str(row.get("company") or "")
    for part in company_field.split("/"):
        part = part.strip()
        if part:
            companies.append(part)
    return companies


def _source_is_fresh(row: dict[str, Any]) -> bool:
    """Return True if the signal's capture/fetch timestamp is within threshold."""
    ts_str = row.get("fetched_at") or row.get("captured_at") or row.get("published_at")
    if not ts_str:
        return False
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(str(ts_str)[:19] + "+00:00", "%Y-%m-%dT%H:%M:%S%z")
            cutoff = datetime.now(timezone.utc) - timedelta(hours=_FRESHNESS_THRESHOLD_H)
            return dt >= cutoff
        except ValueError:
            pass
    return False


def _thread_match(
    companies: list[str],
    threads: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Return (matched_thread_ids, evidence_strings)."""
    matched_ids = []
    evidence = []

    # Static compile-time mapping first
    for company in companies:
        for thread_id in _COMPANY_TO_THREAD.get(company, []):
            if thread_id not in matched_ids:
                matched_ids.append(thread_id)
                evidence.append(f"{company} matches static thread {thread_id}")

    # Dynamic from active_threads.yaml
    for thread in threads:
        thread_id = thread.get("id") or thread.get("thread_id") or ""
        participants = str(thread.get("companies") or thread.get("participants") or "")
        for company in companies:
            if company.lower() in participants.lower():
                if thread_id and thread_id not in matched_ids:
                    matched_ids.append(thread_id)
                    evidence.append(f"{company} matches active thread {thread_id}")

    return matched_ids, evidence


def _watchlist_match(companies: list[str]) -> tuple[bool, bool, list[str]]:
    """Return (is_operator, is_vendor, evidence)."""
    is_operator = any(c in _WATCHLIST_OPERATORS for c in companies)
    is_vendor = any(c in _WATCHLIST_VENDORS for c in companies)
    ev = []
    if is_operator:
        matched = [c for c in companies if c in _WATCHLIST_OPERATORS]
        ev.append(f"Watchlist operator match: {', '.join(matched)}")
    if is_vendor:
        matched = [c for c in companies if c in _WATCHLIST_VENDORS]
        ev.append(f"Watchlist vendor match: {', '.join(matched)}")
    return is_operator, is_vendor, ev


def _contact_match(
    companies: list[str],
    baseline: dict[str, Any] | list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Match companies against baseline contacts. Return (contact_ids, evidence)."""
    contacts = baseline if isinstance(baseline, list) else baseline.get("contacts") or []
    matched_ids = []
    evidence = []
    for contact in contacts:
        contact_company = str(contact.get("company") or "").strip()
        # An empty string is a substring of every company name.  Skipping
        # employer-less contacts prevents one market signal from mapping to the
        # entire baseline and keeps the morning refresh bounded.
        if not contact_company:
            continue
        contact_id = contact.get("contact_id") or contact.get("id") or ""
        for company in companies:
            company = str(company or "").strip()
            if not company:
                continue
            if company.lower() in contact_company.lower() or contact_company.lower() in company.lower():
                if contact_id and contact_id not in matched_ids:
                    matched_ids.append(contact_id)
                    evidence.append(
                        f"{contact.get('name', 'Unknown')} ({contact_company}) matches {company}"
                    )
    return matched_ids, evidence


# ---------------------------------------------------------------------------
# RI assessment producer
# ---------------------------------------------------------------------------

def assess_market_signal_ri(
    row: dict[str, Any],
    *,
    baseline: dict[str, Any] | None = None,
    threads: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Produce a P-036-compatible ri_assessment block for a market signal row.

    Rules:
    - proposed: relationship/action consequence is plausible (thread or watchlist match)
    - blocked: entity exists but no relationship path is resolved
    - irrelevant: no action consequence
    - unavailable: source freshness prevents a confident claim

    Does NOT create touch/contact mutations. Read-only assessment.
    """
    if baseline is None:
        baseline = _load_baseline_index()
    if threads is None:
        threads = _load_active_threads()

    companies = _extract_companies_from_row(row)
    fresh = _source_is_fresh(row)
    source_freshness = "fresh" if fresh else "stale"

    # Confidence base — capped at 0.55 for stale (P-036)
    conf_base = float({"high": 0.82, "medium": 0.65, "low": 0.45}.get(
        str(row.get("confidence") or "medium").lower(), 0.65
    ))
    if not fresh:
        conf_base = min(conf_base, 0.55)

    if not companies:
        return {
            "status": "irrelevant",
            "source": "market_signals",
            "source_freshness": source_freshness,
            "confidence": round(conf_base * 0.5, 2),
            "evidence": ["No identifiable company in signal row"],
            "mapped_contact_ids": [],
            "mapped_thread_ids": [],
            "proposed_mutation": {"command": None, "payload": {}},
            "display_recommendation": "suppress",
            "reason": "Signal row has no mapped company; no RI consequence assessed.",
        }

    # Perform matching
    thread_ids, thread_evidence = _thread_match(companies, threads)
    contact_ids, contact_evidence = _contact_match(companies, baseline)
    is_operator, is_vendor, watchlist_evidence = _watchlist_match(companies)

    all_evidence = thread_evidence + contact_evidence + watchlist_evidence
    has_relationship_path = bool(thread_ids or contact_ids)
    has_watchlist_hit = is_operator or is_vendor

    # Status determination
    strategic_relevance = str(row.get("strategic_relevance") or "low").lower()

    if has_relationship_path and strategic_relevance in ("high", "medium"):
        status = "proposed"
        confidence = min(conf_base, 0.85) if fresh else conf_base
        display = "show"
        reason = (
            f"Signal maps to active thread(s) {thread_ids or contact_ids}. "
            f"Strategic relevance: {strategic_relevance}. "
            "Market news consequence is plausible — verify before action."
        )
    elif has_watchlist_hit and strategic_relevance == "high":
        status = "proposed"
        confidence = min(conf_base * 0.85, 0.70) if fresh else min(conf_base, 0.55)
        display = "show"
        reason = (
            f"Watchlist entity match ({'operator' if is_operator else 'vendor'}). "
            f"No direct contact match, but strategic relevance is high."
        )
    elif has_watchlist_hit or has_relationship_path:
        status = "blocked"
        confidence = conf_base * 0.7
        display = "suppress_unless_asked"
        reason = (
            f"Watchlist/contact entity present but no actionable relationship path resolved. "
            f"Relevance: {strategic_relevance}."
        )
    elif not fresh:
        status = "unavailable"
        confidence = min(conf_base, 0.40)
        display = "suppress"
        reason = "Source freshness prevents confident RI claim. Signal is stale."
    else:
        status = "irrelevant"
        confidence = conf_base * 0.4
        display = "suppress"
        reason = (
            f"No relationship or watchlist match found for companies: {companies}. "
            "No action consequence assessed."
        )

    # Map signal_type to human-readable consequence
    signal_type = str(row.get("signal_type") or "market_update")
    consequence_map: dict[str, str] = {
        "lawsuit": "Legal/reputational risk signal — monitor and consider positioning",
        "implementation_failure": "Implementation risk signal — thesis reinforcement opportunity",
        "pilot_rollback": "Rollback risk signal — vendor trust and incumbent evaluation",
        "product_launch": "Competitive launch — watch for operator adoption signals",
        "operator_priority": "Operator priority shift — relationship entry point possible",
        "partnership": "Partnership signal — integration/competitive intelligence",
        "earnings_signal": "Earnings signal — strategic priority extraction possible",
    }
    consequence = consequence_map.get(signal_type, "Market signal — monitor for relevance")
    if status == "proposed":
        all_evidence.append(consequence)

    return {
        "status": status,
        "source": "market_signals",
        "source_freshness": source_freshness,
        "confidence": round(min(max(confidence, 0.0), 0.98), 2),
        "evidence": all_evidence or [f"Signal about {', '.join(companies)}; no specific RI path"],
        "mapped_contact_ids": contact_ids,
        "mapped_thread_ids": thread_ids,
        "proposed_mutation": {
            "command": None,
            "payload": {},
        },
        "display_recommendation": display,
        "reason": reason,
        "watchlist_match": {
            "is_operator": is_operator,
            "is_vendor": is_vendor,
        },
    }


# ---------------------------------------------------------------------------
# Batch helper (for market_signals.py integration)
# ---------------------------------------------------------------------------

def enrich_with_ri(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Add ri_assessment to each row. Call this after normalize/enrich in market_signals.py.

    Integration point in market_signals.py:
        from market_signals_ri import enrich_with_ri
        enriched_rows = enrich_with_ri(all_ranked)
    """
    baseline = _load_baseline_index()
    threads = _load_active_threads()
    out = []
    for row in rows:
        row = dict(row)
        row["ri_assessment"] = assess_market_signal_ri(row, baseline=baseline, threads=threads)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# CLI (smoke test / standalone)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    fixtures = [
        {
            "title": "Global Payments Genius AI upsell for QSR",
            "company": "Global Payments / Genius",
            "strategic_relevance": "high",
            "confidence": "medium",
            "signal_type": "product_launch",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
        {
            "title": "Pizza Hut / Yum / Dragontail lawsuit",
            "company": "Pizza Hut / Yum Brands / Dragontail / Chaac Pizza Northeast",
            "strategic_relevance": "high",
            "confidence": "high",
            "signal_type": "lawsuit",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
        {
            "title": "Starbucks dumps AI inventory tool",
            "company": "Starbucks",
            "strategic_relevance": "high",
            "confidence": "medium",
            "signal_type": "pilot_rollback",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
        {
            "title": "Generic restaurant industry news",
            "company": "",
            "strategic_relevance": "low",
            "confidence": "low",
            "signal_type": "market_update",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
    ]
    results = enrich_with_ri(fixtures)
    for r in results:
        ri = r["ri_assessment"]
        print(f"[{ri['status']:14s}] {r['title'][:55]:55s} conf={ri['confidence']:.2f}  display={ri['display_recommendation']}")
