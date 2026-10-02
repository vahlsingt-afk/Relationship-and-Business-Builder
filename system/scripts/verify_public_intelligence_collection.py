#!/usr/bin/env python3
"""Hard contract proving today's public-intelligence collection executed."""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone

import rb_core as core

ASSESSMENT_PATH = core.CACHE_DIR / "intelligence_assessment.json"
DISCOVERY_PATH = core.CACHE_DIR / "public_source_discovery.json"
RECEIPT_PATH = core.CACHE_DIR / "public_intelligence_collection_receipt.json"


def _load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def verify(today: date) -> dict:
    assessment = _load(ASSESSMENT_PATH)
    discovery = _load(DISCOVERY_PATH)
    trust = assessment.get("trust_stats") or {}
    phase1 = assessment.get("phase_1_web") or {}
    assessment_current = assessment.get("assessment_date") == today.isoformat()
    discovery_current = discovery.get("date") == today.isoformat()
    sources_scanned = int(trust.get("sources_assessed") or 0)
    entities_checked = len(discovery.get("entities_checked") or [])
    failures = []
    if not assessment_current:
        failures.append("web_assessment_not_current")
    if assessment_current and phase1.get("status") != "ok":
        failures.append("web_assessment_failed")
    if assessment_current and sources_scanned == 0:
        failures.append("zero_public_sources_scanned")
    if not discovery_current:
        failures.append("entity_source_discovery_not_current")
    if discovery_current and entities_checked == 0:
        failures.append("zero_watchlist_entities_checked")
    receipt = {
        "date": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "pass" if not failures else "fail",
        "sources_scanned": sources_scanned,
        "items_fetched": int(trust.get("items_fetched") or 0),
        "entities_checked_for_new_sources": entities_checked,
        "source_candidates_observed": len(discovery.get("candidates") or []),
        "failures": failures,
    }
    RECEIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT_PATH.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = verify(date.fromisoformat(args.date))
    print(json.dumps(result, indent=2) if args.json else
          f"public intelligence collection: {result['status']} | "
          f"sources={result['sources_scanned']} entities={result['entities_checked_for_new_sources']}")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
