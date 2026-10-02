# Codex Handoff — Ecosystem Intelligence Alignment

**Date:** 2026-05-27  
**Prepared for:** Claude next sprint alignment  
**Status:** additive Codex sprint on top of Claude's RB 9.13 Security / Privacy / Agentic Architecture sprint

## Why this handoff exists

Claude's last completed sprint established the security, privacy, ingestion, audit, and retention posture RB needs before handling more raw and semi-structured source data. Codex then began the next product layer: a persistent ecosystem intelligence artifact seeded with Technomic Top 1500 restaurant data.

These are complementary, not competing, workstreams:

- Claude RB 9.13 defines how source material should be handled safely.
- Codex ecosystem intelligence defines where restaurant/operator/vendor intelligence should persist once normalized, minimized, attributed, and interpreted.

Claude should read this file before starting the next ecosystem sprint.

## Since The Last Claude Handoff

This is the concise delta Claude needs before starting the next sprint.

Since Claude's RB 9.13 handoff, Codex accomplished the following:

1. Created a new persistent, industry-agnostic ecosystem intelligence graph.
2. Added a restaurant domain pack as the first vertical vocabulary layer.
3. Added schema validation and CLI tooling for the graph.
4. Copied the 2024 and 2025 Technomic Top 1500 workbooks into RB's ecosystem inbox.
5. Ingested both Technomic workbooks into the graph.
6. Corrected the ingestion path after confirming each workbook has a second `Detailed View` tab.
7. Persisted both summary-tab data and detailed-tab data.
8. Added tests for Technomic normalization, detailed-tab merge, and vendor-evidence posture.
9. Added vendor-category design guardrails so future POS/vendor uploads are treated as evidence claims, not truth.
10. Documented the McDonald's / NCR / NewPOS caution in the restaurant domain pack.

Current graph facts:

- `system/ecosystem_intelligence.json` exists and validates.
- `1,578` restaurant brand entities exist.
- All `1,578` brand entities have Technomic detailed history.
- No vendor relationships exist yet.
- No relationship overlay exists yet.
- No daily brief integration exists yet.

The next sprint should not recreate this work. It should continue from this artifact.

## Claude's Last Completed Sprint

Claude completed the RB 9.13 security/privacy architecture sprint.

Deliverables reported complete:

- `system/SECURITY_PRIVACY_ARCHITECTURE.md`
  - Canonical reference for five-layer data pipeline: raw -> normalized -> intelligence -> memory -> summaries -> audit.
  - Retention classes.
  - Google OAuth scope table.
  - Action gate table.
  - Agentic architecture posture.
  - Delete/forget flow.

- `system/scripts/privacy_guard.py`
  - Seven independent guards:
    - data class classification
    - raw content minimization
    - durable memory judgment gate requiring all 8 metadata fields
    - prompt-injection resistance
    - action gate
    - sensitive disclosure controls
    - cross-profile leakage prevention

- `system/scripts/audit_log.py`
  - Append-only monthly JSONL log.
  - Ten event types.
  - Deterministic event IDs.
  - Secondary index.
  - Query by type/profile/date/data-class.
  - Convenience wrappers for common audit patterns.

- `system/scripts/retention_policy.py`
  - Seven retention classes.
  - Retention windows.
  - Expiry detection.
  - Deletability rules.
  - Forget flow contract: preview -> validate -> tombstone.
  - Blocks audit log deletion.

- `system/protocols/P-038_privacy_security_ingestion.md`
  - Step-by-step ingestion flow for all five pipeline stages.
  - Action gate contract.
  - Integration points with passive RI ingest, mutations, relationship signals, and daily brief.
  - Daily Brief security reporting rules.

- Tests reported:
  - 35 privacy guard tests.
  - 31 injection resistance tests.
  - 46 retention policy tests.

## Codex Work Completed After That Sprint

Codex added the first persistent ecosystem intelligence artifact for industry-agnostic ecosystem mapping, with restaurants as the first domain pack.

New files:

