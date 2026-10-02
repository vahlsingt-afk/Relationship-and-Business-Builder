#!/usr/bin/env python3
"""Build RB's cross-channel observed-interaction ledger and current states.

This ledger is reconstructed deterministically from captured source artifacts;
it does not turn snippets into canonical claims or send/modify anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core


LEDGER_PATH = core.SYSTEM_DIR / "interaction_event_ledger.json"
STATE_PATH = core.CACHE_DIR / "interaction_current_state.json"
HEALTH_PATH = core.CACHE_DIR / "interaction_capture_health.json"
SELF_NAMES = {"todd vahlsing", "todd"}
NAME_EQUIVALENTS = {"josh": "joshua", "mike": "michael", "chris": "christopher",
                    "matt": "matthew", "dan": "daniel", "bob": "robert"}


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _name_keys(value: Any) -> set[str]:
    raw = re.sub(r"\s*\([^)]*\)\s*$", "", str(value or "")).strip()
    variants = {raw}
    if "," in raw:
        last, first = [part.strip() for part in raw.split(",", 1)]
        variants.add(f"{first} {last}")
    out = {_norm(v) for v in variants if _norm(v)}
    for key in list(out):
        parts = key.split()
        if parts and parts[0] in NAME_EQUIVALENTS:
            out.add(" ".join([NAME_EQUIVALENTS[parts[0]], *parts[1:]]))
        for short, formal in NAME_EQUIVALENTS.items():
            if parts and parts[0] == formal:
                out.add(" ".join([short, *parts[1:]]))
    return out


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            try:
                parsed = datetime.fromisoformat(raw[:10]).replace(tzinfo=timezone.utc)
            except ValueError:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _contact_index(baseline: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    by_name, by_email = {}, {}
    for contact in baseline:
        if contact.get("name"):
            for key in _name_keys(contact["name"]):
                by_name.setdefault(key, contact)
        if contact.get("email"):
            by_email.setdefault(str(contact["email"]).lower().strip(), contact)
    return by_name, by_email


def _resolve(person: dict, by_name: dict, by_email: dict) -> tuple[str | None, str | None, str]:
    email = str(person.get("email") or "").lower().strip()
    name = str(person.get("name") or "").strip()
    if email and email in by_email:
        c = by_email[email]
        return c.get("id"), c.get("name"), "high"
    for key in _name_keys(name):
        if key in by_name:
            c = by_name[key]
            return c.get("id"), c.get("name"), "medium"
    return None, name or email or None, "unmatched"


def _priority(contact: dict | None) -> str:
    if not contact:
        return "other"
    if contact.get("rc_tier") == "inner":
        return "inner_circle"
    if contact.get("rc_tier") in {"broader", "dormant_valuable"}:
        return "relationship_card"
    if contact.get("signal_class") in {"RC", "LKI", "LMI"}:
        return "strategic_relationship"
    return "other"


def _event_id(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p or "") for p in parts).encode()).hexdigest()[:24]


def _event(*, channel: str, source: str, source_id: str, at: datetime,
           direction: str, person: dict, subject: str | None, preview: str | None,
           unread: bool | None, by_name: dict, by_email: dict,
           confidence: str = "medium") -> dict:
    contact_id, canonical_name, match_quality = _resolve(person, by_name, by_email)
    contact = next((by_name[k] for k in _name_keys(canonical_name) if k in by_name), None) if canonical_name else None
    return {
        "id": _event_id(channel, source_id, at.isoformat(), direction),
        "observed_at": at.isoformat(timespec="seconds"),
        "channel": channel,
        "direction": direction,
        "contact_id": contact_id,
        "person_name": canonical_name,
        "person_email": person.get("email"),
        "subject": subject,
        "preview": preview,
        "unread": unread,
        "source": source,
        "source_id": source_id,
        "identity_confidence": match_quality,
        "evidence_confidence": confidence,
        "priority_class": _priority(contact),
    }


def _email_events(path: Path, payload: dict, by_name: dict, by_email: dict) -> list[dict]:
    out = []
    for thread in payload.get("threads") or []:
        at = _dt(thread.get("last_message_at"))
        if not at:
            continue
        sender = thread.get("last_message_from") or {}
        sender_self = str(sender.get("email") or "").lower() in core.self_emails() or _norm(sender.get("name")) in SELF_NAMES
        mailbox = thread.get("mailbox") or ("sent" if "email_sent" in path.name else "inbox")
        direction = "outbound" if sender_self or mailbox == "sent" else "inbound"
        people = thread.get("last_message_to") or [] if direction == "outbound" else [sender]
        if direction == "inbound":
            people = [sender]
        for person in people[:5]:
            if not person or (_norm(person.get("name")) in SELF_NAMES):
                continue
            out.append(_event(
                channel="email", source=thread.get("source") or "email_capture",
                source_id=thread.get("thread_id") or "", at=at, direction=direction,
                person=person, subject=thread.get("subject"), preview=thread.get("snippet"),
                unread=thread.get("unread"), by_name=by_name, by_email=by_email,
                confidence="high" if person.get("email") else "medium",
            ))
    return out


def _linkedin_events(payload: dict, by_name: dict, by_email: dict) -> list[dict]:
    out = []
    for msg in payload.get("messages") or []:
        at = _dt(msg.get("date"))
        if not at:
            continue
        sender = msg.get("from") or {}
        sender_self = bool(sender.get("is_self")) or _norm(sender.get("name")) in SELF_NAMES
        sender_url = str(sender.get("profile_url") or "").lower().rstrip("/")
        if sender_url.endswith(("/toddvahlsing", "/todd-vahlsing")):
            sender_self = True
        direction = "outbound" if sender_self or msg.get("direction") == "outbound" else "inbound"
        people = (msg.get("to") or []) if direction == "outbound" else [sender]
        for person in people[:10]:
            if not person or _norm(person.get("name")) in SELF_NAMES:
                continue
            out.append(_event(
                channel="linkedin", source=msg.get("source") or payload.get("source") or "linkedin",
                source_id=msg.get("source_ref") or msg.get("conversation_id") or "",
                at=at, direction=direction, person=person, subject=msg.get("subject"),
                preview=msg.get("content"), unread=msg.get("unread"), by_name=by_name,
                by_email=by_email, confidence="high" if person.get("profile_url") else "medium",
            ))
    return out


def _calendar_events(path: Path, payload: dict, by_name: dict, by_email: dict) -> list[dict]:
    out = []
    for item in payload.get("events") or []:
        at = _dt(item.get("start"))
        if not at:
            continue
        for attendee in item.get("attendees") or []:
            person = attendee if isinstance(attendee, dict) else {"email": attendee}
            if str(person.get("email") or "").lower() in core.self_emails():
                continue
            out.append(_event(
                channel="calendar", source=item.get("source") or "calendar_capture",
                source_id=item.get("id") or "", at=at, direction="scheduled",
                person=person, subject=item.get("title"), preview=None, unread=None,
                by_name=by_name, by_email=by_email, confidence="high" if person.get("email") else "medium",
            ))
    return out


def _dedupe(events: list[dict]) -> list[dict]:
    by_id = {event["id"]: event for event in events}
    return sorted(by_id.values(), key=lambda e: (e["observed_at"], e["id"]))


def _current_states(events: list[dict], baseline: list[dict], now: datetime) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        if event.get("contact_id"):
            grouped[event["contact_id"]].append(event)
    contacts = {c.get("id"): c for c in baseline}
    rows = []
    for contact_id, items in grouped.items():
        items.sort(key=lambda e: e["observed_at"])
        past = [e for e in items if _dt(e["observed_at"]) <= now]
        future_calendar = [e for e in items if e["direction"] == "scheduled" and _dt(e["observed_at"]) > now]
        last = past[-1] if past else items[-1]
        if future_calendar:
            state, reason = "scheduled", "A future calendar event is captured."
            confidence = "high"
        elif last["direction"] == "outbound":
            state, reason = "awaiting_them", "The latest observed communication is outbound."
            confidence = last["evidence_confidence"]
        elif last["direction"] == "inbound":
            state, reason = "awaiting_you_or_review", "The latest observed communication is inbound; content intent is not assumed."
            confidence = last["evidence_confidence"]
        else:
            state, reason, confidence = "unknown", "Evidence is insufficient to infer a response state.", "low"
        c = contacts.get(contact_id) or {}
        rows.append({
            "contact_id": contact_id,
            "name": c.get("name") or last.get("person_name"),
            "company": c.get("current_company"),
            "priority_class": _priority(c),
            "state": state,
            "state_reason": reason,
            "state_confidence": confidence,
            "last_interaction_at": last["observed_at"],
            "last_channel": last["channel"],
            "last_direction": last["direction"],
            "latest_subject": last.get("subject"),
            "latest_preview": last.get("preview"),
            "unread": last.get("unread"),
            "channels_seen": sorted({e["channel"] for e in items}),
            "future_meeting_at": min((e["observed_at"] for e in future_calendar), default=None),
        })
    rank = {"inner_circle": 0, "relationship_card": 1, "strategic_relationship": 2, "other": 3}
    rows.sort(key=lambda r: (rank.get(r["priority_class"], 9), r["last_interaction_at"]), reverse=False)
    return rows


def _health(source_counts: dict[str, int], fetched: dict[str, str | None], now: datetime) -> dict:
    previous = _read(HEALTH_PATH)
    history = list(previous.get("history") or [])[-13:]
    history.append({"run_at": now.isoformat(timespec="seconds"), "counts": source_counts})
    sources = {}
    for source, count in source_counts.items():
        earlier = [int(h.get("counts", {}).get(source, 0)) for h in history[:-1]]
        zero_streak = 0
        for h in reversed(history):
            if int(h.get("counts", {}).get(source, 0)) == 0:
                zero_streak += 1
            else:
                break
        prior_nonzero = any(n > 0 for n in earlier)
        fetched_at = _dt(fetched.get(source))
        age_hours = round((now - fetched_at).total_seconds() / 3600, 1) if fetched_at else None
        status = "healthy"
        issue = None
        if age_hours is None or age_hours > 48:
            status, issue = "stale", "Source has not refreshed within 48 hours."
        elif zero_streak >= 3 and prior_nonzero:
            status, issue = "warning", f"Zero events observed for {zero_streak} consecutive runs after prior activity."
        sources[source] = {"status": status, "event_count": count, "zero_streak": zero_streak,
                           "last_refreshed_at": fetched.get(source), "age_hours": age_hours, "issue": issue}
    return {"generated_at": now.isoformat(timespec="seconds"), "sources": sources, "history": history}


def build(*, now: datetime | None = None, write: bool = True) -> dict:
    now = now or datetime.now(timezone.utc)
    baseline = core.load_baseline()
    by_name, by_email = _contact_index(baseline)
    events, counts, fetched = [], {}, {}

    for path in sorted(core.INBOX_DIR.glob("email*.json")):
        payload = _read(path)
        source = f"email:{path.stem.replace('email_sent.', '').replace('email.', '')}"
        found = _email_events(path, payload, by_name, by_email)
        events.extend(found)
        counts[source] = counts.get(source, 0) + len(payload.get("threads") or [])
        prior_fetch = _dt(fetched.get(source))
        this_fetch = _dt(payload.get("fetched_at"))
        if this_fetch and (not prior_fetch or this_fetch > prior_fetch):
            fetched[source] = payload.get("fetched_at")
    li_path = core.INBOX_DIR / "linkedin.messages.json"
    li_payload = _read(li_path)
    li_events = _linkedin_events(li_payload, by_name, by_email)
    events.extend(li_events); counts["linkedin"] = len(li_payload.get("messages") or []); fetched["linkedin"] = li_payload.get("fetched_at")
    for path in sorted(core.INBOX_DIR.glob("calendar*.json")):
        payload = _read(path)
        source = f"calendar:{path.stem.replace('calendar.', '')}"
        found = _calendar_events(path, payload, by_name, by_email)
        events.extend(found); counts[source] = len(payload.get("events") or []); fetched[source] = payload.get("fetched_at")

    events = _dedupe(events)
    states = _current_states(events, baseline, now)
    health = _health(counts, fetched, now)
    ledger = {"schema_version": 1, "generated_at": now.isoformat(timespec="seconds"),
              "event_count": len(events), "events": events}
    recent_attention_cutoff = now.timestamp() - (14 * 86400)
    state_doc = {"generated_at": now.isoformat(timespec="seconds"), "contact_count": len(states),
                 "states": states,
                 "attention": [
                     r for r in states
                     if r["state"] == "awaiting_you_or_review"
                     and r["priority_class"] != "other"
                     and _dt(r["last_interaction_at"]).timestamp() >= recent_attention_cutoff
                 ]}
    if write:
        LEDGER_PATH.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        HEALTH_PATH.write_text(json.dumps(health, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"ok": True, "event_count": len(events), "contact_states": len(states),
            "attention_count": len(state_doc["attention"]), "source_health": health["sources"],
            "paths": {"ledger": str(LEDGER_PATH), "state": str(STATE_PATH), "health": str(HEALTH_PATH)}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = build(write=not args.dry_run)
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
