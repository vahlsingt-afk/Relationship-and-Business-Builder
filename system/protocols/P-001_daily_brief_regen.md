---
id: P-001
title: Daily Brief Regeneration
script: system/scripts/daily_brief.py
cache: system/.cache/daily_brief.json
reads:
  - system/baseline_index.json
  - system/loop_ledger.md
  - system/settings.json
  - system/restaurant_tech_watchlist.md
  - system/circles/
  - system/cards/
writes:
  - system/today.md
  - system/MANIFEST.md
inputs:
  - name: date
    description: ISO date to treat as 'today'.
    required: false
trigger: daily 05:00 America/Chicago
---

# P-001 — Daily Brief Regeneration

## Purpose

Regenerate `system/today.md` and refresh `system/MANIFEST.md` from current canonical state. This is the procedure the `rb-daily-briefing` scheduled task runs at 05:00 America/Chicago each morning.

The product feel is "daily newspaper on the doorstep": by the time Todd gets coffee, the brief should already be prepared, the ChatGPT cockpit should be ready, a short ChatGPT push should nudge him into the cockpit, and a short completion email should be waiting as backup.

Product philosophy: RB should not behave like another dashboard. It should identify what matters, recommend the next move, and make the next action easy. Metrics and scan proof build trust, but the output is an operating layer that makes the user better.

## When to run

- Scheduled task fires (default daily 05:00 CT).
- Operator says "regenerate today.md" or "rerun the brief."
- Mid-day when material state changes (a new ingestion, multiple loops closed/opened, a meeting prep needed).

## Read set

Exactly these files. Do not load others unless a step explicitly says to.

1. `system/settings.json`
2. selected user profile (`system/profiles/{profile_id}/profile.md`, or `system/00_TODD_PROFILE.md` during legacy single-user mode)
3. `system/01_RB_TENETS.md`
4. `system/ARCHITECTURE.md`
5. `system/SCHEMAS.md`
6. `system/heuristics.md`
7. `system/restaurant_tech_watchlist.md`
8. `system/baseline_index.json`
9. `system/loop_ledger.md`
10. All `system/circles/*.md` (read frontmatter for `circle_type`)
11. `system/network_map.md` (for the "what the system is watching" section)
12. `system/today.md` (the prior brief, for delta comparison only)

Do NOT load `cards/` or `briefs/` wholesale. Only load specific cards/briefs referenced by a contact that surfaces in computation.

## Inputs

- **Today's date** (the trigger supplies this; if missing, use system date).
- **Verbosity mode** from `settings.json` (`normal` default).
- Optional operator override: a focus area to weight ("focus on the Genius pre-screen", "prep me for tomorrow's meeting with X").

## Steps

### 1. Gate on `daily_briefing.enabled`

If `settings.json → daily_briefing.enabled` is `false`, write nothing and reply: `daily briefing disabled — no files written.` Stop.

### 2. Refresh sources and relationship-signal caches

Before computing the brief, attempt the available local source refresh path:

```bash
python3 system/scripts/refresh_sources.py --all --save-health --refresh-signals
```

If the wrapper reports `refreshed`, use the refreshed caches. If a source reports `skipped_no_raw_input`, `skipped_disabled`, or `not_applicable_on_platform`, keep going but surface the limitation in the Data Health / Confidence section. If a source reports `failed`, continue only if the failure does not corrupt existing caches; otherwise stop and report the failure.

The brief may not conclude "quiet day" or "no new signals" from a source that is stale, skipped, or failed. It must say the board is under-instrumented for that source.

The morning automation is responsible for refreshing every source that can be refreshed without Todd:

- **Calendar/email**: use connected Google Calendar/Gmail tools or the local Google OAuth fetcher when available; otherwise normalize the newest raw connector captures via `refresh_sources.py`. Missing raw input is a system setup gap, not a Todd task.
- **SMS messages and phone logs**: run the local Mac fetchers through `refresh_sources.py --messages --calls`; if Full Disk Access is missing, report that operational setup gap.
- **LinkedIn/social public posts and own-post engagement**: refresh overlays from available session captures/exports. Todd should only be asked for the periodic LinkedIn data export when the export itself is missing or stale.
- **Industry/operator watchlist**: scan source-backed restaurant-tech and operator sources before the brief, then write reviewed rows to `system/inbox/market_signals.json` and run `market_signals.py --cache`. Preferred sources include RTN, Hospitality Technology, Restaurant Business, Nation's Restaurant News, Restaurant Dive, QSR Magazine, Fast Casual, relevant operator/vendor LinkedIn posts, earnings calls, SEC filings, investor decks, press releases, product blogs, job postings, and conference/session signals. This scan is expected morning work, not optional garnish.

