# RB 9.16 Sprint Handoff — Operational Intelligence Loop

**Sprint:** RB 9.16 Operational Intelligence Loop  
**Status:** COMPLETE  
**Date closed:** 2026-05-27  
**Test result:** 84/84 pass (1 pre-existing skip: `test_api_ecosystem_graph.py` — FastAPI not in sandbox)

---

## What Was Built

### Deliverable 1 — Vendor relationship seed (15 brand POS relationships)

**File:** `system/inbox/ecosystem/pos_seed_sprint16_additional.csv`

Added 5 new seed rows for brands not covered in the initial import:
Shake Shack (Oracle Simphony), Dutch Bros (proprietary), Jack in the Box (Oracle Micros), Panera (internal digital platform), Panda Express (proprietary). All seeded as `provisional` or `partially_substantiated`. Combined with prior seeds, the ecosystem graph now has POS coverage for the top 15+ restaurant brands by unit count.

---

### Deliverable 2 — Two-state confidence model

**Files modified:** `system/schemas/ecosystem_intelligence.schema.json`, `system/scripts/ecosystem_intelligence.py`

**New CLI commands:** `check-staleness`, `promote-confidence`

**New module-level helper:** `_check_staleness_on_graph(graph, today)` — pure function for testability; `check_staleness(args)` wraps it with disk I/O.

**Two-state logic:**
- `provisional` / `partially_substantiated` → decay model: `confidence.review_after` date set at creation; `check-staleness` flags overdue records with `staleness_flag=True`.
- `substantiated` + sticky tech category (`pos`, `payments`, `back_office`, `erp`, `back_office_accounting`) → locked: `confidence.review_after=None`, `staleness_flag` cleared and never re-set.

**Constants added:** `STICKY_TECH_CATEGORIES`, `ACTIVATION_SIGNAL_CLASSES`, `INTERRUPT_SIGNAL_CLASSES`

**Schema additions to `relationship` definition:** `last_verified_at`, `verified_by`, `staleness_flag`

---

### Deliverable 3 — Watch list

**Files modified:** `system/schemas/ecosystem_intelligence.schema.json`, `system/ecosystem_intelligence.json`, `system/scripts/ecosystem_intelligence.py`, `system/api/server.py`, `system/api/openapi_gpt.yaml`

**New CLI commands:** `watch-list show`, `watch-list add`, `watch-list remove`, `watch-list set-priority`

**New API endpoints:**
- `GET /graphs/ecosystem/watchlist` (`getWatchList`) — returns watch list with entity metrics
- `POST /graphs/ecosystem/watchlist` (`updateWatchList`) — add/remove/set-priority with `confirm=false` preview, `confirm=true` apply

**Tiers:** `tier_1` (user-curated, interrupt_eligible), `tier_2` (CoS-suggested), `tier_3` (ambient)

**Schema additions to graph root:** `watch_list` array with `watch_list_entry` definition

---

### Deliverable 4+5 — Daily brief ecosystem section + CoS activation logic

**Files created:** `system/scripts/ecosystem_brief.py` (~320 lines)

**Key functions:**
- `_classify_signal(title, summary)` → signal class string (7 classes)
- `_match_signals_to_entities(raw_signals, entity_idx, today)` → enriched match records
- `_write_signal_to_graph(graph, match, today)` → idempotent signal write
- `_write_activation_assessment(graph, match, signal_id, today)` → idempotent assessment write
- `build_section(today)` → full `ecosystem_intelligence` dict for daily brief

**Signal classes:** `leadership_change`, `rfp_cycle_signal`, `extreme_pain`, `vendor_displacement`, `funding_event`, `expansion_signal`, `general_market_context`

**Activation classes** (CoS-triggered): `leadership_change`, `rfp_cycle_signal`, `extreme_pain`, `vendor_displacement`

**Brief section rendering order:** `watch_list_signals` → `mutation_log` → `ambient_surface` → `staleness_flags` → `needs_todd`

**Daily brief integration:** `ecosystem_intelligence_report` added to `build_report()` report dict; `_ecosystem_intelligence_section()` populates `sections["ecosystem_intelligence"]` in `build_canonical_brief()`.

---

### Deliverable 6+7 — Interrupt queue + API/OpenAPI/instruction updates

**Files modified:** `system/scripts/ecosystem_intelligence.py`, `system/api/server.py`, `system/api/openapi_gpt.yaml`, `system/api/custom_gpt_instructions_8k.md`, `system/api/custom_gpt_prompt.md`

