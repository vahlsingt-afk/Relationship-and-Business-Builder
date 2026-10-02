#!/usr/bin/env python3
"""
outlook_gui_capture.py — GP (Global Payments) Outlook metadata capture via
computer-use screen reading.

RB-2026-09-16: accounts.yaml documents four independently-blocked live
integration paths for the global-payments account (Graph API OAuth assumed
blocked by tenant consent policy, ICS calendar-publish subscription died,
an Outlook-side auto-forward rule never delivered, AppleScript returns
empty on every query). This confirmed a fifth: New Outlook for Mac's email
list and calendar views also expose almost nothing through the standard
macOS Accessibility API (app_ax_find finds no rows), so a Claude session
with computer-use has to read the rendered screen visually (screenshot +
zoom) row by row — there is no way to script this extraction headlessly.
That live vision step happens in the calling session, NOT in this script.

This script is the second half only: given the metadata a session already
read off the screen (as structured JSON), it normalizes and merges it into
the SAME per-account files outlook_manual_ingest.py writes --
calendar.global-payments.json / email.global-payments.json /
email_sent.global-payments.json -- in the same shape
fetch_via_session.normalize_calendar()/normalize_email() produce, so
rb_core.load_calendar()/load_email() and every downstream consumer
(email_overlay.py, thread_promotion.py, daily_brief.py, ...) picks this up
with zero changes. Deliberately metadata-only per the RB Phase 1
Metadata-First Connector convention (system/scripts/source_permission.py):
email threads never carry real snippet/body text, only
sender/subject/timestamp/read-state -- see `content_access` on each
written thread.

Row input shapes (what a computer-use session should produce from reading
the screen):

    email row (inbox): {
        "counterpart_name": "Kristen Gage",    # sender display name as shown
        "counterpart_email": "k.gage@x.com",   # only if visible in the list; else null
        "subject": "Re: FSTEC: Global Payments...",
        "received_at": "2026-09-15T14:32:00-04:00",  # ISO 8601, best-effort from the day-group + time shown
        "unread": false
    }
    email row (sent): {
        "counterpart_name": "Scott Welvaert",  # RECIPIENT display name -- Outlook's Sent list
        "counterpart_email": null,             # shows who you sent TO, not who sent it (that's always Todd)
        "subject": "Accepted: Subway RFP - Status Call",
        "sent_at": "2026-09-15T06:50:00-04:00"
    }

    calendar row: {
        "title": "GP - Maggie/Todd Meeting",
        "date": "2026-09-16",
        "start": "2026-09-16T13:00:00-04:00",
        "end": "2026-09-16T13:30:00-04:00",   # optional
        "location": null                       # optional
    }

CLI:
    python3 outlook_gui_capture.py --stage --in capture.json
    python3 outlook_gui_capture.py --scan
    python3 outlook_gui_capture.py --ingest-new --confirm
    python3 outlook_gui_capture.py --email --mailbox inbox --in rows.json
    python3 outlook_gui_capture.py --email --mailbox sent --in rows.json
    python3 outlook_gui_capture.py --calendar --in rows.json --window-days 7
    python3 outlook_gui_capture.py --smoke
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402

ACCOUNT_ID = "global-payments"
SELF_EMAIL = "tv74852@globalpayments.com"  # accounts.yaml global-payments entry
SELF_NAME = "Todd Vahlsing"
MIRROR_MAP_PATH = core.CACHE_DIR / "gp_calendar_mirror_map.json"
CAPTURE_DIR = core.INBOX_DIR / "outlook_gui_captures"
CAPTURE_MANIFEST_PATH = core.CACHE_DIR / "outlook_gui_capture_manifest.json"


# ---------------------------------------------------------------------------
# Small local helpers (mirrors outlook_manual_ingest.py's convention of a
# self-contained script rather than growing rb_core's shared surface)
# ---------------------------------------------------------------------------

def _stable_id(*parts: str) -> str:
    joined = "|".join(p or "" for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _read_json_safe(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _merge_by_key(existing: list[dict], new_items: list[dict], key: str) -> list[dict]:
    by_key = {item[key]: item for item in existing if item.get(key)}
    no_key = [item for item in existing if not item.get(key)]
    for item in new_items:
        if item.get(key):
            by_key[item[key]] = item
    return no_key + list(by_key.values())


def _load_rows(args: argparse.Namespace) -> list[dict]:
    if args.in_file:
        raw = Path(args.in_file).read_text()
    else:
        raw = sys.stdin.read()
    data = json.loads(raw)
    if isinstance(data, dict):
        data = data.get("rows") or data.get("items") or []
    if not isinstance(data, list):
        raise ValueError("input must be a JSON list of rows, or an object with a rows/items list")
    return data


def _load_json_input(in_file: str | None) -> Any:
    raw = Path(in_file).read_text() if in_file else sys.stdin.read()
    return json.loads(raw)


def _validate_capture(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("capture input must be a JSON object")
    account_id = payload.get("account_id") or ACCOUNT_ID
    if account_id != ACCOUNT_ID:
        raise ValueError(f"capture account_id must be {ACCOUNT_ID!r}")
    clean = {
        "schema_version": 1,
        "captured_at": payload.get("captured_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "account_id": ACCOUNT_ID,
        "source": "outlook_computer_use",
        "window_days": int(payload.get("window_days") or 7),
    }
    for key in ("inbox", "sent", "calendar"):
        rows = payload.get(key) or []
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"capture {key!r} must be a list of objects")
        clean[key] = rows
    if not any(clean[key] for key in ("inbox", "sent", "calendar")):
        raise ValueError("capture contains no inbox, sent, or calendar rows")
    return clean


def stage_capture(payload: Any, *, dry_run: bool = False, capture_dir: Path | None = None) -> dict:
    """Validate and atomically stage metadata for a later RB sweep."""
    clean = _validate_capture(payload)
    capture_dir = capture_dir or CAPTURE_DIR
    digest = hashlib.sha256(json.dumps(clean, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    stamp = "".join(ch for ch in clean["captured_at"] if ch.isdigit())[:14]
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    target = capture_dir / f"gp-outlook-{stamp}-{digest}.json"
    if not dry_run:
        capture_dir.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(clean, indent=2) + "\n")
        tmp.replace(target)
    return {
        "ok": True,
        "staged": not dry_run,
        "file": str(target),
        "counts": {key: len(clean[key]) for key in ("inbox", "sent", "calendar")},
        "dry_run": dry_run,
    }


def scan_staged(*, capture_dir: Path | None = None, manifest_path: Path | None = None) -> dict:
    capture_dir = capture_dir or CAPTURE_DIR
    manifest_path = manifest_path or CAPTURE_MANIFEST_PATH
    manifest = _read_json_safe(manifest_path)
    processed = set((manifest.get("processed") or {}).keys())
    files = sorted(capture_dir.glob("*.json")) if capture_dir.exists() else []
    return {
        "ok": True,
        "capture_dir": str(capture_dir),
        "total_files": len(files),
        "pending": [str(path) for path in files if path.name not in processed],
        "processed_count": len(processed),
    }


def ingest_staged(*, confirm: bool, dry_run: bool = False, trigger_cascade: bool = False,
                  capture_dir: Path | None = None, manifest_path: Path | None = None) -> dict:
    if not confirm and not dry_run:
        raise ValueError("--ingest-new requires --confirm (or use --dry-run)")
    capture_dir = capture_dir or CAPTURE_DIR
    manifest_path = manifest_path or CAPTURE_MANIFEST_PATH
    manifest = _read_json_safe(manifest_path) or {"processed": {}}
    manifest.setdefault("processed", {})
    pending = [Path(path) for path in scan_staged(
        capture_dir=capture_dir, manifest_path=manifest_path
    )["pending"]]
    results = []
    for path in pending:
        payload = _validate_capture(json.loads(path.read_text()))
        item = {"file": str(path), "captured_at": payload["captured_at"], "results": []}
        if payload["inbox"]:
            item["results"].append(ingest_email(payload["inbox"], "inbox", dry_run=dry_run))
        if payload["sent"]:
            item["results"].append(ingest_email(payload["sent"], "sent", dry_run=dry_run))
        if payload["calendar"]:
            item["results"].append(ingest_calendar(
                payload["calendar"], window_days=payload["window_days"], dry_run=dry_run
            ))
        results.append(item)
        if not dry_run:
            manifest["processed"][path.name] = {
                "captured_at": payload["captured_at"],
                "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "counts": {key: len(payload[key]) for key in ("inbox", "sent", "calendar")},
            }
    if not dry_run and results:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    # RB-DEFECT-2026-09-18: a successful capture used to stop right here --
    # raw files written, nothing downstream ever told. Any real row written
    # (parsed > 0 on at least one sub-result) now triggers the same cascade
    # the scheduled pipeline eventually runs, immediately, instead of
    # waiting for the next 4-5 AM LaunchAgent (and even then landing after
    # that run's own meeting-prep/loop evaluation already executed against
    # stale data -- see post_capture_cascade.py's module docstring).
    #
    # trigger_cascade defaults False so this function stays a pure staging/
    # merge operation for in-process callers and tests (it spawns several
    # real subprocesses -- refresh_sources.py, meeting_prep.py, daily_brief.py,
    # ... -- which no unit test of the merge logic itself should pay for).
    # The CLI (--ingest-new --confirm, the real invocation path for both a
    # live manual-capture session and the scheduled pipeline step) opts in
    # explicitly in main() below.
    cascade_receipt = None
    wrote_any_rows = trigger_cascade and not dry_run and any(
        int((sub_result or {}).get("parsed") or 0) > 0
        for item in results
        for sub_result in item.get("results") or []
    )
    if wrote_any_rows:
        try:
            import post_capture_cascade
            cascade_receipt = post_capture_cascade.run_cascade(trigger="gp_outlook_manual_capture")
        except Exception as exc:  # noqa: BLE001
            cascade_receipt = {"ok": False, "error": f"cascade failed to run: {exc}"}

    return {
        "ok": True, "pending_found": len(pending), "ingested": len(results),
        "results": results, "dry_run": dry_run, "manifest": str(manifest_path),
        "cascade": cascade_receipt,
    }


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def normalize_email_rows(rows: list[dict], mailbox: str) -> list[dict]:
    """Each list-view row becomes its own single-message thread -- Outlook's
    list view shows individual messages, not full Gmail-style threads, and
    we have no thread id to group by (same convention as
    outlook_manual_ingest.py's .eml handling, which is also one-message-
    per-thread).

    A row's `counterpart_name`/`counterpart_email` is whoever Outlook's list
    view displays -- for an inbox row that's the actual sender, but for a
    SENT row Outlook displays the recipient (Todd is always the sender of
    his own Sent items). Map onto last_message_from/last_message_to
    accordingly so email_overlay.py's sent-followup detection (which keys
    off last_message_from being in the operator's own self-email set, per
    accounts.yaml) sees the correct direction rather than every sent item
    looking like it was received from its own recipient.
    """
    threads_out = []
    for row in rows:
        counterpart_name = (row.get("counterpart_name") or row.get("sender_name") or "").strip() or None
        counterpart_email = (row.get("counterpart_email") or row.get("sender_email") or "").strip() or None
        subject = (row.get("subject") or "").strip()
        at = row.get("received_at") or row.get("sent_at") or ""
        unread = bool(row.get("unread")) if mailbox == "inbox" else False
        # Outlook's list view often shows date-only ("Yesterday") with no visible
        # clock time. When two DISTINCT messages share the same counterpart +
        # subject + date (e.g. two separate replies in a back-and-forth), don't
        # invent a fake time to tell them apart -- that would look like real
        # precision we don't have. `occurrence` lets the calling session say
        # explicitly "this is the Nth one I saw", which is honest about what it is.
        occurrence = row.get("occurrence")

        if mailbox == "sent":
            last_message_from = {"email": SELF_EMAIL, "name": SELF_NAME}
            last_message_to = [{"email": counterpart_email, "name": counterpart_name}] if (counterpart_email or counterpart_name) else []
        else:
            last_message_from = {"email": counterpart_email, "name": counterpart_name}
            last_message_to = []

        thread_id = "outlook-gp-" + _stable_id(
            mailbox, counterpart_email or counterpart_name or "", subject, at, str(occurrence or "")
        )
        threads_out.append({
            "thread_id": thread_id,
            "subject": subject or None,
            "last_message_at": at or None,
            "last_message_from": last_message_from,
            "last_message_to": last_message_to,
            "labels": ["UNREAD"] if unread else [],
            "unread": unread,
            "snippet": None,
            "message_count": 1,
            "source": "outlook_gui_capture",
            "content_access": "metadata_only",
            "mailbox": mailbox,
        })
    return threads_out


def ingest_email(rows: list[dict], mailbox: str, *, dry_run: bool = False) -> dict:
    if mailbox not in ("inbox", "sent"):
        raise ValueError(f"mailbox must be 'inbox' or 'sent', got {mailbox!r}")

    normalized = normalize_email_rows(rows, mailbox)
    target = core.email_path_for(ACCOUNT_ID) if mailbox == "inbox" else core.email_sent_path_for(ACCOUNT_ID)
    existing = _read_json_safe(target)
    merged = _merge_by_key(existing.get("threads") or [], normalized, "thread_id")

    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "threads": merged,
        }, indent=2) + "\n")

    return {
        "ok": True, "kind": "email", "mailbox": mailbox, "file": str(target),
        "rows_in": len(rows), "parsed": len(normalized), "total_after_merge": len(merged),
        "dry_run": dry_run,
    }


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

def normalize_calendar_rows(rows: list[dict]) -> list[dict]:
    events_out = []
    for row in rows:
        title = (row.get("title") or "").strip()
        date = row.get("date") or ""
        start = row.get("start") or date or None
        end = row.get("end")
        location = row.get("location")

        event_id = "outlook-gp-cal-" + _stable_id(date, start or "", title)
        events_out.append({
            "id": event_id,
            "title": title or None,
            "start": start,
            "end": end,
            "location": location,
            "description": None,
            "attendees": [],
            "organizer_email": None,
            "html_link": None,
            "source": "outlook_gui_capture",
            "content_access": "metadata_only",
        })
    return events_out


def ingest_calendar(rows: list[dict], *, window_days: int = 7, dry_run: bool = False) -> dict:
    """Merge captured GP calendar rows into calendar.global-payments.json
    and return an added/removed diff against the prior snapshot, scoped to
    the [today, today+window_days) date window this capture covers -- an
    event outside that window disappearing just means we didn't look at it
    this run, not that it was cancelled.
    """
    normalized = normalize_calendar_rows(rows)
    target = core.calendar_path_for(ACCOUNT_ID)
    existing = _read_json_safe(target)
    existing_events = existing.get("events") or []

    today = datetime.now(timezone.utc).date()
    window_end = today + timedelta(days=window_days)

    def _in_window(ev: dict) -> bool:
        raw = (ev.get("start") or "")[:10]
        try:
            d = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            return False
        return today <= d < window_end

    prior_in_window = {ev["id"]: ev for ev in existing_events if ev.get("id") and _in_window(ev)}
    new_in_window = {ev["id"]: ev for ev in normalized if ev.get("id")}

    added = [ev for eid, ev in new_in_window.items() if eid not in prior_in_window]
    removed = [ev for eid, ev in prior_in_window.items() if eid not in new_in_window]

    merged = _merge_by_key(existing_events, normalized, "id")

    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "events": merged,
        }, indent=2) + "\n")

    return {
        "ok": True, "kind": "calendar", "file": str(target),
        "rows_in": len(rows), "parsed": len(normalized), "total_after_merge": len(merged),
        "window_days": window_days,
        "added": added,
        "removed": removed,
        "dry_run": dry_run,
    }


# ---------------------------------------------------------------------------
# Google Calendar mirror map (id bookkeeping only -- the actual
# create/delete calls happen in the calling session via the Calendar MCP)
# ---------------------------------------------------------------------------

def load_mirror_map() -> dict:
    return _read_json_safe(MIRROR_MAP_PATH) or {"mirrored": {}}


def save_mirror_map(m: dict) -> None:
    MIRROR_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    MIRROR_MAP_PATH.write_text(json.dumps(m, indent=2) + "\n")


def record_mirror(gp_event_id: str, google_event_id: str, title: str, start: str | None) -> None:
    m = load_mirror_map()
    m.setdefault("mirrored", {})[gp_event_id] = {
        "google_event_id": google_event_id,
        "title": title,
        "start": start,
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    save_mirror_map(m)


def forget_mirror(gp_event_id: str) -> dict | None:
    m = load_mirror_map()
    entry = (m.get("mirrored") or {}).pop(gp_event_id, None)
    if entry is not None:
        save_mirror_map(m)
    return entry


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def _smoke() -> bool:
    errors: list[str] = []

    email_rows = [
        {"sender_name": "Kristen Gage", "sender_email": "k.gage@x.com",
         "subject": "Re: FSTEC follow-up", "received_at": "2026-09-15T14:32:00-04:00", "unread": False},
        {"sender_name": None, "sender_email": "digest@globalpay.example",
         "subject": "End User Digest", "received_at": "2026-09-15T08:26:00-04:00", "unread": True},
    ]
    normalized = normalize_email_rows(email_rows, "inbox")
    if len(normalized) != 2:
        errors.append("normalize_email_rows: expected 2 threads")
    if any(t.get("snippet") is not None for t in normalized):
        errors.append("normalize_email_rows: snippet must stay None (metadata_only)")
    if not any(t["unread"] for t in normalized):
        errors.append("normalize_email_rows: unread flag lost")
    ids = {t["thread_id"] for t in normalized}
    if len(ids) != 2:
        errors.append("normalize_email_rows: thread_id collision")
    # Re-running the same rows must produce the SAME thread_ids (idempotent dedup key).
    normalized_again = normalize_email_rows(email_rows, "inbox")
    if {t["thread_id"] for t in normalized_again} != ids:
        errors.append("normalize_email_rows: thread_id not stable across re-runs")

    # Sent rows: the on-screen name is the RECIPIENT, not the sender -- Todd is
    # always the sender of his own Sent items.
    sent_rows = [{"counterpart_name": "Scott Welvaert", "counterpart_email": None,
                  "subject": "Accepted: Subway RFP - Status Call", "sent_at": "2026-09-15T06:50:00-04:00"}]
    normalized_sent = normalize_email_rows(sent_rows, "sent")
    if normalized_sent[0]["last_message_from"]["email"] != SELF_EMAIL:
        errors.append("normalize_email_rows(sent): last_message_from must be the operator's own address")
    if normalized_sent[0]["last_message_to"][0]["name"] != "Scott Welvaert":
        errors.append("normalize_email_rows(sent): counterpart must land in last_message_to")
    if normalized_sent[0]["unread"] is not False:
        errors.append("normalize_email_rows(sent): sent items are never 'unread'")

    cal_rows = [
        {"title": "GP - Maggie/Todd Meeting", "date": "2026-09-16",
         "start": "2026-09-16T13:00:00-04:00", "end": "2026-09-16T13:30:00-04:00"},
    ]
    normalized_cal = normalize_calendar_rows(cal_rows)
    if len(normalized_cal) != 1 or not normalized_cal[0]["id"].startswith("outlook-gp-cal-"):
        errors.append("normalize_calendar_rows: unexpected shape")

    if errors:
        for e in errors:
            print(f"  FAIL: {e}")
        return False
    print("outlook_gui_capture smoke: all checks passed")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--email", action="store_true")
    group.add_argument("--calendar", action="store_true")
    group.add_argument("--stage", action="store_true")
    group.add_argument("--scan", action="store_true")
    group.add_argument("--ingest-new", action="store_true")
    group.add_argument("--smoke", action="store_true")
    p.add_argument("--mailbox", choices=["inbox", "sent"], help="Required with --email")
    p.add_argument("--in", dest="in_file", help="Path to JSON rows file (default: stdin)")
    p.add_argument("--window-days", type=int, default=7)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--confirm", action="store_true")
    args = p.parse_args()

    if args.smoke:
        return 0 if _smoke() else 1

    if args.stage:
        result = stage_capture(_load_json_input(args.in_file), dry_run=args.dry_run)
    elif args.scan:
        result = scan_staged()
    elif args.ingest_new:
        result = ingest_staged(confirm=args.confirm, dry_run=args.dry_run, trigger_cascade=True)
    elif args.email:
        if not args.mailbox:
            p.error("--email requires --mailbox inbox|sent")
        rows = _load_rows(args)
        result = ingest_email(rows, args.mailbox, dry_run=args.dry_run)
    else:
        rows = _load_rows(args)
        result = ingest_calendar(rows, window_days=args.window_days, dry_run=args.dry_run)

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
