# RB Test Trace — Sarah McAngus warm recruiter introduction canonical persistence defect

**Trace ID:** T-2026-05-21-001  
**Captured at:** 2026-05-21T10:45:00-05:00  
**Source:** ChatGPT / live RB advisory session summary  
**Operator:** Todd  
**Trace type:** field_defect  
**Severity:** HIGH  
**Category:** Canonicalization failure / invisible persistence defect

## Summary

Todd tested RB during a real high-value recruiting acceleration event: Jeff Staley, a former PAR direct report and trusted source, introduced or warmed a path to recruiter Sarah McAngus for a Commercial Director role with strong McDonald's and enterprise farming alignment. The assistant gave strong tactical advice and interpreted the momentum correctly, but did not visibly prove canonical relationship intelligence capture, artifact mutation, operational orchestration, or persistence.

## Observed issue

RB sounded strategically intelligent but did not demonstrate that the relationship event became operational state. The user could not verify whether RB created or updated contacts, opportunity state, referral edges, loops, prep tasks, or "Who Matters Now" prioritization.

## Test flow

### 1. LinkedIn connection request and warm referral path

**User prompt**

```text
Sarah McAngus sent a LinkedIn connection request. Jeff Staley told Todd he interviewed with Sarah and recommended Todd for the role.
```

**Tool / API call**

- intended_operation: `manualRelationshipIntake`
- response_summary:
```text
The system recognized the warmth and strategic value in prose, but did not surface formal RI detection or proposed canonical writes.
```

**Observed issue:** Warm referral path Jeff Staley -> Sarah McAngus -> Todd was not explicitly modeled as a relationship edge or trust-transfer event.

### 2. Role and strategic alignment identified

**User prompt**

```text
The role is Commercial Director, approximately 70% farming, largely McDonald's-focused.
```

**Tool / API call**

- intended_operation: `classify_recruiting_opportunity_state`
- response_summary:
```text
The system interpreted fit and positioning well, but did not produce a structured opportunity artifact or McDonald's strategic-domain tag.
```

**Observed issue:** McDonald's ecosystem alignment, enterprise farming fit, and opportunity temperature were not rendered as canonical state.

### 3. Outreach timing advice

**User prompt**

```text
Should Todd proactively engage Sarah or wait?
```

**Tool / API call**

- intended_operation: `cos_recommendation_with_persistence_surface`
- response_summary:
```text
The assistant correctly advised proactive engagement, but stayed in advisory prose rather than showing detected signals, confidence, and system action.
```

**Observed issue:** Relationship signal recognition remained invisible and unverified.

### 4. Recruiter accelerated to same-day scheduling

**User prompt**

```text
Sarah messaged first before outreach was sent. The conversation escalated to same-day call scheduling, WhatsApp communication, timezone coordination, and active recruiter engagement.
```

**Tool / API call**

- intended_operation: `persist_recruiting_momentum_event`
- response_summary:
```text
The assistant recognized high responsiveness and momentum, but did not confirm contact creation, loop creation, active opportunity state, or interview-prep orchestration.
```

**Observed issue:** Same-day scheduling and WhatsApp escalation should have been persisted as communication velocity and recruiter responsiveness signals.

## Expected relationship signals

- Warm referral path established: Jeff Staley -> Sarah McAngus -> Todd.
- Recruiter acceleration event detected.
- Trust transfer event detected.
- McDonald's ecosystem alignment detected.
- Enterprise farming alignment detected.
- Engagement momentum: HIGH.
- Qualification friction: LOW.
- Recruiter responsiveness: HIGH.

## Expected artifact mutations

- Contact created or updated: Sarah McAngus.
- Relationship edge created: Jeff Staley referral -> Sarah McAngus.
- Opportunity created: Commercial Director.
- Strategic domain tag added: McDonald's / enterprise restaurant tech.
- Active loop opened: interview process.
- Follow-up preparation tasks created.
- Communication velocity signal stored.
- Who Matters Now / active priority state updated.
- Relationship temperature set to HOT.

## Expected CoS orchestration

### Strategic assessment

- Strong role alignment with Todd's enterprise farming strengths.
- Existing McDonald's credibility reduces onboarding risk.
- Referral trust materially accelerated engagement velocity.

