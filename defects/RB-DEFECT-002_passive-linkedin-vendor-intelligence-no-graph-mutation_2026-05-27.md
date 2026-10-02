# RB-DEFECT-002 — Passive LinkedIn Vendor Intelligence Failed To Mutate Industry Graph

**Date opened:** 2026-05-27  
**Severity:** High  
**Category:** RB / Industry Intelligence / Graph Mutation / Passive RI Ingestion  
**Status:** Closed — RB 9.18 (2026-05-28)

## RB 9.18 Closure Summary

- Vendor verification queue (`system/inbox/ecosystem/vendor_verification_queue.json`) has 4 open items (Qu / Blaze Pizza, Dave's Hot Chicken, GoTo Foods, Playa Bowls)
- `GET /intelligence/passive/verification-queue` endpoint added — lists items filtered by status and vendor; returns promotion path
- `POST /intelligence/passive/promote-claim` endpoint added — wraps `ecosystem_intelligence.promote-confidence`; requires real source attachment; enforces posture ladder; confirm=false for dry run
- Promotion flow enforces: no promotion without source title + type; vendor claims need independent evidence; conflicting records must be resolved first; audit-logged via P-009
- `ecosystem_intelligence.py promote-posture` and `promote-confidence` CLI already enforced these rules; API endpoint now exposes the same gate

## Observed Behavior

User supplied a LinkedIn screenshot from Qu publicly naming Blaze Pizza, Dave's Hot Chicken, GoTo Foods, and Playa Bowls as Qu customers.

RB analyzed the strategic meaning correctly in prose form, but did not automatically:

- mutate the restaurant industry graph
- create/update Qu customer relationship edges
- tag confidence/source metadata
- queue verification tasks
- expand GoTo Foods into brand-family relationship review
- persist the intelligence without explicit user prompting

This caused RB to behave like an advisory assistant instead of an intelligence accumulation and mutation system.

## Expected Behavior

Passive intake of high-confidence vendor/customer intelligence should trigger:

1. Entity extraction for vendor, customer brands, holding companies/platforms, and product category.
2. Ecosystem graph mutation with vendor/customer edges.
3. Metadata persistence: source type, timestamp, confidence, relationship classification, verification state.
4. Ecosystem enrichment: holding-company expansion review, competitive overlap hooks, vendor penetration tracking, brand growth weighting.
5. Verification queue generation for vendor-claimed relationship, module scope unverified, and deployment depth unknown.

## Local Fix

Implemented in `system/scripts/linkedin_freshness_bridge.py`:

- Extracts known vendor/customer LinkedIn claims for Qu.
- Classifies claims as `vendor_customer_intelligence` and `ecosystem_graph_mutation`.
- Automatically writes provisional Qu POS/customer relationships into `system/ecosystem_intelligence.json` when vendor/customer claims are detected, even if the generic LinkedIn signal endpoint was called without `confirm=true`.
- Adds a `vendor_claimed_customer_relationship` signal to the ecosystem graph.
- Opens verification tasks in `system/inbox/ecosystem/vendor_verification_queue.json`.
- Adds GoTo Foods holding-company expansion review without inferring sibling-brand deployment.

`system/scripts/ecosystem_intelligence.py` now recognizes `linkedin_vendor_post` as a medium-quality, provisional source type.

## Backfill Applied

Backfilled the reported Qu LinkedIn screenshot summary into the current ecosystem graph:

- Blaze Pizza → Qu / POS
- Dave's Hot Chicken → Qu / POS
- GoTo Foods → Qu / POS
- Playa Bowls → Qu / POS

All four are `evidence_posture=provisional`, `confidence=medium`, `deployment_claim_type=logo_or_customer_page`, and `verification_state=needs_verification`.

## Verification

- `python3 -m py_compile system/scripts/linkedin_freshness_bridge.py system/scripts/ecosystem_intelligence.py system/tests/test_linkedin_freshness_bridge.py` — pass
- `python3 -m pytest system/tests/test_linkedin_freshness_bridge.py system/tests/test_ecosystem_intelligence.py -q` — 22 passed
- `python3 system/schemas/validate.py --ecosystem-only` — pass
- `python3 system/scripts/ecosystem_intelligence.py query-vendor --vendor Qu --category pos` — returns 4 provisional Qu POS customer relationships

## Remaining Work

- Generalize extractor beyond Qu-specific vendor/customer vocabulary.
- Add direct API surface for reviewing vendor verification queue.
- Add corroboration promotion flow from `vendor_claimed` to verified/substantiated when independent sources arrive.
