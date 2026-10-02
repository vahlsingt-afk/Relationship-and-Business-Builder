# Claude Sprint — RB 9.13 Security, Privacy, and Agentic Architecture

Date: 2026-05-27

## Sprint Thesis

Agentic AI platforms are a market signal, not RB's blueprint.

RB should not become a generic agent framework. RB should operate above the workflow-agent layer as a Chief of Staff intelligence layer:

- generic agents execute;
- RB decides what matters;
- RB decides what should persist;
- RB suppresses noise;
- RB gates action when trust, timing, source freshness, or user consent is insufficient.

The core design principle:

> Memory by judgment, not memory by volume.

## Product Doctrine

RB differentiation remains:

- judgment over generic automation;
- relationship intelligence over task execution;
- signal-vs-noise filtering over dashboard output;
- strategic restraint over constant action;
- trust, timing, and context over chatbot helpfulness.

Excessive agency is a product defect. RB should not act just because it can.

## Security Foundation

Privacy and security are core architecture, not a later hardening phase.

RB may ingest:

- LinkedIn exports and captured LinkedIn-visible content;
- Gmail;
- Google Calendar;
- Google Drive;
- meeting transcripts such as Fathom / Zoom / Otter;
- CRM data;
- relationship notes;
- job opportunities;
- business strategy;
- customer/vendor intelligence.

Every ingestion path must distinguish:

1. raw source data;
2. normalized/enriched data;
3. derived relationship or opportunity intelligence;
4. durable memory;
5. user-facing summaries.

## External Security References

Use these as control references:

- OWASP Top 10 for LLM Applications: prompt injection, sensitive information disclosure, insecure tool/plugin design, excessive agency, and vector/embedding weaknesses are relevant to RB.
- OWASP MCP Top 10: persistent tool contexts, excessive permissions, command injection, long-lived credentials, and cross-context leakage are relevant if RB adopts MCP/tool orchestration.
- Google API Services User Data Policy: RB must request only permissions necessary for implemented features and use clear disclosures.
- Google Drive API scope guidance: choose the narrowest Drive scope possible and avoid broad Drive access where folder/file-scoped access is enough.
- Google Workspace app access controls: enterprise deployments must expect admins to review, trust, restrict, or block third-party OAuth app access.

## Required Architecture Rules

### 1. Least-Privilege OAuth

- Use the narrowest OAuth scopes that support the implemented feature.
- Do not request broad Drive access if app-created, file-specific, or folder-bound access is sufficient.
- Do not request scopes for future features.
- Prefer incremental authorization in context.
- Record scope purpose in a local integration manifest.

### 2. Raw Data Separation

Raw source data must be stored separately from derived intelligence.

Recommended model:

```text
raw_source/        short-lived, access-controlled, retention-limited
normalized/        structured but still source-adjacent
intelligence/      derived RI/opportunity/loop records
memory/            durable judgment-approved memory
summaries/         user-facing artifacts
audit/             access/update/persist/reject logs
```

### 3. Default Raw Data Minimization

Do not persist entire transcripts, full emails, full LinkedIn threads, or full Drive documents by default.

Persist excerpts only when:

- the user explicitly requests it;
- the excerpt is required for auditability;
- the excerpt is short, relevant, and source-labeled;
- retention rules allow it.

### 4. Durable Memory Requires Judgment

Do not store:

```text
Full Fathom transcript from Olivia meeting.
```

Persist:

```text
Olivia Nielsen / PerfectHire discussed QSR scheduling, AI-native ATS, retention improvement, and potential consulting opportunity. Matt Schalsey connection request may indicate internal discussion. Relationship stage: active opportunity. Recommended next action: move toward paid engagement.
```

Every durable RI/opportunity memory item must include:

- source;
- timestamp;
- confidence;
- grounding;
- reason for persistence;
- freshness;
- user-stated vs inferred status;
- retention class.

### 5. Retention Rules

Add explicit retention classes:

- `ephemeral_raw`: raw content deleted or purged after short window unless user pins it.
- `source_cache`: normalized cache retained for refresh/debug window.
- `derived_intelligence`: structured facts retained while relevant.
- `durable_memory`: judgment-approved long-term memory.
- `audit_log`: append-only access/action/persistence metadata.
- `user_exportable`: profile and relationship memory the user can export.
- `forgettable`: must support delete/forget on request.

### 6. Prompt-Injection Resistance

Treat external content as untrusted data, never instructions.

Applies to:

- LinkedIn posts;
- emails;
- transcripts;
- web pages;
- Drive documents;
- job postings;
- CRM notes;
- PDFs and docs.

Controls:

