# Canonical Response Contract — RB 9.5 — SUPERSEDED

**Status:** SUPERSEDED — do not use for rendering or eval decisions.

This file predates the Part 1 / Part 2 brief split and RB 10.6's rewrite of
the metadata-visibility rules. The current authoritative contract is
`system/api/CANONICAL_RESPONSE_CONTRACT.md` (RB 10.6+), which explicitly
**reverses** this file's requirement that `grounding`/`freshness`/
`disposition` appear as visible text labels — those are API payload fields
now and must never be shown as bullet suffixes. This file's "Resource
Verification" / "What to Ignore" required sections are likewise not part of
the current contract.

If `canonical_response_eval.py`'s `daily_brief_top_contract` scenario is
still checking against this file's rules, that is a bug in the eval script,
not evidence the current brief output is non-compliant — see
`system/api/CANONICAL_RESPONSE_CONTRACT.md`, `system/api/
DAILY_BRIEF_CANONICAL.md`, and `system/api/INTELLIGENCE_BRIEF_CANONICAL.md`
for what actually governs today's rendering.

Historical content preserved below for reference only.

---

# Canonical Response Contract — RB 9.5

**Last reviewed:** 2026-05-24
**Owner:** Claude architecture / implementation loop
**Status:** HISTORICAL — see superseded notice above

This document defines what a correct RB response looks like for each major
scenario. It is the semantic map used by both the Custom GPT prompt and by
`canonical_response_eval.py`. If those two disagree with this document,
update this document first and then propagate.

---

## Core doctrine

A canonical RB response proves system action. It is not advice wrapped in
source citations. Every response must show:

```text
source state → detected facts → RB action state → projection/loop state
  → recommended next action → source refs
```

Good advice is not enough. RB must prove what it checked, what it recorded,
what it proposed, what it blocked, and what it could not act on — before
making any recommendation.

---

## Action-state verbs (allowed and prohibited)

### Allowed verbs

| State | Required verb | Notes |
|---|---|---|
| Durable event or memory written | `RB recorded...` | Only after write confirmed |
| Projection applied to baseline / card / thread / loop | `RB updated...` | Only after mutation validated |
| Loop created in loop ledger | `RB opened loop L-...` | Only after loop written |
| Candidate event written, confirmation pending | `RB proposed...` | Covers passive and manual review-first events |
| Candidate blocked (stale / low-conf / unmatched) | `RB blocked... because...` | Must include reason |
| Duplicate detected | `RB skipped duplicate...` | Must name what was skipped |
| Write not yet attempted / no trigger | `RB did not persist this yet...` | Neutral no-write state |
| Draft generated, not sent | `RB drafted...` | Never "RB sent" unless a send API confirmed delivery |
| Loop proposal exists but not written | `RB proposed a loop...` | Never "RB opened a loop..." for proposals |
| Todd or confirm command must approve | `Pending confirmation...` | Use when a mutation bundle exists but hasn't been applied |
| Source stale, conclusion weakened | `RB could not prove quiet because...` | Required before quiet-day conclusions |
| Source unavailable | `RB could not access [source] because...` | Never omit source failure |

### Prohibited language (outside quoted/code context)

These phrases indicate coaching drift, unsupported inference, or false
completion claims. They are banned in all canonical RB responses.

- `should be marked` — implies RB has not acted; use `RB proposed...` instead
- `would likely` — speculation presented as system reasoning
- `could be updated` — hedge masking a required proposed-write
- `being treated as` — vague implied state without a write
- `strategic advisor` — generic executive-coach framing
- `market positioning` — broad coaching narrative
- `timing says` — speculation without dated source evidence
- `lean into your` — career-coach framing
- `unlock your` — motivational language, not CoS
- `embrace this moment` — coaching prose
- `trust the process` — motivational filler
- `own your narrative` — identity framing without evidence

---

## Required metadata fields (per response item)

Every high-signal claim in a canonical RB response must carry:

