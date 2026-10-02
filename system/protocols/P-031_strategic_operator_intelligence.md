---
id: P-031
title: Strategic Operator Intelligence
script: system/scripts/strategic_operators.py
cache: system/.cache/strategic_operators.json
reads:
  - system/strategic_operators.yaml
  - system/schemas/strategic_operators.schema.json
  - system/scoring/strategic_operator_rubric.md
  - system/inbox/market_signals.json
  - system/baseline_index.json
  - system/active_threads.yaml
  - system/settings.json
writes:
  - system/.cache/strategic_operators.json
inputs:
  - name: recent_days
    description: Movement look-back window. Default 90.
    required: false
trigger: Every daily-brief run, every full cache refresh, and after any operator-add/operator-update/operator-record-movement/operator-close mutation.
---

# P-031 — Strategic Operator Intelligence

## Purpose

Maintain a persistent, queryable, relationship-aware intelligence layer for
large franchise operators, multi-brand operators, PE-backed operators,
operator holding companies, large regional operators, and high-influence
franchisee groups.

This is a Chief-of-Staff intelligence function. It is not a news-summary
feature.

## What This Is Not

- Not a second market-signals renderer.
- Not a restaurant industry newsletter.
- Not numeric scoring without calibration.
- Not disposable markdown in the daily brief.
- Not a replacement for relationship intelligence.

`market_signals.py` remains the ephemeral news/source lane.
`strategic_operators.yaml` is the persistent entity lane.

## Read Set

Read only:

- `system/strategic_operators.yaml`
- `system/schemas/strategic_operators.schema.json`
- `system/scoring/strategic_operator_rubric.md`
- `system/inbox/market_signals.json`
- `system/baseline_index.json`
- `system/active_threads.yaml`
- `system/settings.json`

## Canonical State

The canonical state file is `system/strategic_operators.yaml`.

Each operator record carries:

- `id`
- `name`
- `entity_type`
- `watchlist_bucket`
- `companies_owned`
- `brands_in_portfolio`
- `executives`
- `vendor_relationships`
- `relationship_proximity`
- `status`
- `opened`
- `last_movement_at`
- `movements`
- `notes`

Do not write operator state by hand during runtime. Programmatic writes must
go through `system/scripts/mutations.py` so they inherit the P-009
snapshot-then-validate-then-rollback discipline.

## Mutation Contract

Supported mutation subcommands:

- `operator-add`
- `operator-update`
- `operator-record-movement`
- `operator-close`

HTTP/GPT surface:

- `POST /strategic_operators/apply`
- GPT action name: `applyOperatorMutation`

All write paths must support dry-run / preview mode and require explicit
confirmation before mutating canonical state.

## Compute Contract

Run:

```bash
python3 system/scripts/strategic_operators.py --json --cache
```

The overlay must produce:

- `operator_count`
- `active_count`
- `recent_movement_count`
- `market_signal_match_count`
- `top`
- `reconciliation_prompts`
- `rubric`

For each row in `top`, include:

- entity identity and watchlist bucket
- relationship proximity, asserted and resolved
- proximity evidence
- mutual connections
- vendor relationships
- recent movements
- market signal feeder matches
- categorical scores
- recommended action
- source refs

## Scoring Discipline

Use categorical buckets only:

- `low`
- `medium`
- `high`
- `critical`

Dimensions:

- influence
- relationship value
- operational pressure
- ecosystem impact
- future opportunity
- follow-up priority

The rubric lives in `system/scoring/strategic_operator_rubric.md`.

Do not promote continuous numeric fields as primary state until RB has an
evaluation set or other ground truth for calibration. Derived ordering is
allowed for ranking display; canonical meaning stays categorical.

## Daily Brief Contract

`daily_brief.py` embeds the overlay under:

- raw report key: `strategic_operators`
- canonical section: `strategic_operator_movements`

Render `strategic_operator_movements` only for:

- persisted recent movements, or
- source-backed market feeder matches tied to a known operator record.

Do not turn proximity-only watchlist rows into daily action items unless they
also have a recent movement, direct relationship, active thread, or stronger
evidence. Proximity-only rows may produce reconciliation prompts.

Keep market/vendor/macro items in `condensed_industry_context`, rendered as
Market and restaurant-tech context.

## Relationship Proximity

Resolve proximity deterministically:

- `direct`: an operator executive/contact ID exists in baseline and is
  RC/LKI/LMI.
- `one_hop`: portfolio-company or company-text overlap with RC/LKI/LMI.
  Treat as candidate proximity until confirmed.
- `two_hop`: an active thread mentions the operator or portfolio.
- `none`: no known path.
- `unknown`: not enough data.

When asserted proximity in YAML disagrees with the resolved overlay, emit a
reconciliation prompt. Do not silently overwrite YAML.

## Validation

Before considering this protocol green, run:

```bash
python3 system/scripts/strategic_operators.py --smoke
python3 system/schemas/validate.py --strategic-operators-only
python3 system/scripts/daily_brief.py --smoke
python3 system/scripts/refresh_all.py --date YYYY-MM-DD
python3 system/scripts/validate_openapi_gpt.py
```

Expected:

- strategic operator smoke: 0 failures
- strategic operator schema: valid
- daily brief smoke: 0 failures
- refresh_all includes `strategic_operators.py`
- GPT OpenAPI spec stays at or below the 30-operation cap

## Failure Modes

- **Market signals mention an operator but no persistent record exists**:
  keep the item in market context and recommend `operator-add` only if the
  entity should enter a durable watchlist.
- **Resolved proximity feels too broad**:
  emit a reconciliation prompt; do not mark it firm without operator review.
- **Schema validation fails after a mutation**:
  restore the snapshot and report the validation error.
- **Daily brief has no operator movements**:
  render a short health line, not invented movement commentary.
- **GPT cap is full**:
  keep `applyOperatorMutation`; remove a lower-leverage read-only endpoint.

## Voice

Use operator-savvy CoS language:

- who is becoming strategically important;
- why this matters operationally;
- how the entity influences the ecosystem;
- where relationship investment should occur;
- what opportunity window may emerge.

Never merely summarize the article.
