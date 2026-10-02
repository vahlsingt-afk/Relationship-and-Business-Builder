# RB-DEFECT-033: Auth Failures Return Bare 401 — No Structured Cause/Recovery Guidance

**Filed:** 2026-06-08
**Severity:** Low–Medium
**Category:** Infrastructure / Action Endpoint Reliability / Diagnostics
**Status:** Resolved — 2026-06-08

## Originally reported as

A "Loop Ledger Retrieval Failure" — `getLoops(date=2026-06-08)` failed twice with a
`ClientResponseError` while `getDailyBrief` succeeded, blocking loop-management
workflow ("cannot verify current loop state, cannot safely close/re-date/reconcile
loops").

## Investigation — this is NOT a loop-ledger or endpoint-reliability defect

Live server log (`system/automation/api-server.error.log`) shows exactly what
happened, request-by-request, in the same session:

```
INFO:rb.requests:GET /daily_brief?use_cache=true   auth=YES  status=200
INFO:rb.requests:GET /loops?date=2026-06-08        auth=NO   status=401
```

The calling client (ChatGPT Action layer) sent the `x-api-key` header correctly for
`getDailyBrief` but **omitted it** for the `getLoops` call in the same turn — a
client-side auth-header transmission gap, not a server fault. The server correctly
rejected the unauthenticated request with `401`.

**Confirmed healthy** (ruling out every alternative explanation in the original
report):
- `core.parse_loop_ledger()` + `core.loops_by_status()` run cleanly against the live
  ledger — 32 loops parsed, bucketed correctly (`overdue: 1, due_today: 1,
  this_week: 12, future: 10, closed: 8`), JSON-serializes to 12,331 bytes without error
- `/public/bootstrap_status` → `200 {"api_alive": true, "degraded": false, "status": "ready"}`
  — API and tunnel both alive
- A subsequent authenticated `GET /loops?date=2026-06-08` in the same log returns
  `200 OK` in 84ms — the endpoint works correctly when the header is present

So "the live loop ledger could not be retrieved" is not true — it could be, and was
(in other calls in the same log window). What actually happened is indistinguishable,
from the caller's side, between "server is broken" and "I forgot to send my own
API key" — **and that ambiguity is the real defect.**

## Confirmed root cause

`server.py:145-147`:

```python
def _auth(key_header: Optional[str]) -> None:
    if API_KEY and key_header != API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing x-api-key")
```

Returns a bare `401` with a flat string `detail`. FastAPI/the Custom GPT Action
client surfaces this as a generic `ClientResponseError` with no machine-readable
`cause` or `recovery` field — so a client that drops its own auth header on one
call (while successfully sending it on others, as happened here) cannot
distinguish "I made a mistake, retry with the header" from "the ledger / server /
tunnel is down." This pushed the original report toward exactly the wrong
conclusion (infrastructure failure) when the fix is on the calling side.

## Expected behavior (the legitimate part of the original report, narrowed)

`_auth` failures should return a structured body the caller — and any agent
reasoning about the failure — can act on without guessing:

```json
{
  "error": "unauthorized",
  "cause": "missing_or_invalid_x_api_key",
  "recovery": "Resend the request with the x-api-key header set to your configured RB API key. This is a per-request header — confirm your client attaches it to every Action call, not just some.",
  "endpoint": "/loops",
  "auth_present": false
}
```

The `auth_present` flag in particular would have let the original report
self-diagnose immediately ("oh — my own client didn't send the key on this call")
instead of concluding the ledger/endpoint was broken.

## Scope for Codex

1. In `server.py:_auth`, replace the bare `HTTPException(status_code=401,
   detail="invalid or missing x-api-key")` with a structured `JSONResponse`/
   `HTTPException(detail={...})` body carrying `error`, `cause` (`missing_x_api_key`
   vs `invalid_x_api_key` — distinguish "header absent" from "header present but
   wrong", since these have different recovery paths), `recovery`, and
   `auth_present` (bool — was *any* `x-api-key` header present, regardless of
   validity).
2. Apply consistently across all authenticated endpoints (the `_auth` helper is
   shared — confirm the structured body survives FastAPI's exception handling
   uniformly, not just for `/loops`).
3. No change needed to `/loops`, `core.parse_loop_ledger()`, or
   `core.loops_by_status()` — all confirmed healthy; do not "fix" what isn't broken.

## Implementation Result — 2026-06-08

`server.py:_auth` replaced. All three paths verified via TestClient:

| Scenario | Status | `cause` | `auth_present` |
|---|---|---|---|
| Header absent | 401 | `missing_x_api_key` | `false` |
| Header present, wrong value | 401 | `invalid_x_api_key` | `true` |
| Header present, correct value | 200 | — | — |

The two `missing` vs `invalid` causes have explicitly different recovery strings:
- `missing`: "per-request header — confirm your client attaches it to every Action call, not just some. getDailyBrief succeeding while getLoops fails in the same session is the classic symptom."
- `invalid`: "Check the key value in your Custom GPT Action settings."

The original scenario (GPT sends key on `getDailyBrief` but drops it on `getLoops`) now returns `cause: missing_x_api_key, auth_present: false` — self-diagnosing without needing server log access.

**No changes** to `/loops`, loop-ledger parsing, or tunnel/infrastructure — all confirmed healthy per investigation.

---

## Non-goals

- Not a loop-ledger parsing/bucketing fix — that code is healthy and verified.
- Not a tunnel/infrastructure reliability fix — `bootstrap_status` confirms both
  are up; this defect is purely about *diagnosability* of a routine 401.
- Not an investigation into *why* the Custom GPT Action client dropped the header
  on this specific call — that's a client/session-side question outside RB's
  codebase; the fix here is to make the *server's* response self-diagnosing so
  this ambiguity can't recur regardless of which client misbehaves next.
