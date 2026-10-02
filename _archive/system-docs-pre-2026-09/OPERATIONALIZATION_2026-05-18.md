# RB Operationalization Plan — SUPERSEDED

**Status:** SUPERSEDED — archived 2026-09-05. This document's core premise (a ChatGPT/Codex/Claude
three-way role split, with ChatGPT as the daily user interface) no longer describes the live system:
the Custom GPT was retired 2026-08-28 in favor of the rbb-chat Trusted Chat Client as the sole
conversational surface. Kept for historical reference only. See `system/CHARTER.md`'s methodology
section for the current operating model.

**Last reviewed:** 2026-05-18

This document defines how Relationship & Business Builder (RB) should run once it is operationalized across ChatGPT, Codex, and Claude.

The goal is simple: RB should not live inside any single model's context window. RB lives in files, scripts, schemas, APIs, caches, and git history. The models are role-specific clients around that system.

---

## Role split

| Role | Primary job | Should own | Should not own |
|---|---|---|---|
| ChatGPT | Daily user interface and Chief-of-Staff execution surface | Conversation, interpretation, prioritization, user-facing recommendations, drafts, Action calls into RB API | Raw source-of-truth memory, unsupervised edits, hidden arithmetic |
| Codex | File management, memory, mutation, validation, rollback | Canonical files, scripts, schemas, API/MCP surfaces, snapshots, cache refresh, git commits, operational docs | Product philosophy as the final authority |
| Claude | Software engineer and system architect on the sidelines | Architecture, product specs, scoring-model design, protocol design, hard design reviews, feature planning | Daily runtime, long-lived memory, manual recomputation of state |

The practical rule:

```text
ChatGPT talks to Todd.
Codex changes RB.
Claude improves RB.
```

---

## Source of truth

RB source of truth is:

- `system/baseline_index.json`
- `system/cards/`
- `system/briefs/`
- `system/circles/`
- `system/loop_ledger.md`
- `system/active_threads.yaml`
- `system/inbox/*.json`
- `system/scripts/`
- `system/api/`
- `system/mcp/`
- `system/STATUS.md`
- `system/MANIFEST.md`
- `system/today.md`
- git history

No chat transcript is source of truth unless it is written into one of those files or into `system/_sessions/`.

---

## ChatGPT operating contract

ChatGPT is the daily cockpit. It should feel like RB to the user.

### Startup is automatic

`start RB` is deprecated old architecture. The user should not need to type a magic command to latch the system.

On the first message of a session, ChatGPT should automatically:

1. Call `getManifest`.
2. Call `getStatus`.
3. Call `listProtocols`.
4. Summarize the actual operating state in 3-5 lines.
5. Ask what Todd wants to work on.

Keep the useful parts of the old startup idea:

- API reachable / not reachable
- baseline count
- RC count
- active thread count
- trace availability
- source freshness/staleness

Drop the ceremonial language:

- no "SESSION HEARTBEAT — CONFIRMED"
- no "SESSION STATE: LATCHED"
- no "STARTUP STATE: COMPLETE"
- no retry/latch theater in the user interface

### ChatGPT should do

- Call the RB API before answering any network-state question.
- Use `getDailyBrief` for daily state.
- Use `getLoops` for obligations.
- Use `getGapDetection` for missing cards, missing `last_touch`, and contact-field gaps.
- Use `getDrrScore` for relevance explanations.
- Use `findIntro` for intro paths.
- Use `getNetworkAnalysis` and `getNetworkGap` for strategic network-shape questions.
- Use `getInteractionOverlay` for phone/text-derived touch evidence.
- Use `getPostRecommendations` and `getSocialOutboundOverlay` for post strategy and engagement signal.
- Draft messages in Todd's voice after it has grounded the action in RB data.

### ChatGPT may mutate only under explicit confirmation

Write endpoints exist so ChatGPT can execute confirmed user intent through the Codex-backed mutation layer. ChatGPT must not silently mutate.

Allowed mutation pattern:

1. ChatGPT states the exact proposed write.
2. Todd explicitly confirms.
3. ChatGPT calls the relevant write endpoint.
4. ChatGPT immediately calls the relevant read/validation endpoint.
5. ChatGPT reports what changed and whether validation passed.

Examples:

- Close a loop only after Todd says it is closed.
- Add a contact only after Todd supplies enough identity data.
- Touch a contact only when there is real interaction evidence.
- Open or close an active thread only when Todd confirms the strategic context.
- Add own-post or engagement data only from observed social evidence.

### ChatGPT must not

- Invent contacts, scores, dates, source evidence, or loop state.
- Edit files directly.
- Treat stale markdown as more authoritative than live API output.
- Make introductions without checking the intro engine and known suppression heuristics.
- Turn the relationship engine into a CRM pipeline, sales forecast, or deal-value tracker.

### CRM integration boundary

RB should sit above CRM systems such as HubSpot. CRM data is a feed and an action surface; it is not the product.

RB owns:

- relationship signals;
- trust, warmth, and momentum;
- relationship obligations and follow-through;
- strategic context around people and companies;
- timing, preparation, and next-best relationship action.

CRM owns:

- deal values;
- pipeline stages;
- forecasts;
- quota reporting;
- CRM hygiene;
- transactional sales administration.

CRM data should appear in RB only when it changes relationship priority,
opportunity timing, follow-up obligation, preparation, risk, or strategic
focus. RB may write back useful relationship context to CRM when explicitly
confirmed, but RB should not become the system of record for pipeline mechanics.

---

## Codex operating contract

Codex is the memory and mutation engine.

Codex owns:

- schema validation
- deterministic scripts
- API/MCP runtime
- write-back mutation tools
- snapshots before mutation
- rollback when validation fails
- cache refresh
- git commits
- operational status docs

Before handing RB to ChatGPT for daily use, Codex should verify:

```bash
python3 system/scripts/validate_baseline.py --json
python3 system/scripts/refresh_all.py
python3 system/scripts/mcp_smoke_test.py
python3 system/scripts/api_smoke_test.py
```

Current verification as of 2026-05-18:

- Baseline schema: PASS, 2,671 items.
- Integrity checks: PASS, no issues.
- Cache refresh: PASS.
- MCP compute smoke test: PASS, 20/20 tools.
- HTTP API smoke test: PASS, 20/20 GET endpoints.
- Local FastAPI runtime: PASS. `python3 -m uvicorn system.api.server:app --host 127.0.0.1 --port 8765` started successfully with `RB_API_KEY=localtest`; `/health` and authenticated `/daily_brief` returned 200.

