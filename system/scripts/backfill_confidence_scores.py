#!/usr/bin/env python3
"""
backfill_confidence_scores.py — one-time migration (Confidence-Based
Auto-Recording, 2026-09-25).

confidence.score already exists in ecosystem_intelligence.json's relationship
schema (0-1 float) and was already populated for 381 of 419 real relationships
before this migration -- but 38 relationships had confidence.level set with no
score at all (confidence.score: null), a gap traced to ecosystem_intelligence
.py's own _confidence() helper always emitting score=None (fixed separately,
same day, so this gap stops recurring for anything written from here on).

This backfill derives a score for exactly those 38 from data already on the
record -- their existing confidence.level string, mapped through the same
confidence_calibration.CONFIDENCE_BAND_TO_SCHEMA table the new conflict
engine (check_relationship_conflict()) now compares scores against. Not a
re-guess: every one of these relationships already has a human- or
system-assigned level; this only fills in the numeric position within that
level's band.

Usage:
    python3 system/scripts/backfill_confidence_scores.py            # dry-run, prints what would change
    python3 system/scripts/backfill_confidence_scores.py --confirm  # writes for real (snapshots + validates, same as every other graph write)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import ecosystem_intelligence as ei  # noqa: E402
import confidence_calibration as cc  # noqa: E402

# Same level->band choice as ecosystem_intelligence.py's own _confidence()
# fix -- the lower of two bands sharing a schema level (e.g. "medium" over
# "medium_high"), so a bare level string never gets an inflated score.
_LEVEL_TO_BAND = {"low": "low", "medium": "medium", "high": "high", "critical": "very_high"}


def find_missing(graph: dict) -> list[dict]:
    return [
        rel for rel in (graph.get("relationships") or [])
        if (rel.get("confidence") or {}).get("score") is None
    ]


def backfill(graph: dict) -> list[dict]:
    """Fills confidence.score in place for every relationship missing one.
    Returns the list of changed relationships (before mutation is applied,
    for reporting)."""
    changed = []
    for rel in find_missing(graph):
        confidence = rel.setdefault("confidence", {})
        level = (confidence.get("level") or "medium").strip().lower()
        band = _LEVEL_TO_BAND.get(level, "medium")
        _, score = cc.CONFIDENCE_BAND_TO_SCHEMA[band]
        changed.append({
            "relationship_id": rel.get("id"), "level": level,
            "old_score": confidence.get("score"), "new_score": score,
        })
        confidence["score"] = score
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--confirm", action="store_true", help="Write for real. Default is dry-run.")
    args = parser.parse_args()

    graph = ei._read_graph()
    missing_before = len(find_missing(graph))
    changed = backfill(graph)

    print(f"Relationships missing confidence.score before: {missing_before}")
    for c in changed:
        print(f"  {c['relationship_id']}: level={c['level']} score {c['old_score']} -> {c['new_score']}")
    print(f"Total to backfill: {len(changed)}")

    if not args.confirm:
        print("\nDry-run only -- no changes written. Re-run with --confirm to apply.")
        return 0

    ei._write_graph(graph)
    print(f"\nWrote {len(changed)} backfilled confidence.score values to {ei.core.ECOSYSTEM_INTELLIGENCE_PATH}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
