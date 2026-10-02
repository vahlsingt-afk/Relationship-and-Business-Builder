# Claude Sprint — RB 9.11 Continuous Strategic Awareness

Date: 2026-05-26

## Sprint Thesis

RB has moved from daily brief generation into a strategic operating layer, but two gaps remain:

1. RB can now process operator-provided LinkedIn signals, but it still depends too much on user-driven ingestion.
2. RB can now merge evidence into strategic events, but it still needs proactive public-source feeders and stronger relationship/action assessment for industry signals.

The next sprint should make RB less reactive and more continuously aware.

## Recent Codex Progress

Codex added two new foundations:

- `system/scripts/linkedin_freshness_bridge.py`
  - Processes operator-provided LinkedIn screenshots/text/URLs without unsupported scraping.
  - Extracts entities, classifies signal type, detects selected-profile thesis alignment, records meaningful signals, dedupes, and promotes industry items into `market_signals`.
  - Exposed through `POST /linkedin/signals` with operationId `processLinkedInSignal`.

- `system/scripts/strategic_events.py`
  - Creates persistent strategic event objects above individual market/news/social rows.
  - Merges evidence by event key.
  - Scores confidence by source-channel convergence.
  - Tracks lifecycle: `rumor_or_early_signal`, `early_signal`, `corroborated_signal`, `validated_signal`.
  - Links related events into recurring themes such as operational AI trust.
  - Integrated into Daily Brief section `strategic_industry_signals`.

Current test state:

- `python3 -m pytest system/tests -q` → 29 passed
- `python3 system/scripts/daily_brief.py --smoke` → 0 failures
- `python3 system/scripts/validate_openapi_gpt.py` → OK, 29 ops

## Primary Sprint Goal

Build the missing proactive source layer so strategic industry/operator/customer events enter RB before the user manually introduces them through LinkedIn or chat.

The desired behavior:

1. Public source reports a strategic restaurant-tech/operator signal.
2. RB classifies it into `market_signals`.
3. RB merges it into a persistent `strategic_events` object.
4. Later LinkedIn/social/user artifacts attach as additional evidence.
5. Daily Brief surfaces multi-source convergence when confidence and strategic relevance are high.

LinkedIn should be confidence/sentiment amplification, not the only discovery path.

## Priority 1 — Public Source Feeder For Restaurant-Tech / Operator Signals

Build a fixture-first feeder for the existing `market_signals` lane.

Recommended script:

`system/scripts/market_source_feeds.py`

Scope:

- No broad crawler.
- No unsupported scraping.
- Start with source-specific RSS or publicly fetchable feeds where available.
- Preserve manual/operator-curated rows as first-class inputs.
- Store raw or normalized candidate rows before elevation.

Candidate source families:

- restaurant trade news
- enterprise restaurant technology news
- legal/business news
- company press releases
- AI implementation failure reporting
- franchise/operator litigation or sentiment
- restaurant location closures and openings
- mergers, acquisitions, purchases, sell-offs, divestitures, and consolidation
- earnings reports, same-store sales, traffic, margin, unit-growth, and other health indicators
- operator investment announcements, development agreements, and market-entry/exit signals
- technology provider customer wins and deployment announcements
- technology provider product launches, product updates, integrations, and roadmap signals
- public technology provider earnings calls and investor updates
- technology provider C-suite / board / leadership changes
- financial/news aggregation rows when source-backed

Initial source targets can be fixture-backed if live feeds are unstable:

- Restaurant Dive
- Restaurant Business
- Nation's Restaurant News
- QSR Magazine
- PYMNTS / Reuters pickup style rows
- PRNewswire/company press rows
- legal/business news rows for restaurant-tech lawsuits

Output shape should map cleanly into `market_signals.normalize_item()`:

```json
{
  "title": "...",
  "url": "...",
  "source_name": "...",
  "source_type": "vertical_trade|mainstream|public_company_primary|company_blog|linkedin_social|event_signal|legal_business_news",
  "published_at": "YYYY-MM-DD",
  "company": "...",
  "side": "vendor_supply|operator_demand",
  "category": "restaurant_ai|pos|payments|labor|drive_thru|inventory|unit_growth|m_and_a|leadership|...",
  "signal_type": "implementation_failure|lawsuit|pilot_rollback|product_launch|product_update|provider_win|location_closure|location_opening|merger_acquisition|divestiture_selloff|investment_announcement|earnings_signal|c_suite_change|operator_priority|...",
  "pain_point_or_priority": "...",
  "strategic_relevance": "high|medium|low|none",
  "confidence": "high|medium|low"
}
```

Acceptance criteria:

- Given a fixture row about Pizza Hut / Yum / Dragontail litigation, RB emits a `market_signals` row without user LinkedIn input.
- The row includes `restaurant_ai`, implementation/lawsuit risk, franchisee/operator trust, and operational reliability language.
- Given fixture rows about restaurant closures/openings, M&A/divestiture, investment, earnings health indicators, tech-provider wins, product updates, and C-suite changes, RB classifies them into specific `signal_type` values rather than generic `market_update`.
- `refresh_sources.py --market --save-health` treats the feeder output as refreshed.
- Missing/unavailable live feeds are reported as source-health gaps, not silently replaced with synthesis.

