#!/usr/bin/env python3
"""hunter_mutation_review.py — review and resolve Hunter's mutation proposal
queue.

Confirmed gap, 2026-10-10: hunter_change_dispatch.py appends every mutation
proposal that has no registered narrow writer (the common case today --
only one narrow writer exists at all) to
system/.cache/hunter_mutation_proposals.jsonl, and nothing ever read that
file back. 38 real, distinct proposals had piled up silently since
2026-10-03 with no way to see or act on them.

This does NOT write to canonical records itself -- no generic, safe writer
exists for most of these field types yet (that's the separate, larger
"narrow writer" effort). What it provides is the missing review step:
list what's pending, and record Todd's approve/reject decision durably
and idempotently, so:
  - a proposal stops silently piling up once he's acted on it
  - "approved" proposals become a real, visible backlog for the narrow-
    writer work to apply against first, instead of starting from zero
  - "rejected" proposals are recorded with a reason, not just discarded

Two files, same durable-log pattern as every other Hunter ledger in this
codebase:
  - hunter_mutation_proposals.jsonl (written by hunter_change_dispatch.py,
    read-only from here)
  - hunter_mutation_resolutions.jsonl (written here; one row per decision)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

PROPOSAL_QUEUE_PATH = SYSTEM_DIR / ".cache" / "hunter_mutation_proposals.jsonl"
RESOLUTIONS_PATH = SYSTEM_DIR / ".cache" / "hunter_mutation_resolutions.jsonl"
DECISIONS = {"approved", "rejected"}


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def _resolved_ids() -> set[str]:
    return {r.get("proposal_id") for r in _read_jsonl(RESOLUTIONS_PATH) if r.get("proposal_id")}


def list_pending() -> list[dict]:
    """Every queued proposal not yet resolved, oldest first -- the real
    backlog view. Each row carries the full original queue entry (proposal,
    decision, reason, packet_id, queued_at) exactly as hunter_change_
    dispatch.py wrote it, so a reviewer sees everything without a second
    lookup."""
    resolved = _resolved_ids()
    rows = [r for r in _read_jsonl(PROPOSAL_QUEUE_PATH) if (r.get("proposal") or {}).get("proposal_id") not in resolved]
    rows.sort(key=lambda r: r.get("queued_at") or "")
    return rows


def list_resolved(limit: int = 100) -> list[dict]:
    """Most recent decisions first -- an audit trail of what's already
    been acted on, separate from the pending backlog."""
    rows = _read_jsonl(RESOLUTIONS_PATH)
    rows.sort(key=lambda r: r.get("resolved_at") or "", reverse=True)
    return rows[:limit]


def resolve(proposal_id: str, decision: str, *, resolved_by: str, note: str = "") -> dict:
    """Record Todd's (or another owner's) decision on one pending proposal.

    Idempotent and defensive by construction, not by a separate check the
    caller has to remember: raises ValueError for an unknown proposal_id
    (typo, or one that's already been resolved by a concurrent request) or
    an unrecognized decision, rather than silently recording garbage."""
    if decision not in DECISIONS:
        raise ValueError(f"unknown decision: {decision!r} (must be one of {sorted(DECISIONS)})")
    pending = {(r.get("proposal") or {}).get("proposal_id"): r for r in list_pending()}
    if proposal_id not in pending:
        raise ValueError(f"no pending proposal with id {proposal_id!r} (already resolved, or never queued)")
    record = {
        "schema": "rb.hunter_mutation_resolution.v1",
        "proposal_id": proposal_id,
        "decision": decision,
        "resolved_by": resolved_by,
        "resolved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": note,
        "packet_id": pending[proposal_id].get("packet_id"),
    }
    _append_jsonl(RESOLUTIONS_PATH, record)
    return record


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List pending proposals")
    resolved_p = sub.add_parser("resolved", help="List recent resolutions")
    resolved_p.add_argument("--limit", type=int, default=100)
    resolve_p = sub.add_parser("resolve", help="Record a decision on one proposal")
    resolve_p.add_argument("proposal_id")
    resolve_p.add_argument("--decision", required=True, choices=sorted(DECISIONS))
    resolve_p.add_argument("--by", required=True, help="Who made this decision")
    resolve_p.add_argument("--note", default="")
    args = parser.parse_args()

    if args.command == "list":
        print(json.dumps(list_pending(), indent=2, sort_keys=True))
    elif args.command == "resolved":
        print(json.dumps(list_resolved(limit=args.limit), indent=2, sort_keys=True))
    elif args.command == "resolve":
        try:
            print(json.dumps(resolve(args.proposal_id, args.decision, resolved_by=args.by, note=args.note), indent=2, sort_keys=True))
        except ValueError as error:
            print(json.dumps({"error": str(error)}, indent=2), file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
