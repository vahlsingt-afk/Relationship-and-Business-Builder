# Claude Packet - RB 9.2 Architecture Brief

Date: 2026-05-23
Audience: Claude
Purpose: Inspect the completed RB 9.1 sprint results and design the RB 9.2 architecture for Codex to implement.

## Instruction To Claude

Please do not treat this as another feature brainstorming pass. The ask is to inspect the current repo state, confirm what 9.1 completed, and produce a concrete RB 9.2 architecture and execution plan that Codex can complete one priority at a time.

RB 9.2 should focus on the morning operating experience:

- source refresh instrumentation
- native ChatGPT Task delivery
- correct ChatGPT cockpit behavior
- email as a backup notice only
- end-to-end daily brief proof

The core product posture remains: RB is a Chief of Staff for relationships, not a news digest.

## Current State

RB 9.1 is implemented through Priority 5 and smoke-green.

Canonical 9.1 handoff:

- `system/CLAUDE_HANDOFF_2026-05-23_RB_9_1_STRATEGIC_OPERATOR_LAYER.md`

Status file:

- `system/STATUS.md`

Important delivery docs:

- `system/automation/CHATGPT_TASK_DAILY_BRIEF_PUSH.md`
- `system/protocols/P-001_daily_brief_regen.md`
- `system/api/custom_gpt_prompt.md`

Strategic operator layer:

- `system/strategic_operators.yaml`
- `system/schemas/strategic_operators.schema.json`
- `system/scripts/strategic_operators.py`
- `system/scoring/strategic_operator_rubric.md`
- `system/protocols/P-031_strategic_operator_intelligence.md`

## What 9.1 Shipped

### Persistent Strategic Operator Lane

`system/strategic_operators.yaml` now exists as the canonical persistent operator entity state. It is intentionally separate from `market_signals`.

This was the key 9.1 architecture decision:

- `market_signals` is the ephemeral market/news/source lane.
- `strategic_operators` is the persistent relationship-aware entity lane.

Seeded entities:

- Flynn Group
- Carrols Restaurant Group
- Sailormen
- Ghai Management
- Thrive Restaurant Group
- Sizzling Platter
- KBP Foods

### Schema And Validation

`system/schemas/strategic_operators.schema.json` locks the entity shape.

`system/schemas/validate.py` now validates strategic operators and handles YAML date coercion safely.

### Mutation Contract

`system/scripts/mutations.py` now supports:

- `operator-add`
- `operator-update`
- `operator-record-movement`
- `operator-close`

These follow P-009 snapshot-then-validate-then-rollback discipline.

`system/api/server.py` exposes `applyOperatorMutation`.

`system/api/openapi_gpt.yaml` remains capped at 30 operations. `getRecentTestTraces` was removed to make room for `applyOperatorMutation`.

### Strategic Operator Overlay

`system/scripts/strategic_operators.py` computes the strategic operator overlay from:

- `strategic_operators.yaml`
- `system/inbox/market_signals.json`
- `system/active_threads.yaml`
- `system/baseline_index.json`

The overlay produces:

- recent movements
- relationship proximity
- mutual connection hints
- existing vendor relationship hints
- categorical scoring
- reconciliation prompts

### Scoring Discipline

`system/scoring/strategic_operator_rubric.md` defines categorical buckets only:

- `low`
- `medium`
- `high`
- `critical`

Numeric scores are intentionally deferred until there is calibration data.

### Daily Brief Split

The Daily Brief now has a top-tier `strategic_operator_movements` section generated from the persistent operator overlay.

Vendor/macro/market news stays in the market lane.

This is important: the operator section is not a second rendering path for news. It is entity-grounded CoS intelligence.

### Protocol And Settings

`P-031_strategic_operator_intelligence.md` defines the read/write contract.

`settings.json` now has a `daily_briefing.strategic_operators` block.

`refresh_all.py` now includes strategic operator overlay refresh.

## Validation Evidence

Previously verified locally:

```bash
python3 system/scripts/strategic_operators.py --smoke
python3 system/schemas/validate.py --strategic-operators-only
python3 system/scripts/daily_brief.py --smoke
python3 system/scripts/validate_openapi_gpt.py
python3 system/scripts/refresh_all.py --date 2026-05-23
python3 system/scripts/api_smoke_test.py
```

