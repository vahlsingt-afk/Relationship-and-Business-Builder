"""RB-DEFECT-021 — Weekly planning layer.

Provides the data layer the Daily Brief was missing: an explicit set of
weekly outcomes, a portfolio allocation, and an operating-mode calendar.
The brief becomes a *consumer* of this layer (reads `weekly_plan.json`),
not the other way around.

This module owns:
  - the `weekly_plan.json` schema (`new_plan`, `load_plan`, `save_plan`)
  - the five-mode weekly operating calendar (`operating_mode_for`)
  - an outcome-alignment lookup used by the brief's scoring layer
    (`alignment_factor_for_item`)

It deliberately does NOT touch `daily_brief.py`'s assembly internals beyond
exposing small pure functions the brief can call.
"""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

SYSTEM_DIR = Path(__file__).resolve().parent.parent
WEEKLY_PLAN_PATH = SYSTEM_DIR / "weekly_plan.json"
WEEKLY_SCORECARD_DIR = SYSTEM_DIR / "weekly_scorecards"
SNAPSHOTS_DIR = SYSTEM_DIR / "_snapshots"

# --------------------------------------------------------------------------- #
# Operating-mode calendar (generalizes cos_judgment.build_weekend_guidance    #
# from a binary weekend/weekday split into the full five-mode weekly cycle)  #
# --------------------------------------------------------------------------- #

OPERATING_MODES = {
    "weekly_planning": "Monday — set this week's outcomes, allocation, and risks.",
    "execution": "Tue–Thu — execute against active weekly outcomes.",
    "weekly_review": "Friday — score the week: wins, misses, movement, lessons.",
    "recovery": "Saturday — recovery / family mode. Light loop closure only.",
    "weekly_prep": "Sunday — prepare the week ahead; confirm Monday priorities.",
}

_DEFAULT_CALENDAR = {
    0: "weekly_planning",   # Monday
    1: "execution",
    2: "execution",
    3: "execution",
    4: "weekly_review",     # Friday
    5: "recovery",          # Saturday
    6: "weekly_prep",       # Sunday
}


def operating_mode_for(today: date, calendar: dict[int, str] | None = None) -> dict[str, Any]:
    """DEFECT-021A — return today's operating mode + framing question.

    Generalizes cos_judgment.build_weekend_guidance's binary mode into the
    full five-mode weekly cycle. Every mode answers a framing question
    *before* the brief generates execution recommendations — this is a gate,
    not a ranking signal.
    """
    cal = calendar or _DEFAULT_CALENDAR
    mode = cal.get(today.weekday(), "execution")
    framing_questions = {
        "weekly_planning": "What would make this week successful?",
        "execution": "What should happen today given this week's goals?",
        "weekly_review": "How did we perform this week?",
        "recovery": "Should Todd work today, or is this a recovery day?",
        "weekly_prep": "What does next week need from this weekend?",
    }
    return {
        "mode": mode,
        "day": today.strftime("%A"),
        "framing_question": framing_questions[mode],
        "description": OPERATING_MODES[mode],
        "is_execution_day": mode == "execution",
        "suppress_aggressive_actions": mode in {"recovery", "weekly_prep"},
    }


# --------------------------------------------------------------------------- #
# weekly_plan.json schema                                                     #
# --------------------------------------------------------------------------- #

def _week_of(today: date) -> str:
    """Return the ISO date of the Monday on/before `today`."""
    return (today - timedelta(days=today.weekday())).isoformat()


def new_plan(today: date, outcomes: list[dict] | None = None,
             portfolio_allocation: dict[str, float] | None = None,
             risks: list[str] | None = None,
             forcing_functions: list[str] | None = None) -> dict[str, Any]:
    """Construct a new weekly_plan record. Pure — caller persists via save_plan."""
    return {
        "schema_version": "1.0",
        "week_of": _week_of(today),
        "created_at": datetime.utcnow().isoformat() + "Z",
        "operating_mode_calendar": {
            ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][k]: v
            for k, v in _DEFAULT_CALENDAR.items()
        },
        "outcomes": outcomes or [],
        "portfolio_allocation": portfolio_allocation or {},
        "risks": risks or [],
        "forcing_functions": forcing_functions or [],
    }