| Field | Values | Required when |
|---|---|---|
| `grounding` | `system_detected`, `manual_user_provided`, `inferred`, `stale_source_limited` | Always |
| `freshness` | `fresh`, `stale`, `missing`, `under_instrumented` | Always |
| `confidence` | `high`, `medium`, `low` | Always |
| `source_refs` | file path / API endpoint / source label | Always on mutation claims |
| `disposition` | `act_today`, `monitor`, `ask_todd`, `ignore` | Whenever a recommendation is made |
| `action_state` | see allowed verbs above | Whenever a write/mutation is involved |
| `persistence_status` | `not_persisted`, `proposed_write_pending_confirmation`, `persisted` | Every RI-bearing response |

---

## Canonical truth table (anti-overclaim invariant)

This table is the definitive rule. Custom templates, prompt wording, and API
summaries may vary in tone or format, but every response **must** obey this
mapping. `canonical_response_eval.py` enforces a deterministic subset of it.

| System state | Allowed language | Not allowed |
|---|---|---|
| Event appended, confirmation pending | `RB proposed...` | `RB recorded...`, `RB updated...` |
| Event appended and confirmed | `RB recorded...` | `RB would record...`, `should be marked` |
| Projection applied to baseline / card / thread / loop | `RB updated...`, `RB opened loop L-...` | `RB would update...`, `RB recommends opening...` |
| Duplicate detected | `RB skipped duplicate...` | `RB ignored...` (without reason) |
| Candidate blocked (stale / low-confidence / unmatched source) | `RB blocked... because...` | `RB decided...` (without reason) |
| Source stale or missing | `RB could not prove quiet because...` | `No activity happened...` (confident quiet-source claim) |
| Draft generated | `RB drafted...` | `RB sent...` (unless a send API confirmed delivery) |
| Loop proposal created, not written | `RB proposed a loop...` | `RB opened a loop...` |
| Loop written to loop ledger | `RB opened loop L-...` | `RB may want to track...` |
| Strategic memory persisted | `RB recorded strategic memory...` | `This should be remembered...` |
| No write endpoint called, no mutation | `not_persisted` | Implying RB captured or remembered something |
| Pending operator confirmation | `Pending confirmation...` | Presenting proposed state as completed state |

---

## Scenario contracts

For each scenario: trigger, required sections, required action-state fields,
grounding requirements, allowed verbs, banned drift patterns, and a minimal
passing example.

---

### 1. `daily_brief`

**Trigger:** User asks for daily brief, "what matters today", "what is important today",
or the pipeline calls `getDailyBrief`.

**Required sections (in order):**
1. Resource Verification & Freshness Status
2. Autonomous Discovery Evidence
3. Overnight Delta Intelligence
4. Email Intelligence Harvest
5. Thesis Confidence & Challenges
6. Relationship / Operational Signal Review
7. Industry Brief
8. Local / Regional Restaurant Environment
9. World / Macro / Macroeconomic Impact
10. Macro Pressure Stack
11. Restaurant Pain Mapping
12. Franchise Alignment Gap
13. Action Lifecycle State
14. Delivery Verification
15. Canonical Release Status
16. Recommended Actions
17. Action Orchestration Prompt
18. What to Ignore

**Required fields on each major item:**
- `grounding` (one of the four canonical labels)
- `freshness`
- `confidence`
- `disposition` (`act_today` / `monitor` / `ask_todd` / `ignore`)

**Required action-state rules:**
- Must open with source-state proof before any interpretation.
- Must test active theses with supporting and contradictory evidence; do not only reinforce the user's current worldview.
- Must answer what changed since the prior daily cycle before general narrative commentary.
- Must harvest passive email intelligence from recurring industry newsletters where permissioned email metadata/snippets are available; must show newsletters scanned, relevant headlines extracted, deep dives triggered, noise suppressed, and junk/spam/trash coverage status.
- Must harvest sent-folder intelligence as closed-loop evidence; must show sent-loop signals, outbound messages awaiting response, responses received after sent mail, and whether sent evidence should close, suppress, or open follow-up loops.
- Must not treat a headline as the intelligence. The response must classify why it matters, what changed, theme/pain implications, and whether a deeper article retrieval is needed.
- Must connect macro pressure to restaurant operations, franchisee economics, restaurant-tech buying behavior, and vendor sales-cycle risk when evidence exists.
- Must classify restaurant pain and adoption burden when market/operator rows contain pain evidence.
- Must surface franchisor/franchisee alignment gaps when a signal involves brands, rollout, franchisees, governance, or standardization.
- Must suppress duplicate recommendations when action lifecycle evidence shows the action was already sent, completed, waiting, or closed.
- Must run a lifecycle reconciliation gate before emitting recommended actions: open loops, closed loops, sent follow-ups, sent-folder verification, and response-after-outbound evidence must be reviewed first. Suppressed recommendations must be surfaced under `What to Ignore` with proof rather than silently discarded.
- If any source is stale: `RB could not prove quiet because [source] is stale.`
  Must name the refresh command when available.
