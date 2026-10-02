# Claude Handoff — 2026-05-24 — RB 9.7 Delivery Proof + LinkedIn Signal Ingest

## Executive Intent

RB 9.7 should be a delivery-proof sprint with one integrated intelligence lane.

The visible win:

> Todd wakes up to the native ChatGPT `View message` daily brief, clicks it, and sees an already-rendered canonical RB Daily Brief. No command-paste workaround. No dead Action URL. No “brief unavailable” loop.

The intelligence win:

> LinkedIn is no longer just a stale/missing source warning. RB has a practical, permissioned path for LinkedIn messages and feed observations to enter the morning pipeline as relationship intelligence, market/operator signals, and reviewable next actions.

Do not turn this into broad repo cleanup. Finish the delivery path and make LinkedIn ingestion usable enough to support the daily brief.

---

## Current State At Handoff

### Backend / Daily Brief

Local compute is healthy.

Verified on 2026-05-24:

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

The Custom GPT Action is temporarily restored through a quick Cloudflare tunnel:

```text
https://reality-events-transcription-cold.trycloudflare.com
```

The live Custom GPT preview successfully returned the RB Daily Brief via `getDailyBrief`.

Important caveat: this is still a quick tunnel. It will break when the current tunnel process dies.

### Existing Quick Tunnel Processes

The restored temporary path depends on these host-side processes:

```bash
RB_API_KEY=localtest python3 -m uvicorn system.api.server:app --host 127.0.0.1 --port 8765
.tools/cloudflared tunnel --url http://127.0.0.1:8765
```

RB 9.7 must replace this with a durable endpoint.

### Full Disk Access / LaunchAgent

Todd added Python to Full Disk Access. Verify the LaunchAgent path from host terminal before assuming scheduled morning runs are real:

```bash
python3 system/scripts/morning_pipeline_install.py --run-now
python3 system/scripts/morning_pipeline_install.py --status
python3 system/scripts/morning_pipeline_install.py --logs
```

Expected: launchd run exits 0, logs show `morning_pipeline.py` completed, and latest daily artifacts are rewritten.

### LinkedIn Ingestion Status

LinkedIn is not “unbuilt.” It has partial working components:

1. `system/scripts/linkedin_ingest.py`
   - Full LinkedIn export ZIP classifier/ingester.
   - Recognizes `Connections.csv`, `messages.csv`, `invitations.csv`, `comments.csv`, `shares.csv`, `reactions.csv`.
   - Builds baseline/network deltas and dry-run API output.

2. `system/scripts/linkedin_messaging.py`
   - Parses LinkedIn `messages.csv` export.
   - Writes `system/inbox/linkedin.messages.json`.
   - Produces overlay rows: matched contacts, last-touch proposals, inbound RI candidates, outbound evidence, unmatched recurring participants.
   - Protocol: `system/protocols/P-033_linkedin_messaging_export_ingest.md`.

3. `system/scripts/linkedin_session_reader.py`
   - Ephemeral browser-session feed capture.
   - Does not log in or store credentials.
   - Prints browser-console capture JS and ingests visible posts Todd can already see.
   - Stores short-lived rows in `system/inbox/linkedin.session_captures.jsonl`.
   - Materializes non-expired posts into `system/inbox/social.feed.json`.
   - Default TTL: 36 hours.
   - Protocol: `system/protocols/P-011_social_inbound.md`.

4. `system/scripts/refresh_sources.py`
   - Already understands LinkedIn/social source health.
   - `--social` runs social overlay caches and includes LinkedIn/session-reader surfaces in source health.
   - Recovery command for `linkedin_messaging` tells Todd to download Messages export and run `linkedin_messaging.py`.

Main gap: the LinkedIn flows are script-capable but not operator-smooth or fully wired into the morning pipeline as a first-class readiness lane.

Current LinkedIn smoke status:

```bash
python3 system/scripts/linkedin_session_reader.py --self-test
# linkedin_session_reader self-test OK

python3 system/scripts/linkedin_messaging.py --smoke
# linkedin_messaging smoke complete: 0 failure(s)
```

---

## Sprint Objective

Make RB daily delivery durable and make LinkedIn input operationally usable.

Primary objective:

1. Stable public RB API endpoint for Custom GPT Actions.
2. Native ChatGPT Task daily brief delivery proven end-to-end.

