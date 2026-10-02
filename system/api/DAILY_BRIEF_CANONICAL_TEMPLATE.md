# Daily Brief Canonical Template — Part 2 Authoritative Spec

**Status:** AUTHORITATIVE for Part 2 (`getDailyBriefPart2`) rendering — corrected 2026-08-25.

**Correction history:** around RB 9.97 (~2026-06-17), a split was planned — this file would
be deprecated in favor of two new files, `INTELLIGENCE_BRIEF_CANONICAL.md` (Part 1) and
`DAILY_BRIEF_CANONICAL.md` (Part 2) — to fix RB-DEFECT-056 (Intelligence Brief output
cross-contaminated with CoS sections). The Part 1 half of that split stuck:
`INTELLIGENCE_BRIEF_CANONICAL.md` is real and current. **The Part 2 half did not** — the
team kept developing Part 2's spec in this file instead (RB 9.69 two-call delivery,
RB 9.78–9.85 section additions, RB 10.x–10.7 restructuring, the 2026-07-06 confirmProposal
work — all landed here, none in `DAILY_BRIEF_CANONICAL.md`, which was created 2026-06-26 and
never touched again). Every other live KB file (`compact_8k`, `custom_gpt_instructions_8k.md`,
`custom_gpt_prompt.md`) already correctly cites *this* file as the Part 2 spec — only this
file's own header, and one line in `CANONICAL_RESPONSE_CONTRACT.md` (also fixed 2026-08-25),
still said otherwise. The deprecation banner below was never removed when the split reverted;
it had been actively wrong for at least 7 weeks. See [[rb_kb_authority_reconciliation_2026_08_25]].

**For rendering, use the content below — it is the real, current spec, not historical reference.**

---

**⚠ Section numbering below (Section 0, 3, 4, 5...) does not match the actual current render
order — the render sequence in `render_daily_brief.py::render()` was restructured twice on
2026-08-25 (once to lead with Decision Queue + Connect the Dots + GP Dot Connections per Todd's
stated purpose for the document, once more to add the 13 sections listed just below). Renumbering
this entire document to match is a real, not-yet-done task — read `render()` directly for the
authoritative current order until that renumbering happens.**

**13 sections added 2026-08-25 (same "computed, never rendered" bug class documented in
[[rb_daily_brief_autonomous_discovery_and_draft_actions_2026_08_25]] for Part 1's autonomous-
discovery section) — each of these fields was computed daily by `daily_brief.py` and shipped in
the API payload, but had zero implementation in `render_daily_brief.py` until this fix. None had
any item with `disposition=="ask_todd"`, so none were even partially caught by Decision Queue's
catch-all sweep — full silent loss, not partial:**

| Section (rendered heading) | Source field | Renderer function | Placed near |
|---|---|---|---|
| If I Were Your Chief of Staff Today | `cos_today` | `_render_cos_today` | CoS Opening (top) |
| Top Decisions Today | `decision_layer` | `_render_decision_layer` | Decision Queue |
| Risks | `strategic_risks` | `_render_strategic_risks` | Decision Queue |
| Competitive Opportunity Watchlist | `competitive_vulnerability_watchlist` | `_render_competitive_vulnerability_watchlist` | Technology Radar |
| Newsletter & Email Intelligence | `email_intelligence_harvest` | `_render_email_intelligence_harvest` | Technology Radar |
| RC Contact Status | `relationship_momentum_status` | `_render_relationship_momentum_status` | Notable Contact Moves |
| New Relationship Activity | `last_24h_relationship_signals` | `_render_last_24h_relationship_signals_p2` | Notable Contact Moves |
| Loops & Obligations | `loops_and_obligations` | `_render_loops_and_obligations` | My Priorities |
| This Week's Outcomes | `weekly_plan_focus` | `_render_weekly_plan_focus` | This Week / This Month |
| Prep Required | `upcoming_preparation_requirements` | `_render_upcoming_prep_requirements` | This Week / This Month |
| Learned Patterns | `learned_patterns` | `_render_learned_patterns` | This Week / This Month (suppressed when empty) |
| Job Market Scan | `job_intelligence` | `_render_job_intelligence` | Active Opportunities (suppressed when empty — job search inactive) |
| Pending Graph Mutations | `pending_graph_mutations` | `_render_pending_graph_mutations` | Pending Confirmations (suppressed when empty) |

Real content confirmed at time of fix: `loops_and_obligations` (9 items, ALL `act_today`),
`decision_layer` (3 items, ALL `act_today`), `last_24h_relationship_signals` (4 items, ALL
`act_today`), `relationship_momentum_status` (20 items, 13 `act_today` — FROZEN/COLD contacts
grouped and flagged `[ACTION NEEDED]` first, warmer tiers collapsed to a count),
`email_intelligence_harvest` (9 items, 6 `act_today`), `strategic_risks` (3 items, 1 `act_today`,
`[ACTION TODAY]`/`[MONITOR]` prefix), `cos_today` (1 pre-formatted item, rendered as direct
passthrough), and `competitive_vulnerability_watchlist` (27), `weekly_plan_focus` (3),
`upcoming_preparation_requirements` (9), `learned_patterns` (1) — all `monitor` disposition.
`job_intelligence`/`pending_graph_mutations` were empty (0 items) at fix time — wired anyway so
they don't silently disappear again once populated.

---

**Last reviewed:** 2026-06-17

---

## Two-Call Delivery (RB 9.69) / Document 1 vs. Document 2 (RB 9.85)

A single `getDailyBrief` response was approaching the ~70KB Action payload
ceiling, which forced silent, size-based trimming of whichever section
happened to be largest — not whichever was least important. That made
Section 3's "every watchlist entity must appear" mandate (and other
completeness requirements below) structurally impossible in one call.

The brief is now delivered across **two calls**, split exactly along this
template's section boundaries. RB-DEFECT-044's two-document briefing
architecture maps directly onto the two calls: **Document 1 (the
Intelligence Brief — "what changed?")** is Part 1, and **Document 2 (the
Daily Brief — "so what?" CoS synthesis)** is Part 2. "Part 1"/"Part 2" remain
the live Action operation names (`getDailyBrief`/`getDailyBriefPart2` cannot
be renamed without an Actions/`openapi_gpt.yaml` change); "Document 1"/
"Document 2" are the conceptual framing used when discussing the briefing
architecture in RB-DEFECT-044 and related docs. They refer to the same two
calls:

| Call | Operation | Document | Purpose | Contains |
|---|---|---|---|---|
| **Part 1** | `getDailyBrief` | Document 1 — **Intelligence Brief** | Factual layer only — what was scanned, what changed, what was found. ZERO recommendations, ZERO priorities. | What Changed (incl. `personal_intelligence_delta`) → A: World → B: National → C: Restaurant Industry → D: Restaurant Tech → **D+: Newsletter Inbox** → E: Earnings/Corporate → F: Watchlist → G: Opportunities → H: Relationship Deltas → **H+: Identity Confirmations Needed** → I: Strategic Signals → J: Proof Dashboard (delta table) |
| **Part 2** | `getDailyBriefPart2` | Document 2 — **Daily Brief** | CoS layer — so what, priorities, actions, recommendations | Executive Dashboard (What Changed / Top Priorities / Risks / Opportunities) → Technology Radar → My Priorities (14 sub-sections) → Horizon Watch |

**Architecture rule (RB-DEFECT-051 / RB-DEFECT-055):** Intelligence Brief = factual only. Daily Brief = CoS synthesis only.
- Intelligence Brief does NOT contain: priorities, recommendations, next actions, prep requirements, career guidance. ANY of these appearing in Part 1 = rendering failure.
- Daily Brief does NOT open with raw intelligence — it synthesizes from what the Intelligence Brief established.
- **Section 0 (Executive Dashboard) belongs in Part 2, NOT Part 1.** It contains recommendations and priorities — CoS synthesis product, not intelligence product.
- **Every news headline in Part 1 must include: title (as link), source publication, date.** Paraphrasing headlines into theme sentences = rendering failure.

