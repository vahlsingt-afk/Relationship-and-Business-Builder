# Intelligence Brief Canonical Spec — RB 10.7

**Product:** Intelligence Brief (Part 1)
**Last reviewed:** 2026-08-25 (corrected to match actual live routing — see below)
**Authority:** This file governs Part 1 only. For Part 2, read `DAILY_BRIEF_CANONICAL_TEMPLATE.md`.

---

> **PRE-RENDERED BRIEF ARCHITECTURE (corrected 2026-08-25)**
> The Intelligence Brief is pre-rendered at 5am by `render_intelligence_brief.py`.
> **GPT role:** Call `getDailyBrief` → check `pre_rendered_brief.available` → if `true`, display `pre_rendered_brief.markdown` verbatim. Do not generate, reword, or add to it. This is what `custom_gpt_instructions_compact_8k.md`'s RULE 0 has actually specified all along.
> `getRenderedIntelligenceBrief` does not exist in the live Actions schema (retired 2026-08-25 — confirmed via `request.log`: zero calls in its entire history, so nothing regressed by removing it). This file previously instructed the GPT to call it instead of `getDailyBrief`; that instruction was never followed in practice and is now corrected.
> The rendering rules below now govern the Python renderer, not GPT output. The GPT's only rendering job is verbatim display.
> If the call fails or `pre_rendered_brief.available` is `false` → output the HARD RETRIEVAL GATE block from the instructions.

---

---

## The Intelligence Brief Is a Delta Report, Not a Telemetry Dump

The Intelligence Brief answers one question: **What is different today than when I went to bed last night?**

It does not answer: Why does it matter to me? What should I do? What are my priorities?
Those are Daily Brief (Part 2) questions.

**New information density rule:** Every fact must be new, changed, or directly relevant to an active thread. Historical state, static counts, and raw event telemetry (SMS event counts, graph record counts) do not belong in Part 1. If a user already knew it before opening the brief, it failed the density test.

A newspaper editor would cut anything that does not tell the reader what changed.

---

## DATA BINDING CONTRACT (RB 10.0 — HARD RULE)

Every fact, headline, count, and signal in the Intelligence Brief MUST be traceable to a specific field in the API response payload. This is not a style preference — it is the foundational trust contract.

### Headlines (Sections A–E)
- **URL** = `extras.source_url` — the only valid source for links. Never construct, guess, or recall a URL from training data.
- **Date** = `extras.pub_date` — the publication date from the payload. Never substitute today's date or a training-data date.
- **Title** = `title` field, verbatim or lightly trimmed. Never rewrite or paraphrase.
- **"Why it matters"** = summarize from `summary` or `why_it_matters` in the payload. Never inject training-knowledge context.
- **No `extras.source_url`** = skip the item entirely. Never render a headline without a real link.
- **No item in payload** = no headline. If you cannot point to the API item, the headline does not exist.

**PROHIBITED (rendering failure = trust collapse):**
- A headline that did not come from the API payload
- A URL recalled from training data (e.g., "https://bloomberg.com/...")
- A date not present in `extras.pub_date`
- Thematic headlines ("Consumer Spending Remains Resilient", "AI Adoption Accelerates") — these are analyst summaries, not news events

### Counts and Metrics
- All counts in the Proof Block come from `intelligence_collection_summary[0].extras`
- `extras.sources_scanned` is the list of named sources — render each one
- Personal source counts come from `resource_verification_and_freshness_status` and `communication_intelligence`
- Never estimate or round up counts ("approximately 30" is a failure; use the number from the payload)

### Zero-hallucination / no-generation rule (RB-INT-024 — CARDINAL RULE)

**Part 1 is a DATA RENDERER, not a content generator.** The Intelligence Brief is a newspaper, not a blog. The GPT's job is to format what the API sent — nothing more.

