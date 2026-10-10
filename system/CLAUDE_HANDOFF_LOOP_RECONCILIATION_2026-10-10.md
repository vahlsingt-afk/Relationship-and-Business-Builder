# Claude handoff — intelligence-to-loop reconciliation

Todd explicitly requested this defect report after the October 10 weekly closeout returned obsolete execution states despite processed meetings and captured communications.

Read `defects/RB-DEFECT-074_intelligence-to-loop-state-reconciliation-failure_2026-10-10.md` for evidence, live repair receipts, remaining limitations, required implementation, and acceptance cases. Also relates to RB-DEFECT-070.

Immediate live repair: Josh scheduling loop closed; Caplin, Coffland, MTBP, and engineering audit received explicit current-state correction notes via successful API calls. Three business items still have obsolete date-driven overdue classifications because no replacement dates were supplied and no general state-update operation is exposed. Preserve these facts and fix lifecycle reconciliation rather than inventing dates or approvals.

This file is a repository handoff, not proof that a running Claude session received or implemented it.

---

## RB-DEFECT-074 — Claude resolution (2026-10-10)

**Root cause (first missing stage = loop reconciliation, not capture or extraction).**
1. `eolms.match_and_transition()` — the only loop matcher on the declaration path — searched EOLMS (`EL-`) records only. The loops actually worked are legacy `L-` rows in `loop_ledger.md`; they were never candidates, so Todd's four declarations (`exec-action-2026-10-10-0014..0017`) returned `no_match` by construction.
2. Its intent lexicon (complete/block/advance) had no vocabulary for the commonest real updates ("met with", "emailed", "requested", "no response"), so most text was `no completion/advance/block language` before matching began.
3. The `ingestExecutiveDeclaration` receipt (`status: ok`, `action_recorded`) wrapped a `no_match`, so a recorded interaction read like a loop update.
4. Capture processing (`cap-3105295926e1a007`: 3,628 words, processed, 0 `executive_declaration_mutations`) only runs the declaration path on first-person patterns; third-person meeting transcripts never reached any loop at all.
5. The ledger row has no structured state, so the only write path was `redateLoop` + prose, leaving obsolete text and date-only overdue bucketing.

**Fix.** `system/scripts/loop_state.py` (new): structured per-loop state overlay (`system/loop_state.json`), `updateLoopState` (`POST /loops/state`), ledger-aware reconciliation for declarations and processed captures (idempotent by evidence id, obligation-gated closure, review queue for ambiguity), nonresponse tracking + review-first disposition proposal, weekly-plan outcome refresh + `week_of` staleness/duplicate-id health. `getLoops` gains a state-driven `waiting` bucket, `domain` (engineering vs customer_relationship), `plan_health`, `open_review_items`; cockpit exposes loop state, plan health and review items; declaration receipts carry `loop_transitions` (a `no_match` is never a transition). `self_audit_sweep` now reports `skipped | failed | passed | error` with counts/last-run, and a skipped suite can no longer close the self-audit loop as "verified clean".

**Closure rules.** A completion closes a loop only if the evidence satisfies *that loop's* obligation: introduction loops need receipt language, approval-gate loops need approval language, self-audit loops are never declaration-closable, metadata-only (Outlook) evidence never closes. A contact touch records `last_interaction` and a review item — it never closes an introduction or approves an initiative.

**Known limits / not done.** Outlook metadata ingestion is not yet wired into `reconcile_text` automatically (only `metadata_only=True` callers); progress evidence yields review items, not automatic state transitions (waiting party/state need `updateLoopState`); `addLoop` is still absent from rb_cli; `week_of` is flagged stale, not auto-rolled; Josh/Coffland follow-on meeting ("in a couple of weeks", per the Oct 7 transcript) is not yet a loop.