**Flow:**
1. Call `getDailyBrief`. Render Part 1 / Document 1 (Intelligence Brief — factual layer).
2. Read the `continuation` field. It will say `part: "1_of_2"` and `next_action: "getDailyBriefPart2"`.
3. After Part 1, offer Part 2: *"That's the full intelligence picture — want your Daily Brief next? (priorities, calendar prep, opportunities, open loops, risks, week and month ahead)"*
4. If confirmed, call `getDailyBriefPart2` and render Document 2 (Daily Brief — CoS layer). Do not re-render Part 1.
5. If declined, session ends. Not a failure as long as the offer was made.

Pass/fail criteria below apply per-part: Part 1 (Document 1) is graded on the Proof Line + Sections 1–2 checklist; Part 2 (Document 2) is graded on the Sections 3–5 checklist. A session that only renders Part 1 (because the user declined Part 2) is **not** a failure as long as the offer was made.

---

## What a World-Class CoS Brief Is

> Wall Street Journal + Bloomberg Terminal + Executive Assistant + Industry Analyst + Personal CRM — in one briefing.

A world-class CoS brief answers eight questions:

1. **What changed overnight?**
2. **Why does it matter?**
3. **What should Todd do about it?**
4. **What is RB not seeing?**
5. **What signals are strengthening?**
6. **What signals are weakening?**
7. **What opportunities emerged?**
8. **What risks emerged?**

The brief should feel like a newspaper that arrives each morning and is **expected reading**.
If the user already knew everything in it — it failed.

---

## Intelligence Proof Block (always first — RB-DEFECT-049, RB 9.96)

This is the brief's trust anchor. It must appear before any narrative content and must show
**quantified source counts by category** — not source names, not topic names, not category labels.
A brief that opens with opinion before this block, or that shows category names instead of numbers, is a rendering failure.

```
📡 Intelligence Proof — 2026-06-16 | Generated 09:00 AM | Confidence: MEDIUM

Collection Results:
  Global news sources:             23
  National news sources:           18
  Restaurant publications:         14
  Restaurant technology pubs:      16
  Public company releases:          9
  Watch-list companies:            20
  Watch-list executives:           12
  Personal intelligence sources:    6  (4 connected, 2 unavailable)

Intelligence Found:
  Material intelligence items:      7
  Watch-list mutations:             2
  New entities added to monitoring: 1
  User-provided intelligence used:  1
  Emails requiring action:          3
  Calendar events (7d):             5
  Open loops:                      24  (14 overdue)
  Trust score:                     83%  | Freshness: 70%
```

**Rules:**
- Every count is a number. Never replace a count with a category name or topic.
  - PROHIBITED: "Sources scanned: Restaurant technology and payments news"
  - PROHIBITED: "Sources scanned: 32" with no category breakdown
  - REQUIRED: per-category counts as shown above
- Personal intelligence sources: show connected count vs. total — never just "unavailable"
- User-provided intelligence: always show count (0 is valid and expected)
- Confidence level from `brief_confidence` field: HIGH / MEDIUM / LOW / PARTIAL
- If collection failed: `📡 ⚠️ RB delivery failure — collection did not complete. No brief generated from memory.`
- **Sources:** `intelligence_collection_summary`, `daily_prep_summary`, `open_loops`, `resource_verification_and_freshness_status`
- **This block proves what was done. Always first. No exceptions.**

The session header line (`RB — {date} | {entity} [MOUNTED] | {open_loops} open loops | Confidence: {brief_confidence} ({trust_score}%)`) renders immediately before this block. It does not replace it.

---

## Intelligence Brief Observability Block (RB-DEFECT-051, RB 9.94)

**Appears immediately after the Intelligence Proof Block. Always rendered. Always populated from API data.**
These sections prove that intelligence gathering occurred and establish what the system knows vs. doesn't know.
A brief without this block cannot be trusted. "I looked everywhere" is not proof — this block is proof.

### IB-1 — Collection Health

```
⚕ Collection Health — 2026-06-16 | 10:00 AM CDT | Status: DEGRADED ⚠

Healthy (12):   email:personal, email:bridgepoint, calendar:personal,
                calendar:bridgepoint, calls, messages, linkedin_public_posts,
                interaction_overlay, passive_ri_ingest, open_loops,
                uploaded_ri_events, user_artifacts

Stale (5):      linkedin_messaging     — 212h old (threshold: 48h)
                market_signals         — 87h old  (threshold: 72h)
                social_engagement      — 713h old (threshold: 48h)
                social_own_posts       — 713h old (threshold: 48h)
                strategic_operators    — never refreshed

Failed (0):     none
```

**Sources:** `intelligence_collection_summary[0].extras` (stale_sources list, sources_healthy,
sources_stale, sources_failed), `signal_freshness` (per-source age + threshold).
Show age AND threshold for every stale source — never just "stale."

### IB-2 — Personal Information Sources

```
📬 Personal Information Sources

Email:      51 messages scanned | 18 awaiting your response | 12 responses received
            0 unresolved recruiter follow-ups | 13 newsletters processed
SMS:        9,143 events | 211 matched contacts | 0 urgency signals
Calls:      106 events | 4 matched | ⚠ 2 missed calls:
              • Jeff Wayman — Jun 10 (7 days ago)
              • Ryan Hildebrand — Jun 8 (9 days ago)
Calendar:   6 events this week | 4 needing prep | 1 interview activity detected
Contacts:   No job changes detected | No relationship mutations
LinkedIn:   Messages stale (212h) — 0 network mutations available
            Public feed: fresh | Own posts: stale (713h)
```

**Sources:**
- Email: `communication_intelligence[0]` (awaiting/received counts), `email_intelligence_harvest`
- SMS/Calls: `resource_verification_and_freshness_status` items "Direct comms: Apple Messages" and "Apple Calls"
- Calls urgency: `resource_verification_and_freshness_status` items with "Urgency signal: call missed"
- Calendar: `day_ahead` count + `communication_intelligence[1]` (calendar changes)
- Contacts: `relationship_momentum_status` (mutation count)
- LinkedIn: `signal_freshness` items for linkedin_messaging, social_outbound

**Format rule:** Every source renders always — connected, unavailable, or stale.
"0 changes" is intelligence. Silence is not — it creates doubt about whether the source was checked.
Missed calls from known contacts are ⚠ urgency signals — surface them with contact name and date.

**Self-Healing Rule (RB 9.96):** When personal sources are unavailable, do NOT render an error message.
Render the coverage status and the self-healing action required:

```
📬 Personal Information Sources — Coverage: 40% (2 of 5 connected)

Email:      ✗ Not connected — Action required: connect Gmail/Outlook integration
SMS:        ✗ Not connected — Action required: Apple Messages export unavailable
Calls:      ✗ Not connected — Action required: Apple Calls export unavailable
Calendar:   ✓ 6 events this week | 4 needing prep
Contacts:   ✓ No mutations detected
LinkedIn:   ✗ Messages stale (212h) — Action: re-authorize LinkedIn connector

Intelligence confidence reduced to PARTIAL due to 3 unavailable personal sources.
Self-healing: 3 integrations open. Reconnect via /settings to restore full coverage.
```

"Personal Intelligence Sources Not Available" rendered as a single error = rendering failure.
Always show coverage %, which sources are connected, and what action restores each missing source.

### IB-3 — Collection Metrics

```
📊 Collection Metrics

Records processed:      9,573
Mutations generated:    10
Market signals:         41 processed | 0 elevated (source stale — 87h old)
Watchlist entities:     22 scanned | 3 material updates | 19 no change
Sources healthy:        12 of 18 (67%)
Trust score:            83% | Freshness: 70%
```

**Sources:** `intelligence_collection_summary[0].extras` (records_processed, mutations_generated,
sources_healthy, sources_attempted, trust_score, freshness_pct),
`graph_mutation_log[2]` (market signals processed/elevated).

### IB-4 — Watchlist Scan Results

```
🔍 Watchlist Scan — [N] entities

Material Updates:
✓ Global Payments — [signal description]
✓ Worldpay — [signal description]
✓ Yum Brands — [signal description]

No Material Updates:
✓ PAR Technology    ✓ Olo             ✓ NCR Voyix        ✓ Agilysys
✓ Restaurant365     ✓ Toast           ✓ McDonald's       ✓ Starbucks
✓ [... all remaining entities]
```

**Sources:** `watchlist_intelligence` — per-entity items for material updates;
rollup items (`extras.entity_type == "rollup"`) with `extras.no_change_entities` list.

