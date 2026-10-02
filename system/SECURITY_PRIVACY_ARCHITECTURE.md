# RB Security and Privacy Architecture

**Version:** RB 9.13
**Date:** 2026-05-27
**Sprint:** CLAUDE_SPRINT_RB_9_13_SECURITY_PRIVACY_AGENTIC_ARCHITECTURE.md

---

## Design Principle

> Memory by judgment, not memory by volume.

RB is a Chief of Staff intelligence layer, not a generic agent. Agents execute.
RB decides what matters, what persists, what gets suppressed, and what requires
a gate before acting.

Excessive agency is a product defect. RB should not act just because it can.

## Tenant Data Boundary

RB's reusable product is the engine, schemas, workflows, and empty/default
domain vocabulary. A user's data is not reusable product content.

Tenant-owned data includes:

- relationship graphs, contacts, RI events, cards, briefs, loops, and threads
- uploaded spreadsheets, PDFs, transcripts, exports, screenshots, and documents
- purchased or licensed market datasets supplied by the user
- private customer/account topology and operational ecosystem graphs
- derived indexes, projections, confidence records, and summaries created from
  those private sources

Examples in this workspace include Todd's McDonald's NSN workbook and Technomic
Top 1500 files. They belong to Todd's RB instance only. They must not be bundled
as defaults for other users, used as cross-user seed data, leaked into another
profile/team context, or treated as generic restaurant-industry memory.

For a new user, RB starts with product code plus empty/user-neutral templates.
Onboarding and long-term usage build that user's own data layer from sources
they authorize.

---

## Data Classification Model

Every item that enters RB must be assigned a data class before any persistence
decision. The five classes form a directed pipeline — information can move
forward through the pipeline under policy, but never sideways or backward.

```
raw_source
    ↓   (normalize + extract structure, discard full source)
normalized
    ↓   (extract relationship intelligence facts, discard normalized if not needed)
intelligence
    ↓   (judgment: does this warrant durable storage?)
memory
    ↓   (render for output, redact/summarize as needed)
summaries
```

An independent parallel stream exists for governance:

```
[any pipeline stage] → audit
```

### raw_source

Raw content as received from an external system. Examples:

- Full text of a Fathom or Zoom transcript
- Raw email body from Gmail
- LinkedIn post text
- Full Drive document content
- Job posting body
- CRM note text

**Rules:**
- Store only under `raw_source/` or in ephemeral memory, never in durable paths
- Subject to `ephemeral_raw` retention class (see below)
- Never appear in user-facing summaries
- Never passed directly to downstream actors as instructions
- Must be wrapped in data boundaries before any LLM processing (see Prompt-Injection Resistance)

### normalized

Structured extraction from raw source. Examples:

- Parsed calendar event: `{attendees, title, datetime, duration}`
- Parsed email: `{sender, subject, date, snippet}`
- Parsed LinkedIn message: `{from_url, direction, date, snippet}`
- Parsed transcript section: `{speaker, timestamp, topic_tag, excerpt}`

**Rules:**
- Store under `normalized/` or in `source_cache`
- Must not contain full raw body (excerpt max 280 chars)
- Source label required (where did this come from?)
- Subject to `source_cache` retention class

### intelligence

Derived relationship intelligence: facts, signals, opportunities, and assessments
produced by RB's judgment layer. Examples:

- RI event: `{contact, signal_type, confidence, evidence, source, timestamp}`
- Opportunity: `{type, contact, relevance, recommended_action, confidence}`
- Network gap: `{cluster, gap_type, severity, recommended_action}`
- Relationship signal: `{signal_type, direction, contact_id, ri_assessment}`

**Rules:**
- Must include: source, timestamp, confidence, grounding, reason_for_persistence,
  freshness, user_stated_vs_inferred, retention_class
- Must NOT include full raw source text
- Stored in `intelligence/` (RI events JSONL, caches, etc.)
- Subject to `derived_intelligence` retention class

### memory

Judgment-approved long-term relationship memory. Examples:

- Contact relationship stage change with grounding
- Thread outcome summary
- Durable relationship fact (e.g., "Olivia Nielsen leads QSR scheduling for PerfectHire")

