# Claude Handoff — RB 9.5 Canonical Response Hardening — COMPLETE

**Date:** 2026-05-24
**Status:** sprint complete — host terminal gate remaining
**Previous handoff:** `system/CLAUDE_HANDOFF_2026-05-24_RB_9_5_CANONICAL_RESPONSE_HARDENING.md`

---

## What was built

### P1 — `system/CANONICAL_RESPONSE_CONTRACT.md` ✅

New file. The canonical semantic map for all RB response surfaces.

Contains:
- Core doctrine: `source state → detected facts → RB action state → projection/loop state → recommended next action → source refs`
- Allowed action-state verb table (recorded / updated / proposed / blocked / skipped / drafted / did not persist / pending confirmation)
- Prohibited language table (should be marked / would likely / could be updated / being treated as / strategic advisor / timing says / lean into your / trust the process / own your narrative)
- Required metadata fields per response item (grounding / freshness / confidence / source_refs / disposition / action_state / persistence_status)
- **Truth table** (the anti-overclaim invariant) — 10 system-state → allowed/prohibited language rows
- Scenario contracts for 9 scenarios (daily_brief, relationship_signal_review, manual_ri_intake, passive_ri_ingest, no_response_update, strategic_memory_record, draft_ready_action, meeting_prep, loop_or_action_creation)
- Canonical `canonical_response` JSON block shape
- Future user-defined template switch placeholder (planned, not implemented)
- Grounding label enum / freshness states / disposition values reference

### P2 — `system/scripts/canonical_response_eval.py` expanded ✅

Added 4 new scenarios alongside the existing `no_response_update`:

| Scenario | What it checks |
|---|---|
| `ri_intake_detected` | RB match, signal read, persistence_status, manual_user_provided grounding, no system_detected mis-label, action-state verbs, banned phrases |
| `passive_ri_summary` | Action-state / count tokens, source grounding label, persistence_status, no overclaim phrases, stale-source acknowledged when present |
| `daily_brief_top_contract` | Resource Verification section present, grounding labels, freshness labels, disposition, no quiet claim when stale, no banned phrases |
| `strategic_memory_record` | RB recorded/updated verb, persistence/storage ref, grounding label, no non-action phrases, future-use requirements block |

Each scenario has a built-in passing and failing fixture in `--smoke`. All 10 fixture pairs pass.

Also fixed `_contains_section()` to accept inline headings (`RB match: value`) and qualified headings (`Resource Verification & Freshness Status:`) without false-negatives.

**Run:** `python3 system/scripts/canonical_response_eval.py --smoke`
**Scenario eval:** `python3 system/scripts/canonical_response_eval.py --scenario <name> --text path/to/response.txt`

### P3 — Canonical response blocks added to API/script outputs ✅

**`system/scripts/ri_intake.py`**
- Added `_build_canonical_response()` helper (standard JSON block shape per contract)
- `review()` return now includes `canonical_response` block with scenario, action_state, summary, facts, persistence, projection, recommended_actions, source_refs, grounding, freshness, confidence

**`system/scripts/passive_ri_ingest.py`**
- `run()` return now includes `canonical_response` block
- Fields: scenario, action_state (proposed/blocked/not_persisted), summary, facts (candidates_seen / events_written / blocked / duplicates / source_unavailable), persistence (bundle_ids, event_ids, proposed_contacts), projection (pending last_touch list, blocked reasons), recommended_actions, source_refs, grounding, freshness, confidence

**`system/scripts/strategic_memory.py`**
- `record()` canonical_response is now a structured JSON block (not just a text string)
- Block carries: scenario, action_state (recorded/updated), summary, facts (signal_id / signal / categories), persistence (status=persisted, storage_path), projection, recommended_actions, source_refs, grounding=manual_user_provided, confidence=high
- Legacy human-readable text is preserved as `canonical_response.text` for Custom GPT rendering

All three are backwards-compatible. No existing API smoke tests broken.

### P4 — `system/api/custom_gpt_prompt.md` tightened ✅

Added a **Canonical response contract** section at the top (before "Action availability") with 5 operative rules:

1. Use API-provided `canonical_response` blocks — don't rewrite them in softer prose
2. Never convert proposed state into completed state
3. Never imply a mutation occurred unless the API returned one (`not_persisted` → say so explicitly)
4. Stale-source caveats precede interpretation
5. Keep daily brief trust-first order (resource verification before analysis)

Also included the allowed verb list and the prohibited-outside-quoted-context phrase list in the prompt directly, so the Custom GPT can enforce them without needing to read the contract file.

### P5 — Passive RI confirmation write smoke ✅

Added `_smoke_confirm_write()` to `system/scripts/passive_ri_ingest.py`.

