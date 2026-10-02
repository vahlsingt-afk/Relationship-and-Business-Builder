#!/usr/bin/env python3
"""data_tier_smoke.py — scan Tier 1 (canonical public) stores for private-
judgment content that shouldn't be there.

Data Tier Architecture work (2026-09-30, see system/DATA_TIER_ARCHITECTURE.
md). Real motivating cases, both found by hand this same day, not by any
automated check:

  1. system/ecosystem_intelligence.json's entities[].notes field carried a
     live sales-opportunity note on brand-little-caesars (fixed via
     ecosystem_intelligence.clear_entity_field()).
  2. system/brand_profiles/brand-burger-king.json's pain_points field
     carried a real franchisee-complaint note, added through the Team
     Portal's own legitimate add_brand_pain_point() path, and was being
     served live to every teammate (fixed by adding entry-level
     visibility to brand_profile_common.py).

Both were real, already-shipped leaks that nothing caught before a human
happened to look. This scan operationalizes "never flows back up" going
forward instead of relying on manual review: run it after any bulk
ingest, in CI, or on demand, and treat every FLAGGED entry as something a
human should look at (this script never auto-fixes anything -- same
review-first discipline as everywhere else in this codebase).

What it flags:
  - Any entities[].notes field that is non-empty at all (ecosystem_
    intelligence.json has no field-level visibility concept the way
    brand_profiles/ does -- any populated notes value is a candidate for
    review, not something this store's schema can mark "safe" on its own).
  - Any brand_profiles/*.json pain_points/recent_signals entry whose
    last_reviewed_by starts with "human:" or "team:" (i.e. a person, not
    an automated ingest job, wrote it) AND whose visibility is missing or
    "team_shareable" -- the exact Burger King shape. An entry already
    marked visibility: "private" is not flagged; it's already handled.

Usage:
    python3 system/scripts/data_tier_smoke.py           # human-readable report
    python3 system/scripts/data_tier_smoke.py --json     # machine-readable
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent
ROOT = SYSTEM_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import rb_core as core  # noqa: E402

BRAND_PROFILES_DIR = SYSTEM_DIR / "brand_profiles"
_HUMAN_PREFIXES = ("human:", "team:")


def scan_ecosystem_notes() -> list[dict]:
    """Every populated entities[].notes value -- flagged for review, not
    auto-cleared, since this store has no field-level visibility concept
    of its own to check against (unlike brand_profiles/ below)."""
    path = core.ECOSYSTEM_INTELLIGENCE_PATH
    if not path.exists():
        return []
    graph = json.loads(path.read_text(encoding="utf-8"))
    findings = []
    for entity in graph.get("entities") or []:
        notes = entity.get("notes")
        if notes:
            findings.append({
                "store": "ecosystem_intelligence.json",
                "entity_id": entity.get("id"),
                "field": "notes",
                "value_preview": str(notes)[:200],
            })
    return findings


def _is_flagged_entry(entry: dict) -> bool:
    last_reviewed_by = entry.get("last_reviewed_by") or ""
    if not any(last_reviewed_by.startswith(p) for p in _HUMAN_PREFIXES):
        return False
    return entry.get("visibility", "team_shareable") != "private"


def scan_brand_profiles() -> list[dict]:
    if not BRAND_PROFILES_DIR.exists():
        return []
    findings = []
    for path in sorted(BRAND_PROFILES_DIR.glob("*.json")):
        try:
            profile = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for list_key in ("pain_points", "recent_signals"):
            for entry in profile.get(list_key) or []:
                if _is_flagged_entry(entry):
                    findings.append({
                        "store": f"brand_profiles/{path.name}",
                        "brand_id": profile.get("brand_id"),
                        "field": list_key,
                        "last_reviewed_by": entry.get("last_reviewed_by"),
                        "value_preview": str(entry.get("value", ""))[:200],
                    })
    return findings


def run_scan() -> dict:
    return {
        "ecosystem_notes": scan_ecosystem_notes(),
        "brand_profile_entries": scan_brand_profiles(),
    }


def _print_report(result: dict) -> None:
    ecosystem = result["ecosystem_notes"]
    profiles = result["brand_profile_entries"]
    total = len(ecosystem) + len(profiles)
    if total == 0:
        print("data_tier_smoke: no flagged entries. Clean.")
        return
    print(f"data_tier_smoke: {total} entr{'y' if total == 1 else 'ies'} flagged for human review\n")
    if ecosystem:
        print(f"-- ecosystem_intelligence.json entities[].notes ({len(ecosystem)}) --")
        for f in ecosystem:
            print(f"  {f['entity_id']}: {f['value_preview']!r}")
        print()
    if profiles:
        print(f"-- brand_profiles/*.json pain_points/recent_signals ({len(profiles)}) --")
        for f in profiles:
            print(f"  {f['store']} [{f['field']}] ({f['last_reviewed_by']}): {f['value_preview']!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    result = run_scan()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_report(result)
    total = len(result["ecosystem_notes"]) + len(result["brand_profile_entries"])
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
