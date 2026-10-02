---
id: P-021
title: RI event sourcing architecture
script: planned
cache: planned projection caches
reads:
  - system/briefs/
  - system/baseline_index.json
  - system/cards/
  - system/loop_ledger.md
  - system/active_threads.yaml
  - system/inbox/
writes:
  - system/ri_events/*.jsonl
  - projection caches
  - migration reports
inputs:
  - name: source_event
    description: A normalized relationship-intelligence signal from email, transcript, chat, call, calendar, social, manual note, or file ingestion.
    required: true
trigger: every RI-bearing ingestion; migration/backfill from existing briefs; projection rebuild
---

# P-021 — RI Event Sourcing Architecture

## Purpose

Make timestamped RI events the authoritative relationship history and treat current relationship state as a projection.

## Core principle

Every relationship-intelligence signal becomes an immutable event.

The current relationship state is not the source of truth. It is the result of replaying, deduping, and projecting the RI event stream.

## Why this matters

Event sourcing solves several current RB problems:

- duplicate RI creation,
- loss of historical context,
- fragmented relationship continuity,
- weak warming/cooling trend detection,
- missing temporal trust signals,
- unclear reasons for relationship-state changes,
- and inability to explain why a person, company, or thread matters now.

## Event shape

Use the planned schema in `system/SCHEMAS.md` under "Relationship Intelligence Event."

Every event must include:

- `event_id`
- `captured_at`
- `event_at`
- `event_at_confidence`
- `source`
- `entities`
- `dedupe`
- `signal`
- `persistence`

## Date-of-intelligence gate

No RI event may update a projection unless RB can reason about when the intelligence actually happened.

Required distinction:

- `event_at` — when the relationship event actually happened.
- `captured_at` — when RB learned about it.
- `event_at_confidence` — `high`, `medium`, or `low`.

`event_at` is the anchor for daily briefs, dedupe, recency, `last_touch`, DRR, relationship momentum, and stale/duplicate decisions. `captured_at` is audit metadata only.

Rules:

- If `event_at` is explicit in the source, use it with high confidence.
- If `event_at` can be inferred from reliable file metadata, message timestamp, email date, calendar event time, or transcript metadata, use medium confidence and preserve the inference source.
- If `event_at` is missing or only upload-time is available, do not use it for canonical mutation. Ask for reconciliation or store as review-only.
- Never treat `captured_at` as the relationship event date unless Todd confirms that the upload itself is the event.
- Never overwrite a newer projection with older evidence unless the event is explicitly marked as a correction.
- Backfilled artifacts must be projected into the day/week/month they occurred, not the day they were uploaded. Example: if Todd uploads old Fathom files today, each transcript is tied to the call date, not today's brief, unless today's upload creates a separate review action.
- The daily brief may say "newly uploaded historical artifact needs review," but it must not frame the old relationship event as fresh momentum.

## Projection targets

The event stream should project into:

- `baseline_index.json`
- `cards/<id>.md`
- `briefs/*.md` or successor evidence views
- `loop_ledger.md`
- `active_threads.yaml`
- `today.md`
- DRR scores
- relationship evidence summaries
- network-gap and company-intelligence surfaces

## Relationship-led opportunity intake

High-signal opportunities that arrive through a relationship path must not stop at
conversation coaching. They should route through the review-first opportunity
intake surface:

```text
POST /opportunity_intake
```

With `confirm=false`, the operation returns a visible mutation plan and canonical
response text. With `confirm=true`, it applies the safe projections:

- contact create/touch;
- company artifact;
- opportunity artifact;
- relationship edge / referral trust transfer;
- active thread with daily-brief / Who Matters Now boost;
- follow-up loops;
- append-only interaction brief.

This is the canonical answer to the recurring trust gap: RB must show what it
detected, what it proposes to mutate, what was persisted, what is pending
confirmation, and what projection mechanism elevates the opportunity.

## Migration approach

Do not rewrite RB in one step.

Phase 1:

- Keep current canonical files.
- Add event emission to new ingestion paths.
- Mirror high-signal manual captures into RI events.

Phase 2:

- Backfill RI events from existing Interaction Briefs.
- Mark backfilled events as `source.type = "interaction_brief_backfill"`.
- Preserve original brief provenance.

Phase 3:

- Build projection checks.
- Compare projected `last_touch`, signal class, loops, and active threads against current files.
- Report differences for review.

Phase 4:

- Let event-derived projections become the preferred computation source.
- Keep human-readable files as views and audit surfaces.

## Dedupe rules

Every event must record a dedupe decision:

- `new_event`
- `duplicate_of_existing`
- `updates_existing_projection`
- `correction_event`
- `same_source_new_signal`

Dedupe keys should include source type, source id/path, event timestamp, matched people, and signal type.

Duplicate/stale handling:

- If an incoming RI event has the same source id/path/hash, event timestamp, matched entities, and signal type as an existing event, mark `duplicate_of_existing` and do not project it again.
- If the incoming event is older than the current projection and is not a correction, preserve it as historical evidence only; do not overwrite current state.
- If the incoming event conflicts with newer state, produce a reconciliation prompt instead of mutating.
- If the incoming event is newer but appears to describe the same relationship moment from a second source, mark `same_source_new_signal` or `updates_existing_projection` only after preserving both source references.

## Projection discipline

Projection changes should be explicit:

- `last_touch` updates require real interaction evidence.
- LMI to LKI promotion requires bidirectional substantive exchange.
- RC promotion still requires Todd confirmation.
- Active-thread creation requires strategic context, not just a meeting.
- Loop creation requires an actual obligation or action window.

## Output contract

When an event is captured, RB should be able to answer:

- What happened?
- When did it happen?
- When did RB learn it?
- Where did it come from?
- Who/what did it match?
- Was it deduped?
- What did it change?
- What did it not change?
- Why does the current relationship state look the way it does?

## Chief-of-staff implication

The daily brief should be powered by the last 24 hours of RI events, not by ad hoc memory.

The question becomes:

> What relationship events changed the strategic landscape since yesterday?

That is the line between a measured Chief of Staff and a reactive administrative assistant.
