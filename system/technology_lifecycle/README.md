# Technology Lifecycle & Change Propensity Intelligence

Phase 0 scaffolding for a new Tier 1 (public, shared) intelligence layer:
not just "what technology does this restaurant brand use" but "how long
has it been there, how sticky is this category historically, what
normally causes a switch, and is there evidence this brand is approaching
one." Full design in `system/SCHEMAS.md` → "Technology Lifecycle & Change
Events" and the original feature request this was scoped from.

This is a **public intelligence layer by design** (spec §24) — built from
publicly available sources, shareable to any authorized RBB user, never
Todd's private account judgment. It sits alongside `system/
ecosystem_intelligence.json` (Tier 1) rather than inside any one account's
`customers_prospects/` or `blue_sheets/` tree (Tier 2). See `system/
DATA_TIER_ARCHITECTURE.md` for the tier model this follows.

## The core model (revised 2026-10-01, after Deep Research Cycles 7–8)

Cycle 7–8 research found that `Brand → Vendor → Product` is too coarse a
model — it collapses "a vendor was selected" and "a vendor is deployed and
live at 4,000 locations" into the same fact, and it lets one franchisee's
footprint get mistaken for the whole brand's. The model now separately
tracks, for every technology relationship: what was announced, what was
contracted, what the franchisor requires, what franchisees may choose,
what's actually deployed, how much of the eligible estate is live, what
competing products remain installed, how fast penetration is changing,
why the change happened, and whether it ultimately succeeded, stalled, or
reversed. Full field-level detail: `system/SCHEMAS.md` → "Technology
Lifecycle & Change Events". Burger King/RBI is the worked example there —
read it before researching any enterprise multi-brand parent company.

Every record in this layer carries `visibility_class` (default
`public_shared`, since this is all public-source research) — see
`system/SCHEMAS.md`'s `visibility_class` subsection for the public/private
boundary rule, which also governs the separate, larger FDD Technology
Governance & Economics program received 2026-10-01 and recorded (not yet
implemented) in `FDD_GOVERNANCE_ECONOMICS_BRIEF.md` in this folder.

## What's in this folder

| File | What it is | Status |
|---|---|---|
| `technology_relationship_events.jsonl` | **Primary ground-truth ledger.** Append-only lifecycle-state and scope (announced/contracted/mandated/installed/live/verified) observations for each Brand×Category×Vendor×Product relationship. | Empty — schema header only. |
| `technology_governance.jsonl` | Append-only per-brand, per-category governance log (mandated / approved-vendor-list / franchisee-choice / grandfathered / etc.), including FDD-sourced fields. | Empty — schema header only. |
| `technology_penetration.jsonl` | Append-only penetration time series — location/operator/system-sales/transaction/module penetration per Brand×Category×Vendor×Product×Date. | Empty — schema header only. |
| `technology_change_events.jsonl` | Interpretive narrative layer: reconstructed historical switch events (successes, failures, abandoned pilots, and non-switches alike) built on top of the relationship ledger above. | Empty — schema header only. |
| `technology_forcing_signals.jsonl` | Append-only log of standalone pre-change signals observed about a brand's *current* stack (OS/hardware EOL approaching, new CTO, transformation announcement) — not yet a completed switch. | Empty — schema header only. |
| `category_tenure_benchmarks.json` | Derived tenure statistics (mean/median/p25/p75/n) per technology category and segment. | Stub — `"status": "not_yet_computed"`. Regenerated from the event log once enough events exist; never hand-edited. |
| `RESEARCH_KICKOFF.md` | How to prepare and finalize the Hunter lifecycle playbook. | Ready to use. |

## Two working hypotheses this dataset tests

Added 2026-10-01, from IHOP (deep, organizationally costly single-layer
POS migration) and Big Chicken (emerging brand adopting POS + payments +
loyalty + ordering + KDS + back office together from day one) as the
contrasting reference cases:

- **Stack Replacement Hypothesis** — mature enterprise restaurant brands
  tend to modernize incrementally, one layer or tightly-related bundle at
  a time, across multiple buying cycles. Younger/emerging/rapidly-growing
  brands, with less legacy infrastructure and migration risk, are more
  likely to adopt a broader integrated platform at once.
- **Platform Expansion Hypothesis** — a platform vendor is more likely to
  expand into a mature enterprise account through sequential product
  adoption *after* an initial successful deployment than by selling the
  whole stack up front.

These are hypotheses to test, not facts to assume — every event should be
recorded accurately regardless of which way it cuts. That's why
`technology_change_events.jsonl` records `change_scope`,
`existing_stack_retained`, `platform_relationship`,
`follow_on_adoption`, and `brand_maturity` for every event (full field
definitions in `system/SCHEMAS.md`). A vendor that *could* have sold the
operator an adjacent category but didn't is exactly as important to
record as one that did — the absence of consolidation is itself the
intelligence. For Genius, this distinction matters commercially: a
competitor winning one layer doesn't necessarily close the account — it
may instead signal which layer becomes contestable next, and roughly
when.

## The one rule that matters most: evidence type

Every factual claim in every record must carry an `evidence_type`:

- **`vendor_claim`** — the vendor's own marketing, press release, or case
  study says this happened. Useful for *discovery* (which brand, which
  vendor, roughly when) — never sufficient on its own to prove the claimed
  business outcome occurred.
- **`operator_statement`** — the restaurant brand or franchisee itself
  said this (earnings call, investor presentation, press release,
  franchisee commentary, executive interview).
- **`independent_evidence`** — a third party with no stake in the claim
  reported it (trade press — Nation's Restaurant News, Restaurant
  Business, QSR, Hospitality Technology — SEC filings, independent
  reporting, conference presentations not given by the vendor).
- **`rbb_inference`** — RBB (or ChatGPT doing the research) is inferring
  this from surrounding evidence, not quoting a source that stated it
  directly. Must be labeled as inference, never upgraded to fact later
  without new sourcing.
- **`unknown`** — genuinely unverifiable. Preserve the gap; never guess to
  fill it.

**A vendor claim is a lead, not a fact.** Once a candidate change event is
discovered, research must pivot to the operator/independent side before
anything is recorded as more than `vendor_claim`. See
`RESEARCH_KICKOFF.md` and `system/research/HUNTER.md` for how this applies in
practice.

## Append-only / supersedes

All five `.jsonl` logs are event-sourced, matching `system/ARCHITECTURE.md`'s
"event stream over snapshots" principle: a line is never edited or deleted
in place. A correction is a new line carrying a `supersedes` field with
the id it corrects. This preserves full audit history and matches the
pattern RBB already uses elsewhere (e.g. Interaction Briefs, `audit_log`).
`technology_relationship_events.jsonl` is the ground truth; if it and
`technology_change_events.jsonl`'s narrative ever disagree, the dated,
sourced relationship-events lines win.

## What exists vs. what's Phase 1

Phase 0 is data structures and a research template only — it does **not**
yet include:

- A governed read/write Python module (`technology_lifecycle.py`) —
  nothing validates a new line before it's appended yet.
- An importer that turns a ChatGPT research packet (dropped in `system/
  inbox/chatgpt_intelligence_drop/`) into real lines in these files.
- Any API operation (`createTechnologyChangeEvent`, etc.), Team Portal
  route, or UI tab.
- Account Background Brief enrichment.
- Benchmark computation or any change-propensity scoring.

Validated Hunter packets can accumulate in `system/inbox/
chatgpt_intelligence_drop/` as compatibility transport (per
`RESEARCH_KICKOFF.md`). Research must pass Hunter finalization before any
legacy importer, API, or portal consumer processes its nested payload.
