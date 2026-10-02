# RBB Skills-Shaped Architecture — Scope

Date: 2026-09-23
Companion docs: `system/RBB_TOKEN_EFFICIENT_ARCHITECTURE_SCOPE_2026-09-19.md` (Phase 1-4 plan this
supersedes Phase 2 of), `system/RBB_MODEL_TIER_POLICY_2026-09-19.md` (the Tier 0 classification
concept this reuses)

## The question Todd asked

"Should we be looking at designing RBB around skills instead of the large instruction sets we are
using? ... scope what this might look like, why this would be a better way to think about the
structure of the program, and if it simplifies API usage, token costs and code."

Short answer: yes, worth doing, and it's a better fix than Phase 2's original plan (delete unused
tools) — but "skills" here means borrowing the *pattern* (load only what a turn needs, on demand),
not migrating rbb-chat off OpenAI onto Claude's actual Skill mechanism. That migration is a
separate, already-parked decision (see `project_open_decisions_ledger` memory item 18) and nothing
below requires reopening it.

## What's actually happening today (measured, not assumed)

Confirmed by reading `rbb_chat.py`'s real call sites: **every** `client.responses.create()` call —
the first turn, every `previous_response_id`-chained turn, and even the internal follow-up call
made after a tool result comes back mid-turn — passes the full `instructions=_current_instructions()`
and `tools=TOOLS` every single time. `_current_instructions()` is 100% static (a one-line date
prefix plus the whole `custom_gpt_instructions_compact_8k.md` file, unconditionally). There is zero
conditional loading anywhere in the current design. A turn that makes two tool calls pays this
resend twice, not once.

Measured today:

| | chars | approx tokens |
|---|---|---|
| `custom_gpt_instructions_compact_8k.md` | 55,496 | ~13,900 |
| `TOOLS` schema (123 live tools, JSON) | 124,450 | ~31,100 |
| **Total per `responses.create()` call** | **179,946** | **~45,000** |

(For reference, Phase 1's doc cited "~91KB" for this same resend on 2026-09-19 — the real number
has roughly doubled since, because the competitive-intel/document-generation suite shipped in that
same window. The cost problem has gotten worse since Phase 1 was scoped, not better.)

### Where that weight actually goes

Grouped the 123 live tools (JSON schema size) and the compact_8k.md instruction bullets by the
workflow they belong to, and joined against real historical usage from `system/audit/*.jsonl`
(every real tool call rbb-chat's orchestrator has ever made — the same ground-truth source Phase 2
used):

| Category | % of tool schema | % of instructions | Real calls ever |
|---|---:|---:|---:|
| competitive_intel (battle cards, competitor CRUD, market share) | 19.0% | 18.6% | 1 |
| account_plan_family (Account Plan/Green Sheet/Win Plan/RFP Plan) | 13.3% | 15.4% | 26 |
| relationship_family (Relationship Card/Plan, Inner Circle, Referral Network) | 10.6% | 11.9% | 0 |
| confirm_review_queues (action queue, routine research, tech-stack proposals) | 13.2% | 6.4% | 11 |
| capture_intake (ingest, upload, capture processing) | 11.7% | 16.6% | 103 |
| blue_sheet_account_status | 10.9% | 8.6% | 42 |
| loops_relationship_touch (closeLoop, touchContact, declarations) | 7.4% | 4.5% | 71 |
| query_engine | 4.0% | (in core) | 50 |
| master_account_plan | 3.8% | 3.2% | 10 |
| opportunity_job / macro_signal | 2.8% | 1.0% | 13 |

Roughly **55% of every turn's resend weight** (competitive_intel + account_plan_family +
relationship_family + confirm_review_queues) is spent on categories that together account for
**38 of ~372 real tool calls ever** — about 1 turn in 10. That's the concrete case: not "trim what
looks unused" (Phase 2's original, riskier framing — most of this *is* real, wanted, recently-shipped
capability, just not needed on most turns), but "stop paying for it on the 9 turns out of 10 that
don't touch it."

## Proposed architecture

