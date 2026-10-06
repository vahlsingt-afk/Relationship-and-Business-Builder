# Claude Handoff — Gatherer Is RBB's Primary Daily Intelligence Engine

**Date:** 2026-10-02  
**Audience:** Claude instances working in Relationship & Business Builder  
**Decision:** Gatherer is the authoritative daily change-intelligence layer.

## Executive instruction

For daily public intelligence, do not independently interpret raw RSS items,
news headlines, scanner output, or source-refresh results as RBB's daily change
record. Those are inputs. Gatherer owns the normalized daily view.

The operating model is:

```text
Public feeds, scanners, filings and public-news inputs
                         ↓
                      Gatherer
       normalize · resolve · cluster · compare · rank
                         ↓
      Daily Brief / Intelligence Assessment / CoS intake
                         ↓
           Hunter verification where required
                         ↓
       governed mutation proposals and commentary
```

Gatherer answers: **what appears to have changed in the restaurant and
restaurant-technology ecosystem during the rolling last 24 hours?**

Hunter answers: **is the material candidate true, what is its exact scope and
current state, what RBB gap does it close, and what should be proposed or
connected downstream?**

Do not collapse these roles. Gatherer detects and routes. Hunter researches and
verifies. Existing mutation policy and human authorization control persistence.

## Authoritative files

- `system/research/GATHERER.md`
- `system/scripts/gatherer.py`
- `system/schemas/gatherer_daily_change.schema.json`
- `system/INTELLIGENCE_CYCLES.md`
- `system/research/HUNTER.md`
- `system/scripts/hunter_cycle.py`

The current daily packet is:

`system/.cache/gatherer_daily_change.json`

Historical validated packets are retained under:

`system/.cache/gatherer_history/`

Hunter-ready escalation candidates are appended to:

`system/.cache/gatherer_hunter_escalations.jsonl`

## Daily pipeline contract

`system/scripts/intelligence_assessment.py` must run Gatherer after public-web
collection. A successful daily intelligence assessment requires a current,
schema-valid Gatherer packet. The assessment exposes
`primary_daily_intelligence.engine: "Gatherer"` and reports Gatherer's packet,
source coverage, candidate count, and Hunter escalation count.

`system/scripts/daily_brief.py` should render Gatherer's normalized changes as
the primary 24-hour intelligence view. Raw collection counts and sustained
database convergences may supplement that view, but they must not replace it or
silently become facts.

If Gatherer is missing, stale, invalid, or has incomplete source coverage:

1. mark daily intelligence degraded;
2. report the exact collection or validation failure;
3. do not describe zero candidates as “no changes”; and
4. do not create a parallel headline summary as a workaround.

## What Gatherer must do

Each run must:

- enforce the rolling 24-hour discovery window;
- report publication/event-date precision separately;
- exclude unrelated world news unless a tracked entity resolves;
- resolve brands and restaurant-technology vendors to RBB target keys;
- normalize URLs and cluster multiple reports about one event;
- retain independent corroborating sources and evidence-chain limitations;
- compare each candidate with canonical public RBB state;
- distinguish new-to-RBB candidates, re-observed evidence, possible known-state
  confirmation, repeated monitoring signals, and unresolved entities;
- rank impact, ecosystem relevance, recency, and verification need separately;
- retain every change as `verification_state: candidate`;
- report expected, successful, and failed source checks;
- emit a schema-valid, atomically written packet and history record; and
- queue deduplicated Hunter-ready escalation jobs for material candidates.

Gatherer must not:

- treat a headline as a canonical fact;
- infer enterprise deployment from a pilot, announcement, logo, integration,
  franchisee, or location-level observation;
- write canonical entity, relationship, account, or competitor records;
- launch Hunter automatically;
- hide source outages behind a zero-result report; or
- use lack of a signal as proof that nothing changed.

## Hunter escalation workflow

A ready Gatherer candidate can be converted into a Hunter job with:

```bash
python3 system/scripts/hunter_cycle.py prepare-gatherer \
  --change-id <gchg-id> \
  --output /tmp/hunter-job.json
```

This carries forward Gatherer's target keys, known source URLs, prior-state
comparison, event hypothesis, and verification questions. It does not start
research. The calling cycle submits the prepared directive to ChatGPT Deep
Research and finalizes the returned Hunter packet normally.

## Claude behavior

When asked “what changed today,” “what is new,” “what happened in the
industry,” or similar:

1. read the current intelligence assessment and Gatherer packet;
2. state source coverage and packet freshness;
3. report candidates as provisional unless Hunter or canonical evidence has
   verified them;
4. distinguish new-to-RBB candidates from repeated coverage;
5. surface material Hunter escalations and unresolved entity matches; and
6. use CoS commentary only to connect cited, confidence-labeled evidence.

Do not browse independently merely to regenerate Gatherer's daily scan. Use
Hunter when targeted verification or gap resolution is required.

## Verification

Run:

```bash
python3 -m json.tool system/schemas/gatherer_daily_change.schema.json
python3 -m pytest system/tests/test_gatherer.py -q
python3 system/scripts/intelligence_assessment.py --smoke
python3 system/scripts/hunter_cycle_audit.py
```

When changing the daily intelligence pipeline, preserve tests for:

- window enforcement;
- noise exclusion;
- event clustering;
- canonical-state comparison;
- source-health reporting;
- schema-gated atomic persistence;
- history retention;
- deduplicated Hunter escalation; and
- the Gatherer-to-Hunter handoff boundary.

## Bottom line

Gatherer is RBB's daily intelligence front door. Raw public sources flow into
it. RBB's daily briefs and Chief-of-Staff layer consume its normalized change
packet. Hunter verifies the material uncertainties Gatherer identifies. No
other daily process should create a competing public-change record.
