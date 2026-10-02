# Claude Handoff — RB 9.4 Passive RI Persistence MVP

**Date:** 2026-05-24  
**Status:** next sprint design handoff  
**Owner:** Claude architecture / Codex implementation loop  
**Primary objective:** make passive relationship intelligence durable, auditable, and visible as completed/proposed/blocked system action.

## Why This Sprint

RB now has a much stronger Chief of Staff daily brief: it begins with resource verification, separates provenance/freshness/grounding, and ends with action orchestration.

The next trust gap is persistence.

RB can detect relationship intelligence in passive inputs, but the system still too often stops at analysis, recommendation, or proposed language. A real Chief of Staff must remember what it detects and prove what it changed.

The sprint principle:

```text
Passive signal → RI event → projection or proposed mutation → daily brief proof → action-state language
```

Do not treat this as a new event-sourcing design from scratch. The event store, RI review/confirm endpoints, pre-processors, and smoke tests already exist. The sprint is about connecting passive source outputs to that spine and making the projection/result status visible.

## Product Doctrine

RB must distinguish:

- `recorded`: durable RI event written;
- `updated`: projection changed successfully;
- `proposed`: review-first mutation exists and awaits Todd;
- `blocked`: RB could not safely write because input/date/entity was insufficient;
- `unavailable`: source could not be accessed.

Allowed user-facing language:

- "RB recorded..."
- "RB updated..."
- "RB proposed..."
- "RB did not persist this yet..."
- "Blocked because..."

Avoid for completed system action:

- "should be marked"
- "could be updated"
- "would likely"

## Current State To Reuse

Read these first:

- `system/RB_9_0_STRATEGIC_DIRECTION_CHIEF_OF_STAFF_PLATFORM.md`
- `system/ARCHITECTURE.md`
- `system/CLAUDE_DEVELOPMENT_MAP.md`
- `system/protocols/P-021_ri_event_sourcing.md`
- `system/protocols/P-020_conversation_artifact_ingestion.md`
- `system/SCHEMAS.md`
- `system/RI_EVENT_INTAKE_DESIGN.md`

Key implementation files:

- `system/scripts/ri_events.py` — append-only event store. Do not replace.
- `system/scripts/ri_intake.py` — review/confirm dispatcher. Do not bypass.
- `system/scripts/ri_smoke_test.py` — end-to-end RI intake smoke.
- `system/scripts/manual_relationship_intake.py` — classifier/mutation proposal engine.
- `system/scripts/relationship_signals.py` — passive signal detection layer.
- `system/scripts/linkedin_messaging.py` — LinkedIn message overlay and RI event proposals.
- `system/scripts/daily_brief.py` — already surfaces RI event counts in the trust-first brief.
- `system/api/server.py` — already exposes `/ri_events/review`, `/ri_events/recent`, `/ri_events/{event_id}/confirm`.
- `system/api/custom_gpt_prompt.md` — action-state language contract.

## Known Current Capabilities

Already live or partially live:

- `ri_events.py` can validate, dedupe, append, load, and reindex RI events.
- `ri_intake.py` can review source-typed text, write a proposed RI event, stash a mutation bundle, and confirm/reject it.
- API endpoints exist for review/recent/confirm.
- Smoke coverage exists for transcript paste, recruiting update, email paste, LinkedIn screenshot, and negative quiet-chat paths.
- `daily_brief.py` reads recent RI events and reports persisted/proposed/rejected counts.
- `linkedin_messaging.py --emit-ri-events` can emit event-shaped proposals, but those proposals are not yet fully wired into the RI event store / review-confirm lifecycle by default.

## Core Build Goal

Make passive detectors write or propose RI events as a normal part of the morning pipeline.

Minimum viable target:

```text
refresh_all.py
→ source overlays / relationship_signals
→ passive RI event proposal/write layer
→ system/ri_events/*.jsonl
→ daily brief Resource Verification + Relationship / Operational Signal Review
```

No silent mutation. If an event can safely project, say what changed. If it cannot safely project, say what is pending or blocked.

## Priority 1 — Passive RI Event Proposal Aggregator

Build a small orchestrator, likely:

```text
system/scripts/passive_ri_ingest.py
```

Responsibilities:

- collect passive RI candidates from existing overlays;
- normalize them into `ri_intake.review()` payloads or directly valid RI event payloads where appropriate;
- append durable events only through `ri_events.py` / `ri_intake.py`;
- write a cache summary:

```text
system/.cache/passive_ri_ingest.json
```

Candidate sources for v1:

- `linkedin_messaging.linkedin_message_overlay()` / `emit_ri_event_proposals`;
- `relationship_signals.build_report()` high/medium system-detected relationship signals;
- email/calendar relationship-signal candidates already represented in `relationship_signals`;
- direct interaction overlay only when source freshness and local permissions are real enough to avoid false certainty.

Output summary must include:

- candidates_seen;
- events_written;
- duplicates_skipped;
- proposed_mutations_pending;
- blocked_low_confidence_event_at;
- blocked_unmatched_entity;
- source_unavailable;
- by_source;
- event_ids;
- source_refs.

Smoke test:

```bash
python3 system/scripts/passive_ri_ingest.py --smoke
```

Acceptance:

- smoke uses isolated temp event store;
- duplicate candidates do not double-write;
- stale/low-confidence candidates become blocked/proposed, not silently projected;
- output uses action-state words: recorded/proposed/blocked/unavailable.