- `system/ecosystem_intelligence.json`
  - Canonical mutable graph artifact.
  - Stores entities, relationships, signals, assessments, sources, user relevance, and strategic recommendations.

- `system/schemas/ecosystem_intelligence.schema.json`
  - Universal graph schema.
  - Domain-agnostic primitives.
  - Relationship records include vendor scope fields such as `evidence_posture` and `interpretation_scope`.

- `system/domain_packs/restaurants.json`
  - Restaurant-specific vocabulary.
  - Categories include POS, payments, back office, inventory, labor/scheduling, loyalty, online ordering, KDS, drive-thru systems, voice AI, delivery middleware, BI/analytics, camera systems, computer vision, loss prevention, IoT, networking/WiFi, hardware, ERP, catering, training, and AI assistant layer.
  - Includes POS verification caution rules, including the McDonald's / NCR / NewPOS example.

- `system/scripts/ecosystem_intelligence.py`
  - CLI for summary, restaurant ingestion, vendor ingestion, vendor query, and smoke tests.
  - Reads CSV/TSV/XLSX without external dependencies.
  - Ingests both workbook tabs for Technomic files.

- `system/tests/test_ecosystem_intelligence.py`
  - Regression coverage for brand normalization, Technomic `$000` normalization, vendor evidence posture, CSV loading, and detailed-tab merge.

- `system/inbox/ecosystem/README.md`
  - Drop zone and usage notes for ecosystem source files.

Updated files:

- `system/scripts/rb_core.py`
  - Added `ECOSYSTEM_INTELLIGENCE_PATH`.
  - Added `DOMAIN_PACKS_DIR`.

- `system/schemas/validate.py`
  - Added `--ecosystem-only`.
  - Default validation now includes `ecosystem_intelligence.json` when present.

- `system/SCHEMAS.md`
  - Added `ecosystem_intelligence.json` contract and required semantics.

- `system/WHAT_PERSISTS.md`
  - Added ecosystem intelligence and domain packs to persistence contract.

## Technomic Files Ingested

Source files copied into RB:

- `system/inbox/ecosystem/top-1500-chain-restaurants-performance-data-2024-technomic.XLSX`
- `system/inbox/ecosystem/top-1500-chain-restaurants-performance-data-2025-technomic.XLSX`

Both workbooks have two tabs:

- Summary tab.
- `Detailed View`.

Codex originally ingested only the summary tab, then corrected the loader to ingest both tabs.

Current persisted state:

- `1,578` restaurant brand entities.
- `2` source records.
- `0` vendor relationships.
- `0` signals.
- `0` assessments.
- All `1,578` brand entities have detailed Technomic history.

Validation:

```bash
python3 system/schemas/validate.py --ecosystem-only
```

Result:

```text
OK — ecosystem_intelligence.json validates against ecosystem_intelligence.schema.json
```

Spot-check examples:

- McDonald's has 2024 summary metrics:
  - system sales: `53469000000`
  - units: `13557`
  - AUV: `3960000`

- McDonald's has 2024 detailed metrics:
  - company units: `671`
  - franchise units: `12886`
  - company sales: `3197000000`
  - franchise sales: `50272000000`
  - international units: `29920`
  - international sales: `77246000000`

- Burger King has 2024 detailed metrics:
  - company units: `160`
  - franchise units: `6541`
  - international units: `13031`
  - international sales: `16748000000`

## Current Artifact Shape

`ecosystem_intelligence.json` is not intended to be a dashboard table. It is a strategic situational-awareness graph.

Current sections:

- `entities`
  - Brands are populated.
  - Vendors/products/operators/people are not yet populated.

- `relationships`
  - Empty.
  - Intended for vendor/category deployments, ownership, operations, integrations, replacements, pilots, influence, and relationship overlays.

- `signals`
  - Empty.
  - Intended for source-backed events: announcements, filings, job postings, implementation claims, relationship signals, operator movements, vendor transitions.

- `assessments`
  - Empty.
  - Intended for RB judgment: risk, confidence, replacement risk, opportunity timing, strategic interpretation.

