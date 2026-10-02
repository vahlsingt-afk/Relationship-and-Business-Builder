# RB 9.3 Architecture — Chief-of-Staff Action Layer

Date: 2026-05-23
Author: Claude (architect review)
For: Codex (implementer)
Companion handoff: `system/CLAUDE_HANDOFF_2026-05-23_RB_9_2_MORNING_DELIVERY_SOURCE_INSTRUMENTATION.md`

---

## Sprint Objective

RB 9.1 made the intelligence layer durable. RB 9.2 made the morning delivery
operationally real. RB 9.3 closes the gap between "knowing what to do" and
"doing it."

Every existing module now ends with a recommendation that terminates in a
communication, a prepared artifact, or a closed loop. The 9.3 sprint wires those
terminations:

- `action_drafts.py` gives the GPT structured draft specs so it can produce a
  ready-to-review message in Todd's voice — not a generic suggestion, an actual
  draft.
- Meeting prep materializes automatically on the morning path, not on request.
- Source health rows gain `recovery_command` fields so "under-instrumented" is
  actionable, not just a label.
- The three loop lifecycle modules (`smart_loops`, `passive_verification`,
  `closeout`) get a thin orchestrator that connects them across the day.
- Once Todd creates the named tunnel and ChatGPT Task, a verification protocol
  confirms the delivery chain is live.

The operating principle: a Chief of Staff does not just identify what matters.
It puts the prep packet on the desk and hands you the first draft.

---

## What 9.2 Left in Place (Read Before Implementing)

These are ready to use. Do not rewrite them.

- `smart_loops.py` — `--dry-run` (default) and `--apply --confirm` modes. Produces
  `LoopProposal` objects with action types, source refs, verification methods.
- `meeting_prep.py` — `--write --confirm` writes 11-section prep artifacts for
  qualifying calendar events; `event_qualifies()` logic is in place.
- `passive_verification.py` — auto-close evidence-backed loops; `auto_closeable`
  and `possible_resolution` tiers.
- `closeout.py` — 16:30 EOD six-bucket closeout; `--write --confirm` path is
  live.
- `publish.py` — writes `latest_brief.json` and `latest.html`.
- `source_health.json` — per-source `status`, `tier`, `last_refreshed_at`, `reason`.
  Does NOT yet have `recovery_command` (that is Priority 3).
- GPT spec: 27 ops (3 ops of headroom before the 30-op cap).

---

## Architecture Decision Record

### A1 — action_drafts.py produces DraftSpec objects, not draft text

Python generates structured `DraftSpec` objects containing all context the GPT
needs: who, what, why, source evidence, voice constraints, engagement-boundary
check, and a one-sentence `draft_guidance` field. The GPT generates the actual
message text from the spec at render time.

**Why this over template fill-in:** Template-filled drafts are stiff and often
miss register. The GPT already holds Todd's voice profile in its system prompt
and is far better at natural language. Python's job is to assemble the spec
accurately; the GPT's job is to make the words sound like Todd.

**Why not generate text in the morning automation chain:** The brief runs at
05:00 CT without a model in the loop. Deferring draft generation to the GPT
response — when Todd is reading the brief and is in an active session — is the
right split.

**What this means for Codex:** `action_drafts.py` is a pure Python module. It
reads canonical state, builds `DraftSpec` objects, and exposes them through a
new `getDraftActions` endpoint. No LLM calls, no subprocess calls to the GPT.

### A2 — Meeting prep auto-materialization is a wiring change, not a new module

`meeting_prep.py --write --confirm` is already live. The gap is that `refresh_all.py`
does not call it. Priority 2 is wiring, not building: add the call, update
`daily_brief.py` to report existing artifacts, and create prep loops from
`smart_loops.py` automatically when an artifact is written.

### A3 — Loop lifecycle autopilot is a thin orchestrator, not a new compute engine

All loop compute already exists. `loop_autopilot.py` is a single-file orchestrator
that calls the right existing module at the right time of day. Morning mode runs
`smart_loops` proposals. Midday/on-demand runs `passive_verification` evidence
check. Closeout mode sequences `passive_verification --apply --confirm` then
`closeout --write --confirm`. No new scoring, no new data shapes.

### A4 — Source health recovery commands belong in source_health.json, not in the brief

The brief already reads `source_health.json`. Adding `recovery_command` and
`recovery_type` to each row there means both the brief and the API surface
the same actionable next step. No new brief section needed — the Daily Prep
Summary already renders the stale-source block.

