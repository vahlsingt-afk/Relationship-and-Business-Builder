# Claude Handoff — Execution Layer Sprint

**Date:** 2026-05-22
**Builder:** Claude (operator: Todd)
**Status:** All six priorities from the 2026-05-21 operating-layer sprint plan implemented and module-level smoke-validated. API smoke and commits remain host-side.

## What Just Happened

The 2026-05-21 sprint plan called the existing Daily Brief operating layer "surface only" — the brief recommended action but nothing in RB actually *did* anything. This sprint built the six execution-layer pieces behind each affordance.

Each priority is one module, one or two API endpoints, daily-brief wiring, and a smoke test. The pattern repeats deliberately:

1. **Read the canonical state** (`daily_brief.build_report` is the lens).
2. **Compute a structured proposal** (no mutations).
3. **Surface it in the brief** with action options.
4. **Gate any mutation** behind `confirm=true` (memory: smoke-test-mutations + P-009).
5. **Smoke-test in-memory** with synthesized inputs (no I/O, no real ledger touches).

If you (Codex or future-Claude) keep extending this layer, follow that pattern.

## The Six Priorities

| # | Workstream | New module | New endpoints | Smoke checks |
|---|---|---|---|---|
| 1 | Loop creation from canonical items | `smart_loops.py` | `POST /smart_loops/apply` (dual-mode in GPT, also `GET /smart_loops/proposals` in full) | 26 |
| 2 | Meeting prep artifact generation | `meeting_prep.py` | `POST /meeting_prep/write` (GPT dual-mode); `GET /meeting_prep`, `GET /meeting_prep/{event_id}` (full only) | 27 |
| 3 | Passive loop verification | `passive_verification.py` | `POST /passive_verification` (dual-mode) | 21 |
| 4 | End-of-day closeout automation | `closeout.py` | `POST /closeout` (dual-mode) | 26 |
| 5 | RB operational layer destination | `publish.py` | `POST /publish`, `GET /published/{date_str}` (full only) | 26 |
| 6 | LinkedIn messaging parser | `linkedin_messaging.py` | `POST /linkedin_messaging/ingest`, `GET /linkedin_messaging/overlay` (full only) | 31 |

**Total smoke coverage:** 157 in-memory checks across the six modules + the existing `daily_brief --smoke` regression (still passing). Every smoke is pure — no I/O, no mutations, no real ledger reads.

## How the Pieces Compose

```
                       daily_brief.build_report()
                                  │
                                  ▼
                       canonical_brief.sections
                                  │
        ┌─────────┬────────┬──────┴──────┬───────────┬──────────┐
        ▼         ▼        ▼             ▼           ▼          ▼
    smart_loops  meeting_  passive_      closeout    publish    linkedin_
    proposals    prep      verification               + email   messaging
        │         │        │             │           │ composer  overlay
        │         │        │             │           │           │
        ▼         ▼        ▼             ▼           ▼           ▼
    mutations    fs        mutations     fs          fs +        inbox JSON
    .cmd_loop_   (artifact .cmd_loop_    (closeout   email       (writes are
    add         writer)   close          writer)    transport    snapshotted)
                                                    host-side
```

Cross-module wiring:

- `smart_loops.meeting_prep` proposals reference the path `meeting_prep.artifact_path_for(event)` returns. `passive_verification` reads that same path to know what artifact to check for closure evidence.
- `passive_verification` reads `linkedin_messaging.outbound_evidence` — outbound LinkedIn now counts toward "follow_up loop completed" closure.
- `closeout` reads `passive_verification.scan` for its `auto_resolved` bucket.
- `publish` reads the brief's `daily_prep_summary.totals` and `morning_command_center` to compose the email's "Top 3 + signal health" body.
- `linkedin_messaging.inbound_ri_candidates` become both canonical items in `last_24h_relationship_signals` and `follow_up` smart-loop proposals.

## File Inventory

### Added

```
system/scripts/smart_loops.py             # 26 smoke checks
system/scripts/meeting_prep.py            # 27 smoke checks
system/scripts/passive_verification.py    # 21 smoke checks
system/scripts/closeout.py                # 26 smoke checks
system/scripts/publish.py                 # 26 smoke checks
system/scripts/linkedin_messaging.py      # 31 smoke checks

system/CLAUDE_HANDOFF_2026-05-22_EXECUTION_LAYER_SPRINT.md  # this file
```

Three artifact-destination directories will be created on first `--write --confirm` invocation (they do not exist yet by design):

```
system/meeting_briefs/    # populated by meeting_prep.py
system/closeouts/         # populated by closeout.py
system/published/daily/   # populated by publish.py
```

### Modified

