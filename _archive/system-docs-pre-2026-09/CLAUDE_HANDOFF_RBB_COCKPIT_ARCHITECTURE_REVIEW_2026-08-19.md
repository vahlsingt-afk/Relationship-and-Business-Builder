# Claude Handoff — RBB Cockpit Architecture Review

**Date:** 2026-08-19  
**Requested owner:** Claude, as RBB architect  
**Implementation partner:** Codex  
**Status:** Architecture review requested; no further cockpit expansion should occur before review.

## Executive request

Review and either approve, revise, or reject the proposed authority registry and generated cockpit-context boundary before RBB is connected more deeply to its Custom GPT or a ChatGPT Project.

The work completed so far intentionally does **not** create a second RBB system, a replacement Custom GPT, a new account/opportunity database, or a new mutation path. It preserves the existing repository as the sole system of record and adds two reviewable architectural artifacts:

- `system/CANONICAL_REGISTRY.yaml` — proposed machine-readable authority map.
- `system/cockpit/context.json` — generated, read-only context projection.

## Why this review is needed

RBB already implements substantial cockpit behavior, but business state is unevenly canonicalized:

- Relationships have mature stores and an emerging event-first model.
- Accounts, opportunities, strategic theses, and decisions remain distributed across several artifact types.
- Open obligations use two identifier namespaces and stores: legacy `L-` records in `loop_ledger.md` and `EL-` records in EOLMS.
- Weekly-plan adoption can temporarily disagree with cached/rendered briefing projections (RB-DEFECT-067).
- The Custom GPT/ChatGPT project boundary needs a common retrieval and promotion contract without copying canonical state into chat-managed files.

Claude should determine whether the proposed boundary matches RBB's intended architecture and sequencing.

## Canonical repository and recovery state

Canonical workspace:

`/Users/toddvahlsing/Documents/Claude/Projects/Relationship & Business Builder`

The prior August 11–19 dirty state was captured losslessly before checkpointing:

`/private/tmp/rbb-phase0-snapshot-20260819/`

The recovery set contains:

- Full Git history bundle.
- Binary-capable tracked working-tree patch.
- Staged-state patch.
- Complete archive of all untracked files.
- Status and HEAD metadata.
- SHA-256 checksums.

No temporary or product-map working trees were deleted or normalized.

## Commits produced

| Commit | Purpose | Review posture |
|---|---|---|
| `efb43a0` | Add proposed canonical authority registry | Architectural review required |
| `0f85fa4` | Preserve August 12–19 implementation, tests, docs, and RB-DEFECT-067 | Explicit WIP; not represented as fully passing |
| `030d836` | Preserve canonical operational state through August 19 | Checkpoint of live state |
| `4ae5413` | Preserve generated briefs, campaign projections, deltas, and trace output | Generated projection checkpoint |
| `dc96732` | Add generated cockpit context, generator, and focused tests | Architectural review required |

## Current canonical state

As reported by the August 19 manifest:

- 3,083 baseline people.
- 20 recognized relationship cards.
- 1,761 interaction-brief files.
- 11 open legacy `L-` loops and 42 closed legacy loops.
- Seven circles.
- One active weekly outcome: resolve nine overdue loops.

The cockpit projection also sees 19 nonterminal EOLMS `EL-` records, producing 30 active/nonterminal obligations across both namespaces. This is not necessarily an error, but it proves that “open-loop count” is currently representation-dependent.

## Proposed authority model

`system/CANONICAL_REGISTRY.yaml` records ten domains:

1. Identity and relationships.
2. Accounts.
3. Opportunities.
4. Strategic theses.
5. Execution loops.
6. Priorities and planning.
7. Decisions.
8. Industry/ecosystem intelligence.
9. Evidence artifacts.
10. Audit, sessions, and receipts.

It deliberately marks accounts, opportunities, theses, and decisions as `distributed_consolidation_pending` rather than inventing a false authority.

For loops it declares the current operational split:

- `L-` authority: `system/loop_ledger.md`.
- `EL-` authority: `system/eolms/loops.json`.
- Long-term authority decision: unresolved.

## Generated cockpit context

`system/scripts/cockpit_context.py` generates `system/cockpit/context.json` atomically from declared canonical inputs.

The output contains:

