# Canonical Registry Consolidation Plan

**Date:** 2026-08-22/23 (P1-4, RBB trustworthiness workstream)
**Status:** design-doc only — no implementation today. `loop_reconciliation.py` (built and tested today, see `system/scripts/loop_reconciliation.py`) is the one piece of this scope that got real code, because it's a concrete, already-bounded subset of `opportunities` consolidation, not a fourth greenfield registry.

## Why this is scoped as a doc, not a build, today

`CANONICAL_REGISTRY.yaml` marks four domains `distributed_consolidation_pending`: `accounts`, `opportunities`, `strategic_theses`, `decisions`. Each has 3–5 upstream stores today and zero scaffold/draft code anywhere in the repo (confirmed via grep — `canonical_account_registry`, `canonical_opportunity_registry`, `canonical_thesis_registry`, and `canonical_decision_ledger` appear nowhere except as `consolidation_target` values in the registry itself). Building all four for real in one day isn't realistic, and rushing one without the others risks the same "distributed evidence_only stores, invented authority" trap the registry was written specifically to avoid. What's realistic today is naming the current fragmentation precisely and recommending an order — the actual migration work is separately scoped, later.

## Current fragmentation, per domain (from `CANONICAL_REGISTRY.yaml`)

### `accounts`
- **Current stores:** `system/account_intelligence/` (loose markdown docs), `system/ecosystem_intelligence.json` (the entity/relationship graph), `system/campaigns/`, `system/strategic_events.json`.
- **Mutation owners:** `ecosystem_intelligence.py`, `intelligence_mutation_engine.py`, `campaign_engine.py` — three different scripts, no shared writer.
- **Also relevant:** `blue_sheets/` (parked 2026-08-21) is explicitly *downstream* of this domain, not a replacement for it — it needs a real `account_id` to key off of, which doesn't exist yet as a canonical concept, only as an ecosystem-graph `entity_id` (`brand-<slug>`) that different scripts reference inconsistently (see `loop_reconciliation.py`'s sibling finding today: `ecosystem_intelligence.py`'s `entity_id` format is clean and consistent; `account_intelligence/`'s loose markdown has no structured id at all).

### `opportunities`
- **Current stores:** `system/active_threads.yaml`, `system/loop_ledger.md`, `system/eolms/loops.json`, `system/account_intelligence/`, `system/campaigns/`.
- **Mutation owners:** `mutations.py`, `eolms.py`, `weekly_planning.py`.
- **Already has a first concrete slice:** `loop_reconciliation.py` (built today) — detects possible duplicate intent between the `loop_ledger.md`/`eolms/loops.json` pair specifically. It does not touch `active_threads.yaml` or `account_intelligence/`'s opportunity-shaped content; a real opportunity registry would need to reconcile all five stores, not just the two loop namespaces.

### `strategic_theses`
- **Current stores:** `system/active_threads.yaml`, `system/strategic_events.json`, `system/account_intelligence/`, `system/research/`.
- **Mutation owners:** `strategic_events.py`, `intelligence_mutation_engine.py`.
- **Note:** `active_threads.yaml` and `strategic_events.json` already overlap with `opportunities` above — a thesis and an opportunity aren't always cleanly distinguishable in the current stores, which is itself evidence for the recommended order below (resolve identity/account first, so thesis-vs-opportunity classification has a stable anchor to classify against).

### `decisions`
- **Current stores:** `system/_sessions/`, `system/active_threads.yaml`, `system/ri_events/*.jsonl`, `system/audit/*.jsonl`, `system/account_intelligence/`.
- **Mutation owners:** `session_writer.py`, `ri_events.py`.
- **Already `legacy_status: evidence_only`, already append-only** — the lowest-risk of the four, since nothing currently claims decisions-store authority that would need to be revoked or migrated away from.

## Recommended migration order, and why

1. **`accounts` first.** `opportunities`, `strategic_theses`, and `blue_sheets` all key off account identity. Building any of the other three before a canonical account registry exists means re-deriving "which account is this really about" three separate times, with three separate chances to get it wrong differently each time. `ecosystem_intelligence.py`'s `entity_id` format (`brand-<slug>`) is the closest thing to a clean identity scheme already in production — the account registry's identity layer should very likely start from that, not invent a new id scheme.
2. **`opportunities` second.** `loop_reconciliation.py` is the first real slice of this work, already built and tested. The remaining scope is reconciling `active_threads.yaml` and `account_intelligence/`'s opportunity-shaped content against the now-canonical account layer from step 1.
3. **`strategic_theses` third.** Depends on both account identity (step 1) and a settled opportunity/thesis boundary (informed by step 2's reconciliation work) to classify cleanly.
4. **`decisions` last.** Already append-only and lowest-risk; consolidating it is mostly a matter of picking one authoritative append target and redirecting the two mutation owners to it, not resolving competing authority claims the way the other three domains need.

## What "consolidation" concretely means when this work resumes

Per the pattern already established for `blue_sheets` (`CANONICAL_REGISTRY.yaml`'s `architectural_decision` for that domain) and for `execution_loops` (today's `loop_reconciliation.py`): consolidation does not mean picking one existing store and deleting the others. It means:
- Naming one store (or a new one) as authoritative, with the others explicitly demoted to `evidence_only` or `projection`.
- Defining a mutation-owner script (or a shared writer function multiple scripts call) that all future writes go through.
- Building a reconciliation/detection pass first (as `loop_reconciliation.py` did for loops) to surface how much the existing stores actually disagree with each other today, *before* deciding how to resolve the disagreements — guessing at the resolution rule before seeing the real data is how the L-/EL- split happened in the first place.

## Explicitly out of scope for this doc

Actually building any of `canonical_account_registry`, `canonical_opportunity_registry`, `canonical_thesis_registry`, or `canonical_decision_ledger` — each is real, separately-scoped work for a future session, not something to start opportunistically alongside today's other three workstreams.
