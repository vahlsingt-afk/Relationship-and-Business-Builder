# Claude Handoff — 2026-05-24 — RB 9.8 LinkedIn Automation + Own-Post Engagement

## Executive Intent

RB 9.8 should make LinkedIn meaningfully automated where LinkedIn permits it, while preserving RB's safety posture:

- Use official LinkedIn APIs for Todd's own-post engagement if API access/scopes are available.
- Use semi-automated export ingest for LinkedIn messages and archive data.
- Use operator-mediated browser-session capture for feed observations when official API access does not exist.
- Never store Todd's LinkedIn password.
- Never build a headless login scraper or background feed harvester.

The user-visible outcome:

> RB can tell Todd who engaged with his LinkedIn posts, which comments/likes matter for relationships or opportunities, which own-post topics are warming the right people, and what follow-up actions belong in the daily brief.

---

## Current State

This sprint builds on RB 9.7:

- Commit `180199a`: `RB 9.7: stable daily brief delivery and LinkedIn signal ingest readiness`
- `system/scripts/linkedin_messaging.py --smoke`: 0 failures
- `system/scripts/linkedin_session_reader.py --self-test`: OK
- `system/scripts/task_delivery_check.py`: LinkedIn source checks exist and are warn-only.

Existing LinkedIn/social components:

1. `system/scripts/social_outbound.py`
   - Reads Todd's own posts and engagement.
   - Produces:
     - recent post list,
     - engagement by contact,
     - engagement silence,
     - topic-engagement map,
     - active-thread engagement,
     - post recommendations.

2. `system/inbox/social.own_posts.json`
   - Current canonical cache for Todd's own LinkedIn/social posts.
   - Schema documented in `system/inbox/README.md`.

3. `system/inbox/social.engagement.json`
   - Current canonical cache for likes/comments/shares from contacts on Todd's posts.
   - Schema documented in `system/inbox/README.md`.

4. `system/scripts/mutations.py`
   - `my-post-add`: manual own-post insert.
   - `engagement-add`: manual engagement event insert.

5. `system/scripts/linkedin_messaging.py`
   - LinkedIn Messages export ingest.
   - After RB 9.7, `--ingest` is preview by default and `--confirm` writes.

6. `system/scripts/linkedin_session_reader.py`
   - Operator-mediated browser-session feed capture with 36h TTL.

7. `system/scripts/refresh_sources.py`
   - Tracks `social_feed`, `social_engagement`, `social_own_posts`, and `linkedin_messaging` source health.

Main gap:

> Own-post engagement is schema-ready and overlay-ready, but the data source is manual. RB needs an official LinkedIn API adapter where possible, plus a clean fallback importer where official access is not available.

---

## API Reality Check

Before implementing anything that touches LinkedIn online, verify current LinkedIn API access from official docs and the app's approved products/scopes.

Relevant official surfaces to investigate:

- LinkedIn Community Management API
- Social Actions / comments / reactions / social metadata endpoints
- Member vs organization/page post support
- Scopes/products such as:
  - `w_member_social`
  - `r_organization_social`
  - `r_organization_social_feed`
  - any member-post analytics or social-actions read permissions currently available

Important expected constraint:

- Organization/Page post management and engagement is more likely to be officially supported than personal-profile full feed/message access.
- Personal LinkedIn DMs and home feed are usually not available through normal public API access.

If official API access is unavailable, do not fake it with scraping. Implement a clear unavailable/needs-approval status and keep the fallback paths.

---

## Sprint Objective

Build LinkedIn automation in three tiers:

1. **Tier A — Official own-post engagement adapter**
   - Pull Todd's own posts and engagement through LinkedIn official APIs if credentials/scopes permit.
   - Normalize into existing `social.own_posts.json` and `social.engagement.json`.

2. **Tier B — Semi-automated LinkedIn export ingest**
   - Watch/import dropped LinkedIn Messages exports and full LinkedIn ZIPs with preview/confirm safety.

3. **Tier C — Browser-session capture polish**
   - Keep operator-mediated feed capture usable and source-health visible.

---

## Tier A — Official LinkedIn Own-Post Engagement Adapter

### Goal

Add a script that attempts official API-based ingestion of Todd's own LinkedIn posts and engagement.

