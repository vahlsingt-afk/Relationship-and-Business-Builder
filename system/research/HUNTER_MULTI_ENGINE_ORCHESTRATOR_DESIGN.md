# Hunter Multi-Engine Orchestrator & Capacity Manager — Design

Status: **phases 0–2 implemented** (2026-10-06). Config: `research/hunter_orchestrator_config.json`. Module: `scripts/hunter_orchestrator.py`. Tests: `tests/test_hunter_orchestrator.py` (22 passing). Policy revised to admit ChatGPT Work and Claude Co-Work per Todd's decision. Only ChatGPT Deep Research is enabled; Work and Claude stay disabled until their capacity signals are recorded and verified. Phases 3–6 are not started.
Scope: RBB (Relationship & Business Builder). Governing documents: `system/research/HUNTER.md`, `system/research/hunter_resource_policy.json`, `system/schemas/hunter_research_packet.schema.json`.

## 0. Governance gate (read first)

The current checked-in policy makes ChatGPT Deep Research the **sole** Hunter research engine:

- `hunter_resource_policy.json`: Codex or Work limits "do not authorize a substitute Hunter research cycle."
- `HUNTER.md` (research engine section): Deep Research is the only research engine; Codex is local preparation and intake only.
- `scripts/hunter.py:76`: `"Codex is not a Hunter research engine"`.

This design proposes adding ChatGPT Work and Claude Research/Cowork as additional execution engines. That contradicts the current policy. Therefore:

- Every engine-routing behavior in this design is **default-off** behind `engines.enabled` (Section 5), which ships with only `chatgpt_deep_research` enabled.
- Enabling any other engine requires an explicit, documented policy revision to `hunter_resource_policy.json` and `HUNTER.md`, approved by Todd, before the flag is flipped.
- Phases 0–2 below are engine-agnostic and do not change which engine runs today, so they can proceed without that revision.

## 1. Current-state assessment

Reusable infrastructure already in the repo:

| Concern | Existing component | Notes |
|---|---|---|
| Ranked queue | `system/.cache/hunter_priority_queue.json`, `hunter_cycle.py prepare-priority` | Consumed exactly as ranked; must not be re-ranked. |
| Batching | `scripts/hunter_batch.py` (`prepare`, `report`) | Work units, subjobs, bundle statuses already exist. |
| Job files | `hunter_cycle.py prepare` / `prepare-priority` | Job files live in the pending jobs directory. |
| Return path | `scripts/hunter_drive_inbox_sync.py` → `hunter_cycle.py sweep` | Matches returned packets to pending jobs by content digest. |
| Finalize + validate | `hunter_cycle.py finalize`; `schemas/hunter_research_packet.schema.json`; payload schemas | Deterministic normalization; never invents evidence. |
| Playbooks | `research/hunter_playbooks.json` | Per-task structure and gap questions. |
| Gap manifests | `scripts/hunter_gap_manifest.py`, `schemas/hunter_gap_manifest.schema.json` | Explicit gap IDs. |
| Gating | `scripts/hunter.py resource-plan`; `hunter_resource_policy.json` | Codex/Work gate exists but is labeled "not a research engine." |
| Tests | `system/tests/test_hunter_*.py` | Existing coverage for queue, sweep, packet normalization, batch. |

Gaps this design fills:

1. No job lease or idempotency layer. Duplicate prevention today is "skip targets already pending" against a snapshot.
2. No execution-state machine (queued / leased / running / completed / validation_failed / retryable / capacity_blocked / terminal_failed).
3. No engine-level telemetry; packets carry research content only.
4. No configurable reserve or reset-aware burn-down policy. The current gate is a fixed threshold table.
5. No engine-performance history by playbook and task type.

## 2. Proposed architecture

```
Hunter priority queue (CoS-ranked, authoritative)
  ↓
Dispatcher  (new: system/scripts/hunter_dispatch.py)
  ↓  reads capacity snapshots + reserve config + engine enablement
Capacity / reset / reserve / task-fit decision
  ↓  creates or leases a job (deterministic job ID)
Engine adapter:  chatgpt_deep_research | chatgpt_work* | claude_research*   (* default-off)
  ↓  identical assignment payload for every engine
Common Hunter validator  (existing finalize + schema + payload validation)
  ↓  accept | repair | reject
Accepted Hunter packet  →  existing ingest / promotion workflow (unchanged)
```

Invariants:

- The assignment (target key, assignment ID, playbook, gap questions, directives, schema version, public-source rules, evidence/citation/ledger requirements, atomic-finding and confidence method, canonical-mutation restrictions) is generated **once** by existing code. Engine adapters transport it; they never edit it.
- Engine output never writes canonical RBB state. Only the existing validated ingest path does.
- Validation failures are rejected or repaired by the existing finalize step. The schema is never loosened for a particular engine.
- A quota or capacity failure releases the lease and re-queues the job. It never drops the job and never downgrades requirements.

