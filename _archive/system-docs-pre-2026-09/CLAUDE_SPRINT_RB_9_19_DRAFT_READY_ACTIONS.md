# Claude Sprint Brief — RB 9.19 Draft-Ready Actions

**Prepared:** 2026-05-28  
**Prepared by:** Codex  
**Audience:** Claude / next RB implementation sprint  
**Sprint posture:** Product leverage layer. Convert RB's recommended actions into copy-ready, preview-only drafts in the selected user's voice and company/entity boundaries.

---

## Why This Sprint Is Next

RB 9.18 closed the operational wiring and defect-closure layer:

- durable named tunnel is live at `https://rb-api.bridgepointops.org`
- Custom GPT schema validates and is Builder-ready
- Apple Messages / Calls are wired into the morning/source path
- passive intelligence verification and corroboration queues are reviewable
- promotion flow exists with evidence gates
- interrupt TTL expiry exists
- defect-adjacent tests are green

RB now reliably detects, scores, verifies, routes, and briefs. The next product gap is execution energy.

RB frequently knows:

- who matters
- why they matter
- what changed
- what action is recommended
- what confidence/uncertainty exists
- what channel likely fits

But the user still has to write the message.

The 9.19 goal:

> Turn high-confidence RB recommendations into ready-to-edit drafts while preserving preview-only control and the selected user's voice, role, company, and relationship boundaries.

---

## Current Starting Point

There is already a structured draft-spec layer:

- `system/scripts/action_drafts.py`
- `GET /draft-actions` (`getDraftActions`)
- cache: `system/.cache/action_drafts.json`

Important: `action_drafts.py` currently **does not generate message text**. It creates `DraftSpec` objects the GPT can use to draft during an active session.

Existing `DraftSpec` fields include:

- `action_type`
- `contact_id`
- `contact_name`
- `contact_company`
- `rc_tier`
- `relationship_context`
- `trigger`
- `trigger_source`
- `loop_id`
- `thread_id`
- `last_touch`
- `days_since_touch`
- `evidence`
- `channel`
- `tone_suggestion`
- `draft_guidance`
- `engagement_boundary`
- `boundary_reason`
- `voice_constraints`
- `confidence`

Existing supported action types:

- `follow_up`
- `reconnect`
- `intro_request`
- `thank_you`
- `linkedin_message`
- `text_message`

Existing voice constraints for the current selected profile:

- Direct but warm. Midwestern tone.
- No AI fluff. No buzzwords. No exaggerated enthusiasm.
- No one-line paragraphs. Natural cadence. Practical language.
- Conversational, concise. Sound human, not promotional.
- No free advice positioning. No intro-without-conviction framing.
- Current selected-profile boundary: BridgePoint Ops is paid engagement or referral only. Do not volunteer the user to be a free resource.

Build on this. Do not replace it. Generalize the implementation around selected-profile voice and boundary metadata; Todd/BridgePoint is the current instance, not the product abstraction.

---

## Non-Negotiable Invariants

### 1. No Auto-Send

RB may draft. RB must not send.

All generated messages are preview-only artifacts unless the user explicitly copies/sends them outside RB or a separately confirmed send path exists.

Relevant guardrails:

- `system/scripts/privacy_guard.py`
- `system/tests/test_privacy_guard.py`
- `ARCHITECTURE.md` says RB does not auto-send messages or auto-accept connection requests.

### 2. Evidence-Bounded Drafting

Drafts must not invent:

- relationship history
- commitments
- meetings
- interest
- facts about third parties
- confidence RB does not have
- source-backed claims not present in evidence

If evidence is weak, stale, disputed, or absent, the draft should be modest and caveated by omission, not by awkward legalese.

### 3. Passive Intelligence Gate Still Applies

For drafts related to vendor claims, strategic claims, or external posts, do not treat unverified claims as fact. Use `claim_status`, `confidence_score`, `corroboration_count`, and `graph_mutation_eligibility` when present.

Never write a draft that says "I saw that X is true" when RB only has a weak signal or vendor positioning.

### 4. Company / Entity Boundary

Do not volunteer the user or their company/entity as a free resource, free intro broker, unpaid advisor, or commission-only sales channel unless that boundary is explicitly permitted in the selected profile.

The draft should protect relationship capital.

### 5. Channel Fit

Email, SMS, LinkedIn DM, intro ask, and thank-you notes should sound different. The channel should constrain length, formality, and ask shape.

---

## Recommended 9.19 Deliverables

### D1 — Draft Text Generator

Add deterministic draft text generation on top of `DraftSpec`.

Possible implementation paths:

- extend `action_drafts.py` with `render_draft(spec)` / `render_drafts(report)`
- or add `system/scripts/action_draft_text.py` if separation keeps the module cleaner

Each rendered draft should include:

- `draft_id`
- `spec_id`
- `channel`
- `action_type`
- `subject` when channel is email
- `body`
- `tone_suggestion`
- `evidence_used`
- `omitted_due_to_uncertainty`
- `safety_flags`
- `send_allowed=false`
- `requires_user_review=true`

Acceptance:

- every generated draft references only fields available in the `DraftSpec`
- no draft includes unsupported claims
- no draft is marked sendable
- restricted boundary specs either produce no draft or produce a boundary-safe alternative

### D2 — Draft Types

Support at least these draft intents:

1. `follow_up`
2. `reconnect`
3. `intro_request`
4. `thank_you`
5. `linkedin_message`
6. `text_message`

Recommended new intents if useful:

- `relationship_repair`
- `verification_request`
- `meeting_prep_request`
- `opportunity_reactivation`

