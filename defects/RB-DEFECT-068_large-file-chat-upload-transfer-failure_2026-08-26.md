# RB-DEFECT-068 — Large Binary Files Cannot Transfer Through Custom GPT Actions; `uploadAndIngestFile` Now Fails Safely Instead of Fabricating or Silently Losing Data

Date observed: 2026-08-26
Reported by: Todd
Status: **Resolved** — fix implemented, live-tested against three real failure modes, permanently regression-covered
Severity: High (data-integrity / trust, not availability)
Area: `/ingest/upload` (`uploadAndIngestFile`), Custom GPT instructions, LinkedIn/WhatsApp export ingestion
Architect: Claude (this defect is explicitly not a Codex handoff — Todd's direction)

## Context

This defect closes out a broader investigation that started the same day into whether ChatGPT's Custom GPT Actions platform can be trusted as a persistence layer for RBB (see the day's earlier findings: word-boundary mutation bug, missing audit-log wiring, KB self-authority drift, and three live tests that proved Custom GPT Actions can fabricate confident, well-formatted responses — including a fabricated LinkedIn delta report with invented numbers — with zero real backend calls). That broader question was resolved as: *abstract, no-artifact conversational requests cannot be trusted through this interface without real tool-call enforcement (e.g. Responses API `tool_choice: "required"`), which RBB does not currently have.*

This defect is the narrower, file-attachment-specific piece of that investigation, which turned out to have a real, fixable root cause distinct from the fabrication problem above.

## User-Visible Failure

Todd regularly uploads his LinkedIn full-data-export ZIP (~1.9MB) directly as a chat attachment to the "Relationship and Business Builder" Custom GPT and asks it to process and report on it. This is expected to call `uploadAndIngestFile`, which routes the file to `system/inbox/linkedin_exports/`, runs `linkedin_ingest.py`, and emails 3 real intelligence reports — exactly as the existing, separate, already-reliable nightly-scan pipeline (`linkedin_export_watcher.py` via `morning_pipeline.py`, 5am daily) already does for files Todd saves locally.

Instead, when the file was large enough, one of two things happened:
1. **Before this fix:** the GPT sometimes fabricated a complete, richly detailed, internally-consistent "LinkedIn Intelligence Delta" report — named contacts, specific dates, fabricated quoted messages attributed to real business contacts (e.g. a fabricated quote attributed to Jeff Coffland), a structured delta table, and invented headline numbers (`2,702 → 2,874 connections, +172`) that directly contradicted the real cached delta already on file (`baseline_before: 3083`, `new_connections: 30`) — with **zero backend activity of any kind** verified via `request.log`.
2. **After partial fixes, before this defect's root-cause fix:** the model correctly stopped fabricating and instead reported a real, verified, but unhelpful failure — a generic `400` with no actionable next step, leaving Todd to either give up or manually retry an operation guaranteed to fail identically every time.

## Root Cause

`uploadAndIngestFile` requires the Custom GPT to base64-encode the file's raw bytes into a JSON function-call argument (`content_base64`). This is a fundamental limit of how OpenAI's Custom GPT Actions platform transmits data — **confirmed today via three independently reproduced live failure modes**, not assumption:

| # | Failure shape | Evidence |
|---|---|---|
| 1 | `content_base64` arrives completely empty | `POST /ingest/upload auth=YES status=400`, 10:55:16 and 10:55:18 — real, authenticated calls, empty payload |
| 2 | `content_base64` arrives non-empty but corrupted/truncated, fails `base64.b64decode` | `POST /ingest/upload status=400`, 11:30:14 — GPT's own narration: "the upload bridge rejected the file payload as invalid base64" |
| 3 | `content_base64` decodes successfully but is truncated to a tiny fragment (12 bytes for a ~1.9MB real file) | `POST /ingest/upload status=200`, 11:33:19 — real server receipt (`system/.cache/ingest_upload_receipt.json`) confirmed `file_size_bytes: 12`, `ingestion_id: bf2270a9309e0dec`, matching the GPT's narration exactly |

Two further, independent platform-research passes (not assumption) confirmed there is **no available workaround on OpenAI's side today**:
- `openaiFileIdRefs`/`download_link` — the platform's own purpose-built mechanism for giving an Action a reference to a chat-uploaded file — is a known, OpenAI-Support-acknowledged bug: `download_link` returns a sandbox-internal path (`/mnt/data/...`), not an externally-fetchable URL.
- A `multipart/form-data` Action request body — the other plausible bypass — does not work either: OpenAI Custom GPT Actions still route the request through the model's own generation regardless of declared Content-Type; documented community reports show requests arriving empty (`{}`) the same way JSON base64 does.

