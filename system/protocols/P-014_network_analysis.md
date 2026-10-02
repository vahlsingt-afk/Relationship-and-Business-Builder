---
id: P-014
title: Network analysis — strategic report card
script: system/scripts/network_analysis.py
cache: system/.cache/network_analysis.json
reads:
  - system/baseline_index.json
  - system/active_threads.yaml
  - system/cards/
writes:
  - system/analysis/<date>-network-analysis.md  (when run with --write)
inputs:
  - name: date
    description: ISO date for "as of" framing (default: system date).
    required: false
trigger: weekly review; before strategic planning; on-demand whenever the operator asks "where is my network strong/weak"
---

# P-014 — Network analysis

## Purpose

Produce a strategic report card that answers: *where is my network strong, where is it weak, what bridges do I have, what's the composition shape, what should I do about it.* Synthesizes every signal layer in the system into one coherent assessment.

## Why this exists

The daily brief is operational — what's happening today, what's due, who's cooling. The network analysis is *strategic* — the longer view that looks at the shape of the network as a whole, identifies structural issues, and recommends next moves. You'd run the daily brief every morning; you'd run this weekly or monthly.

## What it surfaces

**Strengths.** Top company clusters ranked by relationship depth — anchor presence (binary, huge weight), LKI+ count, average DRR, recency of inner-tier touches. Top clusters are where you can leverage now.

**Weaknesses.** Multiple types:
- Unanchored clusters from `network_gap.py` — size ≥ 5, no inner RC.
- RCs without `last_touch` — invisible to dormancy engine.
- Missing RC cards.
- Contact-field gaps (email/phone) on inner/broader-tier RCs.
- Cooling clusters where most RCs are past their dormancy threshold.

**Bridges.** Granovetter weak-tie value — contacts who span 2+ Circles. These are the people who connect clusters that wouldn't otherwise meet. Inner-tier multi-Circle members rank highest.

**Composition health.** Counts compared to Dunbar-derived target bands:
- Inner-tier RCs: 12–25 (your close working circle)
- Active RCs (all tiers): 15–50 (the people you really know)
- LKI: 50–250 (named acquaintances)
- Card coverage %.
- Inner-tier RCs in their dormancy window (≥60% is healthy).

**Diversity.** Cluster distribution. Over-concentration warning if any single cluster is >30% of LKI+ contacts. Median cluster size flags fragmented vs concentrated networks.

**Recommendations.** Ranked by impact (high → low), with low-effort wins prioritized within each tier. Every recommendation cites which signal layer surfaced it.

## Operations

```bash
# Print to stdout:
python3 system/scripts/network_analysis.py

# Write to system/analysis/<date>-network-analysis.md:
python3 system/scripts/network_analysis.py --write

# JSON for downstream consumers:
python3 system/scripts/network_analysis.py --json --cache

# Specific date:
python3 system/scripts/network_analysis.py --date 2026-05-15 --write
```

Or via MCP (`rb.network_analysis`) / HTTP (`GET /network_analysis`).

## First validation run (2026-05-15)

Surfaced these real findings against the 2,671-entry baseline:

- **PAR Technology** is the strongest cluster (13 LKI+, John Adams as inner anchor, strength score 163.3).
- **McDonald's** is second-strongest (6 LKI+, Bruce Sellnow + Jeff Coffland anchors).
- **6 multi-Circle bridges identified**: Amy Spytko, Brandon Rabinowitz, Chason Forehand, Cristina Gia Luciano, Dave Richards (all RC inner), plus Maggie Benson (LKI).
- **Inner-tier in-window 41.2%** vs target ≥60% — meaningful cooling signal.
- **Median cluster size of 1** — the network is broad but shallow outside the top clusters. New finding.
- **6 unanchored clusters** ranked: Global Payments, Qu POS, Toast, etc.
- **Top recommendation**: add `last_touch` for David Deems (HIGH impact, LOW effort).

## Composition target bands

The Dunbar-derived bands in code (`DUNBAR_TARGETS` in `rb_core.py`) are *suggestions*, not rules. They reflect roughly:

- ~15 close working contacts (intimate layer, daily/weekly engagement)
- ~50 friends (people you've genuinely talked to in the last year)
- ~150 named acquaintances (the wider professional layer you'd recognize)

If the operator's role calls for a different shape (e.g., venture capitalist optimizing for breadth over depth), adjust the targets in code. The bands are a starting reference, not the answer.

## Failure modes

- **Sparse cluster data.** If most baseline entries don't have `current_company` populated, the cluster analysis becomes noisy. Cleanup in baseline > better analysis.
- **Stale recommendations.** If the operator runs this monthly but doesn't act on the recommendations between runs, the list grows. Recommendations are ranked but don't currently track whether they've been "accepted" or "dismissed" — that's V0.1.
- **Bridge underdetection.** V0 defines bridges as multi-Circle membership only. Contacts who bridge two non-overlapping company clusters (without being in a Circle) aren't surfaced. V0.1 could add company-cluster bridging.
- **No industry/sector dimension.** The diversity analysis runs on company clusters, not industries. Real industry diversity requires tagged data the baseline doesn't carry today.

## Roadmap

- **Team mode.** See `system/TEAM_MODE.md`. The team version is this analysis run across the union of multiple operators' baselines, with privacy controls. V1 enterprise feature.
- **Recommendation acceptance tracking.** Persist which recommendations the operator acted on so the analysis can mark them "in progress" or "done" on the next run.
- **Time-series view.** Run the analysis weekly; surface trends — *"your inner-tier-in-window % is dropping; you've added 2 RCs in the last 30 days; the cooling cluster trend is X."*
- **Sector/industry tags.** Add a `sector` or `industry` field to baseline entries so diversity analysis can run on industry, not just company.

## Voice

Strategic and direct. Calls strengths and weaknesses by name. Doesn't soften findings to be polite — *"6 of your top clusters have no inner anchor"* is more useful than *"there may be opportunities to deepen relationships in some clusters."*

Recommendations are written as commands, not suggestions. *"Add `last_touch` for David Deems"* not *"consider adding..."* The operator decides whether to act; the analysis's job is to be specific about what would help.
