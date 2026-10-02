# RB 9.69 (proposed): Conversational Mutation Completeness

**Status:** Not started — scoped 2026-06-12, follows from RB-DEFECT-039 Part A
**Severity / value:** High — this is the gap behind "I told RB something
important and it never showed up anywhere."
**Depends on:** RB-DEFECT-038 (thread_promotion.py, Tier 0/3 model) and
RB-DEFECT-039 (baseline schema regression fix) — both already shipped.

---

## 1. Problem statement

RB-DEFECT-039 Part A found that when Todd describes several discrete
relationship/opportunity updates in a single conversation, the agent
(Claude) only ran the intake → confirm → mutate → thread-update pipeline
for **one of four** signals, and even that one stalled at "proposed /
pending confirmation" — never confirmed, never propagated.

The pipeline itself works once driven end-to-end (verified during
RB-DEFECT-039: all four mutations landed correctly once run manually). The
gap is **completeness and follow-through**, not capability:

- Nothing inventories "how many discrete claims did Todd make this session
  that look like relationship/opportunity signals?"
- Nothing tracks "did each of those claims make it to `claim_status:
  confirmed` / `persistence_status: RB recorded`?"
- Nothing flags stale `proposed` records so they don't silently rot (the
  Foods Connected entry sat in `pending confirmation` for a full day before
  anyone noticed).

This is fundamentally a **session-discipline** problem: a multi-stage
write pipeline that depends on an agent remembering to drive every instance
of it to completion, with no checkpoint or alarm if it doesn't.

---

## 2. Design options

### Option A — End-of-session checklist (process-only, no new code)

Add a step to the session-end routine (`mutations.py session-end`, already
invoked at end of work sessions per P-0xx) that:
1. Re-reads the conversation transcript (or a running list the agent
   maintains) for sentences matching the existing
   `relationship_intake._classify_signal_type` / `insight_intake._classify_text`
   keyword families.
2. Cross-checks each against `interaction_ledger.json` /
   `conversation_insights.json` for a matching `created_at` timestamp this
   session.
3. Reports any "claim with no matching ledger/insight entry" and any
   `claim_status: proposed` entry created this session that's still
   unconfirmed.

**Pros:** zero new infrastructure, reuses existing classifiers, fastest to
ship.
**Cons:** still depends on the agent running the checklist; doesn't catch
proposals that go stale across sessions (e.g. the Foods Connected entry,
which sat overnight).

### Option B — "Pending mutations" daily-brief section (code, builds on DEFECT-021/022)

Add a `pending_mutations` section to `build_canonical_brief()` (in
`daily_brief.py`), analogous to the existing `watchlist_intelligence` /
`overnight_knowledge_mutations` sections:

- Query `interaction_ledger.json` for `claim_status: "proposed"` entries
  older than N hours (suggest N=12, so same-day proposals don't spam, but
  anything that survives to the next morning's brief surfaces).
- Query `conversation_insights.json` / `system/strategic_memory.json`
  similarly for `claim_status: "proposed"`.
- Render: *"3 relationship/opportunity claims from yesterday are still
  pending confirmation: [Foods Connected verbal offer], [...]. Confirm or
  dismiss each."*

**Pros:** uses the brief (a surface Todd already reads daily) as the
backstop; doesn't require the agent to remember anything — it's a pull, not
a push, from existing ledger state. Directly closes the "things sat in
'pending confirmation' and nobody noticed" failure mode from
RB-DEFECT-039.
**Cons:** doesn't solve "claim never entered intake at all" (the Ryan
Hildebrand / Perfect Hire case) — those never created a ledger row to flag.

### Option C — Inline session hook: "RB Reflection Pass" (code + process)

A lightweight script (`session_reflection.py`) that the agent runs near the
end of any conversation containing relationship/career/opportunity content:
takes the raw conversational text for the session, runs it through
`relationship_intake.process_relationship_thread` AND
`insight_intake.process_text` for **every paragraph/turn** (not just the
first), surfaces all resulting proposals in one batch, and the agent
auto-confirms the high-confidence ones (mirroring
`intelligence_mutation_engine.py`'s Stage 4 confidence-based auto-apply:
≥0.80 auto, 0.50–0.79 propose).

**Pros:** addresses both failure modes (incomplete extraction AND stalled
confirmation) in one step; reuses Stage-4 confidence pattern already proven
in `intelligence_mutation_engine.py`.
**Cons:** most new code; needs care to avoid over-triggering on routine
conversation (cost/noise).

---

## 3. Recommendation

**Ship B first, then A, defer C.**

- **B (pending-mutations brief section)** is the highest-leverage, lowest-risk
  fix: it converts "silently stuck forever" into "surfaced within 24
  hours," using infrastructure (`build_canonical_brief`, ledger
  `claim_status` field) that already exists. This alone would have caught
  the Foods Connected entry the next morning.
- **A (end-of-session checklist)** is cheap process discipline that catches
  same-session gaps (the Ryan Hildebrand / Perfect Hire case) without new
  code — add it to the agent's session-end routine /
  `CLAUDE.md` operating instructions.
- **C (full reflection pass)** is the "real" fix for completeness but is
  the highest-cost and highest-noise-risk; revisit after B+A have been live
  for a few weeks and we know how often A actually catches gaps that B
  doesn't.

---

## 4. Implementation sketch (Option B)

1. `daily_brief.py`: new helper `_compute_pending_mutations(report,
   sections)`:
   - Load `interaction_ledger.json`, filter
     `claim_status == "proposed"` and `created_at` older than 12h.
   - Load `conversation_insights.json` (`system/strategic_memory.json`),
     same filter.
   - Build `_canonical_item()` entries: title = entity/claim, summary =
     `source_text_snippet`, recommended_action = "Confirm via
     `relationship_intake.record_interaction(<id>, confirmed=True)` or
     reject", grounding = the ledger/insight id.
2. Add `pending_mutations` to `_NOVELTY_FILTER_EXEMPT` (these should never
   be silently suppressed by DEFECT-022's novelty filter — staleness is the
   point).
3. Wire into `build_canonical_brief()`'s section list + markdown renderer.
4. Tests: fixture with a `proposed` ledger entry dated >12h ago → appears
   in `pending_mutations`; one dated <12h ago → does not; a `confirmed`
   entry → does not.

## 5. Success criteria

- A `proposed`/unconfirmed relationship or insight record older than 12h
  appears in the next daily brief under a dedicated section, with a clear
  confirm/reject action.
- `CLAUDE.md` (or equivalent operating doc) gains a short end-of-session
  checklist item: "for each relationship/opportunity claim Todd made this
  session, confirm an intake record exists and is confirmed."
- No regression to the 2353-test baseline.

## 6. Effort estimate

- Option B: ~1 session (new section + tests + STATUS.md update), similar
  scope to RB-DEFECT-021's `overnight_knowledge_mutations` section.
- Option A: <1 hour, documentation-only.
- Option C: deferred — re-scope after B+A data.
