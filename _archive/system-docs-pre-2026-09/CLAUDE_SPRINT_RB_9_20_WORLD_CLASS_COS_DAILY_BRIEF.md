# Claude Sprint Brief — RB 9.20 World-Class CoS Daily Brief

**Prepared:** 2026-05-28  
**Prepared by:** Codex  
**Audience:** Claude / next RB implementation sprint  
**Sprint posture:** CoS operating-system correction. Convert the Daily Brief from helpful assistant reporting into hard-edged strategic operating guidance with execution closure.

---

## Why This Sprint Exists

RB's Daily Brief has become more structured, but it still drifts toward:

- useful summarization
- agreeable framing
- supportive reinforcement
- additive recommendations
- "interesting context" rather than operational judgment

That is not enough.

The Daily Brief is the trust surface for RB as a Chief of Staff. It must protect
the user's focus, time, positioning, and execution quality. It should sometimes
be uncomfortable. It should say what is not working, what is stale, what is
being avoided, what is failing to convert, and what should be deprioritized.

The sprint principle:

> A Daily Brief is not a friendly recap. It is a strategic operating memo that should make the user more focused, more grounded, and more operationally directed.

This sprint implements `RB-DEFECT-008`, carries forward live validation /
Daily Brief integration for `RB-DEFECT-007`, and adds the live-interaction
CoS orchestration lane for `RB-DEFECT-009`.

---

## Non-Negotiable CoS Invariant

RB must optimize for strategic operational value over pleasant usefulness.

The brief should be direct, evidence-bound, and action-oriented. It may be warm,
but warmth must not dilute truth.

The brief must not:

- flatter the user
- over-reinforce their preferred narrative
- turn weak evidence into momentum
- treat activity as progress
- bury stale-source caveats
- offer recommendations without closure options
- present macro context as generic news
- avoid hard prioritization tradeoffs

The brief must:

- name the highest-leverage actions
- identify opportunity cost
- surface negative space
- challenge weak assumptions
- distinguish fresh vs stale vs inferred evidence
- connect world/macro events to the user's operating terrain
- turn recommendations into loops/tasks/monitors/reminders/pipeline options

---

## Current Starting Points

Primary files:

- `system/scripts/daily_brief.py`
- `system/scripts/morning_pipeline.py`
- `system/scripts/task_delivery_check.py`
- `system/scripts/source_health_report.py`
- `system/scripts/relationship_signals.py`
- `system/scripts/market_signals.py`
- `system/scripts/market_source_feeds.py`
- `system/scripts/strategic_memory.py`
- `system/scripts/action_drafts.py`
- `system/api/server.py`
- `system/CANONICAL_RESPONSE_CONTRACT.md`
- `system/api/custom_gpt_instructions_8k.md`
- `system/api/custom_gpt_prompt.md`

Relevant related systems:

- loops: `system/loop_ledger.md`, `/loops`, `/loops/close`, `/smart_loops/proposals`
- source health: `system/.cache/source_health.json`
- RI events: `system/ri_events/*.jsonl`
- passive intelligence: `system/scripts/passive_intelligence.py`
- LinkedIn delta intelligence: `system/.cache/linkedin_ingest_latest.json`
- LinkedIn ingester: `system/scripts/linkedin_ingest.py`
- LinkedIn ingestion defect: `defects/RB-DEFECT-007_linkedin-ingestion-static-import-instead-of-longitudinal-ri-mutation_2026-05-28.md`
- Live CoS orchestration defect: `defects/RB-DEFECT-009_live-cos-layer-conversational-assistant-instead-of-autonomous-strategic-operator_2026-05-28.md`
- LinkedIn fresh-signal bridge: `system/scripts/linkedin_freshness_bridge.py`
- strategic memory: `system/scripts/strategic_memory.py`, `system/strategic_memory.json`
- RI event stream: `system/scripts/ri_intake.py`, `system/scripts/ri_events.py`
- published briefs: `system/published/daily/<date>/`

Do not rewrite the whole Daily Brief. Add an unavoidable CoS judgment layer and
tests around it.

---

## Reproduction Fixtures

Claude Code should add these as deterministic fixtures or adapt them into
`canonical_response_eval.py` tests.

### Bad Brief Excerpt

This kind of response must fail:

```text
You have good momentum today. There are lots of promising signals across your
network, and the best move is to keep leaning into the relationships where
energy is already showing up.

Operators are still facing margin pressure, so restaurant technology buyers
will continue to value ROI. Here are some helpful suggestions:

- Follow up with a few high-value contacts.
- Stay consistent with your thought leadership.
- Keep nurturing the opportunities already in motion.
```

Why this fails:

- no source freshness proof
- no hard truth
- no negative-space analysis
- macro commentary is generic
- recommendations have no execution closure
- agreeable language substitutes for strategic judgment

### Target Brief Excerpt

This kind of response should pass:

```text
Resource verification:
- Email refreshed at 05:01; LinkedIn delta cache fresh from 2026-05-28.
- Direct messages unavailable; RB cannot prove the day is quiet on SMS/iMessage.

CoS judgment:
- Hard truth: thought leadership visibility is not yet converting into enough
  named strategic conversations. Evidence: 4 content signals, 0 new strategic
  conversation loops. Disposition: act_today.
- Focus leak: RB architecture work is generating product clarity but is at risk
  of crowding out immediate income-generating execution. Evidence:
  3 architecture loops active, 0 monetization loop closures this week.

What is not happening:
- No recruiter follow-up recorded after the expected response window for
  Opportunity X. This is not rejection, but it is now a monitored silence.

Macro-to-operator synthesis:
- Oil volatility -> freight and distribution pressure -> tighter restaurant
  margin tolerance -> longer SaaS experimentation cycles. Implication:
  vendor conversations need ROI proof, not AI novelty.

Execution closure:
- [open_loop] Follow up with X by Friday.
- [monitor_relationship] Watch Y through next Tuesday.
- [ignore] Suppress repeated recommendation Z until new evidence appears.
```

---

## Required Deliverables

### D1 — CoS Judgment Layer

Add a deterministic Daily Brief layer that produces a `cos_judgment` block.

Possible implementation:

- new helper functions in `daily_brief.py`, or
- new module `system/scripts/cos_judgment.py` consumed by `daily_brief.py`

Required shape:

```json
{
  "hard_truths": [],
  "negative_space": [],
  "focus_leaks": [],
  "unsupported_assumptions": [],
  "opportunity_costs": [],
  "prioritization_tradeoffs": [],
  "deprioritize_or_ignore": [],
  "execution_pressure": [],
  "confidence": "high|medium|low",
  "source_refs": []
}
```

Rules:

- Every item must include `claim`, `why_it_matters`, `evidence`, `confidence`,
  `grounding`, and `recommended_disposition`.
- Do not fabricate hard truths. If evidence is insufficient, say the hard-truth
  layer is under-instrumented and name the missing source.
- Hard truths must be evidence-bound. Do not manufacture criticism to sound
  tough. No performative harshness, no invented weakness, no unsupported
  psychological inference.
- If evidence is missing, the correct hard truth is:
  "RB is under-instrumented here; this conclusion cannot be trusted yet."
- Hard-truth language must be direct but not theatrical.

Examples of acceptable CoS judgment:

- "Thought leadership visibility is not yet converting into enough named strategic conversations."
- "This opportunity is consuming attention without producing movement."
- "The current evidence does not support treating this contact as buyer-intent."
- "RB architecture work is at risk of crowding out immediate income-generating execution."

### D2 — Negative-Space Intelligence

Add a Daily Brief section named `What Is Not Happening`.

It should detect:

- no recruiter follow-up after expected window
- no response after sent follow-up
- relationships cooling
- active threads without next action
- content/social engagement not converting into conversations
- opportunity with activity but no state movement
- stale loops or repeated recommendations
- strategic silence from expected sources

Inputs:

- `loop_ledger.md`
- sent-followup state in email overlays
- `relationship_signals`
- `active_threads.yaml`
- `ri_events`
- LinkedIn/social caches where available

Output:

- `what_is_not_happening`: list of items with `expected_signal`,
  `observed_absence`, `why_it_matters`, `confidence`, `next_check`,
  and `recommended_disposition`.

### D2a — LinkedIn Delta Intelligence In Daily Brief

Treat `RB-DEFECT-007` as part of this sprint's operating path, not a separate
ingestion-only fix.

The second LinkedIn export ingestion defect was:

> LinkedIn ingestion behaved like a static contact import instead of a
> longitudinal relationship-intelligence mutation event.

