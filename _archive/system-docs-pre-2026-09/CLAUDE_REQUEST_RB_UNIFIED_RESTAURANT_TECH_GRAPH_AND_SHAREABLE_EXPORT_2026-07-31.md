# Claude Request: Unify RB Restaurant-Tech Intelligence, Daily Evidence Mutation, and Shareable Export

**Requested:** 2026-07-31  
**Priority:** High  
**Requested by:** Todd Vahlsing  
**Status:** Implementation request — inspect the current system before changing it  

## Outcome

Make RB’s existing ecosystem intelligence graph the single source of truth for restaurant-brand technology stacks and vendor customer relationships.

Merge the stronger evidence, deployment-scope, confidence, AI-application, and vendor-customer detail developed in:

`outputs/019fb7fc-6359-7272-a00b-0a57f9dd8a55/Restaurant_Tech_Full_1500_With_Canonical_Stack_2026-07-31.xlsx`

into RB’s canonical ecosystem model:

`system/ecosystem_intelligence.json`

Then generate the internal macro views and a sanitized, shareable Excel workbook from that canonical graph. The workbook must be an export/projection, not an independently maintained fact store.

The daily RB evidence-gathering cycle must:

1. scan the appropriate public sources;
2. extract evidence-backed restaurant-brand/vendor relationships;
3. classify deployment significance and lifecycle;
4. reconcile the claim against existing graph knowledge;
5. mutate the canonical graph only when the evidence gate is satisfied;
6. preserve historical and contradictory evidence;
7. validate the graph;
8. regenerate the internal macro views and shareable workbook when a material change is committed;
9. produce a clear mutation/export receipt.

Do not create a second graph, spreadsheet database, or parallel mutation pipeline.

---

## Architectural Decision

The authoritative fact layer is:

`system/ecosystem_intelligence.json`

The following are projections of that graph:

- RB company intelligence files;
- vendor customer lists;
- canonical restaurant technology stack views;
- macro vendor/category/AI charts;
- the shareable `.xlsx` export;
- daily-brief summaries and strategic narratives.

The existing federated-graph principle remains intact:

- The restaurant-brand/vendor technology map belongs in the **Macro Industry Graph**.
- Account-specific operating topology, such as McDonald’s NSN/StoreTech structure, remains in the applicable **Micro Ecosystem Graph**.
- Personal relationships remain in the **Personal Relationship Graph**.
- New articles, filings, earnings commentary, and public announcements begin as evidence/signals and promote into canonical relationships only through explicit evidence gates.

Do not flatten micro-operational topology or personal relationship data into the shareable workbook.

Reference:

`system/design/FEDERATED_GRAPH_ARCHITECTURE.md`

---

## Current-State Findings Claude Must Respect

### Existing canonical graph

`system/ecosystem_intelligence.json` currently contains approximately:

- 1,610 entities;
- 62 `uses_vendor_for_category` relationships;
- 44 restaurant brands with vendor relationships;
- 20 technology vendors;
- 61 relationships marked active and one historical relationship.

The graph is broader than the new workbook, especially in POS coverage. However, much of the relationship data needs enrichment:

- approximately 42 relationships have `deployment.stage = unknown`;
- approximately 45 relationships do not yet have a completed `relationship_classification.level_name`;
- some sources are described generically rather than preserving a direct URL and precise assertion;
- some records may represent old incumbents, approved hardware, franchisee subsets, or vendor claims that should not be counted as current enterprise wins.

### New workbook

The workbook contains a smaller but more detailed evidence model:

- one-company-per-row canonical technology stack;
- an evidence ledger;
- vendor/customer relationships;
- confidence factors;
- lifecycle and verification status;
- AI applications, including Voice AI and AI personalization;
- deployment status and detailed scope notes;
- current-win macro graphs;
- explicit separation of working profiles, current deployments, franchisee deployments, and historical/superseded relationships.

The workbook is not the existing RB macro graph and must not remain a competing fact store.

### Existing daily infrastructure

Reuse and extend the current architecture, including:

- `system/scripts/morning_pipeline.py`
- `system/scripts/refresh_all.py`
- `system/scripts/refresh_sources.py`
- `system/scripts/earnings_monitor.py`
- `system/scripts/market_signals.py`
- `system/scripts/intelligence_mutation_engine.py`
- `system/scripts/ecosystem_intelligence.py`
- `system/scripts/ecosystem_brief.py`
- `system/scripts/relationship_classification.py`
- `system/scripts/signal_synthesis.py`
- `system/scripts/intelligence_assessment.py`
- `system/earnings_calendar.yaml`
- `system/restaurant_tech_watchlist.md`
- `system/schemas/ecosystem_intelligence.schema.json`
- `system/schemas/validate.py`

