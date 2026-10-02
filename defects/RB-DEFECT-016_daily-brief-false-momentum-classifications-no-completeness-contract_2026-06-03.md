# RB-DEFECT-016 — Daily Brief Emits False Momentum Classifications; No Completeness Contract; No Decision Layer

**Date opened:** 2026-06-03
**Resolved:** 2026-06-03
**Severity:** Critical
**Priority:** Highest
**Category:** Relationship Intelligence / Daily Brief Architecture / Source Reconciliation / Decision Support / CoS Reasoning / Information Completeness
**Status:** Resolved — RB 9.50 (2026-06-03)

## Resolution Summary

**New module:** `system/scripts/completeness_contract.py`

**Modified:** `system/scripts/daily_brief.py`

| Sub-defect | Fix | Status |
|---|---|---|
| 016-F: No completeness contract | `SectionCompleteness` dataclass + `assess_momentum_coverage()` gate | ✅ |
| 016-E: Information debt not operationalized | `build_information_debt_queue()` → `information_debt_queue` section | ✅ |
| 016-A: False momentum from baseline only | `_compute_relationship_momentum()` checks source coverage before classifying | ✅ |
| 016-B: Family contacts in professional queue | `should_exclude_from_professional_momentum()` + surname/tag heuristic | ✅ |
| 016-D: No decision synthesis layer | `build_decision_layer()` → `decision_layer` section with opp-cost | ✅ |
| 016-C: Loop debt dominates top-5 | `enforce_top5_category_balance()` — loop cap=2, strategic guarantee ≥1 | ✅ |

**Verified behavior (2026-06-03 pipeline run):**
- `relationship_momentum_status` emits INCOMPLETE blocking item when SMS/LinkedIn unreconciled
- All 20 RC contacts marked `[UNCONFIRMED — SMS/LinkedIn unreconciled]` instead of false COLD/FROZEN
- `information_debt_queue` shows 3 BLOCKED_PENDING_DATA items (email, LinkedIn, SMS) + 2 DEGRADED items
- `decision_layer` shows Top 3 decisions with opportunity cost
- `five_things_today` loop items: 2 of 5 (cap enforced); slots 3–5 are non-loop high-signal items

---

## Executive Summary

The Daily Brief satisfies many RB truth-contract obligations (no hallucinated state, proposed vs. persisted mutations, stale-source exposure, action-state integrity). However, it operates as an operational dashboard, not a world-class Chief of Staff.

The largest architectural failure: **the brief emits classifications and recommendations even when it lacks sufficient evidence to answer the underlying question.**

A world-class CoS must:
1. Detect that a question cannot be answered with current data.
2. Attempt automatic retrieval or reconciliation.
3. If retrieval is impossible, mark the section INCOMPLETE.
4. Generate a remediation action to obtain missing data.

None of these occur consistently today.

---

## DEFECT-016-A: Momentum Scoring Ignores Multi-Source Coverage

**Severity:** HIGH

### Code Location

`system/scripts/daily_brief.py:3095` — `_compute_relationship_momentum()`

`daily_brief.py:3144` — `tier = _momentum_tier(days_quiet)` executes unconditionally from `baseline` `last_touch` only.

### Observed Behavior

The brief classified Jeff Coffland → FROZEN, Jeff Staley → FROZEN, Jenny Kurdle → COLD, Amy Spytko → COLD based solely on `baseline_index.json` `last_touch` dates.

Meanwhile, the same brief disclosed: Apple Messages metadata-only, LinkedIn coverage incomplete.

SMS interaction with Jeff Coffland, Jenny Kurdle, and Amy Spytko this week was present but unreconciled. LinkedIn interaction with Jeff Staley was unreconciled.

The brief reported source incompleteness **and** emitted momentum classifications derived from incomplete sources. This violates RB's own source-trust doctrine.

### Root Cause

`_compute_relationship_momentum()` reads only `baseline_index.json` `last_touch`. It does not:
- Check whether SMS/LinkedIn/email reconciliation is current.
- Detect conflicting evidence from other communication channels.
- Gate classification on source coverage thresholds.

