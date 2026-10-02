# RB-DEFECT-017 — Daily Brief Intelligence Completeness, Source Validation, and Self-Healing Data Acquisition

**Date opened:** 2026-06-04
**Severity:** Critical
**Priority:** Highest
**Category:** Core Chief of Staff Functionality / Source Validation / Recovery Loop / Delta Analysis
**Status:** Resolved — RB 9.54 (2026-06-04)

## Resolution Summary

| Issue | Build Type | What Was Built | Status |
|---|---|---|---|
| #3: No prominent confidence declaration | Code | `_compute_brief_status_header()` + `brief_status_header` section at position 0 | ✅ |
| #4: No recovery loop | Code | `_check_brief_readiness()` in `morning_pipeline.py` — validate → recover → report | ✅ |
| #7: No external→opportunity correlation | Code | CTD keyword-based active-thread company pass → `[EXT→OPP]` items | ✅ |
| #5: Closed loops not reflected | Code | `intelligence_lifecycle.py` — `awaiting_response` suppression bypass | ✅ |
| #1: Delta labeling | Instruction | DELTA RULE + suppression count in `custom_gpt_prompt.md` | ✅ |
| #2: No source citations | Instruction | CITATION RULE mandatory in `custom_gpt_prompt.md` + `custom_gpt_instructions_8k.md` | ✅ |
| #6: CTD recites beliefs | Instruction | QUALITY RULE — ≥2 external sources required, no belief substitution | ✅ |

**Verified behavior (2026-06-04 live run):**
- `brief_status_header`: `🟡 DAILY BRIEF STATUS: DEGRADED | Intelligence Confidence: 50% | Sources: CRITICAL`
- Stale sources listed: LinkedIn Messages, Market Signals
- Recovery actions provided per source
- Morning pipeline now runs readiness check before brief generation
- CTD `[EXT→OPP]` items fire when active thread companies appear in external signals
- Sent-followup items exempt from DORMANT suppression

---

## Filed By

User-submitted defect report, 2026-06-04.

---

## Executive Summary

The Daily Brief generates output even when critical intelligence sources are missing or degraded, does not prominently communicate confidence level, lacks a pre-brief recovery loop, and does not consistently perform delta analysis. It behaves more like an intelligent aggregation layer than a Chief of Staff that ensures intelligence completeness before response generation.

**Target:** Move from "Smart Summary Generator" → "Executive Chief of Staff" by enforcing source completeness, recovery behavior, delta analysis, source transparency, action-loop awareness, and external-to-internal correlation.

**Current CoS maturity:** ~5/10
**Target CoS maturity:** 9+/10

---

## Issue-by-Issue Honest Assessment

### Issue #1 — Repeated headlines without delta analysis

**Verdict: PARTIALLY IMPLEMENTED — rendering compliance gap.**

- `suppressed_today`: 44 items being suppressed via `intelligence_lifecycle` ✅
- `what_changed_since_yesterday` section exists ✅
- Re-surfaced items not labeled "NEW DEVELOPMENT since [date]" ❌
- Suppressed item counts not prominently communicated in rendered output ❌

**Fix type:** Rendering instruction (no new code needed for the core gap).

---

### Issue #2 — No source transparency / citations

**Verdict: DATA EXISTS — rendering compliance gap.**

- Every item carries `source_refs` ✅
- Rendering instructions mention source citation ✅
- GPT skips citations in conversational output ❌

**Fix type:** Rendering instruction enforcement — citations mandatory, not optional.

---

### Issue #3 — Missing sources treated as informational

**Verdict: PARTIALLY IMPLEMENTED — one genuine code gap.**

