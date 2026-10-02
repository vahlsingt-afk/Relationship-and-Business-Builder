# RB-DEFECT-018 — Opportunity Graph Underpowered; Intelligence Not Synthesized

**Date opened:** 2026-06-04
**Severity:** High
**Priority:** High
**Category:** Opportunity Intelligence / Industry Intelligence / Email Intelligence Synthesis
**Status:** Resolved — RB 9.55 (2026-06-04)

## Resolution

| Issue | Fix | Result |
|---|---|---|
| Newsletter headlines buried in extras | Expanded to 8 individual `_canonical_item` instances with strategic interpretation | Headlines now rendered individually with source attribution |
| Headlines have generic `why_it_matters` | Added active thread cross-referencing + contact matching in headline items | Headlines linking to active opportunity threads now show `act_today` |
| Opportunity evidence age reads only thread text | `_opp_linked_activity()` cross-references `active_thread_company_hits`, `sent_followups`, `from_baseline`, calendar, SMS | All linked email evidence aggregated |
| Date parsing fails for RFC 2822 email dates | `_parse_email_date()` helper handles ISO + RFC 2822 + short date formats | "Mon, 1 Jun 2026" now parsed correctly |
| Automated brief emails inflate evidence age | Daily Brief emails excluded from sent_followup matching | Clean evidence timeline |
| Probability stuck at LOW despite recent activity | Re-score: if linked_age ≤ 3 days and probability == LOW, promote to MEDIUM | Global Payments and Foods Connected re-scored |
| `waiting_on` not computed | Detect "Sent:" prefix vs inbound to determine waiting state | Both active opportunities show "Awaiting response to outbound email" |

**Verified results (2026-06-04):**
- Global Payments: evidence_age 20d → **3d** | probability LOW → **MEDIUM** | waiting_on: **Awaiting response**
- Foods Connected: evidence_age 9d → **1d** | probability LOW → **MEDIUM** | waiting_on: **Awaiting response**
- Patrick Nelson: 19d (no linked activity — correctly shows gap)
- Newsletter headlines: 8 items, individually rendered with strategic interpretation + source attribution

---

## Executive Summary

The Daily Brief is an operationally useful console but not yet an indispensable intelligence product.
The test: "Am I rushing to the door to get the newspaper?" Current answer: No.

The primary gap is not content generation quality — it is intelligence synthesis and cross-entity evidence aggregation.

---

## DEFECT 1 & 2 — Industry Headlines Collected But Not Synthesized or Surfaced

**Root cause (confirmed):** Headlines are collected (7 newsletters, 11 headlines: Restaurant Dive, NRN, QSR Magazine) but buried in `extras.top_headlines` of a single aggregate `EMAIL INTELLIGENCE HARVEST` item. The GPT renders the aggregate header, not individual headlines. Each headline's `why_it_matters` is generic placeholder text. No strategic interpretation connects the headline to active opportunities, active contacts, or the operational-realism thesis.

**Required pipeline:**
```
Newsletter → Headline → Signal Classification → Strategic Interpretation → Opportunity Impact → Brief Output
```
The headline is never the intelligence. The implication is the intelligence.

**Fix type:** Code — expand headlines from extras into individual `_canonical_item` instances with computed strategic interpretation based on entities, active threads, and operational context.

---

## DEFECTS 3, 4, 5 — Opportunity Evidence Age Ignores Actual Activity

**Root cause (confirmed):** `_opp_evidence_age()` regex-scans thread `current_state` + `context` text for ISO dates. Global Payments finds `2026-05-15` (a stale loop date) → 20 days. Foods Connected has no dates in text → falls back to `opened = 2026-05-26` → 9 days.

**Invisible activity:**
- Ryan Hildebrand email sent June 1 (3 days ago) → not linked to Global Payments opportunity
- Foods Connected interview with Duncan this week → not linked to opportunity
- Sarah McAngus communications ongoing → not linked

**Current model:** Opportunity → Thread → text fields only
**Required model:** Opportunity → Company → email_overlay matches + calendar_overlay matches + SMS matches → Aggregated activity timeline

**Required derived fields per opportunity:**
```
latest_activity_date   — from all linked entity sources
latest_activity_type   — email | calendar | sms | loop
waiting_on             — who needs to respond
response_age_days      — days since last outbound
momentum               — improving | stable | cooling | stale
next_action            — what to do based on state
confidence             — based on evidence recency + quality
```

**Fix type:** Code — `_opp_linked_activity(thread, report)` that cross-references email_overlay companies/contacts, calendar events with thread participants, and SMS matches.

---

## DEFECT 6 — Relationship/Loop Intelligence Is Strong (Positive)

The Patrick Nelson section surfaced a real execution obligation correctly. The loop system is one of the strongest parts of RB. Retain current architecture.

---

## Resolution Plan

| Item | Fix | Priority |
|---|---|---|
| Newsletter headline synthesis | Expand `_email_intelligence_harvest_items()` to emit one item per headline with strategic interpretation | High |
| Opportunity evidence cross-referencing | `_opp_linked_activity(thread, report)` → update `_compute_opportunity_board()` | High |
| Opportunity probability recalculation | Re-score after evidence cross-reference — `LOW` probability when Ryan H email is 3 days old is wrong | High |
| Headline→opportunity correlation | Already added via CTD `[EXT→OPP]` items (DEFECT-017) | Done |
