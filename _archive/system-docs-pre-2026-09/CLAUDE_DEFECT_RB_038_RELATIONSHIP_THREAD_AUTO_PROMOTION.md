# RB-DEFECT-038: Relationship/Thread Auto-Promotion and the "Remember vs. Dismiss" Threshold

**Date:** 2026-06-11
**Status:** Closed — Tier 0 + Tier 3 implemented and verified live (see §7)
**Severity:** High — directly causes empty/useless meeting briefs and lost narrative context for active opportunities
**Trigger case:** Daniel von Walzel / BridgePoint Ops podcast thread (2026-06-11 pre-podcast call)

---

## 1. Summary

RB has the data model needed to track relationship/opportunity *journeys* —
`active_threads.yaml`, opportunity dossiers (`opportunity_state`,
`opportunity_state_history`, `narrative`/`status_narrative`), and
cross-channel signal correlation
(`opportunity_signal_correlation.py`) that advances **existing** threads
based on new email/SMS activity.

What RB does **not** have is a step that decides when a brand-new signal
(an inbound message, a reply, a scheduled meeting) is significant enough to
**become** a tracked thread/opportunity/contact in the first place. New
relationships fall into a dead zone: not yet a baseline contact, not yet an
active thread, not yet an opportunity — so none of the existing journey
machinery ever engages.

---

## 2. Evidence

**Calendar event exists today** (`system/inbox/calendar.bridgepoint.json`,
id `eiue3d3grdpcm2t6u2hu5k6gic`):

> "Daniel von Walzel and Todd Vahlsing" — *Virtual Coffee - Let's connect...
> Pre-podcast call.* Attendee: `daniel@growthventure.ai`, accepted.

**Resulting meeting brief**
(`system/meeting_briefs/2026-06-11-daniel-von-walzel-and-todd-vahlsing-eiue3d3grdpcm2t6u2hu5k6gic.md`)
is effectively empty:

```
## 1. Stage and context
1 attendee(s) not yet in baseline: daniel@growthventure.ai.

## 7. Relevant gap-surface
1 attendee(s) not in baseline — add via /contacts if they have RI.
```

**Checked and confirmed missing:**

- No entry for Daniel von Walzel / growthventure.ai in `baseline_index.json`.
- No thread in `active_threads.yaml` (the existing BridgePoint Ops thread,
  `T-2026-05-bridgepoint-ops-engagement`, is about the LongFi/Jose Torres
  engagement model — unrelated).
- No opportunity dossier.
- The brief generator did not parse the calendar event's own description
  ("Pre-podcast call" / "Virtual Coffee - Let's connect") into any narrative
  context, despite that text alone being enough to classify this as a
  media/thought-leadership opportunity.
