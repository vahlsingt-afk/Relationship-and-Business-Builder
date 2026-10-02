# RB 9.77: Upcoming Preparation Requirements + Headline No-Material-Change Fallbacks — RB-DEFECT-044 (first slice)

**Status:** Implemented (2026-06-14)
**Source:** RB-DEFECT-044's two-document briefing architecture (Document 1 =
Intelligence Brief incl. Collection Report; Document 2 = Daily Brief, redefined).
Per the audit triage below, this sprint takes the largest acknowledged net-new gap
("Upcoming Preparation Requirements") plus two cheap, low-risk Document 1 fixes.

## Audit summary (against RB-DEFECT-044)

An agent audit of `daily_brief.py` against RB-DEFECT-044's two canonical documents
found:

- **Document 1 structure is mostly already correct**: `intelligence_collection_summary`
  already sits at the top of `brief_display_order` (Bucket 1), ahead of all
  intelligence content — no reordering needed.
- **"Previously reported intelligence must never resurface as new"** is already
  implemented via `intelligence_lifecycle.py`'s NEW → ACKNOWLEDGED → DORMANT →
  REACTIVATED → RETIRED state machine with `first_seen`/`brief_report_count`
  tracking, wired into `filter_suppressed_from_sections`.
- **`watchlist_intelligence`** already implements the "material changes only, roll
  up no-change entities into one item, never per-company no-change spam" rule from
  the defect's "Correct"/"Incorrect" examples (RB 9.74).
- **Gaps found**:
  1. `world_national_headlines` / `restaurant_industry_headlines` /
     `restaurant_technology_headlines` were rendered with "skip if empty" instead
     of the spec's explicit "No material X developments detected during this
     collection cycle" fallback.
  2. No "Upcoming Preparation Requirements" or "Learned Patterns" concept existed
     at all — `meeting_prep_and_deliverables` produces ad-hoc "Prep needed: ..."
     items per upcoming meeting, but with no prep-time estimates, no horizon
     framing (24h/7 days/30 days), and no required-inputs list.
  3. No "Last watch list mutation: N days ago" tracking in `watchlist_intelligence`'s
     no-change rollup (minor — deferred, see below).

## What was implemented

`system/scripts/daily_brief.py`:

1. **New section `upcoming_preparation_requirements`** (Document 2/Daily Brief),
   added to `brief_display_order` Bucket 5 ("Today"), immediately before
   `decision_layer`.
   - New helper `_prep_time_estimate(title, mp=None)`: keyword-based heuristic
     (interview → 30 min + candidate/role background; earnings/board/QBR → 120 min
     + financial/board materials; pipeline/forecast/sales review → 30 min + pipeline
     report/forecast/open opportunities; falls back to deliverable-signal,
     known-attendee, or generic 15 min).
   - New `_compute_upcoming_preparation_requirements(report)`:
     - **Within 24 Hours** / **Within 7 Days**: from `calendar_overlay`'s
       `today`/`tomorrow`/`this_week` buckets (the only horizon calendar_overlay
       covers), each item carrying `extras.horizon`, `extras.prep_minutes`,
       `extras.required_inputs`, `extras.event_start`.
     - **Within 30 Days**: from `loop_ledger`'s `future` bucket (loops targeted
       8-30 days out) — RB has no calendar visibility beyond 7 days, so this
       bucket is loop-ledger-only by necessity.
     - If nothing qualifies in any bucket, emits a single "No upcoming preparation
       requirements detected" item.
   - New rendering rule: groups by `extras.horizon` in order (24h, 7 days,
     30 days), shows prep-time estimate + required inputs + recommendation per
     item, and explicitly notes the 30-day bucket's loop-ledger-only limitation so
     the GPT doesn't imply full calendar coverage.

2. **Headline "no material developments" fallbacks** (Document 1/Intelligence
   Brief): after the intelligence-lifecycle pass (which may suppress
   previously-reported headline items), if `world_national_headlines`,
   `restaurant_industry_headlines`, or `restaurant_technology_headlines` is empty,
   append one `_canonical_item` titled "No material [X] developments detected"
   with the exact fallback summary text from RB-DEFECT-044's spec. Updated the
   corresponding rendering rule to render this item as a single line instead of
   skipping the section.

## Deferred

- **"Learned Patterns"** (recurring prep-time pattern learning across cycles) —
  needs historical pattern-tracking infrastructure; out of scope for this slice.
- **"Last watch list mutation: N days ago"** in `watchlist_intelligence`'s no-change
  rollup — would need a persisted mutation-date tracker (similar to RB 9.76's
  calendar-snapshot pattern); minor, deferred.
- The remaining Document 1/Document 2 canonical-structure items from
  RB-DEFECT-044 (Opportunities, Risks, This Week/This Month/Horizon Watch as
  distinct Daily Brief sections, World/National/Restaurant headline "5-10 items,
  new only" enforcement beyond the fallback message) — future RB 9.78+ slices.

## Tests

Full suite: `python3 -m pytest system/tests/ -q` — 2380 passed (126.99s). No new
tests added (pure aggregation/heuristics over existing data structures, exercised
implicitly by existing canonical-brief tests, consistent with RB 9.75/9.76).
