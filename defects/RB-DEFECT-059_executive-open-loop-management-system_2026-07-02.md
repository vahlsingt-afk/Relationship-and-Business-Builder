# RB-DEFECT-059: Executive Open Loop Management System (EOLMS)

**Status:** CLOSED (P1–P3) — 2026-07-02
**Reported by:** Todd, via a hand-written "Executive Open Loop Register" review of RB (2026-07-02)

## Problem

Todd wrote an executive-style open loop register covering his real strategic programs (Global
Payments, RB itself, BridgePoint Ops, Publishing, Home Projects, etc.) and assessed RB against it,
flagging 5 concrete gaps:

1. **Dependencies** — e.g. "Enterprise Capture Strategy" should stay blocked until "Account
   Portfolio Received" closes, automatically.
2. **Trigger-based activation** — e.g. the 90-day plan should wake up automatically at end of week 1,
   not sit static on a list.
3. **Aging/staleness detection** — loops stale 30/60/90 days should be flagged for review.
4. **Evidence-backed closure** — telling RB "the well pump is working" should close the loop, update
   the knowledge graph, and surface as a completed milestone in the next brief.
5. **Executive roll-ups** — answer "what's blocked / stalled / changed since yesterday / where am I
   accumulating execution debt," not just list tasks.

The existing `loop_ledger.md` + `rb_core.Loop` + `mutations.py` stack is a flat one-party/one-action/
one-date/open-or-closed tracker — adequate for follow-up reminders, structurally unable to represent
any of the above. A draft technical spec for a replacement (`system/design/EOLMS_SPEC.md`, dated
2026-06-30) already existed but nothing in it had been built — zero references to `eolms`/`ELoop`
anywhere in the codebase prior to this fix.

## Root cause

Nobody had picked up `EOLMS_SPEC.md` and implemented it. The spec itself was sound (confirmed by
building directly against it) — this was a backlog gap, not a design gap.

## Solution (P1–P3 of the spec's own phasing)

