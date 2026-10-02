#!/usr/bin/env python3
"""
migrate_conflicting_relationships.py — one-time migration (Confidence-Based
Auto-Recording, 2026-09-25).

Before this feature, check_relationship_conflict() could leave a relationship
permanently stuck at status="conflicting", requires_confirmation=True,
conflicts_with=<rival id> -- and nothing in the codebase ever read that state
back to resolve it (confirmed live: system/inbox/ecosystem/conflict_queue.jsonl
had 24 append-only entries, oldest 53 days, write-only). The engine itself no
longer produces that state at all (see check_relationship_conflict()'s
"both claims are active" branch) -- this migration replays every relationship
CURRENTLY stuck at status="conflicting" through the same score comparison the
new engine now uses live, so nothing is left behind.

Each stuck relationship already carries a real confidence.score (backfilled
2026-09-25 by backfill_confidence_scores.py) and already points at its rival
via conflicts_with -- this reads both, decides the same way check_
relationship_conflict() would today, and applies it:
  - meaningfully higher confidence than the rival -> promote to "active",
    mark the rival "superseded" (kept, never deleted)
  - otherwise -> downgrade to "rumored", set related_claim_id, still on file

Usage:
    python3 system/scripts/migrate_conflicting_relationships.py            # dry-run
    python3 system/scripts/migrate_conflicting_relationships.py --confirm  # writes for real
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import ecosystem_intelligence as ei  # noqa: E402


def find_stuck(graph: dict) -> list[dict]:
    return [rel for rel in (graph.get("relationships") or []) if rel.get("status") == "conflicting"]


def migrate(graph: dict) -> list[dict]:
    """Resolves every stuck relationship in place. Returns a report list."""
    by_id = ei._index_by_id(graph.get("relationships") or [])
    report = []
    for rel in find_stuck(graph):
        rival_id = rel.get("conflicts_with")
        rival = by_id.get(rival_id)
        entry = {"relationship_id": rel["id"], "rival_id": rival_id}
        if rival is None:
            entry["outcome"] = "skipped_rival_not_found"
            report.append(entry)
            continue

        new_score = ei._relationship_confidence_score(rel)
        rival_score = ei._relationship_confidence_score(rival)
        entry["new_score"] = new_score
        entry["rival_score"] = rival_score

        rel.pop("requires_confirmation", None)
        rel.pop("conflicts_with", None)

        if new_score >= rival_score + ei._SUPERSEDE_SCORE_MARGIN or (
            new_score >= ei._HIGH_CONFIDENCE_THRESHOLD and rival_score < ei._HIGH_CONFIDENCE_THRESHOLD
        ):
            rel["status"] = "active"
            rival["status"] = "superseded"
            rival["superseded_by"] = rel["id"]
            rival["updated_at"] = ei._now()
            entry["outcome"] = "auto_superseded"
        else:
            rel["status"] = "rumored"
            rel["related_claim_id"] = rival_id
            entry["outcome"] = "recorded_alongside"
        report.append(entry)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--confirm", action="store_true", help="Write for real. Default is dry-run.")
    args = parser.parse_args()

    graph = ei._read_graph()
    stuck_before = len(find_stuck(graph))
    report = migrate(graph)

    print(f"Relationships stuck at status=conflicting before: {stuck_before}")
    for entry in report:
        print(f"  {entry['relationship_id']}: {entry['outcome']}"
              + (f" (score {entry.get('new_score')} vs rival {entry.get('rival_score')})" if "new_score" in entry else ""))
    print(f"Total migrated: {len(report)}")

    if not args.confirm:
        print("\nDry-run only -- no changes written. Re-run with --confirm to apply.")
        return 0

    ei._write_graph(graph)
    print(f"\nWrote {len(report)} migrated relationships to {ei.core.ECOSYSTEM_INTELLIGENCE_PATH}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