Do not add new intent names unless the tests and schema/docs are updated.

### D3 — Source Integration

Generate drafts from the action sources RB already produces:

- overdue loops
- dormancy crossings
- last-24h relationship signals
- sent followups awaiting response
- meeting prep candidates
- communication failure risk / channel escalation
- passive verification closure recommendations
- passive intelligence verification/corroboration queue items where an outreach draft is useful

Start with the existing `action_drafts.py` sources, then add one or two high-leverage sources rather than trying to cover everything.

Priority additions:

1. communication failure / channel escalation drafts
2. meeting prep outreach drafts
3. verification request drafts for passive vendor/customer claims

### D4 — API Surface

Current endpoint:

- `GET /draft-actions` returns `DraftSpec` objects.

Options:

- extend `GET /draft-actions` with `include_text=true`
- or add a new endpoint, e.g. `GET /draft-actions/rendered`

Keep the GPT action cap in mind. If a new endpoint is added, decide whether it belongs in `openapi_gpt.yaml` or full API only.

Acceptance:

- Custom GPT can retrieve draft-ready text without shell access
- endpoint returns preview-only drafts
- no send endpoint is added in this sprint

### D5 — Daily Brief Integration

Daily Brief should be able to signal when drafts exist.

Examples:

- "Draft available" on high-priority actions
- action option: `view_draft`
- section summary count: `draft_ready_actions: N`

Do not bloat the main Daily Brief with full draft bodies by default. Full draft text should be available on demand through `getDraftActions` or equivalent.

### D6 — Tests

Add regression tests covering:

- follow-up email draft
- SMS/text draft length and tone
- LinkedIn DM draft length and tone
- intro request includes opt-out / no-pressure language
- restricted/free-advice trigger does not produce a free-resource draft
- stale or weak evidence does not become factual language
- passive intelligence weak signal is not phrased as verified fact
- `send_allowed=false` always present
- no unsupported names/facts appear

Existing smoke:

```bash
python3 system/scripts/action_drafts.py --smoke
```

Suggested test file:

- `system/tests/test_action_drafts_text.py`

---

## Product Behavior Examples

### Follow-Up

Input state:

- action_type: `follow_up`
- channel: `email`
- trigger: waiting on a reply
- tone: `direct_followup`

Expected draft shape:

- concise subject
- acknowledges prior thread without pressure
- makes one specific next-step ask
- avoids guilt language

### SMS / Text

Expected draft shape:

- short
- no long context dump
- no formal signoff
- should feel like the selected user, not a CRM sequence

### Intro Request

Expected draft shape:

- clearly names the target and reason
- explains why the intro is specific
- includes "no pressure" / opt-out language
- does not imply the broker owes the user

### Verification Request

Expected draft shape:

- asks for confirmation of a specific claim
- does not state the claim as fact
- names why verification matters
- keeps the ask small

Example posture:

> "I saw a vendor/customer reference and wanted to verify scope before treating it as real signal. Do you know whether this is a live deployment, a pilot, or just a logo/reference?"

---

## Files Likely To Touch

Primary:

- `system/scripts/action_drafts.py`
- `system/api/server.py`
- `system/api/openapi.yaml`
- `system/api/openapi_gpt.yaml` if GPT-facing changes are needed
- `system/scripts/validate_openapi_gpt.py` if action set changes
- `system/scripts/daily_brief.py`
- `system/tests/test_action_drafts_text.py`

Possible supporting:

- `system/scripts/user_profile.py` for selected-profile voice and boundary constraints
- `system/SCHEMAS.md` if adding a rendered draft contract
- `system/STATUS.md` after completion

---

## Tests To Run Before Calling 9.19 Complete

Minimum:

```bash
python3 system/scripts/action_drafts.py --smoke
python3 -m unittest system.tests.test_privacy_guard
python3 -m unittest system.tests.test_intro_engine
python3 -m unittest system.tests.test_passive_intelligence
python3 -m unittest system.tests.test_direct_comms_ingestion
python3 -m unittest system.tests.test_action_drafts_text
python3 -m py_compile system/scripts/action_drafts.py system/api/server.py system/scripts/daily_brief.py
python3 system/scripts/validate_openapi_gpt.py
```

If the Daily Brief integration changes:

```bash
python3 system/scripts/daily_brief.py --smoke
RB_API_KEY=localtest python3 system/scripts/morning_path_test.py
```

---

## What Not To Do

- Do not auto-send messages.
- Do not add send-email / send-SMS / send-LinkedIn functionality.
- Do not create drafts from raw external content without privacy and evidence gates.
- Do not invent personal familiarity.
- Do not over-personalize when the relationship context is weak.
- Do not make every action verbose; channel fit matters.
- Do not replace the existing `DraftSpec` layer.
- Do not stuff full draft bodies into the Daily Brief by default.
- Do not break the 30-operation Custom GPT cap.

---

## Definition Of Done

RB 9.19 is complete when:

1. `DraftSpec` objects can be rendered into preview-only draft text.
2. Drafts cover at least follow-up, reconnect, intro request, thank-you, LinkedIn DM, and SMS/text.
3. Every draft carries `send_allowed=false` and `requires_user_review=true`.
4. Drafts obey selected-profile voice constraints and company/entity boundaries.
5. Weak/stale/disputed evidence is not promoted into confident factual language.
6. The API can return rendered drafts or an intentional GPT-facing path exists.
7. Daily Brief indicates when draft-ready actions exist without dumping every draft inline.
8. Tests prove no auto-send, no invented facts, and no free-resource positioning.

The sprint is successful if RB moves from "here is what you should do" to "here is the note I would send, ready for your edit."