```
system/scripts/daily_brief.py
  - New canonical section: passive_verification_candidates
  - New report fields: linkedin_messaging, with downstream wiring
  - end_of_day_closeout section now carries concrete bucket counts
  - suggested_loop_creation section appends one item per fresh proposal
  - meeting_prep_and_deliverables items carry meeting_briefs.<path> source_ref
  - last_24h_relationship_signals surfaces LinkedIn inbound RI alongside email
  - Morning Command Center: auto-close tile + publication-status tile

system/scripts/smart_loops.py
  - meeting_prep proposal verification_method now references the artifact path
  - _propose_linkedin_inbound proposer added

system/scripts/passive_verification.py
  - _evidence_from_linkedin_messaging evidence collector
  - verify_loop accepts linkedin_payload; scan() threads it through

system/api/server.py
  - Imports added: smart_loops, meeting_prep, passive_verification, closeout,
    publish, linkedin_messaging
  - Pydantic input models added for each (all with confirm: bool gate)
  - Endpoints added per the priority table above

system/api/openapi.yaml       # full spec, 53 ops
system/api/openapi_gpt.yaml   # curated GPT subset, exactly 30 ops
system/scripts/api_smoke_test.py  # adds new GET/POST coverage + a no-write contract assertion

system/settings.json
  - daily_briefing.operational_layer gains publication_path + public_url_base
    + public_url_base_note (Tenet 1: refuse to fabricate the URL)

system/protocols/P-001_daily_brief_regen.md
  - New step 18b documenting the 16:30 closeout scheduling contract

system/CLAUDE_FOLLOWUP_MORNING_DELIVERY_PIPELINE.md
  - New section listing the three email transports (eml drop, SMTP, Gmail OAuth)
    with exact env vars publish.py reads

system/STATUS.md
  - Last-reviewed date bumped; six new compute rows added; scheduled-tasks
    table updated; "Hardest unbuilt edges" refreshed
```

## Patterns and Constraints to Maintain

### Dual-mode POST endpoints

Each new mutation endpoint accepts a `confirm: bool` flag. `confirm=false` returns the read shape (proposals + previews) with no writes. `confirm=true` runs the mutation through `mutations.cmd_*` which already does snapshot-then-validate-then-rollback per P-009.

This pattern exists for two reasons:

1. **The 30-op Custom GPT cap.** Folding read + write into one operation pays for itself versus splitting into separate GET + POST.
2. **The smoke-test-mutations memory.** A smoke test that hits the apply path with `confirm=false` is provably safe — the gate is enforced inside the module, not at the smoke layer.

When you add the next execution-layer endpoint, follow this. Don't split read and write unless the read endpoint is high-traffic enough to want HTTP caching.

### The 30-op cap — what stays vs. drops

The Custom GPT Actions schema rejects specs with >30 operations. Each priority either added an op or dropped one. Final accounting:

- **Kept in GPT spec:** all six new write endpoints (`applySmartLoops`, `writeMeetingPrepArtifacts`, `runPassiveVerification`, `runCloseout`, `ingestLinkedInMessaging` — wait, that one isn't in GPT spec; `publishBrief` also isn't).

Let me restate. The GPT spec has at exactly 30 ops:

```
read:  getDailyBrief, getNetworkGap, getDrrScore, getCard,
       listActiveThreads, getCalendarOverlay, getSourceReadiness,
       getEmailOverlay, getInteractionOverlay, getSocialOutboundOverlay,
       getPostRecommendations, getNetworkAnalysis, findIntro, getLoops,
       getRecentTestTraces, getRelationshipSignals
write: addLoop, closeLoop, touchContact, addContact, openThread,
       closeThread, addMyPost, saveTestTrace, manualRelationshipIntake,
       processOpportunityIntake, classifyArtifact, ingestLinkedInExport,
       applySmartLoops, writeMeetingPrepArtifacts, runPassiveVerification,
       runCloseout
```

