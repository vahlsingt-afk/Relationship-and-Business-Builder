# RB-DEFECT-027: Codex Intelligence Engine Operating Model Failure

**Date:** 2026-06-07  
**Severity:** Critical  
**Category:** Architecture / Intelligence Operations / Daily Processing  
**Status:** Active Remediation  

## Executive Finding

Relationship Bridge has many of the required intelligence processors, but they do
not yet operate as one continuously accountable intelligence engine.

The current system can collect, classify, mutate, score, and publish. The critical
defect is that these capabilities are fragmented across scripts, caches, optional
steps, and best-effort jobs. A successful brief build does not prove that the
intelligence operating loop completed successfully.

This is a system-level operating-model defect, not an isolated feature bug.

## Required Operating Model

```text
User
  -> ChatGPT Chief of Staff
  -> Codex-owned RB Intelligence Engine
  -> collection + identity + mutation + graph + opportunity + trust processing
  -> canonical intelligence state
  -> daily brief and decision support
```

ChatGPT presents and reasons over canonical intelligence. Claude develops and
remediates the system. The Codex-owned RB Intelligence Engine must be the active,
observable processing layer between source data and presentation.

## Verified Current State

### Present

- A two-phase macOS schedule runs a 4:00 AM pre-brief scan and a 5:00 AM brief build.
- `refresh_all.py` coordinates source refresh, relationship signals, passive RI,
  intelligence assessment, and brief cache generation.
- LinkedIn export watching and delta intelligence exist.
- Apple Contacts identity enrichment exists and is scheduled.
- Newsletter/article intelligence, mutation proposals, graph mutation processing,
  opportunity sensing, and source trust calculations exist.
- The API exposes a source refresh action.

### Defective

1. The 5:00 AM brief-only run overwrote the 4:00 AM scan receipt, erasing the
   most important proof that collection and processing ran.
2. Optional source failures could coexist with an overall pipeline `PASS`.
3. The API refresh action returned source-cache activity, not a complete
   intelligence execution receipt.
4. Brief publication could succeed after upstream degradation, creating a false
   equivalence between "artifact published" and "intelligence refreshed."
5. Historical mutation counts could be presented without a strict run-date
   boundary.
6. No single contract reported records processed, deltas, mutations,
   opportunities, relationship changes, trust, stale sources, errors, and brief
   rebuild status.
7. Available processors are not consistently treated as mandatory stages of one
   operating loop.

## Root Cause

Relationship Bridge evolved processor by processor. Each capability established
its own command, cache, status vocabulary, and failure semantics. The architecture
therefore contains intelligence functions without a sufficiently strong
intelligence control plane.

The primary missing layer is an orchestration contract that:

- owns the complete run;
- records stage start, completion, failure, and skip state;
- distinguishes source unavailability from processor failure;
- measures only current-run deltas;
- blocks or explicitly degrades publication;
- exposes the same receipt to ChatGPT and the operator.

## Corrected Daily Pipeline Contract

1. **Ingestion:** discover and process new files and connector updates.
2. **Identity Resolution:** reconcile names, emails, phones, and duplicate entities.
3. **Mutation Detection:** detect professional, profile, engagement, relationship,
   and communication changes.
4. **Relationship Intelligence:** calculate momentum, sponsor/advocate signals,
   introduction paths, decay, and re-engagement opportunities.
5. **Opportunity Intelligence:** identify hiring, account, relationship, warm-path,
   and timing opportunities.
6. **Market Intelligence:** convert newsletters, earnings, industry news, and
   company news into thesis, watchlist, opportunity, and graph intelligence.
7. **Trust and Delta Analysis:** report freshness, confidence, trust, changes,
   mutations, gaps, and rejected evidence.
8. **Daily Brief Build:** publish only with an explicit `Success`, `Partial`, or
   `Failed` intelligence receipt.

## Required Execution Receipt

Every scheduled and on-demand run must return:

- Refresh Status
- Sources Processed
- Records Processed
- Records Added
- Records Changed
- Mutations Generated
- Opportunities Generated
- Relationship Changes
- Trust Score and Trust Level
- Stale Sources
- Processing Errors
- Intelligence Readiness
- Brief Rebuilt

## Remediation Plan

| Priority | Correction | Target |
|---|---|---|
| P0 | Preserve the pre-brief scan receipt and attach it to the brief-build run | 2026-06-07 |
| P0 | Add the unified execution receipt and expose it through `refreshSources` | 2026-06-07 |
| P0 | Enforce current-run boundaries for delta and mutation reporting | 2026-06-07 |
| P1 | Add stage-level run IDs and durable start/finish/failure state | 2026-06-09 |
| P1 | Make readiness affect publication status, not only metadata | 2026-06-09 |
| P1 | Normalize all ingestors to one trust/delta result schema | 2026-06-10 |
| P1 | Add automatic retry and explicit queued/running status for API refreshes | 2026-06-11 |
| P2 | Unify identity resolution across Contacts, SMS, calls, email, calendar, and LinkedIn | 2026-06-13 |
| P2 | Add sponsor, advocate, and introduction-path inference to the scheduled graph pass | 2026-06-14 |
| P2 | Route every newsletter through thesis, watchlist, opportunity, and graph comparison | 2026-06-15 |
| P2 | Add end-to-end daily pipeline SLOs, alerts, and seven-day execution history | 2026-06-16 |

## Acceptance Criteria

The defect is closed only when:

1. The 4:00 AM scan and 5:00 AM build share one traceable run lineage.
2. ChatGPT can report whether refresh succeeded, partially succeeded, failed, is
   running, or is queued.
3. Every source and processor has an explicit status and error reason.
4. Delta counts represent the current run only.
5. New uploads are discovered and processed without a separate manual command.
6. Identity, mutation, relationship, opportunity, market, and trust stages are
   all represented in the run receipt.
7. A published brief cannot imply fresh intelligence when required stages failed.
8. The same execution receipt is available in local logs, API output, and the
   daily brief trust surface.

## Immediate Corrections Implemented

- Added durable `pre_brief_scan.json` execution evidence.
- Carried the pre-brief scan into the later brief-build result.
- Added a unified `execution_report` to the morning pipeline.
- Added current-run filtering for LinkedIn and Contacts delta claims.
- Upgraded `refreshSources` to run the full intelligence pipeline by default.
- Added the last execution receipt to refresh preview responses.
- Updated Custom GPT guidance to report the intelligence receipt instead of
  claiming only that source caches refreshed.
