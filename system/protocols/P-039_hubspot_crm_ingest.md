---
id: P-039
title: HubSpot CRM Export Ingest
script: system/scripts/hubspot_ingest.py
reads:
  - system/baseline_index.json
  - system/_snapshots/
inputs:
  - name: hubspot_crm_export_csv
    description: A HubSpot "Export contacts" CSV (e.g. hubspot-crm-exports-all-contacts-<date>.csv).
    required: true
trigger: automatic on HubSpot CRM export CSV upload, gated on dataset_classifier.py confidence >= structured_ingest.confidence_threshold
---

# P-039 — HubSpot CRM Export Ingest

## Purpose

Ingest a HubSpot "Export contacts" CSV and produce: an enhanced (not replaced) `baseline_index.json`,
a mutation report, and a snapshot of the pre-merge baseline for rollback. Proof case for the
generalized structured-dataset ingestion framework in
[RB-DEFECT-064](../../defects/RB-DEFECT-064_no-generalized-structured-dataset-ingestion-framework_2026-07-07.md) —
the same auto-recognition RB already gives LinkedIn exports (`P-002`), applied to a CRM export
without the operator needing to say what the file is.

Per Tenet 6: baseline is foundational. Every ingestion **enhances** baseline, never overwrites
operator-confirmed fields silently.

## When to run

- Operator uploads a HubSpot CRM export CSV **in conversation**. Run `dataset_classifier.py`
  against it first; if `dataset_type == "hubspot_crm_export"` and confidence clears
  `structured_ingest.confidence_threshold` (default 0.85, `settings.json`), run this protocol
  immediately — do not ask what the file is or wait for the operator to say "use this as baseline
  data."
- Below threshold: surface the classifier's best guess and ask for confirmation before ingesting.
- Unattended fallback: a CSV dropped into `system/inbox/crm_exports/` is picked up by
  `hubspot_ingest.py --scan` (wired into the same drop-folder pattern as `contacts_ingest.py`).

## Steps

### 1. Classify

Run `dataset_classifier.classify(path)`. Only proceed automatically if `dataset_type ==
"hubspot_crm_export"` and confidence clears the configured threshold.

### 2. Snapshot the current baseline

Copy `system/baseline_index.json` to
`system/_snapshots/baseline_index.pre-hubspot-ingest-<YYYY-MM-DD>.json`. Rollback point.

### 3. Parse

Read the CSV (HubSpot exports are a plain header row, no preamble, unlike LinkedIn's 3-line
preamble). Map HubSpot's column names (`First Name`, `Last Name`, `Email`, `Phone Number`,
`Company Name`, `Job Title`, `City`, `State/Region`) via `FIELD_ALIASES`.

### 4. Resolve identity against baseline

For each row: exact email match first, then unique-name match. Multiple baseline entries sharing
a name with no email to disambiguate become a **duplicate candidate**, surfaced for operator
confirmation rather than guessed at.

### 5. Mutate matched entries

Enhance in place: add the `hubspot_crm_export_<date>` source tag, backfill missing email/phone,
backfill company/title only if currently null. If the row's company **conflicts** with an existing
non-null `current_company`, do not overwrite — append a dated conflict breadcrumb to `notes` and
list it in the mutation report for the operator to resolve (same guardrail as `P-002` step 6).

### 6. Create new entries

Unmatched rows with at least a name or email become new baseline entries: `signal_class = "VC"`,
all RC fields null, `sources = ["hubspot_crm_export_<date>"]`. Rows with no name and no email are
skipped (nothing actionable).

### 7. Write back and generate the mutation report

Overwrite `baseline_index.json` with the merged data. Write
`system/deltas/hubspot_crm_export_<YYYY-MM-DD>.md` with the mutation report: Knowledge Sources
Updated, People Imported, Existing People Updated, New People Created, Duplicate Candidates,
Companies Added, Knowledge Mutations Applied, Confidence. `Relationship Links Created` is reported
as not computed — graph-stage mutation is RB-DEFECT-064 Phase 2+, not this protocol; the existing
graph/intelligence systems (`micro_graph_builder.py`, `gap_detection.py`, etc.) already read
`baseline_index.json` on their own cadence and will pick up these new/enhanced entries without
this protocol needing to push to them directly.

### 8. Surface the report to the operator

Deliver the mutation report inline, duplicate candidates and conflicts named explicitly as needing
operator confirmation.

## Output contract

Files written:

- `system/_snapshots/baseline_index.pre-hubspot-ingest-<YYYY-MM-DD>.json` — pre-merge snapshot.
- `system/baseline_index.json` — overwritten with merged data.
- `system/deltas/hubspot_crm_export_<YYYY-MM-DD>.md` — mutation report.
- `system/.cache/hubspot_ingest_latest.json` — machine-readable summary.

## Failure modes

- **Classifier confidence below threshold** — do not auto-ingest; surface best guess and ask.
- **CSV missing both `First Name`/`Last Name` and `Email` columns** — classifier will not score
  this as `hubspot_crm_export`; halt before Step 2.
- **Baseline parse failure** — halt before Step 2; surface and ask operator to repair or restore
  from the latest snapshot.

## Deterministic wrapper

`system/scripts/hubspot_ingest.py` is the canonical wrapper: `ingest(path)` for a single file,
`scan()` for the `system/inbox/crm_exports/` drop folder.

## Provenance

Added 2026-07-07 in response to
[RB-DEFECT-064](../../defects/RB-DEFECT-064_no-generalized-structured-dataset-ingestion-framework_2026-07-07.md)
(Phase 1: classifier + one proof-case protocol). Modeled directly on `P-002_linkedin_ingest.md`'s
snapshot/resolve/mutate/report shape, scoped down to identity resolution + baseline mutation only —
no career-move narrative layer, no graph/intelligence stages (those are Phase 2+).
