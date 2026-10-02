# Claude Handoff — Next Sprint Kickoff

**Date:** 2026-05-20  
**Builder:** Codex  
**Sprint recommendation:** Canonical CoS Response + Market / Operator Intelligence + Sent Email Awaiting-Response Signals  
**Context:** This follows the RI Event Intake sprint return (`CLAUDE_HANDOFF_2026-05-20_RI_INTAKE_RETURN.md`) and the 2026-05-20 field tests in ChatGPT Custom GPT.

## TL;DR

RI event intake is now implemented and smoke-tested. The next sprint should make RB's morning and ad hoc CoS responses more deterministic by moving structure into code and source-backed data, not relying on Custom GPT prose discipline.

Three live defects motivate the sprint:

1. **Daily brief market/operator gap:** Today's daily brief clearly read email/relationship state, but contained no sourced industry news. Its `Market / Industry Read` was synthetic GPT context, not fresh sourced market/operator intelligence.
2. **No-response canonical drift:** A negative/no-response update about Simin/Harri, Jeff Coffland, and Global Payments produced coaching/advisory prose instead of canonical operational state.
3. **Sent email blind spot:** Email prep currently focuses on inbound/newest sender. Sent threads where Todd is the most recent sender are counted as `self_sent_skipped`, not converted into last-known-interaction evidence or awaiting-response obligations.

The target product behavior:

> RB should behave like a disciplined relationship operating system: observed signals, state changes, inferences, recommended actions, persistence status, and source grounding. Good advice is not enough unless the system also proves what it observed, inferred, proposed, persisted, ignored, or could not know.

## Anchoring Files / Traces

Read these first:

```text
system/CLAUDE_HANDOFF_2026-05-20_RI_INTAKE_RETURN.md
system/RI_EVENT_INTAKE_DESIGN.md
system/CLAUDE_DEVELOPMENT_MAP.md
system/restaurant_tech_watchlist.md
system/OPERATIONALIZATION.md
system/api/custom_gpt_prompt.md
system/test_traces/2026-05-20-negative-no-response-canonical-cos-drift.md
system/test_traces/2026-05-20-negative-no-response-canonical-cos-drift.json
system/test_traces/2026-05-20-maho-transcript-cos-operationalization-defect.md
system/test_traces/2026-05-19-relationship-signal-interpretation-vs-canonical-operationalization.md
```

Relevant current-code anchors:

```text
system/scripts/daily_brief.py
system/scripts/relationship_signals.py
system/scripts/rb_core.py
system/scripts/fetch_via_session.py
system/scripts/fetch_google.py
system/scripts/refresh_sources.py
system/scripts/ri_intake.py
system/scripts/ri_events.py
```

## What We Know From Codex's Inspection

### 1. Market/operator intelligence is not operationalized

`daily_brief.py` creates a `condensed_industry_context` slot in `canonical_brief.sections`, but nothing populates it. The brief loads relationship, calendar, email, social, interaction, active-thread, loop, and RI-event data, not external market/news sources.

`system/restaurant_tech_watchlist.md` defines the source universe and monitoring standard, but it is still a definition, not a working feed:

- restaurant-tech vendors;
- restaurant brands/operators;
- vertical hospitality sources;
- public-company primary data;
- LinkedIn/social operator/founder/buyer signals;
- mainstream business news only as corroboration.

`system/CLAUDE_DEVELOPMENT_MAP.md` already says this explicitly: market/operator source ingestion is specified but not yet implemented as a repeatable feed.

### 2. No-response updates need a first-class canonical shape

The new trace `T-2026-05-20-002` captures the defect:

- No response from Simin regarding Harri opportunity.
- Waiting on Jeff Coffland for possible McDonald's-side advocacy.
- No response from Global Payments follow-up email.

Expected canonical response:

```text
Observed signals
State changes
Inferences
Recommended actions
Persistence
```

Rules:

- Silence is not rejection unless explicit rejection evidence exists.
- Waiting can be the correct action.
- Secondary advocacy lanes are dependencies, not guarantees.
- NRA timing can be context, not proof of intent.
- Evidence and inference must be separated.
- Every item should have grounding, confidence, and disposition.

