#!/usr/bin/env python3
"""
fetch_apple_messages.py — read macOS Messages chat.db into messages.json.

Reads the local SQLite database the Messages app maintains on macOS.
Captures iMessage AND SMS-via-Continuity (when SMS Forwarding is enabled).
Writes a normalized event list to system/inbox/messages.json.

Requires:
    - macOS with Messages.app set up (the same as you've been using).
    - Full Disk Access granted to the process that runs this script
      (System Settings → Privacy & Security → Full Disk Access).
    - Python 3.10+ (sqlite3 is in the standard library).

Privacy:
    By default, only event metadata is captured: handle, direction,
    service, timestamp. Message TEXT is NOT recorded. Pass
    --include-snippets to opt in to truncated (80 char) snippet capture
    for the few cases where content-aware matching is useful.

    Output file lives under system/inbox/ which is git-ignored.

Usage:
    python3 fetch_apple_messages.py                       # default: 365 days, no snippets
    python3 fetch_apple_messages.py --days 90
    python3 fetch_apple_messages.py --include-snippets
    python3 fetch_apple_messages.py --db /path/to/chat.db # override location
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core

DEFAULT_CHAT_DB = Path(os.path.expanduser("~/Library/Messages/chat.db"))

# RB-DEFECT-032 — trusted-sender full-text capture.
#
# Root cause of the Jeff Coffland SMS miss: even with --include-snippets,
# message text is truncated to 80 chars before any extraction stage can see
# it — a material multi-sentence intelligence signal never had a chance to
# be evaluated. This is a privacy-bounded, ALLOWLIST-GATED expansion: full
# text is captured ONLY for senders RB already trusts as intelligence
# sources (not "capture everyone's texts"). The allowlist lives in a small,
# explicit, git-trackable config file (not the bulk baseline), so the user
# controls exactly who this applies to.
TRUSTED_SENDERS_PATH = core.SYSTEM_DIR / "sms_trusted_senders.json"


def _normalize_handle(handle: str | None) -> str:
    """Normalize a phone/email handle for allowlist matching.

    Phones: strip everything but digits, drop a leading country-code '1'
    (matches rb_core._normalize_phone's convention for US numbers).
    Emails: lowercase as-is.
    """
    if not handle:
        return ""
    h = handle.strip()
    if "@" in h:
        return h.lower()
    digits = "".join(ch for ch in h if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def load_sms_intelligence_config(path: Path = TRUSTED_SENDERS_PATH) -> dict:
    """Load the SMS intelligence config from sms_trusted_senders.json.

    Returns a dict with:
      mode: "all" — capture full_text for ALL inbound except exempt_handles
            "allowlist" — only capture for handles in trusted_handles (legacy)
      trusted_handles: set[str] — for mode="allowlist"
      exempt_handles: set[str] — for mode="all" (family/friends to skip)

    When the file does not exist, returns mode="allowlist" with empty trusted set
    (full-text capture disabled — safe default).

    Mode "all" is the production-intent model: every inbound message is an
    intelligence candidate; family/friends are explicitly exempted rather than
    intelligence sources explicitly opted in.
    """
    if not path.exists():
        return {"mode": "allowlist", "trusted_handles": set(), "exempt_handles": set()}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"mode": "allowlist", "trusted_handles": set(), "exempt_handles": set()}
    if not isinstance(data, dict):
        return {"mode": "allowlist", "trusted_handles": set(), "exempt_handles": set()}

    mode = data.get("mode", "allowlist")
    trusted_raw = data.get("trusted_handles") or []
    exempt_raw = data.get("exempt_handles") or []
    return {
        "mode": mode,
        "trusted_handles": {_normalize_handle(h) for h in trusted_raw if isinstance(h, str) and h.strip()},
        "exempt_handles": {_normalize_handle(h) for h in exempt_raw if isinstance(h, str) and h.strip()},
    }


# Keep legacy name as a thin wrapper for any code that calls it directly.
def load_trusted_senders(path: Path = TRUSTED_SENDERS_PATH) -> set[str]:
    cfg = load_sms_intelligence_config(path)
    return cfg["trusted_handles"]

# Apple stores message dates as nanoseconds since 2001-01-01 00:00:00 UTC.
# Unix epoch offset:
APPLE_EPOCH_OFFSET = 978307200


def _decode_attributed_body(blob: bytes | None) -> str | None:
    """Extract plain text from an NSKeyedArchiver attributedBody blob.

    macOS Sonoma/Sequoia stores iMessage body text in message.attributedBody
    (an NSAttributedString serialized via NSKeyedArchiver) when message.text
    is NULL. This is the primary cause of ~99% of SMS events having no
    full_text: the SQL only read message.text, which is NULL for modern iMessages.

    NSKeyedArchiver binary plist layout for NSAttributedString:
      $objects[0]  = "$null"
      $objects[1]  = the plain NSString value  ← what we want
      $objects[2+] = attributes dict, class refs, etc.
    """
    if not blob:
        return None
    try:
        plist = plistlib.loads(blob, fmt=plistlib.FMT_BINARY)
        objects = plist.get("$objects", [])
        # Index 1 is the NSString value in all known Apple Messages archives.
        if len(objects) > 1:
            candidate = objects[1]
            if isinstance(candidate, str) and candidate != "$null" and candidate.strip():
                return candidate
        # Fallback: first substantive non-class-name string in the object list.
        for obj in objects:
            if (isinstance(obj, str)
                    and obj not in ("$null", "")
                    and not obj.startswith("NS")
                    and obj.strip()):
                return obj
    except Exception:
        pass
    return None


def apple_ts_to_iso(apple_ns: int | None) -> str | None:
    if apple_ns is None or apple_ns <= 0:
        return None
    try:
        unix_seconds = apple_ns / 1_000_000_000 + APPLE_EPOCH_OFFSET
        return datetime.fromtimestamp(unix_seconds, tz=timezone.utc).isoformat(timespec="seconds")
    except (ValueError, OSError):
        return None


SQL = """
SELECT
  message.ROWID                                                AS msg_id,
  handle.id                                                    AS handle_text,
  COALESCE(handle.service, 'unknown')                          AS service,
  message.is_from_me                                           AS is_from_me,
  message.date                                                 AS apple_date,
  CASE WHEN message.text IS NOT NULL
            OR message.attributedBody IS NOT NULL THEN 1 ELSE 0 END  AS has_text,
  SUBSTR(COALESCE(message.text, ''), 1, 80)                    AS snippet,
  message.text                                                 AS full_text,
  message.attributedBody                                       AS attributed_body
FROM message
LEFT JOIN handle ON message.handle_id = handle.ROWID
WHERE message.date >= ?
ORDER BY message.date DESC
LIMIT ?;
"""


def fetch(db_path: Path, days: int, limit: int,
          include_snippets: bool, trusted_fulltext: bool = False) -> dict:
    cutoff_ns = int(
        (datetime.now(tz=timezone.utc) - timedelta(days=days)).timestamp() - APPLE_EPOCH_OFFSET
    ) * 1_000_000_000

    # Open read-only so we never corrupt the user's Messages database.
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        cur = conn.cursor()
        cur.execute(SQL, (cutoff_ns, limit))
        rows = cur.fetchall()
    finally:
        conn.close()

    cfg = load_sms_intelligence_config() if trusted_fulltext else {
        "mode": "allowlist", "trusted_handles": set(), "exempt_handles": set()
    }
    mode = cfg["mode"]
    trusted_handles = cfg["trusted_handles"]
    exempt_handles = cfg["exempt_handles"]

    events = []
    fulltext_count = 0
    for msg_id, handle_text, service, is_from_me, apple_date, has_text, snippet, full_text, attributed_body in rows:
        if not handle_text:
            continue

        # macOS Sonoma/Sequoia stores iMessage body in attributedBody when
        # message.text is NULL. Decode it here so the rest of the pipeline
        # sees a plain string regardless of which column held the content.
        resolved_text = full_text or _decode_attributed_body(attributed_body)

        norm_handle = _normalize_handle(handle_text)
        if mode == "all":
            # Opt-out model: all inbound messages are intelligence candidates;
            # family/friends are explicitly exempted via exempt_handles.
            capture_fulltext = (
                trusted_fulltext
                and not is_from_me
                and bool(resolved_text)
                and norm_handle not in exempt_handles
            )
            capture_reason = "all_inbound_except_exempt"
        else:
            # Allowlist (legacy / default): only explicitly listed handles.
            capture_fulltext = (
                trusted_fulltext
                and not is_from_me
                and bool(resolved_text)
                and norm_handle in trusted_handles
            )
            capture_reason = "trusted_intelligence_source"

        event = {
            "id": f"msg-{msg_id}",
            "handle": handle_text,
            "service": "iMessage" if service == "iMessage" else "SMS",
            "direction": "outbound" if is_from_me else "inbound",
            "at": apple_ts_to_iso(apple_date),
            "has_text": bool(resolved_text),
            "snippet": (snippet or (resolved_text[:80] if resolved_text and include_snippets else None)),
        }
        if capture_fulltext:
            event["full_text"] = resolved_text
            event["full_text_capture_reason"] = capture_reason
            fulltext_count += 1
        events.append(event)

    return {
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "source": "apple_messages",
        "events": events,
        "window_days": days,
        "include_snippets": include_snippets,
        "trusted_fulltext": trusted_fulltext,
        "fulltext_mode": mode,
        "trusted_fulltext_count": fulltext_count,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=str(DEFAULT_CHAT_DB),
                   help="Path to chat.db (default: ~/Library/Messages/chat.db)")
    p.add_argument("--days", type=int, default=365,
                   help="Look back this many days.")
    p.add_argument("--limit", type=int, default=20000,
                   help="Hard cap on message events (newest first).")
    p.add_argument("--include-snippets", action="store_true",
                   help="Capture 80-char snippets (default: metadata only).")
    p.add_argument("--trusted-fulltext", action="store_true",
                   help=("RB-DEFECT-032: capture FULL message text for inbound "
                         "messages from senders on the explicit allowlist in "
                         f"{TRUSTED_SENDERS_PATH.name} (default: off — opt-in only). "
                         "This is what lets material multi-sentence intelligence "
                         "signals from trusted contacts reach the mutation engine "
                         "instead of being truncated to an 80-char snippet."))
    p.add_argument("--out", default=None,
                   help="Output path (default: system/inbox/messages.json).")
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        sys.stderr.write(
            f"ERROR: chat.db not found at {db_path}.\n"
            "  - Run on a Mac with Messages.app set up.\n"
            "  - Grant Full Disk Access to your Terminal in System Settings.\n"
        )
        return 2

    try:
        payload = fetch(db_path, args.days, args.limit, args.include_snippets,
                        trusted_fulltext=args.trusted_fulltext)
    except sqlite3.OperationalError as e:
        sys.stderr.write(
            f"ERROR opening chat.db: {e}\n"
            "  - Most often this means Full Disk Access is not granted to the\n"
            "    process running this script. Open System Settings → Privacy &\n"
            "    Security → Full Disk Access, add Terminal (or your IDE).\n"
        )
        return 2

    target = Path(args.out) if args.out else core.INBOX_DIR / "messages.json"
    core.INBOX_DIR.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, default=str))
    rel = target.relative_to(core.PROJECT_DIR) if target.is_relative_to(core.PROJECT_DIR) else target
    print(f"Wrote {len(payload['events'])} message events to {rel} "
          f"(window {args.days}d, snippets={args.include_snippets}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
