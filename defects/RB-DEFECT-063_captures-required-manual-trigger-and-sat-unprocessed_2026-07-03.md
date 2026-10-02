# RB-DEFECT-063: Captures required a manual trigger phrase and sat unprocessed for days

**Status:** FIXED — instruction change made 2026-07-03. Not yet independently verified through a
live Custom GPT brief generation (per Todd's Fixed → Verified → Closed checklist — see
`EOLMS_SPEC.md`'s pending_verification follow-up).
**Reported by:** Todd — assessing the 2026-07-03 daily brief: "I am concerned about... the
unprocessed JPR recordings - these should be addressed in the intelligence gathering process -
they should be converted to text and processed."

## Problem

Three Just Press Record captures (2026-07-01 17:54, 2026-07-01 18:23, 2026-07-02 08:33) sat in
`system/captures/pending/` across multiple daily briefs with zero intelligence extracted. The Daily
Brief just listed "Captures — N Pending Processing" and a note to say "RB, process my captures."
Todd never said it, so the captures never moved past "pending."

## Root cause

Two separate things, only one of which was actually broken:

1. **Transcription (audio → text) already works.** `capture_ingest.py` runs Whisper locally at 5am
   for every JPR file. Checked the three pending JSONs directly: two have real transcripts (48
   words, 17 words); one came back empty (`transcript` = "", likely too short/silent a recording).
   Not a defect — Whisper ran, this one just had nothing to transcribe.
2. **Intelligence extraction (transcript → signals/mutations) was manual by design.** Per
   `custom_gpt_operational_playbook.md` (pre-fix): "The GPT displays this [Captures Pending
   section] verbatim with the rest of the brief. The user then says 'RB, process my captures' to
   trigger GPT-side intelligence extraction." `custom_gpt_instructions_compact_8k.md` reinforced
   this: "Display it verbatim... Do NOT generate capture intelligence from memory." Nothing in the
   pipeline or the GPT's own instructions ever called `submitCapture` unless Todd said the trigger
   phrase in that exact session — a single missed morning meant captures aged out of relevance
   before anyone looked at them.

## Solution

Changed the GPT's instructions (not the Python pipeline — transcription was already fine) so
capture processing is no longer gated on a trigger phrase:

- **`custom_gpt_instructions_compact_8k.md`** (authoritative control-plane instructions): added an
  autonomous-processing rule — before rendering the Captures section of any brief, call
  `getCapturesPending` and run the per-capture loop (`getCapture` → `submitCapture`, not
  `processAllCaptures` — brief generation is the interactive flow, and per-capture receipts stay
  verifiable) for every item with `transcript_available: true`. Rewrote the "display verbatim" rule
  to require processing first, verbatim-display only for what's still genuinely unprocessable
  (`transcript_available: false`).
- **`custom_gpt_instructions_8k.md`** and **`custom_gpt_operational_playbook.md`** (knowledge
  articles): mirrored the same rule for consistency, explicit that this doesn't wait for "process
  my captures" anymore, and that the existing trigger-phrase routing stays available for on-demand
  re-processing outside of brief generation.
- Captures with a failed/empty transcript are explicitly called out as needing attention, not
  silently dropped or retried.

## Verification status

Instruction files edited and reviewed for internal consistency (compact vs. 8k vs. playbook all
agree, and none contradict the existing `processAllCaptures` restriction). **Not yet verified
live** — the actual test is the next real Custom GPT brief generation: does it autonomously call
`submitCapture` for the two transcribed pending captures instead of just listing them as pending?
That can only be confirmed by watching a real GPT session, not by anything in this repo.
