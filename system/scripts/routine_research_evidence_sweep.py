#!/usr/bin/env python3
"""Validate routine-research evidence and route it to a review-first queue."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUEUE_PATH = ROOT / "inbox" / "routine_research_evidence_queue.jsonl"
STATE_PATH = ROOT / ".cache" / "routine_research_evidence_state.json"
REVIEW_PATH = ROOT / ".cache" / "routine_research_field_reviews.json"
LATEST_PATH = ROOT / ".cache" / "routine_research_evidence_sweep_latest.json"
REQUIRED = {"cycle_type", "entity_name", "dimension", "finding_type", "evidence_text",
            "source_url", "source_title", "confidence_pct", "researched_at"}


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def sweep() -> dict:
    lines = QUEUE_PATH.read_text(encoding="utf-8").splitlines() if QUEUE_PATH.exists() else []
    state = _load(STATE_PATH, {"lines_processed": 0})
    start = min(int(state.get("lines_processed") or 0), len(lines))
    review = _load(REVIEW_PATH, {"pending_reviews": []})
    pending = review.setdefault("pending_reviews", [])
    known = {row.get("evidence_id") for row in pending}
    accepted = duplicates = invalid = 0
    for raw in lines[start:]:
        try:
            row = json.loads(raw)
        except ValueError:
            invalid += 1
            continue
        if not isinstance(row, dict) or row.get("cycle_type") != "routine_research" or not REQUIRED.issubset(row):
            invalid += 1
            continue
        identity = "|".join(str(row.get(k) or "") for k in
                            ("entity_name", "dimension", "evidence_text", "source_url"))
        evidence_id = "rre-" + hashlib.sha256(identity.encode()).hexdigest()[:16]
        if evidence_id in known:
            duplicates += 1
            continue
        pending.append({
            "evidence_id": evidence_id,
            "kind": "routine_research_field_evidence",
            "status": "pending_review",
            "requires_confirmation": True,
            "entity_name": row["entity_name"],
            "entity_type": row.get("entity_type"),
            "dimension": row["dimension"],
            "finding_type": row["finding_type"],
            "evidence_text": row["evidence_text"],
            "source_url": row["source_url"],
            "source_title": row["source_title"],
            "source_published_date": row.get("source_published_date"),
            "confidence_pct": row["confidence_pct"],
            "corroborating_source_count": row.get("corroborating_source_count", 1),
            "limitations": row.get("limitations", ""),
            "proposed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        known.add(evidence_id)
        accepted += 1
    REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_PATH.write_text(json.dumps(review, indent=2) + "\n", encoding="utf-8")
    STATE_PATH.write_text(json.dumps({"lines_processed": len(lines), "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=2) + "\n", encoding="utf-8")
    result = {"new_lines": len(lines) - start, "accepted_for_review": accepted,
              "duplicates": duplicates, "invalid": invalid, "canonical_mutations": 0,
              "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    LATEST_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    argparse.ArgumentParser().parse_args()
    print(json.dumps(sweep(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
