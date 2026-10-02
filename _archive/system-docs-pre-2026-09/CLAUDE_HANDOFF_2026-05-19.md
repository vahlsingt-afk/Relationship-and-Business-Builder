# Claude Handoff — Morning Development Pass

**Prepared:** 2026-05-18 evening  
**Prepared by:** Codex + Todd through live Custom GPT testing  
**Purpose:** Give Claude the exact next implementation targets after the first real RB 9.0 Action-layer tests.

## Current State

RB 9.0 is now operating through the private ChatGPT Custom GPT Action layer.

The following are confirmed working from the GPT interface:

- `getRelationshipSignals`
- `getDailyBrief`
- `saveTestTrace`
- `getRecentTestTraces`
- `closeLoop` with already-closed safety behavior
- `touchContact` with post-mutation DRR validation

The OpenAPI schema had to be reduced to 30 operations because Custom GPT Actions rejected the full API surface. The current GPT-facing action set is intentionally curated. Keep the full local API broader, but treat the Custom GPT schema as an operator-facing subset.

## Test Artifacts To Review First

Review these files before coding:

```text
system/test_traces/2026-05-18-rb-action-availability-and-getrelationshipsignals-callable-test.md
system/test_traces/2026-05-18-rb-action-availability-and-getrelationshipsignals-callable-test.json

system/test_traces/2026-05-18-daily-brief-cos-grounding-and-stale-signal-handling-test.md
system/test_traces/2026-05-18-daily-brief-cos-grounding-and-stale-signal-handling-test.json

system/test_traces/2026-05-18-loop-mutation-safety-and-already-closed-post-validation-test.md
system/test_traces/2026-05-18-loop-mutation-safety-and-already-closed-post-validation-test.json

system/test_traces/2026-05-18-touchcontact-projection-sync-defect-jeff-wayman-card-frontmatter-stale-after-suc.md
system/test_traces/2026-05-18-touchcontact-projection-sync-defect-jeff-wayman-card-frontmatter-stale-after-suc.json
```

These traces are more important than the prior abstract roadmap because they capture actual GPT runtime behavior.

## What Passed

### 1. New Action Schema Is Live

The earlier GPT failure was caused by a stale Actions schema. After the schema refresh, the GPT could call:

- `getRelationshipSignals`
- `saveTestTrace`
- `getRecentTestTraces`

One defect remains from the first test:

```text
RB-ACTION-DETECTION-001
```

The assistant initially claimed `getRelationshipSignals` was unavailable even though it was exposed. On a repeated prompt it called the endpoint successfully. This is mostly prompt/runtime behavior, not backend code, but the prompt can be hardened to always inspect callable actions before claiming an RB action is unavailable.

### 2. CoS Daily Brief Behavior Improved

The GPT called `getDailyBrief` and produced a CoS-style brief rather than a generic news briefing.

It correctly:

- led with relationship signal state
- treated zero new signals as unreliable because sources were stale
- included stale-source warnings
- included "what to ignore"
- prioritized overdue loops and warm active threads
- interpreted the board as under-instrumented, not truly quiet

This is a strong product pass.

Remaining issue:

The daily brief currently blends explicit system outputs with assistant inference. That is useful, but RB's "never guess" tenet requires labels.

Add explicit grounding labels wherever possible:

```text
System-detected
Inferred by RB
Manual/user-provided context
Recommended action
Needs reconciliation
```

### 3. Loop Mutation Safety Passed

Test:

Todd asked RB to close `L-2026-05-08-017`.

Result:

- `closeLoop` returned an already-closed error.
- The assistant then validated final state through loop retrieval.
- It correctly reported that the desired state was already achieved.

This is the right behavior:

```text
Failed mutation does not mean failed task if post-validation proves the desired final state already exists.
```

Keep this pattern and document it in the write-back/mutation protocol.

### 4. `touchContact` Mutated Canonical State Correctly

Test:

Todd updated Jeff Wayman's last touch to `2026-05-12` based on interaction evidence.

Confirmed canonical baseline state:

```json
{
  "id": "jeff-wayman",
  "last_touch": "2026-05-12",
  "signal_class": "RC",
  "rc_tier": "inner"
}
```

Confirmed DRR:

```text
score: 92.0
recency: 0.984
```

## Highest Priority Defect

### Projection Sync Defect: RC Card Frontmatter Stale After `touchContact`

After `touchContact` succeeded, the canonical baseline and DRR updated correctly, but the RC markdown card still showed the older value:

```text
system/cards/jeff-wayman.md
last_touch: 2025-12-29
```

This creates trust friction because the user sees two different "truths":

- baseline/index layer says `2026-05-12`
- DRR scoring says `2026-05-12`
- RC card frontmatter says `2025-12-29`

Recommendation:

When `touchContact` succeeds and an RC card exists, update the card frontmatter `last_touch` field in the same mutation transaction.

Expected behavior:

1. Snapshot before mutation.
2. Update `baseline_index.json`.
3. If `system/cards/{contact_id}.md` exists, update YAML/frontmatter `last_touch`.
4. Validate baseline.
5. Optionally validate card frontmatter parse.
6. Refresh relevant caches or clearly mark stale cache state.
7. Return mutation result with both canonical and projection updates:

```json
{
  "ok": true,
  "id": "jeff-wayman",
  "last_touch": "2026-05-12",
  "baseline_updated": true,
  "card_updated": true,
  "cache_refresh_recommended": true
}
```

If no card exists, return:

```json
{
  "baseline_updated": true,
  "card_updated": false,
  "card_reason": "no_card_exists"
}
```

Do not silently leave card projections stale for RCs.

Suggested files:

