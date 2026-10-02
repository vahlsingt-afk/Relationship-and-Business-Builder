#!/usr/bin/env python3
"""Manage the SMS intelligence exempt-handles list (sms_trusted_senders.json).

Usage examples:
  python3 sms_exempt_manager.py --list
  python3 sms_exempt_manager.py --suggest
  python3 sms_exempt_manager.py --add "John Smith"
  python3 sms_exempt_manager.py --add "+12145551234"
  python3 sms_exempt_manager.py --remove "John Smith"
  python3 sms_exempt_manager.py --remove "2145551234"

The exempt list tells RB to skip these contacts during SMS intelligence
processing. Use it for family, close friends, or anyone whose texts should
remain private — all other inbound messages are routed through the mutation
engine (opt-out model).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import rb_core as core

CONFIG_PATH = core.SYSTEM_DIR / "sms_trusted_senders.json"
BASELINE_PATH = core.BASELINE_PATH


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {"mode": "all", "trusted_handles": [], "exempt_handles": []}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"mode": "all", "trusted_handles": [], "exempt_handles": []}
        data.setdefault("mode", "all")
        data.setdefault("trusted_handles", [])
        data.setdefault("exempt_handles", [])
        return data
    except (OSError, json.JSONDecodeError):
        return {"mode": "all", "trusted_handles": [], "exempt_handles": []}


def _save_config(cfg: dict) -> None:
    cfg["_comment"] = (
        "SMS intelligence config. mode='all': all inbound messages processed "
        "except exempt_handles. exempt_handles: phone numbers (10-digit, no "
        "dashes) or email addresses for family/friends to exclude."
    )
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Baseline helpers
# ---------------------------------------------------------------------------

def _load_baseline() -> list[dict]:
    try:
        data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("contacts", [])
    except (OSError, json.JSONDecodeError):
        return []


def _normalize_phone(handle: str) -> str:
    digits = "".join(ch for ch in handle if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _is_phone(s: str) -> bool:
    return bool(re.fullmatch(r"[\d\s\-\+\(\)\.]{7,}", s))


def _contact_phones(c: dict) -> list[str]:
    phones = []
    for field in ("phone", "mobile", "phone_mobile", "phone_work"):
        val = c.get(field)
        if val:
            n = _normalize_phone(str(val))
            if len(n) == 10:
                phones.append(n)
    return phones


def _resolve_by_name(name: str, baseline: list[dict]) -> list[dict]:
    """Find baseline contacts whose name contains all words in `name`."""
    words = name.lower().split()
    return [
        c for c in baseline
        if all(w in (c.get("name") or "").lower() for w in words)
    ]


def _resolve_handle(handle: str, baseline: list[dict]) -> list[str]:
    """Resolve a name or phone/email string to a list of normalized handles."""
    stripped = handle.strip()
    if "@" in stripped:
        return [stripped.lower()]
    if _is_phone(stripped):
        n = _normalize_phone(stripped)
        return [n] if len(n) == 10 else []
    # Try name lookup
    matches = _resolve_by_name(stripped, baseline)
    if not matches:
        return []
    handles = []
    for c in matches:
        handles.extend(_contact_phones(c))
        email = (c.get("email") or "").strip().lower()
        if email:
            handles.append(email)
    return list(set(handles))


def _label_for_handle(handle: str, baseline: list[dict]) -> str:
    """Return 'Name (handle)' if a baseline match exists, else just the handle."""
    for c in baseline:
        phones = _contact_phones(c)
        emails = [(c.get("email") or "").lower()]
        if handle in phones or handle in emails:
            name = c.get("name") or handle
            company = c.get("current_company") or ""
            suffix = f" @ {company}" if company else ""
            return f"{name}{suffix} ({handle})"
    return handle


# ---------------------------------------------------------------------------
# Suggestion logic
# ---------------------------------------------------------------------------

def _suggest_personal_contacts(baseline: list[dict], exempt_set: set[str]) -> list[dict]:
    """Identify baseline contacts likely to be personal rather than professional.

    Heuristics (any match = candidate):
      - Has a tag containing 'family', 'personal', 'friend', 'church', 'faith'
      - In a circle named 'personal', 'family', or 'friends'
      - No current_company AND no current_role AND has a phone number
      - relationship_type in ('personal', 'family', 'friend', 'social')
    """
    personal_keywords = {"family", "personal", "friend", "church", "faith",
                         "friends", "social", "neighbor", "ministry"}
    suggestions = []
    for c in baseline:
        phones = _contact_phones(c)
        if not phones:
            continue  # no phone → can't exempt anyway
        # Already exempt? Skip.
        if any(p in exempt_set for p in phones):
            continue

        score = 0
        reasons = []

        # Tag-based
        tags = [str(t).lower() for t in (c.get("tags") or [])]
        matched_tags = [t for t in tags if any(kw in t for kw in personal_keywords)]
        if matched_tags:
            score += 3
            reasons.append(f"tags: {', '.join(matched_tags)}")

        # Circle-based
        circles = [str(ci).lower() for ci in (c.get("circles") or [])]
        matched_circles = [ci for ci in circles if any(kw in ci for kw in personal_keywords)]
        if matched_circles:
            score += 3
            reasons.append(f"circles: {', '.join(matched_circles)}")

        # relationship_type
        rel_type = (c.get("relationship_type") or "").lower()
        if any(kw in rel_type for kw in personal_keywords):
            score += 2
            reasons.append(f"relationship_type: {rel_type}")

        # No professional context
        has_company = bool(c.get("current_company") or c.get("company"))
        has_role = bool(c.get("current_role") or c.get("role"))
        if not has_company and not has_role:
            score += 1
            reasons.append("no professional context")

        if score > 0:
            suggestions.append({
                "contact": c,
                "phones": phones,
                "score": score,
                "reasons": reasons,
            })

    suggestions.sort(key=lambda x: x["score"], reverse=True)
    return suggestions


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list(baseline: list[dict]) -> None:
    cfg = _load_config()
    exempt = cfg.get("exempt_handles") or []
    print(f"Mode: {cfg.get('mode', 'all')}")
    print(f"Exempt handles ({len(exempt)}):")
    if not exempt:
        print("  (none — all inbound messages are processed)")
        return
    for h in sorted(exempt):
        print(f"  {_label_for_handle(h, baseline)}")


def cmd_suggest(baseline: list[dict]) -> None:
    cfg = _load_config()
    exempt_set = {h for h in (cfg.get("exempt_handles") or [])}
    suggestions = _suggest_personal_contacts(baseline, exempt_set)
    if not suggestions:
        print("No personal/family contacts identified in baseline.")
        print("You can add contacts manually with --add.")
        return
    print(f"Suggested contacts to exempt ({len(suggestions)} found):\n")
    for i, s in enumerate(suggestions[:30], 1):
        c = s["contact"]
        name = c.get("name") or "Unknown"
        phones = ", ".join(s["phones"])
        print(f"  {i:2}. {name} ({phones})")
        print(f"      Reasons: {'; '.join(s['reasons'])}")
    print(f"\nTo exempt all suggestions: --add-suggested")
    print(f"To exempt specific ones: --add \"Name\"")


def cmd_add_suggested(baseline: list[dict]) -> None:
    cfg = _load_config()
    exempt = list(cfg.get("exempt_handles") or [])
    exempt_set = set(exempt)
    suggestions = _suggest_personal_contacts(baseline, exempt_set)
    if not suggestions:
        print("No new personal contacts to add.")
        return
    added = []
    for s in suggestions:
        for phone in s["phones"]:
            if phone not in exempt_set:
                exempt.append(phone)
                exempt_set.add(phone)
                added.append(f"{s['contact'].get('name', phone)} ({phone})")
    if added:
        cfg["exempt_handles"] = sorted(exempt)
        _save_config(cfg)
        print(f"Added {len(added)} handle(s) to exempt list:")
        for a in added:
            print(f"  + {a}")
    else:
        print("All suggested contacts are already exempt.")


def cmd_add(handle_str: str, baseline: list[dict]) -> None:
    cfg = _load_config()
    exempt = list(cfg.get("exempt_handles") or [])
    exempt_set = set(exempt)

    handles = _resolve_handle(handle_str, baseline)
    if not handles:
        # Couldn't resolve — might be a raw phone with formatting
        n = _normalize_phone(handle_str)
        if len(n) == 10:
            handles = [n]
        else:
            print(f"Could not resolve '{handle_str}' to a phone number or email.")
            print("Try using the exact phone number (e.g. 2145551234) or full name as it appears in your contacts.")
            sys.exit(1)

    added = []
    already = []
    for h in handles:
        if h in exempt_set:
            already.append(h)
        else:
            exempt.append(h)
            exempt_set.add(h)
            added.append(h)

    if added:
        cfg["exempt_handles"] = sorted(exempt)
        _save_config(cfg)
        for h in added:
            print(f"  + Exempted: {_label_for_handle(h, baseline)}")
    if already:
        for h in already:
            print(f"  = Already exempt: {_label_for_handle(h, baseline)}")


def cmd_remove(handle_str: str, baseline: list[dict]) -> None:
    cfg = _load_config()
    exempt = list(cfg.get("exempt_handles") or [])
    exempt_set = set(exempt)

    handles = _resolve_handle(handle_str, baseline)
    if not handles:
        n = _normalize_phone(handle_str)
        if len(n) == 10:
            handles = [n]
        else:
            print(f"Could not resolve '{handle_str}'.")
            sys.exit(1)

    removed = []
    not_found = []
    for h in handles:
        if h in exempt_set:
            exempt_set.discard(h)
            removed.append(h)
        else:
            not_found.append(h)

    if removed:
        cfg["exempt_handles"] = sorted(exempt_set)
        _save_config(cfg)
        for h in removed:
            print(f"  - Removed from exempt: {_label_for_handle(h, baseline)}")
    if not_found:
        for h in not_found:
            print(f"  ? Not in exempt list: {h}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="Manage the SMS intelligence exempt-handles list.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--list", action="store_true",
                     help="Show all currently exempt handles with resolved names.")
    grp.add_argument("--suggest", action="store_true",
                     help="Suggest personal/family contacts from baseline to exempt.")
    grp.add_argument("--add-suggested", action="store_true",
                     help="Add all suggested personal/family contacts to exempt list.")
    grp.add_argument("--add", metavar="NAME_OR_PHONE",
                     help="Add a contact by name or phone number.")
    grp.add_argument("--remove", metavar="NAME_OR_PHONE",
                     help="Remove a contact from the exempt list.")
    args = p.parse_args()

    baseline = _load_baseline()

    if args.list:
        cmd_list(baseline)
    elif args.suggest:
        cmd_suggest(baseline)
    elif args.add_suggested:
        cmd_add_suggested(baseline)
    elif args.add:
        cmd_add(args.add, baseline)
    elif args.remove:
        cmd_remove(args.remove, baseline)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
