# RB 9.16 Sprint — Operational Intelligence Loop

Date: 2026-05-27
Status: planned
Builds on: RB 9.15 (Federated Graph Operational Intelligence), RB 9.13 (Security/Privacy), Codex Ecosystem Intelligence seed

---

## Purpose

RB has a macro restaurant ecosystem graph with 1,578 brand entities and a validated schema. It has a live daily brief pipeline, a security/privacy/audit layer, and a passive LinkedIn signal ingress path. None of these are connected to each other.

This sprint wires them together into an operational intelligence loop: a system that accumulates evidence, applies a confidence model, watches the ecosystem on behalf of the user, and surfaces what matters — through the daily brief as the primary mutation and reporting surface.

The product shift: RB stops being a relationship assistant that occasionally reads the graph, and becomes an intelligence system that is always watching, assessing, and reporting through a single daily operating surface.

---

## Core Principle

The daily brief is the primary scheduled mutation surface.

Everything the system learns — new vendor signals, confidence changes, watch list hits, activation triggers, user-provided breaking news — flows into and out of the brief. There is no separate report, no separate signal digest, no separate mutation job. One surface. One time. Always watching in the background; always reporting at the brief.

The one exception: a short list of high-urgency signals that cannot wait. Those interrupt. But the bar is high.

---

## Federated Graph Layers (Sprint Context)

This sprint touches two graph layers:

- **Macro ecosystem graph** (`system/ecosystem_intelligence.json`) — restaurant brands, vendors, relationships, signals, assessments. This is the layer we are building on.
- **Personal relationship graph** (`baseline_index.json`, profiles) — contacts, threads, trust, cadence. This sprint does not mutate the relationship graph; it reads it for the RI overlay.

The macro and personal graphs connect through `user_relevance` records (entity coverage). They do not merge.

---

## Sprint Deliverables

### 1. Vendor Relationship Seed (Prerequisite)

The confidence model and watch list cannot function without edges. Populate POS vendor relationships for a high-value brand subset.

**Target brands:** McDonald's, Burger King, Taco Bell, Wendy's, Subway, Chick-fil-A, Domino's, Chipotle, Starbucks, Dunkin', Shake Shack, Dutch Bros, Jack in the Box, Panera, Panda Express.

**Approach:**
- Use `system/inbox/ecosystem/pos_seed.csv` if populated; otherwise seed manually from known industry knowledge with `evidence_posture: provisional` and `confidence.level: low` for unverified claims.
- Each relationship record must include: `relationship_type`, `category`, `vendor_role`, `evidence_posture`, `interpretation_scope`, `confidence`, `sources`, `created_at`.
- Do not flatten. Use `vendor_role` to distinguish `system_of_record_pos` from `approved_hardware_vendor` from `payment_device_vendor`. McDonald's / NCR / NewPOS caution from the domain pack applies here.
- After seeding, run `python3 system/schemas/validate.py --ecosystem-only` and confirm entity count is still ~1,578 with new relationship records added.

**Acceptance:** At least 15 vendor relationship records across the target brands. Schema validates. `getEcosystemGraphQuery(query_type="vendor", query="par")` returns a non-empty result.

---

### 2. Confidence Model Implementation

The schema has `confidence.level`, `evidence_posture`, and `confidence.review_after`. This sprint adds behavioral rules and the fields needed to implement decay vs. lock.

**Two-state confidence model:**

| State | Trigger | Behavior |
|---|---|---|
| Decaying | `evidence_posture` is `provisional` or `partially_substantiated` | `confidence.review_after` is set; staleness check runs during daily brief |
| Locked | `evidence_posture` is `substantiated` AND relationship is sticky tech (POS, payments, back-office ERP) | `confidence.review_after` is null; confidence does not decay. Unlocks on disruption signal. |

**Schema additions** (add to `relationship` definition in `ecosystem_intelligence.schema.json`):
- `last_verified_at`: `date | null` — date the relationship was last corroborated by a new source
- `verified_by`: `string | null` — source ID that last elevated confidence
- `staleness_flag`: `boolean` — set true by brief pipeline when `review_after` is past and no corroboration has arrived

