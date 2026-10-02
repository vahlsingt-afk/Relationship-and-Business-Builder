# RB-DEFECT-001 — API Availability Regression

**Date logged:** 2026-05-24  
**Severity:** High  
**Status:** Closed — named tunnel live at `https://rb-api.bridgepointops.org`; automated closure checks passed 2026-05-28.  
**Logged by:** Todd Vahlsing (via Chief of Staff session)  
**Investigated:** 2026-05-24 (same session)  
**Last reviewed:** 2026-05-28 (RB 9.18 closeout)

---

## RB 9.18 Closure Verification (2026-05-28)

The durable named tunnel and API path are live.

```text
RB_API_KEY=localtest python3 system/scripts/tunnel_health_check.py --json
status=alive; server_url=https://rb-api.bridgepointops.org; http_status=200

python3 system/scripts/validate_openapi_gpt.py
validate_openapi_gpt: OK (30 ops)

RB_API_KEY=localtest python3 system/scripts/morning_path_test.py
[PASS] tunnel health: tunnel_alive=true; url=https://rb-api.bridgepointops.org
[PASS] source refresh: source_health.json written; overall_health=partial.
[PASS] daily brief smoke: daily_brief.py --smoke reported 0 failures.
[PASS] publish artifacts: published artifacts exist at system/published/daily/2026-05-28 and latest.html.
[PASS] api daily brief: GET /daily_brief?use_cache=true returned 200 with canonical_brief.
[PASS] brief health: GET /brief/health returned 200 with overall_health.
Morning path: 6/6 blocking automated steps passed; 0 warning(s)
```

`system/api/openapi.yaml` and `system/api/openapi_gpt.yaml` now point to `https://rb-api.bridgepointops.org`.

---

## Historical Closure Steps (Superseded By RB 9.18 Closeout)

The quick tunnel from RB 9.7 is presumed dead. To re-establish the Custom GPT connection **right now** (Option A), or permanently close this defect (Option B — the 10.0 target):

**Option A — Quick restart (2 min, not durable):**
```bash
cd ~/Documents/Claude/Projects/Relationship\ Builder
RB_API_KEY=localtest python3 -m uvicorn system.api.server:app --host 127.0.0.1 --port 8765 &
.tools/cloudflared tunnel --url http://127.0.0.1:8765
# Copy the new *.trycloudflare.com URL, then:
python3 system/scripts/update_tunnel_url.py https://<new-url>
# Re-upload system/api/openapi_gpt.yaml in ChatGPT Builder → Relationship Bridge → Actions
```

**Option B — Named tunnel (closes defect permanently):**
```bash
brew install cloudflared
cloudflared login
cloudflared tunnel create rb-api
cloudflared tunnel route dns rb-api rb-api.<your-domain>.com
# Then install LaunchAgent autostart:
python3 system/scripts/launchagent_install.py  # or follow RB_9_7_DELIVERY_PROOF_OPERATOR_RUNBOOK.md
python3 system/scripts/update_tunnel_url.py https://rb-api.<your-domain>.com
# Re-upload schema in ChatGPT Builder
```

**Verify with:**
```bash
python3 system/scripts/tunnel_health_check.py
python3 system/scripts/morning_path_test.py
```

Close this defect by updating **Status** above to `Closed — named tunnel live` once Option B is complete.

---

## Symptom

All tested RB read endpoints return `ClientResponseError` immediately at the plugin/API layer. No partial data, no connector-specific auth errors — the entire RB surface is unavailable.

## Reproduction Set

All of the following failed during the 2026-05-24 session:

| Endpoint | Category |
|---|---|
| `getDailyBrief` | Core orchestration |
| `getRelationshipSignals` | Signal layer |
| `getCalendarOverlay` | Connector (Google/Microsoft) |
| `getEmailOverlay` | Connector (Gmail) |
| `getNetworkAnalysis` | Core analysis |
| `listActiveThreads` | Internal RB data |
| `getLoops` | Smart loop layer |

## Why This Is Not a Connector OAuth Problem

- `listActiveThreads` and `getLoops` are internal RB data paths with no Google/Gmail/Calendar dependency — they fail too.
- Failures are uniform across all endpoints, not scoped to connector-dependent routes.
- Errors occur immediately at the plugin/API layer, not as partial data or degraded signals.
- If only Google/Microsoft OAuth scopes were revoked, the expected behavior would be:
  - Empty overlays on calendar/email routes
  - Explicit auth/permission error messages
  - Non-connector routes (`listActiveThreads`, `getLoops`) returning data normally

The uniformity of failure points to a platform-level break, not a connector authorization issue.

## Root Cause (Confirmed 2026-05-24)

**The previous Cloudflare quick tunnel expired. The backend is healthy.**

Evidence gathered during investigation:

| Check | Result |
|---|---|
| `morning_pipeline.py --date 2026-05-24` | All steps PASS (refresh, write, publish) |
| `api_smoke_test.py` (40 endpoints, TestClient) | All PASS |
| `latest_brief.json` / `latest.html` | Written at 12:56 UTC today |
| `cloudflared` installed on host | Available via repo-local `.tools/cloudflared` |
| `openapi_gpt.yaml` server URL | Restored to current quick tunnel: `https://reality-events-transcription-cold.trycloudflare.com` |
| `openapi.yaml` server URL | Same current quick tunnel |
| `settings.json` → `public_url_base` | Set to current quick tunnel |
| `tunnel_health_check.py` | PASS, `/health` returns 200 |
| Custom GPT preview | PASS, `Show today's RB Daily Brief` renders the brief via Action |

