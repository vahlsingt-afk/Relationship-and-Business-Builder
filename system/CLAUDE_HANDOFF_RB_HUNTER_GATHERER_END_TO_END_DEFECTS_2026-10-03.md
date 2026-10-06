# Claude Handoff — Hunter/Gatherer End-to-End Research Defects

**Date:** 2026-10-03  
**Priority:** High  
**Scope:** Hunter research transport/finalization, pending-job control, multi-target throughput, Gatherer morning-pipeline receipts  
**Do not increase production research volume until the acceptance gates below pass.**

## Executive summary

Hunter's internal contracts are substantially implemented and its focused test
suite passes, but the production ChatGPT Deep Research workflow is not completing
the full RBB lifecycle.

RBB can prepare valid Hunter jobs for restaurant brands, competitors, franchisee
discovery, and franchisee organizations. It can also prepare multiple targets in
one job when those targets share a playbook and payload schema. The current
production failure occurs after preparation: ChatGPT research may run, but its
result does not reliably arrive as a locally readable Hunter packet that can be
matched to the queued job, finalized, validated, and passed through Hunter's
governed dispatcher.

At the time of this audit:

- nine Hunter jobs were pending;
- `python3 system/scripts/hunter_cycle.py sweep` returned no processed or
  unmatched packets;
- no returned Hunter packet was present in `system/inbox/hunter_packets/`;
- ChatGPT conversations existed for KFC, Pizza Hut, McDonald's franchisee
  discovery, Tim Hortons, and other targets;
- at least one ChatGPT conversation claimed that a KFC JSON artifact had been
  saved under `/RBB/ChatGPT Intelligence Drop/`, but that artifact was not
  available to RBB's local sweep;
- several other conversations only started Deep Research or produced a report
  without a locally finalizable complete Hunter envelope.

The active hourly automation is consequently accumulating queued jobs rather
than completing research cycles. This is a transport and lifecycle-completion
failure, not evidence that Hunter's local preparation logic lacks capacity.

Gatherer has a separate receipt/wiring defect: the packet produced during the
morning pipeline reported zero tracked entities and zero successful source
checks while simultaneously reporting hundreds of inputs and dozens of detected
changes. A direct Gatherer rerun produced plausible coverage. This suggests that
the morning-pipeline invocation or its supplied assessment/health metadata can
produce a valid-schema but materially inconsistent receipt.

## What was verified

### Adoption and tests

The following commands passed on 2026-10-03:

```bash
python3 system/scripts/hunter_cycle_audit.py

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  system/tests/test_hunter_contract.py \
  system/tests/test_hunter_runtime.py \
  system/tests/test_hunter_gap_manifest.py \
  system/tests/test_hunter_source_registry.py \
  system/tests/test_hunter_change_dispatch.py \
  system/tests/test_hunter_improvements.py \
  system/tests/test_hunter_cycle_audit.py \
  system/tests/test_gatherer.py \
  system/tests/test_hunter_cycle_queue_sweep.py \
  system/tests/test_hunter_research_priority_queue.py \
  system/tests/test_import_franchisee_research.py \
  system/tests/test_import_brand_company_profile_research.py -q
```

Result: `112 passed` and the Hunter adoption audit returned `valid: true`.

### Multi-target preparation

All three of these dry preparation checks succeeded:

```bash
python3 system/scripts/hunter_cycle.py prepare enterprise_account_profile \
  --universe brands \
  --target company:brand-tim-hortons \
  --target company:brand-kfc \
  --target company:brand-pizza-hut \
  --output /tmp/hunter-brands-multi.json

python3 system/scripts/hunter_cycle.py prepare competitive_positioning \
  --universe competitors \
  --target competitor:revel-systems \
  --target competitor:restaurant365 \
  --target competitor:qu \
  --output /tmp/hunter-competitors-multi.json

python3 system/scripts/hunter_cycle.py prepare franchisee_organization_profile \
  --universe franchisees \
  --target franchisee:flynn-group \
  --target franchisee:kbp-brands \
  --target franchisee:sun-holdings \
  --output /tmp/hunter-franchisees-multi.json
```