**Format rule:** Every entity must appear — either as a material update with detail,
or in the no-material grid. The ✓ proves the entity was scanned.
The user must be able to answer: "Was X checked?" by looking at this section.

### IB-5 — Knowledge Base Mutations

```
🧠 Knowledge Base Mutations

RI Events:      0 persisted | 1 proposed (Tammy — awaiting confirmation)
Passive RI:     1 proposed from system signals (60 duplicates skipped)
Market signals: 41 processed | 0 elevated to KB
Graph changes:  0 permanent mutations this cycle

Proposed (require confirmation):
• Tammy — new relationship event from today's meeting (confirm via reviewRIEvent)
```

**Sources:** `graph_mutation_log` (persisted/proposed RI events, market signals processed),
`pending_mutations` (interactions awaiting confirmation).

**Format rule:** Show counts for each mutation type. If proposed mutations exist,
list them with the confirmation action. "0 mutations" is a valid and expected output.

---

---

## ═══ PART 2 — DAILY BRIEF (getDailyBriefPart2) ═══

**Contains:** Executive Dashboard → Technology Radar → My Priorities (14 sub-sections) → Horizon Watch
**Does NOT open with raw intelligence** — it synthesizes from what Part 1 (Intelligence Brief) established.
**Every priority in Part 2 must trace to a specific signal from Part 1.** Format: Signal (what Part 1 found) → Implication → Action. "Refine your thesis" without naming a specific signal from Part 1 = rendering failure.

---

## Section 0 — Executive Dashboard (RB-DEFECT-050, RB 9.92) — PART 2 OPENER

**Appears immediately after the Intelligence Proof Block, before all other sections.**
This section answers: "What is happening in my world right now?" It is always populated
from the user's personal context — career, calendar, loops, risks — never from news.

```
📊 Executive Dashboard — 2026-06-16

Status
Career opportunities:   2 active (Global Payments — offer stage; Foods Connected — final round)
Decisions pending:      2
Meetings this week:     4 (Tammy 10AM today; Vik tomorrow; Scott Friday; Hospitality Table Thu)
Emails requiring action: 0
Relationship follow-ups: 2 overdue
Material watchlist changes: 3 escalated (Global Payments, Worldpay, Genius)

What Changed Since Yesterday
⚠️ CRITICAL: 2nd Stage Foods Connected interview shows CANCELLED in calendar.
             Clarify status with Sarah immediately — this may be a reschedule or a withdrawal.
• No new inbound from Global Payments (41 days in pipeline).
• Watchlist: 3 escalated entities (Global Payments, Worldpay, Genius).
• Today: preparation and execution day — no inbound fires.

Top Three Priorities
1. Clarify Foods Connected interview status (email or call Sarah today).
2. Build Foods Connected presentation (if interview still active).
3. Complete Global Payments diligence items (PTO, health insurance, commission structure).

Risks
• Foods Connected interview cancellation status is unresolved — act today.
• Presentation preparation window is narrowing.
• Multiple active opportunities creating context switching.

Opportunities
• Quiet inbox — no inbound fires requiring reaction.
• Vik Devjee meeting tomorrow: natural venue to explore restaurant tech alignment.
```

**Sources:**
- Status block:
  - Career opportunities: `opportunity_board` — count items with status ACTIVE or WAITING
  - Decisions pending: `decision_layer` count
  - Meetings this week: `day_ahead` + `this_week_priorities` calendar items
  - Emails requiring action: `email_intelligence_harvest` count
  - Relationship follow-ups: `loops_and_obligations` overdue relationship-category loops
  - Material watchlist changes: `what_changed_since_yesterday` watchlist escalation count
- What Changed: `what_changed_since_yesterday` items + `five_things_today` calendar-change items
- Top Three Priorities: `five_things_today` top 3, synthesized into action-oriented sentences
- Risks: `strategic_risks` + CoS synthesis from `opportunity_board` state
- Opportunities: `opportunities_detected` + quiet-inbox state from `email_intelligence_harvest`

**Critical Calendar Alert Rule:**
If any item in `what_changed_since_yesterday` or `five_things_today` contains a
cancelled calendar event that matches the name or company of an active `opportunity_board`
thread, it must appear as:
- The FIRST item in "What Changed Since Yesterday" with prefix `⚠️ CRITICAL:`
- Item #1 or #2 in "Top Three Priorities"
This is not a routine calendar note — a cancelled interview is potentially disqualifying.
Never bury a cancelled interview in a list of routine calendar changes.

**Format rules:**
- Status block: counts only (numbers, not commentary)
- What Changed: bullet points, most critical first, CRITICAL prefix for interview/offer events
- Top Three Priorities: numbered, action-oriented sentences with named next step
- Risks: bullet points; include career-specific risks beyond auto-detected relationship/loop risks
- Opportunities: bullet points; include quiet-cycle reframe ("no inbound fires" = preparation time)
- If all status items are zero/clean: "No material changes. Today is a clear preparation day."

---

---

## ═══ PART 1 — INTELLIGENCE BRIEF (getDailyBrief) ═══

**The Intelligence Brief is a newspaper, not a memo.**
It reports what happened. It does not interpret what it means or tell the user what to do.
A newspaper editor would cut anything that is not a fact, a count, or a sourced headline.

**Allowed sections (in order):**
1. Intelligence Collection Summary (proof of collection)
2. Intelligence Processing Summary (counts: mutations, material items)
3. World Headlines
4. National Headlines
5. Industry Headlines — Restaurant Operations
6. Industry Headlines — Restaurant Technology
7. Watchlist Intelligence (exception-based)
8. Relationship Intelligence (mutations only)
9. Strategic News Themes (factual patterns only — no advice)
10. Intelligence Snapshot (closing counts and facts — no advice)

### PROHIBITED SECTIONS IN INTELLIGENCE BRIEF (RB-DEFECT-056)

Any of the following appearing in Part 1 = rendering failure. Move to Part 2.

| Prohibited | Example of Violation | Lives In |
|---|---|---|
| Executive Assessment | "You have entered a significant transition week" | Daily Brief Part 2 |
| Open Loops | "Obtain commission documentation" | Daily Brief Part 2 |
| Intelligence Priorities as tasks | "Prepare Foods Connected presentation" | Daily Brief Part 2 |
| Potential Impact sections | "Why This Matters For You" | Daily Brief Part 2 |
| CoS Intelligence Observations | "The enterprise market appears receptive..." | Daily Brief Part 2 |
| Intelligence Bottom Line as advice | "The signal environment today is favorable" | Daily Brief Part 2 |
| Strategic Assessment with probability ratings | "Global Payments — Strong" | Daily Brief Part 2 |
| Any recommendation | "You should consider..." | Daily Brief Part 2 |
| Any career guidance | "Position yourself for..." | Daily Brief Part 2 |

### PROHIBITED HEADLINE FORMAT (RB-DEFECT-056)

Headlines must be reportable facts — the actual title of an event, not an analyst's characterization.

PROHIBITED: "Enterprise Technology Spending Remains Selective" (analyst opinion)
PROHIBITED: "AI Adoption Accelerates in Restaurant Operations" (theme sentence — not a headline)
REQUIRED:   "Federal Reserve Signals Caution on Rate Cuts" (actual news event)
REQUIRED:   "Toast Launches AI-Powered Operator Workflows" (specific action by named entity)

### Mandatory News Headline Format (Part 1 — applies to Section 1 and Section 2)

Every news item must follow this format exactly:

```
[CATEGORY]: [HEADLINE TITLE AS LINK](url)
Source: [Publication] | Date: [date]
Why it matters: [1-2 sentences of significance]
Why Todd cares: [specific connection to active opportunity, watchlist company, or relationship]
```

Example:
```
[INDUSTRY]: Toast Launches AI-Powered Operator Workflows (https://restaurant-business.com/...)
Source: Restaurant Business | Date: Jun 16, 2026
Why it matters: AI shifting from experimentation to embedded operations across QSR.
Why Todd cares: If joining Foods Connected, understanding embedded AI workflows is critical enterprise sales positioning.
```

