# Claude Development Map — RB Operating System

**Date:** 2026-05-24 (updated for RB 9.4 Passive RI Persistence MVP)  
**Purpose:** give Claude the current product/engineering map for turning RB from a relationship-memory system into an indispensable daily operating system.

## Current Assessment

RB is no longer just a knowledge base. It is becoming an operating system for relationship-aware execution.

The core loop we are building toward:

```text
fresh sources
→ relationship-intelligence events
→ canonical projections
→ daily deltas
→ top moves
→ draft-ready actions
→ feedback and reconciliation
```

The system becomes indispensable when it reliably answers:

- What changed since yesterday?
- What should Todd do today?
- What should Todd ignore?
- Which opportunity is warming, cooling, or waiting?
- Which market/operator signal changes Todd's positioning?
- What one question would make tomorrow's recommendation better?

## What Is Strong

### Conceptual Architecture

RB now has a strong product doctrine:

- good advice is not sufficient;
- CoS output must classify, operationalize, and resolve to action state;
- industry intelligence is subordinate to relationship/action intelligence;
- absence of signal is not meaningful when sources are stale;
- every meaningful item should resolve to `act_today`, `monitor`, `ask_todd`, or `ignore`.

### File-Based Memory

The local workspace is a real memory system:

- `baseline_index.json`
- `cards/`
- `briefs/`
- `loop_ledger.md`
- `active_threads.yaml`
- `circles/`
- `protocols/`
- `test_traces/`
- `today.md`
- `MANIFEST.md`

This gives RB continuity outside model context and makes Claude/Codex/ChatGPT handoffs possible.

### Deterministic Compute Layer

The scripts already cover meaningful surfaces:

- daily brief generation
- DRR scoring
- loop parsing
- network gaps
- intro paths
- email/calendar/social/interaction overlays
- relationship signals
- mutation safety
- API and MCP exposure
- test trace capture

### Testing Discipline

The test-trace loop is now one of the strongest product assets. Recent traces captured:

- daily brief drift into news commentary;
- missing grounding labels;
- stale-source behavior;
- `touchContact` projection sync defect;
- manual LinkedIn solicitation assessment gap;
- passive RI persistence gaps;
- transcript/export limitations.
- Maho/Ish strategic-call analysis where advice passed but CoS operationalization failed (`T-2026-05-20-001`).

These are now product requirements, not one-off bugs.

### Chief-of-Staff Platform Direction

RB is evolving into a Chief of Staff platform, not a CRM wrapper, news feed, task manager, or generic AI assistant. The core product value is not "more information." It is less noise, better timing, preserved relationships, strategic clarity, operational awareness, and executive focus.

The system should continuously answer:

- What matters right now?
- What does not matter yet?
- Who requires attention?
- What should wait?
- What risks are emerging?
- What opportunities are developing?
- What should Todd not spend time on?

The daily brief target has been updated from "executive summary" to verified intelligence briefing. The canonical front-door order is:

1. `Resource Verification & Freshness Status`
2. `Relationship / Operational Signal Review`
3. `Industry Brief`
4. `Local / Regional Restaurant Environment`
5. `World / Macro / Macroeconomic Impact`
6. `Recommended Actions`
7. `Action Orchestration Prompt`

Detailed drill-downs may follow, but the brief must prove sources/freshness/provenance before interpretation.

This is the right shape for "I can't imagine starting my day without this."

## What Is Weak

### Source Freshness

RB still depends too much on stale or manual source state. The daily brief must refresh available sources before speaking and must clearly report skipped/stale sources.

Weaknesses:

- email/calendar success path still depends on raw connector exports;
- email ingestion does not yet explicitly scan deleted/trash and junk/spam as lower-trust recovery surfaces;
- Apple messages/calls need real-Mac validation;
- LinkedIn/social is still largely manual or cache-based;
- market/operator source ingestion is specified but not yet implemented as a repeatable feed.

### Passive RI Persistence

**RB 9.4 MVP shipped (2026-05-24).** The passive RI ingest layer is now live:

- `passive_ri_ingest.py` collects high/medium signals from `relationship_signals` and `linkedin_messaging`, normalizes them to RI event payloads, and appends through `ri_events.append()`.
- `passive_signal` source type added to `ri_events.py` VALID_SOURCE_TYPES.
- `refresh_all.py` runs `passive_ri_ingest.py` between `relationship_signals` and `daily_brief`.
- `daily_brief.py` shows passive RI proposed/blocked/duplicate counts in the Resource Verification scan row and in the "RI mutation and loop proof" item.
- Action-state language is enforced: only "RB proposed..." or "RB blocked..." — never "should be marked" or "would likely".

Remaining weaknesses (post-MVP):

