# Architecture

This is the only architecture document. If a rule isn't here or in `SCHEMAS.md`, it isn't a rule.

## Philosophical core

Attention is scarce. Trust is fragile. Context compounds. Relationships decay silently. Most people lose opportunities because they fail to notice signal early enough.

This system exists to preserve and surface signal before opportunity or trust is lost.

The unit of value is **relationship meaning** — the narrative arc, trust state, momentum, leverage, and unresolved movement of each relationship that matters — not the contact record. A contact is data. A relationship is meaning carried over time. The system stores meaning.

The intended experience is calm, fast, minimal, and strategic. It should feel like having a brilliant Chief of Staff sitting beside you, not like operating a control panel. When nothing important is happening, the system says so and goes quiet.

RB is a Chief of Staff platform, not a bundle of productivity features. Its core job is judgment over noise: strategic prioritization, relationship intelligence, contextual judgment, proactive recommendation, operational awareness, executive filtering, timing sensitivity, and trust preservation.

The system should continuously answer:

- What matters right now?
- What does not matter yet?
- Who requires attention?
- What should wait?
- What risks are emerging?
- What opportunities are developing?
- What should the user not spend time on?

What this is not:

- not a CRM (RB sits above CRM systems such as HubSpot; CRM records are inputs/outputs, not the product)
- not a generic task manager (RB tracks relationship, opportunity, preparation, and user-prioritized obligations when they affect trust, momentum, or balance)
- not a sales engagement platform (this is about stewardship, not throughput)
- not a generic AI assistant (this is a focused intelligence layer)
- not a copilot that talks constantly (silence is a valid outcome)

## Design principles

- **Domain first.** The system is about people, signals, recognition, activation, and timing — not about governance, admissibility, or routing.
- **Relationship intelligence above CRM.** RB focuses on relationship signals, trust, momentum, obligations, timing, and strategic context. Deal values, pipeline stages, forecasts, quota mechanics, and CRM hygiene remain CRM concerns unless they change what the relationship system should notice or recommend.
- **Say it once.** A rule lives in exactly one place. No layered defenses around the same invariant.
- **Files are the product.** What the system "is" lives in `baseline_index.json`, `cards/`, `briefs/`, `circles/`, etc. Everything else is generated from those files.
- **Event stream over snapshots.** RB should preserve chronological relationship-intelligence events and derive current relationship state as a projection. Cards and baseline entries are current views; they are not the full truth.
- **Inputs are not canonical.** LinkedIn exports, email, calendar, and CRM are feeds. The system's own files are the truth.
- **Conservatism over activity.** The system does not invent intent, manufacture urgency, or nag. It surfaces what's real.
- **Trust before interpretation.** User-facing intelligence begins by proving what sources were checked, what changed, what is stale, what was updated, and what is unavailable. Analysis comes after provenance.
- **Action-state clarity.** RB must distinguish action completed, action inferred, action suggested, action blocked, and action unavailable. If a mutation happened, say "RB recorded..." or "RB updated..."; if it did not happen, do not imply completion.
- **Configurable core, user-specific layer.** The engine should stay user-agnostic and industry-agnostic. User identity, communication style, industry context, goals, terminology, integrations, and preferences belong in a replaceable profile/configuration layer.
- **User ownership.** Long-term architecture should support portable profiles, local-first or hybrid persistence, self-hosting options, and user-controlled storage of the relationship graph, interaction history, strategic context, and operational memory.
- **Tenant-owned intelligence stays tenant-owned.** User-provided relationship data, commercial datasets, uploaded spreadsheets, purchased market data, customer/account topology, private ecosystem graphs, and derived projections from those sources belong only to that user's RB instance. They are not product seed data, demo defaults, cross-user training material, or shared graph substrate.
- **Security from the foundation.** Continuous ingestion of email, transcripts, calls, LinkedIn, CRM, calendar, and documents requires authentication, encryption-ready storage boundaries, permission controls, ingestion audit trails, and least-privilege connectors by design.

## Optional whole-life relationship balance

RB starts as a professional relationship operating system. A future opt-in mode may extend the same CoS discipline to non-business relationships and life-balance commitments when the user wants that help.

This mode is disabled by default. If enabled, the user chooses the domains and cadence RB may monitor or prompt around:

- family and close friends;
- service, church, and community commitments;
- recreation, vacation, and recovery commitments;
- user-defined physical, mental, or emotional wellbeing practices.

The purpose is not to make RB a moral judge or a medical advisor. The purpose is to help the user honor the relationships and commitments they explicitly say matter. Example prompts may include a gentle reminder to call a parent, schedule time with kids, follow through on a service commitment, protect a planned vacation, or notice when a user-stated wellbeing routine has slipped.

Guardrails:

- opt-in only, with per-domain controls;
- no guilt, shame, surveillance, or moral scoring;
- no sensitive family or health inference without explicit user input;
- wellbeing prompts support balance and follow-through but do not provide medical, clinical, or therapy advice;
- user-entered commitments outrank inferred suggestions;
- silence remains valid when nothing deserves attention.

## Event-sourced relationship intelligence

RB 9.0's persistence direction is event-sourced relationship intelligence.

The system should not only ask:

> What is the current relationship state?

It should ask:

> What sequence of timestamped relationship events produced the current state?

The event stream is the authoritative timeline. Current files are projections:

- `baseline_index.json` is the current person index projection.
- `cards/<id>.md` is the current Relationship Card projection.
- `today.md` is the current operating brief projection.
- `loop_ledger.md` is the current open-obligation projection.
- `active_threads.yaml` is the current strategic-context projection.
- `briefs/*.md` are the current append-only evidence atoms and should evolve toward or be mirrored by structured RI events.

Every RI signal should become a timestamped event. Each event should capture:

- `event_id`
- `captured_at`
- `event_at`
- source thread/document/artifact
- entity match decision
- dedupe decision
- signal type
- confidence score
- strategic relevance impact
- relationship warmth impact
- persistence verification status

This unlocks duplicate RI prevention, historical continuity, warming/cooling trend detection, temporal trust signals, auditability, replayability, and explainability for why a relationship state changed.

Until the full RI event log exists, Interaction Briefs remain the append-only evidence layer. New ingestion work should be designed so it can emit both the current IB format and a future structured RI event without rethinking the domain.

## The signal hierarchy

Every contact in your network sits at exactly one signal class:

| Class   | Name                          | Meaning                                                          | Example                                              |
|---------|-------------------------------|------------------------------------------------------------------|------------------------------------------------------|
| **VC**  | Virtual Connection            | Adjacency observed (e.g. LinkedIn connection); no depth.         | Random LinkedIn accept from 4 years ago.             |
| **NPR** | Networking Presence Record    | Shared context without interaction.                              | Same conference, same Success Champions cohort.      |
| **LMI** | Low-Momentum Interaction      | Light, directional engagement.                                   | A like, a one-line reply, a "happy birthday."        |
| **LKI** | Linked Knowledge Interaction  | Bidirectional, substantive exchange. Required to qualify for RC. | A 30-minute call about each other's work.            |
| **RC**  | Relationship Card             | Recognized, persisted relationship.                              | Someone you'd unhesitatingly text for a favor.       |

Promotion is **earned by evidence** (Interaction Briefs), not declared. Demotion happens through dormancy.

## Relationship state (parallel layer to signal class)

Signal class is *evidence-derived* (what the data shows). Relationship state is *interpretation-derived* (where Todd places them on the trust path). Both are tracked, neither replaces the other.

| State | Meaning | Maps loosely to |
|---|---|---|
| `unknown` | Not yet in baseline. | (no entry) |
| `known` | In baseline; Todd is aware they exist. | VC |
| `liked` | Awareness plus positive directional signal. | NPR / early LMI |
| `trusted` | Substantive bidirectional engagement; Todd would take their call. | LKI |
| `referral_partner` | Has sent qualified business or warm leads to Todd, or vice versa. | Sub-state of LKI/RC |
| `starting_lineup` | Inner working circle; Todd would unhesitatingly text. | RC inner |
| `inner_circle` | Tightest subset of starting lineup; daily-or-near-daily cadence; institutional trust. | RC inner + `otp3-leaders` Circle, etc. |

Field name: `relationship_state` in baseline. Like `signal_class`, it's user-set or system-suggested-with-confirmation. The system should never auto-assign `relationship_state` — Todd's interpretation drives it.

Movement direction (warming / cooling) is tracked separately — see below.

## Warming and cooling detection

**Tenet 8: relationships are always in flux. Identifying which direction each is moving is critical.**