Secondary objective:

3. LinkedIn feed/messages ingestion path is documented, testable, source-health aware, and surfaced in daily brief readiness.

---

## Work Lane A — Durable Delivery Endpoint

### Goal

Replace the quick tunnel with a durable named Cloudflare Tunnel or equivalent stable HTTPS endpoint.

### Required Work

1. Create a named tunnel, preferred name:

```bash
cloudflared tunnel create rb-api
```

2. Route it to a stable DNS name, for example:

```bash
cloudflared tunnel route dns rb-api rb-api.<your-zone>.com
```

3. Configure tunnel ingress to local API:

```yaml
tunnel: <tunnel-uuid>
credentials-file: ~/.cloudflared/<tunnel-uuid>.json

ingress:
  - hostname: rb-api.<your-zone>.com
    service: http://127.0.0.1:8765
  - service: http_status:404
```

4. Run the local FastAPI server under `RB_API_KEY`.

5. Run the named tunnel:

```bash
cloudflared tunnel run rb-api
```

6. Update repo URLs with the helper:

```bash
python3 system/scripts/update_tunnel_url.py https://rb-api.<your-zone>.com
```

This updates:

- `system/api/openapi_gpt.yaml`
- `system/api/openapi.yaml`
- `system/settings.json -> daily_briefing.delivery.public_url_base`

7. Re-publish the updated `system/api/openapi_gpt.yaml` schema in ChatGPT Builder.

8. Confirm Action authentication:

- Type: API Key
- Auth type: Custom
- Header: `x-api-key`
- Value: same as `RB_API_KEY`

### Acceptance Criteria

```bash
curl -i -H 'x-api-key: <key>' 'https://<stable-rb-api>/health'
curl -i -H 'x-api-key: <key>' 'https://<stable-rb-api>/daily_brief?use_cache=true'
RB_API_KEY=<key> python3 system/scripts/tunnel_health_check.py
RB_API_KEY=<key> python3 system/scripts/morning_path_test.py
```

Expected:

- `/health` returns 200.
- `/daily_brief?use_cache=true` returns 200 with `canonical_brief`.
- `tunnel_health_check.py` reports `Tunnel: alive`.
- `morning_path_test.py` reports 6/6 automated blocking steps passed.
- `task_delivery_check.py` no longer warns that `servers[0].url` is a quick tunnel.

---

## Work Lane B — Native ChatGPT Task Proof

### Goal

Make the preferred delivery UX real:

- Native ChatGPT Task at 5:05 AM Central.
- Email from `ChatGPT <noreply@tm.openai.com>`.
- Button: `View message`.
- Clicking it opens the rendered RB Daily Brief.
- No manual `Show today's RB Daily Brief` command needed.

### Required Work

1. Confirm morning pipeline LaunchAgent runs successfully through launchd:

```bash
python3 system/scripts/morning_pipeline_install.py --run-now
python3 system/scripts/morning_pipeline_install.py --status
```

2. Confirm fresh artifacts:

```bash
ls -la system/published/daily/latest_brief.json
ls -la system/published/daily/latest.html
python3 system/scripts/morning_path_test.py
```

3. In Relationship Bridge 9.0 Custom GPT, create or update the native ChatGPT Task using:

```text
system/automation/CHATGPT_TASK_DAILY_BRIEF_PUSH.md
```

Target schedule:

```text
Daily at 5:05 AM Central
```

4. Run an immediate manual preview in GPT Builder:

```text
Show today's RB Daily Brief
```

5. Confirm the task itself sends the native notification/email.

### Acceptance Criteria

- The scheduled task result renders the full brief in ChatGPT.
- The email contains `View message`, not a fallback instruction to paste a command.
- The external RB email is treated as backup-only.
- `system/STATUS.md` is updated only after the native task path is proven.

---

## Work Lane C — LinkedIn Feed + Message Ingest Operationalization

### Goal

Make LinkedIn a practical source for the daily brief without unsafe scraping or credential storage.

RB should support two permissioned input modes:

1. **Messages export mode:** Todd downloads LinkedIn Messages export and drops it into the repo.
2. **Browser-session feed mode:** Todd captures visible posts from his logged-in browser session with the provided JS snippet or an equivalent operator-mediated browser capture.

