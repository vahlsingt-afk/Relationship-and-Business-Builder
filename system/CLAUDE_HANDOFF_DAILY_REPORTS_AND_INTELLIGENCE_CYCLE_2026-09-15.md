# Claude handoff — daily reports and intelligence cycle

**As of:** 2026-09-15 (America/Chicago)  
**Scope:** Business/account intelligence for Todd's Global Payments/Genius role. Purely personal relationship content is out of scope.  
**Status:** The collection, analysis, review, and report-building improvements below are implemented. Do not equate a successful build with successful delivery or a hypothesis with a confirmed opportunity.

## Read this before acting

- `AGENTS.md` is authoritative for RB access: use `python3 system/scripts/rb_cli.py list`, `describe`, and `call`. A real RB mutation exists only after a `call` this turn returns HTTP 2xx. Live account/brief/intel questions likewise require a fresh live read.
- The LinkedIn screenshot/post from Maria C. Ocampo Mercado was captured as strategic/thesis input, not as instructions to RB. Its claim about Gartner's 2029 rehiring forecast has **not** been independently verified here; do not promote it as a canonical fact.
- The repository is heavily dirty from RB's normal operations and prior user work. Preserve unrelated changes. New analytical caches under `system/.cache/` are not canonical claims.
- The daily report has two user-facing parts: `getDailyBrief` (Intelligence Brief / what changed) and `getDailyBriefPart2` (CoS Daily Brief / so what). Display each returned `pre_rendered_brief.markdown` verbatim when answering an `intel` or `brief` request; never reconstruct it.

## What improved in daily reporting

1. **Collection and proof are explicit.** `morning_pipeline.py` separates gather/assess/record/cascade/report receipts, counts checked and processed sources, detects stale/unavailable sources, and exposes quality and delivery state. This gives the brief evidence for what actually ran rather than a generic narrative.
2. **The report is a deterministic operating board.** `daily_brief.py` builds the canonical section contract, with freshness, grounding, source refs, confidence, action posture, negative space, opportunity and competitor sections, relationships, decisions, loops, and follow-through. `render_intelligence_brief.py` and `render_daily_brief.py` pre-render the two parts.
3. **Publishing and retrieval are checked.** `publish.py` materializes daily and latest artifacts. On 2026-09-14, `getDailyBrief` and `getDailyBriefPart2` each returned HTTP 200; Part 2 contained the new ranked queue.
4. **Release quality is gated.** `brief_acceptance_check.py` checks required source freshness, source counts, loop/relationship coverage, provenance, duplicate headlines/story clusters, direct article links, policy alignment, and rendered-content hygiene before email send steps. A false-positive cluster bug was fixed: unrelated companies sharing a “near 52-week low” headline template no longer count as the same story. Its focused test and the 2026-09-14 acceptance gate passed.
5. **The unified intelligence queue is surfaced in Part 2.** `daily_brief.py` loads `intelligence_action_queue.json`, includes its top eight in the canonical brief, and displays action class, score, threshold rationale, missing evidence, and disconfirming questions. These are review recommendations, not executed actions.

## What improved in intelligence gathering

| Layer | Implemented change | Intended value |
|---|---|
| Entity identity | `entity_identity.py` and `web_scanner.py` reject ambiguous generic aliases for attribution (e.g. “pizza” for `&pizza`). | Fewer false entity matches. |
| Source breadth | `public_source_discovery.py` scans a larger rotating batch and recognizes procurement, RFP/RFI, migration, outage, replacement, layoffs, and relevant hiring. `entity_alerts.py` expanded general rotation from 8 to 20 entities and vulnerability query language. | More buying and competitor weakness signals beyond generic news. |
| Material-news exception | `baseline_research_gate.py` dynamically budgets bounded routine research, weighs strategic value/opportunity intensity, distinguishes Radar/Pursuit, and rejects stale events surfaced as current. | Research when a material event meets a genuine baseline gap, without unlimited broad searches. |
| Coverage | `intelligence_coverage_matrix.py` inventories missing account/vendor dimensions and assigns Radar/Pursuit lanes. | Target the next specific gap instead of repeating broad research. |
| Competitor taxonomy | `competitor_intelligence_common.py` and `/competitors` v2 distinguish product-line competitors, adjacent ecosystem vendors, and unclassified tracked vendors. | More precise competitive posture. |
| Opportunity sensing | `sales_opportunity_radar.py` joins brand–incumbent vendor exposure with vulnerability signals; outputs buying-window hypotheses, category/relationship context, source-confidence weighting, missing evidence, disconfirming questions, negative evidence, and calibration state. Exposed via `getSalesOpportunityRadar` (live HTTP 200 verified on 2026-09-14). | Detect both customer shopping windows and competitor displacement possibilities. |
| First-party changes | `entity_page_monitor.py` snapshots/diffs durable first-party pages, distinguishes substantive keyword changes, and retains no-change checks. The explicit watchlist currently has six fetchable pages for Pollo Campero, Starbucks Technology, Yum/Byte by Yum, PAR Technology, and Toast R&D. Six bot-blocked/DNS/timeout endpoints were removed; the clean retry checked 6, changed 0, errored 0. | Earlier detection of hiring, leadership, investor, platform, and partnership shifts. |
| Prioritized actioning | `intelligence_action_queue.py` consolidates buying-window, page-change, baseline-gap, ramification, and competitor-review candidates; groups repeated competitor review items; ranks and classifies into monitor, research further, contact account, build pursuit, and competitive displacement opportunity. Negative checks remain measurable outside the action queue. | One review-first decision surface rather than dispersed cache files. |
| Routing | `system/api/custom_gpt_instructions_compact_8k.md` routes opportunity/displacement questions to `getSalesOpportunityRadar` and documents existing routine-research/downstream-review operations. | Claude/GPT can reach the real source and preserve authorization boundaries. |

