#!/usr/bin/env python3
"""
relationship_card.py — Relationship Card publishing (RB-2026-09-08).

First relationship-side artifact. Unlike every prior artifact this
session, this is deliberately NOT a generator: system/cards/<id>.md is
already Todd's own hand-authored Relationship Card (schema documented in
SCHEMAS.md, 20 real files, served live via getCard in server.py). Todd
confirmed directly: wire the existing cards into the same governed
pattern (versioning/indexing/chat-tools) every other artifact got --
do not build render logic that synthesizes card content. The narrative
sections (Why this matters, Trust state, Leverage, What's lingering,
Risks, How to engage) are his own judgment/prose; a generator would
either duplicate that badly or risk conflicting with it.

publish_relationship_card() takes a verbatim snapshot of the current
system/cards/<id>.md text and versions it via artifact_vault_common.py --
same archive-before-overwrite discipline as every other artifact, just
with real, already-written content instead of computed/rendered content.
get_current_relationship_card() is the "governed" read path (version
metadata + content), distinct from getCard's role (raw file + a
canonical-vs-frontmatter last_touch drift check) -- both stay, each
serving its own purpose.

A contact must already be a real Relationship Card: signal_class == "RC"
in baseline_index.json AND an existing system/cards/<id>.md file. Never
mutates baseline_index.json or the card file itself -- read-only on both.

Reuses rb_core.py's own frontmatter-parsing approach (a plain regex, no
YAML library dependency -- matches getCard's own convention rather than
introducing a new one).

IMPORTANT: rb_core.py's load_baseline(path=BASELINE_PATH) binds its
default at import time -- calling it bare would silently read a stale
path under test isolation (the same historical trap
ecosystem_intelligence.py::_read_graph() was fixed for elsewhere; rb_core.py
itself is not changed here since other real callers depend on today's
behavior). Every call in this module passes core.BASELINE_PATH/core.CARDS_DIR
explicitly so a test's monkeypatch is honored.

Storage: system/artifact_vault/relationship_cards/<contact_id>/ via
artifact_vault_common.py.

CLI:
    python3 system/scripts/relationship_card.py publish <contact_id>
    python3 system/scripts/relationship_card.py publish-all
    python3 system/scripts/relationship_card.py get <contact_id>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index  # noqa: E402

ARTIFACT_TYPE_DIR = "relationship_cards"
ARTIFACT_TYPE_TITLE = "Relationship Card"


def _find_rc_contact(contact_id: str) -> dict:
    """Real, existing signal_class == 'RC' baseline entry, or raises."""
    for entry in core.load_baseline(core.BASELINE_PATH):
        if entry.get("id") == contact_id:
            if entry.get("signal_class") != "RC":
                raise ValueError(f"'{contact_id}' is not a Relationship Card (signal_class={entry.get('signal_class')!r})")
            return entry
    raise FileNotFoundError(f"No baseline_index.json entry for id '{contact_id}'")


def get_current_relationship_card(contact_id: str, *, include_content: bool = False) -> Optional[dict]:
    return avc.get_current_version(ARTIFACT_TYPE_DIR, contact_id, include_content=include_content)


def publish_relationship_card(contact_id: str, *, generated_for: str = "") -> dict:
    """The main entry point. Requires a real RC contact AND an existing
    card file. Takes a verbatim snapshot -- never re-renders or edits the
    card's own text."""
    entry = _find_rc_contact(contact_id)  # raises FileNotFoundError/ValueError
    card_path = core.CARDS_DIR / f"{contact_id}.md"
    if not card_path.exists():
        raise FileNotFoundError(f"No card file for RC contact '{contact_id}' at {card_path}")

    raw_text = card_path.read_text(encoding="utf-8")
    display_name = entry.get("name", contact_id)

    version = avc.register_version(
        ARTIFACT_TYPE_DIR, ARTIFACT_TYPE_TITLE, contact_id, display_name, raw_text,
        generated_for=generated_for, purpose="relationship card snapshot",
    )

    try:
        if version["version"] == 1:
            intelligence_index.register_document(
                display_name, "relationship_card", f"Relationship Card: {contact_id}",
                version["path"], source_system="relationship_card", created_at=avc.today(),
            )
        else:
            intelligence_index.log_update(
                display_name, version["path"], resource_type="relationship_card",
                note=f"republished as v{version['version']}" + (f" for {generated_for}" if generated_for else ""),
            )
    except Exception:  # noqa: BLE001 — indexing is best-effort, never blocks a real artifact write
        pass

    return {"contact_id": contact_id, "markdown": raw_text, "version": version}


def publish_all_relationship_cards() -> list[dict]:
    """Batch convenience: publish every real signal_class == 'RC' contact
    that has an existing card file. Skips (does not raise for) an RC
    contact with no card file yet -- that's a real, separate gap
    gap_detection.py already reports, not something to fail loudly over
    here."""
    results = []
    for entry in core.load_baseline(core.BASELINE_PATH):
        if entry.get("signal_class") != "RC":
            continue
        contact_id = entry.get("id")
        if not contact_id or not (core.CARDS_DIR / f"{contact_id}.md").exists():
            continue
        results.append(publish_relationship_card(contact_id))
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_pub = sub.add_parser("publish")
    p_pub.add_argument("contact_id")
    p_pub.add_argument("--for", dest="generated_for", default="")
    sub.add_parser("publish-all")
    p_get = sub.add_parser("get")
    p_get.add_argument("contact_id")
    args = parser.parse_args()

    if args.cmd == "publish":
        result = publish_relationship_card(args.contact_id, generated_for=args.generated_for)
        print(result["markdown"])
        print(f"\n[registered as version {result['version']['version']}]", file=sys.stderr)
    elif args.cmd == "publish-all":
        results = publish_all_relationship_cards()
        print(f"Published {len(results)} relationship card(s).", file=sys.stderr)
    elif args.cmd == "get":
        current = get_current_relationship_card(args.contact_id, include_content=True)
        if current is None:
            print(f"No Relationship Card published for '{args.contact_id}' yet.", file=sys.stderr)
            sys.exit(1)
        print(current["content"])


if __name__ == "__main__":
    main()
