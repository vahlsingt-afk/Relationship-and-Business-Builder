---
id: P-022
title: Legacy RB thread transfer ingestion
script: system/scripts/legacy_transfer_watch.py (watch only; semantic ingestion planned)
cache: system/.cache/legacy_transfer_watch.json
reads:
  - RB 8.0 transfer data/
  - system/ARCHITECTURE.md
  - system/01_RB_TENETS.md
  - system/SCHEMAS.md
  - system/OPERATIONALIZATION.md
  - system/protocols/
writes:
  - system/.cache/legacy_transfer_watch.json
  - system/_sessions/*.md
  - system/protocols/
  - system/OPERATIONALIZATION.md
  - system/architecture_decisions/ (planned)
  - migration reports
inputs:
  - name: source_dir
    description: Folder of exported RB 8.0 / RB 9.0 transfer documents and prior thread summaries.
    required: true
trigger: on-demand when Todd drops legacy RB thread exports into the workspace
---

# P-022 — Legacy RB Thread Transfer Ingestion

## Purpose

Mine prior RB 8.0/RB 9.0 thread exports for durable architecture, governance, persistence, and product decisions without polluting current RB 9.0 canonical state.

## Principle

Legacy transfer documents are historical source artifacts. They are not automatically canonical.

RB should read them as evidence of prior thinking, extract candidate principles and decisions, then reconcile those against current RB 9.0 files.

## Source location

Current local source folder:

```text
RB 8.0 transfer data/
```

This folder is git-ignored by default. It may contain exported conversations, Word documents, summaries, and prior governance material. Do not commit raw transfer files unless Todd explicitly asks.

## What to extract

Extract and classify:

- durable architecture principles
- persistence model decisions
- governance/admissibility rules
- anti-patterns and historical failure modes
- deprecated assumptions
- cross-model role split
- retry/self-healing requirements
- canonical execution rules
- product features not yet represented in RB 9.0
- conflicts with current RB 9.0 architecture

## What not to do

- Do not overwrite current architecture from legacy docs.
- Do not duplicate rules already present in current files.
- Do not promote old governance language if RB 9.0 has evolved past it.
- Do not treat an RB 8.0 statement as current truth without reconciliation.
- Do not ingest conversational residue as relationship intelligence unless it contains specific people/events that belong in the current RI event stream.

## Review workflow

1. Run `system/scripts/legacy_transfer_watch.py --cache` to inventory files and identify new/changed/removed artifacts since the last watch pass.
2. Extract text from `.docx`, `.pdf`, `.txt`, `.md`, and related formats.
3. Produce a transfer matrix:
   - source file
   - extracted theme
   - current RB 9.0 file it maps to
   - status: already represented / missing / conflicting / deprecated
   - recommended action
4. Apply only approved updates to canonical docs.
5. Write a session memory entry summarizing what was incorporated and what was rejected.

## Output contract

The operator-facing output should include:

- new/changed/removed files since last watch pass
- count of files processed
- major themes found
- what was already represented
- what was missing and added
- what was intentionally rejected
- recommended next implementation work

The canonical output should be small. Legacy transfer processing should improve RB 9.0 clarity, not bloat it.

## Monitoring status

`legacy_transfer_watch.py` runs inside `refresh_all.py` and writes a non-canonical delta report to `system/.cache/legacy_transfer_watch.json`.

The watch report answers:

- how many transfer artifacts exist
- which files were added since the last refresh
- which files changed since the last refresh
- which files were removed since the last refresh
- whether review is needed

This does not extract, summarize, or promote legacy material into RB state. It only keeps RB aware that new source artifacts are waiting for reconciliation.

## Current observation

The first inspected transfer document emphasized:

- persistence outside AI session memory
- canonical repositories and manifests
- proof-based execution completion
- retry/self-healing behavior
- cross-model role separation
- rejection of monolithic prompts
- governance modularization
- AI-provider agnostic architecture

Much of this is already represented in current RB 9.0 via `OPERATIONALIZATION.md`, P-021 event sourcing, API/MCP validation, and the ChatGPT/Codex/Claude role split. Future transfer ingestion should focus on deltas, not repetition.
