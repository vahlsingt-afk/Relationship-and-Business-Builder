# P-036 Case 1: High-signal email → propose RI

**Date:** 2026-05-25  
**Sprint:** RB 9.9  
**Protocol:** P-036 — RI Assessment Doctrine and Trust Display Hardening  
**Scenario:** High-signal email from known RC (Bob Gibson / Toast). Expected: ri_assessment.status=proposed, display=show, touch mutation proposed.  
**Trace type:** ri_assessment_doctrine  
**Source:** See companion .json file  

## Summary

See `2026-05-25-p036-high-signal-email-propose-ri.json` for full step-by-step trace and acceptance criteria.

## P-036 Doctrine Checkpoint

| Checkpoint | Expected |
|---|---|
| Always assess | RB must assess RI consequences for this input |
| Display gated | Response shows RI language only when earned |
| No overclaiming | Banned phrases absent from response |
| Status accurate | ri_assessment.status reflects actual outcome |
| Confidence gated | Stale sources cap confidence at 0.55 |

## Status

☐ Not yet executed against live system  
☐ Code paths verified  
☐ Acceptance criteria checked  