- Passive events are proposed (not auto-applied) — confirmation via operator or future confirm-endpoint needed to project to `baseline_index`, `loop_ledger`, `active_threads`.
- Monitored transcript-folder pipeline not wired (`source_watch.py` inventory only).
- `last_touch` projection from confirmed passive events is not yet automated.
- LinkedIn social/profile signals not yet fed as passive_signal candidates (only messaging overlay is wired).

Target state: meaningful interactions from email, calendar, meeting transcripts, LinkedIn activity, text/call metadata, uploaded documents, CRM updates, notes, and interaction frequency automatically create RI events and projected state changes. Manual "capture RI" should become an exception path, not the normal path.

### Daily Brief Code Enforcement

The trust-first brief contract is now implemented in `daily_brief.py` and the Custom GPT prompt, but it must keep hardening through tests and richer source coverage.

Weaknesses:

- repeat-news suppression is not implemented;
- opportunity temperature is not computed;
- draft-ready actions are generated as specs but still need render-quality testing inside the GPT;
- macro/local lanes depend on curated `market_signals` rows rather than live monitored source feeds;
- action creation still requires explicit confirmation rather than a polished action-orchestration surface.

### Market / Operator Intelligence Is Specified, Not Operationalized

We now know the right source universe:

- social/LinkedIn;
- vertical hospitality sources;
- public-company primary data;
- restaurant-tech vendor watchlist;
- top restaurant brand/operator demand watch.

But the actual monitored company lists, source fetches, dedupe, freshness checks, and signal scoring still need to be built.

### Opportunity State Management

Active threads exist, but RB needs richer workflow state.

Missing states:

- `warming`
- `active`
- `waiting`
- `cooling`
- `stalled`
- `needs_decision`
- `suppress`

Each opportunity should carry next action, waiting-on, follow-up window, risk, owner/contact, and source evidence.

### New-User Onboarding Is Not Designed Yet

RB is currently being built around Todd as the reference implementation. That is the right order, but the reusable product will need a clear adoption path.

Weaknesses:

- no mostly automated install path for a non-technical user;
- no first-run wizard;
- no guided user profile / industry / preference intake;
- no baseline import and review workflow for a new user's network;
- no guided tour or best-practices document that helps a user gain value and trust quickly;
- no first-value report that proves RB is working before asking for deeper setup.

Canonical future-development doc:

```text
system/RB_ONBOARDING_AND_ADOPTION_PLAN.md
```

### User-Abstraction, Local Ownership, And Security Need Architecture

RB must become user-agnostic and configurable before productizing beyond Todd.

Core engine:

- judgment
- prioritization
- orchestration
- relationship intelligence
- workflow management
- source/provenance verification

User layer:

- industry context
- communication style
- strategic goals
- company data
- terminology
- integrations
- preferences

Persistence direction:

- downloadable user profiles;
- portable configuration/state;
- local-first or hybrid storage options;
- user-owned relationship graph, interaction history, operational memory, and strategic context;
- self-hosted deployment path where appropriate.

Security direction:

- authentication and access controls;
- encryption-ready storage boundaries;
- least-privilege ingestion connectors;
- secure monitored-folder ingestion;
- audit trail for source reads, mutations, and automation actions;
- permission boundaries between sources, projections, and model-rendered output.

## Development Gaps To Fill

### 1. RI Event Sourcing Foundation

Implement the event stream described in `system/protocols/P-021_ri_event_sourcing.md`.

Target path:

```text
system/ri_events/*.jsonl
```

Each event should carry:

- `event_id`
- `event_at`
- `captured_at`
- `source`
- `source_ref`
- `entity_type`
- `entity_id`
- `entity_match_status`
- `signal_type`
- `confidence`
- `strategic_relevance`
- `relationship_warmth_delta`
- `recommended_disposition`
- `persistence_status`
- `dedupe_key`

First event sources to support:

- manual LinkedIn/message signals;
- recruiting/job-opportunity conversation text;
- Fathom/Zoom transcript summaries;
- email/calendar relationship signals;
- touch/contact updates.

### 2. Passive RI Persistence And Mutation Projection

**MVP shipped in RB 9.4 (2026-05-24).** `passive_ri_ingest.py` is live.

Remaining work:

- Projection confirmation loop: once a passive RI event is proposed, wire confirmation to actually apply `last_touch`, loop, or thread mutations.
- Monitored transcript-folder: `source_watch.py` inventories but does not parse — wire `ri_intake.review()` per-artifact.
- `proposed` → `persisted` flow from passive events: operator confirm command or auto-confirm policy for high-confidence events (e.g. matched calendar interaction + high event_at_confidence).
- Projection proof: report what actually changed in `baseline_index`, not just what was proposed.

Source priorities for next sprint:
- meeting transcripts via monitored folder → `ri_intake.review()` with source_type=fathom_manual_paste / zoom_manual_paste;
- text/call metadata after P-019 Mac validation is confirmed;
- uploaded documents and notes.

