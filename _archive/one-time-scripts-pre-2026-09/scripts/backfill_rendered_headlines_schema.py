#!/usr/bin/env python3
"""
backfill_rendered_headlines_schema.py — one-time migration.

RB-DEFECT-2026-07-20: the cross-day republished-URL matcher added
2026-07-17 (_corporate_story_already_covered in render_intelligence_brief.py)
only ever sees registry entries carrying `is_corporate`/`anchor_nouns` --
fields that fix started writing going forward, but never backfilled onto
history. Confirmed live: 5 of 9 "Wonder" entries in rendered_headlines.json
predate the fix and have neither field, so the matcher can't see them as
candidates at all -- a schema gap, not just a design gap.

This script re-derives both fields from each entry's already-stored title
(same logic _mark_rendered already applies to new entries: _is_corporate_event
using the bracket badge prefix in the title, then _story_anchor_nouns) and
fills in whatever's missing. Idempotent -- re-running recomputes the same
values from the same stored titles and reports 0 changes on a second pass.

Usage:
    python3 system/scripts/backfill_rendered_headlines_schema.py
    python3 system/scripts/backfill_rendered_headlines_schema.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import render_intelligence_brief as rib  # noqa: E402
import rb_core as core  # noqa: E402

REGISTRY_PATH = core.CACHE_DIR / "rendered_headlines.json"
_BADGE_RE = re.compile(r"^\[([^\]]+)\]")


def backfill(registry: dict) -> int:
    """Fill in missing is_corporate/anchor_nouns on existing entries in place.

    Returns the number of entries changed."""
    changed = 0
    for entry in registry.values():
        if entry.get("is_corporate"):
            continue
        title = entry.get("title") or ""
        badge_match = _BADGE_RE.match(title)
        badge = badge_match.group(1) if badge_match else ""
        if not rib._is_corporate_event(title, badge):
            continue
        nouns = rib._story_anchor_nouns(title)
        if not nouns:
            continue
        entry["is_corporate"] = True
        entry["anchor_nouns"] = sorted(nouns)
        changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                         help="Report what would change without writing.")
    args = parser.parse_args()

    if not REGISTRY_PATH.exists():
        print(f"No registry at {REGISTRY_PATH} -- nothing to backfill.")
        return 0

    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    changed = backfill(registry)
    print(f"{changed} entries backfilled with is_corporate/anchor_nouns.")
    if args.dry_run:
        print("Dry run -- no changes written.")
        return 0
    if changed:
        REGISTRY_PATH.write_text(json.dumps(registry, indent=2, default=str), encoding="utf-8")
        print(f"Wrote {REGISTRY_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
