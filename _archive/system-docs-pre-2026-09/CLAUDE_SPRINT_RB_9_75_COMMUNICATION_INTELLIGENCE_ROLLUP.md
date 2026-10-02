# RB 9.75: Communication Intelligence Rollup — RB-DEFECT-043 #6

**Status:** Implemented (2026-06-14)
**Source:** RB-DEFECT-043 Defect #6 ("Communication Intelligence Is Missing"), the
remaining item from RB 9.74's triage of RB-DEFECT-043.

## Defect #6 recap

> Email, calendar, SMS, and communication activity are not being incorporated
> consistently. Expected: a dedicated "Communication Intelligence" section covering
> emails received, emails requiring response, calendar changes, interview activity,
> open commitments, and important follow-ups.

## What was found

Most of the underlying data already exists as separate sections:
- `email_intelligence_harvest` — newsletter/inbox scan, sent-loop verification
  (`sent_outbound_awaiting_response`, `sent_response_after_outbound` telemetry).
- `sent_followups_awaiting_response` — per-thread overdue/waiting follow-ups.
- `meeting_prep_and_deliverables` — calendar events needing prep ("Prep needed: ...").
- `loops_and_obligations` — open loops with `extras.days_overdue`.

What's genuinely missing — **calendar-change detection** (new/cancelled/moved
meetings since yesterday) and **interview-activity detection** — both require a
day-over-day calendar snapshot to diff against. RB does not currently persist one
(`calendar_overlay.py` fetches live each run; no `system/_snapshots/calendar.*`
exists). Building that snapshot/diff mechanism is net-new infrastructure, out of
scope for this slice — **deferred**, flagged explicitly in the new section's
rendering rule so the GPT doesn't imply it was checked.

## What was implemented

`system/scripts/daily_brief.py`:
- New section `communication_intelligence` (added to the `sections` dict near
  `email_intelligence_harvest`).
- New aggregation block (after the loop-proposal block, ~line 12478): reads
  `passive_email_intelligence.telemetry` (emails awaiting response / responded) and
  counts items already computed in `sent_followups_awaiting_response`,
  `meeting_prep_and_deliverables`, and `loops_and_obligations` (overdue vs. due
  today). Emits one rollup item: "Communication Intelligence — Today's Picture" with
  a single counts line and pointers to the underlying sections.
- Added `communication_intelligence` to `brief_display_order` Bucket 4
  (Personal/Relationship Intelligence), immediately after `email_intelligence_harvest`.
- New rendering rule: render as "📬 Communication Intelligence", show the counts row,
  point to underlying sections for detail, and explicitly note calendar-change and
  interview-activity detection are not yet covered.

## Deferred

- Calendar-change delta (new/cancelled/moved meetings) — needs a persisted
  day-over-day calendar snapshot + diff, analogous to `baseline_index.json`
  snapshotting for LinkedIn (RB-DEFECT-041/RB 9.73 pattern). Net-new infrastructure.
- Interview-activity detection — would build on the same calendar-diff mechanism
  plus keyword/role classification on event titles/attendees.
- The full RB-DEFECT-043 Layer 1/Layer 2 document split remains the larger
  architectural item, still deferred to RB 9.76+.
