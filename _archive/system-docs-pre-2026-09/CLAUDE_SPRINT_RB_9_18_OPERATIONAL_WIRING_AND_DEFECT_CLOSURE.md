# Claude Sprint Brief — RB 9.18 Operational Wiring + Defect Closure

**Prepared:** 2026-05-28  
**Prepared by:** Codex  
**Audience:** Claude / next RB implementation sprint  
**Sprint posture:** Operational wiring, deployment proof, and defect closure. Do not start a new architecture sprint.

---

## Why This Sprint Exists

RB now has several strong subsystems that are implemented locally but not fully wired into the live operating path:

- passive intelligence assessment and confidence scoring
- ecosystem graph mutation and verification queues
- Apple Messages / Calls source readiness and passive RI extraction
- interrupt queue generation and acknowledgment
- source-health-aware Daily Brief trust language

The remaining debt is not conceptual. It is operational: make these systems unavoidable in the morning pipeline, API surface, Daily Brief, and closure tests.

The sprint principle:

> Close the loop between source detection, confidence assessment, graph mutation eligibility, Daily Brief surfacing, and live delivery.

---

## Non-Negotiable Invariant

The passive intelligence gate must not be bypassed.

Any uploaded, pasted, screenshot, LinkedIn, vendor, social, article, or commentary input with factual or strategic claims must flow through:

1. claim extraction
2. source classification
3. corroboration / contradiction check
4. confidence scoring
5. claim status assignment
6. graph mutation eligibility
7. uncertainty preservation

Do not silently promote anecdote, speculation, marketing, rumor, or opinion into canonical graph truth.

Only `graph_mutation_eligibility=canonical_fact` or `corroborated_intelligence` may update canonical relationship/entity truth layers. Everything else remains a weak signal, emerging narrative, disputed item, vendor positioning, or non-eligible observation.

Relevant files:

- `system/scripts/passive_intelligence.py`
- `system/scripts/ecosystem_brief.py`
- `system/scripts/daily_brief.py`
- `system/api/server.py` (`evaluatePassiveIntelligence`)
- `system/SCHEMAS.md` passive intelligence metadata section
- `defects/RB-DEFECT-006_passive-intelligence-confidence-corroboration_2026-05-27.md`

---

## Current Defect Inventory

### RB-DEFECT-001 — API Availability Regression

**Status:** Open operational blocker. Durable named tunnel is still the closure condition.

Current defect says quick tunnel is temporary and presumed fragile. RB compute may be healthy locally, but the Custom GPT Action path is not delivery-proof until the named tunnel is live and schema is republished.

**9.18 treatment:** Priority 1.

Acceptance:

- stable named tunnel or durable tunnel install path is verified
- `openapi_gpt.yaml` points to stable URL, not a dead quick tunnel
- Custom GPT Action preview returns `getDailyBrief`
- `tunnel_health_check.py` passes
- `morning_path_test.py` passes
- defect status updated only after the live path is proven

Relevant files:

- `defects/RB-DEFECT-001_api-availability-regression_2026-05-24.md`
- `system/scripts/durable_tunnel_install.py`
- `system/scripts/tunnel_health_check.py`
- `system/scripts/morning_path_test.py`
- `system/scripts/update_tunnel_url.py`
- `system/api/openapi_gpt.yaml`

---

### RB-DEFECT-002 — Passive LinkedIn Vendor Intelligence Failed To Mutate Industry Graph

**Status:** Locally fixed, not fully generalized.

Qu-specific vendor/customer extraction was implemented and backfilled. Remaining work is to make vendor-claim verification operational beyond the single Qu example.

**9.18 treatment:** Fold into operational hardening.

Acceptance:

- vendor verification queue has a read/review API or CLI surface
- generic vendor/customer claim extractor handles more than Qu-specific vocabulary
- promotion flow exists from provisional/vendor-claimed to corroborated/substantiated once independent evidence arrives
- Daily Brief can surface verification queue items without presenting them as fact

Relevant files:

- `defects/RB-DEFECT-002_passive-linkedin-vendor-intelligence-no-graph-mutation_2026-05-27.md`
- `system/scripts/linkedin_freshness_bridge.py`
- `system/scripts/ecosystem_intelligence.py`
- `system/inbox/ecosystem/vendor_verification_queue.json`
- `system/tests/test_linkedin_freshness_bridge.py`
- `system/tests/test_ecosystem_intelligence.py`

---

### RB-DEFECT-003 — Passive Communication Failure Failed To Mutate RI

**Status:** Mostly addressed by manual RI classifiers and direct-comms heuristics.

The Ryan Hildebrand / Global Payments scenario should now classify bounced-email and channel-escalation language as relationship-impacting operational risk.

**9.18 treatment:** Do not create a separate architecture track. Treat as covered by direct-comms wiring and regression proof.

Acceptance:

- direct SMS/iMessage/call signals can produce passive RI candidates
- bounced email / DNS / routing language produces `communication_failure_risk`
- channel escalation produces `multi_channel_escalation`
- active opportunity overlap raises Daily Brief priority
- regression test covers the scenario or its generalized shape

Relevant files:

- `defects/RB-DEFECT-003_passive-communication-failure-ri-ingestion_2026-05-27.md`
- `system/scripts/manual_relationship_intake.py`
- `system/scripts/relationship_signals.py`
- `system/scripts/direct_comms_health.py`
- `system/tests/test_direct_comms_ingestion.py`

---

### RB-DEFECT-004 — Apple Messages / Calls Ingestion Gap Breaks Daily Brief Completeness

**Status:** Implementation exists; morning pipeline wiring remains deferred.

There is already a five-state readiness model, Apple fetch scripts, direct-comms brief items, and tests. The sprint should wire the implemented pieces into the recurring path and prove Daily Brief trust behavior.

**9.18 treatment:** Main source-coverage completion item.

Acceptance:

- `fetch_apple_messages.py` and `fetch_apple_calls.py` are invoked by `morning_pipeline.py` or an equivalent durable source-refresh step
- `refresh_sources.py --messages --calls --save-health --refresh-signals` writes source health and relationship signals
- Daily Brief explicitly distinguishes:
  - fresh
  - stale
  - unavailable
  - metadata-only
  - snippets available
- Daily Brief must not imply “no new direct communication signals” when Messages or Calls are stale, unavailable, or snippet-limited
- tests cover unavailable, stale, metadata-only, fresh, missed call, and channel escalation cases

Relevant files:

- `defects/RB-DEFECT-004_apple-messages-calls-ingestion-gap-daily-brief-completeness_2026-05-27.md`
- `system/scripts/fetch_apple_messages.py`
- `system/scripts/fetch_apple_calls.py`
- `system/scripts/direct_comms_health.py`
- `system/scripts/refresh_sources.py`
- `system/scripts/morning_pipeline.py`
- `system/scripts/daily_brief.py`
- `system/tests/test_direct_comms_ingestion.py`

Privacy constraints:

- local-only ingestion by default
- no broad raw transcript persistence
- no sending/replying on Todd's behalf
- no Full Disk Access workaround
- permission failures must be surfaced plainly

---

### RB-DEFECT-006 — Passive Intelligence Confidence + Corroboration Gap

**Status:** Implemented and hardened.

Current behavior includes deterministic claim extraction, source classification, local corroboration/contradiction checks, confidence scoring, graph mutation eligibility, Daily Brief metadata rendering, rumor penalty, eligibility gate fix, and explicit corroboration search planning.

**9.18 treatment:** Do not rebuild the model. Add operational promotion and external corroboration wiring only.

Acceptance:

- uncorroborated vendor/social claims remain non-canonical
- `corroboration_search_queue` is surfaced somewhere actionable
- sourced corroboration can be attached to a claim
- promotion from `plausible_but_unverified` to `likely_true` / `verified` requires actual source evidence, not a search plan
- Daily Brief displays confidence level, score, source quality, corroboration count, and status

Relevant files:

- `defects/RB-DEFECT-006_passive-intelligence-confidence-corroboration_2026-05-27.md`
- `system/scripts/passive_intelligence.py`
- `system/scripts/ecosystem_brief.py`
- `system/tests/test_passive_intelligence.py`
- `system/SCHEMAS.md`

---

## Recommended RB 9.18 Work Plan

### 1. Prove Durable API Delivery

Close `RB-DEFECT-001` first if possible. Without a durable Action endpoint, everything else remains locally correct but operationally brittle.

Suggested verification commands:

```bash
RB_API_KEY=localtest python3 system/scripts/tunnel_health_check.py --json
RB_API_KEY=localtest python3 system/scripts/morning_path_test.py
python3 system/scripts/validate_openapi_gpt.py
```

If named tunnel setup requires host/Cloudflare authentication, document the exact blocker and leave the defect open.

### 2. Wire Direct Communications Into The Morning Path

Scripts exist. The gap is recurring invocation and trust reporting.

Wire:

```bash
python3 system/scripts/fetch_apple_messages.py --days 365
python3 system/scripts/fetch_apple_calls.py --days 365
python3 system/scripts/refresh_sources.py --messages --calls --save-health --refresh-signals
```

into `morning_pipeline.py` or the source refresh step already called by it.

Do not let failures hard-crash the entire brief unless the existing pipeline policy requires it. Instead, write source-health state and caveat the Daily Brief.

### 3. Make Verification Queues Reviewable

Add a small read/review surface for:

- `system/inbox/ecosystem/vendor_verification_queue.json`
- passive intelligence `corroboration_search_queue`

The goal is not to complete all external research in 9.18. The goal is to make “needs corroboration” visible and actionable.

### 4. Add Promotion Flow, Not Auto-Promotion

Build a confirmation path that can promote a provisional claim only when an actual source is attached.

Rules:

- search plans do not raise confidence
- vendor claims need independent evidence
- primary sources beat secondary reporting
- disputed evidence must preserve contradiction history
- promotion must update evidence posture and confidence rationale

### 5. Add Interrupt Queue TTL Expiry

Interrupt acknowledgment endpoint exists. Add TTL auto-expiry for records older than 48 hours so stale interrupts do not clutter the operator surface.

Preserve audit trail:

- `acknowledged=true`
- `acknowledged_at=<timestamp>`
- `acknowledgment_reason=ttl_expired` or equivalent

---

## Tests To Run Before Calling 9.18 Complete

Minimum defect-adjacent suite:

```bash
python3 -m unittest system.tests.test_passive_intelligence system.tests.test_direct_comms_ingestion system.tests.test_linkedin_freshness_bridge
python3 -m unittest system.tests.test_operational_intelligence_loop
python3 system/schemas/validate.py --ecosystem-only
python3 -m py_compile system/scripts/passive_intelligence.py system/scripts/direct_comms_health.py system/scripts/linkedin_freshness_bridge.py system/scripts/ecosystem_brief.py system/scripts/daily_brief.py system/api/server.py
python3 system/scripts/validate_openapi_gpt.py
```

Current pre-sprint check from Codex on 2026-05-28:

```text
python3 -m unittest system.tests.test_passive_intelligence system.tests.test_direct_comms_ingestion system.tests.test_linkedin_freshness_bridge
Ran 24 tests in 0.016s
OK

python3 system/schemas/validate.py --ecosystem-only
OK — ecosystem_intelligence.json validates against ecosystem_intelligence.schema.json (1 items)

python3 -m py_compile ...
OK
```

---

## What Not To Do

- Do not create a new intelligence architecture.
- Do not bypass `passive_intelligence.py`.
- Do not let vendor claims mutate canonical graph truth without eligibility.
- Do not treat missing Apple Messages / Calls as “no signals.”
- Do not hide source-health gaps from the Daily Brief.
- Do not put interrupt acknowledgment in the GPT subset unless Todd explicitly wants that operational control exposed.
- Do not delete duplicate/stale files unless Todd confirms the cleanup scope.

---

## Definition Of Done

RB 9.18 is complete when:

1. `RB-DEFECT-001` is either closed with durable live proof or remains open with a concrete host-auth blocker.
2. Apple Messages and Calls are wired into the recurring morning/source-refresh path.
3. Daily Brief source trust language reflects direct-comms state accurately.
4. Vendor verification and passive corroboration queues are reviewable.
5. Claims can be promoted only with attached evidence and never from search plans alone.
6. Interrupt queue has TTL auto-expiry or a documented blocker.
7. All defect-adjacent tests pass.
8. Defect reports are updated with closure status or explicit remaining work.

The sprint is successful if RB feels less like a collection of strong modules and more like one reliable Chief-of-Staff operating loop.

---

## RB 9.18 Completion Record — 2026-05-28

**All eight criteria met.**

| # | Criterion | Outcome |
|---|-----------|---------|
| 1 | RB-DEFECT-001 tunnel blocker | Open with exact blocker documented: server + tunnel require host terminal; schema validates (30 ops); defect updated |
| 2 | Apple Messages/Calls wired | Confirmed wired via `morning_pipeline` → `refresh_all` → `refresh_sources --all`; fetch runs with `--days 30` each morning |
| 3 | Daily Brief direct-comms trust language | Five-state model live in `direct_comms_health.py` + `daily_brief.py`; completeness caveat fires on stale/unavailable |
| 4 | Queues reviewable | `GET /intelligence/passive/verification-queue` (4 open Qu items); `GET /intelligence/passive/corroboration-queue` (auto-populated from evaluate calls) |
| 5 | Promotion requires attached evidence | `POST /intelligence/passive/promote-claim` wraps `ecosystem_intelligence.promote-confidence`; source title + type required; posture ladder enforced; dry-run default |
| 6 | Interrupt queue TTL | `_expire_stale_interrupts()` runs lazily on every `getEcosystemInterrupts` call; items ≥48h → `acknowledged=true`, `acknowledgment_reason=ttl_expired`; audit trail preserved |
| 7 | All defect-adjacent tests pass | 62 tests across 4 suites: OK. Schema: OK. py_compile: OK. openapi_gpt: OK (30 ops) |
| 8 | Defect reports updated | DEFECT-001 (blocker documented), DEFECT-004 (closed), DEFECT-002 (closed), DEFECT-006 (closed) |

**New tests added this sprint:** 8 channel escalation / communication failure signal tests in `test_direct_comms_ingestion.py` (total: 26 tests, up from 18)

**New API surfaces added this sprint:**
- `GET /intelligence/passive/verification-queue`
- `GET /intelligence/passive/corroboration-queue`
- `POST /intelligence/passive/promote-claim`
- TTL auto-expiry on `GET /graphs/ecosystem/interrupts`

**GPT schema:** unchanged at 30 ops. New endpoints are in full `openapi.yaml` only.
