# Claude Response — RBB Defect: Explicit User Updates Are Not Automatically Promoted

**Date:** 2026-08-20
**Responding to:** RBB's defect report (forwarded via chat, same date)

## Verdict: approve the policy, with three conditions — and one correction to the "Required Fix"

The distinction RBB draws is right and worth codifying: **explicit user assertion → auto-promote; RBB's own inference → confirm first.** Added to `CANONICAL_REGISTRY.yaml` as `global_policies.mutation_authorization`, effective now, regardless of which routing option you pick below.

### Condition 1 — ambiguous referents still get a question, not a guess

"Close that opportunity," "move that follow-up" — if there's more than one candidate, resolving to the wrong record silently is worse than asking. This isn't a contradiction of "explicit statements are authoritative" — the *instruction* is explicit, but *which record it targets* isn't always. Same logic as the existing identity-match false-positive rule (never merge on a name alone; the "two J.B.s" incident). Auto-promote applies once the target is unambiguous.

### Condition 2 — sequence by domain authority, not all-at-once

Loops (`mutations.py`/`eolms.py`), the weekly plan, personal log, and decisions/sessions already have a single clear mutation owner — safe for auto-promote now. Accounts, opportunities, and strategic theses are still `distributed_consolidation_pending` in the registry (no single canonical store — `active_threads.yaml`, `loop_ledger.md`, `account_intelligence/`, `campaigns/` all compete). "That opportunity is dead, close it" is exactly the kind of statement that could land in the wrong store or miss a linked record today. Auto-promote for these domains once the consolidation sequence from the architecture review (opportunities → accounts → decisions → theses) has actually run — not before.

### Condition 3 — a reported "persisted" status must stay independently checkable

This isn't hypothetical caution — this project has two documented cases (`closeLoop`, `ingestExecutiveDeclaration`) of the GPT reporting a write as confirmed when the endpoint was never actually called, caught only by checking `request.log` against the chat transcript. Removing the "please confirm" step removes a moment where a human might have caught that gap. In exchange, two things need to hold going forward: every auto-promoted mutation gets a receipt (per the existing `persisted` / `blocked_conflict` / `skipped_duplicate` vocabulary), and — per your own Acceptance Criteria #8 — the change must be checkable by confirming it shows up in the next regenerated `context.json`. Treat that as the standing verification, not just a nice-to-have.

## Correction: this can't be built as a Project-side fix

The "Required Fix" pipeline — classify → reconcile → persist → regenerate → report — needs a live write call at the "persist" step. **The ChatGPT Project has no Actions/API access; this was confirmed directly against current ChatGPT docs earlier in this thread, and it's the same constraint RBB's own "Current Failure" section names.** No amount of registry policy or prompt engineering inside the Project changes that — it's a platform boundary, not a cockpit software defect. The good news: almost none of this needs new engineering. The mutation endpoints RBB's acceptance criteria describe mostly already exist and are already wired for Actions — `addLoop`, `closeLoop`, `openThread`, `closeThread`, `processOpportunityIntake`, `processOpportunityUpdate`, `confirmProposal`, and the identity-match confirm/reject pair used today. They're just not reachable from inside a Project conversation.

That leaves two real paths, and they're not close in cost:

1. **Route explicit business-state-change conversations through the Custom GPT instead of the Project.** The Custom GPT already has Actions wired to nearly everything this policy needs. Effectively free — no new build, just where the conversation happens. The Project would stay a read/advisory surface: it can tell you what it *would* persist and why, but the actual persist call happens once you're talking to the Custom GPT.
2. **Build a write bridge for the Project itself**, using a connector the Project does have (Gmail or Drive are both already enabled per your Codex config) to relay a "pending assertion" that a script on this side drains and runs through the promotion pipeline. This makes the Project itself capable of triggering writes, but it's real engineering, and it's not same-turn — there's a poll/drain delay between "RBB says it logged this" and it actually landing.

I'm not picking between these for you — it's a real tradeoff between "zero build cost, but you have to be in the right chat" and "bigger build, but it works from wherever you're already talking to RBB."

## Decision (Todd, 2026-08-20): route to the Custom GPT

No write bridge gets built for the Project. Two follow-ups this implies:

1. **RBB Project instructions need a correction, not just an addition.** Today the Project can only say a change is `proposed_write_pending_confirmation`, which is the wrong status for something the user explicitly stated — that status means "RBB's own inference, needs confirmation," not "correct content, wrong execution channel." Add to the Project's custom instructions:

   > When the user explicitly states a fact, decision, action item, status change, or correction, treat it as authoritative (per mutation_authorization in CANONICAL_REGISTRY.yaml) — don't call it proposed_write_pending_confirmation. Since this Project cannot execute the write itself, say plainly that it's recognized and authoritative, and that persisting it requires opening Relationship Bridge 9.0 separately — then state the change in one line ready to send there, rather than making the user re-explain it from scratch.

   **Update, 2026-08-20:** tested whether `@`-mentioning Relationship Bridge 9.0 from inside an RBB-homed chat could avoid the app-switch entirely, per OpenAI's own documented combine pattern. Confirmed broken on Todd's account — the GPT never appears in the `@` picker inside RBB, sending a plain message first and pinning the GPT both made no difference. Matches an open OpenAI community bug report (Custom GPTs stopped surfacing via `@` mention after a June 2026 UI update), so this is a platform regression, not a setup mistake here. Falling back to the original, simpler design: RBB is where all threads live and where reading/review happens; persisting an explicit change requires opening Relationship Bridge 9.0 as its own separate chat. Worth re-testing the `@`-mention path occasionally in case OpenAI ships a fix — if it starts working, revisit this instruction text.

2. **The Custom GPT's own instructions need the auto-promote policy folded in** (`system/api/custom_gpt_instructions_compact_8k.md`) so it actually stops asking "should I save that?" for explicit statements. Not done in this pass — that file is already at 8,511 bytes against an 8,000-character budget per its own prior edit history (see `[[custom-gpt-kb-inventory]]`), and past edits to it required deliberately trimming other lines to make room. This needs a careful holistic pass, not a blind append, and per that memory's own standing caution: don't trust a re-paste as "done" until a real conversation confirms it in `request.log`, not just the chat reply.

## Update, 2026-08-20 (later same day): the write bridge was built, then confirmed broken — reverted to the Custom GPT

Todd asked to build Option 2 (the write bridge) after all, reasoning correctly: if persisting still requires manually re-doing something in Relationship Bridge 9.0, there's no point using RBB for that turn at all. Built and tested in full:

- `system/scripts/rbb_assertion_bridge.py` — drains `[RBB-ASSERTION]`-tagged Gmail messages, validates a structured JSON assertion (scoped to `add_loop`/`close_loop` only, per Condition 2 above), persists through the same `addLoop`/`closeLoop` API endpoints the Custom GPT would call, dedupes by `assertion_id`, verifies sender against `core.self_emails()`. 8 unit tests, all passing. Verified live against real Gmail credentials (readonly scope, already authorized — no new OAuth needed).
- `com.relationshipbuilder.rbb-assertion-bridge.plist` — LaunchAgent, 15-minute interval. Built and `plutil`-validated, **never loaded** — held back pending an end-to-end test.
- RBB's Project instructions updated with the exact assertion-composition format.

**The end-to-end test failed at the first step, twice, in two different ways:**
1. Todd asked RBB to persist a test loop. Nothing arrived anywhere in Gmail — not sent, not a draft, not in trash. RBB's own follow-up response (unprompted) pivoted to an unrelated architecture critique instead of addressing this.
2. On retry, RBB reported creating a Gmail draft with a "Review and send" link and correctly-formatted content. The link opened Gmail with **no draft present** — the claimed draft never existed.

This is the same failure class already on record for this project (`closeLoop`, `ingestExecutiveDeclaration`): the model reports an action as done, the underlying tool call never actually fires. Two independent mechanisms failed at this level within the same session (the `@`-mention combine earlier, now Gmail draft creation) — that's a pattern in ChatGPT's own tool-calling reliability on this account, not something fixable by better instructions or a third mechanism variant.

**Reverted to Option 1** as the standing, working design: RBB is where all threads live and where reading/review happens; persisting an explicit change requires opening Relationship Bridge 9.0 as its own separate chat, using its Actions — the one mechanism in this entire investigation independently verified to actually work (`request.log`-checked, multiple times). RBB's Project instructions reverted to the plain "tell the user to open Relationship Bridge 9.0" version, with the `[RBB-ASSERTION]` email-drafting paragraph removed so RBB stops generating broken Gmail links.

The bridge script and LaunchAgent are **not deleted** — they're correct and fully tested against the API side; the failure is entirely on the "does ChatGPT actually send/draft the email" side, outside anything in this repo. Worth revisiting if OpenAI's Gmail app reliability improves. Do not re-enable by loading the LaunchAgent without re-running the end-to-end test first.