**PROHIBITED:**
- Paraphrasing multiple headlines into a theme paragraph ("The restaurant industry is seeing AI adoption...")
- Omitting the link when `extras.source_url` is present
- Omitting Source or Date
- "This is relevant because of your thesis" without naming the specific thesis connection
- Headlines without a "Why Todd cares" line — if no personal connection exists, suppress the item

### Mandatory Watchlist Intelligence Format (Part 1 — applies to IB-4 and Section 3 material updates)

Every watchlist entity must render as exactly ONE of:

**A) Material Update (factual):**
```
✓ [ENTITY]: [SPECIFIC FACT — what happened]
Source: [Publication] | Date: [date] | [Link](url)
Why it matters: [one sentence]
```

**B) No material update:**
```
✓ [Entity] — No material developments detected this cycle.
```

**PROHIBITED:**
- Commentary without a dated source ("Toast is strong in SMB and questions remain about enterprise scalability" — this is OPINION, not intelligence)
- Generic competitive analysis ("X is competing well against Y")
- Characterizations not grounded in a specific dated event

---

## Attribution Type Rendering Rule

Every item carries `attribution_type`. This is the core trust signal.

| Type | Meaning | How to render |
|---|---|---|
| `OBSERVED` | Discovered from live scan this cycle | Full headline — **new intelligence** |
| `SYNTHESIS` | Computed from multiple OBSERVED inputs | Full item — **analysis** |
| `MEMORY` | From knowledge graph; no new scan evidence | `📂 [Context]` prefix — **known state** |
| `HYPOTHESIS` | User-stated belief, not scan-verified | `💭 [Hypothesis]` prefix |

**If the majority of brief items are MEMORY:** Add banner at top:
```
⚠️ Limited new intelligence this cycle. Most content reflects existing knowledge base.
```

---

## Part 1 Headline Sections — Structure and Ordering (RB 9.96)

Part 1 news content is organized into **six distinct sections in fixed order**. Each section uses the mandatory headline format. Combining sections, collapsing into a single "Industry" bucket, or rendering theme paragraphs instead of sourced headlines = rendering failure.

```
Section A: World Headlines          (international events)
Section B: National Headlines       (US economy, policy, markets, labor, regulation)
Section C: Industry Headlines — Restaurant Operations
Section D: Industry Headlines — Restaurant Technology
Section E: Watch List Mutations     (exception-based — material changes only)
Section F: Strategic Signals        (3-5 emerging patterns from today's intelligence)
```

**User-Provided Intelligence Rule (RB 9.96):** Intelligence ingested via `ingestContent` must surface as first-class intelligence in the appropriate section — not discarded, not mentioned separately. Label: `Source: User-provided intelligence ([date]) + [publication if available]`. This proves the system learned from what the user shared.

---

## Section A — World Headlines

```
🌐 World Headlines — 2026-06-16

⚡ Material Changes Since Yesterday:
• Fiserv named new CEO (Payments Dive, Jun 15) — payments infrastructure movement
• 2 new loops opened | 14 loops now overdue (was 12 yesterday)
• Minimal market intelligence change — last scan 87h ago (threshold: 72h)

[WORLD]: AI giants' race to raise funds heats up as ChatGPT-owner plans IPO (https://ft.com/...)
Source: Financial Times | Date: Jun 16, 2026
Why it matters: OpenAI IPO timeline signals AI infrastructure investment cycle beginning.
Why Todd cares: Enterprise buyer appetite for proven AI vendors will accelerate — directly relevant to restaurant tech positioning.

[WORLD]: Middle East tensions escalate, oil markets react (https://bloomberg.com/...)
Source: Bloomberg | Date: Jun 16, 2026
Why it matters: Energy cost pressure on restaurant operators; supply chain risk increasing.
Why Todd cares: Commodity/energy cost pressure = accelerated automation investment thesis.
```

**Mandatory Delta Subsection:** Render `⚡ Material Changes Since Yesterday:` immediately after section header, before headlines. Source: `what_changed_since_yesterday` + loop ledger delta. Never substitute commentary. If nothing changed: "Minimal change detected since previous intelligence cycle."

**Sources:** `world_national_headlines` (world-scope items), `overnight_delta_intelligence`, `what_changed_since_yesterday`

**Format rules:** 5–10 items. Every item must have link, source, date, "Why it matters," "Why Todd cares." Suppress items with no personal connection. User-provided intelligence appears here with label: `Source: User-provided intelligence ([date]) + [publication]`. Never populate from MEMORY. If MEMORY-only: "Limited new global intelligence this cycle."

---

## Section B — National Headlines

```
🇺🇸 National Headlines — 2026-06-16

[NATIONAL]: Fed signals rate cuts may begin later this year (https://wsj.com/...)
Source: Wall Street Journal | Date: Jun 16, 2026
Why it matters: Financing conditions may improve for restaurant expansion and tech investment.
Why Todd cares: If joining Global Payments or Foods Connected, their capital deployment posture shifts.

[NATIONAL]: Immigration reform may be on the table in 2026 (https://reuters.com/...)
Source: Reuters | Date: Jun 16, 2026
Why it matters: Restaurant workforce uniquely impacted — labor cost pressure accelerates automation investment.
Why Todd cares: Core thesis validation — operator pain drives restaurant tech adoption.
```

**Sources:** `world_national_headlines` (US-focused items — economy, federal policy, employment, markets, regulation, major corporate developments)

**Format rules:** 5–10 items. Same mandatory format as Section A. US-scope only. Filter: economy, labor, regulation, markets, technology policy. Skip items without restaurant or career relevance.

---

## Section C — Industry Headlines: Restaurant Operations

```
🍽️ Industry Headlines — Restaurant Operations

[RESTAURANT]: Yum! Brands divests Pizza Hut Turkey business (https://restaurantbusiness.com/...)
Source: Restaurant Business | Date: Jun 16, 2026
Why it matters: Portfolio optimization and franchising discipline — continued focus on core markets.
Why Todd cares: Yum is on watch list; this confirms strategic direction for enterprise tech partnerships.

[RESTAURANT]: Restaurant bankruptcies up 18% YoY as consumer spending normalizes (https://...)
Source: Nation's Restaurant News | Date: Jun 15, 2026
Why it matters: Operator financial stress = increased pressure on tech vendors for ROI proof.
Why Todd cares: Foods Connected enterprise pitch must lead with cost savings, not features.
```

**Sources:** `restaurant_industry_headlines` — operations, labor, consumer trends, franchise, commodity pricing, M&A (non-tech)

**Format rules:** 5–10 items. Same mandatory format. Restaurant operations only — NOT technology. Technology items belong in Section D.

---

## Section D — Industry Headlines: Restaurant Technology

```
💻 Industry Headlines — Restaurant Technology

[RESTTECH]: Toast launches AI-powered operator workflows (https://...)
Source: Restaurant Technology News | Date: Jun 16, 2026
Why it matters: AI shifting from experimentation to embedded operations across QSR.
Why Todd cares: If joining Foods Connected, embedded AI workflows are critical enterprise sales positioning.

[RESTTECH]: PAR Technology announces Pizza Factory platform expansion (https://...)
Source: PAR Technology IR | Date: Jun 15, 2026
Why it matters: Unified platform strategy gaining traction with large franchise operators.
Why Todd cares: PAR is on watch list — platform expansion changes competitive landscape for enterprise sales.
```

**Sources:** `restaurant_technology_headlines`, `strategic_industry_signals` — payments, POS, AI/voice, delivery, loyalty, scheduling, analytics, M&A (tech companies)

**Format rules:** 5–10 items. Same mandatory format. Technology companies only. User-provided intelligence about restaurant tech articles surfaces here.

---

## Section E — Watch List Mutations (Exception-Based)

```
🔍 Watch List Mutations — 2026-06-16
Scanned: 20 companies | 12 executives

Material Updates:
✓ Global Payments — Continued messaging around software-enabled commerce (Reuters, Jun 16)
  Source: Reuters | Date: Jun 16, 2026 | (https://...)
  Why it matters: Platform strategy language aligns with Todd's thesis.

✓ Worldpay — CFO departure announced (FT, Jun 15)
  Source: Financial Times | Date: Jun 15, 2026 | (https://...)
  Why it matters: C-suite instability at payments infrastructure player.

No other material changes detected across 18 remaining monitored companies and 12 executives.
```

