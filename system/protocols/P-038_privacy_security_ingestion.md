---
id: P-038
title: Privacy, security, and agentic architecture — ingestion and action gate
sprint: RB 9.13
date: 2026-05-27
scripts:
  - system/scripts/privacy_guard.py
  - system/scripts/audit_log.py
  - system/scripts/retention_policy.py
reads:
  - system/SECURITY_PRIVACY_ARCHITECTURE.md
  - system/.cache/source_health.json
writes:
  - system/audit/<YYYY-MM>.jsonl
  - system/.cache/audit_log_index.json
tests:
  - system/tests/test_privacy_guard.py
  - system/tests/test_prompt_injection_resistance.py
  - system/tests/test_retention_policy.py
---

# P-038 — Privacy, Security, and Agentic Architecture

## Purpose

This protocol governs how RB handles data across the ingestion pipeline —
from raw source content through normalization, intelligence extraction, durable
memory, and user-facing summaries — with explicit rules for:

- data class assignment
- raw content minimization
- prompt-injection resistance
- action gates for mutating operations
- retention class enforcement
- auditability
- delete/forget flows

Reference architecture: `system/SECURITY_PRIVACY_ARCHITECTURE.md`.

---

## Data Class Pipeline

Every item entering RB must be classified before any persistence decision.

```
raw_source
    ↓ (extract structure, discard full body)
normalized
    ↓ (extract RI facts, discard normalized if not needed)
intelligence
    ↓ (judgment: does this warrant durable storage?)
memory
    ↓ (render for output, summarize as needed)
summaries
```

Audit events are written at each stage to the append-only audit log.

---

## Ingestion Flow

### Step 1 — Source Access

When RB reads from an external source (Gmail, Calendar, Drive, LinkedIn,
transcript, etc.):

1. Classify the data as `raw_source`.
2. Write audit event: `source_accessed` with source, scopes_used, profile_context.
3. Wrap any external text in data-only boundaries via `privacy_guard.wrap_external_content()`.
4. Run injection detection via `privacy_guard.detect_injection_attempt()`.
   - If injection detected: write `security_warning` audit event; continue with wrapped content.
5. Assign retention class `ephemeral_raw`. Do not persist full source unless explicitly required.

### Step 2 — Normalization

When RB extracts structured fields from raw source:

1. Classify as `normalized`.
2. Excerpts only (max 280 chars per field).
3. Assign `source_cache` retention class.
4. Write audit event: `item_persisted` with data_class=normalized, retention_class=source_cache.

### Step 3 — Intelligence Extraction

When RB derives RI signals, opportunity assessments, or relationship facts:

1. Classify as `intelligence`.
2. Must not include raw source content.
3. Must include: source, timestamp, confidence, grounding.
4. Assign `derived_intelligence` retention class.
5. Write audit event: `item_persisted` with data_class=intelligence.

### Step 4 — Durable Memory Promotion

When RB promotes an intelligence item to durable memory (ri_events, baseline
fields, contact cards):

1. Call `privacy_guard.guard_durable_memory(item)`.
   - If blocked: write `item_rejected` audit event with reason; do not persist.
2. Confirm all required fields are present:
   - source, timestamp, confidence, grounding, reason_for_persistence,
     freshness, user_stated_vs_inferred, retention_class
3. Assign `durable_memory` retention class.
4. Write audit event: `item_persisted` with data_class=memory.

### Step 5 — User-Facing Summaries

When RB renders output for the daily brief, meeting prep, or exports:

1. Classify as `summaries`.
2. Summarize rather than quote.
3. Call `privacy_guard.guard_disclosure(text)` before writing to output.
   - If flagged: review warnings before delivering.
4. Label inferred vs verified intelligence.
5. Assign `user_exportable` retention class.

---

## Action Gate Contract

RB must call `privacy_guard.gate_action(action_type, confirmed)` before any
mutating operation.

### Gated actions (require confirmed=True):

- send_email
- send_linkedin_message
- modify_file
- change_calendar_event
- update_crm_record
- change_baseline_contact
- change_durable_profile_fact
- delete_data
- share_export_drive / share_export_document
- write_ri_event (durable)
- apply_last_touch, apply_baseline_mutation
- apply_loop_mutation, apply_thread_mutation, apply_operator_mutation

### Read-only operations (no gate):

- load_daily_brief, read_baseline, search_contacts
- compute_drr_score, generate_meeting_prep (compute only)
- load_ri_events (read)
- cache writes for derived intelligence

### Gate flow:

```
1. Propose action → log mutation_proposed audit event
2. Present proposal to user with proof and confidence
3. User confirms or rejects
4. If confirmed → log mutation_confirmed → execute → log mutation_executed
5. If rejected → log mutation_rejected
6. If no response → gate_blocked; action does not execute
```

