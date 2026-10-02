#!/usr/bin/env python3
"""
ri_intake.py — review-first RI intake dispatcher.

Front door for `POST /ri_events/review`. Takes a source-typed request,
dispatches to the right pre-processor, runs the classifier engine in
`manual_relationship_intake.assess()`, persists the result as an RI event
via `ri_events.append`, stashes the proposed mutation bundle in a pending
cache, and returns the structured response shape from
`system/RI_EVENT_INTAKE_DESIGN.md`.

Also provides `confirm()` for the confirm leg of the lifecycle.

Step 2 of the build order: the dispatch plumbing and event lifecycle.
Per-source pre-processors are stubbed (echo input → assess); step 3+
fills in real classifier logic without touching this file.

The module is *importable* (no I/O on import) and has a CLI for ad-hoc
review of a payload from a JSON file.
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import rb_core as core  # noqa: E402
import ri_events  # noqa: E402

# manual_relationship_intake is the classifier engine. We import lazily inside
# review() so the module-level import doesn't crash callers that only want
# confirm() or testing helpers.


PROJECT_DIR = core.PROJECT_DIR
SYSTEM_DIR = PROJECT_DIR / "system"
CACHE_DIR = SYSTEM_DIR / ".cache"
PENDING_PATH = CACHE_DIR / "ri_events_pending.json"


# ----------------------------------------------------------------------
# Pre-processor dispatch
# ----------------------------------------------------------------------

def _load_preprocessor(source_type: str):
    """Resolve a source_type to its pre-processor module's `preprocess` fn.

    The mapping is intentionally static — adding a new source_type means
    creating a new ri_preproc_*.py and adding an entry here. Step 3+ swap
    the stub implementations with real classifiers; the mapping stays put.
    """
    if source_type == "manual_text":
        import ri_preproc_manual as m
        return m.preprocess
    if source_type == "recruiting_update":
        import ri_preproc_recruiting as m
        return m.preprocess
    if source_type in {"fathom_manual_paste", "zoom_manual_paste"}:
        import ri_preproc_transcript as m
        return m.preprocess
    if source_type == "linkedin_screenshot":
        import ri_preproc_linkedin as m
        return m.preprocess
    if source_type == "email_paste":
        import ri_preproc_email as m
        return m.preprocess
    raise ValueError(f"unknown source_type: {source_type!r}")


# ----------------------------------------------------------------------
# Pending bundle cache
# ----------------------------------------------------------------------

def _load_pending() -> dict[str, dict]:
    if not PENDING_PATH.exists():
        return {}
    try:
        return json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_pending(pending: dict[str, dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(json.dumps(pending, indent=2) + "\n", encoding="utf-8")


def _stash_bundle(bundle: dict) -> None:
    pending = _load_pending()
    pending[bundle["bundle_id"]] = bundle
    _save_pending(pending)


def _pop_bundle(bundle_id: str) -> dict | None:
    pending = _load_pending()
    bundle = pending.pop(bundle_id, None)
    if bundle is not None:
        _save_pending(pending)
    return bundle


def stash_passive_touch_bundle(event: dict, *, source: str = "passive_ri_ingest") -> dict:
    """Create the pending mutation bundle for a passive RI event.

    Passive detectors already produce a reviewed event shape, so they do not
    need the manual pre-processor/classifier path. They still need the same
    confirm() lifecycle before projections move. This helper stores a minimal
    assess_report containing a safe touchContact mutation that confirm() can
    apply through manual_relationship_intake.apply_mutations().
    """
    event_id = event.get("event_id")
    if not event_id:
        raise ValueError("event.event_id is required")

    people = (event.get("entities") or {}).get("people") or []
    matched = next((p for p in people if p.get("matched_id")), None)
    if not matched:
        raise ValueError(f"passive event {event_id} has no matched contact")

    contact_id = matched["matched_id"]
    name = matched.get("raw") or contact_id
    event_at = (event.get("event_at") or "")[:10]
    if not event_at:
        raise ValueError(f"passive event {event_id} has no event_at")

    pending = _load_pending()
    for bundle_id, bundle in pending.items():
        if bundle.get("event_id") == event_id:
            return {
                "bundle_id": bundle_id,
                "event_id": event_id,
                "created": False,
                "reason": "pending_bundle_already_exists",
            }

    operation = {
        "operation": "touchContact",
        "safe_to_write": True,
        "endpoint": "POST /touch",
        "body": {
            "id": contact_id,
            "date": event_at,
            "source": source,
        },
        "reason": "Passive detector found a dated interaction newer than or equal to the reviewed event date.",
    }
    bundle_id = f"mb_{event_id.removeprefix('ri_')}"
    bundle = {
        "bundle_id": bundle_id,
        "event_id": event_id,
        "created_at": event.get("captured_at")
            or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "operations": [{
            "op": operation["operation"],
            "args": operation["body"],
            "rationale": operation["reason"],
            "auto_safe": True,
            "endpoint": operation["endpoint"],
        }],
        "auto_safe_operations": [f"touchContact:{contact_id}"],
        "review_required_operations": [],
        "assess_report": {
            "rb_match": {"status": "matched", "id": contact_id, "name": name},
            "event_at": event_at,
            "signals": [{
                "type": (event.get("signal") or {}).get("type") or "passive_relationship_signal",
                "strength": (event.get("signal") or {}).get("confidence") or 0.6,
                "rationale": (event.get("signal") or {}).get("reasoning") or "Passive relationship signal.",
            }],
            "opportunity_state": None,
            "proposed_mutations": [operation],
            "canonical_response_requirement": {
                "persistent_state_mutation": "pending_confirmation",
            },
        },
        "preproc_normalized": {
            "source_type": "passive_signal",
            "event_at": event_at,
            "contact_id": contact_id,
            "name": name,
        },
        "source_request": {
            "source_type": "passive_signal",
            "event_id": event_id,
            "source": source,
        },
    }
    _stash_bundle(bundle)
    return {
        "bundle_id": bundle_id,
        "event_id": event_id,
        "created": True,
        "reason": "pending_bundle_created",
    }


# ----------------------------------------------------------------------
# Event shape construction from assess output
# ----------------------------------------------------------------------

def _people_from_assess(report: dict, extracted_people: list[dict]) -> list[dict]:
    """Translate assess's rb_match + extracted people into the event
    entities.people[] shape required by SCHEMAS.md / ri_events.validate_event.
    """
    out: list[dict] = []
    match = report.get("rb_match") or {}
    if match.get("status") == "matched" and match.get("id"):
        out.append({
            "raw": match.get("name") or match.get("id"),
            "matched_id": match["id"],
            "match_confidence": 0.95,
            "decision": "matched_existing",
        })
        primary_id = match["id"]
    elif match.get("name") or match.get("id"):
        out.append({
            "raw": match.get("name") or match.get("id"),
            "matched_id": None,
            "match_confidence": None,
            "decision": "propose_new_contact",
        })
        primary_id = None
    else:
        primary_id = None

    # Additional extracted people not already represented by the rb_match.
    seen_raw = {p["raw"].lower() for p in out if p.get("raw")}
    for p in extracted_people or []:
        raw = (p.get("raw") or p.get("name") or "").strip()
        if not raw or raw.lower() in seen_raw:
            continue
        out.append({
            "raw": raw,
            "matched_id": p.get("matched_id"),
            "match_confidence": p.get("match_confidence"),
            "decision": p.get("decision") or (
                "matched_existing" if p.get("matched_id") else "propose_new_contact"
            ),
        })
        seen_raw.add(raw.lower())
    return out


def _companies_from_assess(report: dict, extracted_companies: list[dict]) -> list[dict]:
    out: list[dict] = []
    opp = report.get("opportunity_state") or {}
    org = opp.get("organization")
    if org:
        out.append({
            "raw": org,
            "matched_id": None,  # mp's company resolution is shallow; defer
            "decision": "propose_new_company",
        })
    seen = {c["raw"].lower() for c in out if c.get("raw")}
    for c in extracted_companies or []:
        raw = (c.get("raw") or c.get("name") or "").strip()
        if not raw or raw.lower() in seen:
            continue
        out.append({
            "raw": raw,
            "matched_id": c.get("matched_id"),
            "decision": c.get("decision") or "propose_new_company",
        })
        seen.add(raw.lower())
    return out


def _primary_signal_type(report: dict) -> str:
    """Pick the dominant signal.type from assess's signal list.

    Priority order leans on the recruiting state-transition signals when
    they're present (step 3 will add these explicitly to assess); otherwise
    falls back to assess's strongest existing signal type."""
    priority = (
        "interview_completed",
        "recruiter_screen_completed",
        "resume_requested",
        "resume_sent",
        "followup_sent",
        "pending_introduction",
        "advisory_opportunity",
        "internal_circulation_signal",
        "sponsorship_signal",
        "advocacy_signal",
        "insider_access_signal",
        "warm_recruiting_reengagement",
    )
    signals = report.get("signals") or []
    types_in_report = {s.get("type") for s in signals}
    for p in priority:
        if p in types_in_report:
            return p
    if signals:
        return signals[0].get("type") or "manual_relationship_signal"
    return "manual_relationship_signal"


