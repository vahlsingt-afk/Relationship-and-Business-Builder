#!/usr/bin/env python3
"""Gatherer: produce RBB's lightweight rolling-24-hour ecosystem change packet."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import rb_core as core

CONTRACT = "rb.gatherer_daily_change_packet.v1"
CACHE_PATH = core.SYSTEM_DIR / ".cache" / "gatherer_daily_change.json"
HISTORY_DIR = core.SYSTEM_DIR / ".cache" / "gatherer_history"
HUNTER_QUEUE_PATH = core.SYSTEM_DIR / ".cache" / "gatherer_hunter_escalations.jsonl"
SCHEMA_PATH = core.SYSTEM_DIR / "schemas" / "gatherer_daily_change.schema.json"

MATERIAL_SIGNALS = {
    "acquisition": 95, "bankruptcy": 95, "customer_loss": 90,
    "customer_win": 88, "deployment": 85, "pe_activity": 85,
    "funding_round": 82, "executive_departure": 80, "executive_hire": 75,
    "restructuring": 78, "product_launch": 68, "partnership": 65,
    "earnings_surprise": 65, "closure": 80, "expansion": 60,
}
HIGH_VERIFICATION_NEED = {"acquisition", "bankruptcy", "customer_loss", "customer_win", "deployment", "funding_round", "executive_departure", "executive_hire"}
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


def _content_tokens(value: str) -> set[str]:
    stop = {"about", "after", "announces", "company", "from", "into", "launches",
            "more", "new", "restaurant", "restaurants", "says", "than", "that",
            "their", "this", "with", "will"}
    return {word for word in re.findall(r"[a-z0-9]+", (value or "").casefold())
            if len(word) > 3 and word not in stop}


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


def _tracked_entities(path: Path | None = None) -> tuple[dict[str, dict[str, str]], int, dict, str | None]:
    """Returns (aliases, tracked_total, canonical_state, error). error is
    None on a successful read; a string describing the failure otherwise.

    2026-10-03 (CLAUDE_HANDOFF_RB_HUNTER_GATHERER_END_TO_END_DEFECTS,
    Defect 5): this used to swallow ANY read/parse exception into a bare
    `tracked_entities_total: 0`, indistinguishable from a legitimately
    empty graph -- confirmed as (part of) the root cause of a morning-
    pipeline packet reporting 0 tracked entities and all-sources-failed
    while simultaneously reporting 370 real input items. The caller
    (build_packet) now surfaces this error explicitly on the packet
    rather than silently treating it as "zero."""
    path = path or core.ECOSYSTEM_INTELLIGENCE_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, 0, {"source_urls": set(), "entity_text": {}, "relationships": []}, f"{type(exc).__name__}: {exc}"
    aliases: dict[str, dict[str, str]] = {}
    active = [e for e in (data.get("entities") or []) if e.get("status") != "inactive"]
    for entity in active:
        name = str(entity.get("name") or "").strip()
        if not name:
            continue
        entity_id = str(entity.get("id") or "")
        if not entity_id:
            target_key = ""
        elif entity.get("entity_type") == "brand":
            target_key = f"company:{entity_id}"
        elif entity.get("entity_type") == "vendor":
            target_key = f"competitor:{entity_id.removeprefix('vendor-')}"
        else:
            target_key = f"entity:{entity_id}"
        resolved = {"name": name, "target_key": target_key}
        aliases[name.casefold()] = resolved
        for alias in entity.get("aliases") or []:
            if alias:
                aliases[str(alias).casefold()] = resolved
    source_urls: set[str] = set()
    entity_text: dict[str, str] = {}
    for entity in active:
        if entity.get("entity_type") == "brand":
            key = f"company:{entity.get('id')}"
        elif entity.get("entity_type") == "vendor":
            key = f"competitor:{str(entity.get('id') or '').removeprefix('vendor-')}"
        else:
            key = f"entity:{entity.get('id')}"
        entity_text[key] = json.dumps(entity.get("attributes") or {}, sort_keys=True, default=str).casefold()
    for collection in (data.get("sources") or [], data.get("signals") or [], data.get("relationships") or []):
        for row in collection:
            for field in ("url", "source_url", "canonical_url"):
                if row.get(field):
                    source_urls.add(_canonical_url(str(row[field])))
    canonical = {"source_urls": source_urls, "entity_text": entity_text,
                 "relationships": data.get("relationships") or []}
    return aliases, len(active), canonical, None


