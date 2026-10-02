# RB 9.2 Architecture — Morning Delivery and Source Instrumentation

Date: 2026-05-23
Author: Claude (architect review)
For: Codex (implementer)
Companion packet: `system/CLAUDE_PACKET_2026-05-23_RB_9_2_ARCHITECTURE_BRIEF.md`

---

## Sprint Objective

Make the morning experience operationally real. RB 9.1 made the intelligence layer
durable. RB 9.2 makes the delivery layer trustworthy:

- Source refresh reports its own health — never silently stale.
- The daily brief lands inside ChatGPT as a native task result, not a link to a
  blank cockpit.
- The external RB email is an honest backup doorbell, not a pretend deep link.
- LinkedIn messaging export is exercised against a real file.
- An end-to-end morning path test proves the whole chain works.

North star posture is unchanged: RB is an operator-savvy Chief of Staff for
relationships, not a scheduled background search with an email summary.

---

## Architecture Decision Record

### Q1 — Exact native ChatGPT Task architecture for RB Daily Brief delivery

**Architecture: Scheduled Task created inside the Relationship Bridge Custom GPT,
calling `getDailyBrief` via the GPT's existing Action.**

ChatGPT allows users to create scheduled tasks from within a Custom GPT
conversation. When the task fires, it runs in that GPT's full context and can
invoke the GPT's configured Actions. This is the native delivery path.

The sequence:

```
05:00 CT  LaunchAgent fires on host Mac
            → refresh_sources.py --all --refresh-signals
            → daily_brief.py → today.md
            → publish.py → published/daily/<date>/{index.md,index.html,brief.json}
                         → latest.html
                         → queues backup email

05:05 CT  ChatGPT Task fires (scheduled inside the Relationship Bridge GPT)
            → getDailyBrief action → /daily_brief?use_cache=true
            → renders canonical_brief inline in ChatGPT
            → ChatGPT sends native push + task-result email to Todd
              (sender: ChatGPT <noreply@tm.openai.com>)
              (button: View message → opens the rendered brief in ChatGPT)

05:07 CT  RB backup email arrives (if SMTP / .eml transport is configured)
            → subject: "RB Daily Brief is ready - <weekday>, <date>"
            → doorbell copy only; does not re-paste the brief
```

The 5-minute offset between local generation (05:00) and task fetch (05:05) gives
the LaunchAgent time to complete and ensures `getDailyBrief?use_cache=true`
returns today's already-generated brief, not a live recompute.

### Q2 — Can a ChatGPT Task call the Custom GPT action directly?

**Yes, if the task is created from within the Custom GPT.** Tasks created inside a
Custom GPT session inherit that GPT's action context and can invoke its operations.

The prerequisite: the RB API must be reachable from OpenAI's servers. The repo
already uses a Cloudflare quick tunnel (`trycloudflare.com`) as the `servers[0].url`
in `openapi_gpt.yaml`. That quick tunnel works for the prototype but changes every
restart. The 9.2 blocker is stabilizing the tunnel URL so the Custom GPT action
doesn't break when the Mac restarts.

### Q3 — Smallest secure contract for the hosted retrieval endpoint

**No new endpoint needed beyond stabilizing the existing Cloudflare tunnel.**

The full `/daily_brief` endpoint already returns the structured brief. The task
calls it with `?use_cache=true`, which returns the cached brief JSON without
re-running the compute. Authentication is the existing `x-api-key` header, which
the Custom GPT action already sends.

What Codex DOES need to add:

1. `GET /brief/health` — lightweight endpoint that returns the source health
   summary from `system/.cache/source_health.json` so the task can report data
   freshness without fetching the full brief. (Optional for 9.2, useful for
   debugging.)

2. `GET /brief/latest-json` — a read-only alias for the most recently published
   `brief.json` artifact (from `publish.py`). Returns the static artifact, not a
   live compute. This separates the "retrieve the pre-published artifact" surface
   from the full API, which is useful if the tunnel is down but a static hosting
   fallback is available.

