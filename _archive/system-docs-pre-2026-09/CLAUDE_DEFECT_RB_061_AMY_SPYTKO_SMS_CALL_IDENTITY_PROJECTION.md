# RB-DEFECT-061: Amy Spytko SMS/Call Activity Not Mapped Into Relationship Intelligence

**Date reported:** 2026-07-27  
**Reported by:** Todd / Codex  
**Severity:** High — relationship intelligence miss for an inner-tier RC contact  
**Status:** Resolved — 2026-07-27  

## Summary

RB should be paying attention to SMS texts and phone calls between Todd and Amy Spytko, including a phone call Todd had with Amy the week before 2026-07-27. Instead, Amy's canonical relationship record still appears stale and under-instrumented.

This caused RB to miss context for an active obligation: Todd owes Amy an answer on an integration contact (`L-2026-07-27-002`).

## Expected Behavior

For an inner-tier RC contact like Amy Spytko:

- SMS/iMessage activity should map to `amy-spytko`.
- Phone calls should map to `amy-spytko`.
- `last_touch` should update from recent direct interaction evidence.
- `communication_frequency_90d` should reflect recent activity.
- Relationship intelligence and daily brief should surface meaningful open obligations created or reinforced by the SMS/call thread.
- RB should not classify Amy as quiet/cold if recent SMS/call evidence exists.

## Actual Behavior

Amy's canonical baseline entry still shows:

- `id`: `amy-spytko`
- `rc_state`: `ACTIVE`
- `rc_tier`: `inner`
- `last_touch`: `2026-01-24`
- `communication_frequency_90d`: `0.0`
- `phone`: `null`

Daily-brief cache evidence also previously treated Amy as dormant/quiet:

- `Amy Spytko — COLD [partial — LinkedIn unreconciled]`
- `Amy Spytko is ... past inner dormancy threshold with no active loop`

That is inconsistent with Todd's stated reality on 2026-07-27:

- There have been SMS texts between Todd and Amy.
- Todd and Amy had a phone call last week.
- Todd owes Amy an answer on an integration contact.

## Evidence

Baseline entry:

```json
{
  "id": "amy-spytko",
  "name": "Amy Spytko",
  "current_company": "QSRSoft",
  "current_role": "Vice President of Global Sales",
  "email": "amy.spytko@gmail.com",
  "phone": null,
  "signal_class": "RC",
  "rc_state": "ACTIVE",
  "rc_tier": "inner",
  "last_touch": "2026-01-24",
  "relationship_health": {
    "communication_frequency_90d": 0.0
  }
}
```

Open loop created from Todd correction:

```markdown
| L-2026-07-27-002 | 2026-07-27 | Amy Spytko | Send Amy an answer on the integration contact you owe her. Capture the contact name/context once identified so the loop can close cleanly. | 2026-07-29 | open |
```

Protocol expectation:

- `P-019_apple_interaction.md` says messages/calls should feed `system/inbox/messages.json`, `system/inbox/calls.json`, and `interaction_overlay.py`.
- `P-024_relationship_signals.md` says messages and calls should feed `relationship_signals.py`.
- `P-001_daily_brief_regen.md` says SMS messages and phone logs are part of the morning source refresh.

## Suspected Root Cause

Most likely identity-resolution failure:

1. Amy's baseline entry has no phone number (`phone: null`).
2. The SMS/call fetchers may have captured the raw events, but `interaction_overlay.py` cannot map them to `amy-spytko`.
3. Relationship signals therefore fail to produce a matched `amy-spytko` event.
4. `last_touch`, relationship health, and daily brief context remain stale.

Alternative or additional causes:

- The recent SMS/call source refresh did not run successfully.
- Calls/messages were captured but cached overlay was not regenerated.
- Raw Apple handles for Amy exist under a phone number not merged into baseline/contact aliases.
- The overlay creates proposed writes but they remain pending confirmation and never project to baseline.

## Why This Matters

This is exactly the kind of signal RB is supposed to catch:

- Amy is an inner-tier former-PAR / restaurant-tech relationship.
- The interaction was recent and action-bearing.
- The action is concrete: Todd owes an integration contact answer.
- Failing to capture it makes the daily brief less trustworthy and makes RB look blind to current relationship reality.

This is not only a data freshness issue. It is a relationship intelligence identity-resolution defect.

## Required Fix

Claude should inspect and fix the path:

