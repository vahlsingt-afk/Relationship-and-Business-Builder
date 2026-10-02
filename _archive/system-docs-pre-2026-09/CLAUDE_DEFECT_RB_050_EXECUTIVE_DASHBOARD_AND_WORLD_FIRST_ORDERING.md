# RB-DEFECT-050: Executive Dashboard — World-First Ordering and Remaining CoS Gaps

**Date filed:** 2026-06-16
**Filed by:** Todd
**Status:** Active — implementation in progress (RB 9.92)
**Priority:** Critical
**Category:** Daily Brief / Chief of Staff Rendering
**Builds on:** RB-DEFECT-049 (Intelligence Proof Block, which improved score from 3/10 → 5/10)

---

## Score

5/10 after RB 9.91. Target: 8.5–9/10.

Improved: calendar awareness (8/10), email awareness (7/10), personal context (7/10).

Still failing: intelligence proof (5/10), fresh intelligence (4/10), actionability (6/10),
prioritization (6/10), CoS feel (6/10), trust building (5/10).

---

## Root Cause: Wrong Section Ordering

The current template leads with **news** (Section 1 = Executive Summary of industry
headlines), then market intelligence, then radar. The user's world (career, calendar,
open loops, risks, relationships) appears in Part 2 Section 4 — or not at all.

A real Chief of Staff does not open a briefing with the newspaper. They open with:
1. What is happening in your world right now?
2. What changed since yesterday?
3. What do you need to do today?
4. What are the risks?

Then news, as context.

The current template is the inverse of this. Every Part 1 brief leads with news and
buries personal context in Part 2. This is the structural reason the brief scores 5/10
even when the API data is correct.

---

## Six Specific Defects

### Defect 1 — No Open Loop Management (executive-level)

**Expected:**
```
Open Loops

Global Payments
Status: Offer received. Waiting on compensation review.
Pending:
• PTO negotiation
• Health insurance / Enbrel coverage review
• Commission structure review

Foods Connected
Status: Final-stage candidate.
Pending:
• Wednesday coaching call with Sarah
• Presentation creation
• Clarify nature of McDonald's engagement
• Prepare trust narrative
```

**Actual:** Flat list of 21 loops chronologically ordered, no grouping by opportunity.

**Root cause:** `loops_and_obligations` renders chronologically. No career-opportunity
grouping. No pending-items breakdown per major thread.

**Fix:** When `opportunity_board` has ACTIVE/WAITING items, group loops by opportunity
thread before showing chronological overdue list.

---

### Defect 2 — No Career-Specific Risk Assessment

**Expected:**
```
Risks

Foods Connected
Risk: Presentation could become too resume-oriented.
Mitigation: Lead with trust, enterprise execution, and McDonald's operating realities.

Global Payments
Risk: Excitement of offer may cause premature commitment before benefits review.
Mitigation: Complete compensation and healthcare diligence first.

Personal Capacity
Risk: Multiple opportunities creating context switching.
Mitigation: Time-block presentation work today.
```

**Actual:** Two auto-detected risks: relationship deterioration (1 frozen contact),
operational debt (14 overdue loops). Career-level risks are absent.

**Root cause:** `strategic_risks` is populated by rule-based detectors (FROZEN contacts,
overdue loops). CoS-level career risk synthesis is not in the rendering spec.

**Fix:** CoS RECOMMENDATIONS (Section 4) must derive career risks from `opportunity_board`
state and render them in [Opportunity] → Risk → Mitigation format.

---

### Defect 3 — No Per-Person Relationship Intelligence

**Expected:**
```
Relationship Intelligence

Tammy Billings — Today (10:00 AM)
Objective: Determine strategic fit and next steps.
Prep: She reached out via Bookings on Jun 12. No prior relationship depth.

Vik Devjee — Tomorrow
Context: [Company background + QSR synergies]
Prep: Review company background; likely overlap with restaurant tech experience.
```

**Actual:** Calendar events listed but not merged with relationship context from
`last_24h_relationship_signals` and `relationship_operational_signal_review`.

**Fix:** Day-ahead calendar items must be merged with relationship data: who is this
person, what is the objective, what prep is required.

---

### Defect 4 — News Not Connected to Active Objectives

**Expected:**
```
CarPlay ordering is emerging.
→ Why Todd cares: If joining Global Payments, dashboard commerce = another payment
surface Worldpay may need to support. Monitor for enterprise payments angle.
```

**Actual:** News presented as general industry interest, not filtered through
Todd's active opportunities and objectives.

**Fix:** Every news item in Section 1 and Section 2 requires a "Why Todd cares:"
line framed specifically in terms of: active career opportunities, watchlist companies
Todd is evaluating, relationship implications, or positioning.

---

### Defect 5 — No "What Changed Since Yesterday" as First Content

**Expected (first substantive section):**
```
What Changed Since Yesterday
• No new communications from Global Payments or Foods Connected.
• 2nd Stage Foods Connected interview shows cancelled in calendar — clarify immediately.
• Watchlist: 24 entity status changes (3 escalated, 21 relevant activity).
• Today is a preparation and execution day.
```

