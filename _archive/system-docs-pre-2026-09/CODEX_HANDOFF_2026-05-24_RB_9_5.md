# Codex Handoff — RB 9.5 Canonical Response Hardening

**Date:** 2026-05-24
**From:** Claude (sprint implementation)
**To:** Codex (host terminal gate + commit)
**Status:** All sandbox smokes green. Awaiting host terminal validation and commit.

---

## Your job

1. Run the host terminal gate (listed below).
2. If all pass: commit the 6 changed files as one commit per the hygiene preference.
3. If anything fails: see the failure-handling section at the bottom.

---

## Host terminal gate

Run these in order from the repo root. The `RB_API_KEY is not set` warning is
acceptable for local TestClient runs — it is not a failure.

```bash
python3 system/scripts/ri_smoke_test.py
python3 system/scripts/api_smoke_test.py
python3 system/scripts/morning_path_test.py
```

Expected results:
- `ri_smoke_test.py`: 0 failures
- `api_smoke_test.py`: all endpoints pass (40 as of RB 9.4)
- `morning_path_test.py`: 5/5 automated steps pass (the API step requires fastapi to be installed)

The following already passed in the sandbox and do not need re-running unless
you suspect drift:

```bash
python3 system/scripts/canonical_response_eval.py --smoke   # 10/10 fixture pairs
python3 system/scripts/ri_events.py --smoke
python3 system/scripts/passive_ri_ingest.py --smoke         # 30/30 checks
python3 system/scripts/ri_intake.py --smoke
python3 system/scripts/daily_brief.py --smoke
python3 system/scripts/refresh_all.py --date 2026-05-24
```

Compile check (should be clean):

```bash
python3 -m py_compile \
  system/scripts/canonical_response_eval.py \
  system/scripts/ri_intake.py \
  system/scripts/passive_ri_ingest.py \
  system/scripts/daily_brief.py \
  system/scripts/strategic_memory.py
```

---

## Files changed — what and why

All 6 files are self-contained changes. No schema migrations, no baseline
mutations, no openapi_gpt.yaml changes.

### NEW: `system/CANONICAL_RESPONSE_CONTRACT.md`

The canonical semantic map for all RB response surfaces. Every model, script,
and API endpoint that renders an RB response must comply with it.

Key sections:
- Action-state verb table (what RB says in each system state)
- Prohibited language table (banned outside quoted/code spans)
- 10-row truth table: system state → allowed verb / not allowed
- 9 scenario contracts: daily_brief, relationship_signal_review, manual_ri_intake,
  passive_ri_ingest, no_response_update, strategic_memory_record, draft_ready_action,
  meeting_prep, loop_or_action_creation
- Canonical `canonical_response` JSON block shape
- Future user-defined template switch (documented, not implemented)

Nothing else reads this file yet — it is the doctrine source that everything
else was written to comply with.

### EXPANDED: `system/scripts/canonical_response_eval.py`

4 new scenarios added to the deterministic regression harness:

| New scenario | What it detects |
|---|---|
| `ri_intake_detected` | Missing sections, wrong grounding label, no action-state verb, banned phrases |
| `passive_ri_summary` | No action-state tokens, missing source grounding, no persistence_status, quiet claim despite stale source |
| `daily_brief_top_contract` | Missing Resource Verification section, no grounding/freshness labels, quiet claim when stale |
| `strategic_memory_record` | No RB recorded/updated verb, no storage ref, non-action language, no future-use block |

Each scenario has a passing fixture and a failing fixture baked into `--smoke`.
The `_contains_section()` helper was also fixed to accept:
- Inline labels: `RB match: matched (id)` — previously required heading alone on a line
- Qualified headings: `Resource Verification & Freshness Status:` — previously required exact match

No changes to the existing `no_response_update` scenario or its fixtures.

### MODIFIED: `system/scripts/ri_intake.py`

Added `_build_canonical_response()` helper function (~45 lines, private, above
`_confirmation_required_for()`). Builds the standard JSON block defined in the
contract.

`review()` return dict gains one new key: `canonical_response`. Shape:
```python
{
    "scenario": "manual_ri_intake",
    "action_state": "proposed",
    "summary": "RB proposed <signal_type> RI event for <name>...",
    "facts": [...],
    "persistence": {"status": "proposed_write_pending_confirmation", "bundle_id": ..., ...},
    "projection": {"applied": [], "pending": [...], "blocked": []},
    "recommended_actions": [...],
    "source_refs": [...],
    "grounding": "manual_user_provided",
    "freshness": ...,
    "confidence": ...,
}
```

All existing keys on the `review()` return are untouched. Backwards-compatible.
The `confirm()` function and all other entry points are unchanged.

### MODIFIED: `system/scripts/passive_ri_ingest.py`

Two additions:

**1. `canonical_response` block on `run()` return** (at the end of the summary
section, before `return result`). Same JSON shape as above with
`scenario: passive_ri_ingest`. Includes:
- `action_state`: `proposed` if events_written > 0, else `blocked` if blocked > 0,
  else `not_persisted`
- `facts`: candidates_seen / events_written / duplicates / blocked / source_unavailable counts
- `persistence`: bundle_ids, event_ids, proposed contact names
- `projection.pending`: `last_touch:<name>` for each proposed event
- `projection.blocked`: reason strings for each blocked candidate

