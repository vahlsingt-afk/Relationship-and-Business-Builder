# RB-DEFECT-064: No Generalized Structured-Dataset Recognition and Ingestion Framework

**Date:** 2026-07-07
**Severity:** Critical
**Classification:** Ingestion Pipeline / Knowledge Architecture
**Status:** In Remediation — Phases 1-4 complete, Phase 5(a) complete (Phase 5(b) open, see below)
**Reported by:** Todd

## Remediation progress

- **Phase 1 (done):** `dataset_classifier.py` (signature-based, confidence-gated classifier) +
  `hubspot_ingest.py` + `P-039_hubspot_crm_ingest.md`, wired into `morning_pipeline.py` for
  unattended nightly ingestion. `structured_ingest.confidence_threshold` added to `settings.json`.
- **Phase 2 (done):** Extracted the identity-resolution primitives duplicated across
  `linkedin_ingest.py`/`contacts_ingest.py`/`hubspot_ingest.py` into a shared `identity_matcher.py`;
  all three now delegate to it. `contacts_ingest.py` had zero prior test coverage — added
  characterization tests before migrating so the refactor is verified, not just asserted safe.
- **Phase 3 (done):** Unified the mutation-report format across all three ingestion paths via a
  shared `mutation_report.py` (canonical fields: Knowledge Sources Updated / People Imported /
  Existing People Updated / New People Created / Duplicate Candidates / Companies Added /
  Relationship Links Created / Knowledge Mutations Applied / Confidence). `contacts_ingest.py`
  previously wrote **zero** markdown delta report (JSON cache only) — now writes
  `system/deltas/apple_contacts_export_<date>.md` for the first time. Fields a given source
  genuinely doesn't compute render as "not computed" with a stated reason, never a fabricated
  number.
- **Phase 4 (done):** Stage 6 intelligence generation for `hubspot_ingest.py`, via a new
  `post_ingest_intelligence.py` — no new graph store or scoring model, reuses
  `rb_core.find_intro_paths` (the same broker-scoring `intro_engine.py` already uses on demand) and
  a simple `last_touch` staleness check. Two real signals surface automatically on every import:
  **dormant relationships resurfaced** (an existing contact this import touched has no `last_touch`
  on file, or it's 365+ days old — an old CRM export is often exactly what makes someone like this
  visible again) and **warm introduction candidates** (a newly created contact's company already has
  a baseline insider with genuine proximity — same company, shared Circle, or active-thread overlap).
  Deliberately excludes `find_intro_paths`' no-insider "highest-DRR contact, ask around" fallback
  from the warm-intro count — an early proof-run against the real baseline surfaced a highest-DRR
  contact for two *fictional* test companies with zero actual connection, which would have overstated
  a real relationship signal; fixed by requiring `has_proximity=True` before counting. Confirmed via
  a dry-run against the real baseline: correctly surfaced an actual dormant contact (617 days since
  last touch) and correctly returned zero false-positive warm intros for companies with no real
  insider. Proved via 9 new `post_ingest_intelligence` tests + 2 new `hubspot_ingest` integration
  tests. Did **not** attempt Person→Industry/Event/Opportunity/Meeting edges (Stage 5) — a HubSpot
  "export contacts" CSV carries none of that data; fabricating it would violate the same
  never-report-a-number-that-wasn't-computed discipline `mutation_report.py` already enforces.
  `linkedin_ingest.py`/`contacts_ingest.py` are not yet wired to this module (Phase 5).
