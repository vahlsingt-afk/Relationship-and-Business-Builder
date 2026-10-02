# RB-DEFECT-003 — Passive Communication Failure Failed To Mutate RI

**Date opened:** 2026-05-27
**Resolved:** 2026-06-03
**Severity:** High
**Category:** RI Persistence / CoS Behavioral Logic / Opportunity Tracking
**Status:** Resolved — RB 9.51 (2026-06-03)

## Resolution Summary

Four root causes identified and fixed:

| Root Cause | File | Fix |
|---|---|---|
| `refresh_messages()` called without `--include-snippets` → `available_metadata_only` state | `refresh_sources.py:611` | Added `--include-snippets` and extended window to 90 days |
| `_match_events_to_contacts()` read `contact.get("phones")` (plural) — baseline uses `"phone"` (singular) → 0 matches | `direct_comms_health.py:131` | Added `phone` (singular) field lookup |
| `_match_calls_to_contacts()` same `phones` vs `phone` bug | `direct_comms_health.py:190` | Same fix |
| Timestamp field name mismatch: `fetch_apple_messages.py` writes `"at"`, matcher read `"date"/"timestamp"` → `event_at: None` | `direct_comms_health.py:165,225` | Added `event.get("at")` as primary field |
| `calls.json` uses `"duration_seconds"`, matcher read `"duration"` → missed call detection broken | `direct_comms_health.py:213` | Added `duration_seconds` field lookup |
| `linkedin_ingest_latest.json` missing `_generated_at` → `cos_judgment` threw `TypeError` on `None:.1f` format → `refresh_failed` label | `cos_judgment.py:147` | Fixed None-safe note formatting |
| `linkedin_ingest.py` never wrote `_generated_at` to cache | `linkedin_ingest.py:957` | Added timestamp stamp on cache write |
| `_compute_relationship_momentum()` read only `baseline last_touch`, ignored SMS data | `daily_brief.py` | Added `_sms_latest` index; SMS events override stale baseline for effective momentum |

**Verified results (2026-06-03):**
- Messages: `available_fresh`, 9,230 events, 207 matched, timestamps resolved
- Calls: `available_fresh`, 110 events, matched with proper duration/missed detection
- Jeff Wayman: reclassified FROZEN → HOT via SMS touch 2026-06-02
- `proposed_last_touch_update` carried in extras for CoS confirmation
- All IDQ items downgraded from `BLOCKED_PENDING_DATA` → `DEGRADED_PARTIAL_DATA`

## Scenario

Ryan Hildebrand (Global Payments) escalated to SMS after multiple follow-up
emails bounced during a domain/DNS email outage.

## Expected Behavior

RB should treat this as a relationship-intelligence mutation event:

- identify the known contact when a direct interaction source can match him
- correlate the signal with the active Genius / Global Payments opportunity
- classify the SMS as high-value multi-channel engagement
- flag bounced email / DNS / routing issues as communication risk
- propose relationship/opportunity state mutation and a follow-up loop
- surface the event in the next Daily Brief under relationship/operational risk

## Root Cause

The RI detectors handled direct SMS as generic last-touch evidence and manual
intake handled bounced-email language as conversational context, not as a
relationship-impacting operational event.

## Fix

- Added manual RI classifiers for `communication_failure_risk` and
  `multi_channel_escalation`.
- Added manual opportunity-state handling for active opportunities with
  elevated engagement confidence plus communication-risk flags.
- Added passive message/call heuristics to classify bounced-email and
  channel-escalation language when message snippets are available.
- Linked direct-interaction signals to active threads by contact id or company
  overlap so active opportunity context raises relevance and Daily Brief
  priority.

## Regression Expectation

An inbound SMS or pasted artifact that says a contact tried email multiple
times, emails bounced, or they switched to text/SMS/iMessage should produce a
proposed RI event with recommended action to repair email continuity and
respond through the successful channel.
