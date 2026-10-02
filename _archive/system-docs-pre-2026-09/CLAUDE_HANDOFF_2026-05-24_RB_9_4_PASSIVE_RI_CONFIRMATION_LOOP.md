# Claude Handoff — RB 9.5 Passive RI Confirmation Loop

**Date:** 2026-05-24  
**Status:** next sprint design handoff  
**Owner:** Claude architecture / Codex implementation loop  
**Primary objective:** close the loop from passive RI *proposed* → passive RI *confirmed + projected*.

## What This Sprint Built (RB 9.4 — complete)

`passive_ri_ingest.py` is now wired into the morning pipeline. It:

- collects high/medium signals from `relationship_signals.build_report()` and `linkedin_messaging.linkedin_message_overlay()`;
- normalizes to RI event payloads with `source_type=passive_signal`;
- appends durable events through `ri_events.append()` with dedup;
- writes `system/.cache/passive_ri_ingest.json`;
- blocks low-confidence, stale-source, unmatched-entity, and bad-date candidates;
- surfaces proposed/blocked/duplicate counts in the daily brief.

Smoke tests: all green (ri_events 15/15, passive_ri_ingest 13/13, daily_brief 60+/60+, refresh_all 15/15 scripts OK).

## Why This Sprint

Passive events are now *proposed* — `persistence.status = proposed_write_pending_confirmation`. They live in the event store but **projections have not changed**. The daily brief accurately says "RB proposed N events" but cannot yet say "RB updated last_touch for Olivia Nielsen."

The trust gap is the confirm path. Until confirmation is wired:

- `baseline_index.json` `last_touch` fields don't move from passive signals;
- loop candidates from passive events don't auto-open;
- the brief says "proposed" but nothing downstream acts on it.

## Product Doctrine (unchanged from RB 9.4)

```text
passive signal → RI event → confirmed → projection write → daily brief proof
```

Allowed user-facing language after this sprint:

- "RB recorded..." — durable event written AND confirmed
- "RB updated last_touch for X from Y to Z." — projection applied
- "RB proposed..." — event written, confirmation pending
- "RB blocked..." — could not write, with reason

Still prohibited:

- "should be marked"
- "could be updated"
- "would likely"

## Current State To Reuse

Read these first:

- `system/scripts/passive_ri_ingest.py` — the ingest layer this sprint built
- `system/scripts/ri_intake.py` — `confirm()` function is already implemented
- `system/scripts/ri_events.py` — event store; load_events(), find_by_event_id()
- `system/scripts/mutations.py` — `cmd_touch_contact()` is the projection write
- `system/api/server.py` — `/ri_events/{event_id}/confirm` endpoint exists

Key existing capabilities to reuse:

- `ri_intake.confirm(event_id, accept=[], reject=[], dry_run=False)` — already implements the confirm/reject lifecycle, applies mutations via `manual_relationship_intake.apply_mutations()`, writes a follow-up correction event.
- `mutations.py cmd_touch_contact()` — the safe path for `last_touch` writes.
- `passive_ri_ingest.load_cache()` — reads the current proposed-events summary.
- `ri_events.load_events(persistence_status='proposed_write_pending_confirmation')` — find all pending passive proposals.

## Priority 1 — Passive RI Confirm CLI

Add a confirm sub-command to `passive_ri_ingest.py`:

```bash
python3 system/scripts/passive_ri_ingest.py confirm --all      # confirm all pending passive events
python3 system/scripts/passive_ri_ingest.py confirm --event-id ri_2026-05-23_olivia-nielsen_001
python3 system/scripts/passive_ri_ingest.py confirm --dry-run  # show what would change
```

Behavior:

1. Load all passive RI events with `persistence.status = proposed_write_pending_confirmation` AND `source.type = passive_signal`.
2. For each: check if it has a pending bundle in `ri_events_pending.json`.
   - If yes: call `ri_intake.confirm(event_id, dry_run=dry_run)` to apply mutations.
   - If no pending bundle: the passive event has no bundle (passive_ri_ingest writes directly via ri_events.append, not via ri_intake.review — so there IS no mutation bundle). In this case, apply the projection directly: call `cmd_touch_contact` for `last_touch` update if `signal.type` is `last_touch_update` or `linkedin_inbound_contact`.
3. Write a confirmation summary to `system/.cache/passive_ri_confirm.json`.
4. `--dry-run` must not write to canonical state (per smoke-test-mutations memory).

Acceptance:

```bash
python3 system/scripts/passive_ri_ingest.py --smoke            # ingest smoke still passes
python3 system/scripts/passive_ri_ingest.py confirm --dry-run  # dry run shows proposed changes
python3 system/scripts/daily_brief.py --smoke                  # brief smoke still passes
```

Expected after confirm:

- confirmed events show `persistence.status = persisted` in the event store;
- `baseline_index.json` `last_touch` fields updated for confirmed contacts;
- daily brief says "RB updated last_touch for X." not "RB proposed...".

