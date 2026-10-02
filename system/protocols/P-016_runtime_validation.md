---
id: P-016
title: MCP + HTTP runtime validation
script: system/scripts/mcp_smoke_test.py + system/scripts/api_smoke_test.py
cache: (none — these are validation scripts, not data producers)
reads:
  - system/baseline_index.json
  - system/active_threads.yaml
  - system/inbox/*
  - system/_sessions/index.json
writes: []
inputs: []
trigger: before installing or registering the MCP/HTTP servers; after any change to rb_core.py or the server wrappers
---

# P-016 — Runtime validation for the interface layer

## Purpose

Verify that every MCP tool and every HTTP endpoint will actually work when invoked by a real client, before committing the time to install + register + smoke-test by hand. Catches the runtime issues that compile-checks can't (missing kwargs, dict-key mismatches, JSON serialization edge cases).

## When to run

- Before the first install of the `mcp` or `fastapi` packages — confirms the compute layer is right.
- After any change to `rb_core.py` that touches a function exposed by either server.
- After adding a new tool or endpoint.
- After updating `rb_core` data shapes (e.g. adding a field to the daily-brief report).
- As a CI gate once the project moves to versioned releases.

## Two scripts

**`system/scripts/mcp_smoke_test.py`** — exercises every MCP tool's underlying compute path without requiring the `mcp` library installed. Calls the same functions the tool handlers call, validates the response shape, prints a per-tool pass/fail table. Run this first.

**`system/scripts/api_smoke_test.py`** — exercises every FastAPI endpoint in-process using `fastapi.testclient.TestClient`. Requires `pip install fastapi httpx pydantic`. No network port, no uvicorn — runs entirely in the test client. Run after fastapi is installed.

## Sequence

```bash
# Phase 1 — verify compute (no extra deps):
python3 system/scripts/mcp_smoke_test.py
# Expected: "All N tool compute paths pass."

# Phase 2 — install MCP and register with Claude Desktop:
pip install mcp --break-system-packages
# Edit ~/Library/Application Support/Claude/claude_desktop_config.json
# (see system/mcp/README.md for the snippet)
# Quit + relaunch Claude Desktop, then test the tools in a real conversation.

# Phase 3 — install fastapi and run the HTTP smoke test:
pip install fastapi uvicorn pydantic httpx --break-system-packages
python3 system/scripts/api_smoke_test.py
# Expected: "All N GET endpoints pass."

# Phase 4 — stand up the server and build the Custom GPT:
RB_API_KEY=$(openssl rand -hex 32) uvicorn system.api.server:app --host 0.0.0.0 --port 8765
# In another terminal:
ngrok http 8765
# Follow system/api/README.md to wire the Custom GPT.
```

Each phase can be done independently. Phase 1 is mandatory before phases 2 or 3.

## Failure modes

- **Compute path fails in phase 1.** A function the MCP/HTTP wrapper would call returns the wrong shape, raises, or hangs. The smoke test prints the offending tool + the exception type. Fix in `rb_core.py` and re-run.
- **`mcp` import error.** Wrong Python interpreter or no `mcp` package. Install with `pip install mcp --break-system-packages` and ensure your client uses the same `python3` that has `mcp` available.
- **Claude Desktop doesn't see the tools.** The config file path is wrong, the JSON is malformed, or you didn't quit + relaunch Claude Desktop. Check the dev log.
- **TestClient fails on `fastapi.testclient`.** Means `httpx` isn't installed. Add it: `pip install httpx --break-system-packages`.
- **Custom GPT can't reach the API.** ngrok URL changed (free ngrok rotates per session), local server not running, or the `RB_API_KEY` doesn't match.

## Success criteria

- Phase 1 ✓: every smoke test row shows OK.
- Phase 2 ✓: Claude Desktop responds to a prompt like *"use rb.daily_brief"* and returns the report dict.
- Phase 3 ✓: every endpoint returns 200 in the test client.
- Phase 4 ✓: Custom GPT successfully calls `getDailyBrief` and a `getDrrScore?id=bruce-sellnow` in the preview pane.

When all four phases are green, the interface layer moves from **SCAFFOLDED** to **OPERATING** in STATUS.md.

## Voice

This protocol exists to remove failure modes that would otherwise eat half a day of debugging on first install. Run it. Then trust the layer.
