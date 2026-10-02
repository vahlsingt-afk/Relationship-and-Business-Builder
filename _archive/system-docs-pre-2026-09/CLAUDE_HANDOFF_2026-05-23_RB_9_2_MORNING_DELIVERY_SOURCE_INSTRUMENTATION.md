# Claude Handoff - 2026-05-23 - RB 9.2 Morning Delivery and Source Instrumentation

## Sprint State

RB 9.2 is implemented and smoke-green through Priority 5.

Core framing accepted and implemented:

- Native ChatGPT Task delivery is the canonical morning UX.
- External RB email is backup doorbell only, not the full brief surface.
- Source refresh now reports health explicitly through `source_health.json`.
- The Daily Brief must not claim a quiet day when Tier 1 or Tier 2 sources are stale, skipped, or failed.
- Morning readiness is now testable end to end with `morning_path_test.py`.

The result is an operational morning path:

```text
05:00 CT host refresh/publish -> cached daily brief + health file
05:05 CT ChatGPT Task -> getDailyBrief via GPT Action -> native ChatGPT result
backup email -> doorbell only, if transport is configured
```

## Files Added

- `system/scripts/morning_path_test.py`
- `system/protocols/P-032_native_chatgpt_task_delivery.md`
- `system/protocols/P-033_linkedin_messaging_export_ingest.md`
- `system/.cache/source_health.json` (runtime, git-ignored)
- `system/.cache/morning_path_test.json` (runtime, git-ignored)
- `system/published/daily/latest_brief.json` (runtime artifact)

## Files Modified

- `system/scripts/refresh_sources.py`
- `system/scripts/daily_brief.py`
- `system/scripts/refresh_all.py`
- `system/scripts/linkedin_messaging.py`
- `system/scripts/publish.py`
- `system/scripts/api_smoke_test.py`
- `system/scripts/validate_openapi_gpt.py`
- `system/api/server.py`
- `system/api/openapi.yaml`
- `system/api/openapi_gpt.yaml`
- `system/protocols/P-001_daily_brief_regen.md`
- `system/protocols/index.json`
- `system/automation/CHATGPT_TASK_DAILY_BRIEF_PUSH.md`
- `system/STATUS.md`

## What Shipped

### Priority 1 - Source Health Model

`refresh_sources.py` now accepts:

```bash
python3 system/scripts/refresh_sources.py --all --save-health
```

It writes `system/.cache/source_health.json` with:

- `generated_at`
- `overall_health`
- `brief_trustworthiness`
- `staleness_thresholds_hours`
- per-source rows with `status`, `last_refreshed_at`, `tier`, and optional `reason`

Health levels:

- `green`
- `partial`
- `under_instrumented`
- `not_configured`

Current real state after the sprint:

- `overall_health`: `under_instrumented`
- `brief_trustworthiness`: `under_instrumented`
- Tier 1 refreshed: `email:personal`, `email:bridgepoint`, `calendar:bridgepoint`
- Tier 1 gap: `calendar:personal` has no raw calendar capture
- Tier 2 stale/gap: messages and calls are stale; LinkedIn messaging export is missing

`daily_brief.py` now reads source health and opens Daily Prep Summary with Tier 1 stale/skipped/failed sources when trustworthiness is under-instrumented.

`refresh_all.py` now begins with:

```bash
refresh_sources.py --all --save-health
```

### Priority 2 - LinkedIn Messaging Real-Ingest Path

`linkedin_messaging.py --ingest <messages.csv>` now writes the normalized inbox JSON by default instead of requiring `--confirm`.

Expected operator command after Todd drops a real export:

```bash
python3 system/scripts/linkedin_messaging.py --ingest system/inbox/linkedin_messages_export.csv
```

The command prints:

- total rows
- matched contacts
- unmatched recurring participants
- last-touch proposals
- outbound evidence rows

It writes:

- `system/inbox/linkedin.messages.json`

It does not auto-apply last-touch updates. Review remains separate.

`refresh_sources.py --social` now tells the operator the exact ingest command if `linkedin.messages.json` is missing.

Added P-033 to document the export -> ingest -> review -> apply flow.

