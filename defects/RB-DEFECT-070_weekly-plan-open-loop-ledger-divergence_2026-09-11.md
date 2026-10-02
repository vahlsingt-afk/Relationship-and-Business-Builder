# RB-DEFECT-070 — Weekly plan and open-loop ledger can diverge without detection

**Captured for:** Claude  
**Date:** 2026-09-11  
**Status:** Open — architecture and implementation fix required  
**Severity:** High  
**Area:** Weekly planning, loop lifecycle, cockpit context, Chief of Staff execution integrity

## User requirement

Todd's requirement is explicit:

> There has to be a closer tie to the weekly plan and the open loop ledger — they cannot exist in a vacuum from each other.

The weekly plan is supposed to define the week's outcomes. The loop ledger is supposed to preserve the concrete follow-ups and commitments required to achieve them. They are currently separate artifacts with partial, optional cross-references, so either can appear internally valid while omitting work present in the other.

This is not a display or wording issue. It is a broken execution-control contract.

## User-visible failure observed on 2026-09-11

Todd asked to review and update his open loops. The live loop ledger initially returned eight open loops. After reviewing those loops, Todd correctly suspected that they did not represent everything in the active weekly plan.

Live reconciliation confirmed the concern. The active plan for the week of 2026-09-07 contained five outcomes:

1. McDonald's / Josh Wesolowski collateral and late-September meeting motion.
2. Del Taco Account Plan and opportunity assessment.
3. Pollo Campero RFP post-submission advancement.
4. A four-account Worldpay background-brief tranche: GoTo Foods, Subway, Steak 'n Shake, and Choice Hotels.
5. RB tools and loop hygiene, including the self-audit, Ryan/Five Guys decision, and IKEA.

Only portions of outcomes 1 and 5 were represented by linked open loops. Outcomes 2, 3, and 4 had no corresponding open-loop representation, despite containing unfinished work.

The live systems therefore produced materially different execution pictures:

- The loop ledger implied that reviewing its open items covered Todd's commitments.
- The weekly plan contained additional unfinished commitments that the ledger could not reveal.
- The weekly plan was stale while the loop ledger was fresh.
- Neither system raised a blocking inconsistency or identified the missing links.

## Verified missing work

The reconciliation found real unfinished weekly-plan work absent from the open-loop ledger:

- **Del Taco:** the live API returned `No Account Plan exists yet for 'del-taco'`, despite the weekly outcome requiring a completed Account Plan and clear go/no-go recommendation.
- **Pollo Campero:** the RFP submission was recorded, but receipt/evaluation timing, Amy's payments-progress check, and a dated post-submission checkpoint were not recorded as completed.
- **Worldpay background briefs:** GoTo Foods and Steak 'n Shake were present; Subway and Choice Hotels were missing. The four-account outcome was therefore only half complete.

This work would have been missed if Todd had accepted the open-loop ledger as the complete weekly execution list.

## Related but distinct defects

- **RB-DEFECT-021** created the weekly planning layer and proposed cross-references to loop and opportunity IDs.
- **RB-DEFECT-067** addresses stale weekly-plan adoption and stale Daily Brief rendering.

This defect is narrower and still unresolved: even when a weekly plan exists and its rendering is current, there is no enforced lifecycle relationship between weekly outcomes and executable loops.

## Root-cause hypothesis

The data model treats linkage as descriptive metadata instead of a required integrity constraint.

Likely contributing causes:

1. A weekly outcome may be confirmed without either:
   - linking one or more existing loops; or
   - atomically proposing the new loops required to execute it.
2. A loop can be created, closed, or re-dated without reconciling the status and progress of its linked weekly outcome.
3. Weekly outcomes can contain compound success criteria, while progress is inferred from narrative text rather than explicit child commitments.
4. `getLoops` and `getCockpitContext` expose separate views but do not calculate or block on referential inconsistencies.
5. There is no freshness/version contract proving that the active plan and loop ledger were reconciled after either one changed.
6. The live API provides loop closure and re-dating operations but no clearly exposed review-first operation for creating a missing loop directly from an approved weekly-plan outcome.

## Required operating contract

The weekly plan and loop ledger should remain separate views of the same execution graph, not duplicate narratives.

### Weekly outcome → loop contract

Every active weekly outcome with unfinished, externally observable work must have at least one linked executable loop or an explicit structured exemption.

Valid exemptions should be narrow and machine-readable, for example:

- `completed_at_plan_confirmation`
- `monitoring_only`
- `calendar_event_only`
- `no_follow_up_required`

Free-text absence of a loop is not an exemption.

When a weekly plan is proposed or confirmed, RB should show:

- existing loops it will link;
- missing loops it proposes creating;
- compound success criteria that need separate child loops;
- duplicate or legacy loops it proposes superseding.

Confirmation should atomically persist the plan, links, and approved loop mutations, or fail without leaving a partially synchronized state.

### Loop → weekly outcome contract

Every open business-execution loop should declare one of:

- `linked_outcome_id` for the current week;
- `parked_until` with no current-week allocation;
- `operational_obligation` for work that must remain visible outside strategic outcomes;
- `backlog` / `future_candidate` for work intentionally excluded from the current plan.

An unclassified open loop should be treated as a reconciliation warning, not silently mixed into or omitted from the weekly execution view.

### Lifecycle propagation

