# Claude Handoff — RB Operationalization

**Date:** 2026-05-25  
**Status:** RB 9.9 complete. RI Assessment Doctrine and Trust Display Hardening shipped. All acceptance criteria met. Pending host-terminal commits (see `DIRTY_TREE_AUDIT_RB_9_9.md`).  
**Primary direction:** ChatGPT is the operator-facing interface. Claude is the software architect and implementation engineer. Do not begin 10.x feature work until 9.9 commits are clean.

**Latest focused handoffs — read these first:**

- `system/CLAUDE_HANDOFF_2026-05-25_RB_9_9_RI_ASSESSMENT_TRUST_DISPLAY.md` — **CURRENT SPRINT (COMPLETE).** P-036 protocol, ri_assessment contract on all signals and ingest events, 7-status daily brief proof rows, canonical response language guards, 8 test traces, dirty tree audit, .gitignore cleanup. Commit sequence in `DIRTY_TREE_AUDIT_RB_9_9.md`.
- `system/CLAUDE_HANDOFF_2026-05-24_RB_9_6_MORNING_PIPELINE_AND_TASK_DELIVERY.md` — COMPLETED sprint (2026-05-24). Morning pipeline and task delivery.
- `system/CLAUDE_HANDOFF_2026-05-24_RB_9_5_CANONICAL_RESPONSE_HARDENING_COMPLETE.md` — COMPLETED sprint (2026-05-24). Canonical response hardening.
- `system/CLAUDE_HANDOFF_2026-05-24_RB_9_4_PASSIVE_RI_PERSISTENCE_MVP.md` — COMPLETED sprint (2026-05-24). Passive RI ingest layer.
- `system/RB_9_0_STRATEGIC_DIRECTION_CHIEF_OF_STAFF_PLATFORM.md` — foundational strategic direction. Still the canonical product north star.

**Next sprint candidates (10.x — do not start until 9.9 is committed):**

1. Extend `confirm_events()` to update `ri_assessment.status` → `recorded` after confirmed projection
2. Wire `ri_assessment` into `market_signals.py` for people/company signal hits
3. Add `ri_assessment` blocks to `linkedin_own_engagement.py` and `linkedin_session_reader.py` at the engagement-row level
4. Close RB-DEFECT-001 (API availability regression — all HTTP endpoints blocked)

## Executive Summary

RB has moved from a static relationship memory system toward a Strategic Relevance Intelligence Engine.

The working system now has:

- a local FastAPI surface exposed through a Cloudflare quick tunnel for ChatGPT Actions
- authenticated read/write actions working from a private Custom GPT
- deterministic daily brief, DRR, loops, intro, network gap, social, email/calendar, and interaction overlays
- controlled mutation endpoints for loops, touches, contacts, threads, social inputs, and sessions
- protocol coverage for conversation artifact ingestion, RI event sourcing, and legacy RB transfer documents
- folder monitoring for legacy RB 8.0/RB 9.0 transfer files

Most important product discovery from live testing:

RB's next leap is not more summary. It is last-24-hour strategic signal interpretation.

Important tenet refinement:

RB should never guess, but it also should not stop at "I don't know" when a narrow question can resolve the uncertainty. The operating model is micro-reconciliation: ask the smallest useful question, capture the answer, and improve the graph.

The system must behave like a measured Chief of Staff:

- "What changed since yesterday?"
- "Why does it matter?"
- "Who does it affect?"
- "What should Todd do today?"
- "What should Todd ignore?"
- "Where is opportunity forming?"
- "What small clarification would make the next recommendation more accurate?"

Runtime cleanup:

`start RB` is deprecated. RB 9.0 should initialize automatically through manifest/status/protocol checks. Do not resurrect RB 8.x latch/heartbeat language as a user-facing ritual. Keep real health/status checks; drop ceremonial startup commands.

## Commits Since ChatGPT Operationalization

