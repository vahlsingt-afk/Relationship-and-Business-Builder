---
id: P-002
title: LinkedIn Data Export Ingest
script: system/scripts/linkedin_ingest.py
reads:
  - system/baseline_index.json
  - system/_snapshots/
  - system/schemas/baseline.schema.json
writes:
  - system/baseline_index.json
  - system/_snapshots/baseline_index.<date>.json
  - system/deltas/linkedin_export_<date>.md
inputs:
  - name: linkedin_export_zip
    description: A Complete_LinkedInDataExport_*.zip from LinkedIn.
    required: true
trigger: automatic on LinkedIn export ZIP upload or operator-initiated on a new LinkedIn export
---

# P-002 — LinkedIn Data Export Ingest

## Purpose

Ingest a fresh LinkedIn `Complete_LinkedInDataExport_*.zip` and produce: an updated `baseline_index.json` (enhanced, not replaced), a delta report, and a snapshot of the pre-merge baseline for rollback.

Per Tenet 6: baseline is foundational. Every LinkedIn ingestion **enhances** baseline, preserving every system-set field (`signal_class`, `rc_state`, `rc_tier`, `last_touch`, `circles`, `tags`, `notes`).

## Mutation policy

- Explicit facts present in the LinkedIn export auto-apply: new connection identity, LinkedIn URL, supplied email, connection date, current company, and current title, subject to the conflict protections below.
- Derived scores, inferred relationship strength, inferred tiers, recommended outreach, and other interpretations are advisory only and never mutate canonical contact state.
- A contact absent from a later export is never deleted or demoted automatically. RB may add a reversible `linkedin_disconnected_<date>` reconciliation tag and history breadcrumb while retaining the contact record.

## When to run

- Operator uploads a LinkedIn data export `.zip` file **in conversation**. This is a
  known operational ingestion artifact and should be auto-routed without asking what
  to do with it — run the steps below against the uploaded file immediately, in this
  session. Do not defer it to the nightly pipeline.
- Operator says "ingest the LinkedIn export."
- Unattended fallback (RB-DEFECT-007): if a LinkedIn export ZIP is saved into
  `system/inbox/linkedin_exports/`, the 5 AM `morning_pipeline.py` run invokes
  `linkedin_export_watcher.py --ingest-new --confirm`, which classifies and ingests it
  automatically. This path exists for files dropped directly into the inbox folder —
  it does **not** apply to files only attached in a chat session, which must be
  ingested via the first bullet above.

## Read set

1. `system/settings.json`
2. selected user profile (`system/profiles/{profile_id}/profile.md`, or `system/00_TODD_PROFILE.md` during legacy single-user mode)
3. `system/01_RB_TENETS.md` (Tenet 6 in particular)
4. `system/ARCHITECTURE.md` (file ingestion section)
5. `system/SCHEMAS.md`
6. `system/baseline_index.json`

Do NOT load `cards/`, `briefs/`, `circles/`, or `today.md` unless a step explicitly references them.

## Inputs

- **Path to the LinkedIn export `.zip`** (operator-supplied).
- **Today's date** (system supplies).

## Steps

### 1. Acknowledge the file

Per Tenet 3: every file uploaded must be acknowledged. State the filename and that ingestion is starting.

### 2. Snapshot the current baseline

Copy `system/baseline_index.json` to `system/_snapshots/baseline_index.<YYYY-MM-DD>.json`. If that path already exists, append `-<suffix>` to disambiguate. The snapshot is the rollback point.

### 3. Extract the archive

Extract the `.zip` to a working location. Locate `Connections.csv`.

The LinkedIn CSV has a 3-line preamble. The actual header row begins with `First Name,Last Name,URL,Email Address,Company,Position,Connected On`. Parse from the header row onward.

### 4. Build matchers against current baseline

For each entry in `baseline_index.json`, build three lookups:

- `by_url_slug` — the last path segment of `linkedin_url`, lowercased.
- `by_name` — `(name).lower()` mapped to the entry (list, since names can repeat).
- `by_email` — `(email).lower()` mapped to the entry.

### 5. Match each new export row

For each row in the export:

1. Try URL slug match.
2. If no URL match, try unique-name match (only if exactly one baseline entry has that name).
3. If no name match, try email match.
4. If still no match → this is a **new** connection (Step 8).

When a match is found, that baseline entry's ID is added to `matched_ids`.

### 6. Detect changes on matched entries

For each matched entry, compare the export row against current baseline fields:

- **Source tag.** Add `linkedin_export_<YYYY-MM-DD>` to `sources` if not present.
- **`linkedin_connected_on`** — overwrite with the export value if different (this is LinkedIn-canonical).
- **Reconnect.** If the entry has any `linkedin_disconnected_*` tag, remove it; add a reconnect breadcrumb to `notes`.
- **Company change.** If export company ≠ current company (case-insensitive). Apply only if:
  - The change is NOT a known rebrand (operator-confirmed rebrands live in `heuristics.md` or card notes — check before overwriting).
  - The change is NOT a stale-LinkedIn case the operator has already corrected (look for "CONFIRMED by Todd" or "Conflict resolved" breadcrumbs in `notes`).
  
  If apply: overwrite `current_company`, append a `[YYYY-MM-DD] LinkedIn: company X → Y.` breadcrumb to History (per SCHEMAS.md notes compaction convention).
  
  If don't apply: do NOT overwrite. Append a `[YYYY-MM-DD] CONFLICT: LinkedIn reads X; canonical retained as Y. Confirm.` breadcrumb. The operator resolves later.
