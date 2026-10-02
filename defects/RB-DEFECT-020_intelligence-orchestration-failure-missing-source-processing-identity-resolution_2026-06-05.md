# RB-DEFECT-020: Intelligence Orchestration Failure

**Date:** 2026-06-05  
**Severity:** Critical  
**Classification:** Chief of Staff Intelligence Failure  
**Status:** In Remediation  

---

## Summary

The June 5 Daily Brief exposed a fundamental orchestration failure: the system
generated a brief that appeared complete while skipping multiple available and
high-value intelligence sources. The brief operated as a memory retrieval engine
rather than executing a collection → enrichment → mutation → brief pipeline.

---

## Root Causes

### 1. Apple Contacts — No Pipeline Exists

No script, no pipeline step, no inbox watcher.

The system has no ability to:
- Detect a contacts export in the inbox
- Process vCard or CSV contacts exports
- Enrich baseline contact records with phone/email from Contacts
- Report contacts as a source in source_health.json

**Gap:** `contacts_ingest.py` does not exist.

---

### 2. Apple Messages — Access Check Only, Not Processing Guarantee

`apple_access_check.py` verifies that chat.db is readable. It does not guarantee
that `fetch_apple_messages.py` was called and produced current output. The pipeline
logs a pass if the DB is readable, but does not validate that `messages.json` was
refreshed today.

**Gap:** Source health for `messages` must track the age of `inbox/messages.json`,
not just DB readability.

---

### 3. LinkedIn Export — Drop Location Not Enforced

The export watcher monitors `system/inbox/linkedin_exports/`. If a new export is
dropped anywhere else (e.g., Desktop, Downloads), the watcher never detects it and
the pipeline proceeds without LinkedIn delta intelligence.

**Gap:** No drop location guidance surfaced to user. No detection of exports outside
the watched directory.

---

### 4. Pre-Brief Gate Is Advisory, Not Blocking

`_check_brief_readiness()` in `morning_pipeline.py` records a readiness result and
attempts recovery, but the brief generation step runs regardless of the result. A
DEGRADED or INCOMPLETE readiness result does not block `daily_brief.py`.

**Gap:** Brief must be blocked — or visibly flagged — when required sources have not
been processed today.

---

### 5. Source Health Report Not at Top of Brief

The brief includes source_health data internally but does not render a mandatory
Source Health section at the very top before relationship intelligence. The user
cannot see at a glance what was and was not collected before reading the brief.

**Gap:** Every brief must begin with a Source Health Report showing each source's
processing state (processed / pending / failed).

---

### 6. Identity Resolution Layer Does Not Exist

The system treats Apple Contacts, Apple Messages, LinkedIn, Gmail, and Calendar as
partially independent identity systems. Phone numbers from Messages are matched
against baseline contacts via a `"phone"` field lookup, but there is no unified
identity resolution that merges entities across all sources into a single record.

**Gap:** `identity_resolution.py` does not exist. Every phone number, email, or
name discovered across any source must resolve into exactly one state:
- Merge into existing RB relationship record
- Create new RB relationship record
- Mark as Personal / Exempt (family, church, medical, utilities)

---

### 7. Anonymous Phone Number Governance Not Enforced

Unknown phone numbers can currently contribute to communication activity counts
and velocity signals. There is no governance rule that quarantines unresolved
identities from intelligence scoring.

**Gap:** Unresolved phone numbers must be surfaced for user confirmation and
excluded from all intelligence scoring until confirmed.

---

## Remediation Plan

### Priority 1 — Immediate (RB 9.57)

| Item | File | Action |
|---|---|---|
| Apple Contacts pipeline | `contacts_ingest.py` | Create new script |
| Contacts inbox watch | `morning_pipeline.py` | Add pipeline step |
| Hard pre-brief gate | `morning_pipeline.py` | Upgrade readiness check |
| Source Health brief header | `daily_brief.py` | Mandatory top section |

### Priority 2 — Sprint RB 9.58

| Item | File | Action |
|---|---|---|
| Identity resolution layer | `identity_resolution.py` | Create new script |
| Anonymous phone governance | `contact_index.py` | Quarantine logic |
| Contact refresh reminder | `cos_judgment.py` | Prompt when contacts > 30 days |
| Messages freshness check | `source_health_report.py` | Track messages.json age |

---

## Success Criteria

A Daily Brief must not be generated until:

1. All available intelligence sources have been discovered.
2. All available intelligence sources have been processed.
3. Identity resolution has executed.
4. Mutation generation has executed.
5. Source health has been reported at the top of the brief.
6. Intelligence generation has completed.

---

## Files Changed in This Remediation

- `defects/RB-DEFECT-020_...md` — this file
- `system/scripts/contacts_ingest.py` — new; Apple Contacts export processor
- `system/scripts/morning_pipeline.py` — add contacts step, upgrade gate
- `system/scripts/daily_brief.py` — mandatory Source Health header section
