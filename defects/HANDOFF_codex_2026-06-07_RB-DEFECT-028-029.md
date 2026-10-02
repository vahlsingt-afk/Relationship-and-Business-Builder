# Handoff to Codex — 2026-06-07 (updated 2026-06-08)

Status: 028, 029, 031, and 032 are implemented and verified. 021's core layer is
done; 4 sub-items remain (see below).

---

## RB-DEFECT-028 — Entity Alias & Ticker Scope Expansion — ✅ DONE (verified 2026-06-07)

**Spec:** [RB-DEFECT-028_entity-alias-ticker-scope-expansion_2026-06-07.md](RB-DEFECT-028_entity-alias-ticker-scope-expansion_2026-06-07.md)

Implemented and verified against the design: `entity_identity.py` +
`backfill_entity_identities.py` added; 1,006 aliases across 696 entities; `ticker`
populated on 20 legitimate public-company/parent entities (Olo correctly nulled post
Thoma-Bravo acquisition); `identity_terms`/`search_queries` wired into
`earnings_monitor.py`, `passive_intelligence.py`, `web_scanner.py`. No further action
needed on this item.

**What:** Intelligence-gathering queries currently key off a single canonical
`entity_name`, under-collecting signal for entities referenced under alternate names
(e.g. "Freddy's" vs "Freddys" vs "Freddy's Frozen Custard") or by stock ticker.

**Do:**
1. Backfill the existing (currently empty) `aliases: []` array for all 1,610 entities
   in `system/ecosystem_intelligence.json` — punctuation/spacing variants, DBA/legacy
   names, and for tech vendors, parent↔brand name pairs (Genius/Xenial/RTI-style).
2. Add a new top-level `ticker` field (sibling to `aliases`, not nested in
   `attributes`) — populate for public companies, `null` for private.
3. Update query-construction call sites in `system/scripts/` (grep for
   `entity_name`/`entity["name"]`) to fan out: `[name] + aliases + ([ticker] if set)`.

No schema migration needed — `aliases` already exists and is correctly typed; this is
backfill + one new field + a query-builder change.

---

## RB-DEFECT-029 — External Content Intelligence Extraction Failure — ✅ DONE

**Spec:** [RB-DEFECT-029_external-content-intelligence-extraction-failure_2026-06-07.md](RB-DEFECT-029_external-content-intelligence-extraction-failure_2026-06-07.md)

Implemented and verified 2026-06-08. The live LinkedIn bridge and normalized
`social.feed.json` path now automatically call the mutation engine. Known-author
thesis alignment updates relationship affinity/tag state, creates engagement
opportunities, and appears in Overnight Knowledge Mutations. 337 related tests pass.

**What:** A Bruce Nelson LinkedIn post (independent validation of several Todd
theses) was read but never mutated into knowledge-graph updates, relationship
updates, opportunities, or brief entries. Confirmed root cause: it's a **wiring gap**,
not an engine quality problem — `intelligence_mutation_engine.py` (RB-DEFECT-026,
commit `d570565`) is fully built and working, but is only reachable via two manual
endpoints (`/api/artifacts`, `/intelligence/mutate`). It has zero connection to the
automated LinkedIn ingestion path (`linkedin_export_watcher.py` /
`morning_pipeline.py` — confirmed via grep, no references either direction).

**Do:**
1. Wire `linkedin_export_watcher.py` (or `morning_pipeline.py`'s post-processing
   step) to automatically call `intelligence_mutation_engine.run(text, source_title,
   source_url, source_date, ...)` for authored long-form content items (posts/
   articles — not messages or connection events), using the same call shape
   `server.py:5033` already uses for `/intelligence/mutate`. This is a new *caller*
   into the existing engine, not new extraction logic.
2. Extend the engine's mutation-generation stage with a new mutation type:
   `thesis_alignment_detected(person, thesis_id, alignment_strength)` — fires when
   the resolved content source is a known person in the relationship graph (not just
   a brand/company). It should emit, alongside the existing knowledge-graph mutation:
   - relationship-graph mutation (affinity_score delta, thought-partner tag)
   - opportunity record (`engagement_opportunity`, basis `thesis_reinforcement`,
     person, source_artifact, suggested_action)

   Same confidence-scored, timestamped mutation-log structure the engine already
   uses — additive type, not a parallel system.
