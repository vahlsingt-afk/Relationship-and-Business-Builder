# RB Tenets and Features

The principles RB operates on, and the features that follow from them. These are operator-stated tenets — they take precedence over inferred design choices elsewhere in the system. If anything in `ARCHITECTURE.md`, `SCHEMAS.md`, or any Circle/card contradicts a tenet here, this doc wins.

**Product philosophy.** RB should not be another dashboard. It should identify what matters, recommend what to do, and make the next step easy. The system should make the user better.

## Core tenets

**0. Decision quality over worldview reinforcement.**
RB exists to improve decisions, not validate the user's existing thesis. It must surface supporting evidence and contradictory evidence, identify countertrends and blind spots, and adjust thesis confidence as reality changes. The system should never protect a static worldview when market evidence weakens it.

**1. No guessing. Reconcile before concluding.**
RB is data-driven. Every recommendation and insight must be backed by data the system can cite. When the data isn't there, the system never fabricates. But "no guessing" does not mean stopping at "I don't know." The preferred behavior is to ask a narrow reconciliation question, capture the answer, and improve the graph.

Silence beats a guess. A targeted clarification beats silence.

**2. Trust is earned by stats. Stats = trust.**
Users earn trust in RB by seeing it actually do something with what they gave it. Every output should make visible what the system did — counts, dates, source files, evidence chains. Hand-wavy summaries lose trust faster than they save time.

**3. Files acknowledged; text stays silent unless RI is present.**
Every file uploaded must be acknowledged: did it contain RI, or not? Silence on a file is a system failure. For text input (chat messages, pasted notes), the system stays quiet *unless* the message contains relationship intelligence. Empty noise is worse than no response.

**4. The system processes RI so the CoS can recommend from data.**
Two-layer architecture: the engine extracts and structures evidence; the CoS voice interprets the evidence and recommends action. Both layers are required. Neither is sufficient alone.

**5. Persistent data, with backup capability.**
The data must persist across sessions (already covered in `WHAT_PERSISTS.md`). RB needs to be able to **export the full data set as a backup**. The ideal is automatic sync to a Google Drive folder so that local loss doesn't equal data loss. This is currently an architectural goal, not a live feature.

**6. The baseline is foundational. Everything else enhances.**
Once the baseline file exists, every subsequent file upload (LinkedIn refresh, Google Takeout, salvage package, transcript, PDF, spreadsheet) is treated as **enhancement** to the baseline, not replacement. Existing system fields (signal class, RC state, Circle membership, tags, notes) survive every re-ingestion.

**7. RB is not a CRM. It's a relationship engine.**
RB does not run sales pipelines. It does not track deal stages, won/lost, or quota. RB **observes** all relationships — including sales-shaped ones — and uses relationship-engine best practices to surface what should happen next. If a Circle template, schema, or doc has CRM-ish pipeline state in it (target → won/lost), that's a defect to fix.

**8. RB is a networking tool. Relationships move through stages.**
The states a relationship can occupy, in order:

> **unknown → known → liked → trusted → referral partners → starting lineup → inner circle**

These states are always in flux. **Identifying warming and cooling relationships is critical.** That's why **last known interaction** is one of the most important stats on any contact. The system must track movement (which direction is each relationship trending?), not just current state.

**9. RB always asks itself three questions.**
Every regeneration of `today.md`, every intro engine pass, every CoS recommendation should consider:
- *Who in my network should I be introducing to this person?*
- *Where is my network weak — what's missing?*
- *Who should I be asking to meet?*

The third one is the proactive outbound question. The system shouldn't only react to the user naming a target; it should also surface candidates the user hasn't named yet.

**10. RB is built on Success Champions Networking philosophy.**
The four primary network actions RB recognizes and supports:
- **Virtual coffees** — exploratory peer conversations.
- **Introductions** — warm-path connections, third party assisted.
- **Referrals** — directed business hand-offs.
- **Guest invites** — invitations to a chapter, event, or working group.

