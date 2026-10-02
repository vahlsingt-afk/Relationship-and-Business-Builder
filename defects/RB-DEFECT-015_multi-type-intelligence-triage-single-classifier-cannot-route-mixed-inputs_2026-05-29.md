# RB-DEFECT-015 — Multi-Type Intelligence Triage: Single Classifier Cannot Route Mixed Inputs

**Filed:** 2026-05-29  
**Priority:** HIGH  
**Severity:** ARCHITECTURAL  
**Category:** Intelligence Ingestion / Input Routing / Chief of Staff Functionality  
**Status:** Resolved — RB 9.53 (2026-06-04)  
**Relationship:** Companion to DEFECT-014 (artifact persistence/retrieval). DEFECT-014 is the output layer; DEFECT-015 is the input layer.

---

## The Core Problem

When a user uploads a file or pastes content into ChatGPT, that input may contain **multiple distinct intelligence types** that belong in different parts of the RB intelligence architecture. The current system assumes an input is a single known type and routes it to a single handler.

**Current model:**
```
Input → classifyArtifact → ONE artifact_type → ONE mutation path
```

**Required model:**
```
Input → triageInput → N intelligence_types → N independent extraction/mutation paths
                                           → each with requires_confirmation: true
```

---

## Examples of Mixed-Type Inputs

### Example 1 — LinkedIn post from a McDonald's executive

Content: David Tovar posts about McDonald's Q2 comparable sales, mentions three new operators joining the system in the Southwest, and discusses QSR consumer spending patterns.

| Intelligence found | Type | Destination |
|---|---|---|
| QSR consumer spending trend | `macro_signal` | `macro_intelligence.process_macro_signal()` |
| McDonald's Q2 comps | `micro_graph_enrichment` | McDonald's micro graph enrichment |
| David Tovar post (author is a contact) | `ri_event` | `relationship_intake.process_relationship_thread()` |
| New Southwest operators joining | `micro_graph_enrichment` | McDonald's micro graph |

**Current behavior:** `processLinkedInSignal` runs macro classification. McDonald's-specific data is not routed to the micro graph. RI signal from Tovar is not captured if he is a known contact.

### Example 2 — Uploaded competitive analysis PDF: "PAR Technology 2026 Market Position"

Content: Revenue trends, customer wins (list of restaurant chains), product roadmap, key personnel changes, competitive comparison vs. Toast.

| Intelligence found | Type | Destination |
|---|---|---|
| Market positioning / competitive analysis | `macro_signal` | `macro_intelligence.process_macro_signal()` |
| PAR Technology entity data (customers, deployments) | `micro_graph_candidate` | New PAR micro graph (DEFECT-014 artifact) |
| Named personnel changes (CMO, VP Sales) | `ri_event` | `relationship_intake.process_relationship_thread()` |
| User's strategic assessment | `strategic_memory` | `insight_intake.process_text()` |

**Current behavior:** `classifyArtifact` does not recognize this type. No path exists. The analysis becomes disposable.

### Example 3 — User pastes a meeting transcript

Content: Todd's conversation with a Foods Connected operator about their rollout friction with PAR, mention that Sterling Douglass has left Olo, and Todd's note that this validates his restaurant-AI-consolidation thesis.

| Intelligence found | Type | Destination |
|---|---|---|
| PAR rollout friction at operator | `macro_signal` | Macro — operator pain signal |
| Sterling Douglass departure from Olo | `ri_event` | RI — job change signal for Olo contact |
| Foods Connected operator pain point | `micro_graph_candidate` | Foods Connected micro graph (future) |
| Todd's thesis validation note | `strategic_memory` | `insight_intake.process_text()` |

**Current behavior:** `manualRelationshipIntake` handles the person-level signal. The macro and strategic signals are lost unless Todd explicitly pastes them into a separate call.

### Example 4 — NSN McDonald's workbook upload

Content: 19,000+ store-level records across operators, field offices, markets.

| Intelligence found | Type | Destination |
|---|---|---|
| McDonald's topology data | `micro_graph_build` | `micro_graph_mcdonalds.py` (today: ad hoc ETL) |

**Current behavior:** Works, but only because a one-off ETL exists. No general path.

---

## Current System Gaps

### Gap 1 — `classifyArtifact` is file-extension routing, not content triage

`classifyArtifact` calls `linkedin_ingest.classify_archive(path)` — it looks at the file extension to recognize `linkedin_export_zip`. It returns a single `artifact_type`. It has no content analysis and does not return multiple types.

### Gap 2 — Each extraction module is siloed

Three extraction entry points exist and work independently:
- `macro_intelligence.process_macro_signal()` — macro/industry signals
- `relationship_intake.process_relationship_thread()` — person-level RI
- `insight_intake.process_text()` — user insight / strategic memory

None of them call each other. None of them check whether the same input also belongs in another layer. The Custom GPT instruction contract assumes the operator knows which endpoint to call for which input — which is the wrong assumption for mixed-type inputs.

