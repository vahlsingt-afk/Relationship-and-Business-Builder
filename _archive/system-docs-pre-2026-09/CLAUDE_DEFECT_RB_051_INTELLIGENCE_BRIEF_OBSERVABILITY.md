# RB-DEFECT-051: Intelligence Brief Missing Observability Layer

**Date filed:** 2026-06-17
**Filed by:** Todd
**Status:** Active — implementation in progress (RB 9.94)
**Priority:** Critical
**Category:** Intelligence Brief / Proof of Work / CoS Architecture
**Score:** 5.5/10 against canonical Intelligence Brief specification

---

## Problem Statement

The Intelligence Brief is a curated news summary, not an intelligence product.
It does not prove that intelligence gathering occurred. It cannot be distinguished
from "search restaurant news on Google and summarize." A world-class CoS brief
must demonstrate that it did the work.

Seven specific defects identified:

---

## Defect 1 — No Proof Layer (Critical)

**Expected opening:**
```
Intelligence Collection Proof
Collection Window: June 16 10:00 PM – June 17 6:00 AM

Sources Scanned:
• 1,247 news articles
• 38 restaurant technology publications
• 20 companies on watchlist
• 14 executive watch-list individuals
• SEC filings and press releases
• Earnings calendars
• LinkedIn network mutations
• Email activity (7 messages)
• SMS activity (2 conversations)
• Calendar activity (3 events)
• Relationship database changes

Material Findings:
• 3 watchlist companies had material updates
• 1 executive movement detected
• 2 macro industry developments
• 0 unresolved relationship mutations
• 4 items promoted to Intelligence Brief
```

**Data available:** `intelligence_collection_summary[0].extras` contains:
`collection_window`, `sources_attempted`, `sources_healthy`, `sources_stale`,
`sources_failed`, `records_processed`, `mutations_generated`, `stale_sources`,
`sources_scanned` list.

**Actual:** One-line proof block or nothing visible. No collection window. No source list.
No material findings count.

---

## Defect 2 — No Cross-Source Proof (Personal Information Sources)

**Expected section:**
```
Personal Information Sources

Email:       7 messages scanned | 18 awaiting your response | 12 responses received
SMS:         9,143 events | 211 matched contacts | 0 urgency
Calls:       106 events | 4 matched | 4 missed calls (Jeff Wayman Jun 10, Ryan H. Jun 8)
Calendar:    3 upcoming meetings | 4 meetings needing prep
Contacts:    No job changes detected | No relationship mutations
LinkedIn:    LinkedIn messaging stale (212h old) — 0 updates available
```

Even when nothing happened: "0 changes" is intelligence. Silence is not.

**Data available:**
- `communication_intelligence[0]` — email awaiting/received/followup counts
- `resource_verification_and_freshness_status` — per-source health with counts
  - Apple Messages: 9,143 events, 211 matched contacts
  - Apple Calls: 106 events, 4 matched, 4 urgency signals (missed calls)
- `signal_freshness` — per-source freshness with age and threshold

**Actual:** Not rendered at all. No email count. No SMS count. No calls summary.
No relationship mutation count. No LinkedIn health status.

---

## Defect 3 — No Collection Statistics

**Expected:**
```
Collection Metrics
Articles scanned:       9,573 records processed
Articles promoted:      7 elevated to brief
Market signals:         41 processed | 0 elevated (stale source — 87h old)
Mutations generated:    10
Watch-list entities:    22 scanned | 3 material updates | 19 no change
Sources healthy:        12 of 18 (67%)
Trust score:            83%
```

**Data available:** `intelligence_collection_summary[0].extras`:
`records_processed: 9573`, `mutations_generated: 10`, `sources_healthy: 12`,
`sources_attempted: 18`, `trust_score: 83`, `freshness_pct: 70`.

**Actual:** Not shown.

---

## Defect 4 — No Explicit Watchlist Scan Results

**Expected:**
```
Watchlist Scan — 22 entities

Material Updates:
✓ Global Payments — [signal]
✓ Worldpay — [signal]
✓ Yum Brands — [signal]

No Material Updates:
✓ PAR Technology  ✓ Olo  ✓ NCR Voyix  ✓ Agilysys
✓ Restaurant365   ✓ Toast  ✓ McDonald's  ...
```

**Data available:** `watchlist_intelligence` has 26 items including per-entity entries
with `entity_name`, `entity_type`, disposition, and 2 rollup items with
`extras.no_change_entities` lists.

**Actual:** Rollup sentence only. No ✓ scan proof per entity.

---

## Defect 5 — No Knowledge Base Mutation Reporting

**Expected:**
```
Knowledge Base Mutations

Added:
• Yum capital allocation thesis toward AI platforms
• PAR Pizza Factory platform expansion

Updated:
• McDonald's AI Drive-Thru pilot status

Generated Monitoring:
• Monitor Byte investment announcements
• Monitor PAR platform wins
```

