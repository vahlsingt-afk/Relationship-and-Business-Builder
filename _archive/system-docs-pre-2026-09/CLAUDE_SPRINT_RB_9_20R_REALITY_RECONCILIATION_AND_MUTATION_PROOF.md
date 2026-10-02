# Claude Sprint Brief — RB 9.20R Reality Reconciliation And Mutation Proof

**Prepared:** 2026-05-28  
**Prepared by:** Codex  
**Audience:** Claude Code / next RB implementation sprint  
**Sprint posture:** Stabilization, truth reconciliation, dirty-tree cleanup, and mutation-proof hardening. Do not start a new feature sprint until this is closed.

---

## Why This Sprint Exists

RB 9.20 was reported as complete, but the current workspace does not yet prove
that state cleanly.

The problem is not only test red. The larger problem is source-of-truth drift:

- delivery summaries describe files that are not present in this workspace
- `STATUS.md` is stale and still describes an earlier RB 9.13-era operating
  state
- the dirty tree is large enough that unrelated work, generated artifacts,
  runtime data, sprint code, and defect records are mixed together
- the system continues to show a recurring product defect: strong conversational
  reasoning without durable intelligence mutation

The sprint principle:

> RB cannot become a Chief of Staff operating system while its own repo cannot
> say, with evidence, what is live, what is pending, what is broken, and what
> actually persists.

This is a reconciliation sprint. Treat it as an operational trust sprint, not a
feature sprint.

---

## Current Verification Reality

Codex ran these checks on 2026-05-28.

```bash
python3 -m pytest system/tests -q
```

Result:

```text
309 passed, 2 failed
```

Failures:

```text
system/tests/test_strategic_events.py::test_operational_ai_theme_links_pizza_and_starbucks
system/tests/test_strategic_events_9_11.py::test_operational_ai_theme_links_pizza_and_starbucks
```

Both failures expected:

```text
report["event_count"] == 2
```

Actual:

```text
4
```

Likely issue: `strategic_events.build_report()` uses the persisted
`system/strategic_events.json` store by default, so test fixtures are not
isolated from live stored events. Fix the test or the report path so fixture
tests are deterministic and do not count unrelated existing events.

Additional checks:

```bash
python3 system/scripts/api_smoke_test.py
```

Result:

```text
All 40 endpoints pass.
```

```bash
python3 system/scripts/canonical_response_eval.py --smoke
```

Result:

```text
Overall smoke result: PASS
```

Important mismatch:

- The RB 9.20 delivery summary names `system/scripts/cos_judgment.py`, but that
  file is not present in this workspace.
- The RB 9.20 delivery summary names these tests, but they are not present:
  - `system/tests/test_daily_brief_cos_judgment.py`
  - `system/tests/test_daily_brief_negative_space.py`
  - `system/tests/test_daily_brief_execution_closure.py`
  - `system/tests/test_daily_brief_macro_synthesis.py`
  - `system/tests/test_daily_brief_completion_status.py`
  - `system/tests/test_daily_brief_linkedin_delta_intelligence.py`
- A grep for `CoS Judgment`, `What Is Not Happening`, `hard_truth`,
  `negative_space`, and `daily_brief_status` in the expected daily-brief and
  prompt files did not find the expected implemented surface.

Do not assume RB 9.20 is truly complete until this mismatch is resolved.

---

## Current Defect Inventory

### Closed Or Locally Closed

- `RB-DEFECT-001` — API availability regression  
  Current defect file says closed: named tunnel live at
  `https://rb-api.bridgepointops.org`; automated closure checks passed
  2026-05-28.

- `RB-DEFECT-002` — Passive LinkedIn vendor intelligence failed to mutate
  industry graph  
  Current defect file says closed in RB 9.18. Remaining work exists around
  generalizing beyond Qu-specific examples, but the defect is recorded as
  closed.

- `RB-DEFECT-004` — Apple Messages / Calls ingestion gap breaks Daily Brief
  completeness  
  Current defect file says closed in RB 9.18.

- `RB-DEFECT-006` — Passive intelligence confidence + corroboration gap  
  Current defect file says closed in RB 9.18.

### Needs Status Reconciliation

- `RB-DEFECT-003` — Passive communication failure failed to mutate RI  
  The file describes a fix, but lacks a formal `Status:` field. Decide whether
  to mark it closed after targeted regression proof, or mark it fixed pending
  proof.

- `RB-DEFECT-007` — LinkedIn ingestion treated as static import instead of
  longitudinal RI mutation  
  Status is `Fixed in code; awaiting live Custom GPT validation`.

### Open / Core 9.20R Cluster

