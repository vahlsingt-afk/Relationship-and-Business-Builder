# Protocols

Numbered, deterministic procedures any sufficient AI can execute on the RB file set. The point of this folder is **vendor-portability** — if Claude is busy, ChatGPT (or any other model) should be able to read a protocol and produce identical-shape output.

## Conventions used in every protocol

Each protocol file follows this structure:

1. **Purpose** — one sentence on what the procedure produces.
2. **When to run** — triggers.
3. **Read set** — exactly which files the session needs to load before executing. No extras.
4. **Inputs** — what the operator (or trigger) supplies.
5. **Steps** — numbered, sequential. Each step says what to do, what to write, and what to validate.
6. **Output contract** — the files this procedure writes or modifies, and the shape of each.
7. **Failure modes** — known ways this procedure can go wrong, and what to do.
8. **Voice** — Todd's communication style applies to any operator-facing prose; data updates are silent.

## Why numbered protocols matter

The current ARCHITECTURE.md has procedures described in prose ("ingest the file, produce IBs, recommend next steps"). Prose works for a session that already loaded the operator profile and tenets. It does not work for cross-model portability — different models interpret prose with different defaults.

A protocol file is **the same operation written tightly enough that any model executes it the same way**. The tradeoff is some duplication with ARCHITECTURE.md / SCHEMAS.md; the win is determinism.

## Current protocols

| ID | File | Backed by | Purpose |
|---|---|---|---|
| P-001 | `P-001_daily_brief_regen.md` | `system/scripts/daily_brief.py` | Regenerate today.md + MANIFEST.md from current state. |
| P-002 | `P-002_linkedin_ingest.md` | (prose only) | Ingest a LinkedIn data export, snapshot, delta, write baseline + delta report. |
| P-003 | `P-003_baseline_validation.md` | `system/scripts/validate_baseline.py` | Schema + integrity check on `baseline_index.json`. |
| P-004 | `P-004_rc_card_gap_detection.md` | `system/scripts/gap_detection.py` | Missing cards, orphan cards, no-`last_touch`, contact-field gaps. |
| P-005 | `P-005_loop_parsing.md` | `system/scripts/loop_parser.py` | Bucketed view of `loop_ledger.md` (overdue / today / week / future / closed). |
| P-006 | `P-006_network_gap_scoring.md` | `system/scripts/network_gap.py` | Company clusters lacking inner-tier RC anchors. |
| P-007 | `P-007_drr_scoring.md` | `system/scripts/drr_score.py` | Dynamic Relationship Relevance score (v0 prototype). |
| P-020 | `P-020_conversation_artifact_ingestion.md` | planned | Ingest transcripts, notetaker recaps, and chat logs into relationship intelligence before daily brief. |
| P-021 | `P-021_ri_event_sourcing.md` | planned | Make RI events the authoritative timeline and project current relationship state from them. |
| P-022 | `P-022_legacy_thread_transfer.md` | planned | Mine RB 8.0 transfer documents for architecture decisions without treating them as canonical state. |
| P-031 | `P-031_strategic_operator_intelligence.md` | `system/scripts/strategic_operators.py` | Persistent strategic-operator entity lane, categorical scoring, proximity reconciliation, and daily-brief operator movements. |
| P-032 | `P-032_native_chatgpt_task_delivery.md` | ChatGPT scheduled task | Native 05:05 CT daily brief delivery via ChatGPT task result message. |
| P-033 | `P-033_linkedin_messaging_export_ingest.md` | `system/scripts/linkedin_messaging.py` | LinkedIn messaging export CSV ingest into inbox cache. |
| P-034 | `P-034_loop_lifecycle_autopilot.md` | `system/scripts/loop_autopilot.py` | Morning/midday/closeout loop lifecycle management. |
| P-035 | `P-035_chatgpt_task_delivery_verification.md` | `system/scripts/task_delivery_check.py` | Verify ChatGPT task delivery health; weekly spot-check. |
| P-036 | `P-036_ri_assessment_and_trust_display.md` | `relationship_signals.py`, `passive_ri_ingest.py`, `daily_brief.py` | **RB 9.9** — RI assessment doctrine and trust display hardening. Governs the 7-status contract (recorded/proposed/blocked/duplicate/irrelevant/unavailable), confidence gating, banned trust-claim phrases, and display rules. |

(Add new protocols as procedures stabilize. Don't promote ad-hoc work to a protocol until it's been run 2–3 times and the shape is stable.)

## Script-backed vs prose-only protocols

A **script-backed** protocol has a deterministic implementation in `system/scripts/` and is what we mean by an "operating layer." A **prose-only** protocol is a tight enough specification that any sufficient model can execute it the same way, but the inner steps still depend on the model doing the work.

Goal over time: every numeric or structural step should be script-backed. The model is the strategist and the operator, not the calculator and the state machine.
