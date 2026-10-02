#!/usr/bin/env python3
"""
migrate_to_story_ledger.py — Phase 2 shadow-run migration.

RB-DEFECT-2026-07-20 Phase 2: replays the corporate-tagged entries in the
existing rendered_headlines.json registry through resolve_story_identity() +
StoryLedger, building system/.cache/story_ledger.json. This is a shadow-run
artifact only -- nothing in the live render pipeline reads it yet (that's a
separate, deliberate wiring step). The point of running it now is to verify
empirically, against real production history, that the new identity model
actually closes the gap the old URL-keyed registry couldn't: confirmed live,
9 separate "Wonder" registry entries fragmented across 3 genuinely different
real-world events (an earlier $600M pre-IPO round, a Mighty Quinn's BBQ
acquisition, and a later $650M Series D round) should collapse into exactly
3 stories, not 9 and not 1.

Replays entries in first_rendered order (oldest first) so a story's
first_seen date matches when it was genuinely first covered, not an
arbitrary dict-iteration order.

Usage:
    python3 system/scripts/migrate_to_story_ledger.py
    python3 system/scripts/migrate_to_story_ledger.py --dry-run
    python3 system/scripts/migrate_to_story_ledger.py --report-entity wonder
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import rb_core as core  # noqa: E402

RENDERED_HEADLINES_PATH = core.CACHE_DIR / "rendered_headlines.json"
STORY_LEDGER_PATH = core.CACHE_DIR / "story_ledger.json"


def replay(registry: dict) -> tuple[core.StoryLedger, list[dict]]:
    """Returns (ledger, unresolved) -- unresolved lists corporate entries
    resolve_story_identity() couldn't confidently identify, for visibility."""
    ledger = core.StoryLedger()
    unresolved: list[dict] = []

    corporate_entries = [
        (url, entry) for url, entry in registry.items() if entry.get("is_corporate")
    ]
    corporate_entries.sort(key=lambda ue: ue[1].get("first_rendered") or "")

    for url, entry in corporate_entries:
        title = entry.get("title") or ""
        key = core.resolve_story_identity(title)
        if key is None:
            unresolved.append({"url": url, "title": title})
            continue
        first_rendered = entry.get("first_rendered")
        try:
            entry_date = date.fromisoformat(first_rendered) if first_rendered else date.today()
        except ValueError:
            entry_date = date.today()
        ledger.record(key, url, title, entry_date)

    return ledger, unresolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report only, do not write story_ledger.json.")
    parser.add_argument("--report-entity", help="Print the full cluster for one entity_key (e.g. 'wonder').")
    args = parser.parse_args()

    if not RENDERED_HEADLINES_PATH.exists():
        print(f"No registry at {RENDERED_HEADLINES_PATH} -- nothing to migrate.")
        return 0

    registry = json.loads(RENDERED_HEADLINES_PATH.read_text(encoding="utf-8"))
    ledger, unresolved = replay(registry)

    total_corporate = sum(1 for e in registry.values() if e.get("is_corporate"))
    print(f"{total_corporate} corporate-tagged registry entries replayed.")
    print(f"{len(ledger.stories)} distinct stories identified.")
    print(f"{len(unresolved)} entries could not be resolved to a story identity (left ungrouped).")

    by_entity: dict[str, int] = defaultdict(int)
    for story in ledger.stories.values():
        by_entity[story["entity_key"]] += 1
    multi_story_entities = {k: v for k, v in by_entity.items() if v > 1}
    if multi_story_entities:
        print("\nEntities with more than one distinct story (expected for genuinely "
              "different events about the same company):")
        for entity, count in sorted(multi_story_entities.items(), key=lambda kv: -kv[1])[:15]:
            print(f"  {entity}: {count} stories")

    if args.report_entity:
        print(f"\nFull cluster for entity_key={args.report_entity!r}:")
        for story_id, story in ledger.stories.items():
            if story["entity_key"] == args.report_entity:
                print(f"  [{story_id}]")
                print(f"    first_seen={story['first_seen']} last_seen={story['last_seen']} "
                      f"render_count={story['render_count']}")
                print(f"    disambiguator={story['disambiguator']}")
                for u in story["urls"]:
                    print(f"    - {u}")

    if unresolved:
        print("\nUnresolved entries:")
        for u in unresolved[:20]:
            print(f"  {u['title'][:90]}")

    if args.dry_run:
        print("\nDry run -- story_ledger.json not written.")
        return 0

    STORY_LEDGER_PATH.write_text(json.dumps(ledger.to_dict(), indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {STORY_LEDGER_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
