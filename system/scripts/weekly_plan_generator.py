#!/usr/bin/env python3
"""RB-DEFECT-021 — Monday plan-generation orchestrator.

Generates a *draft* weekly plan from existing system-of-record data — active
threads, the opportunity board, and the loop ledger — and writes it to
`weekly_plan_draft.json` pending human confirmation. This is deliberately a
propose-then-confirm flow (mirrors the mutation-engine and opportunity-intake
patterns elsewhere in RB): outcome selection is a strategic-judgment call,
not something RB should silently auto-commit to.

Without this orchestrator, `weekly_plan.json` never gets created, and
RB-DEFECT-021's entire outcome-alignment scoring layer runs in permanent
neutral-passthrough — exactly what the brief now honestly reports via
`weekly_plan_focus`.

Usage:
    python3 weekly_plan_generator.py                  # propose a draft, print it
    python3 weekly_plan_generator.py --write-draft    # persist the draft to disk
    python3 weekly_plan_generator.py --confirm        # promote draft -> live weekly_plan.json
    python3 weekly_plan_generator.py --confirm --draft-path <path>
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core
import weekly_planning as wp

DRAFT_PATH = core.SYSTEM_DIR / "weekly_plan_draft.json"

# Written by friday_eow_routine.py's Friday close-out when an outcome's
# linked loop(s) are still open at week's end -- read here so an unresolved
# commitment reappears in next week's draft under its OWN title/success
# criteria instead of being swallowed into _candidate_from_overdue_loops'
# generic "Close out overdue and due-today loops" bucket, which loses the
# original context entirely. Cleared by confirm_draft() once the carryover
# has actually been promoted into a live plan -- never cleared just because
# a draft was regenerated, so re-running the generator before confirming
# can't silently drop it.
CARRYOVER_PATH = core.SYSTEM_DIR / "weekly_plan_carryover.json"

#: Candidate outcome generation is capped — a weekly plan with 8 "priorities"
#: is not a plan, it's a wishlist. Per DEFECT-021's design principle, force
#: an actual allocation choice.
MAX_CANDIDATE_OUTCOMES = 5

OPPORTUNITY_THREAD_TYPES = {"job_opportunity", "opportunity", "recruiter_engagement", "partnership"}
# Threads in these states are finished — every thread-derived generator
# (candidates, risks, forcing functions) must exclude them, or a thread
# closed days ago keeps being proposed/flagged as if still open. See
# generate_candidates()'s docstring for the specific incident this covers.
_DEAD_THREAD_STATUSES = {"closed", "done", "complete", "dead", "abandoned"}


def _thread_is_active(thread: dict) -> bool:
    return (thread.get("status") or "").lower() not in _DEAD_THREAD_STATUSES


def _load_threads() -> list[dict]:
    try:
        return core.load_active_threads()
    except Exception:
        return []


def _candidate_from_thread(thread: dict, idx: int) -> dict | None:
    name = thread.get("name") or thread.get("title") or thread.get("company")
    if not name:
        return None
    companies = thread.get("companies") or ([thread.get("company")] if thread.get("company") else [])
    next_step = thread.get("next_step")
    # Thread `status` is a state token (open/closed/stalled), not a sentence —
    # only use it as a fallback if it actually reads like guidance.
    status = thread.get("status")
    status_is_sentence = isinstance(status, str) and len(status.split()) > 3
    success_criteria = (
        next_step or (status if status_is_sentence else None)
        or f"Move {name} to its next concrete milestone this week."
    )
    return {
        "id": f"outcome-thread-{thread.get('id') or idx}",
        "title": f"Advance {name}",
        "success_criteria": success_criteria,
        "linked_opportunity_ids": [thread.get("id")] if thread.get("id") else [],
        "_companies": [c for c in companies if c],
        "_boost": thread.get("boost_for_brief"),
        "_source": "active_threads",
    }


def _load_carryover_candidates() -> list[dict]:
    """Outcomes friday_eow_routine.py flagged as still-open at week's end.
    Ranked ahead of everything else in generate_candidates() -- a broken
    commitment from last week is action debt and should outrank a fresh
    idea this week, the same principle the brief itself already states for
    overdue loops."""
    if not CARRYOVER_PATH.exists():
        return []
    try:
        data = json.loads(CARRYOVER_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for co in data.get("carryover_outcomes") or []:
        loop_ids = co.get("still_open_loop_ids") or []
        if not loop_ids:
            continue
        out.append({
            "id": co["id"],
            "title": co["title"],
            "success_criteria": (
                f"Carried over from week of {data.get('from_week_of')}, still not resolved: "
                f"{co['success_criteria']}"
            ),
            "linked_opportunity_ids": co.get("linked_opportunity_ids") or [],
            "linked_loop_ids": loop_ids,
            "_companies": [],
            "_boost": "high",
            "_source": "carryover",
        })
    return out


def _candidate_from_overdue_loops(loops_by_status: dict, *, exclude_loop_ids: set[str] = frozenset()) -> dict | None:
    overdue = loops_by_status.get("overdue") or []
    due_today = loops_by_status.get("due_today") or []
    ids = [getattr(L, "id", None) or getattr(L, "loop_id", None) for L in (overdue + due_today)]
    ids = [i for i in ids if i and i not in exclude_loop_ids]
    count = len(ids)
    if not count:
        return None
    return {
        "id": "outcome-loop-stability",
        "title": "Close out overdue and due-today loops",
        "success_criteria": (
            f"Resolve all {count} overdue/due-today loop(s) — close, re-date with "
            f"reason, or explicitly defer with a new target."
        ),
        "linked_loop_ids": ids,
        "_companies": [],
        "_boost": "high" if overdue else "medium",
        "_source": "loop_ledger",
    }


def generate_candidates(today: date) -> list[dict]:
    """Derive candidate outcomes from system-of-record data. Pure function —
    does not write anything. Returns ranked candidates (highest-signal first),
    each carrying provenance (`_source`) so a human reviewer can see *why*
    RB proposed it, not just what it proposed."""
    carryover = _load_carryover_candidates()
    carryover_opp_ids = {oid for c in carryover for oid in c.get("linked_opportunity_ids") or []}
    carryover_loop_ids = {lid for c in carryover for lid in c.get("linked_loop_ids") or []}

    candidates: list[dict] = []

    threads = _load_threads()
    # Threads marked closed/dead in active_threads.yaml were never excluded
    # here — a thread closed days ago (e.g. Patrick Nelson / Matrix Software
    # Solutions, closed 2026-07-03) still got proposed as "Advance Patrick
    # Nelson / Matrix Software Solutions follow-up" in a plan drafted three
    # days later, burning a portfolio allocation slot on already-finished
    # work and reading as if RB didn't know it was closed.
    #
    # A thread whose id is already covered by a carryover outcome is also
    # excluded here -- otherwise the same real-world thing (e.g. Pollo
    # Campero) gets proposed twice: once as its carried-forward outcome with
    # full prior context, once again as a fresh, context-free "Advance
    # Pollo Campero" thread candidate.
    opp_threads = [
        t for t in threads
        if t.get("type") in OPPORTUNITY_THREAD_TYPES and _thread_is_active(t)
        and t.get("id") not in carryover_opp_ids
    ]
    # Rank: high-boost threads first, then everything else, cap before loops
    # so operational stability always has a guaranteed seat at the table.
    high = [t for t in opp_threads if t.get("boost_for_brief") == "high"]
    rest = [t for t in opp_threads if t.get("boost_for_brief") != "high"]
    for i, t in enumerate(high + rest):
        c = _candidate_from_thread(t, i)
        if c:
            candidates.append(c)

    try:
        loops = core.parse_loop_ledger()
        buckets = core.loops_by_status(loops, today)
    except Exception:
        buckets = {}
    # Loops already named individually by a carryover outcome are excluded
    # here too -- otherwise the same overdue loop appears both under its
    # real outcome title and again folded into the generic bucket.
    loop_candidate = _candidate_from_overdue_loops(buckets, exclude_loop_ids=carryover_loop_ids)

    # Rank: carried-forward outcomes first -- a broken commitment from last
    # week is action debt and should outrank a fresh idea this week -- then
    # top 2 thread-derived outcomes, then loop stability (if any), then
    # remaining thread-derived outcomes, capped at MAX_CANDIDATE_OUTCOMES.
    ordered = list(carryover)
    ordered += candidates[:2]
    if loop_candidate:
        ordered.append(loop_candidate)
    ordered += candidates[2:]
    return ordered[:MAX_CANDIDATE_OUTCOMES]


def _allocate(candidates: list[dict]) -> list[float]:
    """Simple, transparent allocation: high-boost / loop-stability candidates
    get a larger share; remainder split evenly. Always sums to 100. This is a
    *starting proposal* — the whole point of propose-then-confirm is that the
    human adjusts it to match actual judgment, not that RB gets it perfect."""
    if not candidates:
        return []
    n = len(candidates)
    weights = []
    for c in candidates:
        if c.get("_boost") == "high" or c.get("_source") == "loop_ledger":
            weights.append(2.0)
        else:
            weights.append(1.0)
    total_w = sum(weights)
    pcts = [round(100.0 * w / total_w) for w in weights]
    # Correct rounding drift so allocations sum to exactly 100.
    drift = 100 - sum(pcts)
    if pcts:
        pcts[0] += drift
    return [float(p) for p in pcts]


def _generate_risks(today: date, candidates: list[dict]) -> list[dict]:
    """RB-9.64: Auto-generate weekly risks from opportunity decay, overdue loops,
    DRR decay alerts, and stale relationships. Returns a list of risk dicts."""
    risks: list[dict] = []
    try:
        # Opportunity momentum decay — threads with high boost but no recent activity
        threads = _load_threads()
        for t in threads:
            if not _thread_is_active(t):
                continue
            # Threads use "opened" as the primary date anchor; fall back to
            # last_contact_at / updated_at if available.
            last_contact = (t.get("last_contact_at") or t.get("updated_at")
                            or t.get("opened") or "")
            if not last_contact:
                continue
            try:
                from datetime import datetime as _dt
                age_days = (today - _dt.fromisoformat(last_contact[:10]).date()).days
                company = (t.get("company")
                           or (t.get("companies") or [None])[0]
                           or t.get("name") or t.get("title") or "Unknown")
                if age_days >= 14 and t.get("boost_for_brief") in ("high", "medium"):
                    risks.append({
                        "id": f"risk-decay-{t.get('id', company.lower().replace(' ', '-'))}",
                        "title": f"Opportunity momentum decay: {company}",
                        "description": (
                            f"{company} — {age_days}d since last contact. "
                            f"Without action this week, momentum risk increases."
                        ),
                        "severity": "high" if age_days >= 21 else "medium",
                        "source": "active_threads",
                    })
            except Exception:
                continue
        # RB-2026-08-25: the DRR-relationship-decay risk type that used to
        # be generated here ("Relationship at risk: {name}") was removed --
        # confirmed live, it was a redundant, weekly-stale duplicate of
        # daily_brief.py's own DRR decay alert block (_compute_my_
        # priorities section 6a), which reads the exact same drr_score.json
        # and produces a strictly better version of the same signal: it
        # runs daily (not just when the weekly plan regenerates), uses a
        # dual recency+score threshold instead of recency alone, excludes
        # contacts already covered by an active opportunity loop, and has
        # real cooldown logic so the same contact isn't re-pushed every
        # day. Both fed into My Priorities under two different title
        # formats ("Relationship at risk: Jeff Coffland" vs "↘ Jeff
        # Coffland") that didn't dedup against each other, so the same
        # person rendered twice. Fixing the render-layer symptom (My
        # Priorities dedup by contact name) was done first; this is the
        # source-level fix -- one canonical generator for this signal, not
        # two independently computing it from the same file. The
        # "Opportunity momentum decay" risk type above is untouched: it
        # isn't duplicated anywhere else.
    except Exception:  # noqa: BLE001
        pass
    return risks[:4]  # cap at 4 risks


def _generate_forcing_functions(today: date, candidates: list[dict]) -> list[dict]:
    """RB-9.64: Auto-generate forcing functions from opportunity deadlines,
    overdue loops crossing decay thresholds, and time-sensitive signals."""
    ffs: list[dict] = []
    try:
        # Loops overdue by 7+ days are forcing functions — they need resolution
        loops = core.parse_loop_ledger()
        buckets = core.loops_by_status(loops, today)
        overdue = buckets.get("overdue") or []
        for loop in overdue[:2]:
            due = getattr(loop, "due_date", None) or getattr(loop, "target_date", None)
            if due:
                try:
                    from datetime import datetime as _dt2
                    due_date = _dt2.fromisoformat(str(due)[:10]).date()
                    age_days = (today - due_date).days
                    if age_days >= 7:
                        title = getattr(loop, "title", None) or getattr(loop, "subject", str(loop))
                        ffs.append({
                            "id": f"ff-overdue-{getattr(loop, 'id', 'loop')}",
                            "title": f"Overdue {age_days}d: {str(title)[:60]}",
                            "description": (
                                f"This loop is {age_days} days overdue. "
                                f"Resolve or explicitly defer with a new date by end of week."
                            ),
                            "deadline": str(due),
                            "source": "loop_ledger",
                        })
                except Exception:
                    continue
        # Opportunity stall at 21d is a structural forcing function
        threads = _load_threads()
        for t in threads:
            if not _thread_is_active(t):
                continue
            last_contact = (t.get("last_contact_at") or t.get("updated_at")
                            or t.get("opened") or "")
            if not last_contact:
                continue
            try:
                from datetime import datetime as _dt3
                age_days = (today - _dt3.fromisoformat(last_contact[:10]).date()).days
                company = (t.get("company")
                           or (t.get("companies") or [None])[0]
                           or t.get("name") or t.get("title") or "Unknown")
                if age_days >= 21 and t.get("boost_for_brief") == "high":
                    ffs.append({
                        "id": f"ff-stall-{t.get('id', company.lower().replace(' ', '-'))}",
                        "title": f"21d stall threshold: {company}",
                        "description": (
                            f"{company} has been inactive for {age_days}d. "
                            f"Act this week or risk the opportunity going cold."
                        ),
                        "deadline": None,
                        "source": "active_threads",
                    })
            except Exception:
                continue
    except Exception:  # noqa: BLE001
        pass
    return ffs[:3]  # cap at 3 forcing functions


def build_draft_plan(today: date) -> dict:
    candidates = generate_candidates(today)
    allocations = _allocate(candidates)
    outcomes = []
    portfolio_allocation: dict[str, float] = {}
    for cand, pct in zip(candidates, allocations):
        outcome = wp.make_outcome(
            outcome_id=cand["id"],
            title=cand["title"],
            success_criteria=cand["success_criteria"],
            linked_opportunity_ids=cand.get("linked_opportunity_ids") or [],
            linked_loop_ids=cand.get("linked_loop_ids") or [],
            portfolio_allocation_pct=pct,
            status="active",
        )
        outcomes.append(outcome)
        portfolio_allocation[cand["id"]] = pct

    plan = wp.new_plan(
        today,
        outcomes=outcomes,
        portfolio_allocation=portfolio_allocation,
        risks=_generate_risks(today, candidates),
        forcing_functions=_generate_forcing_functions(today, candidates),
    )
    plan["status"] = "draft_pending_confirmation"
    plan["generated_at"] = datetime.utcnow().isoformat() + "Z"
    plan["generation_provenance"] = [
        {"outcome_id": c["id"], "source": c.get("_source"), "companies": c.get("_companies") or []}
        for c in candidates
    ]
    return plan


def render_proposal(plan: dict) -> str:
    lines = [
        f"DRAFT weekly plan for week of {plan.get('week_of')} "
        f"({len(plan.get('outcomes', []))} candidate outcome(s), pending confirmation)",
        "",
    ]
    for o, prov in zip(plan.get("outcomes", []), plan.get("generation_provenance", [])):
        lines.append(
            f"  [{o.get('portfolio_allocation_pct')}%] {o.get('title')}  "
            f"(source: {prov.get('source')})"
        )
        lines.append(f"      success criteria: {o.get('success_criteria')}")
        if o.get("linked_loop_ids"):
            lines.append(f"      linked loops: {', '.join(o['linked_loop_ids'])}")
    lines.append("")
    lines.append(
        "This is a STARTING PROPOSAL derived from active threads + the loop ledger. "
        "Review, edit titles/success-criteria/allocations as needed, then run "
        "`--confirm` to make it the live plan for outcome-alignment scoring."
    )
    return "\n".join(lines)


def confirm_draft(draft_path: Path = DRAFT_PATH, today: date | None = None) -> dict:
    """Promote a pending weekly plan draft to the live weekly_plan.json.

    Shared by the `--confirm` CLI path and the `confirmProposal(kind=
    "weekly_plan")` API route (RB-2026-07-15) — that API route didn't exist
    before this, so a GPT-side "yes, confirm this week's plan" could never
    actually persist: the only way to promote a draft was this CLI command,
    run by hand. Returns {"error": ...} on failure, otherwise the confirmed
    plan dict.
    """
    today = today or date.today()
    if not draft_path.exists():
        return {"error": f"No draft found at {draft_path}."}
    try:
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"error": f"Could not read draft: {e}"}
    if not wp.is_current(draft, today):
        return {
            "error": (
                f"Draft is for week_of={draft.get('week_of')}, but today ({today.isoformat()}) "
                f"is in week_of={wp._week_of(today)}. Regenerate the draft before confirming."
            )
        }
    draft["status"] = "active"
    draft["confirmed_at"] = datetime.utcnow().isoformat() + "Z"
    draft.pop("generation_provenance", None)
    wp.save_plan(draft)
    # RB-DEFECT-067: wp.save_plan() writes the promoted content to
    # WEEKLY_PLAN_PATH (weekly_plan.json) -- it never touches draft_path
    # (weekly_plan_draft.json), so the on-disk draft file kept reading
    # status="draft_pending_confirmation" forever after a real confirm.
    # _render_weekly_plan_draft_alert() reads draft_path directly and only
    # suppresses the alert when status != "draft_pending_confirmation" --
    # confirmed live: weekly_plan.json correctly showed status=active/
    # week_of=2026-08-17 while weekly_plan_draft.json still showed
    # draft_pending_confirmation for the *same* week, two days after a
    # successful --confirm run. Persist the same status/confirmed_at back
    # to the draft file so the alert can never keep nagging about a week
    # that's already been adopted.
    confirmed_draft_record = dict(draft)
    confirmed_draft_record["status"] = "confirmed"
    draft_path.write_text(json.dumps(confirmed_draft_record, indent=2) + "\n", encoding="utf-8")

    # The carryover this draft was (at least partly) built from has now been
    # promoted into a live plan -- clear it so it isn't re-applied to a LATER
    # week's generation too. Snapshot first, matching wp.save_plan()'s own
    # pre-write safety precedent -- best-effort, must never block a confirm.
    if CARRYOVER_PATH.exists():
        try:
            snapshots_dir = CARRYOVER_PATH.parent / "_snapshots"
            snapshots_dir.mkdir(parents=True, exist_ok=True)
            tag = datetime.utcnow().strftime("consumed-%Y%m%dT%H%M%SZ")
            shutil.copy2(CARRYOVER_PATH, snapshots_dir / f"weekly_plan_carryover.{tag}.json")
            CARRYOVER_PATH.unlink()
        except OSError:
            pass
    return draft


def auto_adopt_if_monday_eod(draft_path: Path = DRAFT_PATH, today: date | None = None) -> dict:
    """RB-2026-08-24: Todd's explicit decision -- if this week's draft is
    still sitting unconfirmed by end of day Monday, auto-adopt it rather
    than leave weekly_plan.json permanently stuck on last week's plan (or
    empty) for however long it takes someone to remember to confirm.

    Never overrides an explicit human decision: only acts when the draft's
    status is still the initial "draft_pending_confirmation" -- if Todd
    already confirmed OR rejected it, this is a no-op. Only fires on Monday
    itself (weekday() == 0); any other day it's a deliberate no-op so this
    is safe to call unconditionally from a daily-scheduled script.
    """
    today = today or date.today()
    if today.weekday() != 0:
        return {"status": "skipped", "reason": "not Monday"}
    if not draft_path.exists():
        return {"status": "skipped", "reason": "no draft present"}
    try:
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"status": "skipped", "reason": f"could not read draft: {e}"}
    if draft.get("status") != "draft_pending_confirmation":
        return {"status": "skipped", "reason": f"draft status is {draft.get('status')!r}, not pending"}

    result = confirm_draft(draft_path=draft_path, today=today)
    if "error" in result:
        return {"status": "failed", "error": result["error"]}
    return {"status": "auto_adopted", "week_of": result.get("week_of")}


def auto_adopt_overdue_draft(draft_path: Path = DRAFT_PATH, today: date | None = None) -> dict:
    """Scheduled recovery for a Monday draft still pending on Tuesday+.

    This preserves review-first behavior throughout Monday and never
    overrides a confirmed or explicitly rejected draft. It only adopts a
    pending draft for the current week after Monday has passed.
    """
    today = today or date.today()
    if today.weekday() == 0:
        return {"status": "skipped", "reason": "Monday review window still open"}
    if not draft_path.exists():
        return {"status": "skipped", "reason": "no draft present"}
    try:
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"status": "skipped", "reason": f"could not read draft: {e}"}
    if draft.get("status") != "draft_pending_confirmation":
        return {"status": "skipped", "reason": f"draft status is {draft.get('status')!r}, not pending"}
    if not wp.is_current(draft, today):
        return {"status": "skipped", "reason": "pending draft is not for the current week"}
    result = confirm_draft(draft_path=draft_path, today=today)
    if "error" in result:
        return {"status": "failed", "error": result["error"]}
    return {"status": "auto_adopted", "week_of": result.get("week_of")}


def reject_draft(draft_path: Path = DRAFT_PATH) -> dict:
    """Mark a pending weekly plan draft rejected without promoting it.

    Leaves the draft file in place (for audit history) but flips its status
    so `_render_weekly_plan_draft_alert` stops nagging about it — same
    confirm/reject symmetry as every other confirmProposal kind.
    """
    if not draft_path.exists():
        return {"error": f"No draft found at {draft_path}."}
    try:
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"error": f"Could not read draft: {e}"}
    draft["status"] = "rejected"
    draft["rejected_at"] = datetime.utcnow().isoformat() + "Z"
    draft_path.write_text(json.dumps(draft, indent=2) + "\n", encoding="utf-8")
    return draft


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="ISO date to anchor the plan week to (default: today).")
    parser.add_argument("--write-draft", action="store_true", help="Persist the draft to weekly_plan_draft.json.")
    parser.add_argument("--confirm", action="store_true",
                        help="Promote the draft (from --draft-path or weekly_plan_draft.json) to the live weekly_plan.json.")
    parser.add_argument("--auto-adopt-eod", action="store_true",
                        help="Monday-only: promote the draft if it's still unconfirmed by end of day. No-op any other day or if already confirmed/rejected.")
    parser.add_argument("--auto-adopt-overdue", action="store_true",
                        help="Tuesday+: promote a still-pending current-week draft; never overrides confirmation or rejection.")
    parser.add_argument("--draft-path", default=str(DRAFT_PATH))
    parser.add_argument("--json", action="store_true", help="Print the draft as JSON instead of a human summary.")
    args = parser.parse_args()

    today = date.fromisoformat(args.date) if args.date else date.today()
    draft_path = Path(args.draft_path)

    if args.confirm:
        result = confirm_draft(draft_path, today)
        if "error" in result:
            sys.stderr.write(result["error"] + "\n")
            return 2
        print(f"Confirmed. {wp.WEEKLY_PLAN_PATH} now holds the live plan for week_of={result.get('week_of')}.")
        return 0

    if args.auto_adopt_eod:
        result = auto_adopt_if_monday_eod(draft_path, today)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"{result['status']}: {result.get('reason') or result.get('week_of') or result.get('error') or ''}")
        return 1 if result["status"] == "failed" else 0

    if args.auto_adopt_overdue:
        result = auto_adopt_overdue_draft(draft_path, today)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"{result['status']}: {result.get('reason') or result.get('week_of') or result.get('error') or ''}")
        return 1 if result["status"] == "failed" else 0

    plan = build_draft_plan(today)

    if args.write_draft:
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote draft to {draft_path}")

    if args.json:
        print(json.dumps(plan, indent=2))
    else:
        print(render_proposal(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
