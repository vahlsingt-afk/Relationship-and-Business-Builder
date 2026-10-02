# RB 9.90: Company Intelligence File + Enrich-Before-Comment — Slice 3 of RB-DEFECT-046 (UIPF)

**Status:** Implemented (2026-06-15)
**Source:** `CLAUDE_DEFECT_RB_046_UNIVERSAL_INTELLIGENCE_PROCESSING_FRAMEWORK.md`,
Proposed Implementation Slice 3 ("Enrich-before-comment for passive
ingestion"), building on Slice 2 (`CLAUDE_SPRINT_RB_9_89_STRATEGIC_NARRATIVES.md`).

## Motivating gap

Slice 2 gave RB a place to *store* accumulated brand intelligence
(`strategic_narratives[]`). This slice gives RB a way to *retrieve* it before
commenting — both for the Custom GPT (chat-driven company queries already had
`getEntitySignals`, but nothing rendered the tech-stack/narrative view) and
for the passive article-ingestion pipeline (`generate_mutations()` produced
new mutations with no visibility into what RB already knew about that brand).

## What was implemented

### 1. `build_company_intelligence_file(entity_name, ecosystem=None)`

New function in `system/scripts/intelligence_mutation_engine.py`. Resolves
`entity_name` to a brand entity via `_resolve_brand()`; if no match, returns
`{"available": False, "reason": ...}`. Otherwise returns:

```json
{
  "available": true,
  "brand_id": "brand-hungry-howie",
  "brand_name": "Hungry Howie",
  "tech_stack": {
    "pos": {"vendor": "Toast", "status": "active", "source": {...}},
    "back_office": {"vendor": "Restaurant365", "status": "active", "source": {...}},
    "loyalty": {"vendor": null, "status": "sunset", "source": null},
    "online_ordering": {"vendor": null, "status": "unknown", "source": null},
    "payments": {"vendor": null, "status": "unknown", "source": null},
    "workforce": {"vendor": null, "status": "unknown", "source": null},
    "voice_ai": {"vendor": null, "status": "unknown", "source": null},
    "automation": {"vendor": null, "status": "unknown", "source": null}
  },
  "strategic_narratives": [ ... ],
  "last_verified": "2026-06-15T18:05:00+00:00"
}
```

Per-category status:
- `active` — an active `uses_vendor_for_category` relationship exists;
  `vendor` is the linked vendor entity's name.
- `sunset` — the brand's `tech_stack_modernization` narrative records a
  `sunset` signal for this category with no subsequent active relationship
  (i.e. not yet replaced); `vendor: null`.
- `unknown` — no relationship and no sunset signal for this category.

`last_verified` is the max of all active relationships' `updated_at`/
`created_at` and the narrative's `last_updated`.

### 2. `generate_mutations()` — additive `enrichment` field

For every `vendor_customer_relationship` or `category_lifecycle_signal`
mutation produced, captures the **pre-mutation** Company Intelligence File for
that brand (built from the `ecosystem` dict as passed into
`generate_mutations()`, before any mutation is applied) — i.e. "what RB
already knew about this brand before this article." Keyed by `brand_id` in
`result["enrichment"]`, deduplicated per brand. This is the "enrich before
commentary" hook: a caller summarizing a new article about Hungry Howie's
loyalty sunset can see, in the same response, that Toast (POS) and
Restaurant365 (back-office) were already on file — enabling "this is the third
signal in an ongoing modernization" framing instead of an isolated summary.

### 3. New endpoint: `GET /entities/{entity_name}/company-intelligence-file`

`operation_id=getCompanyIntelligenceFile`, added in `system/api/server.py`
next to the existing `getEntitySignals` endpoint, calling
`intelligence_mutation_engine.build_company_intelligence_file(entity_name)`
directly. Added to `system/api/openapi.yaml` (full spec) and to the Custom GPT
allowlist in `system/scripts/validate_openapi_gpt.py`
(`GPT_OPERATIONS`, 29 -> 30 — at the documented 30-op Custom GPT Actions cap),
regenerating `system/api/openapi_gpt.yaml`.

### 4. CLI

`python3 system/scripts/intelligence_mutation_engine.py --company-file
"Hungry Howie's"` prints a human-readable Company Intelligence File (tech
stack table + strategic narrative), mirroring the format from the original
defect doc's example. `--json` returns the raw dict.

### 5. Custom GPT prompt instruction

`system/api/custom_gpt_prompt.md` — added a step after the existing
`getEntitySignals` pattern-synthesis instruction: when the input describes a
tech-stack/vendor change, call `getCompanyIntelligenceFile(entity_name)` first
and, if a `tech_stack_modernization` narrative exists, render "Strategic
narrative" output (confidence, already-known supporting signals, how this
signal fits, next expected signals) before the CoS assessment.