The ingester now emits `delta_intelligence` and writes
`system/.cache/linkedin_ingest_latest.json`. This sprint must verify that the
Daily Brief consumes that output and renders it through the CoS lens.

Required behavior:

- If a recent LinkedIn ingest exists, the Daily Brief must include a
  `LinkedIn Relationship Delta` / `Professional Movement` section.
- It must not say "file processed successfully" or merely report records parsed.
- It must surface:
  - baseline comparison
  - title/company/professional movement
  - promotions
  - strategic relationship value changes
  - newly relevant contacts
  - recruiter movement
  - dormant relationship reactivation candidates
  - "Who Matters Now" changes
  - outreach/congratulations/reconnect queue
  - graph mutation / persistence verification
- LinkedIn deltas must feed `cos_judgment`, `what_is_not_happening`, and
  `execution_options` when they create a concrete next action.
- Guardrails remain:
  - no `last_touch` mutation from LinkedIn export presence
  - no trust-score or relationship-tier promotion from LinkedIn edge alone
  - operator-confirmed conflicts remain held for review

Acceptance:

- `daily_brief.py` reads `system/.cache/linkedin_ingest_latest.json` when fresh.
- The rendered brief has at least one hard recommendation or ignore/suppress
  decision when LinkedIn delta items exist.
- Stale LinkedIn delta cache is labelled stale and does not drive current-state
  claims.

### D2b — Live LinkedIn Engagement CoS Orchestration

Treat `RB-DEFECT-009` as the live-interaction counterpart to the Daily Brief
CoS correction.

The defect:

> RB can reason well about a LinkedIn engagement and draft a good response, but
> it fails to mutate relationship intelligence, synthesize longitudinal thesis
> convergence, link cross-thread signals, and proactively recommend strategic
> actions.

Required behavior for LinkedIn engagement / public comment / response-shaping
sessions:

1. Detect relationship signal
   - matched contact
   - public alignment / support / amplification
   - thesis affinity
   - recurring engagement pattern
   - operator-pragmatist or buyer-authority signal

2. Produce review-first RI mutation proposals
   - `supports_<thesis_slug> = true`
   - `public_alignment_signal = true`
   - `potential_content_amplifier = true`
   - affinity strength / confidence
   - recommended tag or card/baseline note
   - no automatic RC/LKI/trust promotion from one public engagement

3. Update or propose strategic memory mutation
   - narrative lane reinforced
   - market validation signal
   - source refs
   - thesis alignment count
   - confidence / freshness

4. Link cross-thread intelligence
   - prior posts
   - prior LinkedIn/comment signals
   - strategic memory
   - market/news signals
   - relationship history
   - macro restaurant/operator context

5. Produce autonomous strategic recommendation
   - content follow-up
   - relationship follow-up
   - monitor amplifier relationship
   - add to narrative convergence tracker
   - open outreach loop
   - ignore/suppress if low-value

Required structured payload:

```json
{
  "live_cos_orchestration": {
    "relationship_signal": {},
    "ri_mutation_proposals": [],
    "narrative_convergence": {},
    "cross_thread_links": [],
    "strategic_recommendations": [],
    "execution_options": [],
    "persistence_status": "not_persisted|proposed_write_pending_confirmation|persisted"
  }
}
```

Acceptance:

- A LinkedIn engagement around a known strategic thesis cannot end with only a
  good draft/response.
- The response must state what RB detected, what it proposes to remember or
  mutate, how this connects to prior thesis signals, and what action should
  happen next.
- If the system cannot safely mutate, it must still propose a review-first
  mutation bundle or explain exactly what evidence is missing.
- The live CoS output must be structured, not only prose.

### D3 — Macro-To-Operator Synthesis

Replace generic macro commentary with a causal-chain synthesis layer.

Required shape:

```json
{
  "macro_chain": [
    {
      "trigger": "Iran instability / oil volatility / tariffs / labor market / rates / VC contraction",
      "mechanism": "freight inflation / food cost pressure / budget tightening",
      "restaurant_operator_implication": "...",
      "restaurant_tech_implication": "...",
      "vendor_or_job_market_implication": "...",
      "user_implication": "...",
      "confidence": "high|medium|low",
      "freshness": "fresh|stale|inferred|source_unavailable",
      "source_refs": []
    }
  ]
}
```

