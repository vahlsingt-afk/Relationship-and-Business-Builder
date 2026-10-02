#!/usr/bin/env python3
"""
outlook_manual_ingest.py — manual Outlook export ingester for the
Global Payments account (tv74852@globalpayments.com).

RB-DEFECT-2026-07-29: New Outlook for Mac has no local, readable data
store (checked live: its sandboxed container has caches/telemetry only,
no mail/calendar database) and no working legacy AppleScript bridge
(every account/message/event query returns empty even though the app
responds). A Microsoft Graph OAuth connector may also be blocked by
Global Payments' Entra ID tenant consent policy. Until one of those is
resolved, the practical path is: Todd drags individual calendar events
and emails out of Outlook to the Finder (both produce standard
.ics/.eml files) into `system/inbox/outlook_exports/`, and this script
parses + merges them into the same per-account files fetch_google.py
would have written — `calendar.global-payments.json` /
`email.global-payments.json` — so the existing account aggregation in
rb_core.py (`load_calendar`/`load_email`) picks them up with no
downstream changes.

Drop location watched:
    system/inbox/outlook_exports/      (.ics and .eml files)

CLI:
    python3 outlook_manual_ingest.py --scan
    python3 outlook_manual_ingest.py --ingest-new --dry-run
    python3 outlook_manual_ingest.py --ingest-new --confirm
    python3 outlook_manual_ingest.py --file <path> --dry-run
"""
from __future__ import annotations

import argparse
import email as email_lib
import email.policy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core  # noqa: E402
import fetch_via_session as fvs  # noqa: E402

ACCOUNT_ID = "global-payments"
EXPORTS_DIR = core.INBOX_DIR / "outlook_exports"
MANIFEST_PATH = core.CACHE_DIR / "outlook_manual_ingest_manifest.json"

# Best-effort mapping for the handful of continental-US Windows timezone
# names Outlook commonly writes into DTSTART;TZID=... . Unrecognized TZIDs
# fall back to a naive (no-offset) local timestamp — still usable for
# date-level brief logic, just not guaranteed hour-precise across a DST edge.
_TZID_OFFSETS = {
    "eastern standard time": "-05:00",
    "eastern daylight time": "-04:00",
    "central standard time": "-06:00",
    "central daylight time": "-05:00",
    "mountain standard time": "-07:00",
    "mountain daylight time": "-06:00",
    "pacific standard time": "-08:00",
    "pacific daylight time": "-07:00",
}

_PARTSTAT_MAP = {
    "ACCEPTED": "accepted",
    "DECLINED": "declined",
    "TENTATIVE": "tentative",
    "NEEDS-ACTION": "needsAction",
}


# ---------------------------------------------------------------------------
# .ics parsing -> raw Google-Calendar-shaped events for normalize_calendar()
# ---------------------------------------------------------------------------

def _unfold_ics(text: str) -> list[str]:
    """RFC 5545 line unfolding: a line starting with a space/tab continues
    the previous line."""
    raw_lines = text.replace("\r\n", "\n").split("\n")
    lines: list[str] = []
    for line in raw_lines:
        if line.startswith(" ") or line.startswith("\t"):
            if lines:
                lines[-1] += line[1:]
            continue
        lines.append(line)
    return [ln for ln in lines if ln.strip()]


def _split_ics_line(line: str) -> tuple[str, dict, str]:
    """Split 'NAME;P1=V1;P2=V2:value' into (name, {params}, value)."""
    if ":" not in line:
        return line.strip().upper(), {}, ""
    head, value = line.split(":", 1)
    parts = head.split(";")
    name = parts[0].strip().upper()
    params: dict[str, str] = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.strip().upper()] = v.strip()
    return name, params, value


def _parse_ics_datetime(value: str, params: dict) -> str:
    value = value.strip()
    if len(value) == 8 and value.isdigit():
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    if value.endswith("Z"):
        try:
            dt = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            return value
    try:
        dt = datetime.strptime(value, "%Y%m%dT%H%M%S")
    except ValueError:
        return value
    tzid = (params.get("TZID") or "").strip().lower()
    offset = _TZID_OFFSETS.get(tzid)
    return dt.isoformat() + offset if offset else dt.isoformat()