**Format rules (Exception-Based — RB 9.96):**
- Lead with total scan counts: `Scanned: [N] companies | [N] executives`
- Show full treatment ONLY for entities with material changes (specific fact + source + date + link)
- **Single sentence for all no-change entities**: `No other material changes detected across [N] remaining monitored companies and [N] executives.`
- PROHIBITED: listing each entity with "No updates" — this creates banner blindness
- PROHIBITED: commentary or opinion about entities without a dated source ("Toast is strong in SMB...")
- If nothing material: `No material changes detected across 20 monitored companies and 12 executives.`

**Sources:** `watchlist_intelligence` (material items only), `what_changed_since_yesterday` (watchlist escalations)

---

## Section F — Strategic Signals

```
📡 Strategic Signals — 2026-06-16

1. AI is shifting from experimentation to embedded operations.
   Evidence: Toast AI workflows (Jun 16) + PAR platform expansion (Jun 15) + McDonald's voice AI expansion (Jun 14).
   Signal: 3 separate vendors announcing embedded AI in the same week = category inflection point.

2. Payments infrastructure is in executive transition.
   Evidence: Worldpay CFO departure (Jun 15) + Fiserv new CEO (Jun 15).
   Signal: Two major payments players changing leadership simultaneously = instability or strategic pivot.

3. Restaurant operator financial stress is rising.
   Evidence: Bankruptcies +18% YoY (Jun 15) + commodity cost pressure (Jun 14).
   Signal: Vendor ROI proof requirements will tighten — enterprise tech pitches must lead with cost savings.
```

**Format rules:** 3–5 signals. Each signal must name ≥2 data points from today's intelligence (not MEMORY). Format: Signal title → Evidence (named items with dates) → Pattern interpretation.

**PROHIBITED:** "The restaurant industry is seeing AI adoption" (one item ≠ a signal). A signal requires ≥2 independent data points converging on the same pattern.

**Sources:** `strategic_industry_signals`, `emerging_themes`, synthesis from Sections A–E items.

**Renamed from "Emerging Themes" (RB 9.96):** "Themes" = reporting. "Signals" = intelligence.

---

---

## Section G — Intelligence Snapshot (Part 1 Closing Section)

The Intelligence Snapshot closes the Intelligence Brief with counts and facts only. No advice. No recommendations. No interpretation. No tasks.

```
📊 Intelligence Snapshot — 2026-06-16

Material developments identified today:  7
Knowledge mutations:                      6
Watchlist mutations:                      4
Relationship mutations:                   2

Highest-significance themes (factual):
• Enterprise technology cost pressure across restaurant operators
• Payments infrastructure companies moving up the software stack
• Continued restaurant technology consolidation activity
• Active changes across personal opportunity watchlists

Sources healthy: 12 of 18 | Trust score: 83% | Next cycle: 2026-06-17 06:00 AM
```

**Format rules:**
- Counts and facts only. Zero interpretation.
- "Highest-significance themes" = factual patterns from today's news — NOT advice, NOT "what to do"
- If Intelligence Priorities are listed: they must be monitoring activities ONLY
  - ALLOWED: "Monitor Global Payments compensation disclosures"
  - PROHIBITED: "Obtain commission documentation" (task), "Prepare presentation" (task)
- Never end with: "The signal environment is favorable" — that is CoS judgment, not intelligence

---

**End of Part 1 (Intelligence Brief).** Offer Part 2: *"Intelligence picture complete. Want your Daily Brief? (priorities, calendar prep, risks, opportunities)"*

**MANDATORY PERSONAL RULE (RB-DEFECT-049):** Active career opportunities from `opportunity_board` must be included as items in Section A or as a Personal Intelligence Delta before Strategic Signals. Never omit active pipeline status from the Intelligence Brief.

**Delta close (end of Part 1):** `⚡ [N] items changed since yesterday. [N] mutations. [N suppressed — no personal connection.]`

---

## Section 3 — Restaurant Technology Radar

```
💻 Restaurant Technology Radar:

[💰 FUNDING] SoundHound raises $50M Series C
Company: SoundHound | Signal: funding_round | Date: Jun 8
Why it matters: Voice AI category now better-capitalized. Deployment velocity will increase.
Implication: Operators evaluating voice AI will have more vendor options. Watch for Yum/QSR deployments.
→ Connected: Active interest in voice AI from McDonald's franchise operators (your network)

[👤 EXEC HIRE] Harri names new CRO from Workday
Company: Harri | Signal: executive_hire | Date: Jun 7
Why it matters: Enterprise SaaS executive hire = shift to enterprise go-to-market.
Implication: Re-evaluate your Harri relationship. New CRO may need network introductions.

No material updates this cycle:
Scanned 18 additional entities — no signals detected. (STRATACACHE, Coates Group, Qu, Agilysys, NCR, Oracle Hospitality, Olo, Lightspeed, Revel, Xenial, SpotOn, Givex, Presto Automation, Bopple, Tacit, Omnivore, Apicbase, Craftable)
```

**Sources:**
- `restaurant_technology_headlines` — tech-specific news, signal-classified
- `watchlist_intelligence` — per-entity monitoring with mandatory coverage
- `strategic_industry_signals` — high-confidence strategic signals
- `condensed_industry_context` — market context items

**Format rules (RB-DEFECT-049 Watchlist Rollup):**
- Entities WITH material signals: full treatment with `[BADGE]`, `Company:`, `Signal:`, `Date:`, `Why it matters:`, `Implication:`, and `→ Connected:` (when cross-entity correlation exists)
- Entities with NO signal: **collapse to a single rollup line**, NOT a per-entity list
  - CORRECT: `No material updates this cycle:\nScanned [N] additional entities — no signals detected. ([Entity1], [Entity2], ...)`
  - PROHIBITED: Listing each entity individually as `[ENTITY] — No signals detected this cycle.`
  - The distinction: a per-entity list of 80+ "No signals" entries creates noise that users learn to ignore. The rollup proves coverage without the noise.
- Sort by signal_weight descending (acquisition > funding > exec hire > customer win > general)
- **Cross-entity correlation:** When two watchlist entities are connected by a signal: `→ Connected: [how this signal relates to another entity or active opportunity]`
- **Signal strength labels:**
  - 🔴 HIGH: acquisition, bankruptcy, funding >$50M, major exec departure
  - 🟡 MEDIUM: exec hire, customer win, product launch, deployment
  - 🟢 MONITOR: partnership, earnings, general news

**Negative Reporting Rule (mandatory):**
Coverage is proven by naming the entities in the rollup sentence, not by listing each one individually. Silence ≠ "checked and found nothing." The rollup sentence proves coverage.

**"No Change" rollup items (RB 9.69+):** `watchlist_intelligence` items with
`extras.entity_type == "rollup"` and `extras.watchlist_status == "No Change"`
each represent ALL entities in one mandatory-coverage bucket
(`extras.category`: `restaurant_brand` or `restaurant_tech`) that had no
signal this cycle. `extras.no_change_entities` is the full name list
(`extras.no_change_count` = its length). For each name in that list, render
one line: `[ENTITY] — No signals detected this cycle.` This is how full
~100-entity coverage is delivered without one verbose item per entity —
do not skip this list or summarize it as "and N others."

Mandatory Coverage — Restaurant Technology (must appear every cycle):
PAR Technology | Toast | NCR Voyix | Oracle Hospitality | Square | SpotOn | Qu | Revel | Samsung Koomi | GK Software | Punchh | Paytronix | Thanx | SessionM | PAR Loyalty | Blackbird | Incentivio | Global Payments | Shift4 | Fiserv | Adyen | Worldpay | FreedomPay | Stripe | Restaurant365 | Crunchtime | QSR Automations | MarginEdge | MarketMan | xtraCHEF | ClearCOGS | Tenzo | NomadGo | Barmetrix | STRATACACHE | Coates Group | Acrelec | Mood Media | Raydiant | Spectrio | Nanonation | Korbyt | Scala | Olo | Lunchbox | Checkmate | Deliverect | ChowNow | SoundHound | Presto | ConverseNow | Kea | Valyant | Hi Auto | Agot | Berry AI | Everseen | Veesion | Harri | Legion | Fourth | HotSchedules | Schoox | WorkJam | Miso Robotics | Serve Robotics | Bear Robotics | Foods Connected | Genius

