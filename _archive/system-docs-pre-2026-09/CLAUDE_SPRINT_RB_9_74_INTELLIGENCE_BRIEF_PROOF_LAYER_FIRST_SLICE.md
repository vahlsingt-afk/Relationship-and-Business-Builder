# RB 9.74: Intelligence Brief Proof-Layer — First Slice of RB-DEFECT-043

**Status:** Implemented (2026-06-14)
**Source:** Triage of `CLAUDE_DEFECT_RB_043_INTELLIGENCE_BRIEF_VS_DAILY_BRIEF_ARCHITECTURE.md`
(8 defects, Critical, filed 2026-06-14).

## Audit summary

RB currently publishes ONE brief document (`system/published/daily/`), driven by
`brief_display_order` (~85 sections). "Intelligence Brief" content is the front
buckets (1-3, per RB 9.72's reordering); "Daily Brief"/CoS content is the back
buckets (5-8). A genuine two-document split — Layer 1 (fact/evidence proof layer) vs
Layer 2 (CoS decision layer) — is a real architectural change and remains deferred to
a multi-sprint RB 9.75+ track, as the defect report itself anticipated.

This sprint took the 3 smallest, safest, code-only items from the 8 defects — items
that improve both layers without requiring the document split — and implemented them
in `system/scripts/daily_brief.py`.

## Item-by-item disposition

| # | Defect | Disposition |
|---|--------|-------------|
| 1 | Intelligence Brief reads like an opinion piece | **Addressed (rendering rule)** — new "EVIDENCE BEFORE SYNTHESIS" rule bans trend/strategic framing in the front-page fact sections; conclusions must be followed by their evidence list. |
| 2 | No proof of work | **Already implemented** (RB-INTEL-021: `intelligence_collection_summary`, `source_scan_manifest`, pipeline status block). No change. |
| 3 | No source links | **Addressed (rendering rule)** — new "SOURCE LINKS REQUIRED" rule: every fact-layer item must show source/date/link, or explicitly say `[no source link available]`. |
| 4 | Watchlist too narrow | **Addressed (code)** — added Agilysys, Botrista, DoorDash, Uber Eats, Wonder to `MANDATORY_RESTAURANT_TECH` (the rest of Todd's ecosystem list — POS, back-office, loyalty, payments, ordering, AI, robotics — was already present). |
| 5 | Relationship intelligence missing | **Deferred** — overlaps RB-DEFECT-041 / RB 9.73 (already triaged, mostly implemented). |
| 6 | Communication intelligence missing | **Deferred to RB 9.75+** — needs a new `communication_intelligence` section (calendar/SMS/open-commitments rollup); not a small slice. |
| 7 | No delta reporting | **Addressed (code)** — `what_changed_since_yesterday` now includes a "Watchlist Delta Summary (Today)" item aggregating `watchlist_intelligence` status counts (Escalated / New activity / Relevant activity / No change) computed from the existing per-entity status function. |
| 8 | Daily Brief not CoS enough | **Addressed (rendering rule)** — new "INTELLIGENCE -> COS VIEW FRAMING" rule requires `executive_summary`/`connect_the_dots`/`decision_layer` items to render as `Intelligence: ...` / `CoS view: ignore|act — ...` pairs. |

## Changes made

`system/scripts/daily_brief.py`:
- `MANDATORY_RESTAURANT_TECH` (~line 5478): added Agilysys, Botrista, DoorDash, Uber
  Eats, Wonder.
- `what_changed_since_yesterday` build block (~line 13430): appended a
  "Watchlist Delta Summary (Today)" canonical item aggregating
  `watchlist_intelligence` extras (`watchlist_status`, `no_change_count`).
- `rendering_rules` (~line 14271): added 3 new rules (EVIDENCE BEFORE SYNTHESIS,
  SOURCE LINKS REQUIRED, INTELLIGENCE -> COS VIEW FRAMING).

## Deferred to future sprints

- **RB 9.75+ (multi-sprint, per RB-DEFECT-043's own recommendation):** the full
  Layer 1 / Layer 2 document split — a genuinely separate Intelligence Brief
  artifact distinct from the Daily Brief, plus Defect #6's new
  `communication_intelligence` section.
- **RB 9.73b** (already recorded in STATUS.md "Next up"): strategic account mapping
  and structured title/company history arrays from the RB-DEFECT-041 triage.