def _parse_cn_mailto(value: str, params: dict) -> dict:
    m = re.search(r"mailto:(.+)", value, re.IGNORECASE)
    email_addr = m.group(1).strip() if m else (value.strip() or None)
    return {"email": email_addr, "name": params.get("CN")}


def parse_ics(text: str) -> list[dict]:
    """Parse .ics VEVENT blocks into the raw shape
    `fetch_via_session.normalize_calendar()` expects."""
    events: list[dict] = []
    current: dict | None = None
    for line in _unfold_ics(text):
        stripped = line.strip()
        if stripped.upper() == "BEGIN:VEVENT":
            current = {"attendees": []}
            continue
        if stripped.upper() == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
            continue
        if current is None:
            continue
        name, params, value = _split_ics_line(line)
        if name == "UID":
            current["id"] = value.strip()
        elif name == "SUMMARY":
            current["summary"] = value.strip()
        elif name == "LOCATION":
            current["location"] = value.strip()
        elif name == "DESCRIPTION":
            current["description"] = value.strip()
        elif name == "DTSTART":
            current["start"] = {"dateTime": _parse_ics_datetime(value, params)}
        elif name == "DTEND":
            current["end"] = {"dateTime": _parse_ics_datetime(value, params)}
        elif name == "ORGANIZER":
            current["organizer"] = _parse_cn_mailto(value, params)
        elif name == "ATTENDEE":
            att = _parse_cn_mailto(value, params)
            att["responseStatus"] = _PARTSTAT_MAP.get((params.get("PARTSTAT") or "").upper(), "needsAction")
            current["attendees"].append(att)
    return events


# ---------------------------------------------------------------------------
# .eml parsing -> raw Gmail-thread-shaped dict for normalize_email()
# ---------------------------------------------------------------------------

def _extract_body_text(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                try:
                    return part.get_content()
                except Exception:
                    continue
        for part in msg.walk():
            if part.get_content_type() == "text/html" and not part.get_filename():
                try:
                    return part.get_content()
                except Exception:
                    continue
        return ""
    try:
        return msg.get_content()
    except Exception:
        return ""


def parse_eml(raw_bytes: bytes) -> dict:
    """Parse a single .eml file into a raw single-message "thread" dict.
    Manual drops are individual emails, not full Gmail-style threads, so
    each file becomes its own one-message thread keyed by Message-ID."""
    msg = email_lib.message_from_bytes(raw_bytes, policy=email.policy.default)
    message_id = (msg.get("Message-ID") or msg.get("Message-Id") or "").strip("<>")
    thread_id = message_id or hashlib.sha256(raw_bytes).hexdigest()[:16]
    to_hdr = msg.get("To") or ""
    to_recipients = [a.strip() for a in to_hdr.split(",") if a.strip()]
    body = _extract_body_text(msg)
    snippet = " ".join((body or "").split())[:300]

    return {
        "id": thread_id,
        "messages": [{
            "id": thread_id,
            "date": msg.get("Date") or "",
            "sender": msg.get("From") or "",
            "subject": msg.get("Subject") or "",
            "labelIds": [],
            "snippet": snippet,
            "toRecipients": to_recipients,
        }],
    }


# ---------------------------------------------------------------------------
# Merge into per-account calendar/email files
# ---------------------------------------------------------------------------

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


def ingest_ics_file(path: Path, *, dry_run: bool = False) -> dict:
    raw_events = parse_ics(path.read_text(encoding="utf-8", errors="replace"))
    normalized = fvs.normalize_calendar({"events": raw_events})
    for ev in normalized["events"]:
        ev["source"] = "outlook_manual_export"

    target = core.calendar_path_for(ACCOUNT_ID)
    existing = _read_json_safe(target)
    merged = _merge_by_key(existing.get("events") or [], normalized["events"], "id")

    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "events": merged,
        }, indent=2) + "\n")

    return {
        "ok": True, "kind": "calendar", "file": path.name,
        "parsed": len(normalized["events"]), "total_after_merge": len(merged),
    }


