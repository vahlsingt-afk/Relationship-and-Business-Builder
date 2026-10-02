#!/usr/bin/env python3
"""signal_correlation.py — cross-source momentum/correlation detection (RB-9.67-A/B/D).

A world-class CoS doesn't just report individual headlines — they notice when
*multiple* signals about the same entity cluster together in a short window,
because that clustering is itself information: a company that generates 3
signals in a week (funding + exec hire + partnership) is moving, and if that
company is tied to one of Todd's active opportunities, the timing of that
opportunity may need to change.

This script correlates:
  - system/.cache/web_scanner_cache.json   (RSS/Atom feed items, entity-tagged)
  - system/.cache/entity_alerts_cache.json (Google-News alert items, RB-9.65-D)

For each entity, it counts non-"general" signal_type items in the last
CORRELATION_WINDOW_DAYS days. Entities crossing MOMENTUM_THRESHOLD are
"momentum signals". If that entity also appears in an open active-thread's
`companies` list, it's additionally flagged as opportunity-relevant —
RB-9.67-D's "network opportunity surfacing": the cluster of activity at a
company RB already has a relationship-building reason to care about.

Output cache: system/.cache/signal_correlation.json

Usage:
    python3 signal_correlation.py --cache   # compute and write cache
    python3 signal_correlation.py --json    # print cache as JSON
    python3 signal_correlation.py           # human-readable summary
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core  # noqa: E402

CACHE_PATH = core.CACHE_DIR / "signal_correlation.json"
WEB_SCAN_CACHE = core.CACHE_DIR / "web_scanner_cache.json"
ENTITY_ALERTS_CACHE = core.CACHE_DIR / "entity_alerts_cache.json"

#: Items older than this are excluded from correlation.
CORRELATION_WINDOW_DAYS = 7

#: An entity needs at least this many non-general-signal items in the window
#: to be flagged as a "momentum signal".
MOMENTUM_THRESHOLD = 2


def _within_window(pub_date: str, today: date) -> bool:
    try:
        d = date.fromisoformat((pub_date or "")[:10])
    except ValueError:
        return False
    return (today - d).days <= CORRELATION_WINDOW_DAYS and d <= today


def _collect_web_scan_items(today: date) -> list[dict]:
    """Flatten web_scanner_cache.json into entity-tagged items within window."""
    out: list[dict] = []
    if not WEB_SCAN_CACHE.exists():
        return out
    try:
        data = json.loads(WEB_SCAN_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    for source_name, rec in (data.items() if isinstance(data, dict) else []):
        if not isinstance(rec, dict):
            continue
        for item in rec.get("items") or []:
            extras = item.get("extras") or {}
            entities = extras.get("entities") or []
            if not entities:
                continue
            if not _within_window(extras.get("pub_date") or "", today):
                continue
            sig_type = extras.get("signal_type") or "general"
            for ent in entities:
                out.append({
                    "entity": ent,
                    "title": item.get("title", ""),
                    "signal_type": sig_type,
                    "signal_badge": extras.get("signal_badge", ""),
                    "pub_date": extras.get("pub_date", ""),
                    "source": source_name,
                })
    return out


def _collect_entity_alert_items(today: date) -> list[dict]:
    """Flatten entity_alerts_cache.json into entity-tagged items within window."""
    out: list[dict] = []
    if not ENTITY_ALERTS_CACHE.exists():
        return out
    try:
        data = json.loads(ENTITY_ALERTS_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    for entity, rec in (data.get("entities") or {}).items():
        for item in rec.get("items") or []:
            if not _within_window(item.get("pub_date") or "", today):
                continue
            out.append({
                "entity": entity,
                "title": item.get("title", ""),
                "signal_type": item.get("signal_type", "general"),
                "signal_badge": item.get("signal_badge", ""),
                "pub_date": item.get("pub_date", ""),
                "source": "google_news_search",
            })
    return out


def _opportunity_relevant_entities() -> dict[str, dict]:
    """Map lowercase company name -> {thread_id, thread_title, boost_for_brief}
    for every company referenced by an open active thread."""
    out: dict[str, dict] = {}
    try:
        threads = [t for t in core.load_active_threads() if t.get("status") == "open"]
    except Exception:  # noqa: BLE001
        threads = []
    for t in threads:
        for company in (t.get("companies") or []):
            out[str(company).lower()] = {
                "thread_id": t.get("id"),
                "thread_title": t.get("title"),
                "boost_for_brief": t.get("boost_for_brief"),
            }
    return out


def build_report(today: date | None = None) -> dict:
    today = today or date.today()
    items = _collect_web_scan_items(today) + _collect_entity_alert_items(today)

    by_entity: dict[str, list[dict]] = {}
    for it in items:
        by_entity.setdefault(it["entity"], []).append(it)

    opp_entities = _opportunity_relevant_entities()

    momentum_signals = []
    for entity, ents_items in by_entity.items():
        non_general = [it for it in ents_items if it["signal_type"] != "general"]
        if len(non_general) < MOMENTUM_THRESHOLD:
            continue
        opp = opp_entities.get(entity.lower())
        momentum_signals.append({
            "entity": entity,
            "item_count": len(ents_items),
            "non_general_count": len(non_general),
            "signal_types": sorted({it["signal_type"] for it in non_general}),
            "items": sorted(non_general, key=lambda x: x.get("pub_date", ""), reverse=True)[:5],
            "opportunity_relevant": opp is not None,
            "thread_id": (opp or {}).get("thread_id"),
            "thread_title": (opp or {}).get("thread_title"),
            "boost_for_brief": (opp or {}).get("boost_for_brief"),
        })

    # Opportunity-relevant momentum first, then by non_general_count desc.
    momentum_signals.sort(key=lambda r: (not r["opportunity_relevant"], -r["non_general_count"]))

    return {
        "_generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": CORRELATION_WINDOW_DAYS,
        "momentum_threshold": MOMENTUM_THRESHOLD,
        "momentum_signals": momentum_signals,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cache", action="store_true", help="Compute and write the cache.")
    p.add_argument("--json", action="store_true", help="Print as JSON.")
    args = p.parse_args()

    report = build_report()
    if args.cache:
        core.write_cache("signal_correlation", report, source="signal_correlation.py")

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    if not report["momentum_signals"]:
        print("No momentum signals (no entity has >= 2 non-general signals in the last "
              f"{CORRELATION_WINDOW_DAYS} days).")
        return 0
    for m in report["momentum_signals"]:
        tag = " [OPPORTUNITY-RELEVANT]" if m["opportunity_relevant"] else ""
        print(f"{m['entity']}{tag}: {m['non_general_count']} signal(s) — {', '.join(m['signal_types'])}")
        for it in m["items"]:
            print(f"    {it.get('signal_badge', '')} {it.get('title', '')[:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
