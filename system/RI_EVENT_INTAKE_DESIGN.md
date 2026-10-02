# RI Event Intake — Design

**Status:** draft — awaiting Todd review
**Author:** Claude (Cowork)
**Date:** 2026-05-19
**Sprint:** RI event intake foundation (post-2026-05-19 testing redirect)
**Motivating traces:** `T-2026-05-19-005` (PerfectHire transcript), `T-2026-05-19-006` (recruiting RI passive persistence)
**Anchoring protocols:** `P-020 Conversation Artifact Ingestion`, `P-021 RI Event Sourcing Architecture`, `SCHEMAS.md` (Relationship Intelligence Event)

## Why this exists

RB analyzes well. RB does not prove persistence. Today, transcripts and recruiting updates get strong analysis but nothing lands in `baseline_index.json`, `cards/`, `loop_ledger.md`, `active_threads.yaml`, or any new artifact. The two new traces confirm both failure modes (`TRANSCRIPT-INGESTION-001`, `PASSIVE-RI-PERSISTENCE-001`, `PERSISTENCE-STATUS-001`, `RECRUITING-LOOP-MATERIALIZATION-001`).

The fix is the event-sourcing foundation that P-021 already specifies: every RI-bearing input becomes an immutable event with explicit `event_at`, dedupe decision, and persistence status; canonical state is a projection of the event stream. This design pins that architecture to concrete file paths, classifier inputs/outputs, endpoint contracts, dedupe formulas, and test fixtures so the next coding session can execute without re-litigating the architecture.

## What already exists (do not rebuild)

`system/scripts/manual_relationship_intake.py` (761 lines) and `POST /manual_relationship_intake` (server.py:741) already implement the classifier engine for pasted/screenshot RI text. The existing `assess()` function takes `text`, optional `contact_id` / `name` / `event_at` / `captured_at` / `organization` / `opportunity`, and returns: detected signals (advocacy, sponsor, internal-circulation, etc.), trust/sponsor/advocacy/strategic-value metrics, DRR before/after projection, opportunity state, proposed mutations with `safe_to_write` flags, and a `persistence` block with status `not_persisted` / `pending_apply`. It even has an `apply=True` mode that executes mutations.

**This is the sprint's foundation, not a competitor.** The new layer adds:

1. **Event sourcing on top** — capture each `assess()` result as an immutable RI event in `system/ri_events/*.jsonl` with the SCHEMAS.md shape, dedupe key, and explicit `persistence.status` lifecycle (review → confirm → persisted, or review → reject).
2. **Per-source classifiers** that pre-process raw input (transcript, screenshot, email paste, recruiting update) into the shape `manual_relationship_intake.assess()` already expects, plus source-specific event_at extraction.
3. **A multi-source endpoint** `POST /ri_events/review` that dispatches by `source_type`, calls the appropriate pre-processor, calls the existing `assess()`, persists as an RI event, and returns the structured response with persistence_status.
4. **Confirmation lifecycle** — `POST /ri_events/{id}/confirm` that closes the loop on `proposed_write_pending_confirmation` events.

So the work is mostly *wrapping* the existing intake engine in the event-sourcing discipline P-021 mandates — not rebuilding it. Significantly smaller scope than this doc's first draft implied.

## Goals

