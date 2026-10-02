# RB-DEFECT-056: Intelligence Brief Contamination — CoS Sections Bleeding Into Intelligence Product

**Date filed:** 2026-06-17
**Status:** Active — implementation RB 9.97
**Score:** 5/10 overall

## Root Cause

The canonical template defines ALLOWED sections in the Intelligence Brief but never
explicitly lists PROHIBITED sections. The GPT invents CoS sections (Executive Assessment,
Open Loops, Intelligence Priorities, Potential Impact) because it is not told they are banned.

## Specific Prohibited Sections Found in Output

| Section Found | Why It's Wrong | Belongs In |
|---|---|---|
| Executive Assessment ("You have entered a significant transition week") | Interpretation — not fact | Daily Brief |
| Open Loops ("Obtain commission documentation") | Task list — not intelligence | Daily Brief |
| Intelligence Priorities (task list) | Tasks — not intelligence activities | Daily Brief |
| Potential Impact sections | CoS analysis — not news reporting | Daily Brief |
| CoS Intelligence Observations | Interpretation | Daily Brief |
| Intelligence Bottom Line as advice | Synthesis/advice | Daily Brief |
| Strategic Assessment with probability ratings | CoS judgment | Daily Brief |

## Prohibited Headline Format

Headlines must be actual headline titles, NOT analysis sentences.

PROHIBITED: "Enterprise Technology Spending Remains Selective"
REQUIRED:   "Federal Reserve Signals Caution on Rate Cuts"

The first is an analyst's characterization. The second is a reportable fact.

## Canonical Intelligence Brief Structure (RB 9.97)

1. Intelligence Collection Summary — quantified source counts by category
2. Intelligence Processing Summary — mutations, material items (counts only)
3. World Headlines — headline + source + link (no analysis)
4. National Headlines — headline + source + link
5. Industry Headlines – Restaurant Operations
6. Industry Headlines – Restaurant Technology
7. Watchlist Intelligence — material changes only; one sentence for no-change
8. Relationship Intelligence — material mutations only
9. Strategic News Themes — factual patterns ONLY (no advice, no recommendations)
10. Intelligence Snapshot — closing counts and facts (no advice)

## Intelligence Snapshot Format (closing section)

```
INTELLIGENCE SNAPSHOT
Material developments identified today: 7
Highest-significance themes:
• Enterprise technology cost pressure
• Payments companies moving up the software stack
• Restaurant technology consolidation
• Active changes across personal opportunity watchlists
```

Facts. Counts. No advice. No recommendations. No tasks.

## Intelligence Priorities Format (if included)

ALLOWED — monitoring activities:
• Monitor Global Payments compensation disclosures
• Monitor Foods Connected executive scheduling changes
• Monitor payments-funded software announcements

PROHIBITED — tasks:
• Obtain commission documentation
• Prepare Foods Connected presentation
• Schedule follow-up with Sarah
