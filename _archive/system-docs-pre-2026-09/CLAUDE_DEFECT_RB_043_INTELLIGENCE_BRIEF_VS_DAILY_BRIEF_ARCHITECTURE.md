# RB-DEFECT-043: Daily Brief and Intelligence Brief Architecture Does Not Meet Executive Chief of Staff Standard

**Date filed:** 2026-06-14
**Filed by:** Todd
**Status:** Filed — not yet triaged/scoped. Recorded verbatim for processing in a
future sprint (proposed: after RB 9.72 lands).
**Priority:** Critical
**Category:** Intelligence Engine / Executive Briefing System
**Trigger:** Review of the Daily Brief and Intelligence Brief outputs following RB
9.71 (brief quality & market freshness cleanup, commit `9a53e22`). The issue is
architectural — how intelligence is collected, surfaced, proven, and synthesized —
not a single bug, so it is being recorded as a defect report rather than folded into
an existing sprint.

---

## Executive Summary

The current Daily Brief and Intelligence Brief outputs are improving but still do not
meet the intended vision of Relationship Bridge as an Executive Chief of Staff
platform.

The Daily Brief is beginning to move toward the desired outcome but still behaves too
much like an assistant summarizing information.

The Intelligence Brief is significantly off target. Rather than acting as a verifiable
intelligence feed and proof layer, it behaves like an analytical newsletter that
provides commentary, opinions, and synthesized observations without demonstrating what
intelligence gathering actually occurred.

The result is a loss of trust because the user cannot determine:

- What sources were monitored
- What changed since yesterday
- What intelligence was discovered
- Why a particular conclusion was reached
- Whether fresh collection occurred at all

The architecture should be redesigned so that the Intelligence Brief becomes the
fact-based intelligence collection layer and the Daily Brief becomes the Chief of
Staff synthesis layer.

---

## Vision

Relationship Bridge should function as:

Executive Chief of Staff
+ Competitive Intelligence Platform
+ Relationship Intelligence Platform
+ Personal Operating System

The user should feel like an entire intelligence team worked overnight and delivered a
morning briefing package.

Current experience: "Here are some thoughts."

Desired experience: "Here is everything that happened, the proof behind it, why it
matters, and what you should do."

---

## Defect #1 — Intelligence Brief Is Acting Like an Opinion Piece Instead of an Intelligence Feed

**Current behavior:** The Intelligence Brief frequently contains commentary,
strategic observations, trend analysis, user coaching, and conclusions without
sufficient supporting evidence.

Example: "Restaurant technology is entering an accountability phase." This may be
true, but the brief fails to show who said it, what happened, what evidence supports
it, or what sources were reviewed.

**Expected behavior:** Every major conclusion should be supported by evidence before
synthesis occurs.

Example:

> **Restaurant Technology Accountability Trend**
>
> Evidence:
> - Restaurant Business article
> - QSR Magazine article
> - Toast executive comments
> - Olo executive comments
> - Operator survey results
>
> Supporting links: [Source 1] [Source 2] [Source 3]
>
> Impact assessment (only after evidence is presented).

**Success criteria:** Every significant conclusion contains supporting evidence,
source attribution, direct links, date discovered, and a significance rating.

---

## Defect #2 — Intelligence Brief Does Not Demonstrate Proof of Work

**Current behavior:** The brief does not show what RB actually monitored. The user
cannot determine which sources were searched, which systems were checked, which
relationships were monitored, or how many signals were found. This creates trust
issues.

**Expected behavior:** Every Intelligence Brief should begin with an "Overnight
Collection Summary":

- **Sources scanned:** restaurant technology sources, payments sources, AI sources,
  franchise sources, industry publications, earnings call sources, LinkedIn, email,
  SMS, calendar, contacts, internal relationship database
- **Signals identified:** high / medium / low priority counts
- **Signals escalated:** list of escalated items

**Success criteria:** User can clearly see what RB checked, what RB found, what RB
ignored, and why RB escalated certain items.

---

## Defect #3 — Intelligence Brief Lacks Source Links

**Current behavior:** News and observations are frequently presented without direct
source links.

**Expected behavior:** Every intelligence item should include headline, source,
publication date, direct link, summary, and "why it matters" — e.g.:

> **Restaurant Business** — June 13, 2026
> Headline / Summary / Link / Impact

**Success criteria:** Every news item is traceable; the user can independently verify
conclusions.

---

## Defect #4 — Watchlist Monitoring Is Too Narrow

**Current behavior:** The system focuses primarily on PAR, Toast, and Olo, while many
important sectors receive limited coverage.

**Expected behavior:** RB should monitor the entire restaurant technology ecosystem,
including (with additional categories dynamically discovered over time):