- Repository revision and generation timestamp.
- Per-input modification time, age, stale-input list, and aggregate fingerprint.
- Weekly outcomes, risks, and forcing functions.
- Active strategic threads/opportunities.
- Legacy and EOLMS obligations with explicit authority labels.
- Active theses and evidence state.
- Recent material intelligence.
- Pending decisions.
- Reconciliation items, including stale active threads and governance gaps.
- The allowed persistence-receipt statuses.

It is generated-only and is not intended to become independently editable state.

## Validation status

Passed:

- Baseline, strategic-operator, and ecosystem schema validation.
- Canonical integrity checks.
- Weekly-plan consistency checks for local cache/rendered/published artifacts.
- Three focused cockpit-context tests.
- JSON validation of the generated context.

Not verified:

- Live `getDailyBrief` consistency because no API key was available during the audit.

Broader suite:

- 3,432 passed.
- 35 failed.
- Several failures were sandbox-only write restrictions, but others are real WIP code/test contract mismatches involving capture rendering, earnings copy/history, identity-confirmation rendering, triage IDs, newsletter deduplication, transcript normalization, watchlist rendering, and weekly-plan draft guidance.

`0f85fa4` is therefore explicitly a WIP checkpoint and should not be treated as a green production release.

## Decisions requested from Claude

### 1. Approve the cockpit boundary

Should `system/cockpit/context.json` remain the compact generated read model supplied to every RBB chat/Custom GPT session, with all writes routed elsewhere?

### 2. Confirm or revise domain authority

For each distributed domain, choose the intended canonical shape and migration order:

- Account registry.
- Opportunity registry.
- Strategic-thesis registry.
- Decision ledger.

### 3. Resolve loop authority

Choose one:

- Keep permanent `L-`/`EL-` namespace authority with a unified projection.
- Make EOLMS authoritative and migrate remaining legacy loops.
- Retain `loop_ledger.md` authority and treat EOLMS as a richer projection.

Also define how duplicate intent across namespaces is detected and reconciled.

### 4. Define Custom GPT versus ChatGPT Project roles

Confirm the recommended posture:

- Existing RBB Custom GPT remains the daily cockpit.
- A ChatGPT Project may group workstreams and hold compact operating instructions.
- Neither stores independently editable canonical business state.
- Both retrieve the generated context and submit findings through a shared promotion contract.

### 5. Approve the promotion lifecycle

Proposed receipt statuses:

- `not_persisted`
- `proposed_write_pending_confirmation`
- `persisted`
- `blocked_conflict`
- `skipped_duplicate`

Proposed transaction: snapshot → authoritative event/state mutation → regenerate projections → validate → read back → receipt.

### 6. Set the next implementation gate

Choose whether Codex should next:

- Fix the 35-test WIP boundary first.
- Add a read-only API endpoint for cockpit context.
- Add ChatGPT Project/Custom GPT operating instructions.
- Build the review-first promotion inbox.
- First consolidate one distributed business domain.

## Recommended architectural sequence

Unless Claude decides otherwise:

1. Review and amend `CANONICAL_REGISTRY.yaml`.
2. Resolve the loop authority policy.
3. Define the minimum canonical account/opportunity identities without migrating documents yet.
4. Fix the WIP test boundary.
5. Expose the cockpit context through the existing authenticated RBB API.
6. Update the existing Custom GPT instructions and optional ChatGPT Project instructions.
7. Implement a review-first promotion inbox reusing RI-event and mutation machinery.
8. Add cross-projection version enforcement and live API consistency checks.

## Guardrails until review is complete

- Do not create another RBB repository or business-state store.
- Do not create a replacement Custom GPT merely to house the new context.
- Do not copy canonical state into ChatGPT Project files for manual maintenance.
- Do not claim persistence without a write receipt.
- Do not consolidate or delete legacy evidence yet.
- Do not treat the 30 combined obligations as equivalent without reconciling intent across namespaces.
- Do not extend cockpit writes until authority and promotion policy are approved.

## Requested response format from Claude

Please return:

1. **Architecture verdict:** approve / approve with changes / reject.
2. **Required registry edits.**
3. **Loop authority decision.**
4. **Custom GPT and ChatGPT Project role decision.**
5. **Ordered implementation plan with explicit gates.**
6. **Any work Codex should revert or quarantine.**

