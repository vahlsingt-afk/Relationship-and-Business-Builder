# LinkedIn Signal Capture — Operator Guide

This guide covers all the ways LinkedIn intelligence enters the RB daily brief. None of these paths log into LinkedIn, store your credentials, or run background scraping. All write operations require explicit `--confirm`.

---

## Which method should I use?

| Goal | Recommended method |
|---|---|
| Own-post engagement (likes, comments) | Official API (Tier A) if configured; browser capture (Tier C) otherwise |
| LinkedIn messages / DM history | Export watcher (Tier B) or direct CSV ingest |
| Connections archive | Export watcher (Tier B) with full LinkedIn ZIP |
| LinkedIn feed context | Browser-session feed capture (Part 2) |

---

## Part 1 — Official LinkedIn API (Own-Post Engagement)

### What it does

Pulls your own post list and per-post engagement (reactions, comments) through the LinkedIn official API, normalizes into `social.own_posts.json` and `social.engagement.json`.

### Configuration

Set these environment variables (never store tokens in git):

```bash
export LINKEDIN_ACCESS_TOKEN="your-token-here"
export LINKEDIN_AUTHOR_URN="urn:li:person:your-id"    # member mode
# OR
export LINKEDIN_ORGANIZATION_URN="urn:li:organization:your-id"  # org/page mode
```

Check status at any time:

```bash
python3 system/scripts/linkedin_own_engagement.py --status
```

### Step-by-step

**Step 1 — Preview the fetch**

```bash
python3 system/scripts/linkedin_own_engagement.py --fetch --dry-run
```

If credentials are not configured, this returns a structured `not_configured` response and does not block delivery.

**Step 2 — Confirm the write**

```bash
python3 system/scripts/linkedin_own_engagement.py --fetch --confirm
```

This merges new posts/events with existing caches (deduplicating by post_id and event hash). Use `--replace` to overwrite rather than merge.

**Step 3 — Verify**

```bash
python3 system/scripts/social_outbound.py --json --cache
python3 system/scripts/refresh_sources.py --social --save-health
```

### When the API is not available

LinkedIn member-profile post analytics and reaction/comment reads are restricted on most developer app tiers. If you see `insufficient_scope` or `not_configured`:

1. The adapter fails safely — delivery is not blocked.
2. Use the browser-session capture (Part 3) or export watcher (Part 2) instead.
3. Check LinkedIn Developer docs for current scope availability: https://developer.linkedin.com/

---

## Part 2 — Export Watcher (Messages + Archive ZIPs)

### What it does

Watches drop locations for LinkedIn export files and routes them to the appropriate ingester:

- **Messages CSV** → `linkedin_messaging.py` (LinkedIn DMs → inbox cache)
- **ZIP with messages.csv** → `linkedin_messaging.py` (same path)
- **Full archive ZIP (with Connections.csv)** → `linkedin_ingest.py` (connections + profile data)

Maintains a hash manifest so files are never reprocessed unless you use `--force`.

### Drop locations

Place your LinkedIn export files here:

```
system/inbox/linkedin_exports/      ← directory (ZIPs and CSVs)
system/inbox/linkedin_messages_export.csv   ← convenience single-file path
```

### Step-by-step

**Step 1 — Request the export from LinkedIn**

1. Log into LinkedIn → Settings → Data Privacy → Get a copy of your data.
2. Select **Messages** for DMs only, or **Download larger data archive** for connections + full history.
3. LinkedIn emails a download link (minutes for messages, hours for full archive).

**Step 2 — Drop the file**

Place the downloaded ZIP or extracted `messages.csv` into one of the drop locations above.

**Step 3 — Scan**

```bash
python3 system/scripts/linkedin_export_watcher.py --scan
```

Shows each file detected, its classification, and whether it has already been processed.

**Step 4 — Ingest**

```bash
# Preview (no writes)
python3 system/scripts/linkedin_export_watcher.py --ingest-new --dry-run

# Confirm write
python3 system/scripts/linkedin_export_watcher.py --ingest-new --confirm
```

**Step 5 — Update source health**

```bash
python3 system/scripts/refresh_sources.py --social --save-health --refresh-signals
```

### What it does NOT do

- It does not log into LinkedIn.
- It does not auto-scroll or re-request exports.
- Reprocessing the same file hash is blocked by the manifest (use `--force` to override).

---

## Part 3 — Browser-Session Own-Post Engagement Capture

### What it does

Captures visible engagement (reactions, comments) from a LinkedIn post page or analytics page you open manually. Ingests into `social.engagement.json` and `social.own_posts.json`. Operator-mediated only — no background access.

### When to use

Use this when the official API is not configured or available, and you want to capture engagement on a specific post you can see in your own browser.

### Step-by-step

**Step 1 — Get the capture snippet**

```bash
python3 system/scripts/linkedin_session_reader.py --capture-own-post-engagement-js
```

This prints a JavaScript snippet to your terminal. Copy the entire output.

**Step 2 — Run it on your post page**

1. Open the LinkedIn post (or the "Who reacted" / analytics page) in your browser.
2. Open the browser console: `Cmd+Option+J` (Mac) / `Ctrl+Shift+J` (Windows/Linux).
3. Paste the snippet and press Enter.
4. The console prints a JSON payload. Copy the entire JSON output.

**Step 3 — Save and ingest**

