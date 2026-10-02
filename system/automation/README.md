# `system/automation/` — scheduled background jobs

Holds the macOS LaunchAgent and any future scheduled-job configuration. The point: things that should "just happen" without the operator opening a terminal.

Today: the Apple Messages + Call History fetchers (P-019) run on a schedule, and the RB morning pipeline can run before the native ChatGPT Task. Without this layer, the operator would have to manually invoke source refresh, RI processing, market-signal ranking, daily brief generation, and publication every day. The automation makes the integration *real product*, not just scripts.

## What runs

| Job | Schedule | What it does | Where logs go |
|---|---|---|---|
| `com.relationshipbuilder.interaction-fetch` | 06:30, 12:30, 18:30 local + at every login | Runs both fetchers (30-day window) and refreshes the interaction-overlay cache. | `system/automation/interaction-fetch.log` / `.error.log` |
| `com.relationshipbuilder.morning-pipeline` | 05:00 local | Runs the canonical morning build: source refresh, RI/signal caches, strategic operators, daily brief cache, published artifacts. | `system/automation/morning-pipeline.log` / `.error.log` |

## Morning pipeline design

RB/Codex owns the backend intelligence work:

1. Refresh every reachable local or connector-derived source.
2. Normalize RI, relationship signals, passive RI candidates, loops, calendar/email overlays, strategic operators, and market/operator/macro signals into local caches.
3. Build the canonical daily brief.
4. Write the local source of truth:
   - `system/.cache/daily_brief.json`
   - `system/published/daily/<YYYY-MM-DD>/brief.json`
   - `system/published/daily/latest_brief.json`
   - `system/published/daily/latest.html`
5. Expose the compact canonical brief through `GET /daily_brief?use_cache=true`.

ChatGPT owns presentation and notification:

1. The native ChatGPT Task runs at 05:05 local.
2. It retrieves the prebuilt RB artifact through the RB Action.
3. It renders the canonical response and sends the native `View message` notification/email.
4. It does not research news live, invent freshness, or rewrite canonical state.

## Install (V0 — your personal Mac, one-time)

```bash
cd ~/Documents/Claude/Projects/Relationship\ Builder
python3 system/scripts/launchagent_install.py
python3 system/scripts/morning_pipeline_install.py
```

The installer:

1. Writes `~/Library/LaunchAgents/com.relationshipbuilder.interaction-fetch.plist` from the template, substituting your actual paths.
2. `launchctl load`s it so it runs on schedule + at every login.
3. Prints the **Full Disk Access** guidance (see below — this is the gotcha).

The morning-pipeline installer writes
`~/Library/LaunchAgents/com.relationshipbuilder.morning-pipeline.plist`,
loads it at 05:00 local, and prints the same Full Disk Access guidance.

## Critical: Full Disk Access for python3

A nasty macOS detail: the Full Disk Access grant you gave **Terminal** does NOT propagate to processes spawned by **launchd** (the background scheduler). Those run in a different security context. You need to grant FDA directly to the Python interpreter the LaunchAgent invokes. This applies both to the interaction fetcher and to the morning pipeline, because the morning pipeline reads the project under Documents plus local inbox/cache artifacts.

The installer prints the exact path of the python3 it wrote into the plist. Grant FDA to that binary:

1. **System Settings** → **Privacy & Security** → **Full Disk Access**.
2. Click the **+** button (authenticate if asked).
3. Press **Cmd+Shift+G** in the file picker. (This lets you type a path; otherwise the picker hides binaries.)
4. Paste the python3 path the installer told you (likely `/usr/bin/python3` or `/Library/Frameworks/Python.framework/Versions/3.x/bin/python3`).
5. Click **Open** and confirm the toggle next to it is **on**.

After the grant, kick off one immediate run to confirm everything is wired:

```bash
python3 system/scripts/launchagent_install.py --run-now
python3 system/scripts/launchagent_install.py --status
python3 system/scripts/launchagent_install.py --logs

python3 system/scripts/morning_pipeline_install.py --run-now
python3 system/scripts/morning_pipeline_install.py --status
python3 system/scripts/morning_pipeline_install.py --logs
```

The `--logs` output should show `=== interaction_fetch_wrapper start ===` followed by `Wrote NNNN message events…` and `Wrote NN call events…`. If you see `sqlite3.DatabaseError: authorization denied`, FDA was not granted to that specific python binary.

## Commands

| Command | What it does |
|---|---|
| `python3 system/scripts/launchagent_install.py` | Install + load the LaunchAgent. Default. Idempotent. |
| `python3 system/scripts/launchagent_install.py --status` | Show plist + launchctl state + latest fetch output timestamps. |
| `python3 system/scripts/launchagent_install.py --logs` | Tail the last 50 lines of both log files. |
| `python3 system/scripts/launchagent_install.py --run-now` | Trigger an immediate fetch outside the regular schedule. |
| `python3 system/scripts/launchagent_install.py --uninstall` | Remove the LaunchAgent. Stops scheduled runs. |
| `python3 system/scripts/morning_pipeline.py` | Run the full morning intelligence build and artifact publication now. |
| `python3 system/scripts/morning_pipeline_install.py` | Install + load the 05:00 daily morning pipeline LaunchAgent. |
| `python3 system/scripts/morning_pipeline_install.py --run-now` | Trigger the morning pipeline immediately. |
| `python3 system/scripts/morning_pipeline_install.py --status` | Show LaunchAgent state and whether latest artifacts exist. |
| `python3 system/scripts/morning_pipeline_install.py --logs` | Tail the morning pipeline logs. |
| `python3 system/scripts/morning_pipeline_install.py --uninstall` | Remove the morning pipeline LaunchAgent. |

## Why this is V0 and not the final answer

LaunchAgent + manual FDA grant is the right path for a developer-operator running their own instance. It is **not** the right path for a non-developer who downloads RB from a marketplace and wants their phone/text data flowing in two clicks.

See `system/automation/MACOS_HELPER_APP.md` for the V1 productized design — a signed macOS Helper App with native FDA prompting, menu-bar status, and one-click install. That's what ships when this becomes a SaaS product.

## Privacy posture

Same as P-019. The LaunchAgent runs **locally** on the operator's Mac. Nothing is sent to any third-party service. The fetcher output (`messages.json`, `calls.json`) lives in `system/inbox/`, which is git-ignored. The log files in `system/automation/` are also git-ignored to avoid accidental commit of message metadata.

## Common issues

- **`sqlite3.DatabaseError: authorization denied` in the logs.** FDA was not granted to the python3 binary the plist invokes. Re-read the FDA section above. Check the plist (`cat ~/Library/LaunchAgents/com.relationshipbuilder.interaction-fetch.plist`) to confirm which python3 it's using.
- **`launchctl load` fails with "Service is disabled".** Re-run with `launchctl bootstrap gui/$(id -u) <plist>` instead — newer macOS prefers that syntax in some contexts. (The installer uses `load -w` which is the older but more compatible form.)
- **The interaction LaunchAgent runs but the daily brief doesn't see the data.** The fetcher succeeded after the brief was generated. Run `python3 system/scripts/morning_pipeline.py` after the fetch completes, or rely on the 05:00 morning pipeline for the canonical artifact.
- **The native ChatGPT Task says the brief is unavailable.** Check `python3 system/scripts/morning_pipeline_install.py --status`, then confirm the API server and stable Cloudflare tunnel are running and the Custom GPT Action schema points to that tunnel.
- **Repeated FDA prompts at every login.** macOS sometimes resets FDA after major updates. Re-grant; the LaunchAgent itself doesn't need reinstallation.