Each generated a directive containing three exact target keys, current RBB
state, stable gap IDs, an appropriate registered payload schema, and a
hash-stamped before-state snapshot.

This proves the local intake layer can support multiple companies per cycle.
It does **not** prove that ChatGPT can return a valid multi-company packet or
that RBB can complete its finalization and dispatch.

## Defect 1 — ChatGPT results do not reliably enter the local Hunter intake

### Expected behavior

For every queued Hunter job:

1. ChatGPT Deep Research completes public-source research.
2. One evidence-native JSON response is captured as UTF-8 text.
3. The response is written under `system/inbox/hunter_packets/` or another
   explicitly supported local intake directory.
4. `hunter_cycle.py sweep` matches it to the exact queued job.
5. `finalize` supplies job-owned envelope fields, validates the packet and
   payload, verifies citations and before-state deltas, and runs the governed
   dispatcher in dry-run mode.
6. The cycle produces a durable packet and finalization/dispatch receipt.

### Actual behavior

- ChatGPT research can start and sometimes complete.
- Returned material may remain in ChatGPT's Library or project storage.
- A claimed `/RBB/ChatGPT Intelligence Drop/...` save does not mean a file is
  present in this repository's `system/inbox/chatgpt_intelligence_drop/`.
- ChatGPT may produce a normal narrative report, a simplified JSON object, or
  only a Deep Research status response instead of the expected evidence-native
  response.
- The local sweep sees no packet and therefore cannot finalize anything.
- The pending job remains queued while later automation invocations queue more
  targets.

### Current evidence

Pending jobs observed during the audit included:

- `company:brand-tim-hortons`
- `company:brand-chipotle-mexican-grill`
- `franchise-discovery:brand-chipotle-mexican-grill`
- `company:brand-kfc`
- `franchise-discovery:brand-mcdonald-s`
- `company:brand-pizza-hut`
- `company:brand-starbucks`
- `franchisee:haza-group`
- `franchisee:the-dhanani-group`

The exact list may change after this report. Inspect
`system/.cache/hunter_pending_jobs/` rather than assuming this snapshot remains
current.

The active automation's memory records repeated local-file transfer blocks,
Deep Research sessions that completed without a local packet, unavailable
research sessions, browser-session ownership problems, and a locked Mac.

### Likely design issue

The workflow currently depends on an unreliable cross-surface handoff:

```text
local queued job
  -> browser/ChatGPT prompt
  -> asynchronous Deep Research result
  -> ChatGPT Library/project artifact or inline response
  -> Codex/browser extraction
  -> local repository file
  -> Hunter sweep/finalize
```

The first and last segments are deterministic. The asynchronous ChatGPT result
capture and conversion into a local file are not.

### Required fix

Implement one explicit, observable transport contract. At minimum:

- identify the exact completed ChatGPT conversation/research run associated
  with each queued job;
- wait for the final research result rather than treating the initial “started”
  acknowledgement as completion;
- read the completed result through a supported interface;
- save the returned UTF-8 response locally without asking ChatGPT to claim it
  saved into a repository path it cannot actually access;
- preserve the response verbatim as the transport artifact;
- run `sweep` immediately;
- retain exact validation errors and allow no more than one corrective ChatGPT
  response before stopping;
- mark or quarantine a job after repeated transport failure so it is not an
  indefinite pending item.

Do not weaken Hunter validation or manually manufacture missing evidence to make
a ChatGPT response pass.

## Defect 2 — The active automation accumulates pending work without backpressure

### Expected behavior

The scheduler should increase pending work only when the transport/finalization
path is draining completed packets. A bounded batch should have an explicit
maximum number of open jobs and should stop queueing when the threshold is
reached.

### Actual behavior

`RB Hunter Priority Queue Cycle` runs hourly and selects the first ranked target
without a pending job. When the browser handoff fails, the prepared job remains
pending. The next invocation can queue another target. This creates the
appearance of progress while the number of completed/finalized packets remains
zero.

### Required fix

