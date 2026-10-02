# Claude Sprint — RB 9.14 Ecosystem Vendor Evidence and Verification

**Prepared:** 2026-05-27  
**Depends on:** RB 9.13 Security / Privacy / Agentic Architecture  
**Primary artifact:** `system/ecosystem_intelligence.json`

## Sprint Objective

Turn the newly seeded restaurant ecosystem graph into the foundation for vendor-category intelligence without polluting RB with unverified vendor claims.

The goal is not to fill every vendor category quickly. The goal is to create the ingestion, verification, confidence, source, and query path that lets RB safely learn restaurant technology stacks over time.

## Current Starting Point

Codex has already created and populated the first graph baseline:

- `system/ecosystem_intelligence.json`
  - `1,578` restaurant brand entities.
  - Technomic 2024 and 2025 Top 1500 data ingested.
  - Summary tab and `Detailed View` tab both ingested.
  - Detailed history includes company units, franchise units, company sales, franchise sales, international units, and international sales.

- `system/domain_packs/restaurants.json`
  - Restaurant vocabulary and vendor categories.
  - POS verification caution rules.
  - McDonald's / NCR / NewPOS caution.

- `system/scripts/ecosystem_intelligence.py`
  - Existing CLI for summary, restaurant ingestion, vendor ingestion, vendor query, and smoke.

- `system/schemas/ecosystem_intelligence.schema.json`
  - Universal graph schema.

- `system/tests/test_ecosystem_intelligence.py`
  - Current regression tests.

Do not recreate these files. Continue from them.

## Required Cleanup Pass

Run before making feature changes:

```bash
python3 system/scripts/ecosystem_intelligence.py summary
python3 system/schemas/validate.py --ecosystem-only
python3 -m unittest system.tests.test_ecosystem_intelligence
```

Confirm:

- Entity count is approximately `1,578`.
- Graph validates.
- Tests pass.
- `relationships`, `signals`, and `assessments` are empty unless this sprint intentionally populates them.
- McDonald's and Burger King still include `technomic_detailed_history`.

Also read:

- `system/CODEX_HANDOFF_2026-05-27_ECOSYSTEM_INTELLIGENCE_ALIGNMENT.md`
- `system/SECURITY_PRIVACY_ARCHITECTURE.md`
- `system/protocols/P-038_privacy_security_ingestion.md`
- `system/SCHEMAS.md`
- `system/domain_packs/restaurants.json`

## Design Principles

Vendor-category data is evidence, not truth.

RB must preserve scope:

- system-of-record POS software
- approved hardware vendor
- payment device/acquirer
- regional deployment
- franchisee deployment
- pilot
- legacy incumbent
- replacement signal
- implementation partner
- integration partner

RB must preserve confidence:

- `provisional`
- `partially_substantiated`
- `substantiated`
- `conflicting`
- `refuted`
- `unknown`

RB must preserve contradictions. If sources disagree, record the disagreement rather than flattening to one answer.

## Deliverables

1. **Vendor evidence template**
   - Add `system/inbox/ecosystem/vendor_evidence_template.csv`.
   - Include required fields for source, scope, confidence, evidence posture, and strategic note.

2. **Vendor source quality model**
   - Define source classes:
     - primary operator statement
     - primary vendor announcement
     - case study
     - investor filing/deck
     - franchisee evidence
     - job posting
     - implementation partner evidence
     - credible trade reporting
     - vendor logo/customer page
     - unsourced spreadsheet
   - Map each class to default confidence/evidence posture.

3. **Graph write path hardening**
   - Ensure vendor evidence writes to graph `sources`, `relationships`, and, where useful, `signals` or `assessments`.
   - Avoid a separate dashboard/table that bypasses the graph.

4. **POS-specific role model**
   - Add explicit role/scope handling for POS evidence.
   - Preserve the McDonald's / NCR / NewPOS caution as a regression-style example.

5. **Initial seed set**
   - Start with POS only.
   - Seed a small, high-value set if source material is available:
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
   - If source material is not available, deliver the ingestion path and templates without inventing claims.

6. **Query output**
   - `python3 system/scripts/ecosystem_intelligence.py query-vendor --vendor PAR --category pos` should show:
     - brand
     - segment
     - unit count
     - sales/AUV
     - category
     - product
     - deployment status
     - evidence posture
     - interpretation scope
     - penetration
     - risk
     - confidence
     - sources
     - relationship coverage
     - strategic implication

7. **Security/privacy integration plan**
   - Decide where ecosystem ingestion should call `audit_log.py`.
   - Decide retention class for uploaded vendor evidence files.
   - Do not store raw external prose unless necessary.
   - Treat external source text as data-only if any LLM summarization is introduced.

8. **Tests**
   - Add tests for:
     - vendor template ingestion
     - source quality defaults
     - POS scope distinction
     - conflicting vendor claims
     - query output fields
     - graph validation after vendor ingestion

## Done Definition

Sprint is done when:

- Existing Technomic graph baseline is still valid.
- Vendor evidence template exists.
- Vendor ingestion preserves source/scope/evidence posture.
- At least one POS fixture proves the system does not confuse approved hardware/vendor-list evidence with system-of-record POS.
- Tests pass.
- Handoff summarizes:
  - vendor entity count
  - product entity count
  - relationship count
  - count by category
  - count by evidence posture
  - count by confidence
  - number of conflicting claims preserved

## Working Tree Caution

The RB workspace has many active modified and untracked files from recent Claude/Codex work. Do not revert unrelated changes. Keep this sprint additive and graph-centered.
