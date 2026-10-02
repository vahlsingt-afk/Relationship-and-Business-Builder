# RB-DEFECT-007 — LinkedIn Ingestion Treated As Static Contact Import Instead Of Longitudinal RI Mutation

**Date opened:** 2026-05-28  
**Severity:** High  
**Priority:** High  
**Category:** Relationship Intelligence / Ingestion / CoS Behavioral Failure  
**Status:** Resolved — RB 9.52 (2026-06-04)

## Summary

LinkedIn export ingestion parsed records successfully but returned an import-style
summary instead of a Chief-of-Staff relationship-intelligence delta. The user
expected "what changed, why it matters, what mutated, and what should happen
next." The system behaved closer to "file processed successfully."

## Root Cause

`system/scripts/linkedin_ingest.py` already compared export rows against
`baseline_index.json`, but the response shape did not make the longitudinal
relationship intelligence explicit enough. The Custom GPT/API path could pass
while only proving parse/import success, not CoS-grade mutation reporting.

## Fix

Implemented a deterministic LinkedIn delta-intelligence layer:

- Baseline Comparison
- Delta Metrics
- Strategic Relationship Changes
- Professional Change Detection
- Opportunity Detection
- Recommended Actions
- Graph Mutations
- Daily Brief Mutations
- Persistence Verification

The ingester now returns a structured `delta_intelligence` payload and renders
the required sections in `summary_markdown`.

It also adds visible mutation tags/notes for deterministic LinkedIn deltas while
preserving guardrails:

- no trust-score or relationship-tier promotion from LinkedIn edge alone
- no `last_touch` mutation from export presence
- operator-confirmed conflicts are held for review

## Files Changed

- `system/scripts/linkedin_ingest.py`
- `system/scripts/api_smoke_test.py`
- `system/tests/test_linkedin_ingest_delta_intelligence.py`

## Verification

- `python3 -m pytest system/tests/test_linkedin_ingest_delta_intelligence.py -q`
- `python3 -m pytest system/tests/test_custom_gpt_artifact_routing.py system/tests/test_linkedin_ingest_delta_intelligence.py -q`
- `python3 system/scripts/api_smoke_test.py`
- `python3 -m py_compile system/scripts/linkedin_ingest.py system/api/server.py system/scripts/canonical_response_eval.py`

## Acceptance Notes

The API smoke test now fails if LinkedIn ZIP dry-run ingest lacks:

- `delta_intelligence`
- `## Baseline Comparison`
- `## Graph Mutations`
- `## Persistence Verification`

Live validation still needs a fresh Custom GPT run against a real uploaded
LinkedIn export ZIP after the updated OpenAPI/action schema and instructions
are reloaded.
