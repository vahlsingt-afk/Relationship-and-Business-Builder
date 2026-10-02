# Claude Handoff — RB 9.14 Ecosystem Vendor Evidence and Verification

**Date:** 2026-05-27  
**Sprint:** RB 9.14 — Ecosystem Vendor Evidence and Verification  
**Status:** Complete  
**Preceded by:** RB 9.13 Security/Privacy Architecture (Claude), Codex ecosystem graph seed

---

## Sprint-Close Summary

All done-definition items are satisfied. The graph baseline is intact, vendor evidence is ingested, the POS scope distinction is structurally enforced, all tests pass, and the security/privacy integration is wired.

---

## Done Definition — Verified

| Requirement | Status |
|---|---|
| Technomic baseline still valid | ✅ 1,578 brand entities, schema validates |
| Vendor evidence ingested for ≥ 3 of top 10 brands | ✅ 10 of 10 priority brands have POS relationships |
| system_of_record_pos ≠ approved_hardware_vendor fixture | ✅ McDonald's/NewPOS and McDonald's/NCR on separate edges |
| Conflicting claims preserved as separate edges | ✅ Burger King/PAR (system_of_record) + BK/NCR (legacy_incumbent) both stored |
| Audit log wired into ingest | ✅ source_accessed + item_persisted on every live ingest |
| All tests pass | ✅ 14/14 |

---

## Sprint-Close Counts

```
python3 system/scripts/ecosystem_intelligence.py summary
```

```json
{
  "entities": 1586,
  "relationships": 12,
  "signals": 0,
  "assessments": 0,
  "sources": 14,
  "user_relevance": 0,
  "strategic_recommendations": 0,
  "entity_types": {
    "brand": 1579,
    "vendor": 7
  },
  "relationship_categories": {
    "pos": 12
  },
  "relationship_evidence_posture": {
    "substantiated": 2,
    "partially_substantiated": 9,
    "provisional": 1
  },
  "relationship_confidence": {
    "high": 2,
    "medium": 9,
    "low": 1
  },
  "relationship_vendor_roles": {
    "system_of_record_pos": 8,
    "approved_hardware_vendor": 1,
    "legacy_incumbent": 3
  }
}
```

