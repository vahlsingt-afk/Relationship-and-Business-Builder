# RB 9.88: Universal Router — Slice 1 of RB-DEFECT-046 (UIPF)

**Status:** Implemented (2026-06-15)
**Source:** `CLAUDE_DEFECT_RB_046_UNIVERSAL_INTELLIGENCE_PROCESSING_FRAMEWORK.md`,
Proposed Implementation Slice 1 ("Universal router").

## What was implemented

- **`intelligence_triage.triage_overlay_text(text, *, source_type,
  source_name, registry)`** (`scripts/intelligence_triage.py`) — thin
  noise-filtering wrapper around `triage_input()`; returns `None` for
  noise-only/empty input, otherwise the full triage dict.
- **`core.email_overlay()`** (`scripts/rb_core.py`) — new additive
  `triage_signals` field. For each thread in `from_baseline` and
  `active_thread_company_hits`, triages `f"{subject}\n{snippet}"` (one
  `_load_artifact_registry()` load reused across all calls). Non-noise
  results appended as `{thread_id, subject, triage}`.
- **`core.calendar_overlay()`** — same pattern for `today` + `tomorrow`
  events, triaging `f"{title}\n{description}"`, appending
  `{event_id, title, triage}` to `triage_signals`.
- **Tests:** `tests/test_intelligence_triage.py` (3 new cases for
  `triage_overlay_text`), new `tests/test_email_overlay_triage.py` and
  `tests/test_calendar_overlay_triage.py` (2 cases each: noise event/thread ->
  empty `triage_signals`; intelligence-bearing event/thread -> populated
  `triage_signals`). Full suite: 2395 passed (pre-existing collection errors
  in 3 unrelated test files + vendor/ are untouched and predate this change).

## Deferred (per original scope)

LinkedIn ingestion, screenshot/OCR branch, and any new canonical brief section
remain out of scope for this slice — see "Explicitly out of scope" below for
rationale. `triage_signals` is currently produced but not yet surfaced in the
daily brief; evaluate signal quality against real data before deciding how/
whether to render it.

---

## Confirmed gap (code-inspection findings, 2026-06-15)

- `system/scripts/email_overlay.py` and `core.email_overlay()` in `rb_core.py`
  (line ~1298) do **pure baseline/active-thread matching** on email subject +
  snippet. Zero calls to `intelligence_triage.triage_input()`.
- `system/scripts/calendar_overlay.py` and `core.calendar_overlay()` (line
  ~869) do the same for calendar events (title/description vs. baseline
  attendees + active-thread companies). Zero triage calls.
- `daily_brief.py` (17,756 lines) never calls `triage_input()`
  programmatically — it only emits prompt text telling the GPT to paste
  content via `triageInput` manually.
- `intelligence_triage.triage_input()` (line 724) is the canonical, read-only,
  multi-type classifier (macro_signal / micro_graph / ri_event /
  strategic_memory / career_pipeline / noise), already used as an internal
  helper at three call sites in `api/server.py` (3876, 4034, 5666) — all three
  pass `registry=_load_artifact_registry()` loaded once per request and reuse
  it across calls. That's the pattern to follow here.

## Proposed Slice 1 implementation (additive, low-risk)

### 1. New helper: `intelligence_triage.triage_overlay_text(text, *, source_type, source_name, registry) -> dict | None`

Thin wrapper around `triage_input()` that returns `None` when the result is
`noise_only` (so callers can filter cheaply), otherwise returns the triage
dict unchanged. Lives in `intelligence_triage.py` next to `triage_input`.
No new classification logic — just a noise filter for batch use.

### 2. `core.email_overlay()` — new `triage_signals` field