1. Confirm whether Amy's recent SMS/call records exist in:
   - `system/inbox/messages.json`
   - `system/inbox/calls.json`
   - `system/.cache/interaction_overlay.json`
   - `system/.cache/relationship_signals.json`
   - `system/ri_events/2026-07.jsonl`
2. Identify the phone handle associated with Amy from raw records or contacts.
3. Add / merge that phone into `amy-spytko` through the proper mutation path, not by ad hoc manual edit.
4. Regenerate interaction overlay and relationship signals.
5. Project a verified `last_touch` update for Amy.
6. Ensure daily brief renders the active Amy obligation and no longer treats her as dormant/quiet.

## Acceptance Criteria

- `amy-spytko.phone` or equivalent alias includes the phone handle used by Apple Messages / Calls.
- `interaction_overlay.py` matches Amy's recent SMS/call activity to `amy-spytko`.
- `relationship_signals.py` produces a recent direct-interaction signal for Amy.
- `last_touch` reflects the recent SMS/call evidence, not `2026-01-24`.
- `communication_frequency_90d` is no longer `0.0` if recent messages/calls are present.
- Daily brief / relationship prep surfaces `L-2026-07-27-002` as an active Amy obligation.
- Regression test covers a baseline contact with email-only identity plus raw SMS/call handle requiring alias/phone resolution.

## Current Workaround

Codex created the explicit loop:

- `L-2026-07-27-002 — Amy Spytko — Send Amy the integration contact answer Todd owes her.`

This keeps the obligation visible, but it does not fix the underlying SMS/call identity mapping problem.

## Resolution — 2026-07-27

**Root cause confirmed:** identity-resolution failure, per suspected cause #1. `rb_core.interaction_overlay()` matches Messages/Calls handles to baseline contacts by exact normalized phone (or email for iMessage-via-email handles). Amy's baseline `phone` was `null`, so her real handle (`+13153278603`) could never resolve — it sat in `unmatched_recurring_handles` (40 events, Phone+iMessage, last seen 2026-07-24) the whole time. The rest of the pipeline (`interaction_overlay.py` → `relationship_health_aggregate.py` → `relationship_signals.py` → `daily_brief.py`) was working correctly; it had nothing to key off of. Confirmed the gap was already self-detected: `contact_gaps` in `daily_brief.json` had Amy flagged `"missing": ["phone"]` before this fix.

No raw source in the repo (contacts exports, CRM/LinkedIn/Google Takeout deltas, calendar snapshots) contained Amy's phone number, so it could not be resolved automatically — algorithmic guessing from bare phone handles (~2,100 unmatched events/30d, no attached names) was rejected as unsafe (risk of misattributing someone else's texts/calls to Amy). Todd supplied the number directly: `315-327-8603`.

**Fix applied (no code changes required — existing mutation path used as designed):**
1. `mutations.py contact-update --id amy-spytko --phone "315-327-8603"` — audited write, snapshot + validate, matches required-fix step 3.
2. `interaction_overlay.py --cache` — regenerated; Amy now resolves with 22/15 messages, 2 outbound + 1 missed call, last interaction 2026-07-24.
3. `mutations.py touch --id amy-spytko --date 2026-07-24 --source apple_interaction_overlay` — projected verified `last_touch` from the overlay's own proposed update.
4. `relationship_health_aggregate.py --apply` — `communication_frequency_90d` moved from `0.0` → `39.0`; `drr_score` 96.8.
5. `relationship_signals.py --hours 168` — now emits a `direct_interaction` signal for `amy-spytko` (strategic_relevance: high, includes the missed-call recommendation).
6. `daily_brief.py --cache` — Amy moved out of `crossings` (overdue) and `contact_gaps` (missing phone), into `quiet_zones` (inner-tier, in good standing, 3 days since last touch). `L-2026-07-27-002` continues to surface under `loops/this_week`.

**Regression test added:** [system/tests/test_interaction_overlay_identity.py](tests/test_interaction_overlay_identity.py) — RB-INTOVERLAY-001/002/003. Covers exactly the acceptance-criteria scenario: an email-only baseline contact with a recurring raw SMS/call handle that (1) stays unmatched with no phone on file, (2) resolves correctly once a phone is merged, and (3) matches across differing phone formatting (dashed vs. E.164).

**Acceptance criteria:** all met. See fix steps above for evidence per item.