### 3. Daily Brief Engine Hardening

The canonical trust-first structure is now implemented. Next hardening:

- repeat-news suppression;
- opportunity temperature;
- richer macro/local source feeds;
- GPT render QA for DraftSpec actions;
- "what RB recorded / updated / blocked" mutation proof in every section;
- task/loop creation flow from recommended actions.

### 4. Source Refresh Before Daily Brief

Make this the scheduled/morning path when local permissions allow:

```bash
python3 system/scripts/refresh_sources.py --all --refresh-signals
python3 system/scripts/daily_brief.py
```

Development work:

- verify real Mac messages/calls paths;
- verify email/calendar raw input success paths;
- add source-refresh status into the brief payload;
- fail gracefully when sources are skipped;
- never infer quiet from stale/skipped sources.

### 5. Repeat Suppression

Build a repeat-suppression layer for market/operator/news items.

Inputs:

- prior `today.md`;
- optional daily brief cache;
- source URL/title/company/date;
- signal type and recommendation.

Rules:

- repeat only with fresh source, material change, changed implication, or new action;
- otherwise omit or send to `What To Ignore`;
- do not replay broad market theses just because they remain true.

### 5. Opportunity Sensing, Temperature, and Workflow State

Extend active-thread/opportunity handling with a sensing layer that detects pain the user or the user's product/service can solve, then tracks workflow state.

Minimum fields:

- `thread_id`
- `company`
- `people`
- `source_type` (`market_news`, `job_posting`, `linkedin`, `email`, `calendar`, `earnings_call`, `press_release`, `company_blog`, `manual_note`)
- `pain_summary`
- `pain_type`
- `user_capability_match`
- `product_service_match`
- `opportunity_type` (`consulting`, `job`, `intro`, `content`, `partnership`, `relationship`)
- `temperature`
- `stage`
- `last_movement_at`
- `next_action`
- `waiting_on`
- `follow_up_window`
- `risk`
- `confidence`

Use this to power the Relationship / Operational Signal Review, Recommended Actions, and action-orchestration layers.

Job postings are first-class intelligence sources. A job posting can indicate an employment opportunity, a sales opportunity, a product/service pain signal, or a market movement signal depending on the selected user profile.

### 6. Restaurant-Tech Vendor and Operator Watchlists

Expand `system/restaurant_tech_watchlist.md` from definition into an actual monitored company set.

Build two watchlists:

- 30-50 restaurant-tech vendors;
- top restaurant brands/operators.

Each company should include:

- category;
- side (`vendor_supply` or `operator_demand`);
- public/private;
- source feeds to monitor;
- network proximity if known;
- why RB cares;
- current monitoring status.

### 7. Market / Operator Signal Scoring

Create a scoring pass for vendor/operator signals.

A signal should rank higher when:

- it affects an active thread;
- Todd has a relationship path into the company;
- it validates Todd's positioning;
- it reveals operator pain that technology can solve;
- it names AI/digital/labor/drive-thru/POS/back-office priorities;
- it creates a content, consulting, job, or outreach move.

It should rank lower when:

- mainstream-only;
- no relationship/action implication;
- repeated from prior days;
- generic macro noise.

### 8. Draft-Ready Execution Layer

For the top one or two moves, generate execution help:

- follow-up email;
- LinkedIn DM;
- intro ask;
- post angle;
- meeting opener;
- recruiter follow-up;
- scope-setting language for advisory/consulting conversations.

Keep this grounded in the actual source evidence and Todd's voice.

### 9. Canonical Write UX

Add safe review-first endpoints or CLI flows for:

- manual social/message RI event capture;
- opportunity/thread update;
- opportunity temperature update;
- proposed mutation queue;
- transcript-derived RI event persistence;
- future-meeting prep loop creation;
- daily brief injection/agenda-prep queue;
- relationship-risk tracking such as unpaid consulting drift;
- transcript action-item classification that promotes priority obligations but
  suppresses generic to-do noise;
- market/operator signal archive or suppression state.

The assistant must be able to say not only "this matters" but also "RB captured it" or "RB needs your confirmation to capture it."

Canonical language requirement:

- Avoid "should be marked", "would likely", and "could be" for system action.
- Use "RB recorded", "RB proposed", "RB did not persist this yet", or "pending confirmation".
- Separate advisory interpretation from RB system action every time.

### 10. New-User Onboarding and Adoption

After Todd's system stabilizes, build the onboarding/adoption layer described in `system/RB_ONBOARDING_AND_ADOPTION_PLAN.md`.

Deliver:

- mostly automated install and environment check;
- first-run wizard;
- user profile, role, preference, and industry intake;
- baseline import/review workflow;
- initial circles and active-thread setup;
- guided tour;
- best-practices document;
- first-value CoS report;
- onboarding smoke tests.

