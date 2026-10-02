# Sprint RB 9.17 — Handoff: Trust Surface Cleanup + Alignment
**Completed:** 2026-05-27

---

## Summary

All 6 deliverables complete. Every thing RB can say, call, mutate, and test is now aligned.
Final test state: **90 tests, 0 failures, 1 intentional skip (fastapi not installed).**

---

## Cleanup Decisions

### D1 — OpenAPI / GPT Action Budget

- Added 5 missing endpoints to `system/api/openapi.yaml`:
  `getWatchList`, `updateWatchList`, `getEcosystemInterrupts`, `evaluatePassiveIntelligence`, `getMicroGraphIndex`
- Removed 5 lower-priority ops from the GPT subset to stay at ≤30:
  `ingestLinkedInExport`, `processLinkedInSignal`, `getSocialOutboundOverlay`, `getNetworkGap`, `getDrrScore`
  — all moved to internal-only (still in full openapi.yaml; not GPT-facing)
- Regenerated `openapi_gpt.yaml` via `--write` from updated full spec + allowlist
- Result: `validate_openapi_gpt.py` → **OK (30 ops)**

### D2 — Schema Documentation

Added to `SCHEMAS.md`:
- **Ecosystem Confidence Model** — `level`, `score`, `rationale`, `review_after`; `evidence_posture` enum; sticky substantiated relationship rule (no silent downgrade on single signal)
- **Watch List schema** — full `watch_list[]` entry shape; tier_1/2/3 semantics
- **Interrupt Queue shape** — `inbox/ecosystem/interrupt_queue.jsonl` per-record format; qualifying `signal_class` values
- **Micro Graph Index** — `rb_micro_graph_presence_index_v1` shape with trust hierarchy and graph entry fields
- **Passive Intelligence Evaluation Metadata** — full field table with types, descriptions, valid values for all 9 metadata fields
- **Daily brief rendering requirements** — explicit display rules for confidence/corroboration metadata in the brief

### D3 — Test Runtime Compatibility

- Fixed `dict | None` annotations in `test_micro_graph_routing.py` → `Optional[dict]` (Python 3.9 compatible)
- Added `from typing import Optional` import
- Converted `test_api_ecosystem_graph.py` hard `ImportError` into a proper `unittest.SkipTest` on missing `fastapi`
- Result: 90 tests, 0 errors, 1 named skip

### D4 — Dirty Tree Audit

- No git repository at system root; audit done by file classification
- Report written to `DIRTY_TREE_AUDIT_RB_9_17.md`
- One confirmed stale duplicate: `scripts/strategic_events (1).py` — recommended for deletion (awaiting Todd confirmation)
- `__pycache__` dirs safe to purge at any time
- All CLAUDE_HANDOFF / CLAUDE_SPRINT / user-authored docs classified and preserved

### D5 — Operational Gap Closure

