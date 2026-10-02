# Claude Handoff — Last-Sprint Carry-Overs (closure)

**Date:** 2026-05-19
**Builder:** Claude (Cowork)
**Scope:** The two carry-overs from `CLAUDE_HANDOFF_2026-05-19_RETURN.md`:

1. Unified `refresh_sources.py` wrapper (deferred in the prior pass).
2. `api_smoke_test.py` round-trip verification (sandbox couldn't run fastapi).

Both are now closed in this pass. The next sprint (RI event intake foundation, post-2026-05-19 testing) starts cleanly after you verify the smoke test passes from your terminal.

---

## 1 — refresh_sources.py is in

**New file:** `system/scripts/refresh_sources.py`

Local-only CLI wrapper over the per-source fetchers. **No FastAPI route, no MCP tool, no Custom GPT exposure.** The auth story for GPT-driven source refresh isn't ready, and the local CLI is enough for the operator (the original rationale from `CLAUDE_HANDOFF_2026-05-19.md`).

### Usage

```bash
# Refresh everything (Mac-only sources will report not_applicable_on_platform off-Mac)
python3 system/scripts/refresh_sources.py --all

# Just one source
python3 system/scripts/refresh_sources.py --messages
python3 system/scripts/refresh_sources.py --calls
python3 system/scripts/refresh_sources.py --email
python3 system/scripts/refresh_sources.py --calendar
python3 system/scripts/refresh_sources.py --social

# Combine, optionally rebuild relationship_signals cache after
python3 system/scripts/refresh_sources.py --messages --calls --refresh-signals

# JSON-tail output for downstream parsers
python3 system/scripts/refresh_sources.py --all --json
```

### Per-source behavior

| Flag | Underlying action | What it reports off-Mac / when raw missing |
|---|---|---|
| `--messages` | `fetch_apple_messages.py --days 30` + one `interaction_overlay.py --cache` | `not_applicable_on_platform` if `chat.db` is missing |
| `--calls` | `fetch_apple_calls.py --days 30` + one `interaction_overlay.py --cache` | `not_applicable_on_platform` if `CallHistory.storedata` is missing |
| `--email` | Per enabled account in `inbox/accounts.yaml`: if a `raw_gmail_threads.<id>.json` (or `raw_email.<id>.json`, or `raw.<id>.email.json`) is in `inbox/`, normalizes via `fetch_via_session.py email --account <id>` | `skipped_no_raw_input` + prints the Cowork MCP command needed to produce the raw file |
| `--calendar` | Same shape as `--email` for `raw_calendar.<id>.json` | Same |
| `--social` | `social_overlay.py --cache` + `social_outbound.py --cache` | Always refreshes the derived caches; warns if `inbox/social.feed.json` is missing |

Per-source result status is one of `refreshed`, `skipped_no_raw_input`, `skipped_disabled`, `not_applicable_on_platform`, `failed`. Exit code is `0` unless something genuinely failed (skipped/not-applicable do not count as failures).

### What the daily brief sees

`system/scripts/relationship_signals.py` → `REFRESH_SPECS` was updated to point every stale-source warning at this wrapper, so the daily brief now surfaces one consistent command shape:

```
Email is 55.7h stale.
  → python3 system/scripts/refresh_sources.py --email
  Run the unified refresh wrapper. It normalizes raw_*.json per enabled
  email account, or prints the exact Cowork MCP command needed when raw
  input is missing.
  Treat 'no new signals from email' as unreliable until this source refreshes.
```

### Validation done from the Cowork sandbox

```text
py_compile system/scripts/refresh_sources.py         : OK
py_compile system/scripts/relationship_signals.py    : OK (REFRESH_SPECS update)
refresh_sources.py --social                          : refreshed (both caches), exit 0
refresh_sources.py --messages --calls                : both reported not_applicable_on_platform, exit 0
refresh_sources.py --email --calendar                : 4 accounts (email:personal, email:bridgepoint, calendar:personal, calendar:bridgepoint) all skipped_no_raw_input with the correct Cowork MCP recipe printed, exit 0
refresh_sources.py --all                             : refreshed 2 (social_overlay, social_outbound), skipped 6, failed 0, exit 0
```

What I could **not** validate from the sandbox:

- `--messages` / `--calls` against a real Mac chat.db / CallHistory.storedata. That needs to happen on your Mac.
- The `--email` / `--calendar` success path. Drop a `raw_gmail_threads.personal.json` into `inbox/` (any valid Cowork Gmail MCP `search_threads` output) and re-run; the wrapper should call `fetch_via_session.py email --account personal --in <raw>` and report `refreshed`.

### Files touched

```text
system/scripts/refresh_sources.py            (new)
system/scripts/relationship_signals.py       (REFRESH_SPECS now points at the wrapper)
system/STATUS.md                             (new row under Compute layer)
```

### Suggested commit

```bash
git add \
  system/scripts/refresh_sources.py \
  system/scripts/relationship_signals.py \
  system/STATUS.md
git commit -m "Add unified refresh_sources.py wrapper (last-sprint carry-over)

Local-only CLI over the per-source fetchers. Flags:
  --messages --calls --email --calendar --social --all
  --refresh-signals  (optionally rebuild relationship_signals cache)
  --json             (final structured result line)

Per-source result is one of:
  refreshed | skipped_no_raw_input | skipped_disabled |
  not_applicable_on_platform | failed

Email/calendar paths walk every enabled account in inbox/accounts.yaml.
When a raw_*.json input is missing, the wrapper prints the exact Cowork
MCP command needed (Gmail MCP search_threads / Calendar MCP list_events)
and exits cleanly; this is not a failure.

relationship_signals.REFRESH_SPECS now points every stale-source warning
at this wrapper so the daily brief surfaces one consistent command form.

EXPLICITLY local-only — no FastAPI route, no MCP tool, no Custom GPT
exposure. The auth story for GPT-driven source refresh isn't ready and
this wrapper does not change that.

Validated from Cowork sandbox:
  --social             : refreshed both caches (exit 0)
  --messages --calls   : not_applicable_on_platform off-Mac (exit 0)
  --email --calendar   : skipped_no_raw_input for all 4 accounts with
                         correct Cowork MCP recipes printed (exit 0)
  --all                : refreshed 2 / skipped 6 / failed 0 (exit 0)

Closes the refresh_sources / POST /refresh_sources follow-up flagged
in CLAUDE_HANDOFF_2026-05-19_RETURN.md (Priority 2)."
```

---

## 2 — api_smoke_test.py verification recipe

The RETURN handoff noted that `api_smoke_test.py` couldn't run from the Cowork sandbox because `fastapi` / `httpx` aren't installable here (PyPI is firewalled in the sandbox; pip retried 3x and got HTTP 403). I re-confirmed that today.

That leaves the two new regression checks added last sprint **unverified outside the sandbox**:

- `cards.jeff-wayman.projection_in_sync` (read-only; closes TOUCHCONTACT-VALIDATION-CONFLICT-001)
- `touch.jeff-wayman.round_trip` (advance → assert in_sync → revert; closes TOUCHCONTACT-PROJECTION-SYNC-001)

### One-shot for your terminal

From the repo root on your Mac:

```bash
# Install once if not already; safe to skip if you've installed before.
pip install fastapi httpx --break-system-packages

# Run the smoke test. The two regression checks are the last two CHK lines.
python3 system/scripts/api_smoke_test.py
```

### What "passing" looks like

The two CHK lines you want to see at the end of the output:

```text
CHK cards.jeff-wayman.projection_in_sync                  OK
    in_sync at last_touch=2026-05-12
CHK touch.jeff-wayman.round_trip                          OK
    round-trip OK ...
```

Followed by the final line:

```text
All N endpoints pass.
```

If either CHK prints `FAIL` instead of `OK`, the message under it will say which assertion missed. The likely failure modes:

| Symptom | Most likely cause |
|---|---|
| `projection_status='stale'` or `frontmatter=<old date>` | Card frontmatter drifted again. Re-run the same touch from `mutations.py` to resync. |
| `card_reason='in_sync'` after the advance step | The sentinel date matched `original`; the test handles this but if you've manually edited Jeff Wayman's card today the offset may need re-picking. |
| Hard exception (`EXCEPT ...`) | `fastapi`/`httpx` weren't installed, or `baseline_index.json` failed to load. Re-run the pip install and try again. |

The round-trip leaves canonical state unchanged on success — it advances to today's date then reverts to `2026-05-12`. The `system/_snapshots/` directory will gain two new gitignored snapshots, which is expected and not committed.

### After the smoke test passes

The remaining items in `CLAUDE_HANDOFF_2026-05-19_RETURN.md` are fully closed. The next sprint (RI event intake foundation, per the 2026-05-19 testing feedback) starts.

---

## What is *not* changed in this pass

- `system/automation/MACOS_HELPER_APP.md`, `automation/README.md`, `scripts/launchagent_install.py` — untouched, per the original handoff's "do not modify" list.
- `mutations.py`, `server.py`, `mcp/server.py`, `openapi.yaml`, `openapi_gpt.yaml`, `custom_gpt_prompt.md` — untouched. The wrapper is intentionally local-only; surfacing it to GPT requires the deferred auth story.
- The two RI-gap traces (`T-2026-05-19-005`, `T-2026-05-19-006`) and the RI intake specification commits (`0e25c9f`, `ec66fd5`, `10fadd4`, `4af458a`, `250a129`) — those describe the **next** sprint, not last sprint's carry-overs.