```text
system/scripts/mutations.py
system/api/server.py
system/api/openapi.yaml
system/api/custom_gpt_prompt.md
system/protocols/P-009_write_back_mutations.md
system/scripts/api_smoke_test.py
```

Add a regression test or smoke-test assertion using Jeff Wayman or a fixture contact:

- call/touch mutation
- assert baseline last_touch updated
- assert card frontmatter updated when card exists
- assert DRR recency reflects new date

## Second Priority: Refresh/Stale Feed Operationalization

The daily brief is now smart enough to say the board is under-instrumented when feeds are stale. That is good.

But the user should not have to manually infer how to restore signal visibility.

Build or expose a clear operator path for:

- email refresh
- calendar refresh
- messages refresh
- calls refresh
- social refresh

Minimum near-term improvement:

Daily brief stale-source warnings should include a next operational action:

```text
Email is 55.7h stale. Run/trigger email refresh before trusting no-new-signal conclusions.
```

Longer-term:

Create a single refresh command/API:

```text
POST /refresh_sources
```

or local-only:

```bash
python3 system/scripts/refresh_sources.py --email --calendar --messages --calls --social
```

Do not expose this to ChatGPT unless authentication and local permissions are safe.

## Third Priority: Grounding Labels In CoS Briefs

The daily brief test passed, but RB needs clearer separation between:

- fields returned directly by the API
- deterministic computed state
- assistant inference/synthesis
- user-provided/manual context

Example issue:

Ish/Maho was prioritized correctly, but because the email feed was stale, the system should label Ish as:

```text
Manual/user-provided context; not freshly detected in current email feed.
```

Recommended output shape for priority items:

```json
{
  "title": "Ish Singh / Maho",
  "grounding": "manual_context_plus_active_thread",
  "freshness": "not_detected_in_current_email_feed",
  "confidence": "medium",
  "recommended_action": "schedule deep dive while warm",
  "reconciliation_prompt": "Confirm whether the Calendly meeting has been booked."
}
```

Update:

```text
system/scripts/daily_brief.py
system/scripts/relationship_signals.py
system/api/custom_gpt_prompt.md
```

## Fourth Priority: Custom GPT 30-Operation Schema Discipline

The full OpenAPI schema exceeds the Custom GPT maximum of 30 operations.

Maintain two schemas going forward:

```text
system/api/openapi.yaml
```

Full internal/local API.

```text
system/api/openapi_gpt.yaml
```

Curated Custom GPT schema capped at 30 operations.

The GPT schema should prioritize:

- daily brief
- relationship signals
- loops
- DRR
- cards
- active threads
- overlays
- intro/network analysis
- touch/contact/thread mutations
- trace save/recent

Cut or keep out of GPT-facing schema:

- manifest/status/protocol helpers
- low-priority social write endpoints
- validation/dev-only endpoints unless needed in the interface

Add a script check:

```bash
python3 system/scripts/validate_openapi_gpt.py
```

Expected checks:

- OpenAPI parses
- operation count <= 30
- required RB interface operations present
- server URL set to current tunnel/host
- no duplicate operationIds

## Fifth Priority: Prompt Hardening

Update `system/api/custom_gpt_prompt.md` with these rules:

1. If the user names an RB operation that is callable, call it.
2. Do not claim an operation is unavailable unless it is absent from the actual callable action set.
3. For CoS briefs, label grounding:
   - system-detected
   - inferred
   - manual/user-provided
   - stale-source-limited
4. For mutations:
   - never claim success before mutation response
   - always post-validate after mutation
   - if mutation returns already-done/already-closed, verify final state and report as desired-state-already-achieved
5. For stale sources:
   - explicitly say when "no new signals" is not reliable
   - recommend the next refresh step

## Suggested Morning Build Order

Use this sequence:

1. Read all four new trace artifacts.
2. Fix projection sync for `touchContact` so RC card frontmatter updates when the baseline updates.
3. Add/adjust smoke test coverage for touch + card sync.
4. Add grounding labels to daily brief / relationship signal outputs.
5. Create `openapi_gpt.yaml` capped at 30 operations and validate it.
6. Update Custom GPT prompt with action availability, grounding, mutation validation, and stale-source rules.
7. Run:

```bash
python3 system/scripts/validate_baseline.py --json
python3 system/scripts/api_smoke_test.py
python3 system/scripts/refresh_all.py
python3 system/scripts/drr_score.py --id jeff-wayman --json
```

8. Commit the development pass.

## Testing Prompts After Claude Implements

Use these from the Custom GPT:

```text
Update Jeff Wayman's last touch to 2026-05-12 based on recent interaction evidence. After updating, verify baseline, DRR, and his RC card frontmatter all agree.
```

```text
Show me my daily brief. Label each major priority as system-detected, inferred, manual context, or stale-source-limited.
```

```text
What relationship signals have emerged in the last 24 hours? If sources are stale, tell me which conclusions are unreliable and what to refresh first.
```

```text
Close loop L-2026-05-08-017 because I updated the CRM pipeline state. If it is already closed, validate final state and tell me that the desired state was already achieved.
```

```text
Export this test session for Codex.
```

## Important Current Dirty State

At handoff creation time, the repo has intentional RB changes:

- `system/baseline_index.json` modified by Jeff Wayman `touchContact`
- new test traces from the live GPT tests
- this handoff file

There are also unrelated untracked automation helper files:

```text
system/automation/MACOS_HELPER_APP.md
system/automation/README.md
system/scripts/launchagent_install.py
```

Do not include those in this development pass unless Todd explicitly asks.

## Product Principle To Preserve

RB should not become timid just because it must not guess.

The target behavior is:

```text
Make grounded judgments.
Label the grounding.
Ask micro-reconciliation questions when the missing data matters.
Suppress noise.
Verify mutations.
Keep projections synchronized with canonical state.
```