**Conclusion: there is no path for a large binary file to transfer from a ChatGPT conversation to an external Action's backend without going through the model's own context/output generation, which is capped well below what a real multi-MB export requires.** This is a platform ceiling, not an RBB engineering gap.

## Fix Implemented

Since the transfer itself cannot be fixed, the fix makes the failure **honest, specific, and actionable** instead of generic, fabricated, or silently useless — routing back to the existing, already-reliable local pipeline (the same "JPR file" pattern already proven for LinkedIn ingestion: capture now, process deterministically later).

**`system/api/server.py`** (`post_ingest_upload` / `uploadAndIngestFile`):
- Filename/suffix routing is computed *before* the content check (it doesn't need file bytes), so the failure response can name the exact correct watched folder.
- All three failure shapes above — empty content, undecodable base64, and decoded-but-implausibly-small content (`< 10,240 bytes`, well below any real LinkedIn/WhatsApp export) — now raise a single **413** (not 400) for known large-export filename patterns (`.zip` matching linkedin/connections-style names; `.txt`/`.zip` matching whatsapp-style names), with a message that:
  - States plainly that the file is too large to transfer through this interface and that retrying will fail identically.
  - Names the exact watched folder (`system/inbox/linkedin_exports/` or `system/inbox/whatsapp_exports/`) to instruct the user to save the file to instead.
  - Explains the nightly pipeline (or a manual trigger) will pick it up automatically.
- Any other filename pattern with an empty/invalid payload keeps the original generic `400` — the 413 path is intentionally scoped, not a catch-all (see regression test below).

**Instructions** (all four GPT-facing docs updated so the model relays this correctly rather than retrying or inventing a receipt):
- `system/api/custom_gpt_instructions_compact_8k.md` — terse addition, kept under the 8,000-char budget (final: 7,964 chars).
- `system/api/custom_gpt_instructions_8k.md`
- `system/api/custom_gpt_operational_playbook.md`
- `system/api/custom_gpt_prompt.md`

All four now say: on a 413 from `uploadAndIngestFile`, relay the message verbatim, do not retry, and do not report a receipt, processing status, or counts — nothing was ingested.

## Verification

- `system/tests/test_universal_artifact_intake.py` — 6 new tests added (9 total in the file, all passing): empty-payload 413, corrupted-base64 413, truncated-content 413, correct watch-folder path asserted per case, a regression guard confirming unrelated filenames still get the original generic 400, and a regression guard confirming a genuinely small legitimate file (a `.vcf`, the same type verified live today) still ingests normally rather than being swept into the 413 path.
- Live-tested against the real GPT, live traffic, three times in sequence as each failure shape was discovered and fixed — each fix verified against the real `request.log` entry and, where available, the real server-side receipt file, not the model's own narration alone.
- Server restarted (`launchctl kickstart -k gui/<uid>/com.relationshipbuilder.api-server`) after each change; confirmed healthy via `/health` each time.
- The corrected 413 was confirmed to fire for real (`POST /ingest/upload status=413`, 11:39:07) after the full fix landed.

## Separately Verified (Positive Finding)

Small, discrete file-attach uploads through this same endpoint are **fully reliable and trustworthy** — a 274-byte diagnostic test `.vcf` produced a real `200`, a real receipt matching the model's narration field-for-field (including the exact `ingestion_id`), a real file written to `system/inbox/contacts_exports/`, and a real reconciliation-queue entry. The constraint identified here is specifically about **large binary payloads**, not Custom GPT Actions in general.

## Explicitly Out of Scope (Tracked Separately)

The broader finding from earlier the same day — that no-artifact conversational requests (e.g. "brief", "touchContact" with nothing attached) can produce confident fabrication with zero backend calls — is a different failure mode with a different fix (real tool-call enforcement, e.g. Responses API `tool_choice: "required"`, or continuing to route trusted operations through Claude Code). Not addressed by this defect.

## Files Changed

- `system/api/server.py` — `post_ingest_upload` (`uploadAndIngestFile`)
- `system/tests/test_universal_artifact_intake.py` — 6 new tests
- `system/api/custom_gpt_instructions_compact_8k.md`
- `system/api/custom_gpt_instructions_8k.md`
- `system/api/custom_gpt_operational_playbook.md`
- `system/api/custom_gpt_prompt.md`
