---
id: P-036
title: RI assessment doctrine and trust display hardening
script: |
  system/scripts/relationship_signals.py (produces ri_assessment blocks per signal)
  system/scripts/passive_ri_ingest.py (formalizes assessment contract on all events)
  system/scripts/daily_brief.py (proof rows distinguish all 7 statuses)
cache: |
  system/.cache/relationship_signals.json
  system/.cache/passive_ri_ingest.json
reads:
  - system/baseline_index.json
  - system/active_threads.yaml
  - system/inbox/
  - system/.cache/passive_ri_ingest.json
  - system/.cache/relationship_signals.json
writes:
  - (no direct canonical writes — proof and display layer only)
inputs:
  - name: ri_assessment
    description: A structured assessment block produced by any RI-detecting source processor.
    required: false
trigger: |
  Embedded in every signal or event produced by relationship_signals.py,
  passive_ri_ingest.py, linkedin_own_engagement.py, linkedin_session_reader.py,
  and any future source processor. Governs what daily_brief.py may say
  about relationship intelligence without a canonical write having occurred.
---

# P-036 — RI Assessment Doctrine and Trust Display Hardening

## Core product doctrine

Every meaningful input should be assessed for relationship intelligence
internally. Not every input should visibly produce RI or trust language.

RB behaves like a measured Chief of Staff:

- always assess relationship consequences in the background
- speak only when the RI changes judgment, confidence, or action
- never claim a mutation happened unless canonical state actually changed
- distinguish recorded vs proposed vs blocked vs irrelevant vs unavailable

The CoS behavior is **quiet judgment**:

> Assess everything. Display only what earns it.

---

## Inputs that must be assessed for RI

The following source types must produce a structured `ri_assessment` block
whenever they emit a signal or event. "Assessed" does not mean "displayed."

| Source | Processor |
|---|---|
| Email overlays | `email_overlay.py`, `relationship_signals.py` |
| Calendar overlays | `calendar_overlay.py`, `relationship_signals.py` |
| LinkedIn/social feed | `social_overlay.py`, `relationship_signals.py` |
| LinkedIn own-post engagement | `linkedin_own_engagement.py`, `linkedin_session_reader.py` |
| Messages and calls (when available) | `interaction_overlay.py`, `relationship_signals.py` |
| Meeting transcripts | `ri_preproc_transcript.py` |
| Uploaded docs and notes | `ri_preproc_manual.py` |
| Manual user prompts / conversation artifacts | `manual_relationship_intake.py`, `passive_ri_ingest.py` |
| Market/news/company signals that mention people, companies, opportunities, operators, or active threads | `market_signals.py` |

---

## RI assessment contract

Every `ri_assessment` block must carry the following fields. This is the
canonical schema; processors that produce partial blocks must use `null`
for fields they cannot populate, not omit them.

```json
{
  "status": "<see statuses below>",
  "source": "<source processor slug, e.g. relationship_signals>",
  "source_freshness": "<fresh | stale | unavailable>",
  "confidence": "<float 0.0–1.0>",
  "evidence": ["<string description of each evidence item>"],
  "mapped_contact_ids": ["<baseline contact_id>"],
  "mapped_thread_ids": ["<active_thread id>"],
  "proposed_mutation": {
    "command": "<mutations.py subcommand or null>",
    "payload": {}
  },
  "display_recommendation": "<show | suppress | suppress_unless_asked>",
  "reason": "<one-sentence explanation of status and display decision>"
}
```

### Field semantics

**`status`** — one of the seven canonical statuses (see below).

**`source`** — the processor that generated this assessment (slug string,
e.g. `"relationship_signals"`, `"passive_ri_ingest"`, `"linkedin_session_reader"`).

**`source_freshness`** — `"fresh"` if the source was fetched within its
defined threshold (see P-024 freshness table); `"stale"` if outside the
threshold but data is present; `"unavailable"` if the source could not be
read or the script is missing.

**`confidence`** — float 0.0–1.0. Stale sources must reduce confidence.
No stale source may produce a confidence above 0.55.

**`evidence`** — list of short strings, each describing one piece of
evidence that drove the assessment. May be empty for `irrelevant`.

**`mapped_contact_ids`** — baseline contact IDs that the signal mentions
or was matched to. Empty list if no match.

