---
id: P-003
title: Baseline validation
script: system/scripts/validate_baseline.py
cache: system/.cache/validate_baseline.json
reads:
  - system/baseline_index.json
  - system/schemas/baseline.schema.json
writes: []
trigger: before any procedure that writes to baseline_index.json
---

# P-003 — Baseline validation

## Purpose

Validate `system/baseline_index.json` against the canonical schema and run a small set of integrity checks the schema can't express cleanly.

## When to run

- Before any procedure that writes back to `baseline_index.json` (P-002 ingest, manual RC promotions, etc.).
- After a hand edit of `baseline_index.json`.
- As part of the daily brief, when the brief mutated baseline (rare).
- Any time downstream scripts return surprising counts.

## Read set

1. `system/baseline_index.json`
2. `system/schemas/baseline.schema.json`
3. `system/scripts/rb_core.py`

## Inputs

(none)

## Steps

1. Run `python3 system/scripts/validate_baseline.py`.
2. If exit code is `0`, the baseline is valid and free of the known integrity hazards (duplicate IDs, future `last_touch`, RC `ACTIVE` without a canonical tier).
3. If exit code is `1`, the baseline failed validation. Surface the full output to the operator. Do not proceed with any procedure that writes to baseline.

## Output contract

- Exit code `0` ⇒ valid.
- Exit code `1` ⇒ failed; details on stderr / stdout.

For machine consumption: `python3 system/scripts/validate_baseline.py --json` emits a structured report.

## Failure modes

- **`jsonschema` not installed.** Install with `pip install jsonschema --break-system-packages`.
- **Schema file missing.** Restore from git.
- **Integrity issue.** Either fix the bad data, or, if the integrity check itself is wrong, update `integrity_checks` in `validate_baseline.py` first.

## Voice

This is a gate. No editorial output. If the gate passes, say so in one line. If it fails, dump the report.
