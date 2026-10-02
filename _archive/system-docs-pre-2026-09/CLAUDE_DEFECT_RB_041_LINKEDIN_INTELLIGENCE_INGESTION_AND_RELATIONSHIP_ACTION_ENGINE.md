# RB-DEFECT-041: LinkedIn Intelligence Ingestion, Baseline Management, and Relationship Action Engine

**Date filed:** 2026-06-12
**Filed by:** Todd
**Status:** Filed — not yet triaged/scoped. Recorded verbatim for processing in a future
sprint (proposed: RB 9.73, after 9.70/9.71/9.72 land — see
`CLAUDE_DEFECT_RB_040_DAILY_BRIEF_ARCHITECTURE_AUDIT.md` §4).
**Priority:** High (per filer)
**Trigger:** A LinkedIn export was uploaded to RB on 2026-06-12 during testing. RB
treated it as a generic file requiring user direction rather than recognizing it as a
structured relationship-intelligence source.

---

## Executive Summary

RB currently behaves more like a repository for LinkedIn data than a relationship
intelligence platform. The objective of RB is not to store data — it is to identify
change, determine significance, and recommend actions that strengthen relationships,
improve account intelligence, and surface opportunities. The current implementation
falls short of that objective.

---

## Defect #1 — LinkedIn Exports Are Not Automatically Recognized as Intelligence Sources

**Current behavior:** When a LinkedIn export is uploaded, RB waits for user
instructions regarding what should be done with the file.

**Expected behavior:** RB should automatically recognize LinkedIn exports as
high-value intelligence sources and initiate a predefined ingestion workflow. The user
should not need to instruct RB to unpack, process, compare against prior baselines,
generate relationship intelligence, recommend actions, etc. — these should happen
automatically.

**Required workflow on upload:**
1. Detect LinkedIn export
2. Unpack archive
3. Inventory available datasets
4. Process records
5. Compare against prior baseline
6. Generate intelligence report
7. Generate recommended actions
8. Update relationship graph
9. Store new baseline

---

## Defect #2 — No Historical Baseline Preservation

**Current behavior:** RB processes LinkedIn exports as point-in-time datasets.

**Problem:** Intelligence requires comparison. Without a prior baseline RB cannot
answer "What changed? What matters? What should I do next?" — the result is data
analysis rather than intelligence.

**Required capability:**
- **Prior snapshot** retained: total connections, connection roster, titles,
  companies, locations, company follows, invitations, saved jobs, engagement activity,
  recruiter activity, relationship classifications.
- **New snapshot** captured immediately after ingestion.
- **Delta engine** that automatically calculates changes between snapshots.

---

## Enhancement #1 — LinkedIn Delta Intelligence Engine

RB should generate a delta report for every upload, covering:

- **Network growth**: connections added/removed, net growth, growth rate (example:
  Previous 2,748 → Current 2,791 → Net +43).
- **Title changes**: promotions/demotions/role changes (example: John Smith, Director
  of Operations → VP Operations → recommended action: send congratulations message).
- **Company changes**: new employers, departures, competitive movement (example:
  former PAR contact joins Toast → recommended action: update competitive intelligence
  profile).
- **Location changes**: strategic moves (example: former Midwest exec relocates to
  Dallas → recommended action: review for expanded territory influence).
- **New followers / company follows**: emerging interests (example: multiple follows
  in AI and restaurant automation → recommended action: flag emerging market focus).

---

## Enhancement #2 — Relationship Action Engine

RB should not stop at reporting — it should recommend actions. For every significant
signal RB should answer:
1. **What changed?** (e.g., contact promoted to VP)
2. **Why does it matter?** (e.g., now influences purchasing decisions)
3. **What should Todd do?** (e.g., send congratulations message and schedule reconnect)

**Example output patterns:**
- *Promotion signal*: "Joe Blow promoted to VP Technology. Last interaction: 18 months
  ago. Recommended action: send congratulations note. Priority: High."
- *New company signal*: "Former McDonald's contact joins Global Payments. Recommended
  action: add to Worldpay account map. Priority: High."
- *Dormant relationship signal*: "Former PAR relationship becomes active again.
  Recommended action: reconnect and assess opportunity. Priority: Medium."

---

## Enhancement #3 — Strategic Account Mapping

RB should automatically update account maps when LinkedIn changes occur, including:

- **Global Payments / Worldpay**: new connections, shared relationships, internal
  influencers.
- **Foods Connected**: supply chain leaders, McDonald's stakeholders, industry
  connections.
- **Restaurant technology ecosystem**: track movement across PAR, Toast, Olo, Qu, NCR
  Voyix, Oracle Hospitality, Agilysys, Restaurant365, Crunchtime, Paytronix, DoorDash,
  Uber Eats, Shift4, Global Payments, Worldpay, and other monitored organizations.

---

## Enhancement #4 — Relationship Graph Persistence

RB should maintain a continuously evolving relationship graph. Each contact should
contain: name, company, title, history of title changes, history of company changes,
last interaction date, relationship score, strategic relevance, customer/prospect/
partner classification, and watchlist status. Historical changes should be retained.

---

## Enhancement #5 — Intelligence Quality Standard

Every LinkedIn ingestion should produce a report containing:

- **What was processed**: files found, records processed, data quality observations.
- **What changed**: new connections, lost connections, promotions, employer changes,
  new follows, engagement changes.
- **Why it matters**: business impact and relationship significance.
- **Recommended actions**: concrete next steps.
- **Confidence rating**: High / Medium / Low.

---