Momentum scoring runs before communication reconciliation. False COLD/FROZEN states result.

### Required Fix

Before any momentum classification:
1. Assess source coverage: SMS, LinkedIn, email, calls.
2. If coverage below threshold OR conflicting evidence detected → set result to `INCOMPLETE`.
3. Never emit HOT/WARM/COLD/FROZEN when coverage thresholds are unmet.
4. Generate a recovery action: "Run communication reconciliation."

### Correct Output Model

```
Relationship Momentum — Jeff Coffland

Required Sources: ✓ baseline  ✗ SMS reconciliation  ✓ email  ✗ LinkedIn reconciliation
Result: INCOMPLETE
Reason: SMS and LinkedIn data unavailable or unreconciled.
Recovery Action: Run communication graph reconciliation.
Confidence: Low
Classification: WITHHELD — insufficient evidence
```

---

## DEFECT-016-B: No Relationship Domain Classification

**Severity:** MEDIUM

### Code Location

`system/baseline_index.json` — no `relationship_domain` field exists on any entry.

`system/scripts/daily_brief.py:3111` — momentum loop filters only on `signal_class == "RC"` and `rc_state in (None, "ACTIVE", "")`.

### Observed Behavior

Scott Vahlsing (family) surfaced in relationship intelligence alongside Jeff Wayman, Ish Singh, Patrick Nelson (professional contacts).

### Root Cause

The baseline schema has no `relationship_domain` field. The momentum engine has no domain-exclusion layer. Family, personal, professional, and vendor contacts are treated identically.

### Required Fix

1. Add `relationship_domain` field to baseline schema: `family | personal | professional | opportunity | industry | vendor | customer`.
2. Exclude `family` and `personal` from professional momentum scoring unless attached to an active professional thread.
3. Backfill known family members (Vahlsing surname connections, explicit tagging).

### Correct Output Model

```
Scott Vahlsing
Classification: Family
Professional Momentum Impact: None
Disposition: Excluded from professional momentum scoring.
```

---

## DEFECT-016-C: Loop Debt Dominates Strategic Prioritization

**Severity:** HIGH

### Code Location

`system/scripts/daily_brief.py:4166` — `_pick_top_priorities()` applies scoring but loops accumulate volume that overwhelms strategic items.

### Observed Behavior

Top 5 priorities were dominated by loop items. Foods Connected, Global Payments / Genius, Patrick Nelson, Hospitality Table obligations did not rank above administrative loop debt.

### Root Cause

No strategic priority weighting layer exists. Loop volume is unbounded relative to opportunity, decision, and risk items. The top-5 picker does not enforce category representation.

### Required Fix

Introduce priority category weights:

```
Strategic Opportunity  > 1.0
Active Decision        > 0.9
Relationship Risk      > 0.8
Loop                   > 0.5 (uncapped count = 1 per loop in top-5)
Administrative         > 0.3
```

Top 5 must always include at least one Strategic Opportunity or Active Decision if present in the brief. Loop items capped at 2 of 5 slots.

---

## DEFECT-016-D: No Decision Synthesis Layer

**Severity:** HIGH

### Code Location

No `_build_decision_layer()` function exists anywhere in the brief pipeline.

### Observed Behavior

The brief reports information. It does not answer: "If Todd only accomplishes three things today, what should they be?"

### Root Cause

The CoS judgment layer (`cos_judgment.py`) generates `execution_options` but does not synthesize them into a ranked decision list with justification and opportunity cost.

### Required Fix

Add mandatory `decision_layer` section to the brief:

```
Top Decisions Today

1. Global Payments / Genius thread — act today or lose window
   Opportunity cost of deferral: relationship momentum risk, competitive timing.

2. Foods Connected — next step required before opportunity cools
   Opportunity cost of deferral: discovery window closes.

3. Patrick Nelson follow-up — loop debt + relationship momentum gap
   Opportunity cost of deferral: COLD classification by next brief.
```

