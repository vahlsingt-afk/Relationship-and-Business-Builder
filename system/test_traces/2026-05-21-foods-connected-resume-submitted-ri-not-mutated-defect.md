# RB Test Trace — Foods Connected resume-submitted RI not mutated

**Trace ID:** T-2026-05-21-005  
**Captured at:** 2026-05-21T19:00:00-05:00  
**Source:** Codex / operator-provided RB 9.0 defect report  
**Operator:** Todd  
**Trace type:** field_defect  
**Severity:** HIGH  
**Category:** Relationship Intelligence / Persistence / Artifact Mutation / Opportunity State

## Summary

The Sarah McAngus / Foods Connected opportunity progressed from initial interest to resume submission. Sarah sent the job description and company website. Todd tailored the resume and sent it back to Sarah.

Additional relationship intelligence surfaced: Todd has prior food safety certification and relevant McDonald's/PAR SureCheck experience. The role is Commercial Director - North America, roughly 70% farming the McDonald's account and 30% hunting new business.

## Observed issue

RB again analyzed the opportunity well in prose, but did not show canonical mutation proof: no contact update, opportunity-stage update, company artifact, relationship edge, loop creation, Who Matters Now elevation, or relationship-signal validation.

## Expected canonical mutations

- Create/update Sarah McAngus.
- Touch/update Jeff Staley and mark active referral influence.
- Create/update Foods Connected company artifact with food safety, traceability, Ireland-founded, Bentonville HQ, McDonald's, Tyson Foods, Walmart tags.
- Create/update Foods Connected - Commercial Director, North America opportunity.
- Set opportunity stage to `resume_submitted`.
- Set temperature to `HOT` and strategic fit to `HIGH`.
- Create referral edge: Jeff Staley -> Sarah McAngus / Foods Connected -> Todd.
- Open loops:
  - Await Sarah response after resume submission.
  - Prepare for next interview step.
  - Research Foods Connected leadership / U.S. expansion strategy.
  - Prepare McDonald's food safety / SureCheck story.
  - Prepare enterprise farming / customer expansion examples.
- Elevate Sarah McAngus, Foods Connected, and Jeff Staley in Who Matters Now.

## Required regression behavior

`POST /opportunity_intake` must accept `stage=resume_submitted`, `materials_received=true`, and `resume_submitted=true`, then return a mutation plan with the post-submission loops instead of the earlier "await JD / tailor resume" loops.

---
*Generated from operator-provided 2026-05-21 RB 9.0 defect report. Secrets not present.*
