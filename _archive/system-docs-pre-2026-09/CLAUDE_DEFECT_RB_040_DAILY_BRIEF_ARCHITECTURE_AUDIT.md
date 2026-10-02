# RB-DEFECT-040: Daily Brief Architecture Audit (response to Todd's 9-defect report, 2026-06-12)

**Date:** 2026-06-12
**Status:** Two root-cause bugs fixed and verified (2353/0 tests); remaining
items triaged and scoped as RB 9.70 (see §4).
**Trigger:** Todd's "Relationship Bridge Defect Report — Daily Brief,
Intelligence Brief, and Personal Operating System Architecture."

---

## 1. Summary

Todd filed a 9-point architectural defect report arguing the Daily Brief
behaves like a "newsletter from conversational memory" rather than a
Chief-of-Staff product driven by fresh intelligence collection. An audit
against the **actual rendered output** of `build_canonical_brief()` (not
just code presence) found that most of the requested machinery already
exists from prior sprints (RB-INTEL-021, RB-DEFECT-021/036/037/038) — but
two of it were **silently broken by key-naming/data-flow bugs**, which
cascade into several of Todd's 9 defects looking like missing features.

## 2. Defect-by-defect status (as observed before today's fixes)

| # | Defect | Status before fix | Status after fix |
|---|---|---|---|
| 1 | Front Page Headlines (dated/sourced/linked, fresh only) | Rendering, but fed stale (380h old) Market Intelligence → SEC 8-K filings shown as "headlines" | **Root cause now visible** (was masked — see §3.2); headline freshness is a *source* problem, scoped for 9.70 |
| 2 | Collection Status (✓/⚠ per source) | Always rendered "Not Available" — `execution_report` was never populated on `report` | **FIXED** — now renders "Collection Status: Degraded ⚠ \| Sources: 7/18 healthy \| Freshness: 43% \| Trust: 24%" + a 9-source "Collection Issues" breakdown |
| 3 | Company Watch List Changes / "no material developments" rollup | Only 7 escalation items, no rollup/"scan complete" item visible | Partially addressed by RB-DEFECT-039's earlier "No Change" rollup fix; full ~100-entity coverage check scoped for 9.70 |
| 4 | Separate Intelligence Brief product (all signals, by category, with counts) | Not built — `/intelligence/signals` is a flat mutation ledger, not a categorized brief | Not built — scoped for 9.70 |
| 5 | Signal drill-down by stable ID ("show me RT-021") | Not built — no stable signal IDs, no get-by-ID endpoint | Not built — scoped for 9.70 |
| 6 | 7/30/90-day calendar horizons with prep lead-time | Not built — no horizon/lead-time logic anywhere | Not built — scoped for 9.70 |
| 7 | Open Loop Management (owner/status/priority/recommended action incl. escalate/convert-to-task) | Mostly present (`loops_and_obligations`: title/summary/recommended_action/disposition) but no `owner` field, limited action vocabulary | Unchanged — scoped for 9.70 |
| 8 | Opportunity detection ("where should Todd focus") | `active_opportunity_pipeline` returned 0 items today despite an active Global Payments offer-stage change | **FIXED for today's data** (backfilled via `opportunity_pipeline.process_opportunity_update`); root cause (dual opportunity stores not kept in sync — see §3.3) scoped for 9.70 |
| 9 | Relationship Intelligence (who engaged/viewed/responded) | Present and populated, but buried at section-order positions ~19/~38, far below Todd's proposed near-top placement | Unchanged (ordering decision) — scoped for 9.70 |

## 3. Root causes found and fixed today

### 3.1 `report["execution_report"]` key bug (fixes Defect 2, contributes to 1)

`_intelligence_collection_summary_items()` (`daily_brief.py:2858`) and the
"what_todd_doesnt_know_yet" front-page logic (`daily_brief.py:~13496`) both
read `report.get("execution_report")`. But `build_report()` never set that
top-level key — the morning pipeline's execution report was only nested at
`report["morning_pipeline_state"]["execution_report"]`. Both readers always
saw `{}`, so:

- The Collection Status section *always* rendered the "Not Available"
  fallback, regardless of whether the morning pipeline actually ran (it
  does run — `.cache/morning_pipeline.json` has full per-source health for
  18 sources).
- The front-page "sources scanned" count fell back to a hardcoded
  estimate of `8`.

**Fix** (`daily_brief.py`, in `build_report()`, immediately after the
report dict is assembled):

```python
report["execution_report"] = (
    (report.get("morning_pipeline_state") or {}).get("execution_report") or {}
)
```

**Verified output** (today, 2026-06-12):

```
Intelligence Collection Summary — 2026-06-12 12:05:08 UTC
Collection Status: Degraded ⚠ | Sources: 7/18 healthy | Freshness: 43% | Trust: 24% | Mutations: 10

Collection Issues — 9 source(s) not healthy
  • Calendar (Bridgepoint): last refresh is 27.1h old; threshold is 24h
  • Calendar (Personal): last refresh is 27.1h old; threshold is 24h
  • Email (Bridgepoint): last refresh is 27.1h old; threshold is 24h
  • Email (Personal): last refresh is 27.1h old; threshold is 24h
  • LinkedIn Messages: last refresh is 119.6h old; threshold is 48h
  • Market Intelligence: last refresh is 380.2h old; threshold is 72h
  ...
```