**Implementation in `ecosystem_intelligence.py`:**
- Add `check-staleness` CLI command: scans all relationships, sets `staleness_flag: true` on any record where `confidence.review_after` is past and `evidence_posture` is not `substantiated`.
- Add `promote-confidence` CLI command: given a relationship ID and a source ID, promotes `evidence_posture` from `provisional` → `partially_substantiated` → `substantiated`; sets `last_verified_at`; clears `staleness_flag`; sets `verified_by`.
- Sticky tech categories (`pos`, `payments`, `back_office`, `erp`) automatically set `confidence.review_after: null` when promoted to `substantiated`.

**Acceptance:** Running `check-staleness` on the seeded POS relationships produces a correct staleness report. Promoting a provisional relationship to substantiated clears the staleness flag and sets `last_verified_at`. Schema validates after both operations.

---

### 3. Watch List

**Purpose:** User-curated priority entities that receive elevated monitoring. CoS ambient scan covers the full graph but only interrupts for watch list hits of sufficient urgency.

**Storage:** Add `watch_list` section to `ecosystem_intelligence.json`.

**Schema addition** to `ecosystem_intelligence.schema.json`:
```json
"watch_list": {
  "type": "array",
  "items": {
    "type": "object",
    "required": ["entity_id", "priority", "added_at", "reason"],
    "additionalProperties": false,
    "properties": {
      "entity_id": {"type": "string"},
      "priority": {"type": "string", "enum": ["tier_1", "tier_2", "tier_3"]},
      "added_at": {"type": "string", "format": "date"},
      "added_by": {"type": "string", "enum": ["user", "cos_suggested"]},
      "reason": {"type": "string"},
      "last_signal_at": {"type": ["string", "null"], "format": "date"},
      "interrupt_eligible": {"type": "boolean"}
    }
  }
}
```

**Tier definitions:**
- `tier_1`: User explicitly named. Any signal triggers immediate consideration. `interrupt_eligible: true` by default.
- `tier_2`: CoS-suggested based on relationship coverage, active thread linkage, or opportunity assessment. `interrupt_eligible: false` by default.
- `tier_3`: Ambient — in the broader ecosystem scan, no special handling.

**API endpoints to add to `server.py`:**
- `GET /graphs/ecosystem/watchlist` (`getWatchList`) — return current watch list with entity metrics and last signal date.
- `POST /graphs/ecosystem/watchlist` (`updateWatchList`) — add, update priority, or remove an entity. `confirm=false` previews; `confirm=true` writes.

**CLI command:** Add `watch-list` command to `ecosystem_intelligence.py` for `--add`, `--remove`, `--show`, and `--set-priority`.

**Acceptance:** User can add McDonald's as `tier_1` via API. `getWatchList` returns it with entity metrics. Schema validates.

---

### 4. Daily Brief Ecosystem Integration

**Purpose:** The daily brief becomes the operational surface for ecosystem intelligence — it reads the graph, reports what changed, surfaces watch list signals, and proposes mutations.

**Add an `ecosystem_intelligence` section to the daily brief output.** This section is generated by a new function in `daily_brief.py` (or a dedicated `ecosystem_brief.py` module) that runs during brief generation.

**Section structure:**
```
ecosystem_intelligence:
  watch_list_signals:       # signals touching watch list entities since last brief
  staleness_flags:          # relationships with staleness_flag: true, needing verification
  ambient_surface:          # 1-2 high-conviction non-watch-list findings (CoS ambient)
  mutation_log:             # mutations applied during this brief cycle
  verification_queue:       # relationships queued for verification (up to 5)
  needs_todd:               # items the CoS cannot resolve alone; user input required
```

**Brief generation logic:**