Mandatory Coverage — Restaurant Brands (must appear every cycle):
McDonald's | Starbucks | Yum Brands | Restaurant Brands International | Chipotle | Wendy's | Domino's | Darden | Inspire Brands | Chick-fil-A | Dutch Bros | Sweetgreen | CAVA | Wingstop

---

### Competitive Opportunity Watchlist (RB 9.69+ — `competitive_vulnerability_watchlist`)

Render immediately after Watchlist Intelligence, still within Section 3.

**Source:** `competitive_vulnerability_watchlist` — account vulnerability/RFP
early-warning scoring (exec change, tech hiring, vendor relationship,
operational friction, strategic change, vendor financial distress).

**Format:** Group by tier (`extras.tier`): "High-Risk Opportunities"
(`high_risk`) first, then "Emerging Opportunities" (`emerging`), then "Watch
List" (`watch`). For each item show:
- Entity name and `Opportunity Score: N/100` (`extras.vulnerability_score`)
- 2-3 bullet signals from `extras.signals` (`category_label` + title, linked
  to `url` when present)
- `Potential Categories: [...]` (`extras.potential_categories`)
- `Estimated Opportunity Horizon: N months` (`extras.estimated_horizon_months`)
- If `extras.rfp_detected` is true, add: "RFP/RFI detected — this opportunity
  may already be in active competition."

Frame this as "where opportunity is likely to emerge next" — not just news.
If `competitive_vulnerability_watchlist` is empty, render: "No elevated
vulnerability signals detected this cycle."

---

## Section 4 — My Priorities (Evidence-Traced Priorities)

**Evidence Traceability Rule (RB-DEFECT-055):** Every priority must name the specific signal from Part 1 (Intelligence Brief) that triggered it. Format:

```
Signal:     [Specific fact from Part 1 — company, date, source]
Implication: [What it means for Todd's current situation]
Action:      [Specific next step — not "continue X"]
```

Example:
```
Signal:     Global Payments messaging continues to emphasize software-enabled commerce (Reuters, Jun 16)
Implication: Supports Todd's payments-funded technology thesis — alignment with their public narrative is confirmed
Action:      Refine enterprise pitch with this specific messaging angle before next call with Mike
```

"Refine your thesis" or "Continue developing your positioning" without a named signal from Part 1 = rendering failure.

```
🎯 My Priorities:

CALENDAR (RB-DEFECT-050 — merge relationship context with calendar):
• [TODAY 10AM] Tammy Billings — Todd Vahlsing General Meeting
  First meeting. She reached out Jun 12 via Bookings (5 prior email exchanges).
  Objective: Determine strategic fit and next steps.
  Prep: No deep relationship context. Frame meeting around understanding her needs.

• [TOMORROW] Vik Devjee — 1:1 conversation
  Context: [Company background from baseline]. Likely QSR/restaurant tech overlap.
  Objective: Explore strategic alignment and potential collaboration.
  Prep: Review company background; identify 2-3 specific connection points to your work.

• [FRIDAY] Scott [last name] — Breakfast
  Context: [Relationship context from baseline].
  Prep: [Agenda or objectives based on active threads].

**Calendar format rule (RB-DEFECT-050):**
Each calendar item must merge with relationship data from `last_24h_relationship_signals`
and `relationship_operational_signal_review`. Show: time, name, objective, relationship
context (depth, last contact, how you know them), and prep requirement.
Never show a calendar item as just a title + company. Always answer:
- Who is this person?
- What is the objective of this meeting?
- What do I need to do before I walk in?

EMAIL:
• [AWAITING RESPONSE] "Let's continue the conversation" — sent Jun 8 (1d)
  Category: direct follow-up. Recipient: [contact]. No response yet — in window.
  → Monitor. Follow up Jun 12 if no reply.

• [AWAITING RESPONSE] "Re: Checking in - Hari" — sent Jun 3 (6d)
  6 days awaiting response. Response window narrowing.
  → Consider gentle follow-up today.

OPEN LOOPS (RB-DEFECT-050 — career-opportunity grouping first):
[Group major active opportunities from opportunity_board before chronological overdue list]

Global Payments — Offer stage
Pending:
• PTO negotiation
• Health insurance / Enbrel coverage review
• Commission structure clarification

Foods Connected — Final-stage candidate
Pending:
• ⚠️ Clarify interview cancellation status with Sarah
• Build presentation (once status confirmed)
• Prepare trust narrative (McDonald's operating experience)

PerfectHire — Advisory discussions
Pending:
• Determine level of commitment
• Evaluate equity opportunity

Overdue relationship loops (14 total):
• L-2026-05-26-004 — Dave Richards: Send owed follow-up text. OVERDUE.
• L-2026-05-08-001 — Bridgett Hendrickson: Reach out. Due this week.
[... remaining overdue loops by priority score]

**Open Loops format rule (RB-DEFECT-050):**
When `opportunity_board` has ACTIVE or WAITING items: group loops BY OPPORTUNITY FIRST.
For each opportunity thread: show status + pending items as a bulleted list.
After opportunity grouping: show remaining overdue loops chronologically.
Never render loops as an undifferentiated chronological list when career opportunities are active.

ACTIVE OPPORTUNITIES:
• [ACT] Genius / Global Payments — Offer stage. 41 days in pipeline.
  Primary contact: Mike Schwartz. Next step: complete diligence items.

• [WAITING] Foods Connected — Final-stage, 21d waiting. Contact: Sarah McAngus.
  ⚠️ Interview shows cancelled in calendar — clarify status before any other action.
  Momentum decay risk. Decision window approaching.

WEEKLY GOALS:
• Publish LinkedIn post — restaurant-tech consolidation thesis (draft ready)
• Schedule Barmetrix consultation call this week

THIS WEEK:
• Amy/Todd Connect — Wed 9am. Deliverable: GTM one-pager.
• Earnings call: Toast (TOST) — Thu, within 7 days.

THIS MONTH:
• L-2026-05-30-002 — Foods Connected: Draft integration proposal. Target Jul 5 (18 days out).
• Earnings call: Olo (OLO) — Jul 9, within 30 days.
• [MONITOR] Genius / Global Payments — 22d in pipeline, no new movement. Keep on the radar.

PREP REQUIRED:
• [Within 7 Days] Daniel von Walzel — 20 min: review BridgePoint thread history.
• [Within 30 Days] Board prep — 45 min: refresh deck with Q2 pipeline numbers.

LEARNED PATTERNS:
• Sales/pipeline review has required 30 min of preparation on 5 of the last 12 days tracked. Schedule 30 min of prep ahead of the next sales/pipeline review.

RISKS (RB-DEFECT-050 — career-specific + auto-detected):
Career risks (synthesized from opportunity_board state):

Foods Connected
Risk: Interview cancellation is unresolved — may signal withdrawal or reschedule.
Mitigation: Contact Sarah today. Do not build presentation until status is confirmed.

Global Payments
Risk: Excitement of offer may cause premature commitment before diligence is complete.
Mitigation: Complete PTO, health insurance, and commission reviews before responding.

Personal Capacity
Risk: Multiple active opportunities creating context switching and preparation debt.
Mitigation: Time-block presentation work today (Foods Connected) before taking calls.

Auto-detected:
• ⚠️ Relationship risk: 1 FROZEN contact (Bridgett Hendrickson) with open loop.
• Operational debt: 14 overdue loops accumulating.

**RISKS format rule (RB-DEFECT-050):**
When `opportunity_board` has ACTIVE or WAITING items: always derive career-specific risks
in [Opportunity] → Risk → Mitigation format. These are the CoS's judgment, not auto-detected
system flags. They must be specific to the current pipeline state — not generic.
Auto-detected risks (FROZEN contacts, overdue loops) follow career risks, not replace them.
"Continue X" is never an acceptable mitigation. Name the specific action.

PENDING CONFIRMATIONS:
• 2 proposed interactions awaiting confirmation >12h (oldest: Jun 12) — ask the user to confirm or reject each by name, then call `confirmProposal` with `kind="relationship"` and the interaction id.

DECISIONS REQUIRED:
• Accept Global Payments offer
  Stakes: compensation, equity structure, Enbrel coverage, PTO policy
  Deadline: Friday (verbally indicated)
  Options: accept as-is / negotiate specific terms / request extension

RECOMMENDED ACTIONS:
Today
• Clarify Foods Connected interview status — email Sarah (Signal: calendar cancellation)
• Complete Enbrel coverage review before any offer decision (Signal: pending diligence item)

This Week
• Time-block 2h for Foods Connected presentation (if interview confirmed)
• Build target list of 10 enterprise restaurant accounts for Global Payments onboarding (Signal: your domain expertise is the differentiator — establish this in week 1)

Next 30 Days
• Publish "software-funded payments" LinkedIn post using operator-simplification macro signal (Signal: Sec. I — operators want consolidation, Reuters Jun 16)
• Map incumbent POS/back-office vendors at top 20 QSR chains (Context: Global Payments enterprise go-to-market)

Next 60-90 Days
• Present restaurant domain strategy to Global Payments leadership (Context: establish yourself as the restaurant expert, not just a new hire)
• Identify first lighthouse restaurant account for platform-economics proof point

CoS RECOMMENDATIONS:
• ↗ Harri: New CRO hire (Section 3) + 3 profile views = re-engagement window. Act this week.
• ↘ Foods Connected: 14d wait is normal. Do not over-contact. Trust the process.
• ⚠️ STRATACACHE context: no new signals this cycle. The June debt-sale risk has passed.
```