Codex added a prompt-level rule in `system/api/custom_gpt_prompt.md` and a doctrine section in `system/OPERATIONALIZATION.md`, but this still needs code-level enforcement and generated structure.

### 3. Sent mail is currently skipped, not operationalized

Current direct Gmail fetch default:

```text
system/scripts/fetch_google.py
--query default: in:inbox newer_than:14d -in:draft
```

`fetch_via_session.py` can normalize a Gmail `search_threads` response, but the refresh wrapper currently looks for one raw email file per account and does not distinguish inbox vs sent captures.

`rb_core.email_overlay()` currently skips threads whose newest sender is Todd:

```text
self_sent_skipped += 1
continue
```

`relationship_signals._what_to_ignore()` reports those as:

```text
email_self_sent: Threads where Todd was the most recent sender — outbound, not new inbound signal.
```

That is directionally useful, but incomplete. Todd's sent follow-ups are relationship events. They should become:

- last-known-interaction evidence;
- `followup_sent` / `awaiting_response` signals;
- response-window timers;
- daily brief waiting-state guidance;
- proposed loops when no response is expected by a date.

## Sprint Goal

Implement source-backed, code-generated operating structure for:

1. Negative/no-response relationship updates.
2. Sent-email follow-up / awaiting-response state.
3. Market/operator intelligence in the daily brief.

Do this in code first. Prompt edits are secondary and should mostly teach the GPT to render generated structures, not invent them.

## Proposed Build Order

### Step 1 — Canonical response evaluator

Add a small local evaluator script for assistant response traces. Suggested file:

```text
system/scripts/canonical_response_eval.py
```

Purpose:

- Given a text response or test trace, check whether a response includes required canonical sections for the scenario type.
- Start with `scenario=no_response_update`.
- Optionally add `scenario=daily_brief` and `scenario=ri_intake` later.

For no-response updates, assert:

- contains `Observed signals`;
- contains `State changes`;
- contains `Inferences`;
- contains `Recommended actions`;
- contains `Persistence`;
- contains at least one grounding label (`manual_user_provided`, `system_detected`, `inferred`, or `stale_source_limited`);
- contains a disposition (`act_today`, `monitor`, `ask_todd`, or `ignore`);
- does not contain banned unsupported coaching phrases unless quoted as a defect:
  - `strategic advisor`
  - `market positioning`
  - `timing says`
  - `being treated as`
  - broad motivational/career-coach framing.

Smoke fixture:

- Use `T-2026-05-20-002` as the failing/expected trace.
- Add one positive fixture string that should pass.

### Step 2 — Sent email ingestion and overlay

Build a first-class sent-email lane.

Likely implementation options:

1. Extend email raw/fetch files to preserve `last_message_from_self` and thread direction.
2. Add separate per-account sent files:
   - `system/inbox/email_sent.<account_id>.json`
   - or a combined normalized email file with `mailbox: inbox|sent|all`.
3. Extend `refresh_sources.py --email` to look for:
   - `raw_gmail_threads.<id>.json` for inbound/current behavior;
   - `raw_gmail_sent_threads.<id>.json` or `raw_email_sent.<id>.json` for sent.

Direct Gmail default should support a sent query:

```text
in:sent newer_than:30d -in:draft
```

Do not treat sent threads as noise. Create a new overlay section, e.g.:

```json
{
  "sent_followups": [
    {
      "thread_id": "...",
      "sent_at": "...",
      "to": [{"name": "...", "email": "...", "matched_id": "..."}],
      "subject": "...",
      "matched_threads": ["T-..."],
      "matched_contacts": ["..."],
      "expected_response_by": "YYYY-MM-DD",
      "response_status": "awaiting_response|response_overdue|responded|unknown",
      "recommended_action": "monitor|follow_up|ask_todd",
      "confidence": "high|medium|low"
    }
  ]
}
```

Initial heuristic:

- If Todd sent the newest message and no inbound newer message exists, classify `awaiting_response`.
- Default response window:
  - active opportunity or recruiting thread: 3-5 business days;
  - warm professional relationship: 5-7 business days;
  - low-urgency/network maintenance: 7-10 business days.