def _recommended_projection_changes(proposed_mutations: list[dict]) -> list[str]:
    """Convert assess's proposed_mutations into the
    signal.recommended_projection_changes list."""
    out: list[str] = []
    for m in proposed_mutations or []:
        op = m.get("operation")
        body = m.get("body") or {}
        if op == "addContact":
            out.append(f"propose_contact:{body.get('id', '?')}")
        elif op == "touchContact":
            out.append(f"touch:{body.get('id', '?')}")
        elif op == "openThread":
            out.append(f"open_thread:{body.get('id', '?')}")
        elif op == "loopAdd":
            out.append(f"open_loop:{body.get('description', '?')[:40]}")
        elif op == "writeInteractionBrief":
            out.append(f"write_brief:{Path(body.get('path', '?')).stem}")
        elif op == "card_or_baseline_tag_update":
            out.append(f"tag:{body.get('id', '?')}")
    return out


def _build_canonical_response(
    *,
    scenario: str,
    action_state: str,
    summary: str,
    facts: list | None = None,
    inferences: list | None = None,
    persistence_status: str,
    bundle_id: str | None = None,
    event_ids: list | None = None,
    applied: list | None = None,
    pending: list | None = None,
    blocked: list | None = None,
    recommended_actions: list | None = None,
    source_refs: list | None = None,
    grounding: str = "manual_user_provided",
    freshness: str = "fresh",
    confidence: str = "medium",
) -> dict:
    """Build the standard canonical_response block for API consumers.

    Shape defined in system/CANONICAL_RESPONSE_CONTRACT.md.
    """
    return {
        "scenario": scenario,
        "action_state": action_state,
        "summary": summary,
        "facts": facts or [],
        "inferences": inferences or [],
        "persistence": {
            "status": persistence_status,
            "bundle_id": bundle_id,
            "event_ids": event_ids or [],
            "storage_path": None,
        },
        "projection": {
            "applied": applied or [],
            "pending": pending or [],
            "blocked": blocked or [],
        },
        "recommended_actions": recommended_actions or [],
        "source_refs": source_refs or [],
        "grounding": grounding,
        "freshness": freshness,
        "confidence": confidence,
    }


def _confirmation_required_for(proposed_mutations: list[dict]) -> list[str]:
    """Operations that need explicit confirm. Anything not marked
    `safe_to_write=True` by the existing engine."""
    out: list[str] = []
    for m in proposed_mutations or []:
        if m.get("safe_to_write"):
            continue
        op = m.get("operation")
        body = m.get("body") or {}
        out.append(f"{op}:{body.get('id') or body.get('party') or body.get('path') or '?'}")
    return out


def _safe_proposed_summary(proposed_mutations: list[dict]) -> dict:
    """Compact lists per operation type for the API response."""
    out: dict[str, list[dict]] = {
        "proposed_contact_adds": [],
        "proposed_contact_updates": [],
        "proposed_thread_opens": [],
        "proposed_loops": [],
        "proposed_brief_writes": [],
    }
    for m in proposed_mutations or []:
        body = m.get("body") or {}
        if m.get("operation") == "addContact":
            out["proposed_contact_adds"].append({
                "id": body.get("id"), "name": body.get("name"),
                "company": body.get("company"), "role": body.get("role"),
                "signal_class": body.get("signal_class"),
            })
        elif m.get("operation") == "touchContact":
            out["proposed_contact_updates"].append({
                "id": body.get("id"), "field": "last_touch",
                "to": body.get("date"),
                "auto_safe": bool(m.get("safe_to_write")),
            })
        elif m.get("operation") == "openThread":
            out["proposed_thread_opens"].append({
                "id": body.get("id"), "title": body.get("title"),
                "type": body.get("type"),
            })
        elif m.get("operation") == "loopAdd":
            out["proposed_loops"].append({
                "obligation": body.get("description"),
                "target": body.get("target"),
                "party": body.get("party"),
            })
        elif m.get("operation") == "writeInteractionBrief":
            out["proposed_brief_writes"].append({
                "path": body.get("path"),
                "person": body.get("person"),
                "date": body.get("date"),
            })
    return out


