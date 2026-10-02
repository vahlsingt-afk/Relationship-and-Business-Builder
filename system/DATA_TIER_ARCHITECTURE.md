# Data Tier Architecture

Formalizes a two-tier data model, requested by Todd 2026-09-30 alongside the
Admin Portal, with a specific eye toward a future team/monetized release
where each user's private data must be cleanly segmented from a shared
public base.

## The two tiers (plus one sub-tier)

**Tier 1 — Canonical Public.** Data gathered from public sources about the
restaurant-brand and restaurant-technology-vendor ecosystem: the top ~1500
brands, large multi-brand franchisees, competitor/vendor facts. Shareable to
any team member with portal access — no masking needed *in principle*,
because it's public by nature. Primary stores: `system/ecosystem_
intelligence.json` (the Unified Restaurant-Tech Graph — entities,
relationships, signals) and `system/brand_profiles/<slug>.json` (per-brand
identity/synopsis/leadership/footprint/pain_points/recent_signals, via
`brand_profile_common.py`).

**Tier 2 — Canonical User Artifacts.** User-generated intelligence: call
transcripts, emails, POV, personal notes, account-specific research. Built
*on top of* Tier 1 (references a brand/vendor already in the ecosystem
graph), flows down only, and must never flow back up into a Tier 1 store.
Primary stores: `customers_prospects/accounts/<slug>/` (account.json,
evidence.jsonl, actions.json), `blue_sheets/accounts/<slug>/`, `system/
account_intelligence/*.md`, and the account-scoped parts of `system/
artifact_vault/` (`green_sheets/`, `account_plans/`).

**Tier 2b — Vendor-Level Private Judgment.** A real third category found
during this work: `system/competitor_intelligence/competitors/<slug>/
competitor.json`'s `todds_pov`/`vs_genius` fields. Vendor-scoped, not
account-scoped (so not Tier 2 in the strict sense), but not neutral public
fact either — it's Todd's own competitive judgment about a vendor, framed
against Genius. Already excluded wholesale from every Team Portal response
(`competitor_intelligence_common.shareable_extended_view`) — no code change
needed here, just named so the model is complete and a future competitor-
facing route knows to keep excluding it. The same reasoning applies to the
vendor-scoped `system/artifact_vault/value_wedges/` and `competitive_briefs/`
registries (hundreds of them) — deliberately **not** given a `data_owner_id`
in this pass (see "Scope decisions" below); they're Tier 2b, not Tier 2.

## The one-way-flow rule

Tier 1 → Tier 2: a Tier 2 artifact may read and reference Tier 1 facts
freely (a Blue Sheet cites a brand's public footprint; a Green Sheet cites a
buying influence's public title). Tier 2 → Tier 1: **never**. No script,
ingest job, or quick-add feature should ever write account-specific,
personal, or editorial content into a Tier 1 store.

This is not a new invention — `blue_sheets`' domain in `system/
CANONICAL_REGISTRY.yaml` (~line 570) already documents exactly this split,
just not generalized or named: `registry_sourced` fields (identity,
ownership, footprint, tech-stack — "public/CRM facts") sync **down** from
canonical data into each Blue Sheet on every canonical mutation and "must
never be hand-edited only inside blue_sheets/"; `blue_sheet_native` fields
(the Miller Heiman qualification scorecard, buying-influence ratings) live
only in `blue_sheets/` and are "never written back upstream except as
ordinary evidence." Every other domain should follow this same pattern.

## Real leaks found and fixed (2026-09-30)

Both found by hand, not by any automated check — the reason `data_tier_
smoke.py` (below) exists now.

1. **Live**: `system/brand_profiles/brand-burger-king.json`'s `pain_points`
   field carried a real franchisee-complaint note, added through the Team
   Portal's own legitimate `add_brand_pain_point()` quick-add feature, and
   was being served to all 3 teammates via `GET /api/brands/brand-burger-
   king/ecosystem-profile` — a real gap in an intended feature (a field can
   be allowlisted as shareable while a specific *entry* within it carries
   content that shouldn't be broadly shared). Fixed: `brand_profile_common.
   py` gained an entry-level `visibility` key (`"team_shareable"` default,
   or `"private"`); `shareable_view()` now filters `pain_points`/
   `recent_signals` per-entry, not just per-field.
2. **Latent**: `system/ecosystem_intelligence.json`'s `entities[].notes` on
   `brand-little-caesars` carried a stray sales-opportunity note. Not
   currently read by any Team Portal route, and the substantive version
   already lived correctly in `system/account_intelligence/2026-07-31-
   little-caesars-genius-international-opportunity.md` — a duplicate
   pointer, not the sole copy. Fixed via the new `ecosystem_intelligence.
   clear_entity_field()` governed op (see below).

## New governed operations

- `brand_profile_common.set_pain_point_visibility(brand_id, value,
  visibility)` — flips one existing pain-point entry's visibility in place,
  via the existing atomic `save_profile()` write path. Never a raw JSON
  edit.
- `ecosystem_intelligence.clear_entity_field(entity_id, field, *, reason)` —
  clears an allowlisted entity-level scalar field (currently just `"notes"`)
  through the real `_write_graph()` snapshot+validate path, logging an
  `audit_log` `mutation_executed` event. No public function existed before
  this to edit or clear an arbitrary entity field at all — `set_entity_
  owner()` was the only precedent, narrowly scoped to ownership fields.
  **Note**: the schema requires `notes` be a string when present (no
  null) — this function deletes the key, never nulls it; an earlier version
  that nulled it wrote invalid content to the real production file before
  validation caught it (see `clear_entity_field`'s own docstring and
  `test_ecosystem_intelligence.py::ClearEntityFieldTest` for the regression
  guard).

## `data_tier_smoke.py`

A new scanner (`system/scripts/data_tier_smoke.py`), run on demand, that
flags — never auto-fixes — private-judgment tells in Tier 1 stores:

- Any populated `entities[].notes` value in `ecosystem_intelligence.json`
  (that store has no field-level visibility concept of its own, so every
  populated value is a review candidate, not a confirmed leak).
- Any `brand_profiles/*.json` `pain_points`/`recent_signals` entry whose
  `last_reviewed_by` starts with `"human:"`/`"team:"` (a person, not an
  ingest job, wrote it) and whose `visibility` is missing or
  `"team_shareable"` — the exact Burger King shape.

Run 2026-09-30, after both fixes above: **0 flagged `brand_profiles`
entries** (confirms the Burger King fix and finds no siblings across all
612 profiles). **8 flagged `ecosystem_intelligence.json` notes** remain —
reviewed by hand, all benign structural notes (parent/holding-company
cross-references, "no Technomic data backfilled yet" placeholders), no
further action needed; left flagged rather than silently allowlisted so a
future reviewer re-checks them against this same bar rather than assuming
the type of field is inherently safe.

## Per-user scoping groundwork (not full multi-tenancy)

Added `data_owner_id: "todd"` to every entry in the account-scoped
portfolio registries — `customers_prospects/_portfolio/customers_prospects_
registry.json`, `blue_sheets/_portfolio/blue_sheet_registry.json`, and the
account-scoped `system/artifact_vault/{green_sheets,account_plans}/*/
registry.json` files. Purely additive (no schema files exist for these
registries, so no validation risk); not touching individual `account.json`
files in this pass. Gives a future multi-tenant filter something to key off
without a later migration.

**Scope decision**: deliberately **not** added to the vendor-scoped
`system/artifact_vault/{value_wedges,competitive_briefs,battle_cards}/*/
registry.json` files (300+ of them) — those are Tier 2b (vendor-level,
not account-level), a different segmentation axis than "which user's
account data is this."

**Naming decisions, deliberate, not oversights:**
- `data_owner_id`, not `owner_user_id` or `tenant_id`. `owner` already
  exists on `customers_prospects_registry.json` entries as a free-text
  sales-rep name (sometimes blank) — a different concept ("who's the rep
  on this account" vs. "whose private data layer does this belong to").
  `tenant` already means something else entirely in this codebase: `system/
  SECURITY_PRIVACY_ARCHITECTURE.md`'s existing "Tenant Data Boundary"
  section uses it for *a whole separate RB deployment* (Todd's instance vs.
  a hypothetical different customer's instance) — an instance-vs-instance
  axis, not a multiple-users-within-one-deployment axis. Reusing either
  name here would collide with an existing, different meaning.

## Phase 2a: Tier 1 rebuild for the 7 real accounts + GoTo Foods (2026-09-30)

Todd's working assumption going into Phase 2 was that Tier 1 for the 15
`customers_prospects` accounts had been contaminated by his own account-level
activity. Investigation found this didn't hold: only 7 of the 15 registry
accounts have any real Tier 2 research at all, and every field in their Tier 1
profile was already tagged `system:*` (gapfill/Technomic/earnings
pipelines) — zero `human:`/`team:` provenance, no leak. The one real Tier 1
contamination case in the whole 612-brand corpus was Burger King, already
fixed above and not one of these 7.

What *was* real: those 7 profiles (plus GoTo Foods, which had no profile at
all) were thin and entirely uncited — every field's `sources` array was
empty. Rebuilt via the existing `top500_profile_gapfill_ingest.py` mechanism
(no code changes) with a new hand-built packet,
`system/inbox/user_artifacts/RBB_top500_profile_gapfill_rebuild_7accounts_
2026-09-30.json`, containing real web-researched facts with real cited
source URLs — unlike the original batched gapfill packets, which had empty
`sources` on every field.

Brands touched: McDonald's (`parent_ownership`, `hq_city_state` — both
previously null), Del Taco (`hq_city_state`, `founded_year` — both
previously null), Church's Texas Chicken (`hq_city_state`, `founded_year` —
both previously null), Five Guys (`parent_ownership`, `hq_city_state` — both
previously null), Subway (`hq_city_state`, previously null — the brand now
runs a dual HQ in Shelton, CT and Miami, FL), and GoTo Foods (`brand-goto-
foods` — an entirely new profile: parent Roark Capital Group, HQ Sandy
Springs GA, founded 2004 as Focus Brands / renamed 2023, ~6,700 units across
7 brands). Pollo Campero and Cafe Rio were left untouched — their existing
fields were already populated and reasonably accurate, and the ingest
mechanism only records new sources when a field's *value* changes, so
resubmitting identical text would have added nothing.

**Worldpay**: confirmed out of scope — as of Jan 2026, Worldpay is a Global
Payments payments product line (the same category as Genius), not an
external prospect/vendor brand. No Tier 1 brand profile applies or is
planned.

Verified after the run: `data_tier_smoke.py` still shows 0 flagged
`brand_profiles` entries (the pre-existing 8 flagged `ecosystem_notes` are
unrelated benign structural notes, unchanged by this rebuild); `git diff
customers_prospects/` shows this rebuild touched nothing under
`customers_prospects/` (the ingest script's only write paths are `system/
brand_profiles/*.json` and `system/ecosystem_intelligence.json`); real
source URLs confirmed present in the graph's `deep_research_profile.
company_profile` namespace for all 16 updated/added fields; full test suite
green.

## Explicitly deferred (Phase 2b, not this pass)

Full de-duplication between `system/brand_profiles/<slug>.json`
(ecosystem-wide) and each account's own `customers_prospects/<slug>/
brand_profile.json` (account-specific) — confirmed real, already-drifting
duplication of the same public facts (e.g. pollo-campero's founding
year/ownership entered independently in both, different `as_of` dates,
no cross-reference). Reconciling 15 real accounts' worth of facts (merge
policy: which `as_of` wins, what happens when a `human:`-reviewed fact
conflicts with a fresher `system:`-sourced one) is a real, valuable, larger
and riskier project on its own — doesn't need to block the tier vocabulary
and one-way-flow rule being written down and enforced going forward. Phase
2a (above) rebuilt Tier 1's own thin/uncited fields; it did not touch this
merge question at all.