Social/LinkedIn is a first-class prep source where permissioned data exists. Refresh and scan LinkedIn public-post feeds, own-post engagement, comments/reactions, and LinkedIn message exports/captures when available. Public posts should feed both RI and Market Movement & Strategic Implications; messaging and interaction evidence should feed RI, loop detection, opportunity detection, and last-touch candidates. If LinkedIn access is manual-only, stale, or unavailable, report that explicitly in Daily Prep Summary rather than silently omitting it.

### 3. Run RI scan

Run or read the current relationship-signal cache before drafting. The brief should use:

- `relationship_signals` for last-24-hour source-backed RI.
- stale-source warnings and recommended refresh commands.
- reconciliation prompts.
- `what_to_ignore`.

### 4. Compute dormancy crossings

For each entry in `baseline_index.json` where `signal_class == "RC"` AND `rc_state == "ACTIVE"`:

- Read `rc_tier` ∈ {inner: 30d, broader: 90d, dormant_valuable: 180d}. The mapping is fixed.
- Read `last_touch` as ISO date. If null, hold separately as a "gap surface" case — do NOT include in crossings.
- Compute `overage = (today - last_touch).days - threshold[tier]`.
- If `overage > 0`, include in crossings list.

Sort crossings by `overage` descending.

### 5. Compute imminent loops

From `loop_ledger.md`:

- For each open loop, parse `Closure target` date.
- If `closure < today` → past target.
- If `today <= closure <= today + 7 days` → upcoming this week.

### 6. Compute RC promotion queue

Scan `baseline_index.json` for entries tagged `rc_candidate_<date>`. If any, list them for operator confirmation. Otherwise: `(none)`.

### 7. Build meeting section

- If calendar is connected (MCP or refreshed inbox feed), pull today's events plus the next 7 days.
- Calendar events are operational inputs, not passive agenda text. Scan every event for known relationship attendees, attendees not in baseline, active-thread/company ties, deliverable/prep wording, timing risk, and expected meeting outputs.
- For today's and tomorrow's material meetings, surface a **Meeting Prep & Deliverables** item with why it matters, who/what it touches, and the recommended prep action.
- For any attendee in `baseline_index.json`, offer or generate a pre-conversation brief per ARCHITECTURE.md → "Pre-conversation briefing" (11-section structure).
- The smart task list / loop creation prompt should offer to create the meeting prep for review and open a tracked prep loop when prep, deliverables, or follow-up state are needed.
- If operator surfaced a meeting in chat, treat that as a meeting.
- If neither, write: `Calendar isn't connected as a live source. To make this section live: connect Google Calendar via MCP, or drop a fresh Google Takeout into the folder.`

### 8. Compute Circle moves

For each Circle file in `system/circles/`:

- Read frontmatter to get `circle_type`, current member list, target table.
- Compare against current `baseline_index.json` (entries with this Circle ID in their `circles` array).
- Report: new members since prior brief, count by `circle_type`, target tables still empty.

### 9. Build intro broker section

If operator named a target in inputs, run the intro engine per ARCHITECTURE.md → "Same-Circle behavior depends on `circle_type`" + `heuristics.md` consultation. Otherwise, carry forward open intro-shaped loops from the loop ledger.

### 10. Build quiet zones section

Inner-tier RCs with `today - last_touch < 30d`. List with days-since values.

### 11. Build gap surface section

- RCs with no `last_touch`.
- RCs without a corresponding `cards/<id>.md` file.
- Conflicting facts noted in baseline `notes` field with "CONFLICT" marker.
- Inner-tier RCs missing `email` or `phone` (group by missing field).

### 12. Compute network state summary

From `baseline_index.json`:

- Total entries.
- Counts by `signal_class`.
- RC tier breakdown.
- Delta vs the version stored in the prior `today.md` if computable; else absolute.

