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
HISTORY_PATH = core.SYSTEM_DIR / "research" / "gatherer_history.jsonl"
ESCALATIONS_PATH = core.SYSTEM_DIR / "research" / "gatherer_hunter_escalations.jsonl"

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


NOVELTY_UNRESOLVED_ENTITY = "unresolved_entity"
NOVELTY_RE_OBSERVED = "re_observed"
NOVELTY_KNOWN_STATE_CONFIRMATION = "possible_known_state_confirmation"
NOVELTY_REPEATED_MONITORING_SIGNAL = "repeated_monitoring_signal"
NOVELTY_NEW_TO_RBB = "new_to_rbb"


def _entities_with_known_relationships(path: Path | None = None) -> set[str]:
    """Canonical entity names with at least one active relationship on record.

    Used to flag a change as *possibly* confirming already-known state
    (e.g. a deployment headline about a brand that already has an active
    vendor relationship for that category on record in
    ecosystem_intelligence.json) rather than introducing something new.
    This is a coarse heuristic, not a fact match -- it never claims the
    specific headline matches the specific relationship, only that the
    entity's state in this area is not a blank slate.
    """
    path = path or core.ECOSYSTEM_INTELLIGENCE_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    id_to_name = {
        entity.get("id"): str(entity.get("name") or "").strip()
        for entity in (data.get("entities") or [])
        if entity.get("id") and entity.get("name")
    }
    names: set[str] = set()
    for rel in data.get("relationships") or []:
        if rel.get("status") != "active":
            continue
        for key in ("from_entity_id", "to_entity_id"):
            name = id_to_name.get(rel.get(key))
            if name:
                names.add(name)
    return names


def _prior_history_signatures(history_records: list[dict]) -> tuple[set[str], set[tuple[str, str]]]:
    """Source URLs and (entity, signal_type) pairs already seen in prior Gatherer runs."""
    source_urls: set[str] = set()
    entity_signal_pairs: set[tuple[str, str]] = set()
    for record in history_records:
        for change in record.get("changes") or []:
            url = change.get("source_url")
            if url:
                source_urls.add(url)
            signal_type = change.get("signal_type")
            for entity in change.get("entities") or []:
                entity_signal_pairs.add((entity, signal_type))
    return source_urls, entity_signal_pairs


def _classify_novelty(change: dict, *, prior_source_urls: set[str],
                      prior_entity_signal_pairs: set[tuple[str, str]],
                      known_relationship_entities: set[str]) -> str:
    """Classify a change against Gatherer's own history and canonical state.

    Priority order: an entity we couldn't resolve makes everything else
    moot (unresolved_entity); the exact same article resurfacing in the
    rolling window is a re-observation, not new information (re_observed);
    failing that, an entity that already has an active canonical
    relationship on record is likely confirming known state
    (possible_known_state_confirmation); failing that, the same kind of
    signal about the same entity recurring under a different URL is an
    ongoing monitoring signal, not a fresh one (repeated_monitoring_signal);
    anything left over is genuinely new to RBB (new_to_rbb).
    """
    if not change["entities"]:
        return NOVELTY_UNRESOLVED_ENTITY
    if change["source_url"] and change["source_url"] in prior_source_urls:
        return NOVELTY_RE_OBSERVED
    if any(entity in known_relationship_entities for entity in change["entities"]):
        return NOVELTY_KNOWN_STATE_CONFIRMATION
    if any((entity, change["signal_type"]) in prior_entity_signal_pairs for entity in change["entities"]):
        return NOVELTY_REPEATED_MONITORING_SIGNAL
    return NOVELTY_NEW_TO_RBB


def _score_dimensions(*, signal_type: str, domain: str, matched_entities: set[str], observed: datetime,
                      end: datetime, confidence: str, materiality: int) -> dict[str, int]:
    """Four independently interpretable 0-100 scores, kept separate from the
    single `materiality` composite (which stays for backward compatibility)
    so a caller can distinguish "how impactful a signal type this is" from
    "how relevant to our tracked ecosystem" from "how fresh" from "how much
    this needs Hunter verification" -- a blended score hides which of those
    actually drove it.
    """
    impact = MATERIAL_SIGNALS.get(signal_type, 35)

    if matched_entities:
        ecosystem_relevance = 90
    elif domain in ("restaurant_technology", "restaurant_industry"):
        ecosystem_relevance = 55
    else:
        ecosystem_relevance = 30

    hours_old = max(0.0, (end - observed).total_seconds() / 3600)
    recency = round(max(0.0, 100 - (hours_old * 100 / 24)))

    verification_need = {"low": 80, "medium": 50, "high": 20}.get(confidence, 50)
    if not matched_entities:
        verification_need = max(verification_need, 85)
    if materiality >= 85:
        verification_need = min(100, verification_need + 15)

    return {
        "impact": impact,
        "ecosystem_relevance": ecosystem_relevance,
        "recency": recency,
        "verification_need": verification_need,
    }


