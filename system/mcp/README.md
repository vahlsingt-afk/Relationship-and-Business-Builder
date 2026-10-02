# RB MCP server

Exposes the deterministic compute scripts in `system/scripts/` as MCP tools. Any MCP-capable client can call them — Claude Desktop, Claude Code, Cursor, Zed, and (per their roadmaps) ChatGPT and others.

## Why this exists

Until now, every model that wanted to operate Relationship & Business Builder (RB) had to read the canonical files itself, derive everything from `baseline_index.json`, and trust its own arithmetic. That's expensive in tokens and fragile across vendors.

With the MCP server running, a model can call `rb.daily_brief()` and get the answer. The scripts are the calculator; the model is the strategist.

## Tools exposed (19)

**Read tools:**
- `rb.daily_brief` — today's brief report dict
- `rb.validate_baseline` — schema + integrity check
- `rb.gap_detection` — RC/card/contact-field gaps
- `rb.loop_parser` — bucketed loop view
- `rb.network_gap` — unanchored-cluster scoring
- `rb.drr_score` — DRR score (top-N or single id)
- `rb.network_analysis` — strategic report card
- `rb.find_intro` — broker paths to a target
- `rb.calendar_overlay` — meeting overlay with baseline match
- `rb.email_overlay` — inbox overlay with baseline + active-thread matching
- `rb.social_overlay` — LinkedIn feed overlay
- `rb.social_outbound_overlay` — engagement on your posts
- `rb.post_recommendations` — ranked post recommendations
- `rb.refresh_all` — rebuild every cache
- `rb.read_status` — STATUS.md
- `rb.read_manifest` — MANIFEST.md
- `rb.list_protocols` — protocols/index.json
- `rb.list_active_threads` — open threads
- `rb.recent_sessions` — recent session memory

**Write tools:**
- `rb.loop_add`, `rb.loop_close`, `rb.touch`, `rb.contact_add`
- `rb.thread_open`, `rb.thread_close`
- `rb.my_post_add`, `rb.engagement_add`, `rb.social_add`
- `rb.session_end`

## Install

```bash
pip install mcp --break-system-packages
```

The `mcp` package is the official Anthropic Python SDK. Python 3.10+ recommended.

## Smoke-test the compute layer first

Before installing `mcp`, run the smoke test that verifies every tool's underlying compute path works against your real data:

```bash
python3 system/scripts/mcp_smoke_test.py
```

Expected output: `All 19 tool compute paths pass.` If anything fails here, fix it before registering the server — the MCP wrapper can't make broken compute work.

## Run locally (foreground test)

```bash
python3 system/mcp/server.py
```

This starts the server in stdio mode. If `mcp` is installed correctly, it sits waiting for MCP protocol input on stdin and prints responses on stdout. Hit Ctrl+C to exit.

## Register with Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (create it if it doesn't exist):

```json
{
  "mcpServers": {
    "relationship-builder": {
      "command": "python3",
      "args": ["/Users/toddvahlsing/Documents/Claude/Projects/Relationship & Business Builder/system/mcp/server.py"]
    }
  }
}
```

Then **fully quit Claude Desktop and relaunch** (the config is only loaded on startup). In a new conversation, the tools should appear — try a prompt like:

> Use rb.daily_brief to check today's state, then rb.find_intro with target "Toast" to show me the broker path.

If the tools appear, the server is live. If they don't, check Claude Desktop's developer log for errors (Menu → Developer → Open Developer Tools).

## Common issues

- **`mcp` module not found on PATH.** The `python3` in the config might not be the same one where you installed `mcp`. Use an absolute path: `"command": "/usr/local/bin/python3"` or wherever `which python3` points.
- **Tools appear but error on call.** Run `python3 system/mcp/server.py` directly first to see the actual exception. The smoke test catches most of these but a fresh-env install can still surprise.
- **No `tools/list` response.** The protocol handshake may have timed out. Make sure no other process is reading stdin/stdout for the same Python process.
- **`fastapi` errors when calling write tools.** The MCP server doesn't import fastapi. If you're seeing this, you're looking at the HTTP API logs, not the MCP server logs.

## Design notes

- **In-process compute.** Tools import the compute functions directly from `system/scripts/`. One Python interpreter, one baseline load per session. No subprocess overhead.
- **Cache-aware.** `rb.daily_brief` checks `system/.cache/daily_brief.json` before recomputing. The cache header carries the baseline file's `mtime`; if the baseline changed, the cache is rejected automatically.
- **Read-by-default.** Write tools are explicit; nothing the read tools do mutates state.

## What's NOT here yet

- No auth layer. The server is local-only. Don't expose it to the network without one.
- No HTTP transport. For HTTP, use the parallel `system/api/server.py` (FastAPI).
- No team-mode tools. See `system/TEAM_MODE.md` for the V1 design.
