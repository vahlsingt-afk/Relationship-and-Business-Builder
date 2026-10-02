# Claude Handoff: RB Productization and Todd-Specific Assumption Audit

**Date:** 2026-06-09  
**Status:** Approved direction; audit and remediation planning required before broader productization  
**Primary objective:** Catalog and remove Todd-specific assumptions from shared RB runtime behavior without deleting Todd's legitimate private tenant data.

## Executive Direction

RB has demonstrated useful Chief of Staff and relationship-intelligence behavior, but the current operating environment still combines three things that must be separated:

1. **Reusable product logic** — intelligence engines, relationship reasoning, canonical response contracts, connector interfaces, and API behavior.
2. **Tenant configuration** — active profile, timezone, connected accounts, voice, boundaries, opportunity context, schedules, credentials, and feature settings.
3. **Todd's private tenant corpus** — contacts, messages, relationship history, active threads, LinkedIn exports, strategic memory, watchlists, market artifacts, briefs, and derived intelligence.

The immediate task is not a global replacement of the word `Todd`. The immediate task is to identify every Todd-specific assumption, classify it correctly, and migrate shared behavior to user/tenant-scoped configuration.

## Non-Negotiable Safety Rule

Do not globally replace or delete `Todd`.

The repository contains:

- references to Todd Vahlsing as the current operator;
- legitimate historical facts about Todd's relationships and career;
- generated tenant artifacts that must remain attached to Todd's tenant;
- test fixtures intentionally using Todd as sample data;
- unrelated contacts whose first name is Todd;
- shared runtime fields and logic that incorrectly encode Todd as the universal user.

Only the last category is a product defect. The other categories require preservation, isolation, migration, or test-fixture labeling.

## Initial Audit Result

A targeted scan of shared Python runtime and API files found **119 candidate references across 30 files** for the following patterns:

- `ask_todd`
- `needs_todd`
- `why_this_matters_to_todd`
- `new_to_todd_likely`
- `Todd Vahlsing`
- Todd's email addresses
- `todd_vahlsing`
- `00_TODD_PROFILE`
- `/Users/toddvahlsing`
- `America/Chicago`

This is an initial candidate count, not a final defect count. Each occurrence must be classified.

The highest-concentration shared-runtime files are:

| File | Candidate references | Primary concern |
|---|---:|---|
| `system/scripts/daily_brief.py` | 49 | Todd-specific schema names, dispositions, rendering language, legacy profile references |
| `system/scripts/user_profile.py` | 13 | Legacy fallback and hard-coded Todd identity |
| `system/scripts/market_signals.py` | 9 | `why_this_matters_to_todd` schema contract |
| `system/scripts/linkedin_messaging.py` | 9 | Hard-coded operator name and email |
| `system/scripts/publish.py` | 5 | `ask_todd` naming in output projection |
| `system/scripts/cos_synthesis.py` | 4 | `ask_todd` as a canonical disposition |
| `system/api/server.py` | 1 confirmed high-risk reference | Hard-coded Todd profile path for opportunity context |
| `system/scripts/job_intelligence.py` | 1 confirmed high-risk reference | Hard-coded Todd opportunity-context path |
| `system/scripts/contact_index.py` | 1 confirmed high-risk reference | Hard-coded operator email exclusion |
| `system/scripts/session_writer.py` | 1 confirmed configuration issue | Hard-coded `America/Chicago` operating timezone |

## Existing Foundation to Preserve

RB is not starting from zero. The repository already contains:

- `system/profiles/{profile_id}/...` as the intended profile layout;
- `system/scripts/user_profile.py` as a profile loader;
- `system/settings.json` with `active_profile_id`;
- `system/PROFILE_NAMING_CONVENTION.md`;
- tests that reject person-specific canonical profile fields;
- `why_this_matters_to_user` in newer opportunity-sensing behavior;
- an explicit product rule that engines should read the selected profile rather than contain Todd-specific assumptions.

The remediation should complete and enforce this architecture, not replace it with a parallel profile system.

## Classification Taxonomy

Every candidate must be placed into exactly one of these categories.

### A. Shared Runtime Defect — Must Change

Todd-specific identity or semantics embedded in reusable code, schemas, API behavior, prompts, or canonical contracts.

Examples:

- `system/api/server.py` directly reading `profiles/todd_vahlsing/opportunity_context.yaml`;
- `system/scripts/job_intelligence.py` binding to Todd's opportunity context;
- `system/scripts/action_drafts.py` hard-coding `SENDER_FULL_NAME = "Todd Vahlsing"`;
- `system/scripts/contact_index.py` hard-coding Todd's email as the self identity;
- `system/scripts/linkedin_messaging.py` identifying self through Todd's name/email;
- canonical fields such as `why_this_matters_to_todd` and `new_to_todd_likely`;
- canonical dispositions such as `ask_todd` when the concept is really “requires user decision.”

Target state: resolve these values from authenticated tenant context and selected profile configuration.

### B. Host/Installation Coupling — Must Parameterize

Machine-specific paths, macOS assumptions, local ports, LaunchAgents, and local service installation details.

Examples:

