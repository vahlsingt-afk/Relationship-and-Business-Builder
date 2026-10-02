# Sprint RB 9.57 — Daily Brief Architecture

**Date:** 2026-06-06  
**Severity:** High  
**Source:** Product defect report — 13 defects  
**Target:** Brief grade C- → A+  

---

## Sprint Goal

Transform the daily brief from a CRM task report into a world-class Chief-of-Staff intelligence product. Target composition: 40% intelligence, 30% synthesis, 20% execution, 10% telemetry.

---

## Defect Inventory and Disposition

| # | Defect | Severity | Root Cause | Fix |
|---|---|---|---|---|
| 001 | Confidence score inconsistent | Critical | Tier 3/4 stale sources tank score when all Tier 1 are fresh; LinkedIn ingest not tracked | Tier-weighted trust score; LinkedIn ingest → source health |
| 002 | Brief starts with actions not intelligence | Critical | GPT rendering order: decisions before headlines | Reorder instructions: intelligence first |
| 003 | "51 signals" is telemetry | High | Count surfaced, not compression | Signal count compression to N meaningful developments |
| 004 | Autonomous discovery has audit data | High | Section renders scan counts not discoveries | Discovery gate: must have actual net-new item or omit |
| 005 | Newsletter harvest broken | Critical | Email harvest shows CRM telemetry not headlines | Newsletter filter + headline extraction from email overlay |
| 006 | Opportunity duplication | Medium | Same opp surfaces across 4+ sections | De-duplicate by thread ID across sections |
| 007 | Relationship momentum factually wrong | Critical | SMS/phone not reconciled before momentum scoring | Cross-source last-touch reconciliation (email + calendar + SMS) |
| 008 | Signal lacks interpretation | High | Contact + count surfaced, not "so what" | Interpretation layer on relationship signals |
| 009 | Proposed RI events hidden | High | Count shown, items not shown | Surface full event detail with approve/reject |
| 010 | Connect-the-dots lacks evidence | High | Recommendation without source | Require evidence field on every CTD item |
| 011 | Weekend awareness missing | Medium | Day-of-week not consulted | Weekend mode: close loops, reading, prep for Monday |
| 012 | Intro engine low-value | Medium | No value threshold check | Intro value filter: mutual benefit, no existing connection |
| 013 | Missing source links | Critical | Items lack provenance | Every major item must carry source + headline + link |

---

## Root Causes — Analysis

**DEFECT-001:** All 6 Tier 1 sources are `refreshed` today. The 54% score comes from Tier 2 (`linkedin_messaging`, `social_engagement`, `social_own_posts`) and Tier 3 (`market_signals`, `relationship_signals`) being stale. LinkedIn export ingest ran 2026-05-29 but `linkedin_delta` is not registered in `source_health.json` — so it is invisible to the confidence calculation.

**DEFECT-007:** `apply_sms_last_touch_updates()` exists and runs in the pipeline. However SMS matching yields 0 contacts because the baseline has only 30 phone numbers. Contacts export has not been ingested. Additionally, email participants and calendar attendees are not reconciled against `last_touch` at all.

**DEFECT-002/003/004/005:** GPT rendering order puts `decision_layer` (actions) before `email_intelligence_harvest` (newsletters). Newsletters themselves pull from email overlay which contains CRM event telemetry mixed with actual newsletter content — no newsletter filter exists.

---

## Implementation — Priority Order

### P1 — Trust (DEFECT-001, 007)
1. Tier-weighted trust score: Tier 1 fresh = score cannot fall below 70 when all 6 are fresh
2. LinkedIn ingest updates `linkedin_delta` in source_health on completion
3. Cross-source last-touch reconciliation: email participants + calendar attendees → `last_touch`

### P2 — Brief Order (DEFECT-002, 003, 004, 005, 006)
4. GPT instruction reorder: intelligence → synthesis → execution
5. Signal compression function: N signals → top K developments
6. Discovery gate: "What RB Found" only surfaces net-new items
7. Newsletter filter: identify newsletters in email overlay by sender domain/pattern
8. Opportunity deduplication across sections

### P3 — Intelligence Quality (DEFECT-008, 009, 010, 011, 012, 013)
9. Weekend mode: day-of-week awareness in recommendation generation
10. Relationship signal interpretation: "11 messages → what does it mean?"
11. Proposed RI events: full visibility not just count
12. CTD evidence requirement
13. Intro value filter
14. Source link requirement on major items

---

## Files To Change

- `system/scripts/daily_brief.py` — trust score, discovery gate, signal compression, weekend mode, deduplication
- `system/scripts/refresh_sources.py` — cross-source last-touch reconciliation (email + calendar)  
- `system/scripts/cos_judgment.py` — weekend-aware recommendations
- `system/api/custom_gpt_instructions_compact_8k.md` — rendering order
- `system/api/custom_gpt_prompt.md` — rendering spec update