1. **Staleness check** — run `check-staleness` scan; include any newly flagged relationships in `staleness_flags`.
2. **Signal assessment** — scan `system/inbox/market_signals.json` and `system/inbox/linkedin.daily_signals.jsonl` for any item whose `company` field matches a graph entity. For matches:
   - If signal is a leadership change, RFP signal, pain signal, or vendor displacement → classify as activation candidate.
   - If signal is general market context → include in `ambient_surface` at most.
   - Write matched signals as graph signal records (auto-mutate for high-confidence; propose for medium; skip for low).
3. **Watch list scan** — for each `tier_1` and `tier_2` entity, check if any new signal touches it. Report in `watch_list_signals`.
4. **Ambient surface** — from the full signal scan, select 1-2 non-watch-list findings that cross a high-conviction threshold (leadership change at a brand with no current vendor lock, funding event, known competitor displacement). No more than 2 ambient slots per brief.
5. **Mutation log** — report all graph mutations applied during this brief cycle with entity, mutation type, old state, new state, source, and confidence.
6. **Needs Todd** — items the CoS assessed but cannot resolve: ambiguous claims, conflicting sources, breaking news from user-supplied input, watch list entities where the CoS recommends adding to tier_1.

**Integration point:** `daily_brief.py` calls the ecosystem brief function as a section builder. The section is included in `canonical_brief` under the section key `ecosystem_intelligence`. The GPT instructions already respect `section_order` from the canonical brief.

**GPT instruction addition** to `custom_gpt_instructions_8k.md`:
- When daily brief includes `ecosystem_intelligence`, render `watch_list_signals` and `mutation_log` before `ambient_surface`.
- Treat `needs_todd` items as `ask_todd` priority.
- Never summarize away staleness flags or mutation log; render them as-is.

**Acceptance:** Brief generated with at least one seeded signal (from `market_signals.json` — Yum Brands AI backbone item already exists) produces a non-empty `ecosystem_intelligence` section. Signal is matched to a graph entity (`brand-yum-brands` or equivalent). Mutation log reflects the write. Staleness check runs and reports correctly.

---

### 5. CoS Activation Logic

**Purpose:** Classify signals into activation triggers that prompt the user to take action — inside the brief, or (for the highest-urgency cases) between briefs.

**Signal classification taxonomy** (add to `ecosystem_intelligence.py` and brief pipeline):

| Signal Class | Examples | Brief Behavior | Interrupt Eligible |
|---|---|---|---|
| `leadership_change` | New CTO, new VP Tech, CEO replacement at a tracked brand | `activation_candidate` in brief | Yes, if tier_1 |
| `rfp_cycle_signal` | RFP announcement, vendor evaluation mention, contract anniversary | `activation_candidate` in brief | Yes, if tier_1 |
| `extreme_pain` | Public operational failure, social volume spike on system issues | `activation_candidate` in brief | Yes, if tier_1 |
| `vendor_displacement` | Confirmed or rumored vendor replacement at a tracked brand | `activation_candidate` in brief | Yes, if tier_1 |
| `funding_event` | New capital round, IPO, acquisition | `watch_list_signal` in brief | No by default |
| `expansion_signal` | New market entry, franchise growth, international push | `ambient_surface` candidate | No |
| `general_market_context` | Industry trend, analyst commentary | `ambient_surface` candidate (low priority) | No |

**Activation record format** (written to graph `assessments` when triggered):
```json
{
  "id": "asm-2026-05-27-yum-leadership-change",
  "entity_id": "brand-yum-brands",
  "assessment_type": "activation_candidate",
  "summary": "Yum Brands CTO public commentary on AI backbone modernization. Platform-level tech transition signal. Consider outreach to restaurant tech leaders in this segment.",
  "risk": "yellow",
  "confidence": {"level": "medium"},
  "sources": ["src-market-signal-2026-05-27-yum"],
  "updated_at": "2026-05-27"
}
```

**Brief rendering:** Activation candidates surface in `watch_list_signals` (if watch list entity) or `ambient_surface` (if not) with a recommended action of `act_today` or `monitor` per the GPT instruction contract.

**Acceptance:** At least one activation candidate is produced from the seeded `market_signals.json` data during brief generation. The assessment is written to the graph and surfaced in the brief's ecosystem section with a named recommended action.

---

