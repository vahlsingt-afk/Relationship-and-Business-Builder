---
id: P-004
title: RC, card, and contact-field gap detection
script: system/scripts/gap_detection.py
cache: system/.cache/gap_detection.json
reads:
  - system/baseline_index.json
  - system/cards/
writes: []
trigger: inside daily brief; after RC promotion; quarterly network review
---

# P-004 — RC, card, and contact-field gap detection

## Purpose

Surface the four kinds of structural gap the daily brief should never silently overlook: RCs without a card, orphan card files, RCs without `last_touch`, and inner/broader RCs missing email or phone.

## When to run

- Inside the daily brief (already wired in via `daily_brief.py`).
- After a new RC is promoted (an inner/broader without a card is a defect).
- Before a quarterly review pass on the network.

## Read set

1. `system/baseline_index.json`
2. `system/cards/*.md` (filename → id mapping only)
3. `system/scripts/rb_core.py`

## Inputs

(none — pure derivation from baseline + cards directory)

## Steps

1. Run `python3 system/scripts/gap_detection.py` (use `--json` for downstream consumers, `--strict` to make missing data an error).
2. For each `RCs without a card file` entry, queue a card-creation task. Use `system/cards/_TEMPLATE.md` as the starting point.
3. For each `RCs without last_touch` entry, ask the operator the last time they actually touched the person — even an approximate month closes the gap.
4. For each `Contact-field gap` row, decide whether to harvest contact data from existing IBs (`system/briefs/<id>__*.md`) or escalate to the operator.

## Output contract

Text or JSON gap report. No files written by this protocol; the operator decides which gaps to act on.

## Failure modes

- **Card filename ≠ RC id.** The matcher uses the file stem. Rename mismatched files (the card stem is the contract).
- **Orphan cards.** The protocol surfaces these but does not delete them — they may be archived people whose RC entry was demoted.

## Voice

Direct, structural. This is a quality-control protocol; no editorial framing.
