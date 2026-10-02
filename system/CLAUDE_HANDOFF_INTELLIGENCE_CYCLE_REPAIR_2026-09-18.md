# Claude Handoff — Intelligence Cycle Repair

Date: 2026-09-18
Repository: Relationship & Business Builder (RB)

## Objective

Repair RB's daily intelligence collection, processing, mutation, reconciliation, and reporting so the morning intelligence report and CoS brief are concise, accurate, causally connected, and improve themselves when coverage or downstream processing fails.

This is an implementation task, not merely an assessment. Inspect the current architecture, make the changes, add tests, run an end-to-end representative scan, and report exact before/after evidence.

## User's canonical mutation policy

Replace “pending intelligence by default” with this decision model:

1. Net-new information that adds a fact without replacing an existing canonical value is written automatically.
2. New information that would overwrite an existing canonical value requires user confirmation before the overwrite.
3. Conflicting information with dates that establish a reliable sequence is not an overwrite: preserve the historical fact and automatically add the newer dated fact/current state, with provenance.
4. Conflicting information without dates sufficient to establish sequence requires user confirmation.
5. Low-confidence identity resolution, ambiguous entity matching, or uncertain scope must not mutate the wrong entity; route it to review with a specific explanation.
6. Every decision must leave an auditable receipt: source, observed date, effective date when known, old value, proposed/new value, decision class, confidence, resulting artifact, and whether user action is required.

Suggested explicit statuses (adapt names to the existing schema if appropriate):

- `auto_added_net_new`
- `auto_added_dated_successor`
- `confirmation_required_overwrite`
- `confirmation_required_undated_conflict`
- `review_required_identity_ambiguity`
- `rejected_duplicate`
- `rejected_low_confidence`

The generic status `pending intelligence` should not survive unless it is a presentation alias backed by one of these exact reasons.

## Evidence from the 2026-09-18 run

### Collection

- 24 public sources attempted; 23 returned data.
- SEC EDGAR current 8-K Atom failed.
- 390 public-intelligence items fetched and written to `system/.cache/intelligence.db`.
- 10 email items classified; 2 duplicates skipped.
- 20 multi-source convergences reported.
- Three watchlist decisions generated: &pizza, Chipotle, and KFC.
- KFC was labeled `auto_applied: true`, while the same execution report said `records_changed: 0` with an empty mutation lifecycle. This must be reconciled.

### Quality defects

- The convergence engine associated unrelated stories with entities such as Burger King and Firehouse Subs, indicating over-broad entity matching.
- The brief reported GP calendar as stale because the later manual GP capture did not trigger downstream reconstruction.
- The interaction overlay reported `skipped_no_raw_input` even though messages and calls were refreshed.
- Zero counts are displayed as information instead of being suppressed unless a repeated-zero pattern indicates a broken source or process.
- Technical scoring and system statuses appear in user-facing prose without explaining why a signal matters.
- Some proposed watchlist additions are not automatically promoted even though they are net-new and non-destructive.

### LinkedIn

- `system/inbox/linkedin.messages.json` increased from 9,924 to 9,928.
- Four notification-derived messages were added, including notifications found in Gmail Trash.
- 15 RI events were written to `system/ri_events/2026-09.jsonl`: 3 inbound-contact signals and 12 last-touch updates.
- All 15 remained `proposed_write_pending_confirmation`, even where the event was a dated, net-new interaction or a newer dated last touch. Under the canonical mutation policy, those should normally auto-apply.
- The latest full LinkedIn export was dated 2026-09-15 and processed 2026-09-16; today's additions came from Gmail notifications, not a fresh full export or browser capture.

### GP Outlook manual scan

- Midnight capture: 0 inbox, 0 sent, 26 calendar.
- Later manual capture at 11:45 UTC: 14 inbox, 10 sent, 19 calendar.
- It captured meaningful Subway RFP/pricing work, Esper/Genius coordination, Pollo Campero DMB RFI activity, SC Controller Management, Worldpay Backbook, procurement transition information, FSTEC preparation, and future meetings.
- The manual capture was ingested after the interaction ledger and briefs had been built, so it did not affect relationship states, meeting prep, loops, priorities, CoS recommendations, or the published briefs.

