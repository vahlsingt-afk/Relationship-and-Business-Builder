# RB-DEFECT-046: Universal Intelligence Processing Framework (UIPF)

**Date filed:** 2026-06-15
**Filed by:** Todd
**Status:** Filed — not yet triaged/scoped. Recorded verbatim for processing in a
future sprint.
**Priority:** Critical
**Category:** Core Architecture / Intelligence Engine / Chief of Staff Framework

## Relationship to RB-DEFECT-041 / 044 / 045

This is the foundational/upstream counterpart to the briefing-architecture work
in RB-DEFECT-044 and RB-DEFECT-045, and to the LinkedIn-specific ingestion work
in RB-DEFECT-041:

- RB-DEFECT-044/045 govern how the Intelligence Brief / Daily Brief **render**
  what RB knows (Collection Report, Layer 2/3 structure, canonical sections).
- RB-DEFECT-041 governs **one specific input type** (LinkedIn exports) and its
  delta/relationship-action pipeline.
- RB-DEFECT-046 (this defect) governs the **intake → analyze → enrich → learn →
  assess → act** pipeline that should apply uniformly to *every* input type
  (email, calendar, articles, screenshots, LinkedIn exports, transcripts, user
  queries) before any of 041/044/045's output logic runs. A persisted "Strategic
  Narrative" record (new in this defect) is what 044/045's brief sections should
  ultimately render.

Triage should sequence this defect's Stage-2/3 work (research-before-comment,
persisted narratives) *ahead of or alongside* 044/045's remaining slices, since
044/045 define the rendering contract for output this defect's pipeline produces.

---

## Executive Summary (verbatim from Todd)

This is bigger than a defect. This is an architectural enhancement request that
should become one of the foundational design principles of RB.

### Architectural Enhancement Request: Universal Intelligence Processing Framework (UIPF)

Relationship Bridge currently processes different types of information using
different workflows.

Examples:

- Relationship information is often enriched and stored.
- Market intelligence is frequently summarized and discarded.
- Emails are read.
- Calendar events are displayed.
- News is summarized.
- LinkedIn exports are analyzed separately.

This creates inconsistent learning behavior and prevents the system from
developing institutional memory.

The proposed solution is a Universal Intelligence Processing Framework (UIPF)
that governs how ALL inputs are handled regardless of source.

The objective is simple:

> Every intelligence-bearing input should leave Relationship Bridge smarter than
> it was before the input arrived.

### Core Design Principle

RB should not ask: "What type of system received this information?"

RB should ask: "Did we learn something worth remembering?"

If yes: mutate the knowledge base. If no: respond and move on.

### Universal Processing Framework

```
Input Received
  -> Stage 0: Intake & Classification
       -> Is this intelligence-bearing?
            No  -> Standard CoS Response
            Yes -> Analyze
                -> Research & Enrich
                -> Update Permanent Knowledge Base
                -> Generate CoS Assessment
                -> Generate Actions
```

### Stage 0: Intake & Classification

Purpose: determine what was received and whether it contains intelligence that
should enter the RB intelligence workflow. Acts as the router for all incoming
information.

Questions: What was received? What type of artifact is it? Does it contain
intelligence? Which workflow should be invoked? Does it require extraction or
unpacking before analysis?

Examples:

- **LinkedIn ZIP file** -> Structured Data Export -> unpack ZIP, extract
  contacts/relationship data/profile updates/company info -> intelligence
  workflow.
- **Screenshot** -> Image -> OCR -> entity extraction -> context analysis ->
  determine whether intelligence exists -> intelligence workflow or normal
  response.
- **Industry article** -> Market Intelligence -> intelligence workflow.
- **Email** -> Communication -> e.g. meeting confirmation = no mutation
  required; executive departure / job offer / customer escalation = mutation
  required.
- **User query** ("Give me questions to ask Ryan about the offer.") -> User
  Query, not intelligence-bearing -> respond directly.

### Stage 1: Analyze

Purpose: understand the intelligence. What is new? What entities are involved
(people, companies, products, technologies, opportunities, risks,
relationships, events)? Is this material? What confidence level exists?

### Stage 2: Research & Enrich

Purpose: move beyond summarization. Before commentary is generated, RB should
gather context: what do we already know, what is missing, what additional
context is required, what internal records exist, what external information
should be gathered. Sources: existing RB records, relationship records,
company records, watchlists, historical intelligence, previous conversations,
market research, public information.