### A5 — getDraftActions stays within the 30-op Custom GPT cap

Current count: 27 ops. Adding `getDraftActions` (1 op) and potentially
`getMeetingPrepArtifacts` (1 op) brings the total to 29. That is within cap.
Do not add both unless the count confirms it. Validate with
`python3 system/scripts/validate_openapi_gpt.py` after every spec change.

---

## Priority Implementation Order for Codex

Implement exactly one priority at a time. Smoke-test before moving to the next.

---

### Priority 1 — Action Drafts: DraftSpec Engine

**Why first:** This is the highest-leverage new capability — the one that turns
the brief from a read-only report into a draft-ready action surface. Everything
else in the sprint either wires existing modules or improves instrumentation.

#### New file: `system/scripts/action_drafts.py`

The module reads the canonical brief cache and produces `DraftSpec` objects for
items that are draft-worthy. It does not generate message text.

##### Data model

```python
@dataclass
class DraftSpec:
    spec_id: str                    # stable hash of contact_id + action_type + trigger_date
    action_type: str                # see ACTION_TYPES below
    contact_id: str                 # baseline ID
    contact_name: str
    contact_company: str
    rc_tier: Optional[str]          # inner | broader | dormant_valuable | None
    relationship_context: str       # 1-2 sentences from card or baseline notes
    trigger: str                    # what in the brief caused this
    trigger_source: str             # system_detected | manual_user_provided | inferred
    loop_id: Optional[str]          # if tied to an open loop
    thread_id: Optional[str]        # if tied to an active thread
    last_touch: Optional[str]       # ISO date
    days_since_touch: Optional[int]
    evidence: list[str]             # source refs from the underlying signal
    channel: str                    # email | linkedin | sms | any
    tone_suggestion: str            # see TONES below
    draft_guidance: str             # one sentence: "Follow up on the PAR referral from May"
    engagement_boundary: str        # clear | check_heuristics | restricted
    boundary_reason: Optional[str]  # why restricted (BridgePoint Ops rules from profile)
    voice_constraints: list[str]    # always the same set (from 00_TODD_PROFILE.md)
    confidence: str                 # high | medium | low
    created_at: str                 # ISO timestamp
```

`ACTION_TYPES` (parallel to `smart_loops` action types where relevant):

```python
ACTION_TYPES = {
    "follow_up",        # follow up on an outbound that has no reply
    "reconnect",        # warm up a cooling or dormant RC
    "intro_request",    # ask a connector to introduce Todd to a target
    "thank_you",        # close a loop with appreciation after a received action
    "linkedin_message", # outbound LinkedIn DM (preferred over email for cold-ish contacts)
    "text_message",     # short iMessage/SMS for inner-tier RCs
}
```

`TONES`:

```python
TONES = {
    "warm_reconnect",     # inner-tier, time since last touch is the main signal
    "direct_followup",    # waiting on a reply; stays factual, no pressure
    "executive_peer",     # PAR/enterprise tier; peer-to-peer, business context
    "grateful_close",     # closing a loop where someone helped; genuine, brief
    "light_check_in",     # broader-tier, low stakes, no ask required
}
```

`VOICE_CONSTRAINTS` (fixed set from `00_TODD_PROFILE.md`, same for every spec):

```python
VOICE_CONSTRAINTS = [
    "Direct but warm. Midwestern tone.",
    "No AI fluff. No buzzwords. No exaggerated enthusiasm.",
    "No one-line paragraphs. Natural cadence. Practical language.",
    "Conversational, concise. Sound human, not promotional.",
    "No free advice positioning. No intro-without-conviction framing.",
    "BridgePoint Ops: paid engagement or referral only. Do not volunteer to be a free resource.",
]
```

##### Engagement boundary check

Before adding a `DraftSpec`, check whether the draft conflicts with Todd's
engagement rules from `00_TODD_PROFILE.md`:

- If the contact/company is in the loop ledger with `action_type == waiting` and
  no reply yet → `engagement_boundary: clear` but `tone: direct_followup`.
- If the trigger is "inbound ask for free advice" or "intro-only request" →
  `engagement_boundary: restricted`, `boundary_reason: "inbound ask for free
  resource — BridgePoint Ops boundary."` Do not suppress the spec; let the GPT
  see the boundary flag and decide how to handle it.
- Default: `engagement_boundary: clear`.

##### Input

Read from `system/.cache/daily_brief.json` and `system/.cache/relationship_signals.json`.
These caches are written by the morning automation. `action_drafts.py` should never
trigger a live recompute; if the caches are missing or stale, report the gap and
exit cleanly.

