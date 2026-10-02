#!/usr/bin/env python3
"""Strategic industry event convergence.

Market rows, LinkedIn posts, company releases, analyst notes, and user
artifacts are evidence. RB should merge evidence into strategic events before
the daily brief decides what matters.

Protocol: P-037 — Strategic Event Convergence
Schema:   system/strategic_events.json (rb_strategic_events_v1)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core  # noqa: E402


EVENTS_PATH = Path(os.environ.get(
    "RB_STRATEGIC_EVENTS_PATH", str(core.SYSTEM_DIR / "strategic_events.json")
))
LINKEDIN_DAILY_PATH = core.INBOX_DIR / "linkedin.daily_signals.jsonl"
FEEDER_OUTPUT_PATH = core.INBOX_DIR / "market_signals_feed.jsonl"


# ---------------------------------------------------------------------------
# Source channel weights (P-037 §Source weight table)
# ---------------------------------------------------------------------------

SOURCE_WEIGHT: dict[str, float] = {
    "restaurant_trade_news": 0.24,
    "enterprise_restaurant_technology_news": 0.24,
    "legal_business_news": 0.22,
    "company_press": 0.18,
    "analyst_commentary": 0.18,
    "earnings_call": 0.20,
    "linkedin_social": 0.16,
    "network_conversation": 0.16,
    "podcast_interview": 0.14,
    "user_uploaded_artifact": 0.12,
}

PUBLIC_SOURCE_TYPES: dict[str, str] = {
    "vertical_trade": "restaurant_trade_news",
    "enterprise_restaurant_technology": "enterprise_restaurant_technology_news",
    "public_company_primary": "company_press",
    "company_blog": "company_press",
    "mainstream": "legal_business_news",
    "press_pickup": "legal_business_news",
    "linkedin_social": "linkedin_social",
    "event_signal": "enterprise_restaurant_technology_news",
    "legal_business_news": "legal_business_news",
}

# Freshness windows per channel in hours (P-037 §Source Freshness Gates)
FRESHNESS_HOURS: dict[str, int] = {
    "restaurant_trade_news": 48,
    "enterprise_restaurant_technology_news": 48,
    "legal_business_news": 72,
    "linkedin_social": 24,
    "company_press": 72,
}
_DEFAULT_FRESHNESS_HOURS = 48


# ---------------------------------------------------------------------------
# Company alias normalization (P-037 §Company normalization)
# ---------------------------------------------------------------------------

COMPANY_ALIASES: dict[str, str] = {
    # Yum family
    "yum": "Yum Brands",
    "yum brands": "Yum Brands",
    "yum! brands": "Yum Brands",
    "pizza hut u.s.": "Pizza Hut",
    "ph": "Pizza Hut",
    "dragontail systems": "Dragontail",
    "dragontail technologies": "Dragontail",
    "chaac pizza": "Chaac Pizza Northeast",
    "cpne": "Chaac Pizza Northeast",
    # Payments
    "globalpay": "Global Payments",
    "global payments inc.": "Global Payments",
    "global payments inc": "Global Payments",
    "genius pos": "Genius",
    "genius by global payments": "Genius",
    # QSR
    "mcdonalds": "McDonald's",
    "mcd": "McDonald's",
    "sbux": "Starbucks",
    "starbucks coffee": "Starbucks",
    "dq": "Dairy Queen",
    "bk": "Burger King",
    "kfc": "KFC",
    "tbell": "Taco Bell",
    # Tech
    "toast pos": "Toast",
    "toast inc.": "Toast",
    "toast inc": "Toast",
    "olo inc": "Olo",
    "hunger rush": "HungerRush",
    "smooth commerce inc": "Smooth Commerce",
    "smooth commerce inc.": "Smooth Commerce",
}

# Canonical company names for entity extraction
KNOWN_COMPANIES: list[str] = [
    "Pizza Hut", "Yum Brands", "Dragontail", "Chaac Pizza Northeast",
    "Starbucks", "McDonald's", "Taco Bell", "KFC", "Burger King",
    "Popeyes", "Restaurant Brands", "Domino's", "Chick-fil-A", "Chipotle",
    "Shake Shack", "Wingstop", "Jack in the Box", "Sonic", "Panera",
    "Darden", "Olive Garden", "Applebee's", "IHOP", "Dine Brands",
    "Subway", "Five Guys",
    "Toast", "Square", "PAX", "NCR", "Aloha", "Oracle", "Micros", "Revel",
    "Lightspeed", "Shift4", "Heartland", "Global Payments", "Genius",
    "Worldpay", "Fiserv", "Paytronix", "Punchh", "Olo", "Qu", "HungerRush",
    "Dragontail", "Presto", "SoundHound", "Xenial", "Tillster",
    "Smooth Commerce", "Brian Deck",
]

_COMPANY_RX = re.compile(
    "|".join(re.escape(c).replace(r"\ ", r"\s+") for c in sorted(KNOWN_COMPANIES, key=len, reverse=True)),
    re.I,
)

# Topic tag normalization (P-037 §Topic tag normalization)
OPERATIONAL_AI_RX = re.compile(
    r"\b(ai|artificial intelligence|automation|computer vision|dragontail|"
    r"inventory|rollout|implementation|franchisee|lawsuit|accuracy|trust|"
    r"operator|operations|friday night|survivability)\b",
    re.I,
)
PIZZA_DRAGONTAIL_RX = re.compile(
    r"\b(pizza hut|yum|dragontail|chaac pizza northeast)\b",
    re.I,
)
STARBUCKS_ROLLBACK_RX = re.compile(
    r"\b(starbucks).{0,60}\b(ai|inventory|counting|rollback|dump|scrap|error)\b|"
    r"\b(ai|inventory|counting|rollback|dump|scrap|error).{0,60}\b(starbucks)\b",
    re.I,
)
GLOBAL_PAYMENTS_RX = re.compile(
    r"\b(global payments|genius|globalpay)\b",
    re.I,
)

# Identity-preserving URL query params (P-037 §URL canonicalization)
_IDENTITY_PARAMS = {"id", "article", "p", "postid"}


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:14]


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_company(name: str) -> str:
    """Map an alias form to canonical company name."""
    return COMPANY_ALIASES.get(name.lower().strip(), name.strip())


def _canonical_url(url: str | None) -> str:
    """Normalize a URL per P-037 URL canonicalization rules."""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        # Lowercase scheme and host
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        # Strip trailing slash from path
        path = parsed.path.rstrip("/") or "/"
        # Collapse double slashes in path
        path = re.sub(r"//+", "/", path)
        # Filter query params — keep only identity-preserving ones
        if parsed.query:
            kept = "&".join(
                part for part in parsed.query.split("&")
                if part.split("=")[0].lower() in _IDENTITY_PARAMS
            )
            query = kept
        else:
            query = ""
        return urlunparse((scheme, netloc, path, "", query, ""))
    except Exception:
        return url or ""


def _is_stale(captured_at: str | None, channel: str) -> bool:
    """Return True if the evidence was captured outside the freshness window."""
    if not captured_at:
        return True
    try:
        cap = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        window_h = FRESHNESS_HOURS.get(channel, _DEFAULT_FRESHNESS_HOURS)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_h)
        return cap < cutoff
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Store I/O
# ---------------------------------------------------------------------------

def _blank_store() -> dict[str, Any]:
    return {
        "schema": "rb_strategic_events_v1",
        "updated_at": None,
        "events": [],
    }


def load_store(path: Path = EVENTS_PATH) -> dict[str, Any]:
    if not path.exists():
        return _blank_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _blank_store()
    if not isinstance(data, dict):
        return _blank_store()
    data.setdefault("schema", "rb_strategic_events_v1")
    data.setdefault("events", [])
    return data


def save_store(store: dict[str, Any], path: Path = EVENTS_PATH) -> None:
    store["updated_at"] = _now()
    path.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Event key matching (P-037 §Matching Protocol)
# ---------------------------------------------------------------------------

def _event_key(text: str) -> str | None:
    low = text.lower()
    if PIZZA_DRAGONTAIL_RX.search(text) and re.search(r"\b(lawsuit|litigation|sue|sues|sued|suing|complaint|court)\b", low):
        return "restaurant-ai:pizza-hut-yum-dragontail-lawsuit"
    if STARBUCKS_ROLLBACK_RX.search(text):
        return "restaurant-ai:starbucks-ai-inventory-rollback"
    if GLOBAL_PAYMENTS_RX.search(text) and re.search(r"\b(ai|upsell|restaurant|qsr|operator)\b", text, re.I):
        return "restaurant-tech:global-payments-genius-ai-integration"
    if OPERATIONAL_AI_RX.search(text):
        return "restaurant-ai:operational-ai-implementation-risk"
    return None


def _event_title(key: str) -> str:
    return {
        "restaurant-ai:pizza-hut-yum-dragontail-lawsuit":
            "Pizza Hut / Yum / Dragontail rollout litigation",
        "restaurant-ai:starbucks-ai-inventory-rollback":
            "Starbucks AI inventory rollback",
        "restaurant-tech:global-payments-genius-ai-integration":
            "Global Payments / Genius AI integration for restaurant operators",
        "restaurant-ai:operational-ai-implementation-risk":
            "Operational AI implementation risk in restaurants",
    }.get(key, key.rsplit(":", 1)[-1].replace("-", " ").title())


# ---------------------------------------------------------------------------
# Theme and entity extraction
# ---------------------------------------------------------------------------

def _themes(text: str) -> list[str]:
    themes = []
    if OPERATIONAL_AI_RX.search(text):
        themes.append("operational_ai_trust")
    if re.search(r"\b(franchisee|franchisees|lawsuit|trust)\b", text, re.I):
        themes.append("franchisee_trust_and_governance")
    if re.search(r"\b(rollout|implementation|accuracy|workflow|operations|friday night|survivability)\b", text, re.I):
        themes.append("friday_night_survivability")
    return themes or ["market_signal"]


def _entities(text: str, row: dict[str, Any]) -> dict[str, list[str]]:
    companies: list[str] = []
    people: list[str] = []

    # Extract from text using known-company regex
    for match in _COMPANY_RX.finditer(text):
        matched = match.group(0)
        # Skip people names used as company markers
        if matched in ("Brian Deck",):
            people.append(matched)
            continue
        canonical = _normalize_company(matched)
        if canonical not in companies:
            companies.append(canonical)

    # Also pull company field from row
    company_field = row.get("company") or ""
    for part in str(company_field).split("/"):
        canonical = _normalize_company(part.strip())
        if canonical and canonical not in companies:
            companies.append(canonical)

    return {
        "companies": list(dict.fromkeys(companies)),
        "people": list(dict.fromkeys(people)),
    }


# ---------------------------------------------------------------------------
# Source channel resolution
# ---------------------------------------------------------------------------

def _source_channel(row: dict[str, Any]) -> str:
    if row.get("source_channel"):
        return row["source_channel"]
    source_type = row.get("source_type")
    if source_type in PUBLIC_SOURCE_TYPES:
        return PUBLIC_SOURCE_TYPES[source_type]
    source_name = str(row.get("source_name") or "").lower()
    if "linkedin" in source_name:
        return "linkedin_social"
    if "law" in source_name or "court" in source_name or "legal" in source_name:
        return "legal_business_news"
    if "restaurant" in source_name or source_type == "vertical_trade":
        return "restaurant_trade_news"
    if row.get("manual"):
        return "user_uploaded_artifact"
    return "enterprise_restaurant_technology_news"


# ---------------------------------------------------------------------------
# Evidence normalization
# ---------------------------------------------------------------------------

def normalize_signal(row: dict[str, Any]) -> dict[str, Any] | None:
    text = _norm_text(" ".join([
        str(row.get("title") or ""),
        str(row.get("summary") or ""),
        str(row.get("text") or ""),
        str(row.get("pain_point_or_priority") or ""),
        str(row.get("why_this_matters_to_todd") or ""),
        str(row.get("restaurant_operator_impact") or ""),
        str(row.get("restaurant_tech_vendor_implication") or ""),
        str(row.get("company") or ""),
    ]))
    key = _event_key(text)
    if not key:
        return None

    url = _canonical_url(row.get("url") or row.get("source_url") or "")
    channel = _source_channel(row)
    captured_at = row.get("captured_at") or _now()
    stale = _is_stale(captured_at, channel)

    evidence_id = "ev_" + _hash("|".join([
        key,
        channel,
        url,
        text.lower()[:300],
    ]))

    source_name = row.get("source_name")
    if not source_name and channel == "linkedin_social":
        source_name = "LinkedIn"

    return {
        "evidence_id": evidence_id,
        "event_key": key,
        "title": row.get("title") or _event_title(key),
        "source_name": source_name,
        "source_channel": channel,
        "source_type": row.get("source_type"),
        "url": url,
        "published_at": row.get("published_at") or row.get("event_at"),
        "captured_at": captured_at,
        "text_excerpt": text[:600],
        "entities": _entities(text, row),
        "themes": _themes(text),
        "classification": {
            "signal_type": row.get("signal_type") or "implementation_risk",
            "strategic_relevance": row.get("strategic_relevance") or "high",
            "confidence": row.get("confidence") or "medium",
        },
        "manual": bool(row.get("manual")),
        "stale": stale,
    }


# ---------------------------------------------------------------------------
# Empty event factory
# ---------------------------------------------------------------------------

def _empty_event(key: str) -> dict[str, Any]:
    return {
        "event_id": "sev_" + _hash(key),
        "event_key": key,
        "title": _event_title(key),
        "created_at": _now(),
        "last_seen_at": None,
        "evidence": [],
        "source_channels": [],
        "themes": [],
        "entities": {"companies": [], "people": []},
        "lifecycle": "early_signal",
        "confidence_score": 0.0,
        "convergence_level": "isolated",
        "thesis_alignment": {},
        "related_event_ids": [],
        "recommended_actions": [],
        "source_freshness_gate": "open",
        "proof_stats": {
            "evidence_count": 0,
            "channels_distinct": 0,
            "last_fresh_evidence_at": None,
        },
    }


# ---------------------------------------------------------------------------
# Scoring (P-037 §Lifecycle and Convergence Rules)
# ---------------------------------------------------------------------------

def _score_event(event: dict[str, Any]) -> None:
    channels = event.get("source_channels") or []
    evidence = event.get("evidence") or []

    # Base score + per-channel weights
    score = 0.35
    for channel in channels:
        score += SOURCE_WEIGHT.get(channel, 0.10)

    # Convergence bonuses
    if len(channels) >= 2:
        score += 0.18
    if "legal_business_news" in channels and "linkedin_social" in channels:
        score += 0.10
    if "operational_ai_trust" in event.get("themes", []):
        score += 0.08
    score = min(score, 0.98)
    event["confidence_score"] = round(score, 2)

    # Freshness gate: check if all evidence is stale
    fresh_evidence = [e for e in evidence if not e.get("stale", False)]
    last_fresh_at = None
    if fresh_evidence:
        last_fresh_at = max(
            (e.get("captured_at") or "") for e in fresh_evidence
        ) or None

    # Proof stats
    event["proof_stats"] = {
        "evidence_count": len(evidence),
        "channels_distinct": len(channels),
        "last_fresh_evidence_at": last_fresh_at,
    }

    # Freshness gate (P-037 FG-1, FG-3)
    all_stale = bool(evidence) and not fresh_evidence
    if all_stale:
        event["source_freshness_gate"] = "stale_blocked"
    else:
        event["source_freshness_gate"] = "open"

    # Lifecycle promotion — stale-blocked events cannot reach validated_signal alone
    if len(channels) >= 2 and score >= 0.75 and not all_stale:
        event["lifecycle"] = "validated_signal"
        event["convergence_level"] = "multi_source_validated"
    elif len(channels) >= 2:
        event["lifecycle"] = "corroborated_signal"
        event["convergence_level"] = "multi_source"
    elif score >= 0.65:
        event["lifecycle"] = "early_signal"
        event["convergence_level"] = "single_strong_source"
    else:
        event["lifecycle"] = "rumor_or_early_signal"
        event["convergence_level"] = "isolated"

    # Thesis alignment
    themes_set = set(event.get("themes", []))
    matched_theses = []
    if "operational_ai_trust" in themes_set:
        matched_theses.append("operational_ai_realism")
    if "franchisee_trust_and_governance" in themes_set:
        matched_theses.append("franchisee_trust")
    if "friday_night_survivability" in themes_set:
        matched_theses += ["restaurant_tech_implementation_risk", "friday_night_survivability"]

    event["thesis_alignment"] = {
        "aligned": bool(matched_theses),
        "matched_theses": list(dict.fromkeys(matched_theses)),
        "summary": (
            "Reinforces Todd's operational AI realism thesis: restaurant AI has "
            "to survive store operations, trust, governance, and measurable ROI."
        ) if matched_theses else "",
    }

    event["recommended_actions"] = [
        a for a in [
            "Develop LinkedIn thought leadership post" if "operational_ai_trust" in themes_set else None,
            "Add to operational AI case-study repository" if "operational_ai_trust" in themes_set else None,
            "Monitor vendor positioning changes",
            "Watch franchisee sentiment and legal escalation" if "franchisee_trust_and_governance" in themes_set else None,
        ]
        if a
    ]


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def merge_signals(
    signals: list[dict[str, Any]],
    *,
    existing: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    by_key = {e.get("event_key"): dict(e) for e in (existing or []) if e.get("event_key")}
    captured = recorded = updated = deduped = 0
    stale_ignored = isolated_suppressed = validated_surfaced = ignored = 0

    for row in signals:
        ev = normalize_signal(row)
        if not ev:
            ignored += 1
            continue

        # P-037 FG-1: stale evidence from a channel already represented is skipped
        # (evidence with stale=True still counts for dedup, but doesn't promote lifecycle)
        captured += 1
        event = by_key.get(ev["event_key"]) or _empty_event(ev["event_key"])
        existing_ids = {e.get("evidence_id") for e in event.get("evidence", [])}

        if ev["evidence_id"] in existing_ids:
            deduped += 1
            by_key[ev["event_key"]] = event
            continue

        # Track stale evidence — attach but flag
        if ev.get("stale"):
            stale_ignored += 1
            # Still attach so the evidence is recorded, but mark stale=True (already set)

        event["evidence"].append(ev)
        event["last_seen_at"] = ev.get("captured_at") or _now()

        # Merge channels — stale evidence from a channel that's already present
        # does NOT add a new channel entry (P-037 FG-1: source-channel dedupe)
        existing_channels = set(event.get("source_channels") or [])
        new_channel = ev["source_channel"]
        # Only add channel if we have fresh evidence from it
        if not ev.get("stale") or new_channel not in existing_channels:
            existing_channels.add(new_channel)
        event["source_channels"] = sorted(existing_channels)

        # Merge themes and entities
        event["themes"] = sorted({*(event.get("themes") or []), *ev.get("themes", [])})
        for bucket in ("companies", "people"):
            event["entities"][bucket] = sorted({
                *(event.get("entities") or {}).get(bucket, []),
                *ev.get("entities", {}).get(bucket, []),
            })

        _score_event(event)

        if ev["event_key"] in by_key:
            updated += 1
        else:
            recorded += 1
        by_key[ev["event_key"]] = event

    events = list(by_key.values())
    _link_related_events(events)

    # Count isolated and validated
    for e in events:
        if e.get("convergence_level") == "isolated":
            isolated_suppressed += 1
        if e.get("lifecycle") == "validated_signal":
            validated_surfaced += 1

    return {
        "events": sorted(events, key=_rank_key),
        "proof_stats": {
            "captured": captured,
            "recorded": recorded,
            "updated": updated,
            "deduped": deduped,
            "stale_ignored": stale_ignored,
            "isolated_suppressed": isolated_suppressed,
            "validated_surfaced": validated_surfaced,
            "ignored": ignored,
        },
    }


def _rank_key(event: dict[str, Any]) -> tuple[float, int, str]:
    return (
        -float(event.get("confidence_score") or 0.0),
        -len(event.get("evidence") or []),
        str(event.get("last_seen_at") or ""),
    )


def _link_related_events(events: list[dict[str, Any]]) -> None:
    for event in events:
        related = []
        themes = set(event.get("themes") or [])
        for other in events:
            if other is event:
                continue
            if themes & set(other.get("themes") or []):
                related.append(other["event_id"])
        event["related_event_ids"] = sorted(set(related))


# ---------------------------------------------------------------------------
# Ingest / build_report
# ---------------------------------------------------------------------------

def ingest(
    signals: list[dict[str, Any]],
    *,
    confirm: bool,
    store_path: Path = EVENTS_PATH,
) -> dict[str, Any]:
    store = load_store(store_path)
    merged = merge_signals(signals, existing=store.get("events") or [])
    if confirm:
        store["events"] = merged["events"]
        save_store(store, store_path)
    return {
        "confirmed": bool(confirm),
        "persistence_status": "persisted" if confirm else "preview",
        "events": merged["events"],
        "proof_stats": merged["proof_stats"],
    }


def _load_linkedin_records(path: Path = LINKEDIN_DAILY_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        row = {
            "title": (record.get("canonical_output") or {}).get("rb_recorded", {}).get("topic")
                     or record.get("recommended_action")
                     or "LinkedIn daily signal",
            "text": record.get("raw_text") or record.get("recommended_action") or json.dumps(record),
            "source_name": "LinkedIn",
            "source_type": "linkedin_social",
            "source_url": (record.get("source") or {}).get("source_url"),
            "event_at": record.get("event_at"),
            "captured_at": record.get("captured_at"),
            "company": ", ".join(
                c.get("name") for c in (record.get("entities") or {}).get("companies", [])
                if c.get("name")
            ),
            "strategic_relevance": (record.get("classification") or {}).get("strategic_relevance"),
            "confidence": (record.get("classification") or {}).get("confidence"),
        }
        rows.append(row)
    return rows


def _load_feeder_rows(path: Path = FEEDER_OUTPUT_PATH) -> list[dict[str, Any]]:
    """Load rows from market_source_feeds.py output (jsonl)."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def build_report(
    *,
    market_report: dict | None = None,
    linkedin_records: list[dict[str, Any]] | None = None,
    feeder_rows: list[dict[str, Any]] | None = None,
    store_path: Path = EVENTS_PATH,
    persist: bool = False,
) -> dict[str, Any]:
    market_rows: list[dict[str, Any]] = []
    if market_report:
        market_rows.extend(market_report.get("all_ranked") or market_report.get("top") or [])

    # Load feeder rows if not supplied
    if feeder_rows is None:
        feeder_rows = _load_feeder_rows()
    market_rows.extend(feeder_rows)

    if linkedin_records is None:
        linkedin_records = _load_linkedin_records()

    rows = [*market_rows, *linkedin_records]
    merged = ingest(rows, confirm=persist, store_path=store_path)
    events = merged["events"]

    # Theme clustering
    themes: dict[str, dict[str, Any]] = {}
    for event in events:
        for theme in event.get("themes") or []:
            bucket = themes.setdefault(theme, {
                "theme": theme,
                "events": [],
                "source_channels": set(),
                "confidence_score": 0.0,
                "trend_status": "early",
            })
            bucket["events"].append(event["event_id"])
            bucket["source_channels"].update(event.get("source_channels") or [])
            bucket["confidence_score"] = max(
                bucket["confidence_score"], event.get("confidence_score") or 0.0
            )

    theme_rows = []
    for bucket in themes.values():
        channels = sorted(bucket.pop("source_channels"))
        bucket["source_channels"] = channels
        if len(bucket["events"]) >= 2 or len(channels) >= 2:
            bucket["trend_status"] = "recurring_theme"
        if bucket["confidence_score"] >= 0.80 and len(channels) >= 2:
            bucket["trend_status"] = "validated_market_trend"
        theme_rows.append(bucket)

    return {
        "schema": "rb_strategic_events_report_v1",
        "generated_at": _now(),
        "input_count": len(rows),
        "event_count": len(events),
        "top": events[:5],
        "themes": sorted(theme_rows, key=lambda t: (-t["confidence_score"], t["theme"])),
        "proof_stats": merged["proof_stats"],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="infile", help="JSON list/dict with signals")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--report", action="store_true", help="Build full convergence report")
    p.add_argument("--persist", action="store_true", help="Persist report events to store")
    args = p.parse_args()

    if args.report:
        report = build_report(persist=args.persist)
        print(json.dumps(report, indent=2))
        return 0

    raw = Path(args.infile).read_text(encoding="utf-8") if args.infile else sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else []
    signals = payload.get("signals", []) if isinstance(payload, dict) else payload
    result = ingest(signals, confirm=args.confirm)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
