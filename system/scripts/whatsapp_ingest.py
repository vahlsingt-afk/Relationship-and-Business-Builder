#!/usr/bin/env python3
"""
whatsapp_ingest.py — WhatsApp export processor for RB intelligence pipeline.

Parses WhatsApp TXT exports (one file per chat) and ZIP archives containing
multiple chat exports. Classifies chats, resolves identities against baseline,
computes communication velocity, and writes intelligence to:

    system/.cache/whatsapp_ingest_latest.json   — ingest summary
    system/.cache/whatsapp_chats.json           — classified chat intelligence

Drop locations watched:
    system/inbox/whatsapp_exports/      (TXT or ZIP files)

Chat classification:
    strategic_relationship  — 1:1 with a baseline contact
    strategic_group         — group chat with 2+ baseline contacts
    personal                — matches personal/exempt pattern
    unknown                 — no baseline match; surfaces for user confirmation

CLI:
    python3 whatsapp_ingest.py --scan
    python3 whatsapp_ingest.py --ingest-new --dry-run
    python3 whatsapp_ingest.py --ingest-new --confirm
    python3 whatsapp_ingest.py --file <path> [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core  # noqa: E402

EXPORTS_DIR = core.INBOX_DIR / "whatsapp_exports"
MANIFEST_PATH = core.CACHE_DIR / "whatsapp_ingest_manifest.json"
LATEST_PATH = core.CACHE_DIR / "whatsapp_ingest_latest.json"
CHATS_PATH = core.CACHE_DIR / "whatsapp_chats.json"

PHONE_CLEAN_RX = re.compile(r"[^\d+]")

# iOS format:    [DD/MM/YYYY, HH:MM:SS] Sender: text
# Android fmt:   MM/DD/YY, HH:MM - Sender: text
# Variant:       [MM/DD/YYYY, HH:MM:SS AM] Sender: text
MSG_PATTERNS = [
    # iOS: [DD/MM/YYYY, HH:MM:SS]
    re.compile(
        r"^\[(\d{1,2}/\d{1,2}/\d{2,4}),\s*(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?)\]\s*(.+?):\s*(.*)",
        re.IGNORECASE,
    ),
    # Android: MM/DD/YY, HH:MM -
    re.compile(
        r"^(\d{1,2}/\d{1,2}/\d{2,4}),\s*(\d{1,2}:\d{2}(?:\s*[AP]M)?)\s*-\s*(.+?):\s*(.*)",
        re.IGNORECASE,
    ),
]
SYSTEM_MSG_RX = re.compile(
    r"(Messages and calls are end-to-end|You deleted this message|"
    r"This message was deleted|<Media omitted>|end-to-end encrypted|"
    r"changed the subject|added|removed|left|created group|"
    r"Your security code with|changed their phone number)",
    re.IGNORECASE,
)
ATTACHMENT_RX = re.compile(r"<attached:\s*.+?>|<Media omitted>", re.IGNORECASE)

PERSONAL_RX = re.compile(
    r"\b(mom|dad|mother|father|sister|brother|aunt|uncle|grandma|grandpa|"
    r"pastor|reverend|church|insurance|pharmacy|hospital|clinic|utilities|"
    r"plumber|contractor|hvac|doctor|dentist)\b",
    re.I,
)

# Known strategic group prefixes — can be extended via settings
STRATEGIC_GROUP_HINTS = re.compile(
    r"\b(wrotp|otp3|exec|leadership|board|strategy|mcd|mcdonalds|franchise|"
    r"advisory|mastermind|founders|cxo|ceo|cfo|coo|partner)\b",
    re.I,
)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _parse_whatsapp_txt(text: str) -> dict:
    """Parse a WhatsApp TXT export. Returns a structured chat dict."""
    lines = text.splitlines()
    messages: list[dict] = []
    participants: set[str] = set()
    current: dict | None = None

    for line in lines:
        matched = False
        for pat in MSG_PATTERNS:
            m = pat.match(line)
            if m:
                date_str, time_str, sender, body = m.groups()
                sender = sender.strip()
                body = body.strip()
                # Skip pure system messages
                if SYSTEM_MSG_RX.search(body) and not body.startswith(sender):
                    current = None
                    matched = True
                    break
                is_attachment = bool(ATTACHMENT_RX.search(body))
                current = {
                    "sender": sender,
                    "date": date_str,
                    "time": time_str,
                    "body": body,
                    "is_attachment": is_attachment,
                    "char_count": len(body),
                }
                messages.append(current)
                if sender and not SYSTEM_MSG_RX.search(sender):
                    participants.add(sender)
                matched = True
                break
        if not matched and current and line.strip():
            # Continuation line — append to current message body
            current["body"] = current["body"] + " " + line.strip()
            current["char_count"] = len(current["body"])

    is_group = len(participants) > 2

    # Infer chat name from filename context (caller sets this) or participant list
    return {
        "is_group": is_group,
        "participant_count": len(participants),
        "participants": sorted(participants),
        "message_count": len(messages),
        "messages": messages,
    }


def _normalize_phone(raw: str) -> str:
    cleaned = PHONE_CLEAN_RX.sub("", raw)
    if not cleaned:
        return ""
    if len(cleaned) == 11 and cleaned.startswith("1"):
        cleaned = cleaned[1:]
    return cleaned if len(cleaned) >= 7 else ""


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def _build_baseline_indexes(contacts: list[dict]) -> tuple[dict, dict]:
    """Return (name_idx, phone_idx) for fast lookup."""
    name_idx: dict[str, dict] = {}
    phone_idx: dict[str, dict] = {}
    for c in contacts:
        key = _name_key(c.get("name") or "")
        if key:
            name_idx[key] = c
        phone = _normalize_phone(c.get("phone") or "")
        if phone:
            phone_idx[phone] = c
    return name_idx, phone_idx


def _match_participant(sender: str, name_idx: dict, phone_idx: dict) -> dict | None:
    """Match a WhatsApp sender (name or phone) to a baseline contact."""
    # Phone match
    phone = _normalize_phone(sender)
    if phone and phone in phone_idx:
        return phone_idx[phone]
    # Exact name match
    key = _name_key(sender)
    if key and key in name_idx:
        return name_idx[key]
    # Partial name match (first + last substring)
    parts = sender.lower().split()
    if len(parts) >= 2:
        for bkey, bc in name_idx.items():
            bname = (bc.get("name") or "").lower()
            if parts[0] in bname and parts[-1] in bname:
                return bc
    return None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _classify_chat(
    chat_name: str,
    parsed: dict,
    matched_participants: list[dict],
) -> str:
    """Return classification string for a chat."""
    is_group = parsed["is_group"]
    n_matched = len(matched_participants)

    if PERSONAL_RX.search(chat_name):
        return "personal"

    if is_group:
        if STRATEGIC_GROUP_HINTS.search(chat_name):
            return "strategic_group"
        if n_matched >= 2:
            return "strategic_group"
        if n_matched == 1:
            return "strategic_group"  # group with at least one known contact
        return "unknown_group"

    # 1:1 chat
    if n_matched >= 1:
        return "strategic_relationship"
    if PERSONAL_RX.search(" ".join(parsed["participants"])):
        return "personal"
    return "unknown"


# ---------------------------------------------------------------------------
# Velocity
# ---------------------------------------------------------------------------

def _compute_velocity(messages: list[dict]) -> dict:
    """Compute monthly message counts for the two most recent months."""
    monthly: Counter = Counter()
    for msg in messages:
        date_raw = msg.get("date") or ""
        # Parse MM/DD/YY or DD/MM/YYYY — try both
        parsed_date = None
        for fmt in ("%m/%d/%y", "%d/%m/%Y", "%m/%d/%Y", "%d/%m/%y"):
            try:
                parsed_date = datetime.strptime(date_raw, fmt).date()
                break
            except ValueError:
                continue
        if parsed_date:
            key = f"{parsed_date.year}-{parsed_date.month:02d}"
            monthly[key] += 1

    sorted_months = sorted(monthly.keys(), reverse=True)
    this_month = sorted_months[0] if sorted_months else None
    last_month = sorted_months[1] if len(sorted_months) > 1 else None
    this_count = monthly.get(this_month, 0) if this_month else 0
    last_count = monthly.get(last_month, 0) if last_month else 0

    trend = "stable"
    if last_count > 0:
        delta_pct = (this_count - last_count) / last_count
        if delta_pct > 0.2:
            trend = "increasing"
        elif delta_pct < -0.2:
            trend = "decreasing"
    elif this_count > 0:
        trend = "new_activity"

    return {
        "this_month": this_month,
        "this_month_count": this_count,
        "last_month": last_month,
        "last_month_count": last_count,
        "trend": trend,
        "total_messages": len(messages),
    }


# ---------------------------------------------------------------------------
# Sender breakdown
# ---------------------------------------------------------------------------

def _sender_stats(messages: list[dict]) -> list[dict]:
    counts: Counter = Counter(m.get("sender") for m in messages if m.get("sender"))
    return [{"sender": s, "count": c} for s, c in counts.most_common(10)]


# ---------------------------------------------------------------------------
# Core ingest
# ---------------------------------------------------------------------------

def _ingest_parsed(
    chat_name: str,
    parsed: dict,
    baseline_contacts: list[dict],
    *,
    dry_run: bool = False,
) -> dict:
    name_idx, phone_idx = _build_baseline_indexes(baseline_contacts)

    matched_participants: list[dict] = []
    unresolved_participants: list[str] = []
    for sender in parsed["participants"]:
        match = _match_participant(sender, name_idx, phone_idx)
        if match:
            matched_participants.append(match)
        else:
            unresolved_participants.append(sender)

    classification = _classify_chat(chat_name, parsed, matched_participants)
    velocity = _compute_velocity(parsed["messages"])
    sender_stats = _sender_stats(parsed["messages"])
    last_message_at = None
    for msg in parsed["messages"]:
        raw = f"{msg.get('date') or ''} {msg.get('time') or ''}".strip()
        for fmt in ("%m/%d/%y %I:%M %p", "%m/%d/%Y %I:%M %p", "%d/%m/%Y %H:%M", "%d/%m/%y %H:%M"):
            try:
                candidate = datetime.strptime(raw, fmt)
                if last_message_at is None or candidate > last_message_at:
                    last_message_at = candidate
                break
            except ValueError:
                continue

    mutations: list[dict] = []
    for bc in matched_participants:
        if bc.get("id"):
            mutations.append({
                "type": "whatsapp_channel_confirm",
                "id": bc["id"],
                "name": bc.get("name"),
                "chat": chat_name,
                "source": "whatsapp",
            })

    return {
        "chat_name": chat_name,
        "classification": classification,
        "is_group": parsed["is_group"],
        "participant_count": parsed["participant_count"],
        "matched_baseline": [{"id": c.get("id"), "name": c.get("name")} for c in matched_participants],
        "unresolved_participants": unresolved_participants,
        "message_count": parsed["message_count"],
        "last_message_at": last_message_at.date().isoformat() if last_message_at else None,
        "velocity": velocity,
        "sender_stats": sender_stats,
        "mutations": mutations,
        "dry_run": dry_run,
    }


def ingest_file(path: Path, *, dry_run: bool = False) -> dict:
    """Process one WhatsApp TXT or ZIP export."""
    baseline_raw = json.loads(core.BASELINE_PATH.read_text())
    baseline_contacts: list[dict] = (
        baseline_raw if isinstance(baseline_raw, list)
        else list(baseline_raw.values()) if isinstance(baseline_raw, dict)
        else []
    )

    results: list[dict] = []

    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            txt_names = [n for n in zf.namelist() if n.lower().endswith(".txt")]
            for name in txt_names:
                try:
                    text = zf.read(name).decode("utf-8", errors="replace")
                except Exception as e:
                    results.append({"chat_name": name, "ok": False, "error": str(e)})
                    continue
                chat_name = Path(name).stem.replace("WhatsApp Chat with ", "").strip()
                parsed = _parse_whatsapp_txt(text)
                r = _ingest_parsed(chat_name, parsed, baseline_contacts, dry_run=dry_run)
                r["ok"] = True
                results.append(r)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        chat_name = path.stem.replace("WhatsApp Chat with ", "").strip()
        parsed = _parse_whatsapp_txt(text)
        r = _ingest_parsed(chat_name, parsed, baseline_contacts, dry_run=dry_run)
        r["ok"] = True
        results.append(r)

    # Persist chat intelligence
    if not dry_run and results:
        _merge_chats_cache(results)

    summary = {
        "ok": True,
        "file": path.name,
        "chats_processed": len(results),
        "results": results,
    }
    return summary


def _merge_chats_cache(new_results: list[dict]) -> None:
    """Merge new chat results into the running chats cache."""
    existing: dict = {}
    if CHATS_PATH.exists():
        try:
            raw = json.loads(CHATS_PATH.read_text())
            existing = {c["chat_name"]: c for c in (raw.get("chats") or [])}
        except Exception:
            existing = {}
    for r in new_results:
        if r.get("ok") and r.get("chat_name"):
            existing[r["chat_name"]] = r
    CHATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHATS_PATH.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chats": list(existing.values()),
    }, indent=2) + "\n")


# ---------------------------------------------------------------------------
# File discovery + manifest
# ---------------------------------------------------------------------------

def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()[:16]


def _discover_files() -> list[Path]:
    if not EXPORTS_DIR.exists():
        return []
    files: list[Path] = []
    for ext in ("*.txt", "*.zip"):
        files += list(EXPORTS_DIR.glob(ext))
    return files


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
        processed_hashes[fhash] = str(path)

    if not dry_run and processed_hashes:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        manifest["processed"].update({
            h: {"path": p, "processed_at": now}
            for h, p in processed_hashes.items()
        })
        _save_manifest(manifest)

    total_chats = sum(r.get("chats_processed", 0) for r in results)
    strategic = sum(
        1
        for r in results
        for chat in (r.get("results") or [])
        if chat.get("classification") in ("strategic_relationship", "strategic_group")
    )
    new_signals = sum(
        len(chat.get("matched_baseline") or [])
        for r in results
        for chat in (r.get("results") or [])
    )

    summary = {
        "ok": True,
        "files_processed": len(results),
        "chats_processed": total_chats,
        "strategic_chats": strategic,
        "new_relationship_signals": new_signals,
        "dry_run": dry_run,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "results": results,
    }
    if not dry_run:
        LATEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        LATEST_PATH.write_text(json.dumps(summary, indent=2) + "\n")
    return summary


# ---------------------------------------------------------------------------
# Brief intelligence block (called by cos_judgment or daily_brief)
# ---------------------------------------------------------------------------

def build_whatsapp_intelligence_block() -> dict:
    """Return a WhatsApp intelligence summary for the daily brief."""
    if not CHATS_PATH.exists():
        return {"available": False, "reason": "no whatsapp chats processed yet"}
    try:
        raw = json.loads(CHATS_PATH.read_text())
    except Exception:
        return {"available": False, "reason": "chats cache unreadable"}

    chats = raw.get("chats") or []
    strategic_rel = [c for c in chats if c.get("classification") == "strategic_relationship"]
    strategic_grp = [c for c in chats if c.get("classification") == "strategic_group"]
    personal = [c for c in chats if c.get("classification") == "personal"]
    unknown = [c for c in chats if c.get("classification") in ("unknown", "unknown_group")]

    velocity_increases = [
        c["chat_name"]
        for c in chats
        if (c.get("velocity") or {}).get("trend") == "increasing"
    ]

    return {
        "available": True,
        "generated_at": raw.get("generated_at"),
        "total_chats": len(chats),
        "strategic_relationships": len(strategic_rel),
        "strategic_groups": len(strategic_grp),
        "personal": len(personal),
        "unknown": len(unknown),
        "velocity_increases": velocity_increases,
        "strategic_relationship_names": [c["chat_name"] for c in strategic_rel],
        "strategic_group_names": [c["chat_name"] for c in strategic_grp],
        "chats": chats,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_summary(summary: dict) -> None:
    print(f"\nWhatsApp Intelligence")
    print(f"  Files processed:            {summary.get('files_processed', 0)}")
    print(f"  Chats processed:            {summary.get('chats_processed', 0)}")
    print(f"  Strategic chats:            {summary.get('strategic_chats', 0)}")
    print(f"  New relationship signals:   {summary.get('new_relationship_signals', 0)}")

    for file_result in (summary.get("results") or []):
        print(f"\n  File: {file_result.get('file')}")
        for chat in (file_result.get("results") or []):
            cls = chat.get("classification", "?")
            vel = (chat.get("velocity") or {}).get("trend", "")
            matched = [m.get("name") for m in (chat.get("matched_baseline") or [])]
            print(
                f"    [{cls}] {chat.get('chat_name')} — "
                f"{chat.get('message_count', 0)} msgs, {vel}"
                + (f" — matched: {', '.join(matched)}" if matched else "")
            )
            unresolved = chat.get("unresolved_participants") or []
            if unresolved:
                print(f"      Unresolved: {', '.join(unresolved[:5])}")

    if summary.get("dry_run"):
        print("\n  [DRY RUN — no changes written]")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan", action="store_true")
    group.add_argument("--ingest-new", action="store_true")
    group.add_argument("--file", metavar="PATH")
    group.add_argument("--brief-block", action="store_true",
                       help="Emit the WhatsApp intelligence block for the daily brief.")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.scan:
        result = run_scan()
    elif args.file:
        result = ingest_file(Path(args.file), dry_run=args.dry_run)
    elif args.brief_block:
        result = build_whatsapp_intelligence_block()
    else:
        dry_run = args.dry_run and not args.confirm
        result = run_ingest_new(dry_run=dry_run)

    if args.json:
        print(json.dumps(result, indent=2))
    elif args.scan:
        print(f"Discovered: {result['discovered']}  New: {result['new']}")
        for f in result.get("files") or []:
            print(f"  {f}")
    elif args.brief_block:
        print(json.dumps(result, indent=2))
    else:
        _print_summary(result)
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
