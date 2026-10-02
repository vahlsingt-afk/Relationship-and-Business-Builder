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
specific Hunter playbook is evident. Gatherer never launches Hunter itself.

## Daily pipeline position

`intelligence_assessment.py` runs Gatherer after public-web ingestion and before
convergence analysis. The durable daily packet is written to
`system/.cache/gatherer_daily_change.json` and embedded in the intelligence
assessment as `phase_2_gatherer`.

