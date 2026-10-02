# Evidence-to-Persistence Map — 2026-08-24

Purpose: when new evidence is recognized (by RBB, by the Custom GPT, or by an automated scan), this is the lookup table for **which persistent files are the plausible mutation targets** — organized by *what kind of thing was learned*, not by which script processes it. Companion to [`INTELLIGENCE_PIPELINE_MAP_2026-08-24.md`](INTELLIGENCE_PIPELINE_MAP_2026-08-24.md), which maps the pipeline stage-by-stage (gather → assess → mutate → report). This doc answers a narrower, more operational question: *"I just learned X — which file(s) should reflect that, and does anything actually write there today?"*

Every store and automation-status claim below was verified against the live system today (file existence checked, or already traced this session), not assumed from naming.

---

## 1. A fact about a PERSON

*Job change, interaction, sentiment shift, life event, referral.*

| Store | Role | Auto-write status |
|---|---|---|
| `system/baseline_index.json` | Canonical contact record — identity, `current_company`/`current_role`, `tags`, `signal_class`, `rc_tier`, `last_touch` | **Partial.** `last_touch` and `tags` now auto-apply (today's fix) when `apply: true` + the contact already resolves to a known baseline entry. `current_company`/`current_role`/`signal_class` are **always manual** — matches the shared convention already used by `linkedin_ingest.py`/`contacts_ingest.py`/`hubspot_ingest.py`: never overwrite an operator-confirmed field from a single free-text mention. |
| `system/cards/<id>.md` | RC card — the relationship's *narrative meaning* (trust arc, leverage, what's lingering, risks), only exists once a contact is promoted to `signal_class: RC` | **Manual only.** No script found that writes these automatically. |
| `system/interaction_ledger.json` | Log of relationship-touch / declared-interaction events | Writes eagerly on intake; only the `claim_status` (proposed→confirmed) is gated. |
| `system/ri_events/*.jsonl` | Canonical, durable RI event history | Real writer exists (`ri_events.append()`), but the default scheduled run only populates a *pending* cache — real confirmation requires `--confirm-passive-ri` explicitly (until today's #5 fix flipped the daily-refresh default to on; see the pipeline map). |
| `system/circles/*.md` | Named curated groups (e.g. `hospitality-table.md`, `otp3-leaders.md`) | Manual only. |
| `system/personal_log.json`, `personal_relationship_log.json`, `personal_timeline.json` | Todd's own practice/life events specifically (Life Lens) | Auto-writes, but only from `ingestExecutiveDeclaration`'s `personal_practice_logged` path — not from general third-party evidence. |

**"RC"** = Relationship Circle, `signal_class: RC`, tiers `inner | broader | dormant_valuable` (`system/SCHEMAS.md`).

**"IC"** = Interaction Card, per Todd — an earlier-generation concept, superseded by the current system (confirmed: no "Interaction Card"/"IC" artifact exists anywhere in the live codebase or its design docs today). Its function — a per-interaction record — is what `system/interaction_ledger.json` now is: one entry per relationship-touch/declared-interaction event, the same row already listed above. For an `RC`-tier contact specifically, the richer narrative half of what an IC likely used to carry (the *meaning* of the interaction, not just that it happened) now lives in that contact's `system/cards/<id>.md`. So "check for an IC" today means: check `interaction_ledger.json` for the event, and the RC card (if one exists) for the narrative interpretation of it.

---

## 2. A fact about a COMPANY / BRAND

*Leadership change, funding, earnings, a vendor-customer relationship, a deployment.*

| Store | Role | Auto-write status |
|---|---|---|
| `system/ecosystem_intelligence.json` | **The macro graph** — canonical vendor-customer/tech-stack relationship graph (`entities`, `relationships`, `signals`, `assessments`, `watch_list`). 1,738 entities / 2,048 signals / 378 relationships as of today. This is the "canonical tech stack" store. | **Real, live.** Several scripts write here unconditionally (`ecosystem_brief.py`, `technomic_watchlist_scan.py`) or on a confidence gate (`intelligence_mutation_engine.py`, ≥0.80 auto-applies). |
| `system/.cache/intelligence.db` | Raw capture of *everything* the daily scan finds — 8,357+ items, before any graph promotion | **Real, live**, fed automatically by the daily web scan. This is the durable "what do we know in the last 90 days" store. |
| `blue_sheets/accounts/<slug>/account.json` → `technology_stack[]` | If the company has an **activated** Blue Sheet, its own synced copy of the tech-stack read | **Partial, as of today (gap #8).** Auto-syncs `status` only, only when a relationship's `category` maps to exactly one unambiguous row — never guesses. Unactivated accounts (everything except Pollo Campero today) are untouched by design. |
| `system/graphs/micro/<slug>/graph.json` | A **micro graph** — a deep, scoped operational-topology graph built from one specific source document (org structure, franchise/operator map). Currently only one exists: `mcdonalds_us_ops`. | **Manual/review-first only.** Built via `micro_graph_mcdonalds.py` (McDonald's-specific parser) or `micro_graph_builder.py` (general ETL, dry-run by default). Never auto-updated from daily evidence. |
| `system/account_intelligence/*.md` | Ad-hoc dated research docs/briefs per account | Hand-authored only, no programmatic writer. |

Retrieval priority for a company-factual question (per `system/graphs/micro/index.json`'s own `trust_hierarchy`): micro graph → macro graph (`ecosystem_intelligence.json`) → verified external source → general knowledge.

---

## 3. An INDUSTRY-WIDE / MACRO trend

*Not one company — e.g. "QSR traffic is softening," "labor costs are rising in this segment."*

| Store | Role | Auto-write status |
|---|---|---|
| `system/behavioral_intelligence.json` | Consumer/operator behavioral signal records (8.7MB, actively populated) | Proposal-only — writes an eager pending record, `claim_status` gated on confirm. |
| `system/entity_intelligence.json` | Entity-level risk/positioning records from macro analysis | Same proposal-only pattern. |
| `system/strategic_events.json` | Entity-level convergence/watchlist signal stream (imported directly by `daily_brief.py`, feeds Section I "Strategic Signals") | **Real, auto-written** on every scheduled brief build. |
| `system/strategic_memory.json` | Referenced by 9 active scripts (`insight_intake.py`, `intelligence_mutation_engine.py`, `intelligence_triage.py`, `macro_intelligence.py`, and others) as a shared strategic-signal input | **Wired in but nearly dormant** — only 3 signals, last updated 2026-06-01. Worth checking whether this is meant to be actively fed and has quietly stopped, or is correctly low-volume by design. |

---

## 4. A WATCHLIST entity worth tracking

| Store | Role | Auto-write status |
|---|---|---|
| `system/ecosystem_intelligence.json`'s `watch_list[]` array | **The real watchlist** — not `system/watchlist.json`, which doesn't exist on disk. | `technomic_watchlist_scan.py` auto-promotes real press-release hits into `entity_alerts_cache.json`/`technomic_watchlist_promoted.json` (feeds Section F of the brief). Adding an entity to the watch list itself is done via the working `getWatchList`/`updateWatchList` API (`ecosystem_intelligence.py watch-list add/remove/set-priority`). `intelligence_assessment.py`'s `watchlist_add` proposals now render in the brief (today's fix) — but still require you to actually call `updateWatchList` yourself; the render doesn't auto-apply. |

---

## 5. A LOOP / open obligation

*"Follow up with X," "close this out."*

| Store | Role | Auto-write status |
|---|---|---|
| `system/loop_ledger.md` (L- namespace) | Legacy execution-loop register | Written via `mutations.cmd_loop_add`/`cmd_loop_close`, API/CLI only. |
| `system/eolms/loops.json` (EL- namespace) | Executive Open Loop Management System — richer lifecycle | Written via `eolms.match_and_transition()`/`eolms.close_by_id()`. |

**This is the one domain RBB's own Drive-based assertion bridge can already auto-persist today** — `add_loop`/`close_loop`, drained every 15 minutes via the loaded LaunchAgent. Everything else in this document is still outside RBB's direct persistence reach; it either goes through Relationship Bridge 9.0 (which has full API access) or waits for the automated pipeline to pick it up on its own schedule.

L- and EL- are kept deliberately namespace-distinct — never summed into one count without checking `reconciliation_queue`.

---

## 5b. A WEEKLY PLAN / weekly outcome or scorecard

*"This week's outcomes are...", a Friday review of wins/misses.*

| Store | Role | Auto-write status |
|---|---|---|
| `system/weekly_plan.json` | Authoritative, adopted weekly outcomes/priorities plan | Draft auto-generates Monday; adoption to authoritative now auto-adopts end-of-day Monday if you haven't confirmed/rejected it yourself (today's #6 fix) — never overrides an explicit decision. Snapshot-backed as of today (see below). |
| `system/weekly_plan_draft.json` | The pending draft before adoption | Auto-generated Monday morning; status flips to `confirmed`/`rejected`/stays `draft_pending_confirmation`. |
| `system/weekly_scorecard_draft.json` | Friday review scorecard — wins, misses, outcome movements, relationship movement, lessons | Auto-generates Friday (`weekly_review_generator.py --write-draft`); adoption into a permanent record is a separate manual step, not yet automated. |

---

## 6. An OPPORTUNITY / career-pipeline update

| Store | Role | Auto-write status |
|---|---|---|
| `system/tracked_opportunities.json` | Todd's own career/business-opportunity pipeline (stage, candidate_position) | Auto-writes from `ingestExecutiveDeclaration` and `processOpportunityUpdate`; API/CLI only, not scheduled. |
| `system/active_threads.yaml` | Hand-curated open career/business threads | Open/close have API endpoints; `update` is CLI-only, no API wiring. |

---

## 7. A STRATEGIC INSIGHT or thesis observation

| Store | Role | Auto-write status |
|---|---|---|
| `system/conversation_insights.json` | Industry-trend/vendor-positioning/thought-leadership observations (`insight_intake.py`) | Proposal-only, eager write + confirm-gated status. |

---

## 8. A CoS / experiential lesson

*A deployment post-mortem, a firsthand lesson from a project.*

| Store | Role | Auto-write status |
|---|---|---|
| `system/experiential_intelligence.json` | Firsthand executive experience records (`experiential_intelligence.py`) | Proposal-only. **Does not exist on disk yet** — nothing has ever been confirmed through this path. |

---

## Structural gaps found in a completeness sweep (2026-08-24)

Requested check: is anything missing from the data stream, or drifted out of sync with what actually exists. Two findings worth acting on, beyond the missing categories folded into the sections above:

1. **`system/WHAT_PERSISTS.md` is itself significantly stale, and it's the file a fresh Claude session is explicitly told to bootstrap from.** It describes an earlier architecture generation — `profiles/{profile_id}/profile.md`, `domain_packs/`, and `_snapshots/` at the top level still exist and check out, but its description of `briefs/` ("Interaction Briefs — append-only evidence atoms, the proof behind every signal-class promotion") no longer matches what that folder actually holds. Confirmed directly: `system/briefs/` today is a genuine collision of two unrelated things —
   - Legacy per-person evidence-rollup files (`2026-03-16-aamir-rajan-linkedin-evidence-rollup.md` and hundreds like it) — last written March 2026, presumably no longer generated by anything live.
   - The current daily/intelligence brief rendered output (`2026-08-24-daily-brief.md`, etc.) — written every day, a completely different concept that now shares the same folder name.

   `WHAT_PERSISTS.md`'s own bootstrap instructions ("First message to that Claude... read... `system/README.md`...") don't currently point a fresh session at this specific document, so the practical risk today is contained — but the document itself would actively mislead if read as written. Worth either updating it to describe the current architecture or retiring it in favor of the two maps built today.

2. **Two "derived, regenerate periodically" views haven't been regenerated in ~3 months** despite `baseline_index.json` growing substantially since: `network_map.md` (last computed 2026-05-12, 199 people) and `intro_brokers.md` (last derived 2026-05-26). Not broken — they say plainly they're regenerable on demand — just stale enough that anything reading them today is looking at a network snapshot from three months ago.

Folded into the categories above rather than called out separately: `weekly_plan.json`/`weekly_plan_draft.json`/`weekly_scorecard_draft.json` (new §5b — this whole layer was missing from the first draft of this map), `strategic_events.json` and `strategic_memory.json` (added to §3 — the latter flagged as wired-in but nearly dormant, worth a direct check on whether that's by design).

---

## Governance — what must never auto-write, regardless of source

- **Blue-sheet-native Miller Heiman judgment fields**: qualification scorecard, buying-influence role/mode/personal-win/competitive-preference/rating, euphoria-panic read, best-action-plan. These exist *only* inside an activated Blue Sheet and require human judgment — enforced by `impact_review.py`'s governance-gated field list (Section 8), confirmed working end-to-end today.
- **A person's `current_company`/`current_role`**: always manual, across every ingestion path in this codebase (LinkedIn, contacts, HubSpot, relationship intake) — a single mention is not enough evidence to overwrite an operator-confirmed fact.
- **`technology_stack[].status` values "Active" / "Reconcile"**: carry operational meaning beyond simple evidence confidence; today's Blue Sheet sync deliberately only ever writes "Verify" or "Confirmed."

---

## Cross-reference

For the full pipeline view (which scripts gather evidence, which classify/assess it, and their current automation status) see [`INTELLIGENCE_PIPELINE_MAP_2026-08-24.md`](INTELLIGENCE_PIPELINE_MAP_2026-08-24.md). For the Blue Sheet field-provenance split (registry-sourced vs. blue-sheet-native) and the mutation-owner status by script, see `system/CANONICAL_REGISTRY.yaml`'s `blue_sheets` domain, updated today.
