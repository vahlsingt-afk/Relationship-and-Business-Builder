# Executive Open Loop Management System — Technical Specification
*Status: Implemented (P1–P3) · Author: RB System · Date: 2026-06-30 · Built: 2026-07-02 (RB-DEFECT-059)*

**Build note (2026-07-02):** P1–P3 are live — `system/eolms/loops.json`, `system/scripts/eolms.py`,
the `ELoop` dataclass in `rb_core.py`, and the Daily Brief Executive Status block in
`render_daily_brief.py` all exist and are seeded with real data (32 loops migrated from
`loop_ledger.md`, 19 strategic-program loops seeded from Todd's Open Loop Register review).
P4 (Custom GPT auto-capture instructions + a live `/eolms` API endpoint) and P5 (knowledge-graph
edge materialization) remain deferred — see §11 for the resolved open questions and what's
still outstanding. See `defects/RB-DEFECT-059_executive-open-loop-management-system_2026-07-02.md`
for the full build record.

**Deviations from this spec, as built:**
- Status enum extended beyond §3.2 to add `deferred` (intentionally paused, resumes via
  `activation_date`/`activation_condition`) and `monitor` (intentionally low-touch, exempt from
  staleness alerts) — needed to represent loops like "Executive Thought Leadership: Active (Paused)"
  and "Passive Career Intelligence: Monitor" that don't fit the original 8-state model.
- Added an explicit `blocked_by: string[]` field (distinct from `related_loop_ids`) so dependency
  enforcement in `eolms.py` is unambiguous, rather than overloading `related_loop_ids` semantics.
- Daily Brief integration reads `system/eolms/loops.json` directly from `render_daily_brief.py`
  (mirrors the existing `_render_captures()` / `capture_ingest` pattern) rather than threading a
  new field through `daily_brief.py`'s canonical-brief pipeline — avoids any risk to that
  19,000-line file's existing self-test harness. The interactive Custom GPT canonical brief does
  not yet surface EOLMS; that's part of deferred P4.

---

## 1. Problem Statement

The current loop system (`loop_ledger.md` + `rb_core.Loop` + `mutations.py`) is structurally a flat relational-action tracker: one party, one action, one target date, one of two states (open/closed). It is adequate for follow-up reminders but cannot represent strategic initiatives, decisions, waiting conditions, relationship health, or evolving long-running work.

EOLMS replaces this with an executive operating system: a persistent, structured, knowledge-graph-connected ledger of every meaningful commitment, initiative, and open obligation in the CEO's operational world.

---

## 2. What Changes vs. What Stays

### Stays
- `loop_ledger.md` remains the source of truth for all existing loops (backward-compatible read path)
- `parse_loop_ledger()` in `rb_core.py` continues to work for existing rows
- `mutations.py cmd_loop_add` remains functional for simple action loops
- `smart_loops.py` / `loop_autopilot.py` / `passive_verification.py` continue to operate

### Changes
- New **EOLMS ledger** stored as `system/eolms/loops.json` (JSON, not Markdown) — richer schema, machine-friendly
- `rb_core` gains an `ELoop` dataclass alongside `Loop`
- New `eolms.py` script: the primary interface for CRUD, lifecycle transitions, and executive status
- `render_daily_brief.py` gains an "Executive Status" block fed from EOLMS
- `smart_loops.py` gains an EOLMS write path (in addition to existing loop_ledger path)
- Knowledge graph registry (`system/graphs/index.json`) gains EOLMS loop nodes as first-class entity type

---

## 3. Data Model

### 3.1 Loop Categories

| Category | Description | Has Due Date | Lifecycle Terminus |
|---|---|---|---|
| `strategic_initiative` | Long-running, no defined end | No | `archived` |
| `project` | Defined work with completion point | Optional | `completed` |
| `action` | Discrete task | Yes | `completed` |
| `decision` | Requires executive judgment | Optional | `completed` |
| `waiting` | Blocked by external event | Yes (expected resolution) | `completed` |
| `relationship` | Ongoing contact requiring attention | No (cadence-based) | `archived` |
| `opportunity` | Potential future value | Optional | `completed` or `archived` |
| `research` | Requires intelligence before action | Optional | `completed` |

### 3.2 Lifecycle States

```
identified → qualified → active → waiting → blocked → dormant → completed
                                                              ↘ archived
```

| State | Meaning | Auto-transition trigger |
|---|---|---|
| `identified` | Surfaced but not yet evaluated | Manual or GPT-proposed |
| `qualified` | Confirmed worth tracking | CoS judgment or explicit confirm |
| `active` | Being worked | Manual or intelligence event |
| `waiting` | Blocked on named external condition | `waiting_on` field populated |
| `blocked` | Blocked on unnamed/systemic condition | Manual |
| `dormant` | No activity for N days (configurable per category) | Auto: 30d for action, 90d for strategic_initiative |
| `completed` | Done | Manual or passive verification |
| `archived` | Intentionally set aside | Manual |

Valid transitions (enforcement in `eolms.py`):
- Forward: any state → any later state
- Backward: `dormant` → `active` only (reactivation)
- Terminal: `completed` and `archived` accept no further transitions

### 3.3 ELoop Schema (JSON)

```json
{
  "id": "EL-2026-06-30-001",
  "title": "Global Payments VP Sales role",
  "category": "opportunity",
  "status": "waiting",
  "priority": "high",
  "strategic_value": "career",
  "owner": "Todd Vahlsing",
  "created_at": "2026-06-30",
  "updated_at": "2026-06-30",
  "last_activity": "2026-06-28",
  "next_action": "Follow up with Ryan Hildebrand if no response by 2026-07-07",
  "waiting_on": "Ryan Hildebrand response re: Global Payments restaurant opportunity",
  "due_date": null,
  "cadence_days": null,
  "related_people": ["Ryan Hildebrand", "Mike Schwartz"],
  "related_orgs": ["Global Payments", "Genius-Xenial"],
  "related_loop_ids": ["L-2026-05-08-007"],
  "related_documents": [],
  "source_ref": "loop_ledger:L-2026-05-08-007",
  "confidence": "high",
  "history": [
    {
      "ts": "2026-06-30T00:00:00",
      "event": "created",
      "note": "Migrated from L-2026-05-08-007"
    }
  ],
  "tags": []
}
```

#### Field Reference

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | `EL-YYYY-MM-DD-NNN` |
| `title` | string | yes | Short, executive-readable |
| `category` | enum | yes | See §3.1 |
| `status` | enum | yes | See §3.2 |
| `priority` | enum | yes | `critical` / `high` / `medium` / `low` / `monitor` |
| `strategic_value` | string | no | Free tag: `career`, `revenue`, `relationship`, `brand`, `research`, etc. |
| `owner` | string | yes | Defaults to Todd Vahlsing |
| `created_at` | ISO date | yes | |
| `updated_at` | ISO date | yes | Updated on any field change |
| `last_activity` | ISO date | yes | Last meaningful event (not just metadata touch) |
| `next_action` | string | no | CoS-surfaced recommended next step |
| `waiting_on` | string | no | Named blocker; triggers `waiting` status |
| `due_date` | ISO date | no | Hard deadline if any |
| `cadence_days` | int | no | For `relationship` category: days between expected touchpoints |
| `related_people` | string[] | no | Canonical person names from baseline_index |
| `related_orgs` | string[] | no | Canonical org names |
| `related_loop_ids` | string[] | no | Legacy `L-` loop ids or other `EL-` ids |
| `related_documents` | string[] | no | Paths or titles |
| `source_ref` | string | no | How the loop was created: `gpt:conversation`, `loop_ledger:L-XXX`, `smart_loops`, `manual` |
| `confidence` | enum | yes | `high` / `medium` / `low` |
| `history` | object[] | yes | Immutable append-only event log |
| `tags` | string[] | no | Free-form |

#### History Event Schema

```json
{
  "ts": "2026-06-30T14:00:00",
  "event": "status_change | field_update | note | intelligence_signal | auto_transition",
  "from": "active",
  "to": "waiting",
  "note": "Outreach sent; awaiting Ryan response"
}
```

---

## 4. Storage

```
system/
  eolms/
    loops.json          ← primary EOLMS ledger (array of ELoop objects)
    loops.schema.json   ← JSON Schema for validation
    archive/
      loops_YYYY-MM-DD.json   ← daily snapshots (write-before-modify)
```

`loops.json` is a JSON array, sorted by `updated_at` descending. Maximum expected size for the executive use case: ~500 loops total; no performance concern with in-memory load.

---

## 5. Affected Scripts

### 5.1 New: `system/scripts/eolms.py`

Primary interface. Subcommands:

```
eolms add        --title --category --status --priority [--waiting-on] [--due] [--people] [--orgs] [--note]
eolms update     --id --status [--next-action] [--waiting-on] [--note]
eolms transition --id --to <new_status> [--note]
eolms get        --id [--json]
eolms list       [--status active,waiting] [--category] [--priority] [--json]
eolms status     → Executive Status summary (counts by category/status)
eolms stale      → Loops with last_activity > dormancy threshold, not yet dormant
eolms migrate    → One-time import of existing loop_ledger.md rows into EOLMS
eolms validate   → Check loops.json against schema
```

All mutating commands: snapshot before write, dry-run by default, require `--confirm` to apply.

### 5.2 Modified: `system/scripts/rb_core.py`

Add:
- `ELoop` dataclass matching §3.3 schema
- `EOLMS_PATH = SYSTEM_DIR / "eolms" / "loops.json"`
- `load_eloops() → list[ELoop]`
- `eloops_by_status(loops) → dict[str, list[ELoop]]` — keyed by status
- `eloops_executive_summary(loops) → dict` — the counts dict for Daily Brief

No changes to existing `Loop`, `parse_loop_ledger`, or `loops_by_status`.

### 5.3 Modified: `system/scripts/render_daily_brief.py`

Add `render_executive_status(brief_data, eloops)` function that produces the Executive Status block:

```
## Executive Status

Active Strategic Initiatives: 3
Active Projects: 2
Decisions Needed: 1
Waiting Items: 4  (2 overdue for follow-up)
Relationship Follow-ups Due: 2
Blocked: 0
Completed Since Yesterday: 1
Dormant >30 Days: 3

CoS Recommendation: Two waiting items need follow-up today — Ryan Hildebrand
(Global Payments, 9 days) and Ed Gartner (MAPS outline, 19 days).
```

Block injected before the Decision Queue section. Falls back gracefully if `loops.json` is missing (renders nothing, no error).

### 5.4 Modified: `system/scripts/smart_loops.py`

After generating proposals (existing behavior), optionally write proposals to EOLMS with `source_ref: "smart_loops"` and `status: "identified"`. Gate behind `--eolms` flag so existing behavior is unchanged by default.

### 5.5 Modified: `system/schemas/` (new file)

`system/eolms/loops.schema.json` — JSON Schema v7 covering all fields in §3.3.

---

## 6. Knowledge Graph Integration

### 6.1 Node Type

EOLMS loops become a first-class node type in the graph registry alongside `person`, `organization`, `document`.

```json
{
  "node_type": "executive_loop",
  "id": "EL-2026-06-30-001",
  "title": "Global Payments VP Sales role",
  "category": "opportunity",
  "status": "waiting"
}
```

### 6.2 Edge Types

| Edge | Source | Target | Meaning |
|---|---|---|---|
| `loop_involves_person` | ELoop | Person | Person is relevant to this loop |
| `loop_involves_org` | ELoop | Org | Org is relevant to this loop |
| `loop_blocks` | ELoop | ELoop | One loop is a blocker for another |
| `loop_evolved_from` | ELoop | ELoop | This loop supersedes or split from another |
| `loop_cites_document` | ELoop | Document | Supporting document |

### 6.3 Query Patterns Enabled

- "What initiatives are affected by Global Payments?" → all ELoops with `loop_involves_org → Global Payments`
- "Which relationships influence my publishing project?" → traverse `loop_involves_person` from `EL-publishing-project`
- "What changed yesterday that impacts active strategic initiatives?" → filter history events with `ts ≥ yesterday` across ELoops where `category = strategic_initiative`
- "What is blocked because of the GP start date?" → ELoops with `waiting_on` containing "Global Payments" or `status = waiting` + `related_orgs` includes "Global Payments"

### 6.4 Implementation Approach

Phase 1 (this sprint): EOLMS loops stored as JSON with `related_people` / `related_orgs` string arrays. Graph queries satisfied by `eolms.py list --people "Ryan Hildebrand"` style filtering — no graph traversal required.

Phase 2 (future): `graphs/index.json` gains an EOLMS graph entry; edges materialized as adjacency lists in `graphs/micro/eolms/`. Enables true cross-entity traversal via the existing micro-ecosystem graph infrastructure.

---

## 7. Daily Brief Integration — Detailed

### 7.1 Data Flow

```
morning_pipeline.py
  → eolms.py status --json   (produces executive_summary.json in .cache/)
  → render_daily_brief.py    (reads .cache/executive_summary.json)
```

### 7.2 Executive Status Block Spec

Rendered immediately after the date/greeting block, before Decision Queue.

**Count rows** (always shown if EOLMS loaded):
- Active Strategic Initiatives
- Active Projects  
- Decisions Needed (status=active, category=decision)
- Waiting Items (status=waiting) — with sub-note if any have `last_activity` > 7 days ago
- Relationship Follow-ups Due (category=relationship, days since last_activity ≥ cadence_days)
- Blocked
- Completed Since Yesterday
- Dormant >30 Days (or >90d for strategic_initiatives)

**CoS Recommendation** (single line, auto-generated): surface the 1-3 loops that most need attention today, derived by: overdue waiting items first, then decisions with no next_action, then relationships past cadence.

### 7.3 Staleness Alert

If any `strategic_initiative` or `project` loop has `last_activity` older than 45 days and `status` is `active`, render a staleness warning in the CoS Recommendation: "3 active strategic initiatives have had no recorded activity in 45+ days."

---

## 8. Migration Plan

### 8.1 Existing loop_ledger.md loops

`eolms.py migrate --dry-run` maps each open `L-` row to an EOLMS loop:
- `category`: inferred from description keywords (introduce → action, reach out → action, monitor → waiting, define/draft → project, discuss → action)
- `status`: open → `active`; closed rows → `completed`
- `related_people`: extracted from `Party` column
- `source_ref`: `loop_ledger:L-XXXX`
- `history`: single "migrated" event

Manual review pass recommended before `--confirm` apply: `eolms.py migrate --dry-run --json > /tmp/migration_preview.json`

### 8.2 Coexistence

Both ledgers can run in parallel. `loop_ledger.md` remains authoritative for legacy L- loops. New loops created after EOLMS launch use EL- ids. No loop should exist in both ledgers for the same intent (migration deduplicates by `source_ref`).

---

## 9. Custom GPT Instructions Impact

`custom_gpt_instructions_8k.md` and `custom_gpt_instructions_compact_8k.md` need updates to:

1. **Capture intent**: When the CEO describes a commitment, initiative, or waiting condition, propose an EOLMS loop (EL-) rather than (or in addition to) an L- loop.
2. **Lifecycle guidance**: GPT should recognize when an existing loop transitions ("Ryan responded → the Global Payments waiting loop can move to active").
3. **Executive Status awareness**: GPT should surface relevant open loops proactively when the CEO discusses a person, org, or topic that appears in `related_people` / `related_orgs`.

Specific instruction additions: `eolms_capture_patterns` (keywords that trigger loop proposal) and `eolms_lifecycle_triggers` (phrases that trigger status transition).

---

## 10. Implementation Sequence

| Phase | Deliverables | Scope |
|---|---|---|
| P1 | `eolms.py` (add/update/list/status/validate), `ELoop` dataclass in `rb_core.py`, `system/eolms/loops.json` (empty), `loops.schema.json` | Core CRUD + schema |
| P2 | `eolms.py migrate`, review pass, seed loops from loop_ledger.md | Migration |
| P3 | `render_daily_brief.py` Executive Status block, `morning_pipeline.py` integration | Brief integration |
| P4 | `smart_loops.py --eolms` flag, GPT instruction updates | Auto-capture |
| P5 | Knowledge graph Phase 2 — materialized edges, cross-entity traversal | Graph linkage |

P1–P3 constitute a usable EOLMS. P4 enables the "automatic by default" principle. P5 enables the full knowledge-graph vision.

---

## 11. Open Questions — Resolved 2026-07-02

1. **Dormancy thresholds by category** — **Adopted as proposed**, plus `decision: 30d` and `waiting: 30d` (not in the original table). See `rb_core.EOLMS_DORMANCY_DAYS`.
2. **Who can create loops** — **Option 2 confirmed by Todd**: GPT/Claude creates directly as `active` (or the appropriate status) for high-confidence items; low-confidence/ambiguous items land as `identified` for review. Enforced today only at the CLI layer (`eolms.py add`); GPT-side judgment calls are part of deferred P4.
3. **Relationship cadence defaults** — **Deferred, not yet needed.** No `relationship`-category loops were seeded with tier-based defaults this pass (Executive Relationship Capital was seeded as a single strategic-initiative-style loop, not decomposed into per-contact cadence loops — that decomposition still lives in `loop_ledger.md`/RC cards). Revisit if/when per-contact relationship loops migrate into EOLMS.
4. **Loop evolution vs. loop succession** — **Update-in-place confirmed**, per the recommendation. `eolms.py transition`/`update` mutate the existing `ELoop` and append to `history` rather than archiving + recreating.
5. **Migration scope** — **Todd confirmed: migrate all open loops now.** `eolms.py migrate` mapped all 32 `loop_ledger.md` rows (24 open → `active`, 8 closed → `completed`); staleness was *not* hand-judged at migration time — instead `eolms.py tick` ran once immediately after migration and classified 3 loops as `dormant` based on `last_activity` (parsed from the most recent "Re-dated" note in each row, or `opened` date otherwise) versus category threshold. This reuses one code path instead of building separate migration-time and steady-state staleness logic.

### Still open (P4/P5 — not built this pass)

- Live `/eolms` API endpoint in `system/api/server.py` + `openapi.yaml`/`openapi_gpt.yaml` so the Custom GPT can read/write EOLMS directly (CRUD, not just closure) instead of only via CLI. Still blocked on the 30-operation Custom GPT cap (confirmed at 30/30) — needs either a retired op or a future cap renegotiation.
- Knowledge graph edge materialization (§6.4 Phase 2).

**Update 2026-07-02 (RB-DEFECT-060) — evidence-backed closure, done:** narrated evidence ("the
well pump is working", "Ryan responded") now auto-transitions the matching loop with no manual
CLI call needed, closing the specific gap called out above. This did **not** require a new GPT
operation or touch the 30-op cap — it extends the existing `ingestExecutiveDeclaration` endpoint
(already GPT-facing) and its `_execute_executive_declaration()` handler, which had an
`action_completed`/`loop` event type that was classified but never actually acted on a loop.
New `eolms.match_and_transition(text)` does the matching (name/org/tag-anchored scoring with a
confidence gate — ambiguous or low-confidence matches are reported but never auto-applied) and
reuses the existing `_apply_transition()`/dependency-gate logic, so this can't bypass `blocked_by`
enforcement. See `defects/RB-DEFECT-060_eolms-evidence-backed-closure-via-exec-declaration_2026-07-02.md`.

**Implemented 2026-07-02 — `pending_verification` status** (was tracked as follow-up
`EL-2026-07-02-021`, now `completed`). Applying Todd's standing "repro → primary path → failure
cases → regressions → telemetry → independent verification → Closed" checklist to RB-DEFECT-060
itself surfaced a real gap in the `completed` status: it means different things for "I sent the
email" (unambiguously done) versus "the fix is in and I dry-ran it, but nobody has independently
confirmed it works" (Fixed, not yet Verified). Built exactly as sketched:
- Add `pending_verification` to `ELOOP_STATUSES`, sitting between the active states and `completed`.
- `match_and_transition`'s `complete` intent targets `pending_verification` instead of `completed`
  when the loop is explicitly flagged as needing independent verification (a per-loop flag set at
  creation time — not inferred from `category`, since "defect-fix-shaped" isn't reliably a category).
- `pending_verification` surfaces distinctly in `eolms status`/the Executive Status block ("Fixed,
  awaiting verification: N") rather than silently vanishing into the same bucket as fully-closed work.
- Critically, `pending_verification` does **not** auto-advance via `tick` — only an explicit human-
  or GPT-confirmed transition moves it to `completed` (verification passed) or back to `active`
  (verification failed). This is a deliberate exception to "minimize friction": some closures need a
  human in the loop, and that's the whole point of adding the state.

New per-loop field `requires_verification: bool` (default `false`, so every existing/migrated loop
is unaffected) added to `ELoop`/`loops.schema.json`; CLI: `eolms add --requires-verification` and
`eolms update --requires-verification {yes,no}`. `tick`'s dormancy step required zero code changes —
it already only examines `status == "active"`, so `pending_verification` loops are excluded by
construction. 4 new tests in `test_eolms_declaration_closure.py` (16 total in that file), zero
regressions in the broader suite.