**What happened:** A Cloudflare quick tunnel (`trycloudflare.com`) was started during a prior sprint session to expose the local FastAPI server to ChatGPT. Quick tunnels are ephemeral — they only resolve while `cloudflared tunnel --url` is actively running in a terminal. When that process ended, the URL went dead. Both OpenAPI schemas still pointed to that dead URL. ChatGPT's Action layer could not reach the backend, returning `ClientResponseError` for every call.

**Current restoration:** A new quick tunnel was started and the OpenAPI schemas were updated. The Custom GPT Action authentication was also corrected to use `x-api-key: localtest`. This restores the live Custom GPT path for the current running tunnel session.

**What is NOT broken:** The local RB compute stack, all Python scripts, all caches, all published artifacts, and the FastAPI server itself are all fully operational.

## Remediation

### Option A — Quick tunnel (minutes, not durable)

Run this in your terminal from the project folder, while `server.py` is running:

```bash
# 1. Start the local API server (if not already running)
cd ~/Documents/Claude/Projects/Relationship\ Builder
python3 system/api/server.py &

# 2. Start a new quick tunnel (requires cloudflared — see install below)
cloudflared tunnel --url http://localhost:8000

# Install cloudflared if missing:
brew install cloudflared
```

Then copy the new `*.trycloudflare.com` URL and:
- Run `python3 system/scripts/update_tunnel_url.py https://<new-url>`
- In ChatGPT Builder → Relationship Bridge 9.0 → Actions → re-upload the updated schema

**Downside:** Dies again when the terminal session ends.

Current temporary URL:

```text
https://reality-events-transcription-cold.trycloudflare.com
```

Current live processes must keep running for the Custom GPT Action to work:

```bash
RB_API_KEY=localtest python3 -m uvicorn system.api.server:app --host 127.0.0.1 --port 8765
.tools/cloudflared tunnel --url http://127.0.0.1:8765
```

### Option B — Named Cloudflare Tunnel (hours, durable) ← Recommended

This is the next delivery-proof sprint's primary objective. It sets up a persistent named tunnel that survives restarts.

```bash
brew install cloudflared
cloudflared login                         # authenticates with your Cloudflare account
cloudflared tunnel create rb-api          # creates a named tunnel
cloudflared tunnel route dns rb-api <your-subdomain>.yourdomain.com
```

Then:
- Set tunnel to autostart via LaunchAgent (same pattern as `morning_pipeline_install.py`)
- Run `python3 system/scripts/update_tunnel_url.py https://<stable-subdomain>`
- Re-upload schema in ChatGPT Builder

**This is the win condition for the next delivery-proof sprint.**

### Verification after either option

```bash
curl -i -H "x-api-key: <key>" "https://<new-url>/health"
curl -i -H "x-api-key: <key>" "https://<new-url>/daily_brief?use_cache=true"
python3 system/scripts/tunnel_health_check.py
python3 system/scripts/morning_path_test.py
```

Then test from the Custom GPT preview and confirm no `ClientResponseError`.

## Remediation Work Completed 2026-05-24

Added:

- `system/scripts/tunnel_health_check.py`
  - Reads `servers[0].url` from `system/api/openapi_gpt.yaml`.
  - Calls `<url>/health` with `x-api-key` from `RB_API_KEY` or `system/settings.json`.
  - Writes `system/.cache/tunnel_health.json`.
  - Prints exact restart/update commands when the tunnel is dead.
- `system/scripts/update_tunnel_url.py`
  - Updates both OpenAPI schemas.
  - Updates `settings.json -> daily_briefing.delivery.public_url_base` when unset.
  - Prints a diff and does not commit.
- `system/scripts/morning_path_test.py`
  - Adds a tunnel-health step and top-level `tunnel_alive` result.
- `system/scripts/validate_openapi_gpt.py`
  - Now enforces the Custom GPT operation-summary length cap so Builder rejects are caught locally.

Verified:

- `validate_openapi_gpt.py`: OK, 28 ops.
- `api_smoke_test.py`: all 40 endpoints pass.
- `tunnel_health_check.py`: tunnel alive, HTTP 200.
- `morning_path_test.py`: 6/6 blocking automated steps passed.
- Custom GPT live preview: returned the RB Daily Brief through `getDailyBrief`.

## Investigation Notes

- All five highest-priority suspected root causes were eliminated: auth token, backend availability, malformed response, manifest drift, and revoked credentials are ruled out.
- The defect framing ("platform regression") was a reasonable initial hypothesis given the uniform failure pattern — but the uniformity was because *all* routes go through the single dead tunnel URL.
- Related: `system/CLAUDE_HANDOFF_2026-05-24_RB_9_6_MORNING_PIPELINE_AND_TASK_DELIVERY.md` → "Stable public Action endpoint" blocker section documented this risk before the defect was formally logged.
- Related architecture: `baseline_index.json` (canonical state), `mutations.py` (write-back), `system/api/server.py` (FastAPI layer).
