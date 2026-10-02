# RB-DEFECT-014 — Intelligence Artifact System: No General Persist / Enrich / Retrieve Layer

**Filed:** 2026-05-29  
**Priority:** HIGH  
**Severity:** ARCHITECTURAL  
**Category:** Knowledge Management / Intelligence Persistence / Chief of Staff Functionality  
**Status:** Resolved — RB 9.52 (2026-06-04)

## Resolution Summary

Infrastructure already existed (sprint RB 9.25A/B): `/artifacts`, `/artifacts/{id}`, `/artifacts/{id}/enrich`, `/artifacts/classify`, `intelligence_triage.py`, `system/artifacts/registry.json`.

Remaining gap (criterion #6): daily brief did not surface stale or stub artifacts.

**Fix:** Added `_load_artifact_freshness_items()` to `daily_brief.py` — scans `system/artifacts/registry.json`, emits:
- `[ARTIFACT STALE]` items when active artifact `freshness_date` > 30 days (DEGRADED)
- `[ARTIFACT STUB]` items for unbuilt artifacts; Global Payments + Foods Connected get `act_today` disposition (active threads)

Wired into `information_debt_queue` section alongside source debt.

**Also fixed:** `test_priority_compression.py` overflow tagging broken by DEFECT-016-C category balance; `test_daily_brief_autonomous_discovery.py` novelty block missing on external module items.

**Test suite: 2,255 passed, 0 failures.**  

---

## Diagnosis Precision (Filed by Codex)

Before describing the gap, the *current actual state* must be stated clearly to prevent fixing the wrong thing.

### What currently exists

The McDonald's NSN micro graph **was built, persisted, and is queryable**:

- Artifact location: `system/graphs/micro/mcdonalds_us_ops/`
- `graph.json`: 86 MB, 19,694 nodes, 127,385 edges
- `index.json`: 1,347 operator entities with stores, 14,049 stores mapped, pre-computed tier distribution
- `getMicroGraphSummary` API route (`/graphs/micro_summary`): returns structured answer to "how many operators" in one API call, no re-upload required
- `getMicroGraphIndex` route (`/graphs/micro/index`): routes company-specific questions to the correct micro graph
- Custom GPT instructions (rule #9): explicitly call `getMicroGraphSummary` for any McDonald's / NSN / StoreTech question
- `micro_graph_query.py`: compact retrieval/index CLI layer

Live smoke confirmation (2026-05-29):

```
getMicroGraphSummary(query="McDonald's") →
  "McDonald's U.S. micro graph currently contains 1,347 operator/entity nodes
   with mapped stores, 14,049 stores mapped to an operator/entity, 103
   enterprise-scale operators/entities with 25+ stores, 807 mid-tier
   operators/entities with 5–24 stores, and 437 single-digit
   operators/entities with 1–4 stores."
```

### What the defect report observed ("No accessible McDonald's Micro Graph exists")

Likely causes — **in order of probability**:

1. **API server not running.** `getMicroGraphSummary` is a live HTTP call. If `server.py` is not running or is unreachable, the Custom GPT falls through to model knowledge and honestly reports it cannot access the graph. This is not a persistence failure; it is an availability failure (see DEFECT-001 pattern).

2. **Custom GPT not calling the right action.** The GPT may have skipped the `getMicroGraphSummary` call and answered from general knowledge, then truthfully admitted it could not access the graph when asked directly. Rule #9 should have triggered on "McDonald's" but rule compliance depends on session context.

3. **Session context lost.** The McDonald's micro graph route is in the 8k instruction file as a trigger-based rule. If the session instruction was truncated or the Custom GPT's active action surface was narrower than expected, the trigger may not have fired.

**The McDonald's micro graph itself is not the defect.** It exists, it answers questions, and the API route works. The defect is the missing general system that would make this reliable and repeatable for any major dataset.

---

## The Actual Architectural Defect

RB has one manually-built micro graph (McDonald's) and one manually-written ETL script (`micro_graph_mcdonalds.py`) that is specific to NSN `.xlsx` topology workbooks. There is no general-purpose intelligence artifact system.

**When a user uploads or references a major business dataset, RB has no:**

1. **General artifact ingest pipeline** — `micro_graph_mcdonalds.py` cannot handle a PAR Technology competitive analysis PDF, a Toast customer relationship export, a Global Payments account dossier, or a Foods Connected org chart. Each new artifact would require a new one-off ETL script.

2. **Artifact classification layer** — `classifyArtifact` exists (DEFECT-010 surface) but it routes to `linkedin_export_zip` and similar known types. There is no `competitive_intelligence_dataset`, `account_dossier`, `vendor_topology_workbook`, or `micro_graph_candidate` classification path.

3. **Auto-registration** — `system/graphs/micro/index.json` is hand-edited. Adding a second micro graph (e.g., PAR Technology market analysis) requires manual JSON edits to the registry, not a command.

4. **Enrichment workflow** — there is no `enrichArtifact` API endpoint. If new NSN data arrives next month, the operator is expected to re-run `micro_graph_mcdonalds.py` with the new file. There is no "here is a new signal — append it to artifact X" path.

5. **General artifact API surface** — `getMicroGraphSummary` and `getMicroGraphIndex` work for McDonald's only. There is no `listIntelligenceArtifacts`, `getArtifactDetails`, `queryArtifact`, or `recordArtifactSignal` pattern that scales across artifact types.

6. **Durability contract** — the current micro graph is a local file directory. There is no `artifact_status` field, no `freshness_date` enforcement, no "artifact is stale" detection in the daily brief, and no recovery path if `graph.json` is corrupted or absent.

7. **Confidence / source lineage** — the micro graph `sources.json` exists but is only populated for McDonald's. There is no general schema for recording: who uploaded this, when, from what source, what confidence level, what subsequent enrichments were applied.

---

## Scope: What This Defect Does NOT Include

- The McDonald's micro graph content quality (that is a separate data-enrichment concern).
- The market_distribution None values in the current index (data quality, not architecture).
- API server availability (DEFECT-001).
- Custom GPT action routing reliability (DEFECT-009).

---

## Failure Specimen (User-Reported)

> User: "Can you access my McDonald's Micro Graph?"  
> RB: "No."

This indicates one of: API server down, GPT rule not firing, or GPT action surface narrow. The graph itself is intact.

The broader failure pattern that motivates the architectural defect:

> User: "Build a PAR Technology competitive intelligence artifact from this analyst report."  
> RB: *No general path exists. Would require a new one-off ETL script.*

> User: "Enrich the McDonald's micro graph with this new NSN file."  
> RB: *No enrichment endpoint exists. Requires full re-run of `micro_graph_mcdonalds.py`.*

> User: "What intelligence artifacts does RB have?"  
> RB: *No `listIntelligenceArtifacts` route. No general catalog.*

---

## Required Architecture

### Intelligence Artifact Registry

A general-purpose catalog at `system/artifacts/registry.json` (or extending `system/graphs/micro/index.json`) that records:

```json
{
  "artifact_id": "micro:mcdonalds_us_ops",
  "artifact_type": "micro_graph",
  "entity": "McDonald's",
  "status": "active",
  "freshness_date": "2026-05-27",
  "source_lineage": [...],
  "confidence": "high",
  "queryable_via": "getMicroGraphSummary",
  "last_enriched": "2026-05-27",
  "enrichment_count": 1
}
```

### Artifact Types (minimum viable)

| Type | Example | Current Support |
|---|---|---|
| `micro_graph` | McDonald's NSN topology | PARTIAL (manual ETL) |
| `account_dossier` | Foods Connected org + history | NOT BUILT |
| `competitive_intelligence` | PAR Technology product/customer analysis | NOT BUILT |
| `vendor_topology` | Toast customer footprint | NOT BUILT |
| `strategic_account` | Global Payments relationship context | NOT BUILT |

### API Surface (minimum viable)

| Endpoint | Purpose | Current State |
|---|---|---|
| `GET /artifacts` | List all registered artifacts | NOT BUILT |
| `GET /artifacts/{id}` | Get artifact details | NOT BUILT (getMicroGraphSummary is McDonald's-specific) |
| `POST /artifacts/classify` | Classify an uploaded dataset → artifact type | PARTIAL (classifyArtifact handles linkedin_export_zip only) |
| `POST /artifacts/{id}/enrich` | Add new signal/data to existing artifact | NOT BUILT |
| `GET /artifacts/{id}/query` | Free-form question against artifact | NOT BUILT (getMicroGraphSummary returns fixed structure) |

### Enrichment Workflow (minimum viable)

1. User uploads or pastes new data related to an existing artifact.
2. `classifyArtifact` routes to `artifact_type=micro_graph_enrichment` or similar.
3. RB extracts signals, proposes additions to the artifact.
4. User confirms.
5. Artifact is enriched; `last_enriched`, `enrichment_count`, and source lineage updated.
6. Daily brief reflects updated artifact freshness.

---

## Success Criteria

1. `GET /artifacts` returns all registered artifacts including McDonald's micro graph.
2. `GET /artifacts/micro:mcdonalds_us_ops` returns queryable intelligence without re-upload.
3. "How many McDonald's operators are there?" is answered from the artifact, not model knowledge, regardless of which GPT session asks it.
4. A second micro graph (e.g., PAR Technology) can be registered without writing a new one-off ETL script.
5. `POST /artifacts/micro:mcdonalds_us_ops/enrich` accepts a new NSN file and updates the artifact.
6. Daily brief `resource_verification_and_freshness_status` reports stale artifacts when `freshness_date` exceeds threshold.
7. Full test suite remains green after implementation.

---

## Recommended Sprint Sequence

**Sprint RB 9.25A — Artifact Registry + General API Surface**
- `system/artifacts/registry.json` schema and loader
- `GET /artifacts` and `GET /artifacts/{id}` routes
- Migrate McDonald's micro graph registration to general registry
- Update Custom GPT instructions to use `listArtifacts` / `getArtifact` instead of McDonald's-specific route

**Sprint RB 9.25B — Artifact Classification + Enrichment**
- Extend `classifyArtifact` to recognize `competitive_intelligence`, `account_dossier`, `micro_graph_candidate`
- `POST /artifacts/{id}/enrich` endpoint
- Enrichment workflow in Custom GPT instructions

**Sprint RB 9.25C — General Micro Graph ETL**
- Generalize `micro_graph_mcdonalds.py` into `micro_graph_builder.py` that accepts any structured topology dataset
- Document artifact creation flow for PAR, Toast, Global Payments, Foods Connected

---

## Immediate Mitigation (Before RB 9.25)

The McDonald's micro graph is already queryable. The immediate gaps are:

1. **API server must be running** for `getMicroGraphSummary` to work — no code change needed, operational discipline only.
2. **Custom GPT instructions already contain rule #9** — if the rule isn't firing, check session instruction truncation.
3. **No action needed on the micro graph content itself** — it answers operator/store questions correctly.

The architectural defect is real and open, but the McDonald's asset is not lost.
