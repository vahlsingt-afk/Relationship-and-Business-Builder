# RB 9.76: Calendar-Change & Interview-Activity Detection — RB-DEFECT-043 #6 (remainder)

**Status:** Implemented (2026-06-14)
**Source:** RB-DEFECT-043 Defect #6, the portion deferred by RB 9.75 ("Calendar-change
detection (new/cancelled/moved meetings) and interview-activity detection ... both
require a day-over-day calendar snapshot to diff against ... deferred").

## What was found

RB does not persist calendar state between runs — `calendar_overlay()` fetches live
each run with no snapshot/diff. However, `latest_brief.json` (written by
`publish.py`) already persists the *entire* `canonical_brief.sections` dict from the
prior run, and `_compute_what_changed` already reads it back for other deltas. This
meant a snapshot/diff mechanism could piggyback on existing persistence — no new
file or script needed, unlike LinkedIn's separate `baseline_index.json` snapshotting.

## What was implemented

`system/scripts/daily_brief.py`:
- New section `calendar_snapshot` (added to the `sections` dict after
  `communication_intelligence`). Not in `brief_display_order` — never renders
  directly. Holds one item with `extras.today` and `extras.tomorrow`, each a list of
  `{id, title, start, end}` for that day's calendar events (from
  `report["calendar"]`, i.e. `core.calendar_overlay()`).
- New aggregation block (after the RB 9.75 communication_intelligence block, in
  `build_canonical_brief`):
  - Builds today's `calendar_snapshot` item (above).
  - Reads `published/daily/latest_brief.json`, pulls its
    `canonical_brief.sections.calendar_snapshot[0].extras.tomorrow` — yesterday's
    forecast for *today* — and diffs it against today's actual `today` events by
    event `id`:
    - **New**: ids in today's events but not in yesterday's forecast.
    - **Cancelled**: ids in yesterday's forecast but not in today's events.
    - **Moved**: same id, different `start`.
  - Scans today/tomorrow/this_week event titles for the substring "interview"
    (case-insensitive) to flag interview activity.
  - Appends one item, "Calendar Changes & Interview Activity (Since Yesterday)", to
    `communication_intelligence`. `disposition="act_today"` if any change or
    interview activity is found, else `"monitor"`. If no prior snapshot exists yet
    (first cycle), summary says so explicitly and `confidence="medium"`.
- Updated the RB 9.75 `communication_intelligence` rendering rule to render this new
  item (previously the rule explicitly said calendar-change/interview detection
  "are not yet covered" — that caveat is now removed and replaced with rendering
  instructions).

## Notes

- This is additive aggregation only — no new ingestion, no new files written by
  `daily_brief.py` itself (the snapshot rides along inside the existing
  `latest_brief.json` published by `publish.py`).
- The diff is necessarily one cycle behind on its first run after this change (no
  prior `calendar_snapshot` to compare against) — handled via
  `extras.has_prior_snapshot` / the "first cycle" message.
- Interview detection is a simple title-substring match. If RB's calendar titles for
  interviews don't contain "interview" literally, this won't catch them — no
  evidence either way was available without live calendar data.

## Deferred

- The full RB-DEFECT-043 Layer 1/Layer 2 (Intelligence Brief vs. Daily Brief)
  document split remains the larger architectural item from RB-DEFECT-043, still
  deferred to RB 9.77+.
- Defect #5 stays with the RB-DEFECT-041/RB 9.73 track (per RB 9.73's triage).

## Tests

Full suite: `python3 -m pytest system/tests/ -q` — 2380 passed baseline expected, no
new tests added (pure aggregation/diff over existing data structures, exercised
implicitly by existing canonical-brief tests).
