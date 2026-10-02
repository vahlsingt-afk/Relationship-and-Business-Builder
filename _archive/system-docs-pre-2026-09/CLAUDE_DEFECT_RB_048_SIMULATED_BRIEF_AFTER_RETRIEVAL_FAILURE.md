# RB-DEFECT-048: System Generated a Simulated Daily Brief Instead of Executing the Daily Brief Process

**Date filed:** 2026-06-15
**Filed by:** Todd
**Status:** Filed — triaged and partially fixed 2026-06-15; live GPT re-sync
completed 2026-06-15, awaiting live re-validation (see below).
**Priority:** Critical
**Category:** Intelligence Engine / Daily Brief Generation

## Issue Summary (verbatim)

After correctly identifying that the canonical Relationship Bridge Daily Brief
could not be generated because the required intelligence-gathering process
had not been executed, the system immediately proceeded to generate a
simulated Daily Brief anyway.

This creates a trust problem. The system acknowledged that it lacked access to
the required intelligence sources and then generated a report that appeared
authoritative despite not being based on the required intelligence gathering
process. The result is a briefing that feels plausible but cannot be trusted.

## Expected Behavior

When the user requests "Give me the daily brief", the system should first
determine whether the required intelligence process has been completed
(calendar, email, SMS, call activity, relationship, LinkedIn, watchlist scans,
market scans, KB updates, overnight signal processing). If unavailable, the
system should explicitly state: "I cannot generate the canonical Daily Brief
because the required intelligence gathering process has not been executed."
The system should never substitute assumptions for intelligence.

## Actual Behavior

The system correctly stated: "I cannot currently produce the canonical
Relationship Bridge Daily Brief." It then proceeded to generate an Executive
Summary, Relationship Intelligence, Career Intelligence, Market Intelligence,
CoS Assessment, and Priorities anyway — a narrative assembled from remembered
conversation context, without performing the intelligence-gathering workflow.

## Root Cause (Todd's framing)

The system is confusing **Context Recall** with **Intelligence Collection**.
Remembering that Foods Connected / Global Payments are active opportunities,
or remembering prior discussions, is not the same as confirming new
intelligence about them today or detecting change. The Daily Brief's value is
change detection — "What changed?" — not "What do we know?"

## Required System Behavior (Todd's spec)

1. Execute intelligence gathering.
2. Analyze collected intelligence.
3. Enrich intelligence.
4. Update knowledge base.
5. Detect changes.
6. Generate CoS assessment.
7. Generate actions.

Only then should the Daily Brief be created.

## Missing Confidence / Provenance Indicators (Todd's spec)

The brief should label content as:
- **Verified Today** — data gathered during the current intelligence cycle.
- **Historical Context** — previously known information relevant to
  interpretation.
- **Inference** — analytical conclusions/recommendations derived from
  intelligence.

Current outputs blend all three without labeling them.

## Additional Defect

The generated report recapped substantial amounts of already-known
information (Foods Connected status, Global Payments status, Toast platform
discussion, existing strategic themes) as if it were new intelligence. The
brief should prioritize novelty/change detection over recapping known
information.

## Success Criteria (verbatim)

A successful Daily Brief should let the user answer: (1) What changed since
yesterday? (2) What matters? (3) What action should I take? (4) What evidence
supports those conclusions? If the system cannot answer those from gathered
intelligence, it should report that intelligence collection has not been
completed and identify the missing sources — not generate simulated
intelligence. "No intelligence available" is preferable to simulated
intelligence.

---

## Triage finding (2026-06-15)

This is a **different failure mode than RB-DEFECT-045/047**. Those defects
described a live session producing a *generic, non-canonical* brief (likely
because it skipped the retrieval call or ran on stale Instructions/KB). This
defect describes a session that **correctly executed the HARD RETRIEVAL GATE
check, correctly reported the failure**, and then **violated its own stated
conclusion** by generating full brief content anyway from conversation memory.

### Gap identified: the live (compact) gate language was a soft instruction

`api/custom_gpt_instructions_compact_8k.md` — the instruction set actually
loaded into the live Custom GPT (per RB-DEFECT-045's finding) — step 3 read:

> **HARD RETRIEVAL GATE:** render only after `status: "ok"` and
> `retrieval_receipt.verified: true`. On failure: `RB delivery failure: ...
> No brief was generated from memory.`

This states what to output on failure, but does not explicitly forbid
*additional* output after that line. The fuller `custom_gpt_instructions_8k.md`
(not the live file) already had the stronger wording: "do not generate a
substitute brief from conversation history, model memory, prior briefs, or
user-provided facts." The live compact file was missing this explicit
prohibition — plausibly why the session, having already emitted the failure
line, treated the rest of the turn as open to "do something useful anyway."

### Fix applied (2026-06-15)

1. **`api/custom_gpt_instructions_compact_8k.md`** (step 3, Bootstrap):
   strengthened the HARD RETRIEVAL GATE to an explicit full-stop: "output only
   [the failure line] and STOP — do not then render any section, summary, or
   CoS assessment reconstructed from conversation history or model memory,
   even partially." Stayed within the 8,000-char Custom GPT Instructions cap
   (trimmed redundant wording elsewhere in the same file — retrieval-order
   line, a generic-menu non-negotiable, and two trailing clauses — to make
   room; file is now exactly 8,000 chars).
2. **`api/custom_gpt_prompt.md`** (Section 6, Daily Brief Rendering): added a
   new "No simulated briefs (RB-DEFECT-048)" paragraph after the CoS contract
   line, restating the full-stop rule and adding the Context-Recall-vs-
   Intelligence-Collection distinction, plus: when the gate *passes*, every
   brief claim must trace to the `getDailyBrief`/`getDailyBriefPart2` payload
   for that date — remembered facts from earlier turns must not be blended in
   as if part of today's payload.

### Deferred: Verified Today / Historical Context / Inference labeling

This is a **new rendering requirement**, not currently present in
`DAILY_BRIEF_CANONICAL_TEMPLATE.md` or any section schema. It would require:
- Tagging each section/sentence by provenance (current-cycle API data vs.
  carried-forward state vs. derived conclusion) — most sections already
  implicitly are one of these (e.g. `last_24h_relationship_signals` is
  inherently "Verified Today", `opportunity_board` status fields are
  "Historical Context" unless `updated_at` is today, `decision_layer`/
  `recommended_actions` are "Inference").
- A rendering convention for surfacing this distinction without violating the
  existing "never display `grounding`/`freshness`/`confidence`/`disposition`
  as visible labels" non-negotiable — i.e. inline framing ("as of [date]") or
  a section preamble, not raw field names.

Not implemented in this pass — needs a design decision on rendering
convention before a prompt/template change, and ideally validation against a
re-synced live session first (per the same "validate before rendering"
cadence used for RB-DEFECT-046 Slices 1/3). Tracked as follow-up.

**Status:** Gate-strengthening fix applied to both `compact_8k` and
`custom_gpt_prompt.md`. **Live GPT re-sync completed 2026-06-15.** Awaiting
re-validation that a retrieval failure now produces *only* the
delivery-failure line, and that a normal request renders the canonical
structure. Provenance labeling (Verified Today/Historical Context/Inference)
remains open as a follow-up enhancement regardless of re-validation outcome.
