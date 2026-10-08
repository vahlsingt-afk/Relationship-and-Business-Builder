#!/usr/bin/env python3
"""render_daily_brief.py — pre-render the RB Daily Brief (Part 2).

Runs at 5am after render_intelligence_brief.py.
Reads from the daily_brief cache, renders all Part 2 sections into
markdown, and writes:

  system/briefs/YYYY-MM-DD-daily-brief.md   — final markdown
  system/briefs/YYYY-MM-DD-daily-brief.json — structured metadata

Usage:
    python3 render_daily_brief.py [--date YYYY-MM-DD] [--dry-run] [--force]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core
import weekly_planning as wp
import relationship_reactivation_scan

try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # type: ignore

BRIEFS_DIR = core.SYSTEM_DIR / "briefs"
DAILY_BRIEF_CACHE = core.SYSTEM_DIR / ".cache" / "daily_brief.json"

# RB-DEFECT-2026-08-16: confirmed live in render_intelligence_brief.py --
# an intelligence-DB record (the Starbucks AI-inventory story, sitting in
# the DB since 2026-08-09) had raw chat-app DOM markup as its why_it_matters
# ("]:pointer-events-auto ... data-turn-id=\"request-...\"
# data-testid=\"conversation-turn-48\"") instead of any real content --
# whatever ingested it captured the wrong page. This file reads the same
# why_it_matters/summary fields independently (Technology Radar, Dot
# Connections) and needs the same backstop so a similarly corrupted record
# can't surface here even though this specific item didn't score high
# enough to reach Dot Connections today.
import re as _re_markup
_MARKUP_GARBAGE_RE = _re_markup.compile(
    r'data-(turn-id|testid)=|pointer-events-(auto|none)|scroll-m[bt]-\[|dir="auto"'
)

# RB-QUALITY-2026-09-04b: Todd's own read on the brief's cross-section
# redundancy (the same 2 overdue loops fully restated across Weekly Plan
# Progress/Decision Queue/Top Decisions Today/Connect the Dots/My
# Priorities/Loops & Obligations/CoS Bottom Line, 13x in one real brief) --
# Loops & Obligations is the one canonical full-detail home; everywhere else
# a loop-linked item is cut to a short pointer instead of restating the same
# sentence. Module-level (not duplicated per-renderer like most small
# helpers in this file) since it's now shared by more than one section and
# hoisting it is itself part of what this fix is for.
_LOOP_ID_RE = _re_markup.compile(r"\bL-\d{4}-\d{2}-\d{2}-\d+\b")


def _loop_ids_in(text: str) -> list[str]:
    return _LOOP_ID_RE.findall(text or "")


def _loop_pointer_note(loop_ids: list[str]) -> str:
    ids = ", ".join(loop_ids)
    return f"→ Full detail in **Loops & Obligations** ({ids})."


def _trim_decision_momentum_loop_tail(action: str, title: str) -> str:
    """cos_synthesis.py's build_decision_momentum() always restates the
    pending decision's own title a second time in recommended_action's tail
    (", then make the call on: {dec_title}. Do not defer again..."), fixed
    templated text, not free-form model output. When that decision is
    loop-linked, the same loop id is already in THIS item's own title (dec_
    title is embedded there too) -- and gets its full detail in Loops &
    Obligations regardless. Trim just that restated tail, keeping the
    "review the new evidence (...)" lead -- the genuinely new-today payload
    Connect the Dots exists to surface, which a blanket loop-item cut would
    have thrown away."""
    loop_ids = _loop_ids_in(title)
    if not loop_ids:
        return action
    marker = ", then make the call on:"
    idx = action.find(marker)
    if idx == -1:
        return action
    head = action[:idx].strip()
    if not head:
        return action
    if not head.endswith("."):
        head += "."
    return f"{head} Full detail on the decision itself is in **Loops & Obligations**."


def _clean_why(why: str) -> str:
    """Drop why_it_matters/summary text that's actually raw HTML/CSS/DOM
    markup rather than prose -- see RB-DEFECT-2026-08-16 above."""
    if why and _MARKUP_GARBAGE_RE.search(why):
        return ""
    return why


def _truncate_clean(text: str, limit: int) -> str:
    """RB-QUALITY-2026-09-04: several detail/body-text truncations in this
    file were bare text[:N] with no ellipsis and no word-boundary check --
    confirmed live, e.g. My Priorities rendering "...active Worl" (Worldpay
    cut mid-word) and "...DMB opport" (opportunity cut mid-word), with
    nothing to show the reader anything was even cut off. Same fix as
    render_intelligence_brief.py's _truncate_at_word_boundary -- duplicated
    here rather than imported to avoid a cross-module import for one small
    helper (the two renderers don't otherwise share runtime state)."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_space = cut.rfind(" ")
    if last_space > 0:
        cut = cut[:last_space]
    return cut.rstrip() + "…"
# RB-DEFECT-2026-07-14: GP/Genius Dot Connections had zero cross-day dedup --
# a story stays in its upstream feed's freshness window for several days, so
# the same bullets ("Fiserv president exits", "Fiserv debit network sale
# skepticism abounds") rendered verbatim for 3+ consecutive briefs. Tracks
# first-shown date per dedup key so a story permanently drops out once it's
# past GP_DOT_DEDUP_WINDOW_DAYS old, same first_rendered-anchored pattern as
# render_intelligence_brief.py's _is_duplicate (RB-DEFECT-2026-07-13).
GP_DOT_RENDERED_PATH = core.SYSTEM_DIR / ".cache" / "gp_dot_connections_rendered.json"
GP_DOT_DEDUP_WINDOW_DAYS = 3

# Connect the Dots had the same zero-cross-day-dedup gap as GP/Genius Dot
# Connections before RB-DEFECT-2026-07-14 fixed that section: a convergence
# signal (e.g. "&pizza -- 44 signals across 7 sources") stays valid in the
# underlying intelligence_db window for days, so the identical bullet
# rendered verbatim across consecutive briefs with nothing new added.
# Same first_rendered-anchored pattern, applied here too.
CONNECT_THE_DOTS_RENDERED_PATH = core.SYSTEM_DIR / ".cache" / "connect_the_dots_rendered.json"
CONNECT_THE_DOTS_DEDUP_WINDOW_DAYS = 3


def _load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# Section 0: CoS Judgment / Opening Assessment
# ---------------------------------------------------------------------------

def _load_weekly_plan() -> dict | None:
    """Load the confirmed weekly plan if it exists."""
    plan_path = core.SYSTEM_DIR / "weekly_plan.json"
    if plan_path.exists():
        try:
            return json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _load_weekly_plan_draft() -> dict | None:
    """Load the pending (unconfirmed) weekly plan draft, if any."""
    draft_path = core.SYSTEM_DIR / "weekly_plan_draft.json"
    if draft_path.exists():
        try:
            return json.loads(draft_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _render_weekly_plan_draft_alert(active_plan: dict | None, draft: dict | None,
                                     today: date | None = None) -> str:
    """Surface a pending weekly-plan draft that's newer than the active plan.

    weekly_plan_generator.py deliberately writes drafts to a separate file
    pending human confirmation (propose-then-confirm, mirroring
    relationship_intake.py's proposal model) — but nothing previously told
    the user a draft existed. Left unconfirmed, the Weekly Review below keeps
    rendering an increasingly stale "active" plan indefinitely: observed, an
    active plan for week_of 2026-06-22 was still being shown during the week
    of 2026-06-29 — the GP transition week — while a draft for that week had
    been sitting confirmed-and-ignored since the day it was generated.

    RB-DEFECT-067: confirmed live -- a Monday draft was still unconfirmed on
    Wednesday, meaning the whole week's outcome-alignment scoring ran against
    a plan from the prior week without anything escalating past this same
    static one-line note. This alone can't force adoption (that's a product-
    policy decision -- see the defect report's "Monday adoption contract"
    question, deliberately not decided unilaterally here), but the warning
    should not read identically on day 1 and day 4 of being ignored.
    """
    if not draft or draft.get("status") != "draft_pending_confirmation":
        return ""
    draft_week = draft.get("week_of") or "unknown"
    active_week = (active_plan or {}).get("week_of") or "none"
    if draft_week == active_week:
        return ""  # draft is for the week already active — nothing stale to flag
    generated = (draft.get("generated_at") or "")[:10]
    days_pending = None
    if today is not None:
        gen_date = _parse_iso_date(generated) if generated else None
        if gen_date:
            days_pending = (today - gen_date).days
    if days_pending is not None and days_pending >= 2:
        header = f"## 🛑 Weekly Plan Draft STILL Unconfirmed ({days_pending} days)\n\n"
        urgency = (
            f"This has been sitting unconfirmed since {generated} — "
            f"{days_pending} days of daily recommendations have now been scored "
            f"against the wrong week's plan. "
        )
    else:
        header = "## ⚠ Weekly Plan Draft Awaiting Confirmation\n\n"
        urgency = f"has been sitting unconfirmed since {generated}. "
    # RB-DEFECT-2026-08-17: same CLI-leak class as the meeting-prep and
    # mutations.py fixes -- a raw shell invocation in user-facing text that
    # the reader (or the GPT mediating the chat) can't execute directly.
    return (
        f"{header}"
        f"A newer weekly plan draft for **week of {draft_week}** {urgency}"
        f"The Weekly Review below is still running "
        f"on the older **active plan for week of {active_week}**.\n\n"
        "Say \"confirm the weekly plan draft\" to promote it to active."
    )


def _render_monday_plan_decision_gate(active_plan: dict | None, draft: dict | None,
                                       today: date) -> str:
    """Monday-only: when this week's draft is still pending, render it as
    the FIRST thing in the entire brief -- ahead of CoS Opening -- framed
    as the day's primary decision rather than a status banner.

    RB-DEFECT-067 (product decision, 2026-08-20): Todd chose to keep the
    propose-then-confirm model (not auto-adopt), but asked that Monday's
    confirmation be made "unavoidable" rather than a line item easy to
    scroll past. RB's interaction surface is a ChatGPT cockpit displaying
    this markdown verbatim, not a UI that can literally force a modal --
    the closest equivalent this renderer can guarantee is: the decision is
    the first thing on the page, every day it remains unresolved, framed
    as blocking everything below it rather than informational. Reuses
    _render_weekly_plan_draft_alert's day-pending calculation but replaces
    the copy entirely -- this is a decision gate, not a status note.

    Returns "" on any non-Monday, or when today.weekday()==0 but there's
    nothing pending to decide (matches _render_weekly_plan_draft_alert's
    own suppression conditions).
    """
    if today.weekday() != 0:
        return ""
    if not draft or draft.get("status") != "draft_pending_confirmation":
        return ""
    draft_week = draft.get("week_of") or "unknown"
    active_week = (active_plan or {}).get("week_of") or "none"
    if draft_week == active_week:
        return ""
    # RB-DEFECT (2026-09-14): this line previously read draft.get("generated_at")
    # -- the NEW draft's own timestamp -- but used it to describe when the
    # OLD active plan was generated, copied from _render_weekly_plan_draft_alert
    # above where that reuse is correct (there it describes the draft itself).
    # Live incident: on 2026-09-14 this rendered "week of 2026-09-07's plan
    # (generated 2026-09-14)" -- stamping today's date onto a plan actually
    # generated 2026-09-08, making the published brief internally inconsistent
    # with weekly_plan.json's own generated_at.
    active_generated = ((active_plan or {}).get("generated_at") or "")[:10]
    return (
        "# ⛔ DECISION REQUIRED — Confirm This Week's Plan\n\n"
        f"This week's plan (**week of {draft_week}**) is drafted and waiting on you. "
        f"Nothing below this — priorities, scoring, recommendations — reflects this week "
        f"until you decide. RB is still running on **week of {active_week}**'s plan "
        f"(generated {active_generated}) until you act.\n\n"
        "Say \"confirm the weekly plan draft\" to adopt it, or \"revise the weekly plan draft\" "
        "to change it first.\n\n---"
    )


def _parse_loop_statuses() -> dict[str, str]:
    """Return {loop_id: status} for all loops in the ledger."""
    import re as _re
    ledger_path = core.SYSTEM_DIR / "loop_ledger.md"
    if not ledger_path.exists():
        return {}
    statuses: dict[str, str] = {}
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        m = _re.match(r"\|\s*(L-[\d-]+)\s*\|.*\|\s*(\*\*closed\*\*|open|abandoned)", line, _re.IGNORECASE)
        if m:
            lid = m.group(1).strip()
            raw = m.group(2).lower().replace("**", "").strip()
            statuses[lid] = raw
    return statuses


def _score_outcome_progress(outcome: dict, loop_statuses: dict, threads: list) -> tuple[str, str]:
    """Return (status_emoji, detail) for a single outcome.

    status_emoji: ✅ complete | 🔄 in progress | ⏳ not started
    detail: short human-readable progress note
    """
    loop_ids = outcome.get("linked_loop_ids") or []
    opp_ids = outcome.get("linked_opportunity_ids") or []

    # Loop-backed outcome — only count loops that actually exist in the ledger
    if loop_ids:
        known = [lid for lid in loop_ids if lid in loop_statuses]
        total = len(known) if known else len(loop_ids)
        closed = sum(1 for lid in known if loop_statuses.get(lid) == "closed")
        remaining = total - closed
        if total == 0:
            return "⏳", "No loops tracked yet."
        if closed == total:
            return "✅", f"All {total} loops resolved."
        elif closed > 0:
            return "🔄", f"{closed}/{total} loops resolved — {remaining} remaining."
        else:
            return "⏳", f"0/{total} loops resolved — {remaining} remaining."

    # Thread-backed outcome
    if opp_ids:
        thread_map = {t.get("id", ""): t for t in threads}
        active = [t for oid in opp_ids for t in [thread_map.get(oid)] if t]
        if active:
            t = active[0]
            status = (t.get("status") or "").lower()
            raw_state = (t.get("current_state") or "").strip()
            # Take only the first clean sentence to avoid mid-word truncation
            import re as _re
            first_sentence = _re.split(r'(?<=[.!?])\s', raw_state)[0][:150] if raw_state else ""
            if status in ("closed", "done", "complete"):
                # current_state is a pre-closure snapshot (e.g. "Two open loops:
                # ...") that's never updated when the thread closes — quoting it
                # here reads as if the thread is still open. close_reason is the
                # actual as-of-closure note, so prefer it when present.
                #
                # close_reason is sometimes written as an internal bug-postmortem
                # note rather than a user-facing summary (e.g. the Patrick Nelson
                # thread's close_reason documents a prior GPT-fabrication defect:
                # "which claimed success but never actually persisted anything").
                # That's real, useful history -- but it belongs in loop_ledger.md
                # / git history, not in a daily brief the user reads every
                # morning. Detect internal-diagnostic language and fall back to a
                # clean "Closed <date>." instead of surfacing the postmortem.
                close_note = (t.get("close_reason") or "").strip()
                _DIAGNOSTIC_MARKERS = [
                    "claimed success", "never actually persisted", "no matching api activity",
                    "independent system", "also closed in", "closing here too",
                ]
                if close_note and any(m in close_note.lower() for m in _DIAGNOSTIC_MARKERS):
                    # Prefer a "Closed YYYY-MM-DD" prefix already present in
                    # the note over the thread's separate closed_date field
                    # (often unset on historical data) so the *when* still
                    # shows even though the postmortem text after it doesn't.
                    date_prefix = _re.match(r"^(Closed\s+\d{4}-\d{2}-\d{2})", close_note)
                    if date_prefix:
                        close_sentence = f"{date_prefix.group(1)}."
                    else:
                        closed_date = (t.get("closed_date") or "").strip()
                        close_sentence = f"Closed {closed_date}." if closed_date else "Closed."
                else:
                    close_sentence = _re.split(r'(?<=[.!?])\s', close_note)[0][:200] if close_note else ""
                return "✅", close_sentence or f"Thread closed: {first_sentence or status}."
            elif first_sentence:
                return "🔄", first_sentence
            else:
                return "⏳", "No activity recorded yet."
        return "⏳", "Thread data unavailable."

    # Manual outcome (no linked data) — show as in-progress by default
    return "🔄", "Self-tracked — no automated signal available."


def _overdue_drift_note(outcome: dict, sections: dict) -> str:
    """If an outcome tracks a fixed roster of overdue loops snapshotted when the weekly
    plan was drafted, and the live overdue count has since grown, say so explicitly.
    Otherwise the brief silently shows two different overdue-loop totals (the plan's frozen
    figure vs. CoS Opening/My Priorities' live one from _get_comm_context) with no
    explanation of the gap — the two numbers have different meanings (a committed roster vs.
    the current live count) but that distinction needs to be visible, not implicit.
    """
    text_blob = f"{outcome.get('title', '')} {outcome.get('success_criteria', '')}".lower()
    if "overdue" not in text_blob:
        return ""
    tracked_total = len(outcome.get("linked_loop_ids") or [])
    if not tracked_total:
        return ""
    live_overdue = _get_comm_context(sections).get("loops_overdue") or 0
    if live_overdue > tracked_total:
        gap = live_overdue - tracked_total
        plural = "s" if gap != 1 else ""
        return f" *({gap} more loop{plural} gone overdue since this plan was drafted — {live_overdue} total now.)*"
    return ""


def _render_weekly_plan_progress(plan: dict, target_date: date, sections: dict) -> str:
    """Tue–Fri: compact daily progress report against weekly outcomes.

    RB-DEFECT-067 (product decision, 2026-08-20): if `plan` isn't actually
    the plan for the week containing `target_date` -- i.e. Monday's draft
    for the current week was never confirmed, so the prior week's plan is
    still "active" -- do not score or rank daily work against it. Todd's
    call: still send the brief (never leave him with nothing), but replace
    scored/ranked progress with a plain statement that scoring is paused
    and why, rather than numbers that look authoritative but are measuring
    the wrong week's outcomes.
    """
    if not wp.is_current(plan, target_date):
        stale_week = plan.get("week_of") or "unknown"
        return (
            "## Weekly Plan — Progress\n\n"
            f"*Scoring paused: the active plan is still week of **{stale_week}**, not this week. "
            f"Confirm this week's draft to resume scored progress — see the decision above.*"
        )

    outcomes = plan.get("outcomes") or []
    if not outcomes:
        return ""

    loop_statuses = _parse_loop_statuses()
    try:
        threads = core.load_active_threads()
    except Exception:
        threads = []

    # Days elapsed since Monday
    week_start = target_date - __import__("datetime").timedelta(days=target_date.weekday())
    days_done = (target_date - week_start).days  # Mon=0 … Fri=4
    days_left = 4 - days_done  # business days remaining (Mon–Fri)
    day_label = f"Day {days_done + 1} of 5"

    lines = [f"## Weekly Plan — Progress ({day_label})\n"]
    if days_left == 0:
        lines.append("*Last business day of the week — close what you can.*\n")
    elif days_left == 1:
        lines.append("*One business day left — focus on highest-impact closes.*\n")

    all_done = True
    active_lines: list[str] = []
    parked_lines: list[str] = []
    for o in outcomes:
        title = o.get("title", "").strip()
        pct = o.get("portfolio_allocation_pct", 0)
        # RB-2026-09-03: confirmed live -- Todd's own weekly_plan.json
        # operator_notes explicitly record IKEA and Five Guys as "parked"/
        # "deferred to next week" with 0% this-week allocation by his own
        # confirmed decision, but this loop rendered them identically to
        # the real active-work outcomes ("⏳ 0/1 loops resolved — 1
        # remaining"), reading as if RB were telling him to work on them
        # this week. Todd: "you are making that up." A 0%-allocation
        # outcome is not behind schedule -- it was deliberately not
        # scheduled this week -- so it gets its own distinct treatment
        # instead of the normal loop-progress scoring.
        if pct == 0:
            parked_lines.append(f"- ⏸ **{title}** — Parked this week (0% allocation).")
            continue
        emoji, detail = _score_outcome_progress(o, loop_statuses, threads)
        if emoji != "✅":
            all_done = False
        drift = _overdue_drift_note(o, sections)
        active_lines.append(f"- {emoji} **{title}** ({pct:.0f}%) — {detail}{drift}")

    lines.extend(active_lines)
    if parked_lines:
        lines.append("\n**Parked / deferred this week:**")
        lines.extend(parked_lines)

    if all_done:
        lines.append("\n*All outcomes complete — strong week.*")

    # RB-2026-09-03: Todd's direct ask -- this section should remind him to
    # update his own loops, not just report outcome-linked progress numbers.
    live_overdue = _get_comm_context(sections).get("loops_overdue") or 0
    if live_overdue:
        plural = "s" if live_overdue != 1 else ""
        lines.append(f"\n*{live_overdue} loop{plural} overdue — close, re-date, or defer before the week closes out.*")

    return "\n".join(lines)


def _render_weekly_plan_review(plan: dict, target_date: date, sections: dict) -> str:
    """Saturday: end-of-week review — what got done, what didn't, carry-forward."""
    outcomes = plan.get("outcomes") or []
    if not outcomes:
        return ""

    loop_statuses = _parse_loop_statuses()
    try:
        threads = core.load_active_threads()
    except Exception:
        threads = []

    week_of = plan.get("week_of", "")
    lines = [f"## Weekly Review — Week of {week_of}\n"]

    completed, in_progress, not_started = [], [], []
    for o in outcomes:
        emoji, detail = _score_outcome_progress(o, loop_statuses, threads)
        detail = detail + _overdue_drift_note(o, sections)
        entry = (o.get("title", "").strip(), o.get("portfolio_allocation_pct", 0), detail)
        if emoji == "✅":
            completed.append(entry)
        elif emoji == "🔄":
            in_progress.append(entry)
        else:
            not_started.append(entry)

    total_pct = sum(o.get("portfolio_allocation_pct", 0) for o in outcomes)
    done_pct = sum(e[1] for e in completed)
    score = int(done_pct / total_pct * 100) if total_pct else 0

    lines.append(f"**Week score: {score}% of allocated effort delivered**\n")

    if completed:
        lines.append("**✅ Completed**")
        for title, pct, detail in completed:
            lines.append(f"- {title} ({pct:.0f}%) — {detail}")
        lines.append("")

    if in_progress:
        lines.append("**🔄 Partial / Carry-forward**")
        for title, pct, detail in in_progress:
            lines.append(f"- {title} ({pct:.0f}%) — {detail}")
        lines.append("")

    if not_started:
        lines.append("**⏳ Not started**")
        for title, pct, detail in not_started:
            lines.append(f"- {title} ({pct:.0f}%) — Decide: carry forward, defer, or drop.")
        lines.append("")

    # Forcing functions carried over
    ffs = [f for f in (plan.get("forcing_functions") or []) if f.get("deadline")]
    if ffs:
        lines.append(f"**Forcing function:** {ffs[0]['title']} — {ffs[0]['description']}")

    return "\n".join(lines)


def _render_weekly_plan_summary(plan: dict, sections: dict, target_date: date | None = None) -> str:
    """Render a compact executive summary of the weekly plan outcomes.

    Unlike the Tue-Fri progress report and Sat review, this Monday path used to
    print each outcome's frozen success_criteria text verbatim — so an outcome
    like "Close or defer Patrick Nelson / Matrix" or "Resolve all 22 overdue
    loops" kept reading as still-open even after the underlying loops/threads
    were closed, because nothing here ever re-checked live state. Reuse the
    same _score_outcome_progress/_overdue_drift_note cross-check the other two
    day-of-week paths already use, so a completed outcome reads as done
    instead of restating stale plan-drafting-time text as if it were current.

    RB-DEFECT-067 (product decision, 2026-08-20): this header says "This
    Week's Plan" -- on a Monday morning before that day's draft has been
    confirmed, `plan` is still whatever was active last week, so that
    header would be labeling the WRONG week's outcomes as current. Same
    guard and same reasoning as _render_weekly_plan_progress.
    """
    if target_date is not None and not wp.is_current(plan, target_date):
        stale_week = plan.get("week_of") or "unknown"
        return (
            "## This Week's Plan\n\n"
            f"*Still showing the plan for week of **{stale_week}** — confirm this week's "
            f"draft (see the decision above) to replace it with this week's outcomes.*"
        )

    outcomes = plan.get("outcomes") or []
    ffs = plan.get("forcing_functions") or []
    if not outcomes:
        return ""

    loop_statuses = _parse_loop_statuses()
    try:
        threads = core.load_active_threads()
    except Exception:
        threads = []

    lines = ["## This Week's Plan\n"]

    # Forcing function as the framing headline if present
    if ffs:
        ff = ffs[0]
        title = str(ff.get("title") or "Weekly forcing function").strip()
        description = str(ff.get("description") or "").strip()
        suffix = f" — {description}" if description else ""
        lines.append(f"**{title}**{suffix}\n")

    lines.append("**Outcomes:**")
    for o in outcomes:
        pct = o.get("portfolio_allocation_pct", 0)
        title = o.get("title", "").strip()
        emoji, detail = _score_outcome_progress(o, loop_statuses, threads)
        is_data_backed = bool(o.get("linked_loop_ids") or o.get("linked_opportunity_ids"))
        if emoji == "✅" or is_data_backed:
            # Loop/thread-backed outcomes have a live count or close reason —
            # show that instead of the plan-drafting-time criteria text, which
            # goes stale the moment any linked loop/thread changes status
            # (e.g. "Resolve all 22 overdue loops" stops being true the moment
            # even one of those 22 closes).
            drift = _overdue_drift_note(o, sections)
            lines.append(f"- {emoji} **{title}** ({pct:.0f}%) — {detail}{drift}")
        else:
            criteria = o.get("success_criteria", "").strip()
            # Truncate criteria to one sentence
            first_sentence = criteria.split(".")[0].strip() + "." if criteria else ""
            lines.append(f"- {emoji} **{title}** ({pct:.0f}%) — {first_sentence}")

    # RB-DEFECT-2026-07-27: a risk sourced from active_threads (id shape
    # "risk-decay-{thread_id}") is baked in at plan-drafting time and never
    # re-checked -- unlike the outcomes above, which re-score against live
    # thread status. "Perfect Hire — 31d since last contact" kept rendering
    # here days after the underlying thread was closed. Drop any
    # active_threads-sourced risk whose thread has since closed.
    try:
        threads_by_id = {t.get("id"): t for t in threads}
    except Exception:
        threads_by_id = {}
    live_risks = []
    for r in (plan.get("risks") or []):
        if r.get("source") == "active_threads" and str(r.get("id") or "").startswith("risk-decay-"):
            thread_id = str(r["id"])[len("risk-decay-"):]
            thread = threads_by_id.get(thread_id)
            if thread and thread.get("status") != "open":
                continue
        live_risks.append(r)

    risks = [r for r in live_risks if r.get("severity") == "high"]
    if risks:
        top_risk = risks[0]
        desc = top_risk.get("description", "")
        if top_risk.get("source") == "loop_ledger":
            # Same stale-number problem as the outcomes above: this description's
            # "N overdue loops" was baked in at plan-drafting time and never
            # refreshed. Swap in the same live count _get_comm_context already
            # treats as canonical elsewhere in the brief.
            import re as _re
            live_overdue = _get_comm_context(sections).get("loops_overdue")
            if live_overdue is not None:
                desc = _re.sub(r"^\d+ overdue loops", f"{live_overdue} overdue loops", desc)
        lines.append("")
        lines.append(f"**Risk:** {desc}")

    return "\n".join(lines)


def _load_weekend_intel_summary(target_date: date) -> list[str]:
    """On Mondays, summarize actual personal/relationship activity (email,
    SMS, notable correspondence) from the preceding Saturday+Sunday.

    RB-DEFECT-2026-07-27: this used to scrape B: National Headlines and
    I: Strategic Signals out of the past 2 days' Intelligence Briefs --
    generic world/macro news with no personal or business tie, already
    covered (and better contextualized) in the Intelligence Brief itself.
    "Weekend catch-up" is a CoS conversation, not a second news digest --
    it should say what happened in Todd's world, not repeat headlines he
    already got. Replaced with a direct read of the weekend's email/SMS
    activity, same data the Intelligence Brief's own "What Changed Today"
    line draws from, just windowed to Saturday+Sunday instead of 24-36h.
    """
    from datetime import timedelta as _td
    if target_date.weekday() != 0:  # Only runs on Mondays
        return []

    weekend_start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc) - _td(days=2)
    weekend_end = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc)

    bullets: list[str] = []

    # Email: total weekend thread count, plus up to 3 notable senders who
    # are known baseline contacts (a real signal worth naming, not routine
    # promotional/newsletter traffic). Reads both accounts directly, same
    # pattern as daily_brief.py's own "What Changed Today" email scan.
    try:
        baseline = core.load_baseline()
        email_idx = core._build_email_index(baseline)
        weekend_threads = []
        notable_senders: list[str] = []
        for acct in ("personal", "bridgepoint"):
            ep = core.email_path_for(acct)
            if not ep.exists():
                continue
            data = json.loads(ep.read_text(encoding="utf-8"))
            for t in data.get("threads") or []:
                lma = t.get("last_message_at") or ""
                if not lma:
                    continue
                try:
                    from email.utils import parsedate_to_datetime as _pdt
                    ts = _pdt(lma).astimezone(timezone.utc) if "," in lma else datetime.fromisoformat(lma.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if not (weekend_start <= ts < weekend_end):
                    continue
                weekend_threads.append(t)
                sender = (t.get("last_message_from") or {})
                sender_email = core._normalize_email(sender.get("email"))
                match = email_idx.get(sender_email) if sender_email else None
                if match and match.get("name") and match["name"] not in notable_senders:
                    notable_senders.append(match["name"])
        if weekend_threads:
            bullets.append(f"**Email:** {len(weekend_threads)} threads over the weekend.")
        for name in notable_senders[:3]:
            bullets.append(f"- Heard from **{name}** (known contact) over the weekend.")
    except Exception:
        pass

    # SMS: distinct contacts active over the same window.
    try:
        if core.MESSAGES_PATH.exists():
            msgs_data = json.loads(core.MESSAGES_PATH.read_text(encoding="utf-8"))
            handles: set[str] = set()
            for ev in msgs_data.get("events") or []:
                at_str = ev.get("at") or ""
                if not at_str:
                    continue
                try:
                    ts = datetime.fromisoformat(at_str.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if weekend_start <= ts < weekend_end:
                    h = ev.get("handle") or ""
                    if h:
                        handles.add(h)
            if handles:
                bullets.append(f"**SMS:** {len(handles)} contacts active over the weekend.")
    except Exception:
        pass

    return bullets


def _get_market_context(target_date: date) -> dict:
    """Read today's watchlist signals and return structured market context."""
    import re as _re
    try:
        pw_path = core.SYSTEM_DIR / "inbox" / "market_signals_earnings.jsonl"
        today_str = target_date.isoformat()
        up_movers: list[str] = []
        down_movers: list[str] = []
        if pw_path.exists():
            seen: set[str] = set()
            for raw in pw_path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                sig = json.loads(raw)
                if str(sig.get("published_at") or "")[:10] != today_str:
                    continue
                title = sig.get("title") or ""
                if "PRICE MOVE" not in title:
                    continue
                company = (sig.get("company") or "").strip()
                if not company or company in seen:
                    continue
                seen.add(company)
                if "↑" in title:
                    up_movers.append(company)
                elif "↓" in title:
                    down_movers.append(company)
        sector_rally = len(up_movers) >= 4
        return {
            "up_movers": up_movers,
            "down_movers": down_movers,
            "sector_rally": sector_rally,
            "rally_count": len(up_movers),
        }
    except Exception:
        return {"up_movers": [], "down_movers": [], "sector_rally": False, "rally_count": 0}


def _get_comm_context(sections: dict) -> dict:
    """Extract communication queue stats from sections."""
    import re as _re
    ci = sections.get("communication_intelligence", [])
    result = {
        "loops_overdue": 0,
        "emails_awaiting": 0,
        "followups_overdue": 0,
        "meetings_needing_prep": 0,
        "raw": "",
    }
    for item in ci:
        s = (item.get("summary") or "").strip()
        if not s:
            continue
        result["raw"] = s
        m = _re.search(r"Loops overdue:\s*(\d+)", s)
        if m:
            result["loops_overdue"] = int(m.group(1))
        m = _re.search(r"Emails awaiting your response:\s*(\d+)", s)
        if m:
            result["emails_awaiting"] = int(m.group(1))
        m = _re.search(r"Follow-ups overdue:\s*(\d+)", s)
        if m:
            result["followups_overdue"] = int(m.group(1))
        m = _re.search(r"Meetings needing prep:\s*(\d+)", s)
        if m:
            result["meetings_needing_prep"] = int(m.group(1))
        break
    return result


def _load_prior_loops_overdue(target_date: date) -> int | None:
    """Read yesterday's rendered daily brief and extract its overdue-loop
    count, for trend comparison. Returns None if no prior brief exists or the
    count can't be parsed (conservative — no claim without real data)."""
    import re as _re
    from datetime import timedelta as _td
    prior_path = BRIEFS_DIR / f"{(target_date - _td(days=1)).isoformat()}-daily-brief.md"
    if not prior_path.exists():
        return None
    try:
        text = prior_path.read_text(encoding="utf-8")
    except Exception:
        return None
    m = _re.search(r"(\d+)\s+overdue loops", text, _re.IGNORECASE)
    return int(m.group(1)) if m else None


def _render_cos_judgment(sections: dict, target_date: date) -> str:
    """CoS Opening — narrative synthesis: what does today mean in context.

    Reads market context, communication state, signals, and GP countdown,
    then writes a 2-3 sentence narrative rather than assembling labeled items.
    """
    is_monday = target_date.weekday() == 0
    is_friday = target_date.weekday() == 4
    weekday_name = target_date.strftime("%A")
    gp_start = date(2026, 7, 7)
    days_to_gp = (gp_start - target_date).days

    mkt = _get_market_context(target_date)
    comm = _get_comm_context(sections)

    signals = sections.get("strategic_industry_signals", [])
    top_signal_title = ""
    if signals:
        extras = signals[0].get("extras") or {}
        days_since = extras.get("days_since_evidence")
        is_stale = extras.get("is_actually_stale") or (
            days_since is not None and days_since >= 5
        )
        if not is_stale:
            top_signal_title = (signals[0].get("title") or "").strip()
            top_signal_title = top_signal_title.replace("Multiple-source convergence: ", "").strip()

    cal_items = sections.get("calendar_intelligence", []) + sections.get("day_ahead", [])
    cal_titles = []
    for item in cal_items:
        t = (item.get("title") or "").strip()
        if t and t not in cal_titles:
            cal_titles.append(t)

    # Build narrative paragraphs — max 3 lines, each a complete thought
    paras: list[str] = []

    # Monday: weekend catch-up block first
    if is_monday:
        weekend_bullets = _load_weekend_intel_summary(target_date)
        if weekend_bullets:
            paras.append("**Weekend catch-up:**")
            for b in weekend_bullets[:4]:
                paras.append(f"- {b}")
            paras.append("")

    # GP countdown narrative — different voice at each threshold
    if 0 < days_to_gp <= 1:
        paras.append(
            "**Tomorrow is Day 1 at Global Payments.** "
            "Confirm start logistics, review your account map one more time, and protect tonight."
        )
    elif 0 < days_to_gp <= 3:
        paras.append(
            f"**{days_to_gp} days.** "
            "The window to close loose ends is closing — anything unresolved now lands in week one."
        )
    elif 0 < days_to_gp <= 8:
        if is_monday:
            paras.append(
                f"**This is the week that matters.** GP Day 1 is {days_to_gp} days out. "
                "What you finish this week, you own. What you don't becomes drag inside a new role."
            )
        else:
            paras.append(
                f"**{days_to_gp} days to Global Payments.** "
                "Transition week — loop clearance, territory prep, and network notification."
            )

    # Sector rally narrative — connect market to role context
    if mkt["sector_rally"] and days_to_gp > 0:
        names_short = ", ".join(mkt["up_movers"][:4])
        if len(mkt["up_movers"]) > 4:
            names_short += f" +{len(mkt['up_movers']) - 4} more"
        paras.append(
            f"**The sector is moving today** — {mkt['rally_count']} watchlist names up simultaneously "
            f"({names_short}). Broad institutional buying heading into your first week. "
            f"Know the story when customers ask why competitors are being valued up."
        )
    elif mkt["sector_rally"]:
        paras.append(
            f"**Sector-wide equity rally today** — {mkt['rally_count']} watchlist names moving higher. "
            "Institutional positioning, not company-specific news. Watch for deal announcements."
        )

    # Top signal — only if not already covered by weekend catch-up on Monday
    _headline_terms = ("global payments", "genius", "worldpay", "mcdonald", "active opportunity", "buying signal", "acquisition")
    show_signal = (
        top_signal_title
        and any(term in top_signal_title.lower() for term in _headline_terms)
        and (not is_monday or not _load_weekend_intel_summary(target_date))
    )
    if show_signal and not mkt["sector_rally"]:  # sector rally already gives a market narrative
        paras.append(f"**Top signal:** {top_signal_title}.")

    # Calendar + comm state on non-Monday (Monday already covered by weekly plan section)
    if not is_monday:
        cal_str = f"**On your calendar:** {cal_titles[0]}." if cal_titles else ""
        queue_note = ""
        if comm["loops_overdue"] >= 10:
            # This used to unconditionally assert "the list is getting longer,
            # not shorter" any time the count crossed 10 — with no actual
            # trend check. It kept saying that even on days the count had
            # measurably shrunk (e.g. 22 -> 18 after real closures), directly
            # contradicting the progress shown a few lines away in This
            # Week's Plan and eroding trust in the whole brief. Compare
            # against yesterday's rendered count and only claim a direction
            # the data actually supports.
            prior_overdue = _load_prior_loops_overdue(target_date)
            if prior_overdue is not None and comm["loops_overdue"] < prior_overdue:
                queue_note = (
                    f" {comm['loops_overdue']} loops overdue — down from {prior_overdue} yesterday, "
                    "real progress but still a lot to clear."
                )
            elif prior_overdue is not None and comm["loops_overdue"] > prior_overdue:
                queue_note = (
                    f" {comm['loops_overdue']} loops overdue — up from {prior_overdue} yesterday."
                )
            else:
                queue_note = f" {comm['loops_overdue']} loops overdue."
        elif comm["emails_awaiting"] >= 5 and comm["followups_overdue"] >= 5:
            queue_note = f" Communication queue: {comm['emails_awaiting']} emails and {comm['followups_overdue']} follow-ups waiting."
        # Calendar inventory belongs in Day Ahead. A double-digit overdue
        # queue is different: it is a material operating condition, and a
        # measured day-over-day direction is a useful opening judgment.
        if queue_note and comm["loops_overdue"] >= 10:
            paras.append(queue_note.strip())

    if not paras:
        return ""

    lines = ["## CoS Opening\n"] + paras
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section 3: My Priorities
# ---------------------------------------------------------------------------

MAX_PRIORITIES = 10

# RB-2026-08-25: internal scoring/algorithm language that leaked into a
# reader-facing detail line -- confirmed live: "Allocation: 100.0% -- items
# aligned to this outcome are boosted; unaligned items are dampened." Same
# class of leak already banned from Decision Queue/Bottom Line via
# _NOISE_PHRASES/_BOTTOM_NOISE, just never filtered for My Priorities.
_MY_PRIORITIES_DETAIL_NOISE = [
    "items aligned to this outcome are boosted",
    "unaligned items are dampened",
]


def _contact_card_hint(name: str) -> str:
    """RB has no web UI -- there is nothing to hyperlink a contact name to.
    getCard is a real, GPT-callable action; render a plain-language trigger
    phrase next to a contact's name so pulling up their card doesn't require
    remembering the right words or scrolling back to a prior brief."""
    if not name:
        return ""
    return f' · Card: ask RB "show me {name}\'s card"'


def _render_my_priorities(sections: dict) -> str:
    """My Priorities — top 10, deduplicated, overdue loops collapsed to a count."""
    items = sections.get("my_priorities", [])
    if not items:
        return "## My Priorities\n\n*No active priorities tracked.*"

    lines = ["## My Priorities\n"]
    seen_titles: set[str] = set()
    rendered: list[str] = []
    item_count = 0  # counts priority items, not rendered lines -- a detail

    # RB-2026-08-25: confirmed live -- Jeff Coffland and Jeff Weaver each
    # appeared TWICE, once as a plain "Relationship at risk: {name}" item
    # (falls through to the generic title+detail branch below) and once as
    # a "↘ {name}" relationship-decay-alert item (its own dedicated branch,
    # richer: direction, days-since-contact, weekly-plan linkage, card
    # hint). Two different upstream generators flagging the same person,
    # under different title text, so the exact-title dedup a few lines down
    # never catches it. Pre-scan for names already covered by the richer
    # decay-alert format so the generic branch can skip them -- richer
    # format wins regardless of which order the items happen to arrive in.
    import re as _re_my_pri
    _decay_alert_names: set[str] = set()
    for _item in items:
        _t = (_item.get("title") or "")
        if "relationship decay alert" in _t.lower() or "↘" in _t or "↗" in _t:
            _name = _re_my_pri.sub(r"↗|↘|[Rr]elationship decay alert:", "", _t).strip().lower()
            if _name:
                _decay_alert_names.add(_name)
    # sub-line under a title must not eat into the MAX_PRIORITIES budget.
    overdue_loop_count = 0
    overdue_loop_examples: list[str] = []

    # RB 2026-08-27: reviewed live -- Todd on the card-hint pattern: "Is
    # telling the user something they are aware good use of this space?
    # ...better would be - Jeff is in your inner circle and you haven't
    # communicated with him in 30 days." The real days-quiet/RC-tier data
    # already exists in relationship_momentum_status (RC Contact Status
    # draws from it) but was never wired into these decay-alert items --
    # extras.days_since_last_contact was empty in practice, so decay_detail
    # below always rendered blank and the card-hint was the only content.
    # Build a name -> (days_quiet, rc_tier) lookup from the same source RC
    # Contact Status already uses, so this renders the real status inline
    # instead of an instruction to go ask for it.
    _momentum_by_name: dict[str, tuple[int | None, str]] = {}
    for _mi in (sections.get("relationship_momentum_status") or []):
        _mtitle = (_mi.get("title") or "")
        _mname_match = _re_my_pri.match(r"^([^—]+)—", _mtitle)
        if not _mname_match:
            continue
        _mname = _mname_match.group(1).strip().lower()
        _msummary = _mi.get("summary") or ""
        _days_match = _re_my_pri.search(r"Days quiet \(effective\):\s*(\d+)", _msummary)
        _tier_match = _re_my_pri.search(r"RC tier:\s*(\w+)", _msummary)
        _momentum_by_name[_mname] = (
            int(_days_match.group(1)) if _days_match else None,
            _tier_match.group(1) if _tier_match else "",
        )

    for item in items:
        title = (item.get("title") or "").strip()
        if not title:
            continue

        # Collapse overdue loops into a count
        if title.lower().startswith("overdue:") or (
            title.startswith("L-20") and "Overdue" in title
        ):
            overdue_loop_count += 1
            # Capture first 3 for the summary
            contact = title.replace("Overdue:", "").split("—")[-1].strip()[:40] if "—" in title else title[8:40]
            if len(overdue_loop_examples) < 3:
                overdue_loop_examples.append(contact)
            continue

        # Skip a plain "Relationship at risk: {name}" item when that same
        # person already has a richer relationship-decay-alert item -- see
        # the pre-scan above for why the two can both exist for one person.
        if title.lower().startswith("relationship at risk:"):
            _name = title.split(":", 1)[1].strip().lower()
            if _name in _decay_alert_names:
                continue

        # Deduplicate
        title_key = title.lower()[:60]
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        # Skip W-2 / job search / interview-stage items now that onboarding is active
        tl = title.lower()
        if "w-2" in tl or "job search" in tl:
            continue
        if "interview" in tl and any(k in tl for k in ["stage", "round", "commercial director", "awaiting response"]):
            continue

        # Skip "if i were your chief of staff" — belongs in CoS opening
        if "if i were your chief of staff" in title.lower():
            continue

        # Skip "Decision #N:" prefixed items — duplicates of real priority items
        if title.lower().startswith("decision #"):
            continue

        # Skip "this week's plan is drafted — awaiting confirmation" when plan is confirmed
        # (it will be shown as its own section with full content)
        tl = title.lower()
        if "week" in tl and "plan" in tl and ("drafted" in tl or "awaiting" in tl or "confirm" in tl):
            continue

        extras = item.get("extras") or {}
        state = (item.get("state") or "").upper()
        days_open = extras.get("days_open") or extras.get("age_days")
        age_str = f" ({days_open}d)" if days_open is not None else ""
        status_flag = " ⚠" if state in ("STALE", "BLOCKED") else (" ✓" if state in ("COMPLETE", "DONE", "CLOSED") else "")

        # Relationship decay alerts — add days-since-contact and weekly plan linkage
        tl_check = title.lower()
        if "relationship decay alert" in tl_check or "↘" in title or "↗" in title:
            # RB-DEFECT-2026-07-09: only "↘" was stripped from the title and
            # the rendered prefix was hardcoded to "↘" regardless of actual
            # direction, so an early-warning (↗, improving-but-still-low)
            # alert rendered as "↘ ↗  Jose Torres" -- the real arrow leaking
            # through as part of the "name" plus the wrong direction symbol
            # hardcoded in front of it.
            direction = "↗" if "↗" in title else "↘"
            contact_name = (
                title.replace("↗", "").replace("↘", "")
                .replace("Relationship decay alert:", "").strip()
            )
            days_since = extras.get("days_since_last_contact") or extras.get("days_since") or days_open
            _rc_tier = ""
            if days_since is None:
                # extras rarely carries this in practice -- fall back to the
                # real momentum data (same source RC Contact Status uses).
                _momentum_days, _rc_tier = _momentum_by_name.get(contact_name.lower(), (None, ""))
                days_since = _momentum_days
            decay_detail = ""
            if days_since is not None:
                if _rc_tier == "inner":
                    decay_detail = f" — inner circle, {days_since}d since last contact"
                else:
                    decay_detail = f" — {days_since}d since last contact"
            else:
                # RB 2026-08-27: confirmed live -- Jeff Weaver/David Kaun
                # aren't RC baseline contacts, so they have no
                # relationship_momentum_status entry either (that's DRR/
                # recency-index scoring for LKI contacts, a genuinely
                # different signal, not just a missing lookup). Don't
                # fabricate a day-count for a contact type that doesn't have
                # one -- reuse the real detail this item's own summary
                # already computed (DRR score, recency index, decay framing)
                # instead of leaving the bullet blank.
                _own_summary = (item.get("summary") or "").strip()
                _own_detail = _re_my_pri.sub(
                    rf"^{_re_my_pri.escape(contact_name)}\s*—\s*", "", _own_summary
                ).strip()
                if _own_detail:
                    decay_detail = f" — {_own_detail}"
            # Flag if this contact is in the weekly plan's inner-circle notification outcome
            weekly_plan = _load_weekly_plan()
            in_plan = False
            if weekly_plan:
                for outcome in (weekly_plan.get("outcomes") or []):
                    desc = (outcome.get("description") or "").lower()
                    if contact_name.lower().split()[0] in desc or "inner circle" in desc:
                        in_plan = True
                        break
            plan_note = " → **in week's plan: notify inner circle**" if in_plan else ""
            # RB 2026-08-27: reviewed live -- Todd: "Is telling the user
            # something they are aware [of] good use of this space?" on the
            # card-hint pattern. Replaced with the real status above instead
            # of an instruction to go ask RB for it.
            rendered.append(f"- {direction} **{contact_name}**{decay_detail}{plan_note}")
            continue

        # Warm path items — add days-since-contact
        if "warm path" in tl_check:
            days_since = extras.get("days_since_last_contact") or extras.get("days_since") or days_open
            days_str = f" — {days_since}d since last contact" if days_since is not None else ""
            rendered.append(f"- {title}{days_str}")
            continue

        rendered.append(f"- **{title}**{status_flag}")
        item_count += 1

        # RB-QUALITY-2026-09-04b: _compute_my_priorities() pulls loop items
        # straight from sections["loops_and_obligations"] via `dict(li)` --
        # a LITERAL copy, same title/summary/why_it_matters as the canonical
        # Loops & Obligations entry. Confirmed live: "Due today: L-2026-08-
        # 10-003 -- Little Caesars / Avery Churchwell" rendered the loop's
        # full description text a second time, verbatim. extras.
        # priority_category is set explicitly at assembly time for exactly
        # these items -- more reliable than re-deriving it from title text.
        if (extras.get("priority_category") == "loop") and (loop_ids := _loop_ids_in(title)):
            rendered.append(f"  {_loop_pointer_note(loop_ids)}")
            if item_count >= MAX_PRIORITIES:
                break
            continue

        # Generic-label titles ("This week's outcome: ...", "Weekly risk
        # flagged") carry zero content on their own -- the actual substance
        # lives in why_it_matters/summary, which this fallback branch
        # otherwise discards entirely. Show it as a detail line, but only
        # when it adds information the title doesn't already have -- skip
        # for items whose title is already self-explanatory (most of them).
        # Uses item_count (not len(rendered)) against MAX_PRIORITIES so this
        # extra line doesn't eat into the priority-count budget.
        # summary usually carries the specific fact (the actual risk text,
        # an outcome's description); why_it_matters is more often generic
        # boilerplate ("Risks... should shape what gets prioritized") --
        # prefer the more concrete field first.
        detail = (item.get("summary") or item.get("why_it_matters") or "").strip()
        # RB-2026-08-25: confirmed live -- two separate bugs let this
        # fallback leak content that never earns its place:
        # (1) "Event: [GP] Connect to Thrive..." rendered as a detail line
        #     under "[PREP] [GP] Connect to Thrive..." -- the redundancy
        #     check above compares full strings including the [PREP] badge,
        #     which the detail text never carries, so title.lower() was
        #     never actually a substring of detail.lower() and the check
        #     silently passed a 100%-redundant line through. Strip a
        #     leading [BADGE] before comparing.
        # (2) "Allocation: 100.0% -- items aligned to this outcome are
        #     boosted; unaligned items are dampened." -- internal scoring-
        #     engine language, the same class of leak already banned from
        #     Decision Queue/Bottom Line (_NOISE_PHRASES/_BOTTOM_NOISE) but
        #     never filtered here.
        _bare_title = _re_my_pri.sub(r"^\[[A-Z ]+\]\s*", "", title).strip()
        _is_noise = any(p in detail.lower() for p in _MY_PRIORITIES_DETAIL_NOISE)
        if (detail and not _is_noise
                and detail.lower() not in _bare_title.lower()
                and _bare_title.lower() not in detail.lower()):
            rendered.append(f"  *{_truncate_clean(detail, 200)}*")

        if item_count >= MAX_PRIORITIES:
            break

    # Add overdue loop summary line. `overdue_loop_count` only counts items that made it
    # into this (already-truncated) my_priorities list and matched the "Overdue: ..." title
    # format exactly — it silently undercounts (e.g. an item titled "Decision #3: Overdue
    # loop ..." isn't caught by either the collapse check above or this tally, and gets
    # dropped without being counted at all). CoS Opening/Bottom Line already surface the
    # true count via _get_comm_context()'s parse of communication_intelligence, which reads
    # the canonical loop_ledger-derived figure — reuse that here so this bullet always
    # agrees with the rest of the brief instead of quietly showing a smaller number.
    canonical_overdue = _get_comm_context(sections).get("loops_overdue") or 0
    display_overdue_count = max(overdue_loop_count, canonical_overdue)
    if display_overdue_count > 0:
        example_str = f" ({', '.join(overdue_loop_examples)}...)" if overdue_loop_examples else ""
        rendered.append(f"- **{display_overdue_count} overdue loops**{example_str} — review in RB")

    if rendered:
        lines.extend(rendered)
    else:
        lines.append("*No active priorities.*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section 4: Day Ahead
# ---------------------------------------------------------------------------

def _render_day_ahead(sections: dict, target_date: date) -> str:
    """Day Ahead — only render if events add prep context beyond My Priorities.

    Suppressed when all events are already listed in My Priorities with no
    additional info (time, attendees, prep context). Only surfaces additive detail.
    """
    import re as _re
    items = sections.get("day_ahead", [])
    cal_items = sections.get("calendar_intelligence", [])

    all_events = (cal_items or []) + [i for i in items
                                       if (i.get("extras") or {}).get("item_type") == "calendar_event"]
    # Collect My Priorities titles for dedup comparison
    priority_titles = {
        _re.sub(r"^\[[A-Z ]+\]\s*", "", (i.get("title") or "")).strip().lower()
        for i in sections.get("my_priorities", [])
    }

    lines = [f"## Day Ahead — {target_date.strftime('%A, %B %-d')}\n"]
    rendered_any = False
    seen_titles: set[str] = set()

    for item in all_events[:10]:
        title = (item.get("title") or "").strip()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        extras = item.get("extras") or {}
        time_str = extras.get("start_time") or extras.get("time") or ""
        attendees = extras.get("attendees") or []
        attendee_str = f" w/ {', '.join(attendees[:3])}" if attendees else ""
        cancelled = extras.get("cancelled") or extras.get("status") == "cancelled"
        summary = (item.get("summary") or "").strip()
        summary_clean = summary.removeprefix("Event:").strip()
        title_bare = _re.sub(r"^\[[A-Z ]+\]\s*", "", title).strip().lower()
        extra_detail = (f" — {summary_clean}"
                        if summary_clean and summary_clean.lower() not in (title.lower(), title_bare)
                        else "")
        # Only include if additive: has a time, attendees, prep context, or cancellation — not just a name repeat
        is_additive = bool(time_str or attendees or extra_detail or cancelled)
        if not is_additive and title_bare in priority_titles:
            continue  # pure duplicate — skip
        prefix = "**[!] CANCELLED:** " if cancelled else ""
        time_part = f" — {time_str}" if time_str else ""
        lines.append(f"- {prefix}**{title}**{time_part}{attendee_str}{extra_detail}")
        rendered_any = True

    # Non-calendar day-ahead items
    for item in items:
        extras = item.get("extras") or {}
        if extras.get("item_type") == "calendar_event":
            continue
        title = (item.get("title") or "").strip()
        if not title or title in seen_titles:
            continue
        title_bare = _re.sub(r"^\[[A-Z ]+\]\s*", "", title).strip().lower()
        if title_bare in priority_titles:
            continue
        seen_titles.add(title)
        summary = (item.get("summary") or "").strip()
        signal = extras.get("signal_type") or ""
        entry = f"- **{title}**"
        if summary:
            summary_clean = summary.removeprefix("Event:").strip()
            if summary_clean.lower() not in (title.lower(), title_bare):
                entry += f" — {_truncate_clean(summary_clean, 150)}"
        if signal:
            entry += f" `[{signal.upper()}]`"
        lines.append(entry)
        rendered_any = True

    if not rendered_any:
        return ""  # suppress entirely when nothing additive

    return "\n".join(lines)


def _compact_day_ahead(markdown: str) -> str:
    """Collapse multiple work sessions for one initiative into one agenda."""
    if not markdown:
        return ""
    import re as _re_day
    bullets = [line for line in markdown.splitlines() if line.startswith("- **")]
    grouped: dict[str, list[str]] = {}
    passthrough: list[str] = []
    for line in bullets:
        title = _re_day.sub(r"^- \*\*|\*\*$", "", line).replace("[PREP] ", "").strip()
        parts = title.split()
        key = " ".join(parts[:2]) if len(parts) >= 2 else title
        if key.lower().endswith("rfp"):
            grouped.setdefault(key, []).append(title)
        else:
            passthrough.append(line)
    compact = []
    for key, titles in grouped.items():
        if len(titles) >= 2:
            topics = [t[len(key):].strip(" -") for t in titles]
            compact.append(f"- **{key} — {len(titles)} working sessions:** " + "; ".join(topics[:5]))
        else:
            compact.append(f"- **[PREP] {titles[0]}**")
    compact.extend(passthrough[: max(0, 5 - len(compact))])
    if not compact:
        return ""
    heading = next((line for line in markdown.splitlines() if line.startswith("## ")), "## Day Ahead")
    return "\n\n".join([heading, "\n".join(compact[:5])])


def _render_priority_items(real_items: list[dict], title_prefix: str, window_label: str) -> list[str]:
    """Shared renderer for This Week / This Month priority items.

    RB-DEFECT-2026-07-27: each pre-earnings alert (earnings_monitor.py)
    carries its own full why_it_matters boilerplate ("Earnings calls
    surface the most candid executive language about...") repeated
    verbatim per company, just swapping a template phrase and the watch-
    for list. On a cycle with several companies reporting in the same
    window (PAR Technology, Olo, Toast, Yum Brands all "This Month"),
    that's 4 near-identical paragraphs. Consolidated into one line per
    watch_for group (vendor/tech companies vs. restaurant operators watch
    for different signal dimensions, so keep those separate) instead of
    repeating the full paragraph per company.
    """
    earnings_items = [i for i in real_items if (i.get("extras") or {}).get("earnings_type") == "pre_earnings_alert"]
    other_items = [i for i in real_items if i not in earnings_items]

    lines: list[str] = []
    for item in other_items[:8]:
        title = (item.get("title") or "").strip().removeprefix(title_prefix).strip()
        detail = (item.get("why_it_matters") or item.get("summary") or "").strip()
        lines.append(f"- **{title}**")
        if detail:
            lines.append(f"  *{_truncate_clean(detail, 200)}*")

    if earnings_items:
        by_watch: dict[str, list[str]] = {}
        for item in earnings_items:
            extras = item.get("extras") or {}
            watch_for = extras.get("watch_dimensions") or "tech-spend and platform-direction signals"
            company = extras.get("company") or (item.get("title") or "").strip()
            by_watch.setdefault(watch_for, []).append(company)
        for watch_for, companies in by_watch.items():
            names = ", ".join(dict.fromkeys(companies))
            lines.append(f"- **Earnings {window_label}:** {names} — watch for {watch_for}.")

    return lines


def _render_this_week_priorities(sections: dict) -> str:
    """This Week -- near-term (2-7 day) priorities: deliverable-shaped
    meetings, upcoming earnings calls, active interview stages. Computed
    daily by daily_brief.py's _compute_this_week_priorities but had no
    Python renderer at all until now -- silently discarded before ever
    reaching the brief Todd reads."""
    items = sections.get("this_week_priorities") or []
    real_items = [i for i in items if i.get("disposition") != "ignore"]
    lines = ["## This Week\n"]
    if not real_items:
        lines.append("No near-term priorities detected.")
        return "\n".join(lines)
    lines.extend(_render_priority_items(real_items, "This week: ", "this week"))
    return "\n".join(lines)


def _render_this_month_priorities(sections: dict) -> str:
    """This Month -- strategic priorities landing 8-30 days out. Same
    'computed but never rendered' gap as This Week (see above)."""
    items = sections.get("this_month_priorities") or []
    real_items = [i for i in items if i.get("disposition") != "ignore"]
    lines = ["## This Month\n"]
    if not real_items:
        lines.append("No strategic priorities detected.")
        return "\n".join(lines)
    lines.extend(_render_priority_items(real_items, "This month: ", "this month"))
    return "\n".join(lines)


def _render_capacity_plan(sections: dict) -> str:
    """Capacity Plan -- when the calendar is light, convert unscheduled time
    into a deliberate allocation instead of leaving "calendar clear" as a
    dead end. Suppressed entirely when the calendar's already full enough
    that the compute layer decided no plan was needed (empty list)."""
    items = sections.get("capacity_plan") or []
    if not items:
        return ""
    item = items[0]
    extras = item.get("extras") or {}
    blocks = extras.get("allocation_blocks") or []
    available = extras.get("available_hours")
    header = (
        f"## Capacity Plan — {available:.0f}h unscheduled\n" if isinstance(available, (int, float))
        else "## Capacity Plan\n"
    )
    lines = [header]
    if not blocks:
        why = (item.get("why_it_matters") or "").strip()
        if why:
            lines.append(why)
        return "\n".join(lines)
    for b in blocks[:5]:
        label = (b.get("label") or "").strip()
        time_est = (b.get("time") or "").strip()
        actions = b.get("actions") or []
        time_str = f" ({time_est})" if time_est else ""
        lines.append(f"- **{label}**{time_str}")
        for a in actions[:2]:
            a = (a or "").strip()
            if a:
                lines.append(f"  - {a}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# RB-2026-08-25: Part 2 field-coverage sweep. Traced every field in
# _ACTION_BRIEF_SECTIONS_PART2 (server.py) against this renderer and found
# 13 with zero implementation -- computed daily, shipped in the API
# payload, never displayed. this_week_priorities/this_month_priorities
# (above) already got fixed once for this same bug class; these are the
# rest. Ranked by real content volume/urgency when found: 0 of 13 had any
# item with disposition=="ask_todd", so none were even partially caught by
# decision_queue's catch-all sweep -- full silent loss, not partial.
# ---------------------------------------------------------------------------

def _render_simple_item_list(items: list[dict], header: str, empty_message: str,
                              limit: int = 7) -> str:
    """Shared renderer for the lower-structure additions below — title +
    why_it_matters (or summary), capped, act_today items first."""
    real_items = [i for i in (items or []) if i.get("disposition") != "ignore"]
    lines = [f"## {header}\n"]
    if not real_items:
        lines.append(empty_message)
        return "\n".join(lines)
    ranked = sorted(real_items, key=lambda i: 0 if i.get("disposition") == "act_today" else 1)
    for item in ranked[:limit]:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        detail = (item.get("why_it_matters") or item.get("summary") or "").strip()
        # RB-2026-09-10: bare `[:220]` cut mid-word ("Coverage gaps: 0 t…",
        # hiding the actual "10 recurring 14-day pattern(s)" that justified
        # the item's act_today flag) -- confirmed live in Downstream
        # Artifact Cascade. Same bug class _truncate_clean was already built
        # for (see its docstring), just never applied to this shared
        # renderer, which most of this file's simpler sections go through.
        detail = _truncate_clean(detail, 220)
        prefix = "[ACTION TODAY] " if item.get("disposition") == "act_today" else ""
        lines.append(f"- {prefix}**{title}**" + (f" — {detail}" if detail else ""))
    remaining = len(real_items) - min(limit, len(ranked))
    if remaining > 0:
        lines.append(f"\n*{remaining} additional item(s) not shown.*")
    return "\n".join(lines)


def _render_cos_today(sections: dict) -> str:
    """If I Were Your CoS Today — a single pre-synthesized item (numbered
    action list already formatted in `summary`); rendered as a direct
    passthrough rather than re-parsed."""
    items = sections.get("cos_today") or []
    if not items:
        return ""
    item = items[0]
    title = (item.get("title") or "If I Were Your Chief of Staff Today").strip()
    body = (item.get("summary") or "").strip()
    chronic = " ".join(
        f"{i.get('title') or ''} {i.get('summary') or ''}"
        for i in (sections.get("strategic_risks") or [])
        if "chronic source failure" in (i.get("title") or "").lower()
    ).lower()
    comms_unverifiable = any(s in chronic for s in ("email:", "calendar:", "linkedin_messaging"))
    lines = []
    if body:
        import re as _re_cos_render
        numbered = []
        action_no = 0
        seen_actions: set[str] = set()
        for line in body.splitlines():
            if _re_cos_render.match(r"^\d+\.\s+", line.strip()):
                content = _re_cos_render.sub(r"^\d+\.\s+", "", line.strip())
                if comms_unverifiable and any(term in content.lower() for term in (
                    "linkedin", "email", "schedule", "meeting", "follow up", "follow-up", "wesolowski",
                )):
                    continue
                action_key = _re_cos_render.sub(r"\s+—.*$", "", content.lower()).strip()
                if action_key in seen_actions:
                    continue
                seen_actions.add(action_key)
                action_no += 1
                line = _re_cos_render.sub(r"^\d+\.\s+", f"{action_no}. ", line.strip(), count=1)
            if line.strip():
                numbered.append(line.strip())
        if numbered:
            label = f"If I Were Your Chief of Staff Today — {action_no} Action{'s' if action_no != 1 else ''}"
            lines = [f"## {label}\n", "\n".join(numbered)]
    if not lines:
        return ""
    return "\n".join(lines)


def _render_loops_and_obligations(sections: dict) -> str:
    """Loops & Obligations — all open loops, grouped by their real bucket so
    overdue items (the ones actually blocking follow-through) lead and
    later-dated loops aren't mislabeled as due today."""
    items = [i for i in (sections.get("loops_and_obligations") or [])
             if i.get("disposition") != "ignore"]
    lines = ["## Loops & Obligations\n"]
    if not items:
        lines.append("No open loops.")
        return "\n".join(lines)
    groups: dict[str, list[dict]] = {"overdue": [], "due_today": [], "this_week": [], "future": []}
    for item in items:
        bucket = (item.get("extras") or {}).get("loop_bucket")
        if bucket not in groups:
            bucket = "overdue" if (item.get("title") or "").lower().startswith("overdue") else "this_week"
        groups[bucket].append(item)
    for bucket, group_label in (
        ("overdue", "Overdue"), ("due_today", "Due Today"),
        ("this_week", "Closing This Week"), ("future", "Upcoming"),
    ):
        group = groups[bucket]
        if not group:
            continue
        lines.append(f"**{group_label}**")
        for item in group[:8]:
            title = (item.get("title") or "").strip()
            # RB-QUALITY-2026-09-04b: this rendered `recommended_action`, a
            # generic templated action string ("Close or re-date L-...",
            # "Prepare next step for L-..."), never `summary` -- the loop's
            # actual real description (the reason it exists at all). Todd's
            # consolidation call made THIS section the one canonical place
            # other sections point to for "full detail" -- confirmed live
            # that promise was false: the loop's real content was rendered
            # nowhere else once those other sections were trimmed to a
            # pointer here. summary now leads; recommended_action only
            # fills in when a loop genuinely has no description.
            detail = (item.get("summary") or item.get("recommended_action") or "").strip()
            detail = _truncate_clean(detail, 220)
            lines.append(f"- **{title}**" + (f" — {detail}" if detail else ""))
    return "\n".join(lines)


def _render_decision_layer(sections: dict) -> str:
    """Top Decisions Today — ranked, highest-ROI decisions. Distinct from
    Decision Queue above (which sweeps disposition==ask_todd items across
    every section): this is a specifically-curated top-N list, and none of
    its items carry ask_todd, so it was invisible to that sweep."""
    items = [i for i in (sections.get("decision_layer") or [])
             if i.get("disposition") != "ignore"]
    chronic_text = " ".join(
        f"{i.get('title') or ''} {i.get('summary') or ''}"
        for i in (sections.get("strategic_risks") or [])
        if "chronic source failure" in (i.get("title") or "").lower()
    ).lower()
    if "email:" in chronic_text or "calendar:" in chronic_text:
        items = [i for i in items if not (
            any("loop_ledger" in str(ref) for ref in (i.get("source_refs") or []))
            and any(term in f"{i.get('summary') or ''} {i.get('recommended_action') or ''}".lower()
                    for term in ("email", "linkedin", "schedule", "meeting", "follow up", "follow-up"))
        )]
    import re as _re_decision_age
    _today = date.today()
    _fresh_items = []
    for item in items:
        if (item.get("extras") or {}).get("source_section") == "last_24h_relationship_signals":
            m = _re_decision_age.search(r"last contact\s+(\d{4}-\d{2}-\d{2})", item.get("summary") or "", _re_decision_age.IGNORECASE)
            if m:
                try:
                    if (_today - date.fromisoformat(m.group(1))).days > 2:
                        continue
                except ValueError:
                    continue
        _fresh_items.append(item)
    items = _fresh_items
    if not items:
        return ""
    # RB-QUALITY-2026-09-04: same repeated-boilerplate pattern already fixed
    # in Risks and 5 Actions -- overdue loops with no more specific reason
    # all get the identical hardcoded "Past target obligations are action
    # debt and should outrank ambient context." (see daily_brief.py), so 2+
    # decisions in a row showed the same sentence with zero decision-
    # specific content. Same fix: show it once, drop the repeat -- each
    # item's own title and action are already distinct.
    lines = ["## Top Decisions Today\n"]
    _seen_why_this_section: set[str] = set()
    for item in items[:5]:
        title = (item.get("title") or "").strip()
        lines.append(f"**{title}**")

        # RB-QUALITY-2026-09-04b: a loop-linked decision (title carries an
        # L-YYYY-MM-DD-NNN id) is already fully spelled out in Loops &
        # Obligations -- Todd's own read is that this section's restatement
        # of the same loop is skimmed, not read. Point at the canonical
        # section instead of repeating why/action.
        loop_ids = _loop_ids_in(title)
        if loop_ids:
            lines.append(_loop_pointer_note(loop_ids))
            lines.append("")
            continue

        why = (item.get("why_it_matters") or "").strip()
        why_key = why.lower()
        if why_key and why_key in _seen_why_this_section:
            why = ""
        elif why_key:
            _seen_why_this_section.add(why_key)
        if why:
            lines.append(why)
        action = (item.get("recommended_action") or "").strip()
        if action:
            lines.append(f"→ {action}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _render_strategic_risks(sections: dict) -> str:
    """Risks — standalone risk synthesis, act_today items flagged."""
    items = [i for i in (sections.get("strategic_risks") or [])
             if i.get("disposition") != "ignore"]
    items = [i for i in items if not (
        "relationship deterioration" in (i.get("title") or "").lower()
        and not ((i.get("extras") or {}).get("contact_name") or (i.get("extras") or {}).get("person"))
    )]
    lines = ["## Risks\n"]
    if not items:
        lines.append("No new risks detected.")
        return "\n".join(lines)
    # RB-QUALITY-2026-09-04: confirmed live -- every risk without its own
    # specific why_it_matters falls back to the identical hardcoded string
    # ("Risks to this week's outcomes should shape what gets prioritized
    # and what gets deferred."), so 3 different named risks rendered the
    # same sentence 3 times with zero risk-specific content. Real fix would
    # be giving each risk type its own reasoning (bigger change); the
    # narrower one here just stops repeating the identical sentence -- show
    # it once, then drop the redundant why/action for a repeat and keep
    # just the risk title, which is still the actual new information.
    _seen_why_this_section: set[str] = set()
    for item in items[:6]:
        title = (item.get("title") or "").strip()
        why = (item.get("why_it_matters") or "").strip()
        action = (item.get("recommended_action") or "").strip()
        prefix = "[ACTION TODAY] " if item.get("disposition") == "act_today" else "[MONITOR] "
        why_key = why.strip().lower()
        if why_key and why_key in _seen_why_this_section:
            why = ""
        elif why_key:
            _seen_why_this_section.add(why_key)
        if "chronic source failure" in title.lower():
            chronic_rows = (item.get("extras") or {}).get("chronic_sources") or []
            named = ", ".join(
                f"{row.get('source')} ({row.get('days_unhealthy')}d)"
                for row in chronic_rows if row.get("source")
            )
            lines.append(f"- {prefix}**{title}**")
            if named:
                lines.append(f"  Broken sources: {named}.")
            if action:
                lines.append(f"  RB recovery: {action}")
            continue
        detail = " — ".join(filter(None, [why, action]))
        detail = _truncate_clean(detail, 220)
        lines.append(f"- {prefix}**{title}**" + (f" — {detail}" if detail else ""))
    return "\n".join(lines)


def _render_relationship_momentum_status(sections: dict) -> str:
    """RC Contact Status — FROZEN/COLD contacts and overdue touchpoints
    lead, grouped ahead of COOLING/WARM/HOT."""
    items = [i for i in (sections.get("relationship_momentum_status") or [])
             if i.get("disposition") != "ignore"]
    lines = ["## RC Contact Status\n"]
    if not items:
        lines.append("No RC contacts in baseline.")
        return "\n".join(lines)

    baseline_only_unverified = [
        i for i in items
        if (i.get("extras") or {}).get("completeness_status") in {"PARTIAL", "INCOMPLETE"}
        and not (i.get("extras") or {}).get("observed_touch")
        and "INCOMPLETE" not in (i.get("title") or "")
    ]
    if baseline_only_unverified:
        missing = sorted({
            source
            for i in baseline_only_unverified
            for source in ((i.get("extras") or {}).get("missing_sources") or [])
        })
        lines.append(
            f"**Coverage incomplete — {len(baseline_only_unverified)} baseline-only momentum classification(s) withheld.** "
            + (f"Missing: {', '.join(missing)}. " if missing else "")
            + "RB will not recommend outreach until recent communication sources are reconciled.\n"
        )
        items = [i for i in items if i not in baseline_only_unverified]
        if not items:
            return "\n".join(lines)

    import re as _re

    def _tier_rank(item: dict) -> int:
        title = (item.get("title") or "").upper()
        if "FROZEN" in title:
            # RB-2026-09-10: FROZEN has no upper bound -- a contact quiet
            # 202 days and one quiet 2,054 days (5.6 years, confirmed live:
            # Bridgett Hendrickson) both ranked identically at the top,
            # ahead of every COLD contact, on pure alphabetical/upstream
            # order. A years-dormant contact isn't more urgent than a
            # 90-day COLD one just because its own bucket sorts first --
            # sink genuinely ancient dormancy (1+ year) below COLD instead
            # of letting it compete for the visible top-10 slots ahead of
            # fresher relationship-decay signals. Still shown, just last.
            m = _re.search(r"\((\d+)D QUIET\)", title)
            days_quiet = int(m.group(1)) if m else 0
            return 2 if days_quiet > 365 else 0
        if "COLD" in title:
            return 1
        if "COOLING" in title:
            return 3
        return 4

    ranked = sorted(items, key=_tier_rank)
    action_needed = [i for i in ranked if _tier_rank(i) <= 2]
    other = [i for i in ranked if _tier_rank(i) > 2]
    shown = action_needed[:10]
    if baseline_only_unverified and not shown and other:
        lines.append("**Verified recent activity**")
        verified = sorted(
            other,
            key=lambda i: (i.get("extras") or {}).get("observed_touch") or "",
            reverse=True,
        )[:5]
        for item in verified:
            extras = item.get("extras") or {}
            sources = ", ".join(extras.get("observed_sources") or [])
            lines.append(
                f"- **{extras.get('contact_name') or item.get('title')}** — "
                f"last observed {extras.get('observed_touch')} via {sources or 'verified communication source'}"
            )
        other = [i for i in other if i not in verified]
    # RB-2026-09-10: cut_from_top10 didn't exist before the ancient-FROZEN
    # re-rank above -- confirmed live: with it in place, 13 contacts had
    # _tier_rank<=2 (real FROZEN/COLD) but only the first 10 rendered, and
    # the 3 cut (including Bridgett Hendrickson, 2,054d quiet) vanished with
    # no trace anywhere in the brief -- `other`'s count only ever covered
    # the genuinely-warmer COOLING/WARM/HOT tail, never FROZEN/COLD contacts
    # that simply didn't fit. Report both counts honestly rather than
    # silently dropping real (if lower-priority) contacts.
    cut_from_top10 = action_needed[10:]
    cut_ancient = sum(1 for i in cut_from_top10 if _tier_rank(i) == 2)
    for item in shown:
        title = (item.get("title") or "").strip()
        action = (item.get("recommended_action") or "").strip()
        action = _truncate_clean(action, 150)
        lines.append(f"- [ACTION NEEDED] **{title}**" + (f" — {action}" if action else ""))
    if cut_from_top10:
        ancient_note = f" ({cut_ancient} long-dormant, 1+ year quiet)" if cut_ancient else ""
        lines.append(
            f"\n*{len(cut_from_top10)} more FROZEN/COLD contact(s) not shown{ancient_note}.*"
        )
    if other:
        lines.append(f"\n*{len(other)} additional contact(s) in warmer momentum tiers not shown.*")
    return "\n".join(lines)


def _render_email_intelligence_harvest(sections: dict, already_shown_keys: set[str] | None = None) -> str:
    """`already_shown_keys` — RB-2026-09-10: normalized (bracket-stripped,
    lowercased) titles GP/Genius: Dot Connections already rendered this run
    (see _render_gp_dot_connections's shown_keys_out). Confirmed live: the
    same Payments Dive "Latitude...stablecoins" story rendered in full here
    a second time, once under GP/Genius's watchlist-escalation framing and
    again here under email_intelligence_harvest's known-contact framing --
    two separate ingestion pipelines (market_signals/newsletter scan vs.
    passive email scan) picking up the same real-world story with no shared
    identity between them. GP/Genius renders first and already gives the
    reader the link + a why-it-matters, so drop it here rather than restate
    it under different words."""
    items = sections.get("email_intelligence_harvest") or []
    if already_shown_keys:
        import re as _re

        def _already_shown(title: str) -> bool:
            # RB-2026-09-10: exact-match against already_shown_keys missed
            # this pair live -- this title is already truncated to ~100
            # chars (word-boundary, trailing "…") by the time it gets here,
            # while GP/Genius's key is the untruncated full headline, so the
            # two normalized strings differ even for the identical story.
            # Prefix-overlap catches it either direction; the length guard
            # avoids a false match on a short, generic title.
            norm = _re.sub(r"^\[[^\]]+\]\s*", "", title).strip().lower()
            norm = norm.rstrip("…").rstrip()
            if len(norm) < 30:
                return norm in already_shown_keys
            return any(norm.startswith(k) or k.startswith(norm) for k in already_shown_keys)

        items = [i for i in items if not _already_shown(i.get("title") or "")]
    return _render_simple_item_list(
        items,
        "Newsletter & Email Intelligence",
        "No newsletter or sent-thread intelligence this cycle.",
    )


def _render_competitive_vulnerability_watchlist(sections: dict) -> str:
    material = []
    for item in sections.get("competitive_vulnerability_watchlist") or []:
        extras = item.get("extras") or {}
        entity = extras.get("entity_name") or extras.get("company")
        text = " ".join(str(item.get(k) or "") for k in ("title", "summary", "why_it_matters", "recommended_action")).lower()
        generic = any(p in text for p in ("monitor competitive exposure", "assess competitive posture", "score:"))
        if entity and not generic and item.get("why_it_matters") and item.get("recommended_action"):
            material.append(item)
    if not material:
        return ""
    return _render_simple_item_list(material, "Competitive Opportunity Watchlist", "", limit=5)


def _render_competitor_battle_card_review(sections: dict) -> str:
    """RB-2026-09-01 — pending items from competitor_intelligence_review.py's
    daily scan (new material signals + stale category battle cards)."""
    deduped, seen = [], set()
    for item in sections.get("competitor_battle_card_review") or []:
        key = (item.get("title") or "").strip().lower()
        if "still pending" in key:
            continue
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)
    return _render_simple_item_list(
        deduped,
        "Competitor Battle Card Review",
        "No competitor battle-card reviews pending this cycle.",
    )


def _render_downstream_artifact_cascade(sections: dict) -> str:
    """RB-2026-08-28 — proof that new intelligence was assessed against
    Blue Sheets / Account Research today. See intelligence_cascade.py."""
    items = sections.get("downstream_artifact_cascade") or []
    import re as _re_cascade
    material = []
    for item in items:
        text = " ".join(str(item.get(k) or "") for k in ("title", "summary", "why_it_matters"))
        nums = [int(n) for n in _re_cascade.findall(r"\b\d+\b", text)]
        impact_patterns = (
            r"([1-9]\d*)\s+field\(s\) auto-synced",
            r"([1-9]\d*)\s+queued for review",
            r"([1-9]\d*)\s+flagged stale",
            r"([1-9]\d*)\s+vendor plan\(s\) auto-synced",
            r"([1-9]\d*)\s+relationship\(s\) touched",
        )
        if any(_re_cascade.search(p, text, _re_cascade.IGNORECASE) for p in impact_patterns):
            material.append(item)
    if not material:
        return ""
    return _render_simple_item_list(
        material,
        "Downstream Artifact Cascade",
        "No downstream-artifact cascade report this cycle.",
    )


def _render_weekly_plan_focus(sections: dict) -> str:
    return _render_simple_item_list(
        sections.get("weekly_plan_focus"),
        "This Week's Outcomes",
        "No weekly-outcome focus items this cycle.",
        limit=5,
    )


def _render_upcoming_prep_requirements(sections: dict) -> str:
    return _render_simple_item_list(
        sections.get("upcoming_preparation_requirements"),
        "Prep Required",
        "No upcoming preparation requirements detected.",
    )


def _render_learned_patterns(sections: dict) -> str:
    items = sections.get("learned_patterns") or []
    if not items:
        return ""
    return _render_simple_item_list(items, "Learned Patterns", "", limit=5)


def _render_last_24h_relationship_signals_p2(sections: dict) -> str:
    # RB 2026-08-27: reviewed live -- Todd, on Jeff Wayman/Amy Spytko (real
    # texts he sent himself) and Noelle Labrie/Daran Adair (stale
    # linkedin_messaging data -- confirmed no real new activity): "Is
    # telling the user something they are aware [of] good use of this
    # space? Intelligence should tell us something we don't know." This
    # section previously dumped the entire last_24h_relationship_signals
    # list unfiltered, bypassing the exact discovery-value classification
    # already built for "What RB Found" (direct_interaction and
    # linkedin_inbound_message score "low" -- Todd was inherently a party,
    # or the signal has no real inferred content behind it). Apply the same
    # filter here instead of building a second standard: only genuinely
    # inferred relationship intelligence (an IME-detected insight, a
    # communication-risk/channel-escalation signal) earns a spot, not a
    # bare "you talked to X" activity log.
    items = [
        i for i in (sections.get("last_24h_relationship_signals") or [])
        if (i.get("novelty") or {}).get("autonomous_discovery_value") != "low"
    ]
    return _render_simple_item_list(
        items,
        "New Relationship Activity",
        "No new relationship activity in the last 24h.",
    )


def _render_job_intelligence(sections: dict) -> str:
    items = sections.get("job_intelligence") or []
    if not items:
        return ""
    return _render_simple_item_list(items, "Job Market Scan", "", limit=5)


def _render_pending_graph_mutations(sections: dict) -> str:
    items = sections.get("pending_graph_mutations") or []
    if not items:
        return ""
    return _render_simple_item_list(
        items, "Pending Graph Mutations", "", limit=6,
    )


# ---------------------------------------------------------------------------
# Section 5: Decision Queue + Recommended Actions
# ---------------------------------------------------------------------------

def _render_decision_queue(sections: dict) -> str:
    items = sections.get("decision_queue", [])
    if not items:
        return "## Decision Queue\n\n*No open decisions.*"

    lines = ["## Decision Queue\n"]
    _NOISE_PHRASES = [
        "action orchestration prompt",
        "meeting prep queue",
        "loops rb can auto-close",
        "loops rb can auto close",
        "auto-close",
        "strategic operator proximity",
        "create tracked loops",
        "run end-of-day closeout",
        "end-of-day closeout",
        "escalate_priority",
        "escalate priority",
        "open_loop:",
        "close_loop:",
        "send_followup:",
        "interaction_newer_than_recorded_last_touch",
        "interaction_newer_than",
        "last_touch_update",
        "update_last_touch",
        "week's plan is drafted",
        "weekly plan is drafted",
        "plan is drafted — awaiting",
        # RB-2026-07-15: raw internal action-type strings and calendar-hygiene
        # noise leaked into Decision Queue as if they were real decisions —
        # "create_task: Jeff Wayman", "monitor_relationship: RB system",
        # "Calendar attendee not in baseline: steven@..." are pipeline
        # bookkeeping, not something Todd can or should act on.
        "calendar attendee not in baseline",
        "create_task:",
        "monitor_relationship:",
        # RB-2026-07-20: internal RB data-hygiene/config-reconciliation tasks
        # were reaching the CEO-facing Decision Queue under a generic title
        # ("One decision needed") that this filter's title-only check never
        # matched -- the identifying phrase only ever appeared in the detail
        # text (recommended_action/why_it_matters), which _is_noise didn't
        # look at. Confirmed live: "Confirm or correct the operator
        # relationship proximity in strategic_operators.yaml." rendered as a
        # CEO decision. See _is_noise below for the full-text fix.
        "one decision needed",
        "strategic_operators.yaml",
        "operator relationship proximity",
        "reconciliation prompt",
        "chatgpt briefing destination",
        "pain mapping under-instrumented",
        "confirm state mutation via ingestexecutivedeclaration",
        "enable linkedin feed monitoring",
    ]
    def _is_noise(item: dict) -> bool:
        # Check title + detail text together -- several housekeeping items
        # use a generic title ("One decision needed") with the actual
        # identifying content only in why_it_matters/summary/recommended_action.
        t = " ".join([
            item.get("title") or "",
            item.get("why_it_matters") or "",
            item.get("summary") or "",
            item.get("recommended_action") or "",
        ]).lower()
        return any(p in t for p in _NOISE_PHRASES)

    def _decision_item_action_hint(item: dict) -> str:
        """RB deep-link so a decision/action item can be acted on from chat
        instead of only being read. Loop-shaped items point at the loop;
        named-contact items reuse the existing contact-card hint pattern."""
        extras = item.get("extras") or {}
        title = (item.get("title") or "")
        m = _LOOP_ID_RE.search(title)
        if m:
            return f' · ask RB "what\'s the latest on loop {m.group(0)}?"'
        contact = extras.get("contact_name") or extras.get("named_role") or ""
        if contact:
            return _contact_card_hint(contact).strip()
        return ""

    decisions_required = [i for i in items
                          if (i.get("extras") or {}).get("requires_decision") and not _is_noise(i)]
    other_actions = [i for i in items
                     if not (i.get("extras") or {}).get("requires_decision") and not _is_noise(i)]
    # Generic research/prep assignments are RB work, not decisions Todd must
    # make. Keep concrete loop/contact actions that can actually be executed.
    _rb_work_phrases = (
        "research support for", "prepare for ", "prep for ", "prep needed:",
        "background research", "[research]", "[build pursuit]",
    )
    other_actions = [i for i in other_actions if not any(
        p in " ".join(str(i.get(k) or "") for k in ("title", "summary", "recommended_action")).lower()
        for p in _rb_work_phrases
    )]

    if decisions_required:
        lines.append("### DECISIONS REQUIRED")
        for item in decisions_required:
            title = (item.get("title") or "").strip()
            extras = item.get("extras") or {}
            deadline = extras.get("deadline") or ""
            deadline_str = f" **[by {deadline}]**" if deadline else ""
            hint = _decision_item_action_hint(item)
            lines.append(f"- **{title}**{deadline_str}{hint}")
            # RB-QUALITY-2026-09-04b: a loop-linked item (title carries an
            # L-YYYY-MM-DD-NNN id) is already fully spelled out in Loops &
            # Obligations -- point there instead of restating the same
            # why/summary sentence a second time.
            loop_ids = _loop_ids_in(title)
            if loop_ids:
                lines.append(f"  {_loop_pointer_note(loop_ids)}")
            else:
                why = _clean_why((item.get("why_it_matters") or item.get("summary") or "").strip())
                if why:
                    lines.append(f"  *{_truncate_clean(why, 200)}*")
        lines.append("")

    def _action_detail(item: dict, title: str) -> str:
        """Generic-label titles ('One decision needed', from reconciliation
        prompts) carry zero content on their own -- the real substance is in
        recommended_action/why_it_matters/summary, which this loop otherwise
        discarded entirely. Add it as a detail line, but only when it says
        something the title doesn't already.

        RB-QUALITY-2026-09-04b: a loop-linked item is already fully spelled
        out in Loops & Obligations -- a pointer there instead of the same
        recommended_action sentence restated a second time."""
        loop_ids = _loop_ids_in(title)
        if loop_ids:
            return f"\n  {_loop_pointer_note(loop_ids)}"
        detail = (item.get("recommended_action") or item.get("why_it_matters")
                  or item.get("summary") or "").strip()
        if detail and detail.lower() not in title.lower() and title.lower() not in detail.lower():
            return f"\n  *{_truncate_clean(detail, 200)}*"
        return ""

    if other_actions:
        lines.append("### RECOMMENDED ACTIONS")

        # Group by time horizon
        today_actions = [i for i in other_actions
                         if (i.get("extras") or {}).get("time_horizon") in ("today", "24h", None)]
        week_actions = [i for i in other_actions
                        if (i.get("extras") or {}).get("time_horizon") in ("this_week", "7d", "week")]
        horizon_actions = [i for i in other_actions
                           if (i.get("extras") or {}).get("time_horizon") in ("30_60_90", "30d", "60d", "90d")]

        if today_actions:
            lines.append("**Today**")
            for item in today_actions[:8]:
                title = (item.get("title") or "").strip()
                extras = item.get("extras") or {}
                role = extras.get("named_role") or extras.get("contact_name") or ""
                role_str = f" [{role}]" if role else ""
                hint = _decision_item_action_hint(item)
                lines.append(f"- {title}{role_str}{hint}{_action_detail(item, title)}")
            lines.append("")

        if week_actions:
            lines.append("**This Week**")
            for item in week_actions[:5]:
                title = (item.get("title") or "").strip()
                hint = _decision_item_action_hint(item)
                lines.append(f"- {title}{hint}{_action_detail(item, title)}")
            lines.append("")

        if horizon_actions:
            lines.append("**30–60–90 Days**")
            for item in horizon_actions[:5]:
                title = (item.get("title") or "").strip()
                extras = item.get("extras") or {}
                horizon = extras.get("time_horizon") or ""
                lines.append(f"- {title} `[{horizon}]`" if horizon else f"- {title}")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Horizon Watch
# ---------------------------------------------------------------------------

def _render_horizon_watch(sections: dict) -> str:
    """Horizon Watch — group items sharing the same date; expand only the top item."""
    items = sections.get("horizon_watch", [])
    if not items:
        return ""

    import re as _re
    from collections import defaultdict

    # Group by horizon date
    by_date: dict[str, list[dict]] = defaultdict(list)
    dateless: list[dict] = []
    for item in items:
        extras = item.get("extras") or {}
        horizon = extras.get("horizon_date") or extras.get("horizon") or ""
        title = _re.sub(r"\s*—\s*\d+ DAYS\s*$", "", (item.get("title") or "").strip()).strip()
        item = dict(item)
        item["_title_clean"] = title
        item["_horizon"] = horizon
        if horizon:
            by_date[horizon].append(item)
        else:
            dateless.append(item)

    lines = ["## Horizon Watch\n"]
    rendered_groups = 0

    for horizon_date, group in sorted(by_date.items()):
        if rendered_groups >= 4:
            break
        if len(group) == 1:
            item = group[0]
            title = item["_title_clean"]
            summary = (item.get("summary") or "").strip()
            lines.append(f"- **{title}** *({horizon_date})*")
            if summary and rendered_groups == 0:
                # Only expand summary for the very first (most imminent) item
                summary_trunc = (summary[:197] + "…") if len(summary) > 200 else summary
                lines.append(f"  {summary_trunc}")
        else:
            # Multiple items on same date — lead with count, list names inline
            names = [i["_title_clean"].replace("[PRE-EARNINGS] ", "") for i in group]
            # Show first item's summary if it's the first group
            first_title = group[0]["_title_clean"]
            first_summary = (group[0].get("summary") or "").strip()
            lines.append(f"- **{first_title}** *({horizon_date})* + {len(group)-1} more: {', '.join(names[1:])}")
            if first_summary and rendered_groups == 0:
                summary_trunc = (first_summary[:197] + "…") if len(first_summary) > 200 else first_summary
                lines.append(f"  {summary_trunc}")
        rendered_groups += 1

    for item in dateless[:2]:
        title = item["_title_clean"]
        lines.append(f"- **{title}**")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# User Active Context
# ---------------------------------------------------------------------------

def _render_active_context(sections: dict) -> str:
    items = sections.get("user_active_context", [])
    if not items:
        return ""

    lines = ["## Active Context\n"]
    for item in items:
        title = (item.get("title") or "").strip()
        summary = (item.get("summary") or "").strip()
        if title:
            lines.append(f"**{title}**")
        if summary:
            summary_trunc = (summary[:297] + "…") if len(summary) > 300 else summary
            lines.append(summary_trunc)
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Proposed Relationship Mutations (cos_synthesis.py)
# ---------------------------------------------------------------------------

def _render_proposed_relationship_mutations(sections: dict) -> str:
    """Proposed Relationship Mutations -- behavioral-signal-driven contact
    reclassification proposals from cos_synthesis.detect_relationship_
    mutations().

    RB-2026-08-24: this section was computed with real confidence
    thresholds (evidence score -> HIGH/MEDIUM/LOW, auto_apply flag on HIGH)
    but only ever reached the reader through a "rendering_rules" GPT-
    instruction list that server.py's compact payload hardcodes to []
    (dead since the pre-rendered-markdown pipeline superseded GPT-side
    generation) -- so these proposals were computed and then never seen by
    anyone. There is also no existing API endpoint that can apply a
    classification/tag upgrade to baseline_index.json (unlike, say,
    updateWatchList for watchlist entries), so unlike that gap this one
    stays deliberately report-only: surfaced clearly, explicitly labeled as
    requiring a manual baseline edit today, not silently promised an
    auto-apply path that doesn't exist.
    """
    items = sections.get("proposed_relationship_mutations", [])
    if not items:
        return ""

    lines = ["## Proposed Relationship Mutations\n"]
    for item in items[:5]:
        extras = item.get("extras") or {}
        contact_name = extras.get("contact_name") or "Unknown Contact"
        current = extras.get("current_classification") or "unknown"
        proposed = extras.get("proposed_classification") or "unknown"
        confidence = extras.get("confidence") or item.get("confidence") or ""
        evidence = extras.get("evidence_summary") or ""
        tags = extras.get("proposed_tags") or []
        lines.append(f"- **{contact_name}**: {current} → {proposed} [{confidence}]")
        if evidence:
            lines.append(f"  Evidence: {evidence}")
        if tags:
            lines.append(f"  Proposed tags: {', '.join(tags)}")
    lines.append(
        "\n*No automatic apply path exists for these yet -- review and edit "
        "the contact's baseline record directly if you agree.*"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Captures (ICS)
# ---------------------------------------------------------------------------

def _render_pending_confirmations(sections: dict) -> str:
    """Pending Confirmations — everything in sections["pending_mutations"]
    still awaiting an explicit confirm/reject/review.

    Surfaces interaction_ledger.json proposals (see _compute_pending_mutations
    in daily_brief.py) that require an explicit confirm/reject. Without this,
    proposed interactions — e.g. a voice-memo mention of a contact — can sit
    unconfirmed indefinitely with no visible prompt to the user.

    confirmRelationshipInteraction was never in the Custom GPT's 30-op action
    list at all (hard platform cap). Fixed 2026-07-06 by adding a unified
    confirmProposal action (kind="relationship") that replaced the two
    single-purpose confirmIdentityMatch/rejectIdentityMatch ops, freeing a
    slot instead of costing one.

    RB defect 2026-10-08: this used to read only items[0] -- but three
    separate daily_brief.py functions append to this SAME list
    (_compute_pending_mutations, _compute_leadership_ownership_same_day,
    _compute_review_queue_backlog), and _compute_pending_mutations always
    appends exactly one item first (real finding or a clean placeholder).
    So items[1:] -- same-day executive-move/ownership candidates, and the
    strategic-assessment Finding 2 review-queue backlog across all 6
    promotion queues -- were computed correctly every single day but could
    never render, regardless of how large or stale the backlog got.
    Confirmed live: 334 real items, oldest 33 days, silently never surfaced.
    Now renders every item whose disposition isn't "ignore" (that
    convention is how each _compute_* function already marks its own
    "nothing to report" placeholder), not just the first.
    """
    items = sections.get("pending_mutations", [])
    if not items:
        return ""

    real_items = [i for i in items if i.get("disposition") != "ignore"]
    if not real_items:
        return "## Pending Confirmations\n\n*No relationship-intelligence proposals or review-queue items pending.*"

    lines = ["## Pending Confirmations\n"]
    for item in real_items:
        source_refs = item.get("source_refs") or []
        if "interaction_ledger.json" in source_refs:
            extras = item.get("extras") or {}
            pending_count = extras.get("pending_count", 0)
            pending_contacts = extras.get("pending_contacts") or []
            contacts = ", ".join(pending_contacts)
            oldest = (extras.get("oldest_created_at") or "")[:10]
            lines.append(
                f"⚠ {pending_count} relationship-intelligence proposal(s) unconfirmed for >12h: {contacts}."
                + (f" Oldest since {oldest}." if oldest else "")
            )
            # Give each pending proposal a chat-actionable hint instead of
            # the raw "call confirmProposal (kind=...)" API instruction,
            # matching the "ask RB ..." convention used for contact cards
            # and other action hints elsewhere in the brief.
            for name in pending_contacts:
                lines.append(f'- {name} · ask RB "confirm my interaction with {name}" (or "reject it")')
            continue

        # Generic fallback for every other contributor to this list
        # (leadership/ownership same-day candidates, the review-queue
        # backlog, and anything added here in the future) -- render from
        # the shared _canonical_item shape rather than a per-source format,
        # so a new contributor doesn't need its own renderer branch to
        # actually surface.
        title = (item.get("title") or "").strip()
        summary = (item.get("summary") or "").strip()
        recommended = (item.get("recommended_action") or "").strip()
        lines.append(f"⚠ {title}" if title else "⚠ Pending item")
        if summary:
            lines.append(f"  {summary}")
        if recommended:
            lines.append(f'  _{recommended}_')

    return "\n".join(lines)


def _render_captures() -> str:
    """Render pending Intelligence Capture System entries for the daily brief.

    Shows captures queued by the morning sweep that need GPT processing.
    Returns empty string if nothing is pending.
    """
    try:
        import capture_ingest
        summary = capture_ingest.pending_brief_summary()
    except Exception:
        return ""

    if not summary:
        return ""

    count = summary.get("pending_count", 0)
    items = summary.get("captures", [])
    no_transcript = summary.get("no_transcript_count", 0)

    lines = [f"## Captures — {count} Pending Processing\n"]

    for item in items[:10]:
        label = item.get("source_label", "")
        ctype = item.get("capture_type", "")
        hint = item.get("title_hint", "")
        wc = item.get("word_count", 0)
        ts = item.get("transcript_available", False)
        transcript_note = f"{wc} words" if ts else "⚠ no transcript"
        lines.append(f"- **{hint}** [{ctype}] — {label} · {transcript_note}")

    if no_transcript > 0:
        lines.append(f"\n*{no_transcript} capture(s) missing transcript — enable JPR transcription or set transcription mode in settings.json.*")

    lines.append(f"\n*Say \"RB, process my captures\" or \"RB, process my [name] meeting\" to extract intelligence.*")

    return "\n".join(lines)


# RB-2026-08-29: transcript_summarizer.py's summarize_transcript() computes a
# why_it_matters field for every LLM-processed capture (a real CoS-style
# synthesis, e.g. "A concrete handoff commitment with a deadline."), stored
# at processing_result.llm_summary.why_it_matters. render_intelligence_brief
# .py's _render_capture_intelligence() deliberately excludes it -- correctly,
# per INTELLIGENCE_BRIEF_CANONICAL.md: it's RB's own editorial judgment on a
# transcript, not a fact from the transcript, and the Intelligence Brief only
# shows what was actually said. That renderer's comment claimed the field
# "belongs in the Daily Brief" instead, but nothing here ever read it --
# confirmed live 2026-08-28 (see memory). This is that home. It deliberately
# does NOT re-render People/Companies/Topics/Decisions/Action items -- those
# already appear in the Intelligence Brief's Capture Intelligence section for
# the same captures, and repeating them here would violate the "every word
# earns its place" editorial standard. why_it_matters is the one field that's
# genuinely unique to a CoS-judgment document like this one.
CAPTURE_INSIGHTS_FRESHNESS_HOURS = 36
_CAPTURE_INSIGHTS_NO_OP_TEXT = "Casual conversation, no follow-up needed."


def _render_capture_insights() -> str:
    """Recently-processed capture insights (why_it_matters) for the Daily Brief.

    Parallel to but distinct from _render_captures(): that section drives
    action on the PENDING queue (captures not yet processed); this one
    surfaces CoS-judgment insight from captures already processed by
    transcript_summarizer.py. Same 36h freshness window and processed-dir
    scan as render_intelligence_brief.py::_render_capture_intelligence, kept
    independent (not shared/imported) to match this codebase's existing
    practice of decoupling the two brief renderers from each other.
    """
    try:
        import capture_ingest
        from datetime import timedelta as _td
        processed_dir = capture_ingest.PROCESSED_DIR
        if not processed_dir.exists():
            return ""
        recent = sorted(processed_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:20]
        items = []
        for p in recent:
            try:
                items.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
    except Exception:
        return ""

    if not items:
        return ""

    cutoff = datetime.now(timezone.utc) - _td(hours=CAPTURE_INSIGHTS_FRESHNESS_HOURS)

    def _is_fresh(item: dict) -> bool:
        ts = item.get("processed_at") or ""
        try:
            processed_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if processed_at.tzinfo is None:
                processed_at = processed_at.replace(tzinfo=timezone.utc)
            return processed_at >= cutoff
        except (ValueError, TypeError):
            return False  # unparseable timestamp — treat as stale, not fresh

    lines = []
    for item in items:
        if not _is_fresh(item):
            continue
        result = item.get("processing_result") or {}
        llm_summary = result.get("llm_summary") or {}
        why = (llm_summary.get("why_it_matters") or "").strip()
        if not why or why == _CAPTURE_INSIGHTS_NO_OP_TEXT:
            continue
        hint = item.get("title_hint") or "Untitled capture"
        lines.append(f"- **{hint}** — {why}")

    if not lines:
        return ""

    lines.insert(0, "## Recent Capture Insights\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CoS Bottom Line
# ---------------------------------------------------------------------------

def _render_executive_status() -> str:
    """Executive Status — roll-up from the EOLMS register (system/eolms/loops.json).

    Mirrors _render_captures()'s import-and-render pattern so this stays
    decoupled from the canonical-brief pipeline: renders nothing, no error,
    if EOLMS isn't loaded or the module can't be imported.

    RB-DEFECT-2026-07-09: this used to render bare counts only ("Active
    Strategic Initiatives: 4") with no way to tell what those 4 things
    actually are -- the underlying ELoop objects have real titles
    (l.title), just never surfaced here. Named lists make this section
    worth reading instead of a set of numbers nobody can act on.
    """
    try:
        import eolms  # noqa: F401 — import verifies the module is importable
        loops = core.load_eloops()
    except Exception:
        return ""

    if not loops:
        return ""

    summary = core.eloops_executive_summary(loops)
    c = summary["counts"]

    active_strategic = [l for l in loops if l.category == "strategic_initiative" and l.status == "active"]
    active_projects = [l for l in loops if l.category == "project" and l.status == "active"]
    blocked = [l for l in loops if l.status == "blocked"]
    waiting_overdue = [l for l in loops if l.status == "waiting" and l.days_since_activity > 7]

    def _named_line(label: str, count: int, named: list, max_names: int = 5) -> str:
        if not named:
            return f"- {label}: {count}"
        names = ", ".join(l.title for l in named[:max_names])
        if len(named) > max_names:
            names += f", +{len(named) - max_names} more"
        return f"- {label}: {count} — {names}"

    lines = ["## Executive Status\n"]
    lines.append(_named_line("Active Strategic Initiatives", c["active_strategic_initiatives"], active_strategic))
    lines.append(_named_line("Active Projects", c["active_projects"], active_projects))
    if c["decisions_needed"]:
        lines.append(f"- Decisions Needed: {c['decisions_needed']}")
    waiting_note = f"  ({c['waiting_items_overdue']} overdue for follow-up)" if c["waiting_items_overdue"] else ""
    waiting_line = f"- Waiting Items: {c['waiting_items']}{waiting_note}"
    if waiting_overdue:
        waiting_line += f" — overdue: {', '.join(l.title for l in waiting_overdue[:3])}"
    lines.append(waiting_line)
    if c["relationship_followups_due"]:
        lines.append(f"- Relationship Follow-ups Due: {c['relationship_followups_due']}")
    lines.append(_named_line("Blocked", c["blocked"], blocked))
    if c["pending_verification"]:
        stale_note = f"  ({c['pending_verification_stale']} awaiting >7 days)" if c["pending_verification_stale"] else ""
        lines.append(f"- Awaiting Verification: {c['pending_verification']}{stale_note}")
    if c["completed_since_yesterday"]:
        lines.append(f"- Completed Since Yesterday: {c['completed_since_yesterday']}")
    # RB-2026-08-25: "Dormant: 0" is a zero-information line -- a count of
    # zero implies no action and names nothing, unlike every other line in
    # this section (which either names real items or is already suppressed
    # when zero). Every other zero-value counter here is already
    # conditionally shown; this one wasn't. "Every word on the page has to
    # earn its place" -- a guaranteed-empty line every single day doesn't.
    if c["dormant"]:
        lines.append(f"- Dormant: {c['dormant']}")
    if c["stale_active"]:
        lines.append(f"- **Stale — no activity in 45+ days: {c['stale_active']}**")

    if summary["recommendation_candidates"]:
        lines.append("")
        lines.append(f"**CoS Recommendation:** {'; '.join(summary['recommendation_candidates'])}.")

    return "\n".join(lines)


def _top_gp_dot_connection_line(gp_dots_markdown: str) -> str:
    """Extract just the headline of the first bullet in an already-rendered
    GP/Genius Dot Connections section, for the CoS Bottom Line's fallback
    (see RB-DEFECT-2026-08-18 in _render_cos_bottom_line). Best-effort: any
    parse miss just returns "" and the caller's existing fallback chain
    continues unaffected.

    RB-2026-08-25: used to also append the bullet's full reasoning clause,
    e.g. "Stripe tips toward staying private -- Stripe is already escalated
    on your watchlist -- this headline touches a situation you're already
    in, not adjacent to it." That reasoning is already shown in full in the
    GP/Genius Dot Connections section itself, which renders earlier in the
    same document -- restating it here isn't a pointer, it's a duplicate,
    and the sentence-splitting logic that was supposed to shorten it broke
    silently whenever the reasoning was already a single sentence (the
    common, correct case), producing a run-on with a grammar error where
    the two clauses met. The caller now builds its own short "see GP/Genius
    Dot Connections above" pointer instead of quoting reasoning text here."""
    import re as _re2
    if not gp_dots_markdown:
        return ""
    m = _re2.search(r"^-\s+\*\*\[([^\]]+)\]\([^)]+\)\*\*\s*—",
                     gp_dots_markdown, _re2.MULTILINE)
    if not m:
        return ""
    return m.group(1).strip()


_ACTION_LINE_RE = re.compile(
    r"^(?P<prefix>\s*(?:\d+\.\s+|→\s+|-\s+(?:\[ACTION NEEDED\]\s+)?"
    r"(?:\*\*.*?\*\*\s+—\s+)?))"
    r"(?P<action>(?:Re-engage|Reply|Refresh|Prep(?:are)?|Open|Close|Confirm|Send)\b[^.\n]*)",
    re.IGNORECASE,
)


def _dedupe_action_tellings(markdown: str) -> str:
    """Tell each concrete action once; later sections get a short pointer.

    The CoS renderer intentionally projects the same source item into summary,
    decision, and status sections. Without a final editorial pass that became
    three full tellings of one action. Day counters are normalized so an aging
    contact is still the same unresolved action, not fresh content.
    """
    seen: set[str] = set()
    out: list[str] = []
    for line in markdown.splitlines():
        match = _ACTION_LINE_RE.search(line)
        if not match:
            out.append(line)
            continue
        phrase = match.group("action").strip()
        key = re.sub(r"\d+", "#", phrase.lower())
        key = re.sub(r"[^a-z#]+", " ", key).strip()
        if key not in seen:
            seen.add(key)
            out.append(line)
            continue
        # A duplicate arrow belongs to the Decision block immediately above;
        # drop that whole redundant block. Duplicate bullets/numbered actions
        # can simply disappear because the first telling remains visible.
        if line.lstrip().startswith("→"):
            while out and out[-1].strip():
                out.pop()
        # no append: the duplicate action has already been told
    result = "\n".join(out)
    # Remove a section that became empty after all of its projected actions
    # were already covered by the executive top-three summary.
    result = re.sub(r"\n## Top Decisions Today\n\s*(?=---\n)", "\n", result)
    return result


def _signal_line_sentence(signal_line: str, signal_is_gp_pointer: bool, *, carrying_in: bool = False) -> str:
    """One sentence naming today's top signal in the CoS Bottom Line.

    RB-2026-08-25: when the signal came from GP/Genius Dot Connections
    (signal_is_gp_pointer), that section already rendered its full
    reasoning earlier in the same document -- this must be a pointer back
    to it, not a second telling. When the signal came from
    strategic_industry_signals (a source with no other rendered section in
    this document), stating it in full here is the only place it appears,
    so the original "worth tracking" framing still applies."""
    if signal_is_gp_pointer:
        return f"**{signal_line}** — see GP/Genius Dot Connections above."
    if carrying_in:
        return f"**{signal_line}** is today's signal worth carrying in — monitor for territory implications."
    return f"**{signal_line}** is the signal worth tracking today."


def _render_cos_bottom_line(sections: dict, target_date: date, gp_dots_markdown: str = "") -> str:
    """CoS Bottom Line — judgment paragraph, not a template.

    Varies voice by day of week, GP proximity, market context, and queue state.
    Reads the same signals as the rest of the brief and synthesizes a takeaway.

    `gp_dots_markdown` (RB-DEFECT-2026-08-18): confirmed live -- a day with a
    $7B Stripe/OpenRouter acquisition AND Stripe-PayPal buyout talks, both
    flagged elsewhere in the same document as directly relevant to an
    escalated watchlist entity, produced a Bottom Line that named neither --
    signal_line below only ever read strategic_industry_signals, never the
    (often richer, always territory-specific) GP/Genius Dot Connections
    pool. Passed in already-rendered so this function doesn't recompute
    Dot Connections' own scan/synthesis pass -- only used as a fallback
    when strategic_industry_signals has nothing fresh to say.
    """
    import re as _re

    is_monday = target_date.weekday() == 0
    is_friday = target_date.weekday() == 4
    gp_start = date(2026, 7, 7)
    days_to_gp = (gp_start - target_date).days
    weekly_plan = _load_weekly_plan()
    mkt = _get_market_context(target_date)
    comm = _get_comm_context(sections)

    _BOTTOM_NOISE = [
        "week's plan is drafted", "weekly plan is drafted", "plan is drafted — awaiting",
        "interaction_newer_than", "last_touch_update",
        "action orchestration prompt", "meeting prep queue", "end-of-day closeout",
        "escalate_priority", "escalate priority", "open_loop:", "close_loop:",
        "loops rb can auto", "auto-close", "create tracked loops", "run end-of-day",
        "strategic operator proximity",
        # RB-2026-07-20: same fix as _render_decision_queue's _NOISE_PHRASES --
        # generic titles ("One decision needed") hid the actual housekeeping
        # content in the detail text, which this filter never checked.
        "one decision needed", "strategic_operators.yaml", "operator relationship proximity",
        "reconciliation prompt", "chatgpt briefing destination", "pain mapping under-instrumented",
        "confirm state mutation via ingestexecutivedeclaration", "enable linkedin feed monitoring",
    ]
    def _is_bottom_noise(item: dict) -> bool:
        t = " ".join([
            item.get("title") or "",
            item.get("why_it_matters") or "",
            item.get("summary") or "",
            item.get("recommended_action") or "",
        ]).lower()
        return any(p in t for p in _BOTTOM_NOISE)

    dq = sections.get("decision_queue", [])
    decisions = [i for i in dq
                 if (i.get("extras") or {}).get("requires_decision") and not _is_bottom_noise(i)]
    other_dq = [i for i in dq if not _is_bottom_noise(i)]

    def _bottom_line_action_text(item: dict | None) -> str:
        """Prefer a substantive action description over a bare label title --
        'One decision needed' (from reconciliation_prompts) carries no
        content on its own; the actual question lives in recommended_action.
        Only substitutes for short/generic titles -- longer ones (e.g.
        'Possible closure: L-... -- Jeff Wayman') are already specific."""
        if not item:
            return ""
        title = (item.get("title") or "").strip()
        detail = (item.get("recommended_action") or item.get("why_it_matters") or "").strip()
        if detail and len(title) < 40 and detail.lower() not in title.lower():
            truncated = _truncate_clean(detail, 150)
            # callers append their own sentence-closing period -- but not if
            # _truncate_clean already added "…" (a period right after would
            # read as "…." rather than a clean truncation marker).
            return truncated if truncated.endswith("…") else truncated.rstrip(".")
        return title

    top_action = _bottom_line_action_text(decisions[0] if decisions else
                                           (other_dq[0] if other_dq else None))

    # RB-DEFECT-2026-07-27: the Intelligence Brief's own Section I already
    # reports this exact signal verbatim, including explicitly saying "no
    # new evidence" for a stale one -- citing it here too, unconditionally,
    # as "today's signal worth tracking" duplicated that stale claim with
    # zero new information. Only surface it here when it's actually fresh;
    # a stale signal has nothing to add beyond what Section I already said.
    #
    # RB-DEFECT-2026-08-17: confirmed live -- "Operational AI implementation
    # risk in restaurants" opened the Bottom Line verbatim for 3+ straight
    # days, including the exact day Section I formally retired that signal
    # ("Retired (stale 5+ days, no longer tracked)"). Root cause: this used
    # extras.is_actually_stale, computed upstream in daily_brief.py with a
    # >7-day threshold, while Section I's own retirement rule
    # (render_intelligence_brief.py's STALE_RETIREMENT_DAYS) is 5 days. A
    # signal aged 6-7 days was retired in Part 1 but still "fresh enough" by
    # Part 2's separate, looser check -- two uncoordinated thresholds for
    # what's supposed to be the same fact. Recompute from the same
    # days_since_evidence field Part 1 reads from, using Part 1's 5-day
    # threshold directly, so the two reports can never disagree about
    # whether a given signal is still being tracked.
    _SIGNAL_STALE_DAYS = 5  # must match render_intelligence_brief.py's STALE_RETIREMENT_DAYS
    signals = sections.get("strategic_industry_signals", [])
    signal_line = ""
    if signals:
        _days_since = (signals[0].get("extras") or {}).get("days_since_evidence")
        _signal_is_stale = bool((signals[0].get("extras") or {}).get("is_actually_stale")) or (
            _days_since is not None and _days_since >= _SIGNAL_STALE_DAYS
        )
        if not _signal_is_stale:
            signal_line = (signals[0].get("title") or "").strip()
            signal_line = signal_line.replace("Multiple-source convergence: ", "").strip()
    # RB-2026-08-25: track whether signal_line is a GP/Genius Dot Connections
    # headline -- that section's full reasoning is already shown in full
    # earlier in this same document, so referencing it here must be a
    # pointer ("see ... above"), never a restatement of the reasoning.
    signal_is_gp_pointer = False
    if not signal_line:
        # strategic_industry_signals had nothing fresh -- try today's actual
        # top Dot Connections item before falling all the way through to a
        # signal-free Bottom Line.
        signal_line = _top_gp_dot_connection_line(gp_dots_markdown)
        signal_is_gp_pointer = bool(signal_line)

    horizon = sections.get("horizon_watch", [])
    horizon_note = ""
    # The horizon_watch computer emits a negative-confirmation sentinel item
    # ("No developments in the 30-90 day horizon detected", disposition
    # "ignore") when there's nothing to show -- that's correct as the
    # section's own placeholder line, but naively taking horizon[0] here
    # injected it into the Bottom Line as if it were a real signal, producing
    # a tautological "Horizon: No developments in the 30-90 day horizon
    # detected." Skip ignore-disposition/sentinel items when synthesizing.
    real_horizon = [h for h in horizon if (h.get("disposition") or "") != "ignore"]
    if real_horizon:
        h_title = _re.sub(r"\s*—\s*\d+ DAYS\s*$", "", (real_horizon[0].get("title") or "").strip()).strip()
        h_extras = real_horizon[0].get("extras") or {}
        h_date = h_extras.get("horizon_date") or ""
        if h_title:
            horizon_note = h_title + (f" ({h_date})" if h_date else "")

    # ---- Compose closing paragraph ----
    sentences: list[str] = []

    # --- GP final day / GP start day ---
    # Both of these used to be a single hardcoded sentence with zero named
    # signals or context ("Tomorrow is Day 1. Nothing on today's list should
    # be harder than getting a good night's sleep.") — a canonical-contract
    # failure by the doc's own explicit anti-test: "If the CoS Bottom Line
    # could appear in any executive's briefing without changing a word, the
    # brief has failed." That sentence could open literally any new job.
    # Build the same named-signal sentences every other branch does, just
    # framed for the milestone day.
    if days_to_gp in (0, 1):
        milestone = "Today is Day 1 at Global Payments." if days_to_gp == 0 else "Tomorrow is Day 1 at Global Payments."
        sentences.append(f"**{milestone}**")
        if mkt["sector_rally"]:
            sentences.append(
                f"Sector rally today ({mkt['rally_count']} names up) — competitors enter your "
                f"first week with institutional momentum behind them; know their positioning."
            )
        elif signal_line:
            sentences.append(_signal_line_sentence(signal_line, signal_is_gp_pointer, carrying_in=True))
        if top_action:
            sentences.append(f"Before anything else: {top_action}.")
        elif comm["loops_overdue"] > 0:
            sentences.append(f"{comm['loops_overdue']} loops remain overdue — close what you can before they follow you into week one.")
        if horizon_note:
            sentences.append(f"Horizon: {horizon_note}.")
        if len(sentences) == 1:
            # No real signal/action/horizon data available at all — still
            # milestone-specific, just without the padding claim about sleep.
            sentences.append("Confirm start logistics and walk in ready.")
        return f"## CoS Bottom Line\n\n{'  '.join(sentences)}"

    # --- Monday with GP countdown ---
    if is_monday and 0 < days_to_gp <= 8 and weekly_plan:
        outcomes = sorted(
            weekly_plan.get("outcomes") or [],
            key=lambda o: o.get("portfolio_allocation_pct", 0), reverse=True
        )
        top_titles = [o.get("title", "") for o in outcomes[:2] if o.get("title")]
        ff = (weekly_plan.get("forcing_function") or {}).get("description", "")

        sentences.append(
            f"**The week's job is singular: arrive at GP ready.** "
            f"Day 1 is {days_to_gp} days out."
        )
        if top_titles:
            sentences.append(
                f"Everything else yields to: {' and '.join(f'**{t}**' for t in top_titles)}."
            )
        if comm["loops_overdue"] >= 10:
            sentences.append(
                f"The {comm['loops_overdue']} overdue loops are the drag — clear them before Friday "
                f"or they follow you into week one."
            )
        if mkt["sector_rally"]:
            sentences.append(
                f"The sector rally today ({mkt['rally_count']} names up) means competitors enter your "
                f"first week with institutional momentum behind them. Know their positioning."
            )
        if ff:
            sentences.append(f"Hard stop: {ff}.")
        return f"## CoS Bottom Line\n\n{'  '.join(sentences)}"

    # --- Friday with GP countdown ---
    if is_friday and 0 < days_to_gp <= 8:
        sentences.append(
            f"**End-of-week checkpoint: what didn't get done this week carries into GP onboarding.** "
            f"{days_to_gp} days left."
        )
        if comm["loops_overdue"] > 0:
            sentences.append(f"Overdue loops: {comm['loops_overdue']}. Close or explicitly defer before EOD.")
        if horizon_note:
            sentences.append(f"Next horizon: {horizon_note}.")
        return f"## CoS Bottom Line\n\n{'  '.join(sentences)}"

    # --- Mid-week with GP countdown ---
    if 0 < days_to_gp <= 8:
        sentences.append(
            f"**{days_to_gp} days to GP — mid-week status.**"
        )
        if top_action:
            sentences.append(f"Move the needle today: {top_action}.")
        if mkt["sector_rally"]:
            sentences.append(
                f"Sector rally ({mkt['rally_count']} names up today) — study the positioning before Day 1."
            )
        elif signal_line:
            sentences.append(_signal_line_sentence(signal_line, signal_is_gp_pointer))
        if horizon_note:
            sentences.append(f"Watch: {horizon_note}.")
        return f"## CoS Bottom Line\n\n{'  '.join(sentences)}"

    # --- Standard (no GP countdown) ---
    if mkt["sector_rally"]:
        sentences.append(
            f"Sector-wide rally today ({mkt['rally_count']} names up simultaneously) — "
            f"institutional movement, not individual company news. Watch for deal announcements."
        )
    elif signal_line:
        sentences.append(_signal_line_sentence(signal_line, signal_is_gp_pointer))

    if top_action:
        sentences.append(f"Move the needle: {top_action}.")
    elif comm["loops_overdue"] >= 5:
        sentences.append(f"{comm['loops_overdue']} loops overdue — today is a clearance day.")

    if horizon_note:
        sentences.append(f"Horizon: {horizon_note}.")

    closing = "  ".join(sentences) if sentences else "Review today's decisions and calendar before 9am."
    return f"## CoS Bottom Line\n\n{closing}"


# ---------------------------------------------------------------------------
# GP/Genius Dot Connections
# ---------------------------------------------------------------------------

_GP_OWN_TERMS = [
    "global payments", "genius", "worldpay", "heartland", "evo payments",
]
_GP_COMPETITIVE_TERMS = list(core.GP_COMPETITOR_COMPANIES)
_GP_MARKET_TERMS = [
    "enterprise restaurant", "enterprise pos", "payment platform", "payment processing",
    "pos replacement", "pos migration", "tech stack", "unified commerce",
    "payment rails", "acquiring", "payment modernization", "qsr technology",
]

# A title can score >=2 purely from one _GP_COMPETITIVE_TERMS hit (e.g. "Stripe") even when
# the article is unrelated to payments/restaurant-tech competition — e.g. a payments-industry
# newsletter running a "Stripe backs a public-health research effort" item. These terms flag
# that case: if present alongside a *competitive*-term-only match (not an own-term match —
# GP/Genius news is relevant regardless of topic), skip inclusion rather than force a generic
# "competitive signal" template onto off-topic content.
_GP_OFF_TOPIC_TERMS = [
    "respiratory infection", "infectious disease", "public health", "vaccine",
    "clinical trial", "cancer research", "medical research", "biotech", "pharmaceutical",
    "french toast", "new menu", "limited-time menu", "menu item", "new flavor",
]


def _is_off_topic(title: str) -> bool:
    tl = title.lower()
    return any(t in tl for t in _GP_OFF_TOPIC_TERMS)

# Dot-connection templates: given a signal, what's the so-what for Todd's territory
_DOT_TEMPLATES: dict[str, str] = {
    "acquisition":     "M&A activity — new ownership typically triggers a POS/payments platform review within 12–18 months. Potential enterprise opportunity.",
    "price_move":      "Unusual price move without confirmed news — possible undisclosed deal, exec change, or strategic announcement incoming. Watch for enterprise customer disruption.",
    "volume_spike":    "Elevated trading volume alongside price move — institutional positioning. May signal a deal announcement that reshapes the competitive landscape.",
    "52w_low":         "Near 52-week low — operator or competitor under financial pressure. Customers may seek stable platform alternatives.",
    "52w_high":        "Near 52-week high — momentum or pre-announcement positioning. Could signal a major contract win or acquisition that affects your territory.",
    "bankruptcy":      "Bankruptcy or closure signal — operators at risk of platform disruption. Outreach window for displaced accounts.",
    "executive_hire":  "New executive — leadership changes often drive platform re-evaluation within 6 months. Warm intro window.",
    "deployment":      "Deployment or platform expansion — confirms active competitive motion in enterprise accounts.",
    "restructuring":   "Restructuring — may displace customer relationships or create budget availability for platform consolidation.",
    "judicial":        "Legal ruling against a competitor — signals financial exposure and potential enterprise account instability. Natural opening to position Genius/Worldpay as the stable alternative.",
    "ruling":          "Legal ruling against a competitor — signals financial exposure and potential enterprise account instability. Natural opening to position Genius/Worldpay as the stable alternative.",
    "lawsuit":         "Litigation signal — distracted leadership and legal costs create platform re-evaluation windows. Monitor affected accounts for displacement opportunity.",
}

# Title keyword → dot template key (for articles without a structured signal_type)
_DOT_TITLE_KEYWORDS: list[tuple[str, str]] = [
    ("rules against", "judicial"),
    ("ruling against", "judicial"),
    ("court rules", "judicial"),
    ("judge orders", "judicial"),
    ("lawsuit", "lawsuit"),
    ("sued", "lawsuit"),
    ("acqui", "acquisition"),
    ("bankrupt", "bankruptcy"),
    ("restructur", "restructuring"),
    ("hires", "executive_hire"),
    ("appoints", "executive_hire"),
    ("names new", "executive_hire"),
    ("deploys", "deployment"),
    ("expands to", "deployment"),
    ("rolls out", "deployment"),
]


# "Square" (Block's payments product) is also a common place-name word — strip
# known "___ Square" place names before matching so they don't false-positive
# the competitive-term scan (same failure mode as "toast"/"ncr" substring hits,
# just at the whole-word level instead of inside another word).
_GP_PLACE_NAME_FALSE_POSITIVES = [
    "logan square", "union square", "times square", "madison square",
    "washington square", "town square", "public square", "market square",
    "red square", "trafalgar square", "tiananmen square", "herald square",
]

# RB-DEFECT-2026-08-14: watchlist/opportunity entity names that are also
# ordinary English words (Genius, and any future addition to that watchlist
# with the same problem) need their common idiomatic uses stripped before
# role-note matching, the same way place names are stripped above -- a word-
# boundary match alone still catches "Genius" the whole word inside "stroke
# of genius," since that IS the company's exact name as a standalone word.
_GP_DOT_IDIOM_FALSE_POSITIVES = [
    "stroke of genius", "sheer genius", "pure genius", "comic genius",
    "genius move", "genius idea", "genius level", "evil genius",
    "marketing genius", "criminal genius",
]


def _gp_dot_term_match(term: str, text: str) -> bool:
    """Word-boundary match — plain `in` lets short competitor names like "ncr",
    "square", or "toast" match inside unrelated words ("Increase", "Logan
    Square", "Toaster"), producing false-positive GP/Genius relevance hits."""
    import re as _re4
    return _re4.search(r"\b" + _re4.escape(term) + r"\b", text) is not None


def _gp_dot_score(text: str) -> int:
    tl = text.lower()
    for phrase in _GP_PLACE_NAME_FALSE_POSITIVES:
        tl = tl.replace(phrase, "")
    # RB-DEFECT-2026-08-14: "genius" is a deliberate _GP_OWN_TERMS entry
    # (Todd's own product, correctly the highest-priority match) -- but that
    # means an idiomatic "stroke of genius" scored a full 3 points before
    # this line existed, clearing the relevance gate on a Domino's menu-item
    # story that has nothing to do with Genius/Worldpay. Stripping the same
    # idiom list _gp_dot_role_note uses keeps the item out of Dot
    # Connections entirely instead of admitting it with a generic fallback.
    for phrase in _GP_DOT_IDIOM_FALSE_POSITIVES:
        tl = tl.replace(phrase, "")
    score = 0
    for t in _GP_OWN_TERMS:
        if _gp_dot_term_match(t, tl):
            score += 3
    for t in _GP_COMPETITIVE_TERMS:
        if _gp_dot_term_match(t, tl):
            score += 2
    for t in _GP_MARKET_TERMS:
        if _gp_dot_term_match(t, tl):
            score += 1
    return score


def _gp_dot_role_context(sections: dict) -> dict:
    """Company/entity names Todd is actively tracking, so a headline with no
    structured signal_type falls back to something more specific than
    'assess customer impact' when it actually touches his active pipeline
    or an already-escalated watchlist entity. Mirrors daily_brief.py's
    _ctd_role_context (same data, same reasoning) but built locally since
    render_daily_brief.py doesn't import the daily_brief module."""
    opp_names: dict[str, str] = {}
    for opp in (sections.get("opportunity_board") or []):
        extras = opp.get("extras") or {}
        companies = extras.get("companies") or ""
        for c in companies.split(";") if companies else []:
            c = c.strip().strip(",")
            if c:
                opp_names[c.lower()] = c
    watch_names: dict[str, str] = {}
    for w in (sections.get("watchlist_intelligence") or []):
        extras = w.get("extras") or {}
        name = extras.get("entity_name") or ""
        status = extras.get("watchlist_status") or ""
        if name and status in ("Escalation", "New Activity"):
            watch_names[name.lower()] = name
    return {"opportunities": opp_names, "watchlist": watch_names}


def _gp_dot_role_note(text: str, role_context: dict | None) -> str:
    """Return a Todd-specific clause if `text` (a headline/title) mentions
    an entity from his active opportunity pipeline or escalated watchlist.

    RB-DEFECT-2026-08-14: confirmed live -- "Solo Dining: Why Domino's New
    Menu Item Might Be a Stroke of Genius" got tagged "Genius is already
    escalated on your watchlist," treating the idiom "stroke of genius" as a
    reference to the watchlist company Genius. Two separate bugs stacked:
    (1) this used a plain substring `in` check where the rest of this file
    (_gp_dot_term_match, used by _gp_dot_score above) already established
    word-boundary matching specifically because short/common names ("ncr",
    "square", "toast") false-positive inside unrelated words -- this
    function never adopted that fix, so a name like "Qu" would similarly
    false-positive inside "quarter"/"acquire"/"equity". (2) even a word-
    boundary match doesn't help when the watchlist name IS an ordinary
    English word used idiomatically (Genius, and potentially others) --
    that needs the same idiom-stripping pattern _GP_PLACE_NAME_FALSE_
    POSITIVES already established for place names like "Union Square"
    false-positiving on "Square" the company.
    """
    role_context = role_context or {}
    tl = (text or "").lower()
    if not tl:
        return ""
    for phrase in _GP_DOT_IDIOM_FALSE_POSITIVES:
        tl = tl.replace(phrase, "")
    for key, disp in (role_context.get("opportunities") or {}).items():
        if key and _gp_dot_term_match(key, tl):
            return f"{disp} is one of your active opportunities"
    for key, disp in (role_context.get("watchlist") or {}).items():
        if key and _gp_dot_term_match(key, tl):
            return f"{disp} is already escalated on your watchlist"
    return ""


GP_DOT_GENERIC_FALLBACK = "Competitive or market signal relevant to your Genius/Worldpay territory — assess customer impact."


def _infer_dot(sig_type: str, title: str, role_context: dict | None = None) -> str:
    """Map a signal type or article title to the most specific dot-connection template."""
    st_key = sig_type.lower().replace("[", "").replace("]", "").replace(" ", "_").strip()
    role_note = _gp_dot_role_note(title, role_context)
    # An upstream acquisition badge is not evidence by itself. Confirm the
    # headline actually describes a transaction before attaching the M&A /
    # new-owner playbook. This blocks false positives such as Square
    # "doubling down" on an ISO channel being promoted as an acquisition.
    if st_key == "acquisition":
        bare_title = __import__("re").sub(r"^\[[^\]]+\]\s*", "", title).lower()
        acquisition_terms = (
            "acqui", "merger", "merge with", "buys ", "buyout", "to buy",
            "purchase of", "deal to buy", "takeover", "to acquire",
        )
        if not any(term in bare_title for term in acquisition_terms):
            st_key = ""
    # Check structured signal type first
    if st_key and st_key in _DOT_TEMPLATES:
        base = _DOT_TEMPLATES[st_key]
        return f"{base} {role_note} — direct territory relevance." if role_note else base
    # Fall through to title keyword matching
    title_lower = __import__("re").sub(r"^\[[^\]]+\]\s*", "", title).lower()
    for keyword, template_key in _DOT_TITLE_KEYWORDS:
        if keyword in title_lower:
            base = _DOT_TEMPLATES[template_key]
            return f"{base} {role_note} — direct territory relevance." if role_note else base
    # Generic fallback only if nothing matched — still name the specific
    # connection when one exists, rather than a boilerplate "assess impact"
    # line that could be attached to any headline about any company.
    if role_note:
        return f"{role_note} — this headline touches a situation you're already in, not adjacent to it."
    return GP_DOT_GENERIC_FALLBACK


def _technology_radar_entity_name(item: dict) -> str:
    extras = item.get("extras") or {}
    name = extras.get("entity_name")
    if name:
        return name
    return (item.get("title") or "").split("—")[0].strip()


def _render_notable_contact_moves(sections: dict) -> str:
    """Notable Contact Moves — exec-hire headlines cross-referenced against
    the baseline. A CTO or other high-value contact changing roles is a
    relationship-intelligence event RB should surface prominently to the
    CoS, with what's already known about that person, not leave buried as
    generic industry news in the Intelligence Brief."""
    items = sections.get("notable_contact_moves") or []
    if not items:
        return ""
    lines = ["## Notable Contact Moves\n"]
    for item in items:
        extras = item.get("extras") or {}
        person = extras.get("person") or ""
        company = extras.get("new_company") or ""
        role = extras.get("new_role") or ""
        cto_flag = " 🎯" if extras.get("is_cto") else ""
        lines.append(f"**{person} → {company} ({role})**{cto_flag}")
        why = (item.get("why_it_matters") or "").strip()
        if why:
            lines.append(why)
        action = (item.get("recommended_action") or "").strip()
        if action:
            lines.append(f"→ {action}")
        # Only offer the card hint when there's an actual baseline match to
        # show — no point sending the user to look up a card that doesn't exist.
        if extras.get("baseline_id"):
            lines.append(_contact_card_hint(person).strip(" ·"))
        lines.append("")
    return "\n".join(lines).rstrip()


_REACTIVATION_MATCH_LABELS = {
    "priority_account": "Priority account",
    "ecosystem_vendor": "Tracked vendor/competitor",
    "ecosystem_brand": "Tracked brand",
}
_REACTIVATION_DISPLAY_LIMIT = 10


def _render_relationship_reactivation_candidates(scan_result: dict | None) -> str:
    """Relationship Reactivation Candidates — RB-2026-09-11, item 3 of the
    M&A/exec-moves/relationship-intel scoping (the other two became
    ownership_promotion.py and executive_move_promotion.py). Runs
    relationship_reactivation_scan.py's already-computed result (a
    scheduled morning_pipeline.py step, not computed here) — untiered
    baseline contacts (3,095 of 3,115 total, not served by Inner Circle/
    Referral Network) whose current_company now matches a tracked
    customers_prospects account or ecosystem_intelligence.json vendor/
    brand. A currently-dormant relationship that just became newly
    relevant, surfaced from data already on file — no new research.

    Same repeat-noise discipline as I+: Entity Signal Convergence: a new
    or changed match gets full detail; a persisting one is only counted.
    Placed in the relationship-intelligence cluster next to Notable
    Contact Moves — both are "someone RB already knows just became
    relevant again," from opposite directions (a news headline vs. data
    already on file)."""
    if not scan_result:
        return ""

    new_findings = scan_result.get("new_findings") or []
    persisting = scan_result.get("persisting_findings") or []
    if not new_findings and not persisting:
        return ""

    lines = ["## Relationship Reactivation Candidates\n"]
    shown = new_findings[:_REACTIVATION_DISPLAY_LIMIT]
    # A first-ever inventory match is not evidence that a relationship just
    # became relevant. Require an actual change timestamp from the scanner;
    # until it exists, do not turn baseline inventory into an outreach queue.
    shown = [f for f in shown if f.get("changed_at")]
    if not shown:
        return ""
    for f in shown:
        label = _REACTIVATION_MATCH_LABELS.get(f["match_type"], f["match_type"])
        role = f" — {f['current_role']}" if f.get("current_role") else ""
        lines.append(f"**{f['name']}** ({f['current_company']}{role})")
        lines.append(f"  {label}: {f['matched_name']}")
        lines.append(_contact_card_hint(f["name"]).strip(" ·"))
        lines.append("")
    remaining = len(new_findings) - len(shown)
    if remaining > 0:
        lines.append(f"*+{remaining} more new reactivation candidate(s) this cycle.*\n")
    # Persisting matches are scanner state, not a user review queue.
    return "\n".join(lines).rstrip()


def _truncate_on_word(text: str, limit: int) -> str:
    """Truncate `text` to `limit` chars without cutting mid-word."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_space = cut.rfind(" ")
    if last_space > limit * 0.6:
        cut = cut[:last_space]
    return cut.rstrip(" .,;:") + "…"


def _render_technology_radar(sections: dict, target_date: date) -> str:
    """Section 3: Technology Radar — escalations only.

    RB-DEFECT-2026-07-27: assessed for overlap with the Intelligence Brief
    (which renders first in the same daily pipeline) -- New Activity items
    and the quiet-entity rollup here were both fully duplicated by the
    Intelligence Brief's own Section F (Watchlist), including the exact
    "N entities scanned — no material developments" noise Todd explicitly
    asked to remove from Section F. Narrowed to escalations only: the one
    tier genuinely worth a second, CoS-voiced mention (near-term action
    required) rather than a straight rerun of content already sent.

    RB-DEFECT-2026-08-12: that narrowing didn't fix the deeper issue --
    an entity stuck in Escalation for multiple days (e.g. PAR/Olo/Fiserv,
    escalated once by a "first earnings event on record" rule with no
    mechanism to ever downgrade) got the identical multi-hundred-word
    press-release paragraph re-rendered here every single morning, on top
    of the same paragraph already repeating in the Intelligence Brief's own
    Section F. daily_brief.py already tags status_changed=True only when an
    entity's status actually differs from yesterday's published brief --
    use that to show full detail on a fresh escalation and a short,
    explicitly-labeled repeat on a persisting one.
    """
    wl_items = sections.get("watchlist_intelligence", [])
    escalated = [i for i in wl_items if (i.get("extras") or {}).get("watchlist_status") == "Escalation"]
    if not escalated:
        return ""

    # RB-DEFECT-2026-08-17: confirmed live -- this rendered as 17 nearly-
    # identical blocks (McDonald's/PAR Technology/PAR Loyalty/Global
    # Payments/Harri/STRATACACHE are all the SAME loop, L-2026-07-23-003),
    # each getting "Implication: Follow up immediately" -- the same six
    # words 15 times over. That's not synthesis, it's the Intelligence
    # Brief's own Section F re-rendered with a template bolted on, which is
    # exactly what this section's job is NOT supposed to be (Part 2 opens by
    # re-rendering raw Part 1 intelligence = a canonical-contract failure).
    # Group by the shared underlying loop -- same helper Section F uses --
    # so this section can say something Part 1 didn't: how many tracked
    # accounts one overdue loop is blocking, in one line instead of six.
    # RB-2026-08-25: confirmed live -- on a day where every single escalation
    # is "unchanged since prior brief" (the common case: a loop stays open
    # for days until someone closes it), this rendered as 7 full 3-4 line
    # blocks -- 21+ lines restating "still open, nothing new" seven
    # different ways. Todd: "every word on the page has to earn its place."
    # A group that's unchanged since yesterday earns one line, not a block;
    # full-block treatment is reserved for what's actually new (a status
    # that just changed, i.e. group["status_changed"]).
    groups = core.group_watchlist_escalations(escalated)
    fresh_groups = [g for g in groups if g["status_changed"]]
    stale_groups = [g for g in groups if not g["status_changed"]]

    lines = ["## Technology Radar\n"]
    for group in fresh_groups:
        entities = group["entities"]
        item = group["items"][0]
        why = _clean_why(group["why"])
        action = (item.get("recommended_action") or "").strip()
        if group["loop_id"] and len(entities) > 1:
            lines.append(f"**[ESCALATED] {len(entities)} entities — same overdue loop**")
            lines.append(f"Touches: {', '.join(entities)}")
            if why:
                lines.append(f"Why it matters: {why}")
            lines.append(
                f"Implication: One overdue loop ({group['loop_id']}) is blocking progress on "
                f"{len(entities)} tracked accounts at once -- closing it clears all {len(entities)}, "
                f"not just one."
            )
        elif not group["loop_id"] and len(entities) > 1:
            # RB-2026-09-03: confirmed live -- "Salad and Go" and "Pizza Hut"
            # both escalated off the exact same Nation's Restaurant News
            # article; core.group_watchlist_escalations now groups these by
            # shared source_url the same way it already groups by loop_id,
            # so this renders once, not as an identical block per entity
            # (or, before this branch existed, only the first entity at all).
            lines.append(f"**[ESCALATED] {len(entities)} entities — same source article**")
            lines.append(f"Touches: {', '.join(entities)}")
            if why:
                lines.append(f"Why it matters: {why}")
            lines.append(
                f"Implication: One story is relevant to {len(entities)} tracked accounts at once -- "
                f"see GP/Genius Dot Connections below for territory implications."
            )
        else:
            company = entities[0]
            lines.append(f"**[ESCALATED] {company}**")
            if why:
                lines.append(f"Why it matters: {why}")
            # RB-DEFECT-2026-08-18: confirmed live -- Stripe's $7B OpenRouter
            # acquisition got "Implication: Follow up immediately," the same
            # blanket default daily_brief.py sets for every bare-status
            # "Escalation" watchlist item regardless of what actually
            # happened. That's a real news story with real evidence already
            # sitting in the Why-it-matters line above, and GP/Genius Dot
            # Connections (later in this same document) already builds the
            # actual territory-specific synthesis for it -- duplicating that
            # analysis here isn't worth the cost, but repeating a content-
            # free command right above a real story is worse. Point at the
            # real analysis instead of restating "follow up."
            if action.strip().lower() == "follow up immediately." and why:
                lines.append(
                    f"Implication: New market activity for {company} -- "
                    f"see GP/Genius Dot Connections below for territory implications."
                )
            else:
                lines.append(f"Implication: {action or 'Escalated status warrants near-term attention.'}")
        lines.append("")

    if stale_groups:
        stale_bits = []
        for group in stale_groups:
            entities = group["entities"]
            label = entities[0] if len(entities) == 1 else f"{entities[0]} +{len(entities) - 1} more"
            stale_bits.append(label)
        plural = "escalations" if len(stale_groups) != 1 else "escalation"
        lines.append(f"**{len(stale_groups)} other {plural} unchanged since yesterday:** " + ", ".join(stale_bits))
        lines.append("")

    return "\n".join(lines)


_CONNECT_THE_DOTS_MAX = 8
_CONNECT_THE_DOTS_CONFIDENCE_RANK = {"high": 2, "medium": 1, "low": 0}


def _connect_the_dots_dedup_key(item: dict) -> str:
    """Stable story identity for an item, independent of the day's changing
    signal count / date-range suffix (e.g. "44 signals ... (07-13 -> 07-14)"
    becomes a different string every day for the same underlying pattern)."""
    import re as _re
    extras = item.get("extras") or {}
    ctype = extras.get("convergence_type") or ""
    if ctype == "db_multi_source" and extras.get("entity"):
        return f"multi_source:{extras['entity'].strip().lower()}"
    if ctype == "db_entity_pair" and extras.get("entity_a") and extras.get("entity_b"):
        pair = sorted([extras["entity_a"].strip().lower(), extras["entity_b"].strip().lower()])
        return f"entity_pair:{pair[0]}|{pair[1]}"
    if ctype == "relationship_activation" and extras.get("contact"):
        return f"relationship_activation:{extras['contact']}"
    # Fallback: normalized title with counts/dates/parentheticals stripped.
    title = (item.get("title") or "").strip().lower()
    title = _re.sub(r"\(.*?\)", "", title)
    title = _re.sub(r"\d+", "", title)
    return _re.sub(r"\s+", " ", title).strip()


def _render_connect_the_dots(sections: dict, target_date: date,
                              rendered: dict | None = None) -> str:
    """RB-DEFECT-2026-07-09: daily_brief.py already computes connect_the_dots
    -- relationship-activation windows, cross-company convergence
    validation, decision-relevant new evidence, cross-time entity tracking,
    entity co-occurrence, and industry+opportunity/relationship linkage --
    but neither render script ever displayed it. This is real, already-
    built dot-connecting synthesis (not a news re-list), which is what was
    actually missing from "GP/Genius: Dot Connections" -- that section only
    ever covers the payments/Genius territory angle; this covers the
    broader relationship + market intelligence picture.

    `rendered` -- persisted {dedup_key: {"first_rendered": "YYYY-MM-DD"}}
    registry (CONNECT_THE_DOTS_RENDERED_PATH), mutated in place by the
    caller. Same cross-day dedup pattern as GP/Genius Dot Connections
    (RB-DEFECT-2026-07-14) -- without it, the same convergence signal (same
    entity/pair, just a bigger count and a wider date range each day) reads
    as new evidence every morning when nothing has actually changed."""
    import re as _re
    if rendered is None:
        rendered = {}
    items = sections.get("connect_the_dots") or []
    if not items:
        return ""

    def _rank(item: dict) -> tuple:
        act_today = 1 if item.get("disposition") == "act_today" else 0
        confidence = _CONNECT_THE_DOTS_CONFIDENCE_RANK.get(item.get("confidence"), 0)
        importance = {"high": 2, "medium": 1, "low": 0}.get((item.get("extras") or {}).get("importance"), 1)
        return (act_today, confidence, importance)

    ranked = sorted(items, key=_rank, reverse=True)

    today_str = target_date.isoformat()
    lines = ["## Connect the Dots — Strategic Connections\n",
             "*What today's intelligence means for your relationships, opportunities, and market picture.*\n"]
    shown = 0
    for item in ranked:
        if shown >= _CONNECT_THE_DOTS_MAX:
            break
        extras = item.get("extras") or {}
        ctype = extras.get("convergence_type")
        if extras.get("convergence_type") == "relationship_activation":
            contact = (extras.get("contact") or "").strip()
            if len(contact.split()) < 2:
                continue
        if ctype in {"cross_company_theme", "db_entity_pair"}:
            continue
        if ctype == "industry_relationship" and "strategic review" in (item.get("title") or "").lower():
            continue
        if ctype == "decision_momentum" and "prep needed:" in (item.get("title") or "").lower():
            continue
        if ctype == "db_multi_source":
            dates = _re.findall(r"\d{4}-\d{2}-\d{2}", item.get("title") or "")
            if len(dates) >= 2 and dates[0] == dates[-1]:
                continue
        key = _connect_the_dots_dedup_key(item)
        entry = rendered.get(key)
        if entry:
            first = _parse_iso_date(entry.get("first_rendered"))
            if first and (target_date - first).days >= CONNECT_THE_DOTS_DEDUP_WINDOW_DAYS:
                continue  # permanently stale -- past its shown-window, drop silently
        else:
            rendered[key] = {"first_rendered": today_str}

        extras = item.get("extras") or {}
        title = (item.get("title") or "").strip()
        why = (item.get("why_it_matters") or "").strip()
        action = (item.get("recommended_action") or "").strip()
        if extras.get("convergence_type") == "decision_momentum":
            action = _trim_decision_momentum_loop_tail(action, title)
        lines.append(f"**{title}**")
        if why:
            lines.append(why)
        if action:
            lines.append(f"→ {action}")
        # Relationship-activation items name a specific contact_id -- offer
        # the same card-lookup hint used elsewhere for named contacts.
        if extras.get("convergence_type") == "relationship_activation" and extras.get("contact"):
            lines.append(_contact_card_hint(extras["contact"]).strip(" ·"))
        lines.append("")
        shown += 1

    if shown == 0:
        return ""

    return "\n".join(lines).rstrip()


def _render_gp_dot_connections(sections: dict, target_date: date,
                                 rendered: dict | None = None,
                                 shown_keys_out: set[str] | None = None) -> str:
    """GP/Genius: Dot Connections — what today's signals mean for Todd's territory.

    Takes facts from the same sources as K: GP/Genius Field Intelligence in the
    Intel Brief, and adds the 'so what' interpretation for the sales mission:
    cross-selling Genius products into enterprise Worldpay/GP customers.

    `rendered` — persisted {dedup_key: {"first_rendered": "YYYY-MM-DD"}}
    registry (GP_DOT_RENDERED_PATH), mutated in place by the caller.

    `shown_keys_out` — RB-2026-09-10: when given, populated with the
    (lowercased, bracket-stripped title) key of every bullet actually
    rendered this run, so a later same-run section can recognize "this
    story already got its link+why here" and not restate it. Built for
    Newsletter & Email Intelligence, which pulls from a separate feed
    (email_intelligence_harvest) than this function's headline/newsletter
    pools -- confirmed live: the same Payments Dive "Latitude...stablecoins"
    story rendered in full in both sections, once under each feed's own
    framing, because there was no shared identity between the two feeds to
    catch it. The `key` values here are already normalized exactly the way
    this function's own cross-day dedup (the `seen` set below) needs them
    to be, so reuse is free.
    """
    if rendered is None:
        rendered = {}
    import re as _re
    # Each bullet is a dict so a "dot" (why-it-matters clause) can be filled
    # in later by the synthesis pass below, once every candidate has been
    # collected. `complete_text` bullets (e.g. the sector-rally bullet) have
    # no dot to substitute and render as-is.
    bullets: list[dict] = []
    _gp_role_context = _gp_dot_role_context(sections)
    _synth_candidates: list[dict] = []  # items with no role-note match, pending LLM synthesis

    # Pull price watch and earnings signals from inbox.
    # Consolidate price-move + volume-spike per ticker into one bullet (mirrors F: Watchlist).
    try:
        from collections import defaultdict as _dd
        pw_path = core.SYSTEM_DIR / "inbox" / "market_signals_earnings.jsonl"
        today_str = target_date.isoformat()
        if pw_path.exists():
            by_ticker: dict = _dd(list)
            for raw in pw_path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                sig = json.loads(raw)
                if str(sig.get("published_at") or "")[:10] != today_str:
                    continue
                ticker = (sig.get("ticker") or sig.get("company") or "UNKNOWN").upper()
                by_ticker[ticker].append(sig)

            for ticker, sigs in by_ticker.items():
                company = (sigs[0].get("company") or ticker)
                score = _gp_dot_score(company + " " + ticker)
                if score < 2:
                    continue
                # Build one consolidated label per ticker
                price_sig = next((s for s in sigs if "PRICE MOVE" in (s.get("title") or "")), None)
                vol_sig = next((s for s in sigs if "VOLUME SPIKE" in (s.get("title") or "")), None)
                hi_lo_sig = next((s for s in sigs if any(x in (s.get("title") or "") for x in ("52W HIGH", "52W LOW"))), None)
                ref_sig = price_sig or hi_lo_sig or sigs[0]
                sig_type = ref_sig.get("signal_type") or ""
                url = ref_sig.get("url") or ""

                parts_label = []
                if price_sig:
                    pm = _re.search(r"([↑↓][0-9.]+%)", price_sig.get("title") or "")
                    if pm:
                        parts_label.append(pm.group(1))
                if vol_sig:
                    vm = _re.search(r"([0-9.]+x) normal volume", vol_sig.get("pain_point_or_priority") or "")
                    if vm:
                        parts_label.append(f"{vm.group(1)} vol")
                if hi_lo_sig:
                    parts_label.append("near 52W high" if "52W HIGH" in (hi_lo_sig.get("title") or "") else "near 52W low")

                move_str = f" ({', '.join(parts_label)})" if parts_label else ""
                _ticker_role_note = _gp_dot_role_note(company, _gp_role_context)
                _ticker_key = f"ticker:{ticker}"
                _ticker_prefix = f"**{company}{move_str}**"
                if sig_type in _DOT_TEMPLATES:
                    bullets.append({"score": score, "prefix": _ticker_prefix, "key": _ticker_key,
                                     "fallback_dot": _DOT_TEMPLATES[sig_type]})
                elif _ticker_role_note:
                    bullets.append({"score": score, "prefix": _ticker_prefix, "key": _ticker_key,
                                     "fallback_dot": f"{_ticker_role_note} — this move is inside a situation you're already in, not adjacent to it."})
                else:
                    fallback = "Signal in the competitive landscape — assess territory impact."
                    bullets.append({"score": score, "prefix": _ticker_prefix, "key": _ticker_key,
                                     "fallback_dot": fallback, "needs_synthesis": True})
                    _synth_candidates.append({"key": _ticker_key, "title": company,
                                               "evidence": f"Price/volume signal: {sig_type or 'movement'}{move_str}"})
    except Exception:
        pass

    # Scan restaurant/tech headlines for GP-relevant items
    pools = [
        sections.get("restaurant_technology_headlines", []),
        sections.get("restaurant_industry_headlines", []),
        sections.get("world_national_headlines", []),
    ]
    for pool in pools:
        for item in pool:
            title = (item.get("title") or "").strip()
            why = _clean_why((item.get("why_it_matters") or item.get("summary") or "").strip())
            extras = item.get("extras") or {}
            url = extras.get("source_url", "").strip()
            source = extras.get("source_name", "").strip()
            sig_type = extras.get("signal_type") or extras.get("signal_badge") or ""
            score = _gp_dot_score(title)
            if score < 2 or not url:
                continue
            if any(j in url for j in ["mail.google.com", "unsubscribe"]):
                continue
            if _is_off_topic(title) and not any(t in title.lower() for t in _GP_OWN_TERMS):
                continue
            # Skip RTN competitor vendor press releases (advertorial content, not intelligence)
            _rtn_src = source.lower() in ("restaurant technology news", "restauranttechnologynews.com")
            _rtn_pr_match = _re.search(
                r"^.{2,45}\b(advances|gives|strengthens|helps|powers|enables|delivers|transforms|simplifies|positions|equips)\b.{0,60}\brestaurants?\b",
                title, _re.IGNORECASE)
            if _rtn_src and _rtn_pr_match:
                if not any(k in title.lower() for k in ("global payments", "genius", "worldpay")):
                    continue
            bare = _re.sub(r"^\[[^\]]+\]\s*", "", title).strip()
            dot = _infer_dot(sig_type, title, _gp_role_context)
            _hl_key = bare.lower()
            _hl_prefix = f"**[{bare}]({url})**"
            bullet = {"score": score, "prefix": _hl_prefix, "key": _hl_key, "fallback_dot": dot}
            if dot == GP_DOT_GENERIC_FALLBACK:
                bullet["needs_synthesis"] = True
                _synth_candidates.append({"key": _hl_key, "title": bare, "evidence": (why or title)[:300]})
            bullets.append(bullet)

    # Scan newsletter articles for GP-relevant items
    _GP_NL_JUNK = ["mail.google.com", "unsubscribe"]
    for nl_item in sections.get("newsletter_intelligence", []):
        extras_nl = nl_item.get("extras") or {}
        source_nl = extras_nl.get("source_name", "")
        for art in (extras_nl.get("articles") or []):
            a_title = (art.get("title") or "").strip()
            a_url = (art.get("url") or "").strip()
            if not a_title or not a_url:
                continue
            if any(j in a_url for j in _GP_NL_JUNK):
                continue
            score = _gp_dot_score(a_title + " " + source_nl)
            if score < 2:
                continue
            if _is_off_topic(a_title) and not any(t in a_title.lower() for t in _GP_OWN_TERMS):
                continue
            dot = _infer_dot("", a_title, _gp_role_context)
            _nl_key = a_title.lower()
            _nl_prefix = f"**[{a_title}]({a_url})**"
            bullet = {"score": score, "prefix": _nl_prefix, "key": _nl_key, "fallback_dot": dot}
            if dot == GP_DOT_GENERIC_FALLBACK:
                bullet["needs_synthesis"] = True
                _synth_candidates.append({"key": _nl_key, "title": a_title, "evidence": a_title})
            bullets.append(bullet)

    # Sector-wide rally dot — if ≥4 watchlist names moved the same direction today,
    # that's a competitive landscape signal worth naming explicitly
    try:
        pw_path = core.SYSTEM_DIR / "inbox" / "market_signals_earnings.jsonl"
        today_str = target_date.isoformat()
        if pw_path.exists():
            import re as _re2
            up_movers: list[str] = []
            for raw in pw_path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                sig = json.loads(raw)
                if not sig.get("_price_watch"):
                    continue
                if str(sig.get("published_at") or "")[:10] != today_str:
                    continue
                if "PRICE MOVE" in (sig.get("title") or "") and "↑" in (sig.get("title") or ""):
                    company = sig.get("company") or sig.get("ticker") or ""
                    if company and company not in up_movers:
                        up_movers.append(company)
            if len(up_movers) >= 4:
                names_str = ", ".join(up_movers[:5])
                if len(up_movers) > 5:
                    names_str += f" +{len(up_movers)-5} more"
                bullets.append({
                    "score": 3, "key": "sector_rally",
                    "complete_text": (
                        f"**Competitive sector rally today** ({names_str}) — "
                        f"Broad institutional buying in restaurant tech/payments. "
                        f"Competitors are being valued up entering your first week. "
                        f"Know their stories — customers will ask."
                    ),
                })
    except Exception:
        pass

    if not bullets:
        return ""

    # Resolve LLM synthesis for candidates with no deterministic role-context
    # match (see brief_synthesis.py) -- reuses any text already cached for a
    # story within its shown window instead of re-calling the API, and never
    # blocks rendering: any failure just leaves fallback_dot in place below.
    _uncached_candidates = [
        c for c in _synth_candidates
        if not (rendered.get(c["key"]) or {}).get("synthesized_why")
    ]
    if _uncached_candidates:
        try:
            import brief_synthesis as _bs
            _synth_result = _bs.synthesize_signals(
                _uncached_candidates,
                "Todd is a restaurant-technology and payments executive joining Global "
                "Payments/Genius, responsible for cross-selling Genius products into "
                "enterprise Worldpay/GP restaurant customers. He tracks a broad watchlist "
                "of restaurant brands and restaurant-tech vendors as competitive/market "
                "intelligence, not all of which are active deals.",
            ) or {}
        except Exception:
            _synth_result = {}
        for _key, _fields in _synth_result.items():
            entry = rendered.setdefault(_key, {})
            entry["synthesized_why"] = _fields.get("why", "")
            entry["synthesized_action"] = _fields.get("action", "")

    # RB-DEFECT-2026-07-09: dedup used to key on bullet[:80] -- the first 80
    # characters of the rendered markdown line, which starts with the
    # article's title but is quickly dominated by its (often very long,
    # tracking-parameter-laden) URL. The same headline picked up from two
    # different source pools (e.g. the general headline scan and the
    # newsletter-article scan) can carry two different URLs for the same
    # story, so both survived as "different" bullets -- confirmed live:
    # "Adyen promotes executives" rendered twice in one brief. Dedup on the
    # normalized story identity (bare title / ticker) captured at each
    # append site instead of a truncated rendering of the final markdown.
    seen: set[str] = set()
    today_str = target_date.isoformat()
    lines = ["## Strategic Connections — GP/Genius\n",
             "*What today's signals mean for your Genius/Worldpay territory.*\n"]
    for b in sorted(bullets, key=lambda x: -x["score"]):
        key = b["key"]
        if key in seen:
            continue
        seen.add(key)
        # RB-2026-09-08: `entry` truthiness used to decide "already went
        # through this dedup gate before" -- wrong, because the synthesis
        # pass above (_uncached_candidates/_synth_result) already calls
        # rendered.setdefault(key, {}) and populates synthesized_why/
        # synthesized_action for a story on its FIRST day, before this loop
        # ever runs. That pre-populated entry has no first_rendered, so the
        # `if entry:` branch below took it (entry was truthy) instead of
        # the `else` branch that stamps first_rendered -- the date was
        # never set, `_parse_iso_date(None)` was always None, and the
        # >= GP_DOT_DEDUP_WINDOW_DAYS check could never fire. Confirmed
        # live: "PAR Technology Launches Guest360" ran as the Intelligence
        # Brief's Top Story identically for 5+ consecutive days with no new
        # evidence -- its persisted entry had synthesized_why/action but no
        # first_rendered at all. Check for the specific first_rendered key,
        # not entry existence, so a synthesis-only entry still gets its
        # clock started the first time it's actually rendered.
        entry = rendered.setdefault(key, {})
        if entry.get("first_rendered"):
            first = _parse_iso_date(entry["first_rendered"])
            if first and (target_date - first).days >= GP_DOT_DEDUP_WINDOW_DAYS:
                continue  # permanently stale -- past its shown-window
        else:
            entry["first_rendered"] = today_str

        if "complete_text" in b:
            bullet_text = b["complete_text"]
        else:
            synthesized_why = entry.get("synthesized_why") if b.get("needs_synthesis") else None
            if synthesized_why:
                dot = synthesized_why
                action = (entry.get("synthesized_action") or "").strip()
                if action and "no specific action warranted" not in action.lower():
                    dot = f"{dot} → {action}"
            else:
                dot = b["fallback_dot"]
            bullet_text = f"{b['prefix']} — {dot}"
        lines.append(f"- {bullet_text}")
        if shown_keys_out is not None:
            shown_keys_out.add(key)

    if len(lines) <= 2:
        return ""
    return "\n".join(lines)


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _compact_gp_connections(markdown: str, limit: int = 3) -> str:
    """Executive projection of the full GP connection analysis.

    The underlying renderer stays complete for diagnostics/tests; the CoS
    brief keeps only conclusions with a specific implication.
    """
    if not markdown:
        return ""
    bullets = [
        line for line in markdown.splitlines()
        if line.startswith("- ") and GP_DOT_GENERIC_FALLBACK not in line
    ][:limit]
    if not bullets:
        return ""
    return "\n".join([
        "## Strategic Connections — GP/Genius",
        "",
        "*Only signals with a specific territory implication.*",
        "",
        *bullets,
    ])


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(target_date: date, dry_run: bool = False, force: bool = False) -> str:
    """Render the Daily Brief (Part 2) for target_date. Returns the markdown string."""
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)

    out_md = BRIEFS_DIR / f"{target_date.isoformat()}-daily-brief.md"
    out_json = BRIEFS_DIR / f"{target_date.isoformat()}-daily-brief.json"

    # RB-DEFECT-067: the cache gate used to be existence-only -- once today's
    # file existed, nothing short of an explicit --force could make this
    # function look at it again, even after the weekly plan was confirmed or
    # regenerated mid-day. Compare the weekly-plan fingerprint stamped into
    # the existing render's companion .json against the current one; a
    # mismatch means upstream state actually changed since that render, so
    # treat it the same as --force rather than serving stale content.
    current_plan_fp = core.weekly_plan_fingerprint()
    current_health_fp = core.source_health_fingerprint()
    if out_md.exists() and not force and not dry_run:
        if core.is_weekly_plan_render_current(out_json) and core.is_source_health_render_current(out_json):
            return out_md.read_text(encoding="utf-8")
        # Fingerprint mismatch (or no fingerprint recorded, e.g. an older
        # render from before this field existed) -- fall through and
        # regenerate rather than trusting a cache that predates a real
        # weekly-plan or source-health state change.

    cache = _load_json(DAILY_BRIEF_CACHE, {})
    data = cache.get("data") or cache
    sections = (data.get("canonical_brief") or {}).get("sections") or {}

    if not sections:
        return f"# RB Daily Brief — {target_date.isoformat()}\n\n*No data available for this date.*\n"

    weekday = target_date.strftime("%A")
    cb = data.get("canonical_brief") or {}
    trust_score = cb.get("trust_score") or 0
    rendered_at = cache.get("_generated_at") or data.get("rendered_at") or ""
    freshness_str = f"data as of {rendered_at[:16].replace('T', ' ')}" if rendered_at else ""
    subheader = f"*{freshness_str}*" if freshness_str else f"*Confidence: {trust_score}%*"

    md_parts = [
        f"# RB Daily Brief — {weekday}, {target_date.strftime('%B %-d, %Y')}",
        subheader,
        "---",
    ]

    # Weekly Plan — day-of-week dispatch
    # Mon: full plan (create/review) | Tue-Fri: progress report | Sat: end-of-week review | Sun: omitted
    weekly_plan = _load_weekly_plan()
    weekly_plan_draft = _load_weekly_plan_draft()
    dow = target_date.weekday()  # Mon=0 … Sun=6

    # RB CoS Brief v2: an executive operating brief, not a rendering of every
    # pipeline section. Underlying ledgers and diagnostics remain available
    # to RB, but only information that can change today's plan is displayed.
    compact_parts = list(md_parts)

    def _add_compact(block: str) -> None:
        if block and block.strip():
            compact_parts.append(block.strip())
            compact_parts.append("---")

    monday_gate_compact = _render_monday_plan_decision_gate(
        weekly_plan, weekly_plan_draft, target_date
    )
    _add_compact(monday_gate_compact)

    # 1. Verified actions. If reconciliation cannot support an action, this
    # section disappears instead of substituting an old loop or generic task.
    _add_compact(_render_cos_today(sections))

    # 2. Calendar preparation with real meeting context.
    _add_compact(_compact_day_ahead(_render_day_ahead(sections, target_date)))

    # 3. Only choices Todd must make and material risks. Recommended system
    # work, loop maintenance, and research queues are intentionally excluded.
    decision_queue_compact = _render_decision_queue(sections)
    if "### DECISIONS REQUIRED" in decision_queue_compact:
        _add_compact(decision_queue_compact.split("### RECOMMENDED ACTIONS", 1)[0].rstrip())
    top_decisions_compact = _render_decision_layer(sections)
    _add_compact(top_decisions_compact)
    risks_compact = _render_strategic_risks(sections)
    if "No new risks detected" not in risks_compact:
        _add_compact(risks_compact)

    # 4. Cross-signal insight downstream of the separate Intelligence Brief.
    ctd_rendered = _load_json(CONNECT_THE_DOTS_RENDERED_PATH, {})
    gp_dot_rendered = _load_json(GP_DOT_RENDERED_PATH, {})
    _gp_dot_shown_keys: set[str] = set()
    _add_compact(_render_connect_the_dots(sections, target_date, rendered=ctd_rendered))
    _add_compact(_compact_gp_connections(_render_gp_dot_connections(
        sections, target_date, rendered=gp_dot_rendered,
        shown_keys_out=_gp_dot_shown_keys,
    )))

    # 5. Relationship status appears only as a verified action or an explicit
    # coverage exception. Baseline-only decay rankings are withheld upstream.
    relationship_compact = _render_relationship_momentum_status(sections)
    if "Coverage incomplete" in relationship_compact or "[ACTION NEEDED]" in relationship_compact:
        if "Coverage incomplete" in relationship_compact:
            relationship_compact = relationship_compact.split("\n**Verified recent activity**", 1)[0].rstrip()
        _add_compact(relationship_compact)

    # 6. RB defect 2026-10-08: pending_mutations (interaction-ledger
    # proposals, same-day executive-move/ownership candidates, and the
    # strategic-assessment Finding 2 review-queue backlog across all 6
    # promotion queues) was computed every day but had no call anywhere in
    # this compact path -- the only *live* render path this function has
    # (everything below the early `return markdown` a few lines down is
    # unreachable). _render_pending_confirmations() already suppresses its
    # own "nothing pending" case, matching the risks_compact/decision_queue
    # filtering convention used just above.
    _add_compact(_render_pending_confirmations(sections))

    if compact_parts and compact_parts[-1] == "---":
        compact_parts.pop()
    markdown = _dedupe_action_tellings("\n\n".join(compact_parts))
    if not dry_run:
        out_md.write_text(markdown, encoding="utf-8")
        _save_json(out_json, {
            "date": target_date.isoformat(),
            "rendered_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            "trust_score": trust_score,
            "weekly_plan_fingerprint": current_plan_fp,
            "source_health_fingerprint": current_health_fp,
            "brief_contract": "cos_concise_v2",
        })
        _save_json(GP_DOT_RENDERED_PATH, gp_dot_rendered)
        _save_json(CONNECT_THE_DOTS_RENDERED_PATH, ctd_rendered)
    return markdown

    # RB-DEFECT-067 (product decision, 2026-08-20): Monday's confirmation
    # must be "unavoidable," not a line item easy to scroll past -- render
    # it as the literal first thing in the brief, ahead of even CoS
    # Opening, when it's still pending. On any other day, fall back to the
    # standard (still prominent, but not gate-framed) draft alert -- see
    # _render_monday_plan_decision_gate's docstring for why this couldn't
    # be a real blocking UI given RB's chat-cockpit interaction surface.
    monday_gate = _render_monday_plan_decision_gate(weekly_plan, weekly_plan_draft, target_date)
    if monday_gate:
        md_parts.append(monday_gate)

    # Sec 0: CoS Opening
    cos = _render_cos_judgment(sections, target_date)
    if cos:
        md_parts.append(cos)
        md_parts.append("---")

    # RB-2026-08-25: cos_today was computed daily, never rendered — see the
    # Part 2 field-coverage sweep note above _render_cos_today's definition.
    cos_today = _render_cos_today(sections)
    if cos_today:
        md_parts.append(cos_today)
        md_parts.append("---")

    if not monday_gate:
        # Surface a pending draft BEFORE the (possibly stale) review below, so
        # staleness is visible rather than silently rendering last cycle's plan.
        # (On Monday with a pending draft, monday_gate already covered this above
        # with stronger framing -- no need to also show the standard banner.)
        draft_alert = _render_weekly_plan_draft_alert(weekly_plan, weekly_plan_draft, today=target_date)
        if draft_alert:
            md_parts.append(draft_alert)
            md_parts.append("---")
    if weekly_plan and dow != 6:  # 6 = Sunday — omit entirely
        if dow == 0:  # Monday — full plan summary for creation/review
            plan_block = _render_weekly_plan_summary(weekly_plan, sections, target_date=target_date)
        elif dow == 5:  # Saturday — end-of-week review
            plan_block = _render_weekly_plan_review(weekly_plan, target_date, sections)
        else:  # Tue–Fri — daily progress report
            plan_block = _render_weekly_plan_progress(weekly_plan, target_date, sections)
        if plan_block:
            md_parts.append(plan_block)
            md_parts.append("---")

    # RB-2026-08-25: restructured section order around the Daily Brief's
    # actual purpose (Todd, 2026-08-25) -- it is downstream of the
    # Intelligence Brief (which already answers "what happened and why it
    # matters"). This document's job is (1) prep for the day, (2) identify
    # risks and opportunities, (3) connect the dots between multiple
    # intelligence signals across multiple days into insight the reader
    # wouldn't have seen alone -- "this is the report the CoS should speak
    # loudest." That reordered decisions/actions and the two dot-connecting
    # sections to lead (right after calendar/plan context), ahead of
    # status/risk detail that used to come first purely because it happened
    # to be computed first upstream.

    # Sec: Decision Queue + Recommended Actions -- "what needs my decision
    # today" is the single highest-priority thing a CoS brief answers.
    md_parts.append(_render_decision_queue(sections))
    md_parts.append("---")

    # RB-2026-08-25: decision_layer/strategic_risks — same "computed, never
    # rendered" gap. Placed right after Decision Queue since they're the
    # same conceptual cluster (what needs deciding today), distinguished by
    # source: Decision Queue sweeps disposition==ask_todd across every
    # section; these are specifically-curated lists that sweep never caught.
    decision_layer = _render_decision_layer(sections)
    if decision_layer:
        md_parts.append(decision_layer)
        md_parts.append("---")
    md_parts.append(_render_strategic_risks(sections))
    md_parts.append("---")

    # Sec: Connect the Dots -- cross-signal, cross-day synthesis; the
    # insight a reader wouldn't have assembled from any single day's
    # intelligence on their own. This is the section that should carry the
    # CoS's loudest, most judgment-heavy voice, so it leads.
    ctd_rendered = _load_json(CONNECT_THE_DOTS_RENDERED_PATH, {})
    connect_the_dots = _render_connect_the_dots(sections, target_date, rendered=ctd_rendered)
    if connect_the_dots:
        md_parts.append(connect_the_dots)
        md_parts.append("---")

    # Sec: GP/Genius: Dot Connections -- the same dot-connecting job,
    # scoped to Todd's employer territory. Sibling to Connect the Dots
    # above, not merged into it (different scope: broader relationship/
    # market picture vs. GP/Genius-specific territory implications).
    gp_dot_rendered = _load_json(GP_DOT_RENDERED_PATH, {})
    _gp_dot_shown_keys: set[str] = set()
    gp_dots = _render_gp_dot_connections(
        sections, target_date, rendered=gp_dot_rendered, shown_keys_out=_gp_dot_shown_keys,
    )
    if gp_dots:
        md_parts.append(gp_dots)
        md_parts.append("---")

    # Sec: Technology Radar -- escalations/risks. Full treatment for
    # watchlist entities with fresh material signals, one collapsed rollup
    # line for everything still open but unchanged since yesterday.
    tech_radar = _render_technology_radar(sections, target_date)
    if tech_radar:
        md_parts.append(tech_radar)
        md_parts.append("---")

    # RB-2026-08-25: same tech-intelligence cluster as Technology Radar
    # above, same "computed, never rendered" gap.
    competitive_watch = _render_competitive_vulnerability_watchlist(sections)
    if competitive_watch:
        md_parts.append(competitive_watch)
        md_parts.append("---")

    # RB-2026-09-01: proposed (never auto-applied) updates from the daily
    # competitor-intelligence review scan.
    battle_cards = _render_competitor_battle_card_review(sections)
    if battle_cards:
        md_parts.append(battle_cards)
        md_parts.append("---")

    # RB-2026-08-28: proof that new intelligence was assessed against
    # downstream artifacts (Blue Sheets, Account Research) today.
    cascade = _render_downstream_artifact_cascade(sections)
    if cascade:
        md_parts.append(cascade)
        md_parts.append("---")

    # Newsletter/email harvesting is an Intelligence Brief input. This CoS
    # brief consumes its implications above instead of repeating the feed.

    notable_contact_moves = _render_notable_contact_moves(sections)
    if notable_contact_moves:
        md_parts.append(notable_contact_moves)
        md_parts.append("---")

    # RB-2026-09-11: same relationship-intelligence cluster, opposite
    # direction from Notable Contact Moves above (data already on file,
    # not a news headline). Direct-load, same pattern render_intelligence_
    # brief.py uses for entity_convergence_scan.py's sibling scan.
    reactivation_candidates = _render_relationship_reactivation_candidates(
        relationship_reactivation_scan.load_last_result()
    )
    if reactivation_candidates:
        md_parts.append(reactivation_candidates)
        md_parts.append("---")

    # RB-2026-08-25: relationship-momentum cluster, same gap. Placed next to
    # Notable Contact Moves since both are relationship-intelligence sections.
    md_parts.append(_render_relationship_momentum_status(sections))
    md_parts.append("---")
    md_parts.append(_render_last_24h_relationship_signals_p2(sections))
    md_parts.append("---")

    # Sec: My Priorities -- RB-2026-08-25: no longer duplicates [PREP]
    # meeting items (removed at the source in daily_brief.py::_compute_my_
    # priorities, not patched here) -- Decision Queue above already gives
    # those meetings a real action, and Day Ahead just below gives them
    # full context. With My Priorities no longer claiming those events at
    # all, Day Ahead's own "is this additive beyond My Priorities" dedup
    # (which reads sections["my_priorities"]'s raw titles) naturally stops
    # treating them as already-covered, so nothing falls through a gap.
    md_parts.append(_render_my_priorities(sections))
    md_parts.append("---")

    # RB-2026-08-25: loops_and_obligations — 9 items, ALL disposition
    # act_today the day this gap was found. Same "computed, never
    # rendered" bug. Placed next to My Priorities as supporting detail.
    md_parts.append(_render_loops_and_obligations(sections))
    md_parts.append("---")

    # Sec: Day Ahead (suppressed when no additive info beyond My Priorities)
    day_ahead = _render_day_ahead(sections, target_date)
    if day_ahead:
        md_parts.append(day_ahead)
        md_parts.append("---")

    # This Week / This Month / Capacity Plan -- forward-looking prep-lead-
    # time context (a genuinely distinct job from "decide today").
    md_parts.append(_render_this_week_priorities(sections))
    md_parts.append("---")
    md_parts.append(_render_this_month_priorities(sections))
    md_parts.append("---")

    # RB-2026-08-25: same forward-looking-prep cluster, same gap.
    md_parts.append(_render_weekly_plan_focus(sections))
    md_parts.append("---")
    md_parts.append(_render_upcoming_prep_requirements(sections))
    md_parts.append("---")
    learned_patterns = _render_learned_patterns(sections)
    if learned_patterns:
        md_parts.append(learned_patterns)
        md_parts.append("---")

    capacity_plan = _render_capacity_plan(sections)
    if capacity_plan:
        md_parts.append(capacity_plan)
        md_parts.append("---")

    # Executive Status — EOLMS roll-up (renders nothing if register is empty/missing)
    exec_status = _render_executive_status()
    if exec_status:
        md_parts.append(exec_status)
        md_parts.append("---")

    # Pending Confirmations — unconfirmed relationship-intelligence proposals (RB 9.69)
    pending_conf = _render_pending_confirmations(sections)
    if pending_conf:
        md_parts.append(pending_conf)
        md_parts.append("---")

    # Proposed Relationship Mutations — behavioral-signal reclassification proposals
    proposed_muts = _render_proposed_relationship_mutations(sections)
    if proposed_muts:
        md_parts.append(proposed_muts)
        md_parts.append("---")

    # RB-2026-08-25: pending_graph_mutations — same "pending mutation"
    # cluster as the two sections just above, same computed-never-rendered
    # gap. Empty (0 items) as of the day this was found, but wired so it
    # doesn't silently disappear again once it has content.
    pending_graph_muts = _render_pending_graph_mutations(sections)
    if pending_graph_muts:
        md_parts.append(pending_graph_muts)
        md_parts.append("---")

    # Opportunities (moved from Intel Brief)
    opp_items = sections.get("opportunity_board", []) + sections.get("w2_intelligence", [])
    _CLOSED_STATES = {"CLOSED", "DECLINED", "REJECTED", "CLOSED_SILENT", "UNKNOWN"}
    active_opps = [i for i in opp_items
                   if (i.get("extras") or {}).get("opportunity_state", "").upper()
                   not in _CLOSED_STATES
                   and not any(s in (i.get("title") or "").upper()
                               for s in ["[REJECTED]", "[CLOSED", "[DECLINED]", "NO ACTIVE OPP", "JOB SEARCH"])]
    if active_opps:
        opp_lines = ["## Active Opportunities\n"]
        for item in active_opps[:5]:
            extras = item.get("extras") or {}
            title = (item.get("title") or "").strip()
            state = (extras.get("opportunity_state") or "ACTIVE").upper()
            age = extras.get("evidence_age_days")
            age_str = f" ({age}d)" if age is not None else ""
            opp_lines.append(f"- **{title}** [{state}{age_str}]")
        md_parts.append("\n".join(opp_lines))
        md_parts.append("---")

    # RB-2026-08-25: job_intelligence — same gap. Empty (0 items, job
    # search inactive) as of the day this was found, wired for when it
    # isn't. Placed next to Active Opportunities as the same career cluster.
    job_intel = _render_job_intelligence(sections)
    if job_intel:
        md_parts.append(job_intel)
        md_parts.append("---")

    # Horizon Watch
    horizon = _render_horizon_watch(sections)
    if horizon:
        md_parts.append(horizon)
        md_parts.append("---")

    # Captures — ICS pending queue
    captures = _render_captures()
    if captures:
        md_parts.append(captures)
        md_parts.append("---")

    # Recent Capture Insights — why_it_matters synthesis for already-processed
    # captures (distinct from the pending queue just above)
    capture_insights = _render_capture_insights()
    if capture_insights:
        md_parts.append(capture_insights)
        md_parts.append("---")

    # CoS Bottom Line (always last)
    md_parts.append(_render_cos_bottom_line(sections, target_date, gp_dots_markdown=gp_dots))

    markdown = _dedupe_action_tellings("\n\n".join(md_parts))

    if not dry_run:
        out_md.write_text(markdown, encoding="utf-8")
        _save_json(out_json, {
            "date": target_date.isoformat(),
            "rendered_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            "trust_score": trust_score,
            "weekly_plan_fingerprint": current_plan_fp,
            "source_health_fingerprint": current_health_fp,
        })
        _save_json(GP_DOT_RENDERED_PATH, gp_dot_rendered)
        _save_json(CONNECT_THE_DOTS_RENDERED_PATH, ctd_rendered)

    return markdown


def main() -> int:
    p = argparse.ArgumentParser(description="Pre-render the RB Daily Brief (Part 2).")
    p.add_argument("--date", default=None, help="Date to render (YYYY-MM-DD). Default: today.")
    p.add_argument("--dry-run", action="store_true", help="Print to stdout, don't save.")
    p.add_argument("--force", action="store_true", help="Re-render even if file exists.")
    args = p.parse_args()

    target = date.fromisoformat(args.date) if args.date else date.today()
    markdown = render(target, dry_run=args.dry_run, force=args.force)

    if args.dry_run:
        print(markdown)
    else:
        out = BRIEFS_DIR / f"{target.isoformat()}-daily-brief.md"
        print(f"✓ Daily Brief rendered: {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
