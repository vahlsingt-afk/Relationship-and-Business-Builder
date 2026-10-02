# RB HTTP API

FastAPI wrapper around the same compute and controlled mutation layer the MCP server exposes. This is the surface a Custom GPT (or any AI with HTTP fetch) calls.

## What's here

- `server.py` — the FastAPI app. Mostly read; write endpoints route through the Codex-backed mutation layer in `system/scripts/mutations.py`.
- `openapi.yaml` — the OpenAPI 3.1 schema to upload to ChatGPT's "Add Actions" step when building the Custom GPT.
- `custom_gpt_prompt.md` — the system prompt for the Custom GPT.

## Endpoints

**Read (GET):**
- `/health`
- `/daily_brief`, `/validate_baseline`, `/gap_detection`, `/loops`
- `/network_gap`, `/drr_score`, `/network_analysis`
- `/intro` — broker paths to a target
- `/calendar_overlay`, `/email_overlay`, `/social_overlay`, `/social_outbound_overlay`
- `/post_recommendations`
- `/status`, `/manifest`, `/protocols`, `/active_threads`
- `/cards/{contact_id}`, `/sessions/recent`

**Write (POST):**
- `/loops`, `/loops/close`, `/touch`, `/contacts`
- `/threads`, `/threads/close`
- `/my_posts`, `/engagement`, `/social`
- `/sessions`

## Install

```bash
python3 -m pip install --user fastapi uvicorn pydantic httpx
```

## Smoke-test (in-process, no network port)

```bash
python3 system/scripts/api_smoke_test.py
```

Uses FastAPI's TestClient to verify every GET endpoint returns 200. Run with `RB_API_KEY=test python3 ...` to exercise the auth-header path.

## Run locally

```bash
RB_API_KEY=$(openssl rand -hex 32) uvicorn system.api.server:app --host 0.0.0.0 --port 8765
```

Save the `RB_API_KEY` — you'll paste it into the Custom GPT's Actions auth setting.

Test from another shell:

```bash
curl -H "x-api-key: $RB_API_KEY" http://localhost:8765/daily_brief?date=2026-05-15 | jq
```

## Building the Custom GPT (10-minute walkthrough)

1. Expose your local server to the internet. **Ngrok** is the easiest path for testing:
   ```bash
   ngrok http 8765
   ```
   Note the `https://xyz.ngrok.io` URL.

2. Replace `YOUR-DEPLOYED-DOMAIN.example.com` in `system/api/openapi.yaml` with your ngrok URL.

3. Go to `chatgpt.com` → "Explore GPTs" → "Create" → "Configure" tab.

4. Paste `system/api/custom_gpt_prompt.md` into the **Instructions** field.

5. Click **"Create new action"** under "Actions" section.

6. Paste the entire contents of `system/api/openapi.yaml` into the schema field.

7. Under **Authentication** → choose **API Key** → set:
   - Auth Type: **Custom**
   - Header Name: `x-api-key`
   - API Key: paste your `RB_API_KEY` value

8. Save the action. Test in the right-hand preview pane with prompts like:
   - *"What does today look like?"* — should call `getDailyBrief`
   - *"Why is Bruce Sellnow scored so high?"* — should call `getDrrScore?id=bruce-sellnow`
   - *"Find me an intro to Toast"* — should call `findIntro?target=Toast`
   - *"What should I post about?"* — should call `getPostRecommendations`

9. If everything works, **Save → Configure** → publish to "Only me" (private GPT). Use it for a week. If it holds up, share with a small group.

## Deployment notes

- Run behind a reverse proxy that terminates TLS. Don't expose `:8765` directly.
- Set `RB_API_KEY` to at least 32 random hex characters. Rotate when you share access.
- The CORS policy is `allow_origins=["*"]` for local dev. Tighten for production or remove the middleware (ChatGPT Actions are server-side; CORS isn't needed).
- For multi-user productization, this surface becomes per-tenant. For V0 it's single-tenant; the Custom GPT is private to you.
- Write endpoints are for confirmed execution only. The Custom GPT should state the exact proposed write, wait for user confirmation, call the endpoint, then call the relevant read/validation endpoint and report the result.

## Common issues

- **422 Unprocessable Entity on a write endpoint.** The request body doesn't match the Pydantic model. Check the OpenAPI schema for required fields.
- **401 Unauthorized.** `RB_API_KEY` env var doesn't match the `x-api-key` header.
- **ChatGPT Action says "I can't access that."** The OpenAPI server URL is wrong, the API isn't reachable from ChatGPT (use a public tunnel or hosted instance), or the auth header isn't configured.
- **Endpoint works in curl but not in the GPT.** ChatGPT requires the Action's server URL to be HTTPS in production. Ngrok provides this; localhost does not.

## What's NOT here yet

- Streaming responses (each endpoint returns the full payload at once).
- Multi-tenant routing (V1 SaaS feature).
- WebSocket / long-poll for write-on-change. The GPT polls.
