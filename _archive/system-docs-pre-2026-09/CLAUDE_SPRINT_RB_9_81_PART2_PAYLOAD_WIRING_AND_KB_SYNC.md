# RB 9.81: Part 2 Payload Wiring + KB Sync for RB 9.70/9.77-9.79 Sections

**Status:** Implemented (2026-06-14)
**Source:** KB review session — auditing `system/api/` Custom GPT knowledge
files against the current `daily_brief.py` section set, in light of
RB-DEFECT-044's two-document framing.

## Scope

An Explore-agent audit found two compounding gaps for four sections added
across RB 9.70/9.77-9.79:

- `pending_mutations` (RB 9.70)
- `upcoming_preparation_requirements` (RB 9.77)
- `strategic_risks` (RB 9.78)
- `this_week_priorities` (RB 9.79)

1. **Wiring gap (critical):** all four are computed by `daily_brief.py` and
   listed in `brief_display_order`, but **none were in
   `_ACTION_BRIEF_SECTIONS_PART1`/`_PART2` in `server.py`** — the Custom GPT
   never received them in either `getDailyBrief` or `getDailyBriefPart2`.
   Four sprints of work was invisible to the live GPT.
2. **Docs gap:** none of the 6 KB files (`CANONICAL_RESPONSE_CONTRACT.md`,
   `custom_gpt_instructions_compact_8k.md`, `custom_gpt_instructions_8k.md`,
   `custom_gpt_prompt.md`, `custom_gpt_operational_playbook.md`,
   `DAILY_BRIEF_CANONICAL_TEMPLATE.md`) mentioned any of the four sections.

A separate, larger framing gap was also identified — all KB files describe
a "Part 1/Part 2 two-call" model (RB 9.69), while RB-DEFECT-044 (filed
2026-06-14) reframes the brief as two *documents* (Intelligence Brief /
Daily Brief). That reframe is scoped as a future sprint (RB 9.82+), not
addressed here.

## What was implemented

### `system/api/server.py`

- Added `this_week_priorities`, `upcoming_preparation_requirements`,
  `decision_layer`-adjacent `strategic_risks`, and `pending_mutations` to
  `_ACTION_BRIEF_SECTIONS_PART2`, positioned alongside the existing My
  Priorities sub-sections (`this_week_priorities`/`upcoming_preparation_requirements`
  near `cos_today`/`decision_layer`; `strategic_risks` after `decision_layer`;
  `pending_mutations` after `pending_graph_mutations`).
- Added `_SECTION_EXTRAS_KEYS` entries for all four (e.g.
  `pending_mutations`: `pending_count`, `pending_interaction_ids`,
  `pending_contacts`, `oldest_created_at`; `upcoming_preparation_requirements`:
  `horizon`, `prep_minutes`, `required_inputs`, `event_start`, `event_id`,
  `loop_id`; etc.) so their `extras` survive compaction.
- Added a compaction-limit branch in `_compact_canonical_brief`: limit 5 in
  Part 2 / 3 in Part 1 for all four (consistent with other "execution
  sections" like `loops_and_obligations`).

### KB files (`system/api/`)

- **`DAILY_BRIEF_CANONICAL_TEMPLATE.md`**: Section 4 ("My Priorities") grew
  from 6 to **10 sub-sections** — added THIS WEEK, PREP REQUIRED, RISKS,
  PENDING CONFIRMATIONS (with example content, source mapping, and the
  "No ... detected" green-board fallback rule). Updated both Part 2
  pass/fail criteria ("6 sub-sections" → "10 sub-sections", with the full
  list spelled out).
- **`CANONICAL_RESPONSE_CONTRACT.md`**: added an RB 9.81 note documenting the
  wiring fix and a new banned pattern — omitting any of the four new Section
  4 sub-sections when present in the Part 2 payload.
- **`custom_gpt_instructions_compact_8k.md`**: updated the two-part-delivery
  bullet and added a new bullet enumerating Section 4's 10 sub-sections
  (now 7,393/8,000 chars — within budget).
- **`custom_gpt_instructions_8k.md`**: added a Section-4 sub-section
  enumeration note to the two-call flow.
- **`custom_gpt_prompt.md`**: expanded the "Section 4 (Part 2), six
  sub-sections" quick-reference to ten, with source mapping and the
  "No ... detected" fallback rule for the four new sub-sections.
- `custom_gpt_operational_playbook.md` — reviewed, no change needed (its
  Section 4 reference is generic and already points to TEMPLATE.md).

## Deferred

- **RB-DEFECT-044 Document 1/Document 2 reframe** of all KB files
  (currently Part 1/Part 2 two-call framing) — larger, separate sprint
  (RB 9.82+).
- **Live GPT re-sync**: all 5 Knowledge files + the compact_8k Instructions
  field need to be re-uploaded/re-pasted to the live Custom GPT, and
  `openapi_gpt.yaml`/Actions schema reviewed (no schema change — existing
  `getDailyBriefPart2` payload now includes 4 more sections, same endpoint).

## Tests

Full suite: `python3 -m pytest system/tests/ -q` — 2380 passed (127.70s). No
new tests added — this is a payload-wiring + docs change to already-tested
section-computation functions; no new code paths.