- wrap external content in data-only boundaries;
- ignore instructions embedded in source artifacts;
- never let source text choose tools, scopes, recipients, persistence, or actions;
- require code paths to decide actions from structured policy, not prose in the artifact;
- test malicious source content.

### 7. Sensitive Information Disclosure Controls

RB outputs should not expose sensitive raw content unless needed and permitted.

Rules:

- summarize rather than quote;
- redact secrets, tokens, private contact details, and sensitive business info by default;
- label inferred vs verified intelligence;
- avoid leaking one profile/team member's private context into another profile or team context;
- user-facing summaries should be appropriate for the selected destination.

### 8. Explicit Action Gates

RB must require explicit user confirmation before:

- sending email or LinkedIn messages;
- modifying files;
- changing Google Calendar events;
- updating CRM records;
- changing baseline/contact records;
- changing durable profile facts;
- deleting data;
- sharing/exporting Drive or other documents.

Read-only analysis may be automated within configured permissions. Mutating actions require gates.

### 9. Auditability

Log:

- source accessed;
- time accessed;
- account/profile context;
- scopes used or connector path;
- data class touched;
- items persisted;
- items rejected;
- mutations proposed;
- mutations confirmed;
- mutations executed;
- failures and security-sensitive warnings.

Logs should support the question:

> What did RB see, what did it keep, what did it ignore, what did it change, and why?

### 10. Delete / Forget

RB must support user-driven delete/forget flows for durable memory and derived intelligence.

Minimum requirements:

- locate memory by person/company/thread/source/date;
- show what will be removed;
- require confirmation;
- write audit event;
- preserve non-sensitive tombstone if needed to prevent accidental re-import.

## Google Drive Position

Google Drive can be part of RB's user-controlled storage model, but only with tight boundaries.

Risks are not only "Google security." RB-specific risks include:

- overbroad OAuth scopes;
- accidental exposure through sharing;
- prompt leakage from Drive docs;
- excessive agent permissions;
- unclear retention;
- unsafe downstream actions;
- cross-profile/team leakage.

RB should prefer:

- user-selected folder roots;
- app-created folders/files where possible;
- explicit source manifests;
- audit logs for every Drive read/write;
- user confirmation for write/share/delete;
- no broad Drive crawling.

## Agentic Architecture Position

RB may use agents, tools, and automations, but RB's core value is not generic agency.

Architecture posture:

```text
source connectors -> safe ingestion -> normalization -> intelligence extraction -> judgment layer -> action proposal -> gated execution
```

RB should stay in the judgment layer:

- evaluate signals;
- decide what matters;
- decide what persists;
- decide what gets suppressed;
- propose actions with proof and confidence;
- delegate execution only through gated tools.

## Backlog / Test Cases

Add tests or smoke checks that verify:

1. RB distinguishes raw data, derived intelligence, durable memory, and user-facing summary.
2. RB does not persist sensitive raw content unless explicitly instructed.
3. Every RI persistence event includes source, confidence, timestamp, grounding, and rationale.
4. Ingestion summaries report privacy/security status.
5. Google Drive integration uses least privilege and avoids unnecessary scopes.
6. Prompt-injection resistance for LinkedIn posts, emails, transcripts, webpages, and job postings.
7. Autonomous actions involving email, files, CRM, Drive, or calendar are refused or gated.
8. Daily Brief includes freshness, accessed sources, stale sources, and security-sensitive failures.
9. RB can delete/forget stored RI when requested.
10. RB labels inferred vs verified intelligence.
11. Raw transcript/email content is not persisted by default.
12. Cross-profile/team leakage is prevented in multi-profile mode.
13. Tool execution logs include profile/account context.
14. External artifact text cannot override RB policy.

## Candidate Implementation Files

Suggested new or updated files:

```text
system/SECURITY_PRIVACY_ARCHITECTURE.md
system/protocols/P-038_privacy_security_ingestion.md
system/scripts/privacy_guard.py
system/scripts/audit_log.py
system/scripts/retention_policy.py
system/tests/test_privacy_guard.py
system/tests/test_prompt_injection_resistance.py
system/tests/test_retention_policy.py
```

## Acceptance Criteria

RB 9.13 is complete when:

- ingestion paths have a documented data-class model;
- at least one privacy guard module enforces raw-vs-derived persistence rules;
- persistence events are auditable;
- prompt-injection fixtures are rejected as instructions;
- mutating tool actions require explicit gates;
- retention classes exist and are tested;
- Google integration docs/scopes are reviewed for least privilege;
- Daily Brief can report security-sensitive source/access failures without exposing raw content.

## Design Motto

RB should be capable enough to help, restrained enough to trust, and auditable enough to deploy.
