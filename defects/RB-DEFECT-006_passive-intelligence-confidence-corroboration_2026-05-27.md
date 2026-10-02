# RB-DEFECT-006 — Passive Intelligence Confidence + Corroboration Gap

## Status

Closed — RB 9.18 (2026-05-28)

## RB 9.18 Closure Summary

- `evaluatePassiveIntelligence` now persists `corroboration_search_queue` items to `system/.cache/corroboration_search_queue.json` on every call; deduplicated by claim_id; resolved/dismissed items not overwritten
- `GET /intelligence/passive/corroboration-queue` endpoint added — lists persisted queue items filtered by status; notes that search queries are starting points only
- `POST /intelligence/passive/promote-claim` enforces: no promotion without real source; search plans do not raise confidence; vendor claims need independent evidence; posture ladder is forward-only; confirms via dry-run/write dual mode

Previously implemented and confirmed preserved:

Implemented deterministic assessment layer plus explicit corroboration-search planning.

## Problem

Passive uploads and external posts could be summarized conversationally without isolating factual claims, scoring source incentives, checking corroboration, preserving uncertainty, or gating graph mutation eligibility.

## Fix

- Added `system/scripts/passive_intelligence.py`.
- Added `evaluatePassiveIntelligence` API endpoint.
- Added passive-intelligence metadata fields to ecosystem `signals[]`.
- Wired daily ecosystem signal ingestion through claim/source/confidence assessment before graph signal writes.
- Added per-claim `corroboration_search_plan` and summary-level `corroboration_search_queue` so RB knows which primary/credible sources to seek before promotion.
- Updated Custom GPT instructions to call passive evaluation before summarizing uploaded/referenced claims.
- Updated schema documentation and added tests.

## Governance Rule

RB must never silently convert anecdote, opinion, rumor, or vendor positioning into implied fact. Only `canonical_fact` and `corroborated_intelligence` are eligible for canonical truth-layer mutation. Weak or emerging claims may remain lower-confidence observations.