3. Confirm the "Overnight Knowledge Mutations" brief section (`server.py` ~line 5070)
   surfaces these new relationship/opportunity mutation types too, not just
   knowledge-graph ones.

**Non-goal:** No retroactive backfill of the Bruce Nelson post is architecturally
required — it can be submitted manually via the existing `/intelligence/mutate`
endpoint as a one-off if desired. The fix is about every *future* post.

---

---

## RB-DEFECT-021 — Weekly Planning Layer Architecture — 🟡 CORE LAYER DONE, 4 sub-items remain

**Spec:** [RB-DEFECT-021_weekly-planning-layer-architecture_2026-06-07.md](RB-DEFECT-021_weekly-planning-layer-architecture_2026-06-07.md)

**What:** The Daily Brief ranked items purely on intrinsic significance with no
concept of weekly goals/outcomes/portfolio allocation to score *against*. Claude
implemented and verified the core data layer + wiring directly (per role
clarification: Claude does architecture *and* engineering for RB).

**Done (verified — 120 regression tests pass, live save→pickup confirmed):**
- New `system/scripts/weekly_planning.py`: `weekly_plan.json` schema, the five-mode
  operating calendar (021A), multiplicative outcome-alignment scoring (021B), the
  maintenance-loop display gate (021C), and the Friday scorecard schema/persistence.
- `cos_judgment.build_operating_mode()` — generalizes the old binary
  `build_weekend_guidance` into the five-mode weekly cycle.
- `daily_brief.py`: loads/caches the active plan, exposes `report["operating_mode"]`
  + `report["weekly_plan"]`, layers `wp.apply_alignment` onto
  `_strategic_significance_score` (additive, degrades to neutral 1.0 with no plan),
  gates `maintenance_loop` items through the new suppression policy, and attaches
  `source_navigation` (`{source, date, est_reading_time_minutes, why_it_matters}`) to
  every contract-bearing item (021G done).