### 13. Forward look

Imminent crossings (within 1 day of threshold), scheduled meetings tomorrow, loops closing in the next 24 hours.

### 14. Build daily operating-system sections

The brief should be generated in this order:

1. **Daily Prep Summary** — timestamp, sources scanned, scan window, scan status, evidence count, RI found/no RI found, actions taken, confidence, and freshness. Negative scan results are mandatory; "no RI found" proves the source was checked.
2. **Morning Command Center** — the operating board: top moves, meeting-prep queue, waiting-on state, loop risk, new signal state, suppressed noise, and one decision needed. This is where RB recommends, not just reports.
3. **Top 3 Moves / Executive Priorities** — no more than three named moves, each with why now, who it affects, exact next action, confidence, and consequence of doing nothing.
4. **Meeting Prep & Deliverables** — today's/tomorrow's calendar commitments that require prep, expected output, reviewed materials, or a tracked prep loop. Offer to create meeting prep for review when useful.
5. **What Changed Since Yesterday** — deltas only: relationship-state changes, opportunity-temperature changes, new/expired windows, source-health changes, and newly resolved or newly discovered operational risks.
6. **Opportunity Temperature** — active opportunities/threads labeled `warming`, `active`, `waiting`, `cooling`, `stalled`, `needs_decision`, or `suppress`.
7. **Relationship Signals** — source-backed RI from the refreshed scan.
8. **Loop Review** — overdue, due-today, waiting, auto-closed, suggested closure, stale follow-up risk, and meeting-prep loops.
9. **Strategic Operator Movements / Market Movement & Strategic Implications** — strategic operator movements first, then vendor, brand/operator, macroeconomic, and geopolitical signals only when fresh, sourced, non-repetitive, and tied to an action or monitoring decision. This is not a news enhancement. It is a strategic intelligence and relationship-prioritization layer for the CoS engine. LinkedIn public posts from known operators, brand leaders, vendor executives, buyers, analysts, and target-account employees are preferred market/RI evidence when available. Each item must move through the intelligence chain: Detect → Interpret → Correlate → Map to Relationships → Recommend Action → Prioritize Timing.
10. **What To Ignore** — explicit suppression/noise section.
11. **One Question For Todd** — one graph-improving or decision-improving question when ambiguity matters.
12. **Draft-Ready Actions** — exact follow-up text, post angle, intro ask, meeting prep brief, intro ask, or opener for the top one or two moves when enough context exists.
13. **Suggested Loop Creation** — ask whether to convert recommendations into tracked loops with entity, source signal, action type, due date, verification method, confidence, and closure criteria. Meeting-prep items should offer prep-loop creation with due time before the meeting.
14. **End-of-Day Closeout Offer** — offer a closeout that reports what closed, what slipped, what is waiting, what passive evidence resolved, and what should roll to tomorrow.
15. **Data Health / Confidence** — source refresh status, stale/skipped sources, and confidence limits.

Every item must resolve to one of: `act_today`, `monitor`, `ask_todd`, or `ignore`.

Every actionable item should include action affordances where possible: create loop, draft next action, create meeting prep, create waiting loop, defer, mark done, mark irrelevant, or monitor. The user should not have to translate the recommendation into an operating step.

Market / operator / macro items must include:

- `signal` — what changed, with source and date.
- `why_it_matters` — why this matters to Todd, not just why it is interesting.
- `restaurant_operator_impact` — how restaurants/operators are affected.
- `restaurant_tech_vendor_implication` — who benefits, who is exposed, which sales cycles may open/stall, where displacement or ROI pressure changes.
- `second_order_impact` — causal chain from macro/operator event to tech-buying or relationship implication.
- `relationship_opportunity` — mapped contacts, active threads, companies, or research gaps.
- `recommended_action` — outreach, monitor, content angle, sales positioning, partner/investor implication, or risk to avoid.
- `timing_priority` — `today`, `this_week`, or `monitor`.

Strategic operator movements must additionally include:

- `strategic_operator_entity_type` — strategic franchise operator, multi-brand franchise group, PE-backed operator, large regional operator, high-influence franchisee group, operator holding company, or consolidator.
- `strategic_operator_movement` — acquisition, divestiture, bankruptcy, restructure, executive movement, concept/geographic expansion, franchise transfer, PE activity, technology standardization, vendor transition, or cross-brand operational behavior.
- influence, relationship value, operational pressure, ecosystem impact, and likely future opportunity scores.
- relationship proximity, known network overlap, mutual connections, existing vendor relationships when known, buying-window probability, follow-up priority, and watchlist bucket.

RB must never merely summarize an operator article. It must interpret, prioritize, contextualize, operationalize, and connect the event to relationship strategy.

### 15. Suppress repeated news

Compare market/operator items against the prior `today.md` and any daily-brief cache available. Do not repeat a news item or market thesis from prior days unless at least one is true:

- there is a fresh source;
- the facts materially changed;
- the relationship/action implication changed;
- Todd has a new active thread, opportunity, or positioning use for it.

Repeated context without a new implication is noise and belongs in `What To Ignore` or should be omitted.

### 16. Write `today.md`

Use the section template from `SCHEMAS.md → today.md`. Omit empty sections. Voice: direct, Midwestern, peer-level, no fluff. Cite source files for any non-trivial claim.

Industry context is optional and subordinate to relationship/action intelligence. Use `system/restaurant_tech_watchlist.md` to define the monitored universe: top 30-50 NRA-adjacent restaurant-tech vendors plus top restaurant brands/operators whose public priorities reveal technology demand. Vendor categories include POS, back office, payments, loyalty/CRM, digital menu boards, online ordering, restaurant AI, POS hardware, drive-thru camera/communication devices, KDS/kitchen ops, guest experience, and enterprise integration layers. Brand/operator demand signals include AI as a strategic pillar, digital sales mix, loyalty/guest data, drive-thru throughput, labor productivity, back-office modernization, POS transitions, franchisee adoption friction, and store-operations pain points. Macro/geopolitical demand signals include tariffs, Middle East/Iran conflict risk, oil/fuel prices, interest rates, consumer spending, labor cost pressure, labor availability, immigration policy, produce/beef/poultry/dairy inflation, supply-chain disruption, commercial real-estate pressure, and regulatory or wage-law shifts. For hospitality and restaurant-tech items, source from social/LinkedIn and vertical trade sources first: operator/founder/buyer posts, target-company announcements, RTN, Hospitality Technology, Restaurant Business, Nation's Restaurant News, Restaurant Dive, QSR Magazine, Fast Casual, conference/session signals, job postings, earnings calls, partner announcements, and similar domain-specific feeds. For public tech/vendor companies and public restaurant brands, earnings call transcripts, investor presentations, SEC filings, market data, stock-price movement, press releases, annual reports, strategic priority decks, product announcements, and company blogs are valid primary signals. Government releases, commodity data, earnings calls, public filings, and primary market data are valid macro inputs. Mainstream news may corroborate financing, M&A, or broad macro context, but it is not sufficient as the primary source for day-to-day hospitality intelligence. If an item is mainstream-only, downgrade confidence, label the source limitation, and include it only when it directly changes a named relationship, opportunity, active thread, or recommended action.

### 17. Refresh `MANIFEST.md`

Overwrite with current computed state. Use the same numbers as today.md — they must agree.

Update:

- Baseline counts (totals, signal_class breakdown, RC tiers).
- Most recent snapshot file name.
- Most recent delta report file name.
- Open loop count + closure summary.
- Circle table (with `circle_type` for each).
- Card count + RCs missing cards.
- Brief count (from `ls system/briefs/*.md | wc -l`).
- Scheduled tasks.
- "Notable in-flight items" — pull from open loops + gap surface.
- "Last updated" = today.
- "Next regeneration" = tomorrow at the configured time.

### 18. Send ChatGPT push + completion email

After the brief is generated and published successfully, the preferred notification surface is a native ChatGPT Task result message, per `settings.json -> daily_briefing.delivery.chatgpt_push_*` and `system/automation/CHATGPT_TASK_DAILY_BRIEF_PUSH.md`.

Because ChatGPT Tasks live in Todd's ChatGPT account/app settings, the RB scheduler does not fabricate this message. The target UX is the ChatGPT-native email/push with a `View message` button that opens a generated ChatGPT task result. External RB email cannot reproduce that behavior; it can only open the RB Custom GPT and ask Todd to send a command.