Suggested new script:

```text
system/scripts/linkedin_own_engagement.py
```

### Responsibilities

1. Read configuration from `system/settings.json` and/or environment:

Suggested env vars:

```bash
LINKEDIN_ACCESS_TOKEN
LINKEDIN_AUTHOR_URN
LINKEDIN_ORGANIZATION_URN
LINKEDIN_API_VERSION
```

Do not store the access token in git-tracked files.

2. Provide explicit modes:

```bash
python3 system/scripts/linkedin_own_engagement.py --status
python3 system/scripts/linkedin_own_engagement.py --fetch --dry-run
python3 system/scripts/linkedin_own_engagement.py --fetch --confirm
python3 system/scripts/linkedin_own_engagement.py --from-fixture system/fixtures/linkedin_own_engagement_sample.json --dry-run
python3 system/scripts/linkedin_own_engagement.py --smoke
```

3. Normalize output into existing cache shapes:

`system/inbox/social.own_posts.json`

```json
{
  "fetched_at": "ISO",
  "source": "linkedin_official_api",
  "posts": [
    {
      "post_id": "urn:li:ugcPost:...",
      "posted_at": "ISO",
      "platform": "linkedin",
      "text": "post text",
      "topics": [],
      "post_url": "https://www.linkedin.com/feed/update/...",
      "engagement_totals": {
        "likes": 0,
        "comments": 0,
        "shares": 0,
        "impressions": null
      }
    }
  ]
}
```

`system/inbox/social.engagement.json`

```json
{
  "fetched_at": "ISO",
  "source": "linkedin_official_api",
  "events": [
    {
      "post_id": "urn:li:ugcPost:...",
      "type": "like|comment|share|reaction",
      "engager": {
        "name": "Person Name",
        "linkedin_url": "https://www.linkedin.com/in/...",
        "urn": "urn:li:person:..."
      },
      "at": "ISO",
      "comment_text": "only for comments"
    }
  ]
}
```

4. Preserve existing manual rows unless `--replace` is explicitly provided.

Default write behavior should merge/dedupe by:

- own post: `post_id`
- engagement: `post_id + type + engager urn/url/name + at + comment_text hash`

5. Write snapshots before mutation, matching local patterns.

6. On unavailable API access, return structured status:

```json
{
  "ok": false,
  "status": "not_configured|missing_token|insufficient_scope|api_unavailable|http_error",
  "safe_fallback": "Use LinkedIn export or browser-session capture.",
  "does_not_block_delivery": true
}
```

### API Design Notes

Implement the adapter behind small functions so endpoint variations are contained:

- `fetch_author_posts(...)`
- `fetch_social_metadata(post_urns...)`
- `fetch_comments(post_urn...)`
- `fetch_reactions(post_urn...)`
- `normalize_own_posts(...)`
- `normalize_engagement_events(...)`

If member-profile endpoints are unavailable but organization/page endpoints work, support organization mode first and clearly report that member mode is not configured.

### Acceptance Criteria

- `--status` reports configuration without exposing token contents.
- `--smoke` passes using a local fixture, no network required.
- `--from-fixture ... --dry-run` produces both normalized cache payloads.
- `--fetch --dry-run` either:
  - returns normalized data from the official API, or
  - returns a clear `insufficient_scope` / `not_configured` status.
- `--fetch --confirm` writes only after explicit confirmation.
- `social_outbound.py --json --cache` reads the written caches and produces meaningful overlay output.
- `refresh_sources.py --social --save-health` marks `social_own_posts` and `social_engagement` fresh after successful confirmed ingest.

---

## Tier B — Semi-Automated LinkedIn Export Ingest

### Goal

Make export ingest low-friction without pretending LinkedIn DMs are live API-accessible.

### Existing components

- `system/scripts/linkedin_ingest.py`
  - full LinkedIn archive ZIP classifier/ingester.
- `system/scripts/linkedin_messaging.py`
  - Messages CSV parser.
- `system/protocols/P-002_linkedin_ingest.md`
- `system/protocols/P-033_linkedin_messaging_export_ingest.md`

### Required Work

1. Add a small inbox watcher/import helper.

Suggested script:

```text
system/scripts/linkedin_export_watcher.py
```

Suggested commands:

```bash
python3 system/scripts/linkedin_export_watcher.py --scan
python3 system/scripts/linkedin_export_watcher.py --ingest-new --dry-run
python3 system/scripts/linkedin_export_watcher.py --ingest-new --confirm
python3 system/scripts/linkedin_export_watcher.py --smoke
```

2. Watch these drop locations:

```text
system/inbox/linkedin_exports/
system/inbox/linkedin_messages_export.csv
```

3. Behavior:

- Detect ZIPs that classify as `linkedin_export_zip`.
- Detect standalone `messages.csv`.
- For ZIPs containing `messages.csv`, extract to a temp path and route through `linkedin_messaging.py` preview/confirm.
- For full connection/archive ZIPs, route through existing `linkedin_ingest.py`.
- Maintain a small local manifest of processed file hashes:

```text
system/.cache/linkedin_export_watcher.json
```

- Never reprocess the same file unless `--force`.
- Default to dry-run.
- Confirm mode writes only the expected caches/deltas.

4. Update `refresh_sources.py` recovery guidance to mention the watcher:

```bash
python3 system/scripts/linkedin_export_watcher.py --scan
python3 system/scripts/linkedin_export_watcher.py --ingest-new --confirm
```

### Acceptance Criteria

- Fixture ZIP with `messages.csv` routes to `linkedin_messaging.py`.
- Fixture full LinkedIn ZIP routes to `linkedin_ingest.py` dry-run path.
- Duplicate file is skipped based on hash.
- `--dry-run` writes no inbox/cache except optional watcher scan report.
- `--confirm` writes expected cache(s) and records processed hash.
- `task_delivery_check.py` LinkedIn messaging recovery command points at the watcher or the direct ingest command.

---

## Tier C — Browser-Session Capture Polish

### Goal

Keep feed capture safe and operator-mediated, while reducing friction.

Existing:

- `system/scripts/linkedin_session_reader.py`
- `system/automation/LINKEDIN_CAPTURE_OPERATOR_GUIDE.md`
- `system/protocols/P-011_social_inbound.md`

### Required Work

1. Add support for own-post engagement browser capture only if it can be done from pages Todd views manually.

This should be separate from feed capture:

```bash
python3 system/scripts/linkedin_session_reader.py --capture-own-post-engagement-js
```

It should print JS that captures visible engagement rows/comments from the current LinkedIn post analytics or post detail page, not the whole LinkedIn site.

2. Ingest captured engagement into the existing `social.own_posts.json` / `social.engagement.json` shapes.

Suggested command:

```bash
python3 system/scripts/linkedin_session_reader.py --ingest-own-engagement --in /tmp/linkedin_engagement.json --dry-run
python3 system/scripts/linkedin_session_reader.py --ingest-own-engagement --in /tmp/linkedin_engagement.json --confirm
```

3. Keep this mode explicit and user-directed.

Do not:

- auto-scroll,
- log in,
- store cookies,
- read pages Todd did not open,
- run on a schedule against LinkedIn pages.

4. Update `LINKEDIN_CAPTURE_OPERATOR_GUIDE.md` with:

- own-post engagement capture workflow,
- when to use official API vs browser capture,
- privacy/TTL notes,
- how the daily brief uses engagement.

### Acceptance Criteria

- Fixture own-post engagement capture ingests in dry-run mode.
- Confirm mode writes/merges engagement caches.
- `social_outbound.py --json --cache` sees captured engagement.
- Source health reports `social_engagement` and `social_own_posts` as fresh after confirmed ingest.
- The guide clearly states this is operator-mediated, not background scraping.

---

## Tier D — Daily Brief / RI Surfacing

### Goal

Turn LinkedIn engagement into useful relationship intelligence, not just counts.

### Required Work

1. Confirm `social_outbound.py` output is included in:

- daily brief relationship signal section,
- post recommendations,
- action board where relevant.

2. Ensure meaningful own-post engagement can produce:

- warm contact signal,
- follow-up suggestion,
- loop evidence,
- topic affinity,
- silence/decay signal for important RC/LKI contacts.

3. Keep durable state changes review-first:

- no automatic baseline mutation from a like/comment alone,
- comments can become RI candidates if substantive,
- follow-up loop closure requires evidence and confirmation unless existing passive verification rules already allow it.

4. Add focused tests/fixtures for:

- in-baseline contact likes Todd's post,
- in-baseline contact comments substantively,
- unknown person comments repeatedly,
- target-company person engages with active-thread topic,
- stale engagement cache warning.

### Acceptance Criteria

- Daily brief can say:
  - “X engaged with your post on restaurant AI; this maps to active thread Y.”
  - “Unknown but recurring commenter Z may be worth adding/reviewing.”
  - “Topic A is warming RC/LKI contacts; consider a follow-up post.”
- It never claims LinkedIn is fresh when no fresh official API/export/session capture exists.

---

## Tier E — Operator Readiness / Status Checks

### Goal

One command should tell Todd the state of LinkedIn automation.

Update:

```text
system/scripts/task_delivery_check.py
```

Add/read statuses:

- `linkedin_api_config`
- `linkedin_own_posts`
- `linkedin_own_engagement`
- `linkedin_messaging`
- `linkedin_session_feed`
- `linkedin_export_watcher`

Status semantics:

- Official API not configured: WARN, with setup instructions.
- Missing messages export: WARN, with export watcher instructions.
- Stale own-post engagement: WARN, with official API/fallback capture instructions.
- No LinkedIn source fresh: WARN, not delivery-blocking.
- Malformed cache or failed confirmed ingest: FAIL.

Acceptance:

```bash
python3 system/scripts/task_delivery_check.py --smoke
python3 system/scripts/task_delivery_check.py
```

Smoke should verify all new LinkedIn checks are recorded and warn/pass only unless there is malformed data.

---

## Suggested Commit Scope

Likely files:

```bash
git add \
  system/scripts/linkedin_own_engagement.py \
  system/scripts/linkedin_export_watcher.py \
  system/scripts/linkedin_session_reader.py \
  system/scripts/social_outbound.py \
  system/scripts/refresh_sources.py \
  system/scripts/task_delivery_check.py \
  system/scripts/api_smoke_test.py \
  system/inbox/README.md \
  system/protocols/P-011_social_inbound.md \
  system/protocols/P-015_social_outbound.md \
  system/protocols/P-033_linkedin_messaging_export_ingest.md \
  system/automation/LINKEDIN_CAPTURE_OPERATOR_GUIDE.md \
  system/CLAUDE_HANDOFF_2026-05-24_RB_9_8_LINKEDIN_AUTOMATION_OWN_POST_ENGAGEMENT.md
```

Adjust to actual touched files. Do not stage unrelated dirty RB 9.x work.

Suggested commit message:

```text
RB 9.8: LinkedIn own-post engagement automation and export watcher
```

---

## Final Sprint Acceptance Criteria

RB 9.8 is complete when:

1. Official LinkedIn own-post engagement adapter exists and smokes via fixture.
2. Adapter can report `not_configured` / `insufficient_scope` safely without breaking delivery.
3. Confirmed official or fixture ingest writes valid `social.own_posts.json` and `social.engagement.json`.
4. Export watcher detects LinkedIn ZIP/CSV drops and routes them to the correct existing ingester.
5. Browser-session fallback can capture own-post engagement from user-opened pages, if implemented.
6. `social_outbound.py --json --cache` produces relationship-relevant engagement overlay.
7. `refresh_sources.py --social --save-health` reports LinkedIn own-post and engagement freshness correctly.
8. `task_delivery_check.py` exposes LinkedIn API/export/session readiness clearly.
9. Daily brief can surface own-post engagement signals without implying unsupported live LinkedIn access.
10. No LinkedIn password, cookies, or headless scraping workflow is introduced.

---

## Claude Execution Notes

- Prefer official API integration where access exists.
- Build fixture-first so tests pass even without LinkedIn credentials.
- Keep every online/API mode safe when credentials/scopes are absent.
- Keep export and browser-session paths as first-class fallbacks, not hacks.
- Reuse existing `social.own_posts.json` and `social.engagement.json` schemas.
- Do not create a parallel social analytics store.
- Use explicit `--confirm` for writes.
- Treat LinkedIn automation as sensitive PII/relationship data.
