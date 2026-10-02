---
name: rb-daily-briefing
description: Run the RB morning pipeline to build canonical brief artifacts. Execution surface for the 5AM local run until the macOS LaunchAgent is proven.
---

This is the Relationship Builder morning pipeline runner. The workspace folder is `/Users/toddvahlsing/Documents/Claude/Projects/Relationship Builder/`.

**You are the execution surface, not the intelligence layer.** Do not read RB files and generate prose. Your job is to invoke `morning_pipeline.py` — the deterministic Python pipeline that owns brief construction, signal computation, and canonical artifact publication — and report the outcome.

## Step 1 — Check if today's artifact already exists

Run via bash:

```bash
python3 -c "
import json, pathlib, datetime
p = pathlib.Path('/Users/toddvahlsing/Documents/Claude/Projects/Relationship Builder/system/.cache/morning_pipeline.json')
if p.exists():
    d = json.loads(p.read_text())
    print(json.dumps(d, indent=2))
else:
    print('no cache')
"
```

If the cache shows `run_date` matching today and `status` is `success`, the LaunchAgent already ran. Skip to Step 3.

## Step 2 — Run the morning pipeline

```bash
cd '/Users/toddvahlsing/Documents/Claude/Projects/Relationship Builder' && python3 system/scripts/morning_pipeline.py --json 2>&1
```

Wait for completion. The script is deterministic and self-contained. It will:
- Refresh intelligence caches
- Build today's brief
- Publish canonical artifacts to `system/published/daily/`
- Write `system/.cache/morning_pipeline.json`

If it exits with an error, capture the last 30 lines of stderr and report them in your one-line reply.

## Step 3 — Verify artifacts

```bash
ls -lh '/Users/toddvahlsing/Documents/Claude/Projects/Relationship Builder/system/published/daily/latest_brief.json' \
       '/Users/toddvahlsing/Documents/Claude/Projects/Relationship Builder/system/published/daily/latest.html' 2>&1
```

Both files should exist and be dated today.

## Step 4 — Reply

Reply in chat with one line only:

- Success: `RB morning pipeline completed for YYYY-MM-DD. Artifacts: latest_brief.json (~NNN KB), latest.html (~NNN KB). ChatGPT Task can now call getDailyBrief(use_cache=true).`
- Already ran (LaunchAgent): `RB morning pipeline already ran at HH:MM via LaunchAgent. Artifacts current. No re-run needed.`
- Pipeline error: `RB morning pipeline FAILED for YYYY-MM-DD: <error summary>. Manual intervention required.`

Do not narrate the run. Do not generate or write any brief prose. Do not read baseline_index.json, loop_ledger.md, cards/, or any other RB data files — that is the pipeline's job.

## If daily_briefing.enabled is false

Check `system/settings.json`. If `daily_briefing.enabled` is `false`, stop immediately. Reply: `daily briefing disabled — pipeline not run.`