- `sources`
  - Technomic 2024 and 2025 workbooks are recorded.

- `user_relevance`
  - Empty.
  - Intended for LinkedIn/email/calendar/CRM/meeting relationship overlay.

- `strategic_recommendations`
  - Empty.
  - Intended for "who matters now" surfacing.

## Important Design Principle

Vendor-category data must be treated as evidence, not truth.

The user specifically flagged an important POS example:

- A spreadsheet may list McDonald's as an NCR POS customer.
- This can be technically true in a limited sense because NCR has been one of McDonald's approved global vendors/hardware providers.
- But McDonald's owns/has owned NewPOS as its POS system context.
- Therefore RB must not flatten "NCR appears in a POS column" into "NCR is the system-of-record POS."

The restaurant domain pack now encodes this rule:

- Distinguish system-of-record POS software.
- Distinguish approved hardware vendor.
- Distinguish payment device/acquiring relationship.
- Distinguish franchisee-level deployment.
- Distinguish regional deployment.
- Distinguish pilot.
- Distinguish global approved vendor list.
- Distinguish legacy incumbent vs replacement signal.

Vendor uploads currently default to:

- `confidence: low`
- `evidence_posture: provisional`
- `interpretation_scope: Uploaded vendor-category signal. Treat as a lead for verification, not proof of system-of-record incumbency.`

## What Is Not Done Yet

The Technomic data does not include vendor-category mappings.

As of this handoff:

- No POS vendor relationships are populated.
- No payments/loyalty/online-ordering/etc. vendor relationships are populated.
- No vendor evidence has been verified.
- No relationship overlay has been applied.
- No daily brief integration has been added.
- No privacy/audit integration has been wired into `ecosystem_intelligence.py`.
- No source corroboration workflow has been implemented.

## Recommended Next Sprint

Suggested sprint name:

**RB Ecosystem Vendor Evidence and Verification Sprint**

Objective:

Populate restaurant technology vendor categories without corrupting RB with unverified spreadsheet/vendor-logo claims.

The restaurant ecosystem graph is the spine of this sprint. Claude should treat `system/ecosystem_intelligence.json`, `system/domain_packs/restaurants.json`, `system/schemas/ecosystem_intelligence.schema.json`, and `system/scripts/ecosystem_intelligence.py` as first-class sprint inputs, not optional context.

Required sprint outcome:

- The graph remains valid.
- The graph keeps the Technomic brand baseline intact.
- Vendor evidence writes into graph relationships/sources/signals/assessments, not into an unrelated dashboard/table.
- Vendor claims preserve evidence posture, interpretation scope, source quality, and confidence.
- Any RI/relationship overlay design points back to graph entity IDs.

## Cleanup Pass Before New Feature Work

Claude should do this cleanup pass before adding vendor data:

1. **Run verification commands**
   - `python3 system/scripts/ecosystem_intelligence.py summary`
   - `python3 system/schemas/validate.py --ecosystem-only`
   - `python3 -m unittest system.tests.test_ecosystem_intelligence`

2. **Inspect graph size and shape**
   - Confirm entity count is still approximately `1,578`.
   - Confirm `relationships`, `signals`, and `assessments` are still empty unless Claude intentionally adds them.
   - Confirm McDonald's and Burger King still have `technomic_detailed_history`.

3. **Review schema/documentation alignment**
   - Ensure new vendor fields are reflected in:
     - `system/schemas/ecosystem_intelligence.schema.json`
     - `system/SCHEMAS.md`
     - `system/domain_packs/restaurants.json`
     - `system/inbox/ecosystem/README.md`

4. **Integrate security/privacy posture**
   - Read `system/SECURITY_PRIVACY_ARCHITECTURE.md`.
   - Read `system/protocols/P-038_privacy_security_ingestion.md`.
   - Decide which ecosystem ingestion events should call `audit_log.py`.
   - Decide which vendor/source artifacts need retention classification.
   - Avoid storing raw external text unless it is necessary for provenance.

