# RB 9.87: title_history/company_history (RB-DEFECT-041 Enhancement #4) + ingest_from_csv_text defect fix

**Status:** Implemented (2026-06-15)
**Source:** `CLAUDE_DEFECT_RB_041_LINKEDIN_INTELLIGENCE_INGESTION_AND_RELATIONSHIP_ACTION_ENGINE.md`,
Enhancement #4, the second and final RB 9.73b item (the first, Enhancement #3
strategic account mapping, was closed in RB 9.86).

## Scope

RB 9.73 found Enhancement #4 "partially built": each LinkedIn ingest already
appends a dated note (`_append_note`) and a `linkedin_delta_<date>` tag when a
contact's company or role changes, which is a de facto history log — but there
was no structured `title_history`/`company_history` array per contact for
programmatic consumption (e.g. "how many companies has this person been at in
the last 2 years?"). RB 9.73 deferred this because it "would require a
`baseline.schema.json` change plus a migration."

## What was implemented

### Structured history arrays

- **`system/schemas/baseline.schema.json`**: added optional `company_history`
  and `title_history` array properties. Each item is
  `{"company"|"role": <prior value, string or null>, "changed_on": "<ISO
  date>"}` — the value that was active *until* `changed_on`, when
  `linkedin_ingest.py` detected a different value in a new export. Most recent
  change is appended last.
- **`linkedin_ingest.py`**: when a company change is detected (and not held as
  a conflict against an operator-confirmed entry), the prior `current_company`
  is appended to `company_history` before `current_company` is overwritten.
  Same pattern for `current_role` → `title_history`. New contacts get
  `company_history: []` / `title_history: []` initialized.
- **Migration**: lazy, via `entry.setdefault("company_history", [])` /
  `entry.setdefault("title_history", [])` — matches the existing pattern for
  `tags`/`circles` on older baseline entries that predate those fields. No bulk
  rewrite of the 2,748-entry `baseline_index.json` needed; entries gain the
  arrays the first time they're touched by an ingest that changes
  company/role.

### Incidental defect fix: `ingest_from_csv_text()` was completely broken

While implementing the above, found two independent bugs in
`ingest_from_csv_text()` (the GPT-upload CSV-text bridge from DEFECT-024,
exposed via `system/api/server.py`'s LinkedIn ingest-csv-text endpoint):

1. **`NameError: _update_entry`** — the matched-existing-contact branch called
   `_update_entry(entry, row, source_tag, d, delta)`, a function that was never
   defined anywhere in the module. Any CSV-text ingest containing a row that
   matched an existing baseline contact would crash.
2. **`KeyError` in `_render_markdown()`** — `ingest_from_csv_text()`'s
   hand-built `report` dict was missing `activity_counts`, `rc_moves`,
   `lki_moves`, and `open_questions`, all of which `_render_markdown()`
   accesses unconditionally (`rep["rc_moves"]`, etc.) while building
   `summary_markdown`. This crashed on *every* call, regardless of whether any
   row matched an existing contact.

Combined, `ingest_from_csv_text()` could not complete successfully for any
input — the GPT-upload ingestion path was dead code in practice.

**Fix:** extracted the matched-entry merge logic out of `ingest()` (company/role
change detection + conflicts + rc/lki move construction + reconnection
handling + the new history-array writes) into a shared `_update_entry()`
function, called by both `ingest()` and `ingest_from_csv_text()`. Added the
missing `activity_counts` (empty dict — CSV-text ingests have no
`activity.csv`/etc. to count), `rc_moves`, `lki_moves`, and `open_questions`
keys to `ingest_from_csv_text()`'s report dict.

## Tests

Added to `system/tests/test_linkedin_ingest_delta_intelligence.py`:

- `test_ingest_records_company_and_title_history_on_change` — `ingest()` with a
  baseline contact whose company/role change; asserts
  `lki_moves[0]["entry_snapshot"]["company_history"]` and `["title_history"]`
  contain the prior values with `changed_on` set to the ingest date.
- `test_ingest_from_csv_text_matches_existing_entry_without_crashing` —
  `ingest_from_csv_text()` against a CSV row matching an existing baseline
  contact with a company/role change; asserts it completes without raising,
  `headline_counts` reflect the match/changes, and `company_history` is
  recorded on the matched entry.

Full suite: `python3 -m pytest system/tests/ -q` — **2396 passed** (128.14s), up
from RB 9.86's 2394 (2 new tests, zero regressions).

## Deferred

- `ingest_from_csv_text()`'s `delta_intelligence` block is still a hand-rolled
  subset of `ingest()`'s `_build_delta_intelligence()` output — no
  `segment_deltas`, `career_activation`, `strategic_relationship_changes`
  (including RB 9.86's `account_map_signals`), or `trust_statistics`. This
  sprint's goal was making the GPT-upload path *not crash*; bringing it to full
  intelligence parity with the file-based `ingest()` path is separate,
  larger scope.
- With this sprint, **RB 9.73b is fully closed** — both Enhancement #3
  (strategic account mapping, RB 9.86) and Enhancement #4 (structured
  history arrays, this sprint) are implemented.
- RB 9.70 Tier 2/3 leftovers and the live GPT re-sync (carried from RB
  9.81-9.85) remain the open items in "Next up".
