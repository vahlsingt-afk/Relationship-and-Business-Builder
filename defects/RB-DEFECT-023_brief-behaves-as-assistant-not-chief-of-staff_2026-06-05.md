# RB-DEFECT-023: Daily Brief Behaves Like an Assistant, Not a Chief of Staff

**Date:** 2026-06-05  
**Severity:** Critical  
**Classification:** Daily Briefing / Chief of Staff Intelligence Model  
**Status:** In Remediation  

---

## Summary

The Daily Brief operates as an intelligent assistant that summarizes information and
offers generic suggestions. A world-class Chief of Staff should instead generate
specific recommendations, conduct proactive research, identify intelligence paths,
and present named actions with expected outcomes — before the principal asks.

---

## Root Causes

### 1. No CoS Action Engine

The brief has no engine that takes opportunity and relationship data and produces
specific, named, actionable recommendations. Generic suggestions ("consider reaching
out to someone") substitute for CoS work.

**Fix:** `cos_action_engine.py` — generates specific who/why/what recommendations
from active threads, baseline contacts, and network intelligence.

### 2. Thesis Section Renders Without New Evidence

"The operator reality thesis remains intact" appears even when no new market signal
was processed today. A thesis status with no new evidence is not intelligence — it
is reassurance.

**Fix:** Gate thesis rendering on new market items being present and fresh.

### 3. LinkedIn Own Posts Surface as Content

The brief references the user's own LinkedIn post content. The user already knows
what they posted. The intelligence is the reaction, not the post.

**Fix:** Only surface own-post items when engagement signal count > 0.

### 4. "What Changed" Renders Without a Delta

The What Changed section appears even when no material change occurred since the
prior brief. An empty change section damages trust.

**Fix:** Suppress What Changed entirely when no actual delta items exist.

### 5. Recommendation Text Is Generic

Items surface with `recommended_action` text that assigns analytical work to the
principal:
- "Consider building a relationship map"
- "Find people who know John Morrison"  
- "Prepare for next steps"

**Fix:** Every `recommended_action` must be specific: named person, stated reason,
expected outcome, suggested talking point. Generic assignments are blocked.

---

## Required Architecture

### Current model

```
Information → Summary → Generic Suggestion
```

### Target model

```
Information → Intelligence → Insight → Recommendation → Named Action
```

### CoS Standard

After reading a Daily Brief, the user should immediately know:
1. What matters
2. Why it matters
3. What changed
4. Who to contact (by name)
5. What action is recommended (specific)
6. What opportunity or risk exists

---

## Remediation Plan

### Priority 1 — This Session (RB 9.57)

| Item | File | Action |
|---|---|---|
| CoS Action Engine | `cos_action_engine.py` | New script |
| Wire into daily brief | `daily_brief.py` | Call action engine for opportunities |
| Thesis gating | `daily_brief.py` | Suppress when no new market evidence |
| Own-post filtering | `daily_brief.py` | Only surface on engagement signal |
| What Changed gating | `daily_brief.py` | Suppress section when delta = 0 |

### Priority 2 — Sprint RB 9.58

| Item | Action |
|---|---|
| Auto-research package | For each waiting opportunity, generate briefing doc |
| Relationship map builder | Auto-map paths to target companies/people |
| Interview prep generator | When opportunity enters interview stage, auto-generate prep |
| Generic action blocker | Reject `recommended_action` text that assigns work to principal |

---

## Example Transformations

### Before (Foods Connected)
"Waiting for next steps. Prepare for the next interview."

### After (Foods Connected)
**Opportunity: Foods Connected Commercial Director**
Status: Waiting on Duncan review (Day 10)
Probability: High — immediate scheduling, CV reviewed, alignment confirmed
Risk: Momentum decay if no contact in next 3 days

Recommended Action: Contact Sarah McAngus today.
Message: "Checking in on next steps from Duncan — happy to accommodate any timing."
Expected outcome: Stage clarity, scheduling momentum, signal on competition

---

### Before (Qu)
"Consider finding people who know John Morrison."

### After (Qu)
**Intelligence Path: Qu / John Morrison**
Recommended Contact: Jenny Kurdle (RC inner)
Reason: Jenny Kurdle introduced John Morrison to Todd via PAR. She is the direct
bridge and would have visibility into Qu priorities and John's current focus areas.
Suggested approach: "I'm looking at Qu more closely. You introduced me to John —
would you be comfortable sharing what you know about their current trajectory?"
Expected outcome: Current intelligence on Qu hiring plans and John Morrison's priorities

---

### Before (Harri)
"General monitoring recommendation."

### After (Harri)
**Strongest Intelligence Path: Harri**
Recommended Contact: John Adams (RC inner, OTP3)
Reason: John Adams has spoken with the Harri CRO. He was hired by Todd into PAR.
This is the highest-trust intelligence source available.
Suggested question: "You mentioned Harri. What was the nature of the conversation?
Did my name come up? What's their timeline looking like?"
Expected outcome: Internal interest level, timeline, whether Todd's name is active
