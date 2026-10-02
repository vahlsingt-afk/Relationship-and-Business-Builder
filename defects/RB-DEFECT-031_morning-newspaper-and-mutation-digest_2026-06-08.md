# RB-DEFECT-031: Brief Lacks "Morning Newspaper" Headlines Layer & Overnight Change Digest

**Filed:** 2026-06-08
**Severity:** High
**Category:** Chief of Staff Decision Quality / Daily Brief Architecture
**Status:** Two highest-leverage sub-items implemented and verified — remainder scoped below

## Source

User scored the current brief 60/100 against the "World-Class CoS" standard, citing
nine gaps. Two are structural-architecture gaps that recur across the other seven —
fixing them raises the floor on everything else:

1. **No headlines layer** — the brief opens with "here are your opportunities"
   instead of "here is what happened in the world." A CoS brief should be a
   newspaper-then-CoS-guidance experience, not a CoS-guidance-only experience.
2. **No "what changed since yesterday" digest** — the brief restates known state
   instead of leading with deltas. Humans orient on change, not snapshots.

These two are the structural fix; the other seven (sources/evidence, signal counts +
deltas, "why it matters" interpretation, opportunity discovery, weekly context,
time-allocation, evidence trust-stats) are largely *consequences* of these two gaps
or of RB-DEFECT-021/029 already in flight (see Relationship to Otherable Defects).

## Architecture: reuse existing data, add two assembly-stage blocks

Both blocks are pure transformations over data the brief **already loads** —
`market_signals_report` (rich per-item fields: `source_name`, `published_at`,
`pain_point_or_priority`, `restaurant_operator_impact`, `confidence`,
`recommended_action` — confirmed via direct inspection) and the knowledge-mutation
log (`MUTATION_LOG_PATH`, populated by `intelligence_mutation_engine` per
RB-DEFECT-029). No new ingestion, no new data sources — this is an assembly-stage
reframing of material already in the report.

### 1. `_build_morning_headlines(market_signals_report)` → `report["morning_headlines"]`

Groups the top market-signal items by `category` (the closest existing proxy for
"world / markets / restaurant industry / restaurant tech" sectioning — a true
geography/markets/world taxonomy would require new signal categories, which is
021F's scope, not this defect's), and renders each as
`{headline, source, date, why_it_matters, url}` — i.e., **exactly** the
source-backed, evidence-first format the critique calls for in points #1 and #2.
This becomes section 1 of the brief, ahead of opportunity guidance — "here is the
newspaper, then here is what it means for you."

### 2. `_build_overnight_change_digest(today)` → `report["overnight_change_digest"]`