- Add a hard pending-job ceiling before preparing new work.
- Recommended initial ceiling: three total pending jobs, or one pending job per
  enabled research family, whichever is smaller.
- Do not count `prepare` or “Deep Research started” as a completed cycle.
- Track explicit states such as `prepared`, `submitted`, `research_complete`,
  `response_captured`, `finalized`, `validation_failed`, and `transport_blocked`.
- Apply a retry/age policy to stale pending jobs.
- Report throughput using finalized packets, not queued targets or started
  ChatGPT conversations.

## Defect 3 — Production automation does not use the multi-target capability

### Expected behavior

After end-to-end reliability is proven, a single bounded Deep Research cycle
should be able to process two or three closely related companies when they use
the same playbook and payload schema.

### Actual behavior

The active hourly automation selects one queue entry and creates one single-
target job. Multi-target preparation exists but is not used in the production
cycle.

### Required design constraint

Batch only homogeneous targets:

- brands sharing `enterprise_account_profile`;
- competitors sharing `competitive_positioning` or another one playbook;
- franchisee organizations sharing `franchisee_organization_profile`;
- franchisee-discovery targets sharing `franchisee_discovery`.

Do not combine brands, competitors, and franchisees into one heterogeneous
packet with multiple incompatible payload schemas.

Batch grouping should also consider source overlap, ownership relationships,
and research-question similarity. A batch of Yum brands with closely related
franchise-disclosure questions is reasonable. Three unrelated companies with
different playbooks is not.

## Defect 4 — Priority ordering can starve competitor research

During this audit, the unified priority queue placed:

- restaurant brands at ranks 1–7;
- franchisee organizations/discovery at ranks 8–15;
- the first competitor at rank 16.

The first competitors included Revel Systems, Restaurant365, Qu, Toast, and
Nory, all with many open gaps. Strictly consuming one global queue can delay
competitor coverage for many cycles.

Preserve one CoS-ranked queue as the authoritative ordering, but add a bounded
coverage policy after transport is reliable. For example, a daily allocation
may reserve capacity for:

- restaurant brands;
- restaurant-technology competitors/vendors; and
- franchisee discovery or franchisee-organization profiles.

Do not silently rewrite the strategic-value model merely to force balance.
Make the coverage allocation explicit and auditable.

## Defect 5 — Gatherer morning-pipeline receipt can be internally inconsistent

### Observed morning packet

The cached Gatherer packet initially inspected on 2026-10-03 reported:

- `tracked_entities_total: 0`;
- `expected_sources: 24`;
- `successful_sources: 0`;
- all 24 sources listed as failed;
- `coverage_pct: 0.0`;
- `input_items: 370`;
- `changes_detected: 69`;
- `hunter_escalations: 14`;
- `input_status: ok`.

Those values cannot all accurately describe the same collection run. The packet
was schema-valid but its coverage receipt was materially misleading.

### Direct rerun