- **Phase 5(a) (done, 2026-07-09):** Wired `post_ingest_intelligence.py` into `linkedin_ingest.py`
  (both `ingest()` and `ingest_from_csv_text()` — dormant relationships resurfaced from matched
  existing entries, warm-intro candidates from newly created connections) and `contacts_ingest.py`
  (dormant relationships resurfaced only — Apple Contacts import never auto-creates new baseline
  entries; unmatched/probable rows go to the reconciliation queue for operator confirmation, so
  there's no newly-created-contact set to score for a broker). Both delta reports now render a
  "Post-Ingest Intelligence" / dormant-relationships section, matching `hubspot_ingest.py`'s existing
  format. Proved via 8 new tests (`test_linkedin_ingest_post_intelligence.py`,
  `test_contacts_ingest_post_intelligence.py`) plus the full existing linkedin/contacts/hubspot
  suites (125 passed, no regressions).
- **Phase 5(b) (open, not started):** Person→Industry/Event/Opportunity/Meeting graph edges — needs
  a source that actually carries that data (e.g. a conference attendee list or calendar export),
  which has no ingest pipeline yet at all (see Current Behavior above). Existing downstream systems
  (`micro_graph_builder.py`, `gap_detection.py`, etc.) still read `baseline_index.json` on their own
  cadence rather than being pushed to directly by any ingest script, same as before this defect.

---

## Summary

Uploading a structured dataset (e.g. `hubspot-crm-exports-all-contacts-2026-07-07.csv`) does not
trigger automatic classification or ingestion. RB analyzed the file but did not recognize it as a
CRM export / candidate baseline-enhancement source, and required the user to state explicitly
"this is my former HubSpot CRM contact list — use this as baseline data enhancement." Even after
that clarification, RB described the ingestion workflow rather than executing it and producing a
mutation report.

## Current Behavior

Structured-source recognition in RB is bespoke and enumerated by hand, one source type at a time,
each requiring its own defect and its own protocol before it is auto-routed:

- LinkedIn export → `P-002_linkedin_ingest.md` (auto-routed per its own trigger rule)
- LinkedIn messaging export → `P-033_linkedin_messaging_export_ingest.md`
- Apple Contacts export → added via [RB-DEFECT-025](RB-DEFECT-025_apple-contacts-not-recognized-as-identity-enrichment-artifact_2026-06-06.md)
- LinkedIn profile URLs → added via [RB-DEFECT-034](RB-DEFECT-034_linkedin-url-ingestion-and-profile-enrichment-failure_2026-06-08.md)
- Conversation-pasted artifacts → `P-020_conversation_artifact_ingestion.md`

No protocol exists for CRM exports (HubSpot, Salesforce, Dynamics), conference attendee lists,
event registrations, or generic contact CSV/XLSX from an unrecognized source. Anything outside the
small enumerated set falls through to conversational handling: RB treats it as a document to
summarize/discuss rather than a knowledge-graph mutation to execute.

## Root Cause

1. **No classifier stage.** There is no step that runs on every upload, before conversational
   handling, to classify file type + intent (CRM export vs. contact export vs. calendar export vs.
   notes vs. financial data) and check it against a confidence threshold. Each source type instead
   got its own hand-written recognition rule added reactively per defect.
2. **No generalized knowledge-source registry.** `source_watch.yaml` / `source_permission.py`
   track source-level trust/permission for *known* sources, but nothing registers a *newly seen*
   dataset (name, type, version, provenance) the way the request describes.
3. **Identity resolution logic is duplicated per source, not shared.** The URL-slug → name →
   email matcher in `P-002` (linkedin_ingest.py) and the phone/email matcher in `contacts_ingest.py`
   solve the same problem (match incoming row to existing baseline person) but are separate,
   source-specific implementations. `entity_identity.py` does the equivalent for companies but has
   no person-level counterpart usable across arbitrary import types.
4. **Mutation reporting is source-specific.** `P-002` produces a delta report
   (`system/deltas/linkedin_export_<date>.md`), but there is no generalized mutation-report format
   (people imported/updated/created, duplicates, companies added, relationship links created) that
   every ingestion path emits the same way.

## Requested Architecture (from report)

A source-agnostic pipeline: classify → register knowledge source → resolve identity against all
existing sources → mutate canonical KB (never duplicate, never overwrite history, track provenance)
→ update the relationship graph (Person→Company/Industry/Event/Opportunity/Meeting) → generate
intelligence (dormant relationships, warm intros, priority outreach) → emit a mutation report —
executed automatically above a configurable confidence threshold, for any structured source
(CRM exports, contacts exports, conference/event lists, calendar exports, email metadata, transcript
systems, business-card OCR, API feeds).

## Recommendation — Phased, Not a Single Change

This defect asks RB to become source-agnostic across identity resolution, KB mutation, graph
updates, and intelligence generation at once — that's most of the ingestion stack. Recommend
proving the pattern once before generalizing everywhere:

- **Phase 1:** Build the classifier stage + a HubSpot CRM export protocol (next: `P-039`) as the
  first instance of the generalized framework, using the exact file from this report as the test
  case. Confidence-threshold config lives in `settings.json` alongside existing ingest toggles.
- **Phase 2:** Extract `P-002`'s matcher (URL slug → name → email) into a shared,
  source-agnostic module; migrate LinkedIn export and Apple Contacts onto it so there's one
  identity-resolution path, not three.
- **Phase 3:** Generalize the mutation-report format (`Knowledge Sources Updated / People Imported /
  Existing People Updated / New People Created / Duplicate Candidates / Companies Added /
  Relationship Links Created`) across every ingestion path, including the existing LinkedIn/Apple
  ones.

## Related

- `system/protocols/P-002_linkedin_ingest.md`, `P-019_apple_interaction.md`,
  `P-020_conversation_artifact_ingestion.md`, `P-033_linkedin_messaging_export_ingest.md`,
  `P-038_privacy_security_ingestion.md`
- `system/scripts/entity_identity.py` (company-level identity matching — no person-level
  equivalent yet)
- `system/scripts/contacts_ingest.py`, `linkedin_ingest.py`, `source_permission.py`,
  `source_watch.yaml`
- [RB-DEFECT-025](RB-DEFECT-025_apple-contacts-not-recognized-as-identity-enrichment-artifact_2026-06-06.md),
  [RB-DEFECT-034](RB-DEFECT-034_linkedin-url-ingestion-and-profile-enrichment-failure_2026-06-08.md) —
  prior instances of this same gap, fixed one source at a time
