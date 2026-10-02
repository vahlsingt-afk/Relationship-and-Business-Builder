---
id: P-007
title: Dynamic Relationship Relevance scoring (prototype)
script: system/scripts/drr_score.py
cache: system/.cache/drr_score.json
reads:
  - system/baseline_index.json
writes: []
inputs:
  - name: date
    description: ISO date to score against.
    required: false
  - name: class
    description: Filter by signal_class (VC, NPR, LMI, LKI, RC).
    required: false
  - name: id
    description: Explain a single entry's score by id.
    required: false
trigger: on-demand; before any procedure that needs a comparable ranking
---

# P-007 — Dynamic Relationship Relevance scoring (prototype)

## Purpose

Produce a single comparable 0–100 score per contact so the system can rank attention across an entire 2,670-entry baseline without having to recompute heuristics every time.

This is a **prototype**. The full spec, including evaluation methodology, lives in `RB_DRR_Specification_and_Evaluation.docx`.

## When to run

- When the operator asks "who should I be paying attention to right now?"
- As input to any other procedure that needs a ranking (e.g., the intro engine selecting among multiple brokers).
- Before adjusting tier thresholds or weights, to baseline the current state.

## Read set

1. `system/baseline_index.json`
2. `system/scripts/rb_core.py` (the scoring function lives in `drr_score()`)

## Inputs

- `--date YYYY-MM-DD` (default: system date).
- `--class {VC, NPR, LMI, LKI, RC}` to filter by signal class.
- `--id <entry-id>` to explain a single contact's score component-by-component.
- `--limit N` (default 50).

## Steps

1. Run `python3 system/scripts/drr_score.py` for an overview, or with `--class RC` for the priority view.
2. To debug a single contact's score: `python3 system/scripts/drr_score.py --id <id>` — this emits the five-component breakdown.
3. If the score "feels wrong" against operator intuition, the weights or the component definitions need work. **Do not silently tune.** Open a note in `loop_ledger.md`, run the eval pass described in the spec doc, and revise `rb_core.drr_score`.

## Output contract

For each scored entry: `id`, `name`, `signal_class`, `rc_tier`, `score` (0–100), and a `components` dict with the five component values (each 0–1).

## Score interpretation (2026-05-15 baseline)

| Score band | Rough meaning |
|---|---|
| 80+ | Inner-tier RC, touched recently, multiple Circles, complete record. |
| 60–80 | Inner RC mid-cooling, or a top LKI with strong evidence breadth. |
| 50–60 | Active LKI; broader-tier RCs cooling. |
| 40–50 | LMI with some evidence; promotion candidates if cluster has anchor gap. |
| <40 | Cold/structural noise. |

Top-10 cutoff today: 78.8. Top-50 cutoff: 54.8. Top-100 cutoff: 49.3.

## Failure modes

- **Recency saturates at 365d.** Anyone touched more than a year ago scores 0 on recency regardless of how long ago. The dormant_valuable tier compensates via the tier weight, but cold high-value contacts get under-ranked. This is a known v0 limitation.
- **`circles` is undercounted for LMIs.** Most LMIs have no `circles` array entry; the prototype treats that as zero. Once `target-*` Circles get target tables, this changes.
- **Weights are guesses.** The 30/25/15/15/15 split is a starting point; the eval doc spells out how to tune.

## Voice

Numeric. If you read a row out loud, do it with the components attached — the score on its own is opaque.
