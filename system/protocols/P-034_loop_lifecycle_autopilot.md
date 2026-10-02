---
id: P-034
title: Loop Lifecycle Autopilot
script: system/scripts/loop_autopilot.py
cache: system/.cache/loop_autopilot_*.json
reads:
  - system/.cache/daily_brief.json (morning)
  - system/loop_ledger.md (all phases)
  - system/.cache/source_health.json (midday/closeout context)
writes:
  - system/.cache/loop_autopilot_*.json
  - system/closeouts/YYYY-MM-DD.md (closeout phase, --confirm)
  - system/loop_ledger.md (morning apply or closeout closure, --confirm)
trigger: morning (05:00 automation, dry-run); midday (on-demand); 16:30 (closeout)
---

# P-034 - Loop Lifecycle Autopilot

## Purpose

Connect the existing loop modules across the day without creating a new scoring
engine. The autopilot proposes loops in the morning, checks closure evidence
midday, and runs the closeout sequence at 16:30.

## Phases

Morning:

```bash
python3 system/scripts/loop_autopilot.py --phase morning
```

Runs `smart_loops.py --json` in read-only mode and writes
`system/.cache/loop_autopilot_morning.json`.

Midday:

```bash
python3 system/scripts/loop_autopilot.py --phase midday
```

Runs `passive_verification.py --json`, reports `auto_closeable` and
`possible_resolution`, and writes `system/.cache/loop_autopilot_midday.json`.

Closeout:

```bash
python3 system/scripts/loop_autopilot.py --phase closeout --confirm
```

Runs `passive_verification.py --apply --confirm --json`, then
`closeout.py --write --confirm --json`, and writes
`system/.cache/loop_autopilot_closeout.json`.

## Safety

Without `--confirm`, every phase is read-only. The orchestrator calls the
existing scripts as subprocesses so their own dry-run and confirm guards remain
the enforcement point.
