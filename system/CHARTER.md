# RB / RBB Charter

**The entry point.** Read this first, before any other document in this system. It states what the system is for, the principles it must never violate, how it's structured, how work actually gets done here, where the roadmap lives, and where everything else fits. It does not replace the detailed documents it points to — it's the index and the summary, so a fresh session (or a fresh person) can get oriented in one read instead of five.

**Last written:** 2026-09-05, as part of a repo cleanup pass. Superseded whenever this system's real shape changes enough to make it wrong — see "Keeping this current" at the end.

---

## 1. Mission

You have thousands of professional connections. You personally know maybe 10% of them. You have a real, working relationship with maybe 5%. The rest is dormant capacity — people who'd help if asked, intros that would land if pursued, deals that would move if someone noticed the signal in time.

This system exists to close that gap — for Todd's own network, and for the restaurant-technology/payments accounts he sells into professionally. It watches continuously, remembers everything, and tells him what matters before he'd otherwise notice: a relationship going cold, a competitor's product move, an account's earnings call revealing a real opportunity, a loop that's about to slip. It is a Chief of Staff, not a dashboard — it identifies what matters, recommends what to do, and makes the next step easy.

## 2. Guiding principles

The full, original statement of these lives in [`01_RB_TENETS.md`](01_RB_TENETS.md) — that document still wins in a conflict with anything below, per its own stated authority. **But read it with a caveat this charter states plainly, not silently**: large parts of `01_RB_TENETS.md` and `ARCHITECTURE.md` (Circles, RC-card tiering, a Free/Pro/Enterprise/F&F/Developer SaaS product structure, session text export, an "MT" trace module) describe an earlier product vision that was never fully built and was explicitly deprioritized in favor of single-user reliability (2026-08-13 decision, see memory). Don't treat those sections as current-system fact. The tenets below are the ones this session has watched hold up, verified, and enforced in real code, over and over:

**Never fabricate.** If the data isn't there, the system says so — it never invents a fact, a number, a contact, an executive name, a source, or a receipt. This has been a *recurring, live-caught* failure mode (a GPT surface fabricating "incorporated" claims with no audit trail; `tool_choice=required` forcing invented "intelligence" into the real database; a regex bug fabricating executive identities from name-shape collisions) — never assume the discipline holds just because it's stated once. Verify.

**Silence beats a guess; a targeted question beats silence.** When the system doesn't know, it asks the smallest useful question rather than inferring. When two sources conflict, it surfaces the conflict rather than picking one.

**Decision quality over worldview reinforcement.** The system must surface contradictory evidence as readily as confirming evidence, and adjust confidence as reality changes. It should never protect a static thesis when the market is telling it otherwise.

**Trust is earned by stats.** Every output shows what the system actually did — counts, dates, source files, evidence chains, an audit trail. A hand-wavy summary loses more trust than it saves time.

**Propose, don't silently mutate.** Anything that writes durable state on inference rather than an explicit user assertion goes through a candidate store and an explicit confirm step — never a silent write. This is now the load-bearing pattern across every mutation-capable feature (Blue Sheets, Competitor Intelligence, tech-stack relationships, watchlist promotions); see §4.

**An LLM's own instructions are not a sufficient guard.** A prompt telling a model "never write X" will still sometimes produce X — proven live, more than once, in this exact system (a strengthened anti-generic prompt reproduced the identical banned pattern on the next real call). Where a failure mode is dangerous or embarrassing enough to matter, back it with a deterministic check, not just better wording.

**Best-effort, never-raises degradation for anything that touches the outside world.** A network call, a paid API, a scrape — every one of these can fail, and none of them may be allowed to break the pipeline that depends on them. Catch, return empty/default, let the caller's existing fallback take over.

## 3. Design structure

The full statement lives in [`ARCHITECTURE.md`](ARCHITECTURE.md) (same staleness caveat as above — its product-shape sections describe an earlier vision; its structural/behavioral sections are more durable) and, for real domain-by-domain authority, in [`CANONICAL_REGISTRY.yaml`](CANONICAL_REGISTRY.yaml), which is the actually-current, actively-maintained governance file (`registry_id: rbb-canonical-registry`, approved with amendments 2026-08-21).