- Every headline must be an item in the API payload with `extras.source_url`
- **A rendered headline without a URL from `extras.source_url` is proof of hallucination.** If you cannot produce `[title](url)`, skip the item. Do not render any version of it — not a bullet, not a summary, nothing. A section with zero valid-URL items renders only: "[N] sources scanned. No material developments this cycle."
- If a section payload is empty or all items are stale: render the quiet-cycle message — do NOT generate headlines from training knowledge
- **Quiet-cycle format:** `"[N] sources scanned. No material developments this cycle."`
- Thematic sentences ("AI Adoption Accelerates", "Consumers Prioritize Value") are analyst summaries — they are not headlines. If the title does not name a specific entity taking a specific action, it does not belong in Sections A–D.
- PROHIBITED: Any headline, signal, or observation that did not come from the API response payload

---

## PROHIBITED SECTIONS (RB-DEFECT-056)

Any of the following appearing in the Intelligence Brief = **rendering failure**. Move to Daily Brief.

| Prohibited Section | Example Violation | Belongs In |
|---|---|---|
| Executive Assessment | "You have entered a significant transition week" | Daily Brief |
| **Executive Read** | "Today's useful signal is restaurant tech demand. The best personal lead is..." | Daily Brief |
| **Executive Takeaways** | "1. The restaurant industry remains resilient but value-focused. 2. ..." | Daily Brief |
| Executive Summary opening | "Today's environment remains characterized by..." | Daily Brief |
| **Mutations / Open Loops** | "1. Add market signal: '...' 2. Action loop: Review Guidepoint request..." | Daily Brief |
| Open Loops (as tasks) | "Obtain commission documentation" | Daily Brief |
| Intelligence Priorities as tasks | "Prepare Foods Connected presentation" | Daily Brief |
| Potential Impact sections | "Potential Impact: - Increased competitive pressure..." (multi-bullet analysis) | Daily Brief |
| CoS Intelligence Observations | "The enterprise market appears increasingly receptive..." | Daily Brief |
| Intelligence Bottom Line as advice | "The signal environment today is favorable" | Daily Brief |
| Strategic Assessment with probability ratings | "Global Payments -- Strong" | Daily Brief |
| Any recommendation | "You should consider..." | Daily Brief |
| Any career guidance | "Position yourself for..." | Daily Brief |
| "Why Todd cares" field on headlines | "Why Todd cares: This validates your thesis..." | Daily Brief |
| Summary paragraph under headlines | Multi-sentence analysis after a headline | Daily Brief |
| "This supports your thesis" language | Any thesis validation without named signal+source+date | Daily Brief |
| Second-person in Part 1 | "your Worldpay/Genius lane", "your thesis", "maps directly to you" | Daily Brief only |
| Chief of Staff Observation | "Chief of Staff Observation: Today's environment is..." | Daily Brief |
| "RB Take:" on headlines | "RB Take: This aligns with your long-held thesis..." | Daily Brief |

## PROHIBITED HEADLINE FORMAT (RB-DEFECT-056)

Headlines must be reportable facts -- the actual title of an event, not an analyst's characterization.

```
PROHIBITED: "Enterprise Technology Spending Remains Selective"  <- analyst opinion
PROHIBITED: "AI Adoption Accelerates in Restaurant Operations"  <- theme sentence
REQUIRED:   "Federal Reserve Signals Caution on Rate Cuts"      <- actual news event
REQUIRED:   "Toast Launches AI-Powered Operator Workflows"      <- named entity + specific action
```

---

## Canonical Section Order (Part 1) — corrected 2026-08-25