def make_outcome(outcome_id: str, title: str, success_criteria: str,
                 linked_opportunity_ids: list[str] | None = None,
                 linked_loop_ids: list[str] | None = None,
                 portfolio_allocation_pct: float = 0.0,
                 status: str = "active") -> dict[str, Any]:
    return {
        "id": outcome_id,
        "title": title,
        "success_criteria": success_criteria,
        "linked_opportunity_ids": linked_opportunity_ids or [],
        "linked_loop_ids": linked_loop_ids or [],
        "portfolio_allocation_pct": portfolio_allocation_pct,
        "status": status,  # active | advanced | stalled | closed
    }


def load_plan(path: Path = WEEKLY_PLAN_PATH) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_plan(plan: dict[str, Any], path: Path = WEEKLY_PLAN_PATH) -> None:
    """Overwrite the live weekly plan. RB-2026-08-24: this had no pre-write
    snapshot at all -- confirmed live when a testing mistake promoted a real
    draft ~8 hours before its intended end-of-day timing and there was no
    way to recover the prior state. Every other mutation path in this
    codebase (mutations.py's loop/baseline/thread writers) snapshots first;
    this brings weekly_plan.json in line. Best-effort: a snapshot failure
    must never block the actual save."""
    if path.exists():
        try:
            SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
            tag = datetime.now().strftime("pre-save-%Y%m%d-%H%M%S")
            shutil.copy2(path, SNAPSHOTS_DIR / f"{path.stem}.{tag}{path.suffix}")
        except OSError:
            pass
    path.write_text(json.dumps(plan, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def is_current(plan: dict[str, Any] | None, today: date) -> bool:
    """Whether `plan` is the active plan for the week containing `today`."""
    if not plan:
        return False
    return plan.get("week_of") == _week_of(today)


def active_outcomes(plan: dict[str, Any] | None) -> list[dict]:
    if not plan:
        return []
    return [o for o in plan.get("outcomes", []) if o.get("status") == "active"]


# --------------------------------------------------------------------------- #
# Outcome-alignment scoring hook (DEFECT-021B/021C)                           #
#                                                                              #
# This is the literal mechanism for "suppress vs. elevate": a multiplicative  #
# factor layered onto daily_brief._strategic_significance_score, NOT a        #
# replacement for it. Items linked to an active outcome get boosted           #
# proportional to that outcome's portfolio allocation; unlinked items are     #
# dampened so they don't crowd out strategic work by default.                 #
# --------------------------------------------------------------------------- #

#: Multiplier applied to items with no link to any active weekly outcome.
UNALIGNED_DAMPENING_FACTOR = 0.7

#: Multiplier applied to items that are aligned, scaled further by the
#: outcome's allocation share (so a 40%-allocation outcome elevates more
#: than a 10%-allocation one).
ALIGNED_BASE_FACTOR = 1.0
ALIGNED_ALLOCATION_BONUS_PER_PCT = 0.01  # +1% factor per allocation point


def _item_reference_ids(item: dict) -> set[str]:
    """Collect every id-shaped reference an item carries, for cross-matching
    against an outcome's linked_opportunity_ids / linked_loop_ids."""
    ids: set[str] = set()
    for key in ("opportunity_id", "loop_id", "id", "entity_id", "source_id"):
        val = item.get(key)
        if isinstance(val, str) and val:
            ids.add(val)
    for key in ("linked_opportunity_ids", "linked_loop_ids", "related_ids"):
        val = item.get(key)
        if isinstance(val, list):
            ids.update(v for v in val if isinstance(v, str))
    return ids


def matching_outcome(item: dict, plan: dict[str, Any] | None) -> dict | None:
    """Return the active outcome an item maps to, if any."""
    if not plan:
        return None
    item_ids = _item_reference_ids(item)
    if not item_ids:
        return None
    for outcome in active_outcomes(plan):
        linked = set(outcome.get("linked_opportunity_ids", [])) | set(outcome.get("linked_loop_ids", []))
        if item_ids & linked:
            return outcome
    return None


def alignment_factor_for_item(item: dict, plan: dict[str, Any] | None) -> tuple[float, dict | None]:
    """DEFECT-021B/021C — compute the multiplicative alignment factor for an item.

    Returns (factor, matched_outcome_or_None). Pure function — daily_brief
    applies it: `aligned_score = round(base_score * factor)`.

    - No current plan at all → factor 1.0 (no-op; system degrades gracefully
      when the weekly planning layer hasn't been run yet).
    - Item maps to an active outcome → boosted by the outcome's allocation share.
    - Item maps to nothing → dampened (suppression-by-default for noise).
    """
    if not plan:
        return 1.0, None
    outcome = matching_outcome(item, plan)
    if outcome is not None:
        pct = float(outcome.get("portfolio_allocation_pct", 0) or 0)
        return ALIGNED_BASE_FACTOR + (pct * ALIGNED_ALLOCATION_BONUS_PER_PCT), outcome
    return UNALIGNED_DAMPENING_FACTOR, None


def apply_alignment(score: int, item: dict, plan: dict[str, Any] | None) -> dict[str, Any]:
    """Convenience wrapper: score an item's outcome alignment and return a
    small dict the brief can merge onto the item for transparency/rendering."""
    factor, outcome = alignment_factor_for_item(item, plan)
    aligned_score = max(0, min(100, round(score * factor)))
    return {
        "base_significance_score": score,
        "alignment_factor": round(factor, 3),
        "aligned_significance_score": aligned_score,
        "aligned_outcome_id": outcome.get("id") if outcome else None,
        "aligned_outcome_title": outcome.get("title") if outcome else None,
    }


# --------------------------------------------------------------------------- #
# Maintenance-item display gate (DEFECT-021C)                                 #
# --------------------------------------------------------------------------- #

def maintenance_should_surface(item: dict, plan: dict[str, Any] | None, today: date) -> tuple[bool, str]:
    """Whether a maintenance-class loop should surface in today's brief.

    Per the design principle: maintenance items should only appear when
    due today, blocking an active strategic outcome, or creating
    reputational risk. Otherwise suppressed by policy regardless of score.
    """
    due = item.get("closure_target") or item.get("due")
    if isinstance(due, str) and due:
        try:
            if datetime.fromisoformat(due).date() <= today:
                return True, "due_today_or_overdue"
        except ValueError:
            pass

    blocks_id = item.get("blocks_outcome_id")
    if blocks_id and plan:
        if any(o.get("id") == blocks_id for o in active_outcomes(plan)):
            return True, "blocks_active_outcome"

    if item.get("reputational_risk"):
        return True, "reputational_risk"

    return False, "suppressed_low_priority_maintenance"


# --------------------------------------------------------------------------- #
# Friday review scorecard (DEFECT-021, "Friday Review" horizon)               #
# --------------------------------------------------------------------------- #

def make_scorecard(plan: dict[str, Any], outcome_movements: dict[str, str],
                    relationship_movement: str = "", lessons: list[str] | None = None,
                    overall_score: int | None = None) -> dict[str, Any]:
    """Build a Friday scorecard record from a week's plan + observed movement.

    `outcome_movements` maps outcome_id -> one of "advanced" | "no_movement" | "stalled" | "closed".
    """
    wins = [o["title"] for o in plan.get("outcomes", [])
            if outcome_movements.get(o["id"]) in {"advanced", "closed"}]
    misses = [o["title"] for o in plan.get("outcomes", [])
              if outcome_movements.get(o["id"]) in {"no_movement", "stalled"}]
    return {
        "week_of": plan.get("week_of"),
        "scored_at": datetime.utcnow().isoformat() + "Z",
        "wins": wins,
        "misses": misses,
        "outcome_movements": outcome_movements,
        "relationship_movement": relationship_movement,
        "lessons": lessons or [],
        "overall_score": overall_score,
    }


def save_scorecard(scorecard: dict[str, Any], directory: Path = WEEKLY_SCORECARD_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    out_path = directory / f"scorecard_{scorecard['week_of']}.json"
    out_path.write_text(json.dumps(scorecard, indent=2) + "\n", encoding="utf-8")
    return out_path


def load_recent_scorecards(n: int = 4, directory: Path = WEEKLY_SCORECARD_DIR) -> list[dict]:
    if not directory.exists():
        return []
    files = sorted(directory.glob("scorecard_*.json"), reverse=True)[:n]
    out = []
    for f in files:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out