(That is the current cap-respecting set; if Codex's recent reformat changed exact ops, treat `validate_openapi_gpt.py` as ground truth.)

**Dropped from GPT spec to make room (still in `openapi.yaml`):**

- `getSocialOverlay` — `getDailyBrief` already inlines social data.
- `getSmartLoopProposals` — `applySmartLoops` with `confirm=false` returns the same payload.

**Full-spec-only (never in GPT):**

- `getMeetingPrepCandidates`, `getMeetingPrepForEvent`, `publishBrief` (the GPT can preview via `writeMeetingPrepArtifacts confirm=false` + the full-spec routes are for local/CLI/hosted use).
- `getPublishedBrief` — used by hosted deployments to serve the published JSON.
- `getLinkedInMessagingOverlay`, `ingestLinkedInMessaging` — ingestion is host-side scripted work, not a GPT operation; overlay flows through `getDailyBrief`.

If you add an endpoint, decide *first* whether it belongs in the GPT spec. The answer is usually no unless the Custom GPT can drive a useful conversation without it.

### No-fabrication discipline (Tenet 1) applied to delivery

`publish.compose_email` refuses to fabricate a CTA URL when `public_url_base` isn't configured — it sets `cta_status: pending_destination` and the email body explicitly names the gap. `send_email` with `confirm=true` and no transport returns `status: pending_transport` with the exact env-var instructions, not a fake "sent OK". Apply the same discipline anywhere you wire transport / hosting / external integrations next.

## Validation Status

### Module smokes — all pass in sandbox

```bash
python3 system/scripts/smart_loops.py --smoke           # 26/26
python3 system/scripts/meeting_prep.py --smoke          # 27/27
python3 system/scripts/passive_verification.py --smoke  # 21/21
python3 system/scripts/closeout.py --smoke              # 26/26
python3 system/scripts/publish.py --smoke               # 26/26
python3 system/scripts/linkedin_messaging.py --smoke    # 31/31
python3 system/scripts/daily_brief.py --smoke           # all checks pass
python3 system/scripts/validate_openapi_gpt.py          # OK (30 ops)
```

### Unverified in sandbox

- `api_smoke_test.py` (no `fastapi` available in the sandbox; sandbox has no PyPI access — `pip install fastapi --break-system-packages` returns 403 Forbidden through the sandbox proxy). **The smoke test itself was updated** with new GET/POST coverage and a no-write-contract assertion; running it is a host-side step.
- Real LinkedIn export ingest (no `messages.csv` in sandbox; the smoke covers the parse path against synthetic rows).
- Actual SMTP send (no credentials in sandbox, intentionally — Tenet 1).
- Git commits (sandbox cannot write `.git/index.lock` per memory `feedback_sandbox_git.md` — host runs them).

### Host-side finish sequence

One commit per priority per Todd's preference (memory: `feedback_commit_per_priority.md`). The full sequence is:

```bash
cd "Relationship Builder"

# 1. Confirm full API smoke
pip install fastapi httpx pyyaml --break-system-packages   # if needed
python3 system/scripts/api_smoke_test.py                   # expect all checks pass

# 2. Six commits, one per priority
git add system/scripts/smart_loops.py system/scripts/daily_brief.py \
        system/scripts/api_smoke_test.py system/api/server.py \
        system/api/openapi.yaml system/api/openapi_gpt.yaml
git commit -m "smart_loops: propose tracked loops from canonical daily brief"

git add system/scripts/meeting_prep.py system/scripts/smart_loops.py \
        system/scripts/daily_brief.py system/api/server.py \
        system/api/openapi.yaml system/api/openapi_gpt.yaml
git commit -m "meeting_prep: generate 11-section prep brief artifacts from canonical brief"

git add system/scripts/passive_verification.py system/scripts/daily_brief.py \
        system/api/server.py system/api/openapi.yaml system/api/openapi_gpt.yaml
git commit -m "passive_verification: auto-close open loops when canonical evidence appears"

git add system/scripts/closeout.py system/scripts/daily_brief.py \
        system/api/server.py system/api/openapi.yaml system/api/openapi_gpt.yaml \
        system/protocols/P-001_daily_brief_regen.md
git commit -m "closeout: 16:30 end-of-day operating layer with six buckets and tomorrow setup"

git add system/scripts/publish.py system/scripts/daily_brief.py \
        system/settings.json system/api/server.py system/api/openapi.yaml \
        system/CLAUDE_FOLLOWUP_MORNING_DELIVERY_PIPELINE.md
git commit -m "publish: RB operational layer destination + email composer (transport host-side)"

git add system/scripts/linkedin_messaging.py system/scripts/daily_brief.py \
        system/scripts/smart_loops.py system/scripts/passive_verification.py \
        system/api/server.py system/api/openapi.yaml
git commit -m "linkedin_messaging: parse exports → overlay, RI candidates, last_touch proposals"

# 3. STATUS.md + this handoff + the memory entries (a 7th cleanup commit)
git add system/STATUS.md system/CLAUDE_HANDOFF_2026-05-22_EXECUTION_LAYER_SPRINT.md
git commit -m "STATUS + handoff for the execution-layer sprint"
```

If the per-priority diffs overlap awkwardly (e.g., `daily_brief.py` modified in all six), squash into one commit titled `execution layer sprint: smart_loops + meeting_prep + passive_verification + closeout + publish + linkedin_messaging`. Todd's stated preference is one-per-priority *when clean*; one merged commit is fine when the diffs interleave.

## Wiring the 05:00 + 16:30 LaunchAgents (host-side)

Two scheduled jobs are needed to make the system feel like "newspaper on the doorstep."

### Morning (05:00 America/Chicago)

```bash
# 1. Refresh sources (where automatable)
python3 system/scripts/refresh_sources.py --all --refresh-signals

# 2. Regenerate today.md + MANIFEST.md
python3 system/scripts/daily_brief.py

# 3. Publish artifacts + send the email
python3 system/scripts/publish.py --write --send --confirm
```

LaunchAgent .plist for that sequence goes at `~/Library/LaunchAgents/com.todd.rb.morning.plist`. Env vars the job needs:

- `RB_PUBLIC_URL_BASE` (or set it in `settings.json`).
- One of: `RB_SMTP_HOST/PORT/USER/PASS` OR `RB_EML_DROP_DIR`.

### Evening (16:30 America/Chicago)

```bash
python3 system/scripts/passive_verification.py --apply --confirm  # optional batch close
python3 system/scripts/closeout.py --write --confirm
```

LaunchAgent .plist at `~/Library/LaunchAgents/com.todd.rb.closeout.plist`. No env vars beyond what's in the morning agent.

## Where to Pick Up

### Priority candidates for the next sprint

In rough order of leverage:

1. **Composition layer (draft-in-voice).** Every module this sprint ends in a recommendation that says "send X" or "reply to Y" — the operator still has to write the words. A draft-in-Todd's-voice layer is now the binding constraint on day-to-day usefulness. Inputs already on the table: smart_loops follow_up proposals, meeting_prep talking points, closeout tomorrow_setup, passive_verification possible_resolution reasons. Composition lives in `system/scripts/composer.py` (proposed); CLI `--target loop:L-... --voice todd` returns a draft.

2. **DRR eval framework.** The prototype has weights; nothing validates them. Eval set in `RB_DRR_Specification_and_Evaluation.docx` Appendix D. The execution layer now provides a natural ground truth — closed loops + sent followups + held meetings — that DRR can be validated against.

3. **Protocol files for the new modules.** P-025 through P-030. Each protocol locks the contract for the corresponding script. ARCHITECTURE.md only describes the 11-section meeting brief (P-026), not the others; a future-Claude that reads the protocols index gets a complete map.

4. **First-run install of P-019 (Apple Messages + Calls) on Todd's Mac.** Highest-leverage data input that's still hand-maintained.

5. **DRR weights × passive_verification feedback loop.** Loops that auto-close versus loops that get re-dated versus loops that slip is a signal about DRR accuracy. Wire that back into the score.

### Don't do next

- **Do not build new Daily Brief sections without an execution layer behind them.** Every new section should ship with the corresponding `--apply --confirm` path. Otherwise we drift back to "dashboard, not operating system."
- **Do not extend the GPT openapi past 30 ops.** If a new endpoint needs to land there, find one to drop *first* and document the trade-off in this handoff's successor.
- **Do not let SMTP/Gmail OAuth creds anywhere near the repo.** All transport config lives in env vars or `~/.rb/`.
- **Do not ingest LinkedIn messages without operator confirmation.** RI event proposals from `linkedin_messaging` carry `persistence_status: proposed_write_pending_confirmation` for a reason.

## Memory Entries Worth Reading

- `rb_architecture.md` — RB 9.0 canonical layout (baseline + projections + mutations).
- `feedback_commit_per_priority.md` — one commit per priority workstream.
- `feedback_sandbox_git.md` — Cowork sandbox cannot commit; host runs them.
- `rb_gpt_action_cap.md` — 30-op cap; openapi_gpt.yaml is the curated subset.
- `feedback_smoke_test_mutations.md` — smoke tests must use dry_run/confirm gate; mutations.py writes canonical paths regardless of tmp-dir tricks.
- `project_sprint_ri_intake.md` (older, partly superseded by this sprint).
- After this sprint: `rb_execution_layer.md` and `rb_dual_mode_post_pattern.md` (saved next).

## Acceptance Standard (carried forward from 2026-05-21)

The user should feel:

> RB prepared my day, checked the right sources, identified what matters, recommended what to do, and made the next step easy.

Not:

> ChatGPT wrote me another thoughtful summary.

This sprint moved one layer below recommendation: when RB now says "open a follow_up loop with Becky" it can actually open the loop; when it says "the meeting prep brief should exist at this path" the prep brief is one `POST` away from being there; when it says "this loop already happened" it can close the loop with named evidence. That's the right direction. Keep going.
