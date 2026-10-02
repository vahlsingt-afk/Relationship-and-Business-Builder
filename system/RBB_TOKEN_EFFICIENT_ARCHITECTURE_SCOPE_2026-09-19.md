# RBB Token-Efficient Architecture — Project Scope

Date: 2026-09-19
Companion doc: `system/RBB_MODEL_TIER_POLICY_2026-09-19.md` (the tier table and escalation rules this project implements)

## Objective

Restructure how RBB's three real AI-driven workflows — intelligence capture, live query, document generation — use model tokens, so each gets the cheapest-appropriate tier by default and escalates only on a real, deterministic signal. Directly targets the structural cost problem already found: rbb-chat's orchestration loop resends ~91KB of instructions+tools every turn and rebills full conversation history via `previous_response_id` chaining, independent of which model is behind it.

## Background — why now

- rbb-chat costs $10-15/day; traced to the structural resend/rebill pattern above, not fundamentally to model choice (the one existing fix, `CHEAP_MODEL` tiering, only touches OCR/narration side-calls, deliberately not the main orchestration loop).
- Codex — subscription/rate-limited, not metered per-token — is separately hitting its own usage ceiling during high-development periods, a different resource being exhausted.
- Todd is already piloting "ChatGPT handles deep research on cheap tokens" inside Codex right now. This project formalizes and extends that instinct across the rest of the system — it should not duplicate or conflict with that in-flight work.
- Productization/monetization is a real but separate future direction (see `project_rbb_future_multitenancy_direction` in memory) — the real blocker there is the single-user/local-file data model, not the interface choice. This project doesn't solve that; it does make the per-user cost story credible if that direction is ever pursued, since today's per-user waste would otherwise multiply by every future user.

## Phases

### Phase 1 — Capture relay (build first)

**Update, same day, post-Codex-session review:** the local-client half of this phase already exists. Codex built `system/inbox/chatgpt_intelligence_drop/` — a watched folder (registered in `settings.json`'s `capture_sources`, `capture_type: "deep_research"`, `enabled: true`) that local ChatGPT/Codex clients with filesystem access write evidence packets into directly, swept by the existing `capture_ingest_scan` pipeline step exactly like every other capture source. Verified end-to-end against real data (a real 2026-09-18 Wendy's/Meritage bankruptcy packet was queued and processed on 2026-09-19). This changes Phase 1's remaining scope substantially — see below.

**Goal:** raw intelligence (text, PDF, Excel, PNG) reaches the existing sweep pipeline at Tier 0/1 cost, without invoking rbb-chat's premium orchestration loop.

**Already exists, reusable, not being rebuilt:**
- `capture_ingest.py`'s pending → processed → brief-reported lifecycle. The trust question here is already resolved: the deterministic morning sweep is the trust mechanism, not live model judgment (same reasoning that let `auto_persist` stay off for live chat while the scheduled sweep auto-persists freely).
- `queue_text()` / `queueCaptureText` — already wraps text-queueing; already routes through the same pipeline.
- `uploadAndIngestFile` + its watched-folder fallback for large binaries (RB-DEFECT-068) — the existing safety net for anything this phase can't handle cleanly.

**Decided 2026-09-19: build both surfaces, not one.** Todd's call — run a Custom GPT capture surface and an rbb-chat fast-path side by side, gather real usage/cost/reliability data, then keep both or sunset one. This resolves what was open question 1; it also means both surfaces must land on the exact same backend rather than each growing its own capture logic, or the comparison later would be confounded by two different pipelines, not just two different front doors.

**Shared backend, built once, used by both surfaces:**
A single capture-relay endpoint (extends the existing `POST /captures/queue-text` / `queueCaptureText` path rather than replacing it) that accepts already-extracted *text* plus minimal metadata (a capture-type hint if the caller has one, which surface it came from), runs the Tier 0/1 classification from the model tier policy, and writes to the existing `capture_ingest.py` pending queue. Both the Custom GPT and the rbb-chat fast-path call this same endpoint — neither surface reimplements classification or queue-writing itself. This is the concrete, smallest instance of the tier policy's "shared config, not reimplemented per surface" principle, built now instead of deferred to Phase 4, because Phase 1 already needs it for two surfaces at once.

