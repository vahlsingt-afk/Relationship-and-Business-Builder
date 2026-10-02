---
id: P-035
title: ChatGPT Task Delivery Verification
script: system/scripts/task_delivery_check.py
cache: system/.cache/task_delivery_check.json
trigger: on-demand after initial Task setup; weekly spot-check
---

# P-035 - ChatGPT Task Delivery Verification

## Purpose

Verify that RB's morning delivery chain is live end to end: cached brief,
source-health endpoint, latest published artifact, stable tunnel, Custom GPT
Action, and native ChatGPT Task result.

## Automated Checks

Run:

```bash
python3 system/scripts/task_delivery_check.py
```

The script verifies:

- `GET /brief/health` returns 200 with `overall_health`.
- `GET /daily_brief?use_cache=true` returns 200 with `canonical_brief`.
- `GET /brief/latest-json` returns 200.
- `system/published/daily/latest_brief.json` exists and is dated today.
- `openapi_gpt.yaml -> servers[0].url` is not a quick `trycloudflare.com` tunnel.

The quick-tunnel check is a warning, not a script failure, because Codex can
still validate the local API before Todd finishes the host-side named tunnel.

## Manual Checklist

Todd must confirm each item before STATUS.md can promote delivery to
`LIVE (canonical, verified)`:

- Named Cloudflare Tunnel is running.
- `openapi_gpt.yaml -> servers[0].url` is the named tunnel URL.
- Custom GPT Actions schema is republished in ChatGPT Builder.
- ChatGPT Task was created inside the Relationship Bridge GPT at 5:05 AM CT.
- Task-result email arrived from `ChatGPT <noreply@tm.openai.com>`.
- Clicking "View message" opens the full brief inline with no command required.
- GPT fallback command `Show today's RB Daily Brief.` returns the brief.
- Backup email, if transport is configured, does not contain the full brief.

## Status Promotion Rule

Do not update the `rb-daily-briefing` STATUS row to
`LIVE (canonical, verified)` until the automated checks pass and Todd confirms
the manual checklist.