- `d0244b2` — Checkpoint RB ChatGPT operational bridge
- `58cd0f2` — Capture Ish Maho signal and backlog intelligence features
- `869da37` — Normalize active thread empty people list
- `1d23c72` — Normalize active thread people ids
- `554dccc` — Add conversation artifact ingestion protocol
- `b8bfdd5` — Define RI event sourcing architecture
- `a47542d` — Add legacy RB thread transfer protocol
- `3ab9458` — Monitor legacy RB transfer folder

## Current Runtime State

Local API:

```bash
RB_API_KEY=localtest python3 -m uvicorn system.api.server:app --host 127.0.0.1 --port 8765
```

Cloudflare tunnel used in ChatGPT Actions:

```text
https://physical-simpson-abu-planner.trycloudflare.com
```

The quick tunnel is temporary and can disappear when the local process stops. For production, replace it with a durable tunnel or hosted API.

Verified Action behavior:

- `health` succeeded.
- `getDailyBrief` returned the full 2026-05-18 structured brief.
- `closeLoop` successfully closed `L-2026-05-08-017`.
- Intro/testing prompt "Who can get me into Toast?" surfaced Bob Gibson correctly.
- Follow-up prompt "Why is Bob Gibson the best path into Toast?" produced the right reasoning using direct Toast proximity, higher DRR, active thread relevance, and strategic fit.
- `drr_score?id=ish-singh` works after active-thread parser fixes and returns Ish boosted by the Maho thread.

Current testing issue:

Todd cannot export a full verbatim RB/ChatGPT test transcript from the current interface. This makes it hard to show Codex/Claude exactly what happened. RB 8.0 had trace mode / developer-level logs; RB 9.0 needs that capability restored.

First captured test trace:

```text
system/test_traces/2026-05-18-rb9-interface-persistence-transcript-test.md
```

This trace documents two defects:

- `D-2026-05-18-001` — statefulness theater / retrieval bypass. RB answered network, job-search, and top-contact questions from conversational inference instead of retrieving the baseline/contact graph.
- `D-2026-05-18-002` — false verbatim transcript export. RB generated a reconstructed approximation while implying a full raw transcript had been exported.

Prompt-level mitigation already added:

- network/top-contact/job-search/gap questions must call RB API first
- strategic answers should label grounding as evidence-backed, inference-backed, or blocked pending retrieval
- transcript exports must distinguish raw transcript, reconstructed summary, and operational test trace

Second captured test trace:

```text
system/test_traces/2026-05-18-rb9-daily-brief-cos-vs-news-test.md
```

This trace documents the daily brief architecture failure:

- RB generated relevant restaurant-tech / AI / payments news commentary.
- It did not retrieve or apply the relationship graph.
- It did not identify contact-level implications, relationship opportunities, network-building moves, executive priorities, or next-best actions.

Core product lesson:

```text
RB's value is not information retrieval.
RB's value is strategic judgment augmentation.
```

Claude should treat this as a canonical briefing redesign requirement: industry intelligence must be subordinate to relationship and action intelligence.

## Important Product Findings From Testing

### 1. Daily Brief Must Include Relationship Signals Since Yesterday

The Ish/Maho email should have surfaced automatically.

User-provided email:

Ish Singh responded warmly, accepted Todd's operational/product framing, invited a deeper dive on Todd's background and thoughts on Maho, and provided Calendly access.

Strategic interpretation:

- relationship warmth increased
- opportunity surface expanded
- Todd's positioning resonated
- follow-up window is active now
- should be a daily-brief priority

This is the next high-value feature.

### 2. Email Intake Must Be Semantic, Not Inbox Management

RB should not summarize every email.

It should classify and elevate only strategically relevant messages:

- known RC/LKI/LMI contact signal
- active-thread movement
- recruiting/job opportunity
- intro/referral
- collaboration or consulting opening
- emotional tone shift
- follow-up obligation
- silence after expected response window
- low relevance/noise

### 3. Job-Seeker Intelligence Is a High-Value Daily Brief Layer

Todd receives many potential-position emails. RB should evaluate:

- role fit against Todd's operator + restaurant-tech + sales/GTM background
- company relevance
- ecosystem strength
- relationship paths into the company
- whether to apply, ignore, investigate, or pursue through a warm path