- **POS:** PAR, Toast, Qu, NCR Voyix, Oracle Hospitality, Agilysys
- **Back office:** Restaurant365, Crunchtime, Fourth
- **Loyalty:** Paytronix, Thanx, Punchh, Incentivio
- **Payments:** Worldpay, Shift4, Fiserv, Toast Payments, Adyen
- **Ordering:** Olo, Lunchbox, ChowNow, Deliverect
- **AI:** SoundHound, ConverseNow, Hi Auto, Berry AI, Miso, Botrista
- **Delivery:** DoorDash, Uber Eats, Wonder

**Success criteria:** Daily monitoring extends beyond a fixed list and surfaces
meaningful signals from across the ecosystem.

---

## Defect #5 — Relationship Intelligence Is Missing

**Current behavior:** LinkedIn intelligence is largely absent. The system does not
consistently surface job changes, promotions, company moves, new executive roles,
funding announcements, speaking engagements, or major content performance.

**Expected behavior:** Dedicated "Relationship Intelligence" section covering new
roles, promotions, executive moves, network changes, and recommended actions — e.g.:

> Joe Smith promoted to SVP at Company X.
> Suggested action: send congratulations message.

**Success criteria:** Relationship intelligence becomes actionable rather than
passive.

**Related:** Overlaps significantly with `CLAUDE_DEFECT_RB_041_LINKEDIN_INTELLIGENCE_INGESTION_AND_RELATIONSHIP_ACTION_ENGINE.md`
(filed 2026-06-12, proposed RB 9.73) — triage these together.

---

## Defect #6 — Communication Intelligence Is Missing

**Current behavior:** Email, calendar, SMS, and communication activity are not being
incorporated consistently. The brief often ignores important active opportunities
(e.g. Global Payments, Foods Connected, Harri, other active conversations).

**Expected behavior:** Dedicated "Communication Intelligence" section covering emails
received, emails requiring response, calendar changes, interview activity, open
commitments, and important follow-ups.

**Success criteria:** The brief reflects the user's actual professional reality.

---

## Defect #7 — No Delta Reporting

**Current behavior:** The user often sees repeated information, with little
indication of what changed since yesterday.

**Expected behavior:** Every briefing should clearly identify New / Changed / Removed
/ Escalated / Resolved — e.g.:

> **Since Yesterday**
> New signals: 7 | Escalated: 2 | Resolved: 3 | No change: 14

**Success criteria:** User immediately understands what changed.

**Related:** RB-INTEL-021's mandatory-coverage watchlist (rollups fixed in RB 9.71)
already produces per-entity "No Change"/"New Activity"/"Escalation" status — this
defect asks for that delta framing to be generalized as a top-level brief summary, not
just within `watchlist_intelligence`.

---

## Defect #8 — Daily Brief Is Still Too Much Summary and Not Enough Chief of Staff

**Current behavior:** The Daily Brief often repeats facts.

**Expected behavior:** The Daily Brief should consume the Intelligence Brief and
answer only: What matters? What does not matter? What should Todd do? What risks are
emerging? What opportunities are emerging? What can be ignored?

Example:

> **Intelligence:** Three AI product announcements.
> **CoS view:** Ignore — no customer adoption evidence, no strategic implication.

**Success criteria:** The Daily Brief becomes decisional rather than informational.

---

## Recommended Architecture

**Layer 1 — Intelligence Brief**
Purpose: facts, evidence, signals, links, monitoring results, changes since
yesterday. No strategic coaching. No opinions. No executive recommendations.

**Layer 2 — Daily Brief**
Purpose: Chief of Staff interpretation — connect dots, prioritize actions, identify
risks, identify opportunities, suppress noise, recommend actions, challenge
assumptions, provide executive guidance.

---

## Target Outcome

When the user reads the Intelligence Brief they should think: "RB searched the world
and showed me exactly what it found."

When the user reads the Daily Brief they should think: "My Chief of Staff reviewed all
of that information and told me what matters."

The two products should be complementary, not overlapping. The Intelligence Brief
should be the proof layer. The Daily Brief should be the decision layer.

---

## Triage Notes (not yet scoped)

This defect is filed verbatim per Todd's report and has **not** been triaged against
the current codebase. Before scoping into a sprint, a future session should:

- Map each defect (#1-#8) against the current `daily_brief.py` section inventory
  (`sections["intelligence_collection_summary"]`, `sections["watchlist_intelligence"]`,
  `sections["strategic_industry_signals"]`, etc.) to identify what already exists,
  what's partial, and what's missing entirely.
- Assess overlap with `CLAUDE_DEFECT_RB_041_LINKEDIN_INTELLIGENCE_INGESTION_AND_RELATIONSHIP_ACTION_ENGINE.md`
  (Defect #5) and RB-INTEL-021 (Defect #7's delta-reporting ask).
- Given the "Critical" priority and architectural scope, this likely warrants its own
  multi-sprint track (e.g. RB 9.74+) rather than a single sprint, sequenced after RB
  9.72 (section reordering) and RB 9.73 (LinkedIn intelligence, RB-DEFECT-041) — since
  both of those touch the same `brief_display_order` / section structure this defect
  asks to split into two distinct briefs.