**Actual:** Delta exists in `what_changed_since_yesterday` (3 items) but is buried
or not rendered as the first substantive section before any news.

**Critical miss from today's data:** The 2nd Stage Commercial Director interview shows
as cancelled in the calendar delta. This is potentially the most important intelligence
in today's brief. It is not surfacing as a critical alert.

**Fix:** "What Changed Since Yesterday" must appear BEFORE news, after the Intelligence
Proof Block, as its own named section.

---

### Defect 6 — Missing Week and Month Horizons

**Expected:**
```
This Week
• Foods Connected coaching call (Wednesday)
• Presentation completion
• Vik Devjee meeting (tomorrow)
• Scott breakfast (Friday)

This Month
• Foods Connected final decision
• Global Payments onboarding decisions
• Advisory opportunities evaluation
```

**Actual:** `this_week_priorities` (8 items) and `this_month_priorities` (4 items)
exist in the data but are either buried in Part 2 or not rendered.

**Fix:** Week and month horizons must appear in every complete brief session.
Part 2 offer must explicitly name these sections so the user knows to request them.

---

## Structural Fix: Executive Dashboard (Section 0)

Insert before all current sections. Sources from existing fields.

```
Executive Dashboard — 2026-06-16

Status
Career opportunities:   2 active (Global Payments, Foods Connected)
Decisions pending:      2
Meetings this week:     4
Emails requiring action: 0
Relationship follow-ups: 2 (overdue)
Material watchlist changes: 3 escalated

What Changed Since Yesterday
⚠️ CRITICAL: 2nd Stage Foods Connected interview shows CANCELLED in calendar.
             Clarify status with Sarah immediately.
• No new inbound from Global Payments (41 days in pipeline).
• Watchlist: 3 escalated (Global Payments, Worldpay, Genius).
• Today: preparation and execution day — no inbound fires.

Top Three Priorities
1. Clarify Foods Connected interview status (email or call Sarah today).
2. Build Foods Connected presentation (if interview still active).
3. Complete Global Payments diligence items (PTO, health insurance, commission).

Risks
• Presentation preparation window is narrowing.
• Multiple active opportunities creating context switching.
• Foods Connected interview cancellation status is unresolved.

Opportunities
• Quiet inbox — no inbound fires requiring reaction.
• Vik Devjee meeting tomorrow: natural venue to explore restaurant tech alignment.
```

Sources:
- Status block: `opportunity_board` (count ACTIVE+WAITING), `decision_layer` (count),
  `day_ahead` (calendar events), `email_intelligence_harvest` (emails requiring action),
  `loops_and_obligations` (overdue relationship loops count), `what_changed_since_yesterday`
  (watchlist escalation count)
- What Changed: `what_changed_since_yesterday`, `five_things_today` (calendar change items)
- Top Three Priorities: `five_things_today` top 3 items, synthesized for actionability
- Risks: `strategic_risks` + `opportunity_board` CoS synthesis
- Opportunities: `opportunities_detected` + quiet-inbox state

---

## Critical Calendar Alert Rule

If `what_changed_since_yesterday` or `five_things_today` contains a cancelled calendar
event matching an active `opportunity_board` thread:

**⚠️ CRITICAL ALERT:** must appear as the first item in "What Changed Since Yesterday"
and in "Top Three Priorities" — before any other content.

Today's example: "2nd Stage Commercial Director (NA) Interview - Todd" was CANCELLED.
This maps to the Foods Connected WAITING thread. This is not a routine calendar note —
it is potentially a disqualifying event that must be surfaced immediately.

---

## Implementation

### Files changed (RB 9.92)

1. `system/api/DAILY_BRIEF_CANONICAL_TEMPLATE.md`
   - Add Section 0 — Executive Dashboard before all current sections
   - Add Critical Calendar Alert rule
   - Add per-opportunity loop grouping rule
   - Add [Opportunity] → Risk → Mitigation format for strategic risks
   - Add per-person relationship prep format for day-ahead calendar items
   - Add "Why Todd cares:" framing rule for news items
   - Promote this_week/this_month to named sections in Part 2 offer

2. `system/api/custom_gpt_instructions_compact_8k.md`
   - Add Executive Dashboard to brief routing
   - Add Critical Calendar Alert rule
   - Add career-risk synthesis rule for CoS RECOMMENDATIONS

### Acceptance Criteria

A brief passes the CoS test if:
- [ ] Opens with Executive Dashboard (status counts by category)
- [ ] What Changed Since Yesterday is the first substantive section, before news
- [ ] Cancelled interview events matching active opportunity threads surface as ⚠️ CRITICAL
- [ ] Top Three Priorities are named, specific, and action-oriented
- [ ] Risks section includes career-specific risks, not just auto-detected relationship/loop risks
- [ ] Open loops for major opportunities show pending-items breakdown per thread
- [ ] Day-ahead calendar items include per-person relationship prep (objective + context)
- [ ] Every news item has "Why Todd cares:" framed in terms of active objectives
- [ ] This Week and This Month sections appear in Part 2 and are offered in Part 1 close