### 6. Interrupt Logic

**Purpose:** Define the narrow set of conditions under which the CoS should alert the user outside the daily brief.

**Interrupt threshold — eligible conditions (ALL must be true):**
1. Entity is on the watch list at `tier_1`.
2. Signal class is `leadership_change`, `rfp_cycle_signal`, `extreme_pain`, or `vendor_displacement`.
3. Signal confidence is `high` (from a primary or strong source).
4. The signal is dated within 48 hours of detection.

**Implementation:** Add `check-interrupt-queue` CLI command to `ecosystem_intelligence.py`. This command:
- Scans new signals written during the brief cycle.
- Identifies signals meeting all four conditions.
- Writes qualifying signals to `system/inbox/ecosystem/interrupt_queue.jsonl` with `entity_id`, `signal_class`, `summary`, `source`, `confidence`, `detected_at`.

**API endpoint:** `GET /graphs/ecosystem/interrupts` (`getEcosystemInterrupts`) — returns pending interrupt queue items not yet acknowledged by the user. Read-only.

**User acknowledgment:** A `confirm` call to `updateWatchList` with `last_signal_at` updated serves as acknowledgment and clears the item from the interrupt queue.

**GPT instruction addition:** When `getEcosystemInterrupts` returns non-empty results, render them before the daily brief or in response to any question, with `act_today` priority. The user does not need to ask; the CoS surfaces them.

**Scope note:** The interrupt mechanism writes a queue file. Actual push notification or OS-level interruption is outside this sprint. The queue is the contract; delivery mechanism is a future layer.

**Acceptance:** A simulated tier_1 watch list entity + high-confidence leadership change signal produces an entry in `interrupt_queue.jsonl`. `getEcosystemInterrupts` returns it. Acknowledging via `updateWatchList` clears it.

---

### 7. Schema, API, and Instruction Updates

**`ecosystem_intelligence.schema.json`:**
- Add `watch_list` array definition (see above).
- Add `last_verified_at`, `verified_by`, `staleness_flag` to relationship definition.
- Increment `version`.

**`server.py`:**
- Add `getWatchList` endpoint.
- Add `updateWatchList` endpoint.
- Add `getEcosystemInterrupts` endpoint.
- Confirm `getEcosystemGraphQuery` returns `staleness_flag` on relationship projections.

**`openapi_gpt.yaml`:**
- Add `getWatchList`, `updateWatchList`, `getEcosystemInterrupts` to the GPT action subset.
- Confirm operation count stays at or below 35.

**`custom_gpt_instructions_8k.md`:**
- Add ecosystem brief rendering rules (watch list signals first, then mutation log, then ambient).
- Add interrupt check rule: call `getEcosystemInterrupts` on brief load; render non-empty results immediately.
- Add `needs_todd` → `ask_todd` routing rule.

**`custom_gpt_prompt.md`:** Sync the same canonical routing rules.

---

### 8. RB-DEFECT-004 — Apple Messages / Calls Ingestion Gap

**Severity:** High  
**Category:** Source Coverage / RI Persistence / Daily Brief Trust / Privacy-Sensitive Local Ingestion

Direct communications are not optional side feeds. SMS/iMessage and phone calls are first-class relationship truth surfaces. They are where high-value signals appear first — when email fails, urgency rises, or a contact chooses a more personal channel. The current partial support for `system/inbox/messages.json` and `system/inbox/calls.json` is not reliable enough for the daily brief to claim coverage completeness without explicit caveats.

**What breaks today when these sources are stale or unavailable:**
- Inbound engagement from strategic contacts goes undetected
- Multi-channel escalation after email failure is missed
- Missed calls requiring same-day response do not surface
- Active opportunity continuity signals from texts or calls are lost
- "No new relationship signals" in the brief is not a reliable claim

**Sprint actions:**

**Preflight readiness checks** — Add a deterministic preflight function (in `refresh_sources.py` or a new `source_health.py`) for both Apple Messages and phone logs. Each check must distinguish five states:
- `unavailable` — database not accessible (Full Disk Access not granted or file missing)
- `available_stale` — accessible but last read exceeds freshness threshold
- `available_metadata_only` — accessible, but snippet content is empty or null
- `available_with_snippets` — accessible and content is readable
- `available_fresh` — accessible, fresh, content readable

