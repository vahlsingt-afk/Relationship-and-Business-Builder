#!/usr/bin/env python3
"""
identity_match_review.py — propose baseline <-> inbound-email identity links.

Most baseline contacts have no email on file (they came from LinkedIn imports,
which don't expose email addresses — 2,629 of 2,855 as of 2026-07). When an
inbound email thread's sender email doesn't match any baseline contact by
email, but the sender's display name is an exact match for a baseline
contact's name, that's a candidate identity link worth confirming.

This never auto-merges. Per the contact-rationalization false-positive rule
(never merge on name alone — see the "two different J.B.s" incident), every
match is held as `proposed_pending_confirmation` until the operator confirms
or rejects it. Decisions persist indefinitely so a pair is never re-asked.

Match scope (v1): exact, punctuation/case-normalized full-name match only.
No nicknames, initials, or partial-name matching.

Candidate store: system/.cache/identity_match_candidates.json
  proposed_pending_confirmation — surfaced in the brief, awaiting a reply
  confirmed  — email written onto the baseline record; not re-surfaced
  confirmed_no_email — identity link confirmed (the thread is that person),
    but the sender address itself is not written on as their email — e.g. a
    platform/notification relay (invitations@linkedin.com) rather than the
    person's own address. Not re-surfaced.
  rejected   — operator said "not the same person"; not re-surfaced

CLI:
    python3 identity_match_review.py                   # scan + write candidates
    python3 identity_match_review.py --json             # scan, print summary JSON
    python3 identity_match_review.py --confirm <id>     # confirm a candidate
    python3 identity_match_review.py --reject <id>      # reject a candidate
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import personal_relationship_guard as personal_guard  # noqa: E402

CACHE_PATH = core.SYSTEM_DIR / ".cache" / "identity_match_candidates.json"


def _normalize_name(name: str | None) -> str:
    """Lowercase, strip punctuation/extra whitespace for exact-match comparison."""
    n = (name or "").strip().lower()
    n = re.sub(r"[.,]", "", n)
    n = re.sub(r"\s+", " ", n)
    return n


def _load_store() -> dict:
    if not CACHE_PATH.exists():
        return {"candidates": {}}
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"candidates": {}}
    data.setdefault("candidates", {})
    return data


def _save_store(store: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    store["_generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    CACHE_PATH.write_text(json.dumps(store, indent=2, default=str, ensure_ascii=False), encoding="utf-8")


def _candidate_id(baseline_id: str, sender_email: str) -> str:
    return f"{baseline_id}::{sender_email.lower()}"


def _is_exempt_sender(sender_name: str | None, sender_email: str | None, text: str | None = None) -> bool:
    """Return True when the personal relationship guard says this sender/item
    should never surface as business relationship intelligence.
    """
    result = personal_guard.classify(
        sender_name=sender_name or "",
        sender_email=sender_email or "",
        text=text or "",
    )
    return result.is_personal


def scan() -> dict:
    """Scan inbound email threads for exact-name matches to baseline contacts
    that don't already have that email on file. Returns a run summary."""
    baseline = core.load_baseline(core.BASELINE_PATH)
    name_index: dict[str, dict] = {}
    for entry in baseline:
        key = _normalize_name(entry.get("name"))
        if key and key not in name_index:
            name_index[key] = entry

    known_emails = set(core._build_email_index(baseline).keys()) | core.self_emails()

    store = _load_store()
    candidates: dict = store["candidates"]
    new_count = 0

    for acct in ("personal", "bridgepoint"):
        ep = core.email_path_for(acct) if hasattr(core, "email_path_for") else None
        if not ep or not ep.exists():
            continue
        try:
            data = json.loads(ep.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for thread in data.get("threads") or []:
            sender = thread.get("last_message_from") or {}
            sender_email = core._normalize_email(sender.get("email"))
            sender_name = sender.get("name") or ""
            if not sender_email or not sender_name:
                continue
            if _is_exempt_sender(sender_name, sender.get("email"), thread.get("subject")):
                continue
            if sender_email in known_emails:
                continue  # already linked to some baseline contact (or is self)

            match = name_index.get(_normalize_name(sender_name))
            if not match or match.get("email"):
                continue  # no exact-name match, or that contact already has a different email on file

            cid = _candidate_id(match["id"], sender_email)
            existing = candidates.get(cid)
            if existing and existing.get("status") in ("confirmed", "rejected"):
                continue  # decision already made — never re-ask

            candidates[cid] = {
                "id": cid,
                "baseline_id": match["id"],
                "baseline_name": match.get("name"),
                "baseline_company": match.get("current_company"),
                "sender_name": sender_name,
                "sender_email": sender.get("email"),
                "account": acct,
                "thread_id": thread.get("thread_id"),
                "thread_subject": thread.get("subject"),
                "last_message_at": thread.get("last_message_at"),
                "status": (existing or {}).get("status", "proposed_pending_confirmation"),
                "first_seen": (existing or {}).get("first_seen") or date.today().isoformat(),
            }
            if not existing:
                new_count += 1

    _save_store(store)
    pending = [c for c in candidates.values() if c["status"] == "proposed_pending_confirmation"]
    return {"total_candidates": len(candidates), "new_this_run": new_count, "pending": len(pending)}


def pending_candidates() -> list[dict]:
    store = _load_store()
    return [
        c for c in store["candidates"].values()
        if c.get("status") == "proposed_pending_confirmation"
        and not _is_exempt_sender(c.get("sender_name"), c.get("sender_email"), c.get("thread_subject"))
    ]


def confirm(candidate_id: str) -> dict:
    store = _load_store()
    cand = store["candidates"].get(candidate_id)
    if not cand:
        return {"error": f"unknown candidate id: {candidate_id}"}
    if cand["status"] != "proposed_pending_confirmation":
        return {"error": f"candidate {candidate_id} already resolved: {cand['status']}"}

    baseline = core.load_baseline(core.BASELINE_PATH)
    entry = next((e for e in baseline if e.get("id") == cand["baseline_id"]), None)
    if entry is None:
        return {"error": f"baseline record {cand['baseline_id']} no longer exists"}

    entry["email"] = cand["sender_email"]
    note_line = (
        f"[{date.today().isoformat()}] Email identity confirmed: {cand['sender_email']} "
        f"(matched via inbound thread \"{cand.get('thread_subject') or ''}\")"
    )
    entry["notes"] = (entry["notes"] + "\n" + note_line) if entry.get("notes") else note_line

    core.BASELINE_PATH.write_text(
        json.dumps(baseline, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    cand["status"] = "confirmed"
    cand["resolved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save_store(store)
    return {"confirmed": True, "baseline_id": cand["baseline_id"], "email": cand["sender_email"]}


def confirm_without_email(candidate_id: str, *, reason: str = "") -> dict:
    """Confirm the identity link without writing the sender address as their email.

    For candidates like invitations@linkedin.com — real match on the person,
    but the address is a platform relay, not theirs.
    """
    store = _load_store()
    cand = store["candidates"].get(candidate_id)
    if not cand:
        return {"error": f"unknown candidate id: {candidate_id}"}
    if cand["status"] != "proposed_pending_confirmation":
        return {"error": f"candidate {candidate_id} already resolved: {cand['status']}"}

    baseline = core.load_baseline(core.BASELINE_PATH)
    entry = next((e for e in baseline if e.get("id") == cand["baseline_id"]), None)
    if entry is None:
        return {"error": f"baseline record {cand['baseline_id']} no longer exists"}

    note_line = (
        f"[{date.today().isoformat()}] Identity confirmed for thread "
        f"\"{cand.get('thread_subject') or ''}\" from {cand['sender_email']}, "
        f"but that address was not added as their email"
        + (f" ({reason})" if reason else "") + "."
    )
    entry["notes"] = (entry["notes"] + "\n" + note_line) if entry.get("notes") else note_line

    core.BASELINE_PATH.write_text(
        json.dumps(baseline, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    cand["status"] = "confirmed_no_email"
    cand["resolved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save_store(store)
    return {"confirmed_no_email": True, "baseline_id": cand["baseline_id"],
            "email_declined": cand["sender_email"]}


def reject(candidate_id: str) -> dict:
    store = _load_store()
    cand = store["candidates"].get(candidate_id)
    if not cand:
        return {"error": f"unknown candidate id: {candidate_id}"}
    if cand["status"] != "proposed_pending_confirmation":
        return {"error": f"candidate {candidate_id} already resolved: {cand['status']}"}
    cand["status"] = "rejected"
    cand["resolved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save_store(store)
    return {"rejected": True, "baseline_id": cand["baseline_id"]}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    p.add_argument("--confirm", metavar="CANDIDATE_ID")
    p.add_argument("--confirm-no-email", metavar="CANDIDATE_ID")
    p.add_argument("--reason", default="", help="Optional note for --confirm-no-email")
    p.add_argument("--reject", metavar="CANDIDATE_ID")
    args = p.parse_args()

    if args.confirm:
        result = confirm(args.confirm)
    elif args.confirm_no_email:
        result = confirm_without_email(args.confirm_no_email, reason=args.reason)
    elif args.reject:
        result = reject(args.reject)
    else:
        result = scan()

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(result)
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