This section must appear before operational recommendations. It must reference open loops, opportunities, and relationship momentum in combination.

---

## DEFECT-016-E: Information Debt Is Not Operationalized

**Severity:** CRITICAL

### Code Location

`system/scripts/daily_brief.py` — stale/incomplete source disclosures are emitted as text caveats only. No `information_debt` section or object type exists.

### Observed Behavior

"Apple Messages incomplete." — reported, no action generated, no blocked classification produced, no debt item created.

### Root Cause

Source incompleteness is disclosed narratively but not modeled as a first-class blocking object. No `information_debt` queue exists. Downstream classifications are not linked to the missing sources that invalidate them.

### Required Fix

Introduce `InformationDebt` as a first-class section:

```
Information Debt Queue

1. Jeff Coffland momentum classification
   Cause: SMS reconciliation missing
   Blocked: COLD/FROZEN classification withheld
   Action: Refresh communication graph
   Status: BLOCKED_PENDING_DATA

2. Jenny Kurdle momentum classification
   Cause: SMS reconciliation missing
   Blocked: COLD/FROZEN classification withheld
   Action: Refresh communication graph
   Status: BLOCKED_PENDING_DATA
```

Relationship debt and loop debt are downstream consequences of unresolved information debt. The brief must surface information debt before relationship and loop debt.

---

## DEFECT-016-F: No Completeness Contract

**Severity:** CRITICAL

### Architectural Root Cause

The system asks: "What can I prove?"

A world-class CoS must ask: "What must be known to answer this question?"

No section in the brief has a formal completeness contract. No section returns `COMPLETE | PARTIAL | INCOMPLETE`. No section links required sources to conclusions. No gate prevents classification when sources are insufficient.

### Required New Architecture Rule — Completeness Before Interpretation

For every major conclusion:

**Step 1:** Can RB answer this question with current data?
- If NO → proceed to Step 2.

**Step 2:** Can RB automatically obtain the missing information?
- If YES → attempt retrieval, retry classification.
- If NO → mark section `INCOMPLETE`, generate remediation action.

Only after passing these gates may interpretation occur.

### Required Section Contract Object

Every major section must carry:

```python
{
    "section": "relationship_momentum_status",
    "completeness": "INCOMPLETE",   # COMPLETE | PARTIAL | INCOMPLETE
    "required_sources": ["baseline", "sms_reconciliation", "email", "linkedin"],
    "missing_sources": ["sms_reconciliation", "linkedin"],
    "recovery_action": "Run communication graph reconciliation.",
    "confidence": "low",
    "classifications_withheld": ["Jeff Coffland", "Jenny Kurdle", "Amy Spytko", "Jeff Staley"]
}
```

No section may emit a classification when `completeness == INCOMPLETE`.

---

## CoS Maturity Assessment

| Dimension | Current Score |
|---|---|
| Truth discipline | 9/10 |
| Mutation discipline | 9/10 |
| Source transparency (disclosure) | 8/10 |
| Relationship reconciliation | 4/10 |
| Decision support | 4/10 |
| Opportunity prioritization | 5/10 |
| Information completeness | 2/10 |

**Overall:** RB behaves as a strong operating dashboard. It does not yet behave as a world-class Chief of Staff because it lacks a completeness contract and information-debt management system.

---

## Recommended Fix Sequence

| # | Fix | Effort | Leverage |
|---|---|---|---|
| 1 | DEFECT-016-F: Completeness contract on momentum section | Medium | Critical |
| 2 | DEFECT-016-E: InformationDebt first-class section | Medium | Critical |
| 3 | DEFECT-016-A: Gate momentum classification on source coverage | Medium | High |
| 4 | DEFECT-016-D: Decision synthesis layer | Medium | High |
| 5 | DEFECT-016-C: Priority category weights + top-5 capping | Low | High |
| 6 | DEFECT-016-B: Relationship domain classification (schema + filter) | High | Medium |

DEFECT-016-F is the highest-leverage change because it changes RB from a reporting system into a decision-grade operating system. All other fixes are applications of the completeness contract to specific sections.
