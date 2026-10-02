---
id: P-033
title: LinkedIn Messaging Export Ingest
script: system/scripts/linkedin_messaging.py
cache: system/inbox/linkedin.messages.json
reads:
  - system/inbox/linkedin_messages_export.csv (operator-dropped)
  - system/baseline_index.json
writes:
  - system/inbox/linkedin.messages.json
trigger: on-demand after each LinkedIn export download; suggest monthly
---

# P-033 - LinkedIn Messaging Export Ingest

## Purpose

Turn a real LinkedIn `messages.csv` export into the normalized
`system/inbox/linkedin.messages.json` cache used by the Daily Prep Summary,
relationship signals, and last-touch review flow.

## Export And Ingest Flow

1. In LinkedIn, open Settings -> Data Privacy -> Get a copy of your data.
2. Request only Messages. Do not request the full archive unless needed.
3. Download the ZIP when LinkedIn emails it, then extract `messages.csv`.
4. Place the file at `system/inbox/linkedin_messages_export.csv`.
5. Preview the ingest first (default — no writes):

```bash
python3 system/scripts/linkedin_messaging.py --ingest system/inbox/linkedin_messages_export.csv
```

This runs in preview/dry-run mode by default. It prints total rows, matched contacts,
unmatched recurring participants, last-touch proposals, and outbound evidence rows,
but does **not** write `system/inbox/linkedin.messages.json`.

6. When the preview looks correct, confirm the write:

```bash
python3 system/scripts/linkedin_messaging.py --ingest system/inbox/linkedin_messages_export.csv --confirm
```

The `--confirm` flag writes `system/inbox/linkedin.messages.json` (after snapshotting
the prior file if one exists).

**Operator contract:** `--ingest` always previews unless `--confirm` is also passed.
This prevents accidental overwrites of the inbox cache from unreviewed exports.

## Match Logic

The parser accepts common LinkedIn export header variants and normalizes each
row into a message with sender, recipients, date, folder, direction, and content.
It matches participants to `system/baseline_index.json` by LinkedIn URL slug
first, then by exact lowercased name.

Direction is outbound when the sender matches Todd's LinkedIn URL or the export
folder is `SENT`; otherwise it is inbound.

## Review Before Applying

Ingest never mutates `baseline_index.json`. Review the overlay first:

```bash
python3 system/scripts/linkedin_messaging.py --overlay
```

The `proposed_last_touch_updates` rows are operator-review material. Apply
last-touch changes only through a separate confirmed mutation flow.

## Failure Modes

- Encoding or malformed CSV: ingest exits non-zero and leaves the prior inbox
  cache untouched.
- Wrong file: summary shows zero rows or zero usable dates; download the LinkedIn
  Messages export again.
- No contact matches: enrich baseline LinkedIn URLs or names, then re-run ingest.
- No outbound evidence: expected when the export window contains inbound-only
  traffic or Todd's LinkedIn URL is not configured.
