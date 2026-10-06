# RB Intelligence Cycles

RB has two separate public-intelligence engines. Hunter owns deep,
target-specific research. Gatherer owns broad daily change detection. They must
never be combined in reporting, scheduling, or proof-of-work.

Gatherer is RBB's authoritative daily change-intelligence layer. Raw feeds,
source refreshes, email-derived public headlines, and scanners are Gatherer
inputs—not competing daily intelligence products. Downstream briefs and CoS
work should use Gatherer's normalized candidates, source coverage, RBB-state
comparison, and Hunter escalations as the primary daily view. Hunter remains
the verification and gap-resolution layer.

## 1. Routine research (baseline construction)

Purpose: establish and periodically refresh the durable background knowledge
RB needs about one named entity. This is historical, entity-centered research,
not a scan for today's news.

- Unit of work: one restaurant brand, operator, technology vendor, competitor,
  executive, or other explicitly tracked entity.
- Execution: prepare the applicable Hunter playbook with
  `system/scripts/hunter_cycle.py prepare`; submit that directive to ChatGPT
  Deep Research; finalize the returned Hunter JSON with `hunter_cycle.py
  finalize`. The former free-form deep-research-cycle process is retired.
- Search posture: start with the entity name and search broadly across primary
  and credible secondary sources.
- Required dimensions when applicable: company profile, ownership, leadership
  and leadership history, strategy/direction, technology stack and integrations,
  earnings/filings and operating trends, material partnerships, acquisitions,
  locations/footprint, and unresolved questions.
- Cadence: metered and weekend-first. Once initial coverage is populated, an
  entity becomes eligible for refresh after 90 days, with stale or incomplete
  priority accounts researched first.
- Persistence: findings enter a dedicated routine-research evidence queue.
  Evidence is assessed and routed through review-first, field-specific mutation
  paths. A research dossier is not itself proof that a canonical field changed.
- Proof: each run names the entity, sources checked, dimensions covered,
  evidence records added, proposals produced, gaps remaining, and next eligible
  refresh date.

## 2. Daily intelligence monitoring (change detection)

Purpose: detect current events about tracked names and compare them with the
existing baseline.

- Unit of work: new items from RB's monitored and continually improving public
  source registry, matched to watch-list, priority-account, competitor, and
  ecosystem names.
- Search posture: source-first monitoring plus targeted entity matching. It is
  not a broad historical search on every entity.
- Typical items: news, press releases, filings, earnings releases, leadership
  announcements, interviews, product/vendor announcements, and credible trade
  reporting.
- Cadence: daily.
- Processing: Gatherer applies a rolling 24-hour window, normalizes and
  deduplicates source items, resolves tracked entities, ranks materiality, and
  emits provisional change candidates. It quantifies which tracked entities
  were observed and never treats missing coverage as proof of no change.
- Execution: `system/scripts/intelligence_assessment.py` invokes
  `system/scripts/gatherer.py` after public-web ingestion. Gatherer's daily
  packet uses `rb.gatherer_daily_change_packet.v1` and is written to
  `system/.cache/gatherer_daily_change.json`.
- Escalation: Gatherer recommends Hunter's `change_monitor`,
  `industry_change_scan`, or a more specific playbook when a material signal
  requires verification. Gatherer never launches Hunter, writes canonical
  claims, or converts a headline directly into a mutation.
- Proof: each daily receipt reports collection, assessment, record/proposal,
  cascade, and delivery results. It must explicitly state that routine research
  was not performed by the daily cycle.

The daily pipeline may ingest evidence produced by a completed weekend routine-
research run. That ingestion is carry-over processing and must not be counted as
daily public-source discovery or as a routine-research run.
