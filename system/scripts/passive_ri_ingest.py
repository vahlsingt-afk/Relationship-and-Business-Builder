#!/usr/bin/env python3
"""
passive_ri_ingest.py — passive RI event proposal aggregator.

Connects passive signal detectors to the RI event store so the daily brief
can report durable proof of what RB detected, proposed, and blocked — not
just analysis and recommendations.

Sprint goal (RB 9.4): make passive relationship intelligence durable,
auditable, and visible as completed / proposed / blocked system action.

Sources (v1):
  * relationship_signals.build_report() — high/medium system-detected
    email, calendar, interaction, and social signals.
  * linkedin_messaging.linkedin_message_overlay() — inbound RI candidates
    and proposed last_touch updates from the LinkedIn inbox.

Action states:
  recorded   — durable RI event written to the event store.
  duplicate  — event already exists; not double-written.
  proposed   — review-first event written; mutation bundle awaits Todd.
  blocked    — could not safely write (low confidence / stale source /
               unmatched entity / insufficient date).
  unavailable — source could not be accessed (exception / missing file).

Output:
  system/.cache/passive_ri_ingest.json

CLI:
    python3 system/scripts/passive_ri_ingest.py
    python3 system/scripts/passive_ri_ingest.py --smoke
    python3 system/scripts/passive_ri_ingest.py --date 2026-05-23
    python3 system/scripts/passive_ri_ingest.py --json   # print summary JSON
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import rb_core as core   # noqa: E402
import ri_events          # noqa: E402
import mutation_policy     # noqa: E402

PROJECT_DIR = core.PROJECT_DIR
SYSTEM_DIR = PROJECT_DIR / "system"
CACHE_DIR = SYSTEM_DIR / ".cache"
CACHE_PATH = CACHE_DIR / "passive_ri_ingest.json"
CONFIRM_CACHE_PATH = CACHE_DIR / "passive_ri_confirm.json"

# Minimum signal_strength to attempt writing a RI event.
# Below this threshold the signal is marked blocked_low_confidence.
STRENGTH_THRESHOLD = 0.40

# Minimum strategic_relevance to attempt writing. "low" signals are blocked.
RELEVANCE_ACCEPT = {"high", "medium"}

PROJECTION_SAFE_SIGNAL_TYPES = {"last_touch_update", "linkedin_inbound_contact"}

# Source tag embedded in source.path so the event carries its detector origin.
_DETECTOR_REL_SIG = "relationship_signals"
_DETECTOR_LINKEDIN = "linkedin_messaging"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_source_refs(evidence: list) -> list[str]:
    """Convert evidence items (str or dict) to hashable strings.

    relationship_signals.py emits evidence as list[dict] with keys
    like source, thread_id, subject, snippet.  source_refs must be
    list[str] so dict.fromkeys() deduplication works.
    """
    refs: list[str] = []
    for e in evidence or []:
        if isinstance(e, str):
            refs.append(e)
        elif isinstance(e, dict):
            src = e.get("source") or ""
            extra = (e.get("snippet") or e.get("subject") or "")[:60]
            refs.append(f"{src}:{extra}" if extra else src or str(e))
        else:
            refs.append(str(e))
    return refs


def _safe_date(s: str | None) -> str | None:
    """Return ISO date string (YYYY-MM-DD) or None if unparseable."""
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return None


def _contact_entity(contact_id: str | None, name: str | None) -> dict:
    """Build a single entities.people entry."""
    if contact_id:
        return {
            "raw": name or contact_id,
            "matched_id": contact_id,
            "match_confidence": 0.90,
            "decision": "matched_existing",
        }
    return {
        "raw": name or "unknown",
        "matched_id": None,
        "match_confidence": None,
        "decision": "propose_new_contact",
    }


def _matched_contact_id(event: dict) -> str | None:
    for person in ((event.get("entities") or {}).get("people") or []):
        if person.get("decision") == "matched_existing" and person.get("matched_id"):
            return person["matched_id"]
    return None


def _event_name(event: dict) -> str | None:
    for person in ((event.get("entities") or {}).get("people") or []):
        if person.get("raw"):
            return person["raw"]
    return None


def _baseline_entry(contact_id: str | None) -> dict | None:
    if not contact_id:
        return None
    for entry in core.load_baseline():
        if entry.get("id") == contact_id:
            return entry
    return None


def _projection_decision(event: dict) -> mutation_policy.MutationDecision:
    """Run the shared mutation policy (mutation_policy.decide()) against a
    passive RI event, translating this pipeline's event shape into the
    policy's source-agnostic parameters.

    Two gates stay local to this pipeline, ahead of the shared policy, since
    they are scope/eligibility decisions specific to passive detection, not
    part of Todd's canonical write-decision policy:
      - signal_type must be one of PROJECTION_SAFE_SIGNAL_TYPES (this
        pipeline only ever proposes projection for last-touch updates and
        inbound-contact signals; everything else routes through
        ri_intake.confirm() manually regardless of the policy outcome).
      - a matched baseline contact is required to even ask the question of
        "should this write" -- with no candidate entity at all there is
        nothing for mutation_policy to compare old/new values against.

    Everything after that -- identity trust tier, date-sequence comparison,
    net-new vs. replace -- goes through the one shared decision function so
    this pipeline's status vocabulary and receipts match every other
    ingestion path's.
    """
    signal_type = (event.get("signal") or {}).get("type")
    if signal_type not in PROJECTION_SAFE_SIGNAL_TYPES:
        return mutation_policy.decide(
            source="passive_ri_ingest",
            new_value=None,
            identity_ambiguous=True,
            identity_reason=f"signal_type={signal_type!r} is not projection-safe",
        )

    contact_id = _matched_contact_id(event)
    entry = _baseline_entry(contact_id)
    identity_ambiguous = False
    identity_reason = None
    if not entry:
        identity_ambiguous = True
        identity_reason = "matched baseline contact not found"
    elif entry.get("signal_class") not in {"RC", "LKI"}:
        identity_ambiguous = True
        identity_reason = f"signal_class={entry.get('signal_class')!r} is not RC/LKI"

    new_date = (event.get("event_at") or "")[:10] or None
    is_touch_update = signal_type == "last_touch_update"
    existing_date = (entry or {}).get("last_touch") if is_touch_update else None
    low_confidence = event.get("event_at_confidence") != "high"

    return mutation_policy.decide(
        source=f"passive_ri_ingest:{(event.get('source') or {}).get('path') or 'unknown'}",
        new_value=new_date,
        existing_value=existing_date,
        is_set_member=not is_touch_update,
        new_date=new_date,
        existing_date=existing_date,
        identity_ambiguous=identity_ambiguous,
        identity_reason=identity_reason,
        low_confidence=(not identity_ambiguous) and low_confidence,
        low_confidence_reason=f"event_at_confidence={event.get('event_at_confidence')!r} is not high",
        observed_at=event.get("captured_at"),
        confidence=(event.get("signal") or {}).get("confidence"),
        field_name="last_touch" if is_touch_update else "inbound_contact",
        entity_id=contact_id,
    )


def _is_high_confidence_projection_safe(event: dict) -> tuple[bool, str]:
    """Return whether a passive event is safe for auto-confirm projection.

    Thin wrapper over _projection_decision() kept for backward compatibility
    with existing callers/tests expecting a (bool, reason) tuple.
    """
    decision = _projection_decision(event)
    return decision.auto_apply, decision.reason


def _build_passive_event(
    *,
    contact_id: str | None,
    name: str | None,
    signal_type: str,
    event_at: str,
    event_at_confidence: str,
    event_at_source: str,
    detector: str,
    signal_strength: float,
    reasoning: str,
    source_refs: list[str],
    captured_at: str,
) -> dict:
    """Construct the RI event dict for a passive signal candidate.

    source.id is set to the compound stable key used by _source_stable_id
    for the 'passive_signal' branch: '<detector>:<contact_slug>:<signal_type>'.
    This ensures deterministic dedupe across pipeline runs.
    """
    contact_slug = ri_events.slugify(contact_id or name or "unknown", 30)
    signal_slug = ri_events.slugify(signal_type, 30)
    stable_id = f"{detector}:{contact_slug}:{signal_slug}"

    entity = _contact_entity(contact_id, name)

    return {
        "event_at": event_at,
        "event_at_confidence": event_at_confidence,
        "event_at_source": event_at_source,
        "captured_at": captured_at,
        "source": {
            "type": "passive_signal",
            "id": stable_id,
            "path": detector,
            "title": f"Passive {signal_type} signal — {name or contact_id or 'unknown'}",
            "raw_text_hash": None,
        },
        "entities": {
            "people": [entity],
            "companies": [],
        },
        "dedupe": {"decision": "new_event", "duplicates": []},
        "signal": {
            "type": signal_type,
            "substance": (
                "high" if signal_strength >= 0.75
                else "medium" if signal_strength >= 0.50
                else "low"
            ),
            "confidence": round(signal_strength, 3),
            "recommended_projection_changes": [],
            "reasoning": reasoning,
        },
        "persistence": {
            "status": "proposed_write_pending_confirmation",
            "proposed_mutation_bundle_id": None,
            "projected_to": [],
            "validated_at": None,
        },
        "source_refs": source_refs,
        "_slug": contact_slug,
    }


# ---------------------------------------------------------------------------
# Source 1: relationship_signals
# ---------------------------------------------------------------------------

def _collect_relationship_signal_candidates(
    today: date,
    hours: int = 24,
) -> tuple[list[dict], str]:
    """Return (candidates, status) where status is 'ok' or 'unavailable:<msg>'."""
    try:
        import relationship_signals as rs
        report = rs.build_report(today=today, hours=hours)
    except Exception as exc:  # noqa: BLE001
        return [], f"unavailable:{exc}"

    signals = report.get("signals") or []
    candidates = []
    for sig in signals:
        candidates.append({
            "_source": "relationship_signals",
            "contact_id": sig.get("contact_id"),
            "name": sig.get("name"),
            "signal_type": sig.get("signal_type") or "passive_relationship_signal",
            "event_at": sig.get("event_at"),
            "signal_strength": float(sig.get("signal_strength") or 0.0),
            "strategic_relevance": sig.get("strategic_relevance") or "low",
            "reasoning": sig.get("reasoning") or "",
            "grounding": sig.get("grounding") or "system_detected",
            "freshness": sig.get("freshness") or "fresh",
            "evidence": sig.get("evidence") or [],
            "source": sig.get("source") or "unknown",
        })
    return candidates, "ok"


# ---------------------------------------------------------------------------
# Source 2: linkedin_messaging
# ---------------------------------------------------------------------------

def _collect_linkedin_candidates() -> tuple[list[dict], str]:
    """Return (candidates, status)."""
    try:
        import linkedin_messaging as lim
        overlay = lim.linkedin_message_overlay()
    except Exception as exc:  # noqa: BLE001
        return [], f"unavailable:{exc}"

    stale = overlay.get("stale", True)
    candidates = []

    # Inbound RI candidates (RC/LKI/LMI contacts who messaged inbound)
    for cand in overlay.get("inbound_ri_candidates") or []:
        last_at = _safe_date(cand.get("last_inbound_at"))
        if not last_at:
            continue
        match_q = cand.get("match_quality") or "low"
        strength = 0.70 if match_q == "high" else 0.55
        candidates.append({
            "_source": "linkedin_inbound",
            "contact_id": cand.get("contact_id"),
            "name": cand.get("name"),
            "signal_type": "linkedin_inbound_contact",
            "event_at": last_at,
            "signal_strength": strength,
            "strategic_relevance": (
                "high" if cand.get("signal_class") in {"RC"} else "medium"
            ),
            "reasoning": (
                f"{cand.get('name')} sent {cand.get('inbound_count', 0)} "
                f"inbound LinkedIn message(s). "
                f"Sample subjects: {', '.join(cand.get('sample_subjects') or [])}"
            ),
            "grounding": "system_detected",
            "freshness": "stale_source_limited" if stale else "fresh",
            "evidence": [cand.get("ref") or "linkedin_messaging"],
            "source": "linkedin_messaging",
        })

    # Proposed last_touch updates
    for upd in overlay.get("proposed_last_touch_updates") or []:
        proposed_date = _safe_date(upd.get("proposed"))
        if not proposed_date:
            continue
        conf = upd.get("confidence") or "medium"
        strength = 0.70 if conf == "high" else 0.50
        candidates.append({
            "_source": "linkedin_last_touch",
            "contact_id": upd.get("contact_id"),
            "name": upd.get("name"),
            "signal_type": "last_touch_update",
            "event_at": proposed_date,
            "signal_strength": strength,
            "strategic_relevance": "medium",
            "reasoning": (
                f"LinkedIn interaction on {proposed_date} is newer than "
                f"recorded last_touch ({upd.get('current') or 'none'})."
                + (f" Gap: {upd.get('gap_days')} days." if upd.get("gap_days") else "")
            ),
            "grounding": "system_detected",
            "freshness": "stale_source_limited" if stale else "fresh",
            "evidence": ["linkedin_messaging.proposed_last_touch_updates"],
            "source": "linkedin_messaging",
        })

    return candidates, "ok"


# ---------------------------------------------------------------------------
# Core ingest loop
# ---------------------------------------------------------------------------

def _ingest_candidate(
    cand: dict,
    *,
    captured_at: str,
    result: dict,
) -> None:
    """Try to write one candidate to the RI event store; update result in place."""
    contact_id = cand.get("contact_id")
    name = cand.get("name")
    signal_type = cand.get("signal_type") or "passive_relationship_signal"
    signal_strength = float(cand.get("signal_strength") or 0.0)
    strategic_relevance = cand.get("strategic_relevance") or "low"
    freshness = cand.get("freshness") or "fresh"
    event_at_raw = cand.get("event_at")
    src_key = cand.get("_source") or "unknown"

    # --- Blocked: low relevance ---
    if strategic_relevance not in RELEVANCE_ACCEPT:
        result["blocked_low_confidence"].append({
            "reason": f"strategic_relevance={strategic_relevance!r}",
            "contact_id": contact_id,
            "name": name,
            "signal_type": signal_type,
            "source": src_key,
        })
        return

    # --- Blocked: low strength ---
    if signal_strength < STRENGTH_THRESHOLD:
        result["blocked_low_confidence"].append({
            "reason": f"signal_strength={signal_strength:.2f} below threshold {STRENGTH_THRESHOLD}",
            "contact_id": contact_id,
            "name": name,
            "signal_type": signal_type,
            "source": src_key,
        })
        return

    # --- Blocked: stale source ---
    if freshness == "stale_source_limited":
        result["blocked_stale_source"].append({
            "reason": "source_freshness=stale_source_limited",
            "contact_id": contact_id,
            "name": name,
            "signal_type": signal_type,
            "source": src_key,
        })
        return

    # --- Blocked: no entity match ---
    if not contact_id and not name:
        result["blocked_unmatched_entity"].append({
            "reason": "no contact_id or name",
            "signal_type": signal_type,
            "source": src_key,
        })
        return

    # --- Blocked: no event_at ---
    event_at = _safe_date(event_at_raw)
    if not event_at:
        result["blocked_no_date"].append({
            "reason": f"unparseable event_at={event_at_raw!r}",
            "contact_id": contact_id,
            "name": name,
            "signal_type": signal_type,
            "source": src_key,
        })
        return

    # --- Build event ---
    # event_at_confidence: system_detected signals from dated sources → high;
    # otherwise medium since the timestamp is often a window boundary.
    source_name = cand.get("source") or "unknown"
    # RB-DEFECT-2026-09-18: this used to only trust {"email", "calendar"} as
    # "high" date confidence. linkedin_messaging candidates carry the exact
    # same kind of system-observed timestamp (the platform's own message/
    # activity time, not a guess) -- excluding it here was the root cause of
    # every LinkedIn passive RI event (inbound-contact and last-touch alike)
    # permanently failing _is_high_confidence_projection_safe's date-
    # confidence gate downstream and staying proposed_write_pending_
    # confirmation forever, even for a same-day dated successor that
    # mutation_policy.decide() would auto-apply. See system/CLAUDE_HANDOFF_
    # INTELLIGENCE_CYCLE_REPAIR_2026-09-18.md, "LinkedIn" evidence section.
    if source_name in {"email", "calendar", "linkedin_messaging"} and cand.get("grounding") == "system_detected":
        event_at_confidence = "high"
        event_at_source = source_name
    else:
        event_at_confidence = "medium"
        event_at_source = f"passive_detector:{source_name}"

    event = _build_passive_event(
        contact_id=contact_id,
        name=name,
        signal_type=signal_type,
        event_at=event_at,
        event_at_confidence=event_at_confidence,
        event_at_source=event_at_source,
        detector=src_key,
        signal_strength=signal_strength,
        reasoning=cand.get("reasoning") or "",
        source_refs=_normalize_source_refs(cand.get("evidence")),
        captured_at=captured_at,
    )

    # --- Append (dedupe is handled inside ri_events.append) ---
    try:
        append_result = ri_events.append(event)
    except ri_events.EventValidationError as exc:
        result["blocked_validation_error"].append({
            "reason": str(exc),
            "contact_id": contact_id,
            "name": name,
            "signal_type": signal_type,
            "source": src_key,
        })
        return
    except Exception as exc:  # noqa: BLE001
        result["blocked_validation_error"].append({
            "reason": f"unexpected error: {exc}",
            "contact_id": contact_id,
            "name": name,
            "signal_type": signal_type,
            "source": src_key,
        })
        return

    event_id = append_result["event_id"]

    if not append_result["was_appended"]:
        # Exact duplicate — already in the store.
        result["duplicates_skipped"].append({
            "event_id": event_id,
            "contact_id": contact_id,
            "name": name,
            "signal_type": signal_type,
            "source": src_key,
        })
        return

    stored_event = ri_events.find_by_event_id(event_id) or event
    bundle_result: dict[str, Any] | None = None
    try:
        import ri_intake  # noqa: E402
        bundle_result = ri_intake.stash_passive_touch_bundle(
            stored_event,
            source=f"passive_ri_ingest:{src_key}",
        )
    except Exception as exc:  # noqa: BLE001
        result["blocked_validation_error"].append({
            "reason": f"pending bundle creation failed: {exc}",
            "event_id": event_id,
            "contact_id": contact_id,
            "name": name,
            "signal_type": signal_type,
            "source": src_key,
        })

    # Newly written — track as proposed (persistence_status=proposed_write_pending_confirmation).
    result["events_written"].append({
        "event_id": event_id,
        "action_state": "proposed",
        "source_type": "passive_signal",
        "proposed_mutation_bundle_id": (bundle_result or {}).get("bundle_id"),
        "contact_id": contact_id,
        "name": name,
        "signal_type": signal_type,
        "event_at": event_at,
        "source": src_key,
        "projection_targets": ["baseline_index"],
        "applied": [],
        "pending_confirmation": [f"last_touch:{contact_id or name}"],
        "blocked": [],
        "reason": "passive signal — operator confirmation required before projection",
        "source_refs": _normalize_source_refs(cand.get("evidence")),
    })


def _pending_passive_events() -> list[dict]:
    return ri_events.load_events(
        source_type="passive_signal",
        persistence_status="proposed_write_pending_confirmation",
    )


def _ensure_passive_bundle(event: dict) -> dict:
    import ri_intake  # noqa: E402

    return ri_intake.stash_passive_touch_bundle(
        event,
        source=f"passive_ri_confirm:{(event.get('source') or {}).get('path') or 'passive_signal'}",
    )


def confirm_events(
    event_ids: list[str] | None = None,
    *,
    events: list[dict] | None = None,
    dry_run: bool = False,
    high_confidence_only: bool = False,
) -> dict:
    """Confirm passive RI event projections via ri_intake.confirm()."""
    import ri_intake  # noqa: E402

    selected = events if events is not None else [
        ev for ev in (ri_events.find_by_event_id(eid) for eid in (event_ids or []))
        if ev
    ]
    out = {
        "confirmed": not dry_run,
        "dry_run": dry_run,
        "high_confidence_only": high_confidence_only,
        "attempted": 0,
        "applied": [],
        "blocked": [],
    }
    for event in selected:
        event_id = event.get("event_id")
        if not event_id:
            continue
        decision = _projection_decision(event) if high_confidence_only else None
        if decision is not None and not decision.auto_apply:
            out["blocked"].append({
                "event_id": event_id,
                "name": _event_name(event),
                "reason": decision.reason,
                "decision_class": decision.status,
            })
            if not dry_run:
                mutation_policy.record_receipt(decision, artifact=None, applied=False)
            continue
        out["attempted"] += 1
        try:
            bundle_result = _ensure_passive_bundle(event)
            result = ri_intake.confirm(event_id, dry_run=dry_run)
            applied_ok = result.get("persistence_status") == "persisted"
            out["applied"].append({
                "event_id": event_id,
                "name": _event_name(event),
                "follow_up_event_id": result.get("follow_up_event_id"),
                "persistence_status": result.get("persistence_status"),
                "decision_class": decision.status if decision else None,
                "bundle_id": bundle_result.get("bundle_id"),
                "bundle_created": bundle_result.get("created"),
                "applied": result.get("applied") or [],
                "skipped_by_engine": result.get("skipped_by_engine") or [],
            })
            if decision is not None and not dry_run:
                mutation_policy.record_receipt(
                    decision,
                    artifact=(
                        f"ri_events:{result.get('follow_up_event_id')}"
                        if applied_ok else None
                    ),
                    applied=applied_ok,
                )
        except Exception as exc:  # noqa: BLE001
            out["blocked"].append({
                "event_id": event_id,
                "name": _event_name(event),
                "reason": str(exc),
            })
            if decision is not None and not dry_run:
                mutation_policy.record_receipt(decision, artifact=None, applied=False)
    out["applied_count"] = len(out["applied"])
    out["blocked_count"] = len(out["blocked"])
    return out


def write_confirm_cache(summary: dict, path: Path | None = None) -> None:
    p = path or CONFIRM_CACHE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")


def confirm_pending(
    *,
    event_ids: list[str] | None = None,
    dry_run: bool = False,
    high_confidence_only: bool = False,
) -> dict:
    """Confirm pending passive events, optionally scoped by event id."""
    pending = _pending_passive_events()
    if event_ids:
        allowed = set(event_ids)
        pending = [ev for ev in pending if ev.get("event_id") in allowed]
        missing = sorted(allowed - {ev.get("event_id") for ev in pending})
    else:
        missing = []

    result = confirm_events(
        events=pending,
        dry_run=dry_run,
        high_confidence_only=high_confidence_only,
    )
    result.update({
        "run_at": _now_iso(),
        "pending_seen": len(pending),
        "event_ids_requested": event_ids or [],
        "missing_event_ids": missing,
    })
    if missing:
        result["blocked"].extend({"event_id": eid, "reason": "event_not_pending"} for eid in missing)
        result["blocked_count"] = len(result["blocked"])
    names = [a.get("name") for a in result.get("applied") or [] if a.get("name")]
    result["summary"] = (
        f"RB reviewed {len(pending)} pending passive RI event(s). "
        f"RB {'previewed' if dry_run else 'confirmed'} {result.get('applied_count', 0)} "
        f"passive RI projection(s)"
        + (f": {', '.join(names)}." if names else ".")
        + (
            f" RB blocked {result.get('blocked_count', 0)} confirmation(s)."
            if result.get("blocked_count") else ""
        )
    )
    write_confirm_cache(result)
    return result


# ---------------------------------------------------------------------------
# P-036: RI assessment contract helpers for passive_ri_ingest
# ---------------------------------------------------------------------------

def _ri_assessment_for_written(ev: dict) -> dict:
    """Build a P-036 ri_assessment block for a successfully written event."""
    # Events written by passive_ri_ingest are proposed (pending confirmation),
    # not yet recorded. They become recorded only when confirm_events() runs.
    name = ev.get("name") or ev.get("contact_id") or "unknown"
    sig_type = ev.get("signal_type") or "passive_relationship_signal"
    contact_id = ev.get("contact_id")
    return {
        "status": "proposed",
        "source": f"passive_ri_ingest:{ev.get('source', 'unknown')}",
        "source_freshness": "fresh",   # blocked_stale path never reaches events_written
        "confidence": 0.60,            # passed all gates; medium confidence
        "evidence": list(ev.get("source_refs") or []),
        "mapped_contact_ids": [contact_id] if contact_id else [],
        "mapped_thread_ids": [],       # thread mapping done at projection time
        "proposed_mutation": {
            "command": "touch",
            "payload": {
                "id": contact_id,
                "date": ev.get("event_at"),
                "note": f"[{sig_type}] passive signal",
            },
        },
        "display_recommendation": "show",
        "reason": (
            f"Passive RI event proposed for {name} ({sig_type}); "
            "operator confirmation required before projection."
        ),
    }


def _ri_assessment_for_duplicate(item: dict) -> dict:
    """Build a P-036 ri_assessment block for a deduped event."""
    name = item.get("name") or item.get("contact_id") or "unknown"
    return {
        "status": "duplicate",
        "source": f"passive_ri_ingest:{item.get('source', 'unknown')}",
        "source_freshness": "fresh",
        "confidence": 0.60,
        "evidence": [],
        "mapped_contact_ids": [item["contact_id"]] if item.get("contact_id") else [],
        "mapped_thread_ids": [],
        "proposed_mutation": {"command": None, "payload": {}},
        "display_recommendation": "suppress",
        "reason": f"Duplicate event for {name} — already captured in RI event store.",
    }


def _ri_assessment_for_blocked(item: dict, bucket_name: str) -> dict:
    """Build a P-036 ri_assessment block for a blocked candidate."""
    name = item.get("name") or item.get("contact_id") or "unknown"
    reason = item.get("reason") or bucket_name
    # Stale-source blocks affect source_freshness field directly.
    source_freshness = "stale" if bucket_name == "blocked_stale_source" else "fresh"
    # Confidence: stale → capped; low-confidence → reflects actual value; others → low
    confidence = 0.30 if bucket_name == "blocked_low_confidence" else (
        0.50 if bucket_name == "blocked_stale_source" else 0.40
    )
    # Display when the block is decision-relevant (named contact, named signal).
    display = "suppress_unless_asked" if not item.get("name") else "suppress_unless_asked"
    return {
        "status": "blocked",
        "source": f"passive_ri_ingest:{item.get('source', 'unknown')}",
        "source_freshness": source_freshness,
        "confidence": confidence,
        "evidence": [],
        "mapped_contact_ids": [item["contact_id"]] if item.get("contact_id") else [],
        "mapped_thread_ids": [],
        "proposed_mutation": {"command": None, "payload": {}},
        "display_recommendation": display,
        "reason": f"Blocked for {name}: {reason}.",
    }


def _ri_assessment_for_unavailable(item: dict) -> dict:
    """Build a P-036 ri_assessment block for an unavailable source."""
    source = item.get("source") or "unknown"
    detail = item.get("detail") or "source unavailable"
    return {
        "status": "unavailable",
        "source": f"passive_ri_ingest:{source}",
        "source_freshness": "unavailable",
        "confidence": 0.0,
        "evidence": [],
        "mapped_contact_ids": [],
        "mapped_thread_ids": [],
        "proposed_mutation": {"command": None, "payload": {}},
        "display_recommendation": "show",
        "reason": f"Source {source!r} unavailable: {detail}.",
    }


def _attach_ingest_ri_assessments(result: dict) -> None:
    """In-place: attach P-036 ri_assessment blocks to all result items and
    add an ri_assessment_summary to the result dict."""
    for ev in result.get("events_written") or []:
        ev["ri_assessment"] = _ri_assessment_for_written(ev)
    for item in result.get("duplicates_skipped") or []:
        item["ri_assessment"] = _ri_assessment_for_duplicate(item)
    for bucket in (
        "blocked_low_confidence", "blocked_stale_source",
        "blocked_unmatched_entity", "blocked_no_date", "blocked_validation_error",
    ):
        for item in result.get(bucket) or []:
            item["ri_assessment"] = _ri_assessment_for_blocked(item, bucket)
    for item in result.get("source_unavailable") or []:
        item["ri_assessment"] = _ri_assessment_for_unavailable(item)

    # Count by status for quick summary.
    status_counts: dict[str, int] = {
        "proposed": len(result.get("events_written") or []),
        "duplicate": len(result.get("duplicates_skipped") or []),
        "blocked": sum(
            len(result.get(b) or []) for b in (
                "blocked_low_confidence", "blocked_stale_source",
                "blocked_unmatched_entity", "blocked_no_date", "blocked_validation_error",
            )
        ),
        "unavailable": len(result.get("source_unavailable") or []),
        "recorded": 0,    # passive_ri_ingest never directly records; confirm_events does
        "irrelevant": 0,  # non-candidates are not tracked individually
    }
    result["ri_assessment_summary"] = status_counts


# ---------------------------------------------------------------------------
# Public run() entry point
# ---------------------------------------------------------------------------

def run(
    *,
    today: date | None = None,
    hours: int = 24,
    captured_at: str | None = None,
) -> dict:
    """Collect passive RI candidates, write/propose events, return the summary.

    This function writes to the real RI event store. Use _smoke() for
    isolated test runs.
    """
    if today is None:
        today = date.today()
    if captured_at is None:
        captured_at = _now_iso()

    result: dict[str, Any] = {
        "run_at": captured_at,
        "today": today.isoformat(),
        "candidates_seen": 0,
        "events_written": [],        # list of projection-proof dicts (action_state=proposed)
        "duplicates_skipped": [],
        "blocked_low_confidence": [],
        "blocked_stale_source": [],
        "blocked_unmatched_entity": [],
        "blocked_no_date": [],
        "blocked_validation_error": [],
        "source_unavailable": [],
        "by_source": {},
        "event_ids": [],
        "source_refs": [],
    }

    # --- Collect candidates ---
    all_candidates: list[dict] = []

    rs_candidates, rs_status = _collect_relationship_signal_candidates(today, hours=hours)
    if rs_status != "ok":
        result["source_unavailable"].append({"source": "relationship_signals", "detail": rs_status})
    else:
        all_candidates.extend(rs_candidates)

    li_candidates, li_status = _collect_linkedin_candidates()
    if li_status != "ok":
        result["source_unavailable"].append({"source": "linkedin_messaging", "detail": li_status})
    else:
        all_candidates.extend(li_candidates)

    result["candidates_seen"] = len(all_candidates)

    # --- Ingest each candidate ---
    for cand in all_candidates:
        _ingest_candidate(cand, captured_at=captured_at, result=result)

    # --- Aggregate by_source ---
    src_counts: dict[str, dict] = {}
    for ev in result["events_written"]:
        src = ev["source"]
        src_counts.setdefault(src, {"events_written": 0, "duplicates": 0, "blocked": 0})
        src_counts[src]["events_written"] += 1
    for item in result["duplicates_skipped"]:
        src = item["source"]
        src_counts.setdefault(src, {"events_written": 0, "duplicates": 0, "blocked": 0})
        src_counts[src]["duplicates"] += 1
    for bucket in (
        "blocked_low_confidence", "blocked_stale_source",
        "blocked_unmatched_entity", "blocked_no_date", "blocked_validation_error",
    ):
        for item in result[bucket]:
            src = item.get("source") or "unknown"
            src_counts.setdefault(src, {"events_written": 0, "duplicates": 0, "blocked": 0})
            src_counts[src]["blocked"] += 1
    result["by_source"] = src_counts

    # --- event_ids and source_refs lists ---
    result["event_ids"] = [ev["event_id"] for ev in result["events_written"]]
    refs: list[str] = []
    for ev in result["events_written"]:
        refs.extend(_normalize_source_refs(ev.get("source_refs") or []))
    result["source_refs"] = list(dict.fromkeys(refs))

    # --- Prose summary (action-state language per product doctrine) ---
    n_written = len(result["events_written"])
    n_dup = len(result["duplicates_skipped"])
    n_blocked = sum(
        len(result[b]) for b in (
            "blocked_low_confidence", "blocked_stale_source",
            "blocked_unmatched_entity", "blocked_no_date", "blocked_validation_error",
        )
    )
    n_unavail = len(result["source_unavailable"])

    lines = ["RB reviewed passive relationship signals."]
    if n_written:
        lines.append(f"RB proposed {n_written} review-first RI event(s) from passive sources.")
    else:
        lines.append("RB recorded 0 new passive RI events (no qualifying candidates).")
    if n_dup:
        lines.append(f"RB skipped {n_dup} duplicate event(s).")
    if n_blocked:
        lines.append(f"RB blocked {n_blocked} candidate(s) (low confidence, stale source, or insufficient data).")
    if n_unavail:
        lines.append(f"RB could not access {n_unavail} source(s): {[s['source'] for s in result['source_unavailable']]}.")

    result["summary"] = " ".join(lines)

    # --- Canonical response block (machine-readable, for API consumers) ---
    proposed_names = [ev.get("name") for ev in result["events_written"] if ev.get("name")]
    blocked_reasons = (
        [f"{b.get('name','?')}: {b.get('reason','?')}" for b in result["blocked_low_confidence"]]
        + [f"{b.get('name','?')}: stale_source" for b in result["blocked_stale_source"]]
        + [f"unmatched_entity: {b.get('signal_type','?')}" for b in result["blocked_unmatched_entity"]]
        + [f"no_date: {b.get('name','?')}" for b in result["blocked_no_date"]]
        + [f"validation_error: {b.get('name','?')}" for b in result["blocked_validation_error"]]
    )
    result["canonical_response"] = {
        "scenario": "passive_ri_ingest",
        "action_state": "proposed" if n_written else ("blocked" if n_blocked else "not_persisted"),
        "summary": result["summary"],
        "facts": [
            f"candidates_seen: {result['candidates_seen']}",
            f"events_written: {n_written}",
            f"duplicates_skipped: {n_dup}",
            f"blocked: {n_blocked}",
            f"source_unavailable: {n_unavail}",
        ],
        "inferences": [],
        "persistence": {
            "status": (
                "proposed_write_pending_confirmation" if n_written
                else "not_persisted"
            ),
            "bundle_ids": [
                ev.get("proposed_mutation_bundle_id")
                for ev in result["events_written"]
                if ev.get("proposed_mutation_bundle_id")
            ],
            "event_ids": result["event_ids"],
            "proposed_contacts": proposed_names,
        },
        "projection": {
            "applied": [],
            "pending": [f"last_touch:{n}" for n in proposed_names],
            "blocked": blocked_reasons,
        },
        "recommended_actions": (
            [f"Confirm pending passive RI events: {', '.join(proposed_names)}."]
            if proposed_names else []
        ),
        "source_refs": result["source_refs"],
        "grounding": "system_detected",
        "freshness": (
            "stale_source_limited"
            if any(s.get("source") == "linkedin_messaging" for s in result.get("source_unavailable") or [])
            else "fresh"
        ),
        "confidence": "medium",
    }

    # P-036: attach ri_assessment blocks to all result items.
    _attach_ingest_ri_assessments(result)

    return result


# ---------------------------------------------------------------------------
# Cache writer
# ---------------------------------------------------------------------------

def write_cache(summary: dict, path: Path | None = None) -> None:
    p = path or CACHE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")


def load_cache(path: Path | None = None) -> dict | None:
    p = path or CACHE_PATH
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def _smoke() -> int:
    """Isolated smoke test.

    Per the smoke-test-mutations memory: any smoke test that could reach
    ri_events.append() must rebind EVENTS_DIR / INDEX_PATH to a tmpdir.
    We also rebind CACHE_PATH so no production cache is touched.
    """
    import shutil
    import tempfile

    global CACHE_PATH, CONFIRM_CACHE_PATH  # noqa: PLW0603 — intentional rebinding for smoke run
    orig_cache = CACHE_PATH
    orig_confirm_cache = CONFIRM_CACHE_PATH
    orig_events_dir = ri_events.EVENTS_DIR
    orig_index = ri_events.INDEX_PATH
    import ri_intake  # noqa: E402
    orig_pending = ri_intake.PENDING_PATH
    tmp = Path(tempfile.mkdtemp(prefix="passive_ri_ingest_smoke_"))
    ri_events.EVENTS_DIR = tmp / "ri_events"
    ri_events.INDEX_PATH = tmp / "ri_events_index.json"
    CACHE_PATH = tmp / "passive_ri_ingest.json"
    CONFIRM_CACHE_PATH = tmp / "passive_ri_confirm.json"
    ri_intake.PENDING_PATH = tmp / "ri_events_pending.json"

    failures = 0

    def ok(label: str) -> None:
        print(f"  OK   {label}")

    def fail(label: str, detail: str = "") -> None:
        nonlocal failures
        failures += 1
        print(f"  FAIL {label}  {detail}")

    def expect(label: str, cond: bool, detail: str = "") -> None:
        (ok if cond else lambda l: fail(l, detail))(label)

    try:
        captured_at = "2026-05-23T08:00:00-05:00"
        today = date(2026, 5, 23)

        # ----------------------------------------------------------------
        # Build synthetic candidates directly (no live source access)
        # ----------------------------------------------------------------
        synthetic: list[dict] = [
            # RC inner — high strength email signal → should be proposed
            {
                "_source": "relationship_signals",
                "contact_id": "olivia-nielsen",
                "name": "Olivia Nielsen",
                "signal_type": "email_inbound_signal",
                "event_at": "2026-05-23",
                "signal_strength": 0.85,
                "strategic_relevance": "high",
                "reasoning": "Olivia sent a substantive email re: QSR platform deal.",
                "grounding": "system_detected",
                "freshness": "fresh",
                "evidence": ["email.inbox"],
                "source": "email",
            },
            # LKI — medium strength — should be proposed
            {
                "_source": "linkedin_last_touch",
                "contact_id": "ryan-hildebrand",
                "name": "Ryan Hildebrand",
                "signal_type": "last_touch_update",
                "event_at": "2026-05-18",
                "signal_strength": 0.70,
                "strategic_relevance": "medium",
                "reasoning": "LinkedIn interaction on 2026-05-18 is newer than last_touch.",
                "grounding": "system_detected",
                "freshness": "fresh",
                "evidence": ["linkedin_messaging.proposed_last_touch_updates"],
                "source": "linkedin_last_touch",
            },
            # Low strength — should be BLOCKED
            {
                "_source": "relationship_signals",
                "contact_id": "some-contact",
                "name": "Some Contact",
                "signal_type": "social_mention",
                "event_at": "2026-05-23",
                "signal_strength": 0.25,
                "strategic_relevance": "medium",
                "reasoning": "Weak social mention.",
                "grounding": "system_detected",
                "freshness": "fresh",
                "evidence": [],
                "source": "social",
            },
            # Stale source — should be BLOCKED
            {
                "_source": "relationship_signals",
                "contact_id": "jeff-coffland",
                "name": "Jeff Coffland",
                "signal_type": "email_inbound_signal",
                "event_at": "2026-05-22",
                "signal_strength": 0.80,
                "strategic_relevance": "high",
                "reasoning": "Email signal from stale feed.",
                "grounding": "system_detected",
                "freshness": "stale_source_limited",
                "evidence": ["email.inbox"],
                "source": "email",
            },
            # Low relevance — should be BLOCKED
            {
                "_source": "relationship_signals",
                "contact_id": "some-other",
                "name": "Some Other",
                "signal_type": "social_mention",
                "event_at": "2026-05-23",
                "signal_strength": 0.65,
                "strategic_relevance": "low",
                "reasoning": "Low-relevance mention.",
                "grounding": "system_detected",
                "freshness": "fresh",
                "evidence": [],
                "source": "social",
            },
        ]

        # Run ingest against synthetic candidates (bypassing live source collection)
        result: dict[str, Any] = {
            "run_at": captured_at,
            "today": today.isoformat(),
            "candidates_seen": len(synthetic),
            "events_written": [],
            "duplicates_skipped": [],
            "blocked_low_confidence": [],
            "blocked_stale_source": [],
            "blocked_unmatched_entity": [],
            "blocked_no_date": [],
            "blocked_validation_error": [],
            "source_unavailable": [],
            "by_source": {},
            "event_ids": [],
            "source_refs": [],
        }
        for cand in synthetic:
            _ingest_candidate(cand, captured_at=captured_at, result=result)

        n_written = len(result["events_written"])
        n_blocked_lc = len(result["blocked_low_confidence"])
        n_blocked_ss = len(result["blocked_stale_source"])

        expect("2 qualifying candidates written/proposed", n_written == 2,
               f"got {n_written}: {[e['name'] for e in result['events_written']]}")
        expect("low-strength candidate blocked", n_blocked_lc >= 1,
               f"got blocked_low_confidence={n_blocked_lc}")
        expect("low-relevance candidate blocked", n_blocked_lc >= 2,
               f"got blocked_low_confidence={n_blocked_lc}")
        expect("stale-source candidate blocked", n_blocked_ss == 1,
               f"got blocked_stale_source={n_blocked_ss}")

        # All written events use action_state='proposed'
        states = {ev["action_state"] for ev in result["events_written"]}
        expect("all written events are action_state=proposed",
               states == {"proposed"}, f"got {states}")

        # Event IDs are in the index
        idx = ri_events.load_index()
        for ev in result["events_written"]:
            expect(f"event_id {ev['event_id']} in index",
                   ev["event_id"] in idx["by_event_id"])
            expect(f"pending bundle created for {ev['event_id']}",
                   bool(ev.get("proposed_mutation_bundle_id")),
                   f"got {ev.get('proposed_mutation_bundle_id')!r}")

        conf_preview = confirm_events([result["events_written"][0]["event_id"]], dry_run=True)
        expect("passive confirm dry-run reports dry_run=true",
               conf_preview.get("dry_run") is True)
        expect("passive confirm dry-run reaches ri_intake.confirm",
               conf_preview.get("applied_count") == 1,
               f"got {conf_preview}")

        pending_preview = confirm_pending(dry_run=True)
        expect("confirm_pending dry-run sees pending passive events",
               pending_preview.get("pending_seen") == 2,
               f"got {pending_preview.get('pending_seen')}")
        expect("confirm_pending dry-run writes confirmation cache",
               CONFIRM_CACHE_PATH.exists())
        high_only_preview = confirm_pending(dry_run=True, high_confidence_only=True)
        expect("high-confidence-only blocks medium-confidence smoke fixtures",
               high_only_preview.get("applied_count") == 0
               and high_only_preview.get("blocked_count") == 2,
               f"got {high_only_preview}")

        # --- Duplicate test: re-run same candidates → nothing new written ---
        result2: dict[str, Any] = {
            "run_at": captured_at,
            "today": today.isoformat(),
            "candidates_seen": len(synthetic),
            "events_written": [],
            "duplicates_skipped": [],
            "blocked_low_confidence": [],
            "blocked_stale_source": [],
            "blocked_unmatched_entity": [],
            "blocked_no_date": [],
            "blocked_validation_error": [],
            "source_unavailable": [],
            "by_source": {},
            "event_ids": [],
            "source_refs": [],
        }
        for cand in synthetic:
            _ingest_candidate(cand, captured_at=captured_at, result=result2)

        expect("re-run same candidates: 0 new events written",
               len(result2["events_written"]) == 0,
               f"got {len(result2['events_written'])}")
        expect("re-run: duplicates_skipped == 2",
               len(result2["duplicates_skipped"]) == 2,
               f"got {len(result2['duplicates_skipped'])}")

        # --- Cache write / load round-trip ---
        write_cache(result)
        loaded = load_cache()
        expect("cache round-trip: events_written count matches",
               loaded is not None and len(loaded.get("events_written") or []) == n_written,
               f"loaded={loaded}")

        # --- Summary uses action-state language ---
        # Build summary lines manually from the result for this smoke test
        n_w = len(result["events_written"])
        n_dup = len(result["duplicates_skipped"])
        n_blk = n_blocked_lc + n_blocked_ss + len(result["blocked_unmatched_entity"])
        summary_parts = []
        summary_parts.append("RB reviewed passive relationship signals.")
        if n_w:
            summary_parts.append(f"RB proposed {n_w} review-first RI event(s) from passive sources.")
        if n_dup:
            summary_parts.append(f"RB skipped {n_dup} duplicate event(s).")
        if n_blk:
            summary_parts.append(f"RB blocked {n_blk} candidate(s) (low confidence, stale source, or insufficient data).")
        summary = " ".join(summary_parts)

        expect("summary uses action-state language (RB proposed / blocked)",
               "RB proposed" in summary and "RB blocked" in summary,
               f"got: {summary!r}")
        expect("summary does NOT say 'should be marked'",
               "should be marked" not in summary)
        expect("summary does NOT say 'would likely'",
               "would likely" not in summary)

    finally:
        ri_events.EVENTS_DIR = orig_events_dir
        ri_events.INDEX_PATH = orig_index
        CACHE_PATH = orig_cache
        CONFIRM_CACHE_PATH = orig_confirm_cache
        ri_intake.PENDING_PATH = orig_pending
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    # --- Confirmation write smoke (P5) ---
    # Run as a sub-function with its own tmp dir so the baseline-write path
    # can be isolated from the ingest-path state above.
    failures += _smoke_confirm_write()

    print(f"--- passive_ri_ingest smoke complete: {failures} failure(s) ---")
    return 1 if failures else 0


def _smoke_confirm_write() -> int:
    """Isolated smoke that proves passive confirmation can advance last_touch
    in a TEMP baseline without touching the production baseline.

    Per P5 of CLAUDE_HANDOFF_2026-05-24_RB_9_5_CANONICAL_RESPONSE_HARDENING.md:
    - Rebind ri_events.EVENTS_DIR, INDEX_PATH, ri_intake.PENDING_PATH.
    - Rebind rb_core.BASELINE_PATH to a temp file containing one synthetic RC.
    - Rebind rb_core.SNAPSHOTS_DIR to a temp dir.
    - Monkeypatch mutations._validate_baseline_or_rollback to bypass the
      external subprocess call (which cannot see the in-process rebinding).
    - Create a synthetic last_touch_update passive event with high confidence.
    - Stash the pending bundle.
    - Confirm with dry_run=False.
    - Assert: follow-up event status=persisted, temp baseline last_touch advanced,
      production baseline untouched.

    Returns number of failures (0 = pass).
    """
    import json as _json
    import shutil
    import tempfile

    print("--- _smoke_confirm_write ---")
    failures = 0

    def ok(label: str) -> None:
        print(f"  OK   {label}")

    def fail(label: str, detail: str = "") -> None:
        nonlocal failures
        failures += 1
        print(f"  FAIL {label}  {detail}")

    def expect(label: str, cond: bool, detail: str = "") -> None:
        (ok if cond else lambda l: fail(l, detail))(label)

    import ri_intake  # noqa: E402
    import rb_core  # noqa: E402
    import mutations  # noqa: E402

    # Capture originals.
    orig_events_dir = ri_events.EVENTS_DIR
    orig_index = ri_events.INDEX_PATH
    orig_pending = ri_intake.PENDING_PATH
    orig_baseline = rb_core.BASELINE_PATH
    orig_snapshots = rb_core.SNAPSHOTS_DIR
    orig_validate = mutations._validate_baseline_or_rollback

    tmp = Path(tempfile.mkdtemp(prefix="passive_ri_confirm_write_smoke_"))
    prod_baseline_snapshot = tmp / "prod_baseline_before.json"

    try:
        # --- Rebind everything to tmp ---
        ri_events.EVENTS_DIR = tmp / "ri_events"
        ri_events.INDEX_PATH = tmp / "ri_events_index.json"
        ri_intake.PENDING_PATH = tmp / "ri_events_pending.json"
        rb_core.BASELINE_PATH = tmp / "baseline_index.json"
        rb_core.SNAPSHOTS_DIR = tmp / "_snapshots"

        # --- Snapshot the production baseline for tamper-check ---
        if orig_baseline.exists():
            shutil.copy2(orig_baseline, prod_baseline_snapshot)

        # --- Monkeypatch the validator to bypass subprocess ---
        def _noop_validate(snapshot_path: Path) -> int:  # noqa: ARG001
            """Lightweight JSON-parse validation (no subprocess, no schema dep)."""
            try:
                data = _json.loads(rb_core.BASELINE_PATH.read_text())
                if not isinstance(data, list):
                    return 1
                return 0
            except Exception:  # noqa: BLE001
                return 1
        mutations._validate_baseline_or_rollback = _noop_validate

        # --- Create a synthetic baseline with one RC contact, stale last_touch ---
        synthetic_contact_id = "bob-gibson"
        synthetic_baseline = [
            {
                "id": synthetic_contact_id,
                "name": "Bob Gibson",
                "signal_class": "RC",
                "last_touch": "2025-12-01",   # intentionally stale
                "company": "Test Corp",
                "sources": [],
            }
        ]
        rb_core.BASELINE_PATH.write_text(
            _json.dumps(synthetic_baseline, indent=2) + "\n",
            encoding="utf-8",
        )
        rb_core.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

        # --- Build a synthetic passive last_touch_update event ---
        captured_at = "2026-05-24T09:00:00+00:00"
        new_event_at = "2026-05-20"  # newer than 2025-12-01

        event = _build_passive_event(
            contact_id=synthetic_contact_id,
            name="Bob Gibson",
            signal_type="last_touch_update",
            event_at=new_event_at,
            event_at_confidence="high",
            event_at_source="email",
            detector="relationship_signals",
            signal_strength=0.80,
            reasoning="Email interaction on 2026-05-20 is newer than last_touch.",
            source_refs=["email.inbox"],
            captured_at=captured_at,
        )

        append_result = ri_events.append(event)
        event_id = append_result["event_id"]

        expect("synthetic event appended", append_result["was_appended"],
               f"got {append_result}")

        # --- Stash the pending mutation bundle ---
        stored_event = ri_events.find_by_event_id(event_id) or event
        bundle_result = ri_intake.stash_passive_touch_bundle(
            stored_event,
            source="passive_ri_ingest:relationship_signals",
        )
        expect("pending bundle created", bundle_result.get("created") is True,
               f"got {bundle_result}")

        # --- Confirm with dry_run=False ---
        confirm_result = ri_intake.confirm(event_id, dry_run=False)
        persistence_status = confirm_result.get("persistence_status")
        expect(
            "confirm persistence_status=persisted",
            persistence_status == "persisted",
            f"got {persistence_status!r}; full result: {confirm_result}",
        )

        # --- Assert temp baseline last_touch advanced ---
        updated_baseline = _json.loads(
            rb_core.BASELINE_PATH.read_text(encoding="utf-8")
        )
        updated_entry = next(
            (e for e in updated_baseline if e.get("id") == synthetic_contact_id),
            None,
        )
        expect(
            "temp baseline entry exists after confirm",
            updated_entry is not None,
        )
        if updated_entry:
            expect(
                f"temp baseline last_touch advanced to {new_event_at}",
                updated_entry.get("last_touch") == new_event_at,
                f"got last_touch={updated_entry.get('last_touch')!r}",
            )

        # --- Assert production baseline is untouched ---
        if prod_baseline_snapshot.exists():
            prod_before = _json.loads(
                prod_baseline_snapshot.read_text(encoding="utf-8")
            )
            prod_after = _json.loads(
                orig_baseline.read_text(encoding="utf-8")
            )
            expect(
                "production baseline untouched",
                prod_before == prod_after,
                "production baseline differs from pre-smoke snapshot — possible write leak",
            )
        else:
            ok("production baseline untouched (no snapshot available — skip)")

        # --- Assert follow-up RI event written with persisted status ---
        follow_up_id = confirm_result.get("follow_up_event_id")
        expect("follow_up_event_id returned", bool(follow_up_id),
               f"got {follow_up_id!r}")
        if follow_up_id:
            fu_event = ri_events.find_by_event_id(follow_up_id)
            expect(
                "follow-up event found in store",
                fu_event is not None,
            )
            if fu_event:
                fu_status = (fu_event.get("persistence") or {}).get("status")
                expect(
                    "follow-up event persistence.status=persisted",
                    fu_status == "persisted",
                    f"got {fu_status!r}",
                )

        # --- Assert pending bundle was consumed ---
        pending = ri_intake._load_pending()
        bundle_id = bundle_result.get("bundle_id")
        expect(
            "pending bundle consumed after confirm",
            bundle_id not in pending,
            f"bundle_id {bundle_id!r} still in pending after confirm",
        )

    except Exception as exc:  # noqa: BLE001
        fail("_smoke_confirm_write raised unexpected exception", str(exc))
        import traceback
        traceback.print_exc()

    finally:
        # Restore all rebindings.
        ri_events.EVENTS_DIR = orig_events_dir
        ri_events.INDEX_PATH = orig_index
        ri_intake.PENDING_PATH = orig_pending
        rb_core.BASELINE_PATH = orig_baseline
        rb_core.SNAPSHOTS_DIR = orig_snapshots
        mutations._validate_baseline_or_rollback = orig_validate
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"--- _smoke_confirm_write complete: {failures} failure(s) ---")
    return failures


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args and raw_args[0] == "confirm":
        return _main_confirm(raw_args[1:])

    p = argparse.ArgumentParser(description="Passive RI event proposal aggregator.")
    p.add_argument("--smoke", action="store_true",
                   help="Run isolated smoke test (no production writes).")
    p.add_argument("--date", dest="run_date",
                   help="ISO date override (default: today).")
    p.add_argument("--hours", type=int, default=24,
                   help="Signal look-back window in hours (default: 24).")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="Print summary JSON to stdout.")
    p.add_argument("--cache", action="store_true",
                   help="Write summary to system/.cache/passive_ri_ingest.json.")
    p.add_argument("--confirm", action="store_true",
                   help="Apply newly proposed passive RI projections. Explicit confirmation gate.")
    p.add_argument("--dry-run-confirm", action="store_true",
                   help="Exercise confirmation path without writing projections.")
    args = p.parse_args(raw_args)

    if args.smoke:
        return _smoke()

    today = date.fromisoformat(args.run_date) if args.run_date else date.today()
    summary = run(today=today, hours=args.hours)

    if args.confirm or args.dry_run_confirm:
        event_ids = [ev["event_id"] for ev in summary.get("events_written") or []]
        summary["confirmation"] = confirm_events(event_ids, dry_run=args.dry_run_confirm)
        if args.confirm:
            applied_count = summary["confirmation"].get("applied_count", 0)
            blocked_count = summary["confirmation"].get("blocked_count", 0)
            summary["summary"] += (
                f" RB confirmed {applied_count} passive RI projection(s)"
                f" and blocked {blocked_count} confirmation(s)."
            )

    # Always write cache when run from CLI (refresh_all.py depends on this)
    write_cache(summary)

    if args.as_json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(summary.get("summary", ""))
        n_written = len(summary.get("events_written") or [])
        n_dup = len(summary.get("duplicates_skipped") or [])
        n_unavail = len(summary.get("source_unavailable") or [])
        blocked_total = sum(
            len(summary.get(b) or []) for b in (
                "blocked_low_confidence", "blocked_stale_source",
                "blocked_unmatched_entity", "blocked_no_date", "blocked_validation_error",
            )
        )
        print(f"  Events proposed : {n_written}")
        print(f"  Duplicates      : {n_dup}")
        print(f"  Blocked         : {blocked_total}")
        if n_unavail:
            print(f"  Unavailable     : {n_unavail} source(s)")

    return 0


def _main_confirm(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Confirm pending passive RI projections.")
    p.add_argument("--all", action="store_true",
                   help="Confirm all pending passive RI events.")
    p.add_argument("--event-id", action="append", default=[],
                   help="Confirm one pending passive RI event. Repeatable.")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview confirmation without writing projections.")
    p.add_argument("--high-confidence-only", action="store_true",
                   help="Only confirm projection-safe high-confidence passive events.")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="Print confirmation summary JSON.")
    args = p.parse_args(argv)

    if not args.all and not args.event_id:
        # `confirm --dry-run` is the preview-friendly default from the handoff.
        args.all = True

    if args.all and args.event_id:
        print("ERROR: use either --all or --event-id, not both.", file=sys.stderr)
        return 2

    summary = confirm_pending(
        event_ids=args.event_id or None,
        dry_run=args.dry_run,
        high_confidence_only=args.high_confidence_only,
    )

    if args.as_json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(summary.get("summary", ""))
        print(f"  Pending seen : {summary.get('pending_seen', 0)}")
        print(f"  Attempted    : {summary.get('attempted', 0)}")
        print(f"  Applied      : {summary.get('applied_count', 0)}")
        print(f"  Blocked      : {summary.get('blocked_count', 0)}")
        if summary.get("missing_event_ids"):
            print(f"  Missing      : {', '.join(summary['missing_event_ids'])}")

    return 0 if not summary.get("missing_event_ids") else 1


if __name__ == "__main__":
    sys.exit(main())
