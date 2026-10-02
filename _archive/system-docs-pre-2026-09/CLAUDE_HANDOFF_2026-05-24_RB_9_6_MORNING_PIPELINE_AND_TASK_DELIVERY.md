# Claude Handoff — 2026-05-24 — RB 9.6 Morning Pipeline + Native Task Delivery

## Current Recommendation

The right design is now explicit:

- **RB/Codex/local automation owns backend intelligence.**
  It refreshes available sources, normalizes RI, updates signal caches, ranks strategic operator / industry / macro / non-macro market signals, builds the canonical daily brief, and publishes local artifacts.
- **ChatGPT owns presentation and notification.**
  A native ChatGPT Task should retrieve the prebuilt canonical artifact, render it, and send the `ChatGPT <noreply@tm.openai.com>` email/push with the `View message` button.
- ChatGPT should **not** do live news research, source refresh, RI mutation, or freshness inference inside the scheduled task.

This matches Todd's desired UX: a ChatGPT Task result message with the brief already rendered, not an external fallback email that asks Todd to paste a command.

## Completed In This Sprint

### RB 9.5 commit

Committed earlier:

- Commit: `dd900a7`
- Message:
  `RB 9.5: canonical response contract, eval harness (5 scenarios), canonical_response blocks on ri_intake/passive_ri/strategic_memory, GPT prompt contract section, passive RI confirmation write smoke`

The Custom GPT full prompt was too long for the Instructions field, so:

- Compact instructions were created at `system/api/custom_gpt_instructions_8k.md`.
- The compact instructions were pasted into the Custom GPT Instructions field.
- Full `system/api/custom_gpt_prompt.md` was uploaded as Knowledge.
- Custom GPT was saved successfully.

### Daily brief email / UX issue

Todd reported that the daily brief email looked like an external plain-text fallback instead of the native ChatGPT Task email with `View message`, and the fallback command failed.

Diagnosis:

- The screenshot Todd wanted is native ChatGPT Task delivery.
- The external RB email cannot reproduce the `View message` behavior.
- The external email should be backup only.

Changes:

- `system/scripts/publish.py`
  - Backup email now clearly labels itself as backup.
  - CTA is a button-style link to Relationship Bridge 9.0.
  - Copy says the native ChatGPT Task is the preferred UX.
- `system/settings.json`
  - `daily_briefing.delivery.subject_template` updated to:
    `RB Daily Brief backup is ready - {weekday}, {date}`

### API action reliability

Problem:

- Local daily brief generation worked, but `getDailyBrief` returned a very large payload.
- Custom GPT preview showed `[debug] Response received` followed by `ClientResponseError`.
- Likely causes were oversized/fragile action response and unstable tunnel/action layer.

Changes:

- `system/api/server.py`
  - `GET /daily_brief` now returns a compact canonical action payload.
  - It prefers `system/published/daily/<date>/brief.json` or `latest_brief.json` when `use_cache=true`.
  - It preserves the top-level `canonical_brief` key.
  - Full artifact remains available at `/brief/latest-json`.

Local verification:

- `GET /daily_brief?date=2026-05-24&use_cache=true` with `x-api-key` returned HTTP 200.
- Compact action payload size was ~37 KB.
- `/brief/latest-json` still returns the full artifact.

### Morning pipeline design implemented

Added:

- `system/scripts/morning_pipeline.py`
  - Runs the full morning backend pipeline.
  - Refreshes intelligence caches.
  - Writes `today.md` / `MANIFEST.md`.
  - Publishes canonical artifacts.
  - Writes `system/.cache/morning_pipeline.json`.
- `system/scripts/morning_pipeline_install.py`
  - Installs a macOS LaunchAgent for the 5:00 AM local morning pipeline.
  - Provides `--run-now`, `--status`, `--logs`, `--uninstall`.
- `system/automation/com.relationshipbuilder.morning-pipeline.plist.template`
  - LaunchAgent template for `com.relationshipbuilder.morning-pipeline`.
- `system/automation/README.md`
  - Now documents the backend/presentation split and morning-pipeline commands.
- `system/automation/CHATGPT_TASK_DAILY_BRIEF_PUSH.md`
  - Now instructs the native ChatGPT Task to call `getDailyBrief(use_cache=true)` and render the returned `canonical_brief`.
  - Explicitly says not to research news live or infer source freshness.
- `.gitignore`
  - Now ignores `system/automation/*.log` and `system/automation/*.error.log`.

Direct pipeline verification:

```bash
python3 system/scripts/morning_pipeline.py --date 2026-05-24
```

Passed:

- `refresh_intelligence_caches`
- `write_today_and_manifest`
- `publish_canonical_artifacts`

Artifacts written:

- `system/published/daily/latest_brief.json` (~173 KB)
- `system/published/daily/latest.html` (~95 KB)
- `system/.cache/morning_pipeline.json` (~3.6 KB)

### Tests / checks passed

These passed after the morning pipeline work:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/rb_pycache python3 -m py_compile \
  system/scripts/morning_pipeline.py \
  system/scripts/morning_pipeline_install.py

