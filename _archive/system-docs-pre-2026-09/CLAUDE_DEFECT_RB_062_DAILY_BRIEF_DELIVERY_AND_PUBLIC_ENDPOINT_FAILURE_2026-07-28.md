# RB-062 Defect — Daily Brief Delivery and Public Endpoint Failure

Date observed: 2026-07-28
Reported by: Todd
Severity: High
Area: Daily briefing delivery, RB Custom GPT cockpit, public API/tunnel operations

## User-Visible Failure

Todd did not receive the daily intelligence report or CoS briefing in the expected operational UX on 2026-07-28.

## Expected Behavior

The morning system should:

1. Refresh available sources before briefing.
2. Generate the Daily Brief, Intelligence Brief, and team/industry variants around the configured 5:00 AM Central run.
3. Publish the canonical daily brief artifacts.
4. Make the full brief retrievable through the RB Custom GPT / ChatGPT cockpit.
5. Send/trigger the configured notice only after the retrieval path is live.
6. Surface source/data-health gaps honestly in the brief.

## Actual Behavior

Local artifacts for 2026-07-28 exist, but were generated at approximately 08:30 Central, not at the normal 05:00 Central path.

The live delivery check failed before manual intervention:

- `latest_brief_artifact`: pass
- `latest_html_artifact`: pass
- `morning_pipeline_cache`: pass
- `live_health_api`: fail
- public endpoint: `https://rb-api.bridgepointops.org/health`
- observed HTTP status: `530`

The RB Custom GPT / ChatGPT cockpit could not reliably retrieve the brief because the public API endpoint was down.

## Operational Findings

`durable_tunnel_install.py status` reported:

- local API health: connection refused
- public API health: HTTP 530
- API LaunchAgent: installed/loaded but not running
- tunnel LaunchAgent: installed/loaded but not running
- launchd last exit code: `78: EX_CONFIG`

Manual foreground startup of the API and cloudflared tunnel succeeded temporarily, proving:

- the app imports successfully
- the local API can serve when run interactively with approval
- the named Cloudflare tunnel can connect when run interactively

Durable LaunchAgent restart/reinstall did not restore persistent service ownership. Detached background starts also did not persist in the current environment.

## Intelligence Quality Gaps in Today's Brief

Today's generated brief also reported under-instrumentation/staleness:

- LinkedIn messaging stale
- LinkedIn own posts stale
- LinkedIn engagement stale
- strategic operators unavailable
- two Just Press Record captures from 2026-07-27 still pending processing
- source refresh status: partial

This means the missed delivery was not just a notification failure; the intelligence layer also did not fully process available relationship/account material before briefing.

## Required Fix

1. Repair durable API and tunnel LaunchAgents so `launchctl` owns long-running API/tunnel processes without an interactive foreground shell.
2. Add a pre-delivery gate: do not mark daily delivery as successful unless `task_delivery_check.py --live` returns zero failures.
3. If the live API is down at delivery time, push a visible fallback notice in this Codex/ChatGPT thread and mark delivery degraded.
4. Add an alert when generated artifacts are later than the configured 05:00 Central briefing window.
5. Ensure pending JPR captures from the prior day are processed or explicitly called out as unprocessed before the CoS briefing is considered complete.
6. Fix or downgrade source-health logic for `strategic_operators`; it should not be reported as unavailable when canonical strategic operator YAML exists but no raw movement input was found.

## Acceptance Criteria

- `RB_API_KEY=<key> python3 system/scripts/task_delivery_check.py --live --json` reports zero failures after a cold restart.
- `pgrep -fl "uvicorn|cloudflared"` shows durable services running without Codex foreground sessions.
- `/health` and `/brief/health` return HTTP 200 through `https://rb-api.bridgepointops.org`.
- The next morning brief is visible in the RB Custom GPT cockpit by 05:10 Central.
- If the cockpit cannot retrieve the brief, Todd receives an explicit degraded-delivery explanation rather than silence.