def _source_owner(item: dict, domain: str) -> str:
    supplied = str((item.get("extras") or {}).get("source_owner") or "")
    if supplied:
        return supplied
    if domain == "world_national":
        return "general_news"
    source = str((item.get("extras") or {}).get("source_name") or "").casefold()
    if any(token in source for token in ("reuters", "associated press", "bloomberg")):
        return "general_news"
    return "trade_press"


def _event_key(title: str, signal: str, target_keys: list[str], observed: datetime) -> str:
    stable_words = " ".join(sorted(_content_tokens(title))[:10])
    return f"{signal}|{'|'.join(target_keys)}|{observed.date()}|{stable_words}"


def _same_event(left: dict, right_title: str, signal: str, target_keys: list[str], observed: datetime) -> bool:
    if left.get("signal_type") != signal:
        return False
    left_date = str(left.get("temporal", {}).get("event_or_publication_date") or "")[:10]
    if left_date != observed.date().isoformat():
        return False
    left_targets = set(left.get("target_keys") or [])
    right_targets = set(target_keys)
    if left_targets and right_targets and not left_targets.intersection(right_targets):
        return False
    a, b = _content_tokens(left.get("title") or ""), _content_tokens(right_title)
    similarity = len(a & b) / max(1, len(a | b))
    return similarity >= .42 or bool(left_targets and left_targets == right_targets and similarity >= .20)


def _rbb_comparison(url: str, title: str, target_keys: list[str], canonical: dict, prior: dict | None) -> dict:
    if prior:
        return {"state": "repeated_monitoring_signal", "basis": "same event fingerprint appeared in the prior Gatherer packet"}
    if url and url in canonical.get("source_urls", set()):
        return {"state": "known_evidence_reobserved", "basis": "canonical RBB state already references this source URL"}
    title_tokens = _content_tokens(title)
    best_overlap = 0.0
    for target_key in target_keys:
        text_tokens = _content_tokens((canonical.get("entity_text") or {}).get(target_key, ""))
        if title_tokens:
            best_overlap = max(best_overlap, len(title_tokens & text_tokens) / len(title_tokens))
    if best_overlap >= .75:
        return {"state": "possible_confirmation_of_known_state", "basis": "headline substantially overlaps the target's canonical public attributes"}
    if target_keys:
        return {"state": "new_to_rbb_candidate", "basis": "tracked target resolved but no matching canonical source or strong state overlap found"}
    return {"state": "entity_resolution_required", "basis": "no canonical RBB target key resolved"}


def _source_coverage(web_phase: dict) -> dict:
    health = web_phase.get("source_health") or []
    expected = len(health)
    successful = sum(str(row.get("status") or "").lower() in {"ok", "healthy", "success"} for row in health)
    failed = [row.get("source") or row.get("url") for row in health
              if str(row.get("status") or "").lower() not in {"ok", "healthy", "success"}]
    return {
        "expected_sources": expected,
        "successful_sources": successful,
        "failed_sources": [value for value in failed if value],
        "coverage_pct": round(100 * successful / expected, 1) if expected else None,
        "status": "unmeasured" if not expected else ("complete" if successful == expected else "partial"),
    }


def _consistency_check(*, input_items: int, source_checks: dict, tracked_total: int,
                       tracked_entities_error: str | None, input_status: str) -> dict:
    """2026-10-03 (Defect 5, Gate E): JSON Schema validity alone let a
    self-contradictory receipt through (tracked_entities_total: 0, all 24
    sources failed, yet input_items: 370 and changes_detected: 69, with
    input_status: "ok"). This is the semantic check the handoff doc
    explicitly calls out as missing. Deliberately narrow -- it only fires
    on the specific contradictions actually observed, not on every zero
    (a genuinely empty graph with no collected items is not an error)."""
    reasons = []
    if (input_items > 0 and input_status == "ok"
            and source_checks["expected_sources"] > 0
            and source_checks["successful_sources"] == 0):
        reasons.append(
            f"input_status is 'ok' and input_items={input_items}, but all "
            f"{source_checks['expected_sources']} configured sources are reported failed -- "
            "no provenance (cached/prior-day input) explains where these items came from."
        )
    if tracked_total == 0 and tracked_entities_error is None and input_items > 0:
        reasons.append(
            "tracked_entities_total is 0 with no tracked_entities_error recorded, despite a "
            "nonzero collection this cycle -- the canonical ecosystem universe is not actually "
            "known to be empty; this combination should not occur silently."
        )
    return {"consistent": not reasons, "reasons": reasons}