- **`system/eolms/loops.json`** + **`system/eolms/loops.schema.json`** — the `ELoop` register.
  Status enum extended beyond the spec's original 8 states to add `deferred` (paused, resumes via
  `activation_date`/`activation_condition`) and `monitor` (intentionally low-touch, exempt from
  staleness alerts) — both needed to represent real entries from Todd's register ("Executive Thought
  Leadership: Active (Paused)", "Passive Career Intelligence: Monitor"). Added an explicit
  `blocked_by: string[]` field (distinct from the spec's generic `related_loop_ids`) so dependency
  enforcement is unambiguous.
- **`system/scripts/eolms.py`** — CLI: `add`, `update`, `transition`, `get`, `list`
  (`--status`/`--category`/`--priority`/`--blocked`/`--stale`/`--changed-since`), `status` (executive
  roll-up counts + a CoS recommendation line), `risks` (top N high-priority at-risk loops), `stale`,
  `tick`, `migrate`, `validate`. All mutating commands snapshot `loops.json` to
  `system/eolms/archive/loops_YYYY-MM-DD.json` (once per day) before writing, default to preview-only
  output, and require `--confirm` to actually write.
- **`tick` — the automation engine**, run in this order so a loop can't fall through the cracks
  within one pass:
  1. Dependency resolution: `blocked` → `waiting` once every `blocked_by` loop is `completed`/`archived`.
  2. Dependency enforcement: `active`/`waiting` → `blocked` if a dependency reopens (safety net).
  3. Trigger activation: `deferred`/`identified` → `active` once `activation_date` has arrived.
  4. Dormancy: `active` → `dormant` once `last_activity` exceeds the category threshold (action/decision
     30d, project/opportunity/research 45d, strategic_initiative 90d; relationship uses `cadence_days`).
  Every transition appends an `auto_transition` history entry and resets `last_activity` (the
  transition itself counts as activity, so a loop that just auto-activated doesn't immediately
  qualify as stale in the same run).
- **`rb_core.py`** gained the `ELoop` dataclass, `EOLMS_PATH`/`EOLMS_DORMANCY_DAYS`/
  `EOLMS_STALENESS_WARNING_DAYS`, `load_eloops()`, `eloops_by_status()`, `eloops_executive_summary()`
  (shared by both the CLI and the Daily Brief so the numbers always agree). No changes to the
  existing `Loop`, `parse_loop_ledger`, or `loops_by_status`.
- **Migration**: `eolms.py migrate` imported all 32 `loop_ledger.md` rows (24 open → `active`, 8
  closed → `completed`), inferring `category` from description keywords and `last_activity` from the
  most recent "Re-dated YYYY-MM-DD" note in each row (falling back to `opened`). Ran `eolms.py tick`
  once immediately after — reused the same staleness logic rather than hand-judging which rows were
  "clearly stale" at migration time. 3 loops (all last touched 2026-05-08, 55 days prior) were
  correctly auto-classified `dormant`.
- **Seeded 19 strategic-program loops** directly from Todd's Open Loop Register text: Global Payments
  (3 loops, including the two below), RB's 4 sub-programs (Intelligence Platform, Capture Platform,
  Executive Chief of Staff, Claude Development), BridgePoint Ops, Publishing, Executive Thought
  Leadership, Executive Relationship Capital, Home Projects (2), Passive Career Intelligence, and the
  5 "Recently Closed" items (backfilled as `completed` with an explicit note that the original
  historical close date wasn't recorded, rather than inventing one).
  - **`EL-2026-07-02-003` "Global Payments — Enterprise Capture Strategy"** — `status=blocked`,
    `blocked_by=["EL-2026-07-02-002"]` (the "Receive Enterprise Account Portfolio" waiting loop) —
    Todd's own dependency example, built as a real, live loop. Verified end-to-end: transitioning
    the portfolio loop to `completed` and running `eolms tick` correctly proposed
    `EL-2026-07-02-003: blocked -> waiting (dependencies cleared)`; the test transition was then
    reverted since the portfolio hasn't actually been received yet.
  - **`EL-2026-07-02-001` "Global Payments — Develop 90-Day Success Plan"** — `status=deferred`,
    `activation_date=2026-07-11` (the Friday ending GP's first week, GP start = 2026-07-07) — Todd's
    own trigger example. Will auto-activate via a future `tick` run on/after that date.
- **Daily Brief integration**: new `_render_executive_status()` in `render_daily_brief.py`, reading
  `system/eolms/loops.json` directly via `rb_core.load_eloops()`/`eloops_executive_summary()` —
  mirrors the existing `_render_captures()` / `capture_ingest` import-and-render pattern precisely so
  it stays decoupled from `daily_brief.py`'s ~19,000-line canonical-brief pipeline (not touched at
  all by this fix — zero risk to its existing self-test harness). Renders nothing if the register is
  empty or the module fails to import. Wired into `render()` immediately after "My Priorities" and
  before "Decision Queue," per the spec's §7.2 placement. Verified via
  `render_daily_brief.py --date 2026-07-02 --dry-run --force`: the block renders with correct counts
  (4 active strategic initiatives, 6 active projects, 1 waiting, 1 blocked, 5 completed since
  yesterday, 3 dormant) at the right position, and the rest of the brief renders unchanged.

## Verification

```
python3 -c "import rb_core"                          # imports cleanly
python3 eolms.py validate                             # OK — 51 loop(s) valid against schema
python3 eolms.py status                               # counts match expectations
python3 eolms.py risks                                # surfaces the 3 Global Payments loops (high priority, at-risk statuses)
python3 eolms.py list --blocked                        # EL-2026-07-02-003 only
python3 eolms.py list --stale                          # empty (nothing both active/waiting AND >45d — the 3 dormant loops are already dormant, not merely stale)
python3 eolms.py tick                                  # dry run — no unexpected transitions after the above verification cycle
python3 render_daily_brief.py --date 2026-07-02 --dry-run --force   # Executive Status block renders correctly, brief otherwise unchanged
```

## Explicitly deferred (not attempted this pass)

- **P4 — Custom GPT auto-capture + live API endpoint.** A `/eolms` endpoint in `system/api/server.py`
  (+ `openapi.yaml`/`openapi_gpt.yaml`) so the Custom GPT can read/write EOLMS directly, plus
  instruction updates (`eolms_capture_patterns`, `eolms_lifecycle_triggers`) so narrated evidence
  ("the well pump is working") auto-transitions the matching loop instead of requiring a manual
  `eolms transition` CLI call. This is genuinely gap #4's full realization — what's built now handles
  the mechanics (transition + history log) but not the automatic *recognition* of narrated evidence.
  Deferred because it touches the live API surface and needs its own pass on auth/request schema and
  GPT capture-pattern tuning.
- **P5 — knowledge graph edge materialization** (`loop_involves_person`, `loop_involves_org`,
  `loop_blocks`, etc., as first-class graph edges). Phase 1 (string-array filtering via
  `related_people`/`related_orgs`, no graph traversal) is sufficient for now.
- **Relationship cadence defaults** (tier-based `cadence_days` inheritance from DRR tiers) — not
  needed yet since no per-contact `relationship`-category loops were migrated this pass;
  `loop_ledger.md` remains the per-contact system of record until/unless that migration happens.

## Files touched

- `system/scripts/rb_core.py` (added, no existing code changed)
- `system/scripts/eolms.py` (new)
- `system/eolms/loops.json`, `system/eolms/loops.schema.json` (new)
- `system/scripts/render_daily_brief.py` (added `_render_executive_status()`, one call site in `render()`)
- `system/design/EOLMS_SPEC.md` (status flipped to Implemented, §11 open questions resolved)
- `system/STATUS.md` (this close-out entry)