**Tunnel stabilization (Todd's responsibility, not Codex):** Replace the quick
tunnel with a named Cloudflare Tunnel (`cloudflared tunnel create rb-api`) that
gets a stable subdomain. See Appendix A for the exact setup steps. Once done,
update `openapi_gpt.yaml` → `servers[0].url` and republish the Custom GPT Actions.

Authentication model:
- All API calls use the `x-api-key` header (current `apiKeyAuth` scheme in the
  OpenAPI spec).
- The Custom GPT stores the key; the ChatGPT Task inherits it through GPT context.
- No new OAuth or per-task auth is needed.

### Q4 — Exact native ChatGPT Task prompt

Create this task from within the Relationship Bridge Custom GPT. Use "Schedule a
task" in the ChatGPT UI and paste this as the task instruction:

```text
Every day at 5:05 AM Central (including weekends):

Call getDailyBrief and render the returned canonical_brief as my Chief-of-Staff
morning brief in this section order:

1. Daily Prep Summary — sources refreshed, scan window, confidence
2. Morning Command Center — top moves, meeting queue, waiting state, one decision
3. Top 3 Priorities — named moves with why-now and consequence-of-nothing
4. Meeting Prep and Deliverables — today and tomorrow
5. What Changed Since Yesterday — deltas only
6. Opportunity Temperature — threads labeled warming/active/waiting/cooling/stalled
7. Relationship Signals — source-backed signals from the last 24 hours
8. Loop Review — overdue, due today, auto-closed, waiting
9. Strategic Operator Movements — only when source-backed
10. What To Ignore — explicit suppression list
11. One Question For Todd — only if ambiguity matters
12. Data Health / Confidence — source freshness, stale sources, limits

Do not produce a generic restaurant-tech or macro news summary. Do not invent
signals from stale sources. If getDailyBrief fails or returns empty data, say
exactly: "RB brief unavailable — [error]. Open the cockpit and send: Show today's
RB Daily Brief." Do not guess or synthesize from prior context.
```

This prompt is already documented in `system/automation/CHATGPT_TASK_DAILY_BRIEF_PUSH.md`
as the "Target Task Prompt." It is ready to use as-is once the API is reachable.

### Q5 — Exact fallback external email wording

`publish.py` composes the backup email. The copy should be updated to reflect the
new honest posture. Target wording for the email body:

```text
Subject: RB Daily Brief is ready — <weekday>, <date>

The native ChatGPT Task delivered today's brief directly to your ChatGPT.
This email is the backup doorbell — open if the Task notification didn't arrive.

Top priorities today:
• <priority 1 from brief>
• <priority 2 from brief>
• <priority 3 from brief>

Source health: <refreshed N / stale M / unavailable P>

Open the RB cockpit → https://<tunnel-url>/chatgpt
Send: Show today's RB Daily Brief.

—RB
```

Key constraints: no full brief in the email, no file paths, no developer language,
no fabricated CTA URL (must gate on `settings.json → daily_briefing.operational_layer.public_url_base`).

### Q6 — Which source refresh gaps belong in 9.2 versus later

**9.2 scope (concrete, executable by Codex or by Todd with clear instructions):**

| Source | Gap | 9.2 Action |
|---|---|---|
| Gmail / Calendar | Raw connector capture is manual; no automation | Document the exact Cowork session command; add to morning checklist |
| Apple Messages + Calls (P-019) | FDA not granted; never run on real Mac | First-run walkthrough in P-019 already written; Todd executes |
| LinkedIn messaging export | Parser is live but no real export ingested | Codex writes the ingest command clearly; Todd downloads export and runs it |
| Source health model | No persistent health file; brief can't self-report | Codex adds `--save-health` to `refresh_sources.py` and writes `source_health.json` |
| Cloudflare tunnel | Quick tunnel changes on restart | Todd creates named tunnel (Appendix A); Codex documents the step |

**Deferred to post-9.2 (require more design or external dependencies):**

| Source | Why deferred |
|---|---|
| SMS / phone logs (P-019 on Mac) | Depends on FDA grant first; run P-019 first-run, then re-evaluate |
| LinkedIn own-post engagement | Manual-only capture today; automation requires API or browser session |
| `fetch_google.py` (direct OAuth) | GCP OAuth setup is non-trivial; Cowork MCP connector is sufficient for 9.2 |
| LinkedIn post feed automation | Browser session approach is sufficient; API requires LinkedIn partner approval |

### Q7 — Minimum credible source health model for morning trust

Five-tier health model. Codex writes this; the brief reads it.

```
Tier 1 — CRITICAL (brief trustworthiness depends on these)
  email, calendar

Tier 2 — HIGH (significant signal; stale weakens CoS judgment)
  messages, calls, linkedin_messaging

Tier 3 — MEDIUM (enriches brief; stale is a noted gap, not a trust failure)
  social_feed, social_engagement, social_own_posts, market_signals

Tier 4 — INTERNAL (always computed from what Tier 1-3 provided)
  relationship_signals, strategic_operators, interaction_overlay
```

Per-source status values (extending the existing `STATUS_*` constants):

```
refreshed              — ran successfully, fetched_at within staleness threshold
stale                  — last successful refresh exists but is past threshold
skipped_no_raw_input   — raw MCP capture needed before this source can refresh
skipped_disabled       — disabled in accounts.yaml
unavailable            — platform/FDA prevents this (e.g., Apple DB on non-Mac)
not_configured         — no account entry for this source type
failed                 — ran but returned non-zero exit or threw exception
```

Overall brief health levels:

```
green              — all Tier 1 and Tier 2 sources are refreshed
partial            — all Tier 1 refreshed; some Tier 2 stale or unavailable
under_instrumented — at least one Tier 1 source is stale, skipped, or failed
not_configured     — no sources have been set up yet (first-run state)
```

Staleness thresholds:

```
email, calendar           : 24h
messages, calls           : 48h (less frequent human-initiated refresh)
social_*, linkedin_*      : 48h
market_signals            : 72h (manual scan cadence)
```

The brief must not say "quiet day" or "no new signals" unless all Tier 1 and Tier
2 sources are `refreshed`. If health is `under_instrumented`, the Daily Prep
Summary leads with the stale sources and the exact refresh command.

### Q8 — Should refresh_sources.py stay local-only in 9.2?

**Yes. Local-only is correct for 9.2.**

`refresh_sources.py` reads macOS SQLite databases (requiring FDA), normalizes raw
MCP captures into `system/inbox/`, and writes to `system/.cache/`. Exposing it via
a GPT Action would require sandboxing the host filesystem side-effects, managing
secrets on the public endpoint, and handling FDA-gated paths — none of which are
worth solving before there is a multi-user need.

The correct 9.2 posture: the LaunchAgent runs `refresh_sources.py` on the host
before the ChatGPT Task fires. The Task's `getDailyBrief` call picks up the
already-refreshed cached brief. If a source failed to refresh, the brief reports
the failure honestly.

### Q9 — STATUS and protocol updates required before Codex implements

Codex must make these file updates as part of Priority 5 (cleanup), after Priorities
1-4 are implemented and smoke-tested.

**`system/STATUS.md`:**
- Update the `rb-daily-briefing` Scheduled Tasks row to say:
  "Native ChatGPT Task (created inside the Relationship Bridge Custom GPT at 5:05 AM
  CT) is the canonical delivery surface. External RB email is backup only.
  Cloudflare tunnel must be a named tunnel (not a quick tunnel) for stable delivery."