Expected results:

- strategic operator smoke: 0 failures
- strategic operator schema: 7 valid items
- daily brief smoke: 0 failures
- GPT OpenAPI spec: 30 operations
- API smoke: 37 endpoints pass

## Important Product Issue Discovered After 9.1

The email link still does not produce the full daily brief in ChatGPT.

What currently happens:

1. External RB email opens the Relationship Bridge custom GPT.
2. The GPT opens to a blank start screen.
3. The user still has to send: `Show today's RB Daily Brief.`

This is a platform constraint of external email links into a custom GPT. The email cannot force ChatGPT to auto-run the command or display the generated brief.

The local repo has been updated to make this honest:

- `system/scripts/publish.py` now describes the external email as a cockpit link plus command, not as a direct brief link.
- `system/api/custom_gpt_prompt.md` now instructs the GPT to immediately call `getDailyBrief` when the user sends the daily brief command.
- `system/automation/CHATGPT_TASK_DAILY_BRIEF_PUSH.md` now distinguishes native ChatGPT Task delivery from external RB backup email.

But the actual live GPT Builder instructions may still need to be updated and published manually.

## New Delivery Target

The user showed an example native ChatGPT Task email:

- sender: `ChatGPT <noreply@tm.openai.com>`
- CTA: `View message`
- clicking opens the generated ChatGPT task result, not a blank custom GPT start screen

That is the desired RB morning UX.

Architecture implication:

External RB email cannot be the canonical UX. It should be backup only.

Canonical UX should be a native ChatGPT Task result message that contains the full RB Daily Brief or a task-generated retrieval of the full brief.

## RB 9.2 Primary Objective

Design and implement the missing morning operating layer:

source refresh -> daily brief generation -> publish artifacts -> native ChatGPT Task result -> optional backup email -> user can act in ChatGPT.

The result should feel like:

> RB put today's Chief of Staff brief directly into ChatGPT.

Not:

> RB emailed me a link to a GPT where I have to figure out what to ask.

## Proposed RB 9.2 Workstreams

### 1. Native ChatGPT Task Delivery Architecture

Claude should decide the concrete retrieval architecture.

Open decision:

- Can a native ChatGPT Task call the RB Custom GPT action directly?
- If not, should RB expose a secure hosted endpoint specifically for task retrieval?
- How should authentication work for that endpoint?
- Should the endpoint return the full rendered brief, structured JSON, or both?

Required output from Claude:

- exact native ChatGPT Task prompt
- retrieval path
- authentication model
- expected success and failure behavior
- what Codex must implement locally
- what Todd must configure manually in ChatGPT, if anything

Acceptance criteria:

- ChatGPT Task notification/email opens a task result that already contains the RB Daily Brief.
- The brief is visible without requiring Todd to type the daily brief command.
- External RB email remains available only as a backup doorbell.

### 2. Source Refresh Instrumentation

RB 9.2 should close the practical source gaps needed for a trustworthy morning brief.

Known source gaps:

- macOS Full Disk Access for Python/LaunchAgent path
- personal Gmail raw connector capture
- calendar raw connector capture
- SMS messages
- phone logs
- LinkedIn messaging export ingestion
- LinkedIn own-post engagement

Important nuance:

`system/scripts/linkedin_messaging.py` exists, is wired, and smokes green. The issue is not "parser missing." The issue is that no real LinkedIn export has been ingested yet.

Relevant docs/code to inspect:

- `system/automation/README.md`
- `system/protocols/P-019_apple_interaction.md`
- `system/inbox/README.md`
- `system/scripts/refresh_sources.py`
- `system/scripts/refresh_all.py`
- `system/scripts/linkedin_messaging.py`

Required output from Claude:

- minimal FDA setup architecture
- raw connector capture contract for Gmail and calendar
- LinkedIn export ingest path
- source readiness gating rules for daily brief quality
- what remains manual versus automated

Acceptance criteria:

- RB can say which sources refreshed today, which were stale, and why.
- Daily Brief source health distinguishes unavailable, under-instrumented, stale, and refreshed.
- LinkedIn messaging export ingest is exercised on a real export.