- `fetch_apple_messages.py` and `fetch_apple_calls.py` — already fully implemented; no code changes needed
- Added `POST /graphs/ecosystem/interrupts/{interrupt_id}/acknowledge` to `server.py` and `openapi.yaml`
- Watch list has 1 entry (McDonald's tier_1); expansion protocol documented with suggested candidates and 4 questions for Todd
- Full plan in `CLAUDE_HANDOFF_RB_9_17_OPERATIONAL_GAP_CLOSURE.md`

### D6 — Passive Intelligence Hardening

- All three core invariants verified via live test:
  - Vendor claims without corroboration cannot reach `canonical_fact` or `corroborated_intelligence` ✓
  - Social rumours stay `plausible_but_unverified` / `weak_signal` ✓
  - Corroborated earnings-call claims can promote to `canonical_fact` ✓
- Added rumour penalty: `rumor` narrative classification now gets -0.08 score deduction (same as vendor claims)
- Fixed eligibility gate: `rumor` + `plausible_but_unverified` now maps to `emerging_narrative` (not `weak_signal`)
- GPT instruction trigger confirmed present and correct in `custom_gpt_instructions_8k.md`
- Daily brief pipeline confirmed: all passive metadata fields present in output payload
- All 5 passive intelligence tests pass

---

## Files Changed

| File | Change |
|---|---|
| `system/api/openapi.yaml` | +5 new endpoints (watchlist, interrupts, passive eval, micro index, ack) |
| `system/api/openapi_gpt.yaml` | Regenerated — 30 ops, drift resolved |
| `system/scripts/validate_openapi_gpt.py` | GPT_OPERATIONS allowlist updated (+5 new, -5 internal) |
| `system/api/server.py` | Added `acknowledgeEcosystemInterrupt` endpoint |
| `system/scripts/passive_intelligence.py` | Rumour penalty + eligibility gate fix |
| `system/tests/test_micro_graph_routing.py` | Python 3.9 compat (`Optional[dict]` annotation) |
| `system/tests/test_api_ecosystem_graph.py` | Proper `SkipTest` guard for missing fastapi |
| `system/SCHEMAS.md` | +6 new schema sections (confidence model, watch list, interrupt queue, micro graph index, passive intel metadata, daily brief rendering rules) |
| `system/DIRTY_TREE_AUDIT_RB_9_17.md` | New — file classification report |
| `system/CLAUDE_HANDOFF_RB_9_17_OPERATIONAL_GAP_CLOSURE.md` | New — gap closure plan |
| `system/CLAUDE_HANDOFF_RB_9_17_SPRINT_COMPLETE.md` | This file |

---

## Test Results

```
=== py_compile ===
passive_intelligence.py, ecosystem_brief.py, ecosystem_intelligence.py, server.py → PASS

=== schema validate ===
OK — ecosystem_intelligence.json validates against ecosystem_intelligence.schema.json (1 items)

=== unit tests ===
Ran 90 tests in 1.258s
OK (skipped=1)
  SKIP: test_api_ecosystem_graph — fastapi not installed (intentional sandbox skip)

=== validate_openapi_gpt ===
validate_openapi_gpt: OK (30 ops)
```

---

## Remaining Risks

| Risk | Severity | Note |
|---|---|---|
| `acknowledgeEcosystemInterrupt` not in GPT subset | Low | Intentional. Ack is internal housekeeping. Interrupt display via `getEcosystemInterrupts` is GPT-facing. |
| Watch list has only 1 entry | Medium | Interrupt queue won't surface signals for untracked entities. Todd needs to seed tier_1/2 entries for active targets. |
| Apple Messages/Calls not wired into `morning_pipeline.py` | Medium | Scripts exist and work; integration deferred to RB 9.18. |
| `strategic_events (1).py` still present | Low | Stale macOS duplicate. Safe to delete; needs Todd confirmation. |
| No TTL-based auto-expiry on interrupt queue | Low | Items older than 48h should auto-acknowledge. Deferred. |
| Passive intelligence external corroboration | Medium | Module is deterministic (no external search). Claims that are true but absent from the local graph will stay `plausible_but_unverified`. External corroboration path not yet built. |

---

## Deferred to RB 9.18

1. **Wire `fetch_apple_messages.py` and `fetch_apple_calls.py` into `morning_pipeline.py`** — scripts are ready, just need invocation added
2. **Watch list seeding session with Todd** — answer 4 questions in gap closure doc, add tier_1/tier_2 entities
3. **Interrupt queue TTL auto-expiry** — mark items >48h as auto-acknowledged; add to morning pipeline
4. **External corroboration path for passive intelligence** — allow a sourced web search result to upgrade `plausible_but_unverified` claims
5. **People-level interrupt support** — track individuals (not just entities) for job change / news signals
6. **Delete `scripts/strategic_events (1).py`** after Todd confirms

---

## Recommendation for RB 9.18

RB's trust surface is clean. The next sprint should focus on **operationalisation completeness**:

- Morning pipeline wiring for Apple signals (calls + messages feed the relationship graph; currently a gap)
- Watch list expansion to 5–8 entities across tier_1 and tier_2 (interrupt queue is useless until populated)
- The passive intelligence evaluation path is solid but isolated — consider adding a lightweight "promote signal to graph" confirmation flow so corroborated claims can graduate to relationships without requiring a full manual mutation session

The architecture is ready. The remaining work is operational wiring and data seeding, not design work.
