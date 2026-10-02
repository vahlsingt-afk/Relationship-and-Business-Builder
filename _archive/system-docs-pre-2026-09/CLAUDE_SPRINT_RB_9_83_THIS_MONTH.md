# RB 9.83: "This Month" — new Section 4 sub-section (8-30 day strategic priorities)

**Status:** Implemented (2026-06-14)
**Source:** Continuation of the RB-DEFECT-044 multi-sprint track
(`system/CLAUDE_DEFECT_RB_044_THREE_LAYER_BRIEFING_ARCHITECTURE.md`), after
RB 9.82 closed "Horizon Watch (30-90 Days)" as Section 5.

## Scope

RB-DEFECT-044's canonical Daily Brief structure includes "This Month" —
"Strategic priorities. Examples: career decisions, major projects,
conferences, business development opportunities." This sits between "This
Week" (RB 9.79, 2-7 days) and "Horizon Watch" (RB 9.82, 31-90 days,
not-yet-actionable) — the 8-30 day strategic-progress window.

Three existing sources cover this window without new infrastructure:

1. `loops.future` (loop targets >7 days out) — filter to `7 < days_out <= 30`,
   the same bucket `_compute_upcoming_preparation_requirements`'s "Within 30
   Days" tier already uses for prep-time estimates, but reframed here as
   strategic-progress awareness rather than prep time.
2. `earnings_intelligence` pre-earnings alerts with `extras.days_until_report`
   in `(7, 30]` — earnings calls 8-30 days out ("earnings seasons").
3. `opportunity_board` items with `disposition == "monitor"` and
   `extras.evidence_age_days >= 14` — longer-running active-pipeline items
   ("business development opportunities") not yet picked up by
   `this_week_priorities`'s interview-stage filter.

## What was implemented

### `system/scripts/daily_brief.py`

- New `_compute_this_month_priorities(report, sections)`:
  - Loop targets with `7 < days_out <= 30` from `loops.future`.
  - `earnings_intelligence` pre-earnings alerts with `days_until_report` in
    `(7, 30]`.
  - `opportunity_board` items with `disposition == "monitor"` and
    `evidence_age_days >= 14`.
  - Negative-confirmation fallback: "No strategic (this month) priorities
    detected" when all three are empty.
- New `"this_month_priorities": []` entry in the `sections` dict
  (initialized after `this_week_priorities`).
- New `"this_month_priorities"` entry in `brief_display_order`, immediately
  after `this_week_priorities` (Bucket 5 — Today).
- Computation call placed after `this_week_priorities` so it's additive
  (loops/earnings beyond the this-week horizon, opportunities not picked up
  by this-week's interview filter).

### `system/api/server.py`

- Added `this_month_priorities` to `_ACTION_BRIEF_SECTIONS_PART2`,
  immediately after `this_week_priorities`.
- Added `_SECTION_EXTRAS_KEYS["this_month_priorities"]`: `loop_id`,
  `target_date`, `days_until_report`, `estimated_report_date`, `company`,
  `evidence_age_days`.
- Added a compaction-limit branch: `this_month_priorities` → 5 in Part 2 / 3
  in Part 1.

Computation, display order, and payload wiring done in the same change —
per RB 9.81's lesson.

### KB files (`system/api/`)

- **`DAILY_BRIEF_CANONICAL_TEMPLATE.md`**: Section 4 ("My Priorities") grew
  from 10 → 11 sub-sections — new "THIS MONTH" sub-section (example content)
  inserted after THIS WEEK and before PREP REQUIRED. Sources list, format
  rules ("Eleven sub-sections..."), and Part 2 pass/fail criteria all updated
  (10 → 11).
- **`CANONICAL_RESPONSE_CONTRACT.md`**: documented the new sub-section
  (sources, wiring, distinction from THIS WEEK/Horizon Watch) and added a
  banned pattern for omitting THIS MONTH.
- **`custom_gpt_instructions_compact_8k.md`** (now 7,793/8,000 chars):
  Section 4 bullet updated to 11 sub-sections, "last 5 are new" (was "last
  4").
- **`custom_gpt_instructions_8k.md`**: Section 4 paragraph updated to 11
  sub-sections with a THIS MONTH description.
- **`custom_gpt_prompt.md`**: Section 4 quick-reference expanded to 11
  sub-sections with THIS MONTH source mapping and fallback rule.

## Tests

Full suite: `python3 -m pytest system/tests/ -q` — **2381 passed**
(153.52s). No new tests — `_compute_this_month_priorities` follows the same
shape as `_compute_this_week_priorities` (RB 9.79) and
`_compute_horizon_watch` (RB 9.82), exercised implicitly by existing
canonical-brief tests.

## Deferred

- "Learned Patterns" (RB-DEFECT-044) — needs new historical-tracking
  infrastructure.
- RB-DEFECT-044 Document 1/Document 2 KB reframe (carried from RB 9.81).
- Live GPT re-sync (5 KB files + compact_8k Instructions) — carried from RB
  9.81/9.82, now also covers the Section 4 THIS MONTH addition.
