# RB 9.7 Delivery Proof — Operator Runbook

This runbook covers the **host-terminal steps** for Lane A (stable Cloudflare tunnel) and Lane B (native ChatGPT Task proof). These steps require your local Mac terminal and cannot be automated from within the Cowork sandbox.

Complete Lane A before Lane B.

---

## Lane A — Replace Quick Tunnel with Named Cloudflare Tunnel

### Prerequisites

- `cloudflared` installed: `brew install cloudflared`
- A Cloudflare account with a zone (domain) you control
- The zone is active in Cloudflare (authoritative DNS)

### Step 1 — Authenticate cloudflared

If you have not authenticated cloudflared on this machine:

```bash
cloudflared tunnel login
```

Follow the browser prompt to authorize the zone you want to use.

### Step 2 — Create the named tunnel

```bash
cloudflared tunnel create rb-api
```

Note the tunnel UUID from the output (format: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`). You will need it in Step 3.

### Step 3 — Write the tunnel config

Create `~/.cloudflared/config.yaml` (or update if it exists):

```yaml
tunnel: <tunnel-uuid>
credentials-file: /Users/<your-user>/.cloudflared/<tunnel-uuid>.json

ingress:
  - hostname: rb-api.<your-zone>.com
    service: http://127.0.0.1:8765
  - service: http_status:404
```

Replace `<tunnel-uuid>` with the value from Step 2 and `<your-zone>.com` with your Cloudflare zone.

### Step 4 — Route DNS

```bash
cloudflared tunnel route dns rb-api rb-api.<your-zone>.com
```

Cloudflare will create a CNAME record pointing `rb-api.<your-zone>.com` to the tunnel. This takes effect immediately (no TTL wait needed — Cloudflare manages the record).

### Step 5 — Start the local FastAPI server

In one terminal window (keep it running):

```bash
cd /path/to/Relationship\ Builder
RB_API_KEY=<your-key> python3 -m uvicorn system.api.server:app --host 127.0.0.1 --port 8765
```

Replace `<your-key>` with the value from your `.env` or `system/settings.json`.

### Step 6 — Run the named tunnel

In a second terminal window (keep it running):

```bash
cloudflared tunnel run rb-api
```

You should see `Registered tunnel connection` in the output.

### Step 7 — Update repo URLs

```bash
python3 system/scripts/update_tunnel_url.py https://rb-api.<your-zone>.com
```

This updates:
- `system/api/openapi_gpt.yaml` — `servers[0].url`
- `system/api/openapi.yaml` — `servers[0].url`
- `system/settings.json` — `daily_briefing.delivery.public_url_base`

Verify the update:

```bash
grep -m1 "url:" system/api/openapi_gpt.yaml
# Expected: - url: https://rb-api.<your-zone>.com
```

### Step 8 — Verify the endpoint

```bash
RB_API_KEY=<your-key> python3 system/scripts/tunnel_health_check.py
# Expected: Tunnel: alive; HTTP: 200

RB_API_KEY=<your-key> python3 system/scripts/morning_path_test.py
# Expected: 6/6 blocking automated steps passed; 0 warning(s)
```

Also test manually:

```bash
curl -i -H 'x-api-key: <your-key>' 'https://rb-api.<your-zone>.com/health'
# Expected: HTTP 200

curl -s -H 'x-api-key: <your-key>' 'https://rb-api.<your-zone>.com/daily_brief?use_cache=true' | python3 -m json.tool | head -20
# Expected: JSON with canonical_brief key
```

### Step 9 — Update and republish the Custom GPT schema

1. Open **ChatGPT → Explore GPTs → Relationship Bridge 9.0 → Edit**.
2. Go to **Configure → Actions**.
3. Click **Import from URL** or paste the updated `system/api/openapi_gpt.yaml` contents.
4. Confirm `Authentication`:
   - Type: **API Key**
   - Auth type: **Custom**
   - Header: `x-api-key`
   - Value: your `RB_API_KEY`
5. Click **Save** in GPT Builder.

### Lane A Acceptance Criteria

```bash
python3 system/scripts/task_delivery_check.py
```

Expected:
- `tunnel_url`: **pass** — shows `https://rb-api.<your-zone>.com` (no `trycloudflare.com`)
- `live_health_api` (with `--live`): **pass** — HTTP 200

---

## Lane B — Native ChatGPT Task Proof

Complete Lane A (stable endpoint) before this lane.

### Step 1 — Verify the LaunchAgent is installed and running

```bash
python3 system/scripts/morning_pipeline_install.py --status
```

If not loaded:

```bash
python3 system/scripts/morning_pipeline_install.py
python3 system/scripts/morning_pipeline_install.py --run-now
```

Expected output: `morning_pipeline.py` completes, latest daily artifacts are rewritten.

Verify artifacts:

```bash
ls -la system/published/daily/latest_brief.json
ls -la system/published/daily/latest.html
python3 system/scripts/morning_path_test.py
```

### Step 2 — Check the LaunchAgent schedule

```bash
python3 system/scripts/morning_pipeline_install.py --status
```

Confirm the launchd plist is loaded and `LastExitStatus = 0` from the most recent run.

To tail recent logs:

```bash
python3 system/scripts/morning_pipeline_install.py --logs
```

### Step 3 — Set up or update the native ChatGPT Task

In the Relationship Bridge 9.0 Custom GPT:

1. Open **ChatGPT → Explore GPTs → Relationship Bridge 9.0 → Edit**.
2. Go to the **Tasks** tab (or use the GPT's built-in scheduler if available in your ChatGPT plan).
3. Set the task prompt using the content in:
   ```
   system/automation/CHATGPT_TASK_DAILY_BRIEF_PUSH.md
   ```
4. Set the schedule: **Daily at 5:05 AM Central** (= `10:05 UTC` in winter, `11:05 UTC` in summer).
5. Save the task.

### Step 4 — Run a manual preview

In the GPT chat (not Builder), type:

```
Show today's RB Daily Brief.
```

Confirm the full brief renders inline without any "brief unavailable" message.

### Step 5 — Confirm the native task email

After the scheduled task fires (or after you trigger it manually from Builder), check:

- Email arrives from `ChatGPT <noreply@tm.openai.com>`.
- Subject line matches the daily brief format.
- Email contains a **View message** button (not a command to paste).
- Clicking **View message** opens the full rendered brief inline in ChatGPT.

### Step 6 — Update STATUS.md

Update `system/STATUS.md` **only after** the native task path is proven:

1. Mark the delivery path as stable.
2. Note the named tunnel URL.
3. Note the LaunchAgent schedule confirmed.

---

## Making the Tunnel and Server Persistent (Recommended)

For the tunnel and server to survive reboots, set up two additional LaunchAgents:

**FastAPI server** (`~/Library/LaunchAgents/com.rb.api-server.plist`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.rb.api-server</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/python3</string>
        <string>-m</string>
        <string>uvicorn</string>
        <string>system.api.server:app</string>
        <string>--host</string>
        <string>127.0.0.1</string>
        <string>--port</string>
        <string>8765</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/Relationship Builder</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>RB_API_KEY</key>
        <string>YOUR_KEY_HERE</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
```

**Named tunnel** (`~/Library/LaunchAgents/com.rb.cloudflared-tunnel.plist`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.rb.cloudflared-tunnel</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/cloudflared</string>
        <string>tunnel</string>
        <string>run</string>
        <string>rb-api</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
```

Load both:

```bash
launchctl load ~/Library/LaunchAgents/com.rb.api-server.plist
launchctl load ~/Library/LaunchAgents/com.rb.cloudflared-tunnel.plist
```

---

## Final Checklist

Run this before marking the sprint done:

```bash
python3 system/scripts/task_delivery_check.py --live
RB_API_KEY=<key> python3 system/scripts/morning_path_test.py
```

Verify manually:
- [ ] `tunnel_url` check is **pass** (named tunnel, not trycloudflare.com)
- [ ] `live_health_api` check is **pass** (HTTP 200 from stable URL)
- [ ] `launchagent_loaded` is **pass**
- [ ] `latest_brief_artifact` is **pass** (dated today)
- [ ] Custom GPT Builder schema is saved with stable URL
- [ ] Native ChatGPT Task is scheduled at 5:05 AM Central
- [ ] Task email arrives with **View message** button
- [ ] Clicking **View message** renders the full brief (no command required)
- [ ] `system/STATUS.md` updated to reflect stable delivery path