Do not bypass `resolve_and_upsert_relationship()`, conflict detection, source provenance, snapshots, schema validation, or the existing mutation log.

---

## Required Canonical Relationship Model

Retain the existing universal graph primitives and enrich `uses_vendor_for_category` relationships so the graph can represent the workbook’s additional precision.

Every restaurant-brand/vendor relationship should be able to preserve:

- `from_entity_id`
- `to_entity_id`
- `relationship_type = uses_vendor_for_category`
- normalized technology `category`
- `product`
- `ai_application`
- `status`
- `vendor_role`
- `deployment.stage`
- `deployment.deployed_units`
- `deployment.target_units`
- `deployment.target_date`
- `deployment.penetration_pct`
- `deployment.scope`
- `deployment.geography`
- `deployment.channel`
- `deployment.customer_operator`
- `deployment.scope_unit_count`
- `deployment.deployment_claim_type`
- `deployment.evidence`
- normalized `deployment_status`
- concise `deployment_detail`
- `evidence_posture`
- `interpretation_scope`
- `confidence.level`
- `confidence.score`
- `confidence.rationale`
- `confidence.review_after`
- `last_verified_at`
- `verified_by`
- `staleness_flag`
- `relationship_classification.level`
- `relationship_classification.level_name`
- `relationship_classification.basis`
- `sources[]`
- `source_assertions[]`
- `strategic_note`
- `created_at`
- `updated_at`

If adding `source_assertions[]`, each assertion should support:

- source ID and URL;
- source title;
- publisher/company;
- publication or filing date;
- discovery date;
- source type;
- source authority;
- commercial-incentive posture;
- exact claim type;
- contracted locations, live locations, or rollout target when stated;
- whether the customer is explicitly named;
- whether the evidence comes from the vendor, operator, regulator, or independent reporting;
- a short paraphrase of the supported assertion;
- current, historical, contradictory, or superseding posture.

Do not paste large copyrighted excerpts. Preserve a concise paraphrase and, where needed, a short compliant quotation.

### Normalized deployment statuses

Support at least:

- `contracted_deployment_pending`
- `active_rollout`
- `significant_deployed_footprint`
- `brand_wide_deployment`
- `enterprise_wide_deployment`
- `all_location_deployment`
- `multi_unit_deployment`
- `franchisee_deployment_not_brand_standard`
- `parent_platform_relationship_brand_scope_unresolved`
- `deployed_scope_not_publicly_disclosed`
- `historical_current_status_not_reconfirmed`
- `historical_reseller_relationship_superseded`
- `discontinued_or_replaced`
- `pilot_only`
- `working_profile_public_source_required`

Use human-readable display labels in exports, but store stable normalized values in the graph.

### Significant-win rule

A vendor/customer relationship may be shown on the detailed evidence ledger when supported, but it may count as a **current meaningful vendor win** only when public evidence establishes one of:

- a production deployment with meaningful installed footprint;
- a brand-wide or enterprise-wide deployment;
- an active rollout with material committed scope;
- a meaningful multi-unit or multi-franchisee deployment;
- a contracted award with material scope, clearly labeled as deployment pending.

Do not count the following as current deployed wins:

- pilots, tests, evaluations, or proofs of concept;
- logo-page appearances without affirmative usage language;
- event sponsorships, integration-directory listings, or generic partnerships;
- a vendor’s own product/module name mistaken for a restaurant customer;
- isolated single-unit use unless strategically significant and explicitly labeled;
- historical relationships with no current confirmation;
- reseller relationships that have ended;
- unverified working profiles.

Contract awards must be distinguished from installed deployments. A signed award may appear as `contracted_deployment_pending`, but must not inflate installed-location counts.

---

## Confidence and Evidence Hierarchy

Use RB’s existing confidence structure, but make the factor logic auditable.

Priority order:

1. SEC filings and comparable regulatory filings;
2. restaurant-brand filings, earnings releases, investor presentations, and prepared remarks;
3. technology-vendor filings, earnings releases, investor presentations, and prepared remarks;
4. official brand deployment announcements;
5. direct vendor case studies with named customer, scope, and outcomes;
6. official customer pages or executive statements;
7. credible trade reporting that directly attributes the claim;
8. vendor logo/listing pages and indirect marketing material.

