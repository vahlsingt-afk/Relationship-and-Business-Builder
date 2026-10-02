# Claude Handoff — Next Intelligence Sprint

Date: 2026-09-18
Repository: Relationship & Business Builder (RB)

## Objective

Following the intelligence-cycle repair and the ingestion-correctness sweep (both closed out earlier today), Todd asked: "what else can we do to get the best intelligence information in the industry that puts us ahead of the curve." Three candidates were proposed and Todd chose to scope all three as the next sprint:

1. Same-day promotion for executive-move and ownership-change detections (currently next-morning-or-never).
2. Finish the ingestion-audit sweep across the remaining scripts.
3. A new leading-indicator source: job postings from customer/prospect/competitor companies.

This document is the scoping output of investigating all three against the current codebase — not yet an implementation. Each section below is written to be handed off as its own authoritative spec once Todd picks an order/subset, the same way `CLAUDE_HANDOFF_INTELLIGENCE_CYCLE_REPAIR_2026-09-18.md` was for the prior sprint.

---

## Workstream 2 — Ingestion-audit sweep: CLOSED, no fix needed

Scoped first because it turned out to require no build — recording the result here so it isn't re-opened later.

**Scripts audited:** `linkedin_ingest.py`, `granola_capture.py` (does not exist as a separate file), `technomic_watchlist_scan.py`, `earnings_monitor.py`/`earnings_trend.py`/`earnings_signal_taxonomy.py`, `jpr_recordings_index.py`, `watchlist_promotion.py`, `tech_stack_relationship_promotion.py`.

**Result:** none are affected by the manifest/overwrite-safety bug class that `contacts_ingest.py`, `whatsapp_ingest.py`, and (today) `hubspot_ingest.py` had.

- The real LinkedIn folder-watcher is `linkedin_export_watcher.py` (not `linkedin_ingest.py`, which takes an explicit path/text argument, not a folder) — it already has full file-hash/manifest dedup (`MANIFEST_PATH`, `_file_hash()`, `_load_manifest`/`_save_manifest`).
- "Granola capture" is actually handled by `capture_ingest.py`'s `sweep()`, which already dedups via a `processed_ids` registry keyed by a file-id hash, and already carries the RB-2026-08-28 `sweep_window_hours` orphaning fix in its comments.
- `technomic_watchlist_scan.py`, `earnings_monitor.py`, `jpr_recordings_index.py`, `watchlist_promotion.py`, and `tech_stack_relationship_promotion.py` all write via natural-key idempotent dedup (signal id, URL hash, or candidate id) or full-overwrite rebuilds — reprocessing the same input a second time is already a safe no-op in every one of them.

**Action:** none. This closes the "audit remaining ingestion scripts" thread opened earlier today. No acceptance tests needed — there is no defect to regress-test against.

---

## Workstream 1 — Same-day promotion for executive-move / ownership-change detections

### Current state (confirmed by reading the code, not assumed)

There is no single reusable "same-day promotion" utility in RB — three scanners that all get described loosely as "scanners" behave completely differently:

- **`technomic_watchlist_scan.py`** (`run_scan()`) is the only one with a real same-day path today. It is not a flag on the finding — it's *where* the finding is written. A material finding is merged straight into `entity_alerts_cache.json`'s `press_release_items` for the matched entity, and the entity name is appended to a dated manifest, `system/.cache/technomic_watchlist_promoted.json` (`{"_scan_date": ..., "entities": [...]}`). `daily_brief.py`'s `_compute_watchlist_intelligence()` reads that manifest and includes an entity in *today's* roster only if `_scan_date == date.today().isoformat()`. No bespoke flag, no queue — just "write into the caches the brief already reads, dated today, before the brief runs."
- **`priority_account_publisher_scan.py`** (the RB-DEFECT-069 scanner) never touches `entity_alerts_cache.json` or any dated manifest. It only writes to its own pending-candidate store (`priority_account_publisher_candidates.json`); confirmation only appends to an account's evidence log. `daily_brief.py` never reads this store. There is no same-day path here, full stop — it was never designed to have one.
- **`executive_move_promotion.py`** and **`ownership_promotion.py`** run daily inside `morning_pipeline.py` (both `required=False`, both after `technomic_watchlist_scan`, explicitly reusing what it already collected). Both are structured identically to the RB-069 scanner: `scan()` only writes pending candidates (`executive_move_candidates.json` / `ownership_promotion_candidates.json`); only a human-confirmed `record_proposal()` mutates real data (`baseline_index.json` or `ecosystem_intelligence.json`'s owner field). Grepped `daily_brief.py` for both candidate-store names — zero hits. **These two currently have no automatic path into any brief at any latency** — not "next morning," but "never, unless a human manually confirms the candidate," and even that confirmation doesn't feed a brief render.

### What "ahead of the curve" actually requires here

The gap isn't "same-day vs. next-day" — it's that a genuinely high-confidence executive-move or ownership-change detection can sit in a candidate file indefinitely with zero visibility unless Todd happens to go looking for it. Fixing that is higher-value than literal same-day timing.

### Proposed scope

Add a second, auto-material branch to `executive_move_promotion.py`'s and `ownership_promotion.py`'s `scan()`, alongside (not replacing) the existing review-first candidate path — mirroring `technomic_watchlist_scan.run_scan()`'s pattern:

1. Define what "high-confidence" means for each detector. **Open design question — needs one more read of each script's extraction/confidence logic before implementation**: does either already compute a confidence score comparable to technomic's `MATERIAL_SIGNAL_CLASSES` + high/medium confidence gate, or would this sprint need to add one? Do not guess at a threshold; read `executive_move_promotion.py`/`ownership_promotion.py` in full first.
2. For candidates that clear the high-confidence bar, write into `entity_alerts_cache.json` and a same-day dated manifest the same way `technomic_watchlist_scan.py` does — but note this conflates two different cache semantics (a "press release" cache being used for an exec-move/ownership fact). Decide during implementation whether to reuse `press_release_items` as-is or add a parallel field (e.g. `leadership_signals`) so `daily_brief.py`'s renderer can label the two differently rather than presenting an ownership change as if it were a press release.
3. Lower-confidence detections keep today's fully human-gated candidate-review path unchanged — this sprint should not weaken the confirm-before-mutate guarantee for anything uncertain.
4. Extend `daily_brief.py`'s watchlist-intelligence section (or add a sibling section) to read the new dated manifest(s), following the exact `_scan_date == date.today()` gate already proven in `_compute_watchlist_intelligence()`.

### Acceptance tests (to write once implemented)

- A high-confidence executive-move detection, injected via `scan()` in a test with isolated cache paths, appears in the SAME day's `daily_brief.py` render — not just in the candidate store.
- A low-confidence detection still lands only in the candidate store and does NOT appear in the same-day brief (the review-first guarantee is unweakened).
- Confirming a low-confidence candidate via `record_proposal()` still writes to `baseline_index.json`/`ecosystem_intelligence.json` exactly as it does today (no regression to the existing confirm path).
- Running `scan()` twice on the same underlying signal does not duplicate the entity in the dated manifest or double-write `entity_alerts_cache.json` (apply the same natural-key-dedup discipline the ingestion-audit sweep just confirmed everywhere else).

### Files likely touched

`system/scripts/executive_move_promotion.py`, `system/scripts/ownership_promotion.py`, `system/scripts/daily_brief.py`, plus new/extended tests in `system/tests/`.

---

## Workstream 3 — Job postings as a leading-indicator source

### Why this is a smaller build than it sounds

The taxonomy this would feed already exists: `vulnerability_taxonomy.py`'s `tech_hiring` category already classifies role-title text (e.g. "Director Restaurant Tech," "POS Program Manager") and is already wired into `entity_alerts.py`/`competitive_vulnerability.py` scoring. `ROADMAP.md` records Todd's own 2026-09-05 decision that job postings are "a fundamentally better-suited data source" than earnings text for this signal, which is why earnings-signal Phase 2 was deliberately scoped down. `STATUS.md` lists the relevant company-intelligence layer as **NOT BUILT**. So this sprint item is specifically: **build the missing acquisition/ingest step that feeds real job-posting text into a classifier that already exists** — not a new taxonomy, not a new scoring model.

### Reusable pieces confirmed in the codebase

- **Candidate/review-queue shape**: `tech_stack_relationship_promotion.py` and `watchlist_promotion.py` share one proven pattern — a `system/.cache/*_candidates.json` store (`{"candidates": {id: {...}}}`), fields for evidence (`source_type`, `source_title`, `source_url`, `evidence_excerpt`, `evidence_date`), provenance (`origin`, `origin_ref`, `supporting_refs` for corroboration), a `conflict_preview`, and a `scan()` → `pending_candidates()` → `record_proposal(id, confirmed=...)` flow where only confirmation writes to a permanent store. `job_postings_promotion.py` should copy this skeleton exactly.
- **Acquisition template**: `earnings_monitor.py` proves the free/no-paid-API pattern this codebase already relies on — official free feeds where they exist (SEC EDGAR, IR RSS), and a documented DDG-search-then-scrape fallback (`https://html.duckduckgo.com/html/?q=site:fool.com "{company}" transcript`, parsing `uddg=` redirects, fetching with a browser-like User-Agent, storing only a short bounded excerpt) where no official API exists. `job_intelligence.py` (Todd's personal job-search scanner — unrelated in purpose, but reusable in mechanism) already contains working URL patterns for greenhouse/lever/ashby/workday/icims job boards. Combining the two: DDG `site:boards.greenhouse.io`/`site:jobs.lever.co`/etc. searches scoped to each tracked brand's name is a proven-pattern, no-paid-API acquisition strategy for this sprint — LinkedIn Jobs/Indeed APIs are paid/restricted and out of scope.
- **No shared entity resolver exists** — this is a real decision, not solved by default. Brand/vendor resolution against `ecosystem_intelligence.json` goes through `entity_identity.find_entity()`; `customers_prospects/` accounts have their own slug space with no fuzzy matching; `baseline_index.json` person-matching (`identity_matcher.py`) is unrelated. A job posting mentioning "Chick-fil-A" needs to be checked against both the ecosystem graph and the customers_prospects registry independently — there is no cross-store bridge. **Decide before implementation**: match the existing convention (each script does its own resolution, accepting the duplication) rather than building a new shared resolver as a side quest — consistent with how every other domain in RB currently works, and avoids scope creep into an unrelated architecture change.
- **Confirm-target decision**: a confirmed job-posting candidate should write into whichever store already owns the fact it represents — `customers_prospects/accounts/<slug>/evidence.jsonl` if framed as customer-side signal (this is the more natural fit, mirroring how `priority_account_publisher_scan.py` already appends evidence per account), or `ecosystem_intelligence.json` only if a specific vendor relationship is being asserted (e.g., a hiring posting explicitly names a vendor/platform, which would be rarer). Default to the account evidence log; only route to `ecosystem_intelligence.json` when the posting text names a specific vendor product, matching the existing `tech_stack_relationship_promotion.py` bar for what counts as a relationship assertion versus a general signal.

### Proposed scope

1. `job_postings_ingest.py` — DDG-search acquisition against tracked brand/vendor names' career pages (greenhouse/lever/ashby/workday/icims patterns from `job_intelligence.py`), parsing role title + posting date + short excerpt. Best-effort, never-raises, matching `earnings_monitor.py`'s resilience discipline.
2. `job_postings_promotion.py` — candidate store + `scan()`/`pending_candidates()`/`record_proposal()`, reusing `vulnerability_taxonomy.py`'s `tech_hiring` classifier to score each posting before creating a candidate (so low-signal generic postings, e.g. ordinary store-crew hiring, never reach the queue).
3. Entity resolution: call `entity_identity.find_entity()` first; fall back to a customers_prospects slug lookup; if neither resolves, do not create a candidate (avoid identity-ambiguity mutation, consistent with Todd's canonical mutation policy from the prior sprint).
4. Wire into `morning_pipeline.py` as a new, `required=False` step alongside the other promotion scanners.

### Acceptance tests (to write once implemented)

- A posting matching a `tech_hiring`-classified role title, for a brand that resolves against the ecosystem graph, produces a candidate with correct evidence/provenance fields.
- A posting for a brand that resolves against NEITHER store produces no candidate (no silent misattribution).
- A generic/non-technical role posting (e.g. "Shift Manager") does not clear the `tech_hiring` classifier and produces no candidate.
- Running the ingest twice against the same posting URL does not duplicate the candidate (natural-key dedup, consistent with every other script the audit swept above).
- `record_proposal(confirmed=True)` writes to the correct store (account evidence log by default) and is idempotent on a second confirm call.

### Files likely touched

New: `system/scripts/job_postings_ingest.py`, `system/scripts/job_postings_promotion.py`, `system/tests/test_job_postings_ingest.py`, `system/tests/test_job_postings_promotion.py`. Modified: `system/scripts/morning_pipeline.py`.

---

## Suggested order

1. **Workstream 1** (same-day promotion) is the smallest, most contained change — two existing scripts, one already-proven pattern to adapt, clear acceptance tests. Best next step.
2. **Workstream 3** (job postings) is a genuinely new source — larger surface area, one real unresolved design decision (entity resolution / confirm-target routing) to settle during implementation rather than in this doc.
3. **Workstream 2** is already closed — nothing to schedule.

Recommend confirming Workstream 1's confidence-gating question (what "high confidence" means in `executive_move_promotion.py`/`ownership_promotion.py` today) as the very first implementation step, since the rest of that workstream's scope depends on the answer.
