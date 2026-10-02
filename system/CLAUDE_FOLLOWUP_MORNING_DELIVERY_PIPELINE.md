# Claude Follow-Up — Morning Relationship Scan + Brief Delivery Pipeline

**Date:** 2026-05-19  
**Prepared by:** Codex + Todd after live RB Action testing  
**Priority:** High  

## Product Intent

RB should feel like waking up to the newspaper on the doorstep.

Product philosophy: restaurants and operators do not need another dashboard. RB should identify what matters, recommend the next move, and make the user better.

Each morning, before the user asks anything, RB should:

1. refresh the relevant source feeds,
2. run the relationship signal scan,
3. generate the canonical CoS daily brief,
4. publish to the RB operational layer,
5. email the user a short executive summary with a CTA into that operational layer.

The user should not need to manually ask RB to scan LinkedIn, email, texts, calls, calendar, HubSpot, or social before the daily brief becomes useful.

## Current State

The relationship signal scan is implemented and callable:

```bash
python3 system/scripts/relationship_signals.py --json --cache
```

The API endpoint is live:

```text
GET /relationship_signals
```

The Custom GPT Action can call:

```text
getRelationshipSignals
```

But as of the May 19 test, the scan ran against stale source feeds:

```text
email:    stale, fetched_at 2026-05-16T17:30:37+00:00, ~66.9h old
calendar: stale, fetched_at 2026-05-16T17:30:37+00:00, ~66.9h old
messages: stale, fetched_at 2026-05-18T12:34:31+00:00, ~23.8h old, threshold 12h
calls:    stale, fetched_at 2026-05-18T12:34:31+00:00, ~23.8h old, threshold 12h
social:   stale, fetched_at 2026-05-17T02:29:52+00:00, ~57.9h old, threshold 48h
social_outbound: fresh enough, ~44.3h old, threshold 48h
```

So the answer to "did the daily relationship scan take place?" is:

```text
The scan ran, but it did not have fresh upstream data.
```

That is not good enough for the finished product.

## Product Requirement

The scheduled morning task must not only regenerate `today.md`.

It must run the full morning pipeline:

```text
source refresh -> relationship scan -> canonical daily brief -> publish to RB operational layer -> email CTA
```

## Proposed Pipeline

Create a single orchestrator:

```text
system/scripts/morning_brief_pipeline.py
```

Suggested CLI:

```bash
python3 system/scripts/morning_brief_pipeline.py --date YYYY-MM-DD
python3 system/scripts/morning_brief_pipeline.py --date YYYY-MM-DD --send-email
python3 system/scripts/morning_brief_pipeline.py --date YYYY-MM-DD --skip-source-refresh
python3 system/scripts/morning_brief_pipeline.py --date YYYY-MM-DD --dry-run
```

The orchestrator should run in this order:

1. Source refresh
   - email
   - calendar
   - messages
   - calls
   - LinkedIn/social public posts and topic feeds if supported
   - LinkedIn/social own-post engagement, comments, and reactions if supported
   - LinkedIn messaging/interactions from export, browser capture, or connector when permissioned
   - HubSpot CRM/opportunity data if connected
   - transcript/chat import when available

2. Signal generation
   - `relationship_signals.py --cache`
   - classify calendar events for meeting prep, deliverable risk, active-thread ties, and prep-loop candidates

3. Daily brief generation
   - `daily_brief.py --cache`
   - produce canonical CoS structure by default
   - open with Morning Command Center after Daily Prep Summary
   - include Meeting Prep & Deliverables before generic task/loop review
   - attach action affordances to actionable recommendations

4. Publication
   - write stable artifacts:

```text
system/published/daily/YYYY-MM-DD/index.md
system/published/daily/YYYY-MM-DD/index.html
system/published/daily/YYYY-MM-DD/brief.json
```

5. Email delivery
   - send one short morning email:

```text
Subject: RB Daily Brief — Tuesday, May 19, 2026

Top 3:
1. ...
2. ...
3. ...

Signal health:
Email stale/fresh, calendar stale/fresh, messages stale/fresh, calls stale/fresh, social stale/fresh.

Open RB operational brief:
https://...
```