def ingest_eml_file(path: Path, *, dry_run: bool = False) -> dict:
    raw_thread = parse_eml(path.read_bytes())
    normalized = fvs.normalize_email({"threads": [raw_thread]})
    for th in normalized["threads"]:
        th["source"] = "outlook_manual_export"

    target = core.email_path_for(ACCOUNT_ID)
    existing = _read_json_safe(target)
    merged = _merge_by_key(existing.get("threads") or [], normalized["threads"], "thread_id")

    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "threads": merged,
        }, indent=2) + "\n")

    return {
        "ok": True, "kind": "email", "file": path.name,
        "parsed": len(normalized["threads"]), "total_after_merge": len(merged),
    }


def ingest_file(path: Path, *, dry_run: bool = False) -> dict:
    suffix = path.suffix.lower()
    if suffix == ".ics":
        return ingest_ics_file(path, dry_run=dry_run)
    if suffix == ".eml":
        return ingest_eml_file(path, dry_run=dry_run)
    return {"ok": False, "file": path.name, "error": f"unsupported extension {suffix!r}"}


# ---------------------------------------------------------------------------
# File discovery + manifest (mirrors whatsapp_ingest.py's pattern)
# ---------------------------------------------------------------------------

def _discover_files() -> list[Path]:
    if not EXPORTS_DIR.exists():
        return []
    files: list[Path] = []
    for ext in ("*.ics", "*.eml"):
        files += list(EXPORTS_DIR.glob(ext))
    return files


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()[:16]


def _load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        try:
            return json.loads(MANIFEST_PATH.read_text())
        except Exception:
            pass
    return {"processed": {}}


def _save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")


def run_scan() -> dict:
    files = _discover_files()
    manifest = _load_manifest()
    new_files = [f for f in files if _file_hash(f) not in manifest["processed"]]
    return {
        "discovered": len(files),
        "new": len(new_files),
        "files": [str(f) for f in new_files],
    }


def run_ingest_new(*, dry_run: bool = False) -> dict:
    files = _discover_files()
    manifest = _load_manifest()
    results: list[dict] = []
    processed_hashes: dict[str, str] = {}

    for path in files:
        fhash = _file_hash(path)
        if fhash in manifest["processed"]:
            continue
        result = ingest_file(path, dry_run=dry_run)
        results.append(result)
        if result.get("ok") and not dry_run:
            processed_hashes[fhash] = str(path)

    if not dry_run and processed_hashes:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        manifest["processed"].update({
            h: {"path": p, "processed_at": now} for h, p in processed_hashes.items()
        })
        _save_manifest(manifest)

    return {
        "ok": True,
        "files_processed": len(results),
        "calendar_files": sum(1 for r in results if r.get("kind") == "calendar"),
        "email_files": sum(1 for r in results if r.get("kind") == "email"),
        "dry_run": dry_run,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "results": results,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_summary(summary: dict) -> None:
    print("\nOutlook Manual Export Ingest")
    print(f"  Files processed:  {summary.get('files_processed', 0)}")
    print(f"  Calendar (.ics):  {summary.get('calendar_files', 0)}")
    print(f"  Email (.eml):     {summary.get('email_files', 0)}")
    for r in summary.get("results") or []:
        if r.get("ok"):
            print(f"    [{r['kind']}] {r['file']} — parsed {r['parsed']}, total {r['total_after_merge']}")
        else:
            print(f"    [error] {r['file']} — {r.get('error')}")
    if summary.get("dry_run"):
        print("\n  [DRY RUN — no changes written]")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan", action="store_true")
    group.add_argument("--ingest-new", action="store_true")
    group.add_argument("--file", metavar="PATH")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.scan:
        result = run_scan()
    elif args.file:
        result = ingest_file(Path(args.file), dry_run=args.dry_run)
    else:
        dry_run = args.dry_run and not args.confirm
        result = run_ingest_new(dry_run=dry_run)

    if args.json:
        print(json.dumps(result, indent=2))
    elif args.scan:
        print(f"Discovered: {result['discovered']}  New: {result['new']}")
        for f in result.get("files") or []:
            print(f"  {f}")
    elif args.file:
        print(json.dumps(result, indent=2))
    else:
        _print_summary(result)
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
