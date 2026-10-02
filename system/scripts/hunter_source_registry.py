#!/usr/bin/env python3
"""Maintain Hunter's growing, quality-ranked public research source registry."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from collections import Counter


SYSTEM_DIR = Path(__file__).resolve().parent.parent
REGISTRY_PATH = SYSTEM_DIR / "research" / "hunter_source_registry.json"
COVERAGE_PATH = SYSTEM_DIR / "research" / "deep_research_coverage.json"

DEFAULT_AUTHORITY = {
    "government_or_regulator": 95,
    "restaurant_or_operator": 92,
    "customer_joint": 88,
    "trade_press": 80,
    "general_news": 70,
    "analyst_or_research": 72,
    "vendor": 62,
    "public_review_or_directory": 48,
    "social_or_event": 40,
    "archive": 35,
    "other": 50,
    "unclassified": 50,
}


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def domain(url_or_domain: str) -> str:
    value = str(url_or_domain or "").strip().lower()
    host = urlparse(value if "://" in value else f"https://{value}").netloc
    return host.removeprefix("www.").split(":", 1)[0]


def score(row: dict) -> dict:
    checked = int(row.get("checked") or 0)
    productive = int(row.get("productive") or 0)
    accessible = int(row.get("accessible") if row.get("accessible") is not None else checked)
    accepted = int(row.get("findings_accepted") or 0)
    rejected = int(row.get("findings_rejected") or 0)
    corrected = int(row.get("findings_corrected") or 0)
    authority = int(row.get("authority_score") or DEFAULT_AUTHORITY.get(row.get("source_class"), 50))
    productivity = round(100 * (productive + 2) / (checked + 4))
    accessibility = round(100 * (accessible + 2) / (checked + 4))
    reviewed = accepted + rejected + corrected
    review_survival = round(100 * (accepted + 0.5 * corrected + 2) / (reviewed + 4))
    confidence = "high" if checked >= 20 and reviewed >= 10 else ("medium" if checked >= 5 else "low")
    # Authority is intentionally dominant: a prolific vendor page or forum
    # cannot outrank a regulator merely because it yielded more observations.
    quality = round(0.5 * authority + 0.2 * productivity + 0.1 * accessibility + 0.2 * review_survival)
    return {
        "quality_score": max(0, min(100, quality)),
        "authority_score": authority,
        "productivity_score": productivity,
        "accessibility_score": accessibility,
        "review_survival_score": review_survival,
        "score_confidence": confidence,
    }


def _urls(value):
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _urls(child)
    elif isinstance(value, list):
        for child in value:
            yield from _urls(child)


def discover_artifact_domains() -> Counter:
    """Bootstrap domains from structured historical research artifacts.

    These are marked as historical uses, not productive checks; subsequent
    Hunter source ledgers supply the performance evidence.
    """
    counts = Counter()
    paths = list((SYSTEM_DIR / "research").glob("*.json"))
    paths += list((SYSTEM_DIR / "inbox" / "chatgpt_intelligence_drop").glob("*.json"))
    paths += list((SYSTEM_DIR / "technology_lifecycle" / "_intake").glob("*.jsonl"))
    for path in paths:
        if path == REGISTRY_PATH:
            continue
        try:
            if path.suffix == ".jsonl":
                values = []
                with path.open(encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            values.append(json.loads(line))
                        except ValueError:
                            continue
            else:
                values = [_read(path, {})]
        except OSError:
            continue
        for value in values:
            for url in _urls(value):
                host = domain(url)
                if host:
                    counts[host] += 1
    return counts


def build_ranked_registry(registry_data: dict | None = None, coverage_data: dict | None = None) -> dict:
    scan_artifacts = registry_data is None and coverage_data is None
    registry_data = registry_data or _read(REGISTRY_PATH, {"schema": "rb.hunter_source_registry.v1", "sources": {}})
    coverage_data = coverage_data or _read(COVERAGE_PATH, {})
    sources = {key: dict(value) for key, value in (registry_data.get("sources") or {}).items()}
    for row in sources.values():
        # Entries explicitly curated into hunter_source_registry.json are
        # approved free-public research starting points unless overridden.
        row.setdefault("access_tier", "free_public")
    for host, stats in (coverage_data.get("sources") or {}).items():
        key = domain(host)
        if not key or not isinstance(stats, dict):
            continue
        row = sources.setdefault(key, {
            "name": key,
            "source_class": "unclassified",
            "authority_score": DEFAULT_AUTHORITY["unclassified"],
            "topics": [],
            "preferred_for": [],
            "notes": "Auto-discovered from Hunter source ledgers; classification pending.",
            "access_tier": "unknown",
        })
        for field in ("checked", "productive", "unproductive", "last_checked_at"):
            if stats.get(field) is not None:
                row[field] = stats[field]
    if scan_artifacts:
        for key, count in discover_artifact_domains().items():
            row = sources.setdefault(key, {
                "name": key,
                "source_class": "unclassified",
                "authority_score": DEFAULT_AUTHORITY["unclassified"],
                "topics": [],
                "preferred_for": [],
                "notes": "Auto-discovered from historical structured research artifacts; classification pending.",
                "access_tier": "unknown",
            })
            row["historical_uses"] = max(int(row.get("historical_uses") or 0), count)
    ranked = []
    for host, row in sources.items():
        item = {"domain": host, **row, **score(row)}
        ranked.append(item)
    ranked.sort(key=lambda item: (-item["quality_score"], -item.get("checked", 0), item["domain"]))
    for index, item in enumerate(ranked, 1):
        item["rank"] = index
    return {
        "schema": "rb.hunter_source_registry.v1",
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scoring_version": 1,
        "source_count": len(ranked),
        "ranked_sources": ranked,
    }


def research_route(registry: dict, modules: list[str], limit: int = 25) -> list[dict]:
    wanted = set(modules)
    ranked = [row for row in registry.get("ranked_sources") or [] if row.get("access_tier") == "free_public"]
    matches = [row for row in ranked if wanted.intersection(row.get("preferred_for") or [])]
    fallback = [row for row in ranked if row not in matches]
    return (matches + fallback)[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank Hunter public research sources")
    parser.add_argument("command", choices=["rank", "route"])
    parser.add_argument("--module", action="append", dest="modules")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    registry = build_ranked_registry()
    result = registry if args.command == "rank" else research_route(registry, args.modules or [], args.limit)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
