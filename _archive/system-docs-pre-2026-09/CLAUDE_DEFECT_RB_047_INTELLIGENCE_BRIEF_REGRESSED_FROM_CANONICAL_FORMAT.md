# RB-DEFECT-047: Daily Intelligence Brief Regressed from Canonical Format

**Date filed:** 2026-06-15
**Filed by:** Todd
**Status:** Filed — triaged 2026-06-15, see finding below.
**Priority:** Critical
**Category:** Intelligence Brief / Chief of Staff Experience

## Issue Summary (verbatim)

The Intelligence Brief generated on June 15, 2026 regressed from the canonical
Relationship Bridge Intelligence Brief format and instead produced a generic
market-news summary.

The output resembled a traditional AI-generated industry briefing rather than
the world-class Chief of Staff briefing that has been repeatedly defined and
refined.

This is not a content issue. It is a framework and execution issue.

## Expected Behavior

When the user requests "Show me today's intelligence brief", the system should
generate the canonical RB Intelligence Brief using all available intelligence
sources and the established intelligence workflow. The brief should function
as a personal Chief of Staff report, not an industry newsletter. The system
should gather, analyze, enrich, update knowledge, generate CoS assessment, and
then generate actions.

## Actual Behavior

The report delivered generic industry news, generic market observations, broad
commentary, limited personalization, no proof of intelligence gathering, no
relationship intelligence, no change detection, no intelligence
prioritization. The output felt like "Here are some restaurant technology
articles that may interest you" instead of "Here is what changed in your world
since yesterday and what you should do about it."

## Canonical Intelligence Brief Structure (Todd's spec, verbatim)

1. **Overnight Intelligence Summary** — concise executive summary: what
   happened overnight, what matters, why the user should care. Significance,
   not volume.
2. **What Changed Since Yesterday** (mandatory) — new intelligence, new
   relationships, new market signals, new company developments, new calendar
   changes, new communication activity. If nothing material changed: "Minimal
   change detected since previous intelligence cycle."
