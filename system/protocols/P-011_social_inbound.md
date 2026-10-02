---
id: P-011
title: Social inbound — what your graph is posting about
script: system/scripts/social_overlay.py
cache: system/.cache/social_overlay.json
reads:
  - system/inbox/social.feed.json
  - system/baseline_index.json
  - system/active_threads.yaml
writes: []
inputs:
  - name: recent_days
    description: Window size for the overlay (default 14).
    required: false
trigger: every daily brief run; on-demand when operator asks "what is my network talking about"
---

# P-011 — Social inbound

## Purpose

Use posts from contacts in your network as ambient context for the chief-of-staff brief. The brief no longer guesses what's on the minds of your top RCs and LKIs — it reads what they posted and matches it against your active threads and DRR rankings.

## Framing — this is not scraping

Everything this protocol does is in-session, user-driven reading of posts the operator is already entitled to see. There is no automated harvesting, no derived database of profile data, no rate-limit circumvention. The operator (or a tool acting in the operator's own logged-in browser session) reads visible posts on the feed, and a normalizer captures the relevant fields for downstream interpretation.

If you can read a post by scrolling LinkedIn while logged in, you can route it through this protocol. If you can't, the protocol can't see it either.

## Source-agnostic design

Same pattern as calendar and email. The overlay reads `system/inbox/social.feed.json`. Anything that produces that file in the documented shape is a valid fetcher:

- `fetch_via_session.py social --in raw.json` — for a Cowork session that pulled posts via Claude in Chrome.
- `linkedin_session_reader.py --ingest --in raw.json` — for ephemeral capture from Todd's own logged-in LinkedIn browser session.
- `mutations.py social-add` — for one-off manual paste from your phone or laptop.
- A future `fetch_sales_navigator.py` — for the productized commercial path (LinkedIn Sales Navigator API).

## Browser-session reader and TTL

RB should never ask for or store Todd's LinkedIn username/password. The browser-session reader only uses pages Todd can already see while logged into LinkedIn in his own browser.

Flow:

1. During normal LinkedIn use, capture visible posts from the current page.
2. Save the capture through `system/scripts/linkedin_session_reader.py --ingest`.
3. The reader stores short-lived records in `system/inbox/linkedin.session_captures.jsonl`.
4. It materializes non-expired posts into `system/inbox/social.feed.json`.
5. The next daily brief reads those posts through the existing social overlay.
6. `refresh_sources.py --social` runs `linkedin_session_reader.py --purge` before building overlays, removing expired session-captured posts from both the session buffer and materialized feed.

Default retention is 36 hours. That gives the next morning brief enough time to use yesterday's LinkedIn observations, then drops them so old LinkedIn context cannot pretend to be fresh.

See the full step-by-step walkthrough (including the browser-console capture procedure) in:

```
system/automation/LINKEDIN_CAPTURE_OPERATOR_GUIDE.md
```

Useful commands:

```bash
# Print the browser-console snippet for a logged-in LinkedIn page.
python3 system/scripts/linkedin_session_reader.py --capture-js

# Ingest a saved browser capture.
python3 system/scripts/linkedin_session_reader.py --ingest --in /tmp/linkedin_posts.json

# Purge expired captures and update social.feed.json.
python3 system/scripts/linkedin_session_reader.py --purge

# Show capture buffer status without reading LinkedIn.
python3 system/scripts/linkedin_session_reader.py --status

# Self-test (no disk I/O on canonical paths).
python3 system/scripts/linkedin_session_reader.py --self-test
```

## Post shape

See `system/inbox/README.md` for the canonical schema. The minimum required per post is:

- `author.name` — string
- `text` — the post body
- `id` — stable identifier; falls back to post URL or `author::date`

Optional but valuable:
- `author.linkedin_url` — used for higher-confidence baseline matching
- `posted_at` — ISO date
- `engagement.likes` / `.comments` / `.shares` — counts

## What the overlay produces

Three buckets:

1. **`from_baseline`** — posts whose author matches a contact in `baseline_index.json` (URL match first, then case-insensitive name match). Each post carries the matched entry's name, signal class, RC tier, and DRR-relevant active thread tags.
2. **`active_thread_company_hits`** — posts where the author or text mentions a company named in an open active thread, even if the author isn't in baseline. Surfaces network-relevant signal from people you haven't yet promoted into the graph.
3. **`topic_signal`** — active-thread companies that appear in 2+ posts in the window. A market signal that no individual post made obvious.

Plus a roll-up: `authors_not_in_baseline` — recurring authors (2+ posts) or single-post authors who hit an active-thread company. The recommendation surface for new LKIs.

## Operations

```bash
# Manual paste (typical flow today):
python3 system/scripts/mutations.py social-add \
    --author-name "Bruce Sellnow" \
    --text "..." \
    --posted-at 2026-05-15 \
    --author-url "https://www.linkedin.com/in/bruce-sellnow"

# Bulk session capture (when you use Claude in Chrome to scroll your feed):
python3 system/scripts/fetch_via_session.py social --in raw_posts.json --merge

# Standalone overlay report:
python3 system/scripts/social_overlay.py
python3 system/scripts/social_overlay.py --json --cache

# Or via MCP (rb.social_overlay, rb.social_add) / HTTP (GET /social_overlay, POST /social)
```

The `--merge` flag is important for ongoing capture: it dedupes by post id, so re-running a capture session adds new posts without losing the old ones.

## Failure modes

- **Author not in baseline.** Surfaced under `authors_not_in_baseline`. If the author is high-value, `mutations.py contact-add` promotes them and the next overlay run will match.
- **Name match collision.** Rare with a 2,670-entry baseline. If two contacts share a name, prefer `linkedin_url` matching by always passing `--author-url` when adding posts.
- **Stale post text.** Engagement counts captured at different times may not match the post's current state. Don't optimize against the engagement counts — use them as relative signal only.
- **Generic words in active-thread company names.** A thread with `companies: [Toast]` will match the word "toast" anywhere in any post. Set company names precisely (e.g., `Toast` not `Toast, Inc.` if both appear).

## Privacy

`system/inbox/social.feed.json` contains posts written by other people. Treat this as sensitive data:

- Always git-ignored.
- Never share the file or its contents outside the system.
- In production, encrypt at rest; treat the file as you would the operator's calendar.
- Do not republish content; the overlay is an interpretation aid, not a content database.

## Voice

The social overlay's narrative is structural. "Donnie posted about Hospitality Table — he's inner-tier RC, the OTP3 active thread doesn't apply but the Hospitality Table thread does (when its `companies` list is populated)." The interpretive layer — "this means you should reach out before the chapter meeting" — is the session's job, not the overlay's.

## Roadmap

V0.1 enhancements worth considering once this is exercised:

- Free-text matching of active thread *titles* (not just companies) for posts that don't name a company explicitly.
- A `social_outbound` protocol — track your own posts and engagement on them, surface which contacts engage with what topics. The post-recommendation engine.
- Topic clustering across longer windows for trend detection (30/60/90-day windows).
- Author proximity scoring — when an author isn't in baseline but interacts with multiple in-baseline contacts, flag for review.