- `RB-DEFECT-008` — Daily Brief drifting toward helpful assistant instead of
  world-class CoS  
  Status: Open.

- `RB-DEFECT-009` — Live CoS layer behaves as intelligent conversational
  assistant instead of autonomous strategic operator  
  Status: Open.

- `RB-DEFECT-010` — Conversational analysis fails to mutate strategic industry
  intelligence  
  Status: Open. Added 2026-05-28. This is the Toast / Regulars Report
  retention-economics defect.

---

## Non-Negotiable Invariant

RB must not stop at conversational analysis.

Any durable strategic signal discovered in conversation, LinkedIn analysis,
market analysis, uploaded artifacts, or Daily Brief review must resolve into one
of these explicit states:

- `RB recorded`
- `RB updated`
- `RB proposed`
- `RB blocked`
- `RB skipped`
- `RB did not persist`
- `pending confirmation`

The user should never have to wonder whether a strategic insight became durable
system memory.

---

## Sprint Goals

### G1 — Clean And Classify The Dirty Tree

Do not blindly stage everything.

Classify changes into:

- RB 9.20 / 9.20R code
- RB 9.20 / 9.20R tests
- defect records
- status and roadmap docs
- API / schema / prompt updates
- runtime data
- generated artifacts
- caches / inbox / vendor files
- unrelated historical drift

Only commit intentionally tracked source, docs, schemas, and tests. Generated
or runtime files should be ignored, parked, or explicitly documented if they
must remain tracked.

Recommended first commands:

```bash
git status --short
git diff --stat
rg --files system/tests | sort
rg --files defects | sort
```

Look especially at:

- `system/vendor/`
- `system/.cache/`
- `system/inbox/`
- `system/published/`
- `system/_snapshots/`
- `system/graphs/`
- `system/audit/`

Do not delete user data. If a path is generated but useful, propose an ignore or
parking strategy instead of removing it.

### G2 — Restore Test Green

Fix the two strategic-event test failures.

Likely routes:

- make tests use a temporary `store_path` when calling `build_report()`
- or adjust `build_report()` to allow an isolated mode / `existing=[]` for
  fixture tests
- or filter report counts to input-derived events when a caller supplies
  explicit `market_report` and `linkedin_records`

Be careful: do not break the live convergence store. The production report
should still be able to merge with persisted strategic events when called in
normal mode.

Acceptance:

```bash
python3 -m pytest system/tests -q
```

must pass.

### G3 — Reconcile RB 9.20 Claimed Artifacts

Determine whether RB 9.20 artifacts are missing, unstaged elsewhere, or never
implemented in this workspace.

Required investigation:

```bash
ls -la system/scripts/cos_judgment.py
rg -n "cos_judgment|hard_truth|negative_space|what_is_not_happening|daily_brief_status" system/scripts system/tests system/api system/*.md
rg --files system/tests | rg "daily_brief_(cos|negative|execution|macro|completion|linkedin)"
```

If missing, implement or explicitly downgrade 9.20 from complete to partial in
`STATUS.md` and defect statuses. Do not leave the repo claiming completion that
the files cannot prove.

### G4 — Close Or Reframe RB-DEFECT-008

The Daily Brief CoS defect cannot be closed by prompt language alone.

Acceptance requires deterministic evidence that the rendered brief or API brief
contains, when evidence supports it:

- hard truths
- negative-space analysis
- stale-source caveats
- macro-to-operator implications
- prioritization tradeoffs
- execution options
- completion/failure artifact or status

Minimum implementation options:

- create `system/scripts/cos_judgment.py` and wire it into
  `daily_brief.build_report()`
- or implement the equivalent inside `daily_brief.py` if that better matches
  local style

Minimum tests:

- a test that fails on generic encouragement without hard truth
- a test that fails when stale sources allow confident quiet claims
- a test that fails when recommended actions lack execution closure
- a test that fails when macro commentary is generic and not mapped to
  restaurant/operator/vendor implications
- a test that checks rendered Daily Brief / API payload, not only a synthetic
  isolated text snippet

### G5 — Implement Mutation-Proof Path For RB-DEFECT-009 And RB-DEFECT-010

RB-DEFECT-009 and RB-DEFECT-010 share the same root pattern:

> good reasoning with no durable mutation proof.

But keep their surfaces distinct:

- `RB-DEFECT-009`: live interaction / LinkedIn / relationship and thesis
  alignment signals
- `RB-DEFECT-010`: conversation-derived industry, positioning, and
  thought-leadership intelligence

Acceptance for 009:

- detect public alignment/support signals
- emit review-first RI mutation proposals for matched contacts
- track thesis/narrative alignment as durable strategic memory
- link current signals to prior strategic-memory items and market signals
- produce narrative convergence counts and confidence
- recommend concrete next actions without waiting for a second prompt
- preserve no-auto-send and no unsupported promotion guardrails