_SOURCE_FAILURE_STATUSES = {"fetch_error", "parse_error", "exception"}
_SOURCE_SUCCESS_STATUSES = {"ok", "cache_hit"}


def _summarize_source_health(web_phase: dict[str, Any]) -> dict[str, Any]:
    """Summarize web_scanner's per-source health for this run.

    An empty `changes` list is ambiguous on its own -- it can mean a genuinely
    quiet 24 hours, or it can mean every source failed to fetch. This summary
    makes that distinction explicit so callers never silently read "zero
    changes" as "no changes occurred."
    """
    raw_health = web_phase.get("source_health")
    upstream_status = web_phase.get("status", "unknown")
    if raw_health is None:
        return {
            "status": "unknown",
            "expected_sources": 0,
            "succeeded_sources": 0,
            "failed_sources": 0,
            "failed_source_names": [],
            "upstream_status": upstream_status,
        }
    expected = len(raw_health)
    failed = [h for h in raw_health if h.get("status") in _SOURCE_FAILURE_STATUSES]
    succeeded = [h for h in raw_health if h.get("status") in _SOURCE_SUCCESS_STATUSES]
    status = "ok"
    if upstream_status == "error" or expected == 0 or failed:
        status = "degraded"
    return {
        "status": status,
        "expected_sources": expected,
        "succeeded_sources": len(succeeded),
        "failed_sources": len(failed),
        "failed_source_names": sorted({str(h.get("source") or "unknown") for h in failed}),
        "upstream_status": upstream_status,
    }


def _cluster_changes(changes: list[dict]) -> tuple[list[dict], int]:
    """Group changes that describe the same underlying event into one.

    Clustering is scoped to entity-bearing changes keyed on
    (entities, signal_type, observed date) -- the same brand, same signal
    type, same day reported by different outlets at different URLs is almost
    always one real-world event, not several. Entity-less changes are left
    alone: clustering those by title alone risks merging genuinely distinct
    stories. The highest-materiality member of a cluster becomes the primary
    change; every other member is kept as a corroborating source rather than
    dropped outright.
    """
    buckets: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    passthrough: list[dict] = []
    for change in changes:
        if not change["entities"]:
            change["corroborating_sources"] = []
            passthrough.append(change)
            continue
        key = (tuple(sorted(change["entities"])), change["signal_type"], change["observed_at"][:10])
        if key not in buckets:
            order.append(key)
            buckets[key] = []
        buckets[key].append(change)

    merged_away = 0
    clustered: list[dict] = []
    for key in order:
        members = buckets[key]
        primary = max(members, key=lambda c: c["materiality"])
        others = [m for m in members if m is not primary]
        primary["corroborating_sources"] = [
            {"change_id": m["change_id"], "title": m["title"], "source": m["source"], "source_url": m["source_url"]}
            for m in others
        ]
        merged_away += len(others)
        clustered.append(primary)

    return clustered + passthrough, merged_away