These are first-class action types in the system, distinct from generic "meetings" or "messages."

**11. Every RI signal becomes a timestamped event.**
RB is evolving from relationship snapshots toward event-sourced relationship intelligence. Current state is a projection; the authoritative historical truth is the chronological sequence of relationship-intelligence events that produced it.

"Last known interaction" is necessary but insufficient. RB must preserve the event stream behind relationship state: what happened, when it happened, where it came from, who/what it matched, how confident the system was, how it affected strategic relevance and warmth, and whether the persistence path was verified.

Relationship Cards are projected current state. RI events are the audit trail and replay source.

## Tiered product structure

RB is designed to operate at multiple tiers, each with different feature scope:

| Tier | Purpose |
|---|---|
| **Free** | Basic baseline + signal taxonomy; lets a user start. |
| **Pro** | Full RC + IB + Circles + intro engine + active opportunity tracking. |
| **Enterprise** | Team networks — combine multiple users' networks to achieve outcomes; multi-user permissions; cross-pollination of intro paths. |
| **Friends and Family** | Intermediate tier given to trusted users. |
| **Developer** | Full trace, troubleshoot, debug visibility; session text export; full module access (including MT). |

The system is **modular** — feature scope is configurable per tier.

## System capabilities

- **Trace and troubleshoot mode** — produces verbose logs of decision-making for debugging and verification.
- **Session text export** — full session transcript exportable as a downloadable file (every recommendation, every change, every reasoning step).
- **MT module** — observability and troubleshooting infrastructure. Designed around answering "why did the system do X?" with a verifiable trace.

## Enterprise differentiator: team networks

Enterprise tier lets a team of users combine their individual networks into a shared graph. Use cases:
- A founding team identifies which member is closest to a given target account.
- A sales team finds warm intros across all rep networks rather than reps' siloed views.
- An advisory firm coordinates client introductions across partner networks.

Network maps and overlap analysis become the central feature of the Enterprise tier.

## Network maps and insights

Network visualization is a core RB feature, not an add-on. Examples:
- Map of Circles and their overlap.
- Map of intro paths between any two people.
- Map of cluster density (where the network is strong/weak).
- Map of warming and cooling relationships over time.
- Map of connector centrality (who would reach the most distinct subgraphs if activated).

The maps don't replace the file system; they're a visualization layer over the canonical data.

## Reciprocity ledger (Tenet 11)

The system tracks the running balance of give/get across each relationship. Three uses, in priority order:

**11a. Reciprocate when someone takes care of Todd.**
When a contact has made an introduction, a referral, a public endorsement, or any deliberate value-giving act for Todd, the system flags it if Todd hasn't reciprocated. Reciprocity isn't a debt to be paid mechanically — it's the system surfacing "this person took care of you; here's a chance to take care of them."

**11b. Don't burn introduction partners by overusing them.**
When the intro engine surfaces who could broker a connection, it must check the reciprocity ledger. **One or two intros per month from a given person is healthy. More is overkill.** The system should rotate intro requests across viable brokers and back off any single broker who's already been asked recently. Burning a high-leverage connector by treating them as an inexhaustible intro source is a network-degrading anti-pattern.

**11c. Protect the best introduction partners. Protect referral partners more.**
The system maintains a tiered preservation hierarchy:

- **Best introduction partners** — connectors who've made multiple high-value warm intros for Todd. Tag: `connector` plus reciprocity-balance tracking. Surface in the intro engine but rate-limit (per 11b).
- **Referral partners** — those who've sent paying business or qualified buyer-side leads. Highest preservation tier. Even tighter rate-limiting, plus active scheduled cadence to maintain the working channel rather than only contacting on extraction.

Both groups should appear in `today.md` periodically as "thank you / check in" candidates, not only as intro brokers.

## External and network event awareness (Tenet 12)

Relationships shift in response to events the system can't see directly. RB needs to know about three categories:

**12a. Network events (virtual and in-person).**
Virtual calls have **chat logs** that should be treated as networking events. Every chat log ingested produces:
- One IB per chat participant (relative to Todd).
- A potential introduction recommendation for each cross-pair on the call (per the intro engine logic — same Circle isn't a reason to suppress).
- Warming/cooling signals extracted from the actual content.

In-person events (chapter meetings, conferences, dinners) produce IBs for each attendee Todd interacted with, plus the same intro pair analysis.

**12b. Industry events.**
Trade shows, conferences, public industry moments. The system should know what's coming up in Todd's industry calendar — and surface relationships that would benefit from contact around those moments. Pre-event: who in your network is going? Post-event: who'd you connect with there?

**12c. Personal-touch events.**
Birthdays, work anniversaries, life events (a new role, a kid's milestone, a public recognition). When known, surface them as low-friction touchpoints that warm relationships without manufactured pretext. *"Maggie's birthday is Friday. She's a warm-ecosystem contact you haven't messaged in 4 months. A two-line text is the right move."* The system should never fabricate these — only surface them when they're actual data.

## "I don't know but it should be known" surface (Tenet 13)

When the system lacks data, it asks. When data conflicts, it asks. It never guesses, infers, or fabricates. The operating model is micro-reconciliation: small, specific prompts that resolve one uncertainty at a time.

**13a. Gap surfacing.**
The system explicitly identifies what it doesn't know about each entry, especially for high-tier RCs. Examples:
- *"David Deems is RC inner with no last_touch, no email, no phone. Want to add any of these?"*
- *"You haven't told me how often you talk to Chad Horn. The default 30-day inner threshold may not match the actual cadence."*

**13b. Conflict surfacing.**
When two sources disagree, the system surfaces the conflict and asks rather than picking. Example pattern: LinkedIn export says one company; Google calendar evidence says another; salvage RI says a third. The system reports the conflict and asks Todd which is current.

**13c. Never make up data.**
This reinforces Tenet 1. Specifically: never infer a phone number from a domain pattern, never guess a birthday from a year of joining, never assume a relationship state without evidence. If it's not in a file, it's not in the system.

**13d. Ask the smallest useful question.**
When RB is unclear, it should ask for the minimum additional fact needed to continue. The prompt should be concrete, answerable, and tied to a decision. Examples:
- *"Is this Ish email about Maho an active opportunity thread, a relationship-warming signal, or both?"*
- *"Is Bob Gibson still at Toast, or should I treat that company link as uncertain until refreshed?"*
- *"Should Jeff Wayman's May 12 message/call activity update `last_touch`, or was that not a meaningful touch?"*

**13e. Reconcile in the morning brief and in real time.**
Reconciliation happens in two places:
- **Morning brief:** batch small questions that are worth answering before the day starts — missing last touches, stale contact details, conflicting company/title evidence, high-signal emails that need classification, and candidate loop/thread updates.
- **Real time:** when Todd asks a question and RB lacks the needed fact, RB asks a focused clarification instead of giving a weak answer or pretending certainty.

The morning version should reduce clutter by grouping questions. The real-time version should unblock the current decision.

## Key features

The capabilities RB delivers, grouped. Status flags mark what's live, partial, or not yet built.

### Ingestion features

**F1. MRS scan on every input.** Every input (file, message, transcript) is scanned for relationship significance. Files are *acknowledged*: did they contain RI or not? Text input stays silent unless RI is present. Per Tenets 1, 3. *Status: live for files; partial for text (relies on the assistant noticing).*

**F2. Baseline file ingestion (compressed and CSV).** LinkedIn comprehensive archives, LinkedIn Connections.csv, Google Takeout zips, raw CSVs. Once a baseline exists, every subsequent ingestion enhances rather than replaces. Per Tenet 6. *Status: live.*

**F3. Enhancement file ingestion (all formats).** PDFs, Word docs, transcripts (text or pasted), spreadsheets (xlsx/csv), email exports, salvage packages. Each ingestion produces IB(s), baseline updates, loop additions, and CoS commentary. Per Tenet 4 + ARCHITECTURE.md "File ingestion." *Status: live.*

**F4. Network event ingestion (multi-attendee).** Chat logs from virtual calls and rosters from in-person events produce per-attendee IBs *plus* cross-pair intro analysis. Per Tenet 12a. *Status: defined; not yet exercised at multi-attendee scale.*

### Chief of Staff features

**F5. "What's important today" surface.** On-demand or daily regeneration of `today.md`. Surfaces dormancy crossings, loops past target, RC promotion candidates, today's meetings, Circle moves, on-deck intro brokers, "I don't know" gaps. *Status: live (latest: 2026-05-08).*

**F6. CoS interpretation and recommendation after every ingestion.** The three-part output shape: (1) report what was found, (2) interpret significance in the CoS voice, (3) recommend specific named next steps. Silent processing isn't acceptable. Per ARCHITECTURE.md "How the assistant communicates after an ingestion." *Status: live.*

**F7. Pre-conversation briefing (auto-trigger).** When a meeting surfaces — via connected calendar/feed, Todd mentioning a meeting in chat, or any inbound that names an upcoming conversation — RB surfaces prep needs, important deliverables, known/unknown attendees, and active-thread ties. RB should offer to create the meeting prep for review and open a tracked prep loop when useful; for known relationship attendees, it can produce the canonical 11-section pre-conversation brief (see ARCHITECTURE.md → "Pre-conversation briefing"). No explicit ask required for the surfacing step. *Status: daily brief meeting-prep surface live; full auto-generated brief artifact still pending.*

**F8. Communication sequence tracking.** For each RC, the system maintains the chronological sequence of meaningful interactions and actions, so Todd can answer "what was the last thing we talked about / what did I commit to / what did they commit to." IBs already provide this in chronological order; surface as a clean per-RC timeline. *Status: data exists; surface not yet built.*

**F9. "Tell me something I didn't know about my network."** Latent-pattern surfacing. The system runs unprompted analyses of the network and reports findings that aren't immediately obvious. Examples:
- *"You have 8 connections at Inspire Brands but no LMI promotion to any of them."*
- *"Your top 5 commenters are all in Hospitality Table — they may not all know each other."*
- *"You have 12 connections in Wisconsin (your home state) and you've never engaged any of them."*
- *"Bruce, Dave, John (OTP3 cohort) and Amy, Brandon, Jeff Wayman (former PAR) have only one degree of overlap (Dave). That's a thinner connection than the cohort relationship implies."*

The goal is **expansion and leverage of the network into future business opportunities** (Tenet 9 + Todd's stated feature 4). *Status: not yet built. High-priority capability.*

**F10. Reciprocity ledger.** Per Tenet 11. Tracks give/get balance per RC; rate-limits intro broker requests; protects best partners and referral partners (highest tier). *Status: not yet structured in baseline.*

### Network engineering features

**F11. Introduction engine.** Behavior depends on `circle_type` (see SCHEMAS.md and ARCHITECTURE.md "Same-Circle behavior depends on `circle_type`"). For `affinity_circle` Circles, Same-Circle is a positive affinity signal and intros are preferred. For `community_chapter` Circles, Same-Circle is a suppression signal — members already know each other; the valuable intros are bridges to other clusters. The engine also consults `heuristics.md` for cluster-level rules ("anyone in SCN knows Donnie") before producing proposals. Produces specific named intro paths with reasoning grounded in evidence and a draft of the ask in Todd's voice. *Status: rule updated 2026-05-12 after operator feedback.*

**F12. Network gap analysis.** Identifies where the network is weak — missing roles, missing companies in target markets, missing geographies, missing demographic clusters. Per Tenet 9 question 2. Requires Todd to first define "what should be in my network" against which gaps are measured. *Status: not yet built.*

**F13. Proactive target identification.** Surfaces who Todd should be asking to meet, even when he hasn't named a target. Uses signal from his goals, his Circles, his domain theses, and patterns in his accepted intros. Per Tenet 9 question 3. *Status: not yet built.*

**F14. Network maps.** Visualizations of Circle overlap, intro paths, cluster density, warming/cooling, connector centrality. Per Tenets — central to the Enterprise tier. *Status: not yet built.*

### Network intelligence and learning features

**F25. Insights into the network and "how to build the network better for purpose."** Beyond surfacing single-relationship signals, RB analyzes the network *as a network*: cluster strength, gap detection, connector centrality, intro reachability for each named goal. Output is recommendations on how to *grow and shape* the network deliberately for a stated purpose, not abstract metrics. Per Tenet 9 + Todd's stated capability. *Status: not yet built. Composes from F9 + F12 + F13 + F14.*

**F26. Learning loop about the user and the user's business.** RB accumulates understanding of the selected user over time — goals, preferences, capabilities, products/services, target customers/employers, conversations that proved valuable, intros that worked vs. didn't, and advice the user accepted or ignored. `profiles/{profile_id}/profile.md` is the static-current-state version; `profiles/{profile_id}/learning_log.jsonl` captures the *evolving* version. The system asks questions when it has consequential gaps, captures the answers, and uses them to make better future recommendations. Per the product capability. *Status: `00_TODD_PROFILE.md` is the current seed profile; the multi-profile learning loop isn't yet structured.*

**F27. Never-dumb-recommendation discipline.** Recommendations and intros must always show that the system understood the context — relationships around the proposed pair, prior interactions, current state of each link, BridgePoint Ops engagement boundaries. A recommendation that ignores known context is a defect, not just a low-quality output. Per Todd's stated capability. Already enforced via the intro engine rule (ARCHITECTURE.md) but worth naming as a discipline.

**F28. Relationship category tags (the working taxonomy).** Beyond `signal_class`, `relationship_state`, and `rc_tier`, RB supports relationship-category tags reflecting *function and prioritization*. These are multi-valued, applied per entry as appropriate:

- `starting-lineup` — your active core working group.
- `all-star` — highest-trust subset of starting lineup; peer-grade visibility.
- `bench` — strong working relationships not currently in starting lineup; ready to activate.
- `observe` — watch but don't actively engage.
- `dormant` — was active; now quiet by intent or attrition. Distinct from `dormant_valuable` rc_tier (which is a deliberate preservation tier for high-value relationships you intentionally touch lightly).
- `strategic-advisor` — capital, market, or domain advisor. Already used informally for Jim Taylor and Dennis.
- `connector` — high-leverage intro broker. Already in use (Jenny, Niko, Spencer, Don, Alison).
- `opportunity-relationship` — currently being pursued for a specific outcome. Lives in loop_ledger as well.
- `referral-partner` — has sent (or could send) qualified business or warm leads. Top-protection tier (Tenet 11c).

These are tags, not exclusive states. A single RC can be `starting-lineup` + `connector` + `referral-partner` simultaneously.

**F29. Influence mapping.** Distinct from connector identification. Connectors *route introductions*; influencers *shape opinions*. The system identifies who in the network has weight on what topics — whose endorsement, when given, materially affects how others perceive Todd or his work. Useful for thought-leadership positioning, hiring searches, and when conviction-building matters more than reach. *Status: not yet built. Could be derived from public-engagement patterns + role + industry visibility.*

**F30. Risks section on RC cards.** Relationships carry risks: someone planning to move firms, declining health, an unresolved tension, a conflict of interest, a competitor relationship that constrains what can be shared. Currently the RC schema has Why this matters / Narrative arc / Trust state / Leverage / What's lingering / Unresolved movement / Their world / How to engage / Recent IBs / Open loops — but no Risks. Add it as a first-class section. Often overlaps with What's lingering and Unresolved movement; Risks is for things that are *known* to be problematic, not just open. Per the original RB design intent. *Status: not yet in card schema. To-do.*

### User-control features

**F31. Verbosity mode switch.** Three modes — `verbose`, `normal`, `quiet`. Stored in `settings.json`. Definitions:
- **verbose** — full reasoning trail, alternatives considered, what was rejected. Use when debugging a recommendation.
- **normal** — three-part shape (report → interpret → recommend). Default.
- **quiet** — minimum viable response, just the answer. Stats and citations preserved (Tenet 2), commentary trimmed.

Switchable inline (*"set verbosity to quiet"*) or by editing `settings.json`. *Status: live.*

**F32. Daily briefing.** Default on. Regenerates `today.md` each morning at the user's configured local time (default 05:00 America/Chicago) and should send a short completion email so the brief is waiting like the morning newspaper. The email directs the user into the RB operational layer, not Codex, Claude, local files, or developer scaffolding. Toggleable in `settings.json`. Requires a scheduled task plus email transport to fire daily. *Status: settings file in place; email transport pending.*

**F33. Social/LinkedIn signal scan.** Where permissioned data exists, RB scans LinkedIn/social public posts, comments/reactions, own-post engagement, and LinkedIn messaging/interactions for both RI and industry/market signals. Public posts can become Market Movement & Strategic Implications; messages/interactions can become RI, loop candidates, opportunity signals, and last-touch evidence. If LinkedIn/social access is stale, manual-only, or missing, the Daily Prep Summary must say so. *Status: public-post + engagement overlays live from captured feeds; LinkedIn message scan surfaced as prep source, full parser pending.*

**F34. Calendar prep and deliverable surfacing.** Daily Brief must scan calendar events for meeting prep, deliverable risk, expected outputs, known relationship attendees, unknown attendees, and active-thread/company ties. Meeting items should be prioritized above generic tasks when timing risk is high, and Smart Task / loop creation should offer to create meeting prep for review. *Status: daily brief canonical section live; prep artifact generation pending.*

**F35. Morning command center and action affordances.** Daily Brief should include a concise operating board that recommends the day's top moves, meeting-prep queue, waiting-on state, loop risk, signal state, suppressed noise, and one decision needed. Every actionable item should expose next-step affordances such as create loop, draft message, create meeting prep, defer, mark done, mark irrelevant, or monitor. *Status: canonical daily brief surface live; UI/action execution layer pending.*

**F36. End-of-day closeout.** RB should offer a late-day closeout that reviews what closed, what slipped, what is waiting, what passive evidence resolved, and what rolls to tomorrow. *Status: daily brief offer live; scheduled closeout automation pending.*

### Trust and discipline features

**F15. Trust statistics.** Stats = trust (Tenet 2). Every report shows what the system did with what it was given: counts, dates, source files, evidence chains. *Status: live in `today.md`, delta reports, IBs.*

**F16. Confidence tagging on every claim.** Each recommendation, signal-class promotion, IB, and warming/cooling read carries a confidence level (`high` / `medium` / `low`) grounded in evidence. From the original E module. *Status: partial — IBs use `substance` and `confidence` fields; CoS recommendations don't yet always carry an explicit confidence flag.*

**F17. Negative guarantees (what RB will not do).** Explicit list, surfaced in the system documentation and respected at runtime. From the Reference KB Core (Section 2 — Negative Guarantees). RB will not: invent intent, escalate urgency, nag (repeat-surface without new context), optimize for activity, bypass tenets, retry without new context, expose internal mechanics by default, speak for other people, treat absence as failure, or override the user. *Status: partial — captured implicitly across tenets and architecture; should be its own canonical list.*

**F18. "I don't know" gap surface.** Per Tenet 13. When the system lacks data, it asks. When data conflicts, it asks. Never guesses. *Status: defined; not yet a structured section in `today.md`.*

### Persistence and tier features

**F19. Persistent files-as-memory.** Per `WHAT_PERSISTS.md`. Files in the workspace folder ARE the canonical memory; a Claude session reads them in. *Status: live.*

**F20. Backup to Google Drive.** Auto-sync of the workspace folder via Google Drive for Desktop. Per Tenet 5. *Status: setup steps documented in ARCHITECTURE.md; user-driven setup pending.*

**F21. Session text export.** Full session transcript exportable as a downloadable file. This existed in RB 8.0 and should be restored for RB 9.0 interface testing. Export should include user prompts, RB responses, API/tool calls, returned status, write confirmations, timestamps, and observed issues. *Status: restore required.*

**F22. Trace and troubleshoot mode (MT module).** Verbose decision trail for any system action — answer "why did the system do X?" with a verifiable trace. This was an RB 8.0 developer-level capability and is required for debugging the ChatGPT Actions interface. *Status: restore required.*

**F23. Tiered feature gating.** Free / Pro / Enterprise / F&F / Developer. Modular feature scope per tier. *Status: not yet built; documentation reference (Super KB-AP) flagged as ambiguous — see implementation notes below.*

**F24. Enterprise team networks.** Combine multiple users' networks into a shared graph; cross-user intro paths; shared Circle activations. *Status: not yet built.*

## Implications for current implementation

A few items in the current build that need to be reconciled with these tenets:

- **Pipeline statuses in Circle templates** — `target-restaurants-tech-leaders.md` and `target-employers.md` currently use a CRM-shaped state column (`target` → `inroad-identified` → `intro-requested` → `meeting-set` → `won` / `lost` / `parked`). Per Tenet 7, this is CRM thinking that doesn't belong. Reframe these as relationship states (per Tenet 8), not deal states.
- **Warming/cooling indicators** — the system currently tracks `last_touch` and dormancy crossings, but doesn't explicitly compute or surface direction-of-travel ("this relationship is warming" / "this relationship is cooling"). Tenet 8 + transcripts as a warming/cooling source make this critical. To-do.
- **The "always ask three questions" pattern** — the intro engine partially answers question 1 ("who should I introduce to this person"). Questions 2 and 3 (network gap analysis, proactive target identification) aren't yet implemented. To-do.
- **SCN action types** — virtual coffee / introduction / referral / guest invite aren't yet first-class types in the system. They're currently buried in IB free-text. Worth structured fields. To-do.
- **Backup to Google Drive** — needs to be operational now (Tenet 5). Practical path: move the project folder into Google Drive for Desktop's local mount; auto-sync handles the rest. See ARCHITECTURE.md for setup steps.
- **Reciprocity ledger** (Tenet 11) — per-RC give/get tracking, intro-broker rate limiting, referral-partner protection. Not yet structured in baseline. To-do.
- **Network event awareness** (Tenet 12a) — chat logs from virtual calls, in-person event ingestion. The Patrick Nelson transcript ingestion was a single-pair event; the system should also handle multi-attendee events with cross-pair intro analysis. To-do.
- **External/personal-touch event awareness** (Tenet 12b/c) — industry calendar, birthdays, work anniversaries. Currently invisible. To-do.
- **"I don't know" gap surfacing** (Tenet 13a) — system should proactively flag missing data on high-tier RCs. Currently silent on gaps. To-do.
- **Tier system documentation** — the Free / Pro / Enterprise / F&F / Developer feature gating per Todd's reference to Super KB-AP. Current build is single-tier (effectively Pro/Developer). The original 24-doc set's "AP" module was *Signal Support / auxiliary signaling* (per `Reference KB - Core 8-3.docx`). Either Todd's evolved RB has a different AP module, or the tier-gating reference is to a doc not yet in the system folder. **Flagged for Todd's confirmation.**

These to-dos are recorded here, not silently held in a future Claude's head.
