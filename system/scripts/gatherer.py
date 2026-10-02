#!/usr/bin/env python3
"""Gatherer: produce RBB's lightweight rolling-24-hour ecosystem change packet."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import rb_core as core

CONTRACT = "rb.gatherer_daily_change_packet.v1"
CACHE_PATH = core.SYSTEM_DIR / ".cache" / "gatherer_daily_change.json"

MATERIAL_SIGNALS = {
    "acquisition": 95, "bankruptcy": 95, "customer_loss": 90,
    "customer_win": 88, "deployment": 85, "pe_activity": 85,
    "funding_round": 82, "executive_departure": 80, "executive_hire": 75,
    "restructuring": 78, "product_launch": 68, "partnership": 65,
    "earnings_surprise": 65, "closure": 80, "expansion": 60,
}
SPECIFIC_PLAYBOOKS = {
    "acquisition": "ownership_funding_ma", "pe_activity": "ownership_funding_ma",
    "funding_round": "ownership_funding_ma", "executive_hire": "leadership_decision_map",
    "executive_departure": "leadership_decision_map", "deployment": "customer_deployment_validation",
    "customer_win": "customer_deployment_validation", "customer_loss": "customer_deployment_validation",
}


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _canonical_url(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")])
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), query, ""))
    except Exception:
        return url


def _clean_title(title: str) -> str:
    return re.sub(r"^\[[^]]+\]\s*", "", title or "").strip()


def _parse_observed(value: str, end: datetime) -> tuple[datetime | None, str]:
    if not value:
        return None, "day"
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        precision = "day" if len(value.strip()) == 10 else "time"
        if precision == "day":
            parsed = parsed.replace(hour=12)
        return parsed.astimezone(timezone.utc), precision
    except ValueError:
        return None, "day"


def _tracked_entities(path: Path | None = None) -> tuple[dict[str, str], int]:
    path = path or core.ECOSYSTEM_INTELLIGENCE_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}, 0
    aliases: dict[str, str] = {}
    active = [e for e in (data.get("entities") or []) if e.get("status") != "inactive"]
    for entity in active:
        name = str(entity.get("name") or "").strip()
        if not name:
            continue
        aliases[name.casefold()] = name
        for alias in entity.get("aliases") or []:
            if alias:
                aliases[str(alias).casefold()] = name
    return aliases, len(active)


def build_packet(web_phase: dict[str, Any], *, now: datetime | None = None,
                 ecosystem_path: Path | None = None) -> dict[str, Any]:
    end = _utc(now)
    start = end - timedelta(hours=24)
    aliases, tracked_total = _tracked_entities(ecosystem_path)
    raw_items: list[dict] = []
    for key, domain in (("industry_primary", "restaurant_industry"),
                        ("industry_technology", "restaurant_technology"),
                        ("world_national", "world_national")):
        for item in web_phase.get(key) or []:
            raw_items.append((item, domain))

    seen: set[str] = set()
    changes: list[dict] = []
    sources: set[str] = set()
    domains: set[str] = set()
    observed_entities: set[str] = set()
    excluded_old = excluded_noise = duplicates = 0

    for item, fallback_domain in raw_items:
        extras = item.get("extras") or {}
        observed, precision = _parse_observed(str(extras.get("pub_date") or ""), end)
        if observed is None or observed < start or observed > end + timedelta(hours=24 if precision == "day" else 0):
            excluded_old += 1
            continue
        title = _clean_title(str(item.get("title") or ""))
        url = _canonical_url(str(extras.get("source_url") or ""))
        key = url or re.sub(r"\W+", " ", title.casefold()).strip()
        if not key or key in seen:
            duplicates += 1
            continue
        seen.add(key)

        supplied = [str(x) for x in (extras.get("entities") or []) if x]
        matched = {aliases.get(x.casefold(), x) for x in supplied}
        haystack = f" {title} {item.get('summary') or ''} ".casefold()
        for alias, canonical in aliases.items():
            if len(alias) >= 4 and re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", haystack):
                matched.add(canonical)
        domain = str(extras.get("domain") or fallback_domain)
        signal = str(extras.get("signal_type") or "general")
        if domain == "world_national" and not matched:
            excluded_noise += 1
            continue

        materiality = MATERIAL_SIGNALS.get(signal, 35)
        if matched:
            materiality = min(100, materiality + 10)
        source = str(extras.get("source_name") or (item.get("source_refs") or [""])[0])
        confidence = str(item.get("confidence") or "medium")
        if confidence not in {"low", "medium", "high"}:
            confidence = "medium"
        change_id = "gchg-" + hashlib.sha256(f"{key}|{observed.date()}".encode()).hexdigest()[:16]
        change = {
            "change_id": change_id,
            "title": title,
            "summary": str(item.get("summary") or title)[:500],
            "observed_at": observed.isoformat(),
            "date_precision": precision,
            "entities": sorted(matched),
            "signal_type": signal,
            "domain": domain,
            "source": source,
            "source_url": url,
            "confidence": confidence,
            "materiality": materiality,
            "verification_state": "candidate",
        }
        changes.append(change)
        sources.add(source)
        domains.add(domain)
        observed_entities.update(matched)

    changes.sort(key=lambda x: (-x["materiality"], x["title"].casefold()))
    escalations = []
    for change in changes:
        if change["materiality"] < 70:
            continue
        reason = "Material 24-hour change signal requires source verification before canonical use."
        if not change["entities"]:
            reason += " Entity resolution is also required."
        escalations.append({
            "change_id": change["change_id"],
            "target_names": change["entities"],
            "recommended_playbook": SPECIFIC_PLAYBOOKS.get(change["signal_type"], "change_monitor"),
            "reason": reason,
            "priority": "high" if change["materiality"] >= 85 else "medium",
        })

    packet_id = "gatherer-" + end.strftime("%Y%m%dT%H%M%SZ")
    return {
        "contract": CONTRACT,
        "packet_id": packet_id,
        "generated_at": end.isoformat().replace("+00:00", "Z"),
        "window": {"start": start.isoformat().replace("+00:00", "Z"),
                   "end": end.isoformat().replace("+00:00", "Z"), "hours": 24},
        "coverage": {
            "tracked_entities_total": tracked_total,
            "tracked_entities_observed": sorted(observed_entities),
            "sources_observed": sorted(s for s in sources if s),
            "domains_observed": sorted(domains),
            "limitations": [
                "Coverage reflects configured public feeds, not proof that every ecosystem entity was checked.",
                "Date-only publication timestamps have day precision.",
                "Candidate signals are unverified until reviewed or escalated to Hunter.",
            ],
        },
        "changes": changes,
        "hunter_escalations": escalations,
        "stats": {"input_items": len(raw_items), "changes_detected": len(changes),
                  "hunter_escalations": len(escalations), "duplicates_removed": duplicates,
                  "excluded_outside_window": excluded_old, "excluded_world_noise": excluded_noise},
    }


def write_packet(packet: dict[str, Any], path: Path = CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assessment", type=Path, default=core.SYSTEM_DIR / ".cache" / "intelligence_assessment.json")
    parser.add_argument("--output", type=Path, default=CACHE_PATH)
    parser.add_argument("--json", action="store_true", dest="emit_json")
    args = parser.parse_args()
    data = json.loads(args.assessment.read_text(encoding="utf-8"))
    packet = build_packet(data.get("phase_1_web") or {})
    write_packet(packet, args.output)
    if args.emit_json:
        print(json.dumps(packet, indent=2))
    else:
        print(f"gatherer: {len(packet['changes'])} changes, {len(packet['hunter_escalations'])} Hunter escalations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