### 3. Morning Path End-To-End Test

RB 9.2 should define a single end-to-end acceptance path:

1. Refresh available sources.
2. Generate `system/today.md`.
3. Generate/update `system/MANIFEST.md`.
4. Publish Daily Brief artifacts.
5. Deliver native ChatGPT Task result.
6. Send backup email only if configured.
7. Open ChatGPT result and verify the brief is visible.
8. Verify the custom GPT fallback command works: `Show today's RB Daily Brief.`

Required output from Claude:

- test script or checklist
- pass/fail criteria
- artifact locations
- exact user-facing wording for failures

### 4. GPT Cockpit Prompt And Spec Hygiene

The custom GPT should remain the operational cockpit.

Known requirement:

- When Todd sends `Show today's RB Daily Brief.`, the GPT must call `getDailyBrief` immediately.

Claude should inspect:

- `system/api/custom_gpt_prompt.md`
- `system/api/openapi_gpt.yaml`
- `system/scripts/validate_openapi_gpt.py`

Acceptance criteria:

- GPT spec remains at or below 30 operations.
- Daily brief command works reliably.
- GPT instructions do not promise direct-link behavior that the platform cannot provide.

### 5. STATUS And Protocol Cleanup

Some status text is now stale because the delivery strategy changed.

Claude should inspect and update architecture guidance for:

- native ChatGPT Task as canonical delivery
- external RB email as backup notice
- source instrumentation as RB 9.2 scope
- remaining host-side scheduler/email transport boundaries

Likely files:

- `system/STATUS.md`
- `system/automation/CHATGPT_TASK_DAILY_BRIEF_PUSH.md`
- `system/protocols/P-001_daily_brief_regen.md`
- possibly a new `P-032_native_chatgpt_task_delivery.md`

## Suggested RB 9.2 Sprint Shape

Codex should not mix all concerns in one giant patch. Recommended sequencing:

1. Native ChatGPT Task retrieval architecture and protocol.
2. Source refresh contracts and source health model.
3. LinkedIn export real-ingest path and smoke.
4. End-to-end morning path test harness.
5. STATUS, handoff, and memory updates.

Each priority should be smoke-tested before moving on.

## Non-Goals For 9.2

Do not reopen the 9.1 strategic operator architecture unless a real defect is found.

Do not add numeric strategic operator scores.

Do not turn the Daily Brief into a generic restaurant industry newsletter.

Do not pretend external email can deep-link into a prefilled custom GPT message.

Do not expand `daily_brief.py` substantially if a smaller module can own the new behavior.

Do not mark email or push delivery successful unless the transport path reports success.

## Questions Claude Should Answer

1. What is the exact native ChatGPT Task architecture for RB Daily Brief delivery?
2. Can a ChatGPT Task retrieve the daily brief through the RB Custom GPT action, or does RB need a separate hosted retrieval endpoint?
3. If a hosted endpoint is needed, what is the smallest secure contract Codex should build?
4. What should the native ChatGPT Task prompt be?
5. What should the fallback external email say, exactly?
6. Which source refresh gaps belong in 9.2 versus a later sprint?
7. What is the minimum credible source-health model for morning trust?
8. Should `refresh_sources.py` stay local-only in 9.2, or should there be a gated API action?
9. What STATUS/protocol updates are required before Codex implements?
10. What is the final 9.2 acceptance test?

## Requested Claude Deliverable

Please produce a concrete architecture document for Codex, preferably:

- `system/CLAUDE_ARCHITECTURE_2026-05-23_RB_9_2_MORNING_DELIVERY_AND_SOURCE_INSTRUMENTATION.md`

It should include:

- sprint objective
- prioritized workstreams
- files to modify
- API/spec changes
- source contracts
- security/auth decisions
- acceptance tests
- explicit non-goals
- one-priority-at-a-time implementation order for Codex

## Final Framing

RB 9.1 made the intelligence layer more durable and CoS-grade.

RB 9.2 should make the morning experience operationally real:

- refreshed sources
- visible ChatGPT-native brief
- honest backup email
- clear source health
- end-to-end proof

The north star is still:

> an operator-savvy Chief of Staff for relationships

not:

> a scheduled background search with an email summary