### C1 — LinkedIn Messages Export

Current script:

```text
system/scripts/linkedin_messaging.py
```

Current protocol:

```text
system/protocols/P-033_linkedin_messaging_export_ingest.md
```

Required improvements:

1. Add or verify a host-friendly command that ingests the standard LinkedIn export ZIP directly if possible.
   - If the ZIP contains `messages.csv`, route it into `linkedin_messaging.py`.
   - If full ZIP handling already exists in `linkedin_ingest.py`, document the exact bridge rather than duplicating parsers.
2. Ensure message ingest supports dry-run and real-write modes clearly.
   - Current `linkedin_messaging.py --ingest` writes by default and `--dry-run` previews.
   - Decide whether this is acceptable or whether the safer operator contract should require `--confirm` for writes.
   - Whatever contract is chosen, update the protocol and smoke tests so it is unambiguous.
3. Ensure source health reads `system/inbox/linkedin.messages.json` and reports:
   - fresh
   - stale
   - missing
   - skipped_no_raw_input
4. Ensure daily brief / relationship signals can surface:
   - matched inbound LinkedIn contact signals,
   - last-touch proposals,
   - outbound follow-up evidence,
   - unmatched recurring LinkedIn participants.
5. Add or update tests so `api_smoke_test.py` or a focused smoke verifies LinkedIn message ingest without mutating canonical files.

Useful commands:

```bash
python3 system/scripts/linkedin_messaging.py --smoke
python3 system/scripts/linkedin_messaging.py --ingest system/inbox/linkedin_messages_export.csv
python3 system/scripts/linkedin_messaging.py --overlay
python3 system/scripts/refresh_sources.py --social --save-health --refresh-signals
```

Acceptance criteria:

- A real or fixture LinkedIn `messages.csv` can be ingested.
- `system/inbox/linkedin.messages.json` is written only in explicit write mode.
- Overlay returns matched contacts and/or clear unmatched diagnostics.
- `source_health.json` includes `linkedin_messaging`.
- Daily brief freshness warnings distinguish “missing export” from “fresh but no signals.”

### C2 — LinkedIn Feed / Browser-Session Reader

Current script:

```text
system/scripts/linkedin_session_reader.py
```

Current protocol:

```text
system/protocols/P-011_social_inbound.md
```

Required improvements:

1. Make the operator flow easier:

```bash
python3 system/scripts/linkedin_session_reader.py --capture-js
python3 system/scripts/linkedin_session_reader.py --ingest --in /path/to/linkedin_posts.json
python3 system/scripts/linkedin_session_reader.py --status
python3 system/scripts/linkedin_session_reader.py --purge
```

2. Consider adding an explicit helper doc or script:

```text
system/automation/LINKEDIN_CAPTURE_OPERATOR_GUIDE.md
```

The guide should explain:

- no password storage,
- no background scraping,
- captures only visible posts from Todd’s logged-in session,
- TTL behavior,
- how the daily brief uses the resulting `social.feed.json`.

3. Confirm `refresh_sources.py --social --save-health` purges expired captures and materializes current ones before overlays. Current code already calls `linkedin_session_reader.py --purge` before social overlays; verify and document that behavior rather than duplicating it.
4. Confirm `social_overlay.py` surfaces:
   - baseline contact posts,
   - active-thread company hits,
   - recurring authors not yet in baseline,
   - topic signals.
5. Add or verify smoke coverage:

```bash
python3 system/scripts/linkedin_session_reader.py --self-test
python3 system/scripts/social_overlay.py --json --cache
```

Acceptance criteria:

- Session-reader self-test passes.
- A fixture browser capture ingests into `linkedin.session_captures.jsonl`.
- Non-expired posts appear in `social.feed.json`.
- Expired posts are purged.
- `refresh_sources.py --social --save-health` reports LinkedIn feed freshness accurately.
- Daily brief can cite LinkedIn feed freshness and signals without implying live LinkedIn access.

### C3 — Safety / Compliance Constraints

Do not:

- Store Todd’s LinkedIn password.
- Build a headless scraper that logs into LinkedIn.
- Circumvent LinkedIn rate limits or access controls.
- Persist raw LinkedIn feed content indefinitely.
- Treat stale LinkedIn data as fresh.
- Auto-mutate baseline from LinkedIn without confirmation.