### Gap 3 — No multi-type triage layer

There is no `intelligence_triage.py`, no `POST /intelligence/triage`, and no `triageInput` action in the Custom GPT surface. When a user uploads a file or pastes content, the system expects a pre-classified input.

### Gap 4 — Micro graph enrichment has no ingest path

Even if content is correctly identified as `micro_graph_enrichment` for McDonald's, there is no API endpoint to receive it. (`getMicroGraphSummary` is read-only.) This is the DEFECT-014 gap on the output side; DEFECT-015 covers the input side.

### Gap 5 — Custom GPT has no multi-type confirmation pattern

When `classifyArtifact` returns one type, the GPT calls one endpoint. If triage returns three types, the GPT has no instruction for sequencing three independent confirmation gates and three independent mutation calls.

---

## Required Architecture

### Component 1 — `intelligence_triage.py`

New script. Multi-type classifier + extraction pipeline.

```python
def triage_input(
    text: str,
    *,
    source_type: str = "paste",           # paste | file | screenshot | url
    source_name: str | None = None,
    author_name: str | None = None,
    author_company: str | None = None,
    event_at: str | None = None,
    captured_at: str | None = None,
) -> dict:
    """
    Identify all intelligence types present in text and extract each.

    Returns:
    {
      "triage_id": "TRG-YYYY-MM-DD-NNN",
      "input_summary": str,
      "identified_types": [
        {
          "intelligence_type": "macro_signal|micro_graph_enrichment|ri_event|strategic_memory|noise",
          "confidence": "high|medium|low",
          "entity_scoped": bool,          # True if tied to a specific named entity
          "entity_name": str | None,       # McDonald's, PAR Technology, etc.
          "extracted_summary": str,
          "proposed_action": str,
          "proposed_mutation": dict,       # the mutation payload for the target endpoint
          "target_endpoint": str,          # which endpoint/function handles this type
          "requires_confirmation": True,
          "source_refs": list[str],
        }
      ],
      "type_count": int,
      "noise_only": bool,
      "requires_confirmation": True,      # always True for any mutation-bearing result
    }
    """
```

**Classifier logic (lightweight, deterministic — no LLM dependency):**

```python
# Macro signal detection
_MACRO_SIGNALS = {
    "behavioral": ["parking lot", "trade down", "value menu", "consumer stress"],
    "market": ["market share", "comp sales", "unit economics", "deployment"],
    "vendor": ["POS", "back office", "loyalty", "online ordering", "restaurant AI"],
}

# Micro graph triggers (entity-scoped topology data)
_MICRO_GRAPH_TRIGGERS = {
    "mcdonalds_us_ops": ["mcdonalds", "mcd", "nsn", "golden arches", "franchisee"],
    # Future: "par_technology", "toast_pos", "global_payments", etc.
}

# RI triggers (person-level signals)
_RI_TRIGGERS = ["joined", "left", "promoted", "hired", "introduced", "met with",
                 "reached out", "responded", "referenced you", "org change"]

# Strategic memory triggers (user thesis / positioning)
_STRATEGIC_MEMORY_TRIGGERS = ["validates", "this confirms", "my thesis", "watchlist",
                               "strategic priority", "note this", "remember this"]
```

### Component 2 — `POST /intelligence/triage` API endpoint

New endpoint. Returns multi-type triage result. Does not mutate anything — all mutations require subsequent confirmed calls.

```json
POST /intelligence/triage
{
  "text": "...",
  "source_type": "paste|file|screenshot|url",
  "source_name": "optional filename or URL",
  "author_name": "optional",
  "author_company": "optional",
  "event_at": "optional ISO date",
  "captured_at": "optional ISO date"
}

Response:
{
  "triage_id": "TRG-2026-05-29-001",
  "identified_types": [...],
  "type_count": 3,
  "noise_only": false,
  "persistence_status": "not_persisted — all mutations require confirmation"
}
```

### Component 3 — Confirmed mutation dispatch

For each identified type in the triage result, the operator calls the appropriate confirmed-write endpoint:

| intelligence_type | Target endpoint | Confirmation pattern |
|---|---|---|
| `macro_signal` | `POST /linkedin/signals` (confirm=true) | Existing |
| `ri_event` | `POST /relationship_intake` (confirm=true) | New (DEFECT-011) |
| `strategic_memory` | `POST /strategic_memory` (confirm=true) | Existing (`recordStrategicMemory`) |
| `micro_graph_enrichment` | `POST /artifacts/{id}/enrich` (confirm=true) | New (DEFECT-014) |
| `micro_graph_build` | `POST /artifacts/build` | New (DEFECT-014) |

### Component 4 — Custom GPT multi-type triage rendering contract

New rendering rule in `custom_gpt_prompt.md` and `custom_gpt_instructions_8k.md`:

```text
When any user input (upload, paste, screenshot, URL, transcript, meeting notes,
article, or competitive analysis) is received that may contain intelligence:

1. Call triageInput (POST /intelligence/triage) before calling any specific
   extraction endpoint.
2. Render the triage result as a structured confirmation menu:
   - Show each identified_type, its confidence, and proposed_action.
   - For each type, ask confirmation before calling the mutation endpoint.
   - Handle each confirmation independently — do not batch mutations.
   - Report persistence_status per type after each confirmed mutation.
3. If type_count > 1, process each type in this order:
   - ri_event first (person-level intelligence is most time-sensitive)
   - micro_graph_enrichment second (entity topology)
   - macro_signal third (market/industry)
   - strategic_memory last (user thesis/watchlist)
4. If noise_only is true, say so and do not attempt any mutations.
5. Never call a single extraction endpoint (processLinkedInSignal,
   manualRelationshipIntake, etc.) as the first step for an unclassified input.
   Always triage first.
```

### Custom GPT confirmation surface for multi-type inputs:

```text
RB found [N] intelligence type(s) in your input:

1. [macro_signal] — [extracted_summary] (confidence: high)
   → Proposed: record macro signal to behavioral_intelligence.json
   → Confirm? Y/N

2. [ri_event] — [extracted_summary] (confidence: medium)
   → Proposed: record RI event for [person/entity]
   → Confirm? Y/N

3. [strategic_memory] — [extracted_summary] (confidence: high)
   → Proposed: record to strategic_memory under tag [tags]
   → Confirm? Y/N

Persistence status: not_persisted — awaiting confirmation per type above.
```

---

## Relationship to DEFECT-014

| Layer | Defect | Description |
|---|---|---|
| Input / Triage | **DEFECT-015 (this)** | Multi-type classifier; routes inputs to correct mutation paths |
| Output / Persistence | **DEFECT-014** | Artifact registry; enrichment API; general micro graph ETL |

DEFECT-015 depends on DEFECT-014's artifact persistence layer for the `micro_graph_enrichment` and `micro_graph_build` mutation paths. However, the triage layer can be built first and the micro graph enrichment path can be stubbed with a `requires_confirmation: True` placeholder until DEFECT-014's `POST /artifacts/{id}/enrich` is implemented.

---

## Success Criteria

1. `POST /intelligence/triage` receives the 2026-05-29 McDonald's LinkedIn post example and returns three identified_types: `macro_signal`, `micro_graph_enrichment`, `ri_event`.
2. `POST /intelligence/triage` receives a meeting transcript and correctly identifies all types present (no type is silently dropped).
3. The Custom GPT calls `triageInput` for all unclassified inputs before calling any specific extraction endpoint.
4. Each identified type is confirmed and mutated independently — a rejected confirmation for type 1 does not block type 2.
5. `persistence_status` is reported per type after each confirmed mutation.
6. Inputs that contain only noise return `noise_only: true` and no mutation is proposed.
7. Full test suite remains green.
8. Regression tests cover: single-type input, multi-type input, noise-only input, mixed RI + macro, mixed micro + RI.

---

## Recommended Sprint Sequence

**Sprint RB 9.25A — DEFECT-015 (triage layer) + DEFECT-014 (artifact registry)**
These should be co-developed. The triage layer classifies inputs; the artifact registry receives the micro graph enrichment mutations.

**Sprint RB 9.25B — Enrichment + confirmed dispatch**
`POST /artifacts/{id}/enrich` and the confirmed dispatch loop for all three triage-identified types.

**Sprint RB 9.25C — General micro graph ETL generalization**
Generalize `micro_graph_mcdonalds.py` so the `micro_graph_build` path works for any entity topology dataset.

---

## Data Models

### IntelligenceType enum
```
macro_signal            Market/industry/consumer/vendor signal
micro_graph_build       New entity topology dataset (first-time artifact creation)
micro_graph_enrichment  Additional data for an existing entity artifact
ri_event                Person-level relationship signal
strategic_memory        User thesis / positioning / watchlist update
noise                   No actionable intelligence identified
```

### TriageResult
```json
{
  "triage_id": "TRG-YYYY-MM-DD-NNN",
  "captured_at": "ISO",
  "input_hash": "sha256 of input text",
  "input_summary": "first 200 chars",
  "source_type": "paste|file|screenshot|url",
  "source_name": "optional",
  "identified_types": [IntelligenceStream],
  "type_count": int,
  "noise_only": bool,
  "persistence_status": "not_persisted"
}
```

### IntelligenceStream
```json
{
  "intelligence_type": "macro_signal",
  "confidence": "high|medium|low",
  "entity_scoped": false,
  "entity_name": null,
  "extracted_summary": str,
  "extracted_entities": [str],
  "extracted_signals": [str],
  "proposed_action": str,
  "proposed_mutation": dict,
  "target_endpoint": "/linkedin/signals",
  "requires_confirmation": true,
  "source_refs": [str],
  "event_at": "ISO date or null",
  "event_at_confidence": "high|medium|low"
}
```
