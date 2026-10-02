# Operational Gap Closure Plan — RB Sprint 9.17
Generated: 2026-05-27

This document closes the open operational items carried over from RB 9.16.

---

## Gap 1 — Apple Messages Fetch Script

**Status: IMPLEMENTED (no action needed)**

`system/scripts/fetch_apple_messages.py` is fully implemented. It reads
`~/Library/Messages/chat.db` (read-only), normalises events into
`system/inbox/messages.json`, and supports `--days`, `--limit`,
`--include-snippets`, and `--db` overrides.

**To activate on Todd's machine:**

```bash
# Grant Full Disk Access to Terminal in System Settings first, then:
python3 system/scripts/fetch_apple_messages.py --days 365
```

Output: `system/inbox/messages.json`

**Next step:** Wire into `morning_pipeline.py` alongside the existing
`fetch_google.py` call. No code changes needed — just add the invocation.

---

## Gap 2 — Apple Calls Fetch Script

**Status: IMPLEMENTED (no action needed)**

`system/scripts/fetch_apple_calls.py` is fully implemented. It reads
`~/Library/Application Support/CallHistoryDB/CallHistory.storedata`
(read-only) and writes normalised call events to `system/inbox/calls.json`.

**To activate on Todd's machine:**

```bash
python3 system/scripts/fetch_apple_calls.py --days 365
```

Output: `system/inbox/calls.json`

**Next step:** Same as messages — wire into `morning_pipeline.py`.
Both scripts are safe to run daily; they overwrite the output file
each run (no append accumulation).

---

## Gap 3 — Interrupt Acknowledgment Endpoint

**Status: IMPLEMENTED in this sprint**

Added `POST /graphs/ecosystem/interrupts/{interrupt_id}/acknowledge`
to `system/api/server.py`.

Behaviour:
- Locates the record by `interrupt_id` in `inbox/ecosystem/interrupt_queue.jsonl`
- Writes `acknowledged=true` and `acknowledged_at` ISO timestamp in place
- Returns `{"acknowledged": true, "interrupt_id": "...", "acknowledged_at": "..."}`
- Returns 404 if `interrupt_id` not found
- Record is preserved in the file for audit; only excluded from future
  `getEcosystemInterrupts` responses

Also added to `system/api/openapi.yaml` (operationId: `acknowledgeEcosystemInterrupt`).

**Note:** This endpoint is intentionally NOT in the GPT subset (openapi_gpt.yaml).
Interrupt acknowledgment is an internal housekeeping action, not a CoS operator
surface action. The GPT reads interrupts via `getEcosystemInterrupts`; acknowledgment
should be handled by the morning pipeline or a direct API call.

---

## Gap 4 — Watch List Seeding Session

**Status: PARTIALLY SEEDED — needs Todd input for expansion**

Current watch list (1 entry):
```
brand-mcdonald-s | tier_1 | interrupt_eligible=true | "Primary target account"
```

### Suggested tier_1 candidates for Todd to confirm

These are candidates based on RB's existing relationship graph, active threads,
and ecosystem intelligence. Todd must confirm each:

| Entity | Suggested tier | Rationale |
|---|---|---|
| McDonald's | tier_1 (already seeded) | Primary target account |
| Burger King / Restaurant Brands International | tier_2 | Largest adjacent QSR, active POS displacement risk |
| Yum! Brands (Taco Bell, KFC, Pizza Hut) | tier_2 | PAR/Brink customer, relevant vendor signals |
| PAR Technology | tier_1 | Key vendor in Todd's domain; leadership/product signals matter |
| Qu (POS vendor) | tier_2 | Competitor displacement signal active (see LinkedIn 2026-05-27) |
| NCR Voyix / Shift4 | tier_2 | Frequent in restaurant POS landscape signals |

### Questions Todd must answer

1. Who are your top 3–5 active target accounts beyond McDonald's?
2. Which vendors do you track most closely for displacement/win signals?
3. Are there any individuals (not just companies) who should trigger interrupts
   on job change / news? (RB currently only tracks entities, not people, in the
   interrupt queue.)
4. Should tier_1 be restricted to accounts where Todd has active pursuit,
   or also include strategic monitors?

### Commands to add entries

```bash
# Add a tier_1 entity
python3 system/scripts/ecosystem_intelligence.py watch-list add <entity_id> \
  --priority tier_1 \
  --reason "Active pursuit account"

# Or via API (with server running):
curl -X POST http://localhost:8765/graphs/ecosystem/watchlist \
  -H "X-API-Key: $RB_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"action":"add","entity_id":"vendor-par-technology","priority":"tier_2","reason":"Track POS displacement signals","confirm":true}'

# Show current watch list
python3 system/scripts/ecosystem_intelligence.py watch-list show
```

---

## Open Item Summary

| Item | Owner | Status | Next Action |
|---|---|---|---|
| fetch_apple_messages.py | System | ✅ Implemented | Wire into morning_pipeline.py |
| fetch_apple_calls.py | System | ✅ Implemented | Wire into morning_pipeline.py |
| Interrupt acknowledgment endpoint | System | ✅ Implemented (RB 9.17) | No action needed |
| Watch list seeding | Todd | ⚠ Needs expansion | Answer 4 questions above, run watch-list add |
| morning_pipeline.py Apple wiring | System | 🔲 Deferred to RB 9.18 | Add fetch_apple_* invocations |
| People-level interrupt support | System | 🔲 Deferred | Not scoped; raise if needed |

---

## Deferred Items (RB 9.18 candidates)

- Wire `fetch_apple_messages.py` and `fetch_apple_calls.py` into `morning_pipeline.py`
- Implement people-level interrupt triggers (job changes for tracked individuals)
- Watch list seeding session with Todd (requires his answers above)
- Consider TTL-based auto-expiry for interrupt queue items older than 48h