```
1.  What RB Found Without You Telling It   what_rb_found_without_you_telling_it
                               Autonomous-discovery digest — items pre-filtered upstream (daily_brief.py) to
                               novelty.autonomous_discovery_value in (high, medium) AND source_discovered=true,
                               deduped by title. Ranked act_today-first then by confidence, capped at 7.
                               Format: "- [ACTION TODAY]? **title** — summary — why_it_matters".
                               Empty → "No autonomous discoveries above monitor-only threshold this cycle."
                               Added 2026-08-25 (RB-DEFECT-2026-08-25): this section existed in the API payload
                               since RB-DEFECT-013 (RB 9.24B) but was never implemented by the pre-render
                               migration — computed and shipped, never actually displayed, until this fix.
2.  What Changed Today         what_changed_since_yesterday + personal_intelligence_delta + last_24h_relationship_signals + communication_intelligence
                               Named bullet facts: NEW interactions (named), state changes, email/SMS/calendar deltas, opportunity mutations.
                               personal_intelligence_delta items carry extras.delta_source (email/sms/calendar/tracked_opportunities).
                               Nothing new → one sentence.
3.  A: World Headlines         world_national_headlines (world-scope)
4.  B: National Headlines      world_national_headlines (US-scope) — never omit
5.  C: Restaurant Industry     restaurant_industry_headlines
6.  D: Restaurant Technology   restaurant_technology_headlines + what_todd_doesnt_know_yet (tech)
                               All headline sections: [title](extras.source_url) / Source | Date / 1-sentence why
                               No URL = skip entirely. Homepage URL (no article path) = skip. Target 5-7 per section.
                               Evergreen observations ("Operators Prioritize Margins") = skip — not news.
                               FRESHNESS GATE: skip any item where extras.pub_date is >7 days before today,
                               unless the item contains a named corporate event (earnings, M&A, funding, exec move).
                               VOLUME FLOOR: render at least 5 headlines per section. If payload has <5 fresh items,
                               use the Restaurant Tech Fallback (Section D) or flag the section as genuinely quiet
                               with a one-sentence explanation — never silently render 2-3 items.
7.  D+: Newsletter Inbox       newsletter_intelligence: per item — extras.source_name | extras.pub_date then each extras.articles[] entry as [title](url).
                               Min 3 articles per newsletter shown. No articles = skip item. Empty section = skip silently.
8.  E: Earnings & Corporate    watchlist_intelligence (earnings/exec/funding/M&A items)
9.  F: Watchlist Scan          watchlist_intelligence: count → material → ONE no-change sentence
10. G: Opportunities           opportunity_board + w2_intelligence: state + days-since + NEW/UNCHANGED/STALE⚠
11. H: Relationship Deltas     last_24h_relationship_signals + relationship_momentum_status: NEW/UNCHANGED/STALE⚠
12. H+: Identity Confirmations Needed   identity_match_candidates: one bullet per candidate ("[sender]" — [baseline name]? + summary + candidate_id).
                               Exact-name matches only, never auto-confirmed. Empty payload = skip silently.
                               Confirm/reject only after the operator replies in this turn or the immediately preceding one.
13. I: Strategic Signals       strategic_industry_signals: 3-5 signals, >=2 named evidence each — facts only, zero editorial
14. J: Proof Dashboard (LAST)  See format below — delta table, named counts, never vague labels.
                               Supersedes the legacy trust_metrics field (same purpose — source coverage/confidence
                               summary — different iteration; trust_metrics is not separately rendered).
```

Then offer Part 2: *"Intelligence picture complete. Want your Daily Brief?"*

---

## Delta Integrity Rule (RB-DEFECT-002)

**"Delta" means: new since the last brief cycle. Not: still exists. Not: still pending. Not: previously unreported.**

Section 1 (What Changed) must only contain items with event timestamps within the current delta window (since last brief). The delta window is the period since the previous brief was generated — typically 24 hours.

**Timestamp gate:** Before rendering any item from `what_changed_since_yesterday` or `overnight_delta_intelligence`:
- If `extras.event_date` or item timestamp is OUTSIDE the delta window → do not render as a delta
- If source is `active_threads.yaml` alone with no timestamp → item is a historical diff, not a delta
- If an item lists threads that were opened/closed weeks or months ago → PROHIBITED as "What Changed Today"