- Add a new row: `source_health.json` — LIVE once `--save-health` is implemented.
- Update `refresh_sources.py` row: add that `--save-health` writes the source
  health file.

**`system/protocols/P-001_daily_brief_regen.md`:**
- Step 18 already reflects the correct delivery posture. No changes needed.
- Step 2 should reference `--save-health` once that flag is implemented.

**New `system/protocols/P-032_native_chatgpt_task_delivery.md`:**
- Required. Documents the full delivery contract. See Priority 5 for the exact
  structure.

**`system/automation/CHATGPT_TASK_DAILY_BRIEF_PUSH.md`:**
- Current content is correct. The "Notes" section should add: "Named Cloudflare
  Tunnel (not a quick tunnel) is required for stable task delivery. See Appendix A
  in the 9.2 architecture doc for setup."

### Q10 — Final 9.2 acceptance test

Pass criteria for declaring 9.2 complete:

```
✓ refresh_sources.py --all --save-health runs without failures on the host Mac
✓ system/.cache/source_health.json exists with per-source status rows
✓ daily_brief.py --smoke passes with 0 failures
✓ publish.py smoke passes; artifacts at published/daily/<date>/brief.json and latest.html
✓ LinkedIn messaging export: at least one real messages.csv ingested and smoke passes
  (last_touch proposals present; inbound RI count ≥ 0; no crashes)
✓ GET /daily_brief?use_cache=true via the Cloudflare tunnel returns 200 + canonical_brief
✓ ChatGPT Task created inside the Relationship Bridge Custom GPT
✓ Task fires at 5:05 AM CT → ChatGPT Task email/push received with "View message" button
✓ Clicking "View message" opens the full daily brief inline (no "Send: Show today's RB Daily Brief")
✓ GPT fallback command "Show today's RB Daily Brief." also works (for cockpit use)
✓ Backup email subject contains "RB Daily Brief is ready" and does NOT contain the full brief
✓ STATUS.md updated; P-032 protocol file exists
✓ validate_openapi_gpt.py passes at ≤30 operations
```