Surface the Full Disk Access state explicitly. Never attempt to bypass macOS permissions. Surface permission failures as `unavailable` with a deterministic recovery instruction, not as silent omission.

**Refresh pipeline hardening** — `refresh_sources.py` must treat `messages` and `calls` as first-class source targets. When run with `--messages` or `--calls`:
- Write fresh source-health state to the cache.
- Never let a stale direct-communication source appear as silently complete in the brief.
- If content is unavailable or metadata-only, write that state explicitly so the brief can report it.

**Passive RI candidate extraction** — Direct interactions must feed passive RI with the following fields per event:
- `contact_id` (matched; null if unmatched)
- `channel` (`sms`, `imessage`, `call_inbound`, `call_outbound`, `call_missed`)
- `direction` (`inbound`, `outbound`)
- `event_at`
- `snippet_available` (boolean)
- `active_thread_ids` (matched from `active_threads.yaml`)
- `urgency_signal` (boolean — channel escalation after email, missed call from active opportunity contact)
- `proposed_mutation` or `block_reason`

Privacy constraints apply: no broad raw transcript persistence. Store only the minimized evidence needed for RI, loop, and brief grounding. Redact or hash raw message content where full text is not necessary. No sending or replying on Todd's behalf. Ever.

**Daily brief trust integration** — The brief must include direct-communication source state in the `resource_verification_and_freshness_status` section and surface it in relationship signal review and operational risks when state is anything other than `available_fresh`. The brief must never imply completeness when direct-communication sources are stale, unavailable, or metadata-only. Specific brief language rules:

- If `unavailable`: "Apple Messages was unavailable. Direct SMS/iMessage signals could not be assessed. [Recovery instruction]."
- If `available_stale`: "Apple Messages is stale (last read: [date]). Signals since [date] were not assessed."
- If `available_metadata_only`: "Apple Messages was read but content was not inspectable. Snippet-based RI was not possible."
- If `available_fresh`: surface matched signal count, passive RI candidate count, and any urgency flags.

**Acceptance:**
- Preflight correctly returns each of the five states given fixture conditions.
- A fresh messages read with a matched contact produces a passive RI candidate record.
- A missed call from an active-opportunity contact produces an `urgency_signal: true` record and surfaces in brief operational risks.
- A stale or unavailable messages source produces the correct brief language — brief does not claim completeness.
- Schema validates. No raw message content persists beyond minimized evidence.

---

### 9. RB-DEFECT-005 — Micro Graph Bypass / Artifact-First Retrieval Failure

**Severity:** High  
**Category:** Artifact Retrieval / Graph Utilization / Trust & Source Governance

**Root cause:** RB currently behaves like a generalized assistant unless explicitly instructed to use a micro graph. When a user asks a company-specific question — "How many McDonald's franchisees are there?" — RB answers from generalized model knowledge instead of routing to the McDonald's micro artifact that already exists in the ecosystem. This breaks the operating model: artifact-first retrieval, proof-based answers, source-aware trust weighting.

**Canonical failure statement:** RB failed to utilize the highest-authority available artifact for a company-specific query and instead defaulted to generalized reasoning.

**Canonical retrieval priority (to be enforced):**

```
User Artifact → RB Micro Graph → RB Macro Graph → Verified External Source → General Model Knowledge
```

The GPT instructions already include a McDonald's activation rule. The gap is that the activation depends on the user mentioning specific trigger terms. An implicit company-specific factual question ("how many franchisees") does not reliably fire the trigger. The fix is two-layered: routing logic in the instruction set, and a graph presence index so RB knows what micro artifacts exist without needing the user to name them.

**Sprint actions:**

