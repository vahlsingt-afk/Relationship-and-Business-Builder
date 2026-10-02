---
id: P-024
title: Relationship signals from the last 24 hours
script: system/scripts/relationship_signals.py
cache: system/.cache/relationship_signals.json
reads:
  - system/inbox/email.*.json
  - system/inbox/calendar.*.json
  - system/inbox/messages.json
  - system/inbox/calls.json
  - system/inbox/social.feed.json
  - system/inbox/social.own_posts.json
  - system/inbox/social.engagement.json
  - system/baseline_index.json
  - system/active_threads.yaml
writes: []
inputs:
  - name: date
    description: ISO anchor date used for calendar bucketing.
    required: false
  - name: hours
    description: Look-back window in hours. Default 24.
    required: false
trigger: |
  Every daily-brief run (the build_report dict embeds the signals report).
  On-demand via `getRelationshipSignals` ("What changed since yesterday?"
  type questions).
---

# P-024 — Relationship signals from the last 24 hours

## Purpose

Promote the most relationally relevant LinkedIn/social, email, text,
phone-call, and calendar events of the last N hours into the daily brief
as a first-class CoS section, with reasoning, evidence, and named
recommended actions.

This implements Phase 9 of `OPERATIONALIZATION.md` and the highest-priority
next-build sequence in `CLAUDE_HANDOFF.md`. It is the response to defect
`D-2026-05-18-003` (daily brief is news-centric, not relationship-centric)
and the four related defects in
`system/test_traces/2026-05-18-rb9-daily-brief-cos-vs-news-test.md`.

## What this is NOT

- Inbox management. RB does not summarize every email.
- Trash/junk triage. RB scans deleted and junk folders only for high-signal
  recovery candidates.
- Industry news commentary. Industry intelligence is subordinate to
  relationship/action intelligence.
- A separate notification stream. The signal layer lives inside the daily
  brief and inside the `getRelationshipSignals` action.

## What this IS

For each relationally relevant event in the window:

- `contact_id`, `name`, `source`, `event_at`
- `signal_type` — one of:
  - `inbound_warmth`
  - `active_thread_movement`
  - `active_thread_company_signal`
  - `job_opportunity`
  - `intro_referral`
  - `collaboration_opportunity`
  - `scheduling_opening`
  - `meeting_completed`
  - `meeting_today_or_imminent`
  - `active_thread_calendar_event`
  - `direct_interaction`
  - `known_contact_post`
  - `active_thread_social_signal`
  - `engagement_on_own_post`
- `signal_strength` (0–1)
- `strategic_relevance` (high / medium / low)
- `recommended_action` (concrete, named)
- `reasoning` (one or two sentences)
- `evidence` (list of source rows with thread/event/post IDs)
- `confidence` (0–1)
- `active_thread_ids`
- `reconciliation_prompt` (only when the signal is uncertain enough that RB
  should ask rather than guess)
- `mailbox` / `labels` when the signal came from email, especially if it came
  from deleted/trash or junk/spam.

## Deleted and junk recovery signals

Email signals may come from inbox, sent, deleted/trash, or junk/spam folders.
Deleted and junk folders are not normal signal surfaces; they are recovery
surfaces.

Surface a deleted/junk email only when at least one of these is true:

- sender matches an RC/LKI/LMI contact;
- sender domain or subject matches an active thread;
- message appears to contain scheduling, intro, recruiting, advisory,
  consulting, partnership, customer, or investor opportunity signal;
- the email is from a known company/person and appears misclassified by the
  mailbox provider;
- the user has specifically asked RB to watch for that sender/domain.

Every surfaced deleted/junk signal must include:

- source mailbox/labels;
- why RB thinks it may matter;
- confidence;
- recommended recovery action (`review`, `restore`, `move_out_of_junk`,
  `reply`, `monitor`, or `ignore`);
- whether a loop or relationship update is recommended;
- a clear note that folder placement alone is not relationship evidence.