**Sources:**
- `day_ahead` — calendar events with prep context (CALENDAR sub-section)
- Passive email intelligence sent loops — outbound awaiting response (EMAIL sub-section)
- `loops_and_obligations` — overdue + due-today + this-week loops (OPEN LOOPS sub-section)
- `w2_intelligence` + `opportunity_board` — active pipeline (ACTIVE OPPORTUNITIES sub-section)
- `weekly_plan_focus` — this week's outcomes (WEEKLY GOALS sub-section)
- `this_week_priorities` (RB 9.79) — near-term (2-7 day) calendar deliverables, loops
  closing this week, upcoming earnings calls, active interviews (THIS WEEK sub-section).
  Distinct from PREP REQUIRED below — this is *awareness* of what's coming, not prep-time.
- `this_month_priorities` (RB 9.83) — strategic priorities landing in the next 8-30
  days: loop targets beyond this week's horizon, upcoming earnings calls in the
  30-day window, and longer-running (`extras.evidence_age_days >= 14`)
  active-opportunity-pipeline items (THIS MONTH sub-section). Distinct from THIS WEEK
  (2-7 days) and Section 5 Horizon Watch (31-90 days, not yet actionable).
- `upcoming_preparation_requirements` (RB 9.77) — prep-time estimates bucketed by
  horizon (Within 7 Days / Within 30 Days), with `extras.prep_minutes` and
  `extras.required_inputs` (PREP REQUIRED sub-section)
- `learned_patterns` (RB 9.84) — recurring preparation-time patterns detected from
  a rolling history of `upcoming_preparation_requirements` (categories: Interview
  prep, Earnings/board review, Sales/pipeline review). Surfaces once a category has
  recurred on 3+ tracked days, with the mode `extras.prep_minutes` and
  `extras.occurrences`/`extras.days_tracked` (LEARNED PATTERNS sub-section).
  Distinct from PREP REQUIRED above — this is the system *learning* a recurring
  pattern across days, not estimating prep time for a single upcoming occurrence.
- `strategic_risks` (RB 9.78) — synthesized risks: weekly-plan flags, competitive
  escalations, relationship deterioration (FROZEN/COLD + open loops), scheduling/
  operational-debt pressure (RISKS sub-section)
- `pending_mutations` (RB 9.70) — proposed relationship interactions
  (`claim_status: "proposed"`) older than 12h, awaiting confirmation via
  `confirmProposal` (`kind="relationship"`) (PENDING CONFIRMATIONS sub-section)
- `cos_today` + `decision_layer` — CoS synthesis and push-backs (CoS RECOMMENDATIONS sub-section)
- `decision_layer` + `opportunity_board` — explicit pending decisions with stakes and deadline (DECISIONS REQUIRED sub-section). Never manufactured from ambiguity — only when a real decision is in `decision_layer` or is inferable from `opportunity_board` state.
- CoS synthesis of `opportunity_board`, `decision_layer`, `watchlist_intelligence`, `cos_today` — specific time-bounded actions by horizon (RECOMMENDED ACTIONS sub-section). Every action must trace to a named signal or known context item. "Continue X" without a named signal = failure.

**Format rules:**
- **Fourteen sub-sections:** CALENDAR | EMAIL | OPEN LOOPS | ACTIVE OPPORTUNITIES | WEEKLY GOALS |
  THIS WEEK | THIS MONTH | PREP REQUIRED | LEARNED PATTERNS | RISKS | PENDING CONFIRMATIONS |
  CoS RECOMMENDATIONS | DECISIONS REQUIRED | RECOMMENDED ACTIONS
- **Dot-connecting mandate:** every signal that enters Section 4 must pass through Signal → Why It Matters to You (role-specific) → Implication → Action. A signal reported without a named connection to Todd's current role, opportunity, or relationship context = rendering failure.
- Each item: action-oriented, specific, with timing rationale
- **THIS WEEK / THIS MONTH / PREP REQUIRED / LEARNED PATTERNS / RISKS / PENDING CONFIRMATIONS**:
  if the section's only item has a title starting with "No " or "Learned patterns:
  insufficient history" (e.g. "No near-term (this week) priorities detected", "No
  strategic (this month) priorities detected", "No recurring preparation patterns
  detected", "No new risks detected"), render that item's `summary` as a single line
  and nothing else — same green-board pattern as Section 2/3 headline fallbacks. Do
  not omit the sub-section header even when empty — the negative confirmation IS the
  content.
- **CoS RECOMMENDATIONS** is the editorial close — the model's judgment layer:
  - ↗ = strengthen this relationship/opportunity
  - ↘ = pull back / don't over-contact
  - ⚠️ = risk flag or push-back against user assumption
- Push-backs are required. A CoS who only validates is not doing the job.
- This section is the brief's payoff — the user should be able to act immediately after reading it.

---

## Section 5 — Horizon Watch (30-90 Days)

```
🔭 Horizon Watch (30-90 Days):

• Toast (TOST) reports Q2 earnings ~Aug 6 (54 days away). Watch for: ARR growth,
  NRR, customer count, technology roadmap, margin signals.
  → No action required yet — revisit as the report date approaches.

• Horizon watch: STRATACACHE — RFP-category competitive vulnerability
  (estimated horizon: 3 months). Monitor for escalation; no action required yet.
```

If no items: render the single negative-confirmation line from `horizon_watch`
("No developments in the 30-90 day horizon detected") — same green-board
pattern as other RB 9.7x-9.8x sections.

---

## CoS Bottom Line (Mandatory Close)

Rendered after Section 5, before the "loop it" close. Always present — no API source required, pure CoS synthesis.

**Format:**
```
📌 CoS Bottom Line

[3-5 sentences. Must name ≥2 specific signals from today's brief with source/date. Must name 
Todd's current named role or opportunity (not generic "your current situation"). Must name 
≥1 concrete implication or recommended action. Written in the CoS's own voice and judgment — 
not a summary of what was already said.]
```

**Example (passes):**
> Macro cost pressure signaled by the delayed U.S.-Iran talks (Reuters, Jun 18) and accelerating operator consolidation signals from the Nation's Restaurant News survey (Jun 17) are converging at the precise moment you are entering Global Payments. This validates a software-funded payments pitch over a traditional SaaS narrative — CFO scrutiny of subscription spend creates an opening, not a headwind. Your first 30 days should establish you as the restaurant domain expert in the room: the person who can name the top 20 enterprise QSR operators and their incumbent technology stack before anyone else on the team.

**Example (fails):**
> The market environment is favorable and signals suggest strong positioning. Focus on your open loops and continue developing your strategic thesis. (generic — no named signals, no named opportunity, no concrete action)

**Failure condition:** A CoS Bottom Line that could have been written for any executive in any industry — with no named signals, no named current context, and no specific implication — is a rendering failure equal to omitting the section entirely.