### 11. User Task Intake and Whole-Life Relationship Balance

Build the future opt-in layer described in `system/RB_WHOLE_LIFE_RELATIONSHIP_BALANCE_PLAN.md`.

Deliver:

- user-entered to-do classification;
- priority scoring for relationally relevant tasks;
- loop / waiting-on / daily-brief injection routing;
- suppression of generic task noise;
- opt-in settings for family, friends, service, church/community, health/wellbeing, recreation, and vacation;
- non-judgmental canonical language;
- privacy and surface controls;
- tests proving personal/life-balance prompts are disabled by default.

## Suggested Sprint Order

### Sprint 1 — Passive RI Persistence MVP

Goal: stop losing passive RI and make RB remember what it detects.

Deliver:

- `system/ri_events/*.jsonl`;
- event writer utility;
- manual LinkedIn/message event type;
- recruiting/opportunity event type;
- transcript summary event type;
- dedupe key and date-of-intelligence enforcement;
- review-first persistence status.
- canonical action language: recorded / proposed / blocked / unavailable.

### Sprint 2 — Ingestion Automation MVP

Goal: configure once, benefit continuously.

Deliver:

- monitored folder for transcript/document drops;
- Fathom/Zoom/Otter transcript classifier;
- transcript-to-RI extraction;
- transcript-to-loop extraction;
- source artifact preservation;
- mutation preview and confirmation path;
- smoke test with a synthetic meeting transcript.

### Sprint 3 — Opportunity State Model

Goal: make active opportunities operational.

Deliver:

- extend or complement `active_threads.yaml`;
- temperature/stage fields;
- follow-up windows;
- waiting-on fields;
- opportunity risks;
- recommended-action and loop-orchestration integration.

### Sprint 4 — Restaurant-Tech / Operator Intelligence

Goal: turn source rules into monitored signals.

Deliver:

- vendor watchlist;
- brand/operator watchlist;
- source feed definitions;
- public-company primary-data fetch/review path;
- vertical/social source intake path;
- market/operator signal scoring.

### Sprint 5 — User Abstraction, Local Ownership, And Security Baseline

Goal: keep single-user reference behavior out of the core engine.

Deliver:

- user profile/config split;
- profile naming convention and selected-profile loader;
- industry context profile;
- communication-style profile;
- opportunity-context profile;
- integration manifest;
- local persistence/portable-state design;
- access-control and audit-trail design;
- security review checklist for ingestion pipelines.

### Sprint 5b — Security, Privacy, And Agentic Architecture

Goal: make privacy/security part of RB's core architecture before adding broader autonomous execution.

Deliver:

- raw data vs derived intelligence vs durable memory data-class model;
- least-privilege OAuth scope review and integration manifest;
- retention classes for raw, normalized, derived, durable, audit, and forgettable data;
- prompt-injection guardrails for external content;
- explicit action gates for email, files, Drive, CRM, calendar, and profile mutations;
- audit log for access, persistence, rejection, and mutation events;
- delete/forget workflow design;
- tests for sensitive raw-content non-persistence and inferred-vs-verified labeling.

Reference: `system/CLAUDE_SPRINT_RB_9_13_SECURITY_PRIVACY_AGENTIC_ARCHITECTURE.md`.

### Sprint 6 — Draft-Ready Actions

Goal: make the brief save execution energy.

Deliver:

- templates and generation rules for top-move drafts;
- post angle generation;
- follow-up email/DM generation;
- intro ask generation;
- confirmation/persistence path for actions taken.

### Sprint 7 — New-User Onboarding

Goal: make RB usable and trustworthy for someone other than Todd.

Deliver:

- new-user bootstrap flow;
- install checker;
- profile and industry intake templates;
- baseline setup/review flow;
- guided tour and best-practices docs;
- first-value report.

### Sprint 8 — User Task Intake and Whole-Life Balance

Goal: let RB track explicit priority tasks and optional non-business relationship commitments without becoming a generic to-do list.

Deliver:

- task classification schema;
- user-entered task priority rules;
- opt-in life-domain settings;
- daily-brief rendering for enabled personal/life-balance prompts;
- guardrail tests for disabled-by-default behavior and non-judgmental wording.

## Definition Of Indispensable

RB is indispensable when Todd starts the day with:

- resource verification he trusts;
- clear knowledge of what changed;
- relationship and operational signals tied to provenance;
- macro/local/industry context connected to his network and goals;
- no repeated noise;
- fresh-source confidence;
- action-ready drafts;
- one useful question that improves tomorrow;
- assurance that important relationship intelligence was captured or queued.

If the brief cannot answer "what did RB check, what did RB update, what changed, what matters, what should I do, what should I ignore, and what should become a loop?" it is not done.
