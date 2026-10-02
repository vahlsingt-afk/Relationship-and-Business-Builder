# RB-DEFECT-024: LinkedIn Ingest Observability / GPT Upload Bridge

**Date:** 2026-06-05  
**Severity:** High  
**Classification:** Execution Verification / Intelligence Ingestion  
**Status:** Resolved  

---

## Investigation Result

All 9 LinkedIn export ZIPs in `system/inbox/linkedin_exports/` are registered
in the watcher manifest and were processed on 2026-05-29. The ingest pipeline,
hash-manifest, and baseline update chain all functioned correctly.

**The file uploaded to the Custom GPT on June 5 was never written to
`system/inbox/linkedin_exports/`.** It existed only as a GPT conversation
attachment. The watcher monitors the filesystem. There was no bridge between
the two.

**Failure mode confirmed:** Mode 1 — upload received by GPT, ingest chain
never triggered because no file reached the watched directory.

---

## Root Cause

The ingest pipeline requires files to be dropped into specific inbox directories:
- `system/inbox/linkedin_exports/`
- `system/inbox/whatsapp_exports/`
- `system/inbox/contacts_exports/`

The Custom GPT conversation interface is a separate surface. When a user uploads
a file to the GPT, the GPT can read its content but cannot write to the local
filesystem. The `/linkedin/ingest` API endpoint takes a local `path`, not file
content. There was no endpoint that accepted a file's bytes and wrote them to
inbox.

**The drop-to-filesystem requirement was a developer workflow, not a user
workflow.** The user workflow is: upload to GPT → system processes it.

---

## What Was Missing

1. No API endpoint to receive file content from the GPT and write it to inbox
2. No processing receipt surfaced in the daily brief
3. No way for the system to report "ingested" vs "not ingested" for a GPT upload

---

## Remediation

### New endpoint: `POST /ingest/upload` (`uploadAndIngestFile`)

Accepts:
- `filename` — original filename with extension (used for pipeline routing)
- `content_base64` — full file bytes encoded as base64
- `dry_run` — preview without mutating baseline

Routing by filename/suffix:
- LinkedIn export ZIP → `inbox/linkedin_exports/` → `linkedin_export_watcher`
- WhatsApp `.txt`/`.zip` → `inbox/whatsapp_exports/` → `whatsapp_ingest`
- Contacts `.vcf`/`.csv` → `inbox/contacts_exports/` → `contacts_ingest`
- Ambiguous ZIP → peek inside to determine type

Returns a **processing receipt**:
```json
{
  "ingestion_id": "abc123def456",
  "received_at": "2026-06-05T09:00:00Z",
  "filename": "Complete_LinkedInDataExport_06-05-2026.zip",
  "file_written": "system/inbox/linkedin_exports/...",
  "pipeline_run": "linkedin",
  "ingest_result": {
    "new_connections": 12,
    "company_changes": 4,
    "role_changes": 7,
    "disconnections": 1
  }
}
```

Receipt is persisted to `system/.cache/ingest_upload_receipt.json` and
surfaced in the daily brief's Source Intelligence Collection Status header.

### GPT instruction update required

The GPT system instructions should direct the GPT to call `uploadAndIngestFile`
immediately when any file is uploaded to the conversation, without asking the
user what to do with it. Routing is determined by filename — the GPT should
not prompt for intent.

### Daily brief

Upload receipt appears in the Source Intelligence Collection Status section:
```
Upload receipt: Complete_LinkedInDataExport_06-05-2026.zip — linkedin pipeline
— received 2026-06-05T09:00:00 — ID abc123def456
Delta: 12 new · 4 company changes · 7 role changes · 1 disconnections
```

---

## Files Changed

- `defects/RB-DEFECT-024_...md` — this file
- `system/api/server.py` — `FileUploadIn` model + `POST /ingest/upload` endpoint
- `system/api/openapi_gpt.yaml` — `uploadAndIngestFile` operation + `FileUploadIn` schema
- `system/scripts/daily_brief.py` — upload receipt in Source Intelligence Collection Status
- `system/tests/test_wiring_gaps.py` — op count updated to 27