Known local dependency note:

- `jsonschema`, `fastapi`, and `httpx` were installed user-local via `python3 -m pip install --user ...`.
- `uvicorn` was installed user-local and runtime-verified.
- `mcp` package install is still pending. The current Python 3.9 package index returned `No matching distribution found for mcp`; MCP compute paths still pass through `system/scripts/mcp_smoke_test.py`, but client registration needs a compatible Python/package path.

---

## Claude operating contract

Claude should be used like an architect and software engineer, not like the runtime.

Use Claude for:

- new feature architecture
- protocol design
- scoring model refinement
- product philosophy
- PRDs and launch docs
- review of output quality
- debugging hard system-design problems

Claude must write durable outputs to files:

- specs to repo root or `system/`
- protocols to `system/protocols/`
- implementation changes to `system/scripts/`, `system/api/`, or `system/mcp/`
- session memory to `system/_sessions/`
- status changes to `system/STATUS.md`

Claude should not be asked to recompute daily state from raw files once a script/API exists. It can critique the result, but Codex scripts own the arithmetic.

---

## Operationalization phases

### Phase 0 — Stabilize local runtime

Status: mostly complete.

- Validate baseline.
- Refresh caches.
- Confirm MCP smoke test.
- Confirm API smoke test.
- Commit current state.
- Keep `today.md`, `MANIFEST.md`, and `STATUS.md` aligned.

### Phase 1 — Stand up local API for ChatGPT

Goal: ChatGPT can call RB through a private Action.

Steps:

1. Start API with `RB_API_KEY` set.
2. Expose local API through a private tunnel or hosted endpoint.
3. Update `system/api/openapi.yaml` server URL.
4. Create a private Custom GPT.
5. Paste `system/api/custom_gpt_prompt.md` into Instructions.
6. Add the OpenAPI schema as an Action.
7. Configure API key auth with header `x-api-key`.
8. Test the workflows below.

Required workflows:

- "What matters today?"
- "What am I forgetting?"
- "Who can get me into Toast?"
- "Why is Bruce Sellnow high relevance?"
- "What loops are overdue?"
- "What did my phone/text interactions change?"
- "What should I post about this week?"

### Phase 2 — Controlled write execution

Goal: ChatGPT can execute safe mutations through Codex-backed API endpoints after explicit confirmation.

Start with:

- close loop
- add loop
- touch contact
- open/close active thread
- add own post
- add engagement event
- write session memory

Do not initially allow:

- bulk baseline edits
- automatic RC promotion
- automatic relationship-state changes
- automatic outreach
- company intelligence writes

### Phase 3 — Pre-conversation briefing

Build protocol P-017 into a real endpoint and ChatGPT workflow.

Inputs:

- calendar event
- matched attendees
- baseline/card/brief history
- active threads
- loops
- DRR
- intro paths
- recent email/social/interaction signals

Output:

- what matters
- what to ask
- what not to say
- relevant history
- open loops
- draft follow-up paths

### Phase 4 — Apple interaction first-run

P-019 is built but needs real Mac operational setup.

Steps:

1. Grant Full Disk Access.
2. Run Apple message/call fetchers.
3. Review unmatched recurring handles.
4. Backfill phones/emails for known RCs/LKIs.
5. Apply confirmed `last_touch` updates.
6. Re-run daily brief and observe dormancy changes.

### Phase 5 — Company intelligence and action policy

Bring the strategy docs into the live engine:

- wire `RB_Action_Recommendation_Policy.docx` into a recommendation endpoint
- implement company profile schema
- implement momentum/vulnerability scoring
- add company intelligence overlay to daily brief
- add "no action recommended" as explicit output

### Phase 6 — Relationship evidence layer

Build a summarized evidence layer so ChatGPT can answer "what do we know from recent email/calendar/social/interactions with this person?" without direct raw inbox access.

Field-discovered test case:

- During Custom GPT testing on 2026-05-18, ChatGPT correctly identified Bob Gibson as the best Toast path through the RB API.
- Todd then added that he had a recent late-March/early-April conversation and email continuity with Bob.
- The Custom GPT could not inspect raw email bodies because the RB API only exposes `email_overlay`, not relationship-specific thread evidence.
- Codex could search Gmail and found Bob Gibson's accepted calendar invite for 2026-04-09, but not a substantive email body in the connected account.

Claude development task:

- Design `relationship_evidence` / `email_thread_summary` as a safe intermediate layer.
- Inputs: Gmail/calendar/social/interaction overlays, matched baseline contact, active threads, loops, and session notes.
- Output: compact relationship-specific evidence summary with dates, sources, confidence, and recommended next-use.
- API shape: likely `GET /relationship_evidence?id=...` and possibly `GET /email_thread_summary?contact_id=...`.
- Privacy rule: expose summaries and citations/metadata by default, not full raw email bodies.
- Mutation rule: summaries should be refreshable and source-linked, but not treated as canonical memory until written into a brief, card, session note, or relationship evidence file.

### Phase 7 — Time-aware daily prioritization

Build stronger time awareness into daily recommendations so RB can distinguish "today and still upcoming" from "today but already passed."

Field-discovered test case:

- During Custom GPT testing on 2026-05-18, the prompt "What am I forgetting right now? Prioritize only things that matter today." correctly ranked the 2:00 PM CT Global Payments / Genius interview as the highest-priority item.
- The answer was strategically right before the event, but RB should become sensitive to current local time and event state.
- After the event passes, the recommended action should shift from "do not miss the interview" to "capture the outcome, next step, names, timing, and follow-up loop."

Claude development task:

- Add current-time context to daily brief and ChatGPT-facing prioritization.
- Classify calendar items as upcoming, in-progress, recently passed, or stale.
- For recently passed high-relevance events, recommend capture/follow-up rather than attendance prep.
- Expose this through `daily_brief`, `calendar_overlay`, and future pre-conversation briefing.
- Preserve restraint: do not keep completed events at the top of "what matters now" unless there is an open follow-up action.

### Phase 8 — Evidence-backed strategic gap narrative

Build a narrative layer over `network_analysis` / `network_gap` so ChatGPT can explain strategic relationship gaps without drifting into unsupported abstraction.

Field-discovered test case:

- During Custom GPT testing on 2026-05-18, the prompt "What relationship gaps are most strategically important right now?" produced a strategically useful answer.
- It correctly emphasized enterprise restaurant-tech, operator-side relationships, internal champions, contact completeness, and anchor sponsors.
- However, the answer used phrases like "the system is implicitly showing" while not citing the underlying RB data directly.
- The live endpoint data included concrete evidence: unanchored clusters in Global Payments, Qu POS, Toast, NCR Voyix, Oracle; missing cards for Bridgett Hendrickson, Jeff Staley, Jenny Kurdle, Kevin Froese; one inner RC without `last_touch`; and contact-field gaps for active RCs.

Claude development task:

- Add a strategy-facing explanation model for network gaps.
- Each strategic gap should include: claim, supporting RB evidence, affected companies/people, recommended relationship move, urgency, confidence, and "what to ignore."
- Distinguish raw data gaps from inferred strategic gaps.
- Penalize uncited abstraction in ChatGPT-facing answers.
- Prefer language like "RB evidence suggests..." followed by concrete signals over "the system implicitly shows..."

### Phase 9 — Relationship signals from the last 24 hours

Promote the last 24 hours of relationally relevant LinkedIn, email, text, phone-call, and calendar changes into the daily briefing as a first-class section.

This is not inbox management. It is relationship signal detection.
It should also produce micro-reconciliation prompts where RB has enough signal to know something matters but not enough certainty to mutate state.

Field-discovered test case:

- On 2026-05-18, Todd received a high-signal inbound email from Ish Singh at Maho.
- The current `email_overlay` was stale (`fetched_at` 2026-05-16) and did not include the message in the daily brief.
- Codex Gmail search found the thread:
  - Todd sent `Next Steps` on 2026-05-01 after a walkthrough, framing his background around helping operators and tech companies bridge product capability into real-world operations and sales cycles.
  - Ish replied on 2026-05-18, agreed that marketing the broader Maho platform is harder because each buyer segment cares about different things, and invited a deeper dive on Todd's background, thoughts on Maho, opportunities, and challenges.
- This should have surfaced as a high-relational-signal item because it contains inbound interest, strategic agreement, a next-step invitation, and opportunity surface area.

Claude development task:

- Add a "Relationship Signals From The Last 24 Hours" section to `daily_brief`.
- Include relevant signals from LinkedIn/social activity, email, texts/iMessage/SMS, phone calls, and calendar events from the last 24 hours, filtered by relationship and strategic relevance.
- Extend email ingestion beyond inbox/sent to include deleted/trash and junk/spam folders when permissions allow, preserving original mailbox/labels on each thread.
- Treat deleted/junk folders as recovery surfaces: lower-trust, higher-noise, and surfaced only when they contain likely relationship, opportunity, scheduling, recruiting, intro, advisory, or active-thread signal.
- Include HubSpot/CRM signals when connected and relevant: notes/activity from known contacts, high-value relationship tasks, company/contact changes tied to active threads, and CRM state changes that alter relationship priority or timing.
- Do not include generic HubSpot counts or raw CRM task dumps. CRM data belongs in the brief only when it changes relationship priority, opportunity timing, follow-up obligation, or strategic focus.
- Treat each input channel as evidence, not truth by itself. The output should explain what changed, why it matters, who it affects, and whether action is warranted.
- Prioritize known RC/LKI/LMI contacts, active threads, target companies, introductions, opportunity signals, hiring/recruiting movement, follow-up obligations, emotional tone changes, and momentum shifts.
- Exclude ordinary inbox noise, newsletters, confirmations, low-context notifications, and purely transactional messages unless they touch an active thread.
- Exclude ordinary junk/trash noise, but count suppressed items by reason so RB can prove it looked without flooding the brief.
- Each surfaced signal should include: person/company, source, timestamp, why it matters, suggested action, urgency, confidence, and whether a loop/thread/contact update is recommended.
- Each surfaced deleted/junk signal should additionally include mailbox/label, `mailbox_warning`, and a recovery-oriented action such as review, restore, move out of junk, reply, monitor, or ignore.
- When classification is unclear, output a narrow reconciliation question rather than guessing. Example: "Should this Ish/Maho exchange become an active thread, a relationship-warming event, or both?"
- Start with metadata and snippets/summaries; avoid exposing full raw email bodies in ChatGPT by default.
- Make stale ingestion visible: if LinkedIn/social, email, text, phone, or calendar feeds have not refreshed within 24 hours, the brief should explicitly say so and recommend refresh/manual paste for high-signal messages.

### Phase 9A — Micro-reconciliation queue

Build a daily and real-time uncertainty resolution layer.

Purpose:

- uphold "RB never guesses"
- avoid dead-end "I don't know" answers
- convert missing/conflicting data into small graph improvements
- reduce ambiguity over time, especially during onboarding and when new contacts appear

Morning brief behavior:

- Batch only the reconciliation questions worth Todd's attention today.
- Prioritize high-value RC/LKI/LMI contacts, active threads, strategic opportunities, stale `last_touch`, missing contact fields for high-tier relationships, conflicting company/title evidence, and high-signal emails needing classification.
- Keep prompts concrete and answerable.

Real-time behavior:

- If Todd asks a question and RB lacks a necessary fact, ask the smallest useful clarification.
- After Todd answers, propose the confirmed write if an API endpoint exists.
- If no write endpoint exists, produce a "manual capture needed" recommendation.

Claude development task:

- Add reconciliation candidates to the planned `relationship_signals.py` output.
- Add a daily brief section such as `Reconciliation Needed`.
- Add API support later as `GET /reconciliation_queue`.
- Candidate fields: `id`, `entity_type`, `entity_id`, `prompt`, `reason`, `source`, `urgency`, `confidence`, `recommended_mutation`, `safe_to_write`.

### Phase 9B — Manual LinkedIn / social message intake

Field-discovered defect:

- A LinkedIn message screenshot produced a long prose interpretation instead of a compact relationship-intelligence response.
- The answer did not clearly state RB match status, grounding, confidence, signal type, strategic relevance, recommended action, or reconciliation need.
- The user had to infer whether the message mattered.

Broader product rule:

- All inputs should pass through a relationship-intelligence screen.
- Plain text conversation should be scanned quietly. If no RI is present, RB should not say so; that would make normal conversation annoying.
- If plain text contains RI, RB should acknowledge the signal, classify it, record or propose capture, and make the relevant recommendation.
- Attachments, screenshots, images, pasted files, transcripts, exports, PDFs, docs, spreadsheets, and copied artifacts should always receive explicit RI-scan acknowledgement:
  - RI found: report the signal and what RB is doing/recommending.
  - No RI found: say so briefly, then proceed with the user's requested task.
