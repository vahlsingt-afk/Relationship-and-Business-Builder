# RB 9.82: Horizon Watch (30-90 Days) — new Section 5

**Status:** Implemented (2026-06-14)
**Source:** Continuation of the RB-DEFECT-044 multi-sprint track
(`system/CLAUDE_DEFECT_RB_044_THREE_LAYER_BRIEFING_ARCHITECTURE.md`), after
RB 9.81 closed the Part 2 payload wiring gap.

## Scope

RB-DEFECT-044's canonical Daily Brief structure includes "Horizon Watch
(30-90 Days)" — "important developments not requiring action yet. Examples:
industry events, earnings seasons, renewal cycles, competitive developments."

The earlier RB-DEFECT-044 audit assumed this section needed new signal
sources. Investigation found two existing sources cover it with small,
bounded changes to existing modules:

1. `earnings_monitor.py`'s `_ALERT_WINDOWS` capped pre-earnings alerts at 30
   days — `_build_pre_earnings_alerts()` dropped reports beyond 30 days
   entirely, even though companies with `watch_priority=True` and a report
   date 31-90 days out are exactly "earnings seasons" worth tracking.
2. `competitive_vulnerability_watchlist` items carry
   `extras.estimated_horizon_months`, computed by
   `vulnerability_taxonomy.category_horizon()`. Only the RFP category has
   `horizon_months == 3` (~90 days) — the other categories (6/12/18 months)
   are beyond the 30-90 day window. RFP-category items with
   `disposition == "monitor"` (not yet escalated to `strategic_risks`) are
   "not yet a risk, but worth tracking" — a Horizon Watch fit.

## What was implemented

### `system/scripts/earnings_monitor.py`

- `_ALERT_WINDOWS` gained a fourth tier: `(90, "90 DAYS", "monitor")`. Reports
  31-90 days out now generate a `pre_earnings_alert` item with
  `extras.alert_window == "90 DAYS"` and `disposition == "monitor"`, instead
  of being silently dropped.

### `system/scripts/daily_brief.py`

- New `_compute_horizon_watch(report, sections)`:
  - Pulls `earnings_intelligence` items with `extras.alert_window == "90
    DAYS"`, reframing them as horizon items (disposition `monitor`,
    "no action required yet" framing).
  - Pulls `competitive_vulnerability_watchlist` items with
    `extras.estimated_horizon_months <= 3` and `disposition == "monitor"`.
  - Negative-confirmation fallback: "No developments in the 30-90 day horizon
    detected" when both sources are empty.
- New `"horizon_watch": []` entry in the `sections` dict (initialized near
  `this_week_priorities`).
- New `"horizon_watch"` entry in `brief_display_order`, Bucket 7 (Forward
  Look), immediately before `connect_the_dots`.
- Computation call placed after both `earnings_intelligence` and
  `this_week_priorities` (which itself runs after
  `competitive_vulnerability_watchlist`), so both source sections are
  populated first.

### `system/api/server.py`

- Added `horizon_watch` to `_ACTION_BRIEF_SECTIONS_PART2` (Synthesis &
  mutations group, before `connect_the_dots`).
- Added `_SECTION_EXTRAS_KEYS["horizon_watch"]`: `horizon_days`,
  `estimated_date`, `company`, `estimated_horizon_months`, `entity_name`.
- Added a compaction-limit branch: `horizon_watch` → 5 in Part 2 / 3 in
  Part 1.

All three layers (computation, display order, payload wiring) were done in
this single change — per RB 9.81's lesson, a section computed but not wired
into `_ACTION_BRIEF_SECTIONS_PART2` is invisible to the GPT.

### KB files (`system/api/`)

- **`DAILY_BRIEF_CANONICAL_TEMPLATE.md`**: new "Section 5 — Horizon Watch
  (30-90 Days)" after Section 4 — example content, source mapping, format
  rules (single section, no sub-sections, "no action required yet" framing,
  green-board fallback). Updated Part 2 pass/fail criteria to require
  Section 5's presence (even in negative-confirmation form).
- **`CANONICAL_RESPONSE_CONTRACT.md`**: documented the RB 9.82 change
  (sources, wiring, rendering) and added a banned pattern for omitting
  Section 5.
- **`custom_gpt_instructions_compact_8k.md`** (now 7,755/8,000 chars): new
  bullet for Section 5.
- **`custom_gpt_instructions_8k.md`**: new paragraph for Section 5 after the
  Section 4 sub-section enumeration.
- **`custom_gpt_prompt.md`**: new bullet for Section 5 (Part 2) with source
  mapping and fallback rule.
- `custom_gpt_operational_playbook.md` — not reviewed for this sprint (no
  Section 4/5-specific content per RB 9.81's review).

## Tests

- Updated `system/tests/test_earnings_monitor_sprint_g.py`:
  - `EG3NoAlertOutside30Days::test_EG3a_no_alert_beyond_30_days` (asserted no
    alert for a ~66-day-out company) → replaced with
    `test_EG3a_horizon_watch_alert_within_90_days`, asserting a "90 DAYS"
    `monitor` alert is now generated.
  - Added `test_EG3a2_no_alert_beyond_90_days` for a ~158-day-out company
    (still outside all windows).
- Full suite: `python3 -m pytest system/tests/ -q` — **2381 passed**
  (125.04s).

## Deferred

- "This Month" Daily Brief section (RB-DEFECT-044).
- "Learned Patterns" (RB-DEFECT-044) — needs new historical-tracking
  infrastructure.
- RB-DEFECT-044 Document 1/Document 2 KB reframe (carried from RB 9.81).
- Live GPT re-sync (5 KB files + compact_8k Instructions) — carried from
  RB 9.81, now also covers the Section 5 addition.
