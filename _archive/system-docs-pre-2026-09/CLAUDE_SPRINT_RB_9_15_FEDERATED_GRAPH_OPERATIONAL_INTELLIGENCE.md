# RB 9.15 Sprint — Federated Graph Operational Intelligence

Date: 2026-05-27
Status: planned

## Purpose

RB is evolving from a relationship assistant into an operational intelligence system that accumulates, queries, and mutates user-owned relationship, industry, and ecosystem data over time.

The macro restaurant ecosystem graph and McDonald's micro operational graph are the first proof points. They must behave as durable intelligence layers, not as uploaded files or prose-only analysis.

## Core Principle

RB should not flatten every signal into one graph.

RB should maintain federated graph layers that connect through shared entity references, provenance, confidence, and activation rules:

- Personal relationship graph: who Todd knows, trust, cadence, proximity, obligations.
- Macro industry ecosystem graph: restaurant brands, vendors, categories, customer relationships, Technomic metrics, public signals.
- Micro operational topology graphs: specific operational ecosystems such as McDonald's NSN/StoreTech/operator topology.
- Signal graph: weak signals, source claims, event evidence, staleness, corroboration tasks.
- Opportunity graph: jobs, deals, intros, timing, decision paths.

These graphs should connect, but not merge.

## Product Requirement

When Todd asks about restaurant brands, restaurant vendors, POS/customer claims, Technomic top-chain data, deployment posture, vendor penetration, or brand performance, RB must query the macro ecosystem graph before answering.

When Todd asks about McDonald's operational topology, store lookup, NSN, StoreTech, operators, markets, field offices, co-ops, OTM/STIM/RFM/FBP/OTP, field support, or deployment accountability, RB must query the McDonald's micro graph before answering.

When Todd supplies passive intelligence from LinkedIn, vendor posts, conference announcements, press releases, customer logos, earnings calls, investor decks, trade coverage, or public brand/operator signals, RB should extract, classify, mutate, persist, queue verification, and report the graph delta.

## Sprint Deliverables

1. Macro graph retrieval surface
   - Add `getEcosystemGraphQuery` to the RB API.
   - Support `summary`, `brand`, `vendor`, and `brands` query modes.
   - Return compact brand metrics, vendor relationships, evidence posture, confidence, sources, and related signals.
   - Expose the action in the Custom GPT OpenAPI subset.
   - Add GPT instruction rules requiring macro graph retrieval for restaurant brand/vendor questions.

2. Micro graph retrieval hardening
   - Keep `getMicroGraphSummary` dormant until contextually activated.
   - Ensure McDonald's operational questions route to the micro graph first.
   - Preserve compact index retrieval so the full graph is not loaded into the GPT context.
   - Add tests for activation terms and failure messaging.

3. Passive graph mutation hardening
   - Generalize LinkedIn/vendor post extraction beyond Qu-specific examples.
   - Support weak-signal classes: `vendor_claimed`, `inferred`, `verified`, `contradicted`, `stale`.
   - Mutate macro graph automatically for high-confidence passive vendor/customer claims.
   - Queue verification tasks for module scope, deployment depth, geography, operator scope, and holding-company expansion.
   - Report relationship additions, updates, skipped duplicates, and verification tasks.

4. Confidence and provenance
   - Every graph mutation must include source type, source timestamp, capture timestamp, source id, confidence, evidence posture, and interpretation scope.
   - Vendor/customer claims must not become systemwide deployments without corroboration.
   - Holding-company mentions must not automatically imply subsidiary brand deployment.
   - Technomic-derived metrics remain Todd-owned data and private to Todd's RB instance.

5. Query behavior and answer contract
   - RB should answer from graph data when graph data exists.
   - RB should say when a graph action is unavailable rather than substituting general knowledge.
   - RB should distinguish macro industry answers from micro operational topology answers.
   - RB should surface source freshness and confidence when giving operational or vendor/customer answers.

6. Assessment and test suite
   - Add API tests for macro graph summary, brand lookup, vendor lookup, and ranked brand filters.
   - Add Custom GPT action subset validation for operation count and required graph actions.
   - Add fixture-based passive signal tests for vendor/customer claims.
   - Add regression tests proving McDonald's macro questions do not unnecessarily activate the micro graph, and operational topology questions do.

## Acceptance Criteria

- A question like "Which brands does Qu have as POS customers?" calls the macro graph and returns Qu customer relationships with posture and confidence.
- A question like "What do we know about Blaze Pizza?" calls the macro graph and includes Technomic metrics plus vendor relationships.
- A question like "How many McDonald's operator entities are in the NSN graph?" calls the micro graph and returns the indexed McDonald's topology summary.
- A passive vendor LinkedIn post naming customers mutates the macro graph without requiring Todd to explicitly say "save this."
- RB never treats Todd's Technomic, McDonald's NSN, relationship, or derived graph data as shared product seed data for other users.
- OpenAPI GPT subset remains at or below 30 operations and validates cleanly.

## Open Questions

- Should the verification queue get its own read/action endpoint for GPT review?
- Should graph mutation require configurable thresholds per source type and tenant profile?
- Should RB maintain separate macro graph files by vertical once restaurants is no longer the only domain pack?
- Should promotion from `vendor_claimed` to `verified` require two corroborating sources, one authoritative source, or Todd confirmation?

## Current Implementation Notes

- `system/ecosystem_intelligence.json` is the current macro restaurant ecosystem graph.
- `system/graphs/micro/mcdonalds_us_ops/index.json` is the GPT-safe McDonald's micro graph index.
- `getEcosystemGraphQuery` is the macro query action.
- `getMicroGraphSummary` is the micro graph query action.
- `processLinkedInSignal` is the current passive signal ingress for LinkedIn-derived ecosystem intelligence.
- `macro_industry_vendor_stack_mutation_seed.xlsx` was applied on 2026-05-27 as the first research-package mutation seed: 38 rows parsed, 38 relationships added, 0 skipped. The macro graph now contains 1,590 brand nodes, 18 vendor nodes, 57 vendor-stack relationships, 54 sources, and 17 relationship categories.
