# Claude Handoff — RI Event Intake sprint (return)

**Date:** 2026-05-20
**Builder:** Claude (Cowork)
**Sprint:** RI event intake foundation (from `CLAUDE_HANDOFF_2026-05-19_CARRYOVERS.md` + the 2026-05-19 testing redirect)
**Anchoring protocols:** `P-020 Conversation Artifact Ingestion`, `P-021 RI Event Sourcing Architecture`
**Anchoring traces:** `T-2026-05-19-005` (PerfectHire transcript), `T-2026-05-19-006` (recruiting RI persistence)
**Anchoring design:** `system/RI_EVENT_INTAKE_DESIGN.md` (written + approved this sprint)

## TL;DR

The RI event intake foundation is implemented end-to-end. POST `/ri_events/review` accepts all six source_types (`manual_text`, `linkedin_screenshot`, `fathom_manual_paste`, `zoom_manual_paste`, `email_paste`, `recruiting_update`), normalizes input through a per-source pre-processor, classifies via the existing `manual_relationship_intake.assess()` engine, captures the result as an immutable RI event in `system/ri_events/<YYYY-MM>.jsonl`, and returns a proposed mutation bundle with `persistence_status = "proposed_write_pending_confirmation"`. POST `/ri_events/{id}/confirm` (with `dry_run` safety) executes the bundle through the existing `mutations.py` write subcommands and writes a follow-up `correction_event`.

The three trace fixtures all produce the expected behavior:

| Fixture | Expected `event_at` | Got | Confidence |
|---|---|---|---|
| Ryan Hildebrand "interview … went well yesterday" | 2026-05-18 | 2026-05-18 | medium |
| Simin "headhunter I talked to on Friday" | 2026-05-15 | 2026-05-15 | medium |
| PerfectHire "QSR Platform Review - May 19" pasted on 2026-05-20 | 2026-05-19 | 2026-05-19 | high |

Date-of-intelligence anchoring is enforced everywhere. The PerfectHire fixture is the load-bearing case: it correctly anchors to the meeting date `2026-05-19` even though `captured_at` is `2026-05-20`.

