# RBB Strategic Assessment — 2026-09-19

Follow-up to `system/RB_STRATEGIC_ASSESSMENT_2026-08-28.md` (three weeks later). Same brief: how does RBB function as a true Chief of Staff, what would make it more automated, and what would make its recommendations genuinely better. This one is grounded in three targeted investigations of the live codebase and its own runtime data (`system/loop_ledger.md`, `system/.cache/mutation_reconciliation.json`), not a re-derivation from scratch.

**Headline: the self-audit mechanism the 08-28 assessment built is working exactly as designed — and that's what exposed the biggest finding here.** It has been correctly re-detecting the same real defect for three weeks straight. Nothing has fixed it, because nothing forces a fix; a loop can be marked closed on belief alone.

---

## Finding 1 — A self-detected defect has round-tripped for 3 weeks without ever being fixed

`self_audit_sweep.py` (built 2026-08-28, runs daily via `morning_pipeline.py`) checks `mutation_reconciliation.py`'s output for API operations that receive live traffic but produce zero confirmed mutations ever ("silent" ops). It found one on day one: `closeThread` and `refreshSources`.

What happened next, per `system/loop_ledger.md`:
- **L-2026-08-28-003** — opened 2026-08-28, closed 2026-09-04, no fix description recorded.
- **L-2026-09-05-001** — the *identical* finding, reopened 2026-09-05, closed 2026-09-11 with the reason "Todd believes the closeThread/refreshSources finding was addressed in development this week."
- **L-2026-09-12-001** — the *identical* finding again, reopened 2026-09-12, target-close 2026-09-15. **Still open today, four days overdue.**

The freshest data (`system/.cache/mutation_reconciliation.json`, generated 2026-09-18T10:00:22Z) confirms `closeThread` and `refreshSources` are still `"status": "silent"` right now. The 2026-09-11 closure was wrong — not maliciously, just unverified. Todd's belief was reasonable given a week of unrelated development work; nothing in the system distinguished "I believe this is fixed" from "the automated check confirms this is fixed."

**Why this matters for "function better as CoS":** a system that finds its own real defects but has no mechanism to ensure they get root-caused rather than re-closed is worse than one that never checks at all, in one specific way — it manufactures false confidence. Todd has now closed this exact finding twice believing it was handled.

**Concrete fix, small:** a loop opened by `self_audit_sweep.py` (tagged `"Self-audit findings:"`) should only be allowed to close automatically once `mutation_reconciliation.py`'s NEXT run shows the flagged op(s) as `healthy` — not via a manual close. If Todd wants to close it anyway (deprioritize, accept the risk), that's fine, but the loop's close reason should say so explicitly rather than asserting a fix that was never verified. This is a policy change to `self_audit_sweep.py`'s own loop-lifecycle logic, not a new subsystem.

---

## Finding 2 — Six review-first queues, no unified "what needs your attention" view

RBB's review-first architecture is genuinely good and consistent: `tech_stack_relationship_promotion.py`, `watchlist_promotion.py`, `priority_account_publisher_scan.py`, `ownership_promotion.py`, `executive_move_promotion.py`, and (as of yesterday) `job_postings_promotion.py` all share one proven `scan()` / `pending_candidates()` / `record_proposal()` shape. Todd's canonical mutation policy (never invent a fact, confirm before mutate) is applied uniformly. That part is not the gap.

The gap is visibility. As of yesterday's work, `executive_move_promotion.py` and `ownership_promotion.py` surface same-day-*fresh* detections into `daily_brief.py`'s `pending_mutations` section. That leaves two real holes:

1. **The other four queues** (`tech_stack_relationship_proposals.json`, `watchlist_promotion_candidates.json`, `priority_account_publisher_candidates.json`, `job_postings_candidates.json`) have **zero** brief visibility — reachable only by running each script's own `... pending` command by hand.
2. **Even the two with same-day visibility only surface the day something is first detected.** A candidate that's sat pending for a week is invisible in the brief either way — same-day promotion tells you about today's finds, not your backlog.

There is no single place that answers "how many things are waiting on my confirm right now, across every queue, and how old is the oldest one" — the exact question a real Chief of Staff exists to answer unprompted. `self_audit_sweep.py` doesn't check queue size/staleness either; it would be a natural home for it.