**PROHIBITED (rendering failure):**
- "2 threads closed, 8 new" when those threads were opened/closed before the current cycle
- Listing opportunity threads as "OPENED" when they pre-date the current brief by >24 hours
- Any overnight/24h label on an item whose event timestamp is not within that window

**When delta data is genuinely empty:** One sentence: "No material changes detected since last brief." Do not manufacture changes from historical state.

---

## Relationship Recommendation Topology Rule (RB-DEFECT-008)

Before rendering any introduction recommendation (Section H or Daily Brief):

**Required topology check — suppress if any condition is true:**
- Person A and Person B already share a named organization in the knowledge graph
- Person A and Person B have prior direct interaction in `interaction_ledger` or `communication_intelligence`
- Person B is listed as a member, participant, or affiliate of an organization Person A belongs to

**PROHIBITED:** Recommending an introduction between two people who already have a shared organizational context visible in the payload — this demonstrates topology blindness.

---

## Proof Dashboard Format (Section J — always last, never first)

**Position rule:** Render all 11 sections (What Changed → A → B → C → D → D+ → E → F → G → H → I) completely before opening Section J. The Proof Dashboard closing the brief is what proves the system ran — it is never a lead, opener, or second section. If the Proof Dashboard appears anywhere except after Section I, the brief has failed structurally.

The Proof Dashboard is a trust anchor, not a lead. It proves the system ran and what it found.
**Format: personal data delta table (Today / Yesterday / Delta), then news scan row, then Source Health.**

```
Proof Dashboard

Personal Data                 Today    Yesterday    Delta
Emails (threads)              N        N            +/-N
SMS (conversations)           N        N            +/-N
Calls                         N        N            +/-N
Calendar events               N        N            +/-N
Contacts                      N        N            +/-N
Mutations declared            N        N            +/-N
Watchlist entities scanned    N        —            —

News Scan
  World: N articles · [named sources]
  Restaurant: N articles · [named sources]
  Tech: N articles · [named sources]

Source Health
  ✓ Healthy: N sources
  ⚠ LinkedIn messaging — 311h stale (threshold: 48h)
  Freshness: N% | Trust: N%
```

**Payload field mapping (RB 10.7 — read from top-level `proof_dashboard` object):**

All counts are promoted to a flat top-level `proof_dashboard` object in the `getDailyBrief` response.
Do NOT navigate `canonical_brief.sections.intelligence_collection_summary[0].extras` — those nested paths are unreliable.

Personal data delta table — use these exact field pairs:
- Emails: `proof_dashboard.email_threads_today` / `proof_dashboard.email_threads_yesterday` / `proof_dashboard.email_threads_delta`
- SMS: `proof_dashboard.sms_events_today` / `proof_dashboard.sms_events_yesterday` / `proof_dashboard.sms_events_delta`
- Calls: `proof_dashboard.calls_today` / `proof_dashboard.calls_yesterday` / `proof_dashboard.calls_delta`
- Calendar: `proof_dashboard.calendar_events_today` / `proof_dashboard.calendar_events_yesterday` / `proof_dashboard.calendar_events_delta`
- Contacts: `proof_dashboard.contacts_total` / `proof_dashboard.contacts_yesterday` / `proof_dashboard.contacts_delta`
- Mutations: `proof_dashboard.mutations_today` / `proof_dashboard.mutations_yesterday` / `proof_dashboard.mutations_delta`
- Watchlist: `proof_dashboard.watchlist_entities_scanned` (no delta — count only)
- Yesterday = `null` on first run → render "—" (no snapshot yet)

News scan row:
- `proof_dashboard.world_articles_scanned` + `proof_dashboard.world_sources`
- `proof_dashboard.restaurant_articles_scanned` + `proof_dashboard.restaurant_sources`
- `proof_dashboard.tech_articles_scanned` + `proof_dashboard.tech_sources`

