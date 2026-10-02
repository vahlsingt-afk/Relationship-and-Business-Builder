---
id: P-010
title: Calendar + email overlay
script: system/scripts/calendar_overlay.py + system/scripts/email_overlay.py
cache: (computed each daily-brief run; not cached separately by default)
reads:
  - system/inbox/accounts.yaml
  - system/inbox/calendar.*.json
  - system/inbox/email.*.json
  - system/baseline_index.json
  - system/active_threads.yaml
writes: []
inputs:
  - name: date
    description: ISO date for 'today' (calendar bucket boundaries).
    required: false
trigger: every daily brief run; on-demand when operator asks about meetings or inbox
---

# P-010 — Calendar + email overlay

## Purpose

Pull the operator's calendar and email out of static prose and into the daily brief as structured signal. The brief no longer guesses what's on the calendar — it knows.

## Why this exists

Until this protocol, RB only saw what the operator typed into `loop_ledger.md` or `baseline_index.json` by hand. Real chief-of-staff work needs ambient awareness of meetings (who is on the calendar, are they in the graph, do they touch an active thread) and email (who is reaching out, is it relevant, is anyone hitting an active-thread company even if not yet in baseline).

A concrete example: on 2026-05-16 the inbox contained a video-interview booking from `cj77746@globalpayments.com` for Monday 5/18 with hiring manager Ryan Hildebrand. Neither Christian nor that interview existed in any file. The overlay surfaced both because the sender's domain matched the `Global Payments Inc.` company in the active `T-2026-05-genius-global-payments` thread.

## Source-agnostic design

The overlay scripts read from `system/inbox/*.json`. **They don't care which fetcher produced those files.** Today the Cowork session uses MCP connectors and `fetch_via_session.py`. In production, `fetch_google.py` runs against the Google APIs directly. Both write the same normalized shape.

See `system/inbox/README.md` for the file shapes.

## Multi-account

`system/inbox/accounts.yaml` lists every identity feeding the brief — work calendar, personal calendar, work Gmail, personal Gmail, board email, etc. The overlay aggregates across all enabled accounts, dedupes by event/thread id, and tags each surfaced row with its `account_id` so the brief shows which calendar a meeting lives on.

The combined `email` set across accounts is the operator's "self" set — used to skip self-attendees and self-sent threads.

Adding a new account is two lines of YAML + one fetcher invocation; no code changes.

## Source readiness and empty-result discipline

Calendar and email overlays must report expected-vs-observed source coverage before the assistant interprets an empty result.

Rules:

- Do not say "no meetings" from an empty default-calendar lookup unless every enabled calendar feed in `accounts.yaml` is present and fresh enough for the decision.
- If an enabled calendar/email account is missing, stale, or not observed, say the result is under-instrumented and name the missing account(s).
- Search all observed accounts first, then recommend connector onboarding only for accounts that are expected but not currently captured.
- Business/work calendars have special weight. A missing work calendar should be treated as a meeting-prep blocker unless the user explicitly says to ignore it.
- The API exposes this through `GET /source_readiness?feed=calendar`; `GET /calendar_overlay` also embeds the same `source_readiness` block.

Canonical empty-result wording:

```text
No meetings found in the currently observed calendars.
Calendar source coverage is incomplete: <missing accounts>.
I should not treat the afternoon as clear until those feeds are connected,
refreshed, or explicitly skipped.
```

## What gets surfaced

### Calendar

- Today / tomorrow / this-week events with timestamps and titles.
- Per attendee: a baseline match (with `signal_class`, `rc_tier`, `id`) or an explicit "not in baseline" flag.
- Event-level active-thread match: if the title, description, or attendee domain hits a company named in an open active thread, the event is tagged.
- A roll-up of "attendees in the week who aren't in baseline" — the recommendation surface for new LKIs.

### Email