Timing:

- 09:11 UTC interaction ledger built
- 09:24 UTC intelligence scan finalized
- 10:06 UTC daily brief generated
- 11:45 UTC manual GP capture
- 11:46 UTC manual GP ingestion

## Required implementation outcomes

### 1. Mutation decision engine

Implement the canonical mutation policy as a single shared decision path used by LinkedIn, email, calendar, public intelligence, relationship events, watchlists, account facts, and research evidence. Do not allow each ingestion path to invent incompatible meanings for “pending.”

Required cases:

- new set/list member -> automatic add
- missing scalar field -> automatic fill
- identical fact -> deduplicate, no user interruption
- newer dated fact -> append history and update current projection automatically
- older dated fact -> append history without replacing current projection
- scalar replacement -> confirmation required
- undated contradiction -> confirmation required
- uncertain identity/entity -> specific review queue

### 2. Immediate downstream cascade after manual capture

Successful GP Outlook or LinkedIn manual ingestion must trigger, or enqueue and verify completion of:

- source-health recomputation
- interaction-event ledger rebuild
- interaction/current-state rebuild
- relationship and last-touch projection
- meeting-prep evaluation
- loop/obligation evaluation
- account and competitive intelligence routing
- daily intelligence report refresh
- CoS brief refresh when the capture materially changes today's priorities
- publication/delivery refresh or a clearly surfaced post-brief amendment

The cascade must be idempotent and leave per-step receipts. A source-file write alone is not success.

### 3. Source-health and self-healing

- Treat repeated zero counts as health signals only after a source-specific baseline or consecutive-run threshold is crossed.
- Do not present ordinary one-day zero counts to the user.
- When a source is stale or fails, attempt the configured recovery path during the scan where safe.
- Recompute health after late/manual ingestion so repaired sources do not remain falsely stale.
- Explain exactly which source failed, what recovery was attempted, and whether user action remains necessary.

### 4. Entity matching and convergence quality

- Repair the over-broad matching that creates false entity/story associations.
- Require evidence showing why each story belongs to each entity.
- Add negative tests for unrelated restaurant stories and similarly named companies.
- Prevent a high article count from creating a high-confidence convergence when the entity links are weak.

### 5. Reporting contract

The intelligence report should answer:

- What materially changed?
- Why does it matter to Todd, GP/Genius, a priority account, competitor, or relationship?
- What did RB connect that Todd would not reasonably see from a single source?
- What was automatically updated?
- What genuinely requires Todd's decision?
- What failed and what did RB do about it?

Suppress normal zeros and internal scoring jargon. Provide working source links. Put system-operation proof near the bottom unless it changes trust in the report.

The CoS brief should consume the intelligence report rather than duplicate it. It should focus on today's few highest-impact actions, named people/accounts, resolved communication state, meetings needing preparation, material risks, and decisions that truly require Todd.

### 6. Audit consistency

Make these fields agree across receipts, reports, and dashboards:

- mutations generated
- automatically applied
- confirmation required
- rejected/deduplicated
- records changed
- artifacts changed
- downstream cascade completed/failed

No mutation may be described as applied unless a durable artifact changed and the receipt names it.

## Acceptance tests

At minimum, add automated tests proving:

1. A new dated LinkedIn inbound interaction automatically updates last touch and preserves provenance.
2. A later dated interaction automatically succeeds an older dated interaction without confirmation.
3. An older historical fact is appended without regressing current state.
4. A proposed replacement of an existing scalar requires confirmation.
5. Two conflicting undated values require confirmation.
6. Ambiguous identities do not mutate either candidate.
7. A manual GP capture triggers all required downstream stages.
8. Source health changes from stale to fresh after a successful late capture.
9. A repeated-zero source eventually alerts; a normal single zero remains suppressed.
10. Unrelated restaurant articles do not create false entity convergences.
11. Mutation totals reconcile exactly across execution report, audit receipt, and brief.
12. A post-brief material capture causes a refreshed brief or explicit amendment.

