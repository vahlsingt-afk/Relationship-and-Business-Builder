# RB-DEFECT-067 — Monday Weekly Plan Was Not Adopted and Brief Rebuild Reused Stale Render

Date observed: 2026-08-19  
Reported by: Todd  
Status: Open — fix required  
Severity: High  
Area: Weekly planning, morning pipeline, Daily Brief rendering, ChatGPT cockpit fidelity

## User-Visible Failure

Todd expected the weekly plan generated on Monday, 2026-08-17, to govern the week. On Wednesday, 2026-08-19, the Daily Brief instead reported that the August 17 plan was still awaiting confirmation and continued operating from the prior week-of-2026-08-10 plan.

The stale state was visible in `system/briefs/2026-08-19-daily-brief.md`:

- `Weekly Plan Draft Awaiting Confirmation`
- draft week: `2026-08-17`
- active week: `2026-08-10`
- weekly progress still showed the prior Campero, Jeff Coffland, Five Guys, and loop-hygiene allocations.

This violates the product model: the weekly plan is the operating frame for daily recommendations, not an optional report that may silently remain stale.

## Expected Behavior

1. Monday's weekly-planning run should create the plan for the current Monday-through-Friday operating week.
2. The plan should be adopted during Monday's planning flow, or Monday's ChatGPT cockpit interaction should make the required confirmation unavoidable and persist it immediately.
3. Tuesday-through-Friday briefs must never silently use a prior-week plan when a current-week plan exists.
4. If a current-week draft is not adopted, the morning run must fail or surface a blocking cockpit decision before publishing recommendations based on the old plan.
5. Once a weekly plan is adopted, every daily artifact and API payload must be recomputed and rendered from that plan without requiring a hidden `--force` repair step.

## Actual State and Evidence

Before correction:

- `system/weekly_plan_draft.json`
  - `week_of`: `2026-08-17`
  - `status`: `draft_pending_confirmation`
  - generated Monday at `2026-08-17T10:02:48.524340Z`
- `system/weekly_plan.json`
  - `week_of`: `2026-08-10`
  - `status`: `active`
- Both the August 18 and August 19 rendered Daily Briefs warned that the current weekly plan remained unconfirmed.

Todd's explicit correction on August 19 was treated as confirmation. Running:

```bash
python3 system/scripts/weekly_plan_generator.py --confirm --date 2026-08-19
```

correctly promoted the August 17 draft to `system/weekly_plan.json`.

However, a second failure then appeared. Running:

```bash
python3 system/scripts/morning_pipeline.py --brief-only
```

reported all steps as passing, but `system/briefs/2026-08-19-daily-brief.md` still contained the stale August 10 plan and the pending-draft warning.

The reason is that `render_daily_brief.py` returns the existing dated output when it exists unless `--force` is supplied. The brief-only pipeline did not invalidate or force regeneration after the canonical weekly-plan state changed.

The stale render was cleared only after explicitly recomputing the report and forcing the renderer:

```bash
python3 system/scripts/daily_brief.py --date 2026-08-19 --cache
python3 system/scripts/render_daily_brief.py --date 2026-08-19 --force
python3 system/scripts/publish.py --write --confirm --json
```

After that repair, the rendered brief correctly showed:

- no pending-plan warning;
- active week `2026-08-17`;
- outcome `Close out overdue and due-today loops`;
- allocation `100%`;
- progress `0/9 loops resolved — 9 remaining`.

## Root-Cause Hypotheses

### 1. Adoption is not guaranteed by the Monday operating contract

`morning_pipeline.py` generates `weekly_plan_draft.json`, but confirmation remains a separate state mutation. If the ChatGPT confirmation interaction does not occur or does not persist, the system continues using the previous week's plan indefinitely.

The existing alert is informative but insufficient. It allows the system to publish an execution brief using a stale weekly frame.

### 2. Current-week draft and prior-week active plan are treated as a warning, not an invalid operating state

On execution days, a current-week draft plus a prior-week active plan should not result in normal recommendations. This is a state-consistency failure requiring adoption, rejection/regeneration, or an explicit degraded mode.

### 3. Render invalidation does not track canonical planning-state changes

`render_daily_brief.py` uses dated-file existence as its cache gate. It does not compare the output against:

- `weekly_plan.json` modification time or content hash;
- the `daily_brief` cache generation time/hash;
- `weekly_plan_draft.json` status changes;
- the canonical report's plan week.

Therefore a successful rebuild can leave the user-facing rendered brief stale.