Manual blocker remaining: no real `system/inbox/linkedin_messages_export.csv` is present yet, so the real-export acceptance step is Todd-side.

### Priority 3 - Morning Path Test

Added:

```bash
python3 system/scripts/morning_path_test.py
```

Automated checks:

1. `refresh_sources.py --all --save-health`
2. `daily_brief.py --smoke`
3. `publish.py --write --confirm`
4. `GET /daily_brief?use_cache=true` through FastAPI TestClient
5. `GET /brief/health` through FastAPI TestClient

Manual checks printed:

- ChatGPT Task verification: native "View message" result opens the brief inline.
- GPT fallback command verification: `Show today's RB Daily Brief.` returns the brief.

Latest result:

```text
Morning path: 5/5 automated steps passed
```

### Priority 4 - GPT Cockpit Spec and Health Endpoints

Added FastAPI endpoints:

- `GET /brief/health`
- `GET /brief/latest-json`

`publish.py` now writes:

- `system/published/daily/latest.html`
- `system/published/daily/latest_brief.json`

OpenAPI behavior:

- `openapi.yaml` includes both endpoints.
- `openapi_gpt.yaml` includes `/brief/health`.
- `openapi_gpt.yaml` does not expose `/brief/latest-json`.
- GPT spec validates at 27 operations, under the 30-operation Custom GPT limit.

### Priority 5 - Status and Protocol Cleanup

Updated:

- `STATUS.md`
- P-001 daily brief regeneration
- P-032 native ChatGPT Task delivery
- P-033 LinkedIn messaging export ingest
- `CHATGPT_TASK_DAILY_BRIEF_PUSH.md`
- `protocols/index.json`

P-032 documents the canonical delivery contract:

- Create the task inside the Relationship Bridge Custom GPT.
- Fire at 5:05 AM CT.
- Call `getDailyBrief`.
- Render the canonical brief inline.
- Use external RB email only as backup.
- Require a stable Cloudflare named tunnel.

## Verification

Green checks run:

```bash
python3 system/scripts/refresh_sources.py --all --save-health
python3 system/scripts/daily_brief.py --smoke
python3 system/scripts/linkedin_messaging.py --smoke
python3 system/scripts/publish.py --smoke
python3 system/scripts/validate_openapi_gpt.py
python3 system/scripts/api_smoke_test.py
python3 system/scripts/morning_path_test.py
```

Results:

- source health written: yes
- daily brief smoke: 0 failures
- LinkedIn messaging smoke: 0 failures
- publish smoke: 0 failures
- GPT OpenAPI: OK, 27 ops
- API smoke: all 39 endpoints pass
- morning path: 5/5 automated steps pass

## Manual Todd-Side Work Remaining

These are not Codex blockers, but must happen before the full product experience is complete:

1. Create a Cloudflare named tunnel and update `openapi_gpt.yaml -> servers[0].url`.
2. Republish the Custom GPT Actions schema.
3. Create the native ChatGPT Task inside the Relationship Bridge Custom GPT at 5:05 AM CT.
4. Drop a real LinkedIn Messages export at `system/inbox/linkedin_messages_export.csv` and run P-033.
5. Grant Full Disk Access for the Python/Terminal runtime and rerun P-019 messages/calls.
6. Capture `calendar:personal` raw calendar data through the Google Calendar connector.

## Product Learning From Tests

The tests exposed a useful truth: RB's compute path is now solid, but the system is still under-instrumented at the human-source boundary. The most valuable next sprint is not "more brief sections." It is making RB act more like a Chief of Staff after it has identified the board.

RB now knows:

- what sources are stale
- what should be acted on
- what loops exist
- what meetings need prep
- what outbound evidence exists
- what strategic operators are moving

The next product leap is helping Todd turn those into messages, prep artifacts, decisions, and closed loops with less friction.

## Recommended Next Sprint: RB 9.3 Chief-of-Staff Action Layer

Recommended sprint objective:

> Make RB not only brief Todd, but prepare the next move and help close the loop.

### Priority 1 - Action Drafting / Draft-In-Voice

Build a deterministic draft layer that turns canonical brief items into ready-to-review communication drafts.