- This distinction is central to the UX: artifacts need processing confirmation; ordinary conversation needs low-friction continuity.

Canonical behavior:

- Treat pasted/uploaded LinkedIn messages, DMs, and screenshots as candidate relationship signals, not generic content to summarize.
- Extract only visible facts: sender, company/title, profile URL, timestamp, message gist, explicit ask.
- Attempt a card lookup only when the sender name can be safely mapped to a likely contact id.
- Label the signal as `manual_user_provided`, not `system_detected`.
- Return the compact intake format:
  - RB match
  - grounding
  - confidence
  - relationship value
  - trust signal
  - signal type
  - strategic relevance
  - risk
  - one-sentence why-it-matters
  - recommended action
  - workflow disposition (`persist_ri`, `crm_worthy`, `create_loop`, `archive_or_suppress`)
  - one reconciliation question if needed
- Suppress long prose unless Todd asks for deeper analysis or a drafted reply.
- Low-value solicitations should resolve to an explicit no-action state:
  `signal_type: noise`, `relationship_value: low`, `trust_signal: weak`,
  `strategic_relevance: none`, `recommended_action: ignore`,
  `persist_ri: no`, `create_loop: no`, and
  `archive_or_suppress: suppress`.
- The governing principle should be explicit when relevant: trust over reach,
  credibility over vanity metrics, and relationship quality over synthetic
  engagement.

Field-discovered defect:

- On 2026-05-19, a low-value LinkedIn solicitation from Jeremy Collins /
  socideveloper.com was correctly judged as spam-like, but the response stayed
  in smart advisory commentary instead of canonical RB operational judgment.
- Trace captured as `T-2026-05-19-007`.
- Architectural rule reinforced: good advice is not sufficient for canonical
  CoS behavior. Manual social-message assessments must classify,
  operationalize, suppress noise, preserve executive attention, and produce
  reusable structured judgment.

Future development:

- Add a write endpoint for manual social/message signals, distinct from public social posts.
- Store these as review-first RI events with source `manual_social_message`.
- Allow ChatGPT to propose `ignore`, `monitor`, `open_loop`, `open_thread`, or `add_contact` based on signal quality.
- Include manually captured high-signal LinkedIn messages in the next daily relationship-signal scan.

### Phase 9C — Negative/no-response relationship updates

Field-discovered defect:

- On 2026-05-20, Todd reported three waiting-state signals: no response from
  Simin regarding Harri, waiting on Jeff Coffland for possible McDonald's-side
  advocacy, and no response from a Global Payments follow-up email.
- The response was conversationally useful but drifted into strategic coaching
  and speculative framing instead of canonical RB operational reporting.
- Trace captured as `T-2026-05-20-002`.

Canonical behavior:

- Treat silence/no-response as a relationship state signal, not as proof of
  rejection and not as an invitation for broad motivational framing.
- Render the response in four explicit partitions:
  - `Observed signals`
  - `State changes`
  - `Inferences`
  - `Recommended actions`
- Keep evidence and inference separate. Label grounding and confidence on each
  inference.
- State opportunity/thread transitions such as:
  - `active -> pending/no_response`
  - `followup_sent -> awaiting_response`
  - `waiting_on_external -> unchanged`
- Identify dependencies explicitly, e.g. Jeff Coffland as a secondary advocacy
  lane for McDonald's-side validation, without implying he has acted unless
  there is direct evidence.
- Name waiting windows. Example: no additional follow-up for 3-5 business days
  when the silence window is still normal.
- Record persistence status: `not_persisted`,
  `proposed_write_pending_confirmation`, or `persisted`.

Negative guarantees:

- Do not infer rejection from silence alone.
- Do not introduce identity or market-positioning narratives unless grounded in
  retrieved evidence.
- Do not blend Todd-provided facts, RB inference, and speculation in a single
  prose paragraph.
- Do not recommend activity for its own sake; waiting can be the correct action.

### Phase 10 — Job-opportunity intelligence from email

Add a job-seeker intelligence layer that evaluates inbound job/recruiting emails against Todd's actual skillset, target markets, relationship graph, and strategic positioning.

This is not generic job-alert parsing and not a simple "apply / do not apply" filter. The goal is to reduce recruiting noise and identify the best opportunity paths.

Field-discovered requirement:

- Todd receives many emails with potential positions.
- The recruiting environment is noisy and low-signal.
- RB should identify which roles are actually worth attention and recommend higher-leverage paths into those opportunities beyond cold application.

Claude development task:

- Add a daily brief section such as "Job Opportunity Signals."
- Ingest recent recruiting/job emails from the last 24-72 hours.
- Extract: role title, company, domain, seniority, location/remote status, compensation if present, recruiter/source, deadlines, and application path.
- Score against Todd fit:
  - restaurant tech / hospitality tech / payments / POS / operations alignment
  - operator + GTM + implementation translation fit
  - enterprise or multi-unit complexity
  - leadership / customer success / sales / partnerships / consulting fit
  - risk flags: pure cold AE spray, weak domain fit, junior role, unclear company, low signal recruiter
- Correlate each opportunity with RB graph:
  - known contacts at company
  - warm intro paths
  - active threads
  - company cluster strength/weakness
  - recent market/company signals
- Output recommendation classes:
  - pursue through warm path
  - investigate before applying
  - monitor
  - ignore/no action
  - apply only if warm path emerges
- For high-fit roles, recommend specific non-application paths: who to ask, what angle to use, what evidence/positioning to lead with, and whether to create an active thread or loop.
- Preserve restraint: RB should aggressively suppress low-fit recruiting noise.

Field-discovered defect:

- A recruiting follow-up session on 2026-05-19 involved two active opportunity
  threads:
  - Ryan Hildebrand / Global Payments
  - Simin Gorgulu / Hari / TritonExec / McDonald's account role
- The assistant drafted and refined useful follow-up emails.
- The assistant correctly interpreted recruiting momentum and recommended
  timing.
- No RB persistence, card update, active-thread update, opportunity-state
  transition, loop creation, DRR/relevance update, or trace was confirmed in
  the original session.
- Trace captured as `T-2026-05-19-006`.

Canonical requirement from the recruiting RI test:

- Recruiting/interview conversation text is passive RI when it contains:
  - completed interview or recruiter screen
  - requested resume
  - resume sent
  - requested second conversation
  - stated high user interest
  - explicit follow-up timing
  - hiring-manager, recruiter, or account-owner names
  - process status movement
- Plain conversational turns should be scanned quietly; if no RI is present,
  do not mention the scan.
- If recruiting RI is present, respond with a compact capture line and explicit
  persistence status:
  - `not_persisted`
  - `proposed_write_pending_confirmation`
  - `persisted`
- Review-first proposed writes should include:
  - create/update recruiter and hiring-manager contacts
  - open/update active job-opportunity thread
  - set opportunity stage such as `screen_complete`, `interview_complete`,
    `second_conversation_requested`, `resume_sent`, or `waiting_on_recruiter`
  - create follow-up loops for scheduling, resume delivery, and status checks
  - update relationship priority/urgency when momentum is high
- If no write endpoint/action is called, the assistant must not imply RB
  recorded the opportunity.

Specific expected extraction from `T-2026-05-19-006`:

- Ryan Hildebrand / Global Payments:
  - interview completed on 2026-05-18
  - positive outcome
  - second conversation requested for the same or following week
  - NRA / industry context shared
  - candidate interest high
  - momentum positive
  - expected loop: send/follow up on availability for next conversation
- Simin Gorgulu / TritonExec / Hari:
  - recruiter screen completed
  - resume requested and sent to `sgorgulu@tritonexec.com`
  - Hari McDonald's account role is high-interest
  - follow-up email sent
  - expected loop: check back after 3-5 business days if no response
  - expected active thread: Hari / McDonald's account opportunity

### Phase 11 — Conversation artifact ingestion

Add a daily ingestion layer for transcripts, AI notetaker recaps, and meeting chat logs from tools like Fathom, Zoom, Otter, Fireflies, Teams, and native Zoom saves.

This turns RB from a reactionary administrative assistant into a measured Chief of Staff: it should process yesterday's conversations before the daily brief, identify what changed, and recommend what matters.

Development direction:

- Build protocol P-020 as the canonical shape for conversation artifact ingestion.
- Add onboarding source setup: ask which notetakers, meeting tools, chat exports, email/calendar systems, phone/text sources, and manual import folders the user actually uses.
- Create a user-specific watch configuration, likely `system/source_watch.yaml`, that records enabled folders, source types, freshness thresholds, and whether each source is automatic, semi-automatic, or manual.
- Support per-tool watched folders where available, such as Fathom exports, Zoom transcript/chat save folders, Otter/Fireflies/Teams exports, and a generic RB Inbox drop folder.
- Make source limitations explicit during onboarding: RB can automatically process connected accounts and watched folders, but LinkedIn, screenshots, private platforms, and unexported notetaker content may still require manual upload/copy-paste unless the user provides a reliable export path.
- Support folder-based import from `system/inbox/conversation_artifacts/`.
- Add adapters for Fathom, Zoom transcripts, Zoom chat logs, Otter/Fireflies/Teams-style exports, and manual `.txt` / `.md` notes.
- Normalize all sources into one artifact shape.
- Extract people, companies, relationship signals, action items, loop closures/openings, active-thread touches, and promotion candidates.
- Classify transcript action items without turning RB into a generic to-do
  list. Promote only obligations that affect relationship momentum,
  opportunity movement, future meeting prep, trust, or strategic follow-through.
  Generic admin tasks should be suppressed, monitored, or left out unless tied
  to an active thread.
- Promoted transcript action items should become review-first loops,
  `waiting_on` state, daily-brief injections, or active-thread next actions.
- Run in review-first mode by default: proposed briefs/loops/baseline updates before canonical mutation.
- Integrate into daily brief as "Conversation Signals Since Yesterday."
- Treat low-value transcripts as suppressed/no-action with a reason, not as hidden clutter.
- Preserve raw transcripts in git-ignored inbox folders; generated briefs carry provenance but do not commit raw transcript content by default.

Field-discovered defect:

- A Fathom transcript for `Todd <> PerfectHire - QSR Platform Review - May 19`
  was pasted into an RB-style session.
- The assistant produced useful relationship analysis but no RB action was
  called.
- No RI persistence, card update, active-thread update, loop creation,
  Interaction Brief, or trace/log was confirmed during the original session.
- Trace captured as `T-2026-05-19-005`.

Canonical requirement from the PerfectHire test:

- A Fathom link alone is not sufficient unless RB can access the transcript
  through an authenticated connector/export path.
- A pasted Fathom transcript is a valid conversation artifact and must run
  through the RI intake screen.
- RB must identify and propose capture for:
  - Olivia Nielsen / PerfectHire as a high-signal warm strategic relationship.
  - Max Holmes / PerfectHire CTO as a medium-high new product/technical
    relationship.
  - Matt Chalzi / PerfectHire CEO as a pending-introduction target.
  - PerfectHire as a discovery/advisory/consulting opportunity.
- RB must propose review-first writes:
  - create or update relevant contacts
  - open or update a `PerfectHire / QSR Platform Review` active thread
  - open a loop for the Matt Chalzi introduction
  - open a loop for follow-up/prep if still outstanding
  - create an Interaction Brief or RI event anchored to the meeting date
- The response must explicitly say whether the RI is:
  - `not_persisted`
  - `proposed_write_pending_confirmation`
  - `persisted`
- If no write action is called, the assistant must not imply that RB captured
  or remembered the transcript.

Second field-discovered defect:

- Two Maho strategic calls were analyzed:
  - April 22 introductory/background discussion.
  - April 29 product walkthrough and strategic discussion.
- The assistant produced strong advisory synthesis and Tuesday meeting prep.
- The assistant correctly recognized:
  - Ish Singh's validation of Todd's background.
  - Maho's need for GTM/adoption/enterprise-positioning guidance.
  - "free consulting drift" risk.
  - Tuesday should not restart background from scratch.
- But the assistant did not prove RB system action:
  - no persistence proof;
  - no relationship mutation confirmation;
  - no opportunity-state update;
  - no future prep loop confirmation;
  - no Tuesday daily brief injection confirmation;
  - no canonical trace/action report.
- Trace captured as `T-2026-05-20-001`.

Canonical requirement from the Maho test:

- Good advice is not enough. RI-bearing conversation analysis must separate:
  1. advisory interpretation, from
  2. RB system action.
