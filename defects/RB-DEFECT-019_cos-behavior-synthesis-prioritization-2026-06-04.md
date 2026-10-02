# RB-DEFECT-019 — CoS Behavior: Synthesis, Prioritization, Autonomous Operation

**Date opened:** 2026-06-04
**Severity:** Critical (4 sub-defects), High (6 sub-defects), Medium (3 sub-defects)
**Category:** Intelligence Synthesis / CoS Behavior / Attention Allocation
**Status:** Resolved — RB 9.56 (2026-06-04)

## Resolution

| Sub-defect | Fix | Result |
|---|---|---|
| 006: Empty calendar → no plan | `_compute_capacity_plan()` → `capacity_plan` section | 7h available today, allocates to job search / decisions / relationship / content / BD |
| 007: No W-2 intelligence | `_build_w2_intelligence()` → `w2_intelligence` section | Both job opportunities shown with stall probability + next review date |
| 013: No CoS allocation section | `_compute_cos_today()` → `cos_today` section | "If I Were Your Chief of Staff Today" — 5 numbered actions with timing rationale |
| 010: No ROI framing | Rendering rules for `decision_layer` | Framed as "Highest ROI Actions Today" with opportunity cost per item |
| 012: No timing rationale | Rendering rule for `recommended_actions` | "Why today" clause required on every recommendation |

**New sections in brief_display_order:**
- `cos_today` — renders immediately after status header (before five_things_today)
- `w2_intelligence` — active job opportunities with management logic
- `capacity_plan` — time allocation when calendar is empty/light

**Test suite: 2,255 passed, 0 failures.**

---

## Executive Summary

The Daily Brief functions as an advanced monitoring dashboard but not yet as a world-class Chief of Staff. Signal collection, loop tracking, and opportunity monitoring are working. Intelligence synthesis, contrarian reasoning, autonomous attention allocation, and career acceleration are not.

The system answers: "What changed? What tasks exist?"
It does not adequately answer: "Why does it matter? What should I do? What am I missing? What would a great CoS tell me I haven't realized?"

---

## Sub-Defect Triage

| # | Title | Verdict | Fix Type |
|---|---|---|---|
| 001 | Stale source recovery | PARTIALLY IMPLEMENTED | Rendering — show what recovery was attempted |
| 002 | Newsletter headlines not rendered | FIXED in RB 9.55 | Rebuild/reload |
| 003 | No contrarian view | SCAFFOLD ONLY | Code — per-opportunity stress test |
| 004 | No "What RB Found Without You" | EXISTS (46 items) | Rendering — render prominently |
| 005 | Opportunity ↔ market not connected | FIXED in RB 9.55 | CTD [EXT→OPP] items |
| 006 | Empty calendar creates no plan | GENUINE GAP | Code — capacity plan |
| 007 | No W-2 career acceleration | GENUINE GAP | Code — job intelligence section |
| 008 | Waiting state lacks management | PARTIAL | Code — stall probability, review date |
| 009 | No daily LinkedIn strategy | PARTIAL | Code — post_recommendations empty |
| 010 | No ROI-ranked actions | PARTIAL | Rendering — decision_layer framing |
| 011 | No "Ignore Today" section | EXISTS (sparse) | Rendering — expand suppression |
| 012 | No "Why Today" timing logic | PARTIAL | Rendering — add opportunity_cost to each rec |
| 013 | No "If I Were Your CoS Today" | GENUINE GAP | Code — cos_today section |

---

## Priority 1 Build: DEFECT-006 — Calendar Capacity Plan

When calendar has 0 events (or < 2 hours scheduled), compute deployable time and allocate it. The absence of meetings is intelligence — 6-8 hours is available for strategic work. Build `_compute_capacity_plan(report, sections)`.

## Priority 2 Build: DEFECT-007 — W-2 Intelligence Section

The user has active job opportunity threads (Foods Connected, Global Payments). Build a `w2_intelligence` section that surfaces: active applications + status, days waiting, next action, confidence.

## Priority 3 Build: DEFECT-013 — "If I Were Your CoS Today"

New `cos_today` section: exactly 5 numbered actions, each with what to do, why today, and time required. Synthesizes decision_layer + loops_due + capacity_plan + W-2 status. Rendered as the FIRST substantive section after the status line.

## Priority 4 Fix: DEFECT-010 — ROI framing + DEFECT-003 Contrarian View

Rendering instruction: frame `decision_layer` as "Highest ROI actions today" with explicit value ranking. Add per-opportunity contrarian item when evidence supports it.