**Graph presence index** — Add a `system/graphs/micro/index.json` file (or extend the existing `system/graphs/micro/mcdonalds_us_ops/index.json` activation record) into a queryable presence index. The index must expose:
- `graph_id`
- `entity_name` (e.g. "McDonald's")
- `entity_aliases` (e.g. ["McDonalds", "MCD"])
- `scope_domains` (e.g. ["operator_topology", "franchisee_structure", "field_ops"])
- `question_domains` — the classes of questions this graph can answer (e.g. ["franchisee_count", "operator_entity_count", "field_office_structure", "store_lookup", "nsn"])
- `freshness_date`
- `confidence_summary`
- `available` (boolean)

**API endpoint** — Add `GET /graphs/micro/index` (`getMicroGraphIndex`) to `server.py`. Returns the presence index: which micro graphs exist, what they cover, and their freshness. The Custom GPT calls this to determine whether a micro graph is available before falling back to general knowledge.

**Routing logic hardening** in `custom_gpt_instructions_8k.md`:
- Add an explicit company-specific question routing rule: before answering any factual question about a named company (counts, structure, relationships, deployments, personnel), check `getMicroGraphIndex` to determine if a micro graph covers that company and question domain.
- If a matching micro graph exists and is available: call `getMicroGraphSummary` first. Do not answer from general knowledge until the micro graph has been queried and either returned insufficient data or is unavailable.
- Extend the existing McDonald's trigger term list to include implicit question types: "how many", "count of", "number of", "who runs", "what is the structure", "how is it organized" — when the subject is a company with a known micro graph.

**Provenance surface requirement** — Every answer that draws from a micro graph must include:
- source artifact name
- freshness date
- confidence level (`verified`, `inferred`, `vendor_claimed`, `estimated`)
- whether the figure is direct (from a table row) or derived (formula, extrapolation)

If the micro graph is queried and returns a count, that count must be labeled with its source. If the micro graph is unavailable, RB must say so and state what it fell back to and why. It must not present a fallback answer at the same confidence level as a verified artifact answer.

**Canonical retrieval audit logging** — When RB answers a company-specific question, log:
- which graph was queried (micro, macro, none)
- which artifact was selected
- whether a fallback occurred and the reason
- the confidence level of the answer returned

This is an audit event (`graph_query_routed`) in `audit_log.py`. It does not need to be surfaced to the user on every query, but it must be written so that retrieval behavior is auditable.

**Acceptance:**
- "How many McDonald's franchisees are there?" calls `getMicroGraphSummary` before answering. Answer includes artifact name, freshness date, and confidence label.
- "How many McDonald's franchisees are there?" does not produce a general-knowledge answer when the micro graph is available and covers franchisee counts.
- If the micro graph is unavailable, RB states it is unavailable and explicitly labels the fallback answer as `estimated` or `general_knowledge`, not `verified`.
- `getMicroGraphIndex` returns the McDonald's micro graph with `question_domains` including `franchisee_count`.
- A general restaurant industry question ("what is Toast's market position?") does not trigger the McDonald's micro graph.
- Retrieval routing is logged as `graph_query_routed` in the audit log.

---

### 10. Test Suite

**Ecosystem intelligence (deliverables 1–7):**
- Vendor seed: at least 15 relationship records validate against schema.
- Confidence decay: `check-staleness` flags provisional relationships with past `review_after`; does not flag substantiated sticky-tech relationships.
- Confidence promotion: `promote-confidence` advances `evidence_posture`, sets `last_verified_at`, clears `staleness_flag`.
- Watch list: add/remove/tier-update via CLI and API; schema validates.
- Brief ecosystem section: brief generated with known signal input produces non-empty `ecosystem_intelligence` section with correct structure.
- Signal classification: at least 4 signal classes (`leadership_change`, `rfp_cycle_signal`, `extreme_pain`, `funding_event`) classified correctly from fixture inputs.
- Interrupt queue: tier_1 + high-confidence `leadership_change` signal produces interrupt record; non-qualifying signal does not.
- RI overlay baseline: `user_relevance` records are created for at least 3 graph entities with direct profile matches (uses existing baseline contacts; no new contacts created).
- OpenAPI subset validates at or below 35 operations.

