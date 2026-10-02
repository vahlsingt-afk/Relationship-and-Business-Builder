#!/usr/bin/env python3
"""
tech_stack_web_research_sweep.py — RB-2026-09-02.

Feeds the rb-1500-brand-deep-tech-stack-research-segment Codex automation's
output into RBB's real intake path. That automation used to write a fresh
free-form markdown file into a NEW throwaway directory every 3-hour run
(~/Documents/Codex/<date>/run-the-next-bounded-segment-of-N/...) because it
ran projectless with cwd=~ -- those files were never read by anything else
in RBB and were confirmed orphaned (same shape as the Pollo Campero RFP
recovery incident). Fixed at the automation-config level (now project-bound,
weekend-only cadence) AND here: the automation now appends one JSON line
per finding to system/inbox/tech_stack_web_research_queue.jsonl (a real,
single, in-repo file) instead of scattering markdown. This script is the
sweep half of that JPR-style pattern -- run once by morning_pipeline.py, not
by the automation itself, so entity resolution and review-first proposal
logic run exactly once per batch, in one place, using
tech_stack_relationship_promotion.py's already-proven
propose_research_finding() (never a direct graph write -- same review-first
discipline as every other RBB intake path).

Brand-tech-stack claims (claim_type="brand_tech_stack") resolve via the same
ecosystem_intelligence._resolve_brand_entity_id() the workbook ingestion
pipeline already trusts; an unresolved or ambiguous brand name is never
guessed -- it's written to the unresolved bucket below for a human (or a
future entity_identity pass) to settle.

Vendor/competitor-profile claims (claim_type="vendor_profile" -- Phase 2 of
the automation's own prompt: product/feature/battle-card material) have no
existing narrow intake path with this shape; they're queued for review
rather than silently dropped or force-fit into a mismatched schema. Wiring
those into competitor_intelligence.py's real evidence path is a real
follow-up, not done here.

CLI:
    python3 tech_stack_web_research_sweep.py sweep     # process new queue lines
    python3 tech_stack_web_research_sweep.py status    # show checkpoint + counts
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import tech_stack_relationship_promotion as promotion  # noqa: E402

QUEUE_PATH = core.SYSTEM_DIR / "inbox" / "tech_stack_web_research_queue.jsonl"
STATE_PATH = core.SYSTEM_DIR / ".cache" / "tech_stack_web_research_sweep_state.json"
UNRESOLVED_PATH = core.SYSTEM_DIR / ".cache" / "tech_stack_web_research_unresolved.jsonl"
VENDOR_PROFILE_PATH = core.SYSTEM_DIR / ".cache" / "tech_stack_web_research_vendor_notes.jsonl"
LATEST_PATH = core.SYSTEM_DIR / ".cache" / "tech_stack_web_research_sweep_latest.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"lines_processed": 0}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"lines_processed": 0}
    data.setdefault("lines_processed", 0)
    return data


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now_iso()
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def sweep(*, dry_run: bool = False) -> dict:
    if not QUEUE_PATH.exists():
        return {"new_lines": 0, "proposed": 0, "unresolved": 0, "vendor_profile_queued": 0, "errors": 0}

    lines = QUEUE_PATH.read_text(encoding="utf-8").splitlines()
    state = _load_state()
    start = state.get("lines_processed", 0)
    new_lines = lines[start:]

    graph = ei._read_graph()
    proposed = unresolved = vendor_queued = errors = 0

    for raw in new_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            errors += 1
            if not dry_run:
                _append_jsonl(UNRESOLVED_PATH, {"ts": _now_iso(), "error": "invalid_json", "raw": raw[:500]})
            continue

        claim_type = rec.get("claim_type")
        if claim_type == "vendor_profile":
            vendor_queued += 1
            if not dry_run:
                _append_jsonl(VENDOR_PROFILE_PATH, {**rec, "queued_at": _now_iso()})
            continue

        if claim_type != "brand_tech_stack":
            errors += 1
            if not dry_run:
                _append_jsonl(UNRESOLVED_PATH, {"ts": _now_iso(), "error": f"unknown claim_type {claim_type!r}", "record": rec})
            continue

        brand_name = (rec.get("brand_name") or "").strip()
        vendor_name = (rec.get("vendor_name") or "").strip()
        evidence_text = (rec.get("evidence_text") or "").strip()
        if not (brand_name and vendor_name and evidence_text):
            errors += 1
            if not dry_run:
                _append_jsonl(UNRESOLVED_PATH, {"ts": _now_iso(), "error": "missing_required_field", "record": rec})
            continue

        brand_id, is_new = ei._resolve_brand_entity_id(brand_name, graph)
        if not brand_id or is_new:
            unresolved += 1
            if not dry_run:
                _append_jsonl(UNRESOLVED_PATH, {
                    "ts": _now_iso(), "error": "brand_not_resolved_or_ambiguous",
                    "brand_name": brand_name, "record": rec,
                })
            continue

        if dry_run:
            proposed += 1
            continue

        result = promotion.propose_research_finding(
            brand_id, vendor_name, evidence_text,
            category=rec.get("category"),
            source_url=rec.get("source_url"),
            source_title=rec.get("source_title"),
            status=rec.get("status"),
        )
        if result.get("error"):
            errors += 1
            _append_jsonl(UNRESOLVED_PATH, {"ts": _now_iso(), "error": result["error"], "record": rec})
        else:
            proposed += 1

    if not dry_run:
        state["lines_processed"] = len(lines)
        _save_state(state)

    result = {
        "new_lines": len(new_lines), "proposed": proposed, "unresolved": unresolved,
        "vendor_profile_queued": vendor_queued, "errors": errors,
    }
    if not dry_run:
        result["generated_at"] = _now_iso()
        LATEST_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def status() -> dict:
    state = _load_state()
    total_lines = len(QUEUE_PATH.read_text(encoding="utf-8").splitlines()) if QUEUE_PATH.exists() else 0
    return {
        "queue_path": str(QUEUE_PATH),
        "total_lines": total_lines,
        "lines_processed": state.get("lines_processed", 0),
        "pending": max(0, total_lines - state.get("lines_processed", 0)),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sweep")
    sub.add_parser("status")
    args = p.parse_args()

    if args.cmd == "sweep":
        print(json.dumps(sweep(), indent=2))
    elif args.cmd == "status":
        print(json.dumps(status(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
