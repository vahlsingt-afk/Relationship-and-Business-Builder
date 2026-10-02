# RB Test Trace — Relationship Signal Interpretation vs Canonical Operationalization

**Trace ID:** T-2026-05-19-009  
**Captured at:** 2026-05-19T13:30:00-05:00  
**Source:** ChatGPT manual SMS/iMessage screenshot assessment  
**Operator:** Todd  
**Trace type:** field_defect  

## Summary

Todd provided an SMS/iMessage exchange with Jeff Coffland, a longtime trusted McDonald's friend/supporter who remains inside the McDonald's corporate ecosystem. The assistant produced strong qualitative interpretation, correctly identifying advocacy, sponsorship indicators, operational trust, McDonald's adjacency, and second-order opportunity potential.

The response was still non-canonical because it did not expose RB-native operational output: no signal scores, trust stats, RC mutation plan, last-touch delta, loop creation/update, DRR projection, or persistent trace.

## Observed issue

Helpful relationship commentary is not sufficient. RB must operationalize relationship intelligence into measurable, persistent, traceable state.

## Steps

### 1. Manual relationship artifact assessed as prose

**User-provided context**

```text
SMS/iMessage exchange with Jeff Coffland:
- emotional check-in ("Been thinking about you")
- job search / industry transition discussion
- Genius migration / help desk risk commentary
- company/opportunity recommendations
- offer to assist with Unisys internally
- offer to investigate Harri via McDonald's VMO contacts
- continued supportive engagement
```

**Tool / API call**

- intended_operation: `manual_relationship_intake`
- actual_behavior: no deterministic intake object was produced in the original assistant response
- response_summary: qualitative relationship interpretation only

**Observed issue:** The assistant correctly understood the relationship but did not return machine-actionable RB state.

### 2. Canonical output requirement clarified

**Required canonical output**

- structured signal detection: advocacy, sponsorship, insider access, reciprocity, engagement velocity, momentum
- quantified trust intelligence: trust score, sponsor probability, advocacy probability, strategic value, dormancy risk
- persistent state mutation plan: RC update, `last_touch`, tags, opportunity linkage, loop generation/update, DRR recalculation
- observable operational outputs: mutation confirmations, score deltas, action queue, daily brief integration

**Observed issue:** No stats or mutation visibility were shown, so the response could not be audited or persisted.

### 3. Engineering response

**Implemented**

- Added deterministic `system/scripts/manual_relationship_intake.py`.
- Added `POST /manual_relationship_intake`.
- Regenerated `system/api/openapi.yaml` and `system/api/openapi_gpt.yaml`.
- Verified Jeff sample produces structured signals, trust metrics, DRR before/after projection, and proposed writes.

## Defects

### MANUAL-RI-CANONICALIZATION-001 — Manual relationship artifacts produced prose instead of RB state

**Severity:** high

Manual screenshots and pasted exchanges can carry high-value relationship intelligence. The prior behavior allowed the assistant to explain the relationship without showing state deltas or proposed writes.

**Recommendation:** Route manual screenshots / pasted messages through `manual_relationship_intake` before responding.

### TRUST-STATS-VISIBILITY-001 — Trust metrics were not surfaced

**Severity:** high

The user specifically expected trust stats. The assistant returned none.

**Recommendation:** Manual RI responses must include trust score, advocacy probability, sponsor probability, strategic value, dormancy risk, and DRR projection when a baseline match exists.

### MUTATION-VISIBILITY-001 — No RC/loop/DRR mutation evidence was visible

**Severity:** high

The assistant did not show whether RB intended to update `last_touch`, create loops, tag McDonald's/Harri/Unisys context, or adjust DRR.

**Recommendation:** Manual RI responses must show writes performed, writes pending confirmation, blocked writes, and post-write validation requirements.

## Regression sample

```bash
python3 system/scripts/manual_relationship_intake.py \
  --id jeff-coffland \
  --event-at 2026-05-19 \
  --captured-at 2026-05-19 \
  --text "Jeff said Been thinking about you, discussed job search and industry transitions, commented on Genius migration and help desk risk, recommended companies and opportunities, offered to assist with Unisys internally, and offered to investigate Harri via McDonald's VMO contacts."
```

Expected result includes:

- `relationship_warmth`
- `advocacy_signal`
- `sponsorship_signal`
- `insider_access_signal`
- `opportunity_signal`
- trust / advocacy / sponsor / strategic-value metrics
- DRR projection
- proposed `touchContact`
- proposed `loopAdd`

---
*Generated during RB 9.0 canonicalization follow-up. Secrets redacted before persistence.*
