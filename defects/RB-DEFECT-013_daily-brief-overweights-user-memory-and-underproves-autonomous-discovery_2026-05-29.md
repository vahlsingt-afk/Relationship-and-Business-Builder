# RB-DEFECT-013 — Daily Brief Overweights User Memory And Under-Proves Autonomous Discovery

**Date opened:** 2026-05-29  
**Severity:** High  
**Priority:** High  
**Category:** Daily Brief / Autonomous Discovery / Provenance Rendering / CoS Trust Surface  
**Status:** Resolved — RB 9.53 (2026-06-04)  
**Sprint target:** RB 9.24 live wiring / Daily Brief trust hardening

## Summary

The 2026-05-29 Daily Brief rendered in ChatGPT sounded strategically useful but
surfaced mostly known operator context, manually supplied strategic memory, and
existing active-thread state. It did not clearly answer the user's core value
question:

> What is RB finding besides my input that makes it valuable?

The canonical brief payload contains provenance, grounding, freshness,
display-policy, and an `autonomous_discovery_evidence` section, but the rendered
ChatGPT response collapsed those controls into a polished executive memo. The
result felt like RB was repeating Todd's own thinking back to him.

## Observed Failure

The ChatGPT-rendered brief led with or prominently emphasized:

- Foods Connected
- PAR leadership interpretation
- restaurant technology thesis validation
- Toast retention / loyalty framing
- Olo / AI ROI / Sterling Douglass / David Mann / Hospitality Table themes
- Global Payments SMS context
- Ish Singh / Ashwin introduction
- LinkedIn retention content strategy

Much of this was valuable context, but it appeared to be operator-supplied,
manual-context, durable memory, or prior-session synthesis. The answer did not
clearly separate:

- net-new system discovery
- fresh connected-source detections
- system-inferred operational changes
- repeated known state
- stale/manual memory

## Why This Is A Product Defect

RB's Daily Brief must prove that it is doing more than organizing Todd's own
thoughts. A Chief of Staff operating system becomes valuable when it surfaces:

- things the user did not know
- things the user forgot
- changed states
- contradictions to existing assumptions
- external signals that require action
- timing windows created by live sources
- source-health limitations that change confidence

If the brief mostly restates the user's own strategy, it may sound intelligent
while failing the operating-system value test.

## What RB Actually Found On 2026-05-29

From the canonical Codex workspace, RB did find some autonomous signal:

- fresh email/calendar scan
  - 44 emails scanned
  - 6 calendar items scanned
  - 103 passive email/snippet items reviewed
  - 65 noisy items suppressed
- sent-loop verification
  - multiple outbound emails classified as `outbound_sent_awaiting_response`
  - examples included Elizabeth Jenswold, Ashwin/Ish introduction, Foods
    Connected, and other follow-up/scheduling threads
- passive email intelligence
  - 9 newsletters scanned
  - 10 relevant headlines extracted
  - 1 deep-dive candidate triggered
- calendar-derived prep
  - Hospitality Table and Ryan/Todd calendar context detected as prep-worthy
- source-health truth
  - LinkedIn messaging, LinkedIn engagement, own-post data, and some
    market/operator lanes were stale or missing

These signals are real, but the rendered brief did not make them the clear
answer to "what did RB find on its own?"

## Root Cause Hypothesis

The canonical payload has the raw ingredients:

- `grounding`
- `freshness`
- `provenance.source_class`
- `display_policy`
- `novelty`
- `autonomous_discovery_evidence`
- source-health rows

But the rendering layer and Custom GPT instructions still allow a narrative
brief to:

- promote manual/user-provided context above fresh system-discovered deltas
- omit or soften `manual_user_provided` labels
- treat durable thesis memory as if it were newly discovered evidence
- bury autonomous discovery inside a long brief instead of presenting it as the
  first value proof
- include broad macro synthesis without source-specific "new since last run"
  proof

## Required Behavior

The Daily Brief must open, after resource verification, with:

```text
What RB Found Without You Telling It
```

This section should include only items that are:

- `system_detected`
- connected-source-backed or local-artifact-backed
- fresh or explicitly freshness-caveated
- net-new, changed-state, contradiction, or action-relevant

Manual memory may appear later, but should be demoted unless it changed
overnight or is needed to interpret a system-detected event.

## Acceptance Criteria

A successful fix must:

1. Add explicit fields or rendering rules for:
   - `new_to_rb`
   - `new_to_todd_likely`
   - `source_discovered`
   - `user_provided_context`
   - `changed_state`
2. Add or promote a top rendered section:
   - `What RB Found Without You Telling It`
3. Suppress or demote `manual_user_provided` / `operator_memory` items unless
   they are:
   - overdue
   - contradicted by fresh source data
   - newly actionable because of a system-detected event
   - required context for a fresh external signal
4. Split the brief into:
   - Net-new external discovery
   - Operational changes from connected sources
   - Known-state reminders
5. Preserve grounding/freshness labels in ChatGPT-rendered output.
6. Add regression tests proving a brief with many manual active-thread items
   still leads with autonomous discoveries when they exist.
7. Add regression tests proving ChatGPT instructions do not allow replacing
   canonical source-backed sections with a loose executive-summary essay.

## Suggested Implementation

- Extend `daily_brief.py` display policy / provenance handling so each canonical
  item carries explicit discovery-value fields.
- Create a deterministic `autonomous_discovery_value` scorer:
  - high: fresh connected-source discovery, changed state, contradiction,
    action-needed
  - medium: fresh system inference from connected source
  - low: durable memory, manual context, repeated thesis, stale market row
- Render "What RB Found Without You Telling It" before Morning Command Center.
- Add a "Known-State Reminders" section for high-priority manual context.
- Update `custom_gpt_prompt.md` and `custom_gpt_instructions_8k.md` to forbid
  replacing the canonical brief with an executive summary.

## Definition Of Done

The user should be able to ask:

> What did RB find today that I did not explicitly tell it?

and receive a compact source-backed answer with:

- fresh detections
- source names
- changed state
- why it matters
- what RB did or proposed
- what remains stale/missing

The answer must not rely primarily on known active threads, prior thesis memory,
or user-provided context.