**The unit of value is relationship and account meaning, not the record.** A contact row or an account file is data; the trust state, momentum, leverage, and unresolved movement carried over time is what the system exists to preserve. Files are the memory — not any chat session, not any model's context window. If it matters, it goes in a file; when a session ends, only what's written down survives.

**One canonical file (or store) per domain, one script (or small script family) that owns writes to it.** `CANONICAL_REGISTRY.yaml` names the owner for each domain explicitly so two subsystems never silently disagree about who's authoritative for a given piece of state. When a registry-listed owner turns out not to exist, or a build turns out to duplicate an existing owner, that's a registry-drift defect — fix the registry or the code, don't let both stand.

**The real subsystems today** (not the RB 9.x vision above): the contact/relationship graph (`baseline_index.json`, RI events, RC cards); the restaurant-tech ecosystem graph (`ecosystem_intelligence.json` — brands, vendors, relationships, signals); Blue Sheets and Master Account Plans (per-account sales intelligence artifacts); Competitive Landscape (market-share/battle-card tooling); Earnings Intelligence (MD&A + transcript excerpts, signal dimensions); the Daily Brief, Intelligence Brief, and Team/Industry Brief (three distinct audiences, three distinct renderers, sharing some synthesis machinery); the watchlist (`watchlist_registry.json`) and its auto-promotion layer; and `rbb_chat.py`, the Trusted Chat Client — the sole live conversational surface (the Custom GPT it replaced is retired, 2026-08-28).

**rbb-chat replaced the ChatGPT/Codex/Claude three-way role split.** `OPERATIONALIZATION.md` described that older split and is now archived (`_archive/system-docs-pre-2026-09/`) — see §4 for what actually governs how work happens today.

## 4. Methodology — how work actually gets done here

This is not documented as one coherent thing anywhere else; it's the pattern this project has converged on, session over session, and the part most worth a fresh session internalizing before touching code.

**Investigate before acting, verify before claiming done.** Read the real file, run the real script, check the real production data — don't reason from what a docstring or a memory file says should be true. Memories are point-in-time notes, not live state; a file path or function name in one may already be gone.

**Root-cause, not symptom-patch.** When something's broken, trace it to the actual defect (a word-boundary regex, a cache-key mismatch, a wrong SEC CIK) rather than papering over the visible failure.

**Propose-then-confirm for anything inferred.** The concrete shape: a JSON candidate store (`status: proposed_pending_confirmation | confirmed | rejected`), a `record_proposal(id, confirmed: bool)` function, a shared `POST /confirm` dispatcher routing by a `kind` literal to the right per-feature handler. Never invent a new shape for a new feature — reuse this one (`tech_stack_relationship_promotion.py` and `watchlist_promotion.py` are two recent, deliberate copies of the same pattern).

**Deterministic backstops over trusting a model's self-discipline.** When an LLM call's own output needs a hard guarantee (never ship a fabricated fact, never ship a known-generic sentence), enforce it with code after the call returns, not just instructions before it. `brief_synthesis.py`'s `_is_generic_hedge()` regex backstop — added after a strengthened prompt *still* reproduced the exact banned pattern on a live test — is the reference example.

**Test with real production data whenever it's reachable, not only synthetic fixtures.** A synthetic test can pass while the real thing is broken (the `_snapshots`/registry-drift/CIK-mismatch defects were all found this way). When a live external call is genuinely needed to verify a fix (an LLM synthesis call, a live scrape), say so plainly and only spend that cost with the user's knowledge — it's real money/tokens, not free.

