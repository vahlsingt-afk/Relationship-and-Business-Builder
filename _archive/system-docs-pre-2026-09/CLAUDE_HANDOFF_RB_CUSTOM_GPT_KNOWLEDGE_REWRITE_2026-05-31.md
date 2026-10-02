# Claude Handoff — Custom GPT Instructions / Knowledge Rewrite

Date: 2026-05-31  
Canonical workspace: `/Users/toddvahlsing/Documents/Claude/Projects/Relationship Builder`  
Do not use secondary workspace: `/Users/toddvahlsing/Documents/Relationship Bridge`

## Why this happened

The Custom GPT instruction window exceeded the 8,000-character Builder limit again after adding the `precomputed_answers.operator_count` Tier 1 retrieval rule. Todd also asked that reusable product instructions stop hardcoding his name and use portable language like “the user.”

Decision:

- Keep the **instruction box** as the compact always-on control plane.
- Move longer doctrine, examples, and defect-playbook language into **Knowledge articles**.
- Do not let the instruction-window limit flatten the product behavior.

## Files changed

### 1. `system/api/custom_gpt_instructions_8k.md`

Rewritten/compacted to fit under Builder limit.

Current size:

```text
7891 bytes
```

Key retained rules:

- API-first behavior
- session bootstrap via `session_bootstrap_status`
- explicit auth failure handling:
  - if `getDailyBrief` returns `invalid or missing x-api-key`, say RB Action is not authenticated; do not call graphs unavailable
- retrieval hierarchy
- McDonald’s/MCD Micro Graph routing
- `precomputed_answers.operator_count.answer` is valid Tier 1 retrieval
- no public/base-model estimates when mounted Micro Graph exists
- named entity routing
- discovery-first Daily Brief rendering
- LinkedIn export ZIP handling
- triage output shape
- write safety
- no narrative essay replacing canonical sections

Portable language:

- Removed `Todd` / `Vahlsing`
- Uses “the user” / “user” except where preserving API taxonomy such as `ask_todd`

Important regression phrase restored:

```text
Do not replace canonical sections with a narrative essay.
```

### 2. `system/api/custom_gpt_operational_playbook.md`

New Knowledge article.

Current size:

```text
9059 bytes
```

Purpose:

Longer doctrine and examples that should not live in the instruction box.

Includes:

- product identity
- instruction split explanation
- retrieval/source discipline
- auth failure wording
- Micro Graph and `precomputed_answers`
- daily brief doctrine
- CoS assessment style
- default response examples
- artifact handling
- triage output doctrine
- entity pattern synthesis
- write safety
- known failure patterns
- portable-language rule

No `Todd` / `Vahlsing`.

### 3. `system/api/custom_gpt_prompt.md`

Replaced old large/stale prompt article with a clean portable Knowledge article.

Old state:

```text
~76KB, Todd-specific, overlapping with old instruction surface
```

New state:

```text
12064 bytes
```

Purpose:

Primary Custom GPT Knowledge article for reupload. It now aligns with the compact instructions and new operational playbook.

Includes:

- operating posture
- retrieval/source discipline
- session bootstrap and mounted assets
- auth failure vs retrieval failure
- Micro Graph and `precomputed_answers`
- named entity/artifact routing
- discovery-first daily brief rendering
- LinkedIn export ZIP rule
- triage output
- relationship/execution intelligence
- write safety
- CoS assessment discipline
- known failure patterns
- portable language rule

Preserved required regression hooks:

- `linkedin_export_zip`
- `ingestLinkedInExport`
- `What would you like to do with it?` as a prohibited failure phrase
- `what_rb_found_without_you_telling_it`
- `known_state_reminders`
- `manual_user_provided`
- `precomputed_answers`
- `invalid or missing x-api-key`

No `Todd` / `Vahlsing`.

## Tests run

Prompt/Knowledge regression tests:

```bash
python3 -m pytest system/tests/test_custom_gpt_artifact_routing.py system/tests/test_daily_brief_autonomous_discovery.py -q
```

Result:

```text
44 passed
```

Spot checks:

```bash
wc -c system/api/custom_gpt_prompt.md system/api/custom_gpt_operational_playbook.md system/api/custom_gpt_instructions_8k.md
rg -n "Todd|Vahlsing" system/api/custom_gpt_prompt.md system/api/custom_gpt_operational_playbook.md system/api/custom_gpt_instructions_8k.md
```

Result:

- instruction file under 8k
- no `Todd` / `Vahlsing` in the three portable GPT files

## Clipboard action completed

Codex copied `system/api/custom_gpt_prompt.md` to Todd’s clipboard for reupload.

Previously copied during this thread:

- `system/api/custom_gpt_instructions_8k.md`
- `system/api/openapi_gpt.yaml`

If Todd needs either copied again, use:

```bash
pbcopy < system/api/custom_gpt_instructions_8k.md
pbcopy < system/api/openapi_gpt.yaml
pbcopy < system/api/custom_gpt_prompt.md
pbcopy < system/api/custom_gpt_operational_playbook.md
```

## Recommended GPT Builder setup now

Instructions box:

```text
system/api/custom_gpt_instructions_8k.md
```

Knowledge uploads:

```text
system/api/custom_gpt_prompt.md
system/api/custom_gpt_operational_playbook.md
system/CANONICAL_RESPONSE_CONTRACT.md
```

Action schema:

```text
system/api/openapi_gpt.yaml
```

Action auth:

```text
Authentication: API Key
Auth type: Custom
Header name: x-api-key
Value: current RB_API_KEY
```

## Important context

The current live GPT issue was not Python graph retrieval. Earlier debugging showed:

- with `x-api-key`, public `/daily_brief` returns mounted graph state
- without `x-api-key`, it returns `{"detail":"invalid or missing x-api-key"}`
- live GPT had been calling without/without-valid auth and then misreporting graph failure

The compact instructions and Knowledge now explicitly distinguish auth failure from retrieval failure.

## Remaining action for Claude/Todd

Reupload:

1. compact instructions into GPT Builder Instructions box
2. updated Knowledge articles
3. updated OpenAPI schema if not already present
4. re-enter API auth after schema paste/save

Then test in Builder:

1. `getPublicBootstrapStatus` if available
2. `getDailyBrief`
3. McDonald’s operator-count question

Expected McDonald’s answer should use:

```text
precomputed_answers.operator_count.answer
```

and cite:

```text
[Source: McDonald's Micro Graph — precomputed at brief build — Tier 1 — verified]
```
