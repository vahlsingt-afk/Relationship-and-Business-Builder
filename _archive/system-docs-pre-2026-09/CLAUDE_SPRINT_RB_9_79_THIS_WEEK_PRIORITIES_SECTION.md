# RB 9.79: "This Week" Section — RB-DEFECT-044 (third slice)

**Status:** Implemented (2026-06-14)
**Source:** RB-DEFECT-044's two-document briefing architecture (Document 2 =
Daily Brief, redefined canonical structure). RB 9.77 implemented "Upcoming
Preparation Requirements", RB 9.78 implemented "Risks"; this sprint implements
"This Week".

## Scope

Per RB-DEFECT-044's canonical Daily Brief, "This Week" covers near-term
priorities: interviews, earnings calls, customer meetings, deliverables. This
was a genuine gap — no section in `daily_brief.py` synthesized a 2-7 day-out
view; "Today" sections (`five_things_today`, `decision_layer`, etc.) cover
today only, and `upcoming_preparation_requirements` (RB 9.77) covers prep-time
estimates, not general awareness of what's coming.

"This Month" and "Horizon Watch (30-90 days)" remain deferred — both need
either new data sources (industry/earnings event calendars beyond the existing
30-day watchlist) or a "month" time horizon RB doesn't currently model.

## What was implemented

`system/scripts/daily_brief.py`:

1. **New section `this_week_priorities`** (Document 2/Daily Brief, Bucket 5
   "Today"), added to `brief_display_order` immediately after `strategic_risks`
   (RB 9.78).
2. **New `_compute_this_week_priorities(report, sections)`**, computed after
   `earnings_intelligence` and `opportunity_board` are populated. Pulls from
   four sources:
   - **Calendar events** in `calendar_overlay`'s `this_week` bucket that carry
     a deliverable-shaped title, known-contact match, or active-thread tie
     (via `_meeting_prep_items`).
   - **Loops** from `loop_ledger`'s `this_week` bucket (closing this week).
   - **Earnings calls**: `earnings_intelligence` items with
     `extras.earnings_type == "pre_earnings_alert"` and
     `extras.days_until_report <= 7`.
   - **Active interviews/recruiter engagements**: `opportunity_board` items
     with `disposition` in `act`/`act_today` and an interview-related keyword
     in the title.
   - If nothing qualifies, emits "No near-term (this week) priorities
     detected" (`disposition=ignore`) — same trust pattern as RB 9.77/9.78.
3. **New rendering rule**: renders `this_week_priorities` as "📆 This Week"
   after `strategic_risks`; green-board confirmation for the empty case;
   explicitly told not to duplicate `upcoming_preparation_requirements`'s
   "Within 7 Days" group (different purpose: awareness vs. prep-time).

## Deferred

- **This Month** and **Horizon Watch (30-90 days)** as distinct Daily Brief
  sections — larger scope, likely need new signal sources.
- **Learned Patterns** — needs historical pattern-tracking infrastructure.
- Headline "5-10 items, new only" enforcement (Document 1) — not addressed.

## Tests

Full suite: `python3 -m pytest system/tests/ -q` — expected 2380 passed. No new
tests added (pure aggregation over existing sections, consistent with
RB 9.77/9.78).
