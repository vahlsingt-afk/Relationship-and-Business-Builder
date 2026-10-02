# RB-DEFECT-044: Major Enhancement Request — Two-Document Briefing Architecture (Intelligence Brief incl. Collection Report / Daily Brief)

**Date filed:** 2026-06-14
**Filed by:** Todd
**Status:** Filed — not yet triaged/scoped. Recorded verbatim for processing in a
future sprint.
**Priority:** Critical
**Category:** Intelligence Engine / Executive Briefing System
**Relationship to RB-DEFECT-043:** This supersedes and substantially expands the
"two-document split" item left open in RB-DEFECT-043's "Next up" entry (RB 9.77+).
RB-DEFECT-043's Defects #1–#8 were partially addressed across RB 9.74–9.76
(proof-layer elements, communication rollup, calendar-change/interview detection).
This defect reframes the remaining architectural work as a **three-layer
conceptual model rendered as two documents**, and should be triaged against what
RB 9.74–9.76 already delivered before scoping the first implementation slice.

---

## Architectural Clarification (2026-06-14 follow-up from Todd)

The three "layers" below describe three distinct **purposes/answers**, not three
separate output documents. This remains a **two-document architecture**:

- **Document 1 — Intelligence Brief.** The Collection Report (Layer 1, "what did we
  scan?") sits **at the top of** the Intelligence Brief (Layer 2, "what changed?")
  as its opening section — proof-of-collection followed by the day's factual
  intelligence. One document, one canonical structure: Collection Report section
  + Intelligence content sections below it.
- **Document 2 — Daily Brief.** Layer 3 ("so what?") — the CoS synthesis,
  unchanged as its own document, but its canonical structure is being redefined as
  part of this request (see "Document 2: Daily Brief" below).

Both documents' canonical structures need to be (re)defined as part of this work:
the Intelligence Brief's canonical structure is Collection Report (top) +
Layer-2 content (below); the Daily Brief's canonical structure is the Layer 3
structure below, redefined from its current form.

---

The text below is recorded verbatim as submitted by Todd, with section headers
annotated above to reflect the two-document grouping clarified above.

---

## Major Enhancement Request: Redesign Relationship Bridge Briefing Architecture

### Executive Summary

The current Intelligence Brief and Daily Brief continue to improve, but they are
still operating primarily as intelligent summaries rather than a true Chief of Staff
intelligence and planning system.

The core issue is architectural.

The system currently blends collection, reporting, analysis, and recommendations
into a single output. As a result, the briefs often feel like an assistant
summarizing information instead of a CoS uncovering changes, identifying
opportunities, predicting needs, and driving decisions.

We need to redesign the briefing architecture into three distinct layers:

1. Collection Report (Proof)
2. Intelligence Brief (Facts)
3. Daily Brief (Chief of Staff Analysis)

Each layer should have a unique purpose and output style.

---

### Design Principle

Collection Report answers: **"What did we scan?"**

Intelligence Brief answers: **"What changed?"**

Daily Brief answers: **"So what?"**

The current implementation often skips the first two questions and jumps directly to
the third.

This creates trust issues because users cannot determine:

- What was scanned
- Whether intelligence collection actually occurred
- Whether information is new
- Whether information is being repeated

The redesigned architecture resolves this.

---

## Document 1: Intelligence Brief

**Canonical structure:** Collection Report section (Layer 1, below) at the top of
the document, followed by the Intelligence content sections (Layer 2, below).

### Layer 1: Collection Report (Proof)

#### Purpose

Establish trust.

Before presenting intelligence, Relationship Bridge must prove that it performed
intelligence gathering and processing activities.

The Collection Report should function as evidence.

#### Canonical Collection Report

**Collection Summary**

- Last Refresh:
- Collection Duration:

**High-Signal Sources Scanned**

Macro Intelligence
- World news sources
- National news sources
- Financial news sources
- Government sources
- Regulatory sources

Company Intelligence
- Public company earnings calls
- Earnings transcripts
- SEC filings
- Investor relations pages
- Press releases
- Executive leadership changes
- Board changes
- Funding announcements
- M&A announcements

Industry Intelligence
- Restaurant industry publications
- Restaurant technology publications
- Payments industry publications
- Franchise industry publications

Relationship Intelligence
- Gmail (all connected accounts)
- SMS records
- Phone logs
- LinkedIn
- Calendar
- Contacts database

**Sources Processed**

| Source | Result |
| --- | --- |
| World News Sources | X |
| National News Sources | X |
| Financial Sources | X |
| Earnings Calls Processed | X |
| Press Releases Analyzed | X |
| Executive Change Alerts | X |
| Restaurant Industry Sources | X |
| Restaurant Technology Sources | X |
| Company Watch List | X |
| LinkedIn Contacts | X |
| Personal Gmail | X |
| Business Gmail | X |
| SMS Messages | X |
| Phone Records | X |
| Calendar Events | X |

**Mutations Detected**

| Database | Mutations |
| --- | --- |
| Macro Intelligence | X |
| Micro Intelligence | X |
| Company Intelligence | X |
| Relationship Intelligence | X |
| Contact Intelligence | X |

**Exceptions**

Examples:
- Contacts requiring identity resolution
- Duplicate contacts detected
- Failed imports
- Missing source refreshes
- Incomplete company profiles

This section exists to prove intelligence gathering occurred.

---

### Layer 2: Intelligence Brief

#### Purpose

Deliver factual intelligence.

The Intelligence Brief should function as the morning newspaper.

Its job is to answer: **"What changed?"**

It should not primarily focus on recommendations.

Target composition:
- 10% Collection Report
- 80% New Intelligence
- 10% Brief CoS Commentary

#### Canonical Intelligence Brief Structure

**World News**

Report 5–10 significant global developments.

Requirements:
- New developments only.
- Do not repeat stories unless materially changed.
- Include source links.
- No filler content.

Examples: Wars, geopolitical developments, trade disputes, economic disruptions,
energy market changes.

*CoS Summary* — Brief commentary on why this matters.

**National News**

Report 5–10 significant national developments.

Requirements:
- New developments only.
- Supporting links.
- No repeated stories without meaningful change.

Examples: Inflation, employment, Federal Reserve, tax policy, tariffs, regulatory
changes.

*CoS Summary* — Why this matters.

**Restaurant Industry News**

Report 5–10 significant developments.

Requirements:
- New developments only.
- No recycled stories.
- No fabricated significance.

If no meaningful developments occurred: "No material restaurant industry
developments detected during this collection cycle." This is acceptable and builds
trust.

*CoS Summary* — Why this matters.

**Restaurant Technology News**

Report 5–10 significant developments.

Examples: Executive changes, product launches, customer wins/losses, funding
events, acquisitions, RFP activity, earnings announcements, leadership changes.

*CoS Summary* — Why this matters.

**Company Watch List Intelligence**

Purpose: The watch list is a signal detection system. It is NOT a status report. It
should report only material changes.

Rules:
1. Only report companies with material changes.
2. Do not report companies that have no changes.
3. If no monitored companies changed, explicitly state: "No material updates
   detected across the monitored company watch list during this collection cycle."
4. Do not repeat previously reported intelligence.
5. Do not recycle historical information as new intelligence.
6. Track first-seen and last-reported dates for every signal.
7. Automatically archive stale signals.

Correct example:

> Company Watch List Intelligence
>
> Material updates detected:
> - Toast announced partnership with XYZ.
> - PAR filed an 8-K regarding leadership changes.
> - Olo announced a new enterprise deployment.

Correct example (no changes):

> Company Watch List Intelligence
>
> No material updates detected across the monitored company watch list during this
> collection cycle.
>
> Last watch list mutation: 3 days ago.

Incorrect example (creates noise, reduces trust):

> PAR: No material changes.
> Toast: No material changes.
> Olo: No material changes.
> NCR Voyix: No material changes.

**Critical Requirement**

Previously reported intelligence must never continue resurfacing as new
intelligence.

Example: "Joseph Yetter promoted to President of PAR." Once reported and
acknowledged, it should be archived. It should only reappear if:
- New organizational changes occur
- Strategic implications emerge
- Additional leadership changes happen
- Earnings discussions reference it

Historical intelligence should never be presented as new intelligence.

**Personal Intelligence**

Report only new intelligence.

Relationships: Promotions, job changes, new connections, meeting acceptances,
relationship mutations.

Communications: Important emails, follow-up opportunities, LinkedIn messages, SMS
conversations.

Calendar Intelligence: New meetings, meeting changes, conflicts, scheduling
pressure.

Contact Resolution: New contacts needing review, duplicate identities, relationship
merges.

*CoS Summary* — Why this matters.

---

## Document 2: Daily Brief

**Canonical structure:** redefined below (Layer 3) as part of this request — this
replaces the Daily Brief's current canonical structure.

### Layer 3: Daily Brief

#### Purpose

The Daily Brief is not a newspaper.

The Daily Brief is the Chief of Staff walking into the room.

It assumes the Intelligence Brief already exists. Its purpose is not to repeat
facts. Its purpose is to synthesize.

The Daily Brief should answer:
- What matters?
- What requires action?
- What risks exist?
- What opportunities exist?
- What should I prepare for?

#### Canonical Daily Brief

**Executive Summary**

Three to five issues deserving attention today. Not every issue. Only the
highest-priority issues.

**Today**

Immediate priorities. Examples: meetings, follow-ups, decisions, deadlines.

**This Week**

Near-term priorities. Examples: interviews, earnings calls, customer meetings,
deliverables, relationship opportunities.

**This Month**

Strategic priorities. Examples: career decisions, major projects, conferences,
business development opportunities.

**Horizon Watch (30–90 Days)**

Important developments not requiring action yet. Examples: industry events, earnings
seasons, renewal cycles, competitive developments.

**Opportunities**

New opportunities identified. Examples: career opportunities, industry
opportunities, relationship opportunities, competitive opportunities.

**Risks**

New risks identified. Examples: hiring delays, competitive threats, market changes,
relationship deterioration, scheduling overload.

**Relationships Requiring Attention**

People requiring action. Examples: congratulations, follow-up, reconnection,
meeting preparation.

**Decisions Approaching**

Upcoming decisions requiring preparation. Examples: job offers, investments,
strategic initiatives, hiring decisions.

**Signals and Patterns**

This is one of the highest-value sections. The CoS should actively connect dots.
Examples: emerging market trends, repeating signals, contradictions, strategic
implications. This section should provide insight rather than simply reporting
facts.

**Upcoming Preparation Requirements**

This is a required section. Relationship Bridge should move beyond calendar
awareness and into preparation awareness.

Examples:

Within 24 Hours — Sales Funnel Review
- Estimated prep time: 30 minutes
- Required inputs: pipeline report, forecast updates, open opportunities
- Recommendation: Prepare Monday afternoon.

Within 7 Days — Foods Connected Interview
- Review prior notes
- Research customer updates

Within 30 Days — Potential Global Payments onboarding
- Build target account list
- Map Worldpay relationships

**Learned Patterns**

Relationship Bridge should identify recurring preparation behaviors. Examples:
- Weekly sales review requires 30 minutes preparation.
- Monthly board review requires 2 hours preparation.
- Quarterly business review requires account research.

The system should learn these patterns and proactively recommend preparation
windows before deadlines occur. This transforms Relationship Bridge from a
reporting platform into a predictive executive operating system.

---

### Success Criteria

- A successful Collection Report proves intelligence gathering occurred.
- A successful Intelligence Brief teaches the user something new.
- A successful Daily Brief changes what the user does today.

Those should be the standards by which all future briefing outputs are measured.

This version captures the biggest lesson from the recent brief reviews: the goal is
not better summaries; the goal is an intelligence system that discovers changes,
builds trust through evidence, and helps the user prepare for the future rather than
merely reporting the past.

---

## Triage Notes (to be completed in a future sprint)

Before scoping implementation, triage against what already exists. Per the
architectural clarification above, this is a **two-document** project: Document 1
(Intelligence Brief, with Collection Report as its opening section) and Document 2
(Daily Brief, canonical structure redefined). Both documents' canonical structures
need to be written up explicitly as part of the eventual implementation sprint(s).

- `intelligence_collection_summary` (RB-INTEL-021) already covers much of the
  "Collection Report / Proof" section's intent — audit log, collection summary
  section, `/intelligence/collection` + `/audit` + `/signals` endpoints,
  `inclusion_reason` on signals. Gap analysis needed against the canonical
  "Sources Processed" / "Mutations Detected" / "Exceptions" table format specified
  above, and against where it sits relative to the rest of the Intelligence Brief
  (it should be the opening section of Document 1, not a separate document).
- `watchlist_intelligence` (RB-DEFECT-020/RB 9.74) already implements the
  "material changes only" + "no change" rollup rule for the company watch list —
  verify it matches the "Correct"/"Incorrect" examples above exactly, including
  first-seen/last-reported tracking and stale-signal archival (may be a gap).
- `communication_intelligence` (RB 9.75/9.76) covers part of "Personal
  Intelligence" (communications, calendar intelligence) — verify against the
  fuller "Personal Intelligence" spec above (relationships, contact resolution).
- `rendering_rules` RB-DEFECT-043 #1/#3/#8 (RB 9.74) already establish
  evidence-before-synthesis, source-link, and Intelligence→CoS framing
  conventions — likely reusable for the Layer 1/Layer 2 split here.
- "Upcoming Preparation Requirements" + "Learned Patterns" are net-new — no
  existing section currently tracks recurring prep-time patterns or proactively
  schedules prep windows. This is likely the largest net-new piece of work.
- World/National/Restaurant Industry/Restaurant Technology headline sections
  exist (`world_national_headlines`, `restaurant_industry_headlines`,
  `restaurant_technology_headlines`, Sprint D/E) — verify against the "5–10 items,
  new only, no recycled stories" requirement and the "no material developments"
  fallback message.