**New CLI command:** `check-interrupt-queue`

**New API endpoint:** `GET /graphs/ecosystem/interrupts` (`getEcosystemInterrupts`) — returns unacknowledged interrupt queue items

**Interrupt queue file:** `system/inbox/ecosystem/interrupt_queue.jsonl`

**Interrupt conditions** (all four required):
1. Entity is on watch list (any tier)
2. Signal class is interrupt-eligible (`leadership_change`, `rfp_cycle_signal`, `extreme_pain`, `vendor_displacement`)
3. Confidence is `high`
4. Signal is within 48 hours of detection

**GPT instruction additions:**
- Session-start: call `getEcosystemInterrupts`; render non-empty items before daily brief
- Company-specific routing: call `getMicroGraphIndex` before answering factual company questions
- Watch list commands: `getWatchList` / `updateWatchList` with confirm pattern
- Ecosystem section rendering order enforced

---

### Deliverable 8 — RB-DEFECT-004: Apple Messages/Calls ingestion hardening

**Files created:** `system/scripts/direct_comms_health.py` (~485 lines)

**Files modified:** `system/scripts/daily_brief.py`

**Five-state readiness model:**
- `unavailable` — inbox missing / Full Disk Access not granted
- `available_stale` — inbox older than 48h threshold
- `available_metadata_only` — events present, no snippets
- `available_with_snippets` — events with readable content
- `available_fresh` — fresh read (< 48h)

**Key functions:**
- `check_messages_readiness(baseline)` → readiness dict with state, event_count, matched_count, urgency_count, ri_candidates, brief_language
- `check_calls_readiness(baseline)` → same for calls
- `build_direct_comms_brief_items(baseline)` → structured brief items including urgency signal items
- `get_completeness_caveat(items)` → caveat string when sources are not fully trustworthy; None if both fresh

**Daily brief integration:**
- `_HAS_DIRECT_COMMS_HEALTH` guard at import
- `direct_comms_items` computed in `build_report()`, added to report dict as `"direct_comms_health"`
- `_direct_comms_section(report)` function renders items into `resource_verification_and_freshness_status` section
- Completeness caveat surfaces as `act_today` item when any source is unavailable/stale
- Urgency signals (missed call from known contact) surface as `act_today` items with `proposed_mutation`

**Privacy constraints enforced (P-038):**
- Local-only ingestion. No external transmission.
- No raw message text stored beyond captured snippet.
- No send/reply on Todd's behalf.
- Full Disk Access failures surfaced with recovery instructions, never bypassed.
- Only minimized RI evidence in candidates: contact_id, channel, direction, event_at, snippet_available, urgency_signal.

---

### Deliverable 9 — RB-DEFECT-005: Micro graph presence index + routing fix

**Files created:** `system/graphs/micro/index.json`

**Files modified:** `system/api/server.py`

**New API endpoint:** `GET /graphs/micro/index` (`getMicroGraphIndex`) — returns the lightweight presence index

**Trust hierarchy enforced:**
1. `user_artifact`
2. `rb_micro_graph`
3. `rb_macro_graph`
4. `verified_external_source`
5. `general_model_knowledge`

**McDonald's micro graph entry includes:**
- `question_domains`: franchisee_count, store_count, operator_structure, field_office_mapping, market_mapping, deployment_topology, pos_system, back_office_system, coop_distribution, regional_accountability, role_coverage, site_lookup
- `implicit_trigger_terms`: mcdonalds, mcd, nsn, storetech, store tech, field office, coop, co-op, rfm, otm, stim, fbp, otp, site id, store code
- `routing_action`: `getMicroGraphSummary` with `query="McDonald's"`

---

### Deliverable 10 — Test suite

**Files created:**
- `system/tests/test_operational_intelligence_loop.py` — 30 tests covering Deliverables 1–7
- `system/tests/test_direct_comms_ingestion.py` — 18 tests for RB-DEFECT-004
- `system/tests/test_micro_graph_routing.py` — 20 tests for RB-DEFECT-005

**Results:** 68 new tests + 16 existing ecosystem tests = **84 total, all pass**

---

## Files Changed This Sprint