**RB-DEFECT-005 micro-graph routing (deliverable 9):**
- Company-specific franchisee count question routes to micro graph, not general knowledge.
- Micro graph unavailable → fallback answer labeled `general_knowledge` or `estimated`, not `verified`.
- General industry question does not activate McDonald's micro graph.
- `getMicroGraphIndex` returns correct presence record with `question_domains`.
- Retrieval routing audit event written correctly.

**RB-DEFECT-004 regression fixtures (deliverable 8):**
- Matched inbound SMS from active-opportunity contact → passive RI candidate produced.
- Missed call from an RC → `urgency_signal: true`, surfaces in brief operational risks.
- iMessage channel escalation after bounced email → urgency signal detected.
- Unavailable Messages database → brief reports unavailable, no completeness claim.
- Available metadata with `snippet=null` → brief reports metadata-only, no completeness claim.
- Stale Calls feed → brief reports stale with last-read date.

---

## Acceptance Criteria

- "What's on my watch list?" calls `getWatchList` and returns entities with metrics, last signal date, and coverage status.
- "What changed in the ecosystem today?" calls `getDailyBrief` and renders the `ecosystem_intelligence` section with watch list signals, mutation log, and ambient findings.
- A Yum Brands leadership signal in `market_signals.json` is classified as `activation_candidate`, mutated into the graph as an assessment, and surfaced in the brief with `act_today` or `monitor` action.
- A provisional POS relationship with a past `review_after` date shows `staleness_flag: true` in the graph and in the brief's `staleness_flags` output.
- A tier_1 watch list entity with a new high-confidence leadership change signal appears in `interrupt_queue.jsonl` and is returned by `getEcosystemInterrupts`.
- The CoS offers to add a non-watch-list entity to the watch list when it surfaces a high-conviction ambient finding ("you're not watching this, but you should consider it").
- "How many McDonald's franchisees are there?" routes to the micro graph first; answer carries source artifact, freshness date, and confidence label.
- Micro graph unavailable path produces an explicitly labeled fallback — never a general-knowledge answer presented as verified.
- `getMicroGraphIndex` is queryable and returns correct presence records.
- Apple Messages preflight returns the correct state for each of the five readiness conditions.
- A stale or unavailable direct-communication source produces explicit brief language; the brief does not claim completeness.
- A missed call from an active-opportunity contact surfaces in brief operational risks with `urgency_signal: true`.
- Schema validates. OpenAPI subset validates at or below 35 operations.

---

## What This Sprint Does Not Include