This should be part of the daily brief, not a separate inbox workflow.

### 4. Conversation Artifact Import Is Needed

Todd has Fathom transcripts and wants to support multiple notetakers:

- Fathom
- Zoom native transcript/chat
- Otter
- Fireflies
- Teams
- manual notes

RB should watch local folders, detect new files, extract candidate relationship intelligence, and present reviewable recommendations before writing canonical state.

Protocol added:

```text
system/protocols/P-020_conversation_artifact_ingestion.md
```

### 5. Event-Sourced Relationship Intelligence Is The Correct Architecture

Key principle:

Every RI signal should become a timestamped event.

Relationship Cards, DRR, loops, active threads, and daily brief outputs should become projections from an immutable relationship event stream.

Protocol added:

```text
system/protocols/P-021_ri_event_sourcing.md
```

Planned event stream:

```text
system/ri_events/*.jsonl
```

## Highest Priority Next Build Sequence

### 0. Restore Trace Mode / Developer Logs

Before deeper interface testing scales, restore RB 8.0-style trace export.

Minimum viable trace:

- user prompt verbatim
- RB answer verbatim
- action/tool operation selected
- endpoint called
- request parameters with secrets redacted
- response status
- compact response body summary
- mutation confirmation and verification state
- timestamp
- user-observed issue

Suggested paths:

```text
system/test_traces/*.md
system/test_traces/*.json
```

Suggested command in ChatGPT:

```text
Export this test session for Codex.
```

This does not need to expose hidden chain-of-thought. It needs to expose the operational trace: what the interface asked, what the API did, what RB said, and what failed.

### 1. Build Relationship Signals From The Last 24 Hours

Suggested script:

```text
system/scripts/relationship_signals.py
```

Core output:

- `contact_id`
- `name`
- `source`
- `event_at`
- `signal_type`
- `signal_strength`
- `strategic_relevance`
- `recommended_action`
- `reasoning`
- `evidence`
- `confidence`
- `reconciliation_prompt` when classification or mutation is unclear

Initial sources:

- LinkedIn/social activity exports or overlays
- `system/inbox/email.*.json`
- `system/inbox/calendar.*.json`
- `system/inbox/messages.json`
- `system/inbox/calls.json`
- `system/inbox/social.feed.json`
- `system/active_threads.yaml`
- `system/baseline_index.json`

Initial test cases:

- Ish Singh / Maho high-signal email
- Christian Jackson / Global Payments interview email
- Jeff Wayman interaction-volume last-touch update
- Bob Gibson / Toast strategic thread

### 2. Add Daily Brief Section

Daily brief should include:

```text
Relationship Signals From The Last 24 Hours
Reconciliation Needed
```

Expected examples:

- Ish Singh — high-signal inbound; strategic openness increased; schedule Maho deep dive.
- Christian Jackson — process advanced to hiring-manager interview; prepare for Ryan Hildebrand.
- Jeff Wayman — real interaction volume materially newer than recorded last_touch; update relationship state.
- LinkedIn/social — high-signal post/comment/engagement from a known RC/LKI that changes relationship warmth, opportunity timing, or network-building priority.
- Calendar — meeting added, shifted, missed, completed, or involving a not-yet-in-baseline person with strategic relevance.

Reconciliation examples:

- Ish Singh — should this Maho exchange be treated as an active opportunity thread, a relationship-warming event, or both?
- Jeff Wayman — should May 12 interaction volume update `last_touch`, or was it not a meaningful touch?
- Bob Gibson — confirm current Toast proximity before making a stronger outreach recommendation.

### 3. Add API Endpoint

Suggested endpoint:

```text
GET /relationship_signals?date=YYYY-MM-DD
```

OpenAPI operation:

```text
getRelationshipSignals
```

ChatGPT usage:

- "What changed since yesterday?"
- "What high-signal relationship events should I care about?"
- "What am I missing today?"

### 4. Add Review-First Email Ingestion

Do not let RB indiscriminately ingest all email.