**Vendor entities:** 7 (NewPOS, NCR, PAR Technology, Oracle, Chick-fil-A internal, Domino's internal, Radiant Systems)  
**Product entities:** 0 (products are stored on relationships, not as separate entities — this is correct per the schema)  
**Relationships:** 12 (all POS category)  
**By evidence posture:** substantiated: 2, partially_substantiated: 9, provisional: 1  
**By confidence:** high: 2, medium: 9, low: 1  
**By vendor role:** system_of_record_pos: 8, approved_hardware_vendor: 1, legacy_incumbent: 3  
**Conflicting claims preserved:** Yes — Burger King has two POS edges (PAR as system_of_record_pos + NCR as legacy_incumbent replacing)

---

## What Was Built This Sprint

### Graph optimization (pre-sprint, same session)

**`query-brand --brand <name>`**  
Full brand profile: entity, all vendor relationships with vendor_role, signals, assessments, user_relevance, strategic_recommendations.

**`query-brands` with filters**  
Filter 1,578 brand entities by `--segment`, `--subsegment`, `--min-sales`, `--max-rank`, `--limit`.

**Enhanced `summary`**  
Now reports entity types, relationship categories, evidence_posture breakdown, confidence breakdown, vendor_role breakdown. Sprint-close counts read directly from this output.

**Source quality model**  
10-class `SOURCE_QUALITY_MODEL` maps source_type → (schema_quality, default_evidence_posture, default_confidence). Source type alone is sufficient to get correct defaults; row-level fields override.

**POS role / `vendor_role` field**  
Added to relationship schema and populated from CSV `pos_role` field. Controlled vocabulary: system_of_record_pos, approved_hardware_vendor, payment_device_vendor, acquirer_or_payments, franchisee_deployment, regional_deployment, pilot, legacy_incumbent, replacement_candidate, unknown.

**Conflict-safe relationship IDs**  
ID format: `rel-{brand_id}-{category}-{slug(vendor_role)}-{vendor_id}`. McDonald's/NCR/hardware and McDonald's/NewPOS/system-of-record are now structurally separate edges. Cannot be collapsed by accident.

### `promote-posture` CLI

```bash
python3 system/scripts/ecosystem_intelligence.py promote-posture \
  --relationship-id rel-brand-burger-king-pos-system-of-record-pos-vendor-par-technology \
  --new-posture substantiated \
  --source-title "PAR Technology 2024 Annual Report" \
  --source-type investor_filing_deck
```

Rules:
- Only forward ladder transitions: provisional → partially_substantiated → substantiated
- Conflicting/refuted must be set via ingest-vendors (preserving source evidence)
- Requires a named corroborating source
- Logs `mutation_executed` audit event on every promotion

### Security/privacy integration

- `ingest-vendors` logs `source_accessed` (before processing) and `item_persisted` (after write) on every live run
- Retention class: `derived_intelligence` (90 days) — vendor evidence files are not raw source content
- Raw source text from vendor evidence CSV rows is not stored in the graph — only structured fields
- Dry-run flag suppresses all audit writes

### POS vendor seed

File: `system/inbox/ecosystem/pos_seed.csv`  
12 rows covering all 10 priority brands with sourced, role-scoped POS evidence.

Highlights:
- **McDonald's**: NewPOS (system_of_record_pos/substantiated/high) + NCR (approved_hardware_vendor/partially_substantiated/medium) — separate edges
- **Burger King**: PAR Brink (system_of_record_pos/partially_substantiated/active_rollout/50% penetration) + NCR Aloha (legacy_incumbent/replacing/historical)
- **Domino's**: PULSE (system_of_record_pos/substantiated/high — proprietary, deeply embedded)
- **Chipotle + Starbucks + Subway**: Oracle MICROS (system_of_record_pos/partially_substantiated/medium)
- **Taco Bell + Wendy's**: NCR Aloha (partially_substantiated/medium)
- **Chick-fil-A**: Proprietary POS (partially_substantiated/medium — not publicly disclosed)
- **Dunkin'**: Radiant/NCR (provisional/low — franchise model, uncertain current state)

### `vendor_evidence_template.csv`

File: `system/inbox/ecosystem/vendor_evidence_template.csv`  
Required fields with three annotated example rows. New vendor evidence should follow this format before ingest.

Required columns: brand, vendor, category, product, pos_role, deployment_stage, status, deployed_units, target_units, target_date, penetration_pct, risk, source_type, source, source_url, evidence_posture, confidence, interpretation_scope, strategic_note

### RI / relationship overlay design doc

File: `system/design/RI_OVERLAY_DESIGN.md`  
Defines the full connection model between ecosystem entities and RB's relationship intelligence layer:
- Brand entities → contacts by current company
- Vendor entities → contacts by current company
- Active threads → entity signals (entity linkage only, no raw content in graph)
- Franchisee groups → brand entities via `operates_units_for_brand`
- `user_relevance` record schema and coverage score guidance
- Privacy constraints from P-038
- Open questions before implementation

### Tests (14 total, up from 5)

New tests added this sprint:
- `test_source_quality_model_sets_defaults_from_source_type`
- `test_pos_scope_distinction_system_of_record_vs_hardware`
- `test_conflicting_vendor_claims_stored_as_separate_edges`
- `test_query_brand_returns_full_profile`
- `test_ingest_vendors_dry_run_counts_correctly`
- `test_graph_validates_after_vendor_ingest`
- `test_promote_posture_advances_ladder`
- `test_promote_posture_rejects_backward_move`
- `test_query_vendor_returns_required_fields`

---

## Files Modified or Created This Sprint

**Modified:**
- `system/scripts/ecosystem_intelligence.py` — all optimization and sprint work
- `system/schemas/ecosystem_intelligence.schema.json` — added `vendor_role` to relationship definition
- `system/tests/test_ecosystem_intelligence.py` — 9 new tests
- `system/ecosystem_intelligence.json` — 12 POS relationships ingested, 7 vendor entities, 14 sources

**Created:**
- `system/inbox/ecosystem/pos_seed.csv` — POS vendor seed for top 10 brands
- `system/inbox/ecosystem/vendor_evidence_template.csv` — ingest format reference with examples
- `system/design/RI_OVERLAY_DESIGN.md` — relationship overlay design artifact
- `system/CLAUDE_SPRINT_RB_9_14_KICKOFF.md` — sprint kickoff doc (produced at session start)
- `system/CLAUDE_HANDOFF_RB_9_14_ECOSYSTEM_VENDOR_EVIDENCE_COMPLETE.md` — this file

---

## What Is Not Done (Next Sprint)

**RI / relationship overlay implementation**  
Design doc is complete (`system/design/RI_OVERLAY_DESIGN.md`). Implementation deferred until vendor edges exist at meaningful scale. Next sprint can build `overlay.py` to:
- Match brand/vendor entity names against RB contact profiles
- Write `user_relevance` records to the graph
- Match active threads to entity IDs and write signals

**Additional vendor categories**  
Sprint 9.14 only covers POS. Next category priorities (suggested order): payments, loyalty, online_ordering, labor_scheduling. Same ingest path applies — create a sourced CSV, run `ingest-vendors`.

**Posture promotion from live evidence**  
`promote-posture` CLI is ready. No promotions have been made yet beyond the initial ingest. As corroborating evidence is found for specific relationships, use:
```bash
python3 system/scripts/ecosystem_intelligence.py promote-posture \
  --relationship-id <id> \
  --new-posture <tier> \
  --source-title "<source>" \
  --source-type <class>
```

**Daily brief integration**  
Ecosystem signals (vendor transitions, new relationships, posture promotions) are not yet surfaced in the daily brief. Requires `user_relevance` overlay to be populated first so "who matters now" surfacing has coverage data.

**Vendor category for the 10 priority brands**  
Payments, loyalty, online ordering are the next highest-value categories for the top 10 brands. These require sourced evidence CSVs and the same ingest flow.

---

## Verification Commands

```bash
# Baseline check
python3 system/scripts/ecosystem_intelligence.py summary
python3 system/schemas/validate.py --ecosystem-only
python3 -m unittest system.tests.test_ecosystem_intelligence -v

# Surface a brand
python3 system/scripts/ecosystem_intelligence.py query-brand --brand "McDonald's"
python3 system/scripts/ecosystem_intelligence.py query-brand --brand "Burger King"

# Filter brands
python3 system/scripts/ecosystem_intelligence.py query-brands --max-rank 10

# Query a vendor's footprint
python3 system/scripts/ecosystem_intelligence.py query-vendor --vendor PAR --category pos
python3 system/scripts/ecosystem_intelligence.py query-vendor --vendor Oracle --category pos

# Smoke test
python3 system/scripts/ecosystem_intelligence.py smoke
```