**Live-verification can surface an inconvenient truth — report it, don't paper over it.** Twice this project has built something, tested it live, and found the live behavior didn't match the design intent (a DuckDuckGo anti-bot block; a prompt fix that didn't fix anything). Both got reported honestly, with the mitigation that was actually shipped, not a claim of success that wasn't earned.

**Close every unit of work the same way:** update `ROADMAP.md`, write (or update) a memory file under the Claude Code memory directory describing what changed and why, and run the real test suite before calling it done. Todd runs multiple parallel Claude Code sessions on this project at once with no shared live visibility between them — a cross-session tracker memory file (`project_open_decisions_ledger`) exists specifically to survive that gap. A future session inherits nothing you didn't write down.

**Ask, don't guess, when a decision is genuinely the user's to make** — a scoping tradeoff, a data-sourcing philosophy, a naming/priority call. Ground the question in real numbers pulled from the system, not an abstract hypothetical, so the user is deciding from evidence.

**Never commit unless asked. Never touch another session's in-flight uncommitted work** — this repo routinely has hundreds of files modified by the daily automated pipeline and by parallel sessions; check `git status` before any bulk file operation, and keep unrelated changes out of your own.

## 5. Roadmap

Lives at [`ROADMAP.md`](ROADMAP.md) — not duplicated here. That's the single place tracking what's queued, what's parked, and what's explicitly decided-against. Update it, don't fork it.

## 6. Document map

What's still genuinely current vs. what's historical, so the sprawl this cleanup pass found doesn't quietly regrow.

| Document | What it's for | Currency |
|---|---|---|
| `CHARTER.md` (this file) | Entry point: mission, principles, structure, methodology, roadmap pointer | Current — 2026-09-05 |
| `01_RB_TENETS.md` | Full original principles statement | **Partially stale** — core tenets hold, SaaS/Circle/tier sections don't match the built system |
| `ARCHITECTURE.md` | Full original design statement | **Partially stale** — same caveat as above |
| `CANONICAL_REGISTRY.yaml` | Domain-by-domain authority/ownership, machine-readable | Current, actively maintained |
| `ROADMAP.md` | What's queued/parked/decided | Current, actively maintained |
| `BOOTSTRAP.md` | Session-onboarding read sequences | Needs a pass — references `system/_sessions/index.json` and `system/settings.json`/`profiles/` which may not match how memory actually works today; not verified in this pass |
| `WHAT_PERSISTS.md` | The persistence contract/philosophy | Current, self-dated 2026-08-24 |
| `SCHEMAS.md` | Data model | Not independently re-verified this pass |
| `STATUS.md` | What's live/partial/designed/not-built, by component | Restructured 2026-09-05 — trimmed to current-state tables; full changelog history moved to `_archive/system-docs-pre-2026-09/STATUS_HISTORY.md`; **structural claims not individually re-verified**, treat as orientation |
| `system/protocols/` | Machine-readable protocol definitions (read-set, script, cache, trigger per protocol) | Live, used by `BOOTSTRAP.md`'s lazy-load mechanism |
| `_archive/` | Everything superseded, historical, or one-time — gitignored, not canonical | Reference only, never edit-in-place |

**The rule going forward:** a new dated one-off document (a sprint writeup, a defect closeout, a handoff note) belongs in a memory file (`.claude/projects/.../memory/`) or in `defects/`, not as a loose file at `system/` top level. If a `system/`-root markdown file's content is genuinely superseded, move it to `_archive/` with a dated subfolder rather than leaving it to accumulate — that's exactly the sprawl this pass just cleaned up (roughly 90 dated `CLAUDE_SPRINT_*`/`CLAUDE_HANDOFF_*`/`CLAUDE_DEFECT_*`/`CODEX_HANDOFF_*` files, plus a 1,535-line `STATUS.md` that had become a pure append-only changelog).

## Keeping this current

This charter is wrong the moment the system changes enough to make it wrong — that's normal, not a failure. When you notice a real gap (a principle this charter states that the code no longer follows, a subsystem in §3 that's been replaced, a methodology habit in §4 that's stopped being true), fix the charter in the same session, the same way `STATUS.md`'s "if a row reads LIVE but isn't running, fix the row, not the claim" discipline already asks for its own tables.
