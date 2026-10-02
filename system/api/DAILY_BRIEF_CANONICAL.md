# Daily Brief Canonical Spec — ABANDONED (marked 2026-08-25)

**Status:** NOT authoritative, not live, not a KB file uploaded to any Custom GPT surface.

Created 2026-06-26 as one half of a planned split (see `DAILY_BRIEF_CANONICAL_TEMPLATE.md`'s
corrected header for the full history). The split never took hold for Part 2 — the team kept
developing the real spec in `DAILY_BRIEF_CANONICAL_TEMPLATE.md` instead, and this file was never
touched again after its creation day. It also still names `getRenderedDailyBrief`, an operation
retired from the live schema (0 calls, ever) — content here has not been kept in sync with reality
for at least 7 weeks. Every other live KB file cites `DAILY_BRIEF_CANONICAL_TEMPLATE.md`, not this
file, as the Part 2 authority. Kept on disk for now rather than deleted — Todd's call whether to
remove it entirely; do not cite it or edit it as if it were live. See
[[rb_kb_authority_reconciliation_2026_08_25]].

---

**Original content below, preserved as-is (stale, not maintained):**

---

> **RB 10.7 — PRE-RENDERED BRIEF ARCHITECTURE**
> The Daily Brief is now pre-rendered at 5am by `render_daily_brief.py`.
> **GPT role:** Call `getRenderedDailyBrief` → display the `markdown` field verbatim. Do not generate, reword, or add to it.
> The rendering rules below now govern the Python renderer, not GPT output. The GPT's only rendering job is verbatim display.
> If `getRenderedDailyBrief` fails → output: "Daily Brief not available — type retry once the server is running." Do not fall back to `getDailyBriefPart2`.

---

---

## The Daily Brief Is a Chief of Staff Memo, Not a Newspaper

The Daily Brief answers one question: **So what? What should I do?**
It synthesizes from the Intelligence Brief (Part 1) -- it does not re-render raw intelligence.
It contains priorities, recommendations, risks, calendar prep, and CoS judgment.

**Every priority must trace to a signal from Part 1.**
Format: Signal (named fact from Intelligence Brief) -> Implication -> Action.
"Refine your thesis" without citing a specific signal = rendering failure.

---

## The Dot-Connecting Mandate

**This is the primary quality test for Part 2.** A brief that reports facts is a newspaper. A brief that connects facts to *this person's specific situation* is a CoS memo.

Every macro signal, industry development, or watchlist mutation that enters Part 2 must pass through this filter before it is rendered:

```
Signal:          [Named fact with source and date from Part 1]
Why it matters   [Specific to Todd's role, current opportunities, relationships, or stated priorities --
to you:           never generic. If you cannot name the connection, the signal does not belong in Part 2.]
Implication:     [Risk, opportunity, or timing consequence -- specific, not abstract]
Action:          [Named next step. "Continue X" or "refine your thesis" = failure.]
```

**Role context is mandatory.** The brief always knows Todd's current context from `opportunity_board`, `active_threads`, and `w2_intelligence`. Every Part 2 signal must be filtered through that context. Example failures:

- "Middle East uncertainty exists." → failure (no connection to Todd's world)
- "Operators want simplification." → failure (observation without connection)
- "AI is maturing." → failure (generic trend without personal implication)

Example passes:

- "U.S.-Iran talks create fuel cost pressure → enterprise restaurant CFOs will increase SaaS scrutiny → software-funded payments thesis becomes more compelling → specific messaging opportunity: 'eliminate monthly SaaS costs, align spend to transactions.'"
- "Operator simplification signals are accelerating → you are entering Global Payments at peak receptivity for bundled economics → validate platform thesis in onboarding conversations."

**The CoS Bottom Line** (mandatory close before "loop it") is the synthesis of Part 2: 3-5 sentences that answer "What does all of this mean for me, right now?" It must name specific signals, name the user's specific context, and name at least one concrete implication or action. Generic observations = failure.

---

## Canonical Section Order (Part 2)

```
1. Section 0: Executive Dashboard    (opens Part 2 -- CoS synthesis of current state)
2. Section 3: Technology Radar       (watchlist with full treatment + competitive opportunities)
3. Section 4: My Priorities          (12 sub-sections -- all evidence-traced, dot-connected)
4. Section 5: Horizon Watch          (30-90 day awareness)
5. CoS Bottom Line                   (mandatory close -- synthesized paragraph, role-specific)
```

Close: `Say "loop it" to open tracking loops for the top items.`

---

## Section 0 -- Executive Dashboard (Part 2 Opener)

This section answers: "What is happening in my world right now?"
Always populated from API data. Never synthesized from memory. Never open with raw intelligence.

```
Executive Dashboard -- 2026-06-16

Status
Career opportunities:     2 active (Global Payments -- offer stage; Foods Connected -- final round)
Decisions pending:        2
Meetings this week:       4 (Tammy 10AM today; Vik tomorrow; Scott Friday; Hospitality Table Thu)
Emails requiring action:  0
Relationship follow-ups:  2 overdue
Material watchlist changes: 3 escalated (Global Payments, Worldpay, Genius)

What Changed Since Yesterday
[!] CRITICAL: 2nd Stage Foods Connected interview shows CANCELLED in calendar.
              Clarify status with Sarah immediately -- may be reschedule or withdrawal.
- No new inbound from Global Payments (41 days in pipeline).
- Watchlist: 3 escalated entities.
- Today: preparation and execution day -- no inbound fires.

Top Three Priorities
1. Clarify Foods Connected interview status (email or call Sarah today).
2. Build Foods Connected presentation (if interview still active).
3. Complete Global Payments diligence items (PTO, health insurance, commission structure).

Risks
- Foods Connected interview cancellation status is unresolved -- act today.
- Presentation preparation window is narrowing.
- Multiple active opportunities creating context switching.

Opportunities
- Quiet inbox -- no inbound fires requiring reaction.
- Vik Devjee meeting tomorrow: natural venue to explore restaurant tech alignment.
```

**Sources:**
- Career opportunities: `opportunity_board` (ACTIVE + WAITING items)
- Decisions pending: `decision_layer` count
- Meetings this week: `day_ahead` + `this_week_priorities`
- Emails requiring action: `email_intelligence_harvest` count
- Relationship follow-ups: `loops_and_obligations` overdue count
- Material watchlist changes: `what_changed_since_yesterday` watchlist escalations
- What Changed: `what_changed_since_yesterday` + `five_things_today` calendar changes
- Top Three Priorities: `five_things_today` top 3, synthesized as action-oriented sentences
- Risks: `strategic_risks` + CoS synthesis from `opportunity_board` state
- Opportunities: `opportunities_detected` + quiet-inbox state from `email_intelligence_harvest`

**Critical Calendar Alert Rule:**
If `what_changed_since_yesterday` or `five_things_today` contains a cancelled event matching
an active `opportunity_board` thread name or company:
- FIRST item in "What Changed Since Yesterday" with prefix `[!] CRITICAL:`
- Priority #1 or #2 in "Top Three Priorities"
A cancelled interview is potentially disqualifying. Never bury it in a routine list.

**Format rules:**
- Status block: counts only (numbers, not commentary)
- What Changed: bullets, most critical first
- Top Three Priorities: numbered, action-oriented, named next step
- Risks: career-specific [Opportunity] -> Risk -> Mitigation format first
- Opportunities: bullets; quiet-cycle reframe ("no inbound fires" = preparation time)
- If all items zero/clean: "No material changes. Today is a clear preparation day."

---

## Section 3 -- Technology Radar

Full treatment for watchlist entities with signals. Rollup for all no-change entities.

```
Technology Radar

[FUNDING] SoundHound raises $50M Series C
Company: SoundHound | Signal: funding_round | Date: Jun 8
Why it matters: Voice AI category now better-capitalized. Deployment velocity will increase.
Implication: Operators evaluating voice AI will have more vendor options.
-> Connected: Active interest in voice AI from McDonald's franchise operators (your network)

[EXEC HIRE] Harri names new CRO from Workday
Company: Harri | Signal: executive_hire | Date: Jun 7
Why it matters: Enterprise SaaS hire = shift to enterprise go-to-market.
Implication: Re-evaluate Harri relationship. New CRO may need network introductions.

No material updates this cycle:
Scanned 18 additional entities. (STRATACACHE, Coates Group, Qu, Agilysys, NCR, Oracle Hospitality,
Olo, Lightspeed, Revel, Xenial, SpotOn, Givex, Presto, Bopple, Tacit, Omnivore, Apicbase, Craftable)
```

**Format rules:**
- Entities with signals: [BADGE], Company, Signal, Date, Why it matters, Implication, -> Connected (if applicable)
- Signal strength: HIGH (acquisition, bankruptcy, funding >$50M, major exec departure) | MEDIUM (exec hire, customer win, product launch) | MONITOR (partnership, earnings, general)
- No-signal entities: SINGLE rollup sentence with full name list -- never per-entity "no signals" lines
- Sort by signal_weight descending

**Mandatory Coverage -- Restaurant Technology:**
PAR Technology | Toast | NCR Voyix | Oracle Hospitality | Square | SpotOn | Qu | Revel | Samsung Koomi | GK Software | Punchh | Paytronix | Thanx | SessionM | PAR Loyalty | Blackbird | Incentivio | Global Payments | Shift4 | Fiserv | Adyen | Worldpay | FreedomPay | Stripe | Restaurant365 | Crunchtime | QSR Automations | MarginEdge | MarketMan | xtraCHEF | ClearCOGS | Tenzo | NomadGo | Barmetrix | STRATACACHE | Coates Group | Acrelec | Mood Media | Raydiant | Spectrio | Nanonation | Korbyt | Scala | Olo | Lunchbox | Checkmate | Deliverect | ChowNow | SoundHound | Presto | ConverseNow | Kea | Valyant | Hi Auto | Agot | Berry AI | Everseen | Veesion | Harri | Legion | Fourth | HotSchedules | Schoox | WorkJam | Miso Robotics | Serve Robotics | Bear Robotics | Foods Connected | Genius

**Mandatory Coverage -- Restaurant Brands:**
McDonald's | Starbucks | Yum Brands | Restaurant Brands International | Chipotle | Wendy's | Domino's | Darden | Inspire Brands | Chick-fil-A | Dutch Bros | Sweetgreen | CAVA | Wingstop

**Competitive Opportunity Watchlist** (render immediately after Technology Radar):
Source: `competitive_vulnerability_watchlist`. Group by tier: high_risk -> emerging -> watch.
Show: entity name, Opportunity Score, Potential Categories, Estimated Horizon, flag rfp_detected.
If empty: "No elevated vulnerability signals detected this cycle."

Sources: `restaurant_technology_headlines`, `watchlist_intelligence`, `strategic_industry_signals`, `competitive_vulnerability_watchlist`

---

## Section 4 -- My Priorities (12 Sub-Sections)

**Evidence Traceability Rule (RB-DEFECT-055):**
Every priority must name the specific signal from Part 1 that triggered it:
```
Signal:      Global Payments messaging emphasizes software-enabled commerce (Reuters, Jun 16)
Implication: Alignment with their public narrative confirmed -- supports thesis
Action:      Refine enterprise pitch with this angle before next call with Mike
```
"Refine your thesis" or "Continue developing your positioning" without a named signal = rendering failure.

### CALENDAR
Each event merges with relationship context. Show: time, name, objective, relationship depth/last contact, prep requirement.
Never show a calendar event as title only. Always answer:
- Who is this person?
- What is the objective?
- What do I need to do before I walk in?

Sources: `day_ahead`, `relationship_operational_signal_review`, `last_24h_relationship_signals`

### EMAIL
Outbound awaiting response. Show: subject, sent date, age, category, recipient, next action.
Sources: passive email intelligence sent loops

### OPEN LOOPS
When `opportunity_board` has ACTIVE or WAITING items: group BY OPPORTUNITY FIRST.
```
Global Payments -- Offer stage
Pending:
- PTO negotiation
- Health insurance / Enbrel coverage review
- Commission structure clarification
```
Then show remaining overdue loops chronologically.
Never render loops as an undifferentiated list when career opportunities are active.

Sources: `loops_and_obligations`

### ACTIVE OPPORTUNITIES
Each active opportunity from `opportunity_board`. Show: status, days in pipeline, primary contact, next step.
Sources: `w2_intelligence`, `opportunity_board`

### WEEKLY GOALS
This week's outcomes.
Sources: `weekly_plan_focus`

### THIS WEEK
Near-term (2-7 day) calendar deliverables, loops closing this week, upcoming earnings calls.
If no items: "No near-term priorities detected." (single line -- do not omit header)
Sources: `this_week_priorities`

### THIS MONTH
Strategic priorities 8-30 days out. Earnings calls 8-30 days out. Longer-running pipeline items.
If no items: "No strategic priorities detected." (single line)
Sources: `this_month_priorities`

### PREP REQUIRED
Prep-time estimates by horizon (Within 7 Days / Within 30 Days).
If no items: single line.
Sources: `upcoming_preparation_requirements`

### LEARNED PATTERNS
Recurring preparation-time patterns from rolling history. Surfaces when a category recurs 3+ tracked days.
If fewer than 3 days: "Learned patterns: insufficient history."
Sources: `learned_patterns`

### RISKS
Career-specific risks FIRST in [Opportunity] -> Risk -> Mitigation format (CoS judgment -- not system flags).
Then auto-detected risks (FROZEN contacts, overdue loops).
"Continue X" is never an acceptable mitigation. Name the specific action.
Sources: `strategic_risks`, `opportunity_board`

### PENDING CONFIRMATIONS
Proposed relationship interactions older than 12h awaiting confirmation.
Sources: `pending_mutations`

### DECISIONS REQUIRED
Explicit decisions the user must make, not tasks. Format: Decision, Stakes, Deadline, Options.
Example: "Accept Global Payments offer (Stakes: comp, equity, role scope | Deadline: Friday | Options: accept / negotiate / decline)"
Omit if none. Never manufacture decisions from ambiguity -- only render when `decision_layer` or `opportunity_board` shows a pending decision.
Sources: `decision_layer`, `opportunity_board`

### RECOMMENDED ACTIONS
Specific, time-bounded actions organized by horizon. Every action must trace to a named signal or known context item.
```
Today
- [action] (Signal: [source])

This Week
- [action] (Signal: [source])

Next 30 Days
- [action] (Context: [role/opportunity/relationship])

Next 60-90 Days
- [action] (Context: [strategic initiative])
```
Never use "continue X", "refine Y", or "explore Z" without naming the specific next action and who it involves.
Sources: CoS synthesis of `opportunity_board`, `decision_layer`, `watchlist_intelligence`, `cos_today`

### CoS RECOMMENDATIONS
The editorial judgment layer -- what the CoS believes, not just what the data says. Must include at least one push-back or contrarian position. Must reference at least one specific signal from Part 1.
- [up] = strengthen this relationship/opportunity
- [down] = pull back / don't over-contact
- [!] = risk flag or push-back against user assumption

A CoS who only validates is not doing the job. Push-backs are required.
Sources: `cos_today`, `decision_layer`

---

## CoS Bottom Line (Mandatory Close)

This is the final section of Part 2, rendered after Section 5 Horizon Watch and before the "loop it" close.

It is a synthesized paragraph -- 3-5 sentences maximum -- that answers: **"What does all of this mean for me, right now?"**

**Requirements:**
- Names at least two specific signals from today's brief (with sources)
- Names the user's specific current context (current role/opportunity by name)
- Names at least one concrete implication or recommended action
- Does not re-list items already covered -- synthesizes across them
- Written as the CoS's own voice and judgment, not a summary

**Example (passes):**
> "Macro cost pressure from the Middle East and accelerating operator simplification signals are converging at the precise moment you are entering Global Payments. This validates the software-funded payments thesis rather than the typical enterprise pitch -- CFO scrutiny of SaaS spending creates an opening, not a headwind. The Aman Narang hiring signal at Toast suggests the enterprise segment is accelerating; your first 30 days at Global Payments should establish you as the restaurant domain expert in the room, not the new person still learning the product."

**Example (fails):**
> "The market environment is favorable and you are well-positioned. Focus on your priorities and continue developing your thesis." (generic -- no named signals, no named context, no action)

**Failure = rendering a CoS Bottom Line that could have been written for any executive in any industry.**

---

## Section 5 -- Horizon Watch (30-90 Days)

```
Horizon Watch (30-90 Days)

- Toast (TOST) reports Q2 earnings ~Aug 6 (54 days away).
  Watch for: ARR growth, NRR, customer count, technology roadmap, margin signals.
  No action required yet -- revisit as date approaches.

- STRATACACHE -- RFP-category competitive vulnerability (estimated: 3 months).
  Monitor for escalation. No action required yet.
```

If no items: "No developments in the 30-90 day horizon detected." (single line -- do not omit header)

Sources: `horizon_watch` (pre-earnings 31-90 days out, RFP vulnerabilities with horizon <= 3 months)

---

## Sections to SKIP (never output to user)

| Section | Why |
|---|---|
| `source_trust_table` | Covered by Intelligence Proof Block (Part 1) |
| `source_gap_declarations` | Inline [STALE] labels handle this |
| `suppressed_today` | Count shown in Part 1 delta close |
| `decision_queue` | Covered by CoS Recommendations |
| `active_knowledge_assets` | Mount directives -- session-load only |
| `known_state_reminders` | Render inline with Section 4 only if overdue |
| `source_audit` | Internal -- never render |
| `intelligence_cycle_continuation` | Internal pipeline checklist |
| `recommended_actions_structured` | Covered by Section 4 |
| `graph_mutation_log` | Summary in Part 1 delta close only |
| `executive_summary` | Scaffold -- content comes from Section 0 |
| `five_things_today` | Covered by Section 4 My Priorities |
| `cos_judgment` | Covered by Section 4 CoS Recommendations |

---

## Pass/Fail Criteria

### PASSES if:
- [ ] Opens with Section 0 Executive Dashboard (Status / What Changed / Top 3 Priorities / Risks / Opportunities)
- [ ] Every signal in Part 2 passes through: Signal → Why It Matters to You (role-specific) → Implication → Action
- [ ] Every Section 4 priority traces to a named signal from Part 1
- [ ] Section 3 covers ALL mandatory watchlist entities (signals get full treatment; no-change = one rollup)
- [ ] Section 4 has all 12 sub-sections (including DECISIONS REQUIRED and RECOMMENDED ACTIONS)
- [ ] RECOMMENDED ACTIONS organized by today / this week / next 30 days / next 60-90 days
- [ ] Section 4 CoS Recommendations includes at least 1 push-back or contrarian position
- [ ] Section 5 Horizon Watch present (even if only the negative-confirmation line)
- [ ] CoS Bottom Line present as final section: names >=2 signals, names user's current context, names >=1 concrete implication
- [ ] Does not re-render Part 1 intelligence content

### FAILS if:
- [ ] Section 0 Executive Dashboard absent from Part 2 opening
- [ ] Any signal in Part 2 is reported without "Why it matters to you" connection to user's role/context
- [ ] Any priority says "refine your thesis" or "continue X" without citing a named signal and specific next action
- [ ] Any mandatory watchlist entity absent from Section 3
- [ ] Section 4 CoS Recommendations only validates -- no push-back
- [ ] Section 4 missing any of its 12 sub-sections
- [ ] RECOMMENDED ACTIONS absent or not organized by time horizon
- [ ] Section 5 Horizon Watch missing (including negative-confirmation form)
- [ ] CoS Bottom Line absent, generic, or contains no named signals
- [ ] Part 2 opens by re-rendering raw intelligence from Part 1
- [ ] CoS Bottom Line could have been written for any executive in any industry (zero personal specificity)

---

## The Indispensability Test

> "If Todd did not receive today's Daily Brief, would he be materially disadvantaged?"

The answer must be **Yes.**

The brief passes when:
1. I know what changed since yesterday and **why it matters to my specific role and situation**.
2. I know my top 3 priorities with specific named next steps and the signal behind each.
3. I know what meetings need preparation and how.
4. I know the risks and opportunities in my current pipeline, with specific signals backing each.
5. I know what decisions I need to make and what the stakes are.
6. I have specific recommended actions organized by today / this week / 30 / 60-90 days.
7. I received a CoS Bottom Line that synthesizes the day into a judgment I could not have written myself.
8. I trust the system did not miss anything important.

**The anti-test:** If the CoS Bottom Line could appear in any executive's briefing without changing a word, the brief has failed.