The native ChatGPT Task should retrieve today's RB Daily Brief through the RB Custom GPT action or a secure hosted RB brief endpoint, then render the brief inside ChatGPT. Until that retrieval path is wired, the interim task may only notify Todd at 5:05 AM Central:

```text
RB Daily Brief is ready. Open the RB ChatGPT cockpit for the action board.
```

Email remains the backup receipt. After the brief is generated and published successfully, send a short completion email to the configured recipient in `settings.json -> daily_briefing.delivery.recipient`.

The email should be concise:

- subject: `RB Daily Brief is ready - <weekday>, <date>`
- one-sentence status that the brief is ready;
- top 3 priorities or executive moves;
- source health / preparation proof summary;
- one or two urgent actions, if any;
- primary CTA into the RB operational layer / daily brief surface.

Do not paste the full brief into the email or push notification. The push/email are the doorbell, not the newspaper itself.

The email must direct Todd to the operational layer where the brief, loops, actions, and follow-up state live. It must not direct him to Codex, Claude, local workspace files, implementation notes, or developer scaffolding. Internal file paths may be recorded in logs for traceability, but they are not user-facing delivery links.

If email transport is not configured, the scheduled run must still generate the brief and record/report delivery as `pending_transport` rather than pretending the email was sent.

### 18b. Schedule the 16:30 closeout

The morning brief opens the day. The closeout closes it. Per `settings.json → daily_briefing.operational_layer.end_of_day_closeout`, the closeout fires at **16:30 America/Chicago** with this scope: review what closed today, what slipped, what is waiting, what passive evidence resolved, and what should roll to tomorrow, plus one recommended setup move for tomorrow morning.

The deterministic compute lives in `system/scripts/loop_autopilot.py`, which
sequences passive verification and closeout through the existing guarded modules.
The scheduler invokes:

```bash
python3 system/scripts/loop_autopilot.py --phase closeout --confirm
```

That applies evidence-backed loop closures, then writes
`system/closeouts/YYYY-MM-DD.md` (snapshotting any prior file at the same path).
The same closeout payload is available through `POST /closeout` (dual-mode:
`confirm=false` previews, `confirm=true` writes).

Wiring on macOS uses LaunchAgent — the same mechanism documented for the 05:00 morning run. The closeout job and the morning job are independent: failure of either does not block the other. If LaunchAgent is not wired yet, the closeout can still be invoked manually or through the API from a Custom GPT.

### 19. Validate before commit

Before writing either file:

- today.md sections in correct order.
- All numbers cited in today.md match what's in MANIFEST.md.
- All file paths cited exist on disk.
- If this run did NOT mutate `baseline_index.json`, no schema validation needed (the brief is read-only against baseline). If it did (rare for the daily brief; common for ingestions), run `python3 system/schemas/validate.py` and confirm it passes.

If a validation fails, do not write either file. Log the failure to the operator with the specific check that failed.

## Output contract

Two files written (overwriting prior versions):

- `system/today.md` — daily brief.
- `system/MANIFEST.md` — current state summary.

Operator chat output: one line, either `today.md and MANIFEST.md regenerated for YYYY-MM-DD; completion email sent.` or the failure message from step 19. If email transport is not configured, say `brief generated; completion email pending transport configuration.`

## Failure modes

- **Date drift between sources** — if `today.md` says "Tuesday" but the system date is Monday, halt and surface the conflict.
- **Baseline parse error** — JSON malformed in `baseline_index.json`. Restore from the most recent snapshot and surface to operator. Do not write today.md from a broken baseline.
- **Card folder mismatch** — RC count in baseline doesn't match card files plus RCs-missing-cards. Surface as gap in today.md.
- **Empty crossings + empty loops + empty meetings** — write today.md anyway. A quiet day is a valid output (ARCHITECTURE.md → "It does not optimize for activity").

## Voice

Direct, Midwestern, peer-level. No buzzwords, no exaggerated enthusiasm. No one-line paragraphs. Conversational, concise. Stats = trust — counts, dates, source files for every claim.

## Provenance

This protocol was lifted from the prose specification in `ARCHITECTURE.md → "The daily brief"` and the inline scheduled-task prompt at the time it was created. If the prose spec is ever updated, refresh this protocol in lock-step.
