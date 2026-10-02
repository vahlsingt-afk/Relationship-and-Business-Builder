# RB-DEFECT-062: Life Lens Had No Write Path

**Status:** Fixed — pending independent (live) verification. Code-level fix, tests, and regression
coverage are complete (2026-07-03); per the Fixed → Verified → Closed discipline (RB-DEFECT-060),
this stays "Fixed" until Todd tells the live GPT a real personal-practice update and confirms
`system/personal_log.json` actually reflects it.
**Reported by:** Todd — told the live GPT "Mary and I prayed together and read the bible together
yesterday" and asked whether it was actually tracked, following up on the RB-DEFECT-061
investigation into GPT-fabricated receipts.

## Problem

RB replied to Todd's declaration with a confident, organized list of "mutations" it claimed to make
across "Relationship Intelligence," "Personal Intelligence," and "Daily Timeline" — e.g. "Faith
practice completed," "Prayer completed," "Bible reading completed," "Relationship with Mary:
Positive interaction." Verified the same way as RB-DEFECT-061: **none of it happened**, and unlike
RB-DEFECT-061, the underlying capability didn't even exist to fail silently — it was never built.

## Investigation

1. **No API call was made.** `request.log`'s most recent real activity at the time was a routine
   5am health check; nothing corresponds to this conversation. Same fabrication shape as
   RB-DEFECT-061 — the model narrated success with no backing tool call.
2. **Mary is not a valid target for `processRelationshipIntake`.** Searched `baseline_index.json`
   for "Mary" — only unrelated contacts came up (Mary Ann Dilling, Mary Catherine McDonald, Mary
   Mount, etc.), none of them Todd's wife. She exists only in the separate personal Life Lens system
   (`system/life_goals.yaml`), not the professional relationship graph. RB's proposed "Relationship
   Intelligence" mutation for her was conceptually confused, not just unrouted — `interaction_ledger.json`
   has zero Mary-related entries, confirming nothing was ever recorded there either.
3. **`system/personal_log.json` did not exist as a file.** This is the data store
   `render_daily_brief.py`'s Life Lens section reads (`_load_personal_log()` /`_goal_streak()`/
   `_goal_week_count()`) — confirmed live in the brief Todd pasted: `Morning Prayer: 0/7 — not
   logged yet ⚠️`, `Devotions: 0/5 — not logged yet ⚠️`, `Quality time with Mary: 0/7 — not logged
   yet ⚠️`, `Sunday Service: 0/4 — not logged yet ⚠️` — every manually-tracked (`data_source: manual`)
   goal, perpetually zero, in every brief since the feature was added.
4. **A write path did exist, but only for a human at a terminal.** `system/scripts/personal_log.py`
   was already present (dated 2026-06-28) — but it's a fully interactive CLI (`_prompt_yn()` /
   `input()` per goal) meant to be run by a person answering prompts in a live terminal session, with
   no non-interactive or GPT-callable path at all. It had never been run even once, which is why the
   file it writes to had never been created.

## Root cause