**`mapped_thread_ids`** — active-thread IDs the signal connects to. Empty
list if no thread match.

**`proposed_mutation`** — the mutations.py command and payload RB would
submit on behalf of this signal, if the status is `proposed`. Null for all
other statuses.

**`display_recommendation`** — one of:
- `"show"` — include trust/RI language in the user-facing output
- `"suppress"` — do not surface; record in proof only
- `"suppress_unless_asked"` — surface only if user asks "what did RB
  record?", "what was blocked?", or similar audit questions

**`reason`** — one sentence explaining why this status and display decision
was reached.

---

## Assessment statuses

| Status | Meaning |
|---|---|
| `recorded` | A canonical RI event or baseline mutation was actually written. |
| `proposed` | Meaningful RI found. Mutation bundle written to review queue; awaits confirmation. |
| `duplicate` | Signal already captured; no new mutation needed or written. |
| `blocked` | Meaningful signal found but evidence, confidence, source freshness, or entity match was insufficient to write or propose. |
| `irrelevant` | Assessed; no relationship consequence found. |
| `unavailable` | Source script is missing, source file is absent, or an exception prevented assessment. Cannot assess honestly. |

### Status rules

- `recorded` requires proof: a mutation that completed without error AND the
  canonical file changed. If the write path was dry-run, the status is
  `proposed`, not `recorded`.
- `proposed` requires a written review record (`passive_ri_confirm.json` or
  equivalent). Do not use `proposed` for ad hoc notes.
- `duplicate` requires an explicit dedupe check against the existing event
  store or cache. Do not assume duplicate without checking.
- `blocked` must carry a `reason` that names the specific gate that failed
  (e.g. `"confidence=0.31 below threshold 0.40"`,
  `"source_freshness=stale; email last fetched 36h ago"`,
  `"entity_match=none; sender not in baseline"`).
- `irrelevant` is the default when a source was successfully scanned and
  nothing relationship-relevant was found. It is not an error.
- `unavailable` means RB cannot assess and must say so. It must not produce
  a confident RI claim when the underlying source is unavailable.

---

## Trust display rules

### When to display trust/RI language

Display when:

1. RB **recorded** or updated relationship state (canonical write happened).
2. RB is **proposing** a mutation (review-first write pending confirmation).
3. RB **blocked** a mutation because evidence was weak, stale, conflicting,
   or the source was unavailable — and the block changes the confidence or
   action that should be taken.
4. A missing or stale source **affects confidence** on a question that
   matters to the user's current decision.
5. A relationship opportunity, risk, silence, reactivation, intro path, or
   active-thread movement **materially changed** since the last brief.
6. The user asks a provenance question: "why", "what changed", "what was
   recorded", "what was blocked", "what am I missing", "did you capture that".

### When NOT to display trust/RI language

Suppress when:

1. The input has **no relationship consequence** (status = `irrelevant`).
2. The assessment is low-value noise that does not change any recommendation.
3. The user asked a simple operational or factual question and RI does not
   affect the answer.
4. RB merely checked a source and found nothing meaningful.
5. Exposing the internal machinery would **reduce** clarity for the user.
6. The signal is a duplicate of something already surfaced in this session.

### Display decision matrix

| Status | display_recommendation | When to override |
|---|---|---|
| `recorded` | `show` | Never suppress a confirmed write |
| `proposed` | `show` | Never suppress a pending mutation |
| `blocked` | `show` if decision-relevant; `suppress_unless_asked` otherwise | Show when block changes confidence or action |
| `duplicate` | `suppress` | Show only if user explicitly asks |
| `irrelevant` | `suppress` | Show only if user asks "what did you check?" |
| `unavailable` | `show` if source affects confidence; `suppress` if source is not in scope | Suppress low-stakes missing sources |

---

## Canonical response language

### Approved phrasings by status

**recorded:**
> "I recorded [X] — [contact name]'s last_touch is now [date]."
> "I updated [contact name]'s relationship state: [change summary]."

**proposed:**
> "I found a possible relationship signal — [description]. I would propose recording [mutation]. Confirm to apply."
> "I'd like to update [contact name]'s [field] based on [evidence]. Should I proceed?"

**blocked:**
> "I found a signal worth noting but did not record it because [reason]."
> "I assessed [source/contact] and found a possible [signal type], but confidence was [value] — below the threshold to write. I'll surface it again if corroborating evidence arrives."
> "The [source] is stale ([age]). I cannot make a confident RI assessment from it without a fresh fetch."