**Remaining for Codex (independent follow-ons, not blocking the core layer's value):**
1. **Monday plan-generation orchestrator** — the schema/loader exists
   (`weekly_planning.new_plan`/`make_outcome`/`save_plan`); build the generator that
   proposes outcomes from open loops + opportunity pipeline and persists
   `weekly_plan.json` each Monday (cross-reference loop-ledger IDs
   `L-YYYY-MM-DD-NNN` and `system/opportunities/*.md` IDs).
2. **Friday review orchestrator** — `make_scorecard`/`save_scorecard` exist; build
   the script that observes the week's outcome movement and calls them
   (`weekly_review.py`), plus a monthly strategy review reading trailing scorecards.
3. **021D/021E** — relationship-activity-as-intelligence and headline→implication
   synthesis. Do **not** build a parallel extraction pipeline: route through
   `intelligence_mutation_engine`, reusing the `thesis_alignment_detected` /
   `engagement_opportunity` machinery DEFECT-029 just landed.
4. **021F** — new macro-signal category (labor/spending/inflation/rates/traffic/
   earnings/AI investment), gated for surfacing by relevance to active
   `weekly_plan.outcomes` (the alignment-factor mechanism from item 1 above applies
   directly here once outcomes exist).

**Design principle (now enforced in code):** every recommendation pass asks "does
this move one of this week's active outcomes forward?" via a multiplicative factor +
display-policy gate layered *on top of* the existing scorer — not a replacement.

---

## RB-DEFECT-031 — Morning Newspaper Headlines + Overnight Change Digest — ✅ DONE

**Spec:** [RB-DEFECT-031_morning-newspaper-and-mutation-digest_2026-06-08.md](RB-DEFECT-031_morning-newspaper-and-mutation-digest_2026-06-08.md)

**What:** User scored the brief 60/100 — biggest miss: it opens with "here are your
opportunities" instead of "here's what happened in the world," and restates known
state instead of leading with deltas ("what changed since yesterday").

**Done (verified — pure assembly-stage reframe over data already loaded, no new
ingestion):**
- `_build_morning_headlines()` → `report["morning_headlines"]` — groups top
  `market_signals` items by category into `{headline, source, date,
  why_it_matters, url, confidence}`, fully source-attributed (live run produced
  real SEC EDGAR items with dates/URLs/confidence — addresses critique #1/#2 directly)
- `_build_overnight_change_digest()` → `report["overnight_change_digest"]` — reads
  the DEFECT-029 mutation log, filters to last 24h (UTC), groups by mutation type
  into a delta-first summary (`counts_by_type`, `total`, `mutations`); degrades
  gracefully to an empty digest while the log is still accumulating volume
- Both wired into `build_report` immediately after `market_signals_report`
- 120 existing regression tests pass unchanged

**Remaining (scoped, not blocking):** signal-strength time-series deltas (critique
#3, e.g. "Harri 83/100, +7") need new persisted per-opportunity tracking — anchor it
to RB-DEFECT-021's outcomes once the Monday plan-generator exists, file separately.
Section reordering to put headlines first in `canonical_brief.sections` is a small
mechanical follow-up once these blocks are validated against a few days of live data.

---

## RB-DEFECT-032 — SMS Treated as Communication Metadata, Not Intelligence Source — ✅ DONE (implemented and verified 2026-06-08)

**Spec:** [RB-DEFECT-032_sms-intelligence-extraction-failure_2026-06-08.md](RB-DEFECT-032_sms-intelligence-extraction-failure_2026-06-08.md)

**What:** Jeff Coffland's SMS containing material contradictory intelligence about
the Foods Connected/McDonald's opportunity thesis was discarded — worse than
DEFECT-029's "wiring gap," because `fetch_apple_messages.py` truncated message text
to 80 chars *before* any extraction stage could see it. Three layers were missing,
not one.

**Done (implemented directly, all layers — verified):**
- **Layer 1** (`fetch_apple_messages.py`): privacy-bounded, allowlist-gated
  full-text capture — `sms_trusted_senders.json` allowlist, `--trusted-fulltext`
  flag, `_normalize_handle`/`load_trusted_senders`, captures `full_text` only for
  inbound messages from senders on the explicit allowlist (opt-in, defaults off)
- **Layer 2** (`sms_content_mutation.py`, new file): thin caller mirroring
  `social_content_mutation.py` exactly (DEFECT-029 playbook) — hash-manifest dedup,
  resolves sender to baseline contact, calls `intelligence_mutation_engine.run()`
- **Layer 3a** (`opportunity_intake.py`): added `Thesis` + `Contradictory Signals`
  structured fields to the opportunity markdown schema
- **Layer 3b** (`intelligence_mutation_engine.py`): new
  `contradictory_opportunity_signal` mutation type — cue-phrase + watchlist-company
  detection, `source_trust` derived from `author_match.rc_tier`, confidence fixed
  below auto-apply threshold (always `requires_confirmation: true`); also fixed a
  latent `thread_cos` scoping bug (was defined only inside the vendor-detection
  loop, would `NameError` on vendor-less messages — hoisted to compute once,
  unconditionally)
- Live-tested against text resembling Jeff's actual SMS: correctly fired two
  `contradictory_opportunity_signal` mutations (McDonald's + Foods Connected) with
  full interpretation/alternative-explanations/recommended-validation payloads
- Regression: 49/49 tests pass (`test_external_content_mutation.py`,
  `test_relationship_mutation_engine.py`)

**Remaining (user action, by design):** populate `system/sms_trusted_senders.json`
with trusted contacts' normalized handles to activate the allowlist — intentionally
a manual, user-controlled step, not auto-populated.

---

All specs include exact file paths and line numbers from the current codebase
state. Flag back to Claude if anything doesn't match what you find when you start
implementation — line numbers may drift as changes land.
