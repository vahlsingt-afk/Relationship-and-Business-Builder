---
id: P-005
title: Loop ledger parsing
script: system/scripts/loop_parser.py
cache: system/.cache/loop_parser.json
reads:
  - system/loop_ledger.md
writes: []
inputs:
  - name: date
    description: ISO date for 'today' comparison.
    required: false
trigger: inside daily brief; meeting prep; any session reasoning about open obligations
---

# P-005 — Loop ledger parsing

## Purpose

Convert the markdown table in `system/loop_ledger.md` into structured records, bucketed by their target date relative to today. This is the input to the daily brief's "Loops" section and to any model session that needs to reason about open obligations.

## When to run

- Inside the daily brief (already wired in).
- Any time a session needs to ask "what's due today / this week / past target."
- Before a meeting prep — to surface open loops for any named party.

## Read set

1. `system/loop_ledger.md`
2. `system/scripts/rb_core.py`

## Inputs

- `--date YYYY-MM-DD` to override the comparison date (default: system date).

## Steps

1. Run `python3 system/scripts/loop_parser.py --date YYYY-MM-DD`.
2. The script returns five buckets: `overdue`, `due_today`, `this_week` (≤7 days), `future`, `closed`.
3. For the daily brief, emit `overdue` first (loud), `due_today` next, then `this_week` as a table.
4. For meeting prep, filter the output by `party` substring match against the meeting's named contact(s).

## Output contract

Five buckets keyed by status. Each loop record carries `id`, `opened`, `party`, `description`, `target`, `closed`, `status_raw`.

`--json` emits the same shape as machine-readable JSON.

## Failure modes

- **Row doesn't match the regex.** A non-canonical row (e.g., a hand-edit that broke the column count) is silently skipped. Surface this by counting input lines vs. parsed loops if they disagree by more than the expected 2 header rows.
- **Loops marked closed without a reason.** Allowed by the parser but discouraged by convention — reasons go in the Status column.

## Voice

Structured. No commentary on what the operator "should" do — that's a session-level judgment.