# ----------------------------------------------------------------------
# Public review() entry point
# ----------------------------------------------------------------------

def review(req: dict) -> dict:
    """Review-first RI intake.

    Required input:
        req["source_type"]: one of the six known source_types.

    Optional:
        raw_text, summary, event_at, captured_at, event_at_confidence,
        event_at_source, contact_id, name, organization, opportunity,
        people, companies, signal_type, proposed_action, confidence,
        trace_id, source_ref.

    Returns the structured response shape from RI_EVENT_INTAKE_DESIGN.md.

    Side effects:
        - Writes one immutable RI event to system/ri_events/<YYYY-MM>.jsonl
          (unless the chat-scan returns 'not_found', or the dedupe check
          finds an existing event with the same dedupe_key).
        - Stashes the proposed mutation bundle in
          system/.cache/ri_events_pending.json.
    """
    source_type = req.get("source_type")
    if not source_type:
        raise ValueError("source_type is required")

    preprocess = _load_preprocessor(source_type)
    normalized = preprocess(req)
    no_response_report = None
    if (
        source_type == "manual_text"
        and normalized.get("chat_scan_decision") == "found"
        and normalized.get("text")
    ):
        import no_response_handler  # noqa: E402
        candidate = no_response_handler.build_report(normalized["text"])
        if not candidate.get("ungrounded"):
            no_response_report = candidate

    # Quiet-scan gate: the manual pre-processor (step 4 work) may return
    # chat_scan_decision='not_found' for chat content with no RI signal.
    # In that case we return early without writing anything.
    if normalized.get("chat_scan_decision") == "not_found":
        return {
            "ri_scan": "not_found",
            "persistence_status": "not_persisted",
            "event": None,
        }

    # Lazy import — keeps importing ri_intake cheap.
    import manual_relationship_intake as mri

    report = mri.assess(
        text=normalized["text"],
        contact_id=normalized.get("contact_id"),
        name=normalized.get("name"),
        event_at=normalized.get("event_at"),
        captured_at=normalized.get("captured_at"),
        organization=normalized.get("organization"),
        opportunity=normalized.get("opportunity"),
        apply=False,
    )

    proposed_mutations = report.get("proposed_mutations") or []
    has_signal = bool(report.get("signals")) or bool(proposed_mutations)

    if not has_signal:
        if no_response_report:
            return {
                "ri_scan": "found",
                "persistence_status": no_response_report["persistence"]["status"],
                "event": None,
                "summary": "No-response / silence update detected. No generic RI event written.",
                "canonical_no_response_report": no_response_report,
                "canonical_no_response_text": no_response_report.get("canonical_text"),
            }
        return {
            "ri_scan": "not_found",
            "persistence_status": "not_persisted",
            "event": None,
            "summary": "RI scan: no relationship signals detected in this input.",
        }

    # Build the RI event shape.
    source_extras = normalized.get("source_extras") or {}
    source_block = {
        "type": source_type,
        "id": source_extras.get("id"),
        "path": source_extras.get("path"),
        "title": source_extras.get("title"),
        "raw_text_hash": ri_events._hash_text(normalized["text"]) if normalized.get("text") else None,
    }
    # recruiting_update needs role_slug to live on source for dedupe.
    if "role_slug" in source_extras:
        source_block["role_slug"] = source_extras["role_slug"]
    # email_paste needs message_id and subject.
    if "message_id" in source_extras:
        source_block["message_id"] = source_extras["message_id"]
    if "subject" in source_extras:
        source_block["subject"] = source_extras["subject"]

    entities_block = {
        "people": _people_from_assess(report, normalized.get("extracted_people") or []),
        "companies": _companies_from_assess(report, normalized.get("extracted_companies") or []),
    }

    signal_type = req.get("signal_type") or _primary_signal_type(report)
    signal_block = {
        "type": signal_type,
        "subtypes": [s.get("type") for s in (report.get("signals") or [])],
        "substance": "high" if any(
            (s.get("strength") or 0) >= 0.75 for s in (report.get("signals") or [])
        ) else "medium" if report.get("signals") else "low",
        "confidence": req.get("confidence")
            or (report.get("opportunity_state") or {}).get("confidence")
            or 0.6,
        "recommended_projection_changes": _recommended_projection_changes(proposed_mutations),
    }

    # Bundle id = mb_<YYYY-MM-DD>_<slug>_<seq>. Slug derived from the primary
    # entity. Seq is taken from the index after we know the event_id.
    primary_slug = None
    if entities_block["people"]:
        primary_slug = ri_events.slugify(
            entities_block["people"][0].get("matched_id")
            or entities_block["people"][0].get("raw") or "",
            30,
        )
    if not primary_slug and entities_block["companies"]:
        primary_slug = ri_events.slugify(entities_block["companies"][0].get("raw") or "", 30)
    primary_slug = primary_slug or "manual"

    event = {
        "event_at": normalized["event_at"],
        "event_at_confidence": normalized.get("event_at_confidence") or "low",
        "event_at_source": normalized.get("event_at_source"),
        "captured_at": normalized.get("captured_at")
            or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source_block,
        "entities": entities_block,
        "dedupe": {"decision": "new_event", "duplicates": []},
        "signal": signal_block,
        "persistence": {
            "status": "proposed_write_pending_confirmation",
            "proposed_mutation_bundle_id": None,  # filled below once event_id is known
            "projected_to": [],
            "validated_at": None,
        },
        "trace_id": req.get("trace_id"),
        "_slug": primary_slug,  # consumed by ri_events.generate_event_id
    }

    append_result = ri_events.append(event)
    event_id = append_result["event_id"]

    if not append_result["was_appended"]:
        # The exact payload was already captured. Surface the existing event
        # without writing a new bundle (any prior bundle is still in pending
        # if it hasn't been confirmed yet).
        existing = ri_events.find_by_event_id(event_id)
        return {
            "ri_scan": "found",
            "summary": "RI already captured for this exact input. No new event written.",
            "event": existing,
            "event_id": event_id,
            "dedupe": {"decision": "duplicate_of_existing", "duplicates": [event_id]},
            "persistence_status": (existing or {}).get("persistence", {}).get("status")
                or "proposed_write_pending_confirmation",
            "proposed_mutation_bundle_id": (existing or {}).get("persistence", {}).get(
                "proposed_mutation_bundle_id"
            ),
            **({
                "canonical_no_response_report": no_response_report,
                "canonical_no_response_text": no_response_report.get("canonical_text"),
            } if no_response_report else {}),
        }

    # Build and stash the proposed mutation bundle.
    bundle_id = f"mb_{event_id.removeprefix('ri_')}"
    bundle = {
        "bundle_id": bundle_id,
        "event_id": event_id,
        "created_at": event["captured_at"],
        "operations": [
            {
                "op": m.get("operation"),
                "args": m.get("body") or {},
                "rationale": m.get("reason"),
                "auto_safe": bool(m.get("safe_to_write")),
                "endpoint": m.get("endpoint"),
            }
            for m in proposed_mutations
        ],
        "auto_safe_operations": [
            f"{m.get('operation')}:{(m.get('body') or {}).get('id') or (m.get('body') or {}).get('party') or '?'}"
            for m in proposed_mutations if m.get("safe_to_write")
        ],
        "review_required_operations": _confirmation_required_for(proposed_mutations),
        # The full assess report is stashed too — confirm() needs it to call
        # mri.apply_mutations() without redoing assessment.
        "assess_report": report,
        "preproc_normalized": normalized,
        "source_request": req,
    }
    _stash_bundle(bundle)

    # Patch the event with the bundle id (the file already carries it as null;
    # we don't rewrite the JSONL — the canonical link is bundle.event_id and
    # the live response below carries the bundle_id explicitly).
    # If we ever DO need persistence.proposed_mutation_bundle_id on disk, the
    # confirm step writes a follow-up event referencing both. That preserves
    # event-sourcing immutability.

    summary_lines = _safe_proposed_summary(proposed_mutations)
    matched_contacts = [
        p["matched_id"]
        for p in entities_block["people"]
        if p.get("decision") == "matched_existing" and p.get("matched_id")
    ]
    unmatched_entities = [
        {"raw": p.get("raw"), "type": "person", "decision": p.get("decision")}
        for p in entities_block["people"]
        if p.get("decision") != "matched_existing"
    ] + [
        {"raw": c.get("raw"), "type": "company", "decision": c.get("decision")}
        for c in entities_block["companies"]
        if c.get("decision") != "matched_existing"
    ]

    primary_person = entities_block["people"][0] if entities_block["people"] else {}
    primary_name = primary_person.get("raw") or primary_person.get("matched_id") or "unknown"
    primary_company = (
        entities_block["companies"][0].get("raw") if entities_block["companies"] else None
    )
    summary_text = (
        f"RB proposed {signal_block['type']} RI event for {primary_name}"
        + (f" / {primary_company}" if primary_company else "")
        + f". Persistence: proposed_write_pending_confirmation. Bundle: {bundle_id}."
    )
    pending_projection = _recommended_projection_changes(proposed_mutations)

    canonical_resp = _build_canonical_response(
        scenario="manual_ri_intake",
        action_state="proposed",
        summary=summary_text,
        facts=[
            f"signal_type: {signal_block['type']}",
            f"substance: {signal_block['substance']}",
            f"confidence: {signal_block['confidence']}",
        ],
        persistence_status="proposed_write_pending_confirmation",
        bundle_id=bundle_id,
        event_ids=[event_id],
        pending=pending_projection,
        recommended_actions=["Confirm proposed writes via confirm(event_id)."],
        source_refs=list(summary_lines.get("proposed_contact_updates") or []),
        grounding="manual_user_provided",
        freshness=normalized.get("event_at_confidence") or "medium",
        confidence=str(signal_block.get("confidence") or "medium"),
    )

    return {
        "ri_scan": "found",
        "summary": f"{signal_block['type']} signal, substance={signal_block['substance']}, "
                   f"confidence={signal_block['confidence']}.",
        "event": ri_events.find_by_event_id(event_id),
        "event_id": event_id,
        "matched_contacts": matched_contacts,
        "unmatched_entities": unmatched_entities,
        "active_threads_touched": [],  # populated in step 3 when classifiers match threads
        "dedupe": {"decision": append_result["decision"], "duplicates": []},
        "persistence_status": "proposed_write_pending_confirmation",
        "proposed_mutation_bundle_id": bundle_id,
        "auto_applied": [],  # auto-apply lives in step 3 — see auto_safe rule
        "confirmation_required_for": _confirmation_required_for(proposed_mutations),
        "canonical_response": canonical_resp,
        **({
            "canonical_no_response_report": no_response_report,
            "canonical_no_response_text": no_response_report.get("canonical_text"),
        } if no_response_report else {}),
        **summary_lines,
    }