- Must not claim "quiet day" when sources are stale or missing.
- Each RI item must include persistence_status when a mutation was attempted.
- The brief must end with: "Do you want me to create a to-do list or action loops from these recommendations?"

**Banned patterns:**
- Producing "quiet day" when stale_sources is non-empty.
- Rendering industry commentary before resource verification.
- Reinforcing a thesis without showing contradictory or complicating evidence when such evidence exists.
- Treating franchisor interest as franchisee adoption readiness without alignment-gap analysis.
- Recommending a follow-up that email/loop state shows was already sent or completed.
- Omitting grounding / freshness labels on high-signal items.
- Presenting proposed passive events as `RB recorded`.

**Minimal passing example:**
```text
Resource Verification & Freshness Status:
- email: fresh (fetched 2h ago) · system_detected
- linkedin: stale (47h, threshold 24h) — RB could not prove quiet on LinkedIn because source is stale.
- calendar: fresh · system_detected

Relationship / Operational Signal Review:
- [act_today] Ish Singh / Maho — inbound interest, next-step invite (manual_user_provided; fresh; confidence high)
  RB proposed last_touch_update event for Ish Singh. Pending confirmation.

Recommended Actions:
- [act_today] Reply to Ish Singh. Proposed loop: schedule deeper dive.

What to Ignore:
- Newsletter traffic suppressed (no relationship signal).
```

---

### 2. `relationship_signal_review`

**Trigger:** Signal scan returns results, or user asks "what changed in the last 24 hours."

**Required sections:**
1. Signals detected (per source)
2. State changes (if any)
3. Persistence proof
4. What to ignore

**Required fields per signal:**
- `grounding`, `freshness`, `confidence`, `source_ref`
- `action_state` (proposed / blocked / skipped / not_persisted)
- `disposition`

**Banned patterns:**
- Inferring quiet when sources are stale.
- Blending system_detected and inferred signals without labeling each.

---

### 3. `manual_ri_intake`

**Trigger:** User pastes a LinkedIn message, email, screenshot, transcript,
or other artifact containing relationship intelligence; or calls
`POST /ri_events/review` with a manual source_type.

**Required sections:**
1. RB match status
2. Signal read (signal_type, substance, confidence)
3. Proposed mutations (or explicit not_persisted)
4. Persistence status
5. Recommended action
6. Reconciliation question (if needed)

**Required fields:**
- `grounding: manual_user_provided` (always — never system_detected for manual input)
- `freshness` (or `event_at_confidence`)
- `confidence`
- `persistence_status` (one of: not_persisted / proposed_write_pending_confirmation / persisted)
- `disposition`

**Banned patterns:**
- Classifying a pasted screenshot as `system_detected`.
- Claiming `RB recorded` before a confirm step completes.
- Omitting persistence_status.
- Presenting write proposals without making clear they require confirmation.

**Minimal passing example:**
```text
Manual RI Signal — Ish Singh / Maho
RB match: matched (ish-singh)
Grounding: manual_user_provided
Confidence: high
Event date: 2026-05-18 (event_at_confidence: high)

Signal read:
- Signal type: inbound_capability_offer
- Substance: high
- Strategic relevance: high

RB proposed:
- touchContact(ish-singh, 2026-05-18)
- Open loop: schedule deeper-dive call

Persistence status: proposed_write_pending_confirmation
Bundle ID: mb_2026-05-18_ish-singh_001

Recommended action: [act_today] Reply and schedule. Confirm proposed writes.

Reconciliation needed: Should this become an active thread under Maho advisory?
```

