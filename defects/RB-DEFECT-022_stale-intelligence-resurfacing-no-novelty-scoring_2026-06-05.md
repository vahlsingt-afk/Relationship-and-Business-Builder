# RB-DEFECT-022: Stale Intelligence Resurfacing / No Novelty Scoring

**Date:** 2026-06-05  
**Severity:** High  
**Classification:** Daily Briefing Intelligence Quality / Chief of Staff Reasoning  
**Status:** In Remediation  

---

## Summary

The daily brief repeatedly surfaces "Joseph Yetter promoted to President of PAR
Restaurants" despite the fact that:
- The information was originally user-supplied
- No meaningful new developments have occurred
- No thesis validation or contradiction has occurred
- No recommended actions can be generated

This is a Chief of Staff failure. A CoS does not brief an executive on facts the
executive already knows unless something has changed.

---

## Root Causes

### 1. Lifecycle Suppression Is Advisory, Not Enforced

`intelligence_lifecycle.py` annotates items with lifecycle state (DORMANT,
ACKNOWLEDGED, SUPPRESSED) and records suppressed items in `suppressed_today`,
but the comment in `process_brief_items()` explicitly states:

> "Source sections are NOT mutated (items are not removed) — this preserves
> backward compatibility with existing rendering paths."

Items tagged DORMANT remain in their source sections and are rendered in the
brief. The lifecycle engine tells the truth about what should be suppressed but
the renderer ignores it.

**Fix:** Add `filter_suppressed_from_sections()` — called after
`process_brief_items()` — that physically removes DORMANT/ACKNOWLEDGED items from
their source sections.

### 2. MEMORY Attribution Has No Stricter Suppression Rules

User-supplied intelligence (grounding=`manual_user_provided`, attribution=MEMORY)
receives the same suppression rules as externally-observed intelligence. A
`leadership_change` item suppresses for 30 days, then resurfaces regardless of
whether the user was the original source.

**Expected behavior:** MEMORY items require external corroboration before they
are eligible for resurfacing. The burden is "what is new since the user told us
this?" If the answer is nothing, suppress indefinitely.

**Fix:** Add `MEMORY` suppression rules in `_DEFAULT_SUPPRESSION_RULES` with
`suppress_days=180` and `require_external_corroboration=True`. MEMORY items in
DORMANT state should never resurface via keyword match alone — they need a
SYNTHESIS or OBSERVED item from an external source.

### 3. No Novelty Score on Canonical Items

Every brief item is treated as equivalent regardless of whether it is:
- Newly discovered this cycle
- Previously reported
- User-supplied with no new delta
- Recycled from memory

**Expected behavior:** Every canonical item should carry an explicit
`novelty_score` (0–100). Items below threshold (default: 20) should be
suppressed from active briefing sections.

Novelty score factors:
- `first_observation` = 100
- `reactivated_with_external_evidence` = 80
- `previously_reported_with_new_signal` = 60
- `previously_reported_no_change` = 10
- `user_supplied_no_corroboration` = 5
- `dormant_resurfaced_no_trigger` = 0

**Fix:** Add `compute_novelty_score()` to `intelligence_lifecycle.py`. Apply
score to all canonical items. Add `novelty_threshold` filter in `daily_brief.py`.

### 4. Self-Contradictory Yetter Baseline Notes

Joe Yetter's baseline contains:

```
[2026-05-29] LinkedIn: role General Manager, Engagement Cloud -> President, PAR Restaurant.
[2026-05-29] LinkedIn: role President, PAR Restaurant -> General Manager, Engagement Cloud.
```

These two entries cancel each other out on the same day — the system captured
both the role change and its reversion. The `current_role` is correctly
"General Manager, Engagement Cloud" but the "President, PAR Restaurant" text in
the notes is triggering intelligence generation on each brief cycle.

**Fix:** Clean the self-contradictory notes; mark as resolved; add
`role-change-confirmed` tag to suppress future pending-mutation surfacing.

---

## Remediation Plan

### Priority 1 — This Session

| Item | File | Action |
|---|---|---|
| Hard section filtering | `intelligence_lifecycle.py` | Add `filter_suppressed_from_sections()` |
| MEMORY suppression rules | `intelligence_lifecycle.py` | Stricter rules, require corroboration |
| Novelty scoring | `intelligence_lifecycle.py` | Add `compute_novelty_score()` |
| Apply novelty filter | `daily_brief.py` | Filter items below novelty threshold |
| Yetter notes cleanup | baseline_index.json | Resolve contradictory entries |

---

## Success Criteria

1. Items in DORMANT or ACKNOWLEDGED lifecycle state do not appear in their
   source sections in the daily brief.
2. User-supplied (MEMORY) items with no external corroboration do not resurface
   after 30 days. They require external evidence to reactivate.
3. Every canonical brief item carries a `novelty_score`.
4. Items with novelty_score < 20 are filtered from active briefing sections.
5. Joseph Yetter's contradictory promotion entries no longer generate a
   "promoted to President" signal.
6. The brief's suppressed_today section correctly logs what was suppressed and why.
7. A world-class CoS question is answerable for every item that appears:
   "Why is Todd seeing this today that he did not know yesterday?"
