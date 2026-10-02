# RB-DEFECT-058: Intelligence Brief Still Analyst Report, Not Newspaper (RB 9.98 Output)

**Filed:** 2026-06-17
**Sprint:** RB 9.99
**Score:** 6.5/10

## Root Cause

The canonical headline format includes mandatory "Why Todd cares" and "Summary" fields.
These fields require the GPT to interpret and analyze every headline -- turning it into an analyst report.
A newspaper does not tell you why you should care. It gives you the headline and lets you decide.

## Defects

| # | Defect | Root Cause |
|---|---|---|
| D058-1 | Every headline followed by summary + interpretation | "Summary" and "Why Todd cares" fields in headline spec |
| D058-2 | CoS Observations still appearing | "Why Todd cares" = CoS analysis in disguise |
| D058-3 | Intelligence Bottom Line still in Part 1 | No enforcement gap -- PROHIBITED not strong enough |
| D058-4 | Relationship Intelligence still not rendering | Section not generating -- needs stronger mandate |
| D058-5 | Intelligence Statistics/Snapshot missing rows | Section J lacks "articles reviewed/promoted" counts |
| D058-6 | Proof Block still generic | Already in spec; execution gap in GPT |
| D058-7 | Personal Intelligence still bare error | Already in spec; execution gap in GPT |

## Fix

1. Remove "Summary" and "Why Todd cares" from ALL Part 1 headline formats
2. Add both to PROHIBITED list in canonical and compact instructions
3. Headline format = `[HEADLINE](url) / Source: [pub] | Date: [date]` ONLY
4. Update Intelligence Snapshot to match user's canonical statistics format
5. Strengthen Relationship Intelligence -- "ALWAYS RENDER even if empty"