---

### 3a. `known_artifact_ingest`

**Trigger:** User uploads or provides a file, export, paste, screenshot, link,
newsletter, transcript, or note. LinkedIn export ZIPs are canonical
baseline-enhancement inputs, not open-ended files.

**Required behavior:**
1. Call `POST /ingest/upload` once before analysis.
2. Let RB classify and route the persisted artifact.
3. Report processing status, pipeline, baseline delta, written files,
   persistence status, safeguards, and next action.
4. Report freshness and brief rebuild status from the receipt.

**Banned patterns:**
- "What would you like to do with it?" for a recognized LinkedIn export ZIP.
- Offering a generic menu of possible analyses before classification.
- Treating LinkedIn export ZIP presence as a `last_touch`.
- Promoting signal class from LinkedIn connection alone.

**Minimal passing example:**
```text
RI found: LinkedIn export ZIP.
RB recognized this as baseline enhancement input and ingested it.

Persistence status: persisted
Baseline delta: 14 new connections, 3 company changes, 1 role change, 0 conflicts.
Files written: system/baseline_index.json; system/deltas/linkedin_export_2026-05-28.md

Safeguards:
- Did not promote RC/LKI status from LinkedIn connection alone.
- Did not treat export presence as last_touch.

Next action: [ask_todd] Review 1 held conflict before RB updates canonical role/company.
```

---

### 4. `passive_ri_ingest`

**Trigger:** `passive_ri_ingest.run()` completes, or the daily brief loads
the passive ingest cache.

**Required sections:**
1. Candidates reviewed (count + sources)
2. Events proposed / recorded (with contact names)
3. Events blocked / skipped (with reasons)
4. Source availability
5. Pending confirmation actions

**Required fields:**
- `action_state` for every outcome: `proposed`, `blocked`, `skipped`, `recorded`, `unavailable`
- `grounding: system_detected` (passive signals from connected sources)
- `persistence_status` per event
- Source refs for proposed events

**Banned patterns:**
- Presenting proposed events as `RB recorded` before confirmation.
- Omitting blocked/skipped counts (the negative path must be explicit).
- Silent source failure (always name unavailable sources).

**Minimal passing example:**
```text
Passive RI Ingest:
- RB reviewed 3 passive signal candidates from relationship_signals and linkedin_messaging.
- RB proposed 1 review-first RI event:
  • last_touch_update — Bob Gibson (2026-05-23) · system_detected; confidence high
    Pending confirmation. Bundle: mb_ri_20260523_bob-gibson_001.
- RB skipped 1 duplicate: Bob Gibson last_touch_update already exists for 2026-05-23.
- RB blocked 1 candidate (signal_strength=0.32 below threshold 0.40): Unnamed contact.
- Source availability: relationship_signals OK; linkedin_messaging stale_source_limited.
```

---

### 5. `no_response_update`

**Trigger:** Todd reports silence, no reply, waiting on someone, stalled
recruiting/opportunity movement.

**Required sections:**
1. Observed signals
2. State changes
3. Inferences
4. Recommended actions
5. Persistence

**Required fields:**
- `grounding` per signal
- `confidence` per inference
- `disposition` per recommended action
- `persistence_status`

**Banned patterns:**
- Inferring rejection from silence alone.
- Identity or market-positioning narratives without evidence.
- Blending facts with interpretations in a single paragraph.
- `strategic advisor`, `timing says`, `lean into your`, etc.
- Recommending activity for its own sake without a named reason.

*(See `canonical_response_eval.py` `no_response_update` scenario for the
full deterministic gate.)*

---

### 6. `strategic_memory_record`

**Trigger:** Todd uses a detection phrase (`note this`, `remember this`,
`industry intelligence`, `this validates my thesis`, etc.) and
`strategic_memory.record()` is called.

