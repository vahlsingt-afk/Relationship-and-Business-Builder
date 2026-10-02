# `system/ri_events/` — Relationship Intelligence event stream

**Status:** Phase 1 (capture). Phase 2 backfill and Phase 3 projection-checks are deferred. See `system/protocols/P-021_ri_event_sourcing.md`.

## Contract

This directory holds the canonical, append-only RI event stream. One JSONL file per ISO month, named `YYYY-MM.jsonl`. Every line is one immutable RI event matching the shape in `system/SCHEMAS.md` → "Relationship Intelligence Event."

The partitioning anchor is **`event_at`**, not `captured_at`. An event whose relationship moment happened on 2026-04-12 lives in `2026-04.jsonl` even if it was captured (uploaded, pasted, transcribed) on 2026-05-19. This preserves the date-of-intelligence rule from P-020 and P-021: when the relationship event happened drives the timeline, not when RB learned about it.

## Append-only

Events are never edited. If a captured event needs to be revised, write a new event with `dedupe.decision = "correction_event"` referencing the prior `event_id` in `dedupe.duplicates`. The reader assembles current relationship state by replaying the stream.

Re-runs of the same input do not write a new event. The append engine (`system/scripts/ri_events.py`) computes `dedupe.dedupe_key` from the event payload, checks `system/.cache/ri_events_index.json` for an existing match, and — on hit — returns the existing `event_id` without writing. The JSONL stays clean; the prior event is the canonical record.

## What is in scope

- Manual/pasted RI intake (`POST /ri_events/review`) writes here.
- The intake confirmation step (`POST /ri_events/{id}/confirm`) writes a follow-up event recording `persistence.status = "persisted"` or `"rejected_by_operator"`.
- Future: classified inbound feeds (email, calendar, messages, calls, social) when P-021 Phase 1 extends to them.

## What is not in scope (yet)

- Phase 2 backfill: existing Interaction Briefs (`system/briefs/`) are not yet replayed into events. When that lands, backfilled events carry `source.type = "interaction_brief_backfill"` and the original brief path in `source.path`.
- Phase 3 projection-check: comparing the projected `last_touch` / signal class / loops / active threads derived from the event stream against the current canonical files. Deferred until Phase 1 is stable.
- Phase 4 projection-as-primary: letting the event stream become the preferred computation source. Way out.

## Reading the stream

Use `python3 system/scripts/ri_events.py` rather than reading JSONL directly:

```bash
# Show recent events (defaults to last 24h by captured_at)
python3 system/scripts/ri_events.py recent

# Lookup by id
python3 system/scripts/ri_events.py get ri_2026-05-19_perfecthire-qsr-platform-review_001

# Lookup by dedupe key
python3 system/scripts/ri_events.py dedupe 'fathom_manual_paste:sha256:abc...:2026-05-19T10:00:00-05:00'

# Rebuild the index from scratch (after a manual edit or recovery)
python3 system/scripts/ri_events.py reindex

# Smoke test (write → read → dedupe → reindex)
python3 system/scripts/ri_events.py --smoke
```

## Git

The JSONL files **are committed** — they are canonical state. Snapshots and the index (`system/.cache/ri_events_index.json`) are not committed (the cache is git-ignored; snapshots use the existing `system/_snapshots/` discipline).

Raw artifact text (transcripts, screenshots, pasted email bodies) is **not** committed. The event preserves only a `source.raw_text_hash` and structured fields. Raw payload stays in `system/inbox/conversation_artifacts/` (git-ignored) per P-020 §"Source folders."