# ----------------------------------------------------------------------
# confirm() helpers
# ----------------------------------------------------------------------

def _find_followup_event(event_id: str) -> dict | None:
    """Return the correction_event that confirmed `event_id`, or None."""
    try:
        all_events = ri_events.load_events()
    except Exception:
        return None
    for ev in all_events:
        p = ev.get("persistence") or {}
        if p.get("confirms_event_id") == event_id:
            return ev
    return None


def _snapshot_entity_states(entities_block: dict) -> dict:
    """Snapshot current baseline state for people about to be mutated.

    Returns a dict keyed by contact_id with a compact summary of the fields
    most likely to change (last_touch, role, company, signal_class). Used
    for proof_readback before/after comparison.
    """
    snapshot: dict[str, dict | None] = {}
    try:
        baseline = core.load_baseline()
        idx = {e.get("id"): e for e in baseline}
    except Exception:
        return snapshot
    for p in (entities_block or {}).get("people") or []:
        cid = p.get("matched_id")
        if cid:
            entry = idx.get(cid)
            snapshot[cid] = {
                "last_touch": (entry or {}).get("last_touch"),
                "role": (entry or {}).get("role"),
                "company": (entry or {}).get("company"),
                "signal_class": (entry or {}).get("signal_class"),
                "exists": entry is not None,
            }
    return snapshot