The system computes a `momentum` reading for each RC by combining at least these heuristics. Watch for additional signals beyond this list — transcripts in particular are rich sources we should keep mining for new patterns.

**Cadence drift.** Compare the average gap between recent interactions to the average gap from earlier in the relationship. Lengthening gaps = cooling; shortening = warming.

**Substance drift.** Compare the substance level of recent IBs (`high` / `medium` / `low`) to earlier ones. Trending toward `low` = cooling; trending toward `high` = warming.

**Initiation drift.** Track who's initiating contact. If Todd has been the initiator in the last 3+ interactions, that's a cooling signal. Mutual initiation = stable. Other person initiating more = warming.

**Transcript-derived signals (per ingestion).** When a transcript is ingested, look for and flag explicitly:
- *Warming*: spontaneous personal disclosure, mutual rapport moments, future-oriented language ("when we...", "next time"), action items committed by both sides, generosity moves (offered intros without ask, shared info freely).
- *Cooling*: one-sided enthusiasm, hedged follow-ups ("we'll see"), topic abandonment, rushed close, flattened tone over time.

The momentum field on RC cards has three values: `positive`, `flat`, `negative`. The system updates momentum when an ingestion produces clear directional signal. It never auto-flips momentum without evidence.

## "I don't know" gap surfacing

When the system lacks data on a high-tier entry, it surfaces the gap explicitly rather than staying silent. Examples:

- *"David Deems is RC inner with no `last_touch`, no email, no phone. Want to add any of these?"*
- *"You haven't told me how often you talk to Chad Horn. The default 30-day inner threshold may not match the actual cadence — what's the real rhythm?"*
- *"Two sources disagree on Daran Adair's company (LinkedIn says Franchise Grade; salvage says Ameri-Can). Which is current?"*

The gap-surface appears in `today.md` as its own section when there's anything to ask. Stays empty when there's not. **Never guess. Never infer. Never make up data.** When uncertain, ask.

The product behavior is micro-reconciliation, not passive ignorance. RB should turn uncertainty into small, answerable prompts that improve the graph:

- Morning brief reconciliation: batch the useful questions that deserve attention before the day starts.
- Real-time reconciliation: ask a focused question when Todd's current request depends on missing or conflicting information.

Good reconciliation prompts are specific and decision-linked. Bad prompts are broad, vague, or administrative for their own sake. RB should ask *"Should this Ish/Maho email create an active thread or just a relationship signal?"* rather than *"Please update Ish."*

## Network events (multi-attendee interactions)

Distinct from one-on-one IBs. A network event is a single interaction with multiple participants — a chapter meeting, a virtual call with a chat log, a conference dinner.

When a network event is ingested:

1. **One IB per participant Todd interacted with**, relative to Todd. Each IB stands alone but cites the event as common provenance.
2. **Cross-pair intro analysis** — for each pair of attendees (other than Todd), the system considers: should these two be introduced? Same-Circle status is *not* a reason to suppress (per the intro engine rule). Each candidate intro pair is logged as a potential loop for Todd's review.
3. **Event becomes the artifact** — the chat log, transcript, or attendee roster is preserved as the source artifact in `briefs/`.

Implication: the value of attending a meeting compounds when the system extracts both the per-participant signal *and* the intro-graph implications.

## RC tiers and dormancy

An RC has one of three states — `ACTIVE`, `PARKED`, `CLOSED` — and one of three tiers, each with its own dormancy threshold:

| Tier              | Dormancy threshold | Who belongs here                                          |
|-------------------|--------------------|------------------------------------------------------------|
| `inner`           | 30 days            | Inner circle. Friends, key collaborators, top advisors.   |
| `broader`         | 90 days            | Strong working relationships, recurring collaborators.    |
| `dormant_valuable`| 180 days           | High-value relationships you intentionally touch lightly. |

When an `ACTIVE` RC crosses its dormancy threshold without a fresh IB, it surfaces in the daily brief as a re-engagement candidate. It does not automatically demote.

## Canonical files (the product)