**Personal intelligence self-heal (RB-INT-018):**
If `proof_dashboard` is null or missing:
```
Personal data                 ⚠ stale — last sync Nh ago | Cached through [date]
```
Never output "Delta data unavailable in this session." Never output "Not connected in this session." Show what IS known (last sync age) — never a bare "unavailable."

**Rules:**
- Named publication counts are required in the news scan row — no named pubs = failure
- "No personal intelligence available" = failure
- Checkmark rows without counts = failure
- Per-entity watchlist rows = failure (watchlist is a single count line only)
- Delta = null/None on first run → render "—" not "0"
- PROHIBITED: "9,256 SMS events" as a finding — use delta only
- PROHIBITED: "9,833 graph records" — use mutations_today

---

## SUPERSEDED SECTIONS REMOVED (RB 10.4)

<!-- Old IB-x and Section A–J specs (lines 167–711 in pre-10.4 file) removed here. -->
<!-- Canonical spec is the 11-section delta-first order above and the Proof Dashboard format. -->

---

## Attribution Type Rendering Rule

| Type | Meaning | How to render |
|---|---|---|
| OBSERVED | Discovered from live scan this cycle | Full headline -- new intelligence |
| SYNTHESIS | Computed from multiple OBSERVED inputs | Full item -- analysis |
| MEMORY | From knowledge graph; no new scan evidence | [Context] prefix -- known state |
| HYPOTHESIS | User-stated belief, not scan-verified | [Hypothesis] prefix |

If majority of items are MEMORY: render banner: "! Limited new intelligence this cycle. Most content reflects existing knowledge base."

---

## Mutation Proof Receipt — `ingestContent` Response Format (RB-INT-MUTATION-VISIBILITY-001)

**Rule:** `ingestContent` must be called BEFORE any analysis of user-provided content. The receipt is rendered FIRST, then any analysis follows.

The receipt distinguishes four things the system did:
1. **Recorded** — items written to IntelligenceDB (`persisted_intelligence_count` + `persisted_intelligence` list)
2. **Proposed** — mutations that require confirmation (`mutation_proposals_count` + `mutation_proposals` list)
3. **Auto-applied** — executive declaration mutations that fired immediately (`executive_mutations_count`)
4. **Reason if zero** — why no mutations occurred (noise / matched existing / below confidence threshold)

**When mutations occurred:**
```
[RECEIPT] QSR Magazine — Domino's CEO Succession | Jun 22, 2026

Intelligence Recorded (persisted_intelligence_count: 2)
  • ri_event [Domino's Pizza, Russell Weiner, Joe Jordan] — high confidence
  • macro_signal [Domino's Pizza — QSR leadership] — medium confidence

Proposed Mutations (require confirmation — mutation_proposals_count: 2)
  • watchlist_add: Domino's Pizza — CEO succession at major QSR chain
  • entity_update: Russell Weiner — retiring CEO status

Say "confirm mutations" to apply, or "skip" to leave as proposals.
```

**When no mutations occurred:**
```
[RECEIPT] Article | Jun 22, 2026

Intelligence Recorded: 1 item
  • macro_signal [Federal Reserve] — matched existing intelligence

Proposed Mutations: 0
Reason: Input consistent with existing knowledge base. No new facts detected.
```

**Field mapping from `ingestContent` response:**
- `persisted_intelligence_count` → "Intelligence Recorded (N)"
- `persisted_intelligence[]` → list each `intelligence_type` + `entities` + `confidence`
- `mutation_proposals_count` → "Proposed Mutations (N)"
- `mutation_proposals[]` → list each proposal title/description
- `executive_mutations_count` → "Auto-applied (N)" — only for CEO declarations
- If all counts are 0 → render reason from `triage.identified_types[0].confidence` or "noise" classification

**PROHIBITED:**
- Analyzing, summarizing, or commenting on content before the receipt is rendered
- One-liner receipt that omits entity list and mutation proposals
- "No intelligence extracted" as the only output when `persisted_intelligence_count > 0`
- Skipping the receipt entirely and going straight to analysis