Do:

- Use operator-provided exports or operator-visible browser-session captures.
- Keep browser-session feed TTL short.
- Use `ri_intake` / passive RI confirmation flow for durable changes.
- Distinguish evidence from inference in the daily brief.

---

## Work Lane D — Delivery Status / Operator Readiness

### Goal

One command should tell Todd whether tomorrow morning will work.

Existing:

```text
system/scripts/task_delivery_check.py
system/scripts/morning_path_test.py
system/scripts/tunnel_health_check.py
```

Required improvements:

1. Ensure one of the checks reports:
   - latest artifact date,
   - LaunchAgent loaded / last exit status,
   - stable URL vs quick tunnel,
   - tunnel health,
   - API `/health`,
   - Custom GPT schema URL,
   - LinkedIn message freshness,
   - LinkedIn feed/session freshness,
   - native Task verification status.

2. `task_delivery_check.py` should keep warning, not failing, on optional LinkedIn sources unless the sprint explicitly marks them required for that day’s brief.

3. `morning_path_test.py` should remain the end-to-end proof and should not mutate canonical data unexpectedly.

Acceptance criteria:

```bash
python3 system/scripts/task_delivery_check.py
RB_API_KEY=<key> python3 system/scripts/morning_path_test.py
```

Expected:

- Delivery readiness is clear.
- If a source is missing/stale, the output gives an exact recovery command.
- LinkedIn source state is visible and not buried.

---

## Suggested Commit Scope

Make one narrow commit for RB 9.7 delivery + LinkedIn operationalization.

Likely files:

```bash
git add \
  system/scripts/tunnel_health_check.py \
  system/scripts/update_tunnel_url.py \
  system/scripts/morning_path_test.py \
  system/scripts/task_delivery_check.py \
  system/scripts/morning_pipeline_install.py \
  system/scripts/refresh_sources.py \
  system/scripts/linkedin_messaging.py \
  system/scripts/linkedin_session_reader.py \
  system/scripts/linkedin_ingest.py \
  system/protocols/P-011_social_inbound.md \
  system/protocols/P-033_linkedin_messaging_export_ingest.md \
  system/automation/README.md \
  system/automation/CHATGPT_TASK_DAILY_BRIEF_PUSH.md \
  system/automation/LINKEDIN_CAPTURE_OPERATOR_GUIDE.md \
  system/api/openapi_gpt.yaml \
  system/api/openapi.yaml \
  system/settings.json \
  system/STATUS.md \
  defects/RB-DEFECT-001_api-availability-regression_2026-05-24.md \
  system/CLAUDE_HANDOFF_2026-05-24_RB_9_7_DELIVERY_PROOF_AND_LINKEDIN_SIGNAL_INGEST.md
```

Adjust the stage set to actual touched files. Do not stage unrelated dirty RB 9.x work.

Suggested commit message:

```text
RB 9.7: stable daily brief delivery and LinkedIn signal ingest readiness
```

---

## Final Acceptance Criteria For Sprint

The sprint is complete only when:

1. Stable HTTPS endpoint replaces quick tunnel in both OpenAPI schemas.
2. Custom GPT Builder schema is updated and saved.
3. Custom GPT preview returns the daily brief via `getDailyBrief`.
4. Native ChatGPT Task is created/updated for 5:05 AM Central.
5. Native ChatGPT `View message` email opens the rendered brief.
6. LaunchAgent morning pipeline runs through launchd with exit 0.
7. `task_delivery_check.py` and `morning_path_test.py` both pass or produce only intentional warnings.
8. LinkedIn message export path is documented, smoke-tested, and source-health visible.
9. LinkedIn browser-session feed capture path is documented, smoke-tested, TTL-bound, and source-health visible.
10. Daily brief does not claim live LinkedIn access unless a fresh export/capture exists.

---

## Claude Execution Notes

- Preserve the backend/presentation split:
  - local RB builds canonical artifacts,
  - ChatGPT renders and notifies.
- Keep LinkedIn ingestion permissioned and operator-mediated.
- Prefer improving the existing scripts over creating a parallel LinkedIn subsystem.
- Use dry-run modes for mutation-oriented tests.
- Do not reset or clean the dirty worktree.
- Update `system/STATUS.md` only after the native delivery path is actually proven.