- Threads where the sender's email matches a baseline contact.
- Threads where the sender's domain or the subject hits an active-thread company — even if the sender isn't in baseline yet. These are surfaced as **active-thread company hits** with a clear "promote this sender to LKI" implication.
- Threads from boundary noise (newsletters, no-reply, jobalerts) are counted but not shown.
- A roll-up of "senders not in baseline, ranked by whether they hit an active thread" — the second recommendation surface.

### Deleted and junk email

RB should scan deleted/trash and junk/spam folders as recovery surfaces when the connector/fetcher supports them. These folders are lower-trust and higher-noise than the inbox, but they can contain important relationship or opportunity signals:

- a known RC/LKI/LMI contact whose email was deleted immediately;
- an active-thread company email misfiled as spam;
- a recruiting, advisory, scheduling, or intro email that should not be in junk;
- a false-positive spam classification for someone already in the graph.

Rules:

- Preserve the source mailbox/labels on every thread (`INBOX`, `TRASH`, `SPAM`, `JUNK`, etc.).
- Do not treat deleted/junk presence as negative relationship evidence by itself; the user may delete aggressively or a provider may misclassify mail.
- Surface only high-signal deleted/junk items, with a clear `mailbox_warning`.
- Recommended actions should be recovery-oriented: `review`, `move out of junk`, `restore`, `reply`, `monitor`, or `ignore`.
- Suppress ordinary junk, bulk marketing, phishing-like solicitations, and low-context vendor noise unless tied to a known relationship or active thread.
- Never auto-restore, auto-unspam, or reply from these folders without explicit user confirmation.

## Operations

```bash
# Run the overlays standalone:
python3 system/scripts/calendar_overlay.py --date 2026-05-15
python3 system/scripts/email_overlay.py

# Or via MCP (rb.calendar_overlay, rb.email_overlay) / HTTP (GET /calendar_overlay, /email_overlay)
# Or as a side effect of the daily brief — overlays appear in today.md automatically.
```

To refresh source data, run a fetcher:

```bash
# Cowork session path — the model calls the calendar/gmail MCP tools and pipes raw JSON in:
python3 system/scripts/fetch_via_session.py calendar --in /tmp/raw_calendar.json
python3 system/scripts/fetch_via_session.py email --in /tmp/raw_threads.json

# Production path — direct Google API (requires GCP setup):
python3 system/scripts/fetch_google.py both --days 7
```

Fetcher requirement: email fetchers should include inbox, sent, deleted/trash, and junk/spam search surfaces when permissions allow, while preserving original labels/mailbox in the normalized thread record.

## Freshness

Both JSON payloads carry a `fetched_at` timestamp. The overlay sets `stale: true` when the data is older than 6 hours; the daily brief shows a stale tag but still uses the data. The threshold is in `rb_core.INBOX_FRESH_SECONDS`.

## Failure modes

- **No inbox files present.** The daily brief renders without calendar/email sections. No error.
- **Expected calendar feed missing.** The overlay reports `source_readiness.status != ready`; the daily brief should flag meeting prep as under-instrumented instead of concluding the schedule is empty.
- **Stale data.** Surfaced with a tag; the operator decides whether to refresh.
- **Unmatched sender that's actually important.** The overlay flags this as an "active-thread company hit" if the sender's domain matches a company in any open thread. If it doesn't match a thread AND isn't in baseline, the brief still lists the sender in `senders_not_in_baseline` so it can be reviewed.
- **Important email in trash/junk.** Surfaced only when it matches a known relationship, active thread, job/advisory opportunity, scheduling signal, or other high-signal pattern. The brief should say which folder it was found in and recommend review/recovery rather than assuming intent.

## Privacy

`system/inbox/*.json` contains the operator's calendar and email. It is **always** git-ignored. Treat as the most sensitive content in the repo. In production, encrypt at rest and scope OAuth tokens to the minimum needed read-only scopes.

## Voice

The overlay's narrative is structural, not interpretive. "Two meetings this week. One attendee (Meghan Winn) is not in baseline." The session layer is what interprets — "this is a Tom Altman intro, worth promoting Meghan to LKI as you take the meeting."
