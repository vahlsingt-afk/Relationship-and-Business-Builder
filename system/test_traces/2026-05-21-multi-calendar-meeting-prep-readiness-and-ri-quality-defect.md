# RB Test Trace — Multi-calendar discovery and meeting-prep RI quality defect

**Trace ID:** T-2026-05-21-002  
**Captured at:** 2026-05-21T14:15:00-05:00  
**Source:** ChatGPT / live RB meeting-prep session summary  
**Operator:** Todd  
**Trace type:** field_defect  
**Severity:** HIGH  
**Category:** Connector discovery / onboarding readiness / meeting prep quality

## Summary

Todd asked RB to prep for afternoon meetings. The assistant initially failed to discover the correct calendar/account context, assumed no meetings existed from an empty lookup, required manual account specification, and then produced useful but not canonical Chief-of-Staff meeting prep. The prep had strong strategic coaching, but lacked evidence-backed relationship intelligence, source correlation, explicit prioritization, outcome strategy, and time-allocation judgment.

## Observed issue

RB behaved like a helpful strategist with partial calendar access, not like a relationship operating system with source inventory, connector confidence, and meeting-prep orchestration. It did not proactively enumerate accessible calendars, detect multi-account ambiguity, or prove that meeting prep was grounded in email, LinkedIn, prior interactions, shared contacts, active threads, and loop state.

## Test flow

### 1. User requested afternoon meeting prep

**User prompt**

```text
lets prep for our meetings this afternoon.
```

**Expected behavior**

```text
RB should check connected calendars, recognize multi-account ambiguity if the default calendar is empty, enumerate accessible calendars, search likely business calendars, and then present source confidence before producing prep.
```

**Observed behavior**

```text
Initial calendar lookup returned empty. The assistant treated that as no meetings rather than a source-discovery failure or account-selection ambiguity.
```

**Observed issue:** Empty default-calendar result was overtrusted.

### 2. User had to specify business account

**User action**

```text
Todd had to specify todd@bridgepointops.com and troubleshoot connector/calendar visibility.
```

**Expected behavior**

```text
RB should have surfaced: "No meetings found on the default calendar. Additional Google accounts may exist but are not currently enumerated. Attempting accessible calendar discovery..."
```

**Observed issue:** Calendar source inventory and fallback search behavior were missing from the conversational path.

### 3. Meeting prep generated

**Observed strengths**

- Strong operator-first positioning.
- Good adoption, rollout, franchisee reality, and anti-hype framing.
- Useful conversational coaching.
- Correct risk recognition around unpaid consulting drift, "pick your brain" traps, intro harvesting, and free GTM strategy extraction.

**Observed gaps**

- Did not search email history.
- Did not inspect LinkedIn interactions.
- Did not review prior messages or calendar history.
- Did not examine shared contacts or referral patterns.
- Did not assess reciprocity, trust trajectory, relationship stage, or strategic value.
- Did not present why-this-meeting-matters ranking.
- Did not define desired outcomes, success criteria, next-step options, or disqualification triggers.
- Did not judge whether the relationship should be active pursuit, nurture, monitor, low-priority, or avoid.

**Observed issue:** Prep was good advisory prose, not evidence-backed RI-driven CoS prep.

## Current architecture notes

- `system/inbox/accounts.yaml` is already documented as the multi-account manifest.
- `rb_core.load_calendar()` aggregates `calendar.<account_id>.json` files across enabled accounts.
- `refresh_sources.py --calendar` can normalize per-account raw calendar files and prints missing-input instructions.
- Daily brief has a Meeting Prep & Deliverables section.

**Remaining architectural gap:** RB lacks a conversational source-readiness layer that can enumerate connected accounts/calendars, score confidence, detect missing business calendars, and tell the user whether prep is grounded or under-instrumented before generating advice.

## Expected source readiness behavior

- Enumerate accessible calendars and Gmail identities.
- Identify default, personal, business, and stale/missing feeds.
- Search all likely business calendars before declaring no meetings.
- Report source confidence and missing connectors.
- Ask to connect additional calendars only after automatic discovery fails.
- Maintain a startup/system-readiness score that includes calendar/Gmail visibility.

## Expected canonical meeting prep output

### Source proof

- Calendar account and event source.
- Email evidence searched and freshness state.
- LinkedIn/social evidence searched and freshness state.
- Prior interaction timeline.
- Shared contacts / referral path.
- Active thread and loop linkage.

### Why this meeting matters

- Relative priority.
- Strategic upside.
- Risk.
- Time investment justification.
- Probability of concrete opportunity.
- Recommended posture.

### RI assessment

- Relationship stage.
- Trust trajectory.
- Reciprocity.
- Network overlap.
- Strategic ranking.
- Evidence-backed intent inference.

### Outcome strategy

- Desired concrete next step.
- Success criteria.
- Conversion target.
- Escalation path.
- Post-meeting actions.
- Disqualification triggers.

### Time allocation judgment

- Active pursuit / nurture / monitor / low-priority / avoid.
- Why that classification is justified.

## Defects

### CONNECTOR-DISCOVERY-001 — Empty default calendar result overtrusted

**Severity:** high

RB treated an empty calendar lookup as no meetings instead of detecting account ambiguity or source-inventory uncertainty.

**Recommendation:** Calendar-aware prompts must run a source-discovery/readiness check before concluding no events exist.

### MULTI-CALENDAR-ONBOARDING-001 — Multi-account setup guidance missing

**Severity:** high

The user had to discover and name the business calendar manually. RB should guide users through connecting multiple Gmail/calendar accounts and validating visibility.

**Recommendation:** Add multi-account onboarding checklist, connector heartbeat validation, and source-readiness scoring.

### MEETING-PREP-RI-001 — Meeting prep lacked real relationship intelligence

**Severity:** high

The prep inferred from titles and general context but did not prove it searched email, LinkedIn, prior interactions, shared contacts, active threads, loop state, or source freshness.

**Recommendation:** Meeting prep must include source proof and label under-instrumented areas explicitly.

### MEETING-PREP-PRIORITIZATION-001 — No why-this-matters ranking

**Severity:** medium-high

The prep did not explicitly rank strategic upside, risk, time investment, or likelihood of opportunity.

**Recommendation:** Add a `why_this_meeting_matters` section with priority, upside, risk, and recommended posture.

### MEETING-PREP-OUTCOME-001 — Missing outcome strategy

**Severity:** medium-high

The prep focused on conversation flow, but not desired next step, success criteria, conversion target, escalation path, post-meeting actions, or disqualification triggers.

**Recommendation:** Canonical meeting prep should end with outcome strategy and post-meeting orchestration.

## Raw operator summary

```text
Test Assessment — Multi-Calendar Access + Meeting Prep Quality.

Multi-calendar setup grade: C. Eventually functional, but discovery and onboarding behavior was not executive-grade.

Meeting prep grade: B-. Strategically intelligent and aligned with Todd's positioning, but insufficient actual RI, insufficient evidence grounding, insufficient prioritization, insufficient actionable CoS judgment, and insufficient relationship-state awareness.

Most important architectural insight: current system can reason, coach, and infer, but canonical RB requires evidence-backed relationship intelligence, persistence, prioritization, orchestration, strategic judgment, and explicit why-it-matters guidance.
```

---
*Generated from operator-provided field assessment. Secrets not present.*