Acceptance for 010:

- detect durable strategic insights inside conversational analysis
- classify insight type:
  - industry trend
  - vendor positioning
  - user positioning
  - thought-leadership theme
  - market signal
  - relationship implication
  - action opportunity
- emit review-first mutation proposals for industry graph and user strategic
  positioning memory
- persist confirmed strategic insights with source, timestamp, confidence,
  claim status, and future-use tags
- link the Toast / Regulars Report retention thesis to restaurant-tech realism
  and operational trust narratives
- make the insight retrievable through strategic-memory queries and eligible for
  future Daily Brief synthesis

Add a fixture for the Toast / Regulars Report scenario. It must fail if RB only
returns conversational analysis without:

- extracted durable insights
- proposed graph mutations
- proposed user-positioning mutations
- thought-leadership reuse tags
- persistence status
- retrieval proof

### G6 — Live Validation For RB-DEFECT-007

The LinkedIn ZIP ingester has local tests, but the defect remains pending live
Custom GPT validation.

Acceptance:

- API returns `delta_intelligence`
- rendered response includes:
  - `Baseline Comparison`
  - `Graph Mutations`
  - `Persistence Verification`
- response does not sound like a static import summary
- live Custom GPT Action schema/instructions are confirmed current, or the
  exact blocker is documented

If live GPT validation cannot be performed in this sprint, keep the defect at:

```text
Fixed in code; awaiting live Custom GPT validation
```

and list the blocker.

### G7 — Update Status And Roadmap Truth

Update `system/STATUS.md` after the code/test reality is settled.

Required updates:

- last reviewed date and sprint
- actual test count and current pass/fail state
- actual API smoke state
- current Daily Brief CoS layer status
- current strategic-memory / mutation-proof status
- open defects
- hardest unbuilt edges

Do not promote a component to `LIVE` unless it has been exercised against real
data or deterministic tests that represent the operating path.

Update `system/CLAUDE_DEVELOPMENT_MAP.md` only as needed to reflect the new
sprint order:

1. RB 9.20R — reality reconciliation and mutation proof
2. RB 9.21 — live CoS orchestration
3. RB 9.22 — Daily Brief CoS contract hardening, if not completed in 9.20R
4. RB 9.23 — draft-ready execution
5. source automation / productization after the trust layer is stable

---

## Recommended Work Order

1. Run `git status --short` and classify the dirty tree.
2. Fix the two failing strategic-event tests.
3. Run the full test suite.
4. Investigate missing RB 9.20 files and tests.
5. Decide whether to restore/implement missing 9.20 artifacts or downgrade
   their status.
6. Implement the minimum mutation-proof path for defects 008/009/010.
7. Add regression tests for the Toast / Regulars Report retention thesis.
8. Run:

   ```bash
   python3 -m pytest system/tests -q
   python3 system/scripts/api_smoke_test.py
   python3 system/scripts/canonical_response_eval.py --smoke
   ```

9. Update defect statuses and `STATUS.md`.
10. Create small intentional commits rather than one giant dirty-tree commit.

---

## Commit Hygiene

Prefer small commits:

1. test isolation fix / strategic events green
2. defect records and sprint handoff
3. RB 9.20 artifact reconciliation or implementation
4. mutation-proof strategic-memory / insight intake changes
5. status and roadmap docs

Do not stage:

- secrets
- raw inbox data unless intentionally tracked
- generated caches
- vendored dependencies unless the repo already tracks them intentionally
- large snapshots without a reason

Before every commit:

```bash
git diff --cached --stat
git diff --cached --name-only
```

---

## Definition Of Done

RB 9.20R is done when:

- the dirty tree is classified and reduced to intentional changes
- `python3 -m pytest system/tests -q` passes
- `api_smoke_test.py` passes
- `canonical_response_eval.py --smoke` passes
- `STATUS.md` reflects current reality
- defect statuses are accurate
- RB 9.20 claimed artifacts are either present and tested or explicitly marked
  partial/missing
- RB-DEFECT-010 has at least one regression fixture or implementation path that
  proves conversation-derived strategic insight no longer dies as prose
- no new feature sprint is started on top of an untrusted repo state

---

## Final Note For Claude Code

Be skeptical of completion claims until the repo proves them.

The product target is not "better analysis." The product target is durable
strategic accumulation:

```text
signal -> classification -> confidence -> proposed mutation -> persistence
proof -> retrieval -> future use
```

Any output that stops before persistence proof is still assistant behavior, not
Chief of Staff behavior.