## Strategic Design Principle

RB should not function as a LinkedIn archive. RB should function as a relationship
intelligence operating system. The purpose of LinkedIn ingestion is not to tell the
user what exists — it is to tell the user (1) what changed, (2) why it matters, and
(3) what to do next. If those three questions are not answered after every LinkedIn
ingestion, RB is delivering data rather than intelligence.

## Impact

Directly affects: relationship management, executive networking, job search
effectiveness, account planning, competitive intelligence, consulting opportunity
identification, and the long-term value of the RB platform.

## Success Criteria

Future LinkedIn uploads should automatically: ingest, compare, detect changes, surface
intelligence, recommend actions, and update baselines — without requiring user
intervention.

---

## Relationship to Existing Architecture (preliminary note, for future triage)

This defect report describes, in essence, a dedicated **LinkedIn ingestion pipeline**
analogous to `morning_pipeline.py`'s source-collection model, plus a **persistent
LinkedIn snapshot store** (prior/current + delta) and a **dedicated delta-to-action
mapping layer**. It overlaps conceptually with:

- RB 9.70 item 6 (Intelligence Brief + signal drill-down, RB 9.71) — the
  "recommended action per signal with priority/confidence" pattern described here is
  the same shape as the Relationship Action Engine requested here.
- The existing `relationship_intake.py` / `mutations.py contact-update` /
  `baseline_index.json` machinery — which today is conversational-input-driven, not
  file-upload-driven, and has no snapshot/delta concept for any source (not just
  LinkedIn).

A full audit of the actual LinkedIn export that triggered this report (its file
format, what fields it contains, and what — if anything — RB's existing scripts
already do with LinkedIn data) is needed before this can be scoped into concrete
sprint items. Not done as part of this filing; this document captures the request
verbatim for that future triage.

---

## RB 9.73 Triage Findings (2026-06-14)

The full audit called for above was completed. Findings against the current
codebase, item by item:

- **Defect #1 (auto-recognition workflow):** Largely already built.
  `linkedin_export_watcher.py` + `linkedin_ingest.py` implement classification,
  unpack, inventory, baseline compare, delta report, and baseline update, and are
  wired into `morning_pipeline.py` (DEFECT-007: `linkedin_export_watcher.py
  --ingest-new --confirm` runs at 5 AM). The literal trigger for this report — the
  June 12 export "sitting unprocessed" — was **not** a code gap: no June-12 file was
  ever placed in `system/inbox/linkedin_exports/`, so the nightly watcher had nothing
  to find. The real gap was a protocol-following gap: when an export ZIP is uploaded
  **in conversation**, P-002 already said to auto-route it, but did not say to do so
  *immediately in-session* rather than relying on the nightly path. **Fixed**: P-002's
  "When to run" section now explicitly distinguishes the in-conversation path (run
  immediately) from the inbox/nightly fallback path (DEFECT-007).
- **Defect #2 (historical baseline preservation):** Already implemented —
  `system/_snapshots/baseline_index.<date>.json` snapshots are written before every
  ingest per P-002 step 2, and `baseline_index.json` is enhanced (not replaced) per
  Tenet 6.
- **Enhancement #1 (delta intelligence engine):** Mostly implemented in
  `linkedin_ingest.py`'s delta blocks — `new`, `company_changes`, `role_changes`,
  `reconnections`, `disconnections`, `rc_moves`/`lki_moves`, `new_highlights` are all
  computed and reported. **Location changes are not implementable**: every entry's
  `location` field is hardcoded to `None` because LinkedIn's `Connections.csv` export
  does not include a location/geography column. This is a data-availability
  limitation, not a code gap — flagged here as **infeasible with current export
  data** rather than deferred.
- **Enhancement #2 (Relationship Action Engine):** Already implemented —
  `_recommend_actions()` in `linkedin_ingest.py` and the
  `linkedin_intelligence_opportunity` brief section surface "what changed / why it
  matters / what to do" for career moves and new highlights.
- **Enhancement #3 (strategic account mapping — Global Payments/Worldpay, Foods
  Connected, restaurant-tech ecosystem):** **Not built.** This would mean tagging
  baseline entries against a strategic-account list (analogous to
  `strategic_operators.yaml`'s watchlist) so LinkedIn moves at those accounts get
  elevated handling. Deferred to a future sprint (9.73b or later) — real net-new
  scope, not a triage item.
- **Enhancement #4 (relationship graph persistence — per-contact history):** Partially
  built. Each ingest appends a dated note (`_append_note`) and a
  `linkedin_delta_<date>` tag to the contact's `notes`/`tags`, which is a de facto
  history log, but there is no structured `title_history`/`company_history` array per
  contact. Deferred — would require a `baseline.schema.json` change plus a migration,
  out of scope for a triage sprint.
- **Enhancement #5 (intelligence quality standard report):** Already substantially
  covered by the existing delta markdown report
  (`system/deltas/linkedin_export_<date>.md`) and `headline_counts` /
  `delta_intelligence` blocks, which report processed/changed counts and confidence
  via `_recommend_actions()`. No further action taken in this triage.

**Net result:** ~80% of this report's asks are already implemented. The literal
trigger was an operational/protocol gap, now fixed in P-002. The remaining net-new
work (Enhancement #3 strategic account mapping, Enhancement #4 structured
title/company history arrays) is real but independent scope, not blocking — deferred
to a future sprint rather than bundled here. This defect is considered **triaged and
closed** for RB 9.73; remaining items may be re-filed as a focused enhancement request
if prioritized later.