For each thread already surfaced in `from_baseline` and
`active_thread_company_hits` (NOT `senders_not_in_baseline` — too noisy /
low-value for v1), build `text = f"{subject}\n{snippet}"` and call
`triage_overlay_text(text, source_type="email", source_name=thread_id,
registry=registry)`. Load `registry` once via `_load_artifact_registry()` at
the top of `email_overlay()` (mirrors `api/server.py`'s pattern).

Append non-`None` results to a new `out["triage_signals"]` list, each item
shaped:
```json
{
  "thread_id": "...",
  "subject": "...",
  "triage": { ...full triage_input() result... }
}
```
Purely additive key — no existing field renamed/removed, so
`tests/test_learned_patterns.py`'s `source_refs=["calendar_overlay"]`
reference and any other consumer of `email_overlay()`'s existing keys are
unaffected.

### 3. `core.calendar_overlay()` — same pattern

For each event in `today` + `tomorrow` (skip `this_week` for v1 — lower
signal density, keep call volume bounded), build
`text = f"{event['title']}\n{event.get('description','')}"`, call
`triage_overlay_text(..., source_type="calendar", source_name=event_id,
registry=registry)`, append non-`None` results to a new
`out["triage_signals"]` list shaped `{event_id, title, triage}`.

### 4. Daily brief surfacing (follow-up, not this slice)

Do **not** wire a new canonical brief section in this slice. Once (2) and (3)
land and have run for a few days against real data, evaluate signal quality
(false-positive rate from short subject/snippet text) before deciding whether
`triage_signals` warrants its own section or should feed into the existing
`relationship_operational_signal_review` / `communication_intelligence`
sections. This sequencing avoids adding a new ~100th canonical section based
on unvalidated output.

## Explicitly out of scope for Slice 1

- LinkedIn ingestion (`linkedin_ingest*.py`) — already has its own
  triage-adjacent delta pipeline (RB-DEFECT-041); revisit after Slice
  2 (persisted narratives) exists, since LinkedIn deltas are a natural first
  consumer of `strategic_narratives[]`.
- Screenshot/OCR → image classification branch — no OCR capability exists in
  RB today; this is a separate, larger piece of work (new dependency,
  `_detect_format` extension) and should be its own slice.
- Any change to `daily_brief.py`'s `section_order` / canonical sections (see
  #4 above).

## Risk / cost notes

- **Call volume:** `email_overlay()`/`calendar_overlay()` run on every brief
  generation. `triage_input()` runs ~5 regex/keyword classifiers per call —
  cheap individually, but multiplied across dozens of threads/events per
  brief. Reusing one `registry` load (per the `api/server.py` pattern) avoids
  the dominant cost (`_load_artifact_registry()` is the expensive part, not
  the classifiers themselves).
- **False positives:** subject+snippet text is short and often
  conversational ("Re: lunch tomorrow?") — expect a non-trivial noise rate
  even after the `noise_only` filter, since e.g. `_classify_ri` may fire on
  casual mentions of people/companies. This is why #4 defers brief-surfacing
  until signal quality is validated.

## Test plan

- New tests in `tests/test_intelligence_triage.py` for
  `triage_overlay_text()`: noise input -> `None`; macro/RI-bearing input ->
  dict with `triage_id` etc.
- New `tests/test_email_overlay.py` / `tests/test_calendar_overlay.py` (new
  files — none exist today): construct mocked `core.load_email()` /
  `core.load_calendar()` payloads with one obviously-noise thread/event and
  one obviously intelligence-bearing one (e.g. subject mentioning an
  executive departure), assert `triage_signals` contains exactly the latter
  and is empty/absent-equivalent for the former.
- Run full suite (`pytest -q`, currently 2396 passing) to confirm the new
  additive keys don't break any existing exact-dict-equality assertions.

## Sequencing note

This slice can land independently of Slice 2 (persisted narratives). It
produces raw triage signals; Slice 2 gives those signals somewhere durable to
accumulate into (`strategic_narratives[]`). Implement in either order, but
Slice 2's mutation-engine stage should be designed to consume
`triage_signals` output from this slice once both exist.