**Concrete fix:** one new `_compute_review_queue_backlog()` function in `daily_brief.py` (or a small new shared module all six scripts already expose enough to build from — each has `pending_candidates()`) that reads all six queues, reports total count + oldest `detected_at` per queue, and surfaces a single rolled-up item once any queue crosses a staleness threshold (mirroring `_compute_pending_mutations()`'s existing 12-hour-staleness pattern for `interaction_ledger.json`, which already does exactly this for one queue and could be the template). This is the single highest-leverage "more automated / better CoS" fix available right now — it doesn't require building anything new, just aggregating what already exists.

---

## Finding 3 — Automation coverage is broad but almost entirely best-effort

`morning_pipeline.py` runs ~60 steps. Only 5 are hard gates (`refresh_intelligence_caches`, `verify_public_intelligence_collection`, `write_today_and_manifest`, `publish_canonical_artifacts`, `brief_acceptance_gate`); the other ~55 are `required=False` — they log a warning and the pipeline continues with degraded data on failure. This is a reasonable design choice (one obscure scanner breaking shouldn't block this morning's brief), but it means a real breakage in a best-effort step is invisible unless something like `self_audit_sweep.py` is specifically checking for it — which reinforces why Finding 1's enforcement gap matters more than it looks: the self-audit layer isn't a nice-to-have, it's the only thing standing between "one of 55 best-effort steps quietly broke" and Todd finding out.

Known, already-documented manual-dependency points remain real and aren't new: GP Outlook capture (no working AppleScript bridge or Graph connector, per existing memory), weekly plan/review drafts requiring an explicit confirm, and HubSpot/contacts/WhatsApp ingestion only processing files someone has already manually exported into an inbox folder. None of these look solvable without either GP's cooperation (Outlook) or accepting the manual-export step as a permanent constraint (the others) — flagging as bounds, not overlooked gaps.

---

## Finding 4 — Cross-signal synthesis is better than 08-28 found it, but the two most-visible "insight" surfaces still aren't synthesis

The 08-28 assessment's "synthesis is the thinnest layer" finding is now only half true. Real multi-source combination exists and has grown:

- **`competitive_vulnerability.py`** joins four independent sources (entity_alerts, web_scanner, LinkedIn sentiment, and an "ecosystem blast-radius" join that attributes a vendor's own signal to every brand using that vendor) into one weighted composite score per entity, with confidence derived from how many distinct categories triggered together — genuine synthesis, not listing.
- **`signal_synthesis.py`** (run daily across ~155 watchlist entities via `entity_convergence_scan.py`) aggregates six independent stores into one dominant-pattern verdict per entity.
- **`daily_brief.py`'s `_compute_ctd_db_convergence`** does real time-based synthesis: it requires an entity to appear from ≥2 distinct sources on ≥2 distinct days within 30 days before calling something a sustained pattern — explicitly built to stop a same-day multi-feed burst from masquerading as a trend.
- **`opportunity_signal_correlation.py`** and **`cos_synthesis.py`** both do real joins (48-hour cross-channel correlation; 3+-distinct-company macro-theme confirmation).

But the section literally named for this — **`daily_brief.py`'s "What RB Found Without You Telling It"** — is still three independent appends (best earnings item, watchlist escalations, fresh strategic signals) deduped only by text fingerprint, never cross-referenced against each other. And **`account_background_brief.py`** — the account-level document a rep would actually read before a call — is explicitly documented in its own code as "pure rendering... never re-synthesized." Its one apparent synthesis feature, a "Conflicting information requiring reconciliation" section, is fed by `contradictions.json`, which is permanently seeded empty with **no detection engine anywhere writing to it**. It's a real, rendered section in every account brief that can never contain anything — an illusion of a capability that doesn't exist, not a work-in-progress.

**Concrete fixes, two separate sizes:**
- *Small, honest:* either remove the "Conflicting information" section from `account_background_brief.py` until something writes to `contradictions.json`, or build the minimal version — a same-account cross-check (does the tech-stack store say Vendor X, does a recent earnings-call mention name a different vendor for the same category, does an evidence.jsonl entry contradict brand_profile.json) that would at minimum catch the cases this session's own promotion scripts are already creating evidence for.
- *Larger, higher ceiling:* extend `_compute_ctd_db_convergence`'s real cross-time/cross-source discipline to "What RB Found Without You Telling It" itself, so the flagship discovery section does what its name claims rather than being the one place in the brief where the 08-28 finding is most visibly still true.

---

## Priority list

1. **Fix the self-audit loop-closure gap (Finding 1).** Smallest change, closes a live 3-week-old false-confidence loop, and hardens the exact mechanism everything else here depends on.
2. **Build the unified review-queue backlog view (Finding 2).** Highest-leverage "more automated / better CoS" fix — six queues already exist and already have the data; this is aggregation, not new detection.
3. **Extend genuine cross-time synthesis to "What RB Found Without You Telling It" (Finding 4, larger).** Highest ceiling for "better recommendations" — the infrastructure to do this already exists and is proven elsewhere in the same file.
4. **Resolve or remove the dead `contradictions.json` scaffold (Finding 4, small).** Cheap, and stops a real account-facing document from implying a capability that doesn't exist.
5. Everything in Finding 3 (manual-dependency points) — not actionable without an external decision (GP cooperation) or already an accepted constraint; listed for completeness, not as a task.

Items 1 and 2 are both small enough to scope and implement directly if wanted.