Not a routing gap (RB-DEFECT-061's shape — tool exists, undocumented). The write capability itself
was never built for the conversational path. `personal_log.py` solved the "human logs their own day
in a terminal" case; nothing solved "the GPT hears about it in conversation and records it."

## Fix — reuse the RB-DEFECT-060 pattern, no new GPT operation

The 30-op Custom GPT cap is still full. Exactly like EOLMS loop closure, this routes through the
**already-GPT-facing** `ingestExecutiveDeclaration` via one new declaration event type.

- **`system/life_goals.yaml`** — added a `log_keywords: [...]` list to each of the 4 manually-logged
  goals (`morning_prayer`, `devotions`, `mary_connection`, `sunday_service`), mirroring the
  `calendar_keywords` field `date_night` already had. `exercise` (Strava) and `date_night` (calendar)
  were left untouched — they already have automated data sources and shouldn't be manually loggable.
- **`system/scripts/personal_log.py`** — extended, not replaced. Added `record_personal_practice(text,
  event_at=None, *, apply=False)`: multi-label match (a single sentence can complete more than one
  goal at once — "prayed and read the bible" completes both `morning_prayer` and `devotions`; no
  confidence gate needed the way EOLMS needs one, since there's no identity ambiguity between
  goals), `_resolve_date()` ("yesterday" in the text wins over `event_at`, since the GPT isn't
  instructed to compute relative dates itself), snapshot-before-write. New `--record TEXT
  [--event-at DATE] [--confirm]` flags added to the existing flat CLI (dry-run by default) —
  the original interactive `log_day()`/`show_log()`/`--show` behavior is unchanged.
- **`system/scripts/intelligence_triage.py`** — new `_EXEC_DECL_PATTERNS` entry: event_type
  `personal_practice_logged`, mutation_target `personal_log`. Deliberately loose (prayer/devotions/
  bible/scripture/church/"with Mary" language) — same reasoning as `state_resolved`: precision lives
  downstream in `record_personal_practice()`'s actual keyword match against `life_goals.yaml`, so an
  over-broad trigger here just resolves to `no_match`, never a bad write.
- **`system/api/server.py`** — `import personal_log`; new `elif event_type ==
  "personal_practice_logged":` branch in `_execute_executive_declaration()` calling
  `personal_log.record_personal_practice(declaration_text, event_at, apply=True)`, surfaced as a
  `personal_log_update` mutation entry (same shape as RB-DEFECT-060's `eolms_loop_transition`).
  Deliberately *not* routed through the interaction-ledger logging used by other event types — this
  isn't a relationship interaction.
- **GPT instructions** — extended the existing CEO-declaration trigger line in
  `custom_gpt_instructions_compact_8k.md` (not a new line) to include personal/spiritual-practice
  language, and require the receipt name which Life Lens goal(s) were logged, not just "noted."
  Mirrored in the superseded/reference `custom_gpt_instructions_8k.md`.

## Tests

New `system/tests/test_personal_log_mutation.py` (9 tests, fully isolated — no test reads or writes
the real `system/personal_log.json` or `system/life_goals.yaml`):
- Classifier recognizes prayer/bible and church language; unrelated text doesn't match.
- Multi-label detection: one sentence correctly logs all three mentioned goals, and explicitly does
  *not* touch the automated `exercise`/`date_night` goals.
- Single-goal match, no-match, dry-run-doesn't-persist, and an existing day's entry is merged into
  (not overwritten by) a new match.
- One integration test hitting `POST /ingest/executive_declaration` via `TestClient(server.app)`
  end-to-end with Todd's exact sentence, asserting a `personal_log_update` mutation with `status:
  applied` and the right three goals.

## Verification

```
python3 personal_log.py --record "Mary and I prayed together and read the bible together yesterday"
  # dry-run: morning_prayer, devotions, mary_connection for yesterday's date; exercise/date_night untouched
python3 -m pytest system/tests/test_personal_log_mutation.py -v    # 9 passed
python3 system/scripts/validate_openapi_gpt.py                     # OK (30 ops) — no new operation added, unaffected
python3 -m pytest <20-file regression subset> -q                   # 467 passed, zero regressions
```

No real `personal_log.json` was created by any of the above — every manual CLI check was a dry run,
and the automated tests are fully path-isolated.

## Live Verification Checklist — run before marking this Closed

1. Say to the live GPT something true and current, e.g. *"I prayed this morning"* or *"Mary and I
   had a great time together tonight."*
2. Confirm the receipt names the specific goal(s) logged and the date — not just "noted."
3. Independently check: `cat system/personal_log.json` (or `git diff` if it already existed) — the
   relevant goal should be `true` for the right date.
4. Re-render today's brief (`python3 render_daily_brief.py --date <today> --dry-run --force`) and
   confirm the Life Lens section shows a non-zero count for that goal instead of "not logged yet ⚠️."
5. Any mismatch between the receipt and the file — reopen, don't mark Verified.

## Files touched

- `system/life_goals.yaml`
- `system/scripts/personal_log.py`
- `system/scripts/intelligence_triage.py`
- `system/api/server.py`
- `system/api/custom_gpt_instructions_compact_8k.md`, `custom_gpt_instructions_8k.md`
- `system/tests/test_personal_log_mutation.py` (new)