The following brief sections are draft-eligible:

1. **Loop Review — overdue or due today loops** with `action_type == follow_up` or
   `waiting` where the expected-response window has passed.
2. **Dormancy crossings** — RCs in `crossings` whose tier crossing is ≥ 1 day.
3. **Relationship Signals** — source-backed signals where the API returned
   `disposition == act_today`.
4. **Strategic Operator Movements** — only when a known relationship contact is
   named and an outreach is warranted.
5. **Sent Followups Awaiting Response** — items in `sent_followups_awaiting_response`
   that are `overdue`.

Do NOT generate specs for items tagged `ignore` or `monitor` unless they also appear
in an overdue loop. Do not generate specs for items with no `contact_id` in baseline.

##### CLI modes

```bash
python3 system/scripts/action_drafts.py                   # list specs, no writes (default)
python3 system/scripts/action_drafts.py --json            # emit specs as JSON to stdout
python3 system/scripts/action_drafts.py --cache           # write system/.cache/action_drafts.json
python3 system/scripts/action_drafts.py --smoke           # in-memory regression (≥20 checks)
```

Default output (no flags): one line per spec, format:
```
[follow_up] Donnie Boivin (inner) — direct_followup — engagement: clear
  "Follow up on the PAR intro conversation from May; last touch 18 days ago."
  evidence: email_overlay:thread_19e315 | loop: L-047
```

##### Cache file: `system/.cache/action_drafts.json`

```json
{
  "generated_at": "2026-05-23T05:01:30",
  "source_brief_date": "2026-05-23",
  "specs": [
    {
      "spec_id": "abc123",
      "action_type": "follow_up",
      "contact_id": "donnie-boivin",
      ...
    }
  ],
  "spec_count": 3,
  "suppressed_count": 1,
  "suppressed_reasons": ["engagement_boundary:restricted:1"]
}
```

##### Smoke test requirements (≥20 checks)

Cover:
- spec_id is stable (same input → same id across runs)
- engagement_boundary flag set correctly for BridgePoint restricted contacts
- VOICE_CONSTRAINTS present on every spec
- spec NOT generated for `ignore`-tagged items
- spec NOT generated for contacts not in baseline
- `days_since_touch` correctly computed from `last_touch`
- `action_type` from loop carries through to spec
- cache write produces valid JSON
- missing cache input produces clean error (not crash)
- `--smoke` exits 0 with synthetic data

#### Files to modify for Priority 1

**`system/scripts/refresh_all.py`**

Add after `daily_brief.py`:

```bash
python3 system/scripts/action_drafts.py --cache
```

This ensures `action_drafts.json` is always fresh when the GPT calls
`getDraftActions`.

**`system/api/server.py`**

Add endpoint:

```python
@app.get("/draft-actions")
def get_draft_actions(contact_id: Optional[str] = None, action_type: Optional[str] = None):
    """Return DraftSpec objects from the action_drafts cache.
    Optional filters: contact_id, action_type."""
    cache = PROJECT_DIR / "system" / ".cache" / "action_drafts.json"
    if not cache.exists():
        raise HTTPException(404, detail="action_drafts.json not found; run refresh_all.py")
    data = json.loads(cache.read_text())
    specs = data.get("specs", [])
    if contact_id:
        specs = [s for s in specs if s.get("contact_id") == contact_id]
    if action_type:
        specs = [s for s in specs if s.get("action_type") == action_type]
    return {"specs": specs, "generated_at": data.get("generated_at"), "spec_count": len(specs)}
```

**`system/api/openapi.yaml` and `system/api/openapi_gpt.yaml`**

Add `GET /draft-actions` to both. Validate:

```bash
python3 system/scripts/validate_openapi_gpt.py
# expect: ≤30 ops (should be 28 after this addition)
```

**`system/api/custom_gpt_prompt.md`**

Add a new operating rule (after rule 10a, before the existing rule 11):

```text
10b. Draft-ready actions. When the daily brief or relationship-signals response
     includes a "Draft-Ready Actions" section, or when Todd asks you to draft a
     message, call `getDraftActions` first. Use the returned DraftSpec objects as
     your drafting brief. For each spec: use the `draft_guidance` field as the
     subject of the message; apply `voice_constraints` exactly; use `tone_suggestion`
     to calibrate register; flag `engagement_boundary: restricted` specs before
     drafting and confirm Todd wants to proceed. Never draft a message that
     contradicts the BridgePoint Ops engagement boundaries in `voice_constraints`.
     Render the draft inside a code block so it is easy to copy. Always end with
     the grounding line: "Source: [spec.evidence joined by ' | ']."
```

