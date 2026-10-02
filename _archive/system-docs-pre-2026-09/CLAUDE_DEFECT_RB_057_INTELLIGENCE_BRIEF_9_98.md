# RB-DEFECT-057: Intelligence Brief — Eight Remaining Defects (RB 9.97 Output)

**Filed:** 2026-06-17
**Sprint:** RB 9.98
**Score:** B- (75/100)
**Product:** Intelligence Brief (Part 1)

---

## Defect Summary

| # | Defect | Score | Root Cause |
|---|---|---|---|
| D057-1 | Personal Intelligence bare error instead of self-heal | 2/10 | IB-2 self-heal rule not enforced as FAILURE |
| D057-2 | Proof Block vague counts ("Major publications: 25+") | 7/10 | Mandatory row labels not explicit enough |
| D057-3 | Watchlist still listing companies with no updates | 4/10 | Exception-based rule not strong enough |
| D057-4 | CoS bleed into Part 1 (Potential Impact, Observations, Bottom Line) | 5/10 | PROHIBITED list insufficient enforcement |
| D057-5 | Headlines missing links | 6/10 | Rule stated but no FAILURE designation |
| D057-6 | Earnings & Corporate Developments section absent | 0/10 | Section not in canonical spec |
| D057-7 | Relationship Intelligence section not rendering | 0/10 | New section not yet adopted by GPT |
| D057-8 | Too much interpretation, not enough reporting | general | GPT defaulting to analyst voice |

---

## Fixes

1. Add Section E: Earnings & Corporate Developments (between Section D and Watchlist)
2. Renumber: old E→F, F→G, G→H, H→I, I→J
3. Remove duplicate Section F in canonical file
4. Strengthen IB-2: "bare error = rendering failure" + mandatory self-heal format required
5. Strengthen watchlist prohibition: NO per-entity no-update lines -- ONE sentence for all no-change
6. Add missing link = rendering failure to critical rules in compact instructions
7. Update compact instructions routing for new section E + renumbered sections