**2. `_smoke_confirm_write()` function** (~120 lines). Called at the end of
`_smoke()` so it runs automatically with `--smoke`. This is the P5 write-path
isolation test. It:
- Rebinds `rb_core.BASELINE_PATH`, `rb_core.SNAPSHOTS_DIR`, `ri_events.EVENTS_DIR`,
  `ri_events.INDEX_PATH`, `ri_intake.PENDING_PATH` — all to a tmpdir
- Monkeypatches `mutations._validate_baseline_or_rollback` with a lightweight
  JSON-parse check (the original calls `subprocess.run(["python3", SCHEMA_VALIDATOR])`
  which starts fresh and reads the real `BASELINE_PATH` — it can't see the in-process
  rebinding, so the monkeypatch is required for isolation)
- Writes a synthetic baseline with one RC contact, `last_touch: 2025-12-01`
- Appends a `last_touch_update` passive event, stashes its bundle
- Calls `ri_intake.confirm(event_id, dry_run=False)` — a live write into the temp baseline
- Asserts all of: `persistence_status == persisted`, temp baseline `last_touch`
  advanced to `2026-05-20`, production baseline untouched (vs. pre-smoke snapshot),
  follow-up event `persistence.status == persisted`, pending bundle consumed
- Restores all rebindings in `finally`

### MODIFIED: `system/scripts/strategic_memory.py`

`record()` used to return `"canonical_response": <string>`. Now returns
`"canonical_response": <dict>` matching the standard block shape with these
specifics:
- `scenario: strategic_memory_record`
- `action_state`: `"recorded"` (new signal) or `"updated"` (seen before)
- `persistence.status: "persisted"` — this is always a real write when it gets here
- `grounding: manual_user_provided`, `confidence: high`
- `canonical_response.text`: the original human-readable string, preserved for
  Custom GPT rendering

If anything downstream parsed `canonical_response` as a string, it will break.
Check: nothing in `api/server.py` or any other script reads `canonical_response`
off the `record()` return before returning it to the caller — the API just passes
it through. If the Custom GPT prompt referenced it, it used the `.text` field
(now explicit instead of the whole value being the string).

### MODIFIED: `system/api/custom_gpt_prompt.md`

New section inserted at the very top, before "Action availability — read this first":

```
## Canonical response contract — read this first
```

Contains 5 operative rules (use API canonical blocks, never convert proposed→completed,
never imply mutation without API confirmation, stale-source caveats first, trust-first
daily brief order) plus the allowed verb list and prohibited phrase list inline.

This is a prompt change. The Custom GPT Instructions field needs to be updated
with the new content before it takes effect for ChatGPT sessions. The file is the
source of truth — paste its full contents into the Instructions field.

---

## Commit instructions

One commit, per the session hygiene preference:

```bash
git add \
  system/CANONICAL_RESPONSE_CONTRACT.md \
  system/scripts/canonical_response_eval.py \
  system/scripts/ri_intake.py \
  system/scripts/passive_ri_ingest.py \
  system/scripts/strategic_memory.py \
  system/api/custom_gpt_prompt.md

git commit -m "RB 9.5: canonical response contract, eval harness (5 scenarios), canonical_response blocks on ri_intake/passive_ri/strategic_memory, GPT prompt contract section, passive RI confirmation write smoke"
```

---

## After commit: Custom GPT prompt update

The `system/api/custom_gpt_prompt.md` change only takes effect in ChatGPT
after you paste the updated file contents into the Custom GPT Instructions field.

Steps:
1. Open `system/api/custom_gpt_prompt.md`
2. Copy the full contents
3. Open the RB Custom GPT → Edit → Instructions
4. Replace with the new contents
5. Save

The new "Canonical response contract" section is the first thing the model reads.
It should immediately affect how the GPT handles `canonical_response` blocks from
the API and whether it uses proposed vs recorded language.

---

## What was explicitly NOT changed

- `system/api/openapi.yaml` — no new endpoints
- `system/api/openapi_gpt.yaml` — op count unchanged (still ≤30)
- `system/baseline_index.json` — not touched
- Any card, brief, loop, or thread file
- `system/STATUS.md` or `system/MANIFEST.md` — update these if your standard
  post-commit flow requires it

---

## If a host terminal test fails

**`ri_smoke_test.py` fails:** Check which endpoint. Most likely cause is the
`canonical_response` key on the `ri_intake` review response being an unexpected
type (dict vs string). The API route for `POST /ri_events/review` passes the
`ri_intake.review()` result through directly — confirm the serializer handles dict.

**`api_smoke_test.py` fails:** Same as above. Also check `POST /strategic_memory/record`
if that endpoint is tested — `canonical_response` is now a dict.

**`morning_path_test.py` fails:** If the API step fails, check the fastapi
startup and the `/daily_brief` route — `passive_ri_ingest.run()` now returns a
`canonical_response` key that was not present in 9.4. The daily brief renderer
should ignore unknown keys from the ingest cache, but verify.

In any of these cases, do not force-push. Fix the failure, re-run the smoke, then commit.

---

## Next sprint for Codex (after this commit lands)

The next priorities are documented in `system/project_sprint_next.md` (memory)
and in the full completion handoff at
`system/CLAUDE_HANDOFF_2026-05-24_RB_9_5_CANONICAL_RESPONSE_HARDENING_COMPLETE.md`.

Top candidate: **passive RI confirmation loop with live data** — the write path
is now proven by the smoke. What's missing is real passive events from live
email/Apple Messages data (P-019). Until P-019 is on real hardware, proposed
events accumulate but projections don't move.