#### Priority 1 acceptance smoke test

```bash
python3 system/scripts/action_drafts.py --smoke
# expect: ≥20 checks, 0 failures, exit 0

python3 system/scripts/action_drafts.py --cache
# expect: system/.cache/action_drafts.json written, valid JSON

python3 system/scripts/api_smoke_test.py
# expect: GET /draft-actions passes

python3 system/scripts/validate_openapi_gpt.py
# expect: ≤30 ops, no validation errors
```

---

### Priority 2 — Meeting Prep Auto-Materialization

**Why second:** `meeting_prep.py --write --confirm` is already built and live.
This priority is wiring changes only — about 30 lines of code total. The
compounding value is high: the morning brief transforms from "you have a meeting,
consider prepping" to "your prep brief for Donnie at 2:00 PM is ready."

#### Files to modify

**`system/scripts/meeting_prep.py`**

Add `--for-today` flag as an alias for `--write --confirm` scoped to today's
events only (not tomorrow). This is the morning automation flag:

```python
p.add_argument("--for-today", action="store_true",
               help="Write prep artifacts for today's qualifying events only. "
                    "Equivalent to --write --confirm scoped to today.")
```

In the write path: when `--for-today` is set, filter `collect_candidate_events()`
results to `event["start"][:10] == today_str` before writing.

Also add: after writing an artifact, emit a structured line to stdout for
`refresh_all.py` to capture:

```
PREP_WRITTEN: system/meeting_briefs/2026-05-23-donnie-boivin-gcal-abc.md
```

**`system/scripts/refresh_all.py`**

Add after `daily_brief.py` (and after `action_drafts.py --cache`):

```bash
python3 system/scripts/meeting_prep.py --for-today
```

Capture and log `PREP_WRITTEN:` lines; they are informational for the operator
log. If no qualifying events, the script exits 0 silently — that is the correct
behavior.

**`system/scripts/daily_brief.py`**

In the Meeting Prep section builder: after identifying qualifying meetings from
the calendar overlay, check whether a prep artifact already exists for each event
using `meeting_prep.artifact_path_for(event)`. If the artifact exists:

```
Meeting Prep — Donnie Boivin, 2:00 PM CT
  Prep brief: system/meeting_briefs/2026-05-23-donnie-boivin-gcal-abc.md
  Status: ready
```

If it does not exist:

```
Meeting Prep — Donnie Boivin, 2:00 PM CT
  Status: prep not yet generated
  Action: python3 system/scripts/meeting_prep.py --event-id gcal-abc --write --confirm
```

This closes the feedback loop: the morning brief confirms whether prep is done
or still pending.

**`system/scripts/smart_loops.py`**

When `meeting_prep.artifact_path_for(event)` returns a path that now exists (i.e.,
an artifact was just written), and no `meeting_prep` loop is already open for
this event, `smart_loops.py` should propose a `meeting_prep` loop pointing at
that artifact path as the verification signal. This is already in the design
(P-001 step 13 calls for it); the gap is that it was conditional on "offer to
create." Make it automatic: if artifact exists, include the proposal.

No changes to the loop deduplication logic — the existing `source_refs` dedup will
prevent duplicate proposals.

#### Priority 2 acceptance smoke test

```bash
python3 system/scripts/meeting_prep.py --smoke
# expect: 0 failures (existing 27 smoke checks still pass)

python3 system/scripts/meeting_prep.py --for-today
# expect: exit 0; either "PREP_WRITTEN: ..." for each qualifying meeting,
#         or silent exit if no qualifying events today

python3 system/scripts/daily_brief.py --smoke
# expect: 0 failures; Meeting Prep section renders "ready" for written artifacts
```

---

### Priority 3 — Source Health Recovery Commands

**Why third:** `source_health.json` now identifies every stale/missing source by
name. Without `recovery_command`, the brief says "Tier 1 source under-instrumented"
and the user has to remember the command. This is a small change with immediate
daily value: from "something is wrong" to "here is the exact next command."

#### Files to modify

**`system/scripts/refresh_sources.py`**

In `--save-health` path, add `recovery_command` and `recovery_type` to each
source row before writing `source_health.json`. The values are deterministic
(not generated at runtime — they are static per source and status):