**Rules:**
- Requires explicit judgment decision to promote from intelligence
- Must carry all required fields (source, timestamp, confidence, grounding,
  reason, freshness, user_stated_vs_inferred, retention_class)
- Subject to `durable_memory` retention class
- Must support delete/forget via the forget flow
- Stored under `memory/` or in the designated durable-memory sections of
  baseline_index.json and ri_events/

### summaries

User-facing artifacts. Examples:

- Daily brief sections
- Meeting prep briefs
- RI assessment proof rows
- Export documents

**Rules:**
- Must summarize, not quote raw source
- Must redact secrets, tokens, private contact details, and sensitive business info
- Must label inferred vs verified intelligence
- Must not leak one profile's private context into another
- Subject to `user_exportable` retention class

### audit

Append-only log of all access, persistence, rejection, mutation, and security-
sensitive events. Never persists raw source content.

**Rules:**
- Append-only — no deletes, no edits
- Subject to `audit_log` retention class
- Supports the question: "What did RB see, what did it keep, what did it
  ignore, what did it change, and why?"

---

## Retention Classes

| Class | Window | Deletable? | Notes |
|---|---|---|---|
| `ephemeral_raw` | 24h default | Yes | Raw content; purged unless user pins |
| `source_cache` | 72h default | Yes | Normalized cache for refresh/debug |
| `derived_intelligence` | 90 days | Yes | Structured facts retained while relevant |
| `durable_memory` | Indefinite | Yes | Judgment-approved long-term memory |
| `audit_log` | 365 days | No (append-only) | Access/action/persistence metadata |
| `user_exportable` | Indefinite | Yes | Profile and relationship memory |
| `forgettable` | Per request | Yes (required) | Must support delete/forget on request |

Any item tagged `forgettable` must be findable and deletable via the forget flow.

---

## Ingestion Pipeline Data Boundaries

Before any LLM processing, external source content must be wrapped as data:

```
[DATA START — source: {source_type}, retrieved: {iso_timestamp}]
{raw content here}
[DATA END]
```

Instructions embedded in the wrapped content are ignored. The wrapping code
decides what actions to take from structured policy, never from prose in the
artifact.

This applies to:
- LinkedIn posts and messages
- Email body text
- Meeting transcripts (Fathom, Zoom, Otter)
- Web pages and job postings
- Drive document content
- CRM notes
- PDF content

---

## Prompt-Injection Resistance

External content is untrusted data, never instructions.

**Controls:**
- All external text wrapped in data-only boundaries before processing
- Source artifacts cannot choose tools, scopes, recipients, or persistence class
- Source artifacts cannot override RB policy
- Code paths decide actions from structured policy keys, not from parsed prose
- Injections embedded in source text (e.g., "Ignore previous instructions and...") are treated as literal text, not commands

**Test surface:** `system/tests/test_prompt_injection_resistance.py`

---

## Action Gates

RB must require explicit user confirmation before any mutating action.

| Action | Gate Required |
|---|---|
| Send email | Yes — explicit confirm |
| Send LinkedIn message | Yes — explicit confirm |
| Modify files | Yes — explicit confirm |
| Change Google Calendar events | Yes — explicit confirm |
| Update CRM records | Yes — explicit confirm |
| Change baseline/contact records | Yes — explicit confirm |
| Change durable profile facts | Yes — explicit confirm |
| Delete data | Yes — explicit confirm |
| Share/export Drive or documents | Yes — explicit confirm |
| Read-only analysis | No gate required |
| Cache writes (derived) | No gate required |

The action gate contract is enforced by `privacy_guard.gate_action()`.

---

## Sensitive Disclosure Controls

- Summarize rather than quote raw source
- Redact secrets, tokens, private contact details, and sensitive business info by default
- Label inferred vs verified intelligence in all user-facing outputs
- Do not leak one profile/team member's private context into another profile or team context
- User-facing summaries are appropriate for the selected destination (brief vs export vs external)

---

## Google OAuth Least-Privilege Requirements

### Scope Discipline

| Feature | Approved Scope | Forbidden Alternatives |
|---|---|---|
| Read Gmail for signal extraction | `gmail.readonly` | `mail.google.com`, `gmail.modify` |
| Read Google Calendar for signal extraction | `calendar.readonly` | `calendar` (full access) |
| Write to app-created Drive folder | `drive.file` | `drive` (full access), `drive.readonly` |
| Read specific Drive files user shares | `drive.file` | `drive` (full access) |