| File | Status | Notes |
|------|--------|-------|
| `system/schemas/ecosystem_intelligence.schema.json` | Modified | Added staleness fields, watch_list schema |
| `system/ecosystem_intelligence.json` | Modified | Version 2; watch_list added; signals + assessments written |
| `system/scripts/ecosystem_intelligence.py` | Modified | ~1700 lines; check-staleness, promote-confidence, watch-list, check-interrupt-queue, `_check_staleness_on_graph` helper |
| `system/scripts/ecosystem_brief.py` | Created | ~320 lines; full ecosystem brief section builder |
| `system/scripts/direct_comms_health.py` | Created | ~485 lines; 5-state readiness, contact matching, brief items |
| `system/scripts/daily_brief.py` | Modified | Ecosystem + direct_comms_health integration; `_direct_comms_section()` |
| `system/api/server.py` | Modified | getWatchList, updateWatchList, getEcosystemInterrupts, getMicroGraphIndex |
| `system/api/openapi_gpt.yaml` | Modified | 4 new paths added (34 total operations) |
| `system/api/custom_gpt_instructions_8k.md` | Modified | getMicroGraphIndex routing rule; ecosystem interrupt + watch list rules |
| `system/api/custom_gpt_prompt.md` | Modified | Rules 9a, 10a, 10b |
| `system/graphs/micro/index.json` | Created | Micro graph presence index |
| `system/inbox/ecosystem/pos_seed_sprint16_additional.csv` | Created | 5 brand POS seed rows |
| `system/tests/test_operational_intelligence_loop.py` | Created | 30 tests |
| `system/tests/test_direct_comms_ingestion.py` | Created | 18 tests |
| `system/tests/test_micro_graph_routing.py` | Created | 20 tests |

---

## Open Questions Resolved

| Question | Resolution |
|----------|-----------|
| Daily brief vs. separate schedule for ecosystem mutations? | Daily brief is the primary mutation surface. Simpler, keeps intelligence surfacing in one place. |
| Two-state or continuous confidence decay? | Two-state: `provisional`/`partially_substantiated` decay on `review_after` date; `substantiated` + sticky-tech = locked (null `review_after`). |
| How many tiers for watch list? | Three: `tier_1` (user + interrupt_eligible), `tier_2` (CoS-suggested), `tier_3` (ambient). |
| Interrupt push or queue? | Queue only for now — surfaced at session start via `getEcosystemInterrupts`. No background push until explicitly scoped. |
| Apple Messages FDA bypass approach? | Surface failures with recovery instructions only. No bypass. No silent workarounds. |

---

## Remaining Gaps / Next Sprint Candidates

- **`getMicroGraphIndex` endpoint not tested via FastAPI** — `test_api_ecosystem_graph.py` requires FastAPI in sandbox; this endpoint was verified via syntax check and routing logic tests only.
- **`SCHEMAS.md` not updated** — document new `confidence` model fields (`last_verified_at`, `verified_by`, `staleness_flag`) and `watch_list` schema additions.
- **Apple Messages/Calls fetch scripts** (`fetch_apple_messages.py`, `fetch_apple_calls.py`) — referenced in recovery commands but not created in this sprint. Required to move from `unavailable` to `available_*` states.
- **Watch list seeding** — McDonald's is on the watch list; other tier_1 candidates need Todd's review and explicit add via `watch-list add`.
- **Interrupt acknowledgment endpoint** — interrupt queue records write `acknowledged: false`; no API endpoint exists yet to mark them acknowledged after Todd reviews.
- **Additional micro graphs** — Yum Brands brands (Taco Bell, KFC, Pizza Hut), Darden, Restaurant Brands International are candidates for micro graph creation as Todd's engagement with those operators deepens.

---

## Commands for Verification

```bash
# Staleness check (dry run)
python3 system/scripts/ecosystem_intelligence.py check-staleness --dry-run

# Watch list
python3 system/scripts/ecosystem_intelligence.py watch-list show
python3 system/scripts/ecosystem_intelligence.py watch-list add brand-mcdonald-s --priority tier_1 --reason "Active McDonald's engagement"

# Interrupt queue
python3 system/scripts/ecosystem_intelligence.py check-interrupt-queue --dry-run

# Full test suite (excluding FastAPI-dependent test)
python3 -m unittest system/tests/test_operational_intelligence_loop.py system/tests/test_direct_comms_ingestion.py system/tests/test_micro_graph_routing.py system/tests/test_ecosystem_intelligence.py

# Daily brief (dry run)
python3 system/scripts/daily_brief.py --dry-run --json
```