---

## Priority Implementation Order for Codex

Implement exactly one priority at a time. Smoke-test before moving to the next.
Do not implement Priority 2 until Priority 1 is green. Do not mix concerns across
priorities.

---

### Priority 1 — Source Health Model

**Why first:** Every other 9.2 deliverable depends on knowing what sources are
healthy. The ChatGPT Task brief cannot report data confidence until this exists.

#### Files to modify

**`system/scripts/refresh_sources.py`**

Add `--save-health` CLI flag. When passed, after all source results are collected,
write `system/.cache/source_health.json` with this shape:

```json
{
  "generated_at": "2026-05-23T05:00:45",
  "overall_health": "partial",
  "brief_trustworthiness": "under_instrumented",
  "staleness_thresholds_hours": {
    "email": 24, "calendar": 24,
    "messages": 48, "calls": 48,
    "social_feed": 48, "social_engagement": 48, "social_own_posts": 48,
    "linkedin_messaging": 48,
    "market_signals": 72
  },
  "sources": {
    "email:personal": {
      "status": "skipped_no_raw_input",
      "last_refreshed_at": null,
      "tier": 1,
      "reason": "no raw_gmail_threads.personal.json in system/inbox/"
    },
    "calendar:bridgepoint": {
      "status": "refreshed",
      "last_refreshed_at": "2026-05-23T04:58:12",
      "tier": 1
    },
    "messages": {
      "status": "unavailable",
      "last_refreshed_at": null,
      "tier": 2,
      "reason": "macOS Full Disk Access not granted"
    }
  }
}
```

`overall_health` and `brief_trustworthiness` are computed after all results
are collected:

- `overall_health = green` if all Tier 1 and Tier 2 sources are `refreshed`
- `overall_health = partial` if all Tier 1 are `refreshed` but some Tier 2 are not
- `overall_health = under_instrumented` if any Tier 1 is stale/skipped/failed
- `overall_health = not_configured` if no sources ran at all

`brief_trustworthiness` maps the same levels; use `under_instrumented` when email
or calendar have not refreshed within their staleness threshold.

For sources that have an existing cached inbox file (e.g., `email.personal.json`),
read its `fetched_at` field to populate `last_refreshed_at` even if this run
skipped or failed (so prior good refreshes are not lost from the health record).

**`system/scripts/daily_brief.py`**

Add a `_load_source_health()` helper that reads `system/.cache/source_health.json`
if it exists (return `None` if absent). Pass the source health into the Daily Prep
Summary section render. When `brief_trustworthiness == under_instrumented`, the
Daily Prep Summary must open with the stale Tier 1 sources and their `reason`
before any relationship intelligence content. This mirrors the existing
`stale_sources` behavior in `relationship_signals.py`.

**`system/scripts/refresh_all.py`**

Add `--save-health` to the `refresh_sources.py` call so the daily automation
writes the health file on every full refresh run.

#### Acceptance smoke test (run before moving to Priority 2)

```bash
python3 system/scripts/refresh_sources.py --all --save-health
# expect: system/.cache/source_health.json exists, valid JSON, has "overall_health" key

python3 system/scripts/daily_brief.py --smoke
# expect: 0 failures; if source_health.json is present, Daily Prep Summary includes health

python3 -c "
import json, pathlib
h = json.loads(pathlib.Path('system/.cache/source_health.json').read_text())
assert 'overall_health' in h
assert 'sources' in h
assert all('tier' in v for v in h['sources'].values())
print('source_health.json schema OK')
"
```

---

### Priority 2 — LinkedIn Messaging Real-Ingest Path

**Why second:** The parser exists and smokes green against synthetic data. The
9.2 requirement is exercising it against a real LinkedIn messages export. This
surfaces the actual data quality (encoding, column names, date formats) before the
morning brief depends on it.

#### What Todd must do (manual, one-time)

1. Go to LinkedIn → Settings → Data Privacy → Get a copy of your data.
2. Request: "Messages" (not the full archive — messages.csv is delivered faster).
3. Download the ZIP when the email arrives (usually within 10 minutes for messages-only).
4. Extract `messages.csv` from the ZIP.
5. Drop it at: `system/inbox/linkedin_messages_export.csv` (git-ignored).

