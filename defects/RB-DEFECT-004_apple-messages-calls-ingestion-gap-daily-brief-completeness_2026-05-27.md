# RB-DEFECT-004 — Apple Messages / Calls Ingestion Gap Breaks Daily Brief Completeness

**Date:** 2026-05-27  
**Severity:** High  
**Category:** Source Coverage / RI Persistence / Daily Brief Trust / Privacy-Sensitive Local Ingestion  
**Status:** Closed — RB 9.18 (2026-05-28)

## RB 9.18 Closure Summary

All acceptance criteria verified:

- `fetch_apple_messages.py` and `fetch_apple_calls.py` are invoked via `morning_pipeline.py` → `refresh_all.py` → `refresh_sources.py --all --save-health`
- `refresh_sources.py` calls `refresh_messages(py)` and `refresh_calls(py)` which run the fetchers with `--days 30`; `relationship_signals.py --cache` runs as a subsequent step in `refresh_all.py` (equivalent to `--refresh-signals`)
- `daily_brief.py` surfaces all five readiness states (`unavailable`, `available_stale`, `available_metadata_only`, `available_with_snippets`, `available_fresh`) via `_direct_comms_section` and `direct_comms_health.py`
- Completeness caveat fires when Messages or Calls are stale/unavailable/metadata-only — Daily Brief cannot claim "no signals" in those states
- `system/tests/test_direct_comms_ingestion.py` — 26 tests, all passing:
  - Five readiness states (messages + calls)
  - Completeness caveat: unavailable, stale, cannot-confirm language, no-caveat-when-fresh
  - Contact matching: known match, unmatched handle, missed call urgency, answered call no-urgency
  - Brief items integration
  - Channel escalation and communication failure signal classification (added RB 9.18)
  - Ryan Hildebrand / Global Payments generalized scenario

## Scenario

RB is expected to behave as an operational Chief of Staff across relationship
channels. SMS/iMessage and phone calls are often where high-value relationship
signals appear first, especially when email fails, urgency rises, or a contact
chooses a more personal channel.

The current architecture has partial support for `system/inbox/messages.json`
and `system/inbox/calls.json`, but ingestion is not yet reliable enough to be
treated as complete Daily Brief coverage.

## Observed Defect

- Apple SMS/iMessage ingestion is missing, stale, incomplete, or unable to
  capture usable snippets in some runs.
- Phone log ingestion is missing or treated as platform-dependent rather than
  first-class source coverage.
- Daily Brief output can appear complete while direct-communication channels
  are stale, missing, or under-instrumented.
- Passive RI mutation can miss relationship signals that only appear in SMS,
  iMessage, or phone logs.
- Operational risks, opportunity movement, missed calls, channel escalation,
  and urgent follow-up signals may not surface automatically.

## Impact

When Apple Messages or phone logs are unavailable, RB can miss:

- inbound engagement from strategic contacts
- multi-channel escalation after email failure
- missed calls requiring same-day response
- relationship warmth or advocacy that happened outside email
- active opportunity continuity signals
- loop closure evidence from texts or calls
- Daily Brief urgency changes

This weakens the Daily Brief’s trust claim. “No new relationship signals” is
not reliable unless Apple Messages and Calls are fresh or explicitly caveated.

## Architectural Concern

Direct communications are not optional side feeds. They are core relationship
truth surfaces. RB should model them as first-class, privacy-sensitive local
sources with:

- source freshness state
- explicit permissions/readiness checks
- snippet availability status
- matched/unmatched entity counts
- passive RI candidate counts
- failure reasons surfaced in the Daily Brief
- deterministic recovery instructions

## Privacy And Security Requirements

Apple Messages and phone logs are highly sensitive local data. Any sprint work
must preserve these constraints:

- local-only ingestion by default
- no broad raw transcript persistence beyond explicit, minimized snippets
- no sending or replying on Todd’s behalf
- redact or hash raw message content where full text is not necessary
- store only source evidence needed for RI, loop, and Daily Brief grounding
- surface permission failures without trying to bypass macOS protections
- require explicit Full Disk Access setup rather than silent workarounds

## Recommended Sprint Actions

1. **Apple Messages Readiness Check**

   Add a deterministic preflight that verifies access to the local Messages
   database, reports Full Disk Access state, and distinguishes:

   - unavailable
   - available but stale
   - available with metadata only
   - available with snippets
   - available and fresh

2. **Phone Log Readiness Check**

   Add the same preflight for CallHistory, including inbound, outbound, and
   missed-call coverage.

3. **Refresh Pipeline Hardening**

   Ensure `refresh_sources.py --messages --calls --save-health --refresh-signals`
   writes fresh source-health state and never lets stale direct-communication
   sources appear silently complete.

4. **Passive RI Candidate Extraction**

   Direct interactions should feed passive RI with:

   - matched contact id
   - channel
   - direction
   - event date
   - snippet availability flag
   - active-thread ids
   - detected urgency or channel escalation
   - proposed mutation or block reason

5. **Daily Brief Trust Integration**

   Daily Brief should include direct-communication source state in:

   - Daily Prep Summary
   - Relationship / Operational Signal Review
   - Operational Risks
   - Immediate Actions
   - Opportunity Protection

6. **Regression Fixtures**

   Add fixtures for:

   - matched inbound SMS from active-opportunity contact
   - missed call from an RC
   - iMessage channel escalation after bounced email
   - unavailable Messages database
   - available metadata with `snippet=null`
   - stale Calls feed

## Success Criteria

RB should be able to say, with evidence:

- Apple Messages were checked at a specific timestamp.
- Calls were checked at a specific timestamp.
- Direct-communication sources are fresh, stale, unavailable, or metadata-only.
- High-value SMS/iMessage/call events became passive RI proposals.
- Active opportunities are protected when direct communication reveals urgency
  or communication failure.
- Daily Brief does not imply completeness when direct-communication sources are
  stale, missing, or snippet-limited.

## Desired Future CoS Behavior

RB should treat SMS/iMessage and phone activity as relationship-bearing
operational signals, not incidental metadata. A future Daily Brief should
proactively identify the difference between:

- “nothing happened”
- “nothing high-value happened”
- “the source was unavailable”
- “the source was available but content was not inspectable”
- “a high-value relationship signal occurred and needs action today”
