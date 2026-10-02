#!/usr/bin/env python3
"""
friday_eow_routine.py — Friday end-of-week close-out (RB-2026-08-24, gap #9b).

Todd's ask: review the weekly plan, close loops, surface important reminders/
actions from the week, and make sure documentation/Blue Sheets/records are up
to date and backed up — plus, separately: a freshness check ensuring key
files are updated at least weekly.

Deliberately review-and-surface, not auto-fix: closing a loop or rewriting a
narrative document (e.g. network_map.md) requires human judgment. This
script's job is to make sure nothing goes silently stale again, the same way
brief_acceptance_check.py exists so a degraded daily brief can't ship quietly
-- applied to a weekly cadence instead of daily.

Four sections, each best-effort: one section's failure never blocks the
others (matches the "additive, never blocks" discipline used throughout this
codebase's other hooks).

  1. Weekly plan review    -- reads weekly_plan.json + weekly_scorecard_draft.json
  2. Loop review            -- L- overdue (rb_core.loops_by_status) + EL- stale
                                (eolms.py's own staleness filter, reused not
                                reimplemented) -- surfaced, never auto-closed
  3. Freshness check        -- a small explicit registry of "should move at
                                least weekly" files/records, checked by mtime
                                or (for Blue Sheets) last_review_date, against
                                a 7-day threshold
  4. Backup                 -- copies core memory files into a dated folder
                                under system/_backups/<date>/ (the existing
                                publish.py --send "backup" path is dead code:
                                unscheduled, disabled by settings, disabled by
                                an env-gate -- this is a real one)
  5. Review-queue staleness -- intelligence_cascade.py (RB-2026-08-27/28)
                                deliberately exempts genuinely ambiguous
                                downstream matches (Blue Sheet, Master
                                Account Plan, Competitor Intelligence
                                review_queue.json entries) from its 24h
                                auto-apply SLA -- by design, an aging clock
                                must never turn a judgment call into an
                                auto-apply. That's correct, but nothing
                                watched the queues themselves for age, so a
                                pending item could sit unnoticed for weeks.
                                Surfaced here, against the same 7-day
                                threshold as the freshness check -- never
                                auto-resolved.
  6. Weekly close-out       -- Todd's ask, 2026-09-04: make sure the week's
                                plan actually gets closed out, not just left
                                to be silently overwritten the following
                                Monday. Any outcome whose linked loop(s) are
                                still open at week's end is written to
                                weekly_plan_carryover.json, which
                                weekly_plan_generator.py reads so the SAME
                                outcome (title, success criteria, and intent)
                                reappears in next week's draft under its own
                                name instead of being swallowed into the
                                generic "close out overdue loops" bucket.
                                Deliberately does NOT auto-confirm the
                                scorecard draft -- weekly_review_generator.py's
                                own docstring is explicit that self-grading
                                the week without a human check is exactly the
                                silent-auto-commit this architecture avoids;
                                this only surfaces that it's ready and waiting.

Usage:
    python3 friday_eow_routine.py            # human-readable summary
    python3 friday_eow_routine.py --json      # JSON result
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import eolms  # noqa: E402
import weekly_planning as wp  # noqa: E402

RESULT_PATH = core.SYSTEM_DIR / ".cache" / "friday_closeout_result.json"
REPORT_PATH = core.SYSTEM_DIR / "friday_closeout.md"
BACKUPS_DIR = core.SYSTEM_DIR / "_backups"

WEEKLY_PLAN_PATH = core.SYSTEM_DIR / "weekly_plan.json"
WEEKLY_SCORECARD_DRAFT_PATH = core.SYSTEM_DIR / "weekly_scorecard_draft.json"
# Written here, consumed by weekly_plan_generator.py's generate_candidates(),
# cleared by its confirm_draft() once actually promoted into a live plan.
WEEKLY_PLAN_CARRYOVER_PATH = core.SYSTEM_DIR / "weekly_plan_carryover.json"
# RB-2026-09-07: repointed from blue_sheets/_portfolio/blue_sheet_registry.json
# and blue_sheets/accounts/ -- both frozen at their pre-migration state since
# the 3-store unification (2026-09-06/07) moved every real read/write onto
# customers_prospects/. The old paths still exist on disk but receive no
# further updates; reading them here silently blinded this sweep to all
# pre-engagement accounts and any post-migration active-engagement activity.
CUSTOMERS_PROSPECTS_REGISTRY_PATH = core.SYSTEM_DIR.parent / "customers_prospects" / "_portfolio" / "customers_prospects_registry.json"
CUSTOMERS_PROSPECTS_ACCOUNTS_DIR = core.SYSTEM_DIR.parent / "customers_prospects" / "accounts"

FRESHNESS_THRESHOLD_DAYS = 7

# Every review_queue.json in the system -- ambiguous downstream matches
# that intelligence_cascade.py (and the engines it calls) deliberately
# leave for Todd to resolve by hand, with no auto-apply SLA. Reusing
# FRESHNESS_THRESHOLD_DAYS rather than a second constant: same 7-day
# cadence this was originally asked about (a weekly sweep), and one
# threshold to keep in sync rather than two that can drift apart.
REVIEW_QUEUE_PATHS = [
    core.SYSTEM_DIR.parent / "blue_sheets" / "_portfolio" / "review_queue.json",
    core.SYSTEM_DIR.parent / "master_account_plans" / "_portfolio" / "review_queue.json",
    core.SYSTEM_DIR / "competitor_intelligence" / "_portfolio" / "review_queue.json",
]

# Core memory files checked for freshness (mtime-based). Deliberately excludes
# architecture/design docs (ARCHITECTURE.md, SCHEMAS.md, WHAT_PERSISTS.md,
# the two dated maps) -- those correctly change rarely; flagging them
# weekly-stale would be noise, not signal. Also excludes today.md, which
# updates daily by design and would always trivially pass.
FRESHNESS_FILES = [
    core.SYSTEM_DIR / "baseline_index.json",
    core.SYSTEM_DIR / "ecosystem_intelligence.json",
    core.SYSTEM_DIR / "interaction_ledger.json",
    core.SYSTEM_DIR / "network_map.md",
    core.SYSTEM_DIR / "intro_brokers.md",
]

# Files backed up weekly into system/_backups/<date>/. Active-engagement
# customers_prospects/ account files are added dynamically for every
# activated (real Blue Sheet workbook) account (see backup()).
BACKUP_FILES = [
    core.SYSTEM_DIR / "baseline_index.json",
    core.SYSTEM_DIR / "ecosystem_intelligence.json",
    WEEKLY_PLAN_PATH,
    CUSTOMERS_PROSPECTS_REGISTRY_PATH,
    core.SYSTEM_DIR / "CANONICAL_REGISTRY.yaml",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _display_path(path: Path) -> str:
    """Repo-relative path for display when possible; falls back to the raw
    path otherwise (e.g. in tests, where FRESHNESS_FILES/BACKUP_FILES are
    monkeypatched to tmp_path locations outside the real repo root)."""
    try:
        return str(path.relative_to(core.SYSTEM_DIR.parent))
    except ValueError:
        return str(path)


def review_weekly_plan() -> dict:
    plan = _load_json(WEEKLY_PLAN_PATH)
    scorecard = _load_json(WEEKLY_SCORECARD_DRAFT_PATH)
    return {
        "plan_week_of": (plan or {}).get("week_of"),
        "plan_status": (plan or {}).get("status"),
        "scorecard_week_of": (scorecard or {}).get("week_of"),
        "scorecard_status": (scorecard or {}).get("status"),
        "wins": len((scorecard or {}).get("wins") or []),
        "misses": len((scorecard or {}).get("misses") or []),
        "overall_score": (scorecard or {}).get("overall_score"),
    }


def review_loops(*, today: date | None = None) -> dict:
    today = today or date.today()
    result = {"l_overdue": [], "el_stale": [], "error": None}
    try:
        # RB-2026-08-24: parse_loop_ledger(path=LOOP_LEDGER_PATH)'s default
        # is bound at def-time -- patching core.LOOP_LEDGER_PATH afterward
        # (the pattern tests use) silently has no effect on a bare no-arg
        # call. Reading the attribute live and passing it explicitly is what
        # actually makes this testable, matching the lesson already learned
        # elsewhere in this codebase today with core.load_baseline().
        loops = core.parse_loop_ledger(path=core.LOOP_LEDGER_PATH)
        bucketed = core.loops_by_status(loops, today)
        result["l_overdue"] = [
            {"id": L.id, "party": L.party, "description": L.description, "target": L.target.isoformat()}
            for L in bucketed["overdue"]
        ]
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"L- loop review failed: {exc}"

    try:
        eloops = eolms._load()  # noqa: SLF001
        stale = []
        for l in eloops:
            threshold = l.dormancy_threshold()
            if l.status not in ("active", "waiting") or threshold is None:
                continue
            if l.days_since_activity > core.EOLMS_STALENESS_WARNING_DAYS:
                # Matches eolms.py cmd_stale()'s own display convention: the
                # inclusion filter is the fixed EOLMS_STALENESS_WARNING_DAYS
                # (45d), but each loop also carries its own, separate
                # dormancy_threshold() -- flag explicitly when a loop has
                # ALSO passed its own threshold, since "53d, threshold 90d"
                # reads as contradictory without this.
                stale.append({
                    "id": l.id, "title": l.title,
                    "days_since_activity": l.days_since_activity, "threshold": threshold,
                    "past_own_dormancy_threshold": l.days_since_activity > threshold,
                })
        stale.sort(key=lambda d: -d["days_since_activity"])
        result["el_stale"] = stale
    except Exception as exc:  # noqa: BLE001
        prior = result.get("error")
        result["error"] = f"{prior}; EL- loop review failed: {exc}" if prior else f"EL- loop review failed: {exc}"

    return result


def _days_since_mtime(path: Path, *, today: date) -> int | None:
    if not path.exists():
        return None
    mtime_date = date.fromtimestamp(path.stat().st_mtime)
    return (today - mtime_date).days


def check_freshness(*, today: date | None = None) -> list[dict]:
    today = today or date.today()
    findings: list[dict] = []

    for path in FRESHNESS_FILES:
        days = _days_since_mtime(path, today=today)
        findings.append({
            "target": _display_path(path),
            "days_since_updated": days,
            "stale": (days is None) or (days > FRESHNESS_THRESHOLD_DAYS),
            "reason": "missing" if days is None else None,
        })

    try:
        registry = json.loads(CUSTOMERS_PROSPECTS_REGISTRY_PATH.read_text(encoding="utf-8"))
        for entry in registry.get("registry", []):
            if entry.get("workbook_path") is None:
                continue  # not activated -- no Blue Sheet content to go stale
            last_review = entry.get("last_review_date")
            days = None
            if last_review:
                try:
                    days = (today - date.fromisoformat(last_review)).days
                except ValueError:
                    days = None
            findings.append({
                "target": f"customers_prospects/accounts/{entry.get('account_id', '')} (last_review_date)",
                "days_since_updated": days,
                "stale": (days is None) or (days > FRESHNESS_THRESHOLD_DAYS),
                "reason": "missing last_review_date" if days is None else None,
            })
    except (OSError, json.JSONDecodeError) as exc:
        findings.append({"target": "customers_prospects_registry.json", "days_since_updated": None,
                          "stale": True, "reason": f"could not read registry: {exc}"})

    return findings


def _item_key(item: dict) -> str:
    return (
        item.get("account_id")
        or item.get("vendor_slug")
        or item.get("competitor_slug")
        or "unknown"
    )


def review_review_queues(*, today: date | None = None) -> list[dict]:
    today = today or date.today()
    findings: list[dict] = []

    for path in REVIEW_QUEUE_PATHS:
        queue = _load_json(path)
        if queue is None:
            continue
        for item in queue.get("pending_reviews", []):
            if item.get("status") != "pending":
                continue
            queued_at = item.get("queued_at")
            days = None
            if queued_at:
                try:
                    days = (today - datetime.fromisoformat(queued_at.replace("Z", "+00:00")).date()).days
                except ValueError:
                    days = None
            findings.append({
                "queue": _display_path(path),
                "item": _item_key(item),
                "kind": item.get("kind"),
                "reason": item.get("reason"),
                "queued_at": queued_at,
                "days_pending": days,
                # No queued_at at all means we genuinely can't tell how long
                # it's been sitting -- treated as stale rather than silently
                # passing, same convention check_freshness() already uses
                # for a missing mtime/last_review_date.
                "stale": (days is None) or (days > FRESHNESS_THRESHOLD_DAYS),
            })

    findings.sort(key=lambda f: (f["days_pending"] is None, -(f["days_pending"] or 0)))
    return findings


def weekly_closeout(*, today: date | None = None) -> dict:
    """Todd's ask, 2026-09-04: make sure the Friday routine actually closes
    out the week's plan, and reassigns any still-open loops to next week's
    plan, rather than letting weekly_plan.json just get silently overwritten
    the following Monday with no memory of what didn't get done.

    Writes WEEKLY_PLAN_CARRYOVER_PATH -- a proposal weekly_plan_generator.py
    reads on its next run, not a live mutation of anything -- so this is
    safe to run unattended. Deliberately does NOT confirm the scorecard
    draft: weekly_review_generator.py's own docstring says self-grading the
    week without a human check is exactly the silent-auto-commit this
    architecture avoids, so this only reports whether one is ready.
    """
    today = today or date.today()
    plan = wp.load_plan(WEEKLY_PLAN_PATH)
    if not wp.is_current(plan, today):
        return {
            "plan_week_of": (plan or {}).get("week_of"),
            "skipped_reason": "no live plan for the current week",
            "carryover_outcomes": [],
        }

    try:
        loops = core.parse_loop_ledger(path=core.LOOP_LEDGER_PATH)
        loop_closed_by_id = {L.id: L.closed for L in loops}
    except Exception:
        loop_closed_by_id = {}

    carryover_outcomes = []
    for outcome in plan.get("outcomes", []):
        linked = outcome.get("linked_loop_ids") or []
        # Only a loop we can actually confirm is still open counts -- an id
        # not found in the ledger at all is skipped rather than assumed
        # open, so a typo'd/retired loop id can't manufacture a carryover.
        still_open = [lid for lid in linked if loop_closed_by_id.get(lid) is False]
        if not still_open:
            continue
        carryover_outcomes.append({
            "id": outcome["id"],
            "title": outcome["title"],
            "success_criteria": outcome.get("success_criteria", ""),
            "linked_opportunity_ids": outcome.get("linked_opportunity_ids") or [],
            "linked_loop_ids": linked,
            "still_open_loop_ids": still_open,
            "prior_allocation_pct": outcome.get("portfolio_allocation_pct"),
            "prior_status": outcome.get("status"),
        })

    if carryover_outcomes:
        WEEKLY_PLAN_CARRYOVER_PATH.write_text(json.dumps({
            "from_week_of": plan.get("week_of"),
            "generated_at": _now_iso(),
            "carryover_outcomes": carryover_outcomes,
        }, indent=2) + "\n", encoding="utf-8")

    scorecard = _load_json(WEEKLY_SCORECARD_DRAFT_PATH)
    scorecard_ready = bool(
        scorecard
        and scorecard.get("week_of") == plan.get("week_of")
        and scorecard.get("status") == "draft_pending_confirmation"
    )

    return {
        "plan_week_of": plan.get("week_of"),
        "skipped_reason": None,
        "carryover_outcomes": carryover_outcomes,
        "scorecard_ready_for_confirmation": scorecard_ready,
    }


def backup(*, today: date | None = None) -> dict:
    today = today or date.today()
    dest_dir = BACKUPS_DIR / today.isoformat()
    copied: list[str] = []
    failed: list[dict] = []

    files_to_copy = list(BACKUP_FILES)
    try:
        registry = json.loads(CUSTOMERS_PROSPECTS_REGISTRY_PATH.read_text(encoding="utf-8"))
        for entry in registry.get("registry", []):
            if entry.get("workbook_path") is None:
                continue
            slug = entry.get("account_id", "").removeprefix("acct-")
            acct_dir = CUSTOMERS_PROSPECTS_ACCOUNTS_DIR / slug
            files_to_copy.append(acct_dir / "account.json")
            files_to_copy.append(acct_dir / "brand_profile.json")
    except (OSError, json.JSONDecodeError):
        pass  # backup proceeds with the static file list even if the registry can't be read

    for src in files_to_copy:
        try:
            if not src.exists():
                failed.append({"path": str(src), "reason": "does not exist"})
                continue
            dest_dir.mkdir(parents=True, exist_ok=True)
            rel_name = src.name if src.parent == core.SYSTEM_DIR else f"{src.parent.name}__{src.name}"
            shutil.copy2(src, dest_dir / rel_name)
            copied.append(_display_path(src))
        except OSError as exc:
            failed.append({"path": str(src), "reason": str(exc)})

    return {"dest_dir": str(dest_dir), "copied_count": len(copied), "copied": copied, "failed": failed}


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def run(*, today: date | None = None) -> dict:
    today = today or date.today()
    return {
        "checked_at": _now_iso(),
        "date": today.isoformat(),
        "weekly_plan": review_weekly_plan(),
        "loops": review_loops(today=today),
        "freshness": check_freshness(today=today),
        "review_queues": review_review_queues(today=today),
        "weekly_closeout": weekly_closeout(today=today),
        "backup": backup(today=today),
    }


def _save_result(result: dict) -> None:
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = RESULT_PATH.with_suffix(RESULT_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, RESULT_PATH)


def _render_report(result: dict) -> str:
    wp = result["weekly_plan"]
    loops = result["loops"]
    stale_freshness = [f for f in result["freshness"] if f["stale"]]
    backup_info = result["backup"]

    lines = [f"# Friday Close-out — {result['date']}\n"]
    lines.append(f"*Generated {result['checked_at']}*\n")

    lines.append("## Weekly Plan\n")
    lines.append(f"- Plan: week_of={wp['plan_week_of']}, status={wp['plan_status']}")
    lines.append(f"- Scorecard: week_of={wp['scorecard_week_of']}, status={wp['scorecard_status']}, "
                  f"{wp['wins']} win(s), {wp['misses']} miss(es), score={wp['overall_score']}\n")

    lines.append("## Loops needing a decision\n")
    if loops.get("error"):
        lines.append(f"*{loops['error']}*")
    if loops["l_overdue"]:
        lines.append(f"**{len(loops['l_overdue'])} overdue (L-):**")
        for l in loops["l_overdue"]:
            lines.append(f"- {l['id']} — {l['party']}: {l['description']} (target {l['target']})")
    else:
        lines.append("No overdue L- loops.")
    if loops["el_stale"]:
        lines.append(f"\n**{len(loops['el_stale'])} stale (EL-, >45d since activity):**")
        for l in loops["el_stale"]:
            flag = " — PAST OWN DORMANCY THRESHOLD" if l.get("past_own_dormancy_threshold") else ""
            lines.append(f"- {l['id']} — {l['title']} ({l['days_since_activity']}d since activity, "
                          f"own dormancy threshold {l['threshold']}d{flag})")
    else:
        lines.append("\nNo stale EL- loops.")
    lines.append("")

    lines.append("## Freshness (>7d since last update)\n")
    if stale_freshness:
        for f in stale_freshness:
            days_str = f"{f['days_since_updated']}d" if f['days_since_updated'] is not None else "unknown"
            reason = f" — {f['reason']}" if f.get("reason") else ""
            lines.append(f"- ⚠ **{f['target']}** — {days_str}{reason}")
    else:
        lines.append("Everything checked is within 7 days.")
    lines.append("")

    lines.append("## Downstream Review Queues (ambiguous matches awaiting Todd)\n")
    stale_reviews = [f for f in result["review_queues"] if f["stale"]]
    if stale_reviews:
        for f in stale_reviews:
            days_str = f"{f['days_pending']}d" if f["days_pending"] is not None else "unknown age"
            lines.append(f"- ⚠ **{f['item']}** ({f['queue']}, {f['kind']}) — pending {days_str}: {f['reason']}")
    else:
        lines.append(f"No pending review-queue item older than {FRESHNESS_THRESHOLD_DAYS}d.")
    lines.append("")

    lines.append("## Weekly Close-out\n")
    wc = result["weekly_closeout"]
    if wc.get("skipped_reason"):
        lines.append(f"*{wc['skipped_reason']} (week_of={wc.get('plan_week_of')}) — nothing to close out.*")
    else:
        carryover = wc["carryover_outcomes"]
        if carryover:
            lines.append(f"**{len(carryover)} outcome(s) carried forward to next week's draft** "
                          f"(still-open loop(s) reassigned):")
            for co in carryover:
                lines.append(f"- {co['title']} — {', '.join(co['still_open_loop_ids'])}")
        else:
            lines.append("No outcome has a still-open linked loop — nothing to carry forward.")
        if wc.get("scorecard_ready_for_confirmation"):
            lines.append("\n⚠ Scorecard draft is ready and awaiting confirmation — "
                          "confirm via chat or `weekly_review_generator.py --confirm`.")
    lines.append("")

    lines.append("## Backup\n")
    lines.append(f"Copied {backup_info['copied_count']} file(s) to `{backup_info['dest_dir']}`.")
    if backup_info["failed"]:
        lines.append(f"\n**{len(backup_info['failed'])} could not be backed up:**")
        for f in backup_info["failed"]:
            lines.append(f"- {f['path']} — {f['reason']}")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    today = date.fromisoformat(args.date) if args.date else date.today()
    result = run(today=today)
    _save_result(result)
    REPORT_PATH.write_text(_render_report(result), encoding="utf-8")

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(_render_report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
