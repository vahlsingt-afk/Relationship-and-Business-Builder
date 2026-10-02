# RB-DEFECT-049: Brief Fidelity — CoS Rendering Fails When GPT Improvises

**Date filed:** 2026-06-16
**Filed by:** Todd
**Status:** Active — implementation in progress
**Priority:** Critical
**Category:** Daily Brief / Chief of Staff Rendering

---

## Canonical Score: 3/10 (2026-06-16 session)

| Category | Expected | Actual | Score |
|---|---|---|---|
| Intelligence Proof | Sources scanned + entity breakdown | Generic statements | 2/10 |
| Fresh Intelligence | Tell me something new | Validated existing beliefs | 3/10 |
| Personal Context | Email, calendar, jobs, relationships | Completely absent | 0/10 |
| Company Watchlist | Material changes only + evidence | Generic commentary | 4/10 |
| Actionability | Specific actions from intelligence | Coaching suggestions | 4/10 |
| Self-Healing Research | Identify gaps, compensate | No enrichment evidence | 2/10 |
| Executive CoS Feel | Managing my world | Industry article | 3/10 |
| Trust Building | Demonstrates work performed | Unsupported claims | 3/10 |

**Overall: 3/10**

---

## Root Cause

The data layer is correct. `brief.json` for 2026-06-16 contains populated sections
for all 10 canonical areas (see RB-DEFECT-047 triage). The failure is entirely at the
**GPT rendering layer**: the GPT improvised from its own knowledge instead of calling
`getDailyBrief` and rendering from the API response.

When the GPT improvises, it produces:
- Generic restaurant technology commentary
- Validation of the user's existing positioning
- No proof of work performed
- No career pipeline awareness
- No personal context (calendar, email, open loops)

---

## Six Specific Defects

### Defect 1 — No Proof (2/10)

**Expected:**
```
Intelligence Proof
Sources scanned: 32
Watchlist entities: 22
Company updates identified: 4
Relationship mutations: 2
Emails requiring attention: 3
Calendar events within 7 days: 5
Open loops detected: 24 (14 overdue)
```

**Actual:** "Sources scanned: Restaurant technology and payments news"

**Root cause:** The Proof Line in DAILY_BRIEF_CANONICAL_TEMPLATE.md is a single line
(`📡 Scanned: N sources | N mutations | N stale | Generated time`). The user's canonical
spec is a structured block with counts by category. The template is insufficient. The
compact instructions say "First line: RB — {date}..." — conflicting with the template.

**Fix:** Replace single-line Proof Line with Intelligence Proof Block; resolve the
conflict between compact instructions (session header) and template (proof line).

---

### Defect 2 — No Personal Context (0/10)

**Expected:** Active career opportunities, calendar events, open commitments, emails
requiring response — in Part 1.

**Actual:** None. Zero mention of Foods Connected, Global Payments, PerfectHire,
Harri, calendar, or communications.

