#!/usr/bin/env python3
"""Report whether priority advertiser targets have stable, monitorable identities."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from urllib.parse import quote_plus

import rb_core as core

CONFIG_PATH = core.INBOX_DIR / "advertiser_target_registry.json"
RESULT_PATH = core.CACHE_DIR / "advertiser_target_coverage.json"


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def run(*, today: date | None = None) -> dict:
    today = today or date.today()
    rows = []
    for target in _load(CONFIG_PATH).get("targets") or []:
        entity = target.get("entity") or ""
        google_id = target.get("google_advertiser_id")
        meta_id = target.get("meta_page_id")
        domain = target.get("domain")
        rows.append({
            **target,
            "google_status": "verified_advertiser" if google_id else "domain_search_ready" if domain else "needs_advertiser_id",
            "meta_status": "verified_target" if meta_id else "keyword_search_only",
            "google_url": target.get("google_url") or (f"https://adstransparency.google.com/advertiser/{google_id}?region=US" if google_id else f"https://adstransparency.google.com/?domain={quote_plus(domain)}&region=US" if domain else "https://adstransparency.google.com/?region=US"),
            "meta_url": target.get("meta_url") or f"https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=US&q={quote_plus(entity)}&search_type=keyword_unordered",
            "discovery_ready": bool(domain or google_id or meta_id),
            "monitoring_verified": bool(google_id or meta_id),
        })
    verified = sum(bool(row["monitoring_verified"]) for row in rows)
    result = {"contract": "rb_advertiser_target_coverage_v1", "date": today.isoformat(), "targets": rows, "summary": {"targets": len(rows), "verified": verified, "needs_identity": len(rows) - verified}, "policy": "keyword searches are discovery aids, not verified advertiser monitoring"}
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    args = parser.parse_args()
    print(json.dumps(run(today=date.fromisoformat(args.date) if args.date else None), indent=2))