- Trust score computed: 50% ✅ (today's live brief)
- IDQ has BLOCKED_PENDING_DATA / DEGRADED_PARTIAL_DATA ✅
- Completeness contract gates momentum classification ✅
- No prominent "Daily Brief Confidence: 50%" header at top of brief ❌
- Trust score buried in `trust_metrics` section, not top-of-brief ❌

**Fix type:** Code — add `_build_brief_confidence_header()` that surfaces a prominent confidence declaration when trust score < threshold.

---

### Issue #4 — No intelligence recovery loop

**Verdict: GENUINE ARCHITECTURAL GAP.**

- `morning_pipeline.py` runs source refresh before brief generation ✅
- No validate → attempt recovery → check again → proceed gate ❌
- System proceeds with degraded brief without attempting source recovery ❌
- No readiness gate — brief generates even when critical sources failed ❌

**Fix type:** Code — `daily_brief_readiness_check()` with validate/recover/proceed logic.

---

### Issue #5 — Closed loops not reflected

**Verdict: PARTIALLY IMPLEMENTED — loop state bug.**

- `sent_followups_awaiting_response` section exists with 8 items ✅
- Sent outreach items being classified as `DORMANT` and suppressed ❌
- No `follow_up_sent` state between `open` and `closed` ❌
- When a loop is closed with "sent follow-up," brief should show "Awaiting Response" ❌

**Fix type:** Code — loop state enhancement + dormancy classifier fix.

---

### Issue #6 — Connect-the-Dots recites beliefs

**Verdict: RENDERING COMPLIANCE GAP.**

- CTD computation produces cross-time convergence signals ✅
- GPT falls back to thesis recitation when external signals are sparse ❌
- No quality threshold rule: "< 2 external sources → say so, don't substitute beliefs" ❌

**Fix type:** Rendering instruction + quality threshold rule.

---

### Issue #7 — Missing external-to-internal correlation

**Verdict: PARTIAL IMPLEMENTATION — one genuine code gap.**

- `entity_spotlight` does cross-section correlation ✅
- CTD does entity convergence ✅
- No explicit external_signal → active_opportunity mapping ❌
- McDonald's traceability signal does not auto-link to Foods Connected opportunity ❌

**Fix type:** Code — extend CTD computation to cross-reference active thread companies.

---

## Build Plan

| Priority | Issue | Type | File | Status |
|---|---|---|---|---|
| 1 | #3: Prominent confidence header | Code | `daily_brief.py` → `_build_brief_confidence_header()` | In Progress |
| 2 | #4: Pre-brief recovery loop | Code | `morning_pipeline.py` + `daily_brief.py` | In Progress |
| 3 | #7: External→opportunity correlation | Code | `daily_brief.py` → CTD extension | In Progress |
| 4 | #5: Loop state machine | Code | `mutations.py` + `rb_core.py` | In Progress |
| 5 | #1: Delta labeling | Instruction | `custom_gpt_prompt.md` | In Progress |
| 6 | #2: Mandatory citations | Instruction | `custom_gpt_prompt.md` | In Progress |
| 7 | #6: CTD quality threshold | Instruction | `custom_gpt_prompt.md` | In Progress |

---

## Key Evidence from Current State

```
Trust score:           50%  (live --json run; 5 AM cached brief = HIGH)
Suppressed today:      44 items (intelligence_lifecycle working)
Sent followups:         8 items (sent_followups_awaiting_response working)
Blocked IDQ items:      0 (email/SMS fresh after sprint)
Degraded IDQ items:     4 (LinkedIn stale + industry_brief stale)
```

**Why trust score is 50% in live runs but HIGH in cached brief:**
The cached brief is built from 4 AM/5 AM data. By the time a live `--json` run executes later in the day, email overlays are > 6 hours old and re-classified as stale. The source_trust_table reads current overlay file timestamps, not the timestamps from when the brief was generated. This is expected behavior — it correctly reflects that the brief was built from fresh data but that data is now aging.

---

## Proposed Brief Structure (from defect report)

```
Section 1: Intelligence Readiness Report
  — Source: PASS/FAIL per source
  — Overall Confidence: XX%

Section 2: Executive Summary
  — Deltas only. No repeats.

Section 3: Strategic Headlines
  — Source + Date + Confidence + Link (mandatory)

Section 4: Relationship Intelligence
  — New activity only. Closed loops reflected.

Section 5: Opportunity Intelligence
  — What changed. Risks. Opportunities. Actions.

Section 6: Connect-the-Dots
  — ≥2 external sources. No belief recitation.

Section 7: Recommended Actions
  — Ranked High / Medium / Low

Section 8: Open Intelligence Gaps
  — What RB cannot answer + collection actions
```

This structure maps closely to existing sections. The gaps are: Section 1 prominence, Section 3 citation enforcement, Section 6 quality threshold, Section 8 formalization.