#### What Codex must implement

**`system/scripts/linkedin_messaging.py`**

Add a `--ingest` CLI mode (if not already present) that accepts a path argument:

```bash
python3 system/scripts/linkedin_messaging.py --ingest system/inbox/linkedin_messages_export.csv
```

The command should:
1. Parse the CSV.
2. Emit a summary: total rows, matched contacts (count), unmatched recurring
   participants (count), last_touch proposals (count), outbound evidence rows (count).
3. Write the normalized output to `system/inbox/linkedin.messages.json`
   (the path `refresh_sources.py --social` already reads).
4. Exit 0 on success; exit non-zero with a clear message on CSV parse failure.

Do NOT auto-apply last_touch updates. The summary is for operator review;
`--apply-last-touch` is a separate confirmed step per the existing design.

**`system/scripts/refresh_sources.py`**

The `refresh_social` function already logs a note when `linkedin.messages.json` is
missing. Ensure the note includes the ingest command:

```
note: system/inbox/linkedin.messages.json is missing; run:
  python3 system/scripts/linkedin_messaging.py --ingest <path/to/messages.csv>
```

**New protocol file `system/protocols/P-033_linkedin_messaging_export_ingest.md`**

Minimal protocol documenting the export-download → ingest → review → apply flow.
Structure:

```markdown
---
id: P-033
title: LinkedIn Messaging Export Ingest
script: system/scripts/linkedin_messaging.py
cache: system/inbox/linkedin.messages.json
reads:
  - system/inbox/linkedin_messages_export.csv (operator-dropped)
  - system/baseline_index.json
writes:
  - system/inbox/linkedin.messages.json
trigger: on-demand after each LinkedIn export download; suggest monthly
---
```

Body should document the 5-step export-and-ingest flow, the match logic, the
last_touch review step, and the failure modes (encoding errors, wrong CSV, no
baseline phone/URL fields).

#### Acceptance smoke test (run after Todd drops a real CSV)

```bash
python3 system/scripts/linkedin_messaging.py --ingest system/inbox/linkedin_messages_export.csv
# expect: exit 0; summary printed with total rows, matched count, last_touch proposals

python3 system/scripts/linkedin_messaging.py --smoke
# expect: 0 failures (existing 31 smoke checks still pass after ingest)

# Verify the output file exists and is valid JSON
python3 -c "
import json, pathlib
data = json.loads(pathlib.Path('system/inbox/linkedin.messages.json').read_text())
assert 'fetched_at' in data
assert 'events' in data or 'messages' in data
print('linkedin.messages.json shape OK, event count:', len(data.get('events', data.get('messages', []))))
"
```

---

### Priority 3 — End-to-End Morning Path Test

**Why third:** After the source health model and LinkedIn ingest are in place, this
priority wraps them into a single runnable proof of the full morning chain.

#### Files to create

**`system/scripts/morning_path_test.py`**

A single script that runs the full morning sequence and reports pass/fail per step.
It must be safe to run at any time without side effects on canonical state (read-only
or dry-run except for `.cache/` and `published/` paths).

```
Steps:
  Step 1: refresh_sources.py --all --save-health
          PASS: exit 0; source_health.json written
          FAIL: any failed source (skipped/unavailable are not failures)

  Step 2: daily_brief.py --smoke
          PASS: 0 failures
          FAIL: any failure

  Step 3: publish.py (dry-run mode if available; else check artifacts from prior run)
          PASS: system/published/daily/<today>/{index.md,index.html,brief.json} exist
                system/published/daily/latest.html exists
          FAIL: any artifact missing

  Step 4: GET /daily_brief?use_cache=true via localhost (TestClient)
          PASS: HTTP 200; response has "canonical_brief" key
          FAIL: non-200 or missing key

  Step 5: GET /brief/health (if implemented)
          PASS: HTTP 200; "overall_health" key present
          FAIL: non-200

  Step 6 (manual, not automated): ChatGPT Task verification
          PASS: "View message" email received; brief visible without sending a command
          FAIL: email arrives but brief is not inline; brief requires manual command

  Step 7 (manual): GPT fallback command verification
          PASS: "Show today's RB Daily Brief." returns the brief
          FAIL: GPT opens blank or requests setup
```

