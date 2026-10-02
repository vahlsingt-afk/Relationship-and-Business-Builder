---
id: P-032
title: Native ChatGPT Task Delivery
trigger: runs automatically at 05:05 CT as a ChatGPT scheduled task
reads: GET /daily_brief?use_cache=true (via Cloudflare tunnel + Custom GPT action)
writes: ChatGPT task result message (native, inside ChatGPT platform)
---

# P-032 - Native ChatGPT Task Delivery

## Purpose

Make the morning brief land inside ChatGPT as a native task result rather than
requiring Todd to open a link and type a command. The native task email has a
"View message" button that opens directly to the rendered brief.

## Architecture

1. A scheduled ChatGPT Task is created inside the Relationship Bridge Custom GPT.
2. At 5:05 AM CT the task fires, calls getDailyBrief, and renders the canonical brief.
3. ChatGPT sends a native push notification and task-result email to Todd.
4. Todd taps "View message" and the brief is already there.

## Prerequisites

- Cloudflare named tunnel, not a quick tunnel, configured and running on the host Mac.
- `openapi_gpt.yaml -> servers[0].url` set to the stable named-tunnel URL.
- Custom GPT Actions republished after the URL change.
- LaunchAgent fires at 05:00 CT to ensure the brief is cached before the task fetches it.

## Creating The Task

Open the Relationship Bridge Custom GPT. Send:

```text
Schedule a daily task at 5:05 AM Central.
```

Paste the task prompt from `system/automation/CHATGPT_TASK_DAILY_BRIEF_PUSH.md`
under "Target Task Prompt".

## Fallback Behavior

If getDailyBrief fails or the tunnel is down:

- The task result message should say: "RB brief unavailable - [error]. Open the
  cockpit and send: Show today's RB Daily Brief."
- The backup RB email, if transport is configured, arrives as a doorbell; Todd
  opens the cockpit and sends the fallback command.
- The GPT is configured to call getDailyBrief immediately on that command.

## Maintenance

- When the Cloudflare tunnel URL changes, update
  `openapi_gpt.yaml -> servers[0].url` and republish the Custom GPT Actions.
- Run `python3 system/scripts/validate_openapi_gpt.py` after any spec change.
- If the task stops firing, check that the ChatGPT Task is active, the tunnel is
  running, and the API key has not rotated.