- **Role change.** Same logic. Additionally: filter out formatting-only changes (typo fixes, case normalization, LinkedIn-appended company-in-title patterns like `"Founder & CEO" → "Founder & CEO @ <company>"`). These should not produce breadcrumbs.
- **Email backfill.** If export has an email and baseline `email` is null, fill it. Never overwrite an existing email.

### 7. Detect disconnections

For each baseline entry that:

- Has a `linkedin_url` or a LinkedIn-derived `source`, AND
- Was NOT in `matched_ids` after Step 5, AND
- Is NOT already tagged `non_linkedin_contact`, AND
- Does NOT already have a `linkedin_disconnected_*` tag:

Append the tag `linkedin_disconnected_<YYYY-MM-DD>` to `tags`. Append a `[YYYY-MM-DD] LinkedIn: connection lost (not present in <export date> export).` breadcrumb to notes. Do NOT change `signal_class` — losing a LinkedIn edge does not reset earned trust.

### 8. Add new connections as VC

For each export row that didn't match anything in baseline:

- **Pre-filter — skip rows with no usable identifying data.** If the row has all of (no name, no URL, no email), it is a LinkedIn-redacted entry (privacy settings, banned account, etc.). **Do NOT create a baseline entry for it.** Track the count for the delta report ("N rows skipped due to LinkedIn redaction"). Skipping is correct behavior — these rows have no actionable signal.
- For non-skipped rows, generate a stable ID using `firstname-lastname` lowercased, hyphenated, with a numeric suffix only if needed to disambiguate. If first name is blank but last name + URL exist, use last-name-from-url. Never produce IDs starting with `unknown`.
- Create the new entry with `signal_class = "VC"`, all RC-related fields null, `sources = ["linkedin_export_<YYYY-MM-DD>"]`, `linkedin_connected_on` populated, `circles = []`, `tags = []`, `notes = ""`.
- Append to baseline.

### 9. Validate the merged baseline

Before writing back, run the schema validator:

```bash
python3 system/schemas/validate.py
```

The validator checks: every entry has an `id`, `name`, `signal_class` ∈ {VC, NPR, LMI, LKI, RC}; no duplicate IDs (TODO: add to schema); RCs have non-null `rc_state` and `rc_tier` of valid values; non-RCs have null `rc_state` and `rc_tier`; `last_touch` is ISO date format or null; IDs match the lowercase-hyphenated pattern.

If validation fails, do NOT write. Surface the specific failures (file path + error message) to the operator. The operator either fixes the source data or repairs the merged data before re-running validation.

### 10. Write back the merged baseline

Overwrite `system/baseline_index.json` with the merged data. The pre-merge snapshot from Step 2 is the rollback.

### 11. Generate delta report

Write `system/deltas/linkedin_export_<YYYY-MM-DD>.md` with:

- Headline counts: new, disconnections, reconnections, company changes, role changes.
- **RC-level moves** — every company / role change to an RC tier entry, separately called out with operator interpretation.
- **LKI-level moves** — list, lighter interpretation.
- **Disconnections** — table.
- **Reconnections** — table.
- **New connections** — top role distribution + 5–10 named highlights.
- **Open questions / conflicts surfaced** — anything Step 6 deferred.
- **What the system did NOT do** — list of guardrails respected (didn't promote, didn't auto-Circle, didn't overwrite operator corrections).

Voice: direct, Midwestern. Three-part shape (Report → Interpret → Recommend) per ARCHITECTURE.md.

### 12. Surface the report to the operator

In chat, deliver the Report-Interpret-Recommend summary with the high-stakes RC moves highlighted. Include explicit "open questions" needing operator decision.

## Output contract

Files written:

- `system/_snapshots/baseline_index.<YYYY-MM-DD>.json` — pre-merge snapshot.
- `system/baseline_index.json` — overwritten with merged data.
- `system/deltas/linkedin_export_<YYYY-MM-DD>.md` — new delta report.

Operator chat output: Report-Interpret-Recommend summary with the highest-stakes findings explicitly named.

## Failure modes

- **`.zip` malformed or missing `Connections.csv`** — halt, do not snapshot, surface the error.
- **Baseline parse failure** — halt before Step 4; surface and ask operator to repair or restore from latest snapshot.
- **Schema validation fails at Step 9** — restore baseline from the Step-2 snapshot, surface the specific failure, ask operator.
- **Operator-confirmed-rebrand check missing** — if the export shows company changes for known-rebrand cases that aren't yet recorded as heuristics, surface as part of the delta report and propose adding to `heuristics.md`.

## Voice

Operator-facing output: three-part Report → Interpret → Recommend. Direct, peer-level, no fluff. The delta report file is comprehensive; the chat summary is the read for action.

## Deterministic wrapper

`system/scripts/linkedin_ingest.py` is the canonical wrapper for this protocol.
It classifies ZIP structure, parses `Connections.csv`, counts companion LinkedIn
activity files, computes baseline deltas, blocks undersized partial archives
from destructive disconnection writes, writes the delta report, and returns the
same CoS-level RI enhancement summary to API callers.

API route:

- `POST /artifacts/classify` detects `artifact_type=linkedin_export_zip`.
- `POST /linkedin/ingest` runs this protocol and returns delta evidence,
  relationship movement detection, opportunity detection, recommended actions,
  and network visualization data.

## Provenance

Lifted from the inline procedure run on the 2026-05-10 export, codified 2026-05-14. If `ARCHITECTURE.md → "File ingestion"` updates, refresh this protocol.
