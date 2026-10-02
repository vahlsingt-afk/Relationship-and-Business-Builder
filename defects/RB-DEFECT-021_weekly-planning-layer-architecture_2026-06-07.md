# RB-DEFECT-021: Daily Brief Lacks Strategic Time Horizon and Goal Framework

**Filed:** 2026-06-07
**Severity:** High
**Category:** Chief of Staff Decision Quality / Architecture (not a UI enhancement)
**Status:** Core layer implemented and verified — 2026-06-08 (remaining sub-items below)

## Reframing

This is correctly classified as an **architectural defect**, not a brief-formatting
request. The Daily Brief is being asked to answer a question — "what matters this
week, and how should that shape today?" — that has no data layer beneath it to
answer from. You cannot fix this by re-ranking brief sections; there is currently
**nothing to rank against**. The fix is to build the missing layer one level above
the brief, then make the brief a *consumer* of it.

## Confirmed current architecture (so the fix plugs in cleanly)

- **Brief assembly**: `system/scripts/daily_brief.py:577` `build_report(today)`,
  orchestrated by `morning_pipeline.py`. It pulls from ~10 subsystems (loop ledger,
  opportunity sensing, relationship signals, market signals, strategic
  events/memory/operators, ecosystem intelligence, comms health) and assembles a
  ~25-section report dict.
- **Ranking today**: a single `_strategic_significance_score`
  (`daily_brief.py:899-907`, 0-100) drives what surfaces — confidence × weight ×
  corroboration, with **no reference to any goal, outcome, or weekly priority**,
  because none exists. This confirms the root cause exactly as filed: every item
  competes on its own intrinsic score, with no context for *why it matters this week*.
- **Day-type awareness**: partially exists. `cos_judgment.py:801-805`
  (`_is_weekend` / `build_weekend_guidance`) already returns a `mode`
  (`sunday_prep` / `saturday_recovery`) consumed at `daily_brief.py:750`. This is the
  right *shape* for DEFECT-021A — it needs to be generalized from a binary
  weekend/weekday split into a full weekly operating-mode cycle (see below), not
  built from scratch.
- **No weekly/objective/portfolio/outcome data structures exist anywhere** — confirmed
  via repo-wide search; the concepts appear only in narrative handoff docs, never as
  live schema. This is a clean slate, not a migration.
- **Plug-in points already present**: `system/loop_ledger.md` (loop IDs
  `L-YYYY-MM-DD-NNN`, parsed by `rb_core.parse_loop_ledger`) and
  `system/opportunities/*.md` (per-opportunity files, IDs from
  `opportunity_intake.py`) are the two canonical id-bearing structures a weekly
  outcomes layer must cross-reference.

## Architecture: a new layer, not a brief rewrite

Introduce **`system/weekly_plan.json`** (or `system/weekly/<week-of>.json`) as a new,
independent data layer sitting *above* the brief in the dependency graph — the brief
reads it; it does not read the brief.

```json
{
  "week_of": "2026-06-08",
  "operating_mode_calendar": {
    "monday": "weekly_planning",
    "tue_thu": "execution",
    "friday": "weekly_review",
    "saturday": "recovery",
    "sunday": "weekly_prep"
  },
  "outcomes": [
    {
      "id": "WK-2026-06-08-01",
      "title": "Resolve Genius opportunity",
      "success_criteria": "Definitive yes/no decision",
      "linked_opportunity_ids": ["genius-global-payments"],
      "portfolio_allocation_pct": 40,
      "status": "active"
    }
  ],
  "portfolio_allocation": {"genius": 40, "foods_connected": 35, "hari": 15, "relationships": 10},
  "risks": [...],
  "forcing_functions": [...]
}
```

This single file structurally answers DEFECT-021A/B/C/F's root cause at once: once
outcomes + allocation + an operating-mode calendar exist as *data*, the scoring
function in `daily_brief.py:899` has something authoritative to score against, and
day-type logic in `cos_judgment.py` has more than two modes to return.

### How each defect resolves once this layer exists

- **021A (day awareness)**: generalize `cos_judgment.build_weekend_guidance` →
  `build_operating_mode(today, weekly_plan)`, returning one of the five modes from
  `operating_mode_calendar` (not just weekend/weekday). The brief asks "what mode is
  today?" *before* generating execution recommendations — gating, not ranking.