**File-transfer rule (applies to the Custom GPT surface specifically):** route extracted *text* through the Action, never raw bytes — Custom GPT Actions base64-encode file content into JSON, proven unreliable above small sizes (RB-DEFECT-068, three confirmed failure modes on a 1.9MB file), while small text payloads are proven reliable. The Custom GPT reads the file natively (PDF/Excel/image parsing it already does for free) and the Action only relays the resulting text to the shared endpoint.

**Build order, revised given what already exists:**
1. **Real, concrete gap found while verifying Codex's work**: `queueCaptureText` (the hosted-surface path — what a real Custom GPT would call, per the drop-folder's own README) hardcodes `capture_type="pasted_content"` server-side (`system/api/server.py:2500`) with no caller-supplied override, unlike `queue_text()`'s underlying function, which already accepts one (same pattern used for the folder-watch source's `capture_type: "deep_research"`). A hosted Custom GPT sending deep-research evidence through `queueCaptureText` today would get it mislabeled `pasted_content`, not `deep_research` — small, well-scoped fix: add an optional, validated `capture_type` override to the endpoint, mirroring the 2026-09-11 precedent for this exact parameter. This is now Phase 1's real starting point, not a new shared endpoint (that already exists as `queueCaptureText`).
2. rbb-chat fast-path — still not built; lower risk than a new Custom GPT since it reuses rbb-chat's already-proven audit/receipt discipline.
3. The Custom GPT capture surface — still not built; needs the live file-transfer verification discipline RB-DEFECT-068 established before it can be trusted, and should call the now-fixed `queueCaptureText` with `capture_type="deep_research"` rather than reinventing a relay.