**Sources:**
- `horizon_watch` (RB 9.82) — important developments not requiring action
  yet: pre-earnings alerts 31-90 days out (`earnings_intelligence`,
  `extras.alert_window == "90 DAYS"`) and RFP-category competitive
  vulnerabilities with `extras.estimated_horizon_months <= 3` and
  `disposition == "monitor"` (not yet escalated to Section 4 RISKS).

**Format rules:**
- One section, no sub-sections.
- Each item: title + summary + "no action required yet" framing — these are
  *awareness* items, distinct from Section 4 RISKS (which require attention)
  and Section 4 THIS WEEK (which is 2-7 days, actionable).
- If the section's only item has a title starting with "No " (e.g. "No
  developments in the 30-90 day horizon detected"), render that item's
  `summary` as a single line and nothing else. Do not omit the section
  header even when empty — the negative confirmation IS the content.

---

## Sections to SKIP (always render silently — never output to user)

| Section | Why skip |
|---|---|
| `source_trust_table` | Covered by Intelligence Proof Block |
| `source_gap_declarations` | Inline [STALE] labels handle this |
| `suppressed_today` | Count shown in Section 1 delta close |
| `decision_queue` | Covered by Section 4 CoS Recommendations |
| `active_knowledge_assets` | Mount directives — session-load only |
| `known_state_reminders` | Render inline with Section 4 only if overdue |
| `source_audit` | Internal — never render |
| `intelligence_cycle_continuation` | Internal pipeline checklist |
| `recommended_actions_structured` | Covered by Section 4 |
| `graph_mutation_log` | Summary in Section 1 delta close only |
| `executive_summary` | Scaffold — content comes from Section 1 |
| `five_things_today` | Covered by Section 4 My Priorities |
| `cos_judgment` | Covered by Section 4 CoS Recommendations |

---

## Pass/Fail Criteria

### Part 1 (`getDailyBrief`) — Document 1: Intelligence Brief

A Part 1 response **passes** if (RB 10.7 — 13 sections):
- [ ] Section 1 What Changed: named bullet facts from today, including `personal_intelligence_delta` email/SMS/calendar/mutation deltas
- [ ] Sections A–D Headlines: all rendered items have `[title](url)` direct article links; no evergreen/thematic sentences
- [ ] Sections A–D: 5–7 headlines per section (fewer only with explicit quiet-cycle explanation)
- [ ] Section D+ Newsletter Inbox: present when `newsletter_intelligence` non-empty; each newsletter shows ≥3 article links
- [ ] Section D+ silently absent when `newsletter_intelligence` is empty (no "no newsletters" message)
- [ ] Section E Earnings/Corporate: present or one-sentence "none this cycle"
- [ ] Section F Watchlist: scan count + material items + exactly ONE no-change sentence
- [ ] Section G/H: state + NEW/UNCHANGED/STALE⚠ labels on every item
- [ ] Section H+ Identity Confirmations Needed: present when `identity_match_candidates` non-empty, silently absent when empty; never confirmed/rejected without an explicit operator reply
- [ ] Section I: 3–5 signals, ≥2 named evidence each, zero second-person editorial
- [ ] Section J Proof Dashboard: personal delta table (Today/Yesterday/Delta) + news scan + Source Health, LAST position
- [ ] No recommendations, priorities, or next actions anywhere in Part 1
- [ ] No grounding/freshness/confidence/disposition label text in output
- [ ] Ends with the Part 2 offer

A Part 1 response **fails** if:
- [ ] Section 1 omits `personal_intelligence_delta` items when payload is non-empty
- [ ] Section D+ `newsletter_intelligence` silently omitted when payload non-empty
- [ ] Newsletter item rendered without article links (source name only = failure)
- [ ] Section J rendered as old Source|Status|Findings table instead of delta table
- [ ] "No personal intelligence available" or checkmarks without counts in Section J
- [ ] Any headline missing its direct article link, source, or date
- [ ] News sections contain theme paragraphs instead of actual sourced headlines
- [ ] **Any recommendation, priority, or "next action" appears in Part 1** — primary architectural failure
- [ ] Executive Dashboard appears in Part 1
- [ ] The Part 2 offer is missing entirely

**RETRIEVAL GATE VIOLATION SIGNALS (RB-DEFECT-049):**
These patterns prove the GPT improvised without calling getDailyBrief. Any one of these
is an automatic Part 1 FAIL and a hard stop:
- "Sources scanned: Restaurant technology and payments news" (name instead of number)
- "This supports your thesis" without a named OBSERVED signal with source and date
- "Continue refining your [X] narrative" as a recommendation
- "Continue developing your [X] thesis" as a recommendation
- Any Proof Block count that could only be known from API data appearing suspiciously round (20, 50, 100) rather than reflecting the actual count
- Section 1 PERSONAL is absent when Foods Connected or Global Payments are active

### Part 2 (`getDailyBriefPart2`) — Document 2: Daily Brief

A Part 2 response **passes** if:
- [ ] Opens with Section 0 Executive Dashboard (What Changed / Top 3 Priorities / Risks / Opportunities)
- [ ] Every signal in Part 2 passes dot-connecting filter: Signal → Why It Matters to You (role-specific) → Implication → Action
- [ ] Every priority in Section 4 traces to a named signal from Part 1
- [ ] Section 3 covers ALL mandatory watchlist entities — entities with signals get full treatment; entities without signals are in a single rollup sentence
- [ ] Section 3 badges high-importance signals (🏢 ACQUISITION / 💰 FUNDING / 👤 EXEC HIRE)
- [ ] Section 4 has all 14 sub-sections: CALENDAR / EMAIL / OPEN LOOPS / OPPORTUNITIES / GOALS /
      THIS WEEK / THIS MONTH / PREP REQUIRED / LEARNED PATTERNS / RISKS / PENDING CONFIRMATIONS /
      CoS RECOMMENDATIONS / DECISIONS REQUIRED / RECOMMENDED ACTIONS
- [ ] RECOMMENDED ACTIONS organized by: Today / This Week / Next 30 Days / Next 60-90 Days
- [ ] Section 4 CoS Recommendations includes at least 1 push-back or contrarian position (not "continue X")
- [ ] Section 5 (Horizon Watch) is present, even if only the negative-confirmation line
- [ ] CoS Bottom Line is present: ≥2 named signals + named current role/opportunity + ≥1 concrete implication
- [ ] No grounding/freshness/confidence/disposition label text appears in output
- [ ] Does not re-render Part 1 content

A Part 2 response **fails** if:
- [ ] Executive Dashboard is absent from the opening of Part 2
- [ ] Any signal reported in Part 2 without "Why it matters to you" connection to Todd's role/context
- [ ] Any priority says "continue X" or "refine your thesis" without citing a named signal from Part 1
- [ ] Any mandatory watchlist entity is absent from Section 3 (negative reporting omitted)
- [ ] STRATACACHE or any mandatory tech vendor missing from Section 3
- [ ] Section 4 CoS Recommendations only validates — no push-back or contrarian position
- [ ] Section 4 is missing any of its 14 sub-sections
- [ ] RECOMMENDED ACTIONS absent or not organized by time horizon
- [ ] Section 5 (Horizon Watch) is missing entirely (including its negative-confirmation form)
- [ ] CoS Bottom Line is absent, generic, or contains no named signals from today's brief
- [ ] CoS Bottom Line could have been written for any executive in any industry
- [ ] More than 3 sections show raw API field names (grounding, freshness, etc.)
- [ ] Part 2 opens by re-rendering Part 1 intelligence instead of synthesizing from it

---

## The Indispensability Test

> "If Todd did not receive today's briefing, would he be materially disadvantaged?"

The answer must be **Yes.**

The brief passes when:
1. I learned something important that I did not know before opening it.
2. I know what changed since yesterday.
3. I know why it matters.
4. I know what action to take.
5. I am confident the system searched comprehensively (negative reporting confirms this).
6. I trust that important developments — like STRATACACHE being sold — were not missed.

---

## The Newspaper Test

> "Am I rushing to the door to get the newspaper?"

A newspaper is valuable because it tells the reader what **changed**, surfaces **unexpected** developments, and provides **discovery** — not recaps of what they already know.

If every fact in the brief was already known to the user before opening it: **the brief failed.**