---

## Restaurant Technology Fallback Format (RB 10.6)

When Section D (`restaurant_technology_headlines`) returns the fallback item (`extras.fallback: true`), render it as-is. The fallback item contains:
- Proof of scan (how many sources were reviewed)
- "No material, net-new restaurant technology developments this cycle"
- Events to Watch (upcoming earnings/events from `watchlist_intelligence`)

**RENDER THE FALLBACK ITEM AS-IS. Do not add content.**

```
D: Restaurant Technology

Scanned [N] restaurant technology sources. No material, net-new restaurant technology developments this cycle.

Events to Watch (upcoming):
[Items from fallback item's summary — watchlist upcoming earnings/events]
```

**Rules:**
- Render the fallback `title` and `summary` fields verbatim — do NOT supplement with generated content
- "Events to Watch" content comes from the fallback item summary only — not from additional API calls or training knowledge
- If the fallback summary has no Events to Watch: the fallback item ends at the scan count line
- PROHIBITED: rendering old articles (>7 days) as current headlines to fill the section
- PROHIBITED: adding "State of Market" bullets from `strategic_industry_signals` — those are analytical and belong in Section I, not Section D. `strategic_industry_signals` content is NEVER rendered in Section D.
- PROHIBITED: generating thematic observations about the market when Section D is quiet

---

## Strategic Signals Rules (RB 10.6 — Editorial Firewall)

Section I is a pattern-recognition section, not a CoS advisory. It surfaces convergence — multiple independent data points pointing the same direction. It does NOT interpret what that means for the user.

**PROHIBITED in Section I (rendering failure):**
- "Your thesis is strengthening" — thesis validation is editorial
- "Worldpay fits the moment" — application to user's situation is CoS
- "Worldpay aligns perfectly with your background" — career framing belongs in Daily Brief
- "This positions you to..." — forward-looking career guidance
- "The signal environment is favorable" — qualitative assessment without named evidence
- Any second-person ("you", "your") language
- Any sentence that tells the user what an signal means for them personally

**REQUIRED format:**
```
1. [Pattern name — factual]
   Evidence: [Named entity A] did X (Source, Date) + [Named entity B] did Y (Source, Date)
   Signal: [One factual sentence describing the pattern — no interpretation of what it means for Todd]
```

The Daily Brief (Part 2) is where signals get connected to user context. Part 1 just proves the pattern exists.

---

## Personal Intelligence Self-Heal — Strengthened (RB 10.6)

**The rule:** Counts prove work was done. "I looked and found nothing" is radically different from "I didn't look." Never conflate them.

Even when personal sources return zero material findings, render counts from top-level `proof_dashboard` fields:

```
Personal Data                 Today    Yesterday    Delta
Emails (threads)              10       9            +1
SMS (conversations)           18       18           0
Calls                         6        6            0
Calendar events               4        3            +1
Mutations declared            0        0            0
```

Fields (all from `proof_dashboard`): `email_threads_today`, `sms_events_today`, `calls_today`, `calendar_events_today`, `mutations_today` + corresponding `_yesterday` and `_delta` fields.

**When a source is unavailable or stale**, use the self-heal format — never a bare "no data":
```
Personal intelligence (email, calendar) | ⚠ Email: stale — last sync 14h ago | Cached through Jun 21
                                        | Calendar: [ok] 4 events | 0 changes
                                        | SMS: ⚠ not connected | last known: Jun 20
```

**PROHIBITED (destroys trust):**
- "No personal intelligence available." — the worst failure: implies system didn't run
- "Email: No new data available." — sounds like failure, not zero findings
- "Personal intelligence: not connected in this session."
- Checkmark rows without counts ("✓ Global news sources scanned")
- Any row that omits counts when `proof_dashboard` has them

---

## Pass/Fail Criteria — RB 10.7

