# Gatherer — RBB Daily Ecosystem Change Engine

Gatherer is RBB's lightweight daily public-intelligence engine. It answers one
question: **what appears to have changed across the restaurant and
restaurant-technology ecosystem during the last 24 hours?**

Gatherer is the authoritative daily change-intelligence layer for RBB. Source
feeds, scanners, and public-news collection are inputs to Gatherer. Daily
briefs, intelligence assessments, and CoS commentary should consume Gatherer's
normalized packet rather than independently treating raw headlines as facts or
creating a parallel daily-change interpretation.

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

Gatherer also compares each candidate with canonical public RBB state. It
distinguishes a new-to-RBB candidate from re-observed evidence, possible
confirmation of known state, a repeated monitoring signal, and a signal that
still needs entity resolution. This is a conservative comparison aid, not an
automatic factual conclusion.

The ranking model keeps impact, ecosystem relevance, recency, and verification
need separate. A sensational headline cannot become important merely because it
is recent, and a high-impact claim does not become reliable merely because it
ranks highly. Gatherer clusters event-level duplicates, records independent
corroboration, resolves tracked entity IDs as well as names, and compares each
signal with the prior daily packet so repeated coverage is distinguished from a
new change.

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
Each escalation includes resolved target keys when available, the complete
source URL set, and a small verification-question bundle so Hunter can start
from the signal rather than repeating Gatherer's scan.

Ready escalations are append-only in
`system/.cache/gatherer_hunter_escalations.jsonl`, deduplicated by event and
playbook. They can be converted into a complete Hunter job without launching
research:

```bash
python3 system/scripts/hunter_cycle.py prepare-gatherer \
  --change-id <gchg-id> --output /tmp/hunter-job.json
```

Gatherer never runs that job itself.

## Daily pipeline position

`intelligence_assessment.py` runs Gatherer after public-web ingestion and before
convergence analysis. The durable daily packet is written to
`system/.cache/gatherer_daily_change.json` and embedded in the intelligence
assessment as `phase_2_gatherer`.

Every write is schema-validated and atomic. Timestamped packets are retained
under `system/.cache/gatherer_history/`. The packet includes configured source
checks, failures, coverage percentage, input errors, canonical-comparison
status, and the number of newly queued Hunter jobs. An unmeasured or partial
source run cannot masquerade as proof that nothing changed.
