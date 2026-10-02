# Claude Request — Automate Cockpit Export to Google Drive

**Date:** 2026-08-19
**Requested by:** Claude, as RBB architect
**Implementation partner:** Codex
**Context:** Follow-up to `system/CLAUDE_REVIEW_RESPONSE_RBB_COCKPIT_ARCHITECTURE_2026-08-19.md` §8 (ChatGPT Project "RBB" addendum).

## Why

The RBB Project in ChatGPT has no live Actions/API access (Project-only limitation, confirmed against current ChatGPT docs — Actions are Custom-GPT-only). Its only path to reading current cockpit state is a linked Google Drive file, which ChatGPT reads live at query time. Today that requires Todd to manually re-upload `system/cockpit/context.json` after every pipeline run.

**Do not use the existing Google Drive copy of `context.json`** (Drive file `1kSnLZZUkxjCLMwPma2m_freByZz2k4Xs`, under a folder tree that mirrors this repo). That copy is a side effect of Codex's own `google-drive@openai-curated` plugin — confirmed via direct inventory to be opportunistic, not continuous: `personal_log.json` in the same mirrored folder hasn't updated since 2026-07-03 while sibling files updated today, proving files only sync when Codex happens to touch them during some other task, with no schedule or guarantee. Building automation on top of it would let the Project go silently stale for weeks with no signal — worse than the manual process it's meant to replace. See `[[rb_infra_surface_map]]` (memory) for the full finding.

## What to build

A deterministic export step, owned by RBB's own pipeline, independent of Codex's incidental Drive activity:

1. **New script:** `system/scripts/cockpit_drive_export.py`. Reads `system/cockpit/context.json` after it's generated and pushes it to one fixed Drive file via the Drive API v3 `files.update` (media upload) — same file ID every time, not a new file per run.
2. **Auth:** OAuth refresh-token flow (`google-auth-oauthlib`), not a service account — simpler for a single-user personal Drive, no folder-sharing step needed, and matches the existing plaintext-credential-in-LaunchAgent pattern already used for `RB_API_KEY` (see `[[rb_infra_surface_map]]`). One-time browser consent from Todd produces a refresh token; store it the same way `RB_API_KEY` is stored (plaintext env var in a LaunchAgent plist, or an equivalent local file with tight permissions — match existing convention, don't invent a new one).
3. **Target file:** create a **new**, dedicated Drive file/folder for this — do not reuse `1kSnLZZUkxjCLMwPma2m_freByZz2k4Xs` or any path under the existing incidental mirror. Suggested: a top-level `RBB Cockpit Export/context.json` folder, created fresh, so it's never ambiguous which copy is the deliberately-maintained one. Record the resulting file ID in `system/.cache/cockpit_drive_export_state.json` (last export timestamp, file ID, success/failure) — this is a health record for the export itself, not a canonical-state receipt; keep it lightweight, not part of the promotion-receipt vocabulary in `CANONICAL_REGISTRY.yaml`.
4. **Wiring:** call this script immediately after `cockpit_context.py` regenerates `context.json` — both from the scheduled `morning-pipeline.plist` run and from any ad hoc regeneration, so Drive freshness tracks the source file's actual freshness rather than a separate schedule.
5. **Failure handling:** non-fatal. A failed Drive push must never fail or block the morning pipeline — log it, record it in the state file, move on. The pipeline's existing delivery-gating logic (see `[[rb_defect_delivery_gating_closeout]]`) should not treat this as a gate.
6. **Test:** one focused test with a mocked Drive client verifying the export function is invoked with the right content and file ID — no live network call in the test suite.

## Registry edit

Add the new export target to `CANONICAL_REGISTRY.yaml` under `cockpit_projection_contract`, consistent with the existing `mirrors_and_worktrees` policy (`role: development_or_cached_projection_only`, `writable_as_canonical: false`) — this is a cached projection endpoint, not a new authority.

## Explicitly out of scope here

- Do not touch, clean up, or build on the existing incidental Drive mirror (`google-drive@openai-curated` plugin's output). Whether to disable that plugin or audit/delete what it's already written is still Todd's open decision, tracked separately in `[[rb_infra_surface_map]]`.
- Do not attempt to give the ChatGPT Project live Actions/API access — confirmed unavailable at the Project level as of this review.

## One-time manual steps only Todd can do

1. Complete the OAuth consent flow once to produce the refresh token (Codex should provide the exact command/script to run for this).
2. In the ChatGPT RBB Project settings, add the Google Drive app and link the new `RBB Cockpit Export/context.json` file once it exists.
3. Decide separately (not blocking this work) what to do about the existing incidental Drive mirror.