5. **Check git/worktree hygiene**
   - Many files are modified/untracked from active RB sprint work.
   - Do not revert unrelated files.
   - Keep ecosystem changes additive and scoped.

If cleanup reveals any mismatch, Claude should fix the mismatch before starting the vendor seed pass.

Recommended deliverables:

1. **Vendor evidence input template**
   - Create `system/inbox/ecosystem/vendor_evidence_template.csv`.
   - Required fields:
     - brand
     - vendor
     - category
     - product
     - scope
     - deployment_stage
     - status
     - source_title
     - source_url_or_path
     - source_type
     - source_quality
     - evidence_posture
     - confidence
     - strategic_note

2. **Source/evidence model hardening**
   - Add explicit `source_type` and `source_quality` interpretation rules for vendor evidence.
   - Map:
     - primary operator statement
     - primary vendor announcement
     - case study
     - investor filing/deck
     - franchisee evidence
     - job posting
     - implementation partner evidence
     - credible trade reporting
     - vendor customer logo page
     - unsourced spreadsheet

3. **Verification posture promotion rules**
   - `provisional`
   - `partially_substantiated`
   - `substantiated`
   - `conflicting`
   - `refuted`
   - `unknown`

   Promotion should require corroboration, not vibes.

4. **POS-specific verification rules**
   - Explicitly model roles:
     - `system_of_record_pos`
     - `approved_hardware_vendor`
     - `payment_device_vendor`
     - `acquirer_or_payments`
     - `franchisee_deployment`
     - `regional_deployment`
     - `pilot`
     - `legacy_incumbent`
     - `replacement_candidate`

5. **Initial vendor seed pass**
   - Start with a narrow category, probably POS.
   - Populate a small high-value subset, not all 1,578 brands.
   - Suggested targets:
     - McDonald's
     - Burger King
     - Taco Bell
     - Wendy's
     - Subway
     - Chick-fil-A
     - Domino's
     - Chipotle
     - Starbucks
     - Dunkin'

6. **Query output hardening**
   - `query-vendor --vendor PAR --category pos` should return:
     - brand
     - segment
     - unit count
     - sales/AUV
     - vendor category
     - product
     - deployment status
     - evidence posture
     - interpretation scope
     - estimated penetration
     - risk color
     - confidence
     - sources
     - relationship coverage
     - strategic implication

7. **Security/privacy integration**
   - Use Claude's RB 9.13 privacy sprint outputs.
   - Ecosystem source ingestion should:
     - classify data class
     - avoid storing raw source text unless needed
     - log ingestion/audit events
     - observe retention classes
     - wrap external content as data-only if later summarized by an LLM

8. **RI/relationship overlay design**
   - Do not implement the full overlay until vendor edges exist.
   - Define how RB will connect:
     - brand entities to baseline contacts by current company
     - vendor entities to baseline contacts
     - operator/franchisee groups to known relationships
     - active threads to ecosystem entities

## Suggested Claude First Step

Before coding, Claude should inspect:

```bash
python3 system/scripts/ecosystem_intelligence.py summary
python3 system/schemas/validate.py --ecosystem-only
python3 -m unittest system.tests.test_ecosystem_intelligence
```

Then read:

- `system/SECURITY_PRIVACY_ARCHITECTURE.md`
- `system/protocols/P-038_privacy_security_ingestion.md`
- `system/SCHEMAS.md` ecosystem section
- `system/domain_packs/restaurants.json`
- `system/scripts/ecosystem_intelligence.py`

## Suggested Claude Output Contract

At the end of the next sprint Claude should produce:

- Updated code/tests.
- A short handoff file.
- Exact commands run.
- Counts:
  - number of vendor entities
  - number of product entities
  - number of vendor relationships
  - number by category
  - number by evidence posture
  - number by confidence
  - number of conflicting claims preserved

## Current Caution

The working tree contains many pre-existing modified/untracked files from prior RB sprint work. Claude should not revert unrelated changes. Treat the ecosystem files above as additive Codex output and continue from them.
