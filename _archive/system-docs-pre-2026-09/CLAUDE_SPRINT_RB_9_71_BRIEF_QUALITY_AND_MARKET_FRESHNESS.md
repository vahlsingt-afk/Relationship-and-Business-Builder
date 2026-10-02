# RB 9.71 (proposed): Brief Quality & Market Freshness Cleanup

**Status:** Not started — scoped 2026-06-13, follows from RB 9.70 close-out
**Severity / value:** Medium — these are the items RB 9.70 explicitly deferred
to "keep today's change surface bounded." None are user-facing defects on
their own, but each chips away at brief trustworthiness (mandatory-coverage
claims that don't survive to the rendered brief, false escalations, stale
market intel).
**Depends on:** RB 9.70 (pending_mutations section, market_signals.json
refresh, opportunity store sync) — all shipped 2026-06-12/13.

---

## 1. Problem statement

RB 9.70's close-out (`system/STATUS.md` lines 5-11) deferred five items to a
future sprint, plus noted two related but unfiled findings. All are
contained, independently fixable, and don't require new infrastructure —
they're tuning/bugfix passes on code that's already live:

1. **Watchlist "scan complete" rollup doesn't survive to the rendered
   brief.** `_compute_watchlist_intelligence()` (`daily_brief.py:5428`)
   correctly builds per-bucket "No Change" rollups (e.g. a 56-entity
   "Restaurant Technology — No Change" item) when run in isolation, and
   places them first in its return value specifically so they survive
   downstream truncation. But in the live canonical-brief build, only 9 of
   28 raw `watchlist_intelligence` items appear in the published brief, and
   the Restaurant Technology rollup is missing entirely — likely a
   compaction/ordering step elsewhere in `build_canonical_brief()` drops or
   reorders items after `_compute_watchlist_intelligence()` returns them.

2. **`_name_in()` false-positive substring matching.**
   `_watchlist_entity_status()`'s `_name_in()` helper (`daily_brief.py:5391`)
   matches if every >2-char token of an entity name appears anywhere in the
   scanned text — as a raw substring, not a word boundary. This produces
   false "Escalation" classifications:
   - "GK Software" → matches on the bare word "software" in an unrelated
     `act_today` item.
   - "Hi Auto" → matches on "auto" inside "Autonomous".

   Both entities are in `MANDATORY_RESTAURANT_TECH`
   (`daily_brief.py:5454-5469`), so this directly pollutes the mandatory
   watchlist with false escalations.

3. **Loop owner field + action vocabulary** (RB 9.70 Tier 2 item #4 —
   referenced but not detailed in STATUS.md; needs investigation to confirm
   current scope before implementation. Likely touches
   `loops_and_obligations` / `_compute_intelligence_cycle_continuation`
   (`daily_brief.py:5685`) and `sent_followups_awaiting_response`.)

4. **Daily-brief section reordering** (RB 9.70 Tier 2 item #5 — also needs
   investigation; likely a `section_order` (`daily_brief.py` ~line 7767)
   adjustment based on operator feedback about reading order).

5. **Market signals ranking favors stale SEC filings over fresh vertical
   trade items.** `market_signals.py`'s `rank()` (line 680) sorts primarily
   by `priority_score` (line 627), which weights `strategic_relevance` ×100
   and `source_quality` ×20 — SEC 8-K filings from EDGAR typically score
   ~400-410, while curated vertical-trade items (from
   `system/inbox/market_signals.json`, refreshed in RB 9.70) score ~395-400
   even when fresher and more directly relevant to active threads (Global
   Payments, Perfect Hire). Result: `morning_headlines`'s top-7 cut keeps
   showing recycled 8-K filings instead of the newly-refreshed curated
   items.

6. **`refresh_all.py` has no `--market` step.** `system/inbox/market_signals.json`
   went 380h (15.8 days) stale before RB 9.70 manually refreshed it. Unless
   `--market` is added to the morning pipeline (`refresh_all.py`, currently
   only `--date` and `--confirm-passive-ri` flags exist — line 27-30), it
   will silently go stale again on the same schedule.

---

## 2. Approach per item

### Item 1 — Watchlist rollup survival (highest priority)

This is the one item that actively breaks a documented guarantee
(RB-INTEL-021: "every mandatory-coverage entity gets an explicit item").
Investigation plan:
- Trace `sections["watchlist_intelligence"]` from `_compute_watchlist_intelligence()`'s
  return (13278) through every subsequent pass that touches
  `sections["watchlist_intelligence"]` (13462, 13703, 13965, 15872) —
  attribution-type stamping, ORI block attachment, and whatever assigns the
  final rendered list — looking for a truncation/compaction call that uses
  a list slice or limit without special-casing rollup items (`extras.entity_type
  == "rollup"`).