Acceptance:

- No macro item may appear unless it maps to at least one of:
  restaurant operations, restaurant tech spend, vendor survivability,
  enterprise deal cycles, hiring trends, platform consolidation, customer
  buying behavior, relationship timing, or opportunity priority.
- If macro data is stale or unavailable, say so before interpretation.

### D4 — Closed-Loop Execution Options

Every recommendation must resolve into a closure option:

- `create_task`
- `open_loop`
- `monitor_relationship`
- `schedule_reminder`
- `escalate_priority`
- `add_to_opportunity_pipeline`
- `open_outreach_loop`
- `ignore`

Add a machine-readable `execution_options` block to the brief:

```json
{
  "execution_options": [
    {
      "recommendation_id": "...",
      "action": "open_loop",
      "target": "person/company/thread",
      "reason": "...",
      "default_priority": "high|medium|low",
      "requires_confirmation": true,
      "endpoint": "POST /loops or /smart_loops/apply or draft action endpoint"
    }
  ]
}
```

The rendered brief must end with concrete choices, not a vague "let me know."

Good end state:

```text
Execution closure:
- [open_loop] Follow up with X by Friday
- [monitor_relationship] Watch Y for response through next Tuesday
- [create_task] Draft outreach to Z
- [ignore] Suppress repeated recommendation A until new evidence appears
```

### D4a — Structured Daily Brief JSON Contract

The new CoS sections must appear in the structured Daily Brief payload, not only
in rendered markdown.

Required top-level or canonical-brief fields:

```json
{
  "canonical_brief": {
    "sections": {
      "resource_verification_and_freshness_status": {},
      "cos_judgment": {},
      "what_is_not_happening": [],
      "macro_to_operator_synthesis": {},
      "linkedin_relationship_delta": {},
      "recommended_actions": [],
      "execution_options": [],
      "what_to_ignore": [],
      "delivery_completion_verification": {}
    },
    "section_order": [
      "resource_verification_and_freshness_status",
      "cos_judgment",
      "what_is_not_happening",
      "macro_to_operator_synthesis",
      "relationship_operational_signal_review",
      "linkedin_relationship_delta",
      "recommended_actions",
      "execution_closure",
      "what_to_ignore",
      "delivery_completion_verification"
    ]
  }
}
```

Acceptance:

- API consumers can render the CoS sections without parsing markdown.
- The Custom GPT should prefer the structured fields over free-form prose.
- Tests must inspect the structured payload and the rendered text.

### D5 — Source Freshness And Stale-Assumption Validation

Strengthen source freshness into a visible trust contract.

Daily Brief must report:

- fresh scan completed
- using cached intelligence older than X days
- no fresh signal detected
- source unavailable
- refresh failed
- historical memory only
- inferred, not verified
- rumor/vendor-positioning if applicable

Add intelligence labels:

- `fresh`
- `stale`
- `inferred`
- `verified`
- `rumor`
- `historical_memory`
- `source_unavailable`
- `refresh_failed`

Acceptance:

- The brief must not say or imply quiet if Tier-1 sources are stale.
- Stale strategic memory must not be rendered as current fact without a label.
- A refresh attempt/failure must be visible in the brief or completion artifact.

### D6 — Daily Brief Completion / Failure Notification Artifact

Make Daily Brief orchestration feel operational, not manually queried.

Add or strengthen a completion artifact, ideally under:

- `system/published/daily/<date>/brief_status.json`, or
- `system/.cache/daily_brief_status.json`

Required fields:

```json
{
  "date": "YYYY-MM-DD",
  "started_at": "...",
  "completed_at": "...",
  "status": "completed|completed_with_warnings|failed",
  "source_refreshes": [],
  "failed_integrations": [],
  "stale_sources": [],
  "critical_failures": [],
  "published_paths": [],
  "notification_line": "RB Daily Brief completed at 5:02 AM..."
}
```

The API should expose this through an existing brief health endpoint or a new
read endpoint only if needed. Prefer extending `getBriefHealth` if it already
fits the surface.

### D7 — Canonical Response Contract Update

Update `system/CANONICAL_RESPONSE_CONTRACT.md` so Daily Brief canonicality
requires:

- Resource Verification & Freshness Status
- CoS Judgment / Hard Truths
- What Is Not Happening
- Macro-To-Operator Synthesis
- Relationship / Operational Signal Review
- Recommended Actions
- Execution Closure
- What To Ignore
- Delivery / Completion Verification

Add banned patterns:

- generic encouragement without evidence
- macro commentary without operating implication
- recommendation without closure option
- "quiet" without source freshness proof
- positive framing that ignores negative-space signals

### D8 — Custom GPT Instruction Update

Update:

- `system/api/custom_gpt_instructions_8k.md`
- `system/api/custom_gpt_prompt.md`

The instruction should say:

> The Daily Brief must behave like a Chief of Staff, not a helpful assistant. It must surface hard truths, negative space, stale-source limits, opportunity cost, and execution closure. Do not soften strategic friction into encouragement.

Keep language user-generic. Avoid hardcoding a specific user's name.

---

## Implementation Order

Implement in this order. Do not start with prompt or tone edits.

1. Source freshness / stale gates
2. Negative-space analysis
3. CoS judgment
4. Execution closure
5. Macro-to-operator synthesis
6. LinkedIn delta integration
7. Completion/failure artifact
8. Canonical contract and Custom GPT instruction updates
9. API smoke / canonical evaluator updates

Reason: freshness and negative-space proof are the foundation. Without them,
hard truths become vibes and macro synthesis becomes generic commentary.

---

## Required Tests

Add or update deterministic tests. Prefer focused tests over huge fixtures.

Suggested files:

- `system/tests/test_daily_brief_cos_judgment.py`
- `system/tests/test_daily_brief_negative_space.py`
- `system/tests/test_daily_brief_execution_closure.py`
- `system/tests/test_daily_brief_macro_synthesis.py`
- `system/tests/test_daily_brief_completion_status.py`
- `system/tests/test_daily_brief_linkedin_delta_intelligence.py`
- `system/tests/test_live_cos_orchestration.py`
- extend `system/scripts/canonical_response_eval.py`

### Test T1 — Hard Truth Required

Fixture: active threads, loops, or signals that show effort without conversion.

Assert:

- `cos_judgment.hard_truths` exists
- rendered brief includes direct strategic judgment
- no banned "encouraging" replacement language

### Test T2 — Negative Space Required

Fixture: sent follow-up overdue, no response, or active opportunity with no state
movement.

Assert:

- `what_is_not_happening` includes the absence
- disposition is `act_today`, `monitor`, `ask_user`, or `ignore`
- silence is not interpreted as rejection unless evidence supports it

### Test T3 — Macro Synthesis Must Map To Operator Implication

Fixture: macro item such as oil volatility or tariffs.

Assert:

- macro trigger maps through mechanism to restaurant/operator/vendor/user
  implication
- generic "operators face margin pressure" alone fails

### Test T4 — Recommendations Require Execution Options

Fixture: any recommendation.

Assert:

- every recommendation has an execution option
- closure options are from the allowed enum
- write-like options require confirmation

### Test T5 — Stale Source Blocks Quiet Claim

Fixture: stale email/social/LinkedIn/direct-comms source.

Assert:

- brief does not claim quiet
- stale source is named
- refresh status appears

### Test T6 — Completion Artifact

Run brief build/publish path.

Assert:

- completion status artifact exists
- includes completed/failed integrations/stale sources
- includes notification line

### Test T7 — Canonical Evaluator Rejects Helpful-Assistant Drift

Extend `canonical_response_eval.py` with a `daily_brief_cos_operator` scenario.

Failing fixture:

```text
You have a lot of promising momentum today. Keep leaning into your network.
Here are a few helpful recommendations...
```

Passing fixture must include:

- freshness proof
- hard truth
- negative space
- macro-to-operator implication
- execution closure

Explicit banned phrases for this scenario:

- "You have good momentum"
- "Keep leaning into"
- "Lots of promising activity"
- "Here are some helpful suggestions"
- "Stay consistent"
- "Operators face pressure" unless followed by a concrete causal mechanism and
  operator/vendor/user implication
- "Margin pressure exists" unless mapped to a specific action or priority
- "Continue nurturing" without a named next action and closure option

### Test T8 — LinkedIn Second-Ingestion Delta Reaches Daily Brief

Fixture: `system/.cache/linkedin_ingest_latest.json` containing a recent
`delta_intelligence` payload with:

- one promotion
- one company/title move
- one newly relevant recruiter or buyer
- one suggested outreach queue item

Assert:

- Daily Brief includes LinkedIn Relationship Delta / Professional Movement
- output includes baseline comparison or delta metrics
- output includes strategic relationship changes / Who Matters Now
- output includes execution option for outreach, monitor, ignore, or reconcile
- output does not contain "file processed successfully" or generic contact-import language
- stale fixture is labelled stale and does not drive current-state claims

### Test T9 — Live LinkedIn Engagement Mutates Strategic Operating State

Fixture: LinkedIn public engagement from a known contact such as Chad Horn
aligning with the user's operational AI realism / restaurant ROI thesis.

Seed strategic memory with related signals:

- Starbucks AI inventory failure
- restaurant AI hype skepticism
- Friday-night survivability
- restaurants buy risk reduction
- prior user post or LinkedIn discussion

Assert:

- `live_cos_orchestration.relationship_signal` exists
- matched contact receives review-first RI mutation proposals
- narrative convergence includes at least 2 prior related signals
- output includes thesis alignment / public amplifier classification
- strategic recommendations include at least one concrete next action
- execution option exists and requires confirmation if it mutates state
- response does not stop at draft generation

Failing pattern:

```text
This is a strong comment. Here is a polished LinkedIn reply...
```

Passing pattern must include:

- signal detected
- proposed RI mutation
- narrative convergence
- cross-thread links
- strategic recommendation
- persistence status

---

## Implementation Guidance

### Keep The Layer Deterministic

This sprint should not depend on an LLM deciding to be tougher. Encode the
Daily Brief contract in data structures, rendering order, and tests.

### Do Not Invent Hard Truths

Hard truths require evidence. When evidence is missing, the hard truth is:

> RB is under-instrumented here; this conclusion cannot be trusted yet.

### Prefer Review-First Execution

Closed-loop execution options should prepare tasks/loops/drafts for
confirmation. Do not auto-send, auto-close, or auto-mutate without the existing
confirmed-write gates.

### Keep Tone Plain

Use language like:

- "This is not converting yet."
- "This is stale."
- "This should not be today's priority."
- "The evidence does not support that assumption."
- "This relationship is cooling."

Avoid drama, scolding, or motivational language.

---

## Definition Of Done

The sprint is complete when:

1. `daily_brief.py` or its helper modules emit `cos_judgment`,
   `what_is_not_happening`, `macro_chain`, and `execution_options`.
2. Rendered Daily Brief includes the required CoS sections in canonical order.
3. Source freshness and stale assumptions are visible before interpretation.
4. Every recommendation has a closure option.
5. Completion/failure notification artifact is written by the brief/publish path.
6. Custom GPT instructions and canonical contract are updated.
7. Tests cover hard truths, negative space, macro synthesis, execution closure,
   stale-source gating, LinkedIn delta intelligence, live CoS orchestration,
   and completion notification.
8. API smoke tests still pass.

Suggested verification commands:

```bash
python3 -m pytest system/tests/test_daily_brief_cos_judgment.py system/tests/test_daily_brief_negative_space.py system/tests/test_daily_brief_execution_closure.py system/tests/test_daily_brief_macro_synthesis.py system/tests/test_daily_brief_completion_status.py -q
python3 -m pytest system/tests/test_linkedin_ingest_delta_intelligence.py system/tests/test_daily_brief_linkedin_delta_intelligence.py -q
python3 -m pytest system/tests/test_live_cos_orchestration.py -q
python3 system/scripts/canonical_response_eval.py --scenario daily_brief_cos_operator --text path/to/rendered_brief.txt
python3 system/scripts/api_smoke_test.py
python3 -m py_compile system/scripts/daily_brief.py system/scripts/morning_pipeline.py system/api/server.py
```

---

## Out Of Scope

- New LLM prompt-only solution with no deterministic tests
- Auto-sending messages
- Auto-promoting relationship tiers from weak evidence
- Building a full task manager
- Replacing every Daily Brief section
- Adding broad web/news scraping without source governance

---

## Product Bar

After reading the Daily Brief, the user should feel:

- more focused
- more aware
- more strategically grounded
- more operationally directed

Not merely:

- informed
- encouraged
- pleasantly summarized

The brief should earn trust by being useful enough to occasionally be
uncomfortable.