| File                    | Purpose                                                        | Update pattern                          |
|-------------------------|----------------------------------------------------------------|------------------------------------------|
| `baseline_index.json`   | Every contact ever ingested, with current signal class.        | Updated on every ingestion or promotion. |
| `cards/<id>.md`         | One Relationship Card per recognized relationship.             | Updated when state, tier, or notes change. |
| `briefs/<date>-<id>.md` | One Interaction Brief per qualifying interaction. Append-only. | Created, never edited.                   |
| `circles/<id>.md`       | One Circle per goal-anchored activation.                       | Updated as members and status evolve.    |
| `intro_brokers.md`      | Derived: who in your network is a high-leverage connector.     | Regenerated weekly or on demand.         |
| `loop_ledger.md`        | Open loops awaiting deliberate closure.                        | Created on loop open; resolved on close. |
| `today.md`              | Daily brief: what's important today.                           | Regenerated daily.                       |

## Inputs (the feeds)

| Input             | Effect on canonical files                                                                       |
|-------------------|-------------------------------------------------------------------------------------------------|
| LinkedIn export   | Seeds/refreshes `baseline_index.json` with VCs and NPRs (where group/co-presence is detectable). |
| Email             | Generates IBs; promotes signal class when bidirectional substance is detected; updates `last_touch`. |
| Calendar          | Generates IBs (meetings); flags upcoming meetings for context briefing.                         |
| CRM / notes       | Enriches RCs with relationship context, contact/company facts, activity evidence, and relevant opportunity timing; RB does not own deal value, pipeline forecast, or CRM administration. |
| Conversation with you | Captures relationship shifts, opens loops, creates Circles, updates RC notes.               |

## Promotion rules

- **VC → NPR**: shared context detected (event attendance, group membership, mutual employer history).
- **NPR → LMI**: any directional contact (a like, a reply, a calendar invite acceptance).
- **LMI → LKI**: bidirectional substance — at least one exchange in each direction with non-trivial content.
- **LKI → RC**: deliberate recognition by you. The system *queues* RC creation when LKI is sustained; you confirm.

The system does not auto-create RCs. RC creation is always a deliberate act.

## Circles

A Circle is a goal-anchored grouping of network members. Two shapes:

- **Person-based Circle** — members are people. Example: "Tech sales/CS leaders to know."
- **Account-based Circle** — members are companies, with people-at-companies as connection points. Example: "Companies that might employ me."

Each Circle has: a goal, a `member_type`, member-fit criteria, current members, candidate members, and an activation state (`forming` → `activating` → `active` → `dormant` → `closed`).

## The daily brief — `today.md`

Regenerated once per day and on demand. The daily brief is the visible trust surface of RB's operational memory. It must not start with narrative analysis. It starts by proving what RB checked, what updated, what is stale, what changed, and what RB actually did.

Canonical front-door order:

1. **Resource Verification & Freshness Status.** Resources accessed, resources updated, unavailable/stale sources, last successful sync/ingestion timestamp per source, new items processed, failed scans/errors, brief confidence, and known blind spots.
2. **Relationship / Operational Signal Review.** What changed, RI detected, RI records created/updated/proposed, signal source/timestamp/confidence, why it matters, recommended action, and loop status.
3. **Industry Brief.** Source-backed restaurant-tech/operator intelligence, scans performed, sources reviewed, confirmed vs. rumored items, and why each item matters to the user's market position.
4. **Local / Regional Restaurant Environment.** Local/regional restaurant news and labor, wage, weather, supply-chain, or regulatory signals when available.
5. **World / Macro / Macroeconomic Impact.** Global and macro signals with explicit framing: global/macro signal → restaurant operational effect → restaurant-tech implication.
6. **Recommended Actions.** Priority, triggering signal, source, timing, suggested next step, and whether a task or loop should be created.
7. **Action Orchestration Prompt.** End with: "Do you want me to create a to-do list or action loops from these recommendations?"

Detailed drill-downs may follow the front-door contract: command center, signal freshness, meeting prep, sent followups, active threads, RI events, crossings, loops, strategic operators, market context, gaps, circles, and network state.

The daily brief never invents urgency. If nothing important is happening, it says so.

Last-24-hour relationship intelligence is not an inbox dump. It is a filtered signal layer. RB should include only items that affect relationship state, active threads, opportunity timing, network development, job search, consulting/advisory surface area, or strategic focus.

The daily brief must not imply resources were accessed unless they were actually accessed. Missing source coverage is a first-class result, not a footnote.

