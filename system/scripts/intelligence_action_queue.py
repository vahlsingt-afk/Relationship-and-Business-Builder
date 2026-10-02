#!/usr/bin/env python3
"""Build one ranked, review-first queue from RB's intelligence detectors.

The queue recommends a posture; it never contacts an account, creates an
opportunity, changes a pursuit stage, or writes a canonical intelligence claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import competitor_intelligence_common as cic
import rb_core as core
from brief_acceptance_check import _story_words

CACHE_PATH = core.CACHE_DIR / "intelligence_action_queue.json"
RADAR_PATH = core.CACHE_DIR / "sales_opportunity_radar.json"
PAGE_PATH = core.CACHE_DIR / "entity_page_changes.json"
BASELINE_PATH = core.CACHE_DIR / "baseline_research_gate.json"
RAMIFICATION_PATH = core.CACHE_DIR / "intelligence_ramifications.json"
# RB-2026-09-15 — Codex handoff item #5 prerequisite: build() below fully
# regenerates CACHE_PATH every cycle (it's a materialized view over four
# other detectors' caches, not itself a source of truth), so any status
# stamped directly onto an item would be silently wiped the next cycle.
# STATE_PATH is the durable side-store build() merges into every fresh
# rebuild, mirroring sales_opportunity_radar.py's own state-merge pattern
# (sales_opportunity_radar_state.json) rather than review_queue.json's
# append-only model, which doesn't fit a rebuilt-from-scratch view.
STATE_PATH = core.CACHE_DIR / "intelligence_action_queue_state.json"

VALID_DISPOSITIONS = frozenset({"accepted", "rejected", "deferred"})


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _id(kind: str, entity: str, evidence: str) -> str:
    digest = hashlib.sha256(f"{kind}|{entity}|{evidence}".encode()).hexdigest()[:16]
    return f"iaq-{digest}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve(queue_id: str, disposition: str, *, note: str = "") -> dict:
    """Record Todd's disposition on one queue item -- the durable record
    outcome calibration will eventually read. Never re-scores or mutates
    the item's action_class; purely an audit-trail annotation, same
    discipline as resolveCompetitorReviewItem. Snapshots item_type/entity/
    action_class/priority_score AS THEY WERE at resolution time, since the
    live item disappears or re-scores on the next build() -- without this
    snapshot, a later calibration pass couldn't tell what was actually
    being accepted or rejected.

    Raises ValueError for an invalid disposition, KeyError if queue_id
    isn't in the current (most recently built) queue -- callers must read
    the queue first to get a real id, same as every other resolve-style
    endpoint in this codebase."""
    if disposition not in VALID_DISPOSITIONS:
        raise ValueError(f"disposition must be one of {sorted(VALID_DISPOSITIONS)}, got {disposition!r}")
    current = _load(CACHE_PATH)
    item = next((row for row in current.get("items") or [] if row.get("queue_id") == queue_id), None)
    if item is None:
        raise KeyError(
            f"queue_id {queue_id!r} not found in the current intelligence action queue. "
            "Call getIntelligenceActionQueue first to get a valid id -- ids can change when "
            "underlying evidence changes."
        )
    state = _load(STATE_PATH)
    resolutions = state.setdefault("resolutions", {})
    now = _now_iso()
    entry = resolutions.get(queue_id) or {"first_seen": now}
    entry.update({
        "queue_id": queue_id,
        "item_type": item.get("item_type"),
        "entity": item.get("entity"),
        "entity_id": item.get("entity_id"),
        "action_class_at_resolution": item.get("action_class"),
        "priority_score_at_resolution": item.get("priority_score"),
        "disposition": disposition,
        "note": note,
        "resolved_at": now,
    })
    resolutions[queue_id] = entry
    cic.save_json_atomic(STATE_PATH, state)
    return entry


def _confidence_points(value: str) -> int:
    return {"high": 12, "medium": 6, "low": 0}.get(str(value).lower(), 2)


def _distinct_evidence_count(evidence: list[str]) -> int:
    """Count independently-corroborating evidence items, not raw strings.

    RB-DEFECT (2026-09-15, Codex handoff item #2): a live Yum Brands
    hypothesis carried 3 supporting_evidence strings, but two of them were
    separate outlets (Business Wire, Benzinga) reporting the identical
    Pizza Hut divestiture -- one real-world event, not two corroborating
    signals. Raw len(supporting_evidence) treated that as full evidence
    weight. Reuses brief_acceptance_check._story_words' same-event
    fingerprint (already relied on to gate the brief itself) so a cluster
    of headlines about one event counts once."""
    clusters: list[set[str]] = []
    for item in evidence:
        words = _story_words(str(item))
        if not words:
            clusters.append(words)
            continue
        matched = False
        for cluster in clusters:
            if not cluster:
                continue
            shared = words & cluster
            overlap = len(shared) / min(len(words), len(cluster))
            if (len(shared) >= 3 and overlap >= 0.30) or (len(shared) >= 2 and overlap >= 0.50):
                matched = True
                break
        if not matched:
            clusters.append(words)
    return len(clusters)


def _threshold(score: int, *, active_pursuit: bool, incumbent_count: int,
               confidence: str, evidence_count: int, entity_id: str | None) -> tuple[str, str]:
    """Return action class and the human-readable reason for its gate.

    RB-DEFECT (2026-09-15, Codex handoff item #2): a live Yum Brands
    hypothesis reached "build_pursuit" -- RB's #1-ranked recommendation --
    with entity_id=None (unresolved identity) and zero verified incumbent
    exposure (no real buying trigger, just a COO retirement and a
    divestiture). A high numeric score alone was enough to escalate. Every
    action class beyond research_further/monitor now also requires a
    resolved entity_id -- Todd cannot act on an account RB cannot even
    identify -- and build_pursuit now requires a verified incumbent, same
    as competitive_displacement_opportunity already did.
    """
    entity_resolved = bool(entity_id)
    if (score >= 78 and active_pursuit and confidence == "high" and evidence_count >= 2
            and entity_resolved):
        return "contact_account", "Active pursuit, high confidence, and multiple current signals."
    if (score >= 72 and incumbent_count and confidence in {"high", "medium"} and evidence_count >= 2
            and entity_resolved):
        return "competitive_displacement_opportunity", "Named incumbent exposure plus multiple evaluation-window signals."
    if (score >= 68 and incumbent_count and confidence == "high" and evidence_count >= 2
            and entity_resolved):
        return "build_pursuit", "High-confidence buying-window evidence, a verified incumbent, and a resolved entity cleared the pursuit-development threshold."
    if score >= 42:
        return "research_further", "Signal is material but does not yet clear an external-action threshold."
    return "monitor", "Evidence is below the research escalation threshold."


def _radar_items(today: date) -> list[dict]:
    report = _load(RADAR_PATH)
    states = (_load(core.CACHE_DIR / "sales_opportunity_radar_state.json").get("entities") or {})
    rows = []
    for item in report.get("buying_window_hypotheses") or []:
        entity_key = item.get("entity_id") or item.get("entity")
        state = states.get(entity_key) or {}
        evidence = [x for x in item.get("supporting_evidence") or [] if x]
        incumbents = item.get("incumbent_exposure") or []
        confidence = str(item.get("confidence") or "low").lower()
        raw = int(item.get("evaluation_likelihood_score") or 0)
        score = min(100, raw + _confidence_points(confidence) + (6 if state.get("outcome") == "active_pursuit" else 0))
        action, reason = _threshold(
            score, active_pursuit=state.get("outcome") == "active_pursuit",
            incumbent_count=len(incumbents), confidence=confidence,
            evidence_count=_distinct_evidence_count(evidence),
            entity_id=item.get("entity_id"),
        )
        rows.append({
            "queue_id": _id("buying_window", str(entity_key), "|".join(evidence)),
            "entity_id": item.get("entity_id"), "entity": item.get("entity"),
            "item_type": "buying_window_hypothesis", "priority_score": score,
            "action_class": action, "threshold_reason": reason,
            "summary": item.get("hypothesis"), "confidence": confidence,
            "supporting_evidence": evidence, "incumbent_exposure": incumbents,
            "missing_evidence": item.get("missing_evidence") or [],
            "disconfirming_questions": item.get("disconfirming_questions") or [],
            "status": "pending_review", "source_cache": str(RADAR_PATH.relative_to(core.SYSTEM_DIR)),
        })
    return rows


def _other_items() -> list[dict]:
    rows = []
    for item in _load(PAGE_PATH).get("changes") or []:
        score = 58 + (15 if item.get("material_keywords_detected") else 0)
        # RB-DEFECT (2026-09-16): _threshold()'s docstring states "every
        # action class beyond research_further/monitor now also requires a
        # resolved entity_id" (2026-09-15 Yum Brands fix), but that gate
        # was only ever wired into _radar_items() -- this branch has always
        # escalated on raw score alone. Found live: a real entity_id
        # happened to be present for today's only case (Toast), so this
        # was silent until now, but an unresolved page-monitor entity would
        # reach RB's top action tier the same way Yum Brands did.
        action = "research_further" if score < 68 or not item.get("entity_id") else "build_pursuit"
        rows.append({
            "queue_id": _id("page_change", str(item.get("entity")), str(item.get("url"))),
            "entity_id": item.get("entity_id"), "entity": item.get("entity"),
            "item_type": "first_party_page_change", "priority_score": score,
            "action_class": action,
            "threshold_reason": "Material first-party page change requires corroboration before external action.",
            "summary": item.get("change_excerpt"), "confidence": "medium",
            "supporting_evidence": [item.get("url")],
            "disconfirming_questions": ["Is this a substantive business change rather than routine page maintenance?"],
            "status": "pending_review", "source_cache": str(PAGE_PATH.relative_to(core.SYSTEM_DIR)),
        })
    for item in _load(BASELINE_PATH).get("queued") or []:
        score = min(70, 40 + int(item.get("opportunity_score") or 0) // 3)
        rows.append({
            "queue_id": _id("baseline_gap", str(item.get("entity_name")), str(item.get("request_id"))),
            "entity_id": item.get("entity_id"), "entity": item.get("entity_name"),
            "item_type": "baseline_research_gap", "priority_score": score,
            "action_class": "research_further", "threshold_reason": "Material signal cannot be judged against an adequate baseline.",
            "summary": f"Fill the missing baseline dimensions: {', '.join(item.get('dimensions_missing') or []) or 'baseline evidence'}.",
            "confidence": "high", "supporting_evidence": item.get("trigger_sources") or [],
            "disconfirming_questions": ["Does existing account research already answer this gap under another entity alias?"],
            "status": "pending_review", "source_cache": str(BASELINE_PATH.relative_to(core.SYSTEM_DIR)),
        })
    for item in _load(RAMIFICATION_PATH).get("ramifications") or []:
        score = min(90, int(item.get("priority_score") or 0) + 10)
        rows.append({
            "queue_id": _id("ramification", str(item.get("entity_id")), str(item.get("ramification_id"))),
            "entity_id": item.get("entity_id"), "entity": item.get("entity_name"),
            "item_type": "downstream_ramification", "priority_score": score,
            # RB-DEFECT (2026-09-16): same unresolved-entity_id gap as the
            # first_party_page_change branch above -- see that comment.
            "action_class": "research_further" if score < 68 or not item.get("entity_id") else "build_pursuit",
            "threshold_reason": "Current evidence may affect downstream account or competitor artifacts.",
            "summary": item.get("evidence_summary"), "confidence": item.get("confidence") or "medium",
            "supporting_evidence": item.get("source_refs") or [],
            "disconfirming_questions": [item.get("recommended_follow_up")],
            "affected_artifacts": item.get("affected_artifacts") or [],
            "status": "pending_review", "source_cache": str(RAMIFICATION_PATH.relative_to(core.SYSTEM_DIR)),
        })
    queue = cic.load_review_queue()
    competitor_groups: dict[str, list[dict]] = {}
    for item in queue.get("pending_reviews") or []:
        if item.get("status") == "pending":
            competitor_groups.setdefault(str(item.get("competitor_slug") or "unknown"), []).append(item)
    for slug, pending in competitor_groups.items():
        reasons = [str(item.get("reason") or "") for item in pending if item.get("reason")]
        evidence_ids = list(dict.fromkeys(item.get("evidence_id") for item in pending if item.get("evidence_id")))
        rows.append({
            "queue_id": _id("competitor_review", slug, "|".join(sorted(str(x) for x in evidence_ids))),
            "entity_id": None, "entity": slug,
            "item_type": "competitor_review", "priority_score": 55,
            "action_class": "research_further", "threshold_reason": "Competitive evidence requires battle-card review.",
            "summary": f"{len(pending)} pending competitor review item(s). Latest: {reasons[-1] if reasons else 'review required'}",
            "confidence": "medium", "supporting_evidence": evidence_ids[:5],
            "disconfirming_questions": ["Does the underlying evidence materially change the category-specific competitive posture?"],
            "status": "pending_review", "source_review_ids": [item.get("review_id") for item in pending],
        })
    return rows


def build(today: date | None = None) -> dict:
    today = today or date.today()
    deduped = {row["queue_id"]: row for row in _radar_items(today) + _other_items()}
    rows = sorted(deduped.values(), key=lambda row: (-row["priority_score"], str(row.get("entity") or "").lower()))
    counts = {key: 0 for key in ("monitor", "research_further", "contact_account", "build_pursuit", "competitive_displacement_opportunity")}
    # RB-2026-09-15: merge Todd's durable dispositions (see resolve() /
    # STATE_PATH above) back onto this fresh rebuild -- without this, a
    # resolved item would read as "pending_review" again on the very next
    # cycle, since rows are rebuilt from scratch every time.
    resolutions = _load(STATE_PATH).get("resolutions") or {}
    resolution_counts = {"pending_review": 0, "resolved": 0}
    for rank, row in enumerate(rows, start=1):
        counts[row["action_class"]] += 1
        row["rank"] = rank
        res = resolutions.get(row["queue_id"])
        if res:
            row["status"] = "resolved"
            row["disposition"] = res.get("disposition")
            row["disposition_note"] = res.get("note")
            row["resolved_at"] = res.get("resolved_at")
        else:
            row["status"] = "pending_review"
        resolution_counts[row["status"]] += 1
    payload = {
        "contract": "rb_intelligence_action_queue_v1", "date": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_pending": len(rows), "action_class_counts": counts,
        "resolution_counts": resolution_counts, "items": rows,
        "negative_evidence": {
            "buying_window_checks_without_signal": len(_load(RADAR_PATH).get("negative_evidence") or []),
            "pages_checked_without_change": len(_load(PAGE_PATH).get("unchanged") or []),
        },
        "policy": "recommendations_only; external outreach, pursuit creation, stage changes, and canonical mutations require their existing authorization paths",
    }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    args = parser.parse_args()
    report = build(date.fromisoformat(args.date) if args.date else None)
    print(json.dumps({key: report[key] for key in ("contract", "date", "total_pending", "action_class_counts", "negative_evidence")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