Reads the knowledge-mutation log (already populated by the DEFECT-029 pipeline),
filters to mutations applied since yesterday, and groups by `type`
(`thesis_alignment_detected`, `engagement_opportunity`, `vendor_customer_relationship`,
`thesis_validation`, `executive_pov`, `watchlist_signal`, ...) into a delta-first
summary: counts by type + the actual mutation records, so "Harri confidence +7" /
"+2 new relationship signals" style reporting (critique point #4) becomes possible
mechanically once the mutation log has volume. This becomes the brief's lead-in:
"here is what changed since yesterday," before restating current state.

## Relationship to other open defects (avoid duplicated work)

- **Critique #2 (sources/evidence) and #5 ("why it matters" interpretation)**: the
  `morning_headlines` block's `{source, why_it_matters}` fields are populated
  straight from `market_signals` item data that already carries
  `pain_point_or_priority`/`restaurant_operator_impact`/`confidence` — this is largely
  a **rendering** fix once headlines exist as a section, not a new extraction problem.
  Where deeper synthesis ("according to whom, based on what") is needed, that's
  021E's headline→implication scope — route through `intelligence_mutation_engine`,
  not a parallel synthesizer.
- **Critique #3 (signal counts + deltas, e.g. "Harri 83/100, delta +7")**: this
  requires a *signal-strength time series* per opportunity — a genuinely new data
  structure (current vs. prior score with persistence). Out of scope for this defect;
  should be filed as its own item once `weekly_plan.json` outcomes give the system a
  natural per-opportunity tracking anchor (RB-DEFECT-021).
- **Critique #6 (opportunity discovery / "hunting while you sleep")**: this is
  `opportunity_sensing.py`'s job already; if it's under-firing, that's a tuning issue
  in that module, not a brief-assembly gap — worth a separate look but not this defect.
- **Critique #7/#8 (weekly context, time allocation)**: **directly solved by
  RB-DEFECT-021**, whose core layer (`weekly_plan.json`, `operating_mode`,
  `portfolio_allocation`) landed 2026-06-08. Once a Monday plan exists, "Recommended
  Day Allocation: 40% Career Search / 30% Relationship..." is a direct render of
  `report["weekly_plan"]["portfolio_allocation"]`. No new architecture needed —
  the Monday plan-generation orchestrator (021's remaining scope item #1) is the
  blocker, not a brief-rendering gap.
- **Critique #9 (newspaper-then-CoS layering)**: this defect's `morning_headlines`
  block, placed first in `canonical_brief.sections`, is the structural fix for this.

## Implementation Result (2026-06-08)

Implemented both blocks in `daily_brief.py`, sourced from data already loaded in
`build_report`:

- `_build_morning_headlines(market_signals_report, *, limit=7)` — groups top market
  signals by `category`, renders `{headline, source, date, why_it_matters, url,
  confidence}` per item. Surfaced as `report["morning_headlines"]` (list of
  `{section, items}` groups), assembled immediately after `market_signals_report`
  is computed (`build_report`, ~line 616) so it has the richest available item data.
- `_build_overnight_change_digest(today)` — reads `MUTATION_LOG_PATH`
  (`system/.cache/knowledge_mutations.json`), filters to `applied_at` within the
  last 24h (UTC, consistent with DEFECT-029's UTC correction), groups by `type`,
  returns `{since, counts_by_type, total, mutations}`. Surfaced as
  `report["overnight_change_digest"]`. Degrades gracefully (empty digest, not an
  error) when the log is empty/absent — confirmed, since the live log currently has
  zero entries pending DEFECT-029's automated triggers accumulating volume.

Both are additive `report[...]` keys — no existing section removed or reordered;
ordering/placement within `canonical_brief.sections` is a presentation-layer follow-up
(small, mechanical) once these are validated against a few days of live data.

**Verification:**
- `py_compile` clean
- Live `build_report()` run: `morning_headlines` populated with 3 category groups
  from real `market_signals` data (titles, sources, dates, why-it-matters strings,
  confidence — all sourced, not invented); `overnight_change_digest` returns a
  well-formed empty digest (`total: 0`) against the current empty mutation log
- Existing daily-brief regression suite: pass (see commands below)

## Implementation Result — wiring + rendering-contract closure (2026-06-08, follow-up)

Follow-up investigation (prompted by user's continued dissatisfaction with the
brief after this defect's initial "done") found the blocks were **computed but
never actually surfaced** — a two-layer gap:

1. **Data → canonical_brief gap**: `morning_headlines`/`overnight_change_digest`
   (and DEFECT-021's `operating_mode`/`weekly_plan`) lived only as orphaned
   top-level `report[...]` fields — `canonical_brief.section_order` (what the GPT
   actually renders from) didn't include them at all. **Fixed**: added four new
   sections — `operating_mode_framing`, `morning_headlines`,
   `overnight_change_digest`, `weekly_plan_focus` — placed immediately after
   `executive_summary` (front of the order, not buried), with renderer functions
   converting the raw computed fields into proper `_canonical_item` entries
   (grounding/freshness/confidence-labeled, source-refed, with honest
   "no plan exists yet" messaging where data is absent rather than hiding the gap).
   Verified live: `morning_headlines` renders real SEC EDGAR items
   (`[Restaurant Ai] 8-K - Current report | SEC EDGAR (MCD)...`),
   `overnight_change_digest` renders real mutation-log entries (`"1 knowledge
   mutations... Thesis 'franchise_technology' strengthened..."`).
   Canonical-brief smoke test's strict front-order assertion updated; 121
   regression tests pass; failure-count comparison (24 pre-existing → 23) confirms
   nothing broken.

2. **canonical_brief → GPT-instructions gap** (the deeper one — would have made
   layer 1's fix invisible regardless): the Custom GPT's *mandatory numbered
   rendering sequence* in all three instruction documents
   (`custom_gpt_instructions_8k.md`, `custom_gpt_instructions_compact_8k.md`,
   `custom_gpt_prompt.md`) **never mentioned these section names** — confirmed via
   grep, zero hits across all three, before this fix. A GPT following an explicit
   numbered sequence has no reason to spontaneously render sections the sequence
   doesn't name. **Fixed**: inserted four new mandatory steps — "This week's lens"
   (`operating_mode_framing`), "Morning headlines" (`morning_headlines`),
   "Overnight changes" (`overnight_change_digest`), and "This week's outcomes"
   (`weekly_plan_focus`, conditional on `is_plan_current`) — into all three
   documents' rendering sequences, positioned right after the status line (lens
   first) and headlines/digest before "Top Decisions" (world before to-do list,
   per the original 60/100 critique's #1 complaint). Also threaded
   RB-DEFECT-032's `contradictory_signals` rendering into the "Active opportunities"
   step across all three documents (`⚠️ Contradictory signal (source, trust: X)...`)
   — closing that defect's brief-surfacing loop too. Cross-references
   renumbered/checked for orphans (none found).

**This is the same "wiring gap" pattern this entire sprint has been chasing — just
one layer further downstream than 029/032 (those were source→engine; this was
engine→rendered-output).** Data existing in the API response that the consumer's
instructions don't name is functionally identical to data that was never computed,
from the user's vantage point.

## Non-goals

- Not a rewrite of `market_signals.py` or `intelligence_mutation_engine.py` — both
  already produce the data; this defect is purely an assembly/rendering reframe.
- Not a fix for critique #3 (signal-strength deltas) — that needs new persisted
  time-series data, filed separately once RB-DEFECT-021's outcome-tracking anchor
  exists.
- Not a `canonical_brief` section-ordering rewrite — the new blocks are additive;
  reordering `sections` to put headlines first is a small follow-up once the blocks
  are validated against several days of live signal volume.
