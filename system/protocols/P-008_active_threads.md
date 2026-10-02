---
id: P-008
title: Active strategic threads
script: (declarative — read system/active_threads.yaml directly)
cache: (consumed via daily_brief and drr_score caches)
reads:
  - system/active_threads.yaml
writes: []
inputs: []
trigger: read at the top of every daily brief and DRR score call
---

# P-008 — Active strategic threads

## Purpose

Active threads are the lens through which everything else in the system is read. Where loops are single deliverables and circles are static groupings, **a thread is a named context the operator is currently moving on**. The daily brief, DRR scoring, and any AI session should read threads first — they tell you what matters *this week*, not just historically.

## Why this exists

Without active threads, every session re-derives priority from scratch. Bruce Sellnow's DRR score is 91.8 in isolation. But is he high-leverage *this week*? Without context, the system can't tell. With active threads, Bruce gets a +10% boost as part of the OTP3 cohort thread, and the daily brief surfaces that thread as the lens for reading his quiet-zone status.

## File: `system/active_threads.yaml`

YAML list under `threads:` with these fields per entry:

| Field | Type | Notes |
|---|---|---|
| `id` | string | Stable kebab-case identifier (e.g. `T-2026-05-genius-global-payments`). |
| `title` | string | One-line human-readable name. |
| `opened` | ISO date | When this thread became active. |
| `target_close` | ISO date (optional) | Soft deadline. |
| `status` | enum | `open` \| `paused` \| `closed`. |
| `type` | enum | `job_opportunity` \| `business_engagement` \| `partnership` \| `chapter_activation` \| `role_search` \| `account_pursuit`. |
| `people` | list of ids | Contact ids in baseline this thread touches. |
| `companies` | list of strings | Companies this thread touches. |
| `context` | paragraph | Why this thread exists, what's at stake. |
| `current_state` | paragraph | What the system observed last. |
| `boost_for_brief` | `high` \| `medium` \| `low` | How loudly to elevate in today.md. |
| `boost_score` | number | DRR multiplier (1.0–1.5). |

## How it's used

1. **Daily brief.** The "Active threads" section appears FIRST, before crossings or loops. High-boost threads get a paragraph; the rest get a compact table. Every other section is read in light of these.
2. **DRR scoring.** Every contact's score is multiplied by the max applicable thread boost. A contact gets boosted if their id is in a thread's `people` OR their `current_company` is in `companies`.
3. **Brief explainability.** The DRR top-N table surfaces the matched thread ids per contact, so the reader can see *why* a contact is elevated.

## Operations

- **Open a thread:** `python3 system/scripts/mutations.py thread-open --id ... --title ... --type ...` (also MCP `rb.thread_open`, HTTP `POST /threads`).
- **Close a thread:** `python3 system/scripts/mutations.py thread-close --id ... --reason ...` (MCP `rb.thread_close`, HTTP `POST /threads/close`). Closed threads are kept in the file with `status: closed` and a `closed_date` — they are the project ledger.

## Failure modes

- **Stale current_state.** Threads decay if `current_state` isn't refreshed. Every Monday, walk the open threads and update or close.
- **People-id typos.** If a `people:` entry doesn't match a baseline id, the boost silently doesn't apply. Run `python3 system/scripts/drr_score.py` and look for the expected boost; if missing, the typo is in the YAML.
- **Boost stacking.** Multiple threads apply → MAX, not SUM. Intentional. A contact in two threads shouldn't get a 2× boost.

## Voice

Threads are declared crisply. `context` and `current_state` are 2–4 sentences each. No hedging. If the thread isn't sharp enough to summarize in four sentences, it isn't a thread yet — it's a vague intent.