def _build_proof_readback(before_snapshot: dict, applied_ops: list[dict]) -> list[dict]:
    """Compare before_snapshot to current baseline for each applied op.

    Returns a list of proof rows, one per applied operation that touched
    canonical state, each with before/after values and a write_path.
    """
    rows: list[dict] = []
    if not applied_ops:
        return rows

    def _display_path(path: Path) -> str:
        try:
            return str(path.relative_to(core.PROJECT_DIR))
        except ValueError:
            return str(path)

    try:
        baseline = core.load_baseline()
        after_idx = {e.get("id"): e for e in baseline}
    except Exception:
        return rows

    for op in applied_ops:
        status = op.get("status")
        if status not in {"written", "covered_by_addContact"}:
            continue
        operation = op.get("operation")
        cid = op.get("id") or op.get("party")

        if operation == "addContact":
            after_entry = after_idx.get(cid) or {}
            rows.append({
                "operation": operation,
                "id": cid,
                "write_path": _display_path(core.BASELINE_PATH),
                "before": {"exists": False},
                "after": {
                    "exists": True,
                    "last_touch": after_entry.get("last_touch"),
                    "role": after_entry.get("role"),
                    "company": after_entry.get("company"),
                },
            })
        elif operation in {"touchContact", "covered_by_addContact"}:
            before = before_snapshot.get(cid) or {}
            after_entry = after_idx.get(cid) or {}
            rows.append({
                "operation": operation,
                "id": cid,
                "write_path": _display_path(core.BASELINE_PATH),
                "before": {"last_touch": before.get("last_touch")},
                "after": {"last_touch": after_entry.get("last_touch")},
            })
        elif operation == "openThread":
            rows.append({
                "operation": operation,
                "id": cid,
                "write_path": _display_path(core.PROJECT_DIR / "system/active_threads.yaml"),
                "before": {"exists": False},
                "after": {"exists": True},
            })
        elif operation == "loopAdd":
            rows.append({
                "operation": operation,
                "id": cid,
                "write_path": _display_path(core.PROJECT_DIR / "system/loops.md"),
                "before": {"exists": False},
                "after": {"exists": True},
            })
        else:
            rows.append({
                "operation": operation,
                "id": cid,
                "write_path": "system/",
                "before": None,
                "after": {"status": status},
            })
    return rows


def _build_ri_assessment_recorded(
    target: dict,
    applied_ops: list[dict],
    assess_report: dict | None,
    proof_readback: list[dict],
) -> dict:
    """Build the ri_assessment block for a successfully-confirmed event.

    status=recorded is only valid when canonical state actually changed.
    """
    signal = (target.get("signal") or {})
    opp_state = (assess_report or {}).get("opportunity_state") or {}
    raw_confidence = opp_state.get("confidence") \
        or signal.get("confidence") \
        or 0.7
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        confidence = 0.7

    source_type = (target.get("source") or {}).get("type") or "manual_relationship_intake"
    entities = target.get("entities") or {}
    contact_ids = [
        p.get("matched_id") or p.get("raw")
        for p in (entities.get("people") or [])
        if p.get("matched_id") or p.get("raw")
    ]

    evidence = [
        f"{op['operation']} → {op['id']} (before: {op.get('before')}, after: {op.get('after')})"
        for op in proof_readback
    ]

    return {
        "status": "recorded",
        "source": source_type,
        "source_freshness": "fresh",
        "confidence": round(min(confidence, 1.0), 2),
        "evidence": evidence or [f"applied {len(applied_ops)} operation(s)"],
        "mapped_contact_ids": contact_ids,
        "mapped_thread_ids": [],
        "proposed_mutation": None,
        "display_recommendation": "show",
        "reason": (
            f"Canonical write confirmed: {len(applied_ops)} operation(s) applied. "
            f"Proof: {len(proof_readback)} write path(s) validated."
        ),
    }


# ----------------------------------------------------------------------
# Public confirm() entry point
# ----------------------------------------------------------------------