Priority: the CoS rank is authoritative. Burn-down mode raises throughput on the top eligible items; it never selects lower-ranked targets while higher-ranked eligible work is queued.

## 3. Files and components

New:

- `system/scripts/hunter_dispatch.py`: dispatcher, lease manager, routing decision, CLI (`plan`, `lease`, `release`, `complete`, `status`).
- `system/scripts/hunter_capacity.py`: capacity snapshot readers and reserve calculation. Pure functions; no network calls.
- `system/scripts/hunter_telemetry.py`: append-only engine telemetry writer.
- `system/research/hunter_orchestrator_config.json`: reserves, burn-down schedule, engine enablement (Section 5).
- `system/schemas/hunter_job_lease.schema.json`: lease record.
- `system/schemas/hunter_engine_telemetry.schema.json`: telemetry record.
- `system/.cache/hunter_jobs.jsonl` (state, gitignored) and `system/.cache/hunter_engine_telemetry.jsonl` (append-only, gitignored).
- `system/tests/test_hunter_dispatch.py`, `test_hunter_capacity.py`, `test_hunter_lease.py`, `test_hunter_telemetry.py`.

Modified (additive only):

- `scripts/hunter_cycle.py`: `prepare`/`prepare-priority` writes the deterministic job ID and an initial `queued` state. `sweep` and `finalize` record completion and validation outcome into the lease ledger. Existing CLI behavior and file formats unchanged.
- `scripts/hunter_batch.py`: report includes lease/state counts. Existing fields unchanged.
- `scripts/hunter.py resource-plan`: reads the new config for the Deep Research path; the Codex/Work gate is kept as-is and marked as the blocked path until policy revision.
- `research/hunter_resource_policy.json`: **not modified in phases 0–2.** Revision is a separate, approved change (Section 0).

Not touched: canonical stores, `CANONICAL_REGISTRY.yaml`, mutation policy, promotion writers, the Drive inbox sync logic (beyond reading its output), and the packet schema.

## 4. Capacity-routing state machine

Job states:

```
queued ──lease──▶ leased ──start──▶ running ──returned──▶ returned
  ▲                 │                  │                    │
  │            lease expiry        timeout/fail        finalize+validate
  │                 ▼                  ▼                 ├─ pass ──▶ completed ──▶ accepted (downstream)
  └──── released ◀──┘           retryable ─┐               ├─ fail ──▶ validation_failed ─┐
  ▲                                ▲      │               │                              │
  │                                └──────┘ (attempts<max)│                              │
  └───────── capacity_blocked ◀── quota/limit rejection ──┘                              │
                                                                                         ▼
                                                              terminal_failed (attempts ≥ max) 
```

Rules:

- `leased` expires after a configurable TTL; expiry returns the job to `queued`.
- `capacity_blocked` returns to `queued` with a `not_before` time derived from the pool's reset window. It is not a failure and does not increment `attempts`.
- `validation_failed` is retryable with repair only if the finalize step can repair it deterministically. Otherwise it becomes `retryable` with the validation errors attached, up to `max_attempts`.
- `terminal_failed` requires Todd's review. It is never silently re-queued.
- `completed` means the packet passed validation. It is **not** "research completed" in any user-facing claim until downstream acceptance is recorded.

Dispatch decision per queued job, in order:

1. Skip if an active lease or a `completed` record exists for the same job ID.
2. Skip if no enabled engine is eligible for the playbook (task-fit config).
3. For each eligible engine in preference order, check its capacity state. Skip an engine whose pool is at or below its current reserve floor.
4. Lease the highest-ranked eligible job to the first engine that passes. Record capacity state at dispatch.

## 5. Configuration

`system/research/hunter_orchestrator_config.json` (example shape, values are placeholders to be set by Todd):

```json
{
  "schema": "rb.hunter_orchestrator_config.v1",
  "engines": {
    "chatgpt_deep_research": {"enabled": true,  "preference": 1, "pool": "deep_research", "capacity_source": "local_ledger"},
    "chatgpt_work":          {"enabled": false, "preference": 2, "pool": "chatgpt_work",   "capacity_source": "usage_settings_manual"},
    "claude_research":       {"enabled": false, "preference": 3, "pool": "claude_research", "capacity_source": "unverified"}
  },
  "reserves": {
    "chatgpt_work": {
      "by_reset_window": [
        {"days_until_reset_min": 4, "reserve_pct": 55},
        {"days_until_reset_min": 1, "days_until_reset_max": 4, "reserve_pct": 30},
        {"days_until_reset_max": 1, "reserve_pct": 10}
      ],
      "short_window_max_share_pct": 50,
      "after_hours_window": {"start": "18:00", "end": "07:00", "local_tz": "America/New_York"},
      "emergency_reserve_pct": 5
    }
  },
  "lease": {"ttl_minutes": 240, "max_attempts": 3},
  "task_fit": {"default": ["chatgpt_deep_research"]}
}
```

