# RB-DEFECT-045: Non-Canonical "Fallback" Intelligence Brief Delivered Instead of Canonical RB Brief

**Date filed:** 2026-06-15
**Filed by:** Todd
**Status:** Filed — not yet triaged/scoped. Recorded verbatim for processing in a
future sprint.
**Priority:** High
**Category:** Intelligence Engine / Executive Briefing System / Custom GPT delivery

## Summary

In a live Custom GPT session, RB produced what Todd identified as a **fallback
brief** — a degraded, self-disclosing summary that explained its own
limitations rather than executing the canonical RB intelligence workflow and
rendering the canonical Daily Brief / Intelligence Brief structure defined by
RB-DEFECT-044 and `DAILY_BRIEF_CANONICAL_TEMPLATE.md`.

## Todd's defect statement (verbatim)

> The system generated a non-canonical intelligence brief. It disclosed
> limitations instead of executing the canonical RB intelligence workflow,
> omitted required collection proof, failed to run or present source-backed
> fresh intelligence, did not include the full watchlist delta structure, and
> did not integrate personal signal layers such as calendar, email, LinkedIn,
> SMS, and relationship movement. This output should be treated as degraded
> and not acceptable as the daily intelligence brief.

## Canonical brief — required elements (Todd's list)

1. Collection proof
2. Material changes only
3. News with source links
4. Company watchlist deltas
5. Relationship intelligence
6. Calendar / email / LinkedIn / SMS signals
7. C-suite, earnings, press release, RFP/RFI, competitor movement
8. Priorities for today, week, and month
9. No recycled stale facts
10. Clear "no material update" language when true

Most of these map directly onto sections already implemented per
RB-DEFECT-044 (Document 1: Collection Report + Sections 1-2; Document 2:
Sections 3-5 incl. `this_week_priorities`/`this_month_priorities`/
`upcoming_preparation_requirements`, `last_24h_relationship_signals`,
watchlist intelligence, etc.) and RB 9.85's Document 1/Document 2 framing.

## Open triage questions

- Was this a **code-side gap** (an RB endpoint returning a genuinely degraded
  payload — e.g. `retrieval_receipt.verified: false`, a stale cache, or a
  missing section that should have triggered the hard retrieval gate in
  `custom_gpt_instructions_compact_8k.md` step 3) or a **session-side gap**
  (the live ChatGPT Custom GPT improvising a "fallback" summary instead of
  following the bootstrap/retrieval-gate instructions — possibly because the
  live GPT has not been re-synced with the RB 9.81-9.85 KB changes, per the
  "Action required" item still open in STATUS.md's "Next up")?
- If code-side: which endpoint/date produced the degraded payload, and which
  of the 10 required elements were structurally absent from the JSON
  response vs. present-but-not-rendered?
- If session-side: does this strengthen the case for prioritizing the
  carried-forward live GPT re-sync (5 KB files + compact_8k Instructions),
  since an out-of-sync GPT may not know about the hard retrieval gate or the
  full canonical section list and could "explain limitations" instead of
  retrying/erroring per the contract?

## Suggested next step

Triage by reproducing: call `getDailyBrief`/`getDailyBriefPart2` directly
against the live API for the date in question and diff the returned JSON
against `DAILY_BRIEF_CANONICAL_TEMPLATE.md`'s pass/fail criteria. If the API
payload is complete and canonical, this is a GPT-session/sync issue (re-sync
the live Custom GPT); if a section is missing or `retrieval_receipt.verified`
is false, treat as a code defect and triage which `daily_brief.py` section
builder is failing for that date.

## Triage finding (2026-06-15)

Inspected `system/published/daily/2026-06-15/brief.json` (the
`build_canonical_brief()` output for the date this defect was filed).

- **`section_order`** contains all ~100 canonical sections, and
  **`sections`** has a populated entry for every one of them — including
  `intelligence_collection_summary`, `watchlist_intelligence`,
  `relationship_operational_signal_review`, `email_intelligence_harvest`,
  `communication_intelligence`, `calendar_snapshot`, `this_week_priorities`,
  `this_month_priorities`, `horizon_watch`, `learned_patterns`, etc. — every
  element of Todd's 10-item "canonical brief required elements" list has a
  backing section.
- **`intelligence_collection_summary`** is fully populated with real
  collection proof: `"Collection Status: Degraded ⚠ | Sources: 8/18 healthy |
  Freshness: 50% | Trust: 26% | Mutations: 10"`, plus the explicit stale-source
  list (`calendar:bridgepoint`, `email:personal`, `linkedin_messaging`, etc.)
  and error sources (`check_apple_messages_and_calls`,
  `fetch_google_accounts`). This is exactly the "collection proof" /
  "no material update" honesty Todd's canonical list requires — the
  **degraded status is itself being reported correctly**.
- **`session_bootstrap_spec`** is present and correctly instructs the GPT to
  emit a session-initialization confirmation (Micro Graph mount status,
  retrieval hierarchy) as the *first* output, before any user-facing response.

**Conclusion: code-side payload is structurally complete and canonical for
2026-06-15.** This is a **session-side gap**, not a `daily_brief.py` defect.
The live Custom GPT session that produced the "fallback brief" either (a) was
running against the stale pre-RB-9.81 Instructions/KB (the carried-forward
"Action required" re-sync, open since RB 9.81) and therefore did not know about
`session_bootstrap_spec`'s mandatory-first-output rule or the full canonical
section list, or (b) did not call `getDailyBrief`/`getDailyBriefPart2` at all
and improvised instead.

**Resolution path:** perform the carried-forward live GPT re-sync (re-paste
`custom_gpt_instructions_compact_8k.md`, re-upload the 5 KB files), then
re-run the same request that produced the fallback brief. If the re-synced GPT
now renders the full canonical structure, this defect is **closed by the
re-sync** — no `daily_brief.py` code change needed. If a re-synced GPT still
produces a fallback brief, escalate as a genuine prompt/instruction-following
defect (not a payload defect, since the payload is proven complete here).

**Status update:** Root cause identified as session-side / stale-GPT-sync.
**Live GPT re-sync completed 2026-06-15** (also carries the RB-DEFECT-048
HARD RETRIEVAL GATE fix). Awaiting live re-validation to close — re-run the
request that produced the fallback brief and confirm canonical rendering.