**Data available:** `graph_mutation_log` — RI events persisted/proposed,
passive RI proposals, market signals processed/elevated.
`strategic_events.json` — strategic event log with timestamps.
`audit/2026-06.jsonl` — audit trail of mutations.

**Actual:** Not rendered.

---

## Defect 6 — No Freshness Verification / Collection Health

**Expected:**
```
Collection Health
Last full scan: 10:00 AM CDT | Status: DEGRADED ⚠

Healthy (12):    email:personal, email:bridgepoint, calendar:personal,
                 calendar:bridgepoint, calls, messages, linkedin_public_posts...
Stale (5):       linkedin_messaging (212h old — threshold 48h)
                 market_signals (87h old — threshold 72h)
                 social_engagement (713h old)
                 social_own_posts (713h old)
                 strategic_operators (never)
Failed (0):      none
```

**Data available:** `intelligence_collection_summary[0].extras.stale_sources`,
`signal_freshness` per-source items with age/threshold, `source_audit`.

**Actual:** Collection health mentioned in one line at most. Stale sources named but
no thresholds, ages, or per-source status table.

---

## Defect 7 — Too Interpretive (Architecture Separation)

Intelligence Brief = factual layer (what happened, how we know).
Daily Brief = CoS layer (priorities, recommendations, actions).

These must be separate documents. Recommendations and "next actions" belong in the
Daily Brief, not the Intelligence Brief.

**Current:** Recommendations mixed into Intelligence Brief output. No clean separation.

**Required:** Intelligence Brief answers only:
- What was scanned?
- What is healthy/stale/failed?
- What changed?
- What did we learn?
- What requires monitoring?

Daily Brief answers:
- What should I do about it?
- What are my priorities?
- What meetings need prep?
- What are the risks and opportunities?

---

## Canonical Intelligence Brief Structure

```
📡 Intelligence Collection Proof
   Collection window | Sources attempted/healthy/stale | Records processed | Mutations | Trust score

⚕ Collection Health
   Per-source: healthy/stale/failed | Age vs threshold | Last refresh

📬 Personal Information Sources
   Email | SMS | Calls (with missed call alerts) | Calendar | Contacts | LinkedIn

📊 Collection Metrics
   Articles scanned | Promoted | Market signals | Mutations generated

🔍 Watchlist Scan Results
   Material updates (named) | No material updates (full entity list with ✓)

🧠 Knowledge Base Mutations
   Added | Updated | Generated monitoring tasks

🌐 World & Macro News
   New developments only — no MEMORY items

🏭 Industry & Watchlist Intelligence
   Material updates only — named entities with signals

🔭 Open Intelligence Questions
   Items requiring monitoring
```

**Intelligence Brief does NOT contain:** Priorities, recommendations, next actions,
prep requirements, career guidance, relationship coaching. Those are Daily Brief.

---

## Data Fields Mapping

| Section | Source Fields |
|---|---|
| Collection Proof | `intelligence_collection_summary[0].extras` |
| Collection Health | `intelligence_collection_summary[1]` (stale list) + `signal_freshness` |
| Personal Sources | `communication_intelligence`, `resource_verification_and_freshness_status` |
| Collection Metrics | `intelligence_collection_summary[0].extras` (records_processed, mutations_generated) |
| Watchlist Scan | `watchlist_intelligence` (per-entity + rollup with no_change_entities) |
| KB Mutations | `graph_mutation_log`, `strategic_events.json` |
| Macro News | `world_national_headlines`, `world_macro_macroeconomic_impact` |
| Industry/Watchlist | `restaurant_technology_headlines`, `watchlist_intelligence` (material items) |
| Open Questions | `reconciliation_prompts`, `intelligence_cycle_continuation` |

---

## Implementation (RB 9.94)

### Files to change

1. `system/api/DAILY_BRIEF_CANONICAL_TEMPLATE.md`
   - Restructure Part 1 as Intelligence Brief with full observability opening
   - Add canonical sections: Collection Proof, Collection Health, Personal Sources,
     Collection Metrics, Watchlist Scan, KB Mutations
   - Explicitly separate Intelligence Brief (factual) from Daily Brief (CoS)

2. `system/api/custom_gpt_instructions_8k.md`
   - Add rendering specs for all 6 new observability sections
   - Add the "no recommendations in Intelligence Brief" hard rule

### Acceptance Criteria

- [ ] Intelligence Brief opens with Collection Proof showing collection window,
      source counts, and material findings
- [ ] Collection Health shows per-source status (healthy/stale/failed) with age vs threshold
- [ ] Personal Information Sources renders email, SMS, calls, calendar, contacts, LinkedIn
      counts — even when zero ("0 changes" is intelligence)
- [ ] Watchlist Scan shows every entity with ✓ status, material vs no-material grouping
- [ ] Knowledge Base Mutations shows what was added, updated, and what monitoring was generated
- [ ] No recommendations or "next actions" appear in Intelligence Brief
- [ ] Missed calls surface in Calls section (urgency signals)
- [ ] Stale sources named with age AND threshold (not just "stale")
