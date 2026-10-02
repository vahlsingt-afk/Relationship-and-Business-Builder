# RB 9.84: "Learned Patterns" — recurring preparation-time patterns (Section 4, 12th sub-section)

**Status:** Implemented (2026-06-14)
**Source:** Continuation of the RB-DEFECT-044 multi-sprint track
(`system/CLAUDE_DEFECT_RB_044_THREE_LAYER_BRIEFING_ARCHITECTURE.md`), after
RB 9.83 closed "This Month" as Section 4's 11th sub-section.

## Scope

RB-DEFECT-044's canonical Daily Brief structure's final, previously-deferred
item: "Learned Patterns" —

> Relationship Bridge should identify recurring preparation behaviors.
> Examples: Weekly sales review requires 30 minutes preparation; Monthly
> board review requires 2 hours preparation; Quarterly business review
> requires account research. The system should learn these patterns and
> proactively recommend preparation windows before deadlines occur.

This was the one item flagged in RB 9.83's "Deferred" section as needing
"new historical-tracking infrastructure" — unlike This Month/Horizon Watch,
which turned out tractable from existing data. This sprint builds the
minimal slice of that infrastructure:

1. A rolling history log (`system/.cache/prep_requirements_history.json`)
   recording, once per day, which recurring-meeting *categories* appeared in
   `upcoming_preparation_requirements` (RB 9.77) and at what prep-time
   estimate.
2. A pattern detector (`_compute_learned_patterns`) that surfaces a category
   once it has recurred on 3+ distinct tracked days, using the mode prep-time
   estimate across those days.

Categories reuse the existing `_PREP_TIME_RULES` keyword groups (Interview
prep, Earnings/board review, Sales/pipeline review) so the same recurring
meeting types that drive today's prep-time *estimate* also drive tomorrow's
*learned pattern* — titles vary day to day (different company names, dates),
but the category is stable.

## What was implemented

### `system/scripts/daily_brief.py`

- `_PREP_CATEGORY_RULES` — 3 category labels mapped to the same keyword
  groups as `_PREP_TIME_RULES` (Interview prep / Earnings-board review /
  Sales-pipeline review). Anything else classifies as `"Other"` and is
  excluded from history (titles too varied to form a meaningful pattern).
- `_prep_item_category(title)` — maps a prep-requirement title to a category
  label.
- `_prep_history_path()` — `core.CACHE_DIR / "prep_requirements_history.json"`.
- `_record_prep_requirements_history(report, prep_items)`:
  - For each `upcoming_preparation_requirements` item with a recognized
    category, records `{category, prep_minutes}` for today.
  - Strips the "Within N Days — " horizon prefix before categorizing.
  - Replaces (not appends to) today's entry on repeated same-day brief
    regenerations — idempotent.
  - Trims history to the most recent 90 days.
- `_compute_learned_patterns(report, sections)`:
  - Loads history, counts per-category occurrences (one per distinct day).
  - For categories with 3+ occurrences, emits "Learned pattern: [category]"
    with the mode `extras.prep_minutes`, `extras.occurrences`,
    `extras.days_tracked`, and a `recommended_action` to schedule that prep
    window ahead of the next occurrence.
  - Negative confirmations: "Learned patterns: insufficient history" (fewer
    than 3 days tracked total) or "No recurring preparation patterns
    detected" (3+ days tracked, nothing recurs).
- New `"learned_patterns": []` entry in the `sections` dict (initialized
  after `upcoming_preparation_requirements`).
- New `"learned_patterns"` entry in `brief_display_order`, immediately after
  `upcoming_preparation_requirements` (Bucket 5 — Today) — it's derived from
  that section's history.
- Computation call: `_record_prep_requirements_history(...)` then
  `_compute_learned_patterns(...)`, placed immediately after the
  `upcoming_preparation_requirements` computation block so history reflects
  today's run.

### `system/api/server.py`

- Added `learned_patterns` to `_ACTION_BRIEF_SECTIONS_PART2`, immediately
  after `upcoming_preparation_requirements`.
- Added `_SECTION_EXTRAS_KEYS["learned_patterns"]`: `category`,
  `prep_minutes`, `occurrences`, `days_tracked`.
- Added a compaction-limit branch: `learned_patterns` → 5 in Part 2 / 3 in
  Part 1.

Computation, display order, and payload wiring done in the same change —
per RB 9.81's lesson.

### KB files (`system/api/`)

- **`DAILY_BRIEF_CANONICAL_TEMPLATE.md`**: Section 4 ("My Priorities") grew
  from 11 → 12 sub-sections — new "LEARNED PATTERNS" sub-section (example
  content) inserted after PREP REQUIRED and before RISKS. Sources list,
  format rules ("Twelve sub-sections..."), and Part 2 pass/fail criteria all
  updated (11 → 12).
- **`CANONICAL_RESPONSE_CONTRACT.md`**: documented the new sub-section
  (history infrastructure, pattern-detection rule, negative-confirmation
  forms) and added a banned pattern for omitting LEARNED PATTERNS. Noted
  this closes RB-DEFECT-044's canonical Daily Brief section list.
- **`custom_gpt_instructions_compact_8k.md`** (now 7,899/8,000 chars):
  Section 4 bullet updated to 12 sub-sections, "last 6 are new" (was "last
  5"), negative-confirmation wording extended to cover "Learned patterns:
  insufficient history".
- **`custom_gpt_instructions_8k.md`**: Section 4 paragraph updated to 12
  sub-sections with a LEARNED PATTERNS description.
- **`custom_gpt_prompt.md`**: Section 4 quick-reference expanded to 12
  sub-sections with LEARNED PATTERNS source mapping and fallback rule.

## Tests

New `system/tests/test_learned_patterns.py` (10 tests):
- Category classification (TestLearnedPatternsCategory): interview, earnings
  /board, sales/pipeline, and "Other" fallback.
- History recording: file creation, "Other" exclusion, same-day
  idempotency.
- Pattern detection: no history file → insufficient history; below-threshold
  days → insufficient history; at-threshold with no recurring category → "No
  recurring preparation patterns detected"; recurring category → "Learned
  pattern: ..." with correct `extras` (category, prep_minutes, occurrences,
  days_tracked).

Full suite: `python3 -m pytest system/tests/ -q` — **2391 passed**
(119.63s), up from 2381 (+10 new tests, zero regressions).

## Deferred

- RB-DEFECT-044 Document 1/Document 2 KB reframe (carried from RB 9.81).
- Live GPT re-sync (5 KB files + compact_8k Instructions) — carried from RB
  9.81/9.82/9.83, now also covers the Section 4 LEARNED PATTERNS addition.
- This sprint's pattern detector only looks at `upcoming_preparation_requirements`
  categories (the data RB already estimates prep time for). The defect's
  fuller vision — e.g. learning prep patterns for *any* recurring meeting
  type, not just the 3 keyword-matched categories — would need a broader
  meeting-type classifier; not attempted here to keep the change bounded.
- With this sprint, **RB-DEFECT-044's canonical Daily Brief section list is
  now fully implemented**: Executive Summary, Today, This Week (9.79), This
  Month (9.83), Horizon Watch (9.82), Opportunities, Risks (9.78),
  Relationships Requiring Attention, Decisions Approaching, Signals and
  Patterns, Upcoming Preparation Requirements (9.77), Learned Patterns
  (9.84).