Note: `custom_gpt_instructions_compact_8k.md` (the live 8K-token instruction
set) does not reference `getEntitySignals` either, so `getCompanyIntelligenceFile`
was not added there — consistent with existing precedent. The fuller
`custom_gpt_prompt.md` is the reference doc for this instruction.

## Tests

`tests/test_company_intelligence_file.py` (6 new tests):
- `build_company_intelligence_file()`: unknown entity -> `available: False`;
  known brand with no data -> all 8 tracked categories `unknown`; active
  relationship populates `tech_stack[category]`; unresolved sunset signal ->
  `status: "sunset"`; active relationship after a sunset signal for the same
  category takes precedence (no leftover `sunset` entry).
- `generate_mutations()`'s `enrichment` field: first article about a new
  brand -> `enrichment[brand_id]["available"] == False` (nothing known yet);
  a follow-up article -> `enrichment[brand_id]` reflects the prior article's
  POS relationship and narrative (confidence `low`, 1 supporting signal) —
  demonstrating enrich-before-comment end-to-end.

Full suite: 2410 passed (up from 2404). `tests/test_wiring_gaps.py`'s
`test_WG4c_total_operation_count_is_29` renamed to
`test_WG4c_total_operation_count_is_30` and updated to assert 30, reflecting
the new GPT operation.

## Deferred / not in this slice

- **`daily_brief.py` rendering.** No canonical brief section yet renders
  `strategic_narratives[]`, `tech_stack`, or the `enrichment` field from
  `generate_mutations()`. Per the same cadence as Slice 1's `triage_signals`
  (validate signal quality against real `apply_mutations()` runs before
  committing to a brief section design), this is deferred until Slice 2/3's
  machinery has run against real article ingestion and produced narratives
  worth rendering.
- **Slice 1 (`triage_signals`) is not yet consumed here.** Email/calendar
  `triage_signals` (Slice 1) and article-text `generate_mutations()`
  (Slices 2-3) remain separate pipelines; connecting them (so an
  intelligence-bearing email about a vendor change also updates
  `strategic_narratives[]`) is future work.
- The GPT operation budget is now at the 30-op cap (30/30). Any future
  addition to `GPT_OPERATIONS` requires retiring an existing operation first.

## RB-DEFECT-046 (UIPF) status

All three proposed implementation slices are now implemented:
1. Universal router (`triage_overlay_text`, RB 9.88) — `CLAUDE_SPRINT_RB_9_88_UNIVERSAL_ROUTER_SCOPING.md`
2. Persisted Strategic Narrative schema (RB 9.89) — `CLAUDE_SPRINT_RB_9_89_STRATEGIC_NARRATIVES.md`
3. Enrich-before-comment + Company Intelligence File (RB 9.90) — this doc

The Hungry Howie's example from the defect doc is now structurally
addressable end-to-end: a sequence of articles about Toast (POS),
Restaurant365 (back-office), and a Howie Rewards (loyalty) sunset accumulates
into one `tech_stack_modernization` narrative (`confidence: "high"`,
`next_expected_signals: ["loyalty platform replacement announcement"]`),
retrievable via `getCompanyIntelligenceFile` and surfaced to
`generate_mutations()` callers via `enrichment` — without the brief-rendering
step, which remains deferred pending real-data validation.