def build_packet(web_phase: dict[str, Any], *, now: datetime | None = None,
                 ecosystem_path: Path | None = None, history_path: Path | None = None) -> dict[str, Any]:
    end = _utc(now)
    start = end - timedelta(hours=24)
    source_health = _summarize_source_health(web_phase)
    aliases, tracked_total = _tracked_entities(ecosystem_path)
    prior_urls, prior_pairs = _prior_history_signatures(read_history(history_path or HISTORY_PATH))
    known_rel_entities = _entities_with_known_relationships(ecosystem_path)
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
        scores = _score_dimensions(signal_type=signal, domain=domain, matched_entities=matched,
                                   observed=observed, end=end, confidence=confidence, materiality=materiality)
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
            "scores": scores,
            "verification_state": "candidate",
        }
        changes.append(change)
        sources.add(source)
        domains.add(domain)
        observed_entities.update(matched)

    changes, clustered_as_same_event = _cluster_changes(changes)
    novelty_counts: dict[str, int] = {}
    for change in changes:
        novelty = _classify_novelty(change, prior_source_urls=prior_urls, prior_entity_signal_pairs=prior_pairs,
                                    known_relationship_entities=known_rel_entities)
        change["novelty"] = novelty
        novelty_counts[novelty] = novelty_counts.get(novelty, 0) + 1
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
        "status": source_health["status"],
        "source_health": source_health,
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
            ] + (
                ["Source health is degraded this run -- a short or empty changes list may reflect fetch "
                 "failures, not a genuinely quiet 24 hours."]
                if source_health["status"] == "degraded" else []
            ),
        },
        "changes": changes,
        "hunter_escalations": escalations,
        "stats": {"input_items": len(raw_items), "changes_detected": len(changes),
                  "hunter_escalations": len(escalations), "duplicates_removed": duplicates,
                  "excluded_outside_window": excluded_old, "excluded_world_noise": excluded_noise,
                  "clustered_as_same_event": clustered_as_same_event, "novelty_counts": novelty_counts},
    }


def write_packet(packet: dict[str, Any], path: Path | None = None) -> None:
    # Resolved at call time (not bound as a def-time default) so a caller
    # can monkeypatch the module-level CACHE_PATH -- e.g. for test
    # isolation -- and have it actually take effect.
    path = path or CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # RB-DEFECT-2026-07-20 precedent (rb_core.write_cache): write to a temp
    # file in the same directory and rename, so a killed process or full
    # disk never leaves a reader observing a truncated packet.
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def write_history(packet: dict[str, Any], path: Path | None = None) -> None:
    """Append this packet's changes to Gatherer's durable cross-run history.

    `CACHE_PATH` only ever holds the latest run. Novelty classification
    (re_observed vs. new_to_rbb) needs prior days' changes to compare
    against, so every run appends one line here in addition to overwriting
    the cache.
    """
    path = path or HISTORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "packet_id": packet.get("packet_id"),
        "generated_at": packet.get("generated_at"),
        "window": packet.get("window"),
        "changes": packet.get("changes", []),
        "stats": packet.get("stats", {}),
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True))
        f.write("\n")


def read_history(path: Path | None = None, *, limit: int | None = None) -> list[dict]:
    """Read Gatherer's run history, oldest first. Malformed lines are skipped."""
    path = path or HISTORY_PATH
    if not path.exists():
        return []
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if limit is not None:
        return records[-limit:]
    return records


def append_hunter_escalations(packet: dict[str, Any], path: Path | None = None) -> list[dict]:
    """Append this packet's Hunter escalation candidates, deduplicated by change_id.

    A change can still be inside the rolling 24-hour window on two
    consecutive runs; without dedup it would be escalated twice. Returns the
    records actually appended (empty if every candidate was already logged).
    """
    path = path or ESCALATIONS_PATH
    escalations = packet.get("hunter_escalations") or []
    if not escalations:
        return []
    existing_ids: set[str] = set()
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    existing_ids.add(json.loads(line).get("change_id"))
                except json.JSONDecodeError:
                    continue
    new_records = []
    for escalation in escalations:
        change_id = escalation.get("change_id")
        if change_id in existing_ids:
            continue
        record = dict(escalation)
        record["packet_id"] = packet.get("packet_id")
        record["logged_at"] = packet.get("generated_at")
        new_records.append(record)
        existing_ids.add(change_id)
    if not new_records:
        return []
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for record in new_records:
            f.write(json.dumps(record, sort_keys=True))
            f.write("\n")
    return new_records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assessment", type=Path, default=core.SYSTEM_DIR / ".cache" / "intelligence_assessment.json")
    parser.add_argument("--output", type=Path, default=CACHE_PATH)
    parser.add_argument("--json", action="store_true", dest="emit_json")
    args = parser.parse_args()
    data = json.loads(args.assessment.read_text(encoding="utf-8"))
    packet = build_packet(data.get("phase_1_web") or {})
    write_packet(packet, args.output)
    write_history(packet)
    new_escalations = append_hunter_escalations(packet)
    if args.emit_json:
        print(json.dumps(packet, indent=2))
    else:
        print(f"gatherer: {len(packet['changes'])} changes, {len(packet['hunter_escalations'])} Hunter escalations "
              f"({len(new_escalations)} new)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