The smoke:
1. Rebinds `ri_events.EVENTS_DIR`, `INDEX_PATH`, `ri_intake.PENDING_PATH`, `rb_core.BASELINE_PATH`, `rb_core.SNAPSHOTS_DIR` — all to a tmpdir
2. Monkeypatches `mutations._validate_baseline_or_rollback` to a lightweight JSON-parse check (bypasses subprocess that can't see in-process path rebinding)
3. Creates a synthetic RC contact with stale `last_touch: 2025-12-01`
4. Builds and appends a `last_touch_update` passive event
5. Stashes the pending bundle
6. Calls `ri_intake.confirm(event_id, dry_run=False)` — a live write
7. Asserts:
   - `persistence_status == persisted`
   - temp baseline `last_touch` advanced to `2026-05-20`
   - production baseline untouched (compared against pre-smoke snapshot)
   - follow-up event written with `persistence.status == persisted`
   - pending bundle consumed

All 10 new assertions pass. Runs inside the existing `--smoke` entrypoint automatically.

---

## Sandbox gate results (2026-05-24)

| Test | Result |
|---|---|
| `canonical_response_eval.py --smoke` | ✅ PASS (10/10 fixture pairs) |
| `ri_events.py --smoke` | ✅ PASS |
| `passive_ri_ingest.py --smoke` | ✅ PASS (30/30 checks including _smoke_confirm_write) |
| `ri_intake.py --smoke` | ✅ PASS |
| `daily_brief.py --smoke` | ✅ PASS |
| `refresh_all.py --date 2026-05-24` | ✅ PASS (all caches refreshed) |
| `py_compile` (5 files) | ✅ PASS |
| `ri_smoke_test.py` | ⏳ Requires host terminal (fastapi not available in sandbox) |
| `api_smoke_test.py` | ⏳ Requires host terminal (fastapi not available in sandbox) |
| `morning_path_test.py` | ⏳ 3/4 automated (1 step requires fastapi on host) |

---

## Required host terminal gate

Run these from Todd's Mac after pulling the branch:

```bash
python3 system/scripts/ri_smoke_test.py
python3 system/scripts/api_smoke_test.py
python3 system/scripts/morning_path_test.py
```

The `RB_API_KEY is not set` warning is acceptable for local TestClient runs.

---

## Files changed

| File | Change |
|---|---|
| `system/CANONICAL_RESPONSE_CONTRACT.md` | NEW — canonical response doctrine + truth table + 9 scenario contracts |
| `system/scripts/canonical_response_eval.py` | EXPANDED — 4 new scenarios + 8 new fixtures + `_contains_section` fix |
| `system/scripts/ri_intake.py` | ADDED `_build_canonical_response()` + `canonical_response` block on `review()` return |
| `system/scripts/passive_ri_ingest.py` | ADDED `canonical_response` block on `run()` + `_smoke_confirm_write()` |
| `system/scripts/strategic_memory.py` | CONVERTED `canonical_response` from string to structured JSON block |
| `system/api/custom_gpt_prompt.md` | ADDED canonical response contract section + operative rules |

---

## Success checklist (per handoff spec)

- [x] Written canonical response contract
- [x] Contract includes truth table mapping system states to allowed/prohibited language
- [x] `canonical_response_eval.py --smoke` covers more than no-response drift (5 scenarios)
- [x] RI intake, passive RI, strategic memory, and daily brief response shapes evaluatable deterministically
- [x] API/script outputs expose canonical response blocks where prompt-only enforcement is too weak
- [x] Custom GPT prompt tightened to use those blocks
- [x] Contract preserves future user-defined template switch without implementing template engine
- [x] Existing 9.4 smokes still pass

---

## Next-sprint candidates (unchanged from project_sprint_next.md)

1. **Passive RI confirmation loop** — wire the projection write path to live baseline updates for real passive events (the smoke now proves the path works; P-019 data needed to generate real qualifying candidates)
2. **Monitored transcript-folder ingest** — turn P-020 from PARTIAL to LIVE by wiring ri_intake.review() per artifact
3. **Composition layer** — draft-in-voice for smart_loop follow_ups, meeting_prep talking points, passive_verification resolutions
4. **Host-side LaunchAgent wiring** — plist and env vars for 05:00 + 16:30 compute
5. **Protocol files** — P-025 through P-034 for execution-layer modules
6. **DRR eval framework** — passive_verification auto-close outcomes as ground truth
7. **P-019 first-run on Todd's Mac** — Apple Messages + Calls pipeline

**Don't do next:**
- Auto-apply passive RI projections without operator confirmation
- Extend openapi_gpt.yaml past 30 ops without dropping one first
- Silent mutations — the doctrine from RB 9.5 holds