python3 system/scripts/morning_pipeline.py --date 2026-05-24
python3 system/scripts/task_delivery_check.py --smoke
python3 system/scripts/api_smoke_test.py
python3 system/scripts/morning_pipeline.py --date 2026-05-24 --json | python3 -m json.tool >/dev/null
```

`api_smoke_test.py` passed all 40 endpoints.

## Current Blockers / Manual Steps

### 1. macOS Full Disk Access for launchd Python

The 5:00 AM LaunchAgent was installed and loaded:

- Label: `com.relationshipbuilder.morning-pipeline`
- Plist:
  `/Users/toddvahlsing/Library/LaunchAgents/com.relationshipbuilder.morning-pipeline.plist`
- Program:
  `/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3.9`
- Script:
  `system/scripts/morning_pipeline.py`

But `launchctl kickstart` failed with:

```text
Operation not permitted
```

The direct script run works. The scheduled launchd run is blocked by macOS privacy permissions.

Todd needs to grant Full Disk Access to:

```text
/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3.9
```

Then verify:

```bash
python3 system/scripts/morning_pipeline_install.py --run-now
python3 system/scripts/morning_pipeline_install.py --logs
python3 system/scripts/morning_pipeline_install.py --status
```

Expected after FDA: the LaunchAgent exits 0 and rewrites the latest artifacts.

### 2. Stable public Action endpoint

Temporary restoration completed after this handoff was first drafted.

Custom GPT Actions currently point to:

```text
https://reality-events-transcription-cold.trycloudflare.com
```

Current state:

- This is a Cloudflare quick tunnel.
- It works right now while the local API and `.tools/cloudflared` processes are running.
- The Custom GPT Action schema was updated and accepted by Builder.
- The Custom GPT Action auth was corrected to `x-api-key: localtest`.
- Live Custom GPT preview now returns the RB Daily Brief through `getDailyBrief`.

New tooling added for RB-DEFECT-001:

- `system/scripts/tunnel_health_check.py`
  - Checks `openapi_gpt.yaml -> servers[0].url`.
  - Calls `<url>/health` with `RB_API_KEY` or the configured API key.
  - Writes `system/.cache/tunnel_health.json`.
  - Prints restart/update commands when dead.
- `system/scripts/update_tunnel_url.py`
  - Updates both OpenAPI schemas and `settings.json`.
  - Prints a diff.
  - Does not commit.
- `system/scripts/morning_path_test.py`
  - Now includes tunnel health and reports top-level `tunnel_alive`.
- `system/scripts/validate_openapi_gpt.py`
  - Now catches Builder's operation-summary length cap locally.

Verification completed:

```bash
python3 system/scripts/validate_openapi_gpt.py
# validate_openapi_gpt: OK (28 ops)

python3 system/scripts/api_smoke_test.py
# All 40 endpoints pass.

RB_API_KEY=localtest python3 system/scripts/tunnel_health_check.py
# Tunnel: alive; HTTP: 200

RB_API_KEY=localtest python3 system/scripts/morning_path_test.py
# Morning path: 6/6 blocking automated steps passed; 0 warning(s)
```

Remaining problem:

- This quick tunnel will die when the terminal/session dies.
- The next delivery-proof sprint must replace it with a named Cloudflare Tunnel or stable hosted HTTPS endpoint.

Next sprint should set up a named Cloudflare Tunnel or other stable HTTPS endpoint, update:

- `system/api/openapi_gpt.yaml`
- `system/api/openapi.yaml` if keeping them aligned
- Custom GPT Action schema in ChatGPT Builder

Then re-test:

```bash
curl -i -H 'x-api-key: <key>' 'https://<stable-rb-api>/health'
curl -i -H 'x-api-key: <key>' 'https://<stable-rb-api>/daily_brief?use_cache=true'
python3 system/scripts/task_delivery_check.py
python3 system/scripts/tunnel_health_check.py
python3 system/scripts/morning_path_test.py
```

### 3. Native ChatGPT Task creation/update

Once the stable endpoint works, update/create the native ChatGPT Task inside Relationship Bridge 9.0 using:

- `system/automation/CHATGPT_TASK_DAILY_BRIEF_PUSH.md`

Target schedule:

- 5:05 AM Central, daily

Expected UX:

- Email from `ChatGPT <noreply@tm.openai.com>`
- Button: `View message`
- Click opens the ChatGPT task result with the full rendered brief.
- No manual `Show today's RB Daily Brief` command required.

## Files Intentionally Touched In This Sprint

Core latest-round files:

- `.gitignore`
- `system/api/server.py`
- `system/api/openapi.yaml`
- `system/api/openapi_gpt.yaml`
- `system/scripts/publish.py`
- `system/settings.json`
- `system/scripts/morning_pipeline.py`
- `system/scripts/morning_pipeline_install.py`
- `system/scripts/morning_path_test.py`
- `system/scripts/tunnel_health_check.py`
- `system/scripts/update_tunnel_url.py`
- `system/scripts/validate_openapi_gpt.py`
- `system/automation/com.relationshipbuilder.morning-pipeline.plist.template`
- `system/automation/README.md`
- `system/automation/CHATGPT_TASK_DAILY_BRIEF_PUSH.md`
- `defects/RB-DEFECT-001_api-availability-regression_2026-05-24.md`

