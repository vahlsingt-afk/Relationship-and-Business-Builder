---
id: P-020
title: Conversation artifact ingestion
script: partial system/scripts/source_watch.py
cache: partial system/.cache/source_watch.json; planned system/.cache/conversation_artifacts.json
reads:
  - system/source_watch.yaml
  - system/inbox/conversation_artifacts/
  - system/baseline_index.json
  - system/active_threads.yaml
  - system/loop_ledger.md
  - system/briefs/
writes:
  - system/.cache/source_watch.json
  - system/briefs/*.md
  - system/loop_ledger.md
  - system/baseline_index.json
  - system/active_threads.yaml
  - system/.cache/conversation_artifacts.json
inputs:
  - name: source_dir
    description: Folder containing exported transcripts, recaps, notes, and chat logs.
    required: false
  - name: since
    description: Date/time lower bound for daily processing.
    required: false
  - name: review_mode
    description: If true, produce proposed mutations but do not write canonical files.
    required: false
trigger: daily before the morning brief; on-demand after uploading/exporting transcripts; after high-value meetings
---

# P-020 — Conversation Artifact Ingestion

## Purpose

Turn meeting transcripts, AI notetaker recaps, and chat logs into measured relationship intelligence before the daily brief runs.

This is not note storage. This is the layer that tells Todd what changed because of conversations that already happened.

## Supported sources

Initial supported classes:

- Fathom transcripts and recap emails.
- Zoom native transcripts.
- Zoom chat logs.
- Otter / Fireflies / Teams / Meet-style notetaker exports.
- Plain `.txt` / `.md` meeting notes.
- PDF or `.docx` meeting summaries when exported from a notetaker.
- Manual transcript pastes from Fathom, Zoom, or other notetakers when a direct
  export is not available.

Future implementation should treat each parser as an adapter that emits the same normalized artifact shape.

## Source folders

Recommended local inbox:

```text
system/inbox/conversation_artifacts/
  fathom/
  zoom/
  otter/
  fireflies/
  teams/
  manual/
```

`system/inbox/` is git-ignored. Raw transcripts and chat logs must not be committed.

If an artifact becomes canonical evidence, preserve only a source pointer in the generated Interaction Brief unless Todd explicitly chooses to preserve the artifact in `system/briefs/_artifacts/`.

## Current watch configuration

First working local scanner:

```text
system/source_watch.yaml
system/scripts/source_watch.py
system/.cache/source_watch.json
```

Todd's active watch setup:

- Zoom native saves: `/Users/toddvahlsing/Documents/Zoom`
- RB Zoom drop folder: `system/inbox/conversation_artifacts/zoom/`
- Fathom downloads: `/Users/toddvahlsing/Downloads`
- RB Fathom drop folder: `system/inbox/conversation_artifacts/fathom/`

The watch scanner inventories candidate files, hashes, freshness, artifact type, and routing hints. It does not parse transcript content or mutate RB state yet.

## Manual paste and link behavior

Fathom share links, notetaker URLs, and meeting-recording links are not reliable
canonical input by themselves unless RB has an authenticated connector or
export path for the underlying content.

Rules:

- If the user provides only a Fathom/Zoom/notetaker URL and RB cannot fetch the
  transcript, say that the content was not ingested and ask for the transcript,
  recap export, downloaded file, screenshot, or watched-folder export.
- If the user pastes a transcript into the chat, treat it as a conversation
  artifact with source type `fathom_manual_paste`, `zoom_manual_paste`, or
  `manual_transcript_paste`.
- Pasted transcripts must go through the same RI screen as uploaded files:
  identify relationship intelligence, propose mutations, and state whether
  anything was actually persisted.
- Do not imply RB remembered a pasted transcript unless a write endpoint/action
  was called and post-validation succeeded.

## Normalized artifact shape

Every parser should normalize into:

```json
{
  "artifact_id": "stable id or file hash",
  "source_type": "fathom|zoom|otter|fireflies|teams|manual",
  "source_path": "system/inbox/conversation_artifacts/...",
  "title": "Meeting title",
  "meeting_at": "YYYY-MM-DDTHH:MM:SS",
  "participants": [
    {"name": "Person Name", "email": "person@example.com", "company": "Company"}
  ],
  "text_available": true,
  "chat_available": true,
  "summary": "...",
  "action_items": [],
  "raw_confidence": "high|medium|low"
}
```

## Date-of-intelligence rule

Conversation artifacts must be anchored to the meeting/call/message date, not the upload date.

Required fields:

- `meeting_at` or equivalent source event timestamp.
- `captured_at` for when RB discovered or scanned the file.
- `meeting_at_confidence` (`high`, `medium`, `low`).
- `date_source` such as transcript metadata, Zoom folder name, chat timestamp, file metadata, calendar match, or Todd confirmation.

Rules:

- A Fathom/Zoom transcript uploaded today but recorded last month belongs to last month's relationship timeline.
- Daily brief inclusion should distinguish "historical artifact newly uploaded" from "fresh relationship signal."
- Do not update `last_touch`, DRR recency, active-thread momentum, or "last 24h signals" from upload time.
- If the call date cannot be determined, produce a reconciliation prompt before proposing any write.

## Signal extraction

For each artifact, extract:

- People and companies mentioned.
- Known contacts matched to baseline.
- Unmatched people worth review.
- Strategic relationship signals:
  - inbound interest
  - positioning resonance
  - buying/advisory/partnership surface area
  - role or job opportunity movement
  - operator pain
  - technology stack dissatisfaction
  - implementation friction
  - budget/timing signals
  - decision-maker or influencer names
  - emotional tone changes
- Action items and promised follow-ups.
- Existing loops closed by the conversation.
- New loops opened by the conversation.
- Active threads touched or created.
- Suggested DRR relevance changes.

## Action item extraction without becoming a task manager

RB is not a generic to-do list. Conversation artifacts often contain many
small tasks, but the CoS function should promote only obligations that affect
relationship momentum, opportunity movement, future prep, trust, or strategic
follow-through.

Classify every transcript action item as one of:

- `relationship_obligation` — promised follow-up, intro, thank-you, check-in,
  answer owed, or relationship repair.
- `opportunity_obligation` — proposal, scope, pricing, resume, availability,
  stakeholder follow-up, commercial next step, or job-process movement.
- `meeting_prep` — preparation needed before a booked or likely future call.
- `daily_brief_injection` — item that should appear in a future daily brief
  because timing, risk, or opportunity is material.
- `admin_task` — low-strategic housekeeping. Capture only if explicitly tied to
  an active thread or user-requested workflow.
- `suppress` — generic notes, FYIs, non-commitments, vague "think about it"
  items, or tasks with no relationship/action implication.

Promotion rules:

- Open or propose a loop only when the item has a named owner/counterparty,
  a due date or reasonable follow-up window, and a relationship/opportunity
  consequence.
- If the transcript contains a future meeting, create or propose a prep loop
  and daily-brief injection when the meeting is strategically material.
- If the user promised to send something, answer something, introduce someone,
  schedule something, follow up, or capture an outcome, propose a loop.
- If the other party promised an action, track it as `waiting_on` inside the
  active thread/opportunity state rather than making it a generic Todd task.
- If an item is useful but low priority, place it under `monitor` or `ignore`
  rather than opening a loop.

Each promoted action item should carry:

```json
{
  "action_type": "relationship_obligation|opportunity_obligation|meeting_prep|daily_brief_injection|admin_task|suppress",
  "owner": "Todd|counterparty|unknown",
  "counterparty": "Person or company",
  "description": "...",
  "target_date": "YYYY-MM-DD or null",
  "source_quote_or_summary": "...",
  "linked_thread_id": "T-... or null",
  "recommended_disposition": "act_today|monitor|ask_todd|ignore",
  "loop_recommended": true,
  "daily_brief_injection_recommended": false,
  "confidence": "high|medium|low"
}
```

Output rule:

- The response should include `Priority tasks captured/proposed`, not a raw
  action-item dump.
- The daily brief should surface only the subset that affects today's focus,
  a future meeting, or an active thread.
- Suppressed action items should be counted with a reason so RB proves it did
  not miss them.

## Persistence status contract

Every RI-bearing conversation artifact response must include one explicit
persistence status:

- `not_persisted` — useful analysis was produced, but no canonical RB write
  occurred.
- `proposed_write_pending_confirmation` — RB prepared review-first mutations
  but is waiting for approval before touching canonical state.
- `persisted` — canonical write actions were called and post-validation
  confirmed the projected state.

If status is `not_persisted`, the assistant must not say or imply that RB has
"captured," "remembered," or "updated" the relationship. It may say "RB should
capture this" and list the proposed writes.

## Daily brief integration

Before the daily brief runs, process conversation artifacts from the prior day and produce a "Conversation Signals Since Yesterday" section.

This section should include only high-signal items. It should answer:

- What changed?
- Why does it matter?
- Who is affected?
- What action should Todd take?
- What should be ignored?
- Does this create or update a loop, active thread, contact, or card?

Low-value transcripts should be summarized as suppressed/no-action, not silently ignored.

## Review-first mutation rule

Default behavior should be review-first:

1. Parse artifacts.
2. Produce proposed Interaction Briefs, loops, baseline changes, and active-thread updates.
3. Show a compact review list.
4. Apply only approved mutations.

Auto-write may be allowed later for low-risk changes such as `last_touch` updates when the participant match is exact and the meeting is clearly with Todd.

## Canonical field test: PerfectHire / Fathom

Trace: `T-2026-05-19-005`.

Input:

- Fathom link:
  `https://fathom.video/share/94njMJ56SsKKmCMXjwS33zbuXdWxeJw6`
- Manual transcript paste for `Todd <> PerfectHire - QSR Platform Review - May
  19`, a 52-minute meeting with Olivia Nielsen and Max Holmes from
  PerfectHire.

Expected extraction:

- Olivia Nielsen / PerfectHire:
  - high-signal warm strategic relationship
  - likely CEO introduction path to Matt Chalzi
  - potential advisory or consulting opportunity
- Max Holmes / PerfectHire CTO:
  - medium-high new technical/product relationship
  - positive engagement around scheduling, POS integration, employee loyalty,
    retention, and manager performance data
- Matt Chalzi / PerfectHire CEO:
  - pending introduction from Olivia
  - prepare strategic positioning before the CEO conversation
- PerfectHire:
  - discovery/advisory/consulting opportunity
  - themes: QSR scheduling, hiring workflow, employee availability,
    retention, call-off reduction, POS integration, manager prompts, loyalty,
    ROI proof, ICP, and restaurant GTM

Expected proposed writes:

- Create or update contact records for Olivia Nielsen, Max Holmes, and Matt
  Chalzi as review-first mutations when not already matched.
- Open or update an active thread for `PerfectHire / QSR Platform Review`.
- Open a loop for the pending Matt Chalzi introduction.
- Open a loop for the follow-up email or next-step prep if not already sent.
- Produce an Interaction Brief or RI event anchored to the meeting date, not
  the transcript upload date.
- Produce a trace/log showing extracted, proposed, skipped, and suppressed
  items.

This test fails if the assistant only summarizes the transcript and does not
state persistence status.

## Output contract

For each qualifying conversation:

- Write one Interaction Brief to `system/briefs/`.
- Update `last_touch` for matched participants when evidence is clear.
- Promote LMI to LKI only when the exchange is bidirectional and substantive.
- Open loops for real obligations.
- Open or update active threads for strategic opportunities.
- Record source provenance.

For each non-qualifying artifact:

- Record suppression reason in the artifact cache.
- Do not create an IB.

## Failure modes

- **Filename does not contain date.** Use transcript metadata when available; otherwise mark `meeting_at` confidence low and ask for confirmation.
- **Participant names are ambiguous.** Do not mutate baseline; flag for review.
- **Notetaker summary is too generic.** Preserve as low-confidence and avoid strategic conclusions.
- **Chat log contains names not in transcript participants.** Treat chat names as possible participants but require confirmation before baseline changes.
- **Duplicate exports.** Deduplicate by source URL, meeting timestamp + title, or file hash.

## Chief-of-staff standard

The feature succeeds when Todd does not have to ask, "Was there anything important in yesterday's calls?"

RB should already know, and the morning brief should say so with restraint.