## Scheduling and verification receipts

- The new source discovery, page monitor, baseline gap gate, coverage matrix, radar, and action queue are scheduled in `morning_pipeline.py`. They are best-effort scan steps, not canonical write authorization.
- The 2026-09-14 full cycle fetched 230 items from 15 accepted sources, generated 20 convergence patterns and 2 mutation proposals, and rebuilt the brief. Its first acceptance gate failed only because the story-cluster checker conflated three different 52-week-low issuers. After that checker was fixed, the gate passed with zero failures. A targeted rebuild/republish was completed; no second email was sent as part of that targeted rebuild.
- The 2026-09-14 unified queue after competitor-review grouping had 15 candidates: 1 build-pursuit hypothesis, 10 research-first, 4 monitor, 0 cleared direct contact, 0 cleared displacement outreach. It separately recorded 41 no-elevated-buying-window checks and 6 unchanged pages. Focused tests: 34 passed.
- The latest 2026-09-15 action queue has 19 candidates: 1 build-pursuit hypothesis, 13 research-first, 5 monitor, 0 direct-contact, 0 displacement. The top item remains **Yum Brands**, but its buying event, incumbent scope, and sponsor path still need validation; do not treat it as a confirmed opportunity.
- Today's 2026-09-15 `brief_only` receipt shows `brief_acceptance_gate=pass` but `ok=false` and `delivery_ok=false`: all three email sends warned `no_smtp` (RB_SMTP_HOST/USER/PASS not configured). Build quality and delivery must be reported separately. Current stale/unavailable inputs also constrain trust; get the fresh live brief or pipeline receipt before stating exact source health.

## Open issues and next work for Claude

1. **Fix same-cycle ordering.** In `morning_pipeline.py`, `intelligence_action_queue` currently runs immediately after `sales_opportunity_radar`, while `competitor_intelligence_review_scan` runs later. `daily_brief.py` then rebuilds `intelligence_ramifications` during brief construction. The queue can therefore miss same-cycle competitor reviews and downstream ramifications. Move queue construction to after those inputs are current, or rebuild it inside brief computation after the ramifications/review data are ready. Prove with a same-day fixture/test.
2. **Avoid over-escalation from weak hypotheses.** The Yum Brands top item is scored “build pursuit” despite `entity_id=null` in an earlier radar output and incumbent not verified. Tighten the action threshold to require resolved entity identity, source dates/links, a real buying trigger, and minimum independent corroboration. A high numeric score alone should not invite a pursuit recommendation.
3. **Expose full queue through a real read operation.** The daily brief shows only eight entries and `getSalesOpportunityRadar` shows radar data, not the consolidated queue. If Todd needs direct queue review, add a read-only API/CLI operation with contract and tests; do not invent a write/resolve path that bypasses existing domain authorization gates.
4. **Improve delivery independently of report quality.** Diagnose the SMTP configuration/transport and scheduled environment. Do not claim the daily email was sent until a real delivery receipt says so. A failed delivery is not evidence that collection or rendering failed.
5. **Calibrate over outcomes.** Track accepted/rejected candidates, false positives, actual buying events, introductions/meetings, pursuit creation, and competitor displacement outcomes. Use the existing radar state and domain review paths; never infer a success merely because a prospect looked vulnerable.
6. **Expand first-party coverage carefully.** Add only verified, durably fetchable official pages. Bot-protected McDonald's careers/investor pages are not suitable for this monitor; its broader SEC/news/web paths remain active.

## Files to start with

`system/scripts/morning_pipeline.py`, `system/scripts/daily_brief.py`, `system/scripts/intelligence_action_queue.py`, `system/scripts/sales_opportunity_radar.py`, `system/scripts/entity_page_monitor.py`, `system/scripts/intelligence_coverage_matrix.py`, `system/scripts/baseline_research_gate.py`, `system/scripts/brief_acceptance_check.py`, `system/api/server.py`, and `system/api/custom_gpt_instructions_compact_8k.md`.

**Non-negotiable boundary:** A queue label or analytical cache is never proof that RB contacted someone, created an opportunity, changed an account stage, or persisted a canonical claim. Use the named domain operation, its real fields/authorization quote where required, and a visible HTTP 2xx response before reporting a mutation as completed.