Behavior:

- Reserves are read from config. Nothing is hard-coded in the dispatcher.
- Weekly and short-window limits are both checked. A short-window allowance is never drained below its reserve. Unused weekly capacity inside the final-day window is released to Hunter in proportion to its reserve schedule.
- `chatgpt_deep_research` uses only the local execution ledger. Remaining allowance is reported as **unknown** unless a real observable signal exists. No count is inferred.
- Engines with `capacity_source: "unverified"` cannot be enabled until their capacity signal is verified.

## 6. Cross-engine leasing and idempotency

- **Job ID:** deterministic hash of `(assignment_id, target_key, playbook_id, schema_version, gap_manifest_digest)`. The same assignment yields the same ID on any engine.
- **Leases** are stored in `hunter_jobs.jsonl` as append-only events. Current state is derived from the latest event per job ID, under an advisory file lock.
- **Corroboration exception:** an assignment that explicitly requests independent corroboration can carry `allow_parallel: true`. Only then may a second engine lease the same job ID, and the two results are kept as separate attempts.
- **Release** happens on failure, timeout, lease expiry, explicit operator release, or `capacity_blocked`. A returned packet for a job that is already `completed` is recorded as a duplicate and not re-ingested.
- The existing content-digest dedupe in the Drive inbox sync remains in place as a second guard.

## 7. ChatGPT worker design

Deep Research (enabled in phase 1):

- Dispatcher issues the assignment through the existing `prepare`/`prepare-priority` output and the existing Drive transport. The human-triggered Deep Research step is unchanged.
- Local execution ledger records: dispatch time, job ID, attempt number, and the Deep Research session usage (counted by RBB, not read from the UI).
- Quota or refusal signals that appear in the returned response or the operator's manual note are recorded as `capacity_blocked` only if they are explicit. Otherwise the job is left `running` until the lease TTL, then returned to `queued`.

ChatGPT Work (phase 4, requires policy revision):

- Capacity source: manual snapshot of Settings → Usage (5-hour and weekly remaining %, reset times) recorded into a local file by the operator, or a verified automation output. The existing "RBB Hunter Capacity Watch" automation is outside this repo and is not trusted as a source until its output format and reliability are verified and documented.
- Execution mechanism: the same assignment file and transport; Work returns the same packet format.

## 8. Claude / Cowork worker design

Status: **unverified.** No supported mechanism has been confirmed for:

- reading Claude's live Usage / session / weekly state, or
- dispatching a job into Cowork and receiving the packet back through a supported path.

Design constraint: no UI scraping. Phase 5 begins only after a supported capacity and dispatch mechanism is documented. Until then, the Claude engine stays `enabled: false` and the dispatcher skips it.

If supported, the worker follows the same contract as the ChatGPT worker: lease → run → return packet → common validator.

## 9. Common validation path

Every engine's output runs through the same sequence:

1. Deterministic envelope fields supplied by the job (`hunter_cycle.py finalize`).
2. Stable-ID and harmless structural-alias normalization (existing).
3. JSON schema validation against `hunter_research_packet.schema.json`.
4. Payload validation against the cycle's payload schema.
5. Existing identity, date, scope, and source-access audit (`hunter_cycle_audit.py`, `hunter_citation_verify.py`).

Outcome: `accept`, `repair` (only deterministic, non-evidentiary normalization), or `reject`. Nothing engine-specific bypasses this. Engine identity is recorded as metadata, never as a validation input that relaxes a requirement.

## 10. Engine-performance telemetry

Stored in `hunter_engine_telemetry.jsonl`, separate from the packet:

- `job_id`, `attempt`, `engine`, `mode`, `model` (where observable)
- `dispatched_at`, `started_at`, `ended_at`, `duration_s`
- `status` (from the state machine), `retry_count`
- `capacity_state_at_dispatch` (pool, remaining %, reserve floor, reset time; or `unknown`)
- `capacity_consumed_estimate` and `capacity_consumed_observed` (only when observable)
- `sources_discovered`, `findings_generated`
- `validation_result`, `validation_errors`, `schema_compliant`
- `unresolved_gaps_count`
- `downstream_outcome` (accepted / rejected / superseded), recorded later by ingest