- If beyond the window, classify `response_overdue` and propose a follow-up action.
- If inside the window, classify `monitor` and explicitly recommend waiting.

### Step 3 — Relationship-signal integration for sent follow-ups

Extend `relationship_signals.py` so outbound follow-ups are no longer only `what_to_ignore`.

Add signal types:

```text
followup_sent
awaiting_response
response_overdue
secondary_advocacy_dependency
```

Each signal should include:

- `source`: `email_sent` or `email`;
- `event_at`: sent timestamp;
- `grounding`: `system_detected` for fetched sent mail, `manual_user_provided` for Todd-stated updates;
- `freshness`;
- `confidence`;
- `strategic_relevance`;
- `recommended_action`;
- active-thread ids if matched;
- evidence with subject/thread id/account id.

Daily brief should surface:

```text
You sent X on May 18; no response yet; wait until May 23.
```

or:

```text
You sent X on May 13; expected-response window has passed; follow up today.
```

### Step 4 — No-response state handler

This can begin as deterministic rendering in `manual_relationship_intake.py` / `ri_intake.py`, or as a separate helper used by the GPT-facing response contract.

Required behavior for Todd saying:

```text
No response from Simin regarding Harri.
Waiting on Jeff Coffland for McDonald's-side advocacy.
No response from Global Payments follow-up email.
```

Expected structured output:

```text
Observed signals:
- Simin / Harri — no response reported by Todd (`manual_user_provided`; confidence medium)
- Jeff Coffland — waiting on possible advocacy lane (`manual_user_provided`; confidence medium)
- Global Payments — follow-up sent, no response reported (`manual_user_provided`; confidence medium)

State changes:
- Harri: active -> pending/no_response
- Jeff Coffland: secondary_advocacy_dependency unchanged
- Global Payments: followup_sent -> awaiting_response

Inferences:
- No explicit rejection detected. Silence lowers Harri confidence but does not close the opportunity.
- Global Payments confidence unchanged if follow-up is still inside normal response window.

Recommended actions:
- [monitor] Harri/Simin — no additional follow-up for 3-5 business days.
- [monitor] Jeff Coffland — leave as secondary dependency unless the response window expires.
- [monitor|act_today] Global Payments — wait or follow up based on sent date.

Persistence:
- status: proposed_write_pending_confirmation or not_persisted
- proposed mutations: RI events and/or waiting-state loop updates
```

Tie this to RI event intake if possible:

- manual_text source type;
- signal types `waiting_on_recruiter`, `followup_sent`, `awaiting_response`, `secondary_advocacy_dependency`;
- proposed loop/open-thread updates but no automatic writes without confirmation.

### Step 5 — Market/operator signal intake

Start minimal. Do not build a giant news crawler first.

Suggested first implementation:

```text
system/scripts/market_signals.py
system/inbox/market_signals.json
system/.cache/market_signals.json
```

Input shape can initially be manually pasted or simple JSON rows:

```json
{
  "fetched_at": "2026-05-20T07:00:00-05:00",
  "items": [
    {
      "title": "...",
      "url": "...",
      "source_name": "Restaurant Business",
      "source_type": "vertical_trade|linkedin_social|public_company_primary|company_blog|mainstream",
      "source_quality": "strong|medium|weak_for_hospitality",
      "published_at": "2026-05-20",
      "company": "...",
      "side": "vendor_supply|operator_demand",
      "category": "restaurant_ai|pos|payments|loyalty|drive_thru|labor|...",
      "signal_type": "product_launch|operator_priority|earnings_signal|partnership|job_posting|event_signal|funding|mna|...",
      "pain_point_or_priority": "...",
      "strategic_relevance": "high|medium|low|none",
      "affected_relationships_or_threads": ["T-...", "person-id"],
      "recommended_action": "act_today|monitor|ask_todd|ignore",
      "confidence": "high|medium|low"
    }
  ]
}
```

First pass can read this file, dedupe by URL/title/company/date, and output top 3-7 items.

Source hierarchy from `restaurant_tech_watchlist.md`:

- Prefer vertical hospitality and operator/social sources.
- Prefer public-company primary data for public vendors/operators.
- Use mainstream news only as corroboration or macro/M&A context.
- If mainstream-only, label `source_quality: weak_for_hospitality` and keep it out of the priority stack unless it directly changes a named action.

### Step 6 — Daily brief integration

Populate `canonical_brief.sections.condensed_industry_context` or rename/add a clearer `market_operator_signals` section.

Requirements:

- The daily brief should include sourced market/operator items when fresh signals exist.
- If market sources are missing or stale, explicitly say so in Data Health / Confidence.
- Do not synthesize broad market theses without source items.
- Every item must carry:
  - source name/url/date;
  - company;
  - source type/quality;
  - signal type;
  - strategic relevance;
  - action disposition;
  - why it matters to Todd's network, active threads, job search, consulting, or positioning.
- Add repeat suppression:
  - do not replay broad theses;
  - repeat only with fresh source, material update, changed implication, or new action.

### Step 7 — API / Custom GPT surface

Only after backend structures exist:

- Add market signals to `GET /daily_brief`.
- Consider `GET /market_signals` local or GPT-exposed later.
- Keep OpenAPI GPT operation count under 30.
- Update prompt to render generated fields, not invent them.

## Acceptance Tests

### A. No-response canonical response

Input:

```text
No response from Simin on Harri, still waiting on Jeff Coffland for possible McDonald's advocacy, and no reply yet from Global Payments after my follow-up.
```

Pass if response includes:

- `Observed signals`;
- `State changes`;
- `Inferences`;
- `Recommended actions`;
- `Persistence`;
- at least one waiting window;
- no unsupported coaching narrative;
- no inference of rejection without evidence.

### B. Sent email awaiting-response signal

Fixture:

- Todd sent a follow-up email to a known active-thread company.
- No newer inbound reply exists.

Pass if:

- sent email is not counted only as `self_sent_skipped`;
- signal appears as `followup_sent` or `awaiting_response`;
- daily brief shows response window and recommended wait/follow-up action;
- last-touch update is proposed/review-first if appropriate.

### C. Response overdue

Fixture:

- Todd sent a follow-up 8 business days ago to an active opportunity.
- No response.

Pass if:

- signal is `response_overdue`;
- recommended action is `act_today` follow-up or `ask_todd` if context is ambiguous;
- proof includes sent date, subject/thread id, account id, and matched thread/contact.

### D. Market/operator signal appears only when sourced

Fixture:

- Add 3 rows to `system/inbox/market_signals.json`.

Pass if:

- daily brief includes sourced Market / Operator Signals;
- each item has source URL/date/type/quality;
- no item appears from generic GPT market memory;
- stale/missing market source state is visible when no file exists.

### E. Repeat suppression

Fixture:

- Same market signal appears two days in a row with no material update.

Pass if:

- day 2 suppresses it or moves it to `What To Ignore`;
- a changed source/action allows it to reappear.

## Validation Commands

Baseline checks to keep green:

```bash
python3 system/scripts/ri_intake.py --smoke
python3 system/scripts/ri_smoke_test.py
python3 system/scripts/validate_openapi_gpt.py --json
python3 system/scripts/api_smoke_test.py
```

New checks to add during the sprint:

```bash
python3 system/scripts/canonical_response_eval.py --smoke
python3 system/scripts/market_signals.py --smoke
python3 system/scripts/relationship_signals.py --json
python3 system/scripts/daily_brief.py --date 2026-05-20 --json
```

## Notes / Constraints

- Do not expose write-capable RI confirm operations to the Custom GPT yet.
- Keep `openapi_gpt.yaml` under 30 operations.
- Do not turn RB into a CRM pipeline or sales forecast.
- Sent email is a relationship signal, but not every sent email deserves a loop.
- Waiting can be the correct action.
- Daily brief industry content must be source-backed or explicitly absent/stale.
- Prompt edits are not enough; generated backend structures should carry the canonical shape.

## Suggested Sprint Name

**Canonical CoS Enforcement + Awaiting-Response Intelligence**

Alternate if market work is included fully:

**Canonical CoS + Market/Operator Signal Intake**