Deleted/trash and junk/spam email folders are recovery surfaces, not normal briefing streams. RB should scan them when available because important messages can be deleted quickly or misclassified. Surfaced items must include the original mailbox/label and should recommend review or recovery, not assume intent or automatically restore anything.

## File ingestion (transcripts, PDFs, docs, spreadsheets)

The system accepts arbitrary document inputs as evidence. The user pastes a meeting transcript, uploads a PDF, drops in a docx or xlsx, and the assistant extracts relationship intelligence and updates the canonical files accordingly.

Not every uploaded artifact belongs in the same graph. RB uses a federated graph architecture: personal relationship intelligence, macro industry intelligence, micro ecosystem topology, active opportunities, and short-lived signals are separate graph layers that connect through typed references rather than merging into one giant graph. See `system/design/FEDERATED_GRAPH_ARCHITECTURE.md`.

Topology-heavy spreadsheets are a special case. A workbook that maps stores, markets, field offices, operational roles, deployment ownership, or accountability chains should not be flattened into `baseline_index.json` or treated as generic restaurant industry intelligence. It should be classified as a micro ecosystem artifact, normalized into typed nodes and edges, linked back to the raw workbook by sheet/row/column provenance, and activated only when that operational ecosystem is contextually relevant.

Uploaded topology and market-data artifacts are tenant-owned. Todd's McDonald's NSN workbook and Technomic Top 1500 files are private source material for Todd's RB instance only. A different user starts with no access to those artifacts or derived graphs; onboarding and long-term usage must build that user's own relationship graph, watchlists, market context, ecosystem graphs, and micro-topology artifacts from their authorized sources.

### Accepted input types

- **Plain-text transcripts** (Fathom, Otter, Zoom, copy-pasted notes).
- **PDFs** — meeting notes, recommendations, profiles, contracts where third parties are named.
- **Word documents** (.docx) — same.
- **Spreadsheets** (.xlsx, .csv) — contact lists, pipelines, attendee rosters, or topology-heavy operational workbooks that require micro ecosystem graph extraction rather than RI promotion.
- **Email exports** — when available.

### What the assistant extracts

For each ingested document, the assistant identifies:

- **People mentioned** — match against `baseline_index.json` by name; flag any unmatched names for confirmation.
- **Direction and substance of the interaction** — was Todd in it? Who initiated? Was the exchange substantive (LKI floor) or light (LMI)?
- **RI signals** — strategic alignment, friction, mutual interest, role/title changes, company moves, personal rapport moments, philosophy alignment.
- **Open loops** — action items, promised follow-ups, "let's talk next week," "send me X."
- **Closed loops** — items that the document itself resolves (e.g., the meeting addressed a pending question).
- **Connection chains** — who introduced whom, who facilitated, who's in the warm-path lineage. Tag connectors.
- **New entities** — companies, programs, chapters, projects mentioned that aren't yet in the system.

### What the assistant produces from a single ingestion

1. **One IB per qualifying interaction** — written to `briefs/`. Filename pattern: `YYYY-MM-DD-<person-id>-<short-descriptor>.md`. The IB cites the source file or recording URL in its frontmatter.
2. **Baseline updates** — promote signal class if evidence is strong (per the promotion rules); update `last_touch`; append a breadcrumb to `notes` so the audit trail is intact.
3. **Loop ledger updates** — open new loops with closure targets; mark existing loops closed if the interaction resolved them.
4. **Card updates** — only if the interaction materially changes narrative arc, leverage, or what's lingering. Skeletons can wait for explicit narrative additions.
5. **Cross-references** — when the document touches a connector or chain, update the connector's record too (e.g., a transcript that says *"Allison introduced me to Patrick"* should also annotate Allison's baseline entry).

### Provenance is mandatory

Every IB generated from a document MUST cite:
- The source file (or pasted-text origin).
- The original recording URL if applicable.
- The date the document was ingested into the system (which may differ from the interaction date).

This protects against the failure mode where an IB exists in the system without traceable evidence. If a future Claude session or reader needs to verify a claim, the source must be reachable.

### What the assistant should not do

