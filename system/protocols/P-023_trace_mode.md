---
id: P-023
title: Trace mode / developer test logging
script: system/scripts/test_trace.py
cache: (none — writes to system/test_traces/ directly)
reads:
  - operator-supplied trace payload (CLI stdin or POST /test_traces body)
writes:
  - system/test_traces/YYYY-MM-DD-<slug>.md
  - system/test_traces/YYYY-MM-DD-<slug>.json
inputs:
  - name: title
    description: Short human title; used to build the filename slug.
    required: true
  - name: trace_type
    description: One of interface_test, regression, field_defect, smoke_test.
    required: false
  - name: source
    description: Where the trace came from (e.g. "ChatGPT Custom GPT").
    required: false
  - name: steps
    description: Ordered list of prompt/response/tool-call records.
    required: false
  - name: defects
    description: Operator-observed defects to capture alongside the trace.
    required: false
  - name: raw_text
    description: Operator-provided verbatim paste; capped at 256KB.
    required: false
trigger: |
  Operator command "Export this test session for Codex." in the Custom GPT,
  or `python3 system/scripts/test_trace.py --append-from path.json` from a
  shell session.
---

# P-023 — Trace mode / developer test logging

## Purpose

Restore RB 8.0-style trace/developer logging in RB 9.0. The ChatGPT
conversation is not enough to debug RB behavior; Todd needs a way to export a
complete operational test trace to Codex/Claude without reconstructing the
session from screenshots.

This protocol is Phase 14 of `OPERATIONALIZATION.md`. It is the prerequisite
for all further interface testing because everything else depends on the
ability to replay what actually happened.

## Why this exists

The 2026-05-18 interface-persistence test trace (`system/test_traces/
2026-05-18-rb9-interface-persistence-transcript-test.md`) surfaced two
critical defects:

- `D-2026-05-18-001` Statefulness theater — RB answered network strategy and
  top-contact prompts from conversational memory instead of calling the API.
- `D-2026-05-18-002` False verbatim transcript export — RB produced a
  reconstructed approximation while implying a true transcript export.

Both defects required a structured, redacted, replayable trace to debug
properly. P-023 makes that trace a first-class operation.

## What trace mode is NOT

- Hidden chain-of-thought capture. Traces do not record internal reasoning.
- Raw scrollback dump. The ChatGPT interface does not expose verbatim
  scrollback; RB cannot produce one from memory.
- Canonical relationship state. Trace files are debugging evidence, not
  source of truth for the relationship graph.

## What trace mode IS

A structured operational record per test session:

- timestamped user prompts (verbatim, as observed in the interface)
- assistant/RB responses (verbatim, as observed)
- intended tool/operation per step
- actual endpoint hit
- request parameters with secrets redacted
- response status and a compact response summary
- mutation confirmation text when a write occurred
- before/after validation call when a mutation occurred
- operator-observed feedback or defect notes per step
- a defects array referencing D-IDs when applicable

## Redaction

The recorder runs every payload through `test_trace.redact()` before
persistence. It strips:

- any dict key matching the secret-key list (x-api-key, authorization,
  rb_api_key, bearer, password, secret, token, cookie, etc.)
- "Bearer <token>" patterns in free-form strings
- "x-api-key: …" or "RB_API_KEY=…" patterns in any string
- long hex blobs that look like opaque secrets

If a payload still contains a sensitive value after redaction, fix the
recorder, not the trace file.

## File shape

```
system/test_traces/YYYY-MM-DD-<slug>.md     # human-readable
system/test_traces/YYYY-MM-DD-<slug>.json   # structured payload
```

The two test traces already in the folder predate this protocol; they are
treated as legacy_markdown by `list_recent` and remain valid evidence even
without a JSON sibling.

Trace IDs use the same shape as loop and defect IDs: `T-YYYY-MM-DD-NNN`.

## API surface

- `POST /test_traces` — `saveTestTrace`. Required: title. Optional: every
  other field. Returns `{trace_id, captured_at, md_file, json_file,
  redacted, warnings}`.
- `GET /test_traces/recent` — `getRecentTestTraces`. Returns a summary list,
  newest first.

## ChatGPT trigger

The Custom GPT should treat any of these as a trigger:

- "Export this test session for Codex."
- "Save this trace."
- "Capture a test trace for what we just ran."

The GPT assembles the structured payload from the visible conversation —
prompts and responses as displayed, tool/endpoint/params/status from its
own action invocations — and POSTs it. It must not claim to have produced
a verbatim system transcript; if only an operator-provided paste exists,
it goes into `raw_text` and the trace is labeled accordingly.

## Failure modes

- If the title is missing the recorder returns HTTP 400. ChatGPT must ask
  for one before retrying.
- If raw_text exceeds 256KB it is truncated and a warning is appended to
  the record.
- If two traces are written on the same day with the same slug, the second
  is suffixed `-2`, `-3`, etc.

## Why this protocol comes before further intelligence work

Per CLAUDE_HANDOFF: "Trace mode should move ahead of most new intelligence
features because it is the debugging substrate." All later signal/CoS work
depends on being able to capture and replay how the interface behaved.
