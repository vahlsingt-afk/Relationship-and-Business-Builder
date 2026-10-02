# Claude Handoff — RB 9.5 Canonical Response Hardening

**Date:** 2026-05-24  
**Status:** next sprint design + execution handoff  
**Owner:** Claude architecture / implementation loop  
**Primary objective:** tighten RB canonical responses so every major surface proves what RB checked, what it recorded/proposed/blocked, what remains stale or pending, and what Todd should do next.

## Why This Sprint

RB 9.4 made passive relationship intelligence durable. That is a major product step, but it also raises the bar for the response layer.

The system now has several powerful compute lanes:

- daily brief / resource verification;
- relationship signals;
- RI event store;
- passive RI ingest;
- manual RI intake;
- strategic memory;
- strategic operators;
- smart loops / meeting prep / closeout / publish.

The risk is now response drift: Claude/ChatGPT can have the right data but render it as advice, summary, or executive-coach prose instead of a canonical RB action report.

This sprint should make canonical responses boringly reliable:

```text
source state → detected facts → RB action state → projection/loop state → recommended next action → source refs
```

Good advice is not enough. RB must prove system action.

## RB 9.4 Testing Summary

RB 9.4 Passive RI Persistence MVP is green as of 2026-05-24.

### What 9.4 Built

- `ri_events.py`
  - Added `passive_signal` to `VALID_SOURCE_TYPES`.
  - Added `passive_signal` `_source_stable_id` branch.
  - Existing RI event-store smoke remained green.

- `passive_ri_ingest.py`
  - New passive RI proposal aggregator.
  - Collects high/medium signals from `relationship_signals` and `linkedin_messaging`.
  - Normalizes to RI event payloads.
  - Appends durable proposed events through `ri_events.append()`.
  - Tracks proposed / duplicate / blocked / unavailable.
  - Blocks low-strength, low-relevance, stale source, unmatched entity, and bad date.
  - Writes `system/.cache/passive_ri_ingest.json`.
  - Enforces action-state language: no "should be marked" / "would likely".

- `refresh_all.py`
  - Wires `passive_ri_ingest.py` between `relationship_signals` and `daily_brief`.

- `daily_brief.py`
  - Loads passive RI ingest cache.
  - Shows passive candidates/proposed/blocked/duplicates in Resource Verification.
  - Extends RI mutation and loop proof with passive RI proposed contacts by name.

### 9.4 Gate Results

The following passed locally:

```bash
python3 system/scripts/ri_events.py --smoke
python3 system/scripts/passive_ri_ingest.py --smoke
python3 system/scripts/daily_brief.py --smoke
python3 system/scripts/refresh_all.py --date 2026-05-24
python3 system/scripts/morning_path_test.py
python3 system/scripts/ri_smoke_test.py
python3 system/scripts/api_smoke_test.py
PYTHONPYCACHEPREFIX=/private/tmp/rb_pycache python3 -m py_compile \
  system/scripts/ri_events.py \
  system/scripts/passive_ri_ingest.py \
  system/scripts/ri_intake.py \
  system/scripts/refresh_all.py \
  system/scripts/daily_brief.py
```

Observed results:

- `ri_events.py --smoke`: 0 failures.
- `passive_ri_ingest.py --smoke`: 0 failures.
- `daily_brief.py --smoke`: 0 failures.
- `refresh_all.py --date 2026-05-24`: all 15 scripts OK.
- `morning_path_test.py`: 5/5 automated steps passed.
- `ri_smoke_test.py`: 0 failures.
- `api_smoke_test.py`: all 40 endpoints passed.
- Syntax compile passed with cache redirected to `/private/tmp/rb_pycache`.

Real passive cache state on 2026-05-24:

```json
{
  "candidates_seen": 0,
  "events_written": 0,
  "duplicates_skipped": 0,
  "blocked": 0,
  "source_unavailable": 0,
  "summary": "RB reviewed passive relationship signals. RB recorded 0 new passive RI events (no qualifying candidates)."
}
```

Meaning: the negative/no-op path is clean. There were no qualifying passive candidates to live-project on 2026-05-24.

### Prep Already Started For 9.5

Codex added a first-class passive confirmation surface:

```bash
python3 system/scripts/passive_ri_ingest.py confirm --all
python3 system/scripts/passive_ri_ingest.py confirm --event-id <event_id>
python3 system/scripts/passive_ri_ingest.py confirm --dry-run
python3 system/scripts/passive_ri_ingest.py confirm --all --high-confidence-only
python3 system/scripts/refresh_all.py --date 2026-05-24 --confirm-passive-ri
```

It writes `system/.cache/passive_ri_confirm.json`.

Verification passed:

```bash
python3 system/scripts/passive_ri_ingest.py --smoke
python3 system/scripts/passive_ri_ingest.py confirm --dry-run --json
python3 system/scripts/refresh_all.py --date 2026-05-24 --confirm-passive-ri
python3 system/scripts/daily_brief.py --smoke
```

Caveat: today had no pending passive events, so the live projection path was a no-op. The smoke covers bundle creation and dry-run confirmation. A true temp-baseline write smoke is still needed if this path becomes part of the next gate.

## Product Doctrine For 9.5

Canonical RB responses must distinguish:

1. what RB checked;
2. what RB detected as fact;
3. what RB inferred;
4. what RB recorded;
5. what RB proposed but did not apply;
6. what RB blocked or skipped;
7. what source freshness limits the conclusion;
8. what Todd should do next.

Allowed action-state language:

- `RB recorded...` — durable event or memory was written.
- `RB updated...` — canonical projection changed.
- `RB proposed...` — review-first mutation/event/loop exists but is not applied.
- `RB blocked...` — candidate could not be safely written.
- `RB skipped...` — duplicate, stale, low-confidence, not relevant, or already covered.
- `RB did not persist this yet...` — no mutation/event was written.
- `Pending confirmation...` — Todd or a confirm command must approve before projection.

Prohibited unless quoted as a defect:

- `should be marked`
- `would likely`
- `could be updated`
- `being treated as`
- generic "strategic advisor" / career-coach framing
- confident quiet-source conclusions when sources are stale

Every high-signal response should carry:

- `grounding`: `system_detected`, `manual_user_provided`, `inferred`, or `stale_source_limited`;
- `freshness`: fresh / stale / missing / under-instrumented;
- `confidence`: high / medium / low;
- `source_refs`: file/API/source references;
- `disposition`: `act_today`, `monitor`, `ask_todd`, or `ignore`;
- mutation/projection proof where relevant.

## Read First

Read these files in this order:

1. `system/OPERATIONALIZATION.md`
   - Phase 9C / Maho canonical response doctrine.
   - The core defect: advice was strong, but RB did not prove action.

2. `system/api/custom_gpt_prompt.md`
   - Current response instructions.
   - Daily brief, strategic memory, draft-ready actions, relationship signals.

3. `system/scripts/canonical_response_eval.py`
   - Existing deterministic harness.
   - Currently mostly covers `no_response_update`.

4. `system/test_traces/2026-05-20-maho-transcript-cos-operationalization-defect.md`
   - The most important canonical-response defect.

5. `system/test_traces/2026-05-20-negative-no-response-canonical-cos-drift.md`
   - No-response response drift.

6. `system/scripts/ri_intake.py`
   - Review/confirm response shape.

7. `system/scripts/passive_ri_ingest.py`
   - Passive proposed/blocked/confirmed response shape.

8. `system/scripts/strategic_memory.py`
   - Existing `canonical_response()` for strategic memory writes.

9. `system/scripts/daily_brief.py`
   - Canonical brief object and rendering rules.

## Sprint Scope

### Priority 1 — Define A Canonical Response Contract

Create a concise spec file:

```text
system/CANONICAL_RESPONSE_CONTRACT.md
```

It should define canonical response shapes for at least these scenarios:

1. `daily_brief`
2. `relationship_signal_review`
3. `manual_ri_intake`
4. `passive_ri_ingest`
5. `no_response_update`
6. `strategic_memory_record`
7. `draft_ready_action`
8. `meeting_prep`
9. `loop_or_action_creation`

For each scenario define:

- trigger / when to use;
- required sections;
- required action-state fields;
- required source/freshness/grounding fields;
- allowed verbs;
- banned drift patterns;
- minimal passing example.

Keep this practical. The goal is not a long philosophy doc; the goal is something a future model or test can enforce.

Include a compact canonical-response truth table. This should become the semantic map used by both Claude and `canonical_response_eval.py`.