---

## Retention Class Enforcement

Each ingested item is assigned a retention class via
`retention_policy.assign_retention_class(item)`.

| Class | Window | Applies To |
|---|---|---|
| `ephemeral_raw` | 24h | Raw source content |
| `source_cache` | 72h | Normalized extractions |
| `derived_intelligence` | 90 days | RI signals, opportunities |
| `durable_memory` | Indefinite | Judgment-approved memory |
| `audit_log` | 365 days | Audit events (append-only) |
| `user_exportable` | Indefinite | Profile, relationship memory |
| `forgettable` | Per request | Any item flagged for forget |

Callers should use `retention_policy.is_expired(rc, stored_at)` to detect
items that have passed their retention window.

---

## Audit Event Schema

Every audit event written by `audit_log.append_event()` must include:

| Field | Required | Description |
|---|---|---|
| `event_id` | Yes | Stable deterministic ID (AUDIT-XXXXXXXXXXXXXXXX) |
| `event_type` | Yes | One of the ten valid event types |
| `timestamp` | Yes | ISO 8601 UTC |
| `data_class` | Yes | Data class of the item touched |
| `item_summary` | Yes | Non-sensitive description (max 500 chars, no raw content) |
| `reason` | Yes | Why this event occurred (max 500 chars) |
| `outcome` | Yes | What happened (max 200 chars) |
| `source` | No | Source system |
| `profile_context` | No | Profile ID |
| `scopes_used` | No | OAuth scopes or connector path |
| `retention_class` | No | Retention class assigned |

### Event types:

- `source_accessed` — a source was read
- `item_persisted` — an item was written to a store
- `item_rejected` — an item was blocked from persistence
- `item_deleted` — an item was deleted/forgotten
- `mutation_proposed` — a mutating action was proposed
- `mutation_confirmed` — user confirmed a proposed mutation
- `mutation_executed` — a mutation was executed
- `mutation_rejected` — user rejected a proposed mutation
- `security_warning` — security condition detected
- `gate_blocked` — action blocked by gate (no confirmation)

---

## Delete / Forget Flow

When a user requests deletion of stored intelligence or memory:

1. Locate items by person / company / thread / source / date range.
2. Call `retention_policy.build_forget_preview(items)` to generate a safe preview.
3. Present the preview to the user — item summaries only, no raw content.
4. Require explicit confirmation.
5. Call `retention_policy.validate_forget_request(request, items)`.
   - If invalid (unconfirmed, append-only items, etc.): report errors; do not delete.
6. For each deletable item:
   - Create tombstone via `retention_policy.make_tombstone(item)`.
   - Remove item from its store.
   - Write `item_deleted` audit event with tombstone.
7. Preserve tombstones to prevent accidental re-import.

---

## Integration Points

| Existing Script | Integration |
|---|---|
| `passive_ri_ingest.py` | Call `guard_ingestion()` before writing RI events; call `log_item_persisted()` / `log_item_rejected()` for each event outcome |
| `relationship_signals.py` | Call `guard_injection_resistance()` on email/social source text |
| `ri_preproc_transcript.py` | Call `wrap_external_content()` on transcript text; `guard_injection_resistance()` |
| `ri_preproc_email.py` | Call `wrap_external_content()` on email body; `guard_injection_resistance()` |
| `mutations.py` | Call `gate_action()` before any mutation; log via `audit_log` |
| `daily_brief.py` | Read `audit_log_index.json` to surface security-sensitive failure counts |
| `refresh_sources.py` | Call `log_source_accessed()` for each source read |

---

## Daily Brief Security Section

The Daily Brief may include a privacy/security status section when:
- A source access failure was logged (status=failed/unavailable)
- An injection attempt was detected (security_warning)
- A retention expiry warning is pending
- An OAuth scope issue was flagged

The section must:
- Report counts and categories, not raw content
- Never quote raw source text
- Never expose tokens or credentials
- Reference `system/.cache/source_health.json` for source status

---

## Acceptance Criteria (RB 9.13)

- [ ] Ingestion paths have a documented data-class model (this protocol + SECURITY_PRIVACY_ARCHITECTURE.md)
- [ ] `privacy_guard.py` enforces raw-vs-derived persistence rules
- [ ] Persistence events are auditable via `audit_log.py`
- [ ] Prompt-injection fixtures are rejected as instructions (test_prompt_injection_resistance.py)
- [ ] Mutating tool actions require explicit gates (privacy_guard.gate_action)
- [ ] Retention classes exist and are tested (retention_policy.py + test_retention_policy.py)
- [ ] Google integration docs/scopes are reviewed for least privilege (SECURITY_PRIVACY_ARCHITECTURE.md)
- [ ] Daily Brief can report security-sensitive failures without exposing raw content (section above)