1. **One write-back path for RI.** Every RI-bearing input — pasted transcript, LinkedIn screenshot, recruiting update, chat signal, email paste, manual note — produces an `ri_events` JSONL entry with the SCHEMAS.md fields.
2. **Persistence_status is mandatory.** Every response that contains RI states one of `not_persisted | proposed_write_pending_confirmation | persisted`. Never imply persistence without a confirmed write.
3. **Event_at anchors projections, not upload time.** A transcript uploaded today but recorded last month belongs to last month's relationship timeline; the daily brief frames it as "newly uploaded historical artifact," not fresh momentum.
4. **Dedupe + stale-overwrite rules are enforced.** Same-source repeats don't double-project; older evidence cannot overwrite newer state unless explicitly flagged as a correction.
5. **Recruiting + transcript signals materialize.** The two traces' expected behaviors execute end-to-end (proposed loops, contact candidates, thread updates, IB).
6. **Review-first by default.** No auto-write to canonical state except where already safe (e.g., `touch` last_touch with high-confidence participant match + clear meeting date — see P-020's exception clause).

## Non-goals (explicitly deferred)

- Rewriting current ingestion paths to be event-sourced (P-021 Phase 4). Phase 1 only.
- Backfilling existing Interaction Briefs into RI events (P-021 Phase 2).
- Projection-check tooling comparing event stream to baseline (P-021 Phase 3).
- Exposing the review endpoint to ChatGPT through the curated openapi_gpt.yaml. The auth/confirmation story for GPT-driven persistence is its own work; this sprint ships the local API + MCP tool. Custom GPT can call the review *read* shape later.
- More UI polish or daily brief prose (per the sprint redirect's "do not start there" line).

---

## File layout

### New on disk

```text
system/ri_events/
  README.md                          new — describes append-only contract
  2026-05.jsonl                      one file per ISO month, append-only
  2026-06.jsonl
  ...
system/.cache/
  ri_events_index.json               new — projected index for fast lookup
  ri_events_pending.json             new — proposed-but-unconfirmed bundle store
system/scripts/
  manual_relationship_intake.py      EXISTING — classifier engine; stays the
                                     authoritative signal/metric/mutation
                                     proposer. Possibly small additions for
                                     new signal subtypes (recruiting state
                                     transitions). Do NOT duplicate its work.
  ri_events.py                       new — load/append/index/dedupe engine;
                                     append-only writer for system/ri_events/*.jsonl
  ri_intake.py                       new — POST /ri_events/review backend.
                                     Dispatches by source_type to per-source
                                     pre-processors, then calls
                                     manual_relationship_intake.assess(),
                                     then persists via ri_events.py
  ri_preproc_transcript.py           new — fathom_manual_paste, zoom_manual_paste
                                     pre-processor: extract title/meeting_at,
                                     normalize participants, hand structured
                                     text + event_at to assess()
  ri_preproc_recruiting.py           new — recruiting_update pre-processor:
                                     classify recruiter-screen vs
                                     interview-completed vs resume-sent etc.
                                     Adds recruiting-specific signal
                                     subtypes to the assess() call.
  ri_preproc_linkedin.py             new — linkedin_screenshot pre-processor
                                     (OCR + structural parse)
  ri_preproc_email.py                new — email_paste pre-processor:
                                     extract headers (from/to/subject/date),
                                     compute event_at from message date
  ri_preproc_manual.py               new — manual_text fallback +
                                     chat-scan screen (quiet-RI gate)
  ri_smoke_test.py                   new — exercises PerfectHire, Ryan, Simin
system/protocols/
  P-021_ri_event_sourcing.md         existing — no change this sprint
  P-020_conversation_artifact_ingestion.md   existing — no change this sprint
system/api/
  server.py                          add POST /ri_events/review +
                                     GET /ri_events/recent +
                                     POST /ri_events/{event_id}/confirm.
                                     The existing POST /manual_relationship_intake
                                     stays — it remains a thin direct-access
                                     endpoint for ad-hoc text. /ri_events/review
                                     is the source-typed, event-persisted
                                     superset.
  openapi.yaml                       add the three operations above
  openapi_gpt.yaml                   NOT TOUCHED this sprint (still 23 ops,
                                     under the 30-op cap). RI ops stay
                                     internal until the GPT auth story is ready.
system/mcp/
  server.py                          add ri_intake_review tool (local-only)
```

### How the existing engine and the new layer compose

```
                             POST /ri_events/review (NEW)
                                       |
                                       v
                            +----------------------+
                            |    ri_intake.py      |  (NEW)
                            +-----------+----------+
                                        |
                                        | (dispatch by source_type)
                                        v
                  +---------------------+---------------------+
                  | ri_preproc_transcript.py / recruiting /   |  (NEW)
                  | linkedin / email / manual                  |
                  | (normalize raw input → text + event_at +    |
                  | structured hints for assess())             |
                  +---------------------+---------------------+
                                        |
                                        v
                  manual_relationship_intake.assess(text, …)   (EXISTING)
                                        |
                                        v
                  +----------------------------------------------+
                  | signals, metrics, drr_projection,            |
                  | opportunity_state, proposed_mutations         |
                  +-----------------------+----------------------+
                                          |
                                          v
                              +-----------------------+
                              |   ri_events.py        |  (NEW)
                              |  append + dedupe +    |
                              |  index                |
                              +-----------+-----------+
                                          |
                                          v
                            system/ri_events/YYYY-MM.jsonl
                            system/.cache/ri_events_index.json
                            system/.cache/ri_events_pending.json
                                          |
                                          v
                              response with persistence_status
```

POST /ri_events/{id}/confirm executes the pending bundle via the existing `mutations.py` write subcommands (the same ones `manual_relationship_intake.apply_mutations` already uses) and writes a follow-up RI event.

### Partitioning rationale (JSONL per month)

P-021 mandates JSONL + append-only. Partitioning options were `single-file`, `per-day`, `per-month`, `per-source`. Per-month wins because:

- Monthly files cap individual file size in the realistic case (low hundreds of events/day max). A single-file approach would be fine today but ugly at 12-month volume.
- Per-day is too granular — daily-brief reads tend to span the trailing 24–48h (often crosses a day boundary) and lookbacks are commonly multi-week.
- Per-source fragments the stream by ingestion path, which fights event sourcing's "one stream is canonical" tenet. Source is a field, not a partition.

The `ri_events_index.json` cache lets readers skip the JSONL scan for common lookups (by event_id, by dedupe_key, by entity, by date range). Rebuilt deterministically from the JSONL files via `ri_events.py --reindex`.

### Why a separate `ri_events_pending.json` cache

Proposed-but-unconfirmed mutations need somewhere to live between the review response and Todd's confirmation. If we wrote them to the JSONL immediately, we'd violate the "events are immutable" rule when Todd rejects. So:

- The review endpoint writes the *RI event itself* (immutable) with `persistence.status = "proposed_write_pending_confirmation"` and a `proposed_mutation_bundle_id`.
- The bundle (what RB *would* write to baseline/threads/loops if confirmed) lives in `ri_events_pending.json`, keyed by `proposed_mutation_bundle_id`. It is a cache, mutable, ephemeral.
- On confirm: execute the mutations through `mutations.py`, write a follow-up RI event with `persistence.status = "persisted"` pointing at the projection targets, drop the bundle from the pending cache.
- On reject: write a follow-up RI event with `persistence.status = "rejected_by_operator"`, drop the bundle.

This preserves event-sourcing semantics ("never edit an existing event; write a corrective event") while keeping the proposal payload out of the canonical stream.

---

## RI event shape — concrete

Anchored on the SCHEMAS.md skeleton. Added a few fields the protocols imply but don't fully name. **Bold** fields are required.

```json
{
  "event_id": "ri_2026-05-19_perfecthire-qsr-platform-review_001",
  "captured_at": "2026-05-19T13:00:00-05:00",
  "event_at": "2026-05-19T10:00:00-05:00",
  "event_at_confidence": "high",
  "event_at_source": "fathom_meeting_title_iso_date",
  "source": {
    "type": "fathom_manual_paste",
    "id": "fathom://share/94njMJ56SsKKmCMXjwS33zbuXdWxeJw6",
    "path": null,
    "title": "Todd <> PerfectHire - QSR Platform Review - May 19",
    "raw_text_hash": "sha256:..."
  },
  "entities": {
    "people": [
      {"raw": "Olivia Nielsen", "matched_id": "olivia-nielsen", "match_confidence": 0.95, "decision": "matched_existing"},
      {"raw": "Max Holmes",     "matched_id": null,             "match_confidence": null, "decision": "propose_new_contact"},
      {"raw": "Matt Chalzi",    "matched_id": null,             "match_confidence": null, "decision": "propose_new_contact"}
    ],
    "companies": [
      {"raw": "PerfectHire", "matched_id": null, "decision": "propose_new_company"}
    ]
  },
  "dedupe": {
    "dedupe_key": "fathom_manual_paste:sha256:...:2026-05-19T10:00:00-05:00",
    "decision": "new_event",
    "duplicates": []
  },
  "signal": {
    "type": "advisory_opportunity",
    "subtypes": ["warm_strategic_relationship", "pending_ceo_intro"],
    "direction": "bidirectional",
    "substance": "high",
    "confidence": 0.85,
    "strategic_relevance_impact": 0.8,
    "relationship_warmth_impact": 0.7,
    "recommended_projection_changes": [
      "propose_contact:max-holmes",
      "propose_contact:matt-chalzi",
      "open_thread:T-2026-05-perfecthire-qsr-platform-review",
      "open_loop:matt-chalzi-intro-followup",
      "open_loop:perfecthire-followup-email",
      "write_brief:2026-05-19-perfecthire-qsr-platform-review"
    ]
  },
  "persistence": {
    "status": "proposed_write_pending_confirmation",
    "proposed_mutation_bundle_id": "mb_2026-05-19_perfecthire_001",
    "projected_to": [],
    "validated_at": null
  },
  "trace_id": "T-2026-05-19-005"
}
```

### Required-field rules (consolidated from P-020 + P-021)

| Field | Required when | Notes |
|---|---|---|
| `event_id` | always | `ri_<YYYY-MM-DD>_<slug>_<seq>`. Slug derived from primary entity + signal subject. Seq increments within the day. |
| `captured_at` | always | UTC offset preserved. |
| **`event_at`** | always | If unresolvable, the event cannot be projected. See "Date-of-intelligence gate." |
| `event_at_confidence` | always | `high`, `medium`, `low`. Drives projection eligibility — `low` events stay review-only. |
| `event_at_source` | always when `event_at_confidence != high` | Names the inference path (transcript metadata, calendar match, file mtime, Todd-confirmed, etc.). |
| `source.type` | always | One of the six source_types below + `interaction_brief_backfill` (Phase 2). |
| `source.raw_text_hash` | when source has raw text | SHA-256 over normalized text. Drives content-dedupe. |
| `entities.people[].decision` | always | `matched_existing`, `propose_new_contact`, `ambiguous_requires_review`, `suppressed`. |
| `entities.companies[].decision` | always | `matched_existing`, `propose_new_company`, `alias_pending`, `suppressed`. |
| `dedupe.dedupe_key` | always | See "Dedupe rules" below. |
| `dedupe.decision` | always | `new_event`, `duplicate_of_existing`, `updates_existing_projection`, `correction_event`, `same_source_new_signal`. |
| `signal.type` | always | Taxonomy in next section. |
| `persistence.status` | always | One of three values (next section). |
| `trace_id` | when triggered from a test trace or operator session | Connects event back to T-IDs. |

---

## Persistence status — state machine

Three states, plus two terminal states for the pending bundle workflow.

```
                                            +---------------+
                                            |  rejected_by_  |
                                            |   operator     |
                                            +-------^--------+
                                                    | (reject via /confirm?accept=false)
+-----------------+        review        +----------+----------+    confirm    +-----------+
| (no event yet)  | -------------------> |  proposed_write_    | ------------> | persisted |
+-----------------+    POST /ri_events   |  pending_           |               +-----------+
                       /review           |  confirmation       |
                                         +----------+----------+
                                                    | (analysis-only response, no mutation)
                                                    v
                                            +---------------+
                                            | not_persisted |
                                            +---------------+
```

### Rules

- `not_persisted` — RB produced analysis but proposed no mutation bundle. Used when the input has no actionable RI (chat scan finds nothing) or when the classifier judges substance too low. **The assistant must not say "RB captured / remembered / updated."**
- `proposed_write_pending_confirmation` — RB wrote the RI event and the proposed mutation bundle. The bundle has not touched `baseline_index.json`, `active_threads.yaml`, `loop_ledger.md`, `cards/`, or `briefs/`. Response says exactly what RB *would* do and waits.
- `persisted` — `mutations.py` ran, post-validation passed (the existing pattern from `touch_contact` projection sync). The event records `projected_to` and `validated_at`.
- `rejected_by_operator` — confirmation request returned reject. Bundle is discarded; corrective RI event recorded so the rejection is part of history.
- *(Internal-only)* `failed_post_validation` — `mutations.py` returned an error or post-read showed drift. Treated identically to `rejected_by_operator` for the chat response: "RB attempted the write, validation did not pass, no canonical state changed." Captured for debugging.

### What each status forbids in the chat response

| Status | Forbidden phrases | Required phrases |
|---|---|---|
| `not_persisted` | "captured", "remembered", "updated", "saved" | "analysis only; nothing persisted" (or equivalent) |
| `proposed_write_pending_confirmation` | "captured", "saved", "updated baseline" | "proposed: ...; persistence pending your confirm" |
| `persisted` | (none — accurate to say captured/saved/updated) | "persisted to: \<projection targets\>" |
| `rejected_by_operator` | "captured" | "rejected; no canonical change" |

---

## Source types — classifier inputs and outputs

Six source_types, one classifier each. All emit the same normalized event shape.

| `source_type` | Trigger | Classifier inputs | Special handling |
|---|---|---|---|
| `manual_text` | Plain chat sentence from Todd that screens positive for RI | `raw_text`, conversation context | The chat-scan path. See "Quiet RI screening" below. |
| `linkedin_screenshot` | Uploaded LinkedIn screenshot (PNG/JPEG) | OCR'd text, image file metadata | `event_at_confidence = medium` unless screenshot includes a visible date. |
| `fathom_manual_paste` | Pasted Fathom transcript | `raw_text`, optional title, optional meeting_at | Anchored to meeting date from title or first-line metadata, not paste time. |
| `zoom_manual_paste` | Pasted Zoom transcript or chat log | `raw_text`, optional title | Same anchoring rule. Zoom chat logs require participant attribution. |
| `email_paste` | Pasted email or thread excerpt | `raw_text`, optional sender/subject headers | Extract `event_at` from message date header; fall back to body context. |
| `recruiting_update` | Operator describes interview/screen/follow-up movement | `raw_text` describing the recruiting state change | Highest classifier priority for `manual_text` content — the recruiting signal screen runs first. |

### Quiet RI screening on chat (per the sprint UX rules)

Plain chat is the default. Do NOT say "no RI found" on ordinary conversation. Trigger an RI event capture only when the message contains a positive RI signal — the classifier's job is to decide. Examples that should trigger:

- "the interview went well"
- "they asked for my resume"
- "I sent the follow-up"
- "Olivia is introducing me to the CEO"
- "we wrapped up the call with PerfectHire"
- "Hari's recruiter went dark on me"

Examples that should NOT trigger:

- "what's on my plate today?"
- "tell me about Olivia"
- "regenerate today.md"
- "draft me an email" (drafting is not RI; the *sent* event is)

The screen runs locally in `ri_classifier_manual.py` using a small regex/keyword tier + a structured prompt to a screening function. False-positive rate matters more than recall — when in doubt, the classifier emits `not_persisted` with no event.

### Artifact uploads: always explicit acknowledgement

For uploaded screenshots, transcripts, pasted files, exports: ALWAYS state "RI scan: \<found | not found\>", identify signal, state event_at + confidence, state persistence_status, propose write if warranted. This is the inverse of the quiet chat rule.

---

## Signal taxonomy

Drawn from P-020 §"Signal extraction" + P-021. The 12 base types:

| `signal.type` | Typical source | Recommended projection |
|---|---|---|
| `inbound_opportunity_signal` | Email / call where the other party signals buying/advisory/partnership interest | Open thread, open loop, possibly LMI→LKI |
| `advisory_opportunity` | Discovery call with strategic discussion | Open thread, propose contact, open IB |
| `job_opportunity_movement` | Recruiter / interviewer status change | Update opportunity thread; open follow-up loop; flag urgency/momentum |
| `interview_completed` | Operator reports interview just happened | `touch` last_touch (high-confidence match); update opportunity thread; open second-conversation loop if intent stated |
| `recruiter_screen_completed` | First recruiter conversation done | Open recruiter contact (if new); open opportunity thread; open resume-send loop if requested |
| `resume_requested` | Recruiter or hiring manager asked for resume | Open loop "send resume to \<recruiter\>" due today |
| `resume_sent` | Operator confirms resume was sent | Close resume loop; open recruiter-followup loop with 5-7 day target |
| `followup_sent` | Operator confirms follow-up email/message went out | Close obligation loop; open response-waited loop if appropriate |
| `pending_ceo_intro` / `pending_introduction` | Someone has offered or implied an intro | Open intro-broker loop tied to the broker contact |
| `relationship_warmth_change` | Operator describes mood/tone shift in a relationship | `touch` last_touch if recent contact; no thread movement without specifics |
| `operator_pain` / `positioning_resonance` | Conversation surfaced ICP or product-market signal | Propose contact/company candidate; open IB; no thread without strategic context |
| `stale_signal` | Source date is older than current projection | Event recorded as historical evidence only; no projection change |

The taxonomy is extensible — adding a type means: define the signal name, define which projection changes it recommends, add a fixture, update the daily-brief renderer to map it. No schema migration required since `signal.type` is a string.

---

## Dedupe rules — concrete formulas

Per P-021, dedupe key is "source type, source id/path, event timestamp, matched people, signal type." Operationalized:

```
dedupe_key = <source_type> + ":" + <source_stable_id> + ":" + <event_at_iso>

where source_stable_id is, in order of preference:
  fathom_manual_paste   → source.id (fathom share URL) if present, else source.raw_text_hash
  zoom_manual_paste     → source.raw_text_hash
  email_paste           → message-id header if present, else source.raw_text_hash + subject
  linkedin_screenshot   → image file SHA-256
  recruiting_update     → matched recruiter contact_id + role-slug + event_at_date
  manual_text           → raw_text_hash + entities + event_at_date
```

### Decision matrix

```
incoming event has dedupe_key K, entities E, signal S, event_at T

if exists prior event with dedupe_key == K:
    decision = duplicate_of_existing
    record duplicates: [prior_event_id]
    do NOT project again

elif exists prior event with same source_stable_id but different event_at:
    decision = same_source_new_signal
    record duplicates: [prior_event_id_list]
    project normally

elif exists prior projection in target with newer state than T AND incoming is NOT marked correction:
    decision = stale_signal (special — see signal.type taxonomy)
    do NOT project; record as historical evidence

elif exists prior event with overlapping entities + signal_type within ±1 hour of T:
    decision = updates_existing_projection
    record duplicates: [prior_event_id]
    project as update (e.g., upgrade signal.substance from "medium" to "high")

elif incoming explicitly flagged correction_event by operator:
    decision = correction_event
    record duplicates: [prior_event_id]
    project as correction (reverses prior projection)

else:
    decision = new_event
    project normally
```

Edge cases worth calling out:

- **Same call, two notetakers.** A Fathom and Zoom transcript of the same meeting produce two different `source_stable_id`s but matching entities + close `event_at`. The ±1 hour rule catches this as `updates_existing_projection`; the second-source event preserves both `source` references.
- **Backfill re-runs.** If P-021 Phase 2 backfill is re-run, dedupe key is `interaction_brief_backfill:<brief_id>:<brief.date>` and is naturally idempotent.

---

## Proposed mutation bundle — format

Each bundle lives in `ri_events_pending.json` keyed by `proposed_mutation_bundle_id`. The bundle is a list of operations, each shaped as a direct mapping to a `mutations.py` subcommand:

```json
{
  "bundle_id": "mb_2026-05-19_perfecthire_001",
  "event_id": "ri_2026-05-19_perfecthire-qsr-platform-review_001",
  "created_at": "2026-05-19T13:00:00-05:00",
  "operations": [
    {
      "op": "contact-add",
      "args": {
        "id": "max-holmes",
        "name": "Max Holmes",
        "company": "PerfectHire",
        "title": "CTO",
        "signal_class": "LMI",
        "source": "fathom_transcript:T-2026-05-19-005"
      },
      "rationale": "Mentioned as PerfectHire CTO in transcript; substantive technical exchange around POS/scheduling.",
      "auto_safe": false
    },
    {
      "op": "thread-open",
      "args": {
        "id": "T-2026-05-perfecthire-qsr-platform-review",
        "title": "PerfectHire / QSR Platform Review",
        "type": "advisory",
        "people": ["olivia-nielsen", "max-holmes"],
        "companies": ["PerfectHire"]
      },
      "rationale": "Strategic context: discovery/advisory opportunity around QSR scheduling.",
      "auto_safe": false
    },
    {
      "op": "loop-add",
      "args": {
        "id": "L-2026-05-19-XXX",
        "obligation": "Follow-up email to Olivia Nielsen after QSR Platform Review",
        "target": "2026-05-22"
      },
      "rationale": "Explicit follow-up commitment in transcript.",
      "auto_safe": false
    },
    {
      "op": "touch",
      "args": {"id": "olivia-nielsen", "date": "2026-05-19", "source": "fathom_transcript:T-2026-05-19-005"},
      "rationale": "Substantive 52-min meeting on 2026-05-19; high-confidence participant match.",
      "auto_safe": true
    }
  ],
  "auto_safe_operations": ["touch:olivia-nielsen"],
  "review_required_operations": ["contact-add:max-holmes", "contact-add:matt-chalzi", "thread-open:...", "loop-add:..."]
}
```

### Auto-safe rule (P-020 §"Review-first mutation rule" exception)

`auto_safe: true` is permitted *only* for:

- `touch` operations on a contact already in baseline whose match confidence is ≥ 0.9 AND `event_at_confidence == "high"` AND `event_at` ≥ existing `last_touch`.

Everything else is review-required this sprint. The auto-safe set can grow once trust accumulates.

The confirm endpoint takes the bundle id and a per-operation accept/reject map. Operations marked `auto_safe: true` execute on the review pass without waiting; the rest wait for confirm.

---

## Endpoint contract

### POST `/ri_events/review`

**Request:**

```json
{
  "source_type": "fathom_manual_paste",
  "raw_text": "...optional full text...",
  "summary": "...optional pre-extracted summary...",
  "event_at": "2026-05-19T10:00:00-05:00",
  "event_at_confidence": "high",
  "event_at_source": "fathom_meeting_title_iso_date",
  "captured_at": "2026-05-19T13:00:00-05:00",
  "people": [
    {"raw": "Olivia Nielsen", "company": "PerfectHire"},
    {"raw": "Max Holmes", "company": "PerfectHire", "title": "CTO"}
  ],
  "companies": [{"raw": "PerfectHire"}],
  "signal_type": "advisory_opportunity",
  "proposed_action": "open thread + propose intros + loop for follow-up",
  "confidence": 0.85,
  "trace_id": "T-2026-05-19-005",
  "source_ref": {
    "id": "fathom://share/94njMJ56SsKKmCMXjwS33zbuXdWxeJw6",
    "title": "Todd <> PerfectHire - QSR Platform Review - May 19"
  }
}
```

All fields except `source_type` are optional — the classifier infers what's missing. `raw_text` is preferred over `summary` because the classifier wants to do its own work; if both are present, `raw_text` wins.

**Response (200):**

```json
{
  "event": { ...the full RI event as written to ri_events JSONL... },
  "ri_scan": "found",
  "summary": "PerfectHire / QSR Platform Review — advisory-opportunity signal, high substance.",
  "matched_contacts": ["olivia-nielsen"],
  "unmatched_entities": [
    {"raw": "Max Holmes", "type": "person", "decision": "propose_new_contact"},
    {"raw": "Matt Chalzi", "type": "person", "decision": "propose_new_contact"},
    {"raw": "PerfectHire", "type": "company", "decision": "propose_new_company"}
  ],
  "active_threads_touched": [],
  "proposed_loops": [
    {"obligation": "Follow-up email to Olivia Nielsen after QSR Platform Review", "target": "2026-05-22"},
    {"obligation": "Prep for Matt Chalzi CEO intro (pending from Olivia)", "target": "2026-05-26"}
  ],
  "proposed_contact_updates": [
    {"id": "olivia-nielsen", "field": "last_touch", "from": "2026-04-30", "to": "2026-05-19", "auto_safe": true}
  ],
  "proposed_contact_adds": [
    {"id": "max-holmes", "name": "Max Holmes", "company": "PerfectHire", "title": "CTO"},
    {"id": "matt-chalzi", "name": "Matt Chalzi", "company": "PerfectHire", "title": "CEO"}
  ],
  "proposed_thread_opens": [
    {"id": "T-2026-05-perfecthire-qsr-platform-review", "title": "PerfectHire / QSR Platform Review", "type": "advisory"}
  ],
  "proposed_brief_writes": [
    {"slug": "2026-05-19-perfecthire-qsr-platform-review", "person": "olivia-nielsen", "date": "2026-05-19"}
  ],
  "dedupe": {"decision": "new_event", "duplicates": []},
  "persistence_status": "proposed_write_pending_confirmation",
  "proposed_mutation_bundle_id": "mb_2026-05-19_perfecthire_001",
  "auto_applied": [
    {"op": "touch", "id": "olivia-nielsen", "date": "2026-05-19"}
  ],
  "confirmation_required_for": [
    "contact-add:max-holmes",
    "contact-add:matt-chalzi",
    "thread-open:T-2026-05-perfecthire-qsr-platform-review",
    "loop-add:L-2026-05-19-XXX",
    "loop-add:L-2026-05-19-XXY",
    "write_brief:2026-05-19-perfecthire-qsr-platform-review"
  ]
}
```

If the classifier finds no RI worth recording (chat-scan negative case), the response is:

```json
{
  "ri_scan": "not_found",
  "persistence_status": "not_persisted",
  "event": null
}
```

— and no JSONL entry is written.

### GET `/ri_events/recent?limit=N&since=ISO`

Returns the trailing N events with full shape. Used by the daily brief to find newly-captured + proposed-unconfirmed RI events since yesterday.

### POST `/ri_events/{event_id}/confirm`

**Request:**

```json
{
  "accept": ["contact-add:max-holmes", "thread-open:T-2026-05-perfecthire-qsr-platform-review", "loop-add:L-2026-05-19-XXX"],
  "reject": ["contact-add:matt-chalzi"]
}
```

**Response:**

```json
{
  "event_id": "ri_2026-05-19_perfecthire-qsr-platform-review_001",
  "follow_up_event_id": "ri_2026-05-19_perfecthire-qsr-platform-review_002",
  "persistence_status": "persisted",
  "applied": [...],
  "rejected": [...],
  "post_validation": {"all_passed": true, "details": [...]}
}
```

The follow-up event makes the persistence transition part of the immutable history (corrective-event style).

---

## Daily brief integration

Add one section to `daily_brief.py` rendering:

```
## RI Events — Last 24 Hours

### Newly captured (persisted)
- 2026-05-19 10:00 — PerfectHire QSR Platform Review (Olivia Nielsen, Max Holmes)
  → opened thread T-2026-05-perfecthire-qsr-platform-review
  → 2 new loops, 2 contact proposals pending confirmation
  → trace: T-2026-05-19-005

### Proposed (awaiting your confirm)
- 2026-05-19 — Ryan Hildebrand interview-completed signal (Global Payments)
  → would open second-conversation loop, update opportunity thread
  → trace: T-2026-05-19-006
  → confirm: POST /ri_events/{event_id}/confirm

### Historical artifacts uploaded today (not fresh momentum)
- 2026-05-19 captured / 2026-04-12 occurred — old Fathom transcript
  → routed to evidence only; daily-brief momentum unchanged

### Suppressed / no action
- 3 chat messages screened, none crossed the RI threshold (operator chat, not relationship signal)
```

This satisfies Priority 5 from the sprint redirect: brief surfaces captured + proposed-unconfirmed + stale + what-to-ignore.

---

## UX rules — compact response shape when RI is detected

The trace defects called out long-prose analysis without persistence. Required compact shape going forward when RI is found:

```
RI scan: found.
Signal: <signal.type> (substance <high|medium|low>, confidence <n>)
Event date: <event_at> (<event_at_confidence>)
Persistence status: <status>
Matched: <contact ids>
Proposing: <count> contact adds, <count> loops, <count> threads, <count> briefs
Auto-applied: <list or "(none)">
Pending your confirm: <list>
```

Long-prose analysis is moved into the proposed IB and the daily brief's RI Events section.

For chat without RI: stay quiet. No "no RI found" pronouncement.

For artifacts (uploaded files, screenshots): always declare the scan result, even when negative ("RI scan: no relationship signals found in this screenshot — appears to be a system notification").

---

## Smoke tests — three canonical fixtures

`system/scripts/ri_smoke_test.py` runs these three end-to-end:

### Fixture 1 — PerfectHire transcript (T-2026-05-19-005)

Input: `source_type = fathom_manual_paste`, title `Todd <> PerfectHire - QSR Platform Review - May 19`, raw_text = canned excerpt.

Expected response:

- `persistence_status = "proposed_write_pending_confirmation"`
- matched: `olivia-nielsen`
- proposed contact adds: `max-holmes`, `matt-chalzi`
- proposed thread opens: 1 (`T-2026-05-perfecthire-qsr-platform-review`)
- proposed loops: ≥2 (Matt Chalzi intro followup, follow-up email)
- proposed brief writes: 1 (`2026-05-19-perfecthire-qsr-platform-review`)
- auto_applied: 1 (`touch:olivia-nielsen`)
- `event_at == "2026-05-19T*"` (NOT today's `captured_at`)
- dedupe.decision: `new_event` on first call, `duplicate_of_existing` on re-call with same raw_text

### Fixture 2 — Ryan Hildebrand interview completed (T-2026-05-19-006)

Input: `source_type = recruiting_update`, raw_text = "The interview with Ryan Hildebrand from Global Payments went well yesterday. He was at NRA and wants to have another conversation either this week or next."

Expected response:

- `persistence_status = "proposed_write_pending_confirmation"`
- matched: existing Ryan Hildebrand contact (if present in baseline)
- signal.type: `interview_completed` (primary) + `job_opportunity_movement`
- proposed thread updates: 1 (`T-2026-05-genius-global-payments` or successor — match against active_threads.yaml)
- proposed loops: ≥1 (second-conversation scheduling)
- auto_applied: `touch:ryan-hildebrand` (high-confidence match)
- `event_at == "2026-05-18"` (yesterday — per "the interview ... went well yesterday")

### Fixture 3 — Simin/Hari recruiter screen (T-2026-05-19-006)

Input: `source_type = recruiting_update`, raw_text = "I have not heard back from the headhunter I talked to on Friday. This was for a job with Hari - managing the McDonalds account - I would be very interested in it."

Expected response:

- `persistence_status = "proposed_write_pending_confirmation"`
- signal.type: `recruiter_screen_completed` (Friday) + `job_opportunity_movement`
- proposed contact adds: Simin Gorgulu (if not in baseline), Hari contact
- proposed thread opens: 1 (`T-2026-05-hari-mcdonalds-account-role` or similar)
- proposed loops: ≥1 (recruiter follow-up)
- `event_at == "2026-05-15"` (the Friday, computed from "Friday" + captured_at)

The smoke test should also assert the chat-scan negative path: input `source_type = manual_text`, raw_text = "what's on my plate today?" → response `ri_scan = "not_found"`, no JSONL write.

---

## Implementation build order (proposed next sessions)

Each row is a discrete coding session, one commit per row (per Todd's per-priority commit preference).

| Step | Deliverable | Files |
|---|---|---|
| 1 | RI event store: append, index, dedupe-key compute. Reads/writes `ri_events/*.jsonl` + `ri_events_index.json`. CLI smoke test for write→read→dedupe. Does NOT touch existing intake engine. | `ri_events.py`, `ri_events/README.md`, `.cache/ri_events_index.json` |
| 2 | `ri_intake.py` dispatch skeleton + pending-bundle cache. Wires source_type → pre-processor → existing `manual_relationship_intake.assess()` → `ri_events.append()`. Pre-processors stubbed (echo input through). Smoke-tests the full plumbing with synthetic input. | `ri_intake.py`, `.cache/ri_events_pending.json` |
| 3 | Recruiting + transcript pre-processors. Covers traces T-005 + T-006. Adds recruiting signal subtypes (`interview_completed`, `recruiter_screen_completed`, `resume_requested`, `resume_sent`, `followup_sent`) to `manual_relationship_intake._detect_signals` if not already present. | `ri_preproc_recruiting.py`, `ri_preproc_transcript.py`, possibly small additions to `manual_relationship_intake.py` |
| 4 | LinkedIn / email / manual pre-processors. Includes the quiet-chat RI screen gate. | `ri_preproc_linkedin.py`, `ri_preproc_email.py`, `ri_preproc_manual.py` |
| 5 | API endpoints: `POST /ri_events/review`, `GET /ri_events/recent`, `POST /ri_events/{id}/confirm`. Confirm endpoint reuses `manual_relationship_intake.apply_mutations` for the actual writes; the new code is just the event-sourcing wrapping. Update `openapi.yaml` (not `openapi_gpt.yaml`). | `server.py`, `openapi.yaml` |
| 6 | MCP tool `ri_intake_review` (local-only). Wraps the same `ri_intake.review()` function the HTTP endpoint calls. | `mcp/server.py` |
| 7 | Smoke tests: `ri_smoke_test.py` with PerfectHire / Ryan / Simin fixtures + chat-scan negative. | new |
| 8 | Daily brief integration: new "RI Events — Last 24 Hours" section in `daily_brief.py`. Reads from `ri_events_index.json`. | `daily_brief.py` |
| 9 | UX rules baked into `custom_gpt_prompt.md` once the API surface is stable AND we've agreed how to handle confirm-on-ChatGPT (likely deferred — see open questions). | `custom_gpt_prompt.md` |

---

## Open questions for Todd

1. **Step 9 — Custom GPT exposure.** The review endpoint can be safely surfaced to the GPT as read (review proposes, never writes). Confirm step needs the auth/UX story. Defer step 9 entirely this sprint, or expose just `POST /ri_events/review` in `openapi_gpt.yaml` (still under 30 ops) and keep `confirm` local-only?
2. **Auto-safe `touch` policy.** Proposal here is: auto-safe only when existing-contact match ≥ 0.9 AND `event_at_confidence == "high"` AND `event_at ≥ existing last_touch`. Acceptable, or tighter?
3. **Backfill timing (P-021 Phase 2).** Out of scope this sprint per the redirect, but want to confirm we will NOT backfill old Interaction Briefs into events until Phase 1 is stable.
4. **Brief slug collisions.** If two transcripts on the same date involve the same primary person (e.g., two Olivia meetings on 2026-05-19), the brief slug needs disambiguation. Proposal: `<date>-<person>-<short-topic-slug>`. OK?
5. **JSONL month boundary.** Proposed: one file per ISO month, file name `YYYY-MM.jsonl`. Acceptable, or per-quarter?
6. **`signal.type` taxonomy lock.** I listed 12 base types above. Anything missing for v1, or anything that should be combined?
7. **Recruiting opportunity thread naming.** Should each role become its own thread (`T-2026-05-genius-global-payments`), or is one per company/recruiter (`T-2026-05-simin-gorgulu-tritonexec`) the right grain? The existing active_threads.yaml has it per role.

---

## What this design does not change

- `openapi_gpt.yaml` stays at 23 operations. RI endpoints land in the full `openapi.yaml` only.
- `mutations.py` is unchanged. All RI persistence routes through the existing 11 subcommands; no new mutation primitives.
- The 7-section daily brief structure stays. One new section ("RI Events — Last 24 Hours") is added on top.
- `baseline_index.json`, `cards/`, `active_threads.yaml`, `loop_ledger.md`, `briefs/` schemas stay. RI events project *into* these via existing mutations.
- P-020 and P-021 are unchanged. This doc operationalizes them; it does not amend them.

---

## How to read this doc

If you want to skim: read the **Goals** section, the **File layout** section, the three smoke-test fixtures, and the **Open questions**. The rest is implementation detail you can hand to me when we start coding.

If you want to push back: the most opinionated calls are the JSONL-per-month partition, the dedupe-key formula, the auto-safe rule, the 12-type signal taxonomy, and the per-step build order. Those are the easiest to change before I write code.