**Core bundle — loaded every turn, no exceptions.** RULE 0 (fetch, don't generate), the
Non-Negotiables (anti-fabrication, mutation-authorization policy), bootstrap, and the
highest-frequency tools: `getDailyBrief`/`getDailyBriefPart2`, `getCard`, `getLoops`, `queryEngine`,
`ingestContent`, `ingestExecutiveDeclaration`, `closeLoop`, `redateLoop`, `touchContact`,
`processRelationshipIntake`, `confirmProposal`, and the capture-processing cluster the brief itself
depends on (`getCapturesPending`/`getCapturesProcessed`/etc., since those run on every brief render
regardless of what else the turn is about). This is roughly the `core_rules_bootstrap_routing` +
`loops_relationship_touch` + `query_engine` + brief-relevant slice of `capture_intake` — call it
~25-30% of today's total weight.

**Skill bundles — loaded only when matched.** One per remaining category: `competitive_intel`,
`account_plan_family`, `relationship_family`, `blue_sheet_account_status`, `master_account_plan`,
`confirm_review_queues`. Each bundle pairs an instruction fragment (the relevant bullets, extracted
from today's monolithic file) with its tool subset (the relevant entries already sitting in
`rbb_chat_tools.py`'s `TOOLS`/`OPERATIONS`, no new tool code needed — just a mapping of which names
belong to which bundle, which the categorization above already produced concretely).

**Selection: deterministic, not a model call.** This is the load-bearing design decision. Two
options exist:

1. *Cheap classifier LLM call* (a genuine Tier-0 use per the model tier policy doc) — adds a real,
   billed extra API round-trip to every turn, working against the whole point of this change.
2. *Deterministic keyword/regex matching* against the incoming message — zero added API calls, same
   shape as the precedent already shipped in Phase 1 (the literal `"capture:"` prefix match). Each
   skill gets a small set of trigger terms (e.g. `relationship_family`: "referral", "intro path",
   "inner circle", contact-name-plus-"relationship plan"; `competitive_intel`: "battle card",
   "market share", competitor names, "vs Genius"). A turn can match zero, one, or several skills.

Recommend (2). It keeps this change purely a payload-size optimization with no new latency, no new
billed calls, and no new failure mode beyond "did the keyword list miss something" — which is a
tunable, inspectable list, not a model's judgment call.

**The one real risk this design has to respect:** rbb-chat calls `responses.create(..., tool_choice="required")`
on every turn — the model is *never* allowed to just reply in plain text; it must call some tool.
If skill-matching under-includes and the actual right tool isn't in that turn's set, the model is
structurally forced to reach for the closest wrong tool rather than say "I don't have that" — and
this codebase already has a documented, live-confirmed incident of exactly that failure shape (the
`submitCapture`-with-a-guessed-file_id incident named in the compact_8k instructions themselves,
RB-2026-08-28). This means keyword matching for this design **must be deliberately generous** —
biased toward over-including a skill on any plausible signal, never toward precision. The cost of
over-inclusion here is a few thousand extra tokens; the cost of under-inclusion is a forced,
potentially fabricated tool call. Any implementation of this has to treat that asymmetry as a hard
rule, not a tuning nicety.

**Fallback when nothing matches (a genuinely novel/ambiguous request).** First version: load
everything, exactly like today — the safe default, not a new failure mode, just the un-optimized
path for the minority of turns that need it. A future refinement (closer to how Claude's own Skill
tool actually works — list available skill *names* cheaply, let the model ask for one by name before
answering) is a legitimate next step once real miss-rate data exists to justify the added round-trip,
but is not part of a first version.

## Why this is a better way to think about the program's structure

Today's instruction file and tool list are organized the way they were *built* — additively, by
whatever shipped that week — not by how the program actually gets *used*. The category boundaries
above aren't invented for this doc; they're the same domains this project's own memory and commit
history already group work by ("the customer-side suite," "the competitive-side suite," "the
relationship-side family" — see the 2026-09-07/08 build trail). A skills-shaped design makes that
existing mental model the literal code structure: each domain becomes one bundle with one owner file,
instead of one bullet buried in an 8,000-word document next to fifteen unrelated ones. That's a real
structural win independent of token cost — it's what Phase 2's step 1 bumped into directly: deciding
whether a tool was safe to touch required first reading the entire monolithic file to understand what
depended on what, because nothing marked the boundaries.

## Honest cost/complexity answer

**Token cost: yes, substantially, on the common path.** A turn that only needs the core bundle
(most turns, per the usage data — brief fetches, queries, loop closes, touches) drops from ~45K
tokens to roughly 12-15K. A turn that needs one rare skill still saves meaningfully (core + one
bundle vs. everything). This is the actual fix for the cost problem Phase 1-4 was opened to solve —
more direct than Phase 2's tool-deletion approach, and it doesn't require deciding a single tool is
safe to remove.

**API usage: neutral, if kept deterministic.** Same one call per turn as today (or two, for a
tool-calling turn) — just a smaller payload on each. Only gets worse if a classifier LLM call is
added; recommend against that for exactly this reason.

**Code: a real but bounded increase in files/moving parts, in exchange for the runtime win.** This
adds: a skill registry (bundle name → tool names + instruction fragment path), a keyword-match
dispatcher, and N instruction-fragment files replacing 1 monolithic file. The monolithic file's
existing cross-references between adjacent bullets (e.g. "a DIFFERENT tool/file than
`getBriefDownloadLink`" warnings that span what would become two different bundles) need to survive
the split without silently losing the comparison — those warnings exist because of real, named past
incidents, not decoration. This is real design work, not a mechanical split. Net: more source files,
smaller runtime payload, and it replaces Phase 2's open-ended "which of the 73 new tools is safe to
delete" question with something principled instead of another manual review pass every few weeks.

## Relationship to the existing Phase 1-4 plan

This **replaces** Phase 2 as scoped ("narrow rbb-chat's tool schema by deleting unused tools") —
Phase 2's step 1 already found that framing doesn't work well against a codebase shipping new,
unproven capability every week; contextual loading solves the same cost problem without ever
requiring that judgment call. It also **is** the concrete shape of Phase 4 ("shared routing policy
... a single shared config any surface consults" — today just a spec). Recommend: fold this in as
the real Phase 2/4 design, supersede the delete-based framing, keep Phase 2 step 1's actual 3-tool
cut (still valid, unrelated mechanism).

## Open questions needing a decision before building

1. **Bundle boundaries** — the 10 categories above are a reasonable first cut (grounded in real
   usage clustering), but "is `master_account_plan` its own bundle or folded into
   `blue_sheet_account_status`" is a real judgment call, not automatable.
2. **Keyword-list ownership and drift** — who updates a skill's trigger terms when a new workflow
   ships. Needs the same discipline `validate_kb_consistency.py` already enforces for tool mentions,
   extended to skill-trigger coverage.
3. **Does the Custom GPT Capture Relay (Phase 1) need this too?** It's a single-operation schema
   already, so likely not — worth confirming once it's live-verified.
4. **How aggressive should "generous" keyword matching be** — this is directly tunable against real
   miss-rate data once built, but the starting bias (favor inclusion) should be a deliberate choice
   Todd signs off on, not an implementation detail.

## Recommended next step

Not a build yet — this is the scope, per your ask. If you want to move on it: start with ONE bundle
end-to-end (suggest `relationship_family` — zero real calls ever, so there's no risk of an
in-flight workflow breaking, and it's small enough to prove the mechanism before doing the other
five) as a spike, measure the real token delta on real turns, then decide whether to do the rest.

## Spike — BUILT 2026-09-23

Built the `relationship_family` bundle end to end, per the above:

- `system/scripts/rbb_chat_skills.py` — the registry (`SKILL_BUNDLES`), deterministic
  keyword matcher (`match_skills`, deliberately generous per the `tool_choice="required"`
  constraint above), and `build_turn_payload()`, the one function `rbb_chat.py` now calls.
- The 5 Relationship Card/Inner Circle/Referral Network (×2)/Relationship Plan bullets moved out of
  `custom_gpt_instructions_compact_8k.md` into a new standalone fragment,
  `system/api/custom_gpt_skill_relationship_family.md` — appended to core instructions only on a
  match, never present otherwise.
- `rbb_chat.py`'s `_run_chat_turn()` computes `(instructions, tools, matched_skills)` once per turn
  and reuses it across all `responses.create()` calls in that turn (initial, chained,
  tool-output-followup, and the empty-reply nudge) — a bundle matched at turn start doesn't vanish
  mid-turn. `matched_skills` is now logged alongside real `input_tokens`/`cost_usd_estimate` in the
  existing `openai_usage.jsonl` usage log (`_log_usage` gained an `extra` param), so the real
  savings are measurable from live traffic, not just estimated.
- `validate_kb_consistency.py`'s `KB_FILES` extended to include the new fragment, so the 16
  relationship-family tools don't false-positive as "undocumented" just because their bullets moved
  out of the always-on file.
- 10 new tests (`test_rbb_chat_skills.py`): bundle-vs-live-schema drift guard, matched/unmatched
  payload shape, the never-drops-a-non-bundled-tool invariant, generous-matching coverage against
  8 realistic phrasings plus a false-positive check against 5 unrelated ones, fragment-completeness,
  the core-file-actually-shrank regression guard, and the usage-log wiring. Full suite green.

**Measured, not estimated** (`build_turn_payload` run against the real live instructions/tools):

| | chars | ~tokens |
|---|---:|---:|
| Old: always-full payload | 173,581 | ~43,400 |
| New: turn with no relationship match | 160,375 | ~40,100 |
| New: turn that matches | 180,718 | ~45,200 |

**~7.6% reduction (~3,300 tokens) per call on any turn that doesn't touch relationship-side
artifacts** — which, per the real audit-log usage data this whole design is grounded in, is
effectively every turn so far (zero historical calls to this bundle). The rare matching turn costs
marginally more than today (fragment overhead), an acceptable trade for the much larger common-case
savings.

**Not yet done — needs real traffic before deciding on the other five bundles:** this is code-complete
and tested against synthetic/pure-function cases, but hasn't yet seen a real live conversation
through rbb-chat. Before building `competitive_intel`/`account_plan_family`/`confirm_review_queues`/
`blue_sheet_account_status`/`master_account_plan` the same way, let this run against real traffic for
a stretch and check `openai_usage.jsonl`'s new `matched_skills` field for: (a) real measured token
savings matching the estimate above, (b) zero false negatives — no real turn that needed a
relationship-family tool and didn't get it (would show up as a forced wrong-tool call, the exact
risk this whole design is built around avoiding).