Suggested file:

- `system/scripts/action_drafts.py`

Inputs:

- canonical brief items
- contact card context
- active thread context
- recent source evidence
- Todd profile / voice constraints

Outputs:

- follow-up email draft
- LinkedIn message draft
- text-message draft
- intro request draft
- thank-you / close-loop draft

Important posture:

- Review-first. Never send.
- Include why this draft exists.
- Include source refs.
- Include tone controls: concise, warm, direct, executive.

Why this matters:

The system already says "follow up with X." A CoS should hand Todd the first good draft.

### Priority 2 - Meeting Prep Auto-Materialization

`meeting_prep.py` exists, but the morning path should automatically materialize prep briefs for high-value meetings rather than merely suggest them.

Build:

- auto-create prep artifacts for today's/tomorrow's meetings with known contacts, active threads, or deliverable language
- surface artifact paths in the Daily Brief
- create or suggest meeting-prep loops with due times before the meeting

Acceptance:

```bash
python3 system/scripts/meeting_prep.py --for-today --write --confirm
python3 system/scripts/daily_brief.py --smoke
```

Why this matters:

A Chief of Staff does not just say "you have a meeting." It puts the prep packet on the desk.

### Priority 3 - Source Health Recovery Commands

The health file now identifies stale/missing sources. Next, make recovery actionable.

Build:

- `source_health.json` rows include `recovery_command`
- Daily Prep Summary shows the exact next command for Tier 1/Tier 2 gaps
- `/brief/health` exposes recovery commands for the GPT

Examples:

- personal calendar raw capture needed
- Full Disk Access needed for messages/calls
- LinkedIn messages export needed

Why this matters:

RB should not merely say "under-instrumented." It should say how to fix the instrumentation.

### Priority 4 - Loop Lifecycle Autopilot

`smart_loops.py`, `passive_verification.py`, and `closeout.py` exist. Next sprint should connect them into a daily loop lifecycle.

Build:

- morning: propose new loops from canonical brief
- midday/on-demand: draft next actions for overdue loops
- evening: auto-close evidence-backed loops and carry forward the rest
- write one consolidated loop lifecycle artifact

Suggested file:

- `system/scripts/loop_autopilot.py`

Why this matters:

A CoS tracks commitments across the day, not just at 5:00 AM.

### Priority 5 - ChatGPT Task Delivery Manual Verification Protocol

Once Todd creates the named tunnel and native ChatGPT Task, codify the live verification.

Build:

- `system/scripts/task_delivery_check.py` if feasible, or a protocol-only P-034 if platform automation is not available
- capture manual result in `system/.cache/task_delivery_check.json`
- update STATUS only when native Task has actually fired successfully

Acceptance:

- Task result email from `ChatGPT <noreply@tm.openai.com>` received
- `View message` opens the full brief inline
- backup email does not contain the full brief
- GPT fallback command works

## Alternative Sprint If Todd Wants Source Completeness First

If the next sprint should reduce under-instrumentation before adding action features, use this order instead:

1. P-019 Full Disk Access first-run for messages/calls
2. personal calendar raw capture
3. real LinkedIn messaging export ingest
4. source-health recovery commands
5. rerun morning path until `overall_health` is `partial` or `green`

This is less product-visible than action drafting, but it improves trust.

## Suggested 9.3 Acceptance Test

RB 9.3 should be considered complete when:

```bash
python3 system/scripts/morning_path_test.py
python3 system/scripts/action_drafts.py --smoke
python3 system/scripts/meeting_prep.py --smoke
python3 system/scripts/passive_verification.py --smoke
python3 system/scripts/closeout.py --smoke
python3 system/scripts/api_smoke_test.py
```

And the morning brief contains at least one of:

- a ready-to-review draft
- a materialized meeting prep artifact
- a specific source-health recovery command
- a loop lifecycle action that can be accepted or deferred

## Final Note For Claude

Do not reopen the RB 9.1 strategic-operator split unless a real defect appears.
Do not add generic news digest behavior.
Do not build more sections for their own sake.

The opportunity now is follow-through: help Todd prepare, draft, decide, and close.
