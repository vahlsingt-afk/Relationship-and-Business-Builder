# RB-DEFECT-066: PAR Earnings Call Missed by Morning Intelligence

**Filed:** 2026-08-07  
**Status:** Fixed (2026-08-07) — pending next live morning-pipeline run for field verification  
**Owner:** Claude  
**Severity:** High — a priority competitor's completed earnings event was omitted  
**Detected by:** Todd Vahlsing

## Fix summary

Addressed every link in the failure chain below:

1. `system/earnings_calendar.yaml` — added `last_reported_date` (auto-reconciled
   going forward by `earnings_monitor._reconcile_confirmed_dates()`) and
   `ir_page_url` (IR-RSS fallback target) for PAR. `_estimate_next_report_date()`
   now skips a report-month cycle that already has a confirmed date, so a
   completed earnings call no longer keeps generating a stale "reports this
   week" reminder.
2. `system/scripts/morning_pipeline.py` — `brief_only` mode's cache-freshness
   gate previously only checked `intelligence_assessment.json`; it now also
   checks `earnings_monitor_health.json` independently and forces a
   synchronous `earnings_monitor.py --cache` run when stale, closing the
   scan_only/brief_only race that let the brief render before the earnings
   monitor finished.
3. `system/scripts/earnings_monitor.py` — a failed IR RSS fetch now falls
   back to the plain IR page (`_fetch_ir_page_fallback`) and records health
   status either way; unresolved failures (both primary and fallback failed)
   are surfaced via `ir_rss_unresolved_companies`.
4. `system/scripts/earnings_monitor.py` — generically-titled SEC 8-Ks
   ("8-K - Current report") are now classified via `_classify_8k_via_index`,
   which checks the filing's index page for Item 2.02 or an EX-99.1 exhibit.
5. Regression fixture: `system/tests/test_defect_066_par_earnings.py`
   (30 tests) — modeled on PAR's actual Aug 6 filings; verifies a Q2 earnings
   item is produced, the stale pre-earnings reminder is dropped, and the
   renderer no longer emits "No earnings...events" when the scan is
   incomplete or an IR source is unresolved.
6. `system/scripts/daily_brief.py` — `build_earnings_intelligence()` now runs
   *before* `_compute_watchlist_intelligence()` (previously ran after,
   several thousand lines later, so its output could never reach Section E);
   a confirmed post-earnings signal now overrides the entity's watchlist
   status to Escalation with an EARNINGS-badged title. A `scan_health` item
   is attached to `earnings_intelligence` recording whether the scan
   completed before this brief's cutoff.
7. `system/scripts/render_intelligence_brief.py` — Section E
   (`_render_earnings`) now checks that `scan_health` item before asserting
   "No earnings...events"; an incomplete scan or unresolved IR failure
   produces a provisional message instead.

## Problem

PAR Technology released Q2 2026 results and held its earnings call on Thursday,
August 6, 2026 at 4:30 p.m. ET. The August 7 morning intelligence process did not
surface the completed earnings release, presentation, call, or post-call takeaways.

Instead, the daily brief retained only a vague forward-looking reminder:

> **Earnings this week:** PAR Technology, Olo, Toast, Global Payments

The full intelligence brief simultaneously stated:

> *No earnings, funding, exec, or M&A events this cycle.*

That statement was false for a high-priority, explicitly monitored competitor.

## Evidence

- `system/briefs/2026-08-07-daily-brief.md` was generated with data as of
  `2026-08-07 05:13` and mentioned only that PAR earnings were "this week."
- `system/briefs/2026-08-07-intelligence-brief.md` reported no earnings events.
- `system/earnings_calendar.yaml` had PAR configured as `watch_priority: true`, but
  the pre-earnings date in prior briefs was August 8 rather than the confirmed
  August 6 date.
- `system/.cache/earnings_monitor_health.json` shows the earnings monitor checked
  PAR's SEC feeds around 05:30 CT and completed its health output around 05:43 CT,
  after the 05:13 brief cutoff.
- PAR's SEC 8-K and 10-Q feeds returned rows, but its configured IR RSS endpoint
  failed: `Could not fetch https://investors.partech.com/rss/news-releases.xml`.
- The SEC feed's generic title (`8-K - Current report`) is insufficient by itself
  to reliably classify the filing as an earnings release without inspecting filing
  metadata/exhibits or the filing body.
- PAR's official investor-relations page confirms the Q2 2026 results and call on
  August 6, 2026.

## Likely failure chain

1. The earnings calendar retained an estimated date and did not reconcile it to the
   company's confirmed announcement.
2. The morning brief rendered before the earnings monitor completed, creating a
   pipeline ordering/race condition.
3. PAR's configured IR RSS endpoint failed with no successful fallback to the live
   investor-relations page or another company-primary release endpoint.
4. SEC metadata collection found filings but represented the 8-K with a generic
   title, preventing reliable earnings classification and useful summarization.
5. The renderer was allowed to assert "No earnings...events" despite incomplete or
   late earnings-source processing.

## Expected behavior

For every `watch_priority: true` public company, the morning process must:

1. Reconcile estimated earnings dates against confirmed company announcements.
2. Complete earnings/IR/SEC collection before generating the intelligence and daily
   briefs.
3. Detect a completed earnings release and call by the next morning at the latest.
4. Fall back to the company IR page, SEC filing exhibits, or another primary source
   when an IR RSS endpoint fails.
5. Classify generic 8-K filings using filing items, exhibits, and linked release
   content rather than the Atom title alone.
6. Surface the event in `E: Earnings & Corporate`, update the watchlist state, and
   replace the pre-earnings reminder with a post-earnings review item.
7. Avoid claiming there were no earnings events when the earnings scan is incomplete,
   failed, or finished after the brief cutoff.

## Acceptance criteria

- [x] The morning pipeline enforces earnings-monitor completion before brief render.
- [x] A failed IR RSS feed triggers a recorded fallback attempt and visible health
      warning if all primary-source fallbacks fail.
- [x] Confirmed dates supersede estimated dates in `earnings_calendar.yaml` and briefs.
- [x] SEC 8-K/10-Q entries for earnings are correctly classified and linked to the
      associated earnings release when available.
- [x] A regression fixture modeled on PAR's August 6 filings produces a Q2 earnings
      item in section E and a post-earnings action for PAR.
- [x] The renderer cannot emit "No earnings...events" unless the required earnings
      scan completed successfully before the brief's data cutoff.
- [x] The regenerated August 7 fixture output identifies PAR's Q2 2026 earnings and
      does not describe the event as merely upcoming.

All seven criteria verified by `system/tests/test_defect_066_par_earnings.py`
(30/30 passing) plus the full existing suite (3381/3383 passing — the 2
unrelated pre-existing failures are in `test_operational_intelligence_loop.py`,
a live `interrupt_queue.jsonl` schema-drift issue unconnected to earnings).
Not yet verified against a live morning-pipeline run (no network access in
this environment to hit real SEC EDGAR / PAR IR endpoints) — the index-page
classification and IR-page fallback logic are unit-tested with mocked HTTP
responses but should be watched on the next real 4 AM/5 AM cycle.

## Non-goals

- Producing a full investment-research report for every monitored company.
- Treating third-party transcript availability as a prerequisite for detecting the
  release or call.
- Silently suppressing source failures to preserve a clean-looking brief.

