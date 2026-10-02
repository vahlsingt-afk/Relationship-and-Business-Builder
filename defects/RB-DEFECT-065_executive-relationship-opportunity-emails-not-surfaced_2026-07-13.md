# RB-DEFECT-065: Executive Relationship Opportunity Emails Not Surfaced

**Date:** 2026-07-13
**Severity:** High
**Classification:** Email Intelligence / Relationship Signal Scoring
**Status:** Open — filed, not yet remediated
**Reported by:** Todd, via a real near-miss — an email from Erika Till (trusted industry contact)
containing a founder introduction (Manu Gahlot / FoodieBee) and a CTO opportunity at a major pizza
brand sat unanswered for several days without ever appearing in a Daily Brief.

## Summary

RB's email pipeline surfaces incoming mail by **sender identity** (is this person already in the
baseline, or does this thread already match a tracked opportunity/company) — never by **content
significance** (does this email contain an introduction, a career/partnership opportunity, or a
direct question awaiting Todd's decision). An email from a known, trusted contact carrying two
open questions ("Would you like to speak with him?" / "Would you be interested in the CTO role?")
had no path to becoming a flagged, persistent open loop, and would not have surfaced even in the
brief's raw email list without being one of the first 10 baseline-matched threads that day.

## Current Behavior (verified against code, not just symptom)

1. **`email_overlay.py`** (`system/scripts/email_overlay.py`) computes exactly two things from
   inbox content: `from_baseline` (sender matches a known baseline contact) and
   `active_thread_company_hits` (sender's domain matches a company on an *already-tracked* active
   thread). Nothing in the module reads email body text for intent — no detection of questions,
   introductions, offers, or requests for a decision. The only body-aware logic in the file is
   `sent_followups`, and that only classifies **Todd's own outbound** messages by response-window
   business days — there is no equivalent "incoming message contains an unanswered question to
   Todd" classification.
2. **Daily Brief rendering** (`system/scripts/daily_brief.py:18048-18055`) prints
   `from_baseline` as a flat, unranked list, hard-capped at the first 10 entries
   (`em["from_baseline"][:10]`), each shown as sender/tier/subject/date only. There is no scoring
   field on these entries and no sort by anything but the underlying fetch order — an email
   carrying two open decisions renders identically to a routine check-in, and a busy inbox day can
   push either off the visible top-10 with no differentiation.
3. **Opportunity-pipeline matching** (`daily_brief.py:8841`, `8911-8913`) — email_overlay hits are
   cross-referenced against opportunities, but only threads matching a company/person **already
   in the opportunity pipeline**. FoodieBee and the CTO role at the pizza brand were brand-new —
   neither existed as a tracked opportunity yet, so this path could not have caught them either.
4. **Open-loop creation** (`cos_judgment.py:601-624`) only opens a loop for (a) dormancy
   crossings against existing baseline contacts, or (b) `active_threads.yaml` entries with no
   loop. Nothing generates a candidate loop from raw inbox content — a brand-new introduction or
   opportunity email never enters the loop system unless a human manually creates one.
5. Net effect: an email must already be tied to a tracked person/company/thread to get *any*
   differentiated treatment, and even then gets no signal for "contains an unanswered question" or
   "introduces a new executive-level contact." Untracked-but-high-value content is invisible until
   a human reads the raw inbox.

## Root Cause

RB's email intelligence is **identity-routed, not content-scored**. It answers "do I already know
this sender/company/thread?" — never "does this message matter regardless of whether I already
know about it?" This is the same shape of gap as prior sourcing defects
([RB-DEFECT-022](RB-DEFECT-022_stale-intelligence-resurfacing-no-novelty-scoring_2026-06-05.md) —
novelty scoring; [RB-DEFECT-059](RB-DEFECT-059_executive-open-loop-management-system_2026-07-02.md)
— loops require manual creation), but distinct: this is specifically about incoming-email *content*
never being read for relationship/career significance at all, deterministically or via the GPT
layer — `custom_gpt_instructions_compact_8k.md`'s only email-handling rule
(`processRelationshipIntake`) fires when Todd *reports* an email conversationally, not when RB
scans the inbox on its own.

## Requested Behavior (from report)

RB should identify emails containing: introductions, referrals, executive networking
opportunities, career opportunities, partnership discussions, requests for a decision, and
unanswered questions — scored on dimensions like trusted-sender status, executive introductions,
strategic partnerships, invitations, and open questions. Matching emails should: appear in the
Daily Brief regardless of prior tracking status, appear in the Relationship dashboard, create an
open loop, continue resurfacing until resolved, and recommend a response draft.

## Recommendation — Phased

Full semantic scoring of arbitrary email content is a judgment call best made by the GPT layer
(same reasoning RB already applies to capture transcripts and conversational relationship intake),
not a deterministic keyword classifier in `email_overlay.py` — false positives here (routine emails
flagged as "opportunities") would erode trust in the brief as fast as false negatives do.

- **Phase 1 — content pass on the existing overlay, not identity-gated.** Extend
  `email_overlay.py` to run a lightweight signal pass (question marks addressed to Todd, common
  introduction/referral phrasing, "would you be interested/available") over **all** fetched
  inbox mail, not only `from_baseline`/`active_thread_company_hits`. Emit a `content_signals` list
  independent of sender-match status so new, untracked senders aren't excluded by construction.
- **Phase 2 — GPT-layer judgment, same pattern as capture processing (RB-DEFECT-063).** Before
  rendering the email section of a brief, have the GPT review `content_signals` hits and decide
  which rise to "Relationship Follow-up" status — mirrors the existing per-item loop
  (`getCapturesPending` → judgment → `submitCapture`) rather than a new deterministic scorer.
  Flagged emails get a `[RELATIONSHIP FOLLOW-UP]` brief section (sender, what's being asked, why
  it matters) and an `open_outreach_loop` proposal via the existing `cos_judgment.py`
  `WRITE_ACTIONS` / `requires_confirmation` convention — no auto-send, ever.
- **Phase 3 — persistence until resolved.** Once flagged, the loop should behave like any other
  EOLMS entry ([RB-DEFECT-059](RB-DEFECT-059_executive-open-loop-management-system_2026-07-02.md))
  — resurface daily until Todd responds or explicitly dismisses it, not a one-time brief mention.
- **New-contact graph candidates:** people introduced in a flagged email (e.g. Manu Gahlot) should
  become provisional baseline candidates, not full baseline entries — same tiered "remember vs.
  dismiss" model documented in the RB-DEFECT-038 close-out (see Related) and implemented in
  `thread_promotion.py`'s Tier 0/3 logic, reused rather than reinvented.

## Related

- `system/scripts/email_overlay.py`, `system/scripts/daily_brief.py` (lines cited above),
  `system/scripts/cos_judgment.py`
- [RB-DEFECT-022](RB-DEFECT-022_stale-intelligence-resurfacing-no-novelty-scoring_2026-06-05.md) —
  novelty scoring gap (adjacent: this defect is about first-contact significance, that one about
  resurfacing already-known intelligence)
- [RB-DEFECT-059](RB-DEFECT-059_executive-open-loop-management-system_2026-07-02.md) — EOLMS is the
  destination system a flagged email's open loop should land in
- [RB-DEFECT-038 close-out](../rb_defect_038_closeout.md) (memory) — Tier 0/3 promotion model to
  reuse for new-contact candidates surfaced by a flagged email
- RB-DEFECT-063 (`defects/RB-DEFECT-063_captures-required-manual-trigger-and-sat-unprocessed_2026-07-03.md`)
  — precedent for GPT-layer judgment replacing a manual-trigger gate, same pattern proposed here