```python
RECOVERY_COMMANDS = {
    # (source_key, status) → (recovery_command, recovery_type)
    ("email", "skipped_no_raw_input"): (
        "From a Cowork Gmail session, run search_threads and save the JSON to "
        "system/inbox/raw_gmail_threads.{account_id}.json. "
        "Then: python3 system/scripts/refresh_sources.py --email",
        "mcp_capture",
    ),
    ("calendar", "skipped_no_raw_input"): (
        "From a Cowork Calendar session, run list_events and save the JSON to "
        "system/inbox/raw_calendar.{account_id}.json. "
        "Then: python3 system/scripts/refresh_sources.py --calendar",
        "mcp_capture",
    ),
    ("messages", "unavailable"): (
        "System Settings → Privacy & Security → Full Disk Access → add Terminal → "
        "python3 system/scripts/fetch_apple_messages.py --days 365",
        "host_setup",
    ),
    ("calls", "unavailable"): (
        "System Settings → Privacy & Security → Full Disk Access → add Terminal → "
        "python3 system/scripts/fetch_apple_calls.py --days 365",
        "host_setup",
    ),
    ("linkedin_messaging", "skipped_no_raw_input"): (
        "Download LinkedIn Messages export → drop at system/inbox/linkedin_messages_export.csv → "
        "python3 system/scripts/linkedin_messaging.py --ingest system/inbox/linkedin_messages_export.csv",
        "manual_export",
    ),
    ("market_signals", "skipped_no_raw_input"): (
        "Scan RTN / Restaurant Business / Nation's Restaurant News / Restaurant Dive / QSR Magazine "
        "and write reviewed rows to system/inbox/market_signals.json. "
        "Then: python3 system/scripts/refresh_sources.py --market",
        "manual_scan",
    ),
}
```

For statuses other than `skipped_no_raw_input` / `unavailable` (e.g., `refreshed`,
`skipped_disabled`, `failed`) the `recovery_command` field is omitted or null.
For `failed`, set `recovery_command` to the command that should be retried plus
"check error output for the specific failure."

The `account_id` placeholder in the email/calendar commands should be filled in
with the actual account_id from the source row before writing.

**Updated `source_health.json` shape (example row):**

```json
"email:personal": {
  "status": "skipped_no_raw_input",
  "last_refreshed_at": null,
  "tier": 1,
  "reason": "no raw_gmail_threads.personal.json in system/inbox/",
  "recovery_command": "From a Cowork Gmail session, run search_threads and save the JSON to system/inbox/raw_gmail_threads.personal.json. Then: python3 system/scripts/refresh_sources.py --email",
  "recovery_type": "mcp_capture"
}
```

**`system/api/server.py`**

`GET /brief/health` already returns `source_health.json`. No change needed — the
recovery commands will be present in the JSON automatically after the source
refresh adds them.

**`system/scripts/daily_brief.py`**

In `_load_source_health()` output rendering: when a source row has a non-null
`recovery_command`, include it in the Daily Prep Summary stale-source block:

```
Tier 1 — email:personal: skipped_no_raw_input
  → From a Cowork Gmail session, run search_threads and save to
    system/inbox/raw_gmail_threads.personal.json.
    Then: python3 system/scripts/refresh_sources.py --email
```

This replaces the generic "stale source" text with an exact instruction. The
`recovery_type` tag (`mcp_capture | host_setup | manual_export | manual_scan`)
should prefix the instruction for scannability:

```
[mcp_capture] email:personal → ...
[host_setup]  messages → System Settings → ...
```

#### Priority 3 acceptance smoke test

```bash
python3 system/scripts/refresh_sources.py --all --save-health
# expect: source_health.json written; Tier 1/Tier 2 gap rows contain recovery_command

python3 -c "
import json, pathlib
h = json.loads(pathlib.Path('system/.cache/source_health.json').read_text())
for k, v in h['sources'].items():
    if v['status'] in ('skipped_no_raw_input', 'unavailable'):
        assert 'recovery_command' in v and v['recovery_command'], f'{k} missing recovery_command'
        assert 'recovery_type' in v, f'{k} missing recovery_type'
print('recovery_command present on all gap rows')
"

python3 system/scripts/daily_brief.py --smoke
# expect: 0 failures; Daily Prep Summary includes recovery commands when health is under_instrumented
```

---

### Priority 4 — Loop Lifecycle Autopilot