## Source Refresh Reality

Do not pretend all feeds can be fully automated today.

Classify each source:

```text
email: automate through Gmail/OAuth when connector/export is ready; manual paste fallback
calendar: automate through Google Calendar/OAuth when connector/export is ready
messages: local macOS fetcher; requires Full Disk Access and local scheduled job
calls: local macOS fetcher; requires Full Disk Access and local scheduled job
social/LinkedIn: scan public posts, own-post engagement, comments/reactions, and messaging/interactions where permissioned data is available; likely manual/semi-automated for now unless browser/export/connector flow is built
HubSpot: connector/API-driven when available; include only relevant CRM movement
transcripts/chats: local folder watcher/importer
```

If a source cannot refresh automatically, the morning email should say so plainly:

```text
Email feed stale — RB did not inspect new inbound email. Paste high-signal email or run refresh.
```

## HubSpot / CRM Relevance Filter

HubSpot belongs in the morning update only when it changes the relationship or opportunity picture.

Do not include generic CRM counts, raw task lists, or stale pipeline noise.

Relevant HubSpot signals include:

- deal stage movement
- new or reopened deals
- close date changes
- overdue high-value tasks
- notes or activity from known RC/LKI/LMI contacts
- new contacts or companies matching active threads
- companies tied to job search, consulting, restaurant-tech, payments, hospitality, or current strategic targets
- stalled opportunities where silence itself matters
- mismatch between HubSpot state and RB state

Each surfaced HubSpot item should include:

- object type: contact, company, deal, ticket, task, note
- event_at and captured_at
- matched RB contact/company/thread if any
- why it matters
- recommended action
- confidence
- whether it should update RB, open/close a loop, or simply be monitored

If HubSpot is connected but nothing relevant changed, the brief may say:

```text
HubSpot: no relationship-relevant CRM movement detected.
```

If HubSpot is unavailable or stale, include it in stale-source warnings only if the user expects CRM visibility that day.

## Onboarding Requirement: Source Setup + Watch Folders

RB onboarding must include a source-setup step based on the user's actual tool stack.

The system should ask which tools the user uses, then configure available connectors, exports, and watch folders.

Example onboarding questions:

```text
Which calendar/email systems do you use? Gmail, Google Calendar, Outlook, Microsoft 365, Apple Calendar?
Do you use Apple Messages/iMessage/SMS on this Mac?
Do you want RB to read Apple call history for relationship-touch detection?
Which notetakers do you use? Fathom, Zoom native transcript, Otter, Fireflies, Teams, other?
Where do those tools save transcripts, recordings, chat logs, or exports?
Do you use LinkedIn exports, Sales Navigator exports, or manual LinkedIn copy/paste?
Do you want RB to watch a general "RB Inbox" folder for dropped files?
```

Expected watch-folder config:

```text
system/source_watch.yaml
```

Possible entries:

```yaml
watch_folders:
  fathom:
    enabled: true
    path: /Users/<user>/Downloads/Fathom
    file_types: [.txt, .docx, .pdf]
  zoom:
    enabled: true
    path: /Users/<user>/Documents/Zoom
    file_types: [.txt, .vtt, .csv]
  rb_inbox:
    enabled: true
    path: /Users/<user>/Documents/RB Inbox
    file_types: [.txt, .md, .pdf, .docx, .csv, .json]
```

The morning pipeline should read this config before source refresh and ingest newly arrived files through the appropriate parser.

This should be explicit in onboarding:

```text
RB can only automatically process sources you connect or folders you ask it to watch.
Everything else remains manual upload/copy-paste.
```

## Scheduling

Current docs now target `rb-daily-briefing` at 05:00 America/Chicago daily, but the implementation should be verified.

Future target:

```text
04:45 CT — refresh source feeds
04:55 CT — run relationship_signals
05:00 CT — generate canonical daily brief
05:05 CT — publish brief
05:10 CT — email operational-layer CTA to user
```

For local Mac scheduling, use LaunchAgent.