- **021B (portfolio allocation)**: `weekly_plan.portfolio_allocation` becomes a new
  top-level brief section, and the existing `_strategic_significance_score` gains a
  multiplicative **outcome-alignment factor**: items that map to a
  `linked_opportunity_id` in an active outcome get boosted proportional to that
  outcome's allocation %; unlinked items get **dampened**, not just left unranked.
  This is the literal mechanism for "suppress vs. elevate."
- **021C (maintenance vs. strategic)**: becomes a *display-policy rule*, not a
  scoring tweak — extend `_display_policy_for_item` (`daily_brief.py:910`) so
  maintenance-class loops (e.g., recurring relationship-touch loops like Dave
  Richards) are gated to surface only if: due today, `blocks` an active weekly
  outcome (new `blocks_outcome_id` cross-reference field on loop-ledger entries), or
  flagged reputational-risk. Otherwise suppressed by policy, regardless of score.
- **021D (relationship activity → intelligence)**: not solved by the weekly layer
  directly — this is the same class of problem as **RB-DEFECT-029** (content →
  mutation, not raw activity counts). The fix is the same: relationship-signal
  summarization must answer "what changed about the relationship" (a mutation/delta),
  not "how many messages." Cross-reference DEFECT-029's mutation-engine wiring as the
  shared mechanism — a `relationship_mutation` record (already designed there) is
  exactly what should render here instead of an activity count.