- Closing the final required loop should propose completion of the linked weekly outcome.
- Re-dating a required loop beyond the current week should flag the weekly outcome as at risk, deferred, or requiring replanning.
- Adding a new loop linked to an active outcome should immediately update its progress view.
- Revising or removing a weekly outcome should require an explicit disposition for linked open loops: retain, reclassify, re-date, or close.
- Compound outcomes should calculate progress from explicit required child commitments, not keyword matching against loop narratives.

## Required fix

1. **Introduce an explicit bidirectional execution graph.**
   - Give every weekly outcome and loop stable IDs and structured linkage.
   - Support one-to-many and, where justified, many-to-one relationships.
   - Store linkage in one canonical location or enforce atomic mirrored writes; do not allow independent manual copies to drift.

2. **Add plan-confirmation reconciliation.**
   - Before confirming a weekly plan, compare every proposed outcome against open loops.
   - Present link/create/supersede/exempt proposals for review.
   - Do not activate a plan containing unexplained unfinished outcomes with zero executable commitments.

3. **Add loop-mutation reconciliation.**
   - `closeLoop`, `redateLoop`, and any loop-creation operation must return the resulting impact on linked weekly outcomes.
   - If a mutation makes an outcome impossible within the week, require or propose an outcome-status update.

4. **Expose a review-first loop creation path.**
   - Add a narrow API operation that can create a loop from an approved weekly outcome with `outcome_id`, owner, due date, closure evidence, and source provenance.
   - Preserve RB's review-first rule; weekly narrative must not silently become a canonical loop.

5. **Create a consistency verifier and make it operational.**
   - Extend or replace `verify_weekly_plan_consistency.py` to detect missing links, dangling IDs, unexplained open loops, overdue child commitments, current-week outcomes whose required loops were pushed past Friday, and completed outcomes with open required loops.
   - Run it after plan confirmation, after every loop mutation, during the morning pipeline, and before returning cockpit context.
   - A material inconsistency should produce a degraded or blocking state, not an informational footnote.

6. **Return one reconciled execution view.**
   - `getCockpitContext` should return weekly outcomes with child loops, calculated progress, freshness, and exceptions.
   - `getLoops` should optionally group or annotate loops by weekly outcome and clearly separate parked, operational, backlog, and unclassified items.
   - The weekly review should use the same graph to distinguish completed, carried forward, deferred, and dropped work.

7. **Add freshness and version integrity.**
   - Persist a reconciliation timestamp and version/hash of both the active weekly plan and loop ledger.
   - Any change to either invalidates reconciliation until the consistency pass succeeds.

## Acceptance criteria

- Confirming a plan with an unfinished Del Taco-style outcome and no linked loop is rejected or presents a loop-creation/exemption decision before activation.
- A compound four-account brief outcome produces explicit required child commitments for all four accounts; completing two reports progress as 2/4 and leaves the other two visible in the open execution view.
- Closing the final required child loop proposes or performs the permitted weekly-outcome completion transition.
- Re-dating the Five Guys loop into the following week immediately marks its current-week outcome deferred/at risk and prevents the current weekly plan from presenting it as on-track.
- Parked items such as IKEA or Jeff Coffland remain queryable but do not masquerade as active current-week work.
- Legacy loops superseded by a current Master Account Plan can be closed with traceable supersession evidence without leaving dangling weekly-plan links.
- `getCockpitContext` and `getLoops` cannot disagree silently about the set of unfinished current-week commitments.
- The consistency verifier reports zero unexplained active outcomes, zero dangling links, and zero unclassified open loops before the cockpit reports the weekly execution state as healthy.
- All plan/loop mutations are review-first and return real mutation receipts; no free-text narrative is auto-promoted into canonical commitments.

## Regression tests requested

1. `test_weekly_plan_confirmation_requires_loop_or_exemption`
2. `test_compound_outcome_creates_or_links_all_required_child_loops`
3. `test_loop_close_updates_linked_outcome_progress`
4. `test_loop_redate_beyond_week_flags_outcome_for_replanning`
5. `test_plan_revision_requires_disposition_of_linked_open_loops`
6. `test_get_cockpit_context_and_get_loops_share_execution_graph`
7. `test_parked_and_backlog_loops_do_not_count_as_current_week_execution`
8. `test_reconciliation_invalidated_when_plan_or_ledger_changes`
9. `test_dangling_and_unclassified_links_block_healthy_status`
10. `test_weekly_review_uses_child_loop_completion_evidence`

## Files likely involved

- `system/weekly_plan.json`
- `system/eolms/loops.json`
- `system/eolms/loops.schema.json`
- `system/scripts/weekly_planning.py`
- `system/scripts/weekly_plan_generator.py`
- `system/scripts/loop_reconciliation.py`
- `system/scripts/verify_weekly_plan_consistency.py`
- `system/scripts/loop_autopilot.py`
- `system/api/server.py`
- `system/api/rbb_chat_tools.py`
- weekly-plan, loop-reconciliation, cockpit-context, and API mutation tests

## Priority guidance for Claude

Treat this as a Chief of Staff trust defect. Do not solve it by copying weekly-plan prose into the loop ledger, adding another warning banner, or merely rendering both lists on the same screen. The required fix is referential integrity plus lifecycle reconciliation: one execution graph, two useful views, and no silent divergence.