**Root cause:** Career pipeline is in Part 2, Section 4 (ACTIVE OPPORTUNITIES).
If the GPT improvises Part 1 (or the user doesn't proceed to Part 2), personal context
never appears. Section 1 PERSONAL items are spec'd but not mandatory — the GPT can
fill them with generic "industry context" when improvising.

**Fix:** Section 1 PERSONAL must be populated from `opportunity_board` (active career
threads) and `day_ahead` (calendar) — these are mandatory fields, not optional. Add
explicit rule to compact instructions: if `opportunity_board` is non-empty, Section 1
PERSONAL must open with active career opportunity status.

---

### Defect 3 — No Delta Engine (implicit)

**Expected:** "What changed since yesterday" as the first substantive content after
the proof block — with specific named changes, not "industry messaging has shifted."

**Actual:** Absent. The section exists in `brief.json` as `what_changed_since_yesterday`
but is not called out as a mandatory first-content section in the compact instructions.

**Fix:** Add "Material Changes Since Yesterday" as a mandatory named subsection of
Section 1, populated exclusively from `what_changed_since_yesterday` and
`overnight_delta_intelligence`. If these are empty or stale: "Minimal change detected
since previous intelligence cycle." — not a license to substitute general commentary.

---

### Defect 4 — Watchlist Noise

**Expected:** Material updates for companies with signals; single rollup for all others:
"I scanned all companies and individuals on your watchlist. No material updates."

**Actual:** Per-company commentary repeated day after day regardless of whether
anything changed. After several days, the section becomes noise.

**Root cause:** Section 3 template says "No signals detected this cycle" per entity —
this creates a wall of negative-confirmation lines that reads as noise. The compact
instructions don't distinguish between "material update" (worth reading) and "scanned,
nothing" (should be a count, not a list).

**Fix:** Section 3 watchlist rollup: entities with signals get full treatment; entities
without signals are collapsed to a single line: "Scanned [N] additional entities — no
material updates detected."

---

### Defect 5 — Recommendations Are Coaching

**Expected:** Specific actions based on intelligence discovered this cycle.
"Sarah expects presentation by Wednesday" — "Draft outline should be completed today."

**Actual:** "Continue refining your Global Payments narrative." "Continue developing
your enterprise thesis."

**Root cause:** When the GPT improvises without API data, it defaults to coaching the
user's known positions. CoS RECOMMENDATIONS requires API-grounded intelligence to be
contrarian. Without the API call, there is no grounding and coaching is all it can do.

**Fix:** Hardening the retrieval gate (Defect 6 fix) resolves this. CoS RECOMMENDATIONS
also requires ≥1 push-back on a user assumption — "Continue X" is never acceptable as
a recommendation. Add explicit prohibition.

---

### Defect 6 — GPT Improvises (root cause of all)

**Expected:** HARD RETRIEVAL GATE blocks any brief response not grounded in API data.

**Actual:** Gate is stated in compact instructions but not enforced — GPT improvises
"sources scanned: restaurant technology" when it should output:
"RB delivery failure: today's canonical brief could not be retrieved."

**Root cause:** The gate says "render only after status: ok" but does not explicitly
prohibit the specific failure modes (summarizing industry news, restating user
positioning, synthesizing from general knowledge). GPT interprets ambiguity as
permission to be helpful.

**Fix:** Add explicit prohibited-response list to the gate. Any response that:
- summarizes industry news without a confirmed getDailyBrief call
- restates the user's existing positioning as intelligence
- uses "Sources scanned: [category name]" instead of a count
- contains "this supports your thesis" without a named OBSERVED signal
...is a RETRIEVAL GATE VIOLATION. Output only the failure line.

---

## Implementation

### Files changed

1. `system/api/DAILY_BRIEF_CANONICAL_TEMPLATE.md`
   - Replace Proof Line with Intelligence Proof Block
   - Add "Material Changes Since Yesterday" as mandatory Section 1a
   - Fix watchlist rollup language

2. `system/api/custom_gpt_instructions_compact_8k.md`
   - Resolve session header vs. proof line conflict
   - Add Intelligence Proof Block source fields
   - Add explicit prohibited-response list to retrieval gate
   - Add mandatory career pipeline rule for Section 1 PERSONAL
   - Add delta section routing rule

### Acceptance Criteria

After GPT re-sync:

A brief **passes** the CoS test if:
- [ ] Opens with Intelligence Proof Block (counts by category, not names/descriptions)
- [ ] Material Changes Since Yesterday appears before any narrative content
- [ ] Section 1 PERSONAL includes active career opportunity status if any exist
- [ ] Watchlist has material-only entries + single rollup sentence for no-signal entities
- [ ] CoS Recommendations include ≥1 push-back that is NOT "continue X"
- [ ] No response contains "Sources scanned: [category]" (must be a number)
- [ ] No response contains "this supports your thesis" without a named OBSERVED signal
- [ ] A session where getDailyBrief was not called outputs only the failure line
