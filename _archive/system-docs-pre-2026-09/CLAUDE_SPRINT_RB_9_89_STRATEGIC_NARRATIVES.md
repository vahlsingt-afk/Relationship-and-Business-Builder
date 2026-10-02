# RB 9.89: Persisted Strategic Narratives — Slice 2 of RB-DEFECT-046 (UIPF)

**Status:** Implemented (2026-06-15)
**Source:** `CLAUDE_DEFECT_RB_046_UNIVERSAL_INTELLIGENCE_PROCESSING_FRAMEWORK.md`,
Proposed Implementation Slice 2 ("persisted Strategic Narrative schema").

## Motivating gap

Before this slice, each article about a brand's tech-stack moves
(POS selection, back-office switch, loyalty program sunset, etc.) produced
independent `vendor_customer_relationship` mutations with no memory connecting
them. Three separate articles about Hungry Howie's (Toast POS, Restaurant365
back-office, Howie Rewards loyalty sunset) would never accumulate into a
single "this brand is mid-modernization" picture — RB had no institutional
memory across signals about the same brand.

## What was implemented

All changes in `system/scripts/intelligence_mutation_engine.py`.

### 1. Category lifecycle signal detection

- `_TECH_STACK_CATEGORIES` — the set of tracked tech-stack categories (`pos`,
  `back_office`, `loyalty`, `online_ordering`, `payments`, `workforce`,
  `voice_ai`, `automation`), mirroring `KNOWN_VENDORS` categories.
- `_CATEGORY_LIFECYCLE_KEYWORDS` + `_LIFECYCLE_VERBS` — detect sentences like
  "Howie Rewards loyalty program will be sunset" (lifecycle verb +
  category keyword, no named vendor required).
- `_extract_category_lifecycle_signals(text)` — returns
  `[{category, signal_type: "sunset", sentence_evidence, confidence: 0.80}]`
  per matching sentence. Wired into `_extract_entities()` as
  `entities["category_lifecycle_signals"]`.

### 2. Persisted `strategic_narratives[]` on brand entities

- `_get_or_create_brand_entity(ecosystem, brand_id, brand_name, now)` — finds
  or creates a minimal stub brand entity in `ecosystem["entities"]`.
- `update_strategic_narratives(brand_entity, *, category, signal_type,
  description, source, now)` — accumulates signals into a single
  `tech_stack_modernization` narrative per brand:
  - Returns `None` for categories outside `_TECH_STACK_CATEGORIES` (no-op).
  - Creates the narrative on first signal (`confidence: "low"`).
  - Confidence escalates by distinct categories touched: 1 -> `low`,
    2 -> `medium`, 3+ -> `high`.
  - `next_expected_signals[]`: unresolved `sunset` categories project a
    "{category} replacement announcement"; once >= 2 categories are seen,
    all remaining tracked categories project "{category} selection or
    change".

### 3. New mutation type: `category_lifecycle_signal`

Mutation 7 in `generate_mutations()`: for each category-lifecycle signal,
resolves the brand via `_resolve_brand(article_subject, ecosystem)`, emits a
`category_lifecycle_signal` mutation (`confidence: 0.80`,
`requires_confirmation: False` — treated as additive/soft narrative input,
not a canonical-fact mutation requiring human confirmation). Both this and
`vendor_customer_relationship` mutations now carry `brand_name` directly
(replacing fragile description-string parsing).

### 4. `apply_mutations()` wiring

- `vendor_customer_relationship` and `category_lifecycle_signal` branches
  both call `_get_or_create_brand_entity()` + `update_strategic_narratives()`,
  tracked via a new `narratives_updated` counter.
- "Write stores" persistence of `ecosystem_intelligence.json` now triggers
  when `narratives_updated > 0` OR a `vendor_customer_relationship` /
  `category_lifecycle_signal` mutation was applied — previously a
  lifecycle-only signal (no vendor relationship in the same article) would
  not have persisted its narrative update.
- `apply_mutations()` return dict now includes `narratives_updated`.

### 5. Trust stats / brief surfacing

- `generate_mutations()`'s `trust_stats` gains
  `category_lifecycle_signals` (count).
- `build_mutation_brief_block()` gains `category_lifecycle_signals` and
  `strategic_narratives_updated` (count of today's
  `vendor_customer_relationship` + `category_lifecycle_signal` mutations).

## Tests

`tests/test_strategic_narratives.py` (9 new tests):
- `_extract_category_lifecycle_signals()`: loyalty sunset detected, no
  lifecycle verb -> no signal, lifecycle verb without tracked category -> no
  signal, POS replacement detected.
- `update_strategic_narratives()`: untracked category -> `None`; first signal
  -> `low` confidence, empty `next_expected_signals`; second category ->
  `medium` + "selection or change" projections; third category -> `high` +
  unresolved-sunset replacement projection.
- End-to-end Hungry Howie's scenario via `generate_mutations()` across two
  articles (Toast POS selection, then Restaurant365 back-office + Howie
  Rewards loyalty sunset in a follow-up article): both resolve to the same
  brand entity (`brand-hungry-howie`), accumulating to a `high`-confidence
  `tech_stack_modernization` narrative with 3 supporting signals across
  pos/back_office/loyalty and a "loyalty platform replacement announcement"
  expectation.

Full suite: 2404 passed (pre-existing collection errors in
`tests/test_entity_identity_scope.py`, `tests/test_external_content_mutation.py`,
`tests/test_universal_artifact_intake.py`, and `vendor/` are untouched and
predate this change).

## Deferred / not in this slice

- No SCHEMAS.md documentation yet for `strategic_narratives[]` — pending.
- `triage_signals` (Slice 1) is not yet consumed by this slice's mutation
  generation — `generate_mutations()` still operates on raw article text via
  `_extract_entities()`. Wiring `triage_signals` -> mutation generation for
  email/calendar overlays is part of Slice 3.
- No brief section renders `strategic_narratives[]` yet — that is Slice 3
  (Company Intelligence File / enrich-before-comment).
- Other tech-stack categories beyond the four with lifecycle keywords
  (`loyalty`, `pos`, `back_office`, `online_ordering`) — `payments`,
  `workforce`, `voice_ai`, `automation` are tracked in
  `_TECH_STACK_CATEGORIES` for confidence/`next_expected_signals` purposes but
  have no `_CATEGORY_LIFECYCLE_KEYWORDS` entries yet, so lifecycle signals for
  those categories won't be detected from free text (only via
  `vendor_customer_relationship` mutations naming a known vendor).

## Sequencing note

Slice 3 (enrich-before-comment + Company Intelligence File) can now consume
`strategic_narratives[]` as the durable store this slice introduces, and
`triage_signals` (Slice 1) as the raw-signal source for email/calendar
overlays once signal quality is validated.
