# RB 9.14 Sprint Kickoff — Ecosystem Vendor Evidence and Verification

**Date:** 2026-05-27
**Preceded by:** Graph optimization pass (this session)
**Primary artifact:** `system/ecosystem_intelligence.json`

---

## Pre-Sprint Verification — Passed

All cleanup checks passed before feature work began.

```
python3 system/scripts/ecosystem_intelligence.py summary
→ entities: 1578, relationships: 0, signals: 0, assessments: 0, sources: 2

python3 system/schemas/validate.py --ecosystem-only
→ OK — ecosystem_intelligence.json validates

python3 -m unittest system.tests.test_ecosystem_intelligence -v
→ Ran 9 tests in 0.001s — OK

python3 system/scripts/ecosystem_intelligence.py smoke
→ OK - ecosystem intelligence smoke passed
```

McDonald's: system_sales $53.47B, unit_count 13,557, detailed_history_years 2019–2024 ✓
Burger King: system_sales $10.98B, unit_count 6,701, detailed_history_years 2019–2024 ✓
`relationships`, `signals`, `assessments` all empty ✓

---

## Graph Optimization — Completed This Session

The following changes were made before the sprint officially begins. All 9 tests pass.

### New capabilities

**`query-brand --brand <name>`**
Returns the full entity profile for any brand: entity attributes, all vendor relationships, signals, assessments, user relevance records, and strategic recommendations. This is the primary RB surface command — a brand's complete intelligence state in one call.

**`query-brands` with filters**
Filters brand entities by `--segment`, `--subsegment`, `--min-sales`, `--max-rank`, and `--limit`. Lets RB scope to a target set (e.g., top 10 LSR brands) without reading the full 1,578-entity graph.

**`summary` breakdown**
Now reports `relationship_evidence_posture`, `relationship_confidence`, and `relationship_vendor_roles` in addition to entity and category counts. The sprint's done-definition counts can be read directly from this output.

### Mutation hardening

**Source quality model**
`vendor_relationship()` now derives `schema_quality`, `evidence_posture`, and `confidence` from a 10-class `SOURCE_QUALITY_MODEL`. Row-level fields override defaults; source type alone is sufficient if not overridden. Supported classes:

| source_type | quality | default_posture | default_confidence |
|---|---|---|---|
| primary_operator_statement | primary | substantiated | high |
| primary_vendor_announcement | strong | partially_substantiated | medium |
| case_study | strong | partially_substantiated | medium |
| investor_filing_deck | strong | partially_substantiated | medium |
| franchisee_evidence | medium | partially_substantiated | medium |
| credible_trade_reporting | medium | partially_substantiated | medium |
| job_posting | medium | provisional | low |
| implementation_partner_evidence | medium | provisional | low |
| vendor_logo_customer_page | weak | provisional | low |
| unsourced_spreadsheet | weak | provisional | low |

**POS role / vendor_role**
`vendor_relationship()` now reads `pos_role` (or `vendor_role` / `scope_role`) from the CSV row and validates it against a controlled vocabulary:
- `system_of_record_pos`
- `approved_hardware_vendor`
- `payment_device_vendor`
- `acquirer_or_payments`
- `franchisee_deployment`
- `regional_deployment`
- `pilot`
- `legacy_incumbent`
- `replacement_candidate`
- `unknown`

The role is stored in both `relationship.vendor_role` and `deployment.scope`.

**Relationship ID now includes vendor_role**
ID format: `rel-{brand_id}-{category}-{slug(vendor_role)}-{vendor_id}`

This means McDonald's/NCR/approved_hardware_vendor and McDonald's/NewPOS/system_of_record_pos are stored as separate edges. The McDonald's/NCR/NewPOS caution from the domain pack is now enforced structurally, not just as a comment.

**`ingest-vendors --source-type`**
Accepts `--source-type <class>` to override the source quality class for an entire file batch (e.g., `--source-type unsourced_spreadsheet` for a raw vendor list).

### Schema change
`vendor_role` added to the `relationship` definition in `ecosystem_intelligence.schema.json` with the controlled vocabulary enum. Existing data validates.

### New test coverage (9 tests total, up from 5)
- `test_source_quality_model_sets_defaults_from_source_type`
- `test_pos_scope_distinction_system_of_record_vs_hardware`
- `test_conflicting_vendor_claims_stored_as_separate_edges`
- `test_query_brand_returns_full_profile`

### Template delivered
`system/inbox/ecosystem/vendor_evidence_template.csv`
Includes all required fields with three annotated example rows (McDonald's/NewPOS, McDonald's/NCR, Burger King/PAR). Demonstrates the system_of_record_pos vs approved_hardware_vendor distinction.

