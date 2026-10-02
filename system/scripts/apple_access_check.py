#!/usr/bin/env python3
"""
apple_access_check.py - verify local Apple Messages/SMS and call-log access.

This is a read-only preflight for Apple users. It checks whether the local
Messages chat.db and CallHistory database exist and can be opened by the same
Python process that will run the morning pipeline. Messages includes iMessage
and SMS forwarded from iPhone when Text Message Forwarding is enabled.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


CHAT_DB = Path(os.path.expanduser("~/Library/Messages/chat.db"))
CALL_DB = Path(os.path.expanduser("~/Library/Application Support/CallHistoryDB/CallHistory.storedata"))


def _check_sqlite(path: Path, sql: str) -> dict:
    row = {
        "path": str(path),
        "exists": path.exists(),
        "readable": False,
        "count": None,
        "status": "missing",
        "error": None,
    }
    if not path.exists():
        row["error"] = "database not found"
        return row
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            count = conn.execute(sql).fetchone()[0]
        finally:
            conn.close()
        row.update({
            "readable": True,
            "count": int(count or 0),
            "status": "ready",
            "error": None,
        })
    except sqlite3.OperationalError as exc:
        row["status"] = "permission_denied"
        row["error"] = str(exc)
    return row


def check() -> dict:
    messages = _check_sqlite(CHAT_DB, "select count(*) from message")
    calls = _check_sqlite(CALL_DB, "select count(*) from ZCALLRECORD")
    ok = messages["status"] == "ready" and calls["status"] == "ready"
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "platform": "macOS",
        "ok": ok,
        "messages_sms": messages,
        "phone_call_log": calls,
        "recovery": {
            "full_disk_access": (
                "System Settings -> Privacy & Security -> Full Disk Access: add the app/process "
                "that runs RB, plus Terminal while testing."
            ),
            "sms_forwarding": (
                "On iPhone: Settings -> Messages -> Text Message Forwarding: enable this Mac. "
                "SMS will then appear in Messages.app and be captured from chat.db."
            ),
            "phone_calls": (
                "On iPhone and Mac: use the same Apple ID for FaceTime/iCloud and enable "
                "Calls on Other Devices / Calls From iPhone. Calls then appear in the local "
                "CallHistory database."
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args()
    payload = check()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Apple access: {'PASS' if payload['ok'] else 'FAIL'}")
        for label, key in [("Messages/SMS", "messages_sms"), ("Phone call log", "phone_call_log")]:
            row = payload[key]
            print(f"  {label}: {row['status']} ({row['path']})")
            if row.get("count") is not None:
                print(f"    rows: {row['count']}")
            if row.get("error"):
                print(f"    error: {row['error']}")
        if not payload["ok"]:
            print("\nRecovery:")
            for value in payload["recovery"].values():
                print(f"  - {value}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
