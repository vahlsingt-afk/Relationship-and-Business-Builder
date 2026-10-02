#!/usr/bin/env python3
"""Capture LinkedIn message-notification metadata from Todd's personal Gmail.

This is the near-real-time complement to LinkedIn's delayed data export.  It
queries all Gmail mail, explicitly including archived messages and Trash,
keeps only probable one-to-one LinkedIn message notifications, and merges
those inbound interaction markers into ``inbox/linkedin.messages.json``.

The notification path can establish who contacted Todd and when.  It cannot
prove that Todd replied inside LinkedIn; that remains ``response_unknown``
until a LinkedIn export or an operator-triggered inbox review supplies
outbound evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_google
import linkedin_messaging
import rb_core as core


ACCOUNT_ID = "personal"
EXPECTED_EMAIL = "vahlsingt@gmail.com"
QUERY = (
    "newer_than:{days}d "
    "(from:messages-noreply@linkedin.com OR "
    "from:messaging-digest-noreply@linkedin.com)"
)

_NON_MESSAGE = re.compile(
    r"data archive|archive is ready|security|verification|password|"
    r"job alert|new jobs|newsletter|invitation|connection request|"
    r"people you may know|profile views|post impressions",
    re.I,
)
_MESSAGE_SIGNAL = re.compile(
    r"sent you (?:a )?message|new message from|messaged you|"
    r"replied to your message|has replied|conversation with|"
    r"you have (?:a )?new message",
    re.I,
)


def _clean(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"[\u034f\u200b-\u200f\ufeff]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _iso_date(raw: object) -> str | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(str(raw))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError):
        return None


def _person_name(thread: dict) -> str | None:
    sender = thread.get("last_message_from") or {}
    display = _clean(sender.get("name"))
    if display and display.lower() != "linkedin":
        display = re.sub(r"\s+via LinkedIn$", "", display, flags=re.I).strip()
        if display and display.lower() != "linkedin":
            return display
    text = " ".join((_clean(thread.get("subject")), _clean(thread.get("snippet"))))
    patterns = (
        r"^(?P<name>.+?)\s+sent you (?:a )?message\b",
        r"\bnew message from\s+(?P<name>[^:|–—]+)",
        r"^(?P<name>.+?)\s+(?:messaged you|replied to your message)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            name = _clean(match.group("name")).strip(" :-–—")
            if name:
                return name
    return None


def notification_to_message(thread: dict) -> dict | None:
    subject = _clean(thread.get("subject"))
    snippet = _clean(thread.get("snippet"))
    combined = f"{subject} {snippet}".strip()
    if not combined or _NON_MESSAGE.search(combined):
        return None
    sender_email = _clean((thread.get("last_message_from") or {}).get("email")).lower()
    if sender_email not in {
        "messages-noreply@linkedin.com",
        "messaging-digest-noreply@linkedin.com",
    }:
        return None
    name = _person_name(thread)
    if not name or not _MESSAGE_SIGNAL.search(combined):
        return None
    thread_id = _clean(thread.get("thread_id"))
    date = _iso_date(thread.get("last_message_at"))
    if not thread_id or not date:
        return None
    return {
        "conversation_id": f"gmail-linkedin:{thread_id}",
        "conversation_title": name,
        "from": {"name": name, "profile_url": None, "is_self": False},
        "to": [{"name": "Todd Vahlsing", "profile_url": "https://www.linkedin.com/in/toddvahlsing"}],
        "date": date,
        "subject": subject or None,
        "content": snippet[:500],
        "folder": "INBOX",
        "direction": "inbound",
        "response_status": "unknown",
        "unread": bool(thread.get("unread")),
        "gmail_labels": list(thread.get("labels") or []),
        "source_ref": f"gmail_thread:{thread_id}",
        "source": "linkedin_gmail_notification",
    }


def extract_messages(payload: dict) -> list[dict]:
    messages = []
    for thread in payload.get("threads") or []:
        message = notification_to_message(thread)
        if message:
            messages.append(message)
    return messages


def _dedupe_key(message: dict) -> str:
    ref = message.get("source_ref")
    if ref:
        return str(ref)
    raw = "|".join((
        str((message.get("from") or {}).get("name") or "").lower(),
        str(message.get("date") or ""),
        str(message.get("subject") or "").lower(),
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def merge_messages(existing: dict, incoming: list[dict]) -> tuple[dict, int]:
    current = list(existing.get("messages") or [])
    seen = {_dedupe_key(message) for message in current}
    added = 0
    for message in incoming:
        key = _dedupe_key(message)
        if key in seen:
            continue
        current.append(message)
        seen.add(key)
        added += 1
    current.sort(key=lambda m: m.get("date") or "")
    sources = existing.get("sources") or [existing.get("source") or "unknown"]
    if "linkedin_gmail_notification" not in sources:
        sources.append("linkedin_gmail_notification")
    return {
        **existing,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "merged",
        "sources": sorted(set(sources)),
        "messages": current,
    }, added


def run(*, days: int, confirm: bool, no_consent: bool) -> dict:
    payload = fetch_google.fetch_email(
        QUERY.format(days=days),
        limit=250,
        account_id=ACCOUNT_ID,
        expected_email=EXPECTED_EMAIL,
        allow_consent=not no_consent,
        include_spam_trash=True,
    )
    incoming = extract_messages(payload)
    existing = linkedin_messaging.load_inbox()
    merged, added = merge_messages(existing, incoming)
    result = {
        "ok": True,
        "dry_run": not confirm,
        "gmail_threads_scanned": len(payload.get("threads") or []),
        "message_notifications_found": len(incoming),
        "new_interactions": added,
        "archived_only_found": sum(
            1 for m in incoming
            if "INBOX" not in (m.get("gmail_labels") or [])
            and "TRASH" not in (m.get("gmail_labels") or [])
            and "SPAM" not in (m.get("gmail_labels") or [])
        ),
        "not_in_inbox_found": sum(1 for m in incoming if "INBOX" not in (m.get("gmail_labels") or [])),
        "trash_found": sum(1 for m in incoming if "TRASH" in (m.get("gmail_labels") or [])),
        "response_status": "unknown_until_outbound_evidence",
        "path": str(linkedin_messaging.INBOX_PATH.relative_to(core.PROJECT_DIR)),
    }
    if confirm:
        linkedin_messaging.INBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
        if linkedin_messaging.INBOX_PATH.exists():
            core.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            snapshot = core.SNAPSHOTS_DIR / (
                f"{linkedin_messaging.INBOX_PATH.stem}.pre-notification-ingest-{stamp}"
                f"{linkedin_messaging.INBOX_PATH.suffix}"
            )
            shutil.copy2(linkedin_messaging.INBOX_PATH, snapshot)
        linkedin_messaging.INBOX_PATH.write_text(
            json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        overlay = linkedin_messaging.linkedin_message_overlay(inbox=merged)
        result["matched_contacts"] = (overlay.get("totals") or {}).get("matched_count", 0)
        result["last_touch_proposals"] = len(overlay.get("proposed_last_touch_updates") or [])
        result["priority_inbound_candidates"] = len(overlay.get("inbound_ri_candidates") or [])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--confirm", action="store_true", help="Persist new interaction markers.")
    parser.add_argument("--no-consent", action="store_true", help="Fail instead of opening OAuth consent.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = run(days=max(1, args.days), confirm=args.confirm, no_consent=args.no_consent)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        mode = "wrote" if args.confirm else "previewed"
        print(f"{mode} {result['new_interactions']} new LinkedIn interaction(s); "
              f"archived_only={result['archived_only_found']}; trash={result['trash_found']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
