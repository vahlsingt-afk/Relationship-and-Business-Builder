#!/usr/bin/env python3
"""RB-DEFECT-021 — Friday review orchestrator.

Observes the live week's plan against current system-of-record state (loop
ledger + active threads) and proposes a *draft* scorecard — outcome-by-outcome
movement classification, wins/misses, lessons — pending human confirmation.

Mirrors `weekly_plan_generator.py`'s propose-then-confirm pattern: RB
*observes and proposes* a read on the week; the human confirms or corrects it.
Self-grading your own week without a human check is exactly the kind of
silent-auto-commit this system's architecture deliberately avoids.

Without this orchestrator, `make_scorecard`/`save_scorecard` (schema landed
2026-06-08 in weekly_planning.py) are reachable only by hand-authoring a
scorecard — so `load_recent_scorecards` always returns empty and the Monthly
Strategy horizon (which reads trailing scorecards) has nothing to read.

Usage:
    python3 weekly_review_generator.py                  # propose, print
    python3 weekly_review_generator.py --write-draft    # persist draft
    python3 weekly_review_generator.py --confirm        # promote draft -> live scorecard file
    python3 weekly_review_generator.py --confirm --draft-path <path>
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core
import weekly_planning as wp

DRAFT_PATH = core.SYSTEM_DIR / "weekly_scorecard_draft.json"

#: Thread-status tokens that count as forward movement on a linked outcome.
ADVANCED_STATUS_TOKENS = {"advanced", "closed", "won", "offer", "signed", "completed"}
STALLED_STATUS_TOKENS = {"stalled", "blocked", "at_risk", "ghosted"}


def _load_threads_by_id() -> dict:
    try:
        threads = core.load_active_threads()
    except Exception:
        threads = []
    out = {}
    for t in threads:
        tid = t.get("id")
        if tid:
            out[tid] = t
    return out


def _loop_status_by_id(today: date) -> dict:
    try:
        loops = core.parse_loop_ledger()
    except Exception:
        return {}
    buckets = core.loops_by_status(loops, today)
    status_for: dict[str, str] = {}
    for bucket_name, bucket_loops in (buckets or {}).items():
        for L in bucket_loops or []:
            lid = getattr(L, "id", None) or getattr(L, "loop_id", None)
            if lid:
                status_for[lid] = bucket_name
    return status_for


def _classify_outcome_movement(outcome: dict, threads_by_id: dict, loop_status: dict) -> tuple[str, str]:
    """Return (movement, rationale) — a *proposed* read, not a verdict.

    Movement is one of advanced | no_movement | stalled | closed, derived
    from the clearest signal available: linked loop resolution first (most
    concrete — a loop is either closed or it isn't), then linked-thread
    status tokens, falling back to "no_movement" with an honest "no signal"
    rationale rather than guessing.
    """
    linked_loops = outcome.get("linked_loop_ids") or []
    if linked_loops:
        statuses = [loop_status.get(lid) for lid in linked_loops]
        known = [s for s in statuses if s]
        if known and all(s == "closed" for s in known):
            return "closed", f"all linked loops closed ({', '.join(linked_loops)})"
        if any(s == "closed" for s in known):
            return "advanced", f"some linked loops closed ({', '.join(linked_loops)})"
        if any(s == "overdue" for s in known):
            return "stalled", f"linked loop(s) still overdue ({', '.join(linked_loops)})"
        if known:
            return "no_movement", f"linked loop(s) open, not yet due/overdue ({', '.join(linked_loops)})"

    linked_opps = outcome.get("linked_opportunity_ids") or []
    for oid in linked_opps:
        thread = threads_by_id.get(oid)
        if not thread:
            continue
        status = (thread.get("status") or "").strip().lower()
        if status in ADVANCED_STATUS_TOKENS:
            return "advanced", f"linked thread '{oid}' status='{status}'"
        if status in STALLED_STATUS_TOKENS:
            return "stalled", f"linked thread '{oid}' status='{status}'"

    if not linked_loops and not linked_opps:
        return "no_movement", "outcome carries no linked loop/opportunity ids to observe — manual read required"
    return "no_movement", "no advancing/stalling signal observed in linked loops/threads this week"


def build_draft_scorecard(today: date, plan: dict) -> dict:
    threads_by_id = _load_threads_by_id()
    loop_status = _loop_status_by_id(today)

    outcome_movements: dict[str, str] = {}
    provenance: list[dict] = []
    for outcome in plan.get("outcomes", []):
        movement, rationale = _classify_outcome_movement(outcome, threads_by_id, loop_status)
        outcome_movements[outcome["id"]] = movement
        provenance.append({"outcome_id": outcome["id"], "title": outcome.get("title"),
                           "movement": movement, "rationale": rationale})

    advanced_or_closed = sum(1 for m in outcome_movements.values() if m in {"advanced", "closed"})
    total = max(1, len(outcome_movements))
    overall_score = round(100 * advanced_or_closed / total)

    scorecard = wp.make_scorecard(
        plan,
        outcome_movements=outcome_movements,
        relationship_movement="",
        lessons=[],
        overall_score=overall_score,
    )
    scorecard["status"] = "draft_pending_confirmation"
    scorecard["generated_at"] = datetime.utcnow().isoformat() + "Z"
    scorecard["generation_provenance"] = provenance
    return scorecard


def render_proposal(scorecard: dict) -> str:
    lines = [
        f"DRAFT Friday scorecard for week of {scorecard.get('week_of')} "
        f"(proposed overall_score={scorecard.get('overall_score')}, pending confirmation)",
        "",
    ]
    for prov in scorecard.get("generation_provenance", []):
        lines.append(f"  [{prov['movement'].upper()}] {prov['title']}")
        lines.append(f"      observed: {prov['rationale']}")
    lines.append("")
    lines.append(f"  Wins:   {', '.join(scorecard.get('wins') or []) or '(none observed)'}")
    lines.append(f"  Misses: {', '.join(scorecard.get('misses') or []) or '(none observed)'}")
    lines.append("")
    lines.append(
        "This is a PROPOSED READ derived from loop-ledger resolution + linked-thread "
        "status — not a verdict. Correct any movement classification, add "
        "relationship_movement/lessons by hand, then run `--confirm` to persist it as "
        "this week's scorecard (feeds the Monthly Strategy horizon's trailing-scorecard read)."
    )
    return "\n".join(lines)


def confirm_draft_scorecard(draft_path: Path = DRAFT_PATH, today: date | None = None) -> dict:
    """Promote a pending weekly scorecard draft to a saved scorecard_<week_of>.json.

    Factored out of `main()`'s `--confirm` branch (mirrors
    `weekly_plan_generator.confirm_draft()`'s own factoring) so callers other
    than the CLI -- Todd confirming via chat, or a review step in
    `friday_eow_routine.py` -- can promote a draft without shelling out.
    Deliberately requires an explicit call: this module's own docstring is
    clear that self-grading the week without a human check is exactly the
    silent-auto-commit this system's architecture avoids, so nothing calls
    this automatically. Returns {"error": ...} on failure, otherwise
    {"scorecard": ..., "path": ...}.
    """
    today = today or date.today()
    if not draft_path.exists():
        return {"error": f"No draft found at {draft_path}."}
    try:
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"error": f"Could not read draft: {e}"}
    plan = wp.load_plan()
    if not wp.is_current(plan, today):
        return {"error": (
            f"No live plan for the current week (week_of={wp._week_of(today)}) — "
            f"refusing to confirm a scorecard with nothing to score against."
        )}
    if draft.get("week_of") != plan.get("week_of"):
        return {"error": (
            f"Draft is for week_of={draft.get('week_of')}, but the live plan is for "
            f"week_of={plan.get('week_of')}. Regenerate the draft before confirming."
        )}
    draft.pop("status", None)
    draft.pop("generation_provenance", None)
    draft["confirmed_at"] = datetime.utcnow().isoformat() + "Z"
    out_path = wp.save_scorecard(draft)
    return {"scorecard": draft, "path": str(out_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="ISO date to anchor the review week to (default: today).")
    parser.add_argument("--write-draft", action="store_true", help="Persist the draft to weekly_scorecard_draft.json.")
    parser.add_argument("--confirm", action="store_true",
                        help="Promote the draft to a saved scorecard_<week_of>.json.")
    parser.add_argument("--draft-path", default=str(DRAFT_PATH))
    parser.add_argument("--json", action="store_true", help="Print the draft as JSON instead of a human summary.")
    args = parser.parse_args()

    today = date.fromisoformat(args.date) if args.date else date.today()
    draft_path = Path(args.draft_path)

    if args.confirm:
        result = confirm_draft_scorecard(draft_path=draft_path, today=today)
        if "error" in result:
            sys.stderr.write(result["error"] + "\n")
            return 2
        print(f"Confirmed. Scorecard saved to {result['path']}.")
        return 0

    plan = wp.load_plan()
    if not wp.is_current(plan, today):
        sys.stderr.write(
            f"No live weekly_plan.json for the current week (week_of={wp._week_of(today)}) — "
            f"nothing to review. Run weekly_plan_generator.py first.\n"
        )
        return 2

    scorecard = build_draft_scorecard(today, plan)

    if args.write_draft:
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(json.dumps(scorecard, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote draft to {draft_path}")

    if args.json:
        print(json.dumps(scorecard, indent=2))
    else:
        print(render_proposal(scorecard))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
