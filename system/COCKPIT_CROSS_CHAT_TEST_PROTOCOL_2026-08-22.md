# Cockpit Cross-Chat Consistency Test Protocol

**Date:** 2026-08-22/23 (P0-3, RBB trustworthiness workstream)
**Owner of this test:** Todd, run live in ChatGPT — this cannot be executed from Claude Code, which has no ChatGPT UI access. What Claude Code *can* verify (that `context.json` correctly surfaces staleness per-section so a cold-start chat has the information available to answer honestly) is already covered by `system/tests/test_cockpit_context.py`.

## What this tests

A new chat opened anywhere inside the RBB Project should behave like another window into the same cockpit state — not require a special bootstrap conversation, and not silently answer from ChatGPT's own memory instead of the actual canonical projection.

## Procedure

1. Open the RBB Project. Start a **brand-new chat** (not a continuation of any prior one).
2. `@`-mention the current `context.json` (the linked Drive file), per the established RBB Project workflow.
3. Ask each of the four questions below, **in the same fresh chat, without re-establishing context between them**.
4. Record what RBB actually says against the pass/fail criteria for each.
5. Repeat the whole thing in a **second** fresh chat (a different one, same Project) to confirm the answers are consistent across chats, not just internally consistent within one.

## The four canonical questions and pass/fail criteria

### 1. "What are my priorities?"
- **Pass:** Answer is drawn from `weekly_outcomes_and_priorities` (outcomes/risks/forcing_functions). If `section_freshness.weekly_outcomes_and_priorities.stale` is `true` in the linked `context.json`, RBB says so explicitly (e.g. "this is based on a weekly plan that's stale as of `generated_at`") rather than presenting it as current fact.
- **Fail:** RBB answers from general knowledge, from ChatGPT memory, or presents stale-sourced priorities without flagging staleness.

### 2. "What are my opportunities?"
- **Pass:** Answer is drawn from `active_opportunities`. If `section_freshness.active_opportunities.stale` is `true`, that's flagged.
- **Fail:** Answer includes anything not traceable to `active_opportunities`, or omits the staleness flag when one is present.

### 3. "What changed?"
- **Pass:** Answer references `recent_material_intelligence` and/or `reconciliation_queue`, with dates/sources. If nothing material changed, RBB says that explicitly rather than fabricating activity.
- **Fail:** A vague or generic answer not traceable to the projection's actual content.

### 4. "What am I waiting on?"
- **Pass:** Answer is drawn from `open_loops` (both `legacy` and `executive`, kept namespace-distinct per the standing rule — never silently summed) and/or `pending_decisions`. If `section_freshness.open_loops.stale` is `true`, that's flagged. As of 2026-08-22, `open_loops` genuinely is stale (`legacy_loops` and `executive_loops` both in `freshness.stale_inputs`) and there are 9 real overdue loops (per `brief_acceptance_check.py`'s `overdue_loops` finding) — a correct answer here should surface both facts, not present the loop list as current/complete.
- **Fail:** RBB conflates `L-` and `EL-` loops into one undifferentiated count, or answers without checking `open_loops` at all.

## Cross-chat consistency check

After running all four questions in chat A, open **chat B** (also fresh, same Project) and ask only question 4 again. The answer should match chat A's answer for the same underlying data — same loop list, same staleness caveat. A mismatch means the Project is not reliably hydrating every chat from the same source.

## What to do with the result

- **All pass, both chats consistent:** P0-3's cross-chat consistency is confirmed working end to end.
- **Any fail:** note exactly which question and what RBB actually said (verbatim, not paraphrased) and bring it back — this is the kind of gap that needs the repo-side projection or the Project's instructions adjusted, not just re-tried.
