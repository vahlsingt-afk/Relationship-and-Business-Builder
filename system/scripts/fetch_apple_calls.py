#!/usr/bin/env python3
"""
fetch_apple_calls.py — read macOS Call History into calls.json.

The Call History database (Core Data SQLite) is at
~/Library/Application Support/CallHistoryDB/CallHistory.storedata.
Captures phone calls (via iPhone Continuity) and FaceTime calls.

Requires Full Disk Access on macOS, same as fetch_apple_messages.py.

Usage:
    python3 fetch_apple_calls.py
    python3 fetch_apple_calls.py --days 90
    python3 fetch_apple_calls.py --db /path/to/CallHistory.storedata
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core

DEFAULT_CALL_DB = Path(os.path.expanduser(
    "~/Library/Application Support/CallHistoryDB/CallHistory.storedata"
))

# CallHistory stores ZDATE as seconds since 2001-01-01 UTC.
APPLE_EPOCH_OFFSET = 978307200


def apple_seconds_to_iso(apple_s: float | None) -> str | None:
    if apple_s is None or apple_s <= 0:
        return None
    try:
        return datetime.fromtimestamp(apple_s + APPLE_EPOCH_OFFSET,
                                       tz=timezone.utc).isoformat(timespec="seconds")
    except (ValueError, OSError):
        return None


SQL = """
SELECT
  Z_PK            AS pk,
  ZADDRESS        AS handle,
  ZDATE           AS apple_date,
  ZDURATION       AS duration_seconds,
  ZORIGINATED     AS is_outbound,
  ZANSWERED       AS is_answered,
  ZSERVICE_PROVIDER AS service
FROM ZCALLRECORD
WHERE ZDATE >= ?
ORDER BY ZDATE DESC
LIMIT ?;
"""


def fetch(db_path: Path, days: int, limit: int) -> dict:
    cutoff_s = (datetime.now(tz=timezone.utc) - timedelta(days=days)).timestamp() - APPLE_EPOCH_OFFSET
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        cur = conn.cursor()
        cur.execute(SQL, (cutoff_s, limit))
        rows = cur.fetchall()
    finally:
        conn.close()

    events = []
    for pk, handle, apple_date, duration_s, is_outbound, is_answered, service in rows:
        if not handle:
            continue
        if is_outbound:
            direction = "outbound"
        else:
            direction = "inbound" if is_answered else "missed"
        if service and "FaceTime" in service:
            service_label = "FaceTime"
        else:
            service_label = "Phone"
        events.append({
            "id": f"call-{pk}",
            "handle": handle,
            "service": service_label,
            "direction": direction,
            "at": apple_seconds_to_iso(apple_date),
            "duration_seconds": int(duration_s) if duration_s else 0,
        })

    return {
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "source": "apple_callhistory",
        "events": events,
        "window_days": days,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=str(DEFAULT_CALL_DB),
                   help="Path to CallHistory.storedata.")
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--limit", type=int, default=10000)
    p.add_argument("--out", default=None)
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        sys.stderr.write(
            f"ERROR: CallHistory.storedata not found at {db_path}.\n"
            "  - Requires macOS with FaceTime/Phone via Continuity from iPhone.\n"
            "  - Grant Full Disk Access to your Terminal.\n"
        )
        return 2

    try:
        payload = fetch(db_path, args.days, args.limit)
    except sqlite3.OperationalError as e:
        sys.stderr.write(
            f"ERROR opening CallHistory.storedata: {e}\n"
            "  - Most often this means Full Disk Access is not granted.\n"
        )
        return 2

    target = Path(args.out) if args.out else core.INBOX_DIR / "calls.json"
    core.INBOX_DIR.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, default=str))
    rel = target.relative_to(core.PROJECT_DIR) if target.is_relative_to(core.PROJECT_DIR) else target
    print(f"Wrote {len(payload['events'])} call events to {rel} "
          f"(window {args.days}d).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