**Required sections:**
1. Detected trigger
2. Recorded / updated signal (id, categories)
3. Storage location
4. Future-use requirements

**Required fields:**
- `action_state`: `RB recorded...` (new) or `RB updated...` (existing)
- `grounding: manual_user_provided`
- `persistence_status: persisted` (only if file was written; else `not_persisted`)
- `source_refs`: storage path

**Banned patterns:**
- `This should be remembered...` (non-action language)
- Claiming `RB recorded` without writing the store file.
- Omitting the future-use requirements block.

**Minimal passing example:**
```text
Industry intelligence detected. RB recorded the following durable signal.

Signal: Starbucks discontinued AI inventory tool across North America after operational issues.
What it proves: Enterprise restaurant AI must survive real-world operating conditions.
Strategic implication: Enterprise buyers will scrutinize AI reliability and ROI.

RB recorded strategic memory:
- ID: si_4a3f2b1c88d
- Categories: static_industry_assessment, strategic_company_watchlist, market_pattern
- Stored in: system/strategic_memory.json

Persistence status: persisted
Grounding: manual_user_provided

Future-use requirements: AI restaurant tech vendors, computer vision companies,
inventory platforms, enterprise rollout claims, LinkedIn post opportunities.
```

---

### 7. `draft_ready_action`

**Trigger:** Daily brief or relationship signals include a "Draft-Ready
Actions" section, or user asks to draft a message.

**Required sections:**
1. Draft (inside code block for easy copy)
2. Voice constraints applied
3. Grounding line (source refs)
4. Engagement boundary flag (if restricted)

**Required fields:**
- `action_state: RB drafted...` — never `RB sent...` unless API confirms delivery
- `grounding` and `source_refs` in grounding line
- `disposition`: typically `act_today` (send) or `ask_todd` (review first)

**Banned patterns:**
- `RB sent...` unless a send API confirmed delivery.
- Drafts that contradict known voice constraints.
- Missing source grounding line.

---

### 8. `meeting_prep`

**Trigger:** User asks for meeting prep, calls meeting_prep endpoint, or
a calendar event with a known attendee appears in the brief.

**Required sections:**
1. Meeting context (attendee(s), date/time, thread/opportunity)
2. What to know
3. What to ask
4. What not to say
5. Open loops / active threads
6. Draft follow-up path

**Required fields:**
- `grounding` per insight
- `freshness` on relationship data
- `disposition` for recommended prep actions
- `persistence_status` if any loop/thread was created

**Banned patterns:**
- Claiming prep is grounded in fresh source data when feeds are stale.
- Presenting historical relationship state as current without validation.

---

### 9. `loop_or_action_creation`

**Trigger:** A loop is proposed or written (open_loop, close_loop,
loopAdd, closeLoop API calls).

**Required sections:**
1. Loop action taken (opened / closed / proposed)
2. Evidence that grounded the action
3. Current loop state
4. Validation result

**Required fields:**
- `action_state`: `RB opened loop L-...` (written) or `RB proposed a loop...` (not written)
- `disposition`
- Post-validation confirmation: `getLoops` result confirming the write

**Banned patterns:**
- `RB opened a loop` before the write API confirms it.
- Closing a loop without post-validating with `getLoops`.
- Presenting a proposed loop as an open loop.

---

## Canonical response block (API / script shape)

When script or API outputs can provide a canonical response directly,
include a `canonical_response` block with this shape:

```json
{
  "scenario": "manual_ri_intake | passive_ri_ingest | strategic_memory_record | no_response_update | ...",
  "action_state": "recorded | updated | proposed | blocked | skipped | not_persisted | drafted",
  "summary": "RB <verb>... one sentence, action-state language.",
  "facts": [],
  "inferences": [],
  "persistence": {
    "status": "not_persisted | proposed_write_pending_confirmation | persisted",
    "bundle_id": null,
    "event_ids": [],
    "storage_path": null
  },
  "projection": {
    "applied": [],
    "pending": [],
    "blocked": []
  },
  "recommended_actions": [],
  "source_refs": [],
  "grounding": "system_detected | manual_user_provided | inferred | stale_source_limited",
  "freshness": "fresh | stale | missing | under_instrumented",
  "confidence": "high | medium | low"
}
```

