# RB Test Trace — Foods Connected / Sarah McAngus opportunity mutation-layer defect

**Trace ID:** T-2026-05-21-004  
**Captured at:** 2026-05-21T18:30:00-05:00  
**Source:** Codex / operator-provided RB 9.0 test case  
**Operator:** Todd  
**Trace type:** field_defect  
**Severity:** HIGH  
**Category:** Relationship Intelligence Persistence / Opportunity Creation / Artifact Mutation / Tool Availability

## Summary

Todd received a LinkedIn connection request from Sarah McAngus after Jeff Staley interviewed with Sarah and recommended Todd as a stronger fit for a Foods Connected Commercial Director role. Todd later had a positive call with Sarah. The role is roughly 70% farming the McDonald's account and 30% hunting new business. Sarah said she would send the job description and company website; Todd is expected to respond with his resume.

Foods Connected context captured: founded in Ireland, global presence, smaller U.S. footprint, U.S. HQ in Bentonville, Arkansas, food safety and traceability focus, known customers include McDonald's, Tyson Foods, and Walmart.

## Observed issue

The assistant recognized the strategic fit in prose, but did not prove operationalization. No contact mutation, company artifact, opportunity artifact, relationship edge, loop creation, Who Matters Now elevation, or RB-native relationship-signal action was shown. Memory persistence was unavailable in the tool context, making the trust gap worse.

## Expected canonical behavior

RB should detect:

- Sarah McAngus recruiter/opportunity engagement.
- Referral path and trust transfer: Jeff Staley -> Sarah McAngus -> Todd.
- Commercial Director opportunity at Foods Connected.
- High strategic fit with Todd's McDonald's enterprise account farming background.
- Domain tags: McDonald's, food safety, traceability, enterprise farming, U.S. expansion, Bentonville, Walmart, Tyson Foods.
- Hot momentum.
- Next expected action: await JD/company website, then tailor and send resume.

RB should propose or apply:

- Contact create/update: Sarah McAngus.
- Contact update/touch: Jeff Staley.
- Company artifact: Foods Connected.
- Opportunity artifact: Foods Connected Commercial Director.
- Relationship edge: Jeff referral/trust transfer.
- Loop: await Sarah's JD and company website.
- Loop: tailor and send resume.
- Who Matters Now elevation via high-boost active thread.

## Defects

### OPPORTUNITY-MUTATION-001 — Opportunity RI had no canonical mutation surface

**Severity:** high

The system could talk about the opportunity but could not show contact/company/opportunity/loop/edge mutations.

**Recommendation:** Add a review-first opportunity intake action that returns a visible mutation summary and applies only after explicit confirmation.

### TOOL-AVAILABILITY-001 — RB-native mutations were not callable in the user-facing context

**Severity:** high

Missing callable actions included create/update contact, create company, create opportunity, create relationship edge, create loop, touch contact, update relationship signals, and Who Matters Now prioritization.

**Recommendation:** Expose a consolidated `processOpportunityIntake` operation that routes to available canonical projections and reports persistence status.

### TRUST-SURFACE-001 — Smart analysis without proof of persistence

**Severity:** high

The user could not verify that RB did anything.

**Recommendation:** Canonical responses must include `RELATIONSHIP INTELLIGENCE PROCESSED`, detected signals, artifact mutations, and persistence status.

## Regression anchor

The Sarah/Foods Connected case passes only if RB can return a mutation plan containing: Sarah contact, Jeff touch/referral edge, Foods Connected company artifact, Commercial Director opportunity artifact, two loops, active thread/Who Matters Now boost, and persistence status.

---
*Generated from operator-provided 2026-05-21 RB 9.0 test case. Secrets not present.*