## Verification and deliverable

Run focused unit/integration tests and one representative end-to-end scan using safe fixtures or existing non-destructive inputs. Then provide:

- root causes found
- files changed
- tests added and exact results
- before/after mutation accounting
- before/after example for LinkedIn last-touch processing
- proof of downstream cascade following a manual GP capture
- any remaining gaps that genuinely require Todd's decision

Preserve existing user data and unrelated worktree changes. Do not claim a mutation or successful cascade without inspecting the durable artifact and receipt.

## Added defect: relationship intake misroutes contact enrichment and intelligence calls

Observed on 2026-09-18 while recording a phone call with Sal Nazir (`contact_id: sal-nazir`).

Input facts:

- Interaction channel: phone call / `call_note`
- Interaction date: 2026-09-18
- Phone number supplied by Todd: `416-457-7269`
- Sal is the former General Manager of Payments at PAR Technology and has been away from PAR since May 2026.
- Sal supplied credible but uncorroborated intelligence that DoorDash may be looking to acquire PAR Technology.

Incorrect behavior:

1. `manualRelationshipIntake(..., apply=true)` persisted the touch, but did not project the supplied phone number into the existing contact's canonical `phone` field. `baseline_index.json` retained `phone: null` even though the number was preserved in prose.
2. `processRelationshipIntake(..., source_type="call_note", apply=true)` returned the confirmed interaction with `source_type: "email"`, losing the caller-supplied channel.
3. The interaction was classified as `signal_type: "unknown"`, `cold`, and `Network Contact` despite a real phone interaction with strategically relevant human-source intelligence.
4. The manual intake invented a recruiting/opportunity interpretation, including `next_expected_action: "await recruiter/team follow-up"`, even though no recruiting context existed.
5. It automatically created an inappropriate relationship-opportunity thread and cadence loop:
   - thread: `T-2026-09-formerly-par-technology-par-technology-doordash-m-a-monitoring`
   - loop party: Sal Nazir; target 2026-09-25
6. It proposed generic tags such as `franchise_governance_signal` and `restaurant_operator_overlap`, which were not grounded in the call.

Required fix:

- Extract explicitly supplied contact fields—at minimum phone, email, current/former organization, and role—from manual relationship inputs.
- Route each field through the canonical mutation decision engine:
  - empty canonical field + explicit value -> auto-add with provenance;
  - identical value -> deduplicate;
  - different existing value -> confirmation required;
  - dated employment succession -> preserve history and update current projection automatically when sequencing is reliable;
  - undated employment conflict -> confirmation required.
- Preserve the explicitly supplied `source_type`; never silently convert `call_note` to `email`.
- Separate relationship activity from opportunity creation. A conversation, strategic intelligence signal, or company mention must not create an opportunity, thread, loop, recruiter narrative, or follow-up unless the source contains a grounded ask, commitment, next step, or user instruction.
- Classify this example as a completed phone interaction plus a human-source M&A intelligence observation. It should update last touch, channel history, contact completeness, and intelligence provenance without inventing a sales/recruiting opportunity.
- Add a repair/migration path for the Sal record that writes `416-457-7269` into the canonical phone field with provenance from the 2026-09-18 call.
- Identify and safely remove or mark erroneous the thread and loop created by this incident after proving they were generated solely by this misclassification.

Additional acceptance tests:

13. An explicit phone number fills an empty canonical phone field automatically and appears in the durable contact record.
14. A different phone number does not overwrite an existing phone without confirmation.
15. `source_type="call_note"` remains a call through persistence and downstream reporting.
16. A relationship call containing strategic intelligence but no ask/commitment does not create an opportunity, thread, loop, or recruiter narrative.
17. A real grounded commitment in a call can create a loop, with the exact commitment and due date preserved.
18. Dated former-employer information preserves employment history without treating the former employer as the contact's current company.
