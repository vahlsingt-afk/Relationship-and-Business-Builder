# Claude Sprint Brief — RB 9.24B Autonomous Discovery Daily Brief Proof

**Prepared:** 2026-05-29  
**Prepared by:** Codex  
**Audience:** Claude Code / next RB implementation pass  
**Sprint posture:** Daily Brief trust-surface correction. Fix the user-visible value proof before adding more live Custom GPT action wiring.

---

## Why This Sprint Exists

The 2026-05-29 morning pipeline now runs and publishes successfully, but the
ChatGPT-rendered Daily Brief failed the user's value test.

User feedback:

> My issue is this surfaced almost only user inputed data — so it is telling me
> many things I already know — what is RB finding besides my input that makes it
> valuable?

The rendered brief sounded strategically useful but mostly elevated known
operator memory, manual context, prior strategic thesis, and active-thread
state. It did not clearly prove what RB found autonomously from connected
sources or local artifacts.

This sprint closes:

```text
defects/RB-DEFECT-013_daily-brief-overweights-user-memory-and-underproves-autonomous-discovery_2026-05-29.md
```

---

## Current Baseline

Canonical workspace:

```text
/Users/toddvahlsing/Documents/Claude/Projects/Relationship Builder/
```

Current verified state before this sprint:

```bash
python3 -m pytest system/tests -q
# 643 passed, 0 failed
```

Morning pipeline was restored in RB 9.24:

```bash
python3 system/scripts/morning_pipeline.py --date 2026-05-29
# morning pipeline: PASS
```

Delivery check:

```bash
python3 system/scripts/task_delivery_check.py --live --json
# automated_fail_count: 0
```

Important warnings still present:

- LinkedIn messaging missing/export-dependent
- LinkedIn session feed stale/missing
- LinkedIn own posts/engagement stale
- LaunchAgent prior exit status warning

Those warnings are fine as caveats. They must not be converted into invented
market confidence.

---

## Failure Specimen

The ChatGPT-rendered brief led with:

- Foods Connected as the highest-probability W-2 opportunity
- PAR leadership changes
- restaurant-tech market validating Todd's thesis
- Iran / instability macro framing
- Toast loyalty report / retention thesis
- Olo / Sterling Douglass / David Mann / Hospitality Table / LinkedIn post
  themes
- Global Payments SMS context
- Ish Singh / Ashwin introduction
- LinkedIn retention content strategy

Some of this may be useful, but much of it was known or user-supplied context.
It did not answer:

```text
What did RB find without Todd telling it?
```

Do not solve this by making the prose harsher. Solve it by changing ordering,
discovery-value scoring, provenance labeling, and tests.

---

## What RB Actually Found On 2026-05-29

From the canonical Codex workspace, RB did autonomously find or verify:

- Fresh email/calendar/direct-comms source state.
- 44 emails scanned.
- 6 calendar items scanned.
- 103 passive email/snippet items reviewed.
- 65 noisy email items suppressed.
- 9 newsletters scanned.
- 10 relevant headlines extracted.
- 1 deep-dive candidate triggered.
- outbound sent-loop evidence classified as `outbound_sent_awaiting_response`.
- examples included Elizabeth Jenswold, Ashwin/Ish intro, Foods Connected, and
  other scheduling/follow-up threads.
- Hospitality Table and Ryan/Todd calendar context detected as prep-worthy.
- stale/missing source truth for LinkedIn messaging, own-post engagement,
  social feed, and some market/operator lanes.

This is the real autonomous value. The brief should make this obvious before
replaying strategic context.

---

## Non-Negotiable Invariant

The Daily Brief must distinguish:

```text
RB found this
RB inferred this from fresh source data
RB remembered this from Todd
RB repeated a known state because it is now overdue/actionable
RB cannot prove this because the source is stale/missing
```

The user should never have to ask whether the brief is discovery or memory.

---

## Required Product Shape

After `resource_verification_and_freshness_status`, the rendered brief should
open with:

```text
What RB Found Without You Telling It
```

This section should include only high-discovery-value items:

- `system_detected`
- fresh connected-source or local-artifact backed
- changed-state, contradiction, newly actionable, or net-new discovery
- explicit source refs
- clear "why this matters"

Then render:

```text
Operational Changes From Connected Sources
```

Then:

```text
Known-State Reminders
```

Manual/operator-memory items belong in Known-State Reminders unless fresh source
data changed them today.

---

## Implementation Requirements