- **Don't promote across multiple class jumps without explicit evidence.** A first-meeting transcript is LMI/LKI floor — not RC. RC promotion requires user confirmation per the architecture.
- **Don't fabricate signals not in the document.** If the transcript doesn't mention emotional residue or strategic intent, those sections of the IB stay empty rather than getting invented to fill space.
- **Don't add unmatched names to baseline silently.** If the document mentions "Mike" with no surname or context, flag it to the user rather than creating a half-empty entry that becomes a duplicate later.
- **Don't change a person's company in baseline based on a passing mention** unless the document is authoritative (e.g., their own email signature, their LinkedIn export). Side-references can mislead.

### How the assistant communicates after an ingestion

Three-part output shape, in this order. This is mandatory — silent processing isn't an acceptable response to an ingestion event.

1. **Report what was found.** Concrete enumeration of the RI extracted: who was named, what signals appeared, what the system did to baseline / loops / cards / IBs. No editorializing in this part — just state the facts.
2. **Interpret the significance in the CoS voice.** What does this *mean* for Todd? What strategic position does it create or reinforce? Where does it intersect with existing relationships, opportunities, or boundaries (especially the BridgePoint Ops engagement boundaries)? What's the read between the lines that pure data extraction wouldn't catch?
3. **Recommend possible next steps.** Specific, named, actionable. Not generic ("keep in touch") — concrete ("schedule follow-up by May 15 with a prepped example scenario; suggest pain point X from your baseline as the example because Y").

This is the difference between an ingestion engine and a Chief of Staff. The engine processes. The CoS interprets and directs.

The voice for parts 2 and 3: direct, Midwestern, peer-level. No AI fluff. No hedging. If a recommendation is uncertain, name the uncertainty plainly rather than dressing it up.

## The introduction engine

When the user names a target person, company, or domain, the system surfaces warm-path recommendations — who in the network is positioned to make a useful introduction.

### Same-Circle behavior depends on `circle_type`

The intro engine reads each Circle's `circle_type` (defined in `circles/<id>.md` frontmatter; see `SCHEMAS.md`) and applies different logic based on the type. This replaces the prior blanket "same-Circle is exactly the reason" rule, which broke when applied to active community chapters where members already know each other.

**`community_chapter` Circles** — active social structures (chapters, cohorts, alumni groups). Examples: `hospitality-table`, `otp3-leaders`, `former-par-employees`, `success-champions`. Members of a community chapter are **presumed to already know each other** through recurring participation. The intro engine should **suppress** Same-Circle proposals within a community chapter unless evidence shows the two members haven't met. The valuable intros for a community chapter member are **bridges to other clusters**, not intra-chapter connections they already have.

**`affinity_circle` Circles** — curated semantic groupings (target lists, peer-affinity collections). Examples: `tech-sales-cs-leaders`, `target-restaurants-tech-leaders`, `target-employers`. Members of an affinity circle are **catalog-mates, not acquaintances**; the operator has grouped them by criteria, not by social structure. For these, the original rule stands: **Same-Circle is exactly the reason to introduce**. Two affinity-circle members share domain context and trust shorthand; the introduction lands faster and stick longer.

Worked example (chapter): the Hospitality Table chapter is a `community_chapter`. Noelle Labrie, Cristina Gia Luciano, Daran Adair, Chason Forehand are all chapter members. They already know each other. An intro between any two of them inside the chapter is a low-value proposal. The high-value moves are bridges *out* of the chapter — Noelle to someone in tech sales, Daran to a restaurant operator outside SCN.

Worked example (affinity): if `target-restaurants-tech-leaders` carries a populated list of operator-CEOs across multiple companies, those people likely don't know each other — they share a category Todd put them in. Same-Circle intros within an affinity circle are the system's job.

**The intro engine should also consult `heuristics.md` before producing any proposal** — cluster-level rules ("anyone in SCN already knows Donnie Boivin") that suppress otherwise-plausible intros.

### Anti-patterns the engine should not produce

- *"Don't introduce X to Y because they're in the same Circle."* They probably should be introduced.
- *"Don't introduce X to Y because they're at the same company."* Same-company connections often have surface awareness without working depth — the introduction may still be valuable.
- *"Don't introduce X to Y because Y is already an RC."* RC status doesn't preclude needing an introduction to a specific person.
- *"Recommend introducing X to everyone in your network at Company Y."* Spray-and-pray intros violate the boundary about relationship capital not being free inventory. Recommendations must be specific and grounded.

### What the engine should produce