**duplicate:**
> (Usually suppress. If surfaced:) "This is already captured — [event/contact] was recorded on [date]."

**irrelevant:**
> (Usually suppress. If surfaced:) "I checked [source/contact] and found nothing relationship-relevant."

**unavailable:**
> "I cannot assess [source] right now — [script/file] is unavailable. Confidence for [topic] is lower as a result."
> "I would check [source] for this, but it hasn't been fetched recently. My answer may be incomplete."

### Banned phrasings

RB must NOT produce these without a confirmed canonical write:

- "trust updated"
- "relationship state updated"
- "recorded" (as a standalone claim)
- "I've updated your notes on [person]"
- "I captured that"

Using any banned phrase when the actual status is `proposed`, `blocked`,
`duplicate`, or `irrelevant` is a Category 1 canonical response defect.

---

## Daily brief proof rows

Each proof row in the daily brief already carries `scan_status` and
`actions_taken`. With P-036, proof rows must be able to distinguish all
seven statuses without making the brief noisy.

### Proof row status mapping

| Event | Proof row label | Notes |
|---|---|---|
| Source scanned, nothing found | `irrelevant_scan` | Brief does not surface |
| Source unavailable | `source_unavailable` | Brief surfaces as staleness warning |
| Source stale | `source_stale` | Brief surfaces confidence caveat |
| Signal found, mutation written | `recorded` | Brief shows proof |
| Signal found, review pending | `proposed` | Brief shows pending item |
| Signal found, gated | `blocked:<reason_slug>` | Brief shows if decision-relevant |
| Signal already captured | `duplicate_skipped` | Brief does not surface |

### Principle: always assess, don't always display

The brief must not become an audit log of every scan. The rule is:

> Show proof of what changed. Show warnings about what couldn't be checked.
> Suppress everything else.

---

## Source freshness and confidence gate

No source with `source_freshness = "stale"` may produce a confidence above
0.55. No source with `source_freshness = "unavailable"` may produce any
RI claim.

This gate is enforced at the signal level, not the display level. A stale
source must emit a reduced-confidence assessment regardless of how strong
the raw signal text looks.

Freshness thresholds (from P-024):

| Source | Threshold |
|---|---|
| email | 24h |
| calendar | 24h |
| messages | 12h |
| calls | 12h |
| social | 48h |
| social_outbound | 48h |
| linkedin_own_engagement | 48h |
| market_signals | 48h |

---

## Integration points

| Script | Change |
|---|---|
| `relationship_signals.py` | Each signal carries `ri_assessment` block per P-036 schema |
| `passive_ri_ingest.py` | Each event carries `ri_assessment` block; `display_recommendation` drives proof row label |
| `daily_brief.py` | Proof rows read `display_recommendation` to decide visibility |
| `linkedin_own_engagement.py` | Engagement rows carry `ri_assessment` with `source_freshness` set from `fetched_at` |
| `linkedin_session_reader.py` | Same as above |
| `market_signals.py` | People/company hits produce `ri_assessment` with status `proposed` or `irrelevant` |
| `refresh_sources.py` / `task_delivery_check.py` | Source-freshness gates feed `source_freshness` field |

---

## Related protocols

- **P-009** — Mutations: the only path to `status=recorded`.
- **P-021** — RI event sourcing: event shape that `proposed` assessments
  pre-populate before confirmation.
- **P-024** — Relationship signals: source freshness thresholds and signal
  classification. P-036 extends P-024 with the `ri_assessment` contract.
- **P-016** — Runtime validation: smokes should verify `ri_assessment` blocks
  are present and structurally valid on signal output.

---

## Acceptance criteria for P-036

- [ ] `ri_assessment` block present on every signal from `relationship_signals.py`
- [ ] `ri_assessment` block present on every event from `passive_ri_ingest.py`
- [ ] All 7 statuses produced by at least one code path
- [ ] No stale source produces confidence > 0.55
- [ ] No unavailable source produces an RI claim
- [ ] Daily brief proof rows distinguish all 7 statuses without becoming noisy
- [ ] Banned phrasings absent from all response templates
- [ ] 8+ representative test traces exist in `system/test_traces/`
- [ ] `protocols/index.json` includes P-036 entry