## Priority 2 — Wire Morning Pipeline

Add passive RI ingest to:

```text
system/scripts/refresh_all.py
```

Recommended placement:

1. refresh sources
2. relationship signals
3. strategic operators
4. passive RI ingest
5. daily brief
6. action drafts
7. meeting prep
8. loop autopilot

Reason: daily brief should see the events written/proposed by the passive ingest pass.

Acceptance:

```bash
python3 system/scripts/refresh_all.py --date 2026-05-23
python3 system/scripts/daily_brief.py --smoke
```

Expected:

- refresh succeeds;
- `system/.cache/passive_ri_ingest.json` exists;
- daily brief RI mutation proof reflects the event/proposal counts.

## Priority 3 — Projection Proof, Not Full Projection Rewrite

Do not attempt full event-sourced projection replacement in this sprint.

Instead, add projection-proof reporting:

- what event was recorded;
- what projection would change;
- whether the change was applied, proposed, blocked, duplicate, or unavailable;
- why.

For this sprint, safe projections can remain review-first unless the existing confirm path already applies them.

Recommended output fields:

```json
{
  "event_id": "...",
  "source_type": "...",
  "action_state": "recorded|updated|proposed|blocked|duplicate|unavailable",
  "projection_targets": ["baseline_index", "loop_ledger", "active_threads"],
  "applied": [],
  "pending_confirmation": [],
  "blocked": [],
  "reason": "...",
  "source_refs": []
}
```

Acceptance:

- no user-facing surface says "updated" unless a projection write actually occurred;
- no daily brief claims passive RI was captured unless an event exists;
- no event updates `last_touch` from low-confidence or stale event dates.

## Priority 4 — Daily Brief Integration

Extend the trust-first daily brief to include passive RI ingest proof.

Likely surfaces:

- Resource Verification & Freshness Status:
  - add passive RI ingest cache row;
  - show events/proposals/blocked counts.
- Relationship / Operational Signal Review:
  - show "RB recorded..." only for persisted events;
  - show "RB proposed..." for review-first events;
  - show "Blocked..." where event date/entity/source is insufficient.

Acceptance:

Regression prompt:

```text
Show me my daily brief.
```

Expected:

- starts with resource verification;
- reports passive RI event/proposal/blocked counts before interpretation;
- relationship section includes mutation proof;
- action language is completed/proposed/blocked, not hypothetical.

## Priority 5 — API / GPT Contract Hygiene

If passive RI ingest becomes a first-class operator surface, add a read endpoint:

```text
GET /passive_ri_ingest
```

Only add it to `openapi_gpt.yaml` if it is genuinely useful for the Custom GPT and the 30-operation budget allows it. Otherwise keep it dev/local only.

If added:

- update `system/api/openapi.yaml`;
- update `system/scripts/validate_openapi_gpt.py`;
- update `system/scripts/api_smoke_test.py`;
- regenerate GPT OpenAPI.

Acceptance:

```bash
python3 system/scripts/validate_openapi_gpt.py
python3 system/scripts/api_smoke_test.py
```

## Non-Goals

Do not do these in this sprint:

- Do not replace `baseline_index.json` / cards / loops with event-derived projections.
- Do not build a new event store.
- Do not auto-create RCs.
- Do not silently mutate ambiguous relationship state.
- Do not build the full monitored transcript-folder pipeline yet; that is the next sprint unless a tiny fixture is needed for tests.
- Do not add broad product UI.
- Do not treat stale source silence as a real quiet signal.

## Required Smoke Suite Before Green

Run:

```bash
python3 system/scripts/ri_events.py --smoke
python3 system/scripts/ri_smoke_test.py
python3 system/scripts/passive_ri_ingest.py --smoke
python3 system/scripts/daily_brief.py --smoke
python3 system/scripts/refresh_all.py --date 2026-05-23
python3 system/scripts/api_smoke_test.py
python3 system/scripts/morning_path_test.py
```

If `passive_ri_ingest.py` is not added under that name, use the final script name but preserve the acceptance intent.

Expected green:

- RI event store smoke passes.
- RI intake end-to-end smoke passes.
- Passive ingest smoke passes.
- Daily brief trust-first regression passes.
- Refresh pipeline writes passive RI ingest cache before daily brief.
- API smoke remains green.
- Morning path remains 5/5 automated.

## Suggested Sprint Sequence

1. Inspect `ri_events.py`, `ri_intake.py`, `ri_smoke_test.py`, `relationship_signals.py`, and `linkedin_messaging.py`.
2. Implement passive RI ingest aggregator with isolated smoke.
3. Wire into `refresh_all.py`.
4. Add daily brief passive RI proof rows.
5. Decide whether an API endpoint is needed.
6. Run full smoke suite.
7. Update `STATUS.md`, `CLAUDE_DEVELOPMENT_MAP.md`, and protocols only after tests are green.

## Success Definition

The sprint is green when RB can say:

```text
RB reviewed passive relationship signals.
RB recorded N durable RI event(s).
RB proposed M review-first mutation(s).
RB skipped D duplicate event(s).
RB blocked B candidate(s), with reasons.
The daily brief now shows those states before interpretation.
```

That is the next step toward RB feeling like a real Chief of Staff: not just noticing, not just advising, but remembering and proving what it did.
