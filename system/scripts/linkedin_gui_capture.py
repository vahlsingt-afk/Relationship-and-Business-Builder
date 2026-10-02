#!/usr/bin/env python3
"""Validate and merge a user-triggered LinkedIn inbox-list capture into RB."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import linkedin_messaging
from linkedin_notification_ingest import merge_messages


CENTRAL = ZoneInfo("America/Chicago")


def _event_time(display: str, captured_at: str) -> str | None:
    try:
        captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00")).astimezone(CENTRAL)
    except (TypeError, ValueError):
        return None
    value = (display or "").strip()
    for fmt in ("%I:%M %p",):
        try:
            parsed = datetime.strptime(value, fmt)
            return captured.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0).astimezone(timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            pass
    for fmt in ("%b %d", "%b %d, %Y"):
        try:
            parsed = datetime.strptime(value, fmt)
            year = parsed.year if "%Y" in fmt else captured.year
            return datetime(year, parsed.month, parsed.day, 12, 0, tzinfo=CENTRAL).astimezone(timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            pass
    return None


def normalize_rows(payload: dict) -> list[dict]:
    captured_at = payload.get("captured_at") or datetime.now(timezone.utc).isoformat()
    out = []
    for row in payload.get("conversations") or []:
        name = re.sub(r"\s+", " ", str(row.get("name") or "")).strip()
        preview = re.sub(r"\s+", " ", str(row.get("preview") or "")).strip()
        if not name or not preview or row.get("sponsored"):
            continue
        date = _event_time(str(row.get("display_time") or ""), captured_at)
        if not date:
            continue
        self_last = preview.lower().startswith("you:")
        content = preview.split(":", 1)[1].strip() if ":" in preview else preview
        identity = hashlib.sha256(f"{name.lower()}|{date}|{preview.lower()}".encode()).hexdigest()[:20]
        out.append({
            "conversation_id": f"linkedin-gui:{name.lower()}",
            "conversation_title": name,
            "from": {
                "name": "Todd Vahlsing" if self_last else name,
                "profile_url": "https://www.linkedin.com/in/toddvahlsing" if self_last else None,
                "is_self": self_last,
            },
            "to": ([{"name": name, "profile_url": None}] if self_last else
                   [{"name": "Todd Vahlsing", "profile_url": "https://www.linkedin.com/in/toddvahlsing"}]),
            "date": date,
            "subject": f"LinkedIn conversation with {name}",
            "content": content[:500],
            "folder": "SENT" if self_last else "INBOX",
            "direction": "outbound" if self_last else "inbound",
            "response_status": "responded" if self_last else "awaiting_response_or_review",
            "unread": bool(row.get("unread")),
            "source": "linkedin_gui_capture",
            "source_ref": f"linkedin_gui:{identity}",
        })
    return out


def reconcile_with_existing(existing: dict, incoming: list[dict]) -> tuple[dict, list[dict], int]:
    """Enrich same-person/same-day notification markers instead of double-counting."""
    messages = list(existing.get("messages") or [])
    enriched = 0
    additions = []
    for candidate in incoming:
        direction = candidate.get("direction")
        other_name = (
            ((candidate.get("to") or [{}])[0].get("name") if direction == "outbound" else
             (candidate.get("from") or {}).get("name")) or ""
        ).lower()
        day = str(candidate.get("date") or "")[:10]
        match = None
        for current in reversed(messages):
            current_direction = current.get("direction")
            current_name = (
                ((current.get("to") or [{}])[0].get("name") if current_direction == "outbound" else
                 (current.get("from") or {}).get("name")) or ""
            ).lower()
            if current_direction == direction and current_name == other_name and str(current.get("date") or "")[:10] == day:
                match = current
                break
        if match is None:
            additions.append(candidate)
            continue
        match["content"] = candidate.get("content") or match.get("content")
        match["response_status"] = candidate.get("response_status")
        match["unread"] = candidate.get("unread")
        refs = list(match.get("corroborating_sources") or [])
        if candidate.get("source_ref") not in refs:
            refs.append(candidate.get("source_ref"))
        match["corroborating_sources"] = refs
        enriched += 1
    return {**existing, "messages": messages}, additions, enriched


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ingest", required=True, help="Staged LinkedIn inbox-list JSON.")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    source = Path(args.ingest)
    payload = json.loads(source.read_text(encoding="utf-8"))
    incoming = normalize_rows(payload)
    existing = linkedin_messaging.load_inbox()
    reconciled, additions, enriched = reconcile_with_existing(existing, incoming)
    merged, added = merge_messages(reconciled, additions)
    result = {
        "ok": True,
        "dry_run": not args.confirm,
        "rows_received": len(payload.get("conversations") or []),
        "rows_normalized": len(incoming),
        "new_interactions": added,
        "existing_interactions_enriched": enriched,
        "outbound_latest": sum(1 for m in incoming if m["direction"] == "outbound"),
        "inbound_latest": sum(1 for m in incoming if m["direction"] == "inbound"),
    }
    if args.confirm:
        linkedin_messaging.INBOX_PATH.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        overlay = linkedin_messaging.linkedin_message_overlay(inbox=merged)
        result["matched_contacts"] = (overlay.get("totals") or {}).get("matched_count", 0)
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
