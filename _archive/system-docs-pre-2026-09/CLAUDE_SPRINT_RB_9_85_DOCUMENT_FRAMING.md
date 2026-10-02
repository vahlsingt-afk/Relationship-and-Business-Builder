# RB 9.85: Document 1 / Document 2 terminology pass + Section 5 consistency fix

**Status:** Implemented (2026-06-15)
**Source:** Continuation of the RB-DEFECT-044 multi-sprint track
(`system/CLAUDE_DEFECT_RB_044_THREE_LAYER_BRIEFING_ARCHITECTURE.md`), after
RB 9.84 closed out RB-DEFECT-044's canonical Daily Brief section list.

## Scope

RB-DEFECT-044 frames the briefing system as a two-*document* architecture:
**Document 1 = Intelligence Brief** ("what changed?") and **Document 2 =
Daily Brief** ("so what?" — CoS synthesis). All 6 Custom GPT KB files still
described only the RB 9.69 "Part 1/Part 2 two-call" model.

Per STATUS.md's open question, this sprint determined that **Part 1 ≈
Document 1 and Part 2 ≈ Document 2 map cleanly** — `getDailyBrief` /
`getDailyBriefPart2` are live Action operation names fixed by
`openapi_gpt.yaml` and cannot be renamed without an Actions change (out of
scope). So this sprint is a **terminology/labeling pass**, not a rewrite:
"Document 1"/"Document 2" are added as the conceptual framing labels from
RB-DEFECT-044, used *alongside* the unchanged "Part 1"/"Part 2" API-call
terminology — Part 1/Part 2 remain the "how to call it" layer.

## Incidental fix: Section 5 omission

While scoping the reframe, found that the "Two-Call Delivery" summary
tables/flow descriptions in several KB files still said Part 2 contains only
"Section 3 + Section 4" — a stale reference predating RB 9.82's addition of
Section 5 (Horizon Watch) to Part 2. This is inconsistent with the Part 2
pass/fail criteria in `DAILY_BRIEF_CANONICAL_TEMPLATE.md`, which already
require Section 5. Fixed alongside the Document 1/2 pass since both touch the
same summary tables/flow text.

## What was implemented

All edits are documentation-only (no `daily_brief.py` / `server.py` changes
— this sprint adds no new section, just terminology + consistency fixes).

- **`DAILY_BRIEF_CANONICAL_TEMPLATE.md`**:
  - Title and "## Two-Call Delivery" header updated to mention RB 9.85
    Document 1/Document 2 framing.
  - New intro paragraph mapping Document 1 (Intelligence Brief) = Part 1,
    Document 2 (Daily Brief) = Part 2, clarifying Part 1/Part 2 stay fixed as
    API operation names.
  - Two-Call table gained a "Document (RB-DEFECT-044)" column; Part 2 row
    fixed to include Section 5 (Horizon Watch).
  - Flow steps 3-4 updated: Part 2 offer now mentions the horizon watch;
    render order now "Section 3 → Section 4 → Section 5"; pass/fail intro
    updated "Sections 3–4" → "Sections 3–5".
  - Pass/fail section headers: "### Part 1 (`getDailyBrief`)" → "... —
    Document 1: Intelligence Brief"; "### Part 2 (`getDailyBriefPart2`)" →
    "... — Document 2: Daily Brief".
- **`CANONICAL_RESPONSE_CONTRACT.md`**: RB 9.69 Two-Part Delivery section
  gained the same Document 1/Document 2 mapping note and table column;
  Part 2 row fixed to include Section 5; "render Sections 3–4" → "Sections
  3–5".
- **`custom_gpt_instructions_8k.md`**: "## Daily Brief" header and rendering
  spec line updated with Document 1/Document 2 framing paragraph and Section
  5 mention; two-call flow step 3 fixed "Section 3 → Section 4" → "Section 3
  → Section 4 → Section 5".
- **`custom_gpt_prompt.md`**: "## 6. Daily Brief Rendering" header updated;
  authoritative rendering spec line gained Section 5 mention plus a new
  Document 1/Document 2 framing paragraph; two-call flow step 3 fixed to
  "Section 3 → Section 4 → Section 5".
- **`custom_gpt_instructions_compact_8k.md`** (now 7,956/8,000 chars):
  "Two-part delivery" bullet updated with "(RB 9.85 Document 1/2 framing)",
  "Part 1 / Document 1", "Document 2:" prefix on the Part 2 offer summary
  (now also mentions Horizon Watch), and "Sections 3–5" (was "Section 3 →
  Section 4 only").
- **`custom_gpt_operational_playbook.md`**: "RB 9.69 — delivered in two
  calls" paragraph fixed to "Section 3 + Section 4 + Section 5" and gained a
  Document 1/Document 2 framing sentence.

## Tests

No code changes — full suite re-run to confirm zero regressions:
`python3 -m pytest system/tests/ -q` — **2391 passed** (121.16s), unchanged
from RB 9.84's baseline.

## Deferred

- Live GPT re-sync (5 KB files + compact_8k Instructions) — carried from RB
  9.81/9.82/9.83/9.84, now also covers RB 9.85's Document 1/Document 2 +
  Section 5 wording changes.
- RB 9.73b (account mapping + title_history/company_history schema) —
  deferred.
- RB 9.70 Tier 2/3 leftovers (watchlist rollup ordering, loop owner field,
  section reordering, RB-DEFECT-041 LinkedIn triage) — deferred.
- With this sprint, the RB-DEFECT-044 KB reframe item from STATUS.md's "Next
  up" is closed: the KB now consistently presents both the Document
  1/Document 2 conceptual framing and the Part 1/Part 2 API-call mechanics,
  and the Section 5 omission found along the way is fixed everywhere it
  appeared.