Steps 1-5 are automated. Steps 6-7 are printed as a manual checklist with exact
pass/fail criteria. The script exits 0 only when all automated steps pass.

Output format: one line per step with `[PASS]`, `[FAIL]`, or `[MANUAL]` prefix,
then a one-line reason. A final summary line: `Morning path: N/M automated steps
passed`.

**`system/scripts/morning_path_test.py`** should also write a structured result
to `system/.cache/morning_path_test.json` so future runs can show regression.

#### Failure wording (user-facing, for each automated step)

| Step | Failure message |
|---|---|
| 1 — source refresh | "Source refresh failed: [source] reported [status]. Brief may be under-instrumented. Run: `python3 system/scripts/refresh_sources.py --[flag]` and check the reason." |
| 2 — daily brief smoke | "Daily brief smoke failed: [failure description]. Do not publish until resolved." |
| 3 — publish artifacts | "Published artifacts missing at [path]. Run `publish.py` and check for errors." |
| 4 — API brief response | "GET /daily_brief returned [status] or missing canonical_brief key. Is the server running? Is the Cloudflare tunnel active?" |
| 5 — brief health | "GET /brief/health returned [status]. Source health endpoint not reachable." |

#### Acceptance smoke test

```bash
python3 system/scripts/morning_path_test.py
# expect: Steps 1-5 show [PASS] or [MANUAL]; no [FAIL]; exit code 0

python3 system/scripts/morning_path_test.py
# expect: system/.cache/morning_path_test.json written, valid JSON with "steps" array
```

---

### Priority 4 — GPT Cockpit Spec and Tunnel Stabilization

**Why fourth:** The cockpit prompt is already correct. The only 9.2 spec work is
confirming the operation count stays ≤30 after adding `GET /brief/health` and
`GET /brief/latest-json`.

#### Files to modify

**`system/api/server.py`**

Add two new endpoints:

```python
@app.get("/brief/health")
def get_brief_health():
    """Return source_health.json from cache. 404 if not yet generated."""
    path = PROJECT_DIR / "system" / ".cache" / "source_health.json"
    if not path.exists():
        raise HTTPException(404, detail="source_health.json not found; run refresh_sources.py --save-health")
    return json.loads(path.read_text())

@app.get("/brief/latest-json")
def get_brief_latest_json():
    """Return the most recently published brief.json artifact."""
    latest = PROJECT_DIR / "system" / "published" / "daily" / "latest_brief.json"
    if not latest.exists():
        raise HTTPException(404, detail="latest_brief.json not found; run publish.py")
    return json.loads(latest.read_text())
```

**`system/scripts/publish.py`**

In addition to `latest.html`, also write `system/published/daily/latest_brief.json`
as a copy of the current `brief.json` artifact. This is the stable path that
`/brief/latest-json` serves.

**`system/api/openapi.yaml` and `system/api/openapi_gpt.yaml`**

Add the two new endpoints to `openapi.yaml`. For `openapi_gpt.yaml`, add only
`/brief/health` (lightweight, useful for the ChatGPT Task to verify freshness
before rendering). Do NOT add `/brief/latest-json` to the GPT spec — it is a
fallback artifact path, not a cockpit operation.

Validate after adding:

```bash
python3 system/scripts/validate_openapi_gpt.py
# expect: ≤30 operations, no validation errors
```

If adding `/brief/health` would push over 30, evaluate which existing operation
is lowest-leverage and remove it first (propose removal to Todd before Codex
executes). Do not silently drop operations.

**`system/api/custom_gpt_prompt.md`** — no changes needed. Step 0 already
instructs the GPT to call `getDailyBrief` immediately on "Show today's RB Daily
Brief." The tunnel stabilization is an infrastructure change, not a prompt change.

#### Acceptance smoke test

```bash
python3 system/scripts/validate_openapi_gpt.py
# expect: exactly ≤30 operations, all valid

python3 system/scripts/api_smoke_test.py
# expect: all endpoints pass (including the two new ones)
```

---

### Priority 5 — STATUS, Protocol, and Handoff Cleanup

**Why last:** These are documentation updates that should reflect the implemented
state, not the intended state. Do them after Priorities 1-4 are smoke-green.

#### Files to modify

**`system/STATUS.md`**

Update the Scheduled Tasks table row for `rb-daily-briefing`:

```
| `rb-daily-briefing` | LIVE compute; ChatGPT Task canonical; backup email optional |
  Native delivery: ChatGPT Task created inside the Relationship Bridge Custom GPT,
  firing at 5:05 AM CT. Calls getDailyBrief via the GPT Action. Requires a stable
  Cloudflare named tunnel (not quick tunnel) in openapi_gpt.yaml → servers[0].url.
  External RB email is backup only — doorbell copy with top 3 priorities and cockpit
  link; does not contain the full brief.
  Source health: system/.cache/source_health.json (written by refresh_sources.py
  --save-health). Morning path test: system/scripts/morning_path_test.py.
```

Add a new Compute layer row:

```
| `morning_path_test.py` | LIVE | End-to-end morning path test: refresh → brief →
  publish → API verify. Writes system/.cache/morning_path_test.json. 5 automated
  steps + 2 manual ChatGPT verification steps. |
```

Update `source_health.json` in `.cache/`:

```
| `system/.cache/source_health.json` | LIVE | Per-source health report written by
  refresh_sources.py --save-health. Read by daily_brief.py to populate Daily Prep
  Summary confidence section. |
```

**New `system/protocols/P-032_native_chatgpt_task_delivery.md`**

```markdown
---
id: P-032
title: Native ChatGPT Task Delivery
trigger: runs automatically at 05:05 CT as a ChatGPT scheduled task
reads: GET /daily_brief?use_cache=true (via Cloudflare tunnel + Custom GPT action)
writes: ChatGPT task result message (native, inside ChatGPT platform)
---

# P-032 — Native ChatGPT Task Delivery

## Purpose

Make the morning brief land inside ChatGPT as a native task result rather than
requiring Todd to open a link and type a command. The native task email has a
"View message" button that opens directly to the rendered brief.

## Architecture

1. A scheduled ChatGPT Task is created inside the Relationship Bridge Custom GPT.
2. At 5:05 AM CT the task fires, calls getDailyBrief, and renders the canonical brief.
3. ChatGPT sends a native push notification and task-result email to Todd.
4. Todd taps "View message" → brief is already there.

## Prerequisites

- Cloudflare named tunnel (not quick tunnel) configured and running on the host Mac.
- `openapi_gpt.yaml → servers[0].url` set to the stable named-tunnel URL.
- Custom GPT Actions republished after URL change.
- LaunchAgent fires at 05:00 CT to ensure brief is cached before the task fetches it.

## Creating the task

Open the Relationship Bridge Custom GPT. Send:

  Schedule a daily task at 5:05 AM Central.

Paste the task prompt from `system/automation/CHATGPT_TASK_DAILY_BRIEF_PUSH.md`
→ "Target Task Prompt".

## Fallback behavior

If getDailyBrief fails or the tunnel is down:
- The task result message should say: "RB brief unavailable — [error]. Open the
  cockpit and send: Show today's RB Daily Brief."
- The backup RB email (if transport configured) arrives as a doorbell; Todd opens
  the cockpit and sends the fallback command.
- The GPT is configured (step 0 of custom_gpt_prompt.md) to call getDailyBrief
  immediately on that command.

## Maintenance

- When the Cloudflare tunnel URL changes (e.g., new named tunnel or domain), update
  `openapi_gpt.yaml → servers[0].url` and republish the Custom GPT Actions.
- Run `validate_openapi_gpt.py` after any spec change.
- If the task stops firing, check: (a) ChatGPT Task is still active in ChatGPT
  settings; (b) tunnel is running; (c) API key has not rotated.
```

---

## Non-Goals for 9.2

Do not reopen the 9.1 strategic operator architecture unless a real defect is found.

Do not add numeric strategic operator scores.

Do not turn the Daily Brief into a generic restaurant industry newsletter.

Do not expose `refresh_sources.py` via the Custom GPT or MCP in 9.2. The auth
story for GPT-driven source refresh is not ready.

Do not pretend external email can deep-link into a prefilled Custom GPT message.

Do not expand `daily_brief.py` substantially for the source health section — a
small `_load_source_health()` helper is sufficient.

Do not build a new hosted service. The Cloudflare tunnel exposes the existing
FastAPI server. No separate cloud deployment is needed in 9.2.

Do not implement named-tunnel setup in code. That is a Todd host-side task
(see Appendix A).

Do not mark any delivery step as successful unless its transport path confirmed
success. `publish.py` already enforces `pending_transport`; maintain that
discipline in the new endpoints.

---

## Appendix A — Named Cloudflare Tunnel Setup (Todd's responsibility, not Codex)