Important distinctions:

- A vendor filing can strongly establish that the vendor claims a win, but does not automatically prove the exact customer or installed footprint.
- An operator filing or announcement is stronger for confirming the operator’s vendor and deployment scope.
- Two sources repeating the same press release do not constitute independent corroboration.
- Earnings-call transcripts may contain forward-looking or approximate language; classify the claim accordingly.
- Source age alone does not prove churn. Mark old evidence for reconfirmation unless a replacement, discontinuation, or end date is evidenced.
- Once a relationship is substantiated, a single conflicting source should create a dispute/review state, not silently erase it.

---

## Required Merge

Build a deterministic, reviewable migration from the workbook evidence into the graph.

### Before mutation

1. Snapshot `system/ecosystem_intelligence.json`.
2. Validate the existing graph.
3. Produce a dry-run reconciliation report.
4. Match brands and vendors by canonical entity ID, normalized name, and aliases.
5. Compare relationships by:
   - brand;
   - vendor;
   - normalized category;
   - product/module;
   - geography/channel/operator scope where relevant.

### Reconciliation outcomes

Every workbook row should receive one outcome:

- `new_relationship`
- `enrich_existing_relationship`
- `duplicate_no_change`
- `new_source_for_existing_relationship`
- `lifecycle_update`
- `supersedes_existing_relationship`
- `conflict_requires_review`
- `working_profile_not_promoted`
- `pilot_not_promoted`
- `invalid_customer_or_module_excluded`
- `entity_resolution_required`

Do not overwrite stronger existing evidence with weaker workbook evidence.

### Known correction that must survive the merge

Checkers & Rally’s Voice AI:

- Hi Auto is the current significant deployment.
- Current public evidence supports 300+ deployed locations; prior rollout evidence referenced 350+ corporate and franchise locations.
- Presto’s relationship was a historical reseller arrangement for the Hi Auto-powered solution.
- Presto’s 2024 SEC filing states that the arrangement ended June 30, 2024.
- Preserve Presto as historical/superseded.
- Count Hi Auto, not Presto, as the current Voice AI win.

### Working McDonald’s profile

The user-provided McDonald’s stack profile must remain visibly separate until each component has suitable public sourcing:

- NEWPOS
- QSRSoft
- Fiserv
- PAR/NCR hardware
- PAR/Acrelec drive-thru timing
- Coates digital menu boards

Do not treat a 100% user working confidence value as source-backed evidence. Convert it into an explicit working-profile assertion or preserve the graph’s stronger pre-existing source-backed record when available.

---

## Expanded Daily Evidence Sources

The current earnings monitor reads SEC EDGAR 8-K feeds and investor-relations RSS for tracked companies. Retain that functionality, but expand the restaurant-tech evidence program.

### Regulatory and earnings sources

For applicable public companies, monitor:

- SEC 8-K filings and attached earnings exhibits;
- SEC 10-Q and 10-K filings;
- registration/proxy materials when they contain material customer concentration or strategic-platform information;
- official earnings releases;
- prepared earnings remarks;
- investor presentations;
- official earnings-call webcast/transcript pages when publicly accessible;
- acquisition and divestiture filings that change the vendor/product identity;
- comparable Canadian or foreign regulatory filings for non-US issuers when relevant.

Extraction must detect:

- named customer wins;
- unnamed “major QSR” or “top-ten restaurant brand” wins;
- renewals;
- product/module expansions;
- contracted locations;
- live locations;
- rollout schedules;
- transaction or ARR contribution;
- customer concentration;
- deployment delays;
- churn, loss, replacement, or non-renewal;
- acquisition-related product/vendor name changes.

Unnamed wins remain signals until identity is corroborated. Do not guess the customer.

### Operator-side primary sources

Monitor public restaurant operators and holding companies for:

- technology-platform selections;
- digital transformation programs;
- AI deployments;
- loyalty and digital-ordering changes;
- POS and payments migrations;
- drive-thru and computer-vision deployments;
- franchisee adoption requirements;
- rollout timing and capital commitments;
- vendor consolidation or platform replacement.

Sources:

- operator SEC/regulatory filings;
- operator earnings releases and calls;
- investor presentations;
- official newsrooms;
- technology and digital leadership presentations;
- public franchise disclosure or franchisee-association material when legally/publicly available;
- public conference presentations and executive interviews.

### Vendor-side primary sources

Monitor:

- vendor SEC/regulatory filings;
- earnings materials;
- official customer announcements;
- case studies;
- newsroom releases;
- product release notes when they identify deployed customers;
- official customer pages;
- implementation and rollout updates;
- acquisition integration announcements.

### Restaurant and technology trade sources

Use as discovery and corroboration:

- Restaurant Business;
- Nation’s Restaurant News;
- QSR Magazine;
- Restaurant Dive;
- Restaurant Technology News;
- Fast Casual;
- Hospitality Technology;
- Payments Dive when relevant;
- reputable financial reporting for public-company events.

Trade reporting must not override a contradictory primary source.

### AI-specific sources

Scan for meaningful restaurant deployments involving:

- Voice AI;
- computer vision;
- AI personalization and next-best action;
- forecasting;
- labor optimization;
- food-waste optimization;
- robotics/autonomous systems;
- predictive maintenance;
- guest sentiment/conversational analytics;
- AI-enabled order accuracy, kitchen throughput, and operations intelligence.

Pilot-only AI evidence must remain a signal and must not count as a current deployment win.

---

## Earnings Watchlist Expansion

`system/earnings_calendar.yaml` currently contains a limited public-company set, and `refresh_earnings()` currently runs `earnings_monitor.py --fetch --watch-only`.

Claude should:

1. Audit the companies already in `system/earnings_calendar.yaml`.
2. Correct entity/category errors before expanding it.
3. Separate:
   - restaurant-technology vendors;
   - payments/hospitality platforms;
   - public restaurant operators/holding companies.
4. Expand the tracked public-company universe to the material restaurant-tech ecosystem.
5. Decide whether daily fetching should:
   - fetch all configured companies; or
   - use priority tiers with a complete lightweight daily check and deeper event-window scans.
6. Preserve rate limits, health reporting, deduplication, and recovery behavior.

At minimum, evaluate coverage for:

### Technology and payments vendors

- PAR Technology
- Toast
- NCR Voyix
- Shift4
- Lightspeed Commerce
- Global Payments
- Fiserv
- Block/Square
- Oracle
- Olo, including how to handle its current public/private status correctly
- SoundHound AI
- Cerence where restaurant Voice AI exposure is material
- DoorDash and Uber where first-party/restaurant-platform relationships are disclosed
- other public restaurant-technology vendors found in the canonical graph

### Restaurant operators and holding companies

- McDonald’s
- Yum! Brands
- Restaurant Brands International
- Wendy’s
- Starbucks
- Chipotle
- Domino’s
- Darden Restaurants
- Dine Brands
- Brinker
- Bloomin’ Brands
- Texas Roadhouse
- Shake Shack
- Wingstop
- Dutch Bros
- CAVA
- Sweetgreen
- Papa Johns
- Jack in the Box
- Cheesecake Factory
- other public parent companies represented in the Top 1500 universe

Do not blindly add companies. Record ticker, jurisdiction/regulator, official IR source, relevance, parent/brand mapping, and monitoring priority.

---

## Daily Mutation Workflow

Integrate the restaurant-tech evidence workflow into the existing morning gather phase.

Recommended sequence:

```text
1. Fetch/refresh public sources
2. Normalize source items
3. Entity-resolve brands, parents, vendors, and products
4. Extract factual deployment assertions
5. Enrich with current Company Intelligence File
6. Compare with canonical graph
7. Score source authority, scope clarity, recency, and corroboration
8. Classify deployment/lifecycle/significance
9. Produce proposed mutations
10. Auto-apply only mutations that meet the existing canonical-fact gate
11. Queue ambiguous/conflicting/unnamed/pilot claims for review
12. Snapshot and mutate through the canonical graph writer
13. Validate schema and relationship invariants
14. Rebuild relationship classifications and staleness flags
15. Regenerate graph-derived macro views and shareable workbook if materially changed
16. Write a daily receipt
```

### Idempotency

The same source must not create duplicate entities, relationships, assertions, mutations, or exports.

Use stable IDs/hashes derived from canonical entity IDs, category/product/scope, source URL, and source assertion.

### Conflict handling

When evidence indicates a vendor displacement:

- preserve the former relationship;
- update its lifecycle/status;
- link the superseding relationship where the schema supports it;
- do not count both as current incumbents unless the evidence supports an active mixed estate;
- preserve geography, channel, franchisee, and rollout distinctions.

When sources disagree:

- retain both assertions;
- mark the relationship disputed or review-required;
- do not silently select the preferred narrative;
- surface the contradiction in the daily receipt.

### Mutation receipt

Every daily run should report:

- sources checked;
- source failures/staleness;
- candidate deployment claims found;
- claims rejected as pilots/logo-only/vague;
- new relationships;
- enriched relationships;
- lifecycle changes;
- superseded relationships;
- conflicts requiring review;
- new AI deployments;
- graph validation result;
- whether the shareable workbook was regenerated;
- export path/version;
- no-change state when applicable.

---

## Shareable Workbook Export

Generate a polished `.xlsx` from the canonical graph.

Required leading tab order: `Dashboard`, `Canonical Tech Stack`, `Vendor Customer Lists`, then the remaining summary, evidence, graph, and playbook tabs. Keep the canonical company stack immediately after the dashboard and the vendor/customer view immediately after the canonical stack in every regenerated export.

The export should include:

### 1. Executive Summary

- coverage statistics;
- current meaningful deployments;
- brands covered;
- vendors covered;
- AI applications covered;
- confidence/freshness distribution;
- clear statement that counts represent evidence coverage, not verified market share.

### 2. Canonical Restaurant Tech Stack

One row per restaurant company/brand with paired confidence fields:

- Company
- Units
- Franchisee-Owned Units
- Company-Owned Units
- Ownership Confidence
- POS + Confidence
- Back Office + Confidence
- Payments + Confidence
- POS Hardware + Confidence
- Drive-Thru Timers + Confidence
- Digital Menu Boards + Confidence
- Loyalty + Confidence
- Online Ordering + Confidence
- KDS + Confidence
- Labor/Workforce + Confidence
- Inventory/Supply Chain + Confidence
- Accounting + Confidence
- Kiosks + Confidence
- Menu Management + Confidence
- Voice AI + Confidence
- Computer Vision + Confidence
- additional AI solutions/use cases + Confidence
- Last Verified
- Evidence Sources
- Stack Coverage
- Research State
- Notes

### 3. Vendor Customer List

One row per vendor × customer × category/product/scope:

- Vendor
- Customer
- Parent/Platform
- Units
- Franchisee-Owned Units
- Technology Category
- Product/Module
- AI Application
- Relationship Scope
- Deployment Status
- Deployment Detail
- Lifecycle/Current State
- Confidence
- Evidence Type
- Verification Status
- Evidence Date
- Source URL
- Notes

Include a vendor summary table based only on current meaningful deployments. Historical, superseded, pilot-only, and working-profile rows may remain visible but must be excluded from current-win counts.

### 4. Evidence Ledger

Preserve auditable source assertions and confidence factors.

### 5. Macro Views

Charts/tables derived from current graph-eligible evidence:

- current meaningful wins by vendor;
- brands covered by technology category;
- AI applications by category/vendor;
- deployment status distribution;
- evidence freshness;
- confidence distribution.

Historical or superseded relationships must not count as current wins.

### 6. Update Playbook

Explain the evidence and mutation rules in plain language for recipients.

### Internal versus shareable export

If RB needs internal strategic notes that should not be shared, support two projections from the same graph:

- internal workbook with full strategic context;
- shareable workbook excluding private relationship data, personal notes, internal hypotheses, opportunity strategy, and restricted-source content.

Both must derive from the same canonical graph and the same export code. Do not maintain separate data copies.

Use explicit export metadata:

- graph version/hash;
- generated timestamp;
- evidence cutoff;
- export type;
- schema version.

---

## Source and Privacy Rules

- Export public evidence only unless Todd explicitly approves another source class.
- Do not expose emails, calendar content, private messages, CRM details, personal relationship strength, opportunity notes, or micro-graph operational data.
- Do not export copyrighted transcript text beyond compliant short quotations.
- Preserve source URLs and concise audit notes.
- Do not promote a private/internal assertion into a public shareable file.
- Make the export filter testable.

---

## Implementation Requirements

1. Inspect current code and tests before editing.
2. Reuse the existing graph writer, snapshots, conflict resolver, confidence model, mutation log, source-health model, and schema validator.
3. Add a deterministic workbook-to-graph migration command with dry-run default and explicit confirmed write.
4. Add a deterministic graph-to-workbook exporter.
5. Add the export step to the daily pipeline only after graph validation succeeds and only when:
   - a material relationship/entity/source assertion changes; or
   - the export is missing/stale; or
   - an explicit rebuild is requested.