**Example — Hungry Howie's.** Article arrives reporting a loyalty program
sunset and a Restaurant365 rollout. Before commentary, RB should automatically:
retrieve the Hungry Howie's company file, retrieve technology stack history,
retrieve previous intelligence, identify the prior Toast POS announcement,
research recent company developments, leadership changes, loyalty history, and
technology stack evolution. Only then generate commentary.

### Stage 3: Update Permanent Knowledge Base

Purpose: create organizational learning. The goal is not article analysis — the
goal is intelligence accumulation. Question: "Will future reasoning improve if
RB remembers this?" If yes, store it.

**Mutation targets:**

- **Relationship Intelligence** — new title, new employer, new reporting
  structure, relationship strength, contact changes.
- **Company Intelligence** — technology stack, executive changes, ownership
  changes, strategic initiatives, vendor relationships, unit counts, expansion
  plans.
- **Vendor Intelligence** — customer wins/losses, product launches, market
  momentum, competitive positioning.
- **Narrative Intelligence** — technology modernization, AI adoption, platform
  consolidation, industry disruption, competitive shifts.
- **Watchlist Intelligence** — priority changes, risk changes, opportunity
  changes, monitoring triggers.

**Company Intelligence Files.** Every tracked company should maintain a living
intelligence profile, e.g.:

```
Hungry Howie's

Technology Stack
  POS: Toast
  Back Office: Restaurant365
  Loyalty: Howie Rewards (sunset) — replacement unknown
  Online Ordering: Unknown
  Last Verified: June 2026

Strategic Narrative: Technology Stack Modernization
  Supporting Signals: Toast selected as POS; Restaurant365 selected; loyalty sunset
  Confidence: High
  Next Expected Signals: loyalty replacement announcement; digital platform
    changes; mobile app updates
```

### Stage 4: Generate CoS Assessment

Only after enrichment and knowledge mutation. Assessment should answer: What
changed? Why does it matter? What do we already know? What narrative does this
support or contradict? What is likely to happen next?

### Stage 5: Generate Actions

Every intelligence event should be evaluated for actionability — relationship
actions (send congratulations, schedule outreach, reconnect), business actions
(track account, monitor competitor, update watchlist), strategic actions
(revise thesis, increase monitoring, investigate trend).

### Example Failure: Hungry Howie's

Known previously: Toast won Hungry Howie's POS business. New information:
Restaurant365 selected, loyalty sunset announced.

Expected output: "Since June, Hungry Howie's has selected Toast as POS provider,
Restaurant365 as back-office platform, and sunsetted Howie Rewards. These
developments collectively support a high-confidence technology stack
modernization narrative. Monitor for a replacement loyalty platform
announcement."

Actual output: individual announcements summarized independently. No
cross-signal correlation occurred. No narrative was developed. No institutional
memory was demonstrated.

### Success Criteria

When any future signal arrives involving Hungry Howie's, RB should
automatically know: prior Toast POS selection, Restaurant365 relationship,
loyalty history, existing narrative hypotheses, relevant vendor relationships —
without rediscovering those facts.

### Governing Principle

RB should not function as a news reader, email reader, calendar reader, or
LinkedIn reader. RB should function as an intelligence system. Every
intelligence-bearing input should: be understood, be enriched, be learned from,
improve the knowledge base, improve future reasoning, generate actionable
advice. After processing any intelligence-bearing input, RB should be smarter
than it was before the input was received.

This framework would address not only the Hungry Howie's example, but many of
the recurring issues identified around LinkedIn imports, daily briefs,
watchlists, company tracking, relationship intelligence, and the broader gap
between information processing and true Chief of Staff behavior.

---

## Gap Analysis vs. Current Architecture (2026-06-15)

Most of UIPF's machinery already exists in pieces. The gap is mostly in
**stitching it together as a mandatory pipeline** plus **one missing schema
concept (persisted narratives)**.

### Stage 0 — Intake & Classification

**Exists:** `intelligence_triage.py` already does multi-type, format-aware
classification — 8 input formats (incl. transcript/article/social_post/
linkedin_post) and 6 intelligence streams (macro_signal, micro_graph_build/
enrichment, ri_event, strategic_memory, noise), always read-only/route-only.