## Priority 2 — Direct Projection for Passive last_touch Signals

The passive ingest events for `last_touch_update` and `linkedin_inbound_contact` signal types are straightforward enough for auto-projection (high confidence, matched contact, dated interaction). These do NOT need full ri_intake.review() mutation bundle review.

Extend `passive_ri_ingest.py` run() to apply `last_touch` projections directly for:

- `signal.type = last_touch_update` AND `event_at_confidence = high` AND `contact_id` matched AND contact is RC/LKI
- Projection path: `mutations.cmd_touch_contact(contact_id, date=event_at, source='linkedin_messaging')`
- Record in the cache summary as `action_state = updated` (not `proposed`)
- Write a follow-up RI event with `persistence.status = persisted`

This turns "RB proposed N events" into "RB updated last_touch for X, Y. RB proposed N additional events." for the daily brief.

Acceptance:

- Confirmed `last_touch` updates are reflected in `baseline_index.json`.
- Daily brief `_load_recent_ri_events` picks them up as `newly_captured` (persisted status).
- Smoke test verifies the write happened (check baseline, not just the event store).

## Priority 3 — Wire confirm into refresh_all.py

After running passive_ri_ingest in `refresh_all.py`, auto-confirm the high-confidence, matchable subset:

```text
passive_ri_ingest.py              # propose events
passive_ri_ingest.py confirm --high-confidence-only  # auto-apply projection-safe events
daily_brief.py                    # reads updated state
```

"High-confidence-only" means:

- `event_at_confidence in {high}`
- `contact_id` is a matched existing baseline contact
- `signal_type in {last_touch_update, linkedin_inbound_contact}`
- source is `fresh` (not stale_source_limited)

Acceptance:

```bash
python3 system/scripts/refresh_all.py --date 2026-05-24
python3 system/scripts/daily_brief.py --smoke
```

Expected: brief RI mutation section includes at least one `updated` item when qualifying candidates exist.

## Priority 4 — Monitored Transcript-Folder Ingest (stub if fast)

`source_watch.py` inventories Zoom/Fathom watch folders but doesn't parse them.

If the confirm loop above completes with time to spare, wire a minimal transcript-ingest path:

```python
# In passive_ri_ingest.py or a new passive_ri_transcripts.py
for artifact in source_watch.iter_unprocessed_artifacts():
    resp = ri_intake.review({
        "source_type": artifact.source_type,  # fathom_manual_paste or zoom_manual_paste
        "raw_text": artifact.text,
        "captured_at": artifact.captured_at,
        "source_ref": {"path": str(artifact.path), "title": artifact.title},
    })
    # track in passive_ri_ingest cache
```

Only do this if the confirm loop is green first. The non-goal from RB 9.4 holds: do not build the full monitored-folder pipeline yet.

## Non-Goals (same as RB 9.4)

- Do not replace `baseline_index.json` / cards / loops with event-derived projections.
- Do not build a new event store.
- Do not auto-create RCs.
- Do not silently mutate ambiguous relationship state.
- Do not add broad product UI.
- Do not treat stale source silence as a real quiet signal.

## Required Smoke Suite Before Green

```bash
python3 system/scripts/ri_events.py --smoke
python3 system/scripts/ri_smoke_test.py              # needs fastapi — run on host
python3 system/scripts/passive_ri_ingest.py --smoke
python3 system/scripts/daily_brief.py --smoke
python3 system/scripts/refresh_all.py --date 2026-05-24
python3 system/scripts/api_smoke_test.py             # needs fastapi — run on host
python3 system/scripts/morning_path_test.py
```

Expected green:

- RI event store smoke passes.
- Passive ingest smoke passes (ingest + confirm dry-run).
- Daily brief trust-first regression passes.
- Refresh pipeline: passive_ri_ingest cache written AND confirmed events show persisted.
- Morning path: 3/4 automated (same as pre-sprint; fastapi sandbox limitation).

## Suggested Sprint Sequence

1. Read `ri_intake.confirm()`, `mutations.cmd_touch_contact()`, and `passive_ri_ingest.run()`.
2. Implement `passive_ri_ingest confirm` sub-command with `--dry-run` and isolated smoke.
3. Add direct last_touch projection for `last_touch_update` signals in `run()`.
4. Wire `--high-confidence-only` auto-confirm into `refresh_all.py`.
5. Verify daily brief shows `updated` (not just `proposed`) for projected contacts.
6. Run full smoke suite.
7. Update `STATUS.md`, `CLAUDE_DEVELOPMENT_MAP.md`, and handoff.

## Success Definition

The sprint is green when RB can say:

```text
RB reviewed passive relationship signals.
RB updated last_touch for N contact(s): [names].
RB proposed M additional review-first event(s).
RB skipped D duplicate event(s).
RB blocked B candidate(s).
The daily brief shows those states with named contacts before any interpretation.
```

That is the next step toward RB feeling like a real Chief of Staff: not just noticing, not just advising, but remembering, persisting, and proving what it actually changed.