**Why fourth:** Three loop lifecycle modules exist and smoke green. None of them
are wired to fire automatically across the day. `loop_autopilot.py` is a thin
orchestrator — roughly 150 lines — that calls the right module at the right phase
and reports the consolidated result.

#### New file: `system/scripts/loop_autopilot.py`

Three modes driven by `--phase`:

```bash
python3 system/scripts/loop_autopilot.py --phase morning     # propose new loops from brief
python3 system/scripts/loop_autopilot.py --phase midday      # show evidence-backed closures
python3 system/scripts/loop_autopilot.py --phase closeout    # apply closures + write closeout
python3 system/scripts/loop_autopilot.py --smoke             # in-memory regression
```

**Morning phase** (`--phase morning`):

1. Call `smart_loops.py` as a subprocess with `--dry-run --json`.
2. Print proposals (count, types, top entities).
3. If `--apply --confirm` is also passed, call `smart_loops.py --apply --confirm`.
4. Write result to `system/.cache/loop_autopilot_morning.json`.

**Midday phase** (`--phase midday`):

1. Call `passive_verification.py` as a subprocess with `--dry-run --json` (or read
   its cache if available).
2. Print `auto_closeable` and `possible_resolution` counts.
3. Suggest the `--closeout` command to apply.
4. Write result to `system/.cache/loop_autopilot_midday.json`.

**Closeout phase** (`--phase closeout`):

1. Run `passive_verification.py --apply --confirm` (if `--confirm` is also passed).
2. Run `closeout.py --write --confirm` (if `--confirm` is also passed).
3. Print the six-bucket summary from the closeout output.
4. Write result to `system/.cache/loop_autopilot_closeout.json`.

Without `--confirm`, all phases are read-only (dry-run). This is the default.
Dry-run is the smoke-test path.

**Important implementation guard:** `loop_autopilot.py` calls the existing scripts
as subprocesses (not by importing them) so that each module's own `--dry-run` and
`--confirm` guards remain the enforcement point. The orchestrator passes flags
through; it does not reimplement mutation logic.

#### Wire into automation

**`system/scripts/refresh_all.py`**

Add after `action_drafts.py --cache`:

```bash
python3 system/scripts/loop_autopilot.py --phase morning
```

This runs in dry-run mode (no `--confirm`), so proposals are shown but not
applied. The operator uses `--apply --confirm` explicitly when ready.

**`system/protocols/P-001_daily_brief_regen.md`**

Step 18b (closeout) should reference `loop_autopilot.py --phase closeout --confirm`
as the recommended single command for the 16:30 LaunchAgent, replacing the
separate `closeout.py --write --confirm` call. Update the step accordingly.

#### New protocol file: `system/protocols/P-034_loop_lifecycle_autopilot.md`

Minimal protocol documenting the morning/midday/closeout pattern. Structure:

```markdown
---
id: P-034
title: Loop Lifecycle Autopilot
script: system/scripts/loop_autopilot.py
cache: system/.cache/loop_autopilot_*.json
reads:
  - system/.cache/daily_brief.json (morning)
  - system/loop_ledger.md (all phases)
  - source_health.json (midday/closeout context)
writes:
  - system/.cache/loop_autopilot_*.json
  - system/closeouts/YYYY-MM-DD.md (closeout phase, --confirm)
  - system/loop_ledger.md (morning apply, --confirm)
trigger: morning (05:00 automation, dry-run); midday (on-demand); 16:30 (closeout)
---
```

#### Priority 4 acceptance smoke test

```bash
python3 system/scripts/loop_autopilot.py --smoke
# expect: ≥15 checks, 0 failures, exit 0

python3 system/scripts/loop_autopilot.py --phase morning
# expect: proposals printed, no writes; exit 0

python3 system/scripts/loop_autopilot.py --phase midday
# expect: auto_closeable / possible_resolution counts printed; exit 0

python3 system/scripts/loop_autopilot.py --phase closeout
# expect: dry-run closeout summary printed; no writes; exit 0

python3 system/scripts/morning_path_test.py
# expect: 5/5 automated steps still pass after these changes
```

---

### Priority 5 — ChatGPT Task Delivery Verification Protocol

**Why fifth:** This priority gates on Todd completing the named-tunnel setup and
creating the ChatGPT Task (both host-side steps from the 9.2 manual checklist).
Codex should not start this priority until Todd confirms those steps are done.

If those steps are not yet done when Codex reaches this priority, implement the
protocol file and `task_delivery_check.py` anyway — they do not depend on the
tunnel being live. The manual verification steps simply won't pass yet.

