# RB-DEFECT-072 — Brief acceptance gate detects repairable defects but aborts instead of self-healing and continuing delivery

**Captured for:** Claude  
**Date:** 2026-09-23  
**Status:** Open — diagnosis and implementation fix required  
**Severity:** High  
**Area:** Morning intelligence orchestration, brief acceptance, self-healing, delivery continuity

## User requirement

Todd asked why RB recognizes defects in the daily brief and intelligence brief but does not repair them, continue the intelligence cycle, and deliver the reports.

The intended behavior is not “generate once, fail a gate, send an alert, and stop.” RB is expected to act as an autonomous operating system: detect a repairable quality or state defect, apply a bounded deterministic repair, rebuild affected artifacts, re-run acceptance, and continue delivery when the repaired output passes.

## Verified 2026-09-23 behavior

The morning pipeline successfully completed the substantive work:

- public-intelligence collection passed;
- all hard-required sources were fresh;
- intelligence readiness was `READY`;
- 24 sources were assessed and accepted;
- 390 public-intelligence items were processed;
- canonical daily artifacts were published for 2026-09-23;
- the intelligence brief rendered successfully;
- the daily brief rendered successfully;
- weekly-plan consistency passed.

The pipeline then ran `brief_acceptance_gate`, which failed with two blocking findings:

1. `no_duplicate_story_clusters` detected two same-event headline pairs:
   - “Wingstop launches Game Day Punch Card, its second football promotion of the season” / “Wingstop Unveils ‘Game Day Punch Card’ for Rewards Members”
   - “Presto integrates with Toast to scale drive-thru voice AI” / “Toast aims to scale drive-thru AI”
2. `delivery_check` contained one automated failure. Its embedded reasons still described the latest brief artifact as dated 2026-09-22 and the launch agent as not loaded, even though the same run subsequently published the 2026-09-23 artifacts and the live post-run delivery check showed:
   - latest brief artifact: pass, dated 2026-09-23;
   - latest HTML artifact: pass;
   - launch agent loaded: pass;
   - live health API: HTTP 200.

Because the gate failed, `morning_pipeline.py` skipped both:

- `send_intelligence_brief_email`
- `send_daily_brief_email`

It ran only `send_failure_alert`, which returned `smtp_sent` to `vahlsingt@gmail.com`.

Both reports therefore existed, but neither was delivered. A repairable content defect and a stale/circular delivery-health assertion became a terminal pipeline failure.

## Core defect

The acceptance gate is implemented only as a terminal predicate. It can identify a defect but cannot classify it, repair it, rebuild the affected artifact, or retry acceptance.

The current control flow is effectively:

```text
collect -> assess -> render -> acceptance gate
                                | pass -> send both reports
                                | fail -> send failure alert and stop
```

Required control flow:

```text
collect -> assess -> render -> acceptance gate
                                | pass -> send both reports
                                | repairable fail
                                |   -> deterministic repair
                                |   -> rebuild affected artifact(s)
                                |   -> refresh post-build health evidence
                                |   -> rerun gate (bounded attempts)
                                |   -> pass -> send both reports
                                | unrecoverable or retry exhausted
                                |   -> preserve artifacts, send precise alert,
                                |      and expose safe recovery action
```

## Specific design failures

### 1. Duplicate-story detection has no correction path

`brief_acceptance_check.py` correctly recognizes differently worded headlines describing the same event, but returns only a failed finding. It does not emit a structured repair plan identifying which story should survive, which duplicate should be removed, or which section needs replenishment.

The renderer/orchestrator then has no mechanism to deduplicate by story cluster, retain the strongest canonical source, backfill the vacated section from the next eligible candidate, and rerender.

### 2. Delivery health is evaluated from stale or pre-repair state

The acceptance gate reads `report["delivery_check"]` from the generated report. On this run, that embedded snapshot still described yesterday's artifact and an unloaded scheduler even though today's artifact publication had already succeeded by the time acceptance ran.

This creates a temporal/circular dependency:

- the gate requires delivery state to be healthy before allowing delivery;
- the checked report may contain delivery state captured before today's artifacts were published;
- publication can repair the condition, but the gate does not refresh the evidence;
- the stale failure blocks the send anyway.

Acceptance must use post-publication, current-run evidence for prerequisites. Checks that can only become true after sending must be post-send verification, not pre-send blockers.

### 3. One shared gate suppresses both deliverables without artifact-specific recovery

The intelligence and daily briefs both rendered successfully, but any blocking finding prevents both emails. The pipeline does not distinguish:

- a defect in the intelligence brief;
- a defect in the daily brief;
- a shared source/readiness defect;
- a scheduler or delivery infrastructure defect;
- an already-repaired stale diagnostic.

It should repair and revalidate the affected artifact. If one independent deliverable remains defective while the other passes its own contract, the policy for partial delivery should be explicit rather than accidental.

### 4. Failure alert substitutes for recovery

`send_failure_alert` is useful only after bounded recovery is attempted or when the defect is unsafe to repair automatically. Here the failures were deterministic and locally repairable, but no repair was attempted.

## Required durable fix

### A. Introduce structured acceptance findings

Every finding should return at least:

- `check`
- `artifact_scope` (`intelligence`, `daily`, `shared`, `delivery`)
- `failure_class` (`content_repairable`, `state_refreshable`, `infrastructure_retryable`, `unrecoverable`)
- `repair_action`
- `affected_item_ids` or stable story fingerprints
- `safe_to_auto_repair`
- `retry_budget`