3. **Relationship Intelligence** — LinkedIn changes, contact changes, network
   movements, promotions, job changes, recent engagement, inactive strategic
   relationships, with recommended actions (e.g. "John Smith promoted to VP
   Operations. Send congratulations message.").
4. **Career Pipeline Intelligence** — Foods Connected (stage, last activity,
   next milestone, risks, recommended actions), Global Payments (same), other
   active opportunities only if materially relevant. The brief should always
   understand the user's active opportunities.
5. **Calendar Intelligence** — upcoming meetings, deadlines, follow-ups, prep
   requirements, conflicts. "Currently missing from most brief generations."
6. **Communication Intelligence** — email, SMS, call logs, relationship
   activity; important unanswered communications, emerging opportunities,
   required follow-ups. "Currently absent."
7. **Market Intelligence** — enterprise restaurant tech, restaurant
   operations, POS, payments, loyalty, AI, McDonald's ecosystem. Implications,
   not headlines.
8. **Company Watchlist** — IMPORTANT CHANGE: do NOT list every company with
   "No material change" (creates noise, reduces trust). Instead: "Material
   Updates" (only companies with meaningful developments) and "No Material
   Updates" as a single rollup statement: "I scanned all companies and
   individuals on your watchlist. No material updates were identified."
9. **Chief of Staff Assessment** — Today's Priorities (top 3-5 actions), This
   Week (key objectives), This Month (strategic objectives). Think beyond
   today; current outputs are too tactical/short-term.
10. **Intelligence Gathering Proof** — companies scanned, sources reviewed,
    earnings reviewed, press releases reviewed, relationship changes analyzed,
    calendar reviewed, communications reviewed. User must trust that gathering
    actually occurred.

## Additional Defects (Todd's list, verbatim)

- **Defect 1 — Missing Fresh Personal Intelligence.** Active hiring processes,
  recent conversations, recent strategic discussions, current opportunities
  are frequently missed. Foods Connected and Global Payments should always
  appear because they are active and strategic.
- **Defect 2 — Excessive Validation of User Opinions.** The brief often tells
  the user his previously stated positions are correct. Not intelligence.
  Should prioritize new information, contradictory information, confirming
  evidence, emerging risks — not repeated validation.
- **Defect 3 — Missing Relationship Actions.** Should routinely generate
  actionable relationship recommendations. The user's network is a strategic
  asset and should be managed proactively.
- **Defect 4 — Missing Time Horizons.** Currently focuses almost entirely on
  today. CoS should maintain Today / This Week / This Month simultaneously.

## Success Criteria (verbatim)

A successful brief should feel less like "Here are today's restaurant
technology headlines" and more like "I reviewed your relationships,
opportunities, communications, calendar, market intelligence, and watchlist.
Here is what changed, why it matters, and what you should do next."

---

## Triage finding (2026-06-15)

This defect describes the **same live-session output** already triaged as
[RB-DEFECT-045](CLAUDE_DEFECT_RB_045_NON_CANONICAL_INTELLIGENCE_BRIEF_FALLBACK.md)
(also filed 2026-06-15), restated as a full canonical-structure specification
with four additional named defects. Cross-checking each item against the
current codebase:

### Sections 1-10 — already implemented, code-side

RB-DEFECT-045's triage already confirmed
`system/published/daily/2026-06-15/brief.json` (the live
`build_canonical_brief()` output for this date) contains a populated section
for every one of Todd's 10 canonical sections:

| Spec section | Backing `daily_brief.py` section(s) |
|---|---|
| 1. Overnight Intelligence Summary | `intelligence_collection_summary` + headline sections |
| 2. What Changed Since Yesterday | `last_24h_relationship_signals`, novelty/lifecycle filtering (RB 9.71's `_NOVELTY_FILTER_EXEMPT`) |
| 3. Relationship Intelligence | `relationship_operational_signal_review`, `last_24h_relationship_signals` |
| 4. Career Pipeline Intelligence | `opportunity_board` (Foods Connected and Global Payments are both tracked in `active_threads.yaml`, lines 5 and 233) |
| 5. Calendar Intelligence | `calendar_snapshot`, `calendar_overlay` (incl. RB-DEFECT-046 Slice 1's `triage_signals`) |
| 6. Communication Intelligence | `communication_intelligence`, `email_intelligence_harvest` (incl. Slice 1's email `triage_signals`) |
| 7. Market Intelligence | `condensed_industry_context`, `market_signals` (RB 9.71 item 4 reserves curated-item slots) |
| 8. Company Watchlist (material vs. rollup) | `watchlist_intelligence` — RB 9.71 item 1 already implements the "scanned, no material updates" rollup language for DORMANT/ACKNOWLEDGED entries, exempted from novelty filtering so it survives every cycle |
| 9. CoS Assessment (Today/Week/Month) | `decision_layer`, `this_week_priorities`, `this_month_priorities`, `horizon_watch` |
| 10. Intelligence Gathering Proof | `intelligence_collection_summary` (RB-INTEL-021) — populated with "Sources: 8/18 healthy \| Freshness: 50% \| Trust: 26% \| Mutations: 10" plus explicit stale/error source lists for 2026-06-15 |

### Defects 1-4 — already addressed structurally

- **Defect 1** (Foods Connected / Global Payments must always appear): both
  are live entries in `active_threads.yaml` feeding `opportunity_board`,
  which `decision_layer`/`this_week_priorities` read from
  (`scripts/daily_brief.py:1908`, `1995`, `9654`, `10299`, `10577`). They are
  structurally always-on, not conditional.
- **Defect 2** (excessive validation of user opinions): RB-DEFECT-046's
  enrich-before-comment design (Slice 3, RB 9.90) is specifically aimed at
  this — correlating new signals against persisted strategic narratives
  ("this confirms/extends/contradicts what we knew") rather than restating.
- **Defect 3** (missing relationship actions): `relationship_operational_signal_review`
  and `last_24h_relationship_signals` already generate recommended actions
  (RB-INTEL-021).
- **Defect 4** (Today/Week/Month horizons): `decision_layer` (today),
  `this_week_priorities`, `this_month_priorities`, and `horizon_watch` are all
  distinct, populated sections (RB 9.85's Document 2 framing).

### Conclusion

Per RB-DEFECT-045's finding, the `build_canonical_brief()` JSON payload for
2026-06-15 is structurally complete against every item in this spec — there is
no missing section, no missing data source integration, and no code change
identified that would close this defect. The behavior described ("generic
market-news summary", "no proof of intelligence gathering") is consistent with
a **live Custom GPT session that either ran on the pre-RB-9.81 stale
Instructions/KB, or did not call `getDailyBrief`/`getDailyBriefPart2` at all**
and instead improvised a generic summary from its own general knowledge.

**This defect does not introduce a new required code change beyond what
RB-DEFECT-045 already identified.** It sharpens the specification (useful as a
durable reference for `custom_gpt_prompt.md` / canonical template wording —
e.g. the explicit "Material Updates" / "No Material Updates" framing for the
watchlist in item 8 is slightly more prescriptive than RB 9.71's existing
rollup language and may be worth a wording pass once the GPT is re-synced and
real output can be checked against it).

**Status:** Closing as a duplicate-root-cause of RB-DEFECT-045, same resolution
path — **live GPT re-sync completed 2026-06-15**
(`custom_gpt_instructions_compact_8k.md` + 5 KB files). Awaiting live
re-validation against this spec's 10 sections + 4 named defects. If the live
brief still fails to match any of them, re-open as a genuine code/prompt
defect with the specific section(s) that failed.