#### New file: `system/scripts/task_delivery_check.py`

A lightweight check script that verifies the automated side of delivery and
prints a manual checklist for the ChatGPT Task side.

```bash
python3 system/scripts/task_delivery_check.py
python3 system/scripts/task_delivery_check.py --smoke
```

Automated checks (via FastAPI TestClient, no live network needed):

1. `GET /brief/health` returns 200 with `overall_health` key.
2. `GET /daily_brief?use_cache=true` returns 200 with `canonical_brief` key.
3. `GET /brief/latest-json` returns 200.
4. `system/published/daily/latest_brief.json` exists and is dated today.
5. `openapi_gpt.yaml` server URL is not a `trycloudflare.com` URL (quick tunnel
   check — if it still is, warn but don't fail the script).

Manual checklist printed after automated checks:

```
Manual verification (mark each step done before updating STATUS.md):

  [ ] Named Cloudflare Tunnel is running
      → cloudflared tunnel list (should show rb-api as HEALTHY)

  [ ] openapi_gpt.yaml → servers[0].url is the named tunnel URL (not trycloudflare.com)
      → python3 system/scripts/validate_openapi_gpt.py

  [ ] Custom GPT Actions schema is republished in ChatGPT Builder

  [ ] ChatGPT Task was created inside the Relationship Bridge GPT
      → Settings → My ChatGPT → Tasks (should show the task at 5:05 AM CT)

  [ ] Task-result email received from ChatGPT <noreply@tm.openai.com>
      → Subject contains "View message"

  [ ] Clicking "View message" opens the full brief inline (no command required)

  [ ] GPT fallback command "Show today's RB Daily Brief." also returns the brief

  [ ] Backup email (if transport configured) does NOT contain the full brief
      → Subject: "RB Daily Brief is ready - <date>"
```

Write result to `system/.cache/task_delivery_check.json`:

```json
{
  "checked_at": "2026-05-23T09:15:00",
  "automated_steps": {
    "brief_health_api": "pass",
    "daily_brief_api": "pass",
    "latest_json_api": "pass",
    "latest_brief_artifact": "pass",
    "tunnel_url_not_quick": "warn"
  },
  "automated_pass_count": 4,
  "automated_warn_count": 1,
  "manual_checklist_printed": true
}
```

#### New protocol file: `system/protocols/P-034_chatgpt_task_delivery_verification.md`

Wait — P-034 is already reserved for loop lifecycle autopilot above. Use P-035.

```markdown
---
id: P-035
title: ChatGPT Task Delivery Verification
script: system/scripts/task_delivery_check.py
cache: system/.cache/task_delivery_check.json
trigger: on-demand after initial Task setup; weekly spot-check
---
```

Body: document the 5 automated checks, the 7 manual checklist items, the pass
criteria, and when STATUS.md should be updated to show ChatGPT Task delivery as
`LIVE (canonical, verified)` rather than `LIVE compute; ChatGPT Task canonical`.

#### STATUS.md update (after manual verification passes)

Only after all 7 manual checklist items are confirmed should the `rb-daily-briefing`
row in STATUS.md be updated to:

```
| `rb-daily-briefing` | LIVE (canonical, verified) | ...
```

Do not promote this row until `task_delivery_check.py` automated checks all pass
AND Todd confirms the manual checklist. If Codex is implementing the protocol
before Todd has set up the tunnel, leave the status at `LIVE compute; ChatGPT Task
canonical` and note that verification is pending.

#### Priority 5 acceptance smoke test

```bash
python3 system/scripts/task_delivery_check.py --smoke
# expect: 0 failures (smoke uses TestClient for automated checks, skips live tunnel)

python3 system/scripts/task_delivery_check.py
# expect: automated steps report pass/warn; manual checklist printed; JSON written
```

---

## Non-Goals for 9.3

Do not generate message text in Python. The GPT generates draft text from
`DraftSpec` objects. If the GPT is not in session, drafts are not available —
that is correct behavior.

Do not add numeric DRR weights. That requires the eval framework from
`RB_DRR_Specification_and_Evaluation.docx`. It is not this sprint.

Do not reopen the 9.1 strategic-operator architecture.

Do not add restaurant-tech newsletter sections to the daily brief.

Do not expose `refresh_sources.py` via the GPT or MCP. Auth story is not ready.

Do not build `loop_autopilot.py` as a new compute engine. It calls existing
modules as subprocesses. If the need arises to share logic, import the module
cleanly — do not duplicate scoring code.

Do not send any message or communication on Todd's behalf. All draft actions are
review-first; the GPT renders them for Todd to copy-send manually.

Do not mark ChatGPT Task delivery as `LIVE (canonical, verified)` in STATUS.md
until the 7-item manual checklist in P-035 is confirmed by Todd.

---

## API and Spec Changes Summary

| Endpoint | Status | Notes |
|---|---|---|
| `GET /draft-actions` | New — Priority 1 | Returns `action_drafts.json` cache. Filter by `contact_id`, `action_type`. |
| `GET /brief/health` | Exists (9.2) | Now includes `recovery_command` per gap row after Priority 3. |
| `GET /brief/latest-json` | Exists (9.2) | No change. |

Expected GPT op count after Priority 1: **28 ops** (27 existing + 1 new).
Validate after every spec change: `python3 system/scripts/validate_openapi_gpt.py`.

---

## Files Added or Modified in RB 9.3 (Master List)

### Priority 1 (action drafts)
- `system/scripts/action_drafts.py` — new module
- `system/scripts/refresh_all.py` — add `action_drafts.py --cache` call
- `system/api/server.py` — add `GET /draft-actions`
- `system/api/openapi.yaml` — add `/draft-actions` endpoint
- `system/api/openapi_gpt.yaml` — add `getDraftActions`
- `system/api/custom_gpt_prompt.md` — add rule 10b (draft-ready actions)
- `system/.cache/action_drafts.json` — new runtime file (git-ignored)

### Priority 2 (meeting prep wiring)
- `system/scripts/meeting_prep.py` — add `--for-today` flag; emit `PREP_WRITTEN:` lines
- `system/scripts/refresh_all.py` — add `meeting_prep.py --for-today` call
- `system/scripts/daily_brief.py` — check artifact existence; render "ready" vs "pending"
- `system/scripts/smart_loops.py` — auto-propose `meeting_prep` loop when artifact exists

### Priority 3 (source health recovery commands)
- `system/scripts/refresh_sources.py` — add `recovery_command` and `recovery_type` to health rows
- `system/scripts/daily_brief.py` — render recovery commands in Daily Prep Summary stale-source block

### Priority 4 (loop lifecycle autopilot)
- `system/scripts/loop_autopilot.py` — new orchestrator
- `system/scripts/refresh_all.py` — add `loop_autopilot.py --phase morning` call
- `system/protocols/P-001_daily_brief_regen.md` — update step 18b to reference loop_autopilot closeout command
- `system/protocols/P-034_loop_lifecycle_autopilot.md` — new protocol file
- `system/.cache/loop_autopilot_*.json` — new runtime files (git-ignored)

### Priority 5 (ChatGPT Task delivery verification)
- `system/scripts/task_delivery_check.py` — new script
- `system/protocols/P-035_chatgpt_task_delivery_verification.md` — new protocol file
- `system/STATUS.md` — update after manual verification (not before)
- `system/.cache/task_delivery_check.json` — new runtime file (git-ignored)

---

## RB 9.3 Final Acceptance Test

Run all of these from the project root:

```bash
python3 system/scripts/action_drafts.py --smoke
python3 system/scripts/meeting_prep.py --smoke
python3 system/scripts/smart_loops.py --smoke
python3 system/scripts/passive_verification.py --smoke
python3 system/scripts/closeout.py --smoke
python3 system/scripts/loop_autopilot.py --smoke
python3 system/scripts/task_delivery_check.py --smoke
python3 system/scripts/daily_brief.py --smoke
python3 system/scripts/refresh_all.py --date 2026-05-23
python3 system/scripts/validate_openapi_gpt.py
python3 system/scripts/api_smoke_test.py
python3 system/scripts/morning_path_test.py
```

Expected results:
- all smoke tests: 0 failures
- GPT spec: ≤30 ops (expect 28)
- API smoke: all endpoints pass (expect 40+)
- morning path: 5/5 automated steps pass
- `action_drafts.json` exists with ≥0 specs (may be 0 if brief cache is empty)
- `source_health.json` gap rows contain `recovery_command`
- Meeting Prep section in today.md says "ready" for any written artifacts

The morning brief should contain at least one of the following to declare 9.3
product-complete:
- A "Draft-Ready Actions" section with ≥1 spec rendered by the GPT.
- A "Meeting Prep — ready" line pointing at a written artifact.
- A "Data Health" section with specific recovery commands for each gap source.
- A "Loop Proposals" section from `loop_autopilot --phase morning`.
