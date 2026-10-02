#!/usr/bin/env python3
"""RB-DEFECT-032 — process trusted-sender full-text SMS through the mutation engine.

Mirrors social_content_mutation.py's pattern exactly (the RB-DEFECT-029 playbook):
a thin caller into the existing, working intelligence_mutation_engine — no parallel
extraction logic. Source material is the `full_text` field that
fetch_apple_messages.py now captures (RB-DEFECT-032 Layer 1) ONLY for inbound
messages from senders on the explicit trusted-intelligence-source allowlist.

This is the unblock for signals like Jeff Coffland's SMS about Foods Connected's
McDonald's footprint: previously truncated to an 80-char snippet before any
extraction stage could see it, now routed through the same confidence-scored
mutation pipeline that LinkedIn content uses.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import rb_core as core
import intelligence_mutation_engine
import fetch_apple_messages as _fam

MESSAGES_PATH = core.INBOX_DIR / "messages.json"
MANIFEST_PATH = core.CACHE_DIR / "sms_content_mutation.json"
SMS_CONFIG_PATH = core.SYSTEM_DIR / "sms_trusted_senders.json"

#: Minimum full-text length to bother routing through the engine — filters
#: out "ok", "thanks", "see you then" noise while letting genuine multi-
#: sentence intelligence signals through.
MIN_TEXT_LENGTH = 60


def _load_exempt_handles() -> set[str]:
    """Return the set of normalized handles exempt from intelligence processing.
    Second line of defence after fetch_apple_messages.py — ensures family/friends
    are never routed through the mutation engine even if full_text was captured."""
    cfg = _fam.load_sms_intelligence_config(SMS_CONFIG_PATH)
    return cfg.get("exempt_handles") or set()


def _hash_event(event: dict) -> str:
    stable = event.get("id") or event.get("at") or event.get("full_text") or ""
    return hashlib.sha256(str(stable).encode()).hexdigest()[:24]


def _load_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _normalize_phone_loose(handle: str) -> str:
    digits = "".join(ch for ch in (handle or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _resolve_sender(handle: str, baseline: list[dict]) -> dict:
    """Resolve a message handle (phone or email) to a baseline contact, so
    the mutation carries a real source identity (name/org/role) rather than
    a bare phone number — matching the LinkedIn path's author-identity shape."""
    handle_norm = _normalize_phone_loose(handle) if "@" not in (handle or "") else (handle or "").lower()
    for c in baseline:
        candidates = [c.get("phone"), c.get("email")]
        for cand in candidates:
            if not cand:
                continue
            cand_norm = _normalize_phone_loose(cand) if "@" not in cand else cand.lower()
            if cand_norm and cand_norm == handle_norm:
                return c
    return {}


def process_new(*, dry_run: bool = False) -> dict:
    messages = _load_json(MESSAGES_PATH, {"events": []})
    manifest = _load_json(MANIFEST_PATH, {"processed": {}})
    processed = manifest.setdefault("processed", {})
    baseline = core.load_baseline() if hasattr(core, "load_baseline") else []
    exempt_handles = _load_exempt_handles()
    results = []
    skipped = []

    for event in messages.get("events") or []:
        text = str(event.get("full_text") or "").strip()
        if not text:
            continue
        # Second-line exempt check — skip family/friends even if full_text was captured
        if exempt_handles:
            norm = _normalize_phone_loose(event.get("handle") or "")
            if norm and norm in exempt_handles:
                skipped.append({"id": event.get("id"), "reason": "exempt_handle"})
                continue
        if len(text) < MIN_TEXT_LENGTH:
            skipped.append({"id": event.get("id"), "reason": "insufficient_content"})
            continue
        event_hash = _hash_event(event)
        if event_hash in processed:
            skipped.append({"id": event.get("id"), "reason": "already_processed"})
            continue

        sender = _resolve_sender(event.get("handle") or "", baseline)
        sender_name = sender.get("name") or event.get("handle") or "unknown sender"
        result = intelligence_mutation_engine.run(
            text,
            source_title=f"SMS from {sender_name}",
            source_url=f"sms:{event.get('id')}",
            source_date=event.get("at") or "",
            source_author_name=sender.get("name") or "",
            source_author_org=sender.get("current_company") or "",
            source_author_role=sender.get("current_role") or "",
            auto_apply=not dry_run,
            dry_run=dry_run,
        )
        results.append({
            "id": event.get("id"),
            "sender": sender_name,
            "at": event.get("at"),
            "mutation_result": result,
        })
        if not dry_run:
            processed[event_hash] = {
                "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "id": event.get("id"),
                "sender": sender_name,
            }

    if not dry_run:
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    return {
        "ok": True,
        "dry_run": dry_run,
        "processed_count": len(results),
        "skipped_count": len(skipped),
        "results": results,
        "skipped": skipped,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    if not args.dry_run and not args.confirm:
        parser.error("specify --dry-run or --confirm")
    result = process_new(dry_run=not args.confirm)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