**Rules:**
- Request only the narrowest scope for the implemented feature
- Do not request scopes for future features
- Prefer incremental authorization: request additional scopes when the user
  activates a feature that requires them, not at initial setup
- Record each scope and its purpose in `system/integration_manifest.yaml`
- Enterprise deployments: expect workspace admins to review and potentially
  restrict third-party OAuth app access

### Google Drive Integration Rules

- Use user-selected folder roots; never crawl broad Drive
- Prefer app-created folders/files (`drive.file` scope) over broad read access
- Maintain explicit source manifests (what was read, when, why)
- Audit log for every Drive read/write
- User confirmation for any Drive write, share, or delete
- No bulk Drive crawling or indexing

---

## Agentic Architecture Posture

```
source connectors
    → safe ingestion (data boundaries, injection resistance)
    → normalization (extract structure, discard raw)
    → intelligence extraction (judgment layer)
    → action proposal (grounded, confidence-labeled)
    → gated execution (explicit user confirmation)
```

RB stays in the judgment layer. It:
- Evaluates signals
- Decides what matters
- Decides what persists
- Decides what gets suppressed
- Proposes actions with proof and confidence
- Delegates execution only through gated tools

RB does not act autonomously on mutating operations.

---

## Auditability

The audit log supports the question:
> What did RB see, what did it keep, what did it ignore, what did it change, and why?

### Required Audit Event Fields

| Field | Description |
|---|---|
| `event_id` | Stable unique ID |
| `event_type` | One of: source_accessed, item_persisted, item_rejected, mutation_proposed, mutation_confirmed, mutation_executed, mutation_rejected, security_warning |
| `timestamp` | ISO 8601 UTC |
| `profile_context` | Which profile/account context |
| `source` | Source system or connector path |
| `scopes_used` | OAuth scopes or connector path |
| `data_class` | Data class of the item touched |
| `retention_class` | Retention class assigned |
| `item_summary` | Non-sensitive description (no raw content) |
| `reason` | Why this event occurred |
| `outcome` | What happened (persisted/rejected/executed/blocked) |

**See:** `system/scripts/audit_log.py`

---

## Delete / Forget Flow

Minimum requirements for any user-driven delete/forget request:

1. Locate memory by person / company / thread / source / date range
2. Show what will be removed (item summaries, not raw content)
3. Require explicit confirmation
4. Write audit event: `item_deleted` with tombstone
5. Preserve non-sensitive tombstone if needed to prevent accidental re-import
6. Remove item from all durable stores (memory, ri_events, baseline fields)

Items tagged `forgettable` must always support this flow.

---

## Daily Brief Security Reporting

The Daily Brief may include a security-sensitive source/access failure section.

**Rules:**
- Report source connectivity failures (stale, unavailable, failed)
- Report scope configuration issues (missing OAuth, insufficient permissions)
- Report prompt-injection detections (counted, not quoted)
- Report retention expiry warnings
- Never include raw content in the brief
- Never expose OAuth tokens, credentials, or raw API responses

Source health is available in `system/.cache/source_health.json` (written by
`refresh_sources.py --save-health`).

---

## Implementation Files

| File | Status | Purpose |
|---|---|---|
| `system/SECURITY_PRIVACY_ARCHITECTURE.md` | This file | Canonical architecture reference |
| `system/protocols/P-038_privacy_security_ingestion.md` | RB 9.13 | Protocol: ingestion data-class flow and action gate contract |
| `system/scripts/privacy_guard.py` | RB 9.13 | Enforces raw-vs-derived rules, injection resistance, action gates |
| `system/scripts/audit_log.py` | RB 9.13 | Append-only audit log |
| `system/scripts/retention_policy.py` | RB 9.13 | Retention class definitions and expiry enforcement |
| `system/tests/test_privacy_guard.py` | RB 9.13 | Privacy guard test suite |
| `system/tests/test_prompt_injection_resistance.py` | RB 9.13 | Injection resistance test suite |
| `system/tests/test_retention_policy.py` | RB 9.13 | Retention policy test suite |

---

## Design Motto

> RB should be capable enough to help, restrained enough to trust, and auditable enough to deploy.