A direct run of:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 system/scripts/gatherer.py --json
```

produced:

- `tracked_entities_total: 1920`;
- `successful_sources: 22` of 24;
- `coverage_pct: 91.7`;
- two failed sources: Restaurant Business Online and SEC EDGAR recent 8-K;
- `input_items: 370`;
- `changes_detected: 67`;
- `hunter_escalations: 14`;
- valid schema and canonical comparison.

### Likely cause

Inspect the morning-pipeline call path and the assessment/source-health object
supplied to Gatherer. The direct Gatherer path can reconstruct valid coverage,
while the morning-pipeline path appears capable of passing incomplete or
misaligned source-check metadata alongside populated collected items.

### Required fix

- Make Gatherer derive or reconcile coverage from the same collection receipt
  that produced its input items.
- Reject or explicitly label a receipt inconsistent when `input_items > 0` and
  all configured sources are reported unsuccessful without corresponding input
  provenance explaining cached/prior input.
- Validate that `tracked_entities_total` is populated from the canonical
  universe whenever canonical comparison succeeds.
- Add a semantic consistency test; JSON Schema validity alone is insufficient.
- Preserve the distinction between fresh collection, cached input, prior-day
  replay, and partial collection.

## Acceptance gates before throughput is increased

Do not increase the automation frequency or target count until all gates pass.

### Gate A — One real restaurant-brand cycle

- Prepare one real brand job.
- Complete ChatGPT Deep Research.
- Capture the actual response locally.
- Sweep and finalize successfully.
- Verify the exact target, gap outcome, payload schema, citations, before-state
  comparison, and dispatcher dry-run receipt.

### Gate B — One real competitor cycle

Repeat Gate A with a competitor and `rb.competitor_platform_research.v1` or the
registered payload for the selected competitor playbook.

### Gate C — One real franchise cycle

Repeat Gate A with either:

- a `franchise-discovery:<brand>` target using
  `rb.franchisee_discovery.v1`; or
- a `franchisee:<organization>` target using
  `rb.franchisee_organization_profile.v1`.

### Gate D — One real homogeneous multi-company cycle

- Prepare two or three targets sharing one playbook and payload schema.
- Confirm every target appears in the exact-target list and gap outcomes.
- Confirm the response does not cross-attribute sources or findings.
- Finalize and produce one valid governed receipt covering the whole packet.

### Gate E — Gatherer semantic receipt test

- Run the morning pipeline through its normal Gatherer integration.
- Confirm its cached packet and embedded `phase_2_gatherer` agree on collection
  health, tracked-universe count, source checks, and input provenance.
- Confirm the semantic consistency test rejects the previously observed
  zero-coverage/populated-input contradiction.

## Ramp plan after all gates pass

Start conservatively:

1. two targets per homogeneous cycle;
2. at most three pending jobs globally;
3. no concurrent Deep Research batches unless the execution surface has a
   reliable independent job/result identity;
4. measure finalized packets, target completion, validation failures, and
   transport failures for at least one day;
5. increase to three targets per cycle only if cross-target evidence remains
   clean and finalization succeeds consistently.

The batch limit must remain bounded by Hunter's page/time budgets and ChatGPT's
observable behavior. Do not claim exact token metering when it is unavailable.

## Files to inspect

- `system/research/HUNTER.md`
- `system/prompts/hunter_research_bot.md`
- `system/research/hunter_playbooks.json`
- `system/research/hunter_payload_registry.json`
- `system/schemas/hunter_research_packet.schema.json`
- `system/scripts/hunter_cycle.py`
- `system/scripts/hunter_packet_normalize.py`
- `system/scripts/hunter_change_dispatch.py`
- `system/scripts/hunter_research_priority_queue.py`
- `system/research/GATHERER.md`
- `system/scripts/gatherer.py`
- `system/scripts/intelligence_assessment.py`
- `system/scripts/morning_pipeline.py`
- `system/tests/test_hunter_cycle_queue_sweep.py`
- `system/tests/test_gatherer.py`
- `/Users/toddvahlsing/.codex/automations/rb-hunter-90-unit-gap-cycle/automation.toml`
- `/Users/toddvahlsing/.codex/automations/rb-hunter-90-unit-gap-cycle/memory.md`

## Safety and governance constraints

- Do not bypass Hunter by writing research findings directly to canonical RBB
  records.
- Do not weaken citation, identity, date, scope, novelty, or payload validation.
- Do not invent missing authorization, entity IDs, source evidence, or packet
  fields.
- A ChatGPT report is not a completed RBB cycle until local finalization and a
  governed dispatch receipt exist.
- Keep dry-run dispatch as the default. Do not add `--confirm` to the research
  automation without a separate, explicit authorization decision.
- Preserve existing user and pipeline changes in the heavily modified working
  tree.

## Bottom line

Hunter's local control plane is ready for controlled end-to-end proving, and
its intake supports multiple companies. The production system is not yet ready
for higher daily volume because the ChatGPT-to-local response transport does
not reliably close the cycle, pending jobs lack sufficient backpressure, and
Gatherer's morning receipt can misstate collection coverage.

Fix transport first, prove one brand, one competitor, one franchise target, and
one homogeneous multi-company packet, then ramp gradually using finalized
packet throughput as the governing metric.