### PASSES if (all 14 sections present, delta-first order):
- [ ] Section "What RB Found Without You Telling It": autonomous-discovery items (act_today-first, capped at 7), or the explicit empty-state sentence when none clear the high/medium discovery-value bar
- [ ] Section 1 What Changed: named bullet facts from today including email/SMS/calendar deltas and opportunity mutations
- [ ] Sections A-D Headlines: all rendered items have `[title](url)` direct article links
- [ ] Sections A-D Headlines: no evergreen/thematic sentences rendered as news items
- [ ] Sections A-D: 5-7 headlines per section (fewer only if genuinely quiet)
- [ ] Section D+ Newsletter Inbox: present when `newsletter_intelligence` non-empty; each newsletter shows ≥3 article links
- [ ] Section E Earnings/Corporate: present or one-sentence "none this cycle"
- [ ] Section F Watchlist: scan count + material items + exactly ONE no-change sentence
- [ ] Section G Opportunities: each with state + days-since + NEW/UNCHANGED/STALE⚠ label
- [ ] Section H Relationship Deltas: named contacts with NEW/UNCHANGED/STALE⚠ labels
- [ ] Section H+ Identity Confirmations Needed: present when `identity_match_candidates` non-empty; each candidate carries a `candidate_id`; none confirmed/rejected without an explicit operator reply
- [ ] Section I Strategic Signals: 3-5 signals with >=2 named evidence items each — no editorial
- [ ] Section J Proof Dashboard: personal delta table (Today/Yesterday/Delta rows) + news scan + Source Health, LAST position
- [ ] No CoS analysis, recommendations, editorial sentences anywhere in Part 1
- [ ] Ends with Part 2 offer

### FAILS if:
- [ ] Brief leads with Proof Dashboard instead of "What RB Found Without You Telling It" (dashboard leads = failure)
- [ ] Any headline, signal, or observation was generated from base model knowledge rather than API payload
- [ ] Any headline rendered without direct article URL
- [ ] Any headline is a theme/evergreen sentence ("Operators Prioritize Margins", "Consolidation Continues", "AI Adoption Accelerates")
- [ ] Any headline URL is a publication homepage (wsj.com, not wsj.com/articles/...)
- [ ] Fewer than 5 headlines per section without a one-sentence "quiet cycle" explanation
- [ ] Any headline with pub_date >7 days old (unless named corporate event: earnings/M&A/funding/exec move)
- [ ] Section D renders old articles instead of Restaurant Tech Fallback format
- [ ] Section D+ `newsletter_intelligence` silently omitted when payload is non-empty
- [ ] Section D+ newsletter item rendered without article links
- [ ] Raw telemetry as standalone rows (9,256 SMS events / 115 calls / 9,833 graph records)
- [ ] Personal intelligence row says "No personal intelligence available" or "not connected" without counts
- [ ] Proof Dashboard shows checkmarks without counts
- [ ] Proof Dashboard uses old Source|Status|Findings table instead of delta table
- [ ] Watchlist has per-entity "No signals" rows
- [ ] Section G/H omits NEW/UNCHANGED/STALE⚠ labels
- [ ] Proof Dashboard is vague ("sources scanned: high") or leads the brief
- [ ] Section I contains "your thesis", "aligns perfectly", "positions you", or any second-person editorial
- [ ] Editorial/CoS sentences in Part 1 ("Winning vendors will likely...", "Restaurants don't need more software")
- [ ] "Intelligence Observations"/"Strategic Intelligence Bottom Line"/"Intelligence Assessment" appears
- [ ] "Unable to validate" appears
- [ ] Part 2 unavailability leaks action names or API architecture
- [ ] Part 2 offer missing

---

## The Newspaper Test

> "Am I rushing to the door to get the newspaper?"

A newspaper is valuable because it tells the reader what **changed**, surfaces **unexpected** developments, and provides **discovery** -- not recaps of what they already know.

If every fact in the Intelligence Brief was already known before opening it: **it failed.**
