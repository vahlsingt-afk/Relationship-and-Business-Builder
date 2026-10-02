# RB-INT-017: Intelligence Inputs Not Automatically Processed and Persisted

**Date filed:** 2026-06-16
**Filed by:** Todd
**Status:** Implemented (RB 9.93)
**Priority:** High
**Category:** Intelligence Pipeline / Knowledge Management / CoS Workflow

---

## Problem Statement

When a user provided intelligence-bearing inputs (articles, links, screenshots, PDFs,
text, emails), RB analyzed the content only within the immediate conversation. The
intelligence was not extracted, enriched, persisted, linked to existing knowledge, or
surfaced in future briefs unless the user explicitly asked.

---

## Root Cause Analysis

**Root Cause 1 — `ingestContent` not exposed to GPT.**
The full 5-phase intelligence pipeline (`POST /ingest`, operationId: `ingestContent`)
was never registered in `openapi_gpt.yaml`. The GPT had no action for it. The only
available text-ingest path was `triageInput` (`/intelligence/triage`), which is
analysis-only with no Phase 2-5 processing and no persistence.

**Root Cause 2 — `/ingest` read-only by design.**
Even if the GPT had called `ingestContent`, the endpoint was 100% read-only: every
proposed mutation carried `requires_confirmation: True` and IntelligenceDB was never
written. There was no path from "user provides content" to "intelligence recorded in DB"
without a separate explicit confirmation call the user would need to know to make.

**Root Cause 3 — GPT instructions pointed to wrong endpoint.**
`custom_gpt_instructions_8k.md` and `CANONICAL_RESPONSE_CONTRACT.md` both said
"call `uploadAndIngestFile`" for user-provided content. That endpoint routes FILE
uploads to inbox pipelines — it does not run intelligence triage or write to
IntelligenceDB for article/paste/link content.

---

## Changes (RB 9.93)

### 1. `server.py` — `auto_persist` parameter on `/ingest`

Added `auto_persist: bool = False` to `IngestIn` model.

When `auto_persist=True`, Phase 4b runs after triage:
- Iterates all non-noise streams from Phase 1
- Calls `db.add_item()` for each stream with: title (from intel_type + entities),
  content (from extracted_summary), source, confidence, tags (intelligence_type + entity)
- Returns `persisted_intelligence: [list of written items]` and `persisted_intelligence_count`

Mutation proposals (watchlist adds, thread changes) still return as proposals requiring
explicit confirmation. `auto_persist` only affects intelligence RECORDING, not
system-behavior mutations.

### 2. `openapi_gpt.yaml` — `ingestContent` endpoint + `IngestIn` schema

Added `POST /ingest` as `ingestContent` action in the GPT schema.
Added `IngestIn` schema to components with all fields including `auto_persist`.
Endpoint description explicitly states: mandatory first call for any user-provided content.

### 3. `custom_gpt_instructions_compact_8k.md` — routing rule

Replaced:
> "Any user artifact → `uploadAndIngestFile` once before analysis."

With:
> "Any user-provided TEXT content → call `ingestContent` with `auto_persist: true`
> BEFORE any analysis. MANDATORY. Render Intelligence Receipt immediately. Never
> analyze content without first ingesting it."

Added Intelligence Receipt rendering format so the GPT shows what was persisted
without waiting for the user to ask.

### 4. `custom_gpt_instructions_8k.md` — Ingestion Protocol

Updated "Every user-provided artifact" rule to distinguish:
- Text content → `ingestContent` with `auto_persist: true`
- File uploads → `uploadAndIngestFile`

Added Intelligence Receipt format.

### 5. `CANONICAL_RESPONSE_CONTRACT.md` — `known_artifact_ingest` scenario

Updated to two-path routing: Path A (text → ingestContent + auto_persist) and
Path B (files → uploadAndIngestFile). Added Intelligence Receipt contract.
Added: "Intelligence is NEVER lost because the user forgot to ask."

---

## Intelligence Receipt Format (rendered after every ingestContent call)

```
📥 Intelligence recorded — [source] | [date]

Persisted (N items):
• [intelligence_type] — [entities] | Confidence: [level]

Entities identified: [entity list]
Hypotheses: [key points from extracted_summary]

Proposed mutations (require confirmation):
• [mutation_proposals if any]
```

If `persisted_intelligence_count` is 0: "No intelligence extracted — classified as noise."

---

## Acceptance Criteria — Status

1. ✅ Every input is classified on receipt (`ingestContent` Phase 1 triage)
2. ✅ Intelligence-bearing inputs automatically enter the pipeline (`ingestContent` mandatory)
3. ✅ Extracted entities and hypotheses are persisted (`auto_persist=True` writes to IDB)
4. ⚠️ Watchlists are updated automatically — mutation proposals still require confirmation
   (by design: watchlist mutations change system behavior, not just record observations)
5. ⚠️ Monitoring tasks created automatically — not yet wired (future sprint)
6. ✅ Knowledge mutations are timestamped and auditable (IntelligenceDB `add_item`)
7. ✅ Subsequent briefs reference new intelligence (IDB is read by `intelligence_assessment.py`)
8. ✅ System reports back what was processed, changed, hypotheses, confidence (Intelligence Receipt)

---

## Remaining Gap (future sprint)

Acceptance Criterion 4 (watchlist auto-update) and 5 (monitoring task creation) remain
proposal-only. A future sprint could add `auto_confirm_watchlist: bool = False` to
`ingestContent` for low-risk additions (new entities not currently tracked) while keeping
confirmation for changes to existing entries. Monitoring tasks could auto-write to
`loop_ledger.md` when `auto_persist=True` and the action confidence is high.