### D1 — Discovery Value Fields

Add explicit fields to surfaced Daily Brief items or their provenance/display
policy:

```json
{
  "new_to_rb": true,
  "new_to_todd_likely": true,
  "source_discovered": true,
  "user_provided_context": false,
  "changed_state": true,
  "autonomous_discovery_value": "high|medium|low"
}
```

Recommended meaning:

- `new_to_rb`: first seen or materially changed since last known run.
- `new_to_todd_likely`: source-discovered and not merely a repetition of
  operator-entered memory.
- `source_discovered`: came from connected account, fetched source, local cache,
  or deterministic system scan.
- `user_provided_context`: came from active threads, cards, loops, strategic
  memory, manual intake, or user-pasted context.
- `changed_state`: RB detected a state transition, waiting status, due/overdue
  change, source freshness change, event, or proposed mutation.
- `autonomous_discovery_value`:
  - `high`: fresh source discovery, changed state, contradiction, or immediate
    action relevance.
  - `medium`: fresh system inference from connected source.
  - `low`: known memory, manual context, stale market row, repeated thesis.

### D2 — Section Ordering

Ensure canonical section order puts autonomous discovery proof before narrative
analysis and before manual active-thread reminders.

Recommended new/renamed sections:

```text
what_rb_found_without_you_telling_it
operational_changes_from_connected_sources
known_state_reminders
```

Existing `autonomous_discovery_evidence` can either be renamed or remain as an
internal section, but the rendered Markdown and Custom GPT instructions must use
language the user understands.

### D3 — Manual Memory Demotion

Demote `manual_user_provided` / `operator_memory` items unless one of these is
true:

- it is overdue
- it is contradicted by fresh source data
- it became newly actionable due to a system-detected event
- it is required context for a fresh external signal

This should be deterministic, not stylistic.

### D4 — Prompt / GPT Rendering Guard

Update:

```text
system/api/custom_gpt_prompt.md
system/api/custom_gpt_instructions_8k.md
```

The Custom GPT must not replace canonical sections with a generic executive
summary. It must preserve:

- grounding
- freshness
- discovery-value sectioning
- source refs
- stale/missing source caveats

Add explicit instruction:

> If the user asks for the Daily Brief, render "What RB Found Without You
> Telling It" before known-state reminders. Do not lead with manual active
> threads unless the API marks them as changed_state or overdue.

### D5 — Tests

Add focused regression tests. Suggested file:

```text
system/tests/test_daily_brief_autonomous_discovery.py
```

Minimum tests:

1. A brief with many manual active-thread items and several source-discovered
   items must render source-discovered items first.
2. `manual_user_provided` items are demoted to Known-State Reminders unless
   overdue or changed by fresh evidence.
3. Every autonomous discovery item includes source refs and discovery-value
   fields.
4. ChatGPT prompt/instructions contain the required "What RB Found Without You
   Telling It" rendering rule.
5. The canonical response contract forbids replacing the canonical brief with a
   loose executive-summary essay.
6. A 2026-05-29-style fixture shows:
   - email scan counts
   - newsletter/headline harvest
   - sent-loop verification
   - prep-worthy calendar items
   - stale LinkedIn source caveats
   before known strategic memory.

---

## Verification Commands

Run:

```bash
python3 -m pytest system/tests -q
python3 system/scripts/daily_brief.py --cache --date 2026-05-29
python3 system/scripts/morning_pipeline.py --date 2026-05-29
python3 system/scripts/task_delivery_check.py --live --json
```

Expected:

- all tests pass
- brief regenerates
- pipeline passes
- delivery check has `automated_fail_count: 0`
- rendered `system/today.md` opens with source verification then autonomous
  discovery proof before known memory

---

## Definition Of Done

RB-DEFECT-013 is closed when:

- The brief clearly answers "what did RB find without me telling it?"
- Manual/user-provided context is labeled and demoted unless newly actionable.
- Known-state reminders no longer dominate the first viewport.
- The Custom GPT rendering contract forbids replacing canonical sections with a
  generic executive-summary memo.
- Regression tests cover the failure mode.
- Full suite remains green.

---

## Product Bar

The goal is not a prettier Daily Brief.

The goal is that Todd can see, within the first minute:

```text
RB checked these sources.
RB found these new things.
RB changed/proposed these states.
RB remembered these older things only where they matter.
RB cannot prove these lanes because they are stale.
```

Anything else is still too close to helpful prose.
