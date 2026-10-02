# RB-DEFECT-025: Apple Contacts Export Not Recognized as Identity-Enrichment Artifact

**Date:** 2026-06-06  
**Severity:** High  
**Classification:** Relationship Intelligence / Ingestion Pipeline  
**Status:** In Remediation  

---

## Summary

User uploaded an Apple Contacts export. The system treated it as a generic contact
file rather than recognizing its strategic purpose as baseline identity-enrichment
infrastructure for SMS/Messages intelligence.

No identity-resolution workflow was initiated. No phone-number matching occurred.
No reconciliation queue was generated. SMS attribution remained materially degraded.

---

## Root Cause

`contacts_ingest.py` performs basic name-matching and phone enrichment but lacks:

1. E.164 phone normalization (exact match standard)
2. Composite evidence matching (phone + email + company)
3. Classification (network entity / personal / family / service number)
4. Reconciliation queue for ambiguous matches
5. Permanent exclusion registry for personal/service numbers
6. Identity map persisted for SMS attribution layer
7. XLSX support (user extracted contacts to spreadsheet)

The artifact classification in the API also does not recognize Apple Contacts
exports as equivalent to LinkedIn exports for baseline-enhancement purposes.

---

## Required Processing Flow

### Stage 1 — Artifact Recognition
- `artifact_type: apple_contacts_export`
- `intent: baseline_identity_enrichment`
- Downstream consumers: SMS Intelligence, Messages Intelligence, Relationship Intelligence

### Stage 2 — Identity Resolution (match priority)
1. Phone Number (E.164 → 10-digit normalized) — primary key, exact match
2. Name Matching (exact → fuzzy → nickname)
3. Composite: phone + email, phone + company, name + company

### Stage 3 — Classification per record
- `matched_network_entity` — auto-linked to baseline, high confidence
- `probable_network_entity` — name/company match, phone pending
- `unmatched` — no baseline match
- `personal_contact` — family, household, personal
- `service_automated` — short codes, businesses, automated numbers

### Stage 4 — Reconciliation Queue
Unresolved records enter a queue with candidate matches for user confirmation.

### Stage 5 — Persistence
- Phone → entity identity map (`contacts_identity_map.json`)
- Phone → personal exclusion registry (`contacts_exclusion_registry.json`)
- Phone → service number registry

### Stage 6 — SMS Intelligence Enablement
Incoming SMS phone → identity map → known person → RI processing

---

## Files Changed

- `defects/RB-DEFECT-025_...md` — this file
- `system/scripts/contacts_ingest.py` — full identity resolution pipeline
- `system/api/server.py` — XLSX endpoint + artifact classification update
- `system/api/openapi_gpt.yaml` — ingestContactsXLSX operation
