# Claude defect handoff: deep-research capture receipts falsely imply zero mutations

Date: 2026-09-30
Reporter: Codex investigation at Todd Vahlsing's request
Severity: High observability / trust defect; canonical writes are occurring but the user-facing receipt reports zero

## Executive summary

Six deep-research packets processed in the 2026-09-30 morning intelligence cycle show `exec_mutations: 0` in their capture `processing_result`. That value does **not** mean the packets produced no canonical mutations.

The structured JSON sidecars were handled later by `competitor_platform_research_import`, which applied the packet findings to canonical competitor records. For the six packets investigated, the sidecars contain approximately 126 structured findings in total and the pipeline/import evidence shows the findings were applied. The capture receipt is therefore materially misleading because it reports only the count of `executive_declaration` mutations and is never reconciled with the downstream structured importer.

This is a trust problem: a reasonable operator reading `exec_mutations: 0` concludes that research was classified but did not update RB. That conclusion is false.

## Affected packets

1. `dr-20260929-1605-alchemer-validation` — 6 structured findings
2. `dr-20260929-1906-all-gravy-validation` — 4 structured findings
3. `dr-20260929-2206-axial-shift-validation` — 4 structured findings
4. `dr-20260929-1623-competitor-platforms` — 53 structured findings
5. `dr-20260929-1924-competitor-platforms` — 55 structured findings
6. `dr-20260930-0708-bixolon-validation` — 4 structured findings

Total structured findings represented: 126.

## Confirmed evidence

### Capture receipts

`getCapturesProcessed` returns each packet with `persisted_count > 0` but `exec_mutations: 0`.

Examples:

- Alchemer: `persisted_count: 4`, `exec_mutations: 0`
- All Gravy: `persisted_count: 4`, `exec_mutations: 0`
- Axial Shift: `persisted_count: 8`, `exec_mutations: 0`
- 16:23 competitor platforms: `persisted_count: 8`, `exec_mutations: 0`
- 19:24 competitor platforms: `persisted_count: 6`, `exec_mutations: 0`
- BIXOLON: `persisted_count: 5`, `exec_mutations: 0`

### Code path that explains the zero

In `system/api/server.py`, both `submitCapture` and scheduled capture processing build `exec_mutations` exclusively by iterating triage streams whose `intelligence_type == "executive_declaration"`.

That counter does not inspect or count canonical writes made by the deep-research sidecar importer.

### Separate importer that actually mutates canonical records

`system/scripts/morning_pipeline.py` runs these steps in sequence:

1. `capture_ingest_scan`
2. `capture_process_all`
3. `deep_research_sidecar_sweep`
4. `competitor_platform_research_import --sweep --confirm`

The capture is marked processed in step 2, before the structured import in step 4. No later step updates the capture's `processing_result`.

The 2026-09-30 pipeline receipt confirms:

- `competitor_platform_research_import` passed.
- BIXOLON: 4 received, 4 applied, 0 queued, 0 unresolved.
- Axial Shift: 4 received, 4 applied, 0 queued, 0 unresolved.
- The other affected competitor profiles contain the sidecar-sourced findings and URLs.

Canonical evidence is visible in records including:

- `system/competitor_intelligence/competitors/alchemer/competitor.json`
- `system/competitor_intelligence/competitors/all-gravy/competitor.json`
- `system/competitor_intelligence/competitors/axial-shift/competitor.json`
- `system/competitor_intelligence/competitors/bixolon/competitor.json`
- the 13 competitor profiles covered by the two broader platform packets

## Root cause

There are two independent processing surfaces with no receipt reconciliation:

1. The Markdown capture is sent through generic `intelligence_triage`, which persists broad triage streams to IntelligenceDB. Its `exec_mutations` field is narrowly an executive-declaration counter.
2. The JSON sidecar is sent through `import_competitor_platform_research.py`, which performs the actual structured competitor mutations.

The capture API and brief expose the first receipt as if it were the complete outcome. The second receipt is retained only in pipeline output/import state and is not attached to the capture.

## Secondary quality defect exposed by this investigation

Generic capture triage is producing obvious false positives on structured deep-research packets, including:

- relationship-intelligence subjects such as `Named`, `Candidate`, `Material`, and `Introduced`;
- repeated Oracle Hospitality, NCR Voyix, McDonald's, and Global Payments graph proposals caused by generic packet vocabulary;
- macro classifications that are much less useful than the structured sidecar findings.

These records are being persisted to IntelligenceDB even though the packet already has a higher-quality structured schema. Deep-research packets should prefer the structured importer and should not generate noisy generic RI/entity records from template language.

## Required behavior

For a deep-research packet, one authoritative receipt should report the complete outcome across capture and structured import:

- packet ID and capture ID;
- triage records persisted;
- structured findings received;
- canonical findings applied;
- deduplicated;
- queued for review;
- rejected/malformed;
- unresolved identities;
- exact canonical targets changed;
- explicit statement when no canonical mutation occurred.

`exec_mutations` must either be renamed to `executive_declaration_mutations` or replaced by an unambiguous set of counters. It must not be presented as a total mutation count.

## Suggested implementation boundary

1. Have `competitor_platform_research_import` emit and persist a packet-keyed durable receipt, not only finding hashes.
2. After the importer runs, reconcile the packet receipt into the matching processed capture record using packet ID/source file.
3. Add fields such as:
   - `executive_declaration_mutations`
   - `structured_import_applied`
   - `structured_import_deduped`
   - `structured_import_queued`
   - `structured_import_rejected`
   - `canonical_targets_changed`
4. Update `getCapturesProcessed`, Intelligence Brief rendering, and Daily Brief rendering to use the unified receipt.
5. For `capture_type == "deep_research"` with a valid structured sidecar, suppress or constrain generic relationship/entity classifiers unless the sidecar explicitly contains those finding types.

## Acceptance tests

1. A deep-research Markdown + JSON pair with four applicable findings finishes with a processed-capture receipt showing `structured_import_applied: 4`, not a misleading total of zero.
2. The receipt lists the exact competitor target changed.
3. A dry-run or review-required finding reports `queued`, not `applied`.
4. A duplicate reports `deduped` and does not inflate applied count.
5. A malformed finding reports `rejected` with its error.
6. Generic deep-research template words such as `Named`, `Candidate`, and `Material` do not become RI subjects.
7. Existing meeting-capture executive-declaration behavior remains unchanged.
8. The morning brief and `getCapturesProcessed` show the same unified mutation outcome.

## Important correction to the initial incident interpretation

The initial observation was “six packets processed with zero mutations.” The investigation proves the more precise defect is “six packets produced canonical mutations, but the capture receipts falsely reported zero because they measured the wrong mutation class and omitted the downstream importer.”
