#!/usr/bin/env python3
"""
direct_comms_health.py — RB-DEFECT-004 preflight and passive RI extraction for direct comms.

Implements the five-state readiness model for Apple Messages and Phone Calls:
    unavailable           — database not accessible (Full Disk Access not granted or file missing)
    available_stale       — inbox file exists but older than the freshness threshold
    available_metadata_only — inbox has events but snippet content is null/empty
    available_with_snippets — inbox has events with readable snippet content
    available_fresh       — inbox is fresh (< staleness_hours old) and has events

Privacy constraints (P-038):
    - Local-only ingestion only. No data is sent externally.
    - Raw message text is never stored beyond the truncated snippet already captured by fetch_apple_messages.
    - No sending or replying on Todd's behalf. Ever.
    - Only minimized RI evidence is persisted: contact_id, channel, direction, event_at,
      snippet_available, active_thread_ids, urgency_signal, proposed_mutation.
    - Full Disk Access failures are surfaced as unavailable with recovery instructions —
      never bypassed.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402

# Freshness threshold in hours — inbox files older than this are considered stale.
MESSAGES_STALENESS_HOURS = 48
CALLS_STALENESS_HOURS = 48

MESSAGES_PATH = core.SYSTEM_DIR / "inbox" / "messages.json"
CALLS_PATH = core.SYSTEM_DIR / "inbox" / "calls.json"

# macOS database paths — used for Full Disk Access detection only (never read here directly).
MESSAGES_DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"
CALLS_DB_PATH = Path.home() / "Library" / "Application Support" / "CallHistoryDB" / "CallHistory.storedata"

READINESS_STATES = {
    "unavailable",
    "available_stale",
    "available_metadata_only",
    "available_with_snippets",
    "available_fresh",
}

# --- Brief language templates ---
BRIEF_LANGUAGE: dict[str, str] = {
    "unavailable": (
        "Apple {source_label} was unavailable. Direct {channel_label} signals could not be assessed. "
        "To enable: grant Full Disk Access to Terminal (or the RB scheduler process) in "
        "System Settings → Privacy & Security → Full Disk Access, then run: {recovery_command}"
    ),
    "available_stale": (
        "Apple {source_label} is stale (last read: {last_read}). "
        "Signals since {last_read} were not assessed. "
        "Run: {recovery_command}"
    ),
    "available_metadata_only": (
        "Apple {source_label} was read but content was not inspectable. "
        "Snippet-based RI was not possible. Metadata-only: {event_count} events captured. "
        "To enable snippets: run: {recovery_command_snippets}"
    ),
    "available_with_snippets": (
        "Apple {source_label} read: {event_count} events, {matched_count} matched to known contacts, "
        "{urgency_count} urgency signal(s). Snippets available."
    ),
    "available_fresh": (
        "Apple {source_label} fresh (read: {last_read}): {event_count} events, "
        "{matched_count} matched to known contacts, {urgency_count} urgency signal(s)."
    ),
}


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_fetched_at(fetched_at_str: str | None) -> datetime | None:
    if not fetched_at_str:
        return None
    try:
        return datetime.fromisoformat(fetched_at_str)
    except ValueError:
        return None


def _age_hours(fetched_at: datetime | None) -> float | None:
    if fetched_at is None:
        return None
    now = _utcnow()
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return (now - fetched_at).total_seconds() / 3600


def _full_disk_access_available(db_path: Path) -> bool:
    """Return True if the database path is readable (Full Disk Access granted)."""
    try:
        return db_path.exists() and db_path.stat().st_size > 0
    except (PermissionError, OSError):
        return False


def _load_inbox(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _has_snippets(events: list[dict]) -> bool:
    return any(
        e.get("snippet") or e.get("text") or e.get("body")
        for e in events
    )


def _match_events_to_contacts(events: list[dict], baseline: list[dict]) -> list[dict]:
    """Match event handles/phones to RB contact profiles. Returns enriched candidates."""
    # Build a lookup of phone/handle slugs → contact ids.
    # DEFECT-003 fix: baseline uses "phone" (singular); also support "phones" (plural) for
    # forward-compat if the schema ever adds it.
    handle_map: dict[str, str] = {}
    for contact in baseline:
        cid = contact.get("id", "")
        # Singular phone field (current baseline schema)
        phone_singular = contact.get("phone") or ""
        if phone_singular:
            norm = "".join(c for c in str(phone_singular) if c.isdigit())[-10:]
            if norm:
                handle_map[norm] = cid
        # Plural phones field (future schema / alternate sources)
        phones = contact.get("phones") or []
        if isinstance(phones, str):
            phones = [phones]
        for ph in phones:
            norm = "".join(c for c in str(ph) if c.isdigit())[-10:]
            if norm:
                handle_map[norm] = cid
        email = contact.get("email") or ""
        if email:
            handle_map[email.lower()] = cid

    # Handles Todd has explicitly marked personal/private via
    # sms_exempt_manager.py (system/sms_trusted_senders.json). These aren't
    # RC identity gaps -- don't keep surfacing them in the resolution queue.
    exempt_handles = core.load_sms_exempt_handles()
    exempt_10digit = {h[-10:] for h in exempt_handles if "@" not in h}
    exempt_emails = {h for h in exempt_handles if "@" in h}

    # Track unmatched handles: {norm_handle: {count, last_at, raw_handle}}
    unmatched_freq: dict[str, dict] = {}

    candidates = []
    for event in events:
        handle = event.get("handle") or event.get("address") or event.get("phone") or ""
        norm_handle = "".join(c for c in str(handle) if c.isdigit())[-10:]
        contact_id = handle_map.get(norm_handle) or handle_map.get(handle.lower())

        channel_raw = event.get("service") or event.get("channel") or "sms"
        channel = "imessage" if "imessage" in channel_raw.lower() else "sms"
        direction = event.get("direction") or event.get("is_from_me")
        if isinstance(direction, bool):
            direction = "outbound" if direction else "inbound"
        elif isinstance(direction, int):
            direction = "outbound" if direction == 1 else "inbound"
        else:
            direction = str(direction).lower() if direction else "unknown"

        snippet = event.get("snippet") or event.get("text") or event.get("body")
        snippet_available = bool(snippet) or bool(event.get("has_text"))
        event_at = event.get("at") or event.get("date") or event.get("timestamp") or event.get("event_at")

        is_exempt = (not contact_id) and (
            norm_handle in exempt_10digit or handle.strip().lower() in exempt_emails
        )
        if not contact_id and norm_handle and not is_exempt:
            rec = unmatched_freq.setdefault(norm_handle, {
                "raw_handle": handle, "count": 0, "last_at": None, "directions": set()
            })
            rec["count"] += 1
            rec["directions"].add(direction)
            if event_at and (rec["last_at"] is None or event_at > rec["last_at"]):
                rec["last_at"] = event_at

        if contact_id:
            block_reason = None
        elif is_exempt:
            block_reason = "exempted_personal"
        else:
            block_reason = "unmatched_handle"

        candidates.append({
            "contact_id": contact_id,
            "channel": channel,
            "direction": direction,
            # DEFECT-003 fix: fetch_apple_messages.py uses "at" as the timestamp field.
            "event_at": event_at,
            "snippet_available": snippet_available,
            "active_thread_ids": [],   # thread matching is done by daily_brief layer
            "urgency_signal": False,   # set below
            "proposed_mutation": None,
            "block_reason": block_reason,
        })

    # Build ranked unmatched-handle list: 2-way conversations first, then by count,
    # filtered to handles with >= 2 events (suppress OTPs / one-time spam).
    ranked_unmatched = sorted(
        [
            {
                "handle": v["raw_handle"],
                "message_count": v["count"],
                "last_activity": v["last_at"],
                "two_way": len(v["directions"]) > 1,
            }
            for v in unmatched_freq.values()
            if v["count"] >= 2
        ],
        key=lambda x: (x["two_way"], x["message_count"]),
        reverse=True,
    )
    # Attach to first candidate so caller can read it without iterating all candidates.
    if candidates:
        candidates[0]["_unmatched_handles"] = ranked_unmatched[:20]

    return candidates


def _match_calls_to_contacts(events: list[dict], baseline: list[dict]) -> list[dict]:
    """Match call records to RB contacts. Tags missed calls and urgency signals."""
    # DEFECT-003 fix: baseline uses "phone" (singular); also support "phones" (plural).
    handle_map: dict[str, str] = {}
    for contact in baseline:
        cid = contact.get("id", "")
        phone_singular = contact.get("phone") or ""
        if phone_singular:
            norm = "".join(c for c in str(phone_singular) if c.isdigit())[-10:]
            if norm:
                handle_map[norm] = cid
        phones = contact.get("phones") or []
        if isinstance(phones, str):
            phones = [phones]
        for ph in phones:
            norm = "".join(c for c in str(ph) if c.isdigit())[-10:]
            if norm:
                handle_map[norm] = cid

    candidates = []
    for event in events:
        handle = event.get("handle") or event.get("address") or event.get("phone") or ""
        norm_handle = "".join(c for c in str(handle) if c.isdigit())[-10:]
        contact_id = handle_map.get(norm_handle)

        answered = event.get("answered", True)
        # DEFECT-003 fix: fetch_apple_calls.py uses "duration_seconds", not "duration".
        duration = event.get("duration_seconds") or event.get("duration") or 0
        missed = not answered or duration == 0
        direction_raw = event.get("direction") or event.get("call_type") or "inbound"
        direction = "inbound" if "inbound" in str(direction_raw).lower() or direction_raw == 4 else "outbound"

        # Urgency signal: missed inbound call from a known contact.
        urgency = missed and direction == "inbound" and bool(contact_id)

        candidates.append({
            "contact_id": contact_id,
            "channel": "call_missed" if missed else f"call_{direction}",
            "direction": direction,
            # DEFECT-003 fix: fetch_apple_calls.py uses "at" as the timestamp field.
            "event_at": event.get("at") or event.get("date") or event.get("timestamp"),
            "snippet_available": False,
            "missed": missed,
            "active_thread_ids": [],
            "urgency_signal": urgency,
            "proposed_mutation": "log_missed_call_touch" if urgency else None,
            "block_reason": None if contact_id else "unmatched_handle",
        })
    return candidates


def check_messages_readiness(baseline: list[dict] | None = None) -> dict:
    """Return the 5-state readiness assessment for Apple Messages.

    States:
        unavailable           — DB not accessible or inbox missing with no FDA
        available_stale       — inbox file older than MESSAGES_STALENESS_HOURS
        available_metadata_only — events present but no snippets
        available_with_snippets — events with snippet content
        available_fresh       — fresh and has events (or fresh and empty is still fresh)
    """
    if baseline is None:
        try:
            baseline = core.load_baseline()
        except Exception:  # noqa: BLE001
            baseline = []

    fda = _full_disk_access_available(MESSAGES_DB_PATH)
    inbox = _load_inbox(MESSAGES_PATH)
    recovery_cmd = "python3 system/scripts/fetch_apple_messages.py --days 365"
    recovery_snippets = "python3 system/scripts/fetch_apple_messages.py --days 90 --include-snippets"

    if inbox is None:
        state = "unavailable"
        return {
            "state": state,
            "source": "messages",
            "full_disk_access": fda,
            "event_count": 0,
            "matched_count": 0,
            "urgency_count": 0,
            "snippet_available": False,
            "last_read": None,
            "age_hours": None,
            "ri_candidates": [],
            "brief_language": BRIEF_LANGUAGE[state].format(
                source_label="Messages",
                channel_label="SMS/iMessage",
                last_read="never",
                event_count=0,
                matched_count=0,
                urgency_count=0,
                recovery_command=recovery_cmd,
                recovery_command_snippets=recovery_snippets,
            ),
        }

    fetched_at = _parse_fetched_at(inbox.get("fetched_at"))
    age_h = _age_hours(fetched_at)
    last_read_str = fetched_at.isoformat() if fetched_at else "unknown"
    events = inbox.get("events") or []
    has_snip = _has_snippets(events)
    is_stale = age_h is not None and age_h > MESSAGES_STALENESS_HOURS

    # Match events to contacts for RI candidate extraction.
    ri_candidates = _match_events_to_contacts(events, baseline) if events else []
    matched_count = sum(1 for c in ri_candidates if c.get("contact_id"))
    urgency_count = sum(1 for c in ri_candidates if c.get("urgency_signal"))
    # Unmatched handles ranked by frequency — attached to first candidate by _match_events.
    unmatched_handles = ri_candidates[0].get("_unmatched_handles", []) if ri_candidates else []
    unmatched_count = sum(1 for c in ri_candidates if c.get("block_reason") == "unmatched_handle")

    if is_stale:
        state = "available_stale"
    elif events and has_snip:
        state = "available_with_snippets" if age_h and age_h > 6 else "available_fresh"
    elif events:
        state = "available_metadata_only"
    else:
        # Fresh read, just empty.
        state = "available_fresh"

    return {
        "state": state,
        "source": "messages",
        "full_disk_access": fda,
        "event_count": len(events),
        "matched_count": matched_count,
        "unmatched_count": unmatched_count,
        "unmatched_handles": unmatched_handles,
        "urgency_count": urgency_count,
        "snippet_available": has_snip,
        "last_read": last_read_str,
        "age_hours": round(age_h, 1) if age_h is not None else None,
        "ri_candidates": ri_candidates,
        "brief_language": BRIEF_LANGUAGE[state].format(
            source_label="Messages",
            channel_label="SMS/iMessage",
            last_read=last_read_str,
            event_count=len(events),
            matched_count=matched_count,
            urgency_count=urgency_count,
            recovery_command=recovery_cmd,
            recovery_command_snippets=recovery_snippets,
        ),
    }


def check_calls_readiness(baseline: list[dict] | None = None) -> dict:
    """Return the 5-state readiness assessment for Apple Phone Calls."""
    if baseline is None:
        try:
            baseline = core.load_baseline()
        except Exception:  # noqa: BLE001
            baseline = []

    fda = _full_disk_access_available(CALLS_DB_PATH)
    inbox = _load_inbox(CALLS_PATH)
    recovery_cmd = "python3 system/scripts/fetch_apple_calls.py --days 365"

    if inbox is None:
        state = "unavailable"
        return {
            "state": state,
            "source": "calls",
            "full_disk_access": fda,
            "event_count": 0,
            "matched_count": 0,
            "urgency_count": 0,
            "missed_count": 0,
            "last_read": None,
            "age_hours": None,
            "ri_candidates": [],
            "brief_language": BRIEF_LANGUAGE[state].format(
                source_label="Calls",
                channel_label="phone/FaceTime",
                last_read="never",
                event_count=0,
                matched_count=0,
                urgency_count=0,
                recovery_command=recovery_cmd,
                recovery_command_snippets=recovery_cmd,
            ),
        }

    fetched_at = _parse_fetched_at(inbox.get("fetched_at"))
    age_h = _age_hours(fetched_at)
    last_read_str = fetched_at.isoformat() if fetched_at else "unknown"
    events = inbox.get("calls") or inbox.get("events") or []
    is_stale = age_h is not None and age_h > CALLS_STALENESS_HOURS

    ri_candidates = _match_calls_to_contacts(events, baseline) if events else []
    matched_count = sum(1 for c in ri_candidates if c.get("contact_id"))
    urgency_count = sum(1 for c in ri_candidates if c.get("urgency_signal"))
    missed_count = sum(1 for c in ri_candidates if c.get("missed"))

    if is_stale:
        state = "available_stale"
    elif age_h is not None and age_h <= CALLS_STALENESS_HOURS:
        state = "available_fresh"
    else:
        state = "available_metadata_only"

    return {
        "state": state,
        "source": "calls",
        "full_disk_access": fda,
        "event_count": len(events),
        "matched_count": matched_count,
        "urgency_count": urgency_count,
        "missed_count": missed_count,
        "last_read": last_read_str,
        "age_hours": round(age_h, 1) if age_h is not None else None,
        "ri_candidates": ri_candidates,
        "brief_language": BRIEF_LANGUAGE[
            "available_stale" if is_stale else "available_fresh"
        ].format(
            source_label="Calls",
            channel_label="phone/FaceTime",
            last_read=last_read_str,
            event_count=len(events),
            matched_count=matched_count,
            urgency_count=urgency_count,
            recovery_command=recovery_cmd,
            recovery_command_snippets=recovery_cmd,
        ),
    }


def build_direct_comms_brief_items(baseline: list[dict] | None = None) -> list[dict]:
    """Return structured brief items for the resource_verification section.

    Rules:
    - If either source is not available_fresh or available_with_snippets, the
      brief must not claim completeness for direct comms.
    - Urgency signals from missed calls or inbound messages surface as
      operational risks with act_today disposition.
    - Each item carries state, brief_language, and ri_candidates for the CoS layer.
    """
    if baseline is None:
        try:
            baseline = core.load_baseline()
        except Exception:  # noqa: BLE001
            baseline = []

    msgs = check_messages_readiness(baseline)
    calls = check_calls_readiness(baseline)

    items = []
    trustworthy_states = {"available_fresh", "available_with_snippets"}

    for report in (msgs, calls):
        state = report["state"]
        source_label = "Apple Messages" if report["source"] == "messages" else "Apple Calls"
        is_trustworthy = state in trustworthy_states

        item: dict[str, Any] = {
            "source": report["source"],
            "state": state,
            "brief_language": report["brief_language"],
            "event_count": report["event_count"],
            "matched_count": report["matched_count"],
            "unmatched_count": report.get("unmatched_count", 0),
            "unmatched_handles": report.get("unmatched_handles", []),
            "urgency_count": report["urgency_count"],
            "completeness_claim_safe": is_trustworthy,
            "ri_candidates": [
                c for c in report.get("ri_candidates", [])
                if c.get("contact_id")  # only matched candidates
            ],
        }
        items.append(item)

        # Urgency items: missed calls or inbound messages from known contacts.
        for cand in report.get("ri_candidates", []):
            if cand.get("urgency_signal") and cand.get("contact_id"):
                items.append({
                    "source": report["source"],
                    "state": "urgency_signal",
                    "contact_id": cand["contact_id"],
                    "channel": cand["channel"],
                    "direction": cand["direction"],
                    "event_at": cand.get("event_at"),
                    "proposed_mutation": cand.get("proposed_mutation"),
                    "brief_language": (
                        f"Urgency signal: {cand['channel'].replace('_', ' ')} "
                        f"from contact {cand['contact_id']} at {cand.get('event_at', 'unknown time')}. "
                        f"Proposed action: {cand.get('proposed_mutation') or 'review and respond'}."
                    ),
                    "completeness_claim_safe": True,
                    "ri_candidates": [],
                })

    return items


def get_completeness_caveat(items: list[dict]) -> str | None:
    """Return a caveat string if direct comms sources are not fully trustworthy.

    Returns None if both sources are fresh and complete.
    """
    problem_items = [
        i for i in items
        if i.get("state") not in {"available_fresh", "available_with_snippets", "urgency_signal"}
        and i.get("source") in {"messages", "calls"}
    ]
    if not problem_items:
        return None
    parts = []
    for item in problem_items:
        state = item["state"]
        source = "Apple Messages" if item["source"] == "messages" else "Apple Calls"
        if state == "unavailable":
            parts.append(f"{source} unavailable")
        elif state == "available_stale":
            parts.append(f"{source} stale (last read: {item.get('last_read', 'unknown')})")
        elif state == "available_metadata_only":
            parts.append(f"{source} metadata-only (no snippets)")
    if parts:
        return (
            "Direct communication sources incomplete: "
            + "; ".join(parts)
            + ". 'No new signals' cannot be confirmed for these channels."
        )
    return None