Build a review-first pipeline:

1. detect candidate high-signal emails
2. classify signal type
3. map to contact/company/thread
4. propose RI event and/or loop
5. require confirmation before mutation

### 5. Then Implement RI Event Stream

Once relationship signals are useful, persist them as immutable events.

Do not wait for perfect event sourcing before shipping value. Use the signal layer as the bridge to event sourcing.

## Interface Testing To Continue While Development Is Parked

Use ChatGPT RB as the product surface and test whether it behaves like a real Chief of Staff.

### Daily Command Center Tests

Ask:

```text
What am I forgetting right now? Prioritize only things that matter today.
```

Evaluate:

- Does it surface hard calendar obligations first?
- Does it suppress low-value noise?
- Does it distinguish urgent vs strategic?
- Does it cite why?

### Relationship Reasoning Tests

Ask:

```text
Who can get me into Toast?
```

Then:

```text
Why is Bob Gibson the best path? Show the reasoning, not just the name.
```

Evaluate:

- Does it call the intro/DRR tools?
- Does it explain direct proximity, relationship strength, active thread relevance, and strategic fit?
- Does it avoid generic networking advice?

### Strategic Gap Tests

Ask:

```text
What relationship gaps are most strategically important right now?
```

Evaluate:

- Does it move beyond missing fields?
- Does it identify ecosystem gaps, sponsor gaps, operator-side gaps, and buyer-access gaps?

### Email Signal Tests

Paste high-signal emails manually and ask:

```text
Should RB have surfaced this in the daily brief?
What signal type is this?
What action should it recommend?
Should this create a loop, active thread, or RI event?
```

Evaluate:

- Does the model understand understated strategic warmth?
- Does it avoid overreacting?
- Does it recommend concrete next action?

### Write-Back Tests

Only after clear confirmation, test:

```text
Close loop L-... because ...
```

Then verify:

```text
Show me open overdue loops.
```

Expected behavior:

- state proposed write before execution
- call the write endpoint
- confirm success
- re-read state
- report final outcome

## Known Weaknesses / Constraints

- ChatGPT Actions can call the API, but the tunnel is temporary.
- Email/calendar overlays are not yet true semantic daily-brief inputs.
- The daily brief can surface stale email overlays if fetch data has not been refreshed.
- DRR scoring works but weights remain heuristic.
- Relationship evidence is not yet deeply exposed in API responses.
- Raw transfer documents and conversation artifacts are intentionally git-ignored.
- Legacy RB transfer files are monitored, not semantically ingested.
- Event sourcing is designed but not implemented.

## Files Claude Should Read First

```text
system/STATUS.md
system/OPERATIONALIZATION.md
system/CLAUDE_HANDOFF.md
system/protocols/P-020_conversation_artifact_ingestion.md
system/protocols/P-021_ri_event_sourcing.md
system/protocols/P-022_legacy_thread_transfer.md
system/api/README.md
system/api/custom_gpt_prompt.md
system/api/openapi.yaml
```

Then inspect:

```text
system/scripts/daily_brief.py
system/scripts/email_overlay.py
system/scripts/calendar_overlay.py
system/scripts/interaction_overlay.py
system/scripts/drr_score.py
system/scripts/intro_engine.py
system/scripts/mutations.py
system/api/server.py
```

## Do Not Do Next

- Do not build generic news summarization.
- Do not ingest every email into canonical memory.
- Do not promote legacy RB transfer docs directly into canonical state.
- Do not overbuild event sourcing before the relationship-signals feature proves the shape.
- Do not turn RB into a CRM UI.
- Do not make ChatGPT the software engineer. ChatGPT is the operator interface and reasoning surface.

## Recommended Claude Task When Tokens Return

Implement `Relationship Signals Since Yesterday` end to end:

1. script
2. cache
3. daily brief section
4. API endpoint
5. OpenAPI action
6. tests/smoke path
7. documented examples using Ish/Maho, Global Payments, Jeff Wayman, and Bob Gibson

That is the next best product move because it creates immediate daily value while preparing RB for RI event sourcing.
