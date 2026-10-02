# Claude Architecture Review Response — RBB Cockpit Boundary

**Date:** 2026-08-19
**Responding to:** `system/CLAUDE_HANDOFF_RBB_COCKPIT_ARCHITECTURE_REVIEW_2026-08-19.md` (commit `c8356bc`)
**Reviewed against:** working tree at `c8356bc`, including `system/CANONICAL_REGISTRY.yaml`, `system/cockpit/context.json`, `system/scripts/cockpit_context.py`, `system/loop_ledger.md`, `system/eolms/loops.json` (55 records), and the concurrent uncommitted operational diff (see "Concurrent work" below).

## 1. Architecture verdict: **Approve with changes**

The core design is correct and should not be re-architected: repository remains sole system of record, `context.json` is generated-only with no independent write path, and the registry's honest use of `distributed_consolidation_pending` rather than inventing false authority is exactly right. The changes required are narrow — one factual correction, one loop-authority resolution grounded in data I checked directly, and a sequencing/verification discipline for the GPT integration given this project's documented history of "confirmed" integrations that weren't actually firing.

## 2. Required registry edits

Apply directly to `system/CANONICAL_REGISTRY.yaml`:

1. **`status: proposed` → `status: approved_with_amendments`** (top-level, line 4), with a note referencing this response document.
2. **`cockpit_projection_contract.status: not_built` → `status: built`.** This is currently factually wrong — `system/cockpit/context.json` and `system/scripts/cockpit_context.py` both exist, validate as JSON, and passed three focused tests per the handoff. Leaving `not_built` in an approved registry will mislead the next reader (human or GPT) into thinking the projection doesn't exist yet.
3. **`execution_loops.consolidation_target`**: change from `explicit_single_loop_authority_decision` (marked unresolved) to the resolution in §3 below — permanent scope-based split, not a pending decision.
4. **`unresolved_governance_decisions`**: remove the loop-authority line (now resolved) and add the consolidation sequencing order from §6 (opportunities → accounts → decisions → theses) so the list reflects an ordered backlog, not an unordered pile.
5. **`execution_loops.validation`**: add a new script entry, e.g. `python3 system/scripts/loop_reconciliation.py check`, per §3.

## 3. Loop authority decision

**Chosen option:** *Keep permanent `L-`/`EL-` namespace authority with a unified projection* — but the split is by **scope**, not by legacy-vs-new, and this needs to be stated explicitly because the two stores are not currently redundant with each other.

I checked this directly rather than assuming. Of the 55 EOLMS records, 34 already carry a `related_loop_ids` back-reference to a legacy `L-` id — so cross-linking infrastructure already exists and is partially used. But narrowing to what actually matters operationally right now: of the **11 currently-open `L-` loops**, only **one** (`L-2026-05-08-005`, the Toast/Bob Gibson watch) has any EOLMS counterpart. The other 10 — including the IKEA/Ray Ghazali debit-support loop and the Worldpay/Genius transaction-metered-POS initiative with Five Guys, both substantive, currently live business threads — have **no** EOLMS record at all. Conversely, EOLMS's 11 *active* records are mostly a different population entirely: RB platform sub-projects, the BridgePoint personal-brand transition, publishing, home projects (bathroom remodel, Blink camera RMA). These are not duplicates of the tactical `L-` loops; they're a genuinely different layer — durable initiatives and personal/meta threads — that happens to share the word "loop."

So the handoff's framing of "30 active/nonterminal obligations across both namespaces" is not a double-count to be reconciled away — it's ~10 distinct tactical obligations plus ~19 distinct initiative-level obligations, with exactly one confirmed overlap. Merging EOLMS into being "the" loop authority (option 2) would either lose the IB-driven tactical pipeline's systematic coverage (every IB can open an `L-` loop today via `mutations.py`; nothing forces every open thread into EOLMS) or require duplicating that pipeline into EOLMS for no benefit, since EOLMS's richer schema (`priority`, `strategic_value`, `activation_condition`, `cadence_days`, `history`) is aimed at multi-week initiatives, not per-interaction follow-ups.