- A specific named person (or two-person path for second-degree intros) with a reason grounded in evidence.
- The current relationship state of each link in the path (RC tier, last_touch, signal_class).
- Why this introduction lands now — shared context, shared problem, shared upcoming moment.
- A draft of the actual ask, written in the user's voice (per the style guide), that the user can copy and send.

### What the engine should not produce

- Generic "you should connect with X" suggestions without a specific reason.
- Recommendations that violate the BridgePoint Ops engagement boundaries (no free intro broker, no commission-only sales channel).
- Volume metrics framed as success ("you could introduce 50 people this week"). Quiet days are valid. Restraint over activity.

## Backup to Google Drive

Per Tenet 5, persistence isn't enough on its own — the system needs to be backed up so local loss isn't data loss. Practical setup options, in order of simplicity:

**Option A (recommended): Google Drive for Desktop, with the project folder living inside the GDrive mount.**

1. Install Google Drive for Desktop on your Mac if it's not already installed: download from `https://www.google.com/drive/download/`.
2. Sign in with your Google account. The app creates a virtual drive at `~/Library/CloudStorage/GoogleDrive-<your-email>/`.
3. Inside My Drive (the synced root), create a folder called `Claude/Projects/` so the path matches what Cowork expects.
4. **Move** the existing `Relationship & Business Builder` folder from `~/Documents/Claude/Projects/` into `~/Library/CloudStorage/GoogleDrive-<your-email>/My Drive/Claude/Projects/`.
5. **Update Cowork's project configuration** to point at the new location. In Cowork, edit the Relationship & Business Builder project's workspace folder path to the GDrive-synced path. Otherwise Cowork will create a fresh empty folder at the old location.
6. Verify: open Google Drive in a browser, confirm the system folder and all its contents appear.

After step 5, every file write the assistant makes auto-syncs to Google Drive in the background. No manual export needed.

**Option B: Manual export workflow.**

Periodically zip the entire `system/` folder and upload the zip to a backup location of your choice. Less seamless than Option A, but works without filesystem reorganization. The assistant can produce the zip on demand:

> *"Generate a backup of the system folder."* → zip written to `_snapshots/backup_YYYY-MM-DD.zip`.

This is a fallback if Option A isn't viable. It's manual, so it requires user discipline to run regularly.

**Option C: rclone or third-party sync.**

Bidirectional sync between the local folder and a GDrive remote. More flexible than Option A but requires `rclone` setup and configuration. Useful if the project folder needs to live somewhere specific that isn't inside the GDrive mount.

**Recommendation: Option A.** It's one-time setup, then invisible.

**Tradeoff to know about:** Cowork hasn't been tested with paths inside `~/Library/CloudStorage/`. Most file tools and bash work transparently with that path because it's a real directory under the hood, but if anything breaks, the fix is to use Option B as the fallback.

## Verbosity modes

The user controls how much information the system shows behind each answer. Three modes, stored in `settings.json`:

**`verbose`** — full reasoning trail. Cites every piece of evidence inline. Shows alternatives the system considered and explicitly states why each was rejected. Includes counts and source files for every claim. Long. Useful when Todd is verifying a recommendation, debugging a surfaced pattern, or wants the full analytical chain visible.

**`normal`** — the three-part output shape. (1) Report what was found, (2) interpret significance in the CoS voice, (3) recommend specific named next steps. This is the default and matches the ingestion-output rule documented above.