**Comparison criteria** (to actually decide keep-both vs. sunset-one later, not just impression):
- Reliability: does the capture consistently land in the queue with correct content, verified against real receipts/logs, not narration.
- Cost per capture, by surface.
- Extraction quality on PDF/Excel/PNG specifically (this is the Custom GPT's real differentiator if it has one).
- Friction: which surface Todd actually keeps reaching for day to day, unprompted.

**Acceptance criteria (Phase 1, both surfaces):**
- A dropped PDF/Excel/PNG/text file lands in the existing capture pipeline with a correct `capture_type`, without invoking rbb-chat's full tool-orchestration loop.
- Extraction producing empty/near-empty text from a non-empty file retries once per the tier policy's escalation rule, then fails safely to the watched-folder path — never silently drops content.
- Every capture is logged through the existing audit discipline — no new, weaker trust surface introduced alongside the cheap path.
- Live-verified end-to-end per surface (a real file, a real receipt checked against `request.log`/audit log, not the model's narration alone) — same discipline as every fix this session, doubly important here given this phase's whole point is trusting a cheaper, less-scrutinized path by default.

### Phase 2 — rbb-chat narrowed to query engine

**SUPERSEDED 2026-09-23, see `system/RBB_SKILLS_ARCHITECTURE_SCOPE_2026-09-23.md`.** The
delete-unused-tools framing below (and step 1's 3-tool cut, which stays valid and unrelated) is
replaced by a contextual-loading design: a core bundle every turn plus skill-shaped bundles loaded
only when a turn's content matches them, instead of judging individual tools safe/unsafe to remove.
Also subsumes Phase 4 below (the "shared routing policy" Phase 4 named but never scoped is exactly
what the skill registry + matcher is). Kept here for history, not current.

**Goal:** once capture traffic moves off rbb-chat's main loop, trim its tool schema to query-oriented tools only, cutting per-turn resent-context size directly (smaller schema = smaller bill on every turn, independent of the routing logic in Phase 1).

**Step 1 — small safe cut, DONE 2026-09-22.** Before cutting anything, checked real usage against `system/audit/*.jsonl`'s `"rbb_chat tool call: <op>"` events (every tool call the orchestrator has ever made, not a guess by category). Finding that changed the plan: of rbb-chat's 126 model-facing tools, 79 have zero calls ever — but git-blame on `rbb_chat_tools.py` showed 73 of those 79 were added 2026-09-05 through 09-19 (the competitive-intel + document-generation artifact suite: Green Sheet, Win Plan, RFP Response Plan, Battle Card, Competitive Brief, Vendor Engagement Analysis, Relationship Card/Plan, Inner Circle, Referral Network, Account Plan, tech-stack + review-queue tools). Zero calls there means "hasn't been reached through chat yet," not dead — cutting that block now would have silently removed capability Todd asked for and RB just finished shipping, before it had a fair shot. Todd's call: cut only what's actually justified, hold the rest.

Removed exactly 3 tools from `rbb_chat_tools.py`'s model-facing `TOOLS` list (`operations`/routing left fully intact — `_execute_operation` still resolves all 126 for internal callers like the Phase 1 fast path and `rb_cli.py call`):
- `closeThread` — self-audit (loop L-2026-09-12-001, recurring 3x) confirmed it takes live chat traffic and produces zero real mutations ever; offering a broken tool risks a fabricated receipt.
- `processAllCaptures` — the live prompt already told the model "scheduled-pipeline only, never call this here"; it was never supposed to be model-callable at all.
- `queueCaptureText` — genuinely superseded for the chat surface by Phase 1's capture fast path (same HTTP call, zero model tokens, no schema entry needed).

`custom_gpt_instructions_compact_8k.md` (the actual live prompt) updated at all 3 routing sites so the model doesn't reach for a tool it no longer has. `validate_kb_consistency.py`'s `KNOWN_OK_STALE_MENTIONS` extended with justification for the 3 names (routing in `operations` still real; only the chat-facing schema entry is gone). Full suite: 5119 passed, 1 pre-existing unrelated failure (`ecosystem_intelligence.json` ticker field), 2 skipped. TOOLS: 126 → 123.

**Step 2 — the real trim, NOT started, revisit ~2026-10-13 to 2026-10-20 (3-4 weeks out).** Re-run the same audit-log usage analysis then, once the 2026-09-05/09-19 artifact suite has had a real chance to be used through chat. At that point, split what's still zero-call into genuinely proven-dead (real trim candidates) vs. still-just-new (hold again) — don't assume everything with zero calls by then is dead; check build date the same way this pass did. This is the step that actually moves the per-turn resend-size needle; step 1 barely touched it (126→123) by design.

### Phase 3 — Document bridge tiering

**Goal:** apply the tier policy's Tier 1/2/3 ladder to the "read these RFP files, produce a deliverable" workflow. Already has a natural home (Codex/Claude, via the existing RFP Response Plan suite and doc-generation tooling) — the work here is tiering *within* that workflow (cheap first-pass extraction/drafting, premium reserved for final assembly/verification), not choosing a new host for it.

Not scoped in detail yet.

### Phase 4 — Shared routing policy + evaluate additional providers

**Goal:** the actual shared-config implementation of `RBB_MODEL_TIER_POLICY_2026-09-19.md` (today the policy is a spec, not code), and a real evaluation of free/third-party model tiers once usage data justifies the added operational complexity.

Explicitly deferred per the policy doc — not scoped, not started.

## Coordination note

Codex is actively working on intelligence-gathering/deep-research changes in this same working tree right now. Nothing in this scope should be built against files Codex's session owns without checking in first — this document exists to align on direction, not to race ahead of that work.

## Immediate next step

Build the shared capture-relay endpoint (Phase 1, build-order item 1) — coordinate with Codex first, since it's actively working on deep-research/capture-adjacent code in this same tree right now.