```bash
pbpaste > /tmp/linkedin_engagement.json     # macOS

# Preview
python3 system/scripts/linkedin_session_reader.py \
  --ingest-own-engagement --in /tmp/linkedin_engagement.json --dry-run

# Confirm write
python3 system/scripts/linkedin_session_reader.py \
  --ingest-own-engagement --in /tmp/linkedin_engagement.json --confirm
```

**Step 4 — Update source health**

```bash
python3 system/scripts/refresh_sources.py --social --save-health
```

### What it does NOT do

- Does not log into LinkedIn.
- Does not auto-scroll, store cookies, or run on a schedule.
- Does not read pages you did not open manually.
- Does not background-harvest LinkedIn data.

---

## Part 4 — LinkedIn Feed / Browser-Session Capture

### What it does

Captures the LinkedIn feed posts you can currently see in your own logged-in browser session. Posts are stored with a 36-hour TTL and materialized into `social.feed.json` for the next daily brief.

### Step-by-step

**Step 1 — Get the feed capture snippet**

```bash
python3 system/scripts/linkedin_session_reader.py --capture-js
```

**Step 2 — Run the snippet in your LinkedIn browser tab**

1. Open LinkedIn, log in, scroll to load the posts you want to capture.
2. Open the browser console (`Cmd+Option+J`).
3. Paste the snippet and press Enter. Copy the JSON output.

**Step 3 — Ingest**

```bash
pbpaste > /tmp/linkedin_posts.json
python3 system/scripts/linkedin_session_reader.py --ingest --in /tmp/linkedin_posts.json
```

**Step 4 — Purge expired captures**

```bash
python3 system/scripts/linkedin_session_reader.py --purge
```

---

## Part 5 — How the Daily Brief Uses LinkedIn Engagement

After any of the above paths successfully writes the caches, `social_outbound.py` surfaces engagement signals in the daily brief:

- **Warm contact signal** — baseline contact (RC/LKI) liked or commented on your post → surfaces as a relationship event.
- **Topic affinity** — contacts engaging with a specific topic cluster → informs post recommendation.
- **Follow-up suggestion** — substantive comment from a known contact → can become RI candidate.
- **Engagement silence / decay** — important contacts who stopped engaging → flagged for follow-up consideration.
- **Unknown recurring commenter** — same person appears multiple times → surfaced as promotion candidate.

The brief will always note when LinkedIn data is absent or stale. It never claims live LinkedIn access when no fresh source exists.

---

## Part 6 — Delivery Readiness

```bash
python3 system/scripts/task_delivery_check.py
```

Includes these LinkedIn checks (all warn-only, never block delivery):

| Check | Green means | Recovery |
|---|---|---|
| `linkedin_api_config` | Access token + URN are set | Set `LINKEDIN_ACCESS_TOKEN` + `LINKEDIN_AUTHOR_URN` env vars |
| `linkedin_own_posts` | `social.own_posts.json` fresh (< 72h) | Run `linkedin_own_engagement.py --fetch --confirm` or export watcher |
| `linkedin_own_engagement` | `social.engagement.json` fresh (< 72h) | Run `linkedin_own_engagement.py --fetch --confirm` or browser capture |
| `linkedin_export_watcher` | No unprocessed files in drop locations | Run `--scan` then `--ingest-new --confirm` |
| `linkedin_messaging` | `linkedin.messages.json` fresh (< 48h) | Run export watcher or direct `linkedin_messaging.py --ingest` |
| `linkedin_session_feed` | `social.feed.json` has non-expired session posts | Run `--capture-js`, paste in browser, ingest result |

---

## Quick-Reference Commands

```bash
# Official API
python3 system/scripts/linkedin_own_engagement.py --status
python3 system/scripts/linkedin_own_engagement.py --fetch --dry-run
python3 system/scripts/linkedin_own_engagement.py --fetch --confirm
python3 system/scripts/linkedin_own_engagement.py --from-fixture system/fixtures/linkedin_own_engagement_sample.json --dry-run
python3 system/scripts/linkedin_own_engagement.py --smoke

# Export watcher
python3 system/scripts/linkedin_export_watcher.py --scan
python3 system/scripts/linkedin_export_watcher.py --ingest-new --dry-run
python3 system/scripts/linkedin_export_watcher.py --ingest-new --confirm
python3 system/scripts/linkedin_export_watcher.py --smoke

# Own-post engagement browser capture
python3 system/scripts/linkedin_session_reader.py --capture-own-post-engagement-js
python3 system/scripts/linkedin_session_reader.py --ingest-own-engagement --in /tmp/linkedin_engagement.json --dry-run
python3 system/scripts/linkedin_session_reader.py --ingest-own-engagement --in /tmp/linkedin_engagement.json --confirm

# Feed browser-session capture
python3 system/scripts/linkedin_session_reader.py --capture-js
python3 system/scripts/linkedin_session_reader.py --ingest --in /tmp/linkedin_posts.json
python3 system/scripts/linkedin_session_reader.py --status
python3 system/scripts/linkedin_session_reader.py --purge
python3 system/scripts/linkedin_session_reader.py --self-test

# Messages export
python3 system/scripts/linkedin_messaging.py --ingest system/inbox/linkedin_messages_export.csv
python3 system/scripts/linkedin_messaging.py --ingest system/inbox/linkedin_messages_export.csv --confirm
python3 system/scripts/linkedin_messaging.py --overlay
python3 system/scripts/linkedin_messaging.py --smoke

# Source health + delivery readiness
python3 system/scripts/refresh_sources.py --social --save-health --refresh-signals
python3 system/scripts/task_delivery_check.py
python3 system/scripts/task_delivery_check.py --smoke
```
