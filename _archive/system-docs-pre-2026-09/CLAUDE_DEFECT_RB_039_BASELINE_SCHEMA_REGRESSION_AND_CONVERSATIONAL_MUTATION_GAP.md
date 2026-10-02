# RB-DEFECT-039: Baseline Schema Regression (silent contact-mutation failure) + Conversational Mutation Pipeline Gap

**Date:** 2026-06-12
**Status:** Resolved (both parts) — schema regression fixed; conversational mutations for 2026-06-11/12 backfilled
**Severity:** High — silently broke the entire contact-mutation write path (`mutations.py contact-update` / `contact-add`) for all contacts, system-wide, since RB-DEFECT-038 shipped (2026-06-11).

---

## 1. Origin

Todd reported that a conversation describing four significant updates (Global
Payments verbal offer / accelerated process, Ryan Hildebrand hiring-manager →
sponsor shift, "Perfect Hire" founder advisory/equity discussion, and a
recurring market-validation pattern) produced **no proactive mutation,
relationship-state change, or daily-brief surfacing** — he had to
reconstruct everything manually. He filed this as a "mutation engine
failure," with 5 sub-tests (Global Payments status, Ryan Hildebrand
relationship state, Perfect Hire opportunity creation, market-pattern
synthesis, and "did a mutation record get created automatically").

## 2. Part A — Conversational mutation pipeline gap (workflow, not engine failure)

Investigation found:

- `relationship_intake.py` **had** run for one of the four signals (Foods
  Connected / verbal-offer snippet), producing ledger entry
  `ri-20260611T112033-3f4d80` with `claim_status: "proposed"` /
  `persistence_status: "pending confirmation"` — but it was **never
  confirmed**, so it never propagated to `active_threads.yaml`,
  `baseline_index.json`, or the mutation/lifecycle engines.
- The other three signals (Ryan Hildebrand, Perfect Hire, market-validation
  pattern) were **never submitted to intake at all** — zero ledger entries.
- `active_threads.yaml`'s `T-2026-05-genius-global-payments` thread was
  unchanged since `2026-06-01`, still describing a May 12 phone screen.

**Root cause:** RB's mutation pipeline (`relationship_intake.py` →
`record_interaction(confirmed=True)` → `mutations.py contact-update` /
`thread_promotion.py` → `insight_intake.py`) is **not autonomous for
chat-dictated updates**. There is no background process that listens to a
live conversation, extracts every discrete factual claim, and drives each
one through confirm → mutate → thread-update. This currently depends on
Claude actively invoking each stage for every discrete fact — and in this
case that only partially happened for one of four signals.

This is a **process/architecture gap**, not a broken engine — but the
practical effect (user must manually reconstruct state) is identical to a
broken engine, so it's tracked here as a real defect.

## 3. Part B — Baseline schema regression (the more serious finding)

While backfilling the mutations for Part A, `mutations.py contact-update
--id ryan-hildebrand ...` **failed validation and rolled back**, with 20 of
22 errors referring to **other, unrelated contacts**:

```
[3]  'signal_class' is a required property  (entry id: daniel)
[4]  'sources' is a required property        (entry id: daniel)
[5]  'rc_state' is a required property        (entry id: daniel)
[6]  'rc_tier' is a required property          (entry id: daniel)
... (same 4 errors for: matt, olivia, vdevjee, chason-f)
```

**Root cause:** `thread_promotion.py` (RB-DEFECT-038, shipped 2026-06-11)
auto-creates minimal `baseline_index.json` stub contacts for new inbound
threads (`source: "thread_promotion_auto (RB-DEFECT-038)"`) with only
`id`, `name`, `email`, `status`, `relationship_stage`, `narrative`,
`source`, `created_at`. These stubs **do not conform to
`system/schemas/baseline.schema.json`**, which requires `signal_class` and
`sources` on every entry (top-level `required`), plus `rc_state`/`rc_tier`
present (as `null` for non-`RC` contacts).

Because `mutations.py`'s `_validate_or_rollback` validates the **entire**
`baseline_index.json` array, **5 non-conforming auto-created entries broke
validation for the whole file** — meaning **every** `contact-update` /
`contact-add` call for **any** contact, by **anyone**, has been silently
failing and rolling back since 2026-06-11. This is a significant, currently
live regression and a strong candidate root cause for "mutations don't seem
to persist" complaints generally — it's not specific to this conversation.

The 5 affected contacts: `daniel` (growthventure.ai — RB-DEFECT-038's own
trigger case), `matt` (theperfecthire.co — this conversation's "Perfect
Hire" founder), `olivia` (theperfecthire.co), `vdevjee`
(bridgeglobalpartners.com), `chason-f` (hr-4u.org).

## 4. Fix applied

1. **Schema regression (Part B)** — added missing required fields to all 5
   `thread_promotion_auto`-created contacts: `signal_class: "LKI"`,
   `sources: [<their existing "source" value>]`, `rc_state: null`,
   `rc_tier: null`. Verified: `python3 system/schemas/validate.py
   --baseline-only` → `OK — baseline_index.json validates against
   baseline.schema.json (2748 items)`.

2. **Backfilled the four 2026-06-11/12 mutations (Part A)**:
   - Confirmed `ri-20260611T112033-3f4d80` (Foods Connected / verbal offer).
   - `active_threads.yaml` → `T-2026-05-genius-global-payments.current_state`
     updated to "Candidate Evaluation → Preferred Candidate / Offer Stage",
     `boost_score` 1.4 → 1.8.
   - `baseline_index.json` contact `ryan-hildebrand` updated: note recording
     Hiring Manager → Sponsor/Advocate reclassification, tags
     `sponsor`/`verbal-offer-received`; confirmed `interaction_ledger.json`
     entry `ri-20260612T110342-53e38c` (`unsolicited_positive_followup`,
     relationship state → `warm`).
   - New thread `T-2026-06-perfect-hire-advisory` opened in
     `active_threads.yaml`. `baseline_index.json` contact `matt` (Perfect
     Hire founder) updated with company/role + advisory/equity note, tags
     `advisory-opportunity`/`equity-discussion`; confirmed
     `interaction_ledger.json` entry `ri-20260612T110342-dee25e`
     (`direct_inquiry`, relationship state → `strategic`).
   - Market-validation pattern recorded and confirmed in
     `system/strategic_memory.json` via `insight_intake.py`
     (`thought_leadership_theme`, "operator + vendor + GTM" recurring
     external-validation thesis).

3. Full suite re-run after all changes: `python3 -m pytest system/tests -q`
   → **2353 passed, 0 failures** (twice, for determinism).

## 5. Recommended follow-up (not yet implemented)

- **Guard against recurrence of Part B**: `thread_promotion.py`'s
  auto-create path should populate `signal_class`/`sources`/`rc_state`/
  `rc_tier` on every contact it creates (or `_validate_or_rollback` should
  validate against `baseline.schema.json` as a pre-commit step in CI, so a
  non-conforming auto-create fails loudly at creation time instead of
  silently breaking all future mutations).
- **Part A (conversational pipeline)**: define an end-of-session checklist
  (or automated hook) that scans the session for discrete factual claims
  about relationships/opportunities and ensures each one is run through
  intake → confirm → mutate → thread-update, rather than relying on the
  agent to remember every instance within a single conversation.