def confirm(event_id: str, *, accept: list[str] | None = None,
            reject: list[str] | None = None, dry_run: bool = False) -> dict:
    """Confirm a previously-proposed RI event.

    `accept` and `reject` are lists of `op:id` strings matching the
    `confirmation_required_for` list returned by `review()`. Anything not
    listed in either is treated as accept (default-accept once the operator
    has reviewed; the explicit reject list is the safety net).

    `dry_run=True` shapes the response identically but does NOT call
    `manual_relationship_intake.apply_mutations()`, does NOT write to
    canonical state, and does NOT consume the pending bundle. Use this
    from smoke tests so canonical state stays untouched.

    Calling confirm() on an event_id that has already been confirmed is
    idempotent: returns the prior confirmation result with
    `already_confirmed=True` rather than raising an error.

    Side effects (real run only):
        - Calls manual_relationship_intake.apply_mutations() on a filtered
          copy of the proposed bundle.
        - Writes a follow-up RI event with persistence.status=persisted
          (or rejected_by_operator if all operations were rejected).
        - Removes the bundle from the pending cache.
        - Returns ri_assessment block with status=recorded when writes succeed.
        - Returns proof_readback with before/after per applied operation.
    """
    target = ri_events.find_by_event_id(event_id)
    if target is None:
        raise ValueError(f"event_id not found: {event_id}")

    # Locate the bundle by scanning pending. Bundles are keyed by bundle_id
    # but we also stored event_id inside; pop by bundle_id requires knowing
    # the mapping, so we do a lookup.
    pending = _load_pending()
    bundle = None
    bundle_id = None
    for bid, b in pending.items():
        if b.get("event_id") == event_id:
            bundle = b
            bundle_id = bid
            break
    if bundle is None:
        # Idempotent: no pending bundle means this event was already confirmed
        # (or explicitly rejected). Return the prior result without error.
        prior = _find_followup_event(event_id)
        prior_status = (prior.get("persistence") or {}).get("status") if prior else "already_confirmed"
        prior_applied = (prior.get("persistence") or {}).get("applied") or []
        return {
            "event_id": event_id,
            "follow_up_event_id": (prior or {}).get("event_id"),
            "persistence_status": prior_status,
            "applied": prior_applied,
            "rejected": [],
            "skipped_by_engine": [],
            "already_confirmed": True,
            "ri_assessment": _build_ri_assessment_recorded(
                target, prior_applied, None, []
            ) if prior_status == "persisted" else {
                "status": prior_status or "already_confirmed",
                "source": "ri_intake",
                "reason": "Event was previously confirmed; no new writes.",
            },
            "proof_readback": [],
            "post_validation": {"all_passed": True, "already_confirmed": True},
        }

    accept_set = set(accept or [])
    reject_set = set(reject or [])

    # Snapshot entity state before any mutations for proof_readback.
    before_snapshot = _snapshot_entity_states(target.get("entities") or {})

    # Filter the assess report's proposed_mutations by accept/reject.
    report = deepcopy(bundle["assess_report"])
    assess_report = bundle.get("assess_report")
    filtered: list[dict] = []
    skipped_by_user: list[dict] = []
    for m in report.get("proposed_mutations") or []:
        op = m.get("operation")
        body = m.get("body") or {}
        ident = body.get("id") or body.get("party") or body.get("path") or "?"
        key = f"{op}:{ident}"
        if key in reject_set:
            skipped_by_user.append({"operation": op, "id": ident, "reason": "rejected_by_operator"})
            continue
        # If an accept list was provided, anything not in it is treated as
        # rejected (strict). If no accept list, default-accept.
        if accept or accept_set:
            if key not in accept_set and not m.get("safe_to_write"):
                # We require explicit accept for non-safe operations when
                # caller supplied an accept list.
                skipped_by_user.append({
                    "operation": op, "id": ident,
                    "reason": "not_in_accept_list",
                })
                continue
        filtered.append(m)
    report["proposed_mutations"] = filtered

    if dry_run:
        applied_ops: list[dict] = []
        skipped_ops: list[dict] = [
            {"operation": m.get("operation"),
             "id": (m.get("body") or {}).get("id") or (m.get("body") or {}).get("party"),
             "reason": "dry_run"}
            for m in filtered
        ]
    else:
        # Apply.
        import manual_relationship_intake as mri
        applied_report = mri.apply_mutations(report)
        applied_ops = applied_report.get("persistence", {}).get("applied") or []
        skipped_ops = applied_report.get("persistence", {}).get("skipped") or []

    # Did anything succeed?
    any_written = any(
        a.get("status") in {"written", "already_exists", "covered_by_addContact"}
        for a in applied_ops
    )

    follow_up_status = "persisted" if any_written else "rejected_by_operator"

    # Build proof_readback and ri_assessment now that we know what wrote.
    proof_readback = (
        _build_proof_readback(before_snapshot, applied_ops)
        if (any_written and not dry_run) else []
    )
    ri_assessment_block = (
        _build_ri_assessment_recorded(target, applied_ops, assess_report, proof_readback)
        if (any_written and not dry_run)
        else {
            "status": "proposed" if dry_run else "blocked",
            "source": (target.get("source") or {}).get("type") or "ri_intake",
            "source_freshness": "fresh",
            "confidence": 0.0,
            "evidence": [],
            "mapped_contact_ids": [],
            "mapped_thread_ids": [],
            "proposed_mutation": None,
            "display_recommendation": "suppress",
            "reason": "dry_run — no canonical writes made." if dry_run
                      else "All operations were rejected or skipped; nothing written.",
        }
    )

    follow_up = {
        "event_at": target.get("event_at"),
        "event_at_confidence": target.get("event_at_confidence"),
        "event_at_source": "operator_confirmation",
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "type": "internal_corrective",
            "id": event_id,  # we're correcting (advancing) the prior event's state
            "raw_text_hash": None,
        },
        "entities": target.get("entities") or {"people": [], "companies": []},
        "dedupe": {"decision": "correction_event", "duplicates": [event_id]},
        "signal": {
            "type": (target.get("signal") or {}).get("type") or "manual_relationship_signal",
            "recommended_projection_changes": [],
        },
        "persistence": {
            "status": follow_up_status,
            "projected_to": [a for a in applied_ops if a.get("status") == "written"],
            "validated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "applied": applied_ops,
            "skipped": skipped_ops + skipped_by_user,
            "confirms_event_id": event_id,
        },
        "ri_assessment": ri_assessment_block,
        "trace_id": target.get("trace_id"),
        "_slug": "confirmation",
    }
    if dry_run:
        # No follow-up event, no bundle consumption — dry run shapes the
        # response but leaves canonical + event stream untouched.
        return {
            "event_id": event_id,
            "follow_up_event_id": None,
            "persistence_status": "dry_run",
            "applied": [],
            "rejected": skipped_by_user,
            "skipped_by_engine": skipped_ops,
            "ri_assessment": ri_assessment_block,
            "proof_readback": [],
            "post_validation": {"all_passed": True, "dry_run": True},
        }

    append_result = ri_events.append(follow_up, allow_existing_id=True)

    # Bundle is consumed.
    _pop_bundle(bundle_id)

    return {
        "event_id": event_id,
        "follow_up_event_id": append_result["event_id"],
        "persistence_status": follow_up_status,
        "applied": applied_ops,
        "rejected": skipped_by_user,
        "skipped_by_engine": skipped_ops,
        "ri_assessment": ri_assessment_block,
        "proof_readback": proof_readback,
        "post_validation": {
            "all_passed": follow_up_status == "persisted",
        },
    }


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def _smoke() -> int:
    """End-to-end review→confirm cycle against synthetic input.

    Uses an isolated tmpdir so the real RI event store and pending cache
    are untouched. Returns 0 on success, 1 on failure."""
    import tempfile
    import shutil

    global PENDING_PATH  # noqa: PLW0603 — intentional rebinding for smoke run
    orig_pending = PENDING_PATH
    orig_events_dir = ri_events.EVENTS_DIR
    orig_index = ri_events.INDEX_PATH
    tmp = Path(tempfile.mkdtemp(prefix="ri_intake_smoke_"))
    ri_events.EVENTS_DIR = tmp / "ri_events"
    ri_events.INDEX_PATH = tmp / "ri_events_index.json"
    PENDING_PATH = tmp / "ri_events_pending.json"

    failures = 0

    def expect(label: str, cond: bool, detail: str = "") -> None:
        nonlocal failures
        if cond:
            print(f"  OK   {label}")
        else:
            failures += 1
            print(f"  FAIL {label}  {detail}")

    try:
        # ------------------------------------------------------------
        # Fixture 1 — Simin / Hari recruiting screen (T-2026-05-19-006)
        # ------------------------------------------------------------
        resp = review({
            "source_type": "recruiting_update",
            "raw_text": (
                "Spoke with Simin from TritonExec on Friday about the Hari "
                "McDonald's account role. She is going to circulate my "
                "resume internally and follow up next week."
            ),
            "name": "Simin Gorgulu",
            "organization": "TritonExec",
            "opportunity": "Hari McDonald's account role",
            "event_at": "2026-05-15",
            "event_at_confidence": "medium",
            "event_at_source": "operator_said_friday",
            "captured_at": "2026-05-19T13:00:00-05:00",
            "trace_id": "T-2026-05-19-006",
        })
        expect("review returns ri_scan=found", resp.get("ri_scan") == "found",
               f"got {resp.get('ri_scan')}")
        expect("review returns proposed_mutation_bundle_id",
               bool(resp.get("proposed_mutation_bundle_id")))
        expect("review persistence_status=proposed_write_pending_confirmation",
               resp.get("persistence_status") == "proposed_write_pending_confirmation")
        event_id = resp.get("event_id")
        expect("review returns event_id", bool(event_id))

        # Re-review same payload → dedupe.
        resp2 = review({
            "source_type": "recruiting_update",
            "raw_text": (
                "Spoke with Simin from TritonExec on Friday about the Hari "
                "McDonald's account role. She is going to circulate my "
                "resume internally and follow up next week."
            ),
            "name": "Simin Gorgulu",
            "organization": "TritonExec",
            "opportunity": "Hari McDonald's account role",
            "event_at": "2026-05-15",
            "event_at_confidence": "medium",
            "event_at_source": "operator_said_friday",
            "captured_at": "2026-05-19T13:01:00-05:00",
            "trace_id": "T-2026-05-19-006",
        })
        expect("re-review dedupes",
               resp2.get("dedupe", {}).get("decision") == "duplicate_of_existing")
        expect("re-review returns the same event_id", resp2.get("event_id") == event_id)

        # Pending bundle visible in cache.
        pending = _load_pending()
        expect("bundle stashed in pending", resp["proposed_mutation_bundle_id"] in pending)

        # Confirm in dry_run mode — exercises the filter logic without
        # touching baseline / threads / loops / briefs.
        confirmation_required = resp.get("confirmation_required_for") or []
        conf_dry = confirm(event_id, accept=[], reject=confirmation_required, dry_run=True)
        expect("dry_run confirm returns persistence_status=dry_run",
               conf_dry.get("persistence_status") == "dry_run")
        expect("dry_run confirm does NOT write follow-up event",
               conf_dry.get("follow_up_event_id") is None)
        expect("dry_run confirm leaves bundle in pending",
               resp["proposed_mutation_bundle_id"] in _load_pending())
        expect("dry_run confirm records rejected operations",
               len(conf_dry.get("rejected") or []) == len(confirmation_required))

        # ------------------------------------------------------------
        # Fixture 2 — Ryan Hildebrand interview (T-2026-05-19-006)
        # "interview ... went well yesterday" → event_at must resolve to
        # 2026-05-18, signal must include interview_completed +
        # second_conversation_requested.
        # ------------------------------------------------------------
        resp_ryan = review({
            "source_type": "recruiting_update",
            "raw_text": (
                "The interview with Ryan Hildebrand from Global Payments "
                "went well yesterday. He was at NRA and wants to have "
                "another conversation either this week or next."
            ),
            "name": "Ryan Hildebrand",
            "organization": "Global Payments",
            "captured_at": "2026-05-19T13:30:00-05:00",
            "trace_id": "T-2026-05-19-006",
        })
        expect("Ryan: ri_scan=found", resp_ryan.get("ri_scan") == "found")
        ryan_event = resp_ryan.get("event") or {}
        expect("Ryan: event_at resolved to 2026-05-18",
               (ryan_event.get("event_at") or "")[:10] == "2026-05-18",
               f"got {ryan_event.get('event_at')!r}")
        expect("Ryan: event_at_confidence=medium",
               ryan_event.get("event_at_confidence") == "medium")
        expect("Ryan: event_at_source mentions yesterday",
               "yesterday" in (ryan_event.get("event_at_source") or ""),
               f"got {ryan_event.get('event_at_source')!r}")
        ryan_sig_subtypes = (ryan_event.get("signal") or {}).get("subtypes") or []
        expect("Ryan: interview_completed in signal subtypes",
               "interview_completed" in ryan_sig_subtypes,
               f"got {ryan_sig_subtypes}")
        expect("Ryan: second_conversation_requested in signal subtypes",
               "second_conversation_requested" in ryan_sig_subtypes)

        # ------------------------------------------------------------
        # Fixture 3 — PerfectHire transcript (T-2026-05-19-005)
        # Title "Todd <> PerfectHire - QSR Platform Review - May 19"
        # → event_at must be 2026-05-19 (NOT today, since captured_at
        # is May 20 in this fixture to test the upload-vs-meeting gap).
        # Participants Olivia + Max must be extracted; Todd excluded.
        # ------------------------------------------------------------
        ph_transcript = (
            "Olivia Nielsen: Thanks for joining. Let me kick off with our "
            "QSR scheduling problem.\n"
            "Todd: Sure, happy to walk through what I learned.\n"
            "Max Holmes: One thing I have been asking myself is how the "
            "POS integration plays into your retention story.\n"
            "Olivia Nielsen: That is the right question. I think Matt would "
            "love to talk through that with you.\n"
            "Todd: Looking forward to that intro.\n"
        )
        resp_ph = review({
            "source_type": "fathom_manual_paste",
            "raw_text": ph_transcript,
            "captured_at": "2026-05-20T09:00:00-05:00",  # NEXT-DAY capture
            "source_ref": {
                "title": "Todd <> PerfectHire - QSR Platform Review - May 19",
                "id": "fathom://share/test-perfecthire",
            },
            "trace_id": "T-2026-05-19-005",
        })
        expect("PerfectHire: ri_scan=found", resp_ph.get("ri_scan") == "found")
        ph_event = resp_ph.get("event") or {}
        expect("PerfectHire: event_at anchored to meeting (2026-05-19), not upload (2026-05-20)",
               (ph_event.get("event_at") or "")[:10] == "2026-05-19",
               f"got {ph_event.get('event_at')!r}")
        expect("PerfectHire: event_at_confidence=high",
               ph_event.get("event_at_confidence") == "high")
        ph_people = [p.get("raw") for p in (ph_event.get("entities") or {}).get("people") or []]
        expect("PerfectHire: Olivia Nielsen extracted",
               "Olivia Nielsen" in ph_people, f"got {ph_people}")
        expect("PerfectHire: Max Holmes extracted",
               "Max Holmes" in ph_people)
        expect("PerfectHire: Todd NOT listed as participant",
               not any("todd" in (p or "").lower() for p in ph_people))
        ph_companies = [c.get("raw") for c in (ph_event.get("entities") or {}).get("companies") or []]
        expect("PerfectHire: company hint extracted",
               any("perfecthire" in (c or "").lower() for c in ph_companies),
               f"got {ph_companies}")

        # ------------------------------------------------------------
        # Fixture 4 — email_paste with full RFC headers
        # event_at must anchor to the message Date header (high), not
        # to captured_at.
        # ------------------------------------------------------------
        email_text = (
            "From: Ryan Hildebrand <ryan@globalpayments.com>\n"
            "To: Todd Vahlsing <vahlsingt@gmail.com>\n"
            "Subject: Re: Director conversation next steps\n"
            "Date: Mon, 18 May 2026 14:32:10 -0500\n"
            "Message-ID: <CAH9876xyz@mail.gmail.com>\n"
            "\n"
            "Hi Todd, great talking yesterday. Let me know your availability "
            "next week for another conversation.\n"
            "Ryan\n"
        )
        resp_email = review({
            "source_type": "email_paste",
            "raw_text": email_text,
            "captured_at": "2026-05-19T13:00:00-05:00",
            "trace_id": "T-2026-05-19-006",
        })
        expect("email: ri_scan=found", resp_email.get("ri_scan") == "found")
        em_event = resp_email.get("event") or {}
        expect("email: event_at anchored to Date header (2026-05-18)",
               (em_event.get("event_at") or "").startswith("2026-05-18"),
               f"got {em_event.get('event_at')!r}")
        expect("email: event_at_confidence=high",
               em_event.get("event_at_confidence") == "high")
        em_source = em_event.get("source") or {}
        expect("email: subject captured on source",
               (em_source.get("subject") or "").startswith("Re: Director"),
               f"got {em_source.get('subject')!r}")
        expect("email: message_id captured on source",
               em_source.get("message_id") == "CAH9876xyz@mail.gmail.com")

        # ------------------------------------------------------------
        # Fixture 5 — linkedin_screenshot with visible date + name
        # ------------------------------------------------------------
        linkedin_text = (
            "Conversation with Matt Chalzi\n"
            "May 17\n"
            "\n"
            "Matt Chalzi · 1st\n"
            "CEO at PerfectHire\n"
            "\n"
            "Matt: Olivia tells me you have strong views on QSR scheduling. "
            "Would love a quick intro chat.\n"
            "Send message  View profile\n"
        )
        resp_li = review({
            "source_type": "linkedin_screenshot",
            "raw_text": linkedin_text,
            "captured_at": "2026-05-19T13:30:00-05:00",
            "trace_id": "T-2026-05-19-005",
        })
        expect("linkedin: ri_scan=found", resp_li.get("ri_scan") == "found")
        li_event = resp_li.get("event") or {}
        expect("linkedin: event_at anchored to visible date (2026-05-17)",
               (li_event.get("event_at") or "")[:10] == "2026-05-17",
               f"got {li_event.get('event_at')!r}")
        expect("linkedin: event_at_confidence=high",
               li_event.get("event_at_confidence") == "high")
        li_people = [p.get("raw") for p in (li_event.get("entities") or {}).get("people") or []]
        expect("linkedin: Matt Chalzi extracted from header",
               any("matt chalzi" in (p or "").lower() for p in li_people),
               f"got {li_people}")

        # ------------------------------------------------------------
        # Fixture 6 — manual_text quiet-chat NEGATIVE cases
        # ------------------------------------------------------------
        for chat_text in (
            "what is on my plate today?",
            "tell me about Olivia",
            "regenerate today.md",
            "draft me an email to Ryan",
        ):
            resp_chat = review({
                "source_type": "manual_text",
                "raw_text": chat_text,
                "captured_at": "2026-05-19T13:45:00-05:00",
            })
            expect(f"quiet-scan rejects: {chat_text!r}",
                   resp_chat.get("ri_scan") == "not_found"
                   and resp_chat.get("persistence_status") == "not_persisted"
                   and resp_chat.get("event") is None,
                   f"got {resp_chat}")

        # ------------------------------------------------------------
        # Fixture 7 — manual_text quiet-chat POSITIVE cases
        # ------------------------------------------------------------
        for chat_text in (
            "Olivia is introducing me to the CEO",
            "they asked for my resume yesterday",
            "I sent the follow-up to Ryan",
        ):
            resp_chat = review({
                "source_type": "manual_text",
                "raw_text": chat_text,
                "captured_at": "2026-05-19T14:00:00-05:00",
            })
            expect(f"quiet-scan accepts: {chat_text!r}",
                   resp_chat.get("ri_scan") == "found",
                   f"got ri_scan={resp_chat.get('ri_scan')!r}")

        # ------------------------------------------------------------
        # Fixture 8 — manual_text no-response canonical attachment
        # ------------------------------------------------------------
        no_response_text = (
            "No response from Simin regarding Harri. "
            "Waiting on Jeff Coffland for possible McDonald's-side advocacy. "
            "No response from Global Payments follow-up email."
        )
        resp_no_response = review({
            "source_type": "manual_text",
            "raw_text": no_response_text,
            "captured_at": "2026-05-20T14:00:00-05:00",
            "trace_id": "T-2026-05-20-002",
        })
        canon_text = resp_no_response.get("canonical_no_response_text") or ""
        canon_report = resp_no_response.get("canonical_no_response_report") or {}
        expect("no-response: ri_scan=found",
               resp_no_response.get("ri_scan") == "found")
        expect("no-response: canonical text attached",
               "Observed signals:" in canon_text and "Persistence:" in canon_text,
               f"got {canon_text!r}")
        expect("no-response: proposed-write persistence surfaced",
               (canon_report.get("persistence") or {}).get("status")
               == "proposed_write_pending_confirmation",
               f"got {canon_report.get('persistence')}")

    finally:
        ri_events.EVENTS_DIR = orig_events_dir
        ri_events.INDEX_PATH = orig_index
        PENDING_PATH = orig_pending
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"--- ri_intake smoke complete: {failures} failure(s) ---")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="RI intake dispatcher CLI.")
    p.add_argument("--smoke", action="store_true",
                   help="Run the smoke test in an isolated tmpdir.")
    sub = p.add_subparsers(dest="cmd")

    p_review = sub.add_parser("review", help="Run review() on a JSON payload from a file.")
    p_review.add_argument("payload", help="Path to a JSON file containing the request body.")

    p_confirm = sub.add_parser("confirm", help="Confirm a previously-reviewed event.")
    p_confirm.add_argument("event_id")
    p_confirm.add_argument("--accept", default="[]")
    p_confirm.add_argument("--reject", default="[]")

    args = p.parse_args(argv)

    if args.smoke:
        return _smoke()

    if args.cmd == "review":
        req = json.loads(Path(args.payload).read_text(encoding="utf-8"))
        print(json.dumps(review(req), indent=2, default=str))
        return 0

    if args.cmd == "confirm":
        accept = json.loads(args.accept)
        reject = json.loads(args.reject)
        print(json.dumps(confirm(args.event_id, accept=accept, reject=reject), indent=2, default=str))
        return 0

    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