def _source_ledger(changes: list[dict]) -> list[dict]:
    ledger, seen = [], set()
    for change in changes:
        rows = [{"source": change["source"], "source_url": change["source_url"],
                 "source_owner": change["source_owner"], "change_ids": [change["change_id"]]}]
        rows.extend({**row, "change_ids": [change["change_id"]]}
                    for row in change.get("supporting_sources") or [])
        for row in rows:
            key = (row.get("source_url"), row.get("source"))
            if key in seen:
                existing = next(item for item in ledger if (item.get("source_url"), item.get("source")) == key)
                existing["change_ids"] = sorted(set(existing["change_ids"] + row["change_ids"]))
                continue
            seen.add(key)
            ledger.append(row)
    return ledger


def build_packet(web_phase: dict[str, Any], *, now: datetime | None = None,
                 ecosystem_path: Path | None = None, prior_packet: dict[str, Any] | None = None) -> dict[str, Any]:
    end = _utc(now)
    start = end - timedelta(hours=24)
    aliases, tracked_total, canonical_state, tracked_entities_error = _tracked_entities(ecosystem_path)
    raw_items: list[dict] = []
    for key, domain in (("industry_primary", "restaurant_industry"),
                        ("industry_technology", "restaurant_technology"),
                        ("world_national", "world_national")):
        for item in web_phase.get(key) or []:
            raw_items.append((item, domain))

    seen: set[str] = set()
    event_index: dict[str, dict] = {}
    prior_fingerprints = {str(row.get("fingerprint")): row for row in (prior_packet or {}).get("changes") or [] if row.get("fingerprint")}
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
        matched_records = []
        for value in supplied:
            resolved = aliases.get(value.casefold())
            matched_records.append(resolved or {"name": value, "target_key": ""})
        haystack = f" {title} {item.get('summary') or ''} ".casefold()
        for alias, resolved_entity in aliases.items():
            if len(alias) >= 4 and re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", haystack):
                matched_records.append(resolved_entity)
        matched_by_name = {row["name"]: row for row in matched_records}
        matched = set(matched_by_name)
        target_keys = sorted({row.get("target_key") for row in matched_by_name.values() if row.get("target_key")})
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
        clustered = next((candidate for candidate in changes
                          if _same_event(candidate, title, signal, target_keys, observed)), None)
        prior_cluster = next((candidate for candidate in (prior_packet or {}).get("changes") or []
                              if _same_event(candidate, title, signal, target_keys, observed)), None)
        fingerprint = (clustered or prior_cluster or {}).get("fingerprint") or hashlib.sha256(
            _event_key(title, signal, target_keys, observed).encode()).hexdigest()[:20]
        change_id = "gchg-" + fingerprint[:16]
        source_owner = _source_owner(item, domain)
        prior = prior_fingerprints.get(fingerprint)
        impact_score = materiality
        relevance_score = 90 if target_keys else (60 if domain != "world_national" else 25)
        age_hours = max(0.0, (end - observed).total_seconds() / 3600)
        recency_score = max(0, round(100 - (age_hours / 24 * 60)))
        verification_need = 90 if signal in HIGH_VERIFICATION_NEED else (70 if confidence == "low" else 45)
        priority_score = round(impact_score * .4 + relevance_score * .3 + recency_score * .15 + verification_need * .15)
        change = {
            "change_id": change_id,
            "title": title,
            "summary": str(item.get("summary") or title)[:500],
            "observed_at": observed.isoformat(),
            "date_precision": precision,
            "temporal": {
                "discovered_at": end.isoformat().replace("+00:00", "Z"),
                "published_at": observed.isoformat(),
                "event_or_publication_date": observed.date().isoformat(),
                "event_date_status": "publication_date_used_as_proxy",
            },
            "entities": sorted(matched),
            "target_keys": target_keys,
            "signal_type": signal,
            "domain": domain,
            "source": source,
            "source_url": url,
            "source_owner": source_owner,
            "confidence": confidence,
            "materiality": materiality,
            "fingerprint": fingerprint,
            "novelty": {"state": "seen_prior_day" if prior else "new_in_window", "prior_change_id": prior.get("change_id") if prior else None},
            "rbb_comparison": _rbb_comparison(url, title, target_keys, canonical_state, prior),
            "corroboration": {"source_count": 1, "independent_source_count": 1 if source_owner in {"trade_press", "general_news", "government_or_regulator"} else 0, "state": "single_source"},
            "scores": {"impact": impact_score, "ecosystem_relevance": relevance_score, "recency": recency_score, "verification_need": verification_need, "priority": priority_score},
            "verification_state": "candidate",
        }
        existing = clustered or event_index.get(fingerprint)
        if existing:
            duplicates += 1
            existing["corroboration"]["source_count"] += 1
            if source_owner in {"trade_press", "general_news", "government_or_regulator"}:
                existing["corroboration"]["independent_source_count"] += 1
            existing["corroboration"]["state"] = "multi_source"
            existing.setdefault("supporting_sources", []).append({"source": source, "source_url": url, "source_owner": source_owner})
            existing["confidence"] = "high" if existing["corroboration"]["independent_source_count"] >= 2 else existing["confidence"]
            existing["clustered_headlines"] = sorted(set((existing.get("clustered_headlines") or []) + [title]))
            continue
        event_index[fingerprint] = change
        changes.append(change)
        sources.add(source)
        domains.add(domain)
        observed_entities.update(matched)

    changes.sort(key=lambda x: (-x["scores"]["priority"], x["title"].casefold()))
    escalations = []
    for change in changes:
        if change["materiality"] < 70:
            continue
        reason = "Material 24-hour change signal requires source verification before canonical use."
        if not change["entities"]:
            reason += " Entity resolution is also required."
        playbook = SPECIFIC_PLAYBOOKS.get(change["signal_type"], "change_monitor")
        dedupe_key = f"{change['fingerprint']}:{playbook}"
        escalations.append({
            "change_id": change["change_id"],
            "target_names": change["entities"],
            "target_keys": change["target_keys"],
            "recommended_playbook": playbook,
            "reason": reason,
            "priority": "high" if change["materiality"] >= 85 else "medium",
            "verification_questions": [
                "Does an underlying primary or entity-controlled source confirm the event?",
                "What is the exact effective date, scope, and current state?",
                "Is there conflicting or disconfirming evidence?",
            ],
            "source_urls": [change["source_url"]] + [row["source_url"] for row in change.get("supporting_sources") or []],
            "dedupe_key": dedupe_key,
            "status": "ready_for_hunter_preparation" if change["target_keys"] else "needs_entity_resolution",
            "hunter_job": {
                "playbook": playbook,
                "target_keys": change["target_keys"],
                "depth": "forensic" if change["materiality"] >= 90 else "monitor",
                "objective": f"Verify Gatherer change candidate {change['change_id']}: {change['title']}",
                "known_source_urls": [change["source_url"]] + [row["source_url"] for row in change.get("supporting_sources") or []],
                "prior_state_hint": change["rbb_comparison"],
            },
        })

    packet_id = "gatherer-" + end.strftime("%Y%m%dT%H%M%SZ")
    source_checks = _source_coverage(web_phase)
    input_status = web_phase.get("status") or "unknown"
    consistency = _consistency_check(
        input_items=len(raw_items), source_checks=source_checks, tracked_total=tracked_total,
        tracked_entities_error=tracked_entities_error, input_status=input_status,
    )
    return {
        "contract": CONTRACT,
        "packet_id": packet_id,
        "generated_at": end.isoformat().replace("+00:00", "Z"),
        "window": {"start": start.isoformat().replace("+00:00", "Z"),
                   "end": end.isoformat().replace("+00:00", "Z"), "hours": 24},
        "coverage": {
            "tracked_entities_total": tracked_total,
            "tracked_entities_error": tracked_entities_error,
            "tracked_entities_observed": sorted(observed_entities),
            "sources_observed": sorted(s for s in sources if s),
            "domains_observed": sorted(domains),
            "source_checks": source_checks,
            "limitations": [
                "Coverage reflects configured public feeds, not proof that every ecosystem entity was checked.",
                "Date-only publication timestamps have day precision.",
                "Candidate signals are unverified until reviewed or escalated to Hunter.",
            ],
        },
        "source_ledger": _source_ledger(changes),
        "changes": changes,
        "hunter_escalations": escalations,
        "run_receipt": {
            "status": "complete" if source_checks["status"] == "complete" else "partial",
            "input_status": input_status,
            "collection_errors": web_phase.get("errors") or [],
            "canonical_state_compared": tracked_entities_error is None,
            "schema_validation": "pending_write_gate",
            "receipt_consistent": consistency["consistent"],
            "receipt_consistency_reasons": consistency["reasons"],
        },
        "stats": {"input_items": len(raw_items), "changes_detected": len(changes),
                  "hunter_escalations": len(escalations), "duplicates_removed": duplicates,
                  "excluded_outside_window": excluded_old, "excluded_world_noise": excluded_noise,
                  "new_signals": sum(c["novelty"]["state"] == "new_in_window" for c in changes),
                  "seen_prior_day": sum(c["novelty"]["state"] == "seen_prior_day" for c in changes),
                  "multi_source_changes": sum(c["corroboration"]["state"] == "multi_source" for c in changes),
                  "new_to_rbb_candidates": sum(c["rbb_comparison"]["state"] == "new_to_rbb_candidate" for c in changes),
                  "known_evidence_reobserved": sum(c["rbb_comparison"]["state"] in {"known_evidence_reobserved", "possible_confirmation_of_known_state"} for c in changes)},
    }


