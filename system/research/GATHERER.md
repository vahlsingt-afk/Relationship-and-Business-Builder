# Gatherer — RBB Daily Ecosystem Change Engine

Gatherer is RBB's lightweight daily public-intelligence engine. It answers one
question: **what appears to have changed across the restaurant and
restaurant-technology ecosystem during the last 24 hours?**

Gatherer is deliberately different from Hunter. Hunter performs bounded,
target-specific research and attempts to close evidence gaps. Gatherer scans
broadly, detects and ranks recent change signals, and hands uncertain or
material items to Hunter for verification.

## Contract

Each daily run:

1. consumes the fresh public-web and industry-news results already collected by
   the morning pipeline;
2. applies an explicit rolling 24-hour cutoff;
3. excludes general world news unless it names a tracked ecosystem entity;
4. normalizes URLs and titles and removes duplicates;
5. identifies tracked entities, signal types, source ownership, and confidence;
6. ranks material changes without promoting headlines into facts;
7. emits one `rb.gatherer_daily_change_packet.v1` packet; and
8. proposes Hunter follow-up for high-impact, weakly corroborated, conflicted,
   or strategically relevant changes.

Gatherer is not a canonical-data writer. A headline is a candidate signal, not
a verified deployment, leadership change, customer relationship, transaction,
or product fact. Existing review-first mutation policy remains authoritative.

## Scope

The universe is the set of active entities in
`system/ecosystem_intelligence.json`, plus restaurant-industry and
restaurant-technology signals returned by the configured daily public feeds.
Coverage is reported honestly: `tracked_entities_observed` is not the same as
the entire tracked universe, and lack of a signal is never evidence of no
change.

The default window is the 24 hours ending at `generated_at`. Date-only feed
timestamps are admitted when their calendar date overlaps that UTC window and
are marked `date_precision: day`.

## Escalation to Hunter

Gatherer creates a Hunter escalation candidate when a signal is both material
and needs verification. Typical reasons are M&A, funding, bankruptcy,
leadership movement, customer wins/losses, deployments, product changes, or
conflicting reports. The candidate recommends `change_monitor` unless a more
specific Hunter playbook is evident. Gatherer never launches Hunter itself —
an operator turns a day's escalations into an actual research job by hand,
via `hunter_cycle.py prepare-gatherer` (see "Handing escalations to Hunter"
below).

## Event clustering

Different outlets covering the same real-world event produce different URLs
and titles. Gatherer groups changes sharing the same tracked entities, signal
type, and observed date into one change; the highest-materiality member
becomes the primary change, and every other member is kept on it as a
`corroborating_sources` entry (never silently dropped). Clustering is scoped
to entity-bearing changes only — entity-less changes are never merged by
title alone, since that risks collapsing genuinely distinct stories.

## Canonical-state comparison (novelty)

Every change carries a `novelty` classification against Gatherer's own
cross-run history (`system/research/gatherer_history.jsonl`) and against
`ecosystem_intelligence.json`'s active relationships:

- `unresolved_entity` — no tracked entity could be matched; nothing else can
  be classified until that is resolved.
- `re_observed` — the exact same article (by source URL) already appeared in
  a prior run's history.
- `possible_known_state_confirmation` — the entity already has an active
  relationship on record (a coarse heuristic: it means the entity is not a
  blank slate in this area, not that this specific headline matches that
  specific relationship).
- `repeated_monitoring_signal` — the same entity and signal type were already
  seen in prior history, under a different URL.
- `new_to_rbb` — none of the above; genuinely new information.

## Separately-ranked dimensions

Each change carries four independent 0-100 scores under `scores`, kept apart
from the single `materiality` composite (which remains for backward
compatibility and is what sorting and Hunter-escalation thresholds use):
`impact` (how significant this signal type is, independent of entity match
or recency), `ecosystem_relevance` (how related to RBB's tracked ecosystem),
`recency` (linear decay across the 24-hour window), and `verification_need`
(driven by confidence, unresolved entities, and high materiality). A blended
single score hides which of these actually drove it — these stay separate on
purpose.

## Source health and degraded detection

Every packet reports `source_health` (expected/succeeded/failed source
counts, failed source names, upstream status) and a top-level `status`
of `ok`, `degraded`, or `unknown`. `degraded` means a short or empty
`changes` list may reflect fetch failures, not a genuinely quiet 24 hours —
Gatherer never lets that ambiguity pass silently. `intelligence_assessment.py`
carries this into `primary_daily_intelligence.status`/`counts`/`source_health`,
and `daily_brief.py`'s Gatherer section (not the separate world/national or
industry-headline sections — see "Non-interference" below) surfaces an
explicit degraded-state notice whenever `status == "degraded"` and no
changes were detected that run.

## History and escalation persistence

- `write_packet()` writes the latest packet atomically (temp file + rename)
  to `system/.cache/gatherer_daily_change.json` — a killed process or full
  disk never leaves a reader with a truncated file.
- `write_history()` appends one line per run to
  `system/research/gatherer_history.jsonl` (tracked, append-only). This is
  what novelty classification compares new changes against.
- `append_hunter_escalations()` appends genuinely new escalation candidates
  to `system/research/gatherer_hunter_escalations.jsonl`, deduplicated by
  `change_id` across runs — a change still inside the rolling 24-hour window
  on two consecutive days is escalated once, not twice.

## Handing escalations to Hunter

`hunter.prepare_gatherer_cycle(playbook, packet=...)` and the
`hunter_cycle.py prepare-gatherer <playbook> --output <file>` CLI turn one
packet's escalations recommending a given playbook into the same kind of
research-ready cycle directive `prepare_cycle()` builds for the tracked
gap-manifest universes (brands/competitors/franchisees/fdd), so the existing
`hunter_cycle.py prepare`/`finalize` flow carries them through unchanged.
Gatherer-sourced targets use `target_key` values of the form
`gatherer:<change_id>` and have no prior Hunter coverage history by
definition — that is accurate, not a gap.

## Daily pipeline position

`intelligence_assessment.py` runs Gatherer after public-web ingestion and before
convergence analysis. The durable daily packet is written to
`system/.cache/gatherer_daily_change.json` and embedded in the intelligence
assessment as `phase_2_gatherer`. A summary — `engine`, `status`, `packet_id`,
`counts` (changes / hunter_escalations / new_hunter_escalations), `coverage`,
`source_health` — is also written as the top-level `primary_daily_intelligence`
block, purely additive alongside `phase_2_gatherer`.

## Non-interference with existing raw-feed coverage

Gatherer is strictly additive. It reads `phase_1_web` (`industry_primary`,
`industry_technology`, `world_national`) without ever mutating it, and
`daily_brief.py`'s World & National Headlines, Restaurant Industry Headlines,
and Restaurant Technology Headlines sections (and `condensed_industry_context`)
all read `report["web_scan"]` — the raw phase_1_web data — directly and
independently of `phase_2_gatherer`. Gatherer's own filtering, clustering,
ranking, and degraded-state handling apply only to its own derived `changes`
list and its own section of the brief; they never gate, filter, or replace
the macro/micro world-news, industry-news, and press-release coverage those
other sections already provide.