- `/Users/toddvahlsing/Documents/Claude/Projects/Relationship Builder`;
- `/Users/toddvahlsing/Library/Application Support/Relationship Builder`;
- `~/Downloads` as an implicit ingestion location;
- LaunchAgent templates and copied runner scripts;
- `127.0.0.1:8765` as an assumed deployment topology;
- `America/Chicago` as a universal timezone.

Target state: installation root, data root, cache root, timezone, schedules, host mode, and connector paths come from a documented configuration contract. Local macOS operation may remain a supported deployment mode, but it cannot be the only product architecture.

### C. Authentication and Tenant Boundary — Redesign Required

The current `RB_API_KEY`/`x-api-key` model is suitable for a single trusted operator prototype, not a multi-user product.

Target state:

- user authentication and workspace membership;
- tenant-scoped authorization on every read/write;
- per-connector OAuth grants;
- managed secrets rather than keys copied into prompts or install files;
- audit records containing tenant/workspace identity;
- no endpoint able to access data without an explicit tenant context;
- ChatGPT acting as an authenticated client, not as the owner of RB state.

Do not embed or reproduce any real API key in documentation, examples, fixtures, logs, or generated artifacts.

### D. Tenant Configuration — Preserve and Relocate

Todd-specific preferences are legitimate, but must live under Todd's tenant/profile rather than shared defaults.

Examples:

- full name and self email aliases;
- timezone;
- voice and drafting style;
- career stage and target roles;
- BridgePoint positioning;
- industry preferences;
- boundaries and intro philosophy;
- daily brief schedule;
- notification channels.

Target state: `profiles/{profile_id}` or tenant configuration storage, with neutral onboarding defaults for new users.

### E. Todd Private Tenant Data — Preserve and Isolate

This data should not be generalized, copied to seed data, or removed merely because it contains Todd's name.

Examples:

- `system/profiles/todd_vahlsing/**`;
- `system/00_TODD_PROFILE.md` during migration;
- baseline and relationship graphs;
- briefs, cards, circles, interaction ledgers, RI events, active threads;
- LinkedIn, email, calendar, Apple, transcript, and uploaded artifact data;
- Todd-owned market datasets and derived micro graphs;
- strategic memories and current opportunities.

Target state: migrate into a Todd tenant namespace with retention and provenance intact.

### F. Historical Documentation — Archive or Relabel

Past sprint notes, defects, test traces, and handoffs may accurately describe Todd's prototype. They should not be rewritten as if history were generic.

Target state:

- mark historical documents as single-user prototype history;
- prevent them from being treated as current product contracts;
- move reusable requirements into current architecture documents;
- retain provenance.

### G. Test Fixtures — Keep, Generalize, or Label

Todd may remain in tests when testing Todd's tenant migration or when the text is merely sample relationship content. Shared-contract tests should use neutral synthetic users.

Target state:

- add at least two synthetic profiles with different names, timezones, industries, and career stages;
- run shared engines under both;
- ensure no Todd identifiers appear in the second tenant's output;
- retain dedicated legacy migration tests for `todd_vahlsing`.

### H. Unrelated Person Named Todd — Never Alter Automatically

Files such as contact briefs for Todd Dallapiazza, Todd Staley, or other contacts are not evidence of operator coupling. Identity resolution must distinguish the operator from contacts sharing the same first name.

## Initial Removal and Migration Catalog

| ID | Area | Current coupling | Required change | Priority |
|---|---|---|---|---|
| TS-001 | API profile lookup | Hard-coded `profiles/todd_vahlsing` in `system/api/server.py` | Resolve profile and tenant from authenticated request context | P0 |
| TS-002 | Job intelligence | Hard-coded Todd opportunity context | Inject selected profile/opportunity context | P0 |
| TS-003 | Self identity | Hard-coded Todd name/email in messaging and contact indexing | Add canonical self-identity aliases to profile/connector config | P0 |
| TS-004 | Canonical schema | `why_this_matters_to_todd` | Migrate to `why_this_matters_to_user`; read old field during compatibility window | P0 |
| TS-005 | Novelty schema | `new_to_todd_likely` | Migrate to `new_to_user_likely` with compatibility reader | P0 |
| TS-006 | Decision disposition | `ask_todd` | Adopt neutral machine value such as `needs_user_decision`; render “Ask {first_name}” only at presentation time | P1 |
| TS-007 | Intelligence queue | `needs_todd` | Rename to `needs_user_input` or `needs_user_decision` | P1 |
| TS-008 | Draft sender | `SENDER_FULL_NAME = "Todd Vahlsing"` | Resolve draft identity and voice from active profile | P0 |
| TS-009 | Legacy profile | `00_TODD_PROFILE.md` fallback | Keep migration adapter, remove as universal fallback for new installations | P1 |
| TS-010 | Profile selection | Auto-select first profile alphabetically when several exist | Require explicit active profile or authenticated tenant context | P0 |
| TS-011 | Timezone | `America/Chicago` hard-coded in session/scheduling docs and behavior | Store IANA timezone per user/workspace | P0 |
| TS-012 | Absolute paths | Todd home/workspace paths in automation and skills | Generate paths from install/data roots | P0 |
| TS-013 | Scheduler | LaunchAgent-only daily operation | Define scheduler interface; support managed cloud jobs and optional local agent | P1 |
| TS-014 | API authentication | Shared static API key | Move to user/workspace auth with scoped tokens | P0 before external users |
| TS-015 | Local state | Shared JSON/cache/database files | Add tenant/workspace partitioning and storage abstraction | P0 |
| TS-016 | Connectors | Todd's Google/email accounts embedded in manifests/docs | Onboarding-created connector records and OAuth grants | P0 |
| TS-017 | Prompts | Todd phrasing in current operational prompts | Use profile display name at render time; neutral internal contracts | P1 |
| TS-018 | Seed knowledge | Todd's restaurant-tech context risks becoming product default | Empty neutral tenant plus optional industry templates with no private data | P0 |
| TS-019 | Logs/artifacts | Logs contain absolute paths and potentially tenant data | Tenant-aware logging, redaction, retention, and secret scanning | P1 |
| TS-020 | Tests | Many tests assume Todd/restaurant tech/Chicago | Add cross-profile and cross-tenant isolation suite | P0 |