This block is the machine-readable complement to the human-readable canonical
response text. API consumers should prefer the `canonical_response` block when
available. The Custom GPT must use the `action_state` and
`persistence.status` fields to determine which action-state verb to render —
it must not infer these from prose.

---

## Future user-defined response template switch

**This is not implemented in RB 9.5.** This section preserves the design path.

Future product direction: users should be able to define their own canonical
response templates for the daily brief and other Chief-of-Staff responses.

Planned examples:
- daily brief template
- relationship-signal review template
- meeting-prep template
- no-response / waiting-state template
- strategic-memory recorded template
- action-orchestration template

Planned settings shape:

```json
{
  "response_templates": {
    "mode": "system_default",
    "allow_user_defined": false,
    "templates_path": "system/response_templates/",
    "active": {
      "daily_brief": "default",
      "relationship_signal_review": "default",
      "meeting_prep": "default"
    }
  }
}
```

**Non-negotiable invariants across all templates (user-defined or not):**

User-defined templates may change order, tone, labels, or verbosity. They
must not weaken the proof contract. These invariants remain mandatory:

1. Source freshness must appear before quiet-source conclusions.
2. Action state must appear before interpretation.
3. Persisted vs proposed vs blocked must always be distinct.
4. `grounding`, `freshness`, `confidence`, and `source_refs` are required on
   high-signal items.
5. No completed-action language unless the system actually persisted or
   projected the change.
6. The truth table above governs all verb choices regardless of template.

When a future sprint implements the template engine:
- Add `system/response_templates/*.md`.
- Add schema validation for required fields/sections.
- Add a settings switch selecting `system_default` vs user-defined.
- Add evals proving custom templates still preserve action-state truth
  (extend `canonical_response_eval.py` with a `--template` mode).

---

## Grounding labels (canonical enum)

Use these exact strings. Do not paraphrase.

| Label | Meaning |
|---|---|
| `system_detected` | Signal came directly from a connected source within the freshness window |
| `manual_user_provided` | Sourced from operator-typed or operator-pasted content |
| `inferred` | RB synthesized this from rules (dormancy, suppression, reconciliation) |
| `stale_source_limited` | Would be `system_detected` but the underlying feed is past its threshold |

---

## Freshness states

| State | Meaning |
|---|---|
| `fresh` | Source was accessed within its threshold (typically 24h) |
| `stale` | Source was accessed but is past its freshness threshold |
| `missing` | Source could not be accessed (connection error, file absent) |
| `under_instrumented` | Source is not connected or not yet ingested |

---

## Disposition values

| Value | Meaning |
|---|---|
| `act_today` | Take this action now — time-sensitive or highest-leverage |
| `monitor` | Keep watching; no action required yet |
| `ask_todd` | RB needs Todd's input before acting |
| `ignore` | Low value, noise, or explicitly suppressed |

Every recommendation must resolve to exactly one of these.

---

## Enforcement

`canonical_response_eval.py` provides a deterministic shape/discipline gate for:
- `no_response_update` — required sections, grounding labels, dispositions, banned phrases
- `ri_intake_detected` — match status, action-state verbs, persistence_status, grounding
- `passive_ri_summary` — candidates/proposed/blocked counts, action-state language
- `daily_brief_top_contract` — resource verification before analysis, stale-source check
- `strategic_memory_record` — detected trigger, stored signal, persistence_status

Run the gate before every commit that touches response shape:

```bash
python3 system/scripts/canonical_response_eval.py --smoke
```

For scenario-specific evaluation:

```bash
python3 system/scripts/canonical_response_eval.py --scenario ri_intake_detected --text path/to/response.txt
python3 system/scripts/canonical_response_eval.py --scenario passive_ri_summary --text path/to/response.txt
python3 system/scripts/canonical_response_eval.py --scenario strategic_memory_record --text path/to/response.txt
python3 system/scripts/canonical_response_eval.py --scenario daily_brief_top_contract --text path/to/response.txt
```