- The response must explicitly render a canonical operational block:
  - `Relationship intelligence detected — RB recorded/proposed/skipped the following...`
  - affected entities;
  - relationship state;
  - opportunity stage/temperature;
  - signal strength and confidence;
  - risk state, including unpaid-consulting drift when relevant;
  - recommended posture;
  - next actions;
  - priority tasks captured/proposed;
  - persistence status;
  - loops created/proposed;
  - daily brief injection status;
  - trace/source references.
- Avoid vague action language such as:
  - "should be marked";
  - "would likely";
  - "could be".
- Preferred wording:
  - "RB recorded..."
  - "RB proposed..."
  - "RB did not persist this yet..."
  - "Pending confirmation..."
- Future-meeting commitments must create or propose dated prep loops.
- High-priority future meetings must be injected into the relevant daily brief
  or queued for daily brief rendering with objective, risks, desired posture,
  expected takeaways, and commercial implications.
- Transcript action items must be classified as relationship obligations,
  opportunity obligations, meeting prep, daily brief injections, admin tasks,
  or suppressed noise. RB should keep Todd on track with priority tasks, not
  become a glorified to-do list.

### Phase 12 — Event-sourced RI architecture

RB is evolving from relationship snapshots to event-sourced relationship intelligence.

Current state should become a projection of immutable RI events, not the primary source of truth.

Development direction:

- Treat every RI signal as a timestamped event.
- Require both `event_at` and `captured_at`; `event_at` is the date of intelligence and the only valid anchor for recency, daily briefs, `last_touch`, DRR, relationship momentum, and dedupe. `captured_at` is only when RB learned/scanned/uploaded the artifact.
- If Todd uploads old Fathom/Zoom/email/chat files, project each RI signal to the original call/message/thread date, not the upload date. The upload may create a current review task, but it must not make old relationship activity look fresh.
- If `event_at` is missing or low-confidence, route to reconciliation before any canonical mutation.
- Compare incoming RI against existing state before projection. Reject duplicates, preserve stale historical evidence without overwriting newer state, and require explicit correction semantics before older data can replace newer data.
- Add planned `system/ri_events/*.jsonl` event stream.
- Implement P-021 as the architectural migration protocol.
- Preserve current files during migration:
  - `baseline_index.json` as person-index projection
  - `cards/` as RC projection
  - `briefs/` as append-only evidence view / backfill source
  - `loop_ledger.md` as open-obligation projection
  - `active_threads.yaml` as strategic-context projection
- Add event fields for `captured_at`, `event_at`, source, entity match decision, dedupe decision, signal type, confidence, strategic relevance impact, relationship warmth impact, and persistence verification status.
- Add date confidence and stale/duplicate decision fields so uploaded historical artifacts cannot silently rewrite current relationship state.
- Build replay/projection checks before replacing current computation paths.

The core product question becomes: "What sequence of relationship events produced the current state?"

### Phase 13 — Legacy RB thread transfer

Todd is downloading prior RB 8.0/RB 9.0 thread exports and storing them in `RB 8.0 transfer data/`.

These are valuable historical source artifacts, but they must not automatically overwrite current RB 9.0 canonical state.

Development direction:

- Treat legacy transfer files as git-ignored source artifacts.
- Monitor the folder during `refresh_all.py` and cache new/changed/removed file deltas for review.
- Implement P-022 as the safe ingestion/reconciliation protocol.
- Extract durable architecture, governance, persistence, and product decisions.
- Classify each finding as already represented, missing, conflicting, or deprecated.
- Apply only approved deltas to current architecture/protocol docs.
- Preserve the transfer folder as a source archive, not as live memory.

### Phase 14 — Trace mode and developer test logs

Restore the RB 8.0 developer-level trace capability for RB 9.0.

This is required because ChatGPT's visible conversation is not enough to debug RB behavior. Todd needs a way to export or show a complete test trace to Codex/Claude without manually reconstructing the session from screenshots.

Trace mode should capture:

- timestamped user prompts
- RB/ChatGPT responses
- intended tool/API operation
- actual endpoint called
- request parameters, with secrets redacted
- response status and compact response summary
- write confirmation text
- before/after validation call when mutations occur
- model reasoning summary, not hidden chain-of-thought
- observed defect or user feedback

Recommended storage:

```text
system/test_traces/*.md
system/test_traces/*.json
```

Recommended API surface:

- `POST /test_traces` to save a structured trace
- `GET /test_traces/recent` to retrieve recent trace summaries

Recommended ChatGPT command:

```text
Export this test session for Codex.
```

The export should be verbatim where the user-facing text is concerned, but must redact API keys and private tokens.

First defect trace captured:

```text
system/test_traces/2026-05-18-rb9-interface-persistence-transcript-test.md
```

Issues exposed:

- RB answered network strategy, job-search leverage, and top-contact reconciliation prompts without retrieving the contact graph.
- RB asked for LinkedIn CSV upload even though the baseline already existed in the operational system.
- RB over-claimed transcript export capability and produced a reconstructed artifact instead of a verbatim transcript.

Mitigation requirement:

- ChatGPT must not answer network graph questions from conversational memory alone.
- ChatGPT must label important answers as evidence-backed, inference-backed, or blocked pending retrieval.
- Transcript export must distinguish raw transcript, reconstructed summary, and operational test trace.

### Phase 15 — Canonical CoS daily briefing architecture

Fix the daily brief so it cannot collapse into industry-news summarization.

First defect trace:

```text
system/test_traces/2026-05-18-rb9-daily-brief-cos-vs-news-test.md
```

Problem:

- A test daily brief generated relevant restaurant-tech / AI / payments news commentary.
- It did not perform relationship graph retrieval, relationship impact analysis, network activation, opportunity prioritization, or next-best-action orchestration.
- This optimized for relevance, not executive usefulness.

Canonical CoS briefing requirements:

- **Fresh-source preflight:** before each daily brief, refresh available
  sources or explicitly report which sources could not refresh. Run the RI
  scans from current source caches before writing the brief. A no-signal
  conclusion is invalid when relevant sources are stale or skipped.
- **No-repeat news discipline:** do not repeat the same market/news item day
  after day. Industry/vendor/operator items may reappear only when there is a
  fresh source, a material update, a changed implication, or a new action. Prior
  context without a new decision is noise.
- **Daily operating-system shape:** the brief should answer what Todd should
  know, ignore, decide, and do today that he would not have reliably seen on
  his own. It should feel like a morning executive operating system, not a
  newsletter.