**Gap:** Not the *universal* mandatory entry point. Email, calendar, and
LinkedIn ingestion run through their own dedicated pipelines
(`email_overlay.py`, `calendar_overlay.py`, `linkedin_ingest*.py`) without
necessarily passing through `triage_input()` first — exactly the "different
workflows for different input types" symptom UIPF describes. Screenshot/image
OCR classification (UIPF's "Image" branch) does not appear to exist.

### Stage 1 — Analyze

**Exists:** `intelligence_mutation_engine.py` Stage 1-2 (entity extraction +
resolution against `ecosystem_intelligence.json` + baseline) and
`passive_intelligence.py` (claim-level confidence scoring) cover this well —
but only for content already triaged as `macro_signal`.

**Gap:** None major, conditional on Stage 0 routing everything here.

### Stage 2 — Research & Enrich (the Hungry Howie's gap)

**Exists:** `signal_synthesis.py`'s `getEntitySignals` aggregates across
`active_threads.yaml`, `ecosystem_intelligence.json`, `ri_events/`, social
overlay, etc., producing a `synthesis_hypothesis` and pattern classification (8
types: exit_positioning, growth_mode, distress, consolidation,
competitive_shift, transition, stable, unknown).

**Gap (core gap #1):** The "enrich before commentary" rule is currently
enforced only for **chat-driven company-name queries** (GPT prompt rule: call
`getEntitySignals` before CoS assessment). It is **not wired into the passive
article/news ingestion pipeline** — an incoming article goes through
`intelligence_mutation_engine` for entity/relationship mutation, but nothing
forces a "what do we already know about this entity" pull *before* the brief
writes commentary on it.

### Stage 3 — Update Permanent Knowledge Base

**Exists:** Substantial — `ecosystem_intelligence.json` (entities,
relationships, deployments, confidence, evidence_posture), `entity_intelligence.json`,
`strategic_memory.json`, `knowledge_mutations.json`, the relationship
classification model (6-level vendor significance).

**Gap (core gap #2):** No persisted **"Strategic Narrative"** object — a record
like *"Hungry Howie's: Tech Stack Modernization, confidence: high,
supporting_signals: [Toast POS, Restaurant365, loyalty sunset],
next_expected_signals: [loyalty replacement, digital platform changes]"* that
accumulates across multiple ingestion events. `synthesis_hypothesis` in
`signal_synthesis.py` is **computed fresh on each call, not persisted** — it
cannot accumulate "we already knew X, now Y confirms it" cross-signal
correlation. This is the direct cause of the Hungry Howie's failure mode.

Also missing: a rendered **"Company Intelligence File"** view (tech stack table
+ narrative + last-verified date) per watchlisted entity. Account dossiers
(`artifacts/data/*.json`, `rb_account_dossier_v1`) exist but only for **active
opportunities**, not general watchlist companies.

### Stage 4 — CoS Assessment

**Exists:** `cos_synthesis.py`, `cos_judgment.py`.

**Gap:** Downstream of Stage 2/3 — once those exist, this stage mostly needs to
consume the persisted narrative record instead of (or in addition to) the raw
signal.

### Stage 5 — Generate Actions

**Exists:** `action_drafts.py`, `cos_action_engine.py` — reasonably mature
already. No major gap.

---

## Proposed Implementation Slices (for future sprint scoping)

1. **Universal router.** Make `intelligence_triage.triage_input()` (or a thin
   wrapper) the mandatory first stop for every input type — email, calendar,
   LinkedIn, articles/screenshots (incl. new OCR/image classification branch) —
   not just pasted/uploaded content.
2. **Persisted Strategic Narrative schema.** New `strategic_narratives[]` array
   (likely per-entity inside `ecosystem_intelligence.json`, or a sibling file)
   with `narrative_type`, `confidence`, `supporting_signals[]`,
   `next_expected_signals[]`, `last_updated`. `intelligence_mutation_engine.py`
   gets a new stage that checks/updates this on every entity-relevant signal.
3. **Enrich-before-comment for passive ingestion.** Wire
   `getEntitySignals`/narrative-lookup as a mandatory pre-step in the
   macro_signal -> mutation -> brief pipeline, not just the chat company-query
   path. Once (2) exists, the Daily Brief / Intelligence Brief sections defined
   by RB-DEFECT-044/045 render from the persisted narrative record.