Do not require the orchestrator to parse human-readable `detail` strings.

### B. Add a bounded repair-and-revalidate loop

For safe failures:

1. execute the registered repair;
2. rerender only the affected artifact(s), plus any dependent combined artifact;
3. republish canonical artifacts atomically;
4. recompute current-run delivery/readiness evidence;
5. rerun the acceptance gate;
6. deliver on pass.

Cap attempts per failure class and persist a receipt for each attempt. Detect no-progress loops using artifact hashes and finding fingerprints.

### C. Repair duplicate story clusters deterministically

When same-event duplicates are found:

1. cluster them before final rendering where possible;
2. choose one canonical story using explicit ranking such as directness, source authority, content completeness, recency, and relevance;
3. preserve useful corroborating URLs as supporting sources rather than separate headlines when appropriate;
4. remove the duplicate headline;
5. backfill the section from the next nonduplicate eligible candidate if section minimums matter;
6. rerun cross-section and story-cluster deduplication.

### D. Separate pre-send readiness from post-send verification

Pre-send checks may verify:

- current-run artifacts exist and carry today's date;
- required sources are ready;
- render and publication succeeded;
- email configuration is available;
- current live health is reachable.

Post-send checks should verify:

- each email send returned success;
- delivery receipts/logs were persisted;
- published/downloadable artifacts remain available.

Do not block today's send because an embedded snapshot captured before today's publication still points to yesterday.

### E. Recompute instead of trusting embedded stale diagnostics

The gate should receive or compute a current-run manifest containing timestamps, artifact hashes, and step receipts. It must reject diagnostic evidence older than the step whose success it is intended to validate.

The run manifest should make temporal ordering explicit:

- collected at
- rendered at
- published at
- acceptance checked at
- repaired at
- revalidated at
- sent at
- delivery verified at

### F. Preserve cycle continuity

A repairable report-quality finding must not discard already completed intelligence work or restart collection unnecessarily. Resume from the earliest affected stage. For today's defect, deduplication should restart at selection/rendering, not collection or assessment.

## Acceptance criteria

- Given the two duplicate story pairs observed on 2026-09-23, RB automatically selects one headline per event, backfills if required, rerenders, passes acceptance, and sends both reports without human intervention.
- A delivery snapshot older than the current publication step cannot block the current run.
- After publishing today's artifact, the gate recomputes artifact date and availability and sees 2026-09-23 rather than retaining 2026-09-22.
- The pipeline attempts safe repair before sending a failure alert.
- Repair retries are bounded and cannot loop indefinitely.
- A retry that makes no artifact or finding change terminates with an explicit no-progress reason.
- Intelligence collection and assessment are not rerun when only headline selection/rendering needs repair.
- Each repair attempt leaves a receipt naming the failed check, action taken, before/after artifact hash, and revalidation result.
- The intelligence and daily brief each have explicit acceptance scope; one artifact's local defect does not silently suppress the other without a documented policy decision.
- Pre-send readiness and post-send delivery verification are distinct phases.
- The final pipeline status distinguishes `passed_first_attempt`, `repaired_and_delivered`, `partially_delivered`, and `failed_after_repair_exhausted`.
- Failure alerts state what RB tried, what changed, why recovery stopped, and where the completed-but-undelivered artifacts can be accessed.

## Regression tests requested

1. `test_duplicate_story_gate_emits_structured_repair_plan`
2. `test_pipeline_deduplicates_rerenders_and_delivers`
3. `test_duplicate_removal_backfills_section_without_new_duplicate`
4. `test_repair_resumes_from_render_stage_not_collection`
5. `test_acceptance_refreshes_delivery_health_after_publication`
6. `test_stale_embedded_delivery_snapshot_cannot_block_current_artifact`
7. `test_pre_send_gate_does_not_require_post_send_receipt`
8. `test_repair_loop_is_bounded`
9. `test_repair_loop_stops_on_unchanged_artifact_hash`
10. `test_repair_receipt_records_before_and_after_finding`
11. `test_artifact_scoped_failure_policy_is_explicit`
12. `test_failure_alert_runs_only_after_recovery_exhausted_or_unsafe`
13. `test_pipeline_status_reports_repaired_and_delivered`

## Files likely involved

- `system/scripts/morning_pipeline.py`
- `system/scripts/brief_acceptance_check.py`
- `system/scripts/render_intelligence_brief.py`
- `system/scripts/render_daily_brief.py`
- `system/scripts/task_delivery_check.py`
- `system/scripts/send_brief_email.py`
- `system/scripts/send_failure_alert.py`
- report construction code that embeds `delivery_check`
- morning-pipeline and brief-acceptance tests

## Priority guidance for Claude

Treat this as an orchestration defect, not as a request to weaken or bypass the acceptance gate. The gate correctly prevented duplicate stories from being delivered; the defect is that RB had no governed recovery path after detecting a safe, deterministic problem and used temporally stale delivery evidence as a blocker.

Implement the repair state machine, fix the timing contract for delivery evidence, add focused tests, and replay the 2026-09-23 case end to end. The successful outcome is not merely “the gate passes”; it is a verified `repaired_and_delivered` run with both email receipts and an auditable repair record.