- The originating June 5 outreach email (per Todd's account: Daniel found
  BridgePoint Ops and referenced Todd's POV on AI in restaurants) is not
  present in any synced inbox file (`email.personal.json`,
  `email.bridgepoint.json`, `linkedin.messages.json`) — either it was never
  ingested or it has aged out of the synced window. This is a secondary
  ingestion-completeness gap, but **even without recovering that email, the
  calendar event alone is a strong enough signal that RB should have created
  a tracked thread.**

---

## 3. Root Cause

Two related gaps:

1. **No entity-resolution/promotion step on ingestion.** Email overlay,
   LinkedIn overlay, calendar overlay, and SMS ingestion all write to raw
   inbox/cache files, but nothing asks *"does this new signal belong to an
   existing thread/opportunity, or does it represent the start of a new
   one worth tracking?"* `opportunity_signal_correlation.py` only scans
   for stakeholders that match an **already-active** dossier
   (`load_active_dossiers()` → `_match_stakeholder()`); a sender who matches
   nothing is silently dropped from journey tracking.

2. **No promotion criteria exist anywhere in the system.** There is no
   threshold definition for "this is now worth a baseline entry" vs. "this
   is worth a thread/opportunity" vs. "this is noise, dismiss it." The
   closest analog (`graph_mutation_eligibility` in `SCHEMAS.md`) governs
   *ecosystem fact* mutations from articles, not *relationship/thread*
   creation from personal communications.

---

## 4. Proposed Threshold Model: "When does the CoS remember vs. dismiss?"

This proposes a tiered signal-strength model, modeled on the existing
`graph_mutation_eligibility` / RC-promotion (LMI→LKI) pattern but applied to
**thread and contact creation** rather than fact mutation.

### Tier 0 — Noise (no mutation, log only)

- Inbound message with no prior relationship AND matches a cold-outreach /
  template-spam signature (generic financing pitches, "let's hop on a
  15-min Zoom" boilerplate from unknown senders — see the Anel
  Paul/QualiFi-style LinkedIn messages already in `linkedin.messages.json`).
- Action: stays in raw inbox/signal store. Never surfaced, never promoted.
  Reuses cues already partially present in `relationship_classification.py`.

### Tier 1 — Candidate Signal (silent tracking, surfaced in collection summary only)

- Inbound message from a previously-unseen sender that is **specific to
  Todd/BridgePoint Ops** (references Todd by name, BridgePoint Ops, his
  actual work, a real shared context — not a generic template), but Todd
  has not yet responded.
- Action: write a lightweight "candidate thread" record (new, small store —
  e.g. `system/.cache/candidate_threads.json`). Surfaced only in
  `intelligence/collection` summary as "new contact noticed, no action
  taken yet." No `baseline_index` or `active_threads.yaml` write.

### Tier 2 — Engaged Exchange (auto-create baseline contact + narrative)

- Two-way exchange detected: Todd replied to a Tier-1 inbound, **or** the
  contact replied to something Todd sent.
- Action: auto-create a `baseline_index.json` entry with
  `status: "prospect"` / `relationship_stage: "new_contact"`, plus a
  short `narrative` field synthesized from the exchange (who reached out,
  why, what was said) — this is the "Daniel discovered BridgePoint Ops and
  reached out because of Todd's POV on AI in restaurants" sentence from the
  original memo. `requires_confirmation: false` — this is additive and
  low-risk (creating a record, not overwriting one).

### Tier 3 — Calendar Confirmation (auto-create/upgrade thread + dossier)

- A calendar event is created with an external attendee not yet a
  Tier-2+ contact, **or** an existing Tier-2 contact gets a meeting
  scheduled. This is the strongest signal in the system — calendar
  acceptance from a real person is very hard to fake and is a stronger
  promotion trigger than email alone.
- Action:
  - If no baseline entry exists yet, create one now (retroactive Tier 2),
    pulling narrative context from (a) the calendar event's own
    title/description, and (b) a search across all inbox sources
    (email, LinkedIn, SMS) for this person's email/handle/name.
  - Open an `active_threads.yaml` entry. Classify `type` from
    description keywords — e.g. "podcast"/"interview"/"guest" →
    `media_thought_leadership`; "intro"/"connect" → `networking`; etc.
    (extends the existing `classify_opportunity_type` logic in
    `opportunity_sensing.py`).
  - Populate `narrative`/`context` with the synthesized story, and set
    `boost_for_brief` based on type (media/thought-leadership = high,
    since external validation of Todd's thesis is itself a signal worth
    the CoS tracking — ties back to `thesis_alignment_detected` /
    `engagement_opportunity` mutations already in
    `intelligence_mutation_engine.py`).

### Tier 4 — Relationship/Opportunity Confirmation (existing process)

- After the meeting occurs and/or follow-up happens, existing RC-promotion
  (LMI→LKI, RC review queue) and `opportunity_signal_correlation.py`
  state-advancement take over. **No new work needed here** — this is the
  part of the system that already works, once Tier 3 has created something
  for it to operate on.

### Dismissal / Decay Rules

- Tier 1 candidates with no reply within ~14 days → archived to history,
  removed from active surfacing (kept in raw signal log for audit, never
  deleted).
- Tier 2/3 threads with no advancement (no reply, no meeting held, no
  follow-up) past `target_close` or a default dormancy window → auto-
  transition `status: dormant` (not deleted), with `re-entry signal`
  monitoring — this directly addresses the Unisys example from the original
  memo ("Closed" → "Dormant with re-entry signals").

---

## 5. Implementation Plan

1. **New module `system/scripts/thread_promotion.py`**
   - `evaluate_promotion(event, baseline, active_threads)` — takes a
     normalized interaction/calendar event, returns a tier (0-3) and a
     proposed mutation (new baseline entry / new thread / dossier upgrade).
   - Reuses `_resolve_baseline_contact`-style matching from
     `intelligence_mutation_engine.py` and `classify_opportunity_type` from
     `opportunity_sensing.py`.

2. **Wire into ingestion paths**
   - `calendar_overlay.py` / `fetch_google.py`: new external attendee on a
     real (non-"hold"/"block") meeting → Tier 3 evaluation.
   - `email_overlay.py` / `interaction_overlay.py` / LinkedIn overlay:
     two-way exchange detection → Tier 2 evaluation.

3. **Meeting brief generator fix (most urgent — directly fixes today's
   empty brief)**
   - Before falling back to "attendee not in baseline," run Tier 3
     evaluation: parse the calendar event's title/description, search all
     inbox sources for the attendee's email/name, and render a narrative
     section even if no baseline record exists yet.
   - Auto-create the baseline/thread record per Tier 3 so the *next* brief
     for this person starts from an existing record.

4. **Tests**
   - New `system/tests/test_thread_promotion.py` covering tier
     classification (noise/candidate/engaged/calendar), dismissal/decay,
     and the Daniel von Walzel calendar-event case as a regression fixture.
   - New wiring-gap test confirming meeting briefs for not-yet-baseline
     attendees include synthesized narrative context, not just a gap note.

5. **Audit/observability**
   - Every auto-created baseline entry / thread should be logged the same
     way mutations are (`knowledge_mutations.json` pattern) — "RB created
     a new thread because X" — so Todd can review/correct auto-promotions,
     mirroring the existing `requires_confirmation` transparency contract.

---

## 6. Open Questions for Todd — resolved

- **Calendar acceptance alone (Tier 3) → auto-create or confirm-first?**
  Resolved as **auto-create the baseline contact** (additive, idempotent,
  reversible — delete the entry if wrong) but **confirm-first for
  `active_threads.yaml`**, which stays hand-curated. RB renders a
  ready-to-paste YAML block and logs `mutation_proposed`; nothing is
  written to `active_threads.yaml` without Todd pasting it. This mirrors
  the `requires_confirmation` / `"pending confirmation"` pattern from
  RB-DEFECT-037's `opportunity_pipeline.py`.
- **Dormancy window defaults by thread type?** Deferred — out of scope for
  this pass. Tier 3 promotion fires once per unmatched calendar attendee
  (event-driven, not a recurring scan), so no decay/dormancy logic was
  needed to make the Daniel von Walzel case work. If/when Tier 1/2 (silent
  candidate tracking, engaged-exchange auto-create) are built, dormancy
  windows should be revisited then.

---

## 7. Implementation Summary (2026-06-11)

Implemented **Tier 0** (noise dismissal) and **Tier 3** (calendar
confirmation) from §4. Tiers 1 and 2 (candidate-signal tracking and
engaged-exchange auto-create from email/LinkedIn two-way replies, without a
calendar event) were **not** implemented in this pass — they require wiring
into the email/LinkedIn overlay ingestion paths rather than the meeting-brief
generator, and weren't needed to fix the trigger case.

- **New module `system/scripts/thread_promotion.py`**:
  - `is_cold_template()` / Tier 0 — template-phrase + `core.NOISE_DOMAIN_PATTERNS`
    dismissal.
  - `classify_thread_type()` — keyword classification into
    `media_thought_leadership` / `recruiting` / `business_engagement` /
    `networking` / `general_relationship`, with `_BOOST_BY_TYPE` mapping to
    `boost_for_brief`.
  - `search_inbox_history()` — cross-account email (inbound + sent) and
    LinkedIn message search for prior context.
  - `synthesize_narrative()` — evidence-only narrative (Tenet 1: never
    invents facts; states "no prior history found" when there's none).
  - `evaluate_promotion(event, attendee)` — the Tier-3 entry point. Returns
    `None` for Tier-0 dismissal, else a proposal dict with
    `proposed_baseline_entry`, `proposed_thread`, `proposed_thread_yaml`,
    `persistence_status: "pending confirmation"`.
  - `apply_baseline_entry()` — idempotent baseline append (dedupes on
    id/email).
- **`meeting_prep.py`** wired: unknown attendees get a `promotion` proposal;
  briefs render a new "## 1a. New relationship — proposed thread" section;
  `write_artifact()` (non-dry-run) calls `_apply_promotions()`, which
  auto-creates the baseline entry and logs both `item_persisted`
  (baseline) and `mutation_proposed` (thread YAML) via `audit_log.py`.
- **17 new tests** (`system/tests/test_thread_promotion.py`, TP1–TP6),
  including `test_TP4c_daniel_von_walzel_regression` pinning this exact
  case. Full suite: 2329 → 2346 passed, zero regressions (same 7
  pre-existing unrelated failures).
- **Verified live**: `meeting_prep.py --for-today --confirm` regenerated
  today's briefs. The Daniel von Walzel brief
  (`system/meeting_briefs/2026-06-11-daniel-von-walzel-and-todd-vahlsing-eiue3d3grdpcm2t6u2hu5k6gic.md`)
  now shows: a synthesized narrative ("Daniel first reached out on
  2026-06-08 (via email.bridgepoint). Calendar context: ... Pre-podcast
  call."), `media_thought_leadership` classification, `boost_for_brief:
  high`, and a pasteable `active_threads.yaml` entry (`T-2026-06-11-daniel`).
  A new baseline entry `daniel` (daniel@growthventure.ai,
  `status: prospect`, `relationship_stage: new_contact`) was auto-created
  and logged in `system/audit/2026-06.jsonl`. Three other unmatched
  attendees from today's other meetings (Matt/Olivia from
  theperfecthire.co, Vdevjee from bridgeglobalpartners.com) were promoted
  the same way, confirming the fix generalizes beyond the single trigger
  case.

**Known follow-on (not blocking, not filed as a separate defect)**: display
names for attendees with no calendar-supplied name fall back to a
title-cased email local-part (e.g. "Vdevjee" from
`vdevjee@bridgeglobalpartners.com`). This is a cosmetic naming issue, not a
promotion-logic issue — Todd can correct the `name` field in
`baseline_index.json` directly if/when he confirms who these contacts are.