- **021E (headline → implication)**: a synthesis-layer requirement on
  `market_signals`/`opportunity_sensing` output — each surfaced signal needs a
  derived `implication` field (pain/buying-behavior/operator-pressure/opportunity/
  relationship/theme), populated at ingestion (likely the same
  `intelligence_mutation_engine` pipeline from DEFECT-029, since "extract implication
  from text" is the same capability as "extract thesis-alignment from text").
- **021F (macro layer)**: a new structured signal category
  (labor/consumer-spending/inflation/rates/traffic/earnings/AI-investment) feeding
  `market_signals`, scored for *relevance to active weekly outcomes* — i.e., this
  category only earns brief real estate when it can be tied back to
  `weekly_plan.outcomes`, which is precisely the gating this whole layer exists to
  enable.
- **021G (source navigation)**: a rendering-contract requirement — every elevated
  signal item carries `{source, date, est_reading_time, why_it_matters}`. Cheapest of
  the seven; can be done independent of the weekly layer, but should land alongside
  it since "why it matters" *is* the outcome-alignment explanation once the layer
  exists.

## New cadence artifacts (the four time horizons)

| Horizon | Trigger | Reads | Writes |
|---|---|---|---|
| Weekly Planning (Mon) | `operating_mode == weekly_planning` | open loops, opportunities, prior week's `weekly_plan` | new `weekly_plan.json` (outcomes, allocation, risks) |
| Daily Brief (daily) | existing pipeline | `weekly_plan.json` + existing inputs | brief report, scored/gated against active outcomes |
| Friday Review | `operating_mode == weekly_review` | `weekly_plan.json` + week's loop/opportunity deltas | `weekly_scorecard_<date>.json` (wins/misses/movement/score) |
| Monthly Strategy | 4th Friday or explicit trigger | trailing 4 `weekly_scorecard` files | portfolio-review narrative, "things to stop doing" |

Each is a thin orchestration script in `system/scripts/`, following the existing
pattern of `cos_judgment.py` (judgment/guidance generation) and `daily_brief.py`
(assembly) — no new framework needed.

## Scope for Codex

1. Define and create `weekly_plan.json` schema + a `weekly_planning.py` generator
   that runs in `weekly_planning` mode (Monday), producing outcomes/allocation/risks
   from current open loops + opportunity pipeline.
2. Generalize `cos_judgment.build_weekend_guidance` → `build_operating_mode`
   supporting the full five-mode weekly calendar (021A).
3. Add outcome-alignment scoring factor to `_strategic_significance_score`
   (`daily_brief.py:899`) and a `blocks_outcome_id` cross-reference to loop-ledger
   schema, wired into `_display_policy_for_item` (021B/021C).
4. Add `weekly_review.py` (Friday scorecard generator) and a monthly strategy
   review generator reading trailing scorecards.
5. Land 021D and 021E as shared work with **RB-DEFECT-029** — both require the same
   underlying capability (text/activity → extracted implication/mutation, not raw
   counts/headlines). Do not build parallel synthesis logic; route through
   `intelligence_mutation_engine`.
6. Add `{source, date, est_reading_time, why_it_matters}` to elevated-signal render
   contract (021G) — independent, low-risk, can land first as a quick win.
7. Add macro-signal category (021F), gated for surfacing by relevance to active
   `weekly_plan.outcomes`.

## Design principle this enforces

> Every recommendation engine pass should ask: "Does this move one of this week's
> active outcomes forward?" If no → suppress (or dampen). If yes → elevate
> proportional to that outcome's portfolio allocation.

This is implemented as a **multiplicative scoring factor + display-policy gate**
layered onto the existing `_strategic_significance_score` / `_display_policy_for_item`
machinery — not a replacement of it. The existing scorer correctly measures "is this
item credible and significant"; the new layer adds "...and does it matter *right now*
relative to what we said mattered this week."

## Non-goals

- Not a rewrite of `daily_brief.py`'s assembly/scoring engine — it's sound at
  measuring item-level significance; it's missing the *reference frame*.
- Not solved by reordering brief sections — the defect is the absence of a goal
  layer, not a presentation defect.
- 021D/021E should not spawn a second extraction pipeline parallel to
  `intelligence_mutation_engine` (DEFECT-029) — that would create exactly the kind of
  duplicated, drifting logic this system already struggles with.

## Implementation Result (core layer — 2026-06-08)

Built and verified by Claude (architecture + engineering, per role clarification):

- **New module `system/scripts/weekly_planning.py`**: `weekly_plan.json` schema
  (`new_plan`/`make_outcome`/`load_plan`/`save_plan`/`is_current`/`active_outcomes`),
  the five-mode operating calendar (`operating_mode_for` — weekly_planning /
  execution / weekly_review / recovery / weekly_prep, each with a framing question),
  multiplicative outcome-alignment scoring (`alignment_factor_for_item`/
  `apply_alignment` — aligned items boosted proportional to allocation %, unaligned
  dampened by `UNALIGNED_DAMPENING_FACTOR=0.7`), the maintenance-loop display gate
  (`maintenance_should_surface` — due/blocks-outcome/reputational-risk only), and
  Friday scorecard schema + persistence (`make_scorecard`/`save_scorecard`/
  `load_recent_scorecards`).
- **`cos_judgment.py`**: added `build_operating_mode()` generalizing the existing
  binary `build_weekend_guidance` (line 805, untouched/still functional) into the
  full five-mode weekly cycle (021A done).
- **`daily_brief.py`**:
  - loads/caches the active week's plan (`_current_weekly_plan`, degrades to `None`
    gracefully when absent or stale — verified neutral 1.0 factor / unchanged
    behavior with no plan present)
  - new `report["operating_mode"]` and `report["weekly_plan"]` sections (outcomes,
    allocation, risks, forcing functions)
  - `_strategic_significance_score` output now passed through `wp.apply_alignment` —
    additive layer, not replacement (verified live: 50→70 for an item aligned to a
    40%-allocation outcome, 50→35 for an unaligned item) (021B done)
  - `_display_policy_for_item` gates `item_class == "maintenance_loop"` items through
    `maintenance_should_surface` — suppressed by default unless
    due/blocks-outcome/reputational-risk (021C done)
  - `_apply_intelligence_contract` now attaches `source_navigation`
    (`{source, date, est_reading_time_minutes, why_it_matters}`) to every
    contract-bearing item via new `_source_navigation_block`/
    `_estimated_reading_minutes` helpers (021G done)

**Verification:**
- `py_compile` clean on all three touched/new files
- live save→load→pickup of a real `weekly_plan.json` confirmed end-to-end (plan
  appears in `report["weekly_plan"]`, alignment factors apply immediately); test
  artifact removed after verification
- 120 existing daily-brief / cos-judgment regression tests pass unchanged

## Implementation Result (Monday plan-generation orchestrator — 2026-06-08)

Built and verified by Claude:

- **New module `system/scripts/weekly_plan_generator.py`**: derives *candidate*
  outcomes from system-of-record data — `active_threads.yaml` (job_opportunity /
  opportunity / recruiter_engagement / partnership threads, ranked by
  `boost_for_brief`) and the loop ledger (overdue + due-today loops, surfaced as
  a guaranteed "operational stability" candidate so they can't get crowded out by
  aspirational outcomes). Caps at `MAX_CANDIDATE_OUTCOMES = 5` — per 021's own
  design principle, a plan with 8 "priorities" is a wishlist, not a plan.
