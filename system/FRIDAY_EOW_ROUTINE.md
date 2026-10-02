# Friday End-of-Week Routine

What `friday_eow_routine.py` checks, why, and what it deliberately does not do. Built 2026-08-24 in direct response to `network_map.md`/`intro_brokers.md` sitting untouched for ~3 months with nothing flagging it.

## What it does — review and surface, never auto-fix

Runs every Friday at 4:00 PM (`com.relationshipbuilder.friday-eow-routine.plist`), also runnable on demand: `python3 system/scripts/friday_eow_routine.py [--json]`.

1. **Weekly plan review** — reads `weekly_plan.json` (adopted) and `weekly_scorecard_draft.json` (the wins/misses/outcome-movements draft `weekly_review_generator.py` already auto-generates every Friday). Reports status only.
2. **Loop review** — surfaces overdue L- loops (`rb_core.loops_by_status()`) and stale EL- loops (the same filter `eolms.py cmd_stale` uses — a fixed 45-day inactivity cutoff, `core.EOLMS_STALENESS_WARNING_DAYS`). Never closes anything — closing a loop is a judgment call, not something this script should decide unattended.
3. **Freshness check** — the new piece. A small, explicit registry of files/records that should move at least weekly, each checked against a 7-day threshold:
   - `baseline_index.json`, `ecosystem_intelligence.json`, `interaction_ledger.json` — core memory.
   - `network_map.md`, `intro_brokers.md` — derived narrative views (the pair that prompted this whole routine).
   - Every **activated** Blue Sheet account (`workbook_path is not None` in `blue_sheet_registry.json`) — checked via its own `last_review_date` field, not mtime.
4. **Backup** — copies the same core memory files plus every activated Blue Sheet's `account.json`/`brand_profile.json` into `system/_backups/<date>/`. This is a real backup; the pre-existing `publish.py --send`/"send_backup_email" mechanism is dead code (unscheduled, disabled by a settings flag, disabled by an env-gate) and never archived files even when it ran — it only sent a status email.

## Deliberately excluded from the freshness check, and why

- **Architecture/design docs** (`ARCHITECTURE.md`, `SCHEMAS.md`, `WHAT_PERSISTS.md`, `INTELLIGENCE_PIPELINE_MAP_*.md`, `EVIDENCE_TO_PERSISTENCE_MAP_*.md`). These correctly change rarely — flagging them "stale" on a 7-day cadence would be noise, not signal.
- **`today.md`**. Regenerates daily by design; would always trivially pass a weekly check and adds nothing.

If "documentation" should mean something broader than this, the registry (`FRESHNESS_FILES` and the Blue Sheet loop in `check_freshness()`, both in `friday_eow_routine.py`) is a short, explicit list — easy to extend.

## What it doesn't do

- Doesn't regenerate `network_map.md`/`intro_brokers.md` itself. Confirmed 2026-08-24: these are genuine strategic-narrative documents (named judgment calls about brokers, gaps, promotion candidates), not a mechanical data pull — regenerating them properly is real analytical work that deserves its own session, not something this routine should attempt or fake.
- Doesn't confirm the weekly plan or scorecard draft, or close any loop. All of that stays a human decision, surfaced clearly, never auto-applied.

## Output

- `system/.cache/friday_closeout_result.json` — structured result, same atomic-write shape `brief_acceptance_check.py` uses.
- `system/friday_closeout.md` — human-readable report, regenerated each run (same "most recent generation persists" convention as `today.md`), not a new dated file every week.