6. Do not make the morning brief fail because an optional workbook export fails. Report a degraded export state while preserving the validated graph.
7. Do not write partial graph mutations when validation fails.
8. Preserve backwards compatibility for existing ecosystem queries and Company Intelligence Files.
9. Update schema and architecture documentation.
10. Update `system/STATUS.md` after implementation.

---

## Required Tests

Add focused tests for:

### Migration

- imports a new workbook relationship;
- enriches an existing graph relationship;
- does not duplicate an identical relationship;
- attaches a new source assertion idempotently;
- does not promote working-profile rows;
- does not promote pilot-only evidence;
- rejects product/module names masquerading as restaurant customers;
- preserves stronger existing evidence;
- flags conflicting vendors in the same category;
- preserves franchisee scope rather than upgrading it to brand-wide;
- keeps contracted locations distinct from live locations.

### Lifecycle

- displacement makes the prior relationship historical/superseded;
- mixed-estate evidence can preserve two current vendors with distinct scope;
- old evidence becomes review-required without being declared churned;
- Presto/Checkers is historical and Hi Auto/Checkers is current;
- current-win metrics exclude historical, superseded, pilot, and working-profile rows.

### Earnings and source extraction

- parses named customer wins from SEC/earnings materials;
- preserves unnamed wins as signals only;
- extracts contracted versus deployed locations;
- recognizes rollout, expansion, renewal, replacement, and loss language;
- deduplicates the same earnings release across EDGAR exhibit, IR release, and syndication;
- maps parent-company disclosures to the correct restaurant brands without overgeneralizing scope.

### Export

- workbook is generated solely from the graph;
- internal and shareable exports reconcile on public facts;
- private/internal fields are absent from the shareable export;
- macro totals reconcile to eligible canonical relationships;
- formulas contain no errors;
- key tabs render legibly;
- repeated export with unchanged graph is deterministic or produces an explicit no-change result.

### Pipeline

- source failure produces health/degraded status without corrupting the graph;
- validation failure prevents graph write/export promotion;
- successful material mutation triggers export;
- no material mutation does not produce unnecessary export churn;
- daily receipt contains mutation and export counts.

Run the relevant targeted tests plus the full test suite. Report exact pass/fail/skip counts and identify any pre-existing failures.

---

## Acceptance Criteria

This request is complete only when:

1. `system/ecosystem_intelligence.json` is demonstrably the only canonical restaurant-tech relationship fact store.
2. The workbook evidence has been reconciled into the graph through a dry-run-reviewed migration.
3. The workbook can be regenerated from the graph without manually copying data.
4. Daily evidence ingestion can add, enrich, supersede, dispute, or reconfirm relationships idempotently.
5. Earnings, filings, investor materials, operator sources, vendor sources, and restaurant trade sources are incorporated with source-health visibility.
6. Pilot-only and vague marketing evidence do not inflate current-win counts.
7. Current, contracted, actively deploying, franchisee-only, historical, and superseded relationships remain distinguishable.
8. AI applications—including Voice AI and computer vision—use the same evidence and lifecycle rules as other technology categories.
9. Macro graphs and vendor customer lists update from canonical graph mutations.
10. The shareable workbook contains only approved public intelligence.
11. Graph validation, workbook formula checks, and visual export checks pass.
12. Claude provides a final handoff describing:
    - files changed;
    - migration results;
    - new/enriched/superseded/conflicted counts;
    - source coverage added;
    - test results;
    - daily run commands;
    - export command and output path;
    - remaining evidence gaps.

---

## Implementation Posture

This is a data-governance and trust change, not merely a spreadsheet enhancement.

Prefer:

- one canonical graph;
- appendable evidence;
- explicit lifecycle;
- reversible, snapshotted mutations;
- deterministic exports;
- conservative customer-win classification;
- source-backed confidence;
- transparent no-change and failure receipts.

Avoid:

- two mutable fact stores;
- workbook-only corrections;
- counting pilots as wins;
- silently deleting incumbents;
- guessing unnamed customers;
- upgrading franchisee evidence to enterprise scope;
- treating vendor marketing confidence as deployment confidence;
- rebuilding a second morning intelligence pipeline.
