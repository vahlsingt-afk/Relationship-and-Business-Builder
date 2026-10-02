# `system/inbox/` — source-agnostic calendar + email feed

This directory holds normalized JSON snapshots of the operator's calendar and email. The daily brief reads from here; the *fetcher* that produces these files is a separate, swappable concern.

## Why source-agnostic

Today this Cowork project has Google Calendar and Gmail MCP connectors authenticated for the founder. Tomorrow the productized version will use its own Google API client per tenant. By landing both into the **same JSON shape** in this directory, the compute layer (daily brief, conversation prep, intro engine) doesn't care which fetcher ran.

## Files

| File | Produced by | Shape |
|---|---|---|
| `accounts.yaml` | hand-maintained | List of identities (work, personal, others) — see below. |
| `calendar.<account_id>.json` | any calendar fetcher | `{ "fetched_at": ISO, "account_id": "...", "events": [...] }` |
| `email.<account_id>.json` | any email fetcher | `{ "fetched_at": ISO, "account_id": "...", "threads": [...] }` |
| `social.feed.json` | any social fetcher | `{ "fetched_at": ISO, "source": "...", "posts": [...] }` |
| `linkedin.session_captures.jsonl` | `linkedin_session_reader.py` | Ephemeral browser-session LinkedIn captures with per-record `expires_at`; materialized into `social.feed.json`, then purged. |
| `social.own_posts.json` | manual paste or LinkedIn analytics fetcher | `{ "fetched_at": ISO, "posts": [...] }` — posts *you* published. |
| `social.engagement.json` | manual paste or LinkedIn analytics fetcher | `{ "fetched_at": ISO, "events": [...] }` — likes/comments/shares from your contacts on your posts. |
| `messages.json` | `fetch_apple_messages.py` (or any fetcher) | `{ "fetched_at": ISO, "source": "...", "events": [...] }` — text/iMessage events with handle + direction + timestamp. |
| `calls.json` | `fetch_apple_calls.py` (or any fetcher) | `{ "fetched_at": ISO, "source": "...", "events": [...] }` — phone/FaceTime call events. |
| `calendar.json` *(legacy)* | early single-account fetcher | Same shape, read for back-compat only. New code writes per-account. |
| `email.json` *(legacy)* | early single-account fetcher | Same shape, read for back-compat only. |

## Social post shape

```json
{
  "id": "stable-hash-or-linkedin-post-id",
  "author": {
    "name": "Bruce Sellnow",
    "linkedin_url": "https://www.linkedin.com/in/bruce-sellnow",
    "headline": "McDonald's franchise operations"
  },
  "posted_at": "2026-05-14T09:13:00-05:00",
  "text": "Full post text. Image alt text + caption if applicable.",
  "post_url": "https://www.linkedin.com/posts/...",
  "platform": "linkedin",
  "engagement": {
    "likes": 47,
    "comments": 12,
    "shares": 3
  },
  "captured_at": "2026-05-16T18:00:00Z",
  "captured_via": "claude_in_chrome"
}
```

Author matching against `baseline_index.json` is attempted in this order:
1. Exact `linkedin_url` match.
2. Case-insensitive `name` match.
3. Fallback: the post is surfaced under "authors not in baseline."

## LinkedIn browser-session captures

`linkedin.session_captures.jsonl` is a short-lived buffer for posts captured from Todd's own logged-in LinkedIn browser session. RB never stores LinkedIn credentials. Default retention is 36 hours: enough for tomorrow morning's brief, then the reader purges expired posts from both the JSONL buffer and the materialized `social.feed.json`.

```bash
python3 system/scripts/linkedin_session_reader.py --capture-js
python3 system/scripts/linkedin_session_reader.py --ingest --in /tmp/linkedin_posts.json
python3 system/scripts/linkedin_session_reader.py --purge
```

## Your own posts (outbound)

```json
{
  "post_id": "stable-id",
  "posted_at": "2026-05-12T08:00:00-05:00",
  "platform": "linkedin",
  "text": "Full text of your post.",
  "topics": ["restaurant-tech", "operator-pain"],
  "post_url": "https://www.linkedin.com/posts/todd-vahlsing-...",
  "engagement_totals": {
    "likes": 73,
    "comments": 18,
    "shares": 4,
    "impressions": 2400
  }
}
```

`topics` is operator-tagged free-text. Used by the post-recommendation engine to find topics that have engaged specific contacts before.

## Engagement events

```json
{
  "post_id": "matches social.own_posts.json post_id",
  "type": "like" | "comment" | "share",
  "engager": {
    "name": "Bruce Sellnow",
    "linkedin_url": "https://www.linkedin.com/in/bruce-sellnow"
  },
  "at": "2026-05-12T11:23:00-05:00",
  "comment_text": "..."  // only for comments
}
```

Engager matching uses the same URL → name fallback chain as the inbound feed.

## Messages and calls (direct-interaction signal)

```json
// messages.json event
{
  "id": "stable-id",
  "handle": "+15551234567"   // phone or email
  "service": "iMessage" | "SMS",
  "direction": "outbound" | "inbound",
  "at": "2026-05-15T11:30:00Z",
  "has_text": true,
  "snippet": null            // null by default — see Privacy
}

// calls.json event
{
  "id": "stable-id",
  "handle": "+15551234567",
  "service": "Phone" | "FaceTime",
  "direction": "outbound" | "inbound" | "missed",
  "at": "2026-05-15T13:00:00Z",
  "duration_seconds": 1740
}
```