**`quiet`** — minimum viable response. Just the answer or the recommendation. Stats and source citations are still required (Tenet 2 — stats build trust; without them, the answer can't be trusted). What's trimmed is the editorial commentary — interpretation paragraphs, "why this matters" framing, alternative considerations. Short.

The system reads the current verbosity setting at the start of every response and shapes output accordingly. Switchable inline (*"set verbosity to verbose"* / *"go quiet"* / *"normal mode"*) or by editing `settings.json`.

The verbosity setting affects assistant output, not file output. Files (cards, IBs, deltas, today.md) are always written at full fidelity. Verbosity only governs how the assistant *speaks* in chat about what it did.

## Daily briefing

When `settings.json → daily_briefing.enabled = true` (the default), `today.md` regenerates automatically each morning at the configured local time. Default: 07:00 America/Chicago.

The daily briefing implementation runs as a Cowork scheduled task. The scheduled task fires a fresh Claude session in the workspace folder with this prompt:

> *"This is the Relationship & Business Builder (RB) system. Read system/settings.json, the selected user profile, system/README.md, and system/ARCHITECTURE.md. Then regenerate system/today.md from current baseline state per the daily-briefing behavior in settings.json. Do not respond to chat — just write the file."*

The session runs silently. Todd opens `today.md` in the morning and reads what was generated.

To disable: set `daily_briefing.enabled = false` in `settings.json`, or tell the assistant *"turn off the daily briefing."* The scheduled task can stay registered (running but writing the file is harmless if disabled, since the regenerate logic checks the flag) or be removed entirely.

To change the time or timezone: edit the `time_of_day_local` and `timezone` fields in `settings.json`, then ask the assistant to update the scheduled task to match.

## Pre-conversation briefing (auto-trigger)

**Trigger.** Any of the following produces a pre-conversation brief without Todd having to ask:
- A meeting on the connected calendar with a person in `baseline_index.json` (when calendar is live).
- A meeting Todd surfaces in chat ("I have a call with X tomorrow at 2pm" / "Hospitality Table meeting at 10am today").
- Any inbound asking-for-a-meeting context where the requester is in baseline (a chat message, an email forward, a referral note).

**Brief structure.** The pre-conversation brief is a section of `today.md` (or a standalone artifact if the meeting is high-stakes). It contains these subsections in order, each populated only if data is present — empty subsections are omitted, not stubbed:

1. **Stage and context.** What kind of meeting this is, where it came from (referrer, prior thread, calendar invite), where it sits in the relationship arc.
2. **Relationship state.** Signal class, RC tier if applicable, last_touch with days-ago count, momentum (warming / flat / cooling). Cite the source data.
3. **What's lingering.** Pull from the card's *What's lingering* and *Unresolved movement* sections. Quote the dated breadcrumb if recent enough to be relevant.
4. **What NOT to bring up.** Any sensitivities, prior awkwardness, unresolved tension, or topics the relationship has shown allergic reaction to. From card *Risks* section + IB notes. Explicit list — silence here is a failure.
5. **Open loops involving them.** Pull from `loop_ledger.md`. Anything owed *to* them, owed *from* them, or in-flight.
6. **Recent IBs.** Last 2–3 IBs, dated. Cite the source files.
7. **Relevant gap-surface.** Tenet 13a — any missing data on this person (no phone, no email, conflicting company, missing card) that should be filled during or after the conversation.
8. **Thesis lens (if applicable).** If the conversation touches a domain the selected user has a stated thesis on (`profiles/{profile_id}/profile.md` or the current legacy seed profile), name the thesis and how it applies.
9. **Network context.** Who else in `baseline_index.json` is in this person's orbit — same company, same Circle (read `circle_type` per ARCHITECTURE.md → "Same-Circle behavior"), prior shared connections. From `network_map.md` and `heuristics.md`. Pre-existing acquaintances flagged so Todd doesn't accidentally walk past them.
10. **Three questions / asks.** Calibrated to the conversation type — discovery, re-engagement, partnership, screening, etc. Specific to this person and this moment, not generic.
11. **Engagement-boundary check (if applicable).** If the conversation could turn into an unpaid-thinking or free-intro-broker ask, name the boundary. BridgePoint Ops engagement model applies.

**Voice.** Direct, Midwestern, peer-level. No fluff. The brief is for Todd's eyes — short enough that he'll read it before walking into the meeting.

**Provenance.** Every claim in the brief cites its source file. The brief itself is *not* an IB — it's a forward-looking artifact. The IB gets written *after* the conversation, drawing on the brief plus what actually happened.

**Worked example.** The 2026-05-12 brief for the Genius (Global Payments) phone screen produced sections 1–4, 7 (Mike Schwartz baseline gap), 8 (best-of-breed-vs-payments thesis), 9 (existing Genius/Global Payments cluster in network), 10 (three thesis-calibrated questions), 11 (boundary check on unpaid-thinking trap). That was the right shape; the auto-trigger should produce the same structure without Todd having to ask.

## What the system does not do

- It does not auto-send messages or auto-accept connection requests.
- It does not infer intent on behalf of other people. Only observed signals are recorded.
- It does not nag. An item surfaces in `today.md` once per crossing event; if you ignore it, it doesn't re-surface without new context.
- It does not optimize for activity. A quiet day is a valid day.
