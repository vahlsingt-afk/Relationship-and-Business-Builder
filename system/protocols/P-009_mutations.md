---
id: P-009
title: Write-back mutations
script: system/scripts/mutations.py
cache: (none — mutations invalidate caches on the next read by mtime)
reads:
  - system/baseline_index.json
  - system/loop_ledger.md
  - system/active_threads.yaml
  - system/schemas/baseline.schema.json
writes:
  - system/baseline_index.json
  - system/loop_ledger.md
  - system/active_threads.yaml
  - system/_snapshots/
inputs:
  - name: subcommand
    description: One of loop-add, loop-close, touch, contact-add, thread-open, thread-close.
    required: true
trigger: operator-initiated (or AI-initiated after operator confirms an outcome)
---

# P-009 — Write-back mutations

## Purpose

Record what happened. Close a loop after a call. Update a `last_touch` after a meeting. Add a new contact mid-conversation. Open or close an active thread. Without this, RB is an assistant that takes dictation; with it, RB keeps records.

## Safety contract

Every mutation:

1. **Snapshots** the file being changed into `system/_snapshots/<file>.<pre-op-tag>.<ext>`. Rollback is one `cp` away.
2. **Applies** the change.
3. **Validates** (where applicable — `validate_baseline.py` runs after any baseline edit). On failure, the snapshot is restored automatically and the operation exits non-zero.
4. **Projects** the change onto materialized views in the same transaction when a projection exists. Today the only projection that requires synchronous update is the RC card frontmatter; see "Projection sync" below.

The mutation surface is intentionally small and explicit. There is no "AI freely edits the graph" path. Each operation is a named verb with required arguments.

## Projection sync

`baseline_index.json` is the canonical source of truth for contact state. The RC card files under `system/cards/{id}.md` are a materialized projection — the YAML frontmatter mirrors a subset of canonical fields so a human can read the card in isolation. Historically these could drift after `touch` (TOUCHCONTACT-PROJECTION-SYNC-001, 2026-05-18) because the mutation only wrote the baseline.

The current contract:

- After a successful baseline write, `touch_contact()` checks for `system/cards/{id}.md` and rewrites the frontmatter `last_touch` field in place. The card is snapshotted before rewrite.
- The card update is best-effort and never rolls back the baseline. If the card cannot be parsed or written, the result reports `card_reason: card_update_failed:<msg>` and the canonical state is still considered authoritative.
- The mutation result distinguishes between canonical and projection outcomes so callers can render an honest status:

  ```json
  {
    "ok": true,
    "id": "jeff-wayman",
    "last_touch": "2026-05-12",
    "prior_last_touch": "2025-12-29",
    "baseline_updated": true,
    "card_exists": true,
    "card_updated": true,
    "card_prior_last_touch": "2025-12-29",
    "card_reason": "replaced",
    "cache_refresh_recommended": true
  }
  ```

  Possible `card_reason` values: `replaced`, `added`, `in_sync`, `no_card_exists`, `no_frontmatter_block`, `dry_run`, `card_update_failed:<msg>`.

- `GET /cards/{id}` returns `canonical.last_touch`, `frontmatter.last_touch`, and a `projection_status` flag (`in_sync`, `stale`, `frontmatter_missing`, `canonical_missing`, `no_frontmatter_block`) so any reader can detect drift without trusting the markdown alone.

The general rule: when a canonical write has a materialized projection, the projection update is part of the mutation, not a separate maintenance job. If a future write surface (e.g., circles, threads YAML) adds a projection of canonical state, extend `mutations.py` rather than relying on out-of-band rebuilds.

## Operations

### loop-add

Append a new row to `loop_ledger.md`. Loop id is auto-assigned as `L-<today>-<NNN>` unless overridden.

```bash
python3 system/scripts/mutations.py loop-add \
  --party "Mike Schwartz" \
  --description "Send updated resume." \
  --target 2026-05-22
```

### loop-close

Mark an existing loop closed with a reason note. The reason replaces the `open` status in-place; the row stays in the ledger as the audit trail.

```bash
python3 system/scripts/mutations.py loop-close \
  --id L-2026-05-12-003 \
  --reason "Phone screen captured; promoted Mike to LKI."
```

### touch

Update `last_touch` for an existing baseline entry. Optionally append a source tag to the sources list.

```bash
python3 system/scripts/mutations.py touch \
  --id bruce-sellnow \
  --date 2026-05-15 \
  --source "chapter_meeting_2026-05-15"
```

### contact-add

Add a new entry to `baseline_index.json`. Validates the entire baseline after; rolls back on schema failure.

```bash
python3 system/scripts/mutations.py contact-add \
  --id mike-schwartz \
  --name "Mike Schwartz" \
  --company "Global Payments Inc." \
  --signal-class LKI \
  --source "screen_2026-05-12"
```

### thread-open / thread-close

Manage entries in `active_threads.yaml`. Requires PyYAML (`pip install pyyaml --break-system-packages`).

```bash
python3 system/scripts/mutations.py thread-open \
  --id T-2026-05-some-deal \
  --title "Some new partnership" \
  --type partnership \
  --people contact-id-1 contact-id-2 \
  --boost-score 1.25

python3 system/scripts/mutations.py thread-close \
  --id T-2026-05-some-deal \
  --reason "Deal signed."
```

## --dry-run

Every subcommand accepts `--dry-run`, which prints the change it *would* make without writing anything. Use this before any unfamiliar mutation.

## Cross-surface availability

The same operations are exposed via MCP (`rb.loop_add`, `rb.loop_close`, `rb.touch`, `rb.contact_add`, `rb.thread_open`, `rb.thread_close`) and HTTP (`POST /loops`, `POST /loops/close`, `POST /touch`, `POST /contacts`, `POST /threads`, `POST /threads/close`). The compute path is identical — the CLI handler is the source of truth, and both server surfaces call into it.

## Failure modes

- **Validation failure on baseline write.** The snapshot is restored automatically. Read the error output; fix the input; retry.
- **Loop id collision.** Pass an explicit `--id` if the auto-assigner picks a number you don't want.
- **Active-thread write requires PyYAML.** The reader has a fallback; the writer requires `yaml.safe_dump`. If PyYAML isn't installed, the operation will fail loudly with an install instruction.
- **Concurrent writes.** No locking. Don't run two `mutations.py` invocations against the same file simultaneously.

## Voice

Mutations have no narrative. They print one line per successful operation, an error path on failure. The audit trail lives in the snapshot directory and in git history — not in the script's output.