This is **exactly** the Collection Status table Todd asked for in Defect 3
— it was computed correctly by `morning_pipeline.py` / `intelligence_observability.py`
all along, just never reaching the brief.

### 3.2 Market Intelligence source is 380 hours (15.8 days) stale (Defect 1 root cause)

The fix in 3.1 didn't just unlock Collection Status — it also **exposed**
why Defect 1 (stale/recycled headlines) happens: `Market Intelligence` —
the source `_morning_headlines_section_items()` draws from — last
refreshed **2026-05-27**, 16 days ago. The function itself works
correctly; it's faithfully reporting from a stale cache. This is a source-
refresh problem (the web/market scan step in `morning_pipeline.py` isn't
running or is failing silently), not a headline-rendering problem. Scoped
as the first item in 9.70 — likely the single highest-leverage fix for
Defect 1, since the rendering logic doesn't need to change at all once the
source is fresh.

### 3.3 Two parallel opportunity-tracking stores (Defect 8 root cause)

`active_opportunity_pipeline` (the section Todd expects to answer "where
should I focus") is driven by `opportunity_pipeline.py`'s own JSON store
(`system/tracked_opportunities.json`, via `recent_changes(within_days=1)`),
which is **separate from** `active_threads.yaml` (the store RB-DEFECT-039
updated for the Global Payments offer-stage change). Updating one does not
update the other. Today's brief showed 0 active-opportunity items despite
an active, just-changed Global Payments thread, because the change was
recorded in `active_threads.yaml` but not in `tracked_opportunities.json`.

**Backfilled today**: ran
`opportunity_pipeline.process_opportunity_update(company="Global Payments",
stage_override="offer_verbal", apply=True)`, which now produces:

```
Career pipeline update — Global Payments
NEW SINCE LAST BRIEF: Global Payments — current stage: offer_verbal.
[2026-06-12] stage: offer_verbal
```

**Not fixed**: the underlying duplication. `active_threads.yaml` and
`tracked_opportunities.json` represent overlapping but distinct views of
the same career-opportunity state, with no reconciliation. Scoped for 9.70.

## 4. Scope for RB 9.70 (next sprint — not started)

In priority order, each addressing one or more of Todd's remaining defects:

1. **Fix Market Intelligence source refresh** (Defect 1). Investigate why
   `morning_pipeline.py`'s web/market scan step hasn't updated
   `system/.cache/market_signals.json` (or equivalent) since 2026-05-27.
   This is likely the single highest-impact fix remaining — once headlines
   are fresh, `_morning_headlines_section_items()` should produce real
   Global/US/Restaurant Industry/Restaurant Tech news without further
   rendering changes.
2. **Reconcile `active_threads.yaml` ↔ `tracked_opportunities.json`**
   (Defect 8). Either make `mutations.py`'s thread-update path also call
   `opportunity_pipeline.process_opportunity_update` for `job_opportunity`-
   type threads, or merge the two stores. Add a regression test mirroring
   today's Global Payments case.
3. **Full watch-list coverage + "scan complete, no material developments"
   rollup** (Defect 3). Verify `_compute_watchlist_intelligence` actually
   iterates the full ~100-entity mandatory coverage list and emits an
   explicit "no material developments" item per bucket when nothing
   changed, not just when items happen to be present.
4. **Open Loop `owner` field + expanded action vocabulary** (Defect 7).
   Add `owner` to loop records (default: Todd) and extend
   `recommended_action` generation beyond close/re-date to include
   escalate / defer / convert-to-task / new-loop, per Todd's spec.
5. **Section reordering** (Defect 9 + Todd's proposed top-level structure).
   Once items 1-4 land, revisit `section_order` to move
   `intelligence_collection_summary` → front-page headlines → company
   watch-list changes → personal/relationship intelligence → today →
   open loops → forward look → CoS perspective, matching Todd's proposed
   structure. This is purely an ordering change once the underlying
   sections are populated correctly.
6. **Intelligence Brief product + signal drill-down** (Defects 4, 5) —
   largest net-new scope. Needs: stable signal IDs (e.g. `RT-021`)
   assigned at collection time, a categorized signal index with counts,
   and a `/intelligence/signals/{id}` detail endpoint. Recommend scoping
   as its own sprint (9.71) after 9.70's fixes are live, since it's
   additive rather than a fix to existing broken behavior.
7. **7/30/90-day calendar horizons with prep lead-time** (Defect 6) —
   net-new, no existing code to build on. Also recommend its own sprint
   (9.72): needs a "prep effort estimate by event type" heuristic plus a
   lead-time → start-date calculation, surfaced in a new `forward_look`
   section.

## 5. Verification

- Both fixes (3.1, 3.3) applied; full suite re-run: **2353 passed, 0
  failures**.
- `intelligence_collection_summary` and `active_opportunity_pipeline`
  sections both verified non-empty and correctly populated for today's
  brief (2026-06-12).