---

## Sprint Scope — RB 9.14

The graph is now ready to receive vendor evidence. The sprint work begins here.

### What exists (do not recreate)
- 1,578 restaurant brand entities with 6 years of Technomic detailed history
- Schema, domain pack, CLI, and tests all passing
- Source quality model, POS role vocabulary, vendor_role field all wired
- `vendor_evidence_template.csv` drop zone ready

### Sprint deliverables remaining

**1. Initial POS vendor seed**
Ingest sourced POS vendor evidence for the top 10 priority brands:
McDonald's, Burger King, Taco Bell, Wendy's, Subway, Chick-fil-A, Domino's, Chipotle, Starbucks, Dunkin'

Use `system/inbox/ecosystem/vendor_evidence_template.csv` as the input format.
Only ingest what can be sourced — do not invent claims. Cite source_type for every row.

```bash
python3 system/scripts/ecosystem_intelligence.py ingest-vendors \
  system/inbox/ecosystem/pos_seed.csv --dry-run
```

**2. Verification posture promotion rules (code)**
Add a `promote-posture` CLI path or helper that advances a relationship's `evidence_posture` when corroborating evidence is added. Promotion requires explicit corroboration — not a config flag.

Posture ladder: `provisional` → `partially_substantiated` → `substantiated`
Conflict state: `conflicting` (preserved, not collapsed)
Reversal states: `refuted`, `unknown`

**3. Security / privacy integration**
Read `system/SECURITY_PRIVACY_ARCHITECTURE.md` and `system/protocols/P-038_privacy_security_ingestion.md`.

Decisions needed before writing audit calls:
- Which ecosystem ingestion events warrant an audit log entry? (At minimum: ingest-vendors writes, posture promotions)
- What retention class do uploaded vendor evidence CSV files get?
- Does the template CSV need to be classified before being stored in `inbox/ecosystem/`?

Wire `audit_log.py` calls into `ingest_vendors()` and any posture promotion path.

**4. RI / relationship overlay design doc**
Define (as a design doc, not code yet) how RB will connect:
- brand entities → baseline contacts by current company
- vendor entities → baseline contacts
- active email/calendar threads → ecosystem entity IDs
- franchisee groups → brand entities

This is a design artifact, not an implementation. Implementation follows in a later sprint once vendor edges exist at scale.

**5. Query output hardening**
`query-vendor --vendor PAR --category pos` already returns the required fields.
Verify it outputs `relationship_coverage` correctly once user_relevance records exist (currently returns `"unknown"` — that is correct for now).

**6. Additional tests**
- Vendor template ingest dry-run produces correct added/updated/skipped counts
- Posture promotion preserves conflicting claims
- Graph validates after vendor ingest
- `query-vendor` returns correct fields for a seeded relationship

---

## Done Definition

Sprint closes when:

- [ ] Existing Technomic baseline still valid (`validate.py --ecosystem-only` passes)
- [ ] At least one POS vendor seed is ingested for ≥ 3 of the top 10 priority brands
- [ ] At least one fixture proves system_of_record_pos ≠ approved_hardware_vendor for the same brand
- [ ] Conflicting claims preserved as separate edges (not collapsed)
- [ ] Audit log integration decision documented and wired (even if minimal)
- [ ] All tests pass
- [ ] Sprint-close `summary` output reports:
  - vendor entity count
  - product entity count
  - relationship count
  - count by category
  - count by evidence_posture
  - count by confidence
  - count of vendor_role types

---

## Commands Reference

```bash
# Verify baseline
python3 system/scripts/ecosystem_intelligence.py summary
python3 system/schemas/validate.py --ecosystem-only
python3 -m unittest system.tests.test_ecosystem_intelligence -v

# Surface a brand
python3 system/scripts/ecosystem_intelligence.py query-brand --brand "McDonald's"

# Filter brands for targeting
python3 system/scripts/ecosystem_intelligence.py query-brands --max-rank 10

# Ingest vendor evidence (dry run first)
python3 system/scripts/ecosystem_intelligence.py ingest-vendors \
  system/inbox/ecosystem/pos_seed.csv --dry-run

# Ingest with source class override for a batch
python3 system/scripts/ecosystem_intelligence.py ingest-vendors \
  system/inbox/ecosystem/pos_seed.csv \
  --source-type credible_trade_reporting

# Query a vendor's footprint
python3 system/scripts/ecosystem_intelligence.py query-vendor \
  --vendor PAR --category pos
```
