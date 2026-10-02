#!/usr/bin/env python3
"""
personal_relationship_guard.py — personal vs. business classification for
email/calendar intelligence gathering (system/personal_relationship_exempt.json).

RB-2026-08-28. Todd's rule, stated explicitly: RB is a business/professional
relationship-capital tool. Personal relationships and personal content must
never be recorded or reported -- only business (and "business personal", a
business contact who is also a friend) should be. Same principle already
established for SMS on 2026-07-27 (sms_trusted_senders.json's exempt_handles,
managed by sms_exempt_manager.py) -- family/personal contacts never get a
baseline entry, even a minimal one, regardless of tier. This extends the
identical discipline to email and calendar, with two signal types instead
of one:

  - exempt_senders: a name/email that is ALWAYS personal unless the specific
    item is explicitly flagged otherwise (Tony Fryer, Nick Neylon, ...).
  - exempt_topics: a keyword/phrase that makes the ITEM personal regardless
    of sender (fantasy football, ...).

Context and sender both matter, per Todd's own framing -- classify() checks
both and either one is sufficient to mark something personal. Business is
the default; nothing is excluded unless it actually matches a configured
signal, keeping this an opt-out (deny-list) model like the SMS version, not
an opt-in one that would silently drop unlisted business contacts.

CLI:
    python3 personal_relationship_guard.py --list
    python3 personal_relationship_guard.py --add-sender "Tony Fryer"
    python3 personal_relationship_guard.py --add-sender "someone@example.com"
    python3 personal_relationship_guard.py --remove-sender "Tony Fryer"
    python3 personal_relationship_guard.py --add-topic "fantasy football"
    python3 personal_relationship_guard.py --remove-topic "fantasy football"
    python3 personal_relationship_guard.py --check --sender "Tony Fryer" --text "lunch Saturday?"
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402

CONFIG_PATH = core.SYSTEM_DIR / "personal_relationship_exempt.json"


def _default_config() -> dict:
    return {
        "_comment": (
            "Personal vs. business classification for email/calendar intelligence "
            "gathering. See personal_relationship_guard.py."
        ),
        "exempt_senders": [],
        "exempt_topics": [],
    }


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return _default_config()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_config()
    if not isinstance(data, dict):
        return _default_config()
    data.setdefault("exempt_senders", [])
    data.setdefault("exempt_topics", [])
    return data


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


@dataclass
class PersonalGuardResult:
    is_personal: bool
    reason: str | None  # e.g. "exempt_sender: Tony Fryer" or "exempt_topic: fantasy football"


def _sender_matches(sender_name: str, sender_email: str, exempt_senders: list) -> str | None:
    name_lower = (sender_name or "").strip().lower()
    email_lower = (sender_email or "").strip().lower()
    for entry in exempt_senders:
        if not isinstance(entry, dict):
            continue
        e_name = (entry.get("name") or "").strip().lower()
        e_email = (entry.get("email") or "").strip().lower()
        if e_email and email_lower and e_email == email_lower:
            return entry.get("name") or entry.get("email")
        if e_name and name_lower and (e_name in name_lower or name_lower in e_name):
            return entry.get("name")
    return None


def _topic_matches(text: str, exempt_topics: list) -> str | None:
    text_lower = (text or "").lower()
    for topic in exempt_topics:
        if not isinstance(topic, str) or not topic.strip():
            continue
        if topic.strip().lower() in text_lower:
            return topic
    return None


def classify(
    *,
    sender_name: str = "",
    sender_email: str = "",
    text: str = "",
    explicit_business_flag: bool = False,
    config: dict | None = None,
) -> PersonalGuardResult:
    """Classify one email/calendar item as personal or business.

    explicit_business_flag: the "unless flagged otherwise" override -- set
    True when the user has explicitly said this specific item, despite
    matching an exempt sender, is actually business (e.g. "that email from
    Tony Fryer about the Genius conference was business, log it"). A topic
    match is NOT overridable this way -- content itself being personal
    (fantasy football) doesn't become business because of who sent it.
    """
    cfg = config if config is not None else load_config()

    topic_hit = _topic_matches(text, cfg.get("exempt_topics") or [])
    if topic_hit:
        return PersonalGuardResult(True, f"exempt_topic: {topic_hit}")

    sender_hit = _sender_matches(sender_name, sender_email, cfg.get("exempt_senders") or [])
    if sender_hit and not explicit_business_flag:
        return PersonalGuardResult(True, f"exempt_sender: {sender_hit}")

    return PersonalGuardResult(False, None)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--list", action="store_true")
    p.add_argument("--add-sender")
    p.add_argument("--sender-email", help="Optional email to pair with --add-sender")
    p.add_argument("--remove-sender")
    p.add_argument("--add-topic")
    p.add_argument("--remove-topic")
    p.add_argument("--check", action="store_true")
    p.add_argument("--sender", default="")
    p.add_argument("--email", default="")
    p.add_argument("--text", default="")
    p.add_argument("--business-override", action="store_true")
    args = p.parse_args()

    cfg = load_config()

    if args.list:
        print(json.dumps(cfg, indent=2))
        return 0

    if args.add_sender:
        cfg["exempt_senders"].append({"name": args.add_sender, "email": args.sender_email})
        save_config(cfg)
        print(f"Added exempt sender: {args.add_sender}")
        return 0

    if args.remove_sender:
        before = len(cfg["exempt_senders"])
        cfg["exempt_senders"] = [
            e for e in cfg["exempt_senders"]
            if (e.get("name") or "").strip().lower() != args.remove_sender.strip().lower()
        ]
        save_config(cfg)
        removed = before - len(cfg["exempt_senders"])
        print(f"Removed {removed} matching exempt sender(s).")
        return 0

    if args.add_topic:
        cfg["exempt_topics"].append(args.add_topic)
        save_config(cfg)
        print(f"Added exempt topic: {args.add_topic}")
        return 0

    if args.remove_topic:
        before = len(cfg["exempt_topics"])
        cfg["exempt_topics"] = [t for t in cfg["exempt_topics"] if t.strip().lower() != args.remove_topic.strip().lower()]
        save_config(cfg)
        removed = before - len(cfg["exempt_topics"])
        print(f"Removed {removed} matching exempt topic(s).")
        return 0

    if args.check:
        result = classify(
            sender_name=args.sender, sender_email=args.email, text=args.text,
            explicit_business_flag=args.business_override, config=cfg,
        )
        print(json.dumps({"is_personal": result.is_personal, "reason": result.reason}, indent=2))
        return 0

    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