Prior RB 9.5 / related files in dirty tree may include many unrelated changes from previous sprints. Do not revert them casually.

## Dirty Worktree Warning

The repo contains many pre-existing modified and untracked files from RB 9.x work. Treat this as normal for the current project state.

Do not use destructive cleanup. Do not reset. If committing the RB 9.6 morning-pipeline slice, stage only the files intentionally changed for this slice unless Todd explicitly requests a broader commit.

Recommended stage set for this slice:

```bash
git add \
  .gitignore \
  system/api/server.py \
  system/api/openapi.yaml \
  system/api/openapi_gpt.yaml \
  system/scripts/publish.py \
  system/settings.json \
  system/scripts/morning_pipeline.py \
  system/scripts/morning_pipeline_install.py \
  system/scripts/morning_path_test.py \
  system/scripts/tunnel_health_check.py \
  system/scripts/update_tunnel_url.py \
  system/scripts/validate_openapi_gpt.py \
  system/automation/com.relationshipbuilder.morning-pipeline.plist.template \
  system/automation/README.md \
  system/automation/CHATGPT_TASK_DAILY_BRIEF_PUSH.md \
  system/CLAUDE_HANDOFF_2026-05-24_RB_9_6_MORNING_PIPELINE_AND_TASK_DELIVERY.md \
  defects/RB-DEFECT-001_api-availability-regression_2026-05-24.md
```

Suggested commit message:

```text
RB 9.6: morning pipeline artifact builder and native Task delivery contract
```

## Next Sprint Vision

This should be a **delivery-proof sprint**, not a broad cleanup sprint.

The user-visible outcome is simple:

> Every morning, RB builds the canonical brief locally before Todd wakes up; ChatGPT reads that artifact through a stable Action; Todd receives the native ChatGPT `View message` notification/email; clicking it opens the already-rendered daily brief with no manual command.

Architectural split to preserve:

- **RB/Codex/local automation is the intelligence engine.**
  It refreshes data, computes RI and market/operator/macro signals, writes canonical local artifacts, and exposes a compact API payload.
- **ChatGPT is the presentation and notification surface.**
  It renders the returned `canonical_brief` and sends the native Task notification. It should not perform live research, infer stale-source health, or generate the intelligence layer itself.

The sprint is done only when the native ChatGPT Task path works end-to-end. The backup email is not the win condition.

### Primary Sprint Objective

Make native ChatGPT Task delivery real end-to-end.

### Primary Work Plan

1. Grant/verify Full Disk Access for launchd Python and confirm the 5:00 AM LaunchAgent run succeeds.
2. Install/configure a named Cloudflare Tunnel or stable hosted HTTPS endpoint for the RB API.
3. Update OpenAPI server URL and republish the Custom GPT Action schema.
4. Verify `getDailyBrief(use_cache=true)` succeeds from the live Custom GPT preview.
5. Create/update the native ChatGPT Task at 5:05 AM Central with the target prompt.
6. Confirm the `View message` email opens a rendered daily brief without manual command.

### Focused Cleanup Lane

Do a cleanup pass only where it supports delivery proof. Avoid repo-wide cleanup.

Add these items to the sprint:

1. Commit the RB 9.6 morning-pipeline slice so the delivery foundation is not floating in the dirty tree.
2. Decide whether `system/published/` should be versioned or ignored.
   - Recommendation: generated daily artifacts should usually be ignored unless Todd wants a committed historical brief archive.
   - If ignored, add the pattern intentionally and document how ChatGPT/API reads the local generated artifact.
3. Decide whether `system/api/custom_gpt_instructions_8k.md` should be committed.
   - Recommendation: commit it as the canonical "Custom GPT Instructions field" deployment copy.
4. Add or update a small delivery-status command/checklist that reports:
   - latest artifact date,
   - LaunchAgent loaded/last exit status,
   - API `/health`,
   - current OpenAPI server URL,
   - whether the URL is a quick tunnel or stable endpoint,
   - Custom GPT preview verification status,
   - native Task verification status.
5. Update `system/STATUS.md` only after the native Task path is proven.

Do **not** clean every dirty file. The repo has active RB 9.x work in flight. The cleanup goal is traceability for the delivery path, not a spotless tree.

### Acceptance Criteria

- `morning_pipeline_install.py --run-now` exits 0 through launchd.
- `latest_brief.json` and `latest.html` are dated today after the scheduled path runs.
- Public HTTPS endpoint returns 200 for `/health` and `/daily_brief?use_cache=true` with auth.
- Custom GPT preview renders the brief from `getDailyBrief`.
- Native ChatGPT Task email arrives from ChatGPT with `View message`.
- Clicking `View message` opens the brief inline.
- The external RB email is clearly backup-only and is not treated as successful native delivery.
- RB 9.6 delivery-path changes are committed in a narrow commit or explicitly staged for review.