Do not surface ordinary spam, phishing-like messages, bulk marketing, generic
newsletters, or low-context vendor solicitations unless they hit an active
thread or known high-value relationship.

## Reconciliation queue

When RB has signal but not enough certainty to mutate state, it produces a
narrow question with a recommended_mutation and a `safe_to_write` flag.
Examples:

- "Direct interaction with Jeff Wayman on 2026-05-12 is newer than recorded
  last_touch (2026-04-30). Should RB update last_touch?" — safe_to_write:
  true; mutation: `touchContact`.
- "Email from `someone@toasttab.com` (subject `Operations conversation`)
  hits active-thread companies [Toast]. Add to baseline and link to thread
  T-2026-05-toast-bob-gibson?" — safe_to_write: false.
- "Event `Genius / Global Payments interview` has 1 attendee not in
  baseline. Promote?" — safe_to_write: false.

Resolving these is how RB improves the graph without guessing. The Custom
GPT surfaces them inside the brief as yes/no/either-or questions.

## Stale-source warnings

Each source has a freshness threshold:

| Source           | Threshold |
|------------------|----------:|
| email            | 24h       |
| calendar         | 24h       |
| messages         | 12h       |
| calls            | 12h       |
| social           | 48h       |
| social_outbound  | 48h       |

If a source's `fetched_at` is older than its threshold the report includes a
`stale_sources` entry with the age, threshold, and a recommendation (run
the fetcher or paste high-signal items manually). The brief makes this
visible so the operator knows the signal layer may be incomplete.

## What gets ignored

The `what_to_ignore` block captures items RB looked at and chose not to
surface:

- noise senders suppressed by domain pattern (newsletters, no-reply, etc.)
- deleted/junk items scanned and suppressed as ordinary spam, bulk marketing,
  phishing-like, or unrelated vendor noise
- self-sent threads where Todd is the most recent sender
- baseline-sender threads whose subjects match the noise subject pattern
  (out-of-office, delivery status, etc.)

Surfacing the suppression on purpose (per Tenet 13) lets the operator
audit it instead of trusting silence.

## Sorting

Signals are sorted by `(strategic_relevance, -signal_strength, -event_at)`
so the brief leads with high-impact items. Reconciliation prompts are
sorted by urgency. Stale-source warnings list every stale source — none
are dropped.

## Worked examples

These four cases drove the design and are the canonical correctness tests:

1. **Ish Singh / Maho** (2026-05-18) — inbound email replying to Todd's
   2026-05-01 "Next Steps" thread, agreeing with the framing, inviting a
   deeper dive on Maho. Expected classification: `inbound_warmth` (high
   if active-thread linked) with action "Reply with proposed time and
   leading strategic framing; treat as a relationship-warming + opportunity
   exchange."
2. **Christian Jackson / Global Payments** — interview scheduling/coordination
   email. Expected classification: `job_opportunity`, high relevance,
   active thread `T-2026-05-genius-global-payments`.
3. **Jeff Wayman** — direct-interaction volume materially newer than the
   recorded `last_touch`. Expected: `direct_interaction` signal plus a
   `safe_to_write` reconciliation prompt offering the `touchContact`
   mutation.
4. **Bob Gibson / Toast** — continuity of the Toast strategic thread.
   Expected: `active_thread_movement` if there is a touch in the window,
   `active_thread_company_signal` if a non-baseline sender at Toast
   surfaces.

## Integration

- `daily_brief.py` calls `relationship_signals.build_report` and renders
  the result as the CoS section at the top of `today.md`.
- `refresh_all.py` runs `relationship_signals.py --cache --json` before
  `daily_brief.py` so the cache is warm.
- `GET /relationship_signals` exposes the same dict to the Custom GPT.
- `getRelationshipSignals` is the first action the Custom GPT calls for
  "what changed since yesterday?" style questions.