41/41 function-level assertions pass via `python3 system/scripts/ri_intake.py --smoke` (sandbox-runnable). A separate HTTP-level smoke (`python3 system/scripts/ri_smoke_test.py`) layers identical fixtures through `FastAPI TestClient` and needs to run on your terminal (sandbox can't install fastapi).

## What landed, by step

### Step 1 — RI event store (storage layer)

**New files:** `system/scripts/ri_events.py`, `system/ri_events/README.md`.

`ri_events.py` implements: `compute_dedupe_key()` per source_type, `generate_event_id()` with monotonic seq, `validate_event()` strict-mode schema enforcement, `append()` dedupe-aware writer (returns existing event_id on re-append, never duplicates), `load_events()` filtered reader, `find_by_event_id` + `find_by_dedupe_key` index lookups, `reindex()` deterministic rebuild from JSONL. CLI: `recent | get | dedupe | reindex | --smoke`. Partition anchor: `event_at`, not `captured_at` (an event for April lands in `2026-04.jsonl` even when captured in May).

`system/.cache/ri_events_index.json` is the secondary index (by_event_id + by_dedupe_key + per-file fingerprints). Cache, gitignored.

Smoke: 16/16 assertions pass in an isolated tmpdir.

### Step 2 — Dispatch + pending bundle cache

**New files:** `system/scripts/ri_intake.py`, `system/scripts/ri_preproc_{manual,recruiting,transcript,linkedin,email}.py` (stubs at this step; filled in by steps 3+4).

`ri_intake.review(req)` dispatches by `source_type` → pre-processor → existing `manual_relationship_intake.assess()` → builds the SCHEMAS.md-shaped RI event → `ri_events.append()` → stashes the proposed mutation bundle in `system/.cache/ri_events_pending.json` → returns the structured response from the design doc.

`ri_intake.confirm(event_id, accept, reject, dry_run)` filters the bundle by operator decision, calls `manual_relationship_intake.apply_mutations()` (unless dry_run), and writes a follow-up RI event with `dedupe.decision = "correction_event"` and `persistence.status` either `persisted` or `rejected_by_operator`.

`dry_run=True` is a load-bearing safety flag (see `Memory note` below).

Smoke: 11/11 assertions pass.

### Step 3 — Recruiting + transcript classifiers (real)

**Files touched:** `system/scripts/manual_relationship_intake.py` (extended), `system/scripts/ri_preproc_recruiting.py` (rewritten), `system/scripts/ri_preproc_transcript.py` (rewritten), `system/scripts/ri_intake.py` (smoke fixtures added).

`manual_relationship_intake._detect_signals` now emits 8 new recruiting state-transition signals alongside the existing tone-oriented ones:

| Signal type | Phrasing |
|---|---|
| `interview_completed` | "interview … went well", "had my interview", "finished interviewing" |
| `recruiter_screen_completed` | "the headhunter I talked to", "recruiter screen", "spoke to a recruiter" |
| `resume_requested` | "asked for my resume", "requested my resume" |
| `resume_sent` | "sent my resume", "sent the resume over" |
| `followup_sent` | "sent the follow-up", "followed up", "replied to the recruiter" |
| `second_conversation_requested` | "wants another conversation", "second interview", "talk again next week" |
| `waiting_on_recruiter` | "haven't heard back", "no response from the recruiter", "waiting on the recruiter" |
| `high_interest_role` | "very interested", "really want this role", "high interest" |

`ri_preproc_recruiting.py` resolves relative dates against `captured_at`: yesterday, today, this morning/afternoon/evening, the day before yesterday, earlier this week, last week, "<weekday>", "this <weekday>" (forward bias for upcoming weekdays in the current week, otherwise default-past). Sets `event_at_confidence` to `high` for explicit ISO, `medium` for clear relative phrasing, `low` for fallback. Records `event_at_source` describing the parse path.

`ri_preproc_transcript.py` parses meeting titles for ISO ("2026-04-12"), long-form ("April 12, 2025"), short-form ("May 19", year inferred from `captured_at`), and slash ("4/12") dates. Extracts participants from speaker labels in the transcript body (handles "Olivia Nielsen: ...", "[00:12:34] Olivia Nielsen ...", and "Olivia Nielsen (00:12:34):" patterns). Filters Todd. Pulls company hint from "Todd <> CompanyName -" title patterns.

Smoke: 24/24 cumulative assertions pass (the prior 11 + 13 new for Ryan / PerfectHire).

### Step 4 — Manual / email / LinkedIn classifiers (real)

**Files touched:** `system/scripts/ri_preproc_{manual,email,linkedin}.py` (rewritten), `system/scripts/ri_intake.py` (smoke fixtures added).

`ri_preproc_manual.py` implements the **quiet-chat RI screen gate**. Returns `chat_scan_decision = "not_found"` for ordinary chat (queries starting with what/when/who/how/tell me/show me/summarize, commands starting with draft/regenerate/refresh/create, meta talk about today.md / MANIFEST.md / test traces). Returns `chat_scan_decision = "found"` for messages containing RI keywords (interview / resume / recruiter / follow-up sent / introducing me / met with / spoke with). Default-passes-through for ambiguous content. Bias is false-positive over false-negative — ordinary chat must never get a "no RI found" response.

`ri_preproc_email.py` parses RFC 5322 headers (From/To/Subject/Date/Message-Id) using `email.utils.parsedate_to_datetime`, plus tolerant fallback for Gmail-style "On Date, Name wrote:" attribution lines. `event_at` anchors to the Date header at high confidence; falls through to attribution-line parse at medium; falls through to `captured_at` at low. Sender name+address pulled into `extracted_people`. `subject` and `message_id` surface on `source_extras` so `compute_dedupe_key` produces a stable email dedupe key.

`ri_preproc_linkedin.py` resolves visible dates from OCR text: "May 17" absolute (high), "Today"/"Yesterday" (high), "2d"/"3w"/"1mo" relative-age (medium). Strips LinkedIn UI noise ("Send message", "View profile", etc.) before classifier sees text. Extracts names from conversation/profile/1st-degree header patterns. Image SHA-256 from `source_ref.image_hash` wins; otherwise hashes the OCR text. Default `event_at_confidence = "medium"` when no visible date (per design-doc default for screenshots).

Smoke: 41/41 cumulative assertions pass (24 + 17 new for email / LinkedIn / quiet-chat positive + negative cases).

### Step 5 — API endpoints

**Files touched:** `system/api/server.py`, `system/api/openapi.yaml`.

Three new operations under tag `ri_intake`:

```text
POST /ri_events/review              → ri_intake.review()
GET  /ri_events/recent              → ri_events.load_events()
POST /ri_events/{event_id}/confirm  → ri_intake.confirm() with dry_run flag
```

`openapi.yaml` grows from 35 to 38 operations. **`openapi_gpt.yaml` is intentionally NOT touched** (still 24 ops, under the 30 Custom GPT cap); RI endpoints stay local-only until the GPT auth/confirm story is solid. Verified via `validate_openapi_gpt.py --json` — 0 RI ops in the GPT subset.

### Step 6 — MCP tools

**Files touched:** `system/mcp/server.py`.

Three new tools alongside the existing `rb.*` surface:

```text
rb.ri_intake_review   → ri_intake.review()
rb.ri_events_recent   → ri_events.load_events()
rb.ri_event_confirm   → ri_intake.confirm() with dry_run
```

Same shape as the FastAPI endpoints; same local-only constraint.

### Step 7 — HTTP smoke test

**New file:** `system/scripts/ri_smoke_test.py`.

End-to-end smoke through the FastAPI surface via TestClient. Covers PerfectHire transcript (T-005), Ryan Hildebrand interview (T-006), Simin recruiter screen (T-006), email_paste with RFC headers, linkedin_screenshot with visible date, three quiet-chat negative fixtures, and GET /ri_events/recent verification. Isolates the event store + pending cache to a tmpdir; uses `dry_run=True` on confirm.

Needs fastapi+httpx installed on the host terminal.

### Step 8 — Daily brief integration

**Files touched:** `system/scripts/daily_brief.py`.

`build_report()` now loads recent RI events via `ri_events.load_events()` (24h window, anchored on `captured_at`) and buckets them into `newly_captured` / `proposed_unconfirmed` / `historical_uploaded` (event_at >14 days before captured_at) / `rejected_today`. `render_today_md` emits a `## RI Events — Last 24 Hours` markdown block only when at least one event landed in the window (silent on empty stream).

Historical-uploaded events render with both `captured_at` AND `occurred_at` so the operator never reads upload as fresh momentum. Proposed-unconfirmed events surface the confirm endpoint URL and bundle_id. Import of `ri_events` is local + `try/except ImportError` so older RB installs without `ri_events.py` don't break.

## Files added or modified

```text
NEW
  system/RI_EVENT_INTAKE_DESIGN.md            (design doc, approved this sprint)
  system/CLAUDE_HANDOFF_2026-05-20_RI_INTAKE_RETURN.md   (this file)
  system/ri_events/README.md
  system/scripts/ri_events.py
  system/scripts/ri_intake.py
  system/scripts/ri_preproc_manual.py
  system/scripts/ri_preproc_recruiting.py
  system/scripts/ri_preproc_transcript.py
  system/scripts/ri_preproc_email.py
  system/scripts/ri_preproc_linkedin.py
  system/scripts/ri_smoke_test.py

MODIFIED
  system/scripts/manual_relationship_intake.py
    + 8 recruiting state-transition signal regexes
    + 8 signal emitter blocks in _detect_signals
  system/scripts/daily_brief.py
    + _load_recent_ri_events() helper
    + ri_events_recent field on the report dict
    + ## RI Events — Last 24 Hours rendering block
  system/api/server.py
    + ri_intake + ri_events imports
    + RIEventsReviewIn + RIEventsConfirmIn pydantic models
    + 3 new endpoint handlers
  system/api/openapi.yaml
    + 3 new operation specs (POST /ri_events/review, GET /ri_events/recent,
      POST /ri_events/{event_id}/confirm)
  system/mcp/server.py
    + ri_intake + ri_events imports
    + 3 new Tool definitions
    + 3 new call_tool branches

NOT TOUCHED (intentionally)
  system/api/openapi_gpt.yaml          (RI ops stay internal; GPT auth deferred)
  system/api/custom_gpt_prompt.md      (step 9 deferred per design doc)
  system/scripts/mutations.py          (RI never adds new mutation primitives;
                                        it routes through the existing ones)
  system/protocols/P-020_*.md          (already specifies the contract)
  system/protocols/P-021_*.md          (already specifies the contract)
```

## Commit commands

Run these from your terminal in the repo root. Order matters because step 5+ depend on the storage + dispatch layers existing. The Cowork sandbox cannot commit (per `feedback_sandbox_git`).

```bash
# Step 1 — storage
git add system/scripts/ri_events.py system/ri_events/README.md
git commit -m "Add ri_events append-only store (RI sprint step 1)

system/scripts/ri_events.py is the storage layer for the RI event-
sourcing foundation in P-021. One JSONL file per ISO month under
system/ri_events/, partitioned on event_at (not captured_at) so a
relationship moment from 2026-04 lands in 2026-04.jsonl even when
captured in May.

Append is dedupe-aware: compute_dedupe_key() runs a source-typed
formula (fathom_manual_paste → fathom share URL or raw_text_hash;
recruiting_update → recruiter+role+event_at_date; email_paste →
message-id or raw_text_hash+subject; manual_text → text hash +
entity signature + date; linkedin_screenshot → image SHA-256;
zoom_manual_paste → raw_text_hash). On a hit, append returns the
existing event_id without writing — the JSONL stays clean.

validate_event() enforces the required-field set; event_at_source
required when event_at_confidence != 'high'.

system/.cache/ri_events_index.json is the secondary index;
reindex() rebuilds deterministically.

CLI: recent | get | dedupe | reindex | --smoke. 16/16 smoke
assertions pass in an isolated tmpdir.

Step 1 of system/RI_EVENT_INTAKE_DESIGN.md build order."

# Step 2 — dispatch + per-source pre-processor stubs
git add \
  system/scripts/ri_intake.py \
  system/scripts/ri_preproc_manual.py \
  system/scripts/ri_preproc_recruiting.py \
  system/scripts/ri_preproc_transcript.py \
  system/scripts/ri_preproc_linkedin.py \
  system/scripts/ri_preproc_email.py
git commit -m "Add ri_intake dispatch + pending bundle cache + per-source pre-processors (RI sprint steps 2-4)

ri_intake.py is the front door for POST /ri_events/review. review()
dispatches by source_type to one of five per-source pre-processor
modules, runs manual_relationship_intake.assess() to produce
signals + metrics + proposed_mutations, builds the SCHEMAS.md-shaped
RI event, persists it via ri_events.append, stashes the proposed
mutation bundle in system/.cache/ri_events_pending.json, and returns
the structured response from system/RI_EVENT_INTAKE_DESIGN.md.

confirm(event_id, accept, reject, dry_run) filters the bundle by
operator decision, calls manual_relationship_intake.apply_mutations
unless dry_run, and writes a follow-up RI event with
dedupe.decision='correction_event' (persisted or rejected_by_operator).

Pre-processors:

  ri_preproc_recruiting.py — resolves relative dates (yesterday,
  Friday, this morning, last week, this Friday) against captured_at.
  event_at_confidence promotes high (explicit ISO) / medium (clear
  relative phrasing) / low (fallback). Pulls recruiter name from
  'the headhunter I talked to' phrasings.

  ri_preproc_transcript.py — parses ISO/long-form/short-form/slash
  date patterns from meeting titles; disambiguates missing years
  against captured_at; extracts participants from Fathom/Zoom/Otter
  speaker labels; filters Todd + stopwords; surfaces speaker list
  in source_extras; pulls company hint from titles.

  ri_preproc_manual.py — quiet-chat RI screen gate. Hard-rejects
  queries (what/when/who/how/tell me/summarize) and commands
  (draft/regenerate/refresh/create) and meta talk (today.md,
  MANIFEST.md, baseline_index). Hard-accepts recruiting + movement
  keywords. Default-passthrough for ambiguity. Bias is
  false-positive over false-negative.

  ri_preproc_email.py — RFC 5322 header parse (From/To/Subject/Date/
  Message-Id) via email.utils.parsedate_to_datetime; Gmail-style
  'On Date, Name wrote:' attribution-line fallback. event_at anchors
  to Date header (high), falls through to attribution-line (medium),
  falls through to captured_at (low). subject + message_id on
  source_extras for stable dedupe key.

  ri_preproc_linkedin.py — visible date resolution (absolute 'May
  17', 'Today'/'Yesterday', relative-age '2d'/'3w'/'1mo'). Strips
  LinkedIn UI noise before classifier sees text. Extracts names
  from conversation/profile/1st-degree header patterns. Image
  SHA-256 from source_ref.image_hash; otherwise hashes OCR text.

ri_intake.py --smoke exercises all six source_types end-to-end:
41/41 assertions pass. Canonical state untouched (dry_run=True on
all confirm calls). Includes the three trace fixtures (PerfectHire,
Ryan Hildebrand, Simin/Hari) + email + LinkedIn + quiet-chat
negative + positive cases.

Memory note: smoke tests that touch mutations.py must use dry_run
because mutations.py writes through core.PROJECT_DIR regardless of
adjacent tmpdir rebinding. The smoke harness encodes this.

Steps 2-4 of system/RI_EVENT_INTAKE_DESIGN.md build order."

# Step 3 — recruiting signal regexes (separate so the diff is reviewable
# without the pre-processor noise)
git add system/scripts/manual_relationship_intake.py
git commit -m "Extend signal detection with recruiting state transitions (RI sprint step 3)

manual_relationship_intake._detect_signals gains eight recruiting
state-transition signals alongside the existing tone-oriented
detectors:

  interview_completed
  recruiter_screen_completed
  resume_requested
  resume_sent
  followup_sent
  second_conversation_requested
  waiting_on_recruiter
  high_interest_role

The detectors describe what *happened*; the existing
warm_recruiting_reengagement and internal_circulation_signal
continue to describe how it *felt*. Coverage tested against the
two T-2026-05-19-006 fixtures (Ryan Hildebrand interview +
Simin/Hari recruiter screen) and the new state-transition
phrasings.

Step 3 of system/RI_EVENT_INTAKE_DESIGN.md build order."

# Step 5 — API endpoints
git add system/api/server.py system/api/openapi.yaml
git commit -m "Wire /ri_events/* endpoints into the FastAPI server (RI sprint step 5)

Three new operations under tag 'ri_intake':
  POST /ri_events/review        → ri_intake.review()
  GET  /ri_events/recent        → ri_events.load_events()
  POST /ri_events/{id}/confirm  → ri_intake.confirm() with dry_run flag

Auth via the existing x-api-key header. openapi.yaml grows from 35
to 38 operations. openapi_gpt.yaml is intentionally NOT touched
(still 24 ops, under the 30 Custom GPT cap); RI endpoints stay
local-only until the GPT auth/confirm story is ready.

Step 5 of system/RI_EVENT_INTAKE_DESIGN.md build order."

# Step 6 — MCP tools
git add system/mcp/server.py
git commit -m "Add ri_intake MCP tools (RI sprint step 6)

Three new tools alongside the existing rb.* surface:
  rb.ri_intake_review   → ri_intake.review()
  rb.ri_events_recent   → ri_events.load_events()
  rb.ri_event_confirm   → ri_intake.confirm() with dry_run

Same shape as the FastAPI endpoints; same local-only constraint
(no Custom GPT exposure).

Step 6 of system/RI_EVENT_INTAKE_DESIGN.md build order."

# Step 7 — HTTP-level smoke test
git add system/scripts/ri_smoke_test.py
git commit -m "Add ri_smoke_test.py HTTP-level smoke test (RI sprint step 7)

End-to-end smoke through FastAPI TestClient. Covers PerfectHire
transcript (T-005), Ryan Hildebrand interview (T-006), Simin
recruiter screen (T-006), email_paste with RFC headers,
linkedin_screenshot with visible date, three quiet-chat negative
fixtures, and GET /ri_events/recent verification.

Isolates the event store + pending cache to a tmpdir so canonical
state is untouched. Confirm calls use dry_run=True per the
smoke-test-mutations rule.

Layered on top of the sandbox-runnable ri_intake.py --smoke
(41/41 assertions); this script needs fastapi+httpx installed
on the host terminal.

Step 7 of system/RI_EVENT_INTAKE_DESIGN.md build order."

# Step 8 — Daily brief integration
git add system/scripts/daily_brief.py
git commit -m "Add 'RI Events — Last 24 Hours' section to daily brief (RI sprint step 8)

build_report() now loads recent RI events via ri_events.load_events
(24h window, anchored on captured_at) and buckets them into:
  newly_captured        (persistence_status='persisted')
  proposed_unconfirmed  (proposed_write_pending_confirmation)
  historical_uploaded   (event_at >14 days before captured_at)
  rejected_today        (rejected_by_operator)

render_today_md emits a '## RI Events — Last 24 Hours' markdown
block only when at least one event landed in the window (silent
when nothing happened). Each event line shows event_at, signal
type, matched people, companies, and optional trace_id.
Historical-uploaded events render with both captured_at AND
occurred-at so the operator never reads upload as fresh momentum
(per P-020/P-021 date-of-intelligence rule). Proposed-unconfirmed
events surface the confirm endpoint URL and bundle_id.

Import is local + try/except so older RB installs without
ri_events.py don't break.

Step 8 (final) of system/RI_EVENT_INTAKE_DESIGN.md build order."

# Optional — the design doc + this handoff
git add \
  system/RI_EVENT_INTAKE_DESIGN.md \
  system/CLAUDE_HANDOFF_2026-05-20_RI_INTAKE_RETURN.md
git commit -m "Add RI event intake design doc + sprint-return handoff"
```

## Action items for Codex / Todd

These are the things that need to happen on the host terminal to close the sprint.

1. **Run the commits above.** Sandbox can't write git; everything above needs your terminal.
2. **Verify both smoke tests pass on your machine:**

   ```bash
   # Function-level (no fastapi needed)
   python3 system/scripts/ri_intake.py --smoke
   # Expected: 41/41 assertions pass, EXIT=0

   # HTTP-level (needs fastapi+httpx)
   pip install fastapi httpx --break-system-packages   # if not installed
   python3 system/scripts/ri_smoke_test.py
   # Expected: ~25/25 assertions pass, EXIT=0
   ```
3. **Verify canonical state is untouched after the smokes:**

   ```bash
   python3 -c "
   import json
   b = json.load(open('system/baseline_index.json'))
   ids = {e.get('id') for e in b}
   assert 'ryan-hildebrand' not in ids
   assert 'max-holmes' not in ids
   assert 'matt-chalzi' not in ids
   # Simin/Olivia status depends on what was in baseline pre-sprint;
   # just verify nothing new appeared from running the smokes.
   print('canonical state clean: OK')
   "
   ```
4. **One real end-to-end exercise once you trust the path.** Pick one of the canonical traces (suggest Ryan Hildebrand or Simin/Hari since their captures are unambiguous) and run a real review → confirm cycle so the first true RI event lands in `system/ri_events/2026-05.jsonl`. Verify the daily brief surfaces it the next morning under "## RI Events — Last 24 Hours".

   Example for Ryan:

   ```bash
   curl -sS -X POST http://127.0.0.1:8765/ri_events/review \
     -H "Content-Type: application/json" \
     -H "x-api-key: $RB_API_KEY" \
     -d '{
       "source_type": "recruiting_update",
       "raw_text": "The interview with Ryan Hildebrand from Global Payments went well yesterday. He was at NRA and wants to have another conversation either this week or next.",
       "name": "Ryan Hildebrand",
       "organization": "Global Payments",
       "captured_at": "2026-05-19T13:30:00-05:00",
       "trace_id": "T-2026-05-19-006"
     }' | python3 -m json.tool
   ```

   The response carries an `event_id` and a `proposed_mutation_bundle_id`. To confirm:

   ```bash
   curl -sS -X POST "http://127.0.0.1:8765/ri_events/<event_id>/confirm" \
     -H "Content-Type: application/json" \
     -H "x-api-key: $RB_API_KEY" \
     -d '{
       "accept": ["addContact:ryan-hildebrand", "openThread:T-2026-05-global-payments-..."],
       "reject": [],
       "dry_run": false
     }' | python3 -m json.tool
   ```

5. **Cloudflare tunnel / FastAPI status check.** The Custom GPT's openapi_gpt.yaml points at `https://physical-simpson-abu-planner.trycloudflare.com`. None of the RI endpoints are exposed there (deliberately), so this doesn't gate the sprint — but the existing GPT actions still need the tunnel up. If you want the GPT to see the new RI surface, expose `POST /ri_events/review` (read-shape, no writes) in `openapi_gpt.yaml` as a follow-up sprint — the design doc Open Question #1 covers that decision.

## Carry-overs into the next sprint

1. **Step 9 from the design doc — Custom GPT prompt hardening for RI.** Deferred per the design doc. Decision needed: expose `POST /ri_events/review` (read-shape only) in `openapi_gpt.yaml`, or keep the whole RI surface local until confirm-via-GPT auth is worked out?

2. **Stale `today.md` / ChatGPT visibility.** Noted on 2026-05-19 but never investigated this sprint. `today.md` header still reads `2026-05-18` even though the `rb-daily-briefing` scheduled task fired this morning. Either (a) the scheduled task didn't successfully complete the write, (b) `daily_briefing.enabled` is false in `settings.json`, or (c) the Cloudflare tunnel + FastAPI weren't running so the GPT couldn't reach `/daily_brief`. Worth a 30-minute investigation.

3. **`api_smoke_test.py` round-trip verification from the previous sprint's carry-overs.** Still flagged as un-run in `CLAUDE_HANDOFF_2026-05-19_CARRYOVERS.md`. Two regression checks (`cards.jeff-wayman.projection_in_sync` and `touch.jeff-wayman.round_trip`) need a host-terminal run.

4. **P-021 Phase 2 — backfill existing Interaction Briefs into RI events.** Deferred per the design doc. Out of scope this sprint; can be picked up once Phase 1 has been exercised on real captures.

5. **P-021 Phase 3 — projection-check tooling.** Compare projected `last_touch` / signal class / loops / active threads derived from the event stream against current canonical files. Deferred until Phase 1 is stable.

6. **Tightening `manual_relationship_intake._proposed_mutations` for the new signal types.** Today, when `interview_completed` fires, `_proposed_mutations` falls through to the generic warm-recruiting opportunity-state thread + loop. A follow-up could add signal-specific bundles: e.g., `interview_completed` → second-conversation scheduling loop with 3-day target; `resume_sent` → recruiter-followup loop with 5-7 day target; `followup_sent` → response-waited loop. The plumbing supports this — just add cases in `_proposed_mutations`.

## Known limitations / gotchas

- **`ri_events/` is committed to git.** The JSONL files are canonical state per P-021. The README in that directory makes this explicit. Raw transcript text never lands here (only `source.raw_text_hash`).
- **`.cache/ri_events_pending.json` is NOT committed.** It's a cache; gitignored along with the rest of `system/.cache/`.
- **`mutations.py` writes through `core.PROJECT_DIR` regardless of tmp-path rebinding on adjacent modules.** Smoke tests touching `apply_mutations()` must use `dry_run=True`. The smoke harness already enforces this; documented in `feedback_smoke_test_mutations.md` memory.
- **Image OCR for `linkedin_screenshot` is not yet in the pipeline.** The pre-processor expects the caller to have OCR'd the screenshot and passed the text in `raw_text`. Step 5+ work to add local OCR via the workspace.
- **Forward-looking date phrasing.** "This Friday" (future) resolves correctly to the upcoming Friday in the same week, but the design assumes recruiting events are usually past-tense. Future-looking phrasings ("recruiter wants to talk Friday") are an edge case; the RI event itself anchors to "now" (the moment the operator stated the commitment), and the loop target carries the forward date.
- **Signal taxonomy is extensible.** Adding a new `signal.type` is a string-only change — no schema migration. Just register the regex in `manual_relationship_intake.py`, emit the signal in `_detect_signals`, and add fixture coverage.

## Memory updates saved this sprint

- `feedback_smoke_test_mutations.md` — smoke tests touching `mutations.py` must use `dry_run`.
- `project_sprint_ri_intake.md` — sprint direction context (already saved earlier).

## Closing

All eight build-order steps land. The traces that motivated the sprint (T-005 and T-006) now have working capture, persistence, and confirmation paths. Once the commits and smoke tests are green on your terminal, the sprint is fully closed and the gaps the field tests flagged are real defects against the new contract — not analysis-only gaps.

The next conversation Todd has where someone says "I had my interview yesterday" or "they asked for my resume" should — through the Custom GPT or directly via the local API — produce a durable RI event with `event_at = yesterday`, a proposed mutation bundle Todd can confirm, and a daily-brief surface that proves it.
