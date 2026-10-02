# Claude Follow-Up — Canonical Daily Brief Default

**Date:** 2026-05-19  
**Prepared by:** Codex + Todd after live Custom GPT daily brief testing  
**Priority:** High  

## Why This Exists

Todd tested the default prompt:

```text
show me the daily briefing
```

The output improved after the latest prompt/schema work, but it still did not behave as RB's canonical Chief-of-Staff daily brief by default.

The key product decision:

```text
The user should not have to say "make it CoS-level."
```

The bare daily briefing command must produce the canonical CoS operating brief.

## Test Traces To Read

```text
system/test_traces/2026-05-19-default-daily-brief-drifted-from-cos-canonical-response.md
system/test_traces/2026-05-19-default-daily-brief-drifted-from-cos-canonical-response.json

system/test_traces/2026-05-19-default-daily-brief-improved-but-still-missing-canonical-cos-sections.md
system/test_traces/2026-05-19-default-daily-brief-improved-but-still-missing-canonical-cos-sections.json
```

The first trace shows the daily brief drifting into strategic industry/news commentary.

The second trace shows improvement: relationship/strategic priorities came first and industry content was relevant, but canonical sections were still missing.

## What Improved

The second daily brief test:

- led with relationship/strategic priorities
- treated Ish Singh / Maho as a high-upside strategic conversation
- included restaurant-tech industry context that was relevant to Todd's world
- tied industry items to Todd's thesis more clearly than prior versions
- gave some recommended actions

This is progress.

## What Still Failed

The response still missed the canonical RB daily-brief contract:

- no visible grounding labels:
  - `system_detected`
  - `inferred`
  - `manual_user_provided`
  - `stale_source_limited`
- no stale-source warnings
- no explicit last-24h relationship signal section
- no loop/overdue obligation section
- no "what to ignore"
- no reconciliation prompts
- no clear separation between API-detected facts and assistant inference
- industry section was still overweight relative to the relationship/action layer

## Product Decision

Industry news is allowed.

But it must be:

- condensed
- relevant to Todd's industry, network, active threads, job search, consulting pipeline, or strategic positioning
- fresh enough to matter
- tied to an action, monitoring decision, relationship implication, or opportunity

Random industry news is noise.

Repeated day-to-day narrative is noise unless something materially changed.

Industry context should never be the spine of the daily brief.

## Recommended Architecture Change

Do not rely only on Custom GPT prompt instructions for daily brief structure.

The API should return a canonical rendered CoS brief structure, or at minimum an explicitly ordered `canonical_sections` object, so the GPT is summarizing a structure rather than inventing one.

Suggested response shape in `daily_brief.py`:

```json
{
  "canonical_brief": {
    "section_order": [
      "signal_freshness",
      "top_priorities_today",
      "last_24h_relationship_signals",
      "loops_and_obligations",
      "recommended_actions",
      "what_to_ignore",
      "reconciliation_prompts",
      "condensed_industry_context"
    ],
    "sections": {
      "signal_freshness": [],
      "top_priorities_today": [],
      "last_24h_relationship_signals": [],
      "loops_and_obligations": [],
      "recommended_actions": [],
      "what_to_ignore": [],
      "reconciliation_prompts": [],
      "condensed_industry_context": []
    }
  }
}
```

Each item should carry:

```json
{
  "title": "",
  "summary": "",
  "why_it_matters": "",
  "recommended_action": "",
  "grounding": "system_detected | inferred | manual_user_provided | stale_source_limited",
  "freshness": "fresh | stale_source_limited | manual_context | unknown",
  "source_refs": [],
  "confidence": "high | medium | low"
}
```

## Canonical Default Section Order

For any request like:

```text
show me the daily briefing
daily brief
what does today look like
what am I forgetting
```

RB should default to this order:

1. **Signal freshness / instrumentation state**
   - Are email/calendar/messages/calls/social fresh?
   - If stale, say which conclusions are unreliable.

2. **Top priorities today**
   - Relationship/action priorities first.
   - Must include grounding and confidence.

3. **Last-24h relationship signals**
   - Group by source.
   - If none, say whether that conclusion is reliable or stale-source-limited.

4. **Loops / obligations**
   - Overdue, due today, this week.
   - Include loop IDs.

5. **Recommended actions**
   - Named, specific, sequenced.
   - Include restraint/no-action recommendations.

6. **What to ignore**
   - Suppressed noise, no-reply/newsletter/self-sent, stale or low-action items.

7. **Reconciliation prompts**
   - Small questions that would improve RB's certainty.

8. **Condensed industry context**
   - Only fresh/relevant items.
   - Each must say why it matters to Todd's network, active threads, opportunities, job search, consulting work, or strategic positioning.

## Acceptance Criteria

After implementation, this bare prompt:

```text
show me the daily briefing
```

must:

- call `getDailyBrief`
- call or use `getRelationshipSignals`
- render the canonical sections above
- include grounding labels
- include stale-source warnings if present
- include loop/obligation state
- include what-to-ignore
- include reconciliation prompts if present
- include industry context only after relationship/action sections
- keep industry context short and relevance-filtered

It should not require the user to ask:

```text
Make it CoS-level, not a news briefing.
```

## Likely Files

```text
system/scripts/daily_brief.py
system/scripts/relationship_signals.py
system/api/custom_gpt_prompt.md
system/api/server.py
system/api/openapi.yaml
system/api/openapi_gpt.yaml
system/scripts/api_smoke_test.py
system/scripts/validate_openapi_gpt.py
```

## Testing Prompt

After implementation and GPT refresh, test with only:

```text
show me the daily briefing
```

Do not add hints.

If the response needs a hint to be CoS-level, the implementation is not done.