Handle matching against baseline goes: normalized phone → exact email → fallback (surface as "unmatched handle"). Phone normalization strips non-digits then re-prefixes US `+1` when length suggests a US number.

### Privacy posture (important)

`messages.json` and `calls.json` contain the operator's direct communication metadata. Treat as the **most sensitive content in the inbox**.

- Always git-ignored.
- Default: `snippet` is `null`. Message body content stays in the OS SQLite store and never enters this directory. The fetcher records the *event* (who/when/direction), not the *content*.
- Opt-in snippet capture: pass `--include-snippets` to the fetcher only when the operator explicitly wants to enable content-aware matching. Even then, snippets are truncated to 80 chars and never sent to any third-party API.
- Never copy these files out of `system/`.
- In production multi-tenant deployments, encrypt at rest, restrict to the owning operator, and treat all message/call metadata as PII subject to the deletion-on-request flow.

All JSON files are git-ignored. `accounts.yaml` is versioned because the manifest itself is structural, not personal data.

## Multi-account: how it works

You can have any number of accounts — work calendar + personal calendar, work Gmail + personal Gmail, plus things like a board email or a side-project address. Each one lives in `accounts.yaml`:

```yaml
accounts:
  - id: work
    label: "Work — todd@bridgepointops.com"
    email: todd@bridgepointops.com
    role: work
    feeds: [calendar, email]
    enabled: true
  - id: personal
    label: "Personal — vahlsingt@gmail.com"
    email: vahlsingt@gmail.com
    role: personal
    feeds: [email]
    enabled: true
```

The overlays:

1. Read `accounts.yaml`.
2. Aggregate every `calendar.<id>.json` and `email.<id>.json` for enabled accounts.
3. Dedup by event id / thread id (an event invited to both accounts shows once).
4. Tag each row with `account_id` so the brief shows which calendar a meeting is on.
5. Build a `self_emails` set from all account emails — used to skip self-attendees and self-sent threads.

The legacy single-file paths (`calendar.json`, `email.json`) are still read if present, tagged `_legacy`, for back-compat.

## Adding a new account

1. Add a new entry to `accounts.yaml` with a unique `id`.
2. In Cowork, connect a Google Calendar and/or Gmail MCP authenticated to that account's email.
3. From a session, fetch raw JSON via the MCP and pipe to `fetch_via_session.py`:

```bash
python3 system/scripts/fetch_via_session.py calendar \
    --account my-new-account --in raw_calendar.json
python3 system/scripts/fetch_via_session.py email \
    --account my-new-account --in raw_gmail_threads.json
```

4. The next daily brief automatically picks up the new account.

## Calendar event shape

```json
{
  "id": "google-event-id",
  "title": "Meghan Winn and Todd Vahlsing",
  "start": "2026-05-20T15:30:00-05:00",
  "end": "2026-05-20T16:00:00-05:00",
  "location": "https://us05web.zoom.us/...",
  "description": "Virtual Coffee - Let's connect ...",
  "attendees": [
    {"email": "mwinn@rti-inc.com", "response": "accepted", "self": false},
    {"email": "todd@bridgepointops.com", "response": "accepted", "self": true}
  ],
  "organizer_email": "todd@bridgepointops.com",
  "html_link": "https://www.google.com/calendar/event?eid=...",
  "source": "google_calendar"
}
```

## Email thread shape

```json
{
  "thread_id": "19e315118faebd3a",
  "subject": "Donnie Boivin has added you to the Networking from Scratch space",
  "last_message_at": "2026-05-16T15:04:17Z",
  "last_message_from": {"email": "no-reply@notification.circle.so", "name": null},
  "labels": ["INBOX"],
  "mailbox": "inbox",
  "unread": false,
  "snippet": "Hey Todd, Donnie Boivin has added you to the private Networking from Scratch space...",
  "message_count": 1,
  "source": "gmail"
}
```

Email fetchers should preserve the original mailbox or provider labels. Expected normalized `mailbox` values include `inbox`, `sent`, `trash`, `spam`, `junk`, `archive`, or `unknown`. Gmail labels such as `TRASH` and `SPAM` should still be preserved in `labels`.

Deleted/trash and junk/spam records are allowed in this file. They are recovery inputs for RB, not normal briefing content. Downstream overlays should surface them only when they contain likely relationship, scheduling, recruiting, advisory, intro, opportunity, or active-thread signal.

## Fetchers

| Fetcher | Use case | How it's run |
|---|---|---|
| `system/scripts/fetch_via_session.py` | Cowork session populates inbox from the MCP connectors. | A model in this Cowork project calls the calendar/gmail MCP tools, formats the result, and writes the JSON. The script is a thin formatter. |
| `system/scripts/fetch_google.py` | Production — per-tenant OAuth, no AI session needed. | Run on a schedule (cron / scheduled task). Requires GCP project, OAuth client, token refresh. **Scaffolded; not wired to live Google API yet.** |

## Freshness

Each file carries a `fetched_at` timestamp. The daily brief flags overlays older than 6 hours and treats them as stale.

## Privacy

These files contain the operator's calendar and email. Treat as the most sensitive content in the repo:

- Always git-ignored.
- Never copy out of `system/` without a written reason.
- Encrypt at rest in production.
