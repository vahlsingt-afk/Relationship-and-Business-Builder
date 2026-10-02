# RBB Model Tier Policy — 2026-09-19

## Principle

Every RBB workflow uses the cheapest model tier that can plausibly do the task, and escalates to a more expensive tier only when a concrete, deterministic signal says the cheap tier failed — never by default assumption, never on a vibe, never because escalating "feels safer." A flyswatter unless the job actually needs a sledgehammer.

This generalizes the one precedent already in the code (`rbb_chat.py`'s `CHEAP_MODEL` for OCR/narration, premium model reserved for orchestration) from two hardcoded special cases into a policy every current and future surface — the capture relay, rbb-chat, the Codex/document bridge, and anything built later — reads from the same place, rather than each surface inventing its own ad hoc tiering.

**This policy governs model selection only.** It has no bearing on mutation trust — the confirm-before-mutate / review-first discipline (Todd's canonical mutation policy) applies identically regardless of which tier produced a candidate. A Tier 0 classification and a Tier 3 synthesis are both just proposals until something already in the codebase's review-first path confirms them. Cheaper does not mean less trusted; it means less reasoning was needed for that specific task.

## Tiers

| Tier | Shape of task | Current candidate | Cost posture |
|---|---|---|---|
| **0 — near-free** | Pure classification with a small, closed answer space: is this a capture or a live question, what `capture_type` is this, is there anything here worth processing at all | Cheapest available chat-completion model (today: `gpt-4o-mini`-class) | Should approach zero marginal cost per call; if it doesn't, the task is mis-tiered |
| **1 — cheap** | Extraction / summarization with a known target shape: OCR a PDF, pull structured fields out of an Excel sheet, deep-research fetch-and-summarize, first-pass draft of one document section | `gpt-4o-mini`-class (already `CHEAP_MODEL` in `rbb_chat.py`) | Cheap and short — bounded input, bounded output, single call, no multi-turn loop |
| **2 — mid** | Synthesis requiring some judgment across more than one source: correlating a few pieces of evidence, drafting a full multi-section document that has to hang together, not just extract | A capable-but-not-frontier model (today: mid-tier OpenAI/Claude offering) | Moderate — still bounded turns, but real reasoning happening |
| **3 — premium** | Live orchestration with tool selection over current data, anything shipped without a second pass, or genuinely hard structural/dev reasoning | Frontier model (today: `gpt-5.5`-class for OpenAI-side orchestration; Claude Opus/Sonnet for structural dev work) | Expensive by design — reserved for where it's actually earned |

Vendor is pluggable per tier, not hardcoded to this table. The policy names *today's* real candidates; a future free or third-party model at Tier 0 or Tier 1 is a config swap, not a redesign — see "Deferred" below.

## Task → tier mapping (current three workflows)

| Workflow | Task | Tier | Escalation trigger |
|---|---|---|---|
| Capture relay | "Is this a drop-off or a live question?" | 0 | N/A — misclassification here should be rare and cheap to be wrong about; caught downstream by a human seeing the wrong thing land in the wrong place, not by escalating the classifier itself |
| Capture relay | Extract text from an uploaded PDF/Excel/image | 1 | Extraction produces empty/near-empty text from a non-empty file → retry once at Tier 2 before failing safely to the watched-folder fallback (same fallback already built for RB-DEFECT-068) |
| Capture relay | Tag `capture_type` for the extracted text | 0 | N/A — same reasoning as classification above; a wrong `capture_type` is a labeling error the morning sweep already tolerates (see `queueCaptureText`'s explicit-override design), not a correctness failure worth paying more to avoid |
| Deep research (Codex-driven today) | Fetch + summarize a source for the intelligence cycle | 1 | Summary fails a schema/shape check (missing required fields, empty where evidence clearly exists) → retry once at Tier 2 |
| Document bridge | First-pass draft of one RFP section from source material | 1 | Draft is shorter than a sane floor for the section, or omits a required field the source material clearly contains → retry at Tier 2 |
| Document bridge | Assemble/reconcile a full multi-section document | 2 | Sections contradict each other, or the assembled document fails a structural check (missing a required section, malformed table) → escalate to Tier 3 for final synthesis only, not a full redo |
| Document bridge | Final polish/verification before the document is presented as done | 3 | N/A — this step exists specifically because it's the one place a second pass is non-negotiable |
| rbb-chat query engine | Answering a live question against current account/intelligence data | 3 | N/A — this is deliberately the premium path by design; a live query that needs read-only lookup with no reasoning (e.g. "what's X's phone number") is a candidate to demote to Tier 1 later, but only after real usage data shows that's a meaningful share of query traffic, not assumed upfront |
| Structural/dev work | Anything like this conversation | 3 (Claude, human-initiated) | N/A — out of scope for automated tiering; a person decided to spend premium tokens here on purpose |

## Escalation mechanics

1. **One escalation, one step up, by default.** A Tier 0 task that fails escalates to Tier 1, not straight to Tier 3. A Tier-1-failing-again task surfaces as needing attention rather than escalating again automatically — runaway cost from a repeatedly-failing task is worse than a task that waits for a human to look at it once.
2. **The trigger must be deterministic**, not the cheap model's own self-reported confidence. A model saying "I'm not sure" is a weaker signal than a schema failing to validate, a required field being empty, or an extracted document having fewer sections than the source material's own structure implies. Self-reported confidence can be used as a secondary signal but must never be the only one — this mirrors the same discipline `_extract_executive`/`_extract_acquirer_name` (this session's own promotion scripts) already apply: a miss is just a miss, never guessed past, never trusted on the model's own say-so.
3. **Every escalation is logged** — which task, which tier it started at, what tier it escalated to, and what the trigger was. This is both a cost-monitoring signal (a task escalating constantly is mis-tiered and should move up a tier by default, or the Tier 0/1 approach needs fixing) and matches this system's standing audit discipline: nothing about a mutation-adjacent decision happens silently.

## Where the policy lives

A single shared config (not yet built — this document is the spec, not the implementation) that any surface consults before making a model call: given a task type, return the tier to start at and the escalation trigger to check. Each surface (capture relay, rbb-chat, the document bridge) owns its own trigger-checking logic (what "failed" means is task-specific), but the tier table and escalation-count ceiling are shared, not reimplemented per surface. This is the concrete shape of "one interface routing intelligently" from the wider design discussion — not one merged UI, one shared routing policy every surface defers to.

## Deferred, explicitly not now

- **Additional/free-tier third-party providers.** Real lever for monetization economics later, but adds provider auth, rate-limit management, and output-quality variance to a system that's deliberately been kept single-provider-simple while it has one user. Build the tiering skeleton against what's already integrated (OpenAI's ladder, Claude's) first; treat a new provider as a config-level swap at whichever tier it fits, once the escalation logic is proven and real usage/monetization justifies the added operational surface.
- **Demoting rbb-chat's query engine off Tier 3.** Plausible some query traffic is simple lookups that don't need premium reasoning, but this should be sized from real usage data after the capture-path work ships and rbb-chat's traffic mix actually shifts, not assumed now.
- **A literal unified interface merging the Custom GPT, Codex, and rbb-chat surfaces.** Not achievable — separate vendor products. The achievable version is the shared routing policy above, with one of the existing surfaces (most naturally rbb-chat, already the trustworthy/audit-logged one) as the front door.
