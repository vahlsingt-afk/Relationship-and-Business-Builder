---
id: P-006
title: Network gap scoring
script: system/scripts/network_gap.py
cache: system/.cache/network_gap.json
reads:
  - system/baseline_index.json
writes: []
inputs:
  - name: min_cluster
    description: Minimum cluster size to consider.
    required: false
  - name: gaps_only
    description: Show only unanchored clusters.
    required: false
trigger: weekly (Monday); after any material ingest; on-demand when operator asks
---

# P-006 — Network gap scoring

## Purpose

Identify company-clusters that are large enough to matter but have **no inner-tier RC anchor**. These are the strongest signals that an LKI promotion would change the system's reach in a real way.

## When to run

- Weekly (suggested Monday) — flagged in `STATUS.md` as a not-yet-built scheduled task.
- After any LinkedIn / Google Takeout ingest that materially changed the cluster size of any company.
- Whenever the operator asks "which clusters am I weakest in?"

## Read set

1. `system/baseline_index.json`
2. `system/scripts/rb_core.py`

## Inputs

- `--min N` minimum cluster size (default 5).
- `--limit N` how many rows to show.
- `--gaps-only` show only unanchored clusters.

## Steps

1. Run `python3 system/scripts/network_gap.py --gaps-only --limit 15`.
2. For each `anchor_gap == True` row with `lki_count >= 2`, treat as a promotion candidate cluster.
3. Inside each candidate cluster, identify the highest-DRR LKI (`drr_score.py --class LKI`) and surface as the lead promotion target.
4. Cross-check against `heuristics.md` — some clusters have explicit "do not promote" rules (e.g., a company Todd has chosen to avoid).

## Output contract

Ranked table of clusters with `total`, `any_rc`, `lki_count`, `inner_rcs`, `anchor_gap`, `gap_score`. JSON via `--json`.

## Current top gaps (2026-05-15)

For context — these will move; the script is the source of truth.

| Company | Size | LKI count |
|---|---:|---:|
| Qu POS | 10 | 2 |
| Global Payments Inc. | 6 | 2 |
| Toast | 10 | 1 |
| True Group, Inc. | 9 | 1 |
| NCR Voyix | 8 | 1 |

Bob Gibson (Toast) is the named promotion candidate per `MANIFEST.md` history.

## Failure modes

- **Company-name drift.** "Toast" vs. "Toast, Inc." count as different clusters. Normalize before running if needed.
- **"Retired" / "Self-employed".** These are noise clusters. Flag them visually but don't act on them.

## Voice

Analytic. Treat each unanchored cluster as a hypothesis ("RB has no inner-tier line into [company]; promoting [LKI] would close the gap") and require the operator to confirm before promotion.