Suggested starting table:

| System state | Allowed language | Not allowed |
|---|---|---|
| Event appended, confirmation pending | `RB proposed...` | `RB recorded...`, `RB updated...` |
| Event appended and confirmed | `RB recorded...` | `RB would record...` |
| Projection applied to baseline/card/thread/loop | `RB updated...`, `RB opened loop L-...` | `RB would update...`, `RB recommends opening...` |
| Duplicate detected | `RB skipped duplicate...` | `RB ignored...` |
| Candidate blocked by stale/low-confidence/unmatched source | `RB blocked... because...` | `RB decided...` without reason |
| Source stale or missing | `RB could not prove quiet because...` | `No activity happened...` |
| Draft generated | `RB drafted...` | `RB sent...` |
| Loop proposal created, not written | `RB proposed a loop...` | `RB opened a loop...` |
| Loop written | `RB opened loop L-...` | `RB may want to track...` |
| Strategic memory persisted | `RB recorded strategic memory...` | `This should be remembered...` |

The truth table is the anti-overclaim invariant. Custom templates, prompt wording, and API summaries may vary, but they must obey this table.

### Priority 2 — Expand `canonical_response_eval.py`

Turn the evaluator from a no-response-only harness into a multi-scenario gate.

Add scenarios:

```text
no_response_update          # existing
ri_intake_detected          # manual transcript/email/LinkedIn/recruiting RI
passive_ri_summary          # proposed/blocked/duplicate/unavailable passive RI
daily_brief_top_contract    # trust-first resource verification before analysis
strategic_memory_record     # durable memory write response
```

At minimum, the evaluator should check:

- required headings or structured labels;
- action-state verbs are present;
- banned phrases are absent outside quoted/code spans;
- grounding label present;
- disposition present when the scenario implies a recommendation;
- persistence/projection status present;
- source refs present;
- no "quiet day" claim when stale-source labels are present.

Do not overfit to exact prose. This should be a deterministic shape/discipline gate.

Acceptance:

```bash
python3 system/scripts/canonical_response_eval.py --smoke
python3 system/scripts/canonical_response_eval.py --scenario ri_intake_detected --text <fixture>
python3 system/scripts/canonical_response_eval.py --scenario passive_ri_summary --text <fixture>
python3 system/scripts/canonical_response_eval.py --scenario strategic_memory_record --text <fixture>
```

Prefer adding built-in passing/failing fixtures to `--smoke` so one command covers the new scenarios.

### Priority 3 — Add Canonical Response Renderers Where Useful

Do not rely only on prompts.

Identify places where API/script outputs can provide a canonical response block directly:

- `ri_intake.review()` / `confirm()`
- `passive_ri_ingest.run()` / `confirm_pending()`
- `strategic_memory.record()`
- `relationship_signals.build_report()` or its API surface

If the shape is already present, standardize field names. If not, add a `canonical_response` or `canonical_summary` block with:

```json
{
  "scenario": "...",
  "action_state": "recorded|updated|proposed|blocked|skipped|not_persisted",
  "summary": "...",
  "facts": [],
  "inferences": [],
  "persistence": {},
  "projection": {},
  "recommended_actions": [],
  "source_refs": [],
  "grounding": "...",
  "freshness": "...",
  "confidence": "..."
}
```

Keep this backwards-compatible. Do not break existing API smoke tests or Custom GPT actions.

### Priority 4 — Align Custom GPT Prompt With The Contract

Update:

```text
system/api/custom_gpt_prompt.md
```

Make the prompt reference `CANONICAL_RESPONSE_CONTRACT.md` language, especially:

- use API-provided canonical blocks when present;
- never convert proposed state into completed state;
- never imply a mutation occurred unless the API returned one;
- include stale-source caveats before interpretation;
- keep daily brief trust-first order.

Avoid bloating the prompt. Tighten the relevant response-contract paragraphs.

### Priority 5 — Passive RI Confirmation Write Smoke

If time allows, add one isolated smoke that proves passive confirmation can move `last_touch` without touching production state.

Desired test:

1. Rebind `ri_events.EVENTS_DIR`, `ri_events.INDEX_PATH`, `ri_intake.PENDING_PATH`.
2. Rebind or monkeypatch baseline path/loaders if feasible.
3. Create a synthetic baseline with one RC/LKI contact and stale `last_touch`.
4. Create a passive `last_touch_update` event with high confidence.
5. Stash bundle.
6. Confirm with `dry_run=False` against the temp baseline.
7. Assert:
   - confirmation follow-up event status is `persisted`;
   - temp baseline `last_touch` advanced;
   - production baseline untouched.

If rebinding the baseline safely is too invasive, document the limitation and keep the smoke dry-run only.

### Priority 6 — Preserve A Future User-Defined Response Template Switch

Do not fully build this in RB 9.5, but leave the architecture open for it.

Future product direction: users should eventually be able to define their own canonical response templates for the daily brief and other Chief-of-Staff responses.

Examples:

- daily brief template;
- relationship-signal review template;
- meeting-prep template;
- no-response / waiting-state template;
- strategic-memory recorded template;
- action-orchestration template.

For now, document the intended switch in `CANONICAL_RESPONSE_CONTRACT.md` and avoid implementation choices that would make response contracts Todd-only or hard-coded into one fixed prose shape.

Suggested future setting shape:

```json
{
  "response_templates": {
    "mode": "system_default",
    "allow_user_defined": false,
    "templates_path": "system/response_templates/",
    "active": {
      "daily_brief": "default",
      "relationship_signal_review": "default",
      "meeting_prep": "default"
    }
  }
}
```

RB 9.5 should not implement a template engine. It should simply keep the canonical contract modular enough that a later sprint can add:

- `system/response_templates/*.md`;
- schema validation for required fields/sections;
- a settings switch selecting default vs user-defined templates;
- evals proving custom templates still preserve action-state truth.

Important constraint: user-defined templates may change order, tone, labels, or verbosity, but they must not weaken the proof contract. Required invariants remain mandatory:

- source freshness before quiet-source conclusions;
- action state before interpretation;
- persisted vs proposed vs blocked distinction;
- grounding/freshness/confidence/source refs;
- no completed-action language unless the system actually persisted or projected it.

## Non-Goals

- Do not redesign the daily brief.
- Do not replace the event store.
- Do not rewrite the Custom GPT prompt from scratch.
- Do not build a UI.
- Do not build the user-defined response-template engine yet; only preserve the design path.
- Do not auto-create contacts from passive signals.
- Do not silently apply ambiguous RI or stale-source conclusions.
- Do not turn canonical responses into verbose templates that make every answer heavy.
- Do not expand into a broad platform redesign. This sprint is response truthfulness and enforceability.

## Required Smoke Suite Before Green

Run:

```bash
python3 system/scripts/canonical_response_eval.py --smoke
python3 system/scripts/ri_events.py --smoke
python3 system/scripts/passive_ri_ingest.py --smoke
python3 system/scripts/ri_intake.py --smoke
python3 system/scripts/daily_brief.py --smoke
python3 system/scripts/refresh_all.py --date 2026-05-24
python3 system/scripts/ri_smoke_test.py
python3 system/scripts/api_smoke_test.py
python3 system/scripts/morning_path_test.py
PYTHONPYCACHEPREFIX=/private/tmp/rb_pycache python3 -m py_compile \
  system/scripts/canonical_response_eval.py \
  system/scripts/ri_intake.py \
  system/scripts/passive_ri_ingest.py \
  system/scripts/daily_brief.py \
  system/scripts/strategic_memory.py
```

If API/morning path emits the `RB_API_KEY is not set` local warning, that is acceptable for local TestClient validation. Do not expose the service without auth.

## Success Definition

This sprint is green when:

1. There is a written canonical response contract.
2. The contract includes a truth table mapping system states to allowed and prohibited language.
3. `canonical_response_eval.py --smoke` covers more than no-response drift.
4. RI intake, passive RI, strategic memory, and daily brief response shapes can be evaluated deterministically.
5. API/script outputs expose canonical response blocks where prompt-only enforcement is too weak.
6. The Custom GPT prompt is tightened to use those blocks.
7. The contract preserves a future user-defined template switch without implementing the full template engine.
8. Existing 9.4 smokes still pass.

RB should feel less like:

```text
Here is a smart interpretation.
```

and more like:

```text
RB checked these sources.
RB recorded/proposed/blocked these changes.
Here is the interpretation.
Here is the next action and why.
```

That is the difference between advice and a Chief of Staff operating system.