### Recommended actions

1. Prepare enterprise farming examples.
2. Prepare McDonald's/franchisee navigation stories.
3. Prepare cross-sell/account expansion examples.
4. Research organizational commercial structure.
5. Prepare first-90-day strategic framing.

### Risk surface

- Potential overemphasis on hunter-sales profile.
- Need to position operational credibility as commercial advantage.
- Clarify travel and global expectations early.

## Defects

### CURRENT-CLASSIFIER-PROBE — Manual intake partially detects but misclassifies third-party interview

**Probe command:** `manual_relationship_intake.py --json --name "Sarah McAngus" ...`

**Observed current behavior:** The review path proposed `addContact`, `openThread`, `loopAdd`, and `writeInteractionBrief` for Sarah McAngus, with `writes_pending_confirmation=4`. That means the backend has partial machinery for this case when the user explicitly routes the story through manual RI intake.

**Remaining gap:** The advisory session did not visibly call that path or render its canonical persistence surface.

**New classifier bug:** The phrase "Jeff Staley told me he interviewed with Sarah" was classified as `interview_completed`. In this scenario Jeff interviewed with Sarah; Todd had not yet completed an interview. The parser needs to distinguish third-party referral evidence from Todd's own interview-stage transition.

### RECRUITER-INTRO-RI-SURFACE-001 — Relationship intelligence remained embedded in prose

**Severity:** high

The assistant recognized warm recruiter momentum but did not render an explicit "Relationship signal detected" surface with classification, confidence, source evidence, and recommended operation.

**Recommendation:** Recruiting-introduction sessions must return structured detected signals whenever a trusted referral, recruiter response, scheduling acceleration, role fit, or strategic-domain alignment appears.

### RECRUITER-INTRO-PERSISTENCE-001 — Artifact mutation proof missing

**Severity:** critical

No visible evidence showed that Sarah McAngus, Jeff Staley referral context, Commercial Director opportunity state, McDonald's alignment, or active interview loop were created, updated, proposed, or skipped.

**Recommendation:** Every RI-bearing recruiting acceleration should emit `persistence_status`, proposed/applied mutation list, affected entities, and post-validation state.

### RECRUITER-INTRO-ORCHESTRATION-001 — CoS actions were not operationalized

**Severity:** high

The assistant gave good guidance but did not create or propose preparation tasks, research tasks, scheduling follow-ups, or daily-brief injection.

**Recommendation:** Same-day recruiter scheduling should propose interview-prep loops and daily-brief/meeting-prep visibility.

### OPERATIONAL-TRUST-SURFACE-001 — No canonical success surface

**Severity:** high

The user could not tell whether RB had recorded, ignored, proposed, or blocked state mutation.

**Recommendation:** RB responses must separate advice from system action: detected signals, impact, mutations, persistence, and next operations.

## Canonical expected output shape

```text
RELATIONSHIP SIGNAL DETECTED
Classification: Warm recruiter acceleration
Confidence: High

SIGNALS
- Jeff Staley referral validated
- Recruiter initiated same-day scheduling
- Direct WhatsApp escalation
- Minimal qualification friction

IMPACT
- Candidate priority likelihood elevated
- Trust transfer confirmed
- Strategic domain alignment confirmed

ARTIFACT MUTATIONS
- Contact updated: Sarah McAngus
- Opportunity created: Commercial Director
- Relationship edge recorded: Jeff referral path
- Active loop opened: Interview process

STATUS
Canonical RI persistence: VERIFIED or proposed_write_pending_confirmation
```

## Raw operator summary

```text
RB 9.0 Test Case Study
Test Name: Warm Recruiter Introduction Without Canonical RI Persistence
Date: 2026-05-21
Scenario Type: Relationship Intelligence Detection / CoS Orchestration / Artifact Mutation Validation
Severity: HIGH
Category: Canonicalization Failure / Invisible Persistence Defect

The session showed strong conversational guidance quality but failed canonical RB behaviors around relationship intelligence persistence, artifact mutation, structured signal evaluation, operational orchestration, and trust-verifiable state mutation.
```

---
*Generated from operator-provided test case summary. Secrets not present.*