## Priority 2 — Convergence Hardening

Codex created `strategic_events.py`; Claude should harden it into the durable event model.

Tasks:

- Add a formal protocol doc, likely `system/protocols/P-037_strategic_event_convergence.md`.
- Add schema doc or JSON schema for `system/strategic_events.json`.
- Extend event matching beyond the first hardcoded patterns:
  - normalized company sets
  - normalized topic tags
  - URL canonicalization
  - source-channel dedupe
  - recurring theme clustering
- Add explicit proof stats:
  - events created
  - evidence attached
  - duplicate evidence skipped
  - stale evidence ignored
  - isolated signals suppressed
  - validated signals surfaced
- Add source freshness effects:
  - stale source cannot promote to `validated_signal` by itself
  - manual/user-provided evidence can amplify but should not become `system_detected`

Acceptance criteria:

- Multiple rows about Pizza Hut / Dragontail from public news, legal/business source, and LinkedIn merge into one event.
- Starbucks AI inventory rollback links as a related event under operational AI trust.
- Daily Brief surfaces “Multiple-source convergence detected” only when independent evidence exists.
- Isolated LinkedIn commentary remains useful but does not masquerade as proactive discovery.

## Priority 3 — Wire `ri_assessment` Into `market_signals.py`

This was already on the 9.10 roadmap and belongs here because convergence is only useful if it can affect relationships and actions.

Current known deferred item:

> `market_signals.py` produces company/industry signals that mention people and operators. P-036 specifies it should produce `ri_assessment` blocks when signals mention known contacts or active-thread companies.

Tasks:

- For each enriched market signal, produce a P-036-compatible `ri_assessment`.
- Map companies in market rows to:
  - active threads
  - baseline contacts
  - circles/watchlists
  - strategic operators
- Classify status:
  - `proposed` when a relationship/action consequence is plausible
  - `blocked` when entity exists but no relationship path is resolved
  - `irrelevant` when no action consequence exists
  - `unavailable` when source freshness prevents a confident claim
- Do not create touch/contact mutations from market news alone.

Acceptance criteria:

- Global Payments/Genius market signal maps to the active Genius/Global Payments thread.
- Pizza Hut/Yum/Dragontail maps to operational AI thesis and target restaurant-tech/operator watchlist, even if no direct contact is matched.
- Starbucks rollback maps as market-pattern evidence, not a relationship touch.
- Daily Brief can explain “relationship relevance: high/medium/low” from returned `ri_assessment`, not prose inference.

## Priority 4 — Populate Actual Watchlists

This is from the roadmap and should be included only if Priority 1 needs a monitored universe.

Roadmap item:

> Expand `system/restaurant_tech_watchlist.md` from definition into an actual monitored company set: 30-50 restaurant-tech vendors and top restaurant brands/operators.

Minimum sprint scope:

- Add a machine-readable watchlist file or section for:
  - top restaurant-tech vendors
  - top operator/brand ecosystems
  - franchise/operator groups when known
- Include:
  - category
  - side (`vendor_supply` or `operator_demand`)
  - source feeds to monitor
  - network proximity if known
  - why RB cares
  - monitoring status

Acceptance criteria:

- Feeder can prioritize watchlist entities.
- Market/convergence scoring boosts watched entities.
- Daily Brief does not treat non-watchlist mainstream AI news as high priority unless it directly maps to Todd’s thesis or network.

## Explicit Non-Goals

- Do not build unsupported LinkedIn scraping.
- Do not store LinkedIn passwords/cookies.
- Do not turn Daily Brief into a generic news digest.
- Do not promote every article into a strategic event.
- Do not claim source freshness when live source fetches failed.
- Do not mutate relationship state from public news without review/confirmation.

## Tests To Add

Add or extend tests for:

- `RB-MULTI-SOURCE-SIGNAL-CONVERGENCE-001`
- `RB-LI-DAILY-SIGNAL-BRIDGE-001`
- market source feeder fixture ingestion
- market signal P-036 `ri_assessment`
- source-health reporting for market feeder success/failure
- daily brief strategic industry signal rendering

Minimum expected commands:

```bash
python3 -m pytest system/tests -q
python3 system/scripts/daily_brief.py --smoke
python3 system/scripts/validate_openapi_gpt.py
```

## Roadmap Items Not In This Sprint

Keep these out unless they block the above:

- Composition layer / draft-in-voice
- DRR eval framework
- P-019 Apple local data first-run
- MCP registration
- missing RC cards
- Monday active-thread cleanup
- operator-experience-level setting
- full LinkedIn official API path beyond current governance/safety work

## Definition Of Done

RB should be able to answer:

> “How did you know this mattered before Todd pasted the LinkedIn post?”

With proof:

- source(s) checked
- source freshness
- normalized signal row(s)
- strategic event id
- convergence score/lifecycle
- related event/theme
- relationship/action relevance
- Daily Brief placement decision

This is the difference between reactive ingestion and continuous strategic awareness.