## Required Work Sequence

### Phase 0 — Freeze the Contract

Before broad edits:

1. Define `tenant_id`, `workspace_id`, `user_id`, and `profile_id`.
2. Define request context and background-job context.
3. Define canonical neutral field names.
4. Define configuration roots and secret ownership.
5. Define which Todd files are private tenant data versus historical repository artifacts.

Deliverable: an architecture decision record approved before schema migration.

### Phase 1 — Build the Catalog Mechanically

Create a repeatable audit command or script that scans:

- shared runtime code;
- API schemas and prompts;
- automation/install files;
- current architecture and operational contracts;
- tests;
- tenant data and historical artifacts as separate scopes.

The report should include:

- file and line;
- matched phrase;
- category A-H;
- proposed action;
- migration risk;
- owner/status;
- whether the item may contain private data.

The scanner must support an allowlist for legitimate Todd tenant data and unrelated contacts named Todd.

### Phase 2 — Neutralize Shared Contracts

Implement compatibility-first renames:

- `why_this_matters_to_todd` → `why_this_matters_to_user`;
- `new_to_todd_likely` → `new_to_user_likely`;
- `needs_todd` → `needs_user_input`;
- `ask_todd` → `needs_user_decision`.

Readers may temporarily accept legacy names. Writers must emit only neutral names after migration. Add migration receipts and tests before deleting compatibility readers.

### Phase 3 — Remove Identity and Path Coupling

Inject active profile, self aliases, timezone, install root, data root, and connector settings. No shared engine may know Todd's name, email, home directory, or career context without reading tenant/profile data.

### Phase 4 — Prove Tenant Isolation

Create two synthetic tenants:

- User A: restaurant-technology executive in `America/Chicago`;
- User B: early-career healthcare professional in a different timezone.

Run ingestion, intelligence generation, daily brief, drafting, and API retrieval for both. Fail the suite if names, contacts, paths, recommendations, market assumptions, or credentials cross tenants.

### Phase 5 — Product Delivery Architecture

After the Todd-specific audit is controlled, design the delivery model:

- RB cloud control plane and tenant storage;
- background job runner;
- connector/OAuth service;
- ChatGPT app or Custom GPT client;
- optional local companion for Apple/local-only sources;
- onboarding and first-value workflow;
- upgrade, migration, observability, and support model.

Codex remains an engineering/admin tool. It must not be required for routine customer use.

## Acceptance Criteria

The Todd-specific audit is complete only when:

1. Every candidate is categorized A-H.
2. Shared runtime code contains no direct Todd name, email, home path, profile ID, or timezone dependency except explicit legacy migration adapters and labeled fixtures.
3. Canonical schemas and API responses use user-neutral field names.
4. Every persisted record and cache is scoped to a tenant/workspace.
5. Background jobs carry tenant context.
6. A new user can initialize RB without Todd files, Todd credentials, or Todd's industry data.
7. Two synthetic users complete the same core workflows without data or language leakage.
8. Todd's current RB instance still operates after migration and retains provenance.
9. Historical documents remain available but are not loaded as current product instructions.
10. Secrets are absent from source, generated docs, fixtures, logs, and prompts.

## Claude's Immediate Assignment

Do not start with global replacements.

First produce:

1. `system/audits/todd_specific_catalog.csv` with one row per candidate;
2. `system/audits/todd_specific_allowlist.yaml` for legitimate tenant data, historical records, fixtures, and unrelated contacts;
3. `system/design/TENANT_CONTEXT_AND_PROFILE_RESOLUTION.md`;
4. a proposed neutral schema migration map;
5. a test plan for two-user isolation;
6. a bounded Phase 1 implementation plan with files, dependencies, and rollback strategy.

Then stop for architecture review before modifying persisted schemas or moving tenant data.

## Guiding Product Principle

RB should feel deeply personal because it is configured and grounded for each user, not because the shared product is secretly Todd's system with the names changed.