- **Deliberately propose-then-confirm**, mirroring the mutation-engine /
  opportunity-intake pattern already used elsewhere in RB — outcome selection is a
  strategic-judgment call RB should never silently auto-commit to:
  - default invocation prints a human-readable proposal (`render_proposal`)
  - `--write-draft` persists to `weekly_plan_draft.json` (status:
    `draft_pending_confirmation`, carries `generation_provenance` showing *why*
    each outcome was proposed — source + linked companies — so a human reviewer
    isn't reverse-engineering RB's reasoning)
  - `--confirm` promotes the draft to the live `weekly_plan.json` via
    `wp.save_plan`, but **only after checking `wp.is_current(draft, today)`** —
    a stale draft (generated for a prior week) is rejected with a clear message
    rather than silently becoming this week's plan
- **Transparent allocation math** (`_allocate`): high-boost / loop-stability
  candidates get double weight, remainder split evenly, rounding-drift corrected
  so allocations always sum to exactly 100 — a starting proposal the human is
  expected to adjust, not a black box.
- **Guards against ugly fallback text**: thread `status` tokens (`"open"`,
  `"closed"`) are state markers, not guidance — `_candidate_from_thread` only uses
  `status` as a `success_criteria` fallback when it actually reads like a sentence
  (>3 words), otherwise falls through to a generated "move X to its next concrete
  milestone" phrase.

**Live-tested**: generated a real 5-outcome draft from the live system —
`Advance Genius / Global Payments role conversation` (23%), `Advance Patrick
Nelson / Matrix Software Solutions follow-up` (22%), `Close out overdue and
due-today loops` (22%, correctly linked `L-2026-05-26-004` + `L-2026-05-08-001`),
`Advance Coates Group / Director, McDonald's Global Account role` (22%), `Advance
Foods Connected Commercial Director opportunity` (11%) — allocations sum to 100,
provenance correctly attributes each to its source. Full propose → write-draft →
confirm → `is_current`/`active_outcomes`/`alignment_factor_for_item` lifecycle
verified end-to-end against temp paths (no live-system files touched during
testing).

**New test file** `system/tests/test_weekly_plan_generator.py` — 10/10 pass,
covering allocation math (sums to 100, weighting, empty-input), candidate
derivation (name-required guard, next_step-vs-status-token preference, loop
surfacing), draft construction (cap enforcement, provenance alignment), proposal
rendering, and the full confirm lifecycle including the **stale-week rejection
guard** (a prior-week draft must not silently become this week's live plan).

**Net effect**: `weekly_plan.json` no longer requires manual hand-authoring to
come into existence — running `weekly_plan_generator.py --write-draft` then
reviewing and `--confirm`-ing produces a real plan, which immediately activates
021's outcome-alignment scoring (previously confirmed to run in neutral
passthrough without one) and clears the honest "no plan on file" flag the brief
now surfaces via `weekly_plan_focus` (RB-DEFECT-031 wiring).

## Implementation Result (Friday review orchestrator — 2026-06-08)

Built and verified by Claude:

- **New module `system/scripts/weekly_review_generator.py`**: observes the live
  week's `weekly_plan.json` against current system-of-record state (loop ledger
  resolution + linked-thread status tokens) and proposes a *draft* scorecard —
  per-outcome movement classification (`advanced`/`no_movement`/`stalled`/`closed`),
  wins/misses, and a proposed `overall_score` (% of outcomes advanced-or-closed).
- **Same propose-then-confirm discipline** as the Monday generator — RB observes
  and proposes a read on the week; it does not self-grade silently:
  - default invocation prints a human-readable proposal (`render_proposal`) showing
    each outcome's classified movement *and the observed rationale* (e.g.,
    `"all linked loops closed (L-2026-...)"` / `"linked thread 'x' status='stalled'"`)
    so a reviewer can correct RB's read rather than reverse-engineer it
  - `--write-draft` persists to `weekly_scorecard_draft.json`
    (`status: draft_pending_confirmation`, carries `generation_provenance`)
  - `--confirm` promotes via `wp.save_scorecard`, but only after checking
    **both** that a live current-week plan exists (refuses to score against
    nothing) **and** that the draft's `week_of` matches the live plan's —
    guards against a stale draft silently becoming this week's record
- **Honest "no signal" classification**: outcomes with no linked loop/opportunity
  ids, or no advancing/stalling signal observed, are classified `no_movement` with
  an explicit rationale (`"no advancing/stalling signal observed..."` or `"...carries
  no linked loop/opportunity ids to observe — manual read required"`) rather than
  guessing — preserving the system's "name the gap, don't fabricate" discipline.
