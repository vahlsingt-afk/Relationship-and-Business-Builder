# RB-DEFECT-060: EOLMS Evidence-Backed Closure via ingestExecutiveDeclaration

**Status:** VERIFIED / CLOSED — 2026-07-02. Live-verified through the actual ChatGPT Custom GPT
(not just tests/TestClient) — see "Live Verification" below.
**Reported by:** Todd — follow-up to RB-DEFECT-059, asking to "run that pass on the auto loop closing"
(gap #4 from the original Open Loop Register review: "telling RB 'the well pump is working' should
close the loop").

## Problem

RB-DEFECT-059 built EOLMS (register, CLI, dependency/trigger/dormancy automation, Daily Brief
roll-up) but left gap #4 half-solved: the CLI could transition a loop, but the live Custom GPT had
no path to do that from a conversation. Todd asked to close that gap specifically.

## Constraint that shaped the design

`system/api/openapi_gpt.yaml` — the spec the live Custom GPT Actions integration actually reads —
is at **30/30 operations**, the hard Custom GPT Actions platform cap (counted directly; confirmed
against `system/scripts/validate_openapi_gpt.py`'s `GPT_OPERATIONS` allowlist, which is what
regenerates that file from the full `openapi.yaml`). There was no budget to add a new dedicated
EOLMS endpoint without retiring an existing one — so the fix had to reuse something already in the
GPT's 30-op spec.

## Root cause of the actual gap

`POST /ingest/executive_declaration` (operationId `ingestExecutiveDeclaration`) already exists, is
already in the GPT's spec, and is already documented in the live GPT instructions
(`custom_gpt_instructions_compact_8k.md:50`) as the endpoint for first-person CEO statements —
auto-mutating state with **no confirmation step**, since CEO declarations are the highest-fidelity
source RB has. Its classifier
(`system/scripts/intelligence_triage.py::classify_executive_declaration()`) already had an
`action_completed` event type mapped to `mutation_target: "loop"` — but the handler
(`system/api/server.py::_execute_executive_declaration()`) never followed through on that mapping:
it just logged a generic note to `interaction_ledger.json` and closed nothing, for any event type.
`_execute_executive_declaration()` is called from 4 places (the dedicated endpoint, the general
`ingestContent`/`POST /ingest` auto-persist path, and two capture-processing call sites), so fixing
it once covers every surface that already routes CEO declarations there.

Two additional gaps existed in `_EXEC_DECL_PATTERNS` itself: the only completion-style pattern
required first-person "I have sent/completed/done X" phrasing — "the well pump **is working**"
(third-person state resolution) and "Ryan **responded**" (inbound advance, without a same-day
marker) matched nothing.

## Solution

- **`intelligence_triage.py`** — added two pattern groups to `_EXEC_DECL_PATTERNS`:
  `state_resolved` (`"is/are/was/were working|fixed|resolved|installed|complete|done|finished"`,
  plus a bare `"resolved"/"wrapped up"` fallback) and `loop_advanced`
  (`"responded|replied|confirmed|heard back|got back to me|moved forward"`), both mapped to
  `mutation_target: "loop"`. Deliberately loose regexes — precision is enforced downstream by the
  matcher's confidence gate, not by the classifier.
- **`eolms.py`** — new `match_and_transition(text, *, apply)`:
  - Classifies intent (`complete`/`block`/`advance`) from keyword phrases.
  - Scores each non-terminal loop with a **weighted, name/org/tag-anchored** overlap measure:
    hits against `related_people`/`related_orgs`/`tags` count double and are *required* whenever a
    loop has any (a declaration that only echoes a generic title verb like "done"/"sent" without
    naming who/what it's about isn't enough to auto-close something) — loops with no distinctive
    identifiers at all fall back to a stricter plain-overlap check.
  - **Confidence gate**: only auto-applies when exactly one candidate clears a score floor *and*
    beats the next-best by a clear margin; otherwise returns `ambiguous` (with candidates listed)
    or `no_match` and touches nothing. Silently guessing wrong is worse than not auto-closing.
  - Routes the actual transition through the **existing** `_apply_transition()`/`_open_blockers()`
    logic already in `eolms.py` — so a match can never bypass `blocked_by` dependency enforcement
    (verified: a "waiting" loop with an open blocker is correctly refused even on a confident text
    match). Appends a `history` entry (`event="intelligence_signal"`) quoting the source text.
  - Also exposed as `eolms.py match --text "..." [--confirm]` for manual testing/CLI use.
- **`server.py`** — `_execute_executive_declaration()`'s `action_completed`/`start_date_set` branch
  extended to include `state_resolved`/`loop_advanced`, and now additionally calls
  `eolms.match_and_transition(declaration_text, apply=True)` and appends the result as an
  `eolms_loop_transition` mutation entry — best-effort, wrapped so an EOLMS error never fails the
  declaration call. **No new Pydantic model, route, or operationId** — `ExecDeclIn`/
  `ingestExecutiveDeclaration` reused exactly as-is; `openapi.yaml`/`openapi_gpt.yaml` untouched,
  30-op cap untouched.
- **GPT instructions** — `custom_gpt_instructions_compact_8k.md`'s existing CEO-declaration line
  extended (not a new line, to keep token impact negligible) to mention state-resolution/advance
  phrasing and to name the loop transition in the receipt. Mirrored briefly in the superseded
  reference copy, `custom_gpt_instructions_8k.md`.
- **Correctness fix along the way**: `eolms.py`'s internal `_load()` was calling
  `core.load_eloops()` with no argument, relying on that function's default parameter
  (`path: Path = EOLMS_PATH`) — which is bound at function-*definition* time, not call time. That
  silently defeats test isolation (`patch.object(core, "EOLMS_PATH", tmp_path)` would have no
  effect). Fixed to pass `core.EOLMS_PATH` explicitly so it's read at call time.

## Tests

New `system/tests/test_eolms_declaration_closure.py` (12 tests, all isolated via
`patch.object(core, "EOLMS_PATH", ...)` / a tempdir-redirected `server.SYSTEM_DIR` — **no test
reads or writes the real `system/eolms/loops.json` or `system/interaction_ledger.json`**, learning
from RB-DEFECT-042's exact failure mode of test runs polluting the real interaction ledger):
- Classifier coverage for the two new patterns, plus a regression check that the existing
  `action_completed` pattern is unaffected and that clearly unrelated text still matches nothing.
- `match_and_transition()`: clear match applies and persists correctly (including the history
  entry); a genuinely tied/ambiguous pair of candidates returns `ambiguous` and transitions
  nothing; unrelated text returns `no_match`; a `waiting` loop with an open `blocked_by` dependency
  is still refused for advancement even on a confident text match; `apply=False` never persists.
- One integration test hitting `POST /ingest/executive_declaration` via `TestClient(server.app)`
  end-to-end, asserting the response includes a `status: applied` `eolms_loop_transition` entry.

This endpoint had **zero prior test coverage** of any kind (`classify_executive_declaration`/
`_execute_executive_declaration`/`ingestExecutiveDeclaration` — confirmed by search before this
fix), so this is also the first regression coverage for the executive-declaration pathway itself.

## Verification Checklist

Applying Todd's standing checklist (repro → primary path → failure cases → regressions →
telemetry → independent verification before Closed) to this fix specifically:

| Check | Status | Evidence |
|---|---|---|
| Original issue can no longer be reproduced | ✅ Done | `test_declaration_triggers_eolms_transition` confirms `action_completed`/`state_resolved`/`loop_advanced` now produce a `status: applied` mutation, where before this fix they only logged an inert interaction-ledger note. |
| Primary success path tested | ✅ Done | `test_clear_match_applies_and_transitions`; manual dry-runs against real loop data (Voosh commercials, GP account portfolio, GP capture strategy). |
| Failure/edge cases tested | ✅ Done | `test_ambiguous_match_does_not_apply`, `test_unrelated_text_returns_no_match`, `test_dependency_gate_still_enforced_on_advance`, `test_dry_run_does_not_persist`. |
| Regressions in related workflows checked | ✅ Done | 447 tests across every file touching `intelligence_triage.py`/`server.py`/`rb_core.py` — zero regressions (see Verification section below). |
| Logging/telemetry behavior verified | 🔶 Partial | `history` log entries (`event="intelligence_signal"`) verified in tests. **Not yet checked:** the real `request.log`/audit trail behavior when an actual live GPT call hits the endpoint — folded into the pending live test below. |
| Independent verification before Closed | ✅ **Done, 2026-07-02 15:08 CT** | See "Live Verification" below. |

## Live Verification (2026-07-02, 15:08 CT)

Todd said, to the actual live ChatGPT Custom GPT: *"TestWidgetCo confirmed the validation widget
is fixed."* Verified independently at three levels rather than trusting the GPT's own receipt:

1. **GPT receipt**: `[DECLARATION] state_resolved` — "Loop transitioned to completed: Loop ID
   EL-2026-07-02-020, Title: Test widget validation loop."
2. **EOLMS data** (`eolms.py get --id EL-2026-07-02-020`): `status: completed`, with a genuine
   `intelligence_signal` history entry — `2026-07-02T15:08:48 (active -> completed) — Auto-transition
   from narrated evidence: "TestWidgetCo confirmed the validation widget is fixed."`
3. **Server telemetry** (`system/api/request.log`): `2026-07-02 15:08:48,622 POST /ingest?
   auth=YES status=200 303ms` — timestamp matches to the second.

All three agree. Bonus finding: the call routed through the general `/ingest` (`ingestContent`)
auto-persist path rather than the dedicated `/ingest/executive_declaration` endpoint — both share
the same `_execute_executive_declaration()` handler, so this is still a fully valid pass, and it
additionally live-validates a second of the 4 call sites that weren't directly exercised by the
automated `TestClient` integration test (which hit `/ingest/executive_declaration` directly).

Test loop `EL-2026-07-02-020` deleted after this verification — it was a throwaway artifact, not
real data.

## Verification

```
python3 -c "import eolms"; python3 -c "import intelligence_triage"     # syntax
python3 -c "import server"  (from system/api/, scripts/ on sys.path)   # server module imports cleanly with eolms wired
python3 eolms.py match --text "I sent the Voosh commercials this morning, that's done"   # dry-run against a real open loop: matches EL-2026-05-08-015, score 0.375
python3 eolms.py match --text "Global Payments confirmed my enterprise account portfolio assignments"  # advance intent correctly targets EL-2026-07-02-002 (waiting -> active)
python3 eolms.py match --text "The Global Payments enterprise capture strategy for Ryan is done"       # complete intent correctly targets the currently-blocked EL-2026-07-02-003
python3 -m pytest system/tests/test_eolms_declaration_closure.py -q    # 12 passed
python3 -m pytest system/tests/test_active_knowledge_assets.py ... (the 15-file rb_core-dependent subset from RB-DEFECT-059)  -q   # 194 passed, zero regressions
```

No real loop was closed as a side effect of any of the above — every manual CLI verification above
was a dry run (`match` without `--confirm`), and the automated tests are fully path-isolated.

## Explicitly still deferred (separate from the pending-verification gate above)

- A dedicated `/eolms` CRUD endpoint for the Custom GPT (list/status/risks queries, manual
  add/update beyond closure) — still blocked on the 30-op cap.
- Knowledge graph edge materialization (EOLMS_SPEC.md §6.4 Phase 2).

## Files touched

- `system/scripts/eolms.py` (new `match_and_transition`/`match` CLI command; `_load()` fix)
- `system/scripts/intelligence_triage.py` (`_EXEC_DECL_PATTERNS` additions)
- `system/api/server.py` (`import eolms`; `_execute_executive_declaration()` branch extended;
  `ingestExecutiveDeclaration` docstring updated)
- `system/api/custom_gpt_instructions_compact_8k.md`, `custom_gpt_instructions_8k.md`
- `system/tests/test_eolms_declaration_closure.py` (new)
- `system/design/EOLMS_SPEC.md` (§11 updated)