- Once found, either (a) exempt rollup items from the limit the same way
  `pending_mutations` is exempted from the novelty filter, or (b) raise the
  limit specifically for `watchlist_intelligence` to accommodate
  rollups + top-N individual signals.
- Regression test: a fixture with 1 rollup item (56 entities) + 27 individual
  items → rendered brief's `watchlist_intelligence` section must include the
  rollup with its full `no_change_entities` list intact.

### Item 2 — `_name_in()` word-boundary fix

- Change `_name_in()` to require each name token to match as a whole word
  (regex `\bTOKEN\b` or split-on-non-alphanumeric + set membership), not a
  substring.
- Add a denylist or minimum-token-length check for generic single-word
  entity names ("Software", "Auto") — alternatively, require *all* tokens of
  multi-word names to match as whole words AND require single-word entity
  names (e.g. none in current lists, but defensive) to match a longer
  minimum length.
- Regression tests: "GK Software" must NOT match on bare "software"; "Hi
  Auto" must NOT match on "auto" inside "Autonomous"; existing true-positive
  matches (e.g. "PAR Technology" matching "PAR Technology Corp announces...")
  must still match.

### Items 3 & 4 — Loop owner field / section reordering

Both need a short investigation pass before implementation — STATUS.md
references them as "Tier 2 #4" and "#5" without restating the original
defect text. First step: locate the source defect/scope doc that originally
defined these (likely `CLAUDE_DEFECT_RB_040_DAILY_BRIEF_ARCHITECTURE_AUDIT.md`
or the RB 9.69/9.70 scoping docs) and confirm scope before estimating.

### Item 5 — Market signals ranking tuning

- Add a bonus to `priority_score` (line 627) for items sourced from the
  curated `system/inbox/market_signals.json` feed specifically when they
  relate to an active thread (cross-reference `active_threads.yaml` entity
  names against item title/summary) — mirroring the `combined`/`macro_force`
  bonuses already present (lines 631-633).
- Alternative/simpler: cap how many SEC 8-K-sourced items can occupy the
  top-7 `morning_headlines` slots (e.g. max 3 of 7), guaranteeing room for
  curated items regardless of raw `priority_score`.
- Regression test: a fixture with 5 high-`priority_score` 8-K items and 2
  lower-`priority_score` curated items tied to an active thread →
  `morning_headlines` top-7 includes at least one curated item.

### Item 6 — `refresh_all.py --market` step

- Add `--market` flag to `refresh_all.py` (mirrors `--confirm-passive-ri`
  pattern at line 30), wired to call `refresh_sources.py --market
  --save-health` (the exact command RB 9.70 ran manually).
- Decide cadence: every morning run, or a separate weekly cadence (market
  intel doesn't change daily the way relationship signals do — daily refresh
  may just re-fetch the same curated items). Recommend: add as a flag first
  (opt-in), observe for a week, then decide whether to make it
  unconditional in the morning pipeline.

---

## 3. Recommendation / sequencing

1. **Item 1 (rollup survival)** first — it's the only active breach of a
   documented coverage guarantee and likely a small, localized fix once the
   culprit pass is found.
2. **Item 2 (`_name_in` fix)** next — small, isolated, well-specified, two
   known failing cases to use as regression tests.
3. **Item 6 (`--market` flag)** — small, mechanical, prevents regression of
   RB 9.70's freshness fix.
4. **Item 5 (ranking tuning)** — needs more judgment (don't want to overcorrect
   and bury genuinely high-relevance SEC filings); do after 1/2/6 are done
   and with a fresh look at `morning_headlines` output.
5. **Items 3 & 4** — investigate scope first; if small, fold into this
   sprint; if they turn out to be larger redesigns, split into RB 9.72.

---

## 4. Success criteria

- Live canonical brief's `watchlist_intelligence` section includes the
  per-bucket "No Change" rollup item(s) with full `no_change_entities` lists,
  verified against a real brief regeneration (not just unit tests).
- "GK Software" and "Hi Auto" no longer appear as false "Escalation" entries
  when their generic-word substrings appear elsewhere in the brief.
- `refresh_all.py --market` exists and successfully runs
  `refresh_sources.py --market --save-health`.
- `morning_headlines` top-7 includes at least one curated `market_signals.json`
  item when one exists and ties to an active thread, even when SEC 8-K items
  have higher raw `priority_score`.
- No regression to the 2364-test baseline (RB 9.70).

## 5. Effort estimate

- Item 1: ~1-2 hours investigation + fix, once the offending pass is located.
- Item 2: <1 hour — isolated helper function + 2-3 new tests.
- Item 6: <30 min — mechanical flag addition.
- Item 5: ~1 hour — tuning + regression test, plus manual review of
  `morning_headlines` output before/after.
- Items 3 & 4: unscoped — investigate first (~30 min), then estimate.
- Total: likely a single session if items 3/4 are small; otherwise items 3/4
  split to RB 9.72.