The current `openapi_gpt.yaml` uses a quick tunnel URL
(`trycloudflare.com`). Quick tunnels generate a new URL each restart and
break the Custom GPT Action. Replace it with a named tunnel:

```bash
# 1. Install cloudflared if not present
brew install cloudflare/cloudflare/cloudflared

# 2. Authenticate (opens browser)
cloudflared tunnel login

# 3. Create a named tunnel
cloudflared tunnel create rb-api

# 4. Note the tunnel UUID printed; create config at ~/.cloudflared/config.yml
cat > ~/.cloudflared/config.yml << 'EOF'
tunnel: <tunnel-UUID>
credentials-file: ~/.cloudflared/<tunnel-UUID>.json
ingress:
  - hostname: rb-api.<your-cloudflare-zone>.com
    service: http://localhost:8000
  - service: http_status:404
EOF

# 5. Add DNS record
cloudflared tunnel route dns rb-api rb-api.<your-zone>.com

# 6. Run the tunnel (add to LaunchAgent for auto-start)
cloudflared tunnel run rb-api
```

After setup:
1. Update `openapi_gpt.yaml → servers[0].url` to `https://rb-api.<your-zone>.com`
2. Run `python3 system/scripts/validate_openapi_gpt.py`
3. Paste the updated schema into ChatGPT → Relationship Bridge GPT → Configure →
   Actions → Edit and republish.

If you do not have a Cloudflare-managed domain, use the quick tunnel as a temporary
measure and regenerate the Custom GPT Actions URL each session. The named tunnel
is required for the ChatGPT Task to work reliably across Mac restarts.

---

## Appendix B — P-019 First-Run Checklist (Todd's responsibility)

Source health will report `messages` and `calls` as `unavailable` until FDA is
granted. This is expected. To unblock those sources:

```
1. System Settings → Privacy & Security → Full Disk Access
   → Add Terminal (or iTerm2 or the Python launcher you use)
   → Toggle on

2. python3 system/scripts/fetch_apple_messages.py --days 365
   expect: system/inbox/messages.json written

3. python3 system/scripts/fetch_apple_calls.py --days 365
   expect: system/inbox/calls.json written

4. python3 system/scripts/interaction_overlay.py
   expect: matched contacts, proposed last_touch updates, unmatched handles

5. Review unmatched handles; enrich baseline phone fields for recognized contacts

6. python3 system/scripts/interaction_overlay.py --apply-last-touch
   (only after reviewing the proposed updates)
```

After first-run, `refresh_sources.py --messages --calls` will report `refreshed`
and source health will promote messages and calls to `Tier 2 / refreshed`.

---

## Files Modified or Created in RB 9.2 (Master List)

### Priority 1 (source health model)
- `system/scripts/refresh_sources.py` — add `--save-health` flag and write logic
- `system/scripts/refresh_all.py` — pass `--save-health` through to refresh_sources
- `system/scripts/daily_brief.py` — add `_load_source_health()` + Daily Prep Summary integration
- `system/.cache/source_health.json` — new file (written at runtime, git-ignored)

### Priority 2 (LinkedIn messaging ingest)
- `system/scripts/linkedin_messaging.py` — add/confirm `--ingest <csv_path>` CLI mode
- `system/scripts/refresh_sources.py` — update social note to include ingest command
- `system/protocols/P-033_linkedin_messaging_export_ingest.md` — new protocol file
- `system/inbox/linkedin.messages.json` — new file (written after ingest, git-ignored)

### Priority 3 (morning path test)
- `system/scripts/morning_path_test.py` — new test script
- `system/.cache/morning_path_test.json` — new file (written at runtime, git-ignored)

### Priority 4 (spec and cockpit hygiene)
- `system/api/server.py` — add `GET /brief/health` and `GET /brief/latest-json`
- `system/scripts/publish.py` — write `latest_brief.json` alongside `latest.html`
- `system/api/openapi.yaml` — add two new endpoints
- `system/api/openapi_gpt.yaml` — add `GET /brief/health` only (stay ≤30 ops)

### Priority 5 (status and protocol cleanup)
- `system/STATUS.md` — update rb-daily-briefing row; add source_health and morning_path_test rows
- `system/protocols/P-032_native_chatgpt_task_delivery.md` — new protocol file
- `system/automation/CHATGPT_TASK_DAILY_BRIEF_PUSH.md` — add named-tunnel requirement note