For hosted/production scheduling, use cron/GitHub Actions/server scheduler/cloud job.

## API Additions

Consider adding read endpoints:

```text
GET /published_briefs/recent
GET /published_briefs/{date}
```

Consider adding a controlled operator-only endpoint later:

```text
POST /morning_brief/run
```

Do not expose source refresh writes to the Custom GPT until the auth/security model is clear.

## Email Delivery

This should not dump the full brief into email.

Email should contain:

- date
- top 3 priorities
- command-center summary
- meeting prep / deliverable risk when material
- source freshness state
- one or two urgent actions
- link/button into the RB operational layer daily brief surface

Avoid noisy long emails.

The point is a doorstep newspaper, not an inbox wall of text.

### Transport options (publish.py)

The composer lives in `system/scripts/publish.py`. The actual send is host-side. Three viable transports, in order of operator effort:

**1. `.eml` drop into the macOS desktop client (lowest effort, no OAuth).**

```bash
export RB_EML_DROP_DIR="$HOME/Library/Mail/V10/Drafts"   # or any folder Mail watches
python3 system/scripts/publish.py --write --send --confirm
```

Drops `rb-daily-brief-YYYY-MM-DD.eml` into the folder. Mail.app picks it up on import or restart. Good for development; not great for true unattended delivery.

**2. SMTP via Gmail app password (mid effort, no OAuth refresh).**

```bash
export RB_SMTP_HOST="smtp.gmail.com"
export RB_SMTP_PORT="587"
export RB_SMTP_USER="vahlsingt@gmail.com"
export RB_SMTP_PASS="<16-char-app-password>"   # Google account → Security → App passwords
python3 system/scripts/publish.py --write --send --confirm
```

Direct SMTP with STARTTLS. Reliable for daily LaunchAgent firing. The app password lives in the LaunchAgent's env, not in the repo.

**3. Gmail OAuth via a service account (highest effort, production-grade).**

Currently unimplemented in `publish.py`. To add: write a small OAuth client mirroring `system/scripts/fetch_google.py`'s pattern, store credentials at `~/.rb/gmail-credentials.json`, and add a `transport=gmail` branch to `send_email`. This is the right destination for V1 hosted deployment.

### Operational-layer URL

The email CTA points at `<RB_PUBLIC_URL_BASE>/daily/latest.html`. Set it either:

```bash
# Persistent: edit settings.json
"operational_layer": { "public_url_base": "https://rb.example.com" }
```

```bash
# Per-run: export the env var
export RB_PUBLIC_URL_BASE="https://rb.example.com"
```

While `public_url_base` is null AND `RB_PUBLIC_URL_BASE` is unset, `publish.compose_email` reports `cta_status: "pending_destination"` and the email body explicitly says the destination is unconfigured — it refuses to fabricate a URL. That's Tenet 1 ("no guessing") applied to the delivery layer.

## Acceptance Criteria

On any normal morning:

1. RB refreshes all available source feeds or reports which ones failed/stayed stale.
2. RB runs `relationship_signals.py`.
3. RB runs `daily_brief.py`.
4. RB publishes a stable daily brief artifact.
5. RB surfaces meeting prep / deliverable candidates and offers to create prep briefs for review.
6. RB exposes action affordances for recommendations: create loop, draft next action, create meeting prep, create waiting loop, defer, mark done, mark irrelevant, or monitor.
7. RB offers an end-of-day closeout for closed/slipped/waiting/auto-resolved/roll-forward state.
8. RB sends the user an email with a link/button into the RB operational layer.
9. The brief clearly distinguishes:
   - fresh signals
   - stale-source-limited conclusions
   - stored thread/loop state
   - manual/user-provided context
10. If no fresh signals exist, RB says whether that conclusion is trustworthy.

## Why This Matters

RB is not just a chat tool.

RB should become the measured Chief of Staff that has already read yesterday's relationship environment before the user starts the day.

The user should wake up to:

```text
what changed,
why it matters,
who it affects,
what to do,
what to ignore,
and where RB needs clarification.
```
