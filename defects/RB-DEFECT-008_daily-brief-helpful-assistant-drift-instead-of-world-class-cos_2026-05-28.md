# RB-DEFECT-008 — Daily Brief Drifting Toward Helpful Assistant Instead Of World-Class CoS

**Date opened:** 2026-05-28  
**Severity:** Critical  
**Priority:** High  
**Category:** Daily Brief / CoS Behavior / Strategic Intelligence / Execution Governance  
**Status:** Resolved — RB 9.20R (2026-05-28)

## Summary

RB Daily Brief output has improved structurally but still drifts toward
supportive assistant behavior: useful summarization, agreeable framing,
positive reinforcement, and additive recommendations.

Canonical RB should behave like a world-class Chief of Staff:

- prioritize ruthlessly
- protect focus and time
- surface hard truths
- identify negative space
- expose stale assumptions
- connect macro conditions to operating implications
- force execution tradeoffs
- turn intelligence into closed-loop action

The defect is not tone polish. It is a CoS philosophy and operating-system
failure.

## Failure Modes

### 1. Missing hard-truth strategic analysis

Briefs do not reliably say what is not working, what is being avoided, where
focus is leaking, where assumptions are weak, or where effort is not converting
into movement.

### 2. Missing macro/world-event synthesis

Macro sections remain generic. They should connect world events, geopolitics,
tariffs, labor, immigration, commodities, freight/oil, interest rates,
consumer confidence, AI capital, VC contraction, and enterprise budget pressure
to restaurant operations, restaurant technology spend, vendor survivability,
hiring trends, enterprise deal cycles, platform consolidation, and customer
buying behavior.

### 3. No closed-loop execution system

The brief informs but does not consistently operationalize: create task, open
loop, monitor relationship, schedule reminder, escalate priority, add to
opportunity pipeline, or open outreach loop.

### 4. Stale-source intelligence not transparent enough

The brief must explicitly distinguish fresh, stale, inferred, verified, rumor,
historical memory, source unavailable, and refresh failed. Stale assumptions
must not be allowed to sound current.

### 5. No "what is not happening" analysis

Negative-space intelligence is missing: stalled momentum, missing follow-ups,
weak conversion, absent recruiter movement, cooling relationships, strategic
silence, underperforming initiatives.

### 6. Too much "yes man" weighting

The system still optimizes for pleasant usefulness instead of strategic
operational value. A CoS is not a motivational assistant.

## Acceptance Criteria

A successful fix must make Daily Brief output include, when evidence supports
it:

1. Hard-truth strategic analysis
2. Negative-space intelligence
3. Macro/world-event synthesis mapped to industry and user implications
4. Explicit prioritization tradeoffs and opportunity cost
5. Closed-loop execution prompts and machine-readable action options
6. Stale intelligence detection and refresh attempts
7. Transparent source freshness report
8. Daily Brief completion/failure notification artifact
9. Intelligence status labels: fresh, stale, inferred, verified, rumor,
   historical_memory, source_unavailable, refresh_failed
10. Behavioral weighting from agreeable/helpful to strategic/useful

## Sprint

Implemented by:

- `system/CLAUDE_SPRINT_RB_9_20_WORLD_CLASS_COS_DAILY_BRIEF.md`

## Resolution — RB 9.20R (2026-05-28)

Root cause: `cos_judgment.py` was written to the wrong directory
(`/Documents/Relationship Bridge/`) during initial development and never
landed in the real production repo (`/Documents/Claude/Projects/Relationship Builder/`).

Fix applied:
1. `system/scripts/cos_judgment.py` — copied to production repo
2. `system/scripts/daily_brief.py` — best-effort import + wiring:
   - `_HAS_COS_JUDGMENT` flag on import
   - `_cj.build_all(report, today)` called in `build_report()` after all signals assembled
   - CoS blocks (`source_freshness`, `cos_judgment`, `what_is_not_happening`,
     `execution_options`, `macro_to_operator_synthesis`, `linkedin_relationship_delta`)
     injected into the report dict before `build_daily_prep_summary()`
   - CoS sections added to `build_canonical_brief()` sections dict and populated
   - CoS rendering added to `render_today_md()`
   - Two rendering rules added to the brief contract
3. `system/tests/test_cos_judgment_integration.py` — 14 regression tests:
   - Import verification (DEFECT-008 regression gate)
   - build_all() returns all 6 required blocks
   - hard_truths are evidence-bound (claim + evidence required)
   - write-like execution options always have `requires_confirmation=True`
   - source_freshness returns per-source labels
   - `daily_brief._HAS_COS_JUDGMENT is True`

Test result: 360 passed, 0 failed.