- **Movement-classification precedence**: linked-loop resolution is treated as the
  most concrete signal (a loop is either closed or it isn't) and checked first;
  linked-thread status tokens (`advanced`/`closed`/`won`/`stalled`/`blocked`/etc.)
  are the fallback when no loop signal is available.

**Live-tested**: full propose → write-draft → confirm lifecycle verified end-to-end
against temp paths (no live-system files touched) — a two-outcome plan with one
closed-loop outcome and one stalled-thread outcome correctly classified as
`closed`/`stalled`, wins/misses correctly populated (`overall_score: 50`), draft
correctly promoted to `scorecard_<week_of>.json` via `wp.save_scorecard`. Both
guard rails (no-live-plan, week_of mismatch) verified to refuse confirmation with
clear messages. Note: no live `weekly_plan.json` exists yet (only a Monday draft
pending human confirmation), so this orchestrator currently has nothing live to
review — that's the Monday orchestrator's confirmation step unblocking this one,
not a defect in either.

**Net effect**: `make_scorecard`/`save_scorecard` (schema landed 2026-06-08) are no
longer reachable only by hand-authoring a scorecard — running
`weekly_review_generator.py --write-draft` then `--confirm` on a Friday produces a
real `scorecard_<week_of>.json`, which `load_recent_scorecards` will pick up,
giving the Monthly Strategy horizon (4th-Friday trailing-scorecard review) something
to actually read for the first time.

## Implementation Result (Monday auto-draft trigger — 2026-06-08)

**The gap this closes:** The Monday planning engine (`weekly_plan_generator.py`)
existed but nothing *started* it — it sat idle until someone manually ran it.
That's the same "computed but not wired" failure class as the other 021/031
defects, just one layer earlier (orchestration, not rendering). On a Monday with
no live plan in place, the brief's `weekly_plan_focus` section said "run the
weekly planning step" — but the step itself hadn't fired. The brief could not
show what the plan *would be*; it could only point at a gap.

**What was built:**

- **`_maybe_autodraft_weekly_plan(today)` in `daily_brief.py`**: called from
  `_weekly_plan_focus_items` when no current plan is in place. Behavior by day:
  - **Monday** + no draft/live plan: generates a 5-outcome proposal via
    `wpg.build_draft_plan`, persists it to `weekly_plan_draft.json`, sets
    `_autodraft_generated_today: True`. The brief immediately renders the
    proposal inline — outcomes with allocations, note that it was generated
    automatically, recommended action to review and confirm.
  - **Monday** + draft already exists for this ISO week: returns existing draft
    unchanged — idempotent if brief is re-pulled later the same day.
  - **Any other day** + no draft: returns None → falls through to the existing
    passive "run the planning step" message.
  - **Any other day** + stale draft from prior week: ignored.
  - **Live plan is current** (`is_current: True`): `_maybe_autodraft` is never
    called — normal outcome-rendering path proceeds as before.
- **`_weekly_plan_focus_items` rewrite**: when the auto-draft returns a
  current-week draft, renders it as a confirmable proposal with `disposition:
  "ask_todd"` — not `"act"` — because reviewing outcomes is a judgment call,
  not a task. Surfaces outcome list with allocations, generation note, and
  explicit recommended action. `extras.draft_outcomes` + `extras.week_of`
  included for GPT rendering.
- **Routing update in `custom_gpt_instructions_compact_8k.md`**: Daily Brief
  routing note updated to name Monday auto-draft as an expected state — when
  `weekly_plan_focus` carries `disposition: ask_todd` and `_autodraft_generated:
  true` metadata in extras, the GPT surfaces it as a "here's this week's
  proposed plan — confirm to activate" prompt, not as a passive note.

**Live-tested**: three scenarios verified — Monday first call (generates draft,
correct outcomes + allocations), Monday second call (idempotent, no regen),
live-plan-in-place (normal outcomes path — no autodraft trigger). Syntax clean.

**Net effect**: Monday morning, first brief of the week now arrives with a ready
proposal already in it. The user sees: "Here are this week's proposed outcomes.
Review and say 'confirm' to make this the live plan." One confirm later,
outcome-alignment scoring is active for the entire week — no manual script run
required, no gap between "brief exists" and "plan exists."

**Remaining for full 021 closure** (independent follow-ons, not blocking the core
layer's or either orchestrator's value):
- 021D/021E/021F — **Resolved 2026-06-08** (see Implementation Result below)
- Monthly Strategy review generator (reads trailing 4 `weekly_scorecard` files —
  schema/loader (`load_recent_scorecards`) exist; the generator itself does not yet.
  Now unblocked: once 2-4 weeks of real scorecards accumulate via this orchestrator,
  this becomes the natural next horizon to build)

---

## Implementation Result — 021D/021E/021F — 2026-06-08

### 021D — Relationship activity → intelligence mutations (delta framing)

**Root cause:** LinkedIn inbound signal rendering (`build_canonical_brief` around line
10288) surfaced raw activity counts ("11 inbound LinkedIn message(s)") instead of
answering "what changed about the relationship."

**Fix:**
- At `build_canonical_brief` startup, loads `knowledge_mutations.json` and builds
  `_muts_by_name: dict[str, dict]` — highest-confidence IME mutation per person name
  (today only; filters to `thesis_alignment_detected`, `engagement_opportunity`,
  `watchlist_signal` types).
- LinkedIn inbound rendering now checks `_muts_by_name` for the contact:
  - If IME mutation found → surfaces mutation `description` as `summary` (e.g.
    "Patrick Nelson independently reinforced Todd's restaurant-tech-roi thesis").
    `why_it_matters` names the mutation type and confidence.
  - If no mutation → derives delta from `sample_subjects` ("Reached out discussing:
    [topics]") or last-contact date ("Sent N message(s); last contact YYYY-MM-DD").
    Raw count never appears as the primary summary.
- Does **not** build parallel synthesis logic — reads the existing IME mutation log
  (already computed overnight); this is the same wiring-gap pattern as DEFECT-031/035.

### 021E — Headline → implication field

**Fix:**
- `web_scanner.py`: added `detect_implication(signal_type, title, desc) → str`
  function with two-tier classification:
  1. Direct `signal_type` → implication map (acquisition→opportunity,
     exec-change→relationship, funding→buying-behavior, earnings→operator-pressure,
     labor→operator-pressure, closure→opportunity, etc.)
  2. Keyword fallback over title+description for uncategorized signal_types
  - Categories: pain | buying-behavior | operator-pressure | opportunity | relationship | theme
- `implication` added to `extras` dict in `_build_brief_item()`.
- Wired through to: `_market_item_to_canonical()` extras, `_to_headline()`,
  `_morning_headlines_section_items` canonical item extras, `_signal_inventory_items`
  signal records and canonical item extras.

### 021F — Macro-signal category gated by weekly_plan.outcomes

**Fix:**
- `web_scanner.py`: added `detect_macro_category(title, desc) → str | None` function
  with keyword-pattern detection for 7 categories:
  labor | consumer-spending | traffic | inflation | rates | earnings | AI-investment
  Returns `None` for articles that don't match any macro category.
- `macro_category` added to `extras` dict in `_build_brief_item()`.
- `daily_brief.py`: new `_macro_signal_items(report, plan_data, limit)` function:
  - Reads all web_scan items with a non-None `macro_category`
  - Applies outcome-relevance gating: each macro category maps to industry keywords
    that must appear in the active weekly_plan outcome labels/companies.
    Examples: labor/consumer-spending/traffic/inflation → gated by restaurant/operator/
    food/dining keywords in outcomes; rates → gated by acquisition/deal/investment
    keywords; earnings → gated by company name matching between article text and
    outcomes; AI-investment → gated by tech/software/platform keywords.
  - Returns `[]` if no plan or no relevant signals — never adds noise to brief.
  - Items render as `[MACRO:CATEGORY] title` with `outcome_relevance` in extras.
- New `macro_signals` section added to `canonical_brief.sections` dict and wired
  between `signal_inventory` and `personal_operating_system`.
- `_macro_signal_items(_active_plan)` called with the active `weekly_plan.json`
  at section-wiring time.

### Files changed

- `system/scripts/web_scanner.py` — `detect_implication`, `detect_macro_category`,
  `_IMPLICATION_BY_SIGNAL_TYPE`, `_IMPLICATION_KEYWORD_MAP`, `_MACRO_CATEGORY_PATTERNS`,
  `implication` + `macro_category` in `_build_brief_item` extras
- `system/scripts/daily_brief.py` — `_macro_signal_items` (new function), mutation
  lookup in `build_canonical_brief`, delta framing for LinkedIn inbound, `implication`
  wired into `_market_item_to_canonical` / `_to_headline` / `_signal_inventory_items`,
  `macro_signals` section dict entry + wiring call