- **Top 3 moves:** lead with no more than three named moves. Each move should
  include why now, who it affects, exact next action, confidence, and consequence
  of doing nothing.
- **What changed since yesterday:** include a delta layer before broad context.
  Name relationship-state changes, opportunity-temperature changes, new/expired
  windows, stale-source changes, and fixed/resolved defects that affect today's
  trust level.
- **Opportunity temperature:** each active opportunity/thread should resolve to
  a state such as `warming`, `active`, `waiting`, `cooling`, `stalled`,
  `needs_decision`, or `suppress`.
- **Executive priority stack:** top developments ranked by urgency, leverage, and relevance to Todd.
- **Relationship impact analysis:** which people/companies matter now, whose priority changed, which relationships deserve warming, and which outreach windows opened.
- **Opportunity detection:** consulting, advisory, job-search, partnership, thought-leadership, intro, or positioning opportunities.
- **Recommended actions:** explicit named actions such as reach out, reconnect, monitor, publish, investigate, ask for intro, schedule, or ignore.
- **Draft-ready execution:** for the top one or two moves, include or offer the
  exact follow-up email, LinkedIn DM, post angle, intro ask, or meeting opener
  when enough context exists.
- **One sharp question:** ask exactly one graph-improving or decision-improving
  question when ambiguity matters. It should be answerable and immediately
  useful, not a questionnaire.
- **Exact grounding labels:** use the canonical enum values `system_detected`,
  `inferred`, `manual_user_provided`, and `stale_source_limited`. Do not
  paraphrase them as "manual context" or other near-matches.
- **Current-state validation:** do not present historical defects as current
  operating risk unless a current read/validation action confirms they still
  fail. Fixed defects should be labeled historical/resolved or omitted.
- **Stale-source operationalization:** every stale-source-limited claim should
  carry a refresh command or explicit verification step before RB uses it for
  strategy.
- **What to ignore:** every daily brief should include a required suppression
  section before optional industry commentary.
- **Vertical-source discipline:** hospitality/restaurant-tech industry signals
  should come primarily from social/LinkedIn and vertical trade sources, not
  mainstream news. Preferred sources include operator/founder/buyer posts,
  target-company announcements, RTN, Hospitality Technology, Restaurant
  Business, Nation's Restaurant News, Restaurant Dive, QSR Magazine, Fast
  Casual, conference/session signals, job postings, earnings calls, partner
  announcements, and similar domain-specific feeds. Mainstream business press
  can corroborate financing, M&A, and macro context, but should not be the
  source of truth for day-to-day hospitality intelligence.
- **Restaurant-tech company universe:** monitor the top 30-50 companies that
  materially shape the restaurant operating stack, especially NRA-adjacent
  exhibitors, sponsors, speakers, and launchers. Categories include POS, back
  office, payments, loyalty/CRM, digital menu boards, online ordering,
  restaurant AI, POS hardware, drive-thru camera/communication devices,
  KDS/kitchen ops, guest experience, and enterprise integration layers. The
  canonical definition lives in `system/restaurant_tech_watchlist.md`.
- **Restaurant brand/operator demand watch:** monitor top public, scaled
  private, franchise, QSR, fast-casual, casual dining, convenience/foodservice,
  and emerging multi-unit brands for technology priorities and operating pain.
  Earnings calls, investor decks, strategic priorities, annual reports,
  executive interviews, public franchisee communications, conference sessions,
  and operator LinkedIn/social posts are valid buyer-side sources. Watch for AI
  as a strategic pillar, digital sales mix, loyalty/guest data, drive-thru
  throughput, labor productivity, back-office modernization, POS transitions,
  franchisee adoption friction, and store-operations pain points.
- **Public-company tech signal discipline:** for public restaurant-tech,
  payments, POS, AI, marketplace, and enterprise-software companies, RB may use
  earnings call transcripts, investor presentations, SEC filings, market data,
  stock-price movement, press releases, product announcements, and company
  blogs as primary tech-company signal. These should be interpreted through
  relationship/action relevance, not treated as generic stock-news commentary.
- **Pattern recognition:** structural shifts and second-order implications, not isolated article commentary.
- **Personal strategic alignment:** filter every signal through Todd's goals, active threads, current opportunities, relationship graph, and bandwidth.

Non-negotiable:

- Refresh or attempt to refresh sources before the daily brief; otherwise label
  missing/stale sources and do not infer quiet.
- Run the RI scan from refreshed source caches before drafting.
- Industry intelligence is subordinate to relationship/action intelligence.
- A news item only belongs in the canonical RB brief if RB can explain why it matters to Todd, who it affects, and what action or non-action it implies.
- Do not replay news or market commentary from prior days unless something
  materially changed.
- The brief must explicitly say what to ignore.
- Each item should carry evidence, confidence, urgency, affected relationships/companies, and recommended next step.
- Each industry/tech item should carry `source_type` and `source_quality`. If
  the item is mainstream-only, mark `source_quality: weak_for_hospitality` and
  keep it below relationship, opportunity, vertical/social, and public-company
  primary-data signals unless it directly changes a named action.
- Every brief item must resolve to one of: `act_today`, `monitor`, `ask_todd`,
  or `ignore`.

Recommended canonical structure for Claude development:

1. `Top 3 Moves`
2. `What Changed Since Yesterday`
3. `Opportunity Temperature`
4. `Relationship Signals`
5. `Market / Operator Signals`
6. `What To Ignore`
7. `One Question For Todd`
8. `Draft-Ready Actions`
9. `Data Health / Confidence`

Claude development target:

- Update `daily_brief.py` so this structure is generated from API/source data,
  not left to Custom GPT prose discipline.
- Make `refresh_sources.py --all --refresh-signals` the preflight path for
  scheduled daily briefing when local permissions and raw inputs allow it.
- Add a repeat-suppression mechanism comparing against prior `today.md` and/or
  a small daily-brief cache of previously surfaced market items.
- Add opportunity-temperature computation from active threads, loops, source
  signals, and stale-source state.
- Render `what_to_ignore`, reconciliation prompts, source-refresh status, and
  one sharp question as first-class sections.

### Phase 16 — Operator experience level

Add a switch that adapts how RB explains CoS answers based on the user's
desired operating mode. This is separate from verbosity: verbosity controls
length, while operator experience level controls how much scaffolding,
explanation, and trust-building appears around the same evidence-backed answer.

Planned setting:

```json
{
  "operator_experience_level": "expert | intermediate | beginner"
}
```

Modes:

- **Expert:** just the facts. Short answer, ranked items, proof points, next
  move. Minimal explanation. Assumes Todd understands RB's concepts and only
  needs the decision surface.
- **Intermediate:** some explanation. Short CoS answer plus brief reasoning,
  source confidence, and why the recommendation follows.
- **Beginner:** more explanation and trust-building. RB explains what it
  checked, what it found, what is uncertain, and how it is working on Todd's
  behalf. This mode should help a user trust the system without burying the
  answer.

Non-negotiable across all modes:

- Same facts, same proof, same grounding labels.
- Same dispositions: `act_today`, `monitor`, `ask_todd`, `ignore`.
- No invented certainty.
- Stale-source caveats still appear even in expert mode.
- Beginner mode explains more, but does not become a tutorial unless asked.

Development target:

- Add `operator_experience_level` to `settings.json` as a real runtime setting.
- Expose it through `getDailyBrief` or a settings endpoint.
- Teach the Custom GPT prompt to choose the right response density:
  - expert: `Short answer` + `Proof` + `Next move`
  - intermediate: default canonical CoS template
  - beginner: canonical template plus one short "How RB got there" paragraph
- Add a test trace for the same prompt rendered in all three modes.

### Phase 17 — New-user onboarding and adoption

Create the onboarding layer after Todd's RB is stabilized and the reusable
framework is ready for other users.

Canonical planning doc:

```text
system/RB_ONBOARDING_AND_ADOPTION_PLAN.md
```

Goal:

- mostly automated install;
- clear first-run instructions;
- guided intake for user profile, role, preferences, industry, and goals;
- baseline creation from available sources;
- first-value CoS report;
- guided tour;
- best-practices documentation;
- trust-building explanation for what RB checked, what it knows, and what is
  still uncertain.

Non-negotiable:

- Onboarding must produce value quickly, not just configure files.
- RB should explain privacy, local storage, source freshness, and grounding in
  plain language.
- New users should learn the three core questions:
  - "What is important today?"
  - "What am I missing?"
  - "What should I ignore?"
- Onboarding should adapt to `operator_experience_level`.

### Phase 18 — User task intake and whole-life relationship balance

Add a future opt-in layer that lets RB prioritize user-entered to-dos and,
when enabled, support non-business relationships and life-balance commitments.

Canonical planning doc:

```text
system/RB_WHOLE_LIFE_RELATIONSHIP_BALANCE_PLAN.md
```

Goal:

- treat user-entered to-dos as high-signal inputs;
- classify tasks before deciding whether to create a loop, `waiting_on` state,
  daily-brief injection, or suppression;
- prioritize relationally relevant tasks over generic admin work;
- support optional family, friends, service, church/community, recreation,
  vacation, and wellbeing prompts;
- keep the feature disabled by default and controlled by the user.

Planned task classes:

- `relationship_obligation`
- `opportunity_obligation`
- `meeting_prep`
- `personal_relationship_care`
- `service_commitment`
- `church_or_community_commitment`
- `health_wellbeing_prompt`
- `recreation_recovery`
- `vacation_planning`
- `admin_task`
- `suppress`

Non-negotiable:

- RB must not become a generic to-do list.
- User-entered relational tasks outrank inferred reminders.
- Personal/life-balance domains are opt-in only.
- RB must use non-judgmental language.
- RB must not infer sensitive family, religious, health, or emotional
  obligations without explicit user input.
- Wellbeing prompts support balance and follow-through but do not provide
  medical, clinical, or therapy advice.
- Daily brief surfaces only the subset that matters today, affects an active
  thread, protects trust, or connects to user-stated priorities.

---

## Current open issues

- `CLAUDE_DEVELOPMENT_MAP.md` is now the sprint compass for the RB operating
  system: daily brief engine, RI event sourcing, opportunity state,
  restaurant-tech/operator intelligence, draft-ready actions, and canonical
  write UX.
- `STATUS.md` and `MANIFEST.md` must stay aligned after generated counts change.
- `system/api/README.md` and `custom_gpt_prompt.md` must describe write endpoints consistently.
- API smoke test currently validates GET endpoints; POST write endpoints need a dry-run or isolated fixture test before ChatGPT write execution is trusted.
- `today.md` is generated but must be committed after regeneration.
- The Custom GPT should start read-mostly; writes require explicit confirmation.
- P-017 pre-conversation briefing and P-018 network-event ingestion are still designed, not implemented.
- Relationship-specific email/calendar evidence is not yet exposed to ChatGPT; see Phase 6.
- Daily prioritization is date-aware but not yet fully time-state-aware; see Phase 7.
- Strategic gap answers need explicit evidence citations from network analysis outputs; see Phase 8.
- Daily brief does not yet surface high relational-signal email/calendar changes from the last 24 hours; see Phase 9.
- Daily brief does not yet evaluate inbound job/recruiting emails against Todd fit and relationship paths; see Phase 10.
- Daily brief does not yet process prior-day transcripts/notetaker recaps/chat logs into conversation signals; see Phase 11 / P-020.
- RB does not yet have a structured RI event stream; current state is still stored mainly as projections/snapshots. See Phase 12 / P-021.
- Legacy RB 8.0 transfer documents are now monitored for new/changed files, but still need review/reconciliation before any content becomes canonical. See Phase 13 / P-022.
- RB 8.0 trace/developer logging is not yet restored in RB 9.0. This is blocking clean interface testing and issue handoff. See Phase 14.
- Daily briefing can still degrade into industry-news commentary rather than CoS-level relationship/action orchestration. See Phase 15.
- New-user onboarding/adoption is planned but not implemented. See Phase 17 and `system/RB_ONBOARDING_AND_ADOPTION_PLAN.md`.
- User task intake and whole-life relationship balance are planned but not implemented. See Phase 18 and `system/RB_WHOLE_LIFE_RELATIONSHIP_BALANCE_PLAN.md`.

---

## Default daily flow once operational

1. Todd opens ChatGPT RB.
2. ChatGPT calls `getManifest`, `getStatus`, and `getDailyBrief`.
3. ChatGPT summarizes the real operating state.
4. Todd asks for action.
5. ChatGPT calls the narrowest RB endpoint.
6. If action requires a write, ChatGPT asks for confirmation.
7. Codex-backed API mutates files with snapshot/validation discipline.
8. ChatGPT reports the result.
9. Session summary is written to `system/_sessions/`.

This is RB as an operating system, not a chat thread.