**Resolution:**
- `loop_ledger.md` / `L-` remains authoritative for IB-generated tactical obligations (owner: `system/scripts/mutations.py`).
- `system/eolms/loops.json` / `EL-` remains authoritative for durable initiatives, projects, and personal/meta threads (owner: `system/scripts/eolms.py`).
- `related_loop_ids` cross-linking is now **mandatory, not optional**, whenever an `EL-` record concerns a subject that already has an open `L-` loop (or vice versa via a new `related_loop_ids`-equivalent on the `L-` side, currently absent from `loop_ledger.md`'s schema).
- New validation: `system/scripts/loop_reconciliation.py check` — flags open `L-`/`EL-` pairs that share person/org/subject overlap but no cross-link (semantic-duplicate detection), and reports the true distinct-obligation count (raw sum minus confirmed links) so "30 open obligations" never gets read as 30 distinct things without that context. `cockpit_context.py` should surface this computed distinct count alongside the raw per-namespace lists it already renders correctly (it does *not* currently flatten them into one misleading total — that's good, keep it).

## 4. Custom GPT and ChatGPT Project role decision

**Approve the proposed posture as written:**
- The existing RBB Custom GPT ("Relationship Bridge 9.0," backed by `openapi_gpt.yaml`'s 30-op `queryEngine`/Actions surface) remains the daily cockpit.
- A ChatGPT Project may later group workstreams and hold compact operating instructions — this is additive and optional, not a prerequisite for anything else in this plan. Lowest priority in the sequence below.
- Neither stores independently editable canonical business state.
- Both retrieve `context.json` and submit findings through the shared promotion contract (§5).

**One addition, non-negotiable given this project's history:** this repository's own memory log records at least two prior incidents (`closeLoop`, `ingestExecutiveDeclaration`) where a Custom GPT Actions/Instructions change was re-pasted, the user confirmed the re-upload, and the model still never called the underlying endpoint — verified only by later finding zero matching entries in `request.log`, not by the "confirmed" chat exchange. Any new Action or Instructions change that wires the GPT to `context.json` must be verified the same way — a live conversation that produces a matching `request.log` entry for the new context-retrieval call — before it is treated as done. Do not let a "user confirmed the paste" status stand in for that.

## 5. Promotion lifecycle: **Approve as proposed**, with one addition

Receipt statuses (`not_persisted`, `proposed_write_pending_confirmation`, `persisted`, `blocked_conflict`, `skipped_duplicate`) and the transaction shape (snapshot → authoritative mutation → regenerate projections → validate → read back → receipt) are correct and match the event-first direction already declared for `identity_relationships`.

**Addition:** every issued receipt must be appended to `system/audit/*.jsonl` (the existing `audit_sessions_receipts` domain), not held only in-memory or in a GPT-side transcript. That domain is already declared `authority_status: operational` and is exactly the mechanism that would have caught the two GPT-integration failures above sooner if the corresponding endpoint calls had been auditable the same way. Reuse it rather than inventing a parallel receipt log.

## 6. Ordered implementation plan with explicit gates

Revises the handoff's proposed sequence based on the findings above:

1. **Apply the registry edits in §2** (mechanical; Codex).
2. **Build `loop_reconciliation.py`** (§3) — cheap, and it operationalizes the loop-authority decision immediately using data that already exists (`related_loop_ids`).
3. **Triage the 35 failing tests** — classify sandbox-only failures vs. real WIP contract mismatches (the handoff already names the affected areas: capture rendering, earnings copy/history, identity-confirmation rendering, triage IDs, newsletter dedup, transcript normalization, watchlist rendering, weekly-plan draft guidance). Fix anything touching `cockpit_context.py`, `CANONICAL_REGISTRY.yaml`, briefing, or entity-alert paths first, since those overlap the concurrent operational diff (§7). Defer genuinely unrelated WIP failures with a tracked note rather than blocking on them.
4. **Expose `context.json` through the existing authenticated RBB API** as a read-only endpoint. Required before any GPT/Project wiring.
5. **Update the Custom GPT Instructions/Actions** to retrieve the new endpoint — gated on live `request.log` verification per §4, not on upload confirmation alone.
6. **Build the review-first promotion inbox**, receipts logged to `system/audit/*.jsonl` per §5.
7. **Begin domain consolidation** in this order — opportunities, then accounts, then decisions, then theses — only after the promotion inbox exists, so consolidation writes go through the reviewed path rather than ad hoc:
   - *Opportunities first*: `active_threads.yaml` is already the closest thing to a seed registry and is what `cockpit_context.py` already reads for `active_opportunities`.
   - *Accounts next*: needed as a stable foreign key before opportunities/theses can cleanly reference them.
   - *Decisions*: lowest schema risk (mostly an append-only ledger already scattered across `_sessions/`, `ri_events/`, `audit/`).
   - *Theses last*: depends on both account and evidence linkage being stable first.
8. **ChatGPT Project instructions** — **done 2026-08-19**, ahead of the rest of this sequence; see §8.

## 7. Concurrent work: nothing to revert or quarantine

The working tree carries uncommitted changes to `daily_brief.py`, `entity_alerts.py`, `ecosystem_brief.py`, `strategic_events.json`, `strategic_operators.yaml`, and today's intelligence brief, all dated 2026-08-19 and tagged `RB-DEFECT-2026-08-19`. I read the diffs: they're a watchlist-brand expansion (30→78 brands, applied identically to both `daily_brief.py` and its known hand-duplicated copy in `entity_alerts.py` — consistent with the existing sync obligation already on record) and a fix separating "record to the entity graph" from "surface as a reported/material signal" in `ecosystem_brief.py`. Both are narrowly scoped, unrelated to the cockpit boundary, and don't touch any file the registry declares authoritative for a domain under review. Leave them untouched, as already done. The untracked `.tmp/`, `system/artifacts/jeff_coffland_product_map/`, and `system/scripts/technomic_watchlist_scan.py` are likewise outside this review's scope — no action taken or recommended.

No commits from this handoff (`efb43a0` through `dc96732`) need reverting. `0f85fa4` remains correctly labeled WIP per the handoff and should stay that way until §6 step 3 completes.

## 8. Addendum — ChatGPT Project "RBB" set up 2026-08-19

Todd elevated §4's ChatGPT Project item from optional/deferred to immediate: the Project (named **RBB**) should always present the cockpit context and respond in the RBB persona. I confirmed a material platform constraint first: ChatGPT Projects (as of mid-2026) support persistent custom instructions and uploaded files, but have **no Actions/live-API access** — that capability is Custom-GPT-only. So "always loaded" inside this Project cannot mean a live pull the way the Custom GPT's `queryEngine` Actions can; it means an uploaded `context.json` snapshot plus instructions telling the model to treat it as authoritative, refreshed on a manual cadence (recommended: after each morning pipeline run, replacing rather than accumulating).

**Instructions pasted into the RBB Project's custom instructions field (confirmed by Todd, 2026-08-19, well under the 8,000-char limit):**
- Persona: identify as RBB in every reply in this Project.
- Authority: the most recently uploaded `context.json` is the active operating context; open loops across `L-`/`EL-` namespaces are kept distinct per §3, not summed without checking `reconciliation_queue`; `context.json` is not editable state.
- A follow-up guardrail paragraph addressing two Project-level platform settings surfaced in the Advanced Settings panel:
  - **Memory: "Open," not user-changeable** — this Project can pull in ChatGPT's cross-chat memory and leak facts back out to unrelated chats, entirely outside the promotion contract (no snapshot/validate/receipt). Instructions now tell the model not to treat memory-derived facts as canonical.
  - **Library access: Enabled** — the Project can surface any file from Todd's whole ChatGPT file library, not just this Project's uploads, risking a stale file being read as current RBB state. Instructions now tell the model to defer to the most recent `context.json` over anything memory or the wider library surfaces, and to say so when there's a conflict.

**Standing operational consequence:** until implementation gate 4 (read-only cockpit API endpoint) exists and, separately, ChatGPT Projects gain Actions/connector support for custom APIs (not available as of this review), the Custom GPT remains the only RBB surface that can be verifiably fresh on every turn. The RBB Project is freshness-bounded by whenever `context.json` was last manually re-uploaded — its own instructions now say this explicitly rather than implying parity with the GPT. Re-upload cadence is an operational discipline to track going forward, not a one-time setup step.