def validate_packet(packet: dict[str, Any]) -> list[dict]:
    from jsonschema import Draft7Validator, FormatChecker
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = []
    for error in Draft7Validator(schema, format_checker=FormatChecker()).iter_errors(packet):
        errors.append({"path": "/".join(map(str, error.absolute_path)) or "<root>",
                       "message": error.message})
    return errors


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _queue_hunter_escalations(packet: dict, path: Path = HUNTER_QUEUE_PATH) -> int:
    existing = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                existing.add(json.loads(line).get("dedupe_key"))
            except ValueError:
                continue
    changes = {row.get("change_id"): row for row in packet.get("changes") or []}
    additions = []
    for escalation in packet.get("hunter_escalations") or []:
        change = changes.get(escalation.get("change_id")) or {}
        if escalation.get("status") != "ready_for_hunter_preparation":
            continue
        if change.get("novelty", {}).get("state") != "new_in_window":
            continue
        if escalation.get("dedupe_key") in existing:
            continue
        additions.append({"schema": "rb.gatherer_hunter_escalation.v1",
                          "queued_at": packet.get("generated_at"),
                          "packet_id": packet.get("packet_id"), **escalation})
    if additions:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for row in additions:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    return len(additions)


def write_packet(packet: dict[str, Any], path: Path = CACHE_PATH, *,
                 history_dir: Path = HISTORY_DIR, queue_path: Path = HUNTER_QUEUE_PATH) -> None:
    packet.setdefault("run_receipt", {})["schema_validation"] = "valid"
    errors = validate_packet(packet)
    if errors:
        raise ValueError(f"Gatherer packet failed schema validation: {errors[:3]}")
    queued = _queue_hunter_escalations(packet, queue_path)
    packet["run_receipt"]["hunter_jobs_queued"] = queued
    _atomic_json(path, packet)
    history_path = history_dir / f"{packet['packet_id']}.json"
    _atomic_json(history_path, packet)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assessment", type=Path, default=core.SYSTEM_DIR / ".cache" / "intelligence_assessment.json")
    parser.add_argument("--output", type=Path, default=CACHE_PATH)
    parser.add_argument("--json", action="store_true", dest="emit_json")
    args = parser.parse_args()
    data = json.loads(args.assessment.read_text(encoding="utf-8"))
    prior = None
    if args.output.exists():
        try:
            prior = json.loads(args.output.read_text(encoding="utf-8"))
        except Exception:
            prior = None
    packet = build_packet(data.get("phase_1_web") or {}, prior_packet=prior)
    write_packet(packet, args.output)
    if args.emit_json:
        print(json.dumps(packet, indent=2))
    else:
        print(f"gatherer: {len(packet['changes'])} changes, {len(packet['hunter_escalations'])} Hunter escalations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