- Push notification or OS-level interrupt delivery (queue only).
- Full RI overlay implementation (see `RI_OVERLAY_DESIGN.md`; baseline matching is included, full overlay build is not).
- Automated external news crawling or RSS ingestion (manual `market_signals.json` inbox remains the signal surface for now).
- Micro graph (McDonald's NSN) integration into the brief — keep the micro graph dormant unless explicitly activated by a user question.
- Multi-tenant or multi-user scope. All graph data is Todd's private intelligence layer.
- Push notification or OS-level interrupt delivery for direct-comms urgency signals (queue only; delivery is a future layer).
- Automated Apple Messages reply or send on Todd's behalf (explicitly out of scope permanently until explicitly scoped as a separate hardened feature).

---

## Implementation Notes

- `system/ecosystem_intelligence.json` is the canonical mutable graph. All writes go through `ecosystem_intelligence.py` CLI or the API write endpoints.
- All ecosystem ingestion events should call `audit_log.py` (`item_persisted` for mutations, `source_accessed` for signal reads). See `system/SECURITY_PRIVACY_ARCHITECTURE.md`.
- No raw signal text is stored in the graph. Signals store data-only summaries and entity references per P-038.
- Technomic-derived brand metrics are Todd-owned private data. They must not become seed data for other users.
- The `pos_seed.csv` in `system/inbox/ecosystem/` should be reviewed before manual seeding — it may already contain vendor evidence rows.

---

## Open Questions

1. Should `watch_list` live inside `ecosystem_intelligence.json` or as a separate `watch_list.json` file? (Recommendation: inside the graph for schema coherence and single-file validation, but separable if the watch list becomes large.)
2. Should the CoS ambient surface slot be configurable (default 2, user can set to 0 to suppress)? Initial recommendation: hardcoded at 2, configurable in a future sprint.
3. Should signal matching against graph entities use exact name match + slug, or fuzzy? Initial recommendation: slug match with alias fallback; no probabilistic fuzzy matching until entity resolution is a dedicated sprint.
4. Should `promote-confidence` require Todd confirmation when promoting to `substantiated`, or is corroboration from two sources sufficient? Initial recommendation: two-source corroboration is sufficient for `partially_substantiated`; `substantiated` requires Todd confirmation or one primary source.

---

## Files This Sprint Touches

### Modified
- `system/ecosystem_intelligence.json` — vendor relationships, watch list, signal/assessment records
- `system/schemas/ecosystem_intelligence.schema.json` — watch list definition, new relationship fields
- `system/scripts/ecosystem_intelligence.py` — `check-staleness`, `promote-confidence`, `watch-list`, `check-interrupt-queue` CLI commands
- `system/scripts/daily_brief.py` — ecosystem brief section builder; direct-comms source state integration
- `system/scripts/refresh_sources.py` — messages and calls as first-class source targets; source-health state writes
- `system/api/server.py` — `getWatchList`, `updateWatchList`, `getEcosystemInterrupts`, `getMicroGraphIndex` endpoints; staleness field on relationship projection
- `system/api/openapi_gpt.yaml` — new endpoints in GPT action subset (`getMicroGraphIndex` added)
- `system/api/custom_gpt_instructions_8k.md` — ecosystem brief rendering rules, interrupt check rule, micro-graph routing hardening (company-specific question routing, implicit trigger expansion, provenance surface requirement)
- `system/api/custom_gpt_prompt.md` — sync routing rules
- `system/SCHEMAS.md` — document new fields

### New
- `system/tests/test_operational_intelligence_loop.py` — test suite for this sprint
- `system/tests/test_direct_comms_ingestion.py` — RB-DEFECT-004 regression fixtures
- `system/tests/test_micro_graph_routing.py` — RB-DEFECT-005 routing and provenance fixtures
- `system/graphs/micro/index.json` — micro graph presence index
- `system/inbox/ecosystem/interrupt_queue.jsonl` — interrupt queue (created on first hit)

### Read (context only, do not modify)
- `system/SECURITY_PRIVACY_ARCHITECTURE.md`
- `system/protocols/P-038_privacy_security_ingestion.md`
- `system/design/FEDERATED_GRAPH_ARCHITECTURE.md`
- `system/design/RI_OVERLAY_DESIGN.md`
- `system/CODEX_HANDOFF_2026-05-27_ECOSYSTEM_INTELLIGENCE_ALIGNMENT.md`
- `system/domain_packs/restaurants.json`
- `system/inbox/ecosystem/README.md`

---

## Suggested Claude First Steps

Before writing any code:

```bash
python3 system/scripts/ecosystem_intelligence.py summary
python3 system/schemas/validate.py --ecosystem-only
python3 -m unittest system.tests.test_ecosystem_intelligence
```

Confirm:
- Entity count is ~1,578.
- `relationships`, `signals`, `assessments`, `watch_list` are empty.
- All tests pass.

Then review `system/inbox/ecosystem/pos_seed.csv` to determine if vendor evidence rows are already present before seeding manually.

---

## Sprint Output Contract

At sprint close, produce:

- Updated code and tests.
- A handoff file (`CLAUDE_HANDOFF_RB_9_16_OPERATIONAL_INTELLIGENCE_LOOP.md`) with:
  - Entity count (should be ~1,578, unchanged).
  - Vendor relationship count, breakdown by category and evidence posture.
  - Watch list entity count.
  - Signal records written.
  - Assessment records written.
  - Interrupt queue records (if any from tests).
  - Test counts by category.
  - Any open questions resolved or escalated.
  - Exact commands run.