Derived reports (phase 6): acceptance rate, validation-failure rate, and unresolved-gap rate by `engine × playbook × task_type`. These feed the eventual task-fit routing. No routing change is made from telemetry until sample sizes are reviewed and Todd approves a rule.

## 11. Failure and recovery

| Failure | Behavior |
|---|---|
| Quota/limit rejection | `capacity_blocked`, release, `not_before` = reset window. Job kept. |
| Lease expires (no return) | Back to `queued`; attempt counted. |
| Validation fails, not repairable | `validation_failed` → `retryable` with errors, up to `max_attempts`. |
| Attempts exhausted | `terminal_failed`; surfaced for Todd. Not silently re-queued. |
| Duplicate packet for completed job | Recorded as duplicate; not re-ingested. |
| Dispatcher crash mid-write | Append-only log; state rebuilt from last complete event. Partial lines ignored and reported. |
| Engine disabled mid-lease | Lease released; job re-queued for an enabled engine. |
| Config missing or invalid | Dispatcher refuses to dispatch any engine other than Deep Research; logs the error. |

Never: lose a job to quota, silently lower the research requirement, report "research completed" on an execution attempt alone, or let an engine result mutate canonical state.

## 12. Testing plan

Unit:

- Job ID determinism across engines and reruns.
- State transitions, including illegal transitions rejected.
- Lease expiry, release, and duplicate-completion handling.
- Reserve calculation at each reset-window boundary and after-hours window; weekly and short-window independence.
- `capacity_blocked` does not increment attempts.
- Config validation: unknown engine, disabled engine, unverified capacity source.

Integration (fixtures, no live engines):

- Queue ordering preserved; a lower-ranked job is never leased while a higher-ranked eligible one is queued.
- Deep Research-only config reproduces current `prepare-priority` output byte-for-byte (backward compatibility).
- Sweep of a valid packet records `completed`; an invalid packet records `validation_failed` and does not alter the schema.
- Simulated quota rejection falls through to the next enabled engine without losing the job.

Regression:

- All existing `system/tests/test_hunter_*.py` pass unchanged.
- Packet format and schema unchanged; diff check on schema file must be empty.

Manual (Todd-run, after phase 2):

- One real Deep Research cycle through the dispatcher, confirming the ledger entry and that the packet ingests exactly as before.

## 13. Rollout phases and rollback

| Phase | Scope | Engines active | Policy change needed |
|---|---|---|---|
| 0 | Config and schemas, read-only plan command | none | no |
| 1 | Lease table, job IDs, telemetry, Deep Research via dispatcher | Deep Research | no |
| 2 | Burn-down reserve logic for local-ledger-only pools; dry-run reports | Deep Research | no |
| 3 | ChatGPT Work manual-snapshot capacity source, dry-run only | Deep Research | **yes** |
| 4 | ChatGPT Work live dispatch | + Work | **yes** |
| 5 | Claude worker, after verified mechanism | + Claude | **yes** |
| 6 | Telemetry-informed task-fit routing | per config | Todd approval per rule |

Rollback:

- Set `engines.*.enabled` to `false` except `chatgpt_deep_research`. Dispatcher reverts to the current Deep Research path.
- The new scripts are additive. Removing the dispatcher and reverting the additive edits to `hunter_cycle.py`, `hunter_batch.py`, and `hunter.py` restores the prior behavior.
- Job and telemetry logs are kept for audit; they are not required for the existing pipeline.

## 14. Limitations that prevent full automation

- **ChatGPT Deep Research allowance** cannot be read reliably from the UI. Remaining capacity is tracked as unknown except for RBB's own ledger.
- **Deep Research execution** still depends on a human-triggered step in ChatGPT and on the Drive return path. Full automation is not possible on the current transport.
- **ChatGPT Work usage** is visible only in Settings → Usage. Without a verified export, the capacity signal is manual.
- **Capacity Watch automation** lives in ChatGPT, outside this repo. It cannot dispatch work or read Claude's state.
- **Claude Research/Cowork**: no verified capacity read or dispatch mechanism. Automation depends on a supported mechanism being found; UI scraping is excluded.
- **Policy**: multi-engine routing is blocked by the current Deep-Research-only policy until it is revised and approved.

## 15. Decisions needed from Todd

1. Approve (or reject) revising `hunter_resource_policy.json` and `HUNTER.md` to admit ChatGPT Work as a research engine.
2. Confirm the reserve starting values in Section 5.
3. Confirm the after-hours window and time zone.
4. Confirm that the Capacity Watch output will be treated as advisory until its format is documented.
5. Approve starting phase 0–2, which do not change the current engine.