### 4. Pipeline success does not verify semantic consistency

`morning_pipeline.py --brief-only` returned PASS even though the output contradicted the active canonical plan. The pipeline checks command completion, not whether all artifacts reflect the same weekly-plan version.

## Required Fix

1. Define and enforce the Monday adoption contract.
   - Preferred behavior: the Monday cockpit presents the generated plan as the primary decision and persists Todd's acceptance through the existing `weekly_plan` confirmation API.
   - If product policy is automatic adoption, promote the generated Monday plan atomically and make revisions a separate explicit flow.
   - Do not silently infer automatic adoption without an agreed product rule.

2. Add a stale-week execution gate.
   - On Tuesday through Friday, if `weekly_plan.json.week_of` is not the current Monday and a current-week draft exists, mark the briefing pipeline degraded or blocked.
   - Do not score or rank daily work against the prior week's plan.
   - Surface one actionable cockpit affordance: adopt current plan, revise it, or explicitly carry forward the prior plan.

3. Make plan confirmation update draft state consistently.
   - After promotion, either mark `weekly_plan_draft.json.status` as `confirmed` with `confirmed_at`, archive it, or otherwise guarantee it cannot continue appearing pending.
   - Keep the adopted plan and audit history traceable.

4. Add dependency-aware render invalidation.
   - A change to `weekly_plan.json`, weekly-plan confirmation state, or `daily_brief` cache must invalidate today's Daily Brief render.
   - The pipeline should not require an operator to know about `render_daily_brief.py --force`.
   - Prefer content hashes/version identifiers over modification-time-only checks.

5. Add semantic post-build verification.
   - Verify the active plan week in `weekly_plan.json`, `system/.cache/daily_brief.json`, the rendered Daily Brief, published `brief.json`, and the `getDailyBrief` payload all match.
   - Fail the run if a current-week plan is active but any user-facing artifact names a prior week or shows a pending alert for that same adopted week.

6. Ensure corrected same-day artifacts can be republished safely.
   - Recompute, rerender, and republish dated/latest artifacts after adoption.
   - Preserve email idempotency unless a deliberate corrected-brief resend is requested.

## Acceptance Criteria

- On Monday, a plan for the current week is either active or presented as a blocking cockpit decision; the system cannot quietly proceed with last week's plan.
- On Tuesday through Friday, `weekly_plan.json.week_of` equals the Monday of the current operating week before recommendations are generated.
- Confirming a draft through CLI or API causes the draft to stop presenting as pending.
- After confirmation, one normal pipeline command regenerates every dependent artifact; no manual `--force` flag is required.
- A test that pre-creates today's rendered brief, changes the active weekly plan, and reruns the pipeline proves that the rendered and published outputs change.
- A consistency test verifies the same plan week and outcome IDs across:
  - `system/weekly_plan.json`
  - `system/.cache/daily_brief.json`
  - `system/briefs/YYYY-MM-DD-daily-brief.md`
  - `system/published/daily/YYYY-MM-DD/brief.json`
  - live `getDailyBrief`
- The pipeline exits nonzero or explicitly degraded when it detects a prior-week active plan alongside a current-week pending draft on an execution day.

## Regression Tests Requested

1. `test_monday_weekly_plan_adoption_contract`
2. `test_execution_day_rejects_prior_week_plan_when_current_draft_exists`
3. `test_confirm_weekly_plan_clears_pending_draft_state`
4. `test_weekly_plan_change_invalidates_existing_daily_render`
5. `test_brief_only_pipeline_rebuilds_after_weekly_plan_confirmation`
6. `test_weekly_plan_version_consistent_across_cache_render_publish_and_api`

## Files Most Likely Involved

- `system/scripts/morning_pipeline.py`
- `system/scripts/weekly_plan_generator.py`
- `system/scripts/weekly_planning.py`
- `system/scripts/daily_brief.py`
- `system/scripts/render_daily_brief.py`
- `system/scripts/publish.py`
- `system/api/server.py`
- `system/tests/test_weekly_plan_generator.py`
- `system/tests/test_weekly_plan_draft_alert.py`
- `system/tests/test_weekly_plan_summary_live_status.py`

## Priority Guidance

Treat this as a trust and operating-control defect, not a cosmetic rendering issue. The Daily Brief can look healthy while recommending work against the wrong weekly strategy. The fix is complete only when adoption, recomputation, rendering, publication, and cockpit retrieval share one verifiable weekly-plan version.
